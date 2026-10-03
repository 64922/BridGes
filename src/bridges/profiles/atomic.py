"""V2 Issue 08：原子画像（无固定类别的长期信息列表）。

四维记录仍是自动抽取与冲突消解的写入引擎（``four_dimensions``）；本模块把
它的结果镜像成用户看到、修改和删除的原子条目，并承担旧四类数据的一次性
原子化迁移。

不变量：

- **事实身份**：主体/关系/对象/范围构成完整事实身份（``fact_key``），同一
  事实只保留一条活动条目并补充证据；不同事实（喜欢与正在学习、年级与专业、
  并行目标）并存。单值属性槽（年级/专业/身份）的明确新值替代旧活动条目，
  被替代版本保留正文与替代链供对账。``identity_key``（账户 + 规范化正文）
  仍是文本抑制键，兼容旧墓碑。
- **用户权威**：用户编辑过的条目不再被自动抽取改写（正文不同即跳过），
  换值同时抑制旧正文与旧事实身份；删除后转为墓碑，旧消息重放与自动抽取
  都不会让它复活。
- **账户隔离**：持久化实现全部经 ``BridgesDatabase.scoped`` 或按账户过滤，
  跨账户读写在领域层即不可达。
- **可对账、可恢复**：迁移按批次记账并给出确定性对账摘要，回滚只删除该
  批次新建的条目，旧四维记录本身不被改写。

本模块不调用语言模型：记住／忘掉、迁移与切片编译全部是确定性规则。
"""

from __future__ import annotations

import calendar
import copy
import hashlib
import json
import logging
import re
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from bridges.contracts.atomic_profile import (
    AtomicProfileEvidenceQuoteStatus,
    AtomicProfileEvidenceSource,
    AtomicProfileEvidenceSourceStatus,
    AtomicProfileFactIdentity,
    AtomicProfileFactRelation,
    AtomicProfileFactScope,
    AtomicProfileFeedback,
    AtomicProfileFeedbackKind,
    AtomicProfileFeedbackProjection,
    AtomicProfileGoalState,
    AtomicProfileItem,
    AtomicProfileItemEvidenceProjection,
    AtomicProfileItemFeedbackRequest,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemProjection,
    AtomicProfileItemStatus,
    AtomicProfileMemoryKind,
    AtomicProfileMemoryResult,
    AtomicProfileMemoryStatus,
    AtomicProfileMigrationReport,
    AtomicProfileMigrationStatus,
    AtomicProfileReconciliationEntry,
    AtomicProfileReconciliationOutcome,
    AtomicProfileTombstoneEntry,
    AtomicProfileValidityStatus,
    AtomicProfileWriteOrigin,
    feedback_effect_for,
    feedback_message_for,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profile_adoption import (
    AdoptedProfileItem,
    AdoptedProfileSlice,
    ProfileSliceExclusion,
    ProfileSlicePurpose,
)
from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionProfileRecord,
    FourDimensionRecordStatus,
    ProfileSensitivityClass,
    ProfileSlice,
    ProfileSliceItem,
    UnusedSliceItem,
)
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import ProfileError
from bridges.profiles.commit import ProfileCommit, SourceWithdrawal, SourceWithdrawalStatus
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    confidence_rank,
    is_recallable_confidence,
)
from bridges.profiles.purpose import (
    adoption_reason,
    applicability,
    build_purpose,
    constraint_conditions,
    expression_preference_tags,
    has_task_topic,
    is_overridden,
    is_resource_constraint,
    task_topic_matches,
)
from bridges.profiles.transactions import joined_transaction
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError

logger = logging.getLogger(__name__)

ATOMIC_PROFILE_MIGRATION_VERSION = "profile-atomic-v1"
#: 逐条对账原因码（工单 01）。迁移报告的计数回答「迁入了几条」，这里回答
#: 「每条为什么是这个结论」；全部是确定性规则，且不含任何画像正文。
MIGRATION_REASON_MIGRATED = "source_migrated"
MIGRATION_REASON_EMPTY_TEXT = "source_text_empty"
MIGRATION_REASON_SOURCE_WITHDRAWN = "source_withdrawn_tombstone_written"
MIGRATION_REASON_SOURCE_ALREADY_MIGRATED = "source_record_already_linked"
MIGRATION_REASON_IDENTITY_TOMBSTONED = "identity_suppressed_by_tombstone"
MIGRATION_REASON_USER_ITEM_KEPT = "user_item_kept_not_overwritten"
MIGRATION_REASON_IDENTITY_EXISTS = "identity_exists_item_linked"
MIGRATION_REASON_MIGRATION_FAILED = "source_migration_failed"
#: 单轮注入模型的最大条目数（最小切片：只取当前任务必要的几条）。
MAX_SLICE_ITEMS = 4
_MAX_ITEM_TEXT_LENGTH = 1000
#: 忘掉指令的目标最短长度：过短的目标会误删无关条目，宁可返回「没找到」。
_MIN_FORGET_TARGET_LENGTH = 2
#: 条目与当前问题的二元组重合度下限（另有「至少共享两个二元组」的绝对门）。
_QUESTION_MATCH_RATIO = 0.25

_CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}")
#: 指令必须出现在消息开头或标点之后：这样「我记住了」「不要记住」「别忘掉」
#: 之类的叙述或否定不会误触发写入与删除。
_DIRECTIVE_PREFIX = r"(?:^|[，。；：！？、,;:!?\s])"
_REMEMBER_DIRECTIVE_RE = re.compile(
    _DIRECTIVE_PREFIX
    + r"(?:请|麻烦)?(?:帮我)?记住[：:,，]?\s*(?P<value>[^。；;！!\n]{1,200})"
)
_FORGET_DIRECTIVE_RE = re.compile(
    _DIRECTIVE_PREFIX
    + r"(?:请|麻烦)?(?:帮我)?(?:忘掉|忘记|删掉|删除)[：:,，]?\s*(?P<value>[^。；;！!\n]{1,200})"
)
_DIRECTIVE_TAIL_RE = re.compile(r"(?:吧|了|好吗|可以吗|谢谢)[。！!？?，,；;]*$")


class AtomicProfileError(ProfileError):
    """原子画像领域错误；消息可直接展示，不泄漏其他账户的存在。"""


class MemoryDirective:
    """一条「记住／忘掉」指令（含目标正文，仅在本轮内存中使用）。"""

    def __init__(self, kind: AtomicProfileMemoryKind, target: str) -> None:
        self.kind = kind
        self.target = target


@dataclass(frozen=True)
class ProfileSourceMessage:
    """证据来源消息的最小只读快照（组合根从聊天仓库按账户映射）。"""

    message_id: str
    conversation_id: str
    role: str
    status: str
    content: str
    created_at: datetime


#: 来源读取端口：按账户与消息标识返回快照；不存在时返回 ``None``。
#: 未接线时证据接口如实把来源标为不可读，绝不伪造原话或定位。
ProfileEvidenceSourceReader = Callable[[str, str], ProfileSourceMessage | None]


def normalize_text(text: str) -> str:
    """折叠空白后的条目正文（去重的规范化基础）。"""

    return " ".join(text.split())


def identity_key(account_id: str, text: str) -> str:
    """账户 + 规范化正文构成的文本去重与抑制键（旧墓碑兼容）。"""

    payload = f"{account_id}|{normalize_text(text).casefold()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


#: 事实主体：当前账户用户。事实身份不描述其他人的事实。
FACT_SUBJECT_USER = "user"
#: 单值属性槽中年级的取值形状；对象归一化为年级 token 本身，让
#: 「大二学生」与「大二」合并为同一事实。
_GRADE_TOKEN_RE = re.compile(
    r"(?:我)?(?:现在|目前)?(?:是|在读|就读)?"
    r"(?P<grade>大[一二三四五六]|研[一二三]|高[一二三四]|初[一二三四]|博[一二三四]|本[一二三四])"
    r"(?:的?学生|年级)?(?:了)?[。！!]?"
)
_MAJOR_HINT_RE = re.compile(r"(?:专业|主修|院系|学院|系)")
_CURRENT_SCOPE_RE = re.compile(r"(?:这次|本轮|本周|今天|今晚)")
_LEARNING_RE = re.compile(
    r"^(?:我)?(?:现在|目前)?(?:正在|在|想|要|准备|打算)?\s*学(?:习)?\s*(?P<obj>[^，。；;！!？?]+)"
)
_RESEARCH_RE = re.compile(r"(?:正在|在)?\s*研究\s*(?P<obj>[^，。；;！!？?]+)")
_INTEREST_RE = re.compile(
    r"(?:对(?P<a>[^，。；;！!？?]+?)\s*(?:很|比较|特别)?感兴趣"
    r"|(?:很|比较|特别|现在|目前)?(?:更)?(?:喜欢|爱|偏好)\s*(?P<b>[^，。；;！!？?]+))"
)
_GOAL_RE = re.compile(
    r"(?:我的|我这阶段的|我目前的)?(?:阶段)?"
    r"(?:目标(?:是|为)?|计划(?:是|为)?|打算|规划|备考|准备)\s*"
    r"(?P<obj>[^，。；;！!？?]+)"
)
#: 明确收回：``不考研了`` 中的对象 ``考研``。只用于「明确变更」路径。
_NEGATED_OBJECT_RE = re.compile(
    r"(?:不再|不打算|不想|不|放弃|取消)"
    r"(?P<obj>[^，。；;！!？?\s]{2,20}?)(?:了|，|。|；|;|$)"
)
#: 明确转向后的新值：``转为准备就业`` 中的 ``准备就业``。
_REPLACEMENT_TAIL_RE = re.compile(
    r"(?:改(?:为|成|做)?|转(?:为|向)?|换(?:成|为)?|准备|打算)\s*"
    r"(?P<obj>[^，。；;！!？?]+)$"
)


def parse_fact_identity(
    text: str,
    *,
    dimension: FourDimension | None = None,
    subject: str = FACT_SUBJECT_USER,
) -> AtomicProfileFactIdentity:
    """从完整事实正文解析主体/关系/对象/范围身份（确定性规则）。

    ``dimension`` 只作解析提示：旧四维抽取丢失关系时（如 ``考研``、
    ``软件工程专业``），维度帮助选择属性槽；身份本身不使用维度作冲突键，
    因此「年纪」与「专业」并存、「考研」与「六级」并存。解析不确定时退回
    ``statement`` 整句事实，宁可并存也不猜测合并。
    """

    normalized = normalize_text(text)
    scope = (
        AtomicProfileFactScope.CURRENT
        if _CURRENT_SCOPE_RE.search(normalized)
        else AtomicProfileFactScope.LONG_TERM
    )
    negated, replacement = parse_explicit_change(normalized)
    if negated and replacement is None:
        # 纯否认不构成新的正事实（"我不考研了"）：按整句陈述保存，让明确
        # 收回走 forget/明确变更路径，绝不把被否认的值当成新的事实。
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.STATEMENT,
            object=normalized,
            scope=scope,
        )
    grade = _GRADE_TOKEN_RE.fullmatch(normalized)
    if grade is not None:
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.GRADE,
            object=grade.group("grade"),
            scope=scope,
        )
    if _MAJOR_HINT_RE.search(normalized) and not (
        _INTEREST_RE.search(normalized) or _LEARNING_RE.search(normalized)
    ):
        major = _MAJOR_HINT_RE.sub("", normalized)
        major = re.sub(r"^(?:我(?:现在|目前)?(?:是|在读|就读|学的是|学的)?)", "", major)
        major = normalize_text(major)
        if major:
            return AtomicProfileFactIdentity(
                subject=subject,
                relation=AtomicProfileFactRelation.MAJOR,
                object=major,
                scope=scope,
            )
    learning = _LEARNING_RE.search(normalized)
    if learning is not None and normalize_text(learning.group("obj")):
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.LEARNING,
            object=normalize_text(learning.group("obj")),
            scope=scope,
        )
    research = _RESEARCH_RE.search(normalized)
    if research is not None and normalize_text(research.group("obj")):
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.RESEARCH,
            object=normalize_text(research.group("obj")),
            scope=scope,
        )
    interest = _INTEREST_RE.search(normalized)
    if interest is not None:
        value = interest.group("a") or interest.group("b") or ""
        if normalize_text(value):
            return AtomicProfileFactIdentity(
                subject=subject,
                relation=AtomicProfileFactRelation.INTEREST,
                object=normalize_text(value),
                scope=scope,
            )
    goal = _GOAL_RE.search(normalized)
    if goal is not None and normalize_text(goal.group("obj")):
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.GOAL,
            object=normalize_text(goal.group("obj")),
            scope=scope,
        )
    if dimension is FourDimension.ACADEMIC_STATUS:
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.IDENTITY,
            object=normalized,
            scope=scope,
        )
    if dimension is FourDimension.STAGE_GOAL:
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.GOAL,
            object=normalized,
            scope=scope,
        )
    if dimension in {FourDimension.KNOWLEDGE_INTEREST, FourDimension.HOBBY}:
        return AtomicProfileFactIdentity(
            subject=subject,
            relation=AtomicProfileFactRelation.INTEREST,
            object=normalized,
            scope=scope,
        )
    return AtomicProfileFactIdentity(
        subject=subject,
        relation=AtomicProfileFactRelation.STATEMENT,
        object=normalized,
        scope=scope,
    )


def fact_identity_key(account_id: str, identity: AtomicProfileFactIdentity) -> str:
    """账户 + 主体/关系/对象/范围事实身份键。"""

    payload = "|".join(
        (
            account_id,
            identity.subject.casefold(),
            identity.relation.value,
            normalize_text(identity.object).casefold(),
            identity.scope.value,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def parse_explicit_change(text: str) -> tuple[list[str], str | None]:
    """解析明确变更语句：被收回的对象列表与转向后的新值。

    ``我不考研了，转为准备就业`` → ``(["考研"], "准备就业")``；只有被收回
    对象而没有新值时新值为空。该解析只服务「用户明确变更」路径，普通自动
    抽取仍由证据与边界检查决定写不写。
    """

    negated = [
        normalize_text(match.group("obj"))
        for match in _NEGATED_OBJECT_RE.finditer(normalize_text(text))
        if normalize_text(match.group("obj"))
    ]
    replacement = _REPLACEMENT_TAIL_RE.search(normalize_text(text))
    new_value = (
        normalize_text(replacement.group("obj")) if replacement is not None else None
    )
    return list(dict.fromkeys(negated)), new_value


def parse_memory_directive(content: str) -> MemoryDirective | None:
    """解析本轮消息里的显式记忆指令；没有明确目标时返回 ``None``。

    只有出现在句首或标点之后的完整指令才算数：``我记住了`` 与 ``别忘掉``
    都不会被当成写入或删除请求。忘掉优先于记住，同一句里只处理最早出现的
    那一条。
    """

    candidates: list[tuple[int, AtomicProfileMemoryKind, str]] = []
    for pattern, kind in (
        (_FORGET_DIRECTIVE_RE, AtomicProfileMemoryKind.FORGET),
        (_REMEMBER_DIRECTIVE_RE, AtomicProfileMemoryKind.REMEMBER),
    ):
        match = pattern.search(content)
        if match is None:
            continue
        value = _DIRECTIVE_TAIL_RE.sub("", match.group("value").strip())
        value = value.lstrip("了").strip()
        if value:
            candidates.append((match.start("value"), kind, value))
    if not candidates:
        return None
    candidates.sort(key=lambda entry: (entry[0], entry[1].value))
    _, kind, target = candidates[0]
    return MemoryDirective(kind, target)


#: 明示时间表达在写入时按来源消息时间锚解析一次（工单 18）：之后一律以绝对
#: 区间判定，使用当天不再重解释「下周」；没有时间表达就不设期限，不编造日期。
_VALIDITY_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*[年\-/]\s*(?P<month>\d{1,2})\s*[月\-/]\s*(?P<day>\d{1,2})\s*日?"
)
_VALIDITY_OFFSET_RE = re.compile(r"(?P<count>\d{1,3})\s*(?P<unit>天|日|周|个?月)\s*后")
_VALIDITY_NEXT_WEEK_RE = re.compile(r"下(?:个)?(?:周|星期)")
_VALIDITY_THIS_WEEK_RE = re.compile(r"(?:本|这)(?:个)?(?:周|星期)")
_VALIDITY_NEXT_MONTH_RE = re.compile(r"下(?:个)?月")
_VALIDITY_TOMORROW_RE = re.compile(r"明天")
_VALIDITY_DAY_AFTER_TOMORROW_RE = re.compile(r"后天")

#: 目标生命周期信号（工单 18）：只按用户明确表达变更，不根据行为推断。
_LIFECYCLE_TARGET_NOISE = (
    "我的",
    "我",
    "自己",
    "已经",
    "现在",
    "目前",
    "终于",
    "马上",
    "就",
    "也",
    "都",
    "要",
    "准备",
    "开始",
)
_PAUSE_SIGNAL_PATTERNS = (
    re.compile(r"(?:暂时|暂|先)\s*不\s*(?P<target>[^，。；;！!？?\s]{1,20})"),
    re.compile(r"暂停\s*(?P<target>[^，。；;！!？?\s]{1,20})"),
)
#: 否定信号（不想/别/没/不打算…）：命中的不是变更声明，不改变目标状态。
_SIGNAL_NEGATION_RE = re.compile(r"(?:不|别|没|莫)\s*(?:(?:想|要|打算|准备|再|用|必|停)\s*)?$")
_COMPLETE_SIGNAL_RE = re.compile(
    r"(?P<target>[^，。；;！!？?\s]{0,20}?)"
    r"(?P<verb>考完(?:试)?|考砸|结\s*束|完\s*成|搞\s*定)了"
)
_RESUME_SIGNAL_RE = re.compile(
    r"(?:继续|恢复|重新(?:开始|准备)?)\s*(?:准备|备考|学习|接着)?\s*"
    r"(?P<target>[^，。；;！!？?]{0,20})"
)


@dataclass(frozen=True)
class ValidityWindow:
    """一段按来源消息时间锚解析出的绝对有效期。"""

    valid_from: datetime
    valid_until: datetime
    phrase: str


class GoalLifecycleKind(StrEnum):
    """目标生命周期信号类型。"""

    PAUSE = "pause"
    COMPLETE = "complete"
    RESUME = "resume"


@dataclass(frozen=True)
class GoalLifecycleSignal:
    """一条明确的目标生命周期信号及其对象。"""

    kind: GoalLifecycleKind
    target: str
    #: 信号本身只可能指目标（如「考完了」）：没有对象时按唯一目标处理。
    target_implied: bool = False


@dataclass(frozen=True)
class GoalLifecycleOutcome:
    """一次目标生命周期变更的结果（不含正文）。"""

    kind: GoalLifecycleKind
    matched_count: int


def _as_aware(anchor: datetime) -> datetime:
    return anchor if anchor.tzinfo is not None else anchor.replace(tzinfo=UTC)


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1) - timedelta(seconds=1)


def _week_bounds(anchor: datetime, *, weeks_ahead: int) -> tuple[datetime, datetime]:
    start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = start - timedelta(days=start.weekday()) + timedelta(weeks=weeks_ahead)
    return monday, monday + timedelta(days=7) - timedelta(seconds=1)


def _add_months(anchor: datetime, months: int) -> datetime:
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def _month_bounds(anchor: datetime, *, months_ahead: int) -> tuple[datetime, datetime]:
    first = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = _add_months(first, months_ahead)
    next_start = _add_months(first, months_ahead + 1)
    return start, next_start - timedelta(seconds=1)


def parse_validity_window(text: str, anchor: datetime) -> ValidityWindow | None:
    """把原文明示的时间表达解析为绝对有效期；没有明示时间时返回空。

    相对时间以 ``anchor``（来源消息时间）为锚，解析一次后落库为绝对区间，
    使用当天不再重解释；没有给年份的日期不猜测年份。未明示期限（如长期偏好）
    不设统一 TTL。
    """

    normalized = normalize_text(text)
    if not normalized:
        return None
    moment = _as_aware(anchor)
    candidates: list[tuple[int, ValidityWindow]] = []

    date_match = _VALIDITY_DATE_RE.search(normalized)
    if date_match is not None:
        try:
            start: datetime | None = datetime(
                int(date_match.group("year")),
                int(date_match.group("month")),
                int(date_match.group("day")),
                tzinfo=moment.tzinfo,
            )
        except ValueError:
            start = None
        if start is not None:
            end = start + timedelta(days=1) - timedelta(microseconds=1)
            candidates.append(
                (date_match.start(), ValidityWindow(start, end, date_match.group(0)))
            )

    offset_match = _VALIDITY_OFFSET_RE.search(normalized)
    if offset_match is not None:
        count = int(offset_match.group("count"))
        unit = offset_match.group("unit")
        if unit in {"天", "日"}:
            _, end = _day_bounds(moment + timedelta(days=count))
        elif unit == "周":
            _, end = _day_bounds(moment + timedelta(weeks=count))
        else:
            _, end = _day_bounds(_add_months(moment, count))
        candidates.append(
            (
                offset_match.start(),
                ValidityWindow(moment, end, offset_match.group(0)),
            )
        )

    next_week = _VALIDITY_NEXT_WEEK_RE.search(normalized)
    if next_week is not None:
        start, end = _week_bounds(moment, weeks_ahead=1)
        candidates.append(
            (next_week.start(), ValidityWindow(start, end, next_week.group(0)))
        )

    this_week = _VALIDITY_THIS_WEEK_RE.search(normalized)
    if this_week is not None:
        start, end = _week_bounds(moment, weeks_ahead=0)
        candidates.append(
            (this_week.start(), ValidityWindow(start, end, this_week.group(0)))
        )

    next_month = _VALIDITY_NEXT_MONTH_RE.search(normalized)
    if next_month is not None:
        start, end = _month_bounds(moment, months_ahead=1)
        candidates.append(
            (next_month.start(), ValidityWindow(start, end, next_month.group(0)))
        )

    tomorrow = _VALIDITY_TOMORROW_RE.search(normalized)
    if tomorrow is not None:
        start, end = _day_bounds(moment + timedelta(days=1))
        candidates.append(
            (tomorrow.start(), ValidityWindow(start, end, tomorrow.group(0)))
        )

    day_after = _VALIDITY_DAY_AFTER_TOMORROW_RE.search(normalized)
    if day_after is not None:
        start, end = _day_bounds(moment + timedelta(days=2))
        candidates.append(
            (day_after.start(), ValidityWindow(start, end, day_after.group(0)))
        )

    # 已经过去的明示时间（如「2023年5月1日入职」）是历史事件时间，不是有效期：
    # 来源消息当时就已结束的区间不能把事实判成过期。相对表达在写入时按来源锚
    # 解析，锚在消息时间上；这里淘汰的只是写入时已经结束的绝对日期。
    usable = [entry for entry in candidates if entry[1].valid_until > moment]
    if not usable:
        return None
    return min(usable, key=lambda entry: entry[0])[1]


def _clean_lifecycle_target(raw: str | None) -> str:
    value = normalize_text(raw or "")
    changed = True
    while changed and value:
        changed = False
        for token in _LIFECYCLE_TARGET_NOISE:
            if value.startswith(token):
                value = value[len(token) :].lstrip("的了")
                changed = True
    return value.strip("的了，。；;！!？? ")


def _is_negated_signal(normalized: str, start: int) -> bool:
    """信号前紧邻否定词（不想继续/别暂停/没考完）：不是变更声明。"""

    prefix = normalized[max(0, start - 6) : start]
    return _SIGNAL_NEGATION_RE.search(prefix) is not None


def parse_goal_lifecycle_signal(text: str) -> GoalLifecycleSignal | None:
    """解析明确的暂停/完成/恢复信号；普通陈述或否定变更不在这里处理。"""

    normalized = normalize_text(text)
    if not normalized:
        return None
    candidates: list[tuple[int, GoalLifecycleSignal]] = []

    for pattern in _PAUSE_SIGNAL_PATTERNS:
        match = pattern.search(normalized)
        if match is None or _is_negated_signal(normalized, match.start()):
            continue
        target = _clean_lifecycle_target(match.group("target"))
        if target:
            candidates.append(
                (match.start(), GoalLifecycleSignal(GoalLifecycleKind.PAUSE, target))
            )

    complete = _COMPLETE_SIGNAL_RE.search(normalized)
    if complete is not None and not _is_negated_signal(normalized, complete.start()):
        target = _clean_lifecycle_target(complete.group("target"))
        # 「考完了/考砸了」本身只可能指考试目标；「完成了/结束了/搞定了」是
        # 通用动词，没有对象时不知道指哪个目标，不能推测成唯一目标完成。
        exam_bound = complete.group("verb").startswith("考")
        if target or exam_bound:
            candidates.append(
                (
                    complete.start(),
                    GoalLifecycleSignal(
                        GoalLifecycleKind.COMPLETE,
                        target,
                        target_implied=not target,
                    ),
                )
            )

    resume = _RESUME_SIGNAL_RE.search(normalized)
    if resume is not None and not _is_negated_signal(normalized, resume.start()):
        target = _clean_lifecycle_target(resume.group("target"))
        candidates.append(
            (
                resume.start(),
                GoalLifecycleSignal(
                    GoalLifecycleKind.RESUME,
                    target,
                    target_implied=not target,
                ),
            )
        )

    if not candidates:
        return None
    return min(candidates, key=lambda entry: entry[0])[1]


def _now() -> datetime:
    return datetime.now(UTC)


def _stable_id(prefix: str, account_id: str, key: str) -> str:
    payload = "|".join([ATOMIC_PROFILE_MIGRATION_VERSION, prefix, account_id, key])
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _new_item_id() -> str:
    return f"item-{secrets.token_urlsafe(16)}"


def _new_feedback_id() -> str:
    return f"profile-feedback-{secrets.token_urlsafe(16)}"


def _validity_status(
    item: AtomicProfileItem, now: datetime
) -> AtomicProfileValidityStatus:
    """把有效期与目标生命周期折算为页面可读的当前时效状态。

    目标暂停/完成优先于期限；已过去的期限显示过期；起点在未来的显示
    尚未生效；没有任何明示期限的长期信息显示未设期限。
    """

    if item.goal_state == AtomicProfileGoalState.PAUSED:
        return AtomicProfileValidityStatus.PAUSED
    if item.goal_state == AtomicProfileGoalState.COMPLETED:
        return AtomicProfileValidityStatus.COMPLETED
    if item.valid_until is not None and item.valid_until <= now:
        return AtomicProfileValidityStatus.EXPIRED
    if item.valid_from is not None and item.valid_from > now:
        return AtomicProfileValidityStatus.SCHEDULED
    if item.valid_from is None and item.valid_until is None:
        return AtomicProfileValidityStatus.UNBOUNDED
    return AtomicProfileValidityStatus.ACTIVE


def _digest(pairs: Iterable[tuple[str, str]]) -> str:
    payload = "|".join(sorted(f"{left}:{right}" for left, right in pairs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bigrams(text: str) -> set[str]:
    value = normalize_text(text).casefold()
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _item_matches_question(text: str, current_question: str | None) -> bool:
    """条目是否与当前问题相关（确定性词面判据，不调用模型）。

    没有当前问题时不做相关性裁剪；有当前问题时按互含、中文二元组重合度与
    拉丁词命中判断，宁可漏掉也不把无关信息塞进本轮上下文。
    """

    if not current_question or not current_question.strip():
        return True
    question = normalize_text(current_question).casefold()
    content = normalize_text(text).casefold()
    if not content:
        return False
    if content in question or question in content:
        return True
    grams = _bigrams(content)
    if grams:
        shared = sum(1 for gram in grams if gram in question)
        if shared >= 2 and shared / len(grams) >= _QUESTION_MATCH_RATIO:
            return True
    return any(
        word in question for word in _CJK_OR_WORD_RE.findall(content) if word.isascii()
    )


def _is_same_turn_extraction(
    item: AtomicProfileItem, current_user_message_id: str | None
) -> bool:
    """本条是否由本轮消息自动整理出来（下一轮才允许进入上下文）。"""

    if current_user_message_id is None:
        return False
    if item.write_origin != AtomicProfileWriteOrigin.AUTOMATIC:
        return False
    return current_user_message_id in item.source_message_ids


#: 可靠度门禁的稳定排除原因：聊天侧据此决定是否提示「需要确认」。
RECALL_LOW_CONFIDENCE_REASON = "可靠程度不足，暂不用于当前回答"


def _recall_exclusion(item: AtomicProfileItem, now: datetime) -> str | None:
    """召回前的有效性检查（工单 18）：返回排除原因或 ``None``。

    已撤回/被替代、仅适用于当时那轮、目标暂停/完成、过期、可靠度不足（且
    不是用户明确编辑）的条目一律不进入本轮切片。用户编辑的权威独立于自动
    可靠度：用户改过的条目即使沿用了旧档位也可召回。
    """

    if item.status != AtomicProfileItemStatus.ACTIVE:
        return "已撤回或失效，不再用于当前回答"
    if item.fact_scope == AtomicProfileFactScope.CURRENT:
        return "仅适用于提出时的那一轮，不作为长期信息"
    if item.goal_state == AtomicProfileGoalState.PAUSED:
        return "目标已暂停，不再作为当前目标使用"
    if item.goal_state == AtomicProfileGoalState.COMPLETED:
        return "目标已完成，不再作为当前目标使用"
    if item.valid_until is not None and item.valid_until <= now:
        return "已过期，不再用于当前回答"
    if not is_recallable_confidence(item.confidence) and item.user_edited_at is None:
        return RECALL_LOW_CONFIDENCE_REASON
    return None


@dataclass(frozen=True)
class _AdoptedCandidate:
    """用途选择阶段的一条候选（排序前的内部结构）。"""

    item: AtomicProfileItem
    tier: int
    decisions: tuple[str, ...]
    reason: str
    conditions: tuple[str, ...]
    is_default: bool


def _authority_rank(item: AtomicProfileItem) -> int:
    """来源权威排序：用户编辑/明确记住的条目优先于自动条目。"""

    if item.user_edited_at is not None or (
        item.write_origin == AtomicProfileWriteOrigin.USER
    ):
        return 0
    return 1


def _slice_sensitivity_for(
    relation: AtomicProfileFactRelation,
) -> ProfileSensitivityClass:
    """按事实关系标注切片敏感度：学习/目标类按学习材料，其余按偏好。"""

    if relation in {
        AtomicProfileFactRelation.IDENTITY,
        AtomicProfileFactRelation.GRADE,
        AtomicProfileFactRelation.MAJOR,
        AtomicProfileFactRelation.LEARNING,
        AtomicProfileFactRelation.RESEARCH,
        AtomicProfileFactRelation.GOAL,
    }:
        return ProfileSensitivityClass.LEARNING
    return ProfileSensitivityClass.PREFERENCE


def _adopted_item(candidate: _AdoptedCandidate) -> AdoptedProfileItem:
    """把排序后的候选折算为采用合同的完整事实条目。"""

    item = candidate.item
    return AdoptedProfileItem(
        profile_item_id=item.profile_item_id,
        version=item.version,
        fact_text=item.text,
        relation=item.fact_relation,
        scope=item.fact_scope,
        source_authority=item.write_origin.value,
        expires_at=item.valid_until,
        applicable_to=candidate.decisions,
        adoption_reason=candidate.reason,
        conditions=candidate.conditions,
        is_default=candidate.is_default,
    )


#: 近义撤回抑制只作用于「同一关系槽、可被普通提及再次表达」的事实；单值
#: 属性槽（年级/专业/身份）的明确变化有替代语义，不在此列。
_SYNONYM_SCOPED_RELATIONS = frozenset(
    {
        AtomicProfileFactRelation.INTEREST,
        AtomicProfileFactRelation.LEARNING,
        AtomicProfileFactRelation.RESEARCH,
        AtomicProfileFactRelation.GOAL,
    }
)
#: 无法语义裁决时按字符重合判断「不确定近义」：达到阈值就暂缓自动写入。
_SYNONYM_CHAR_OVERLAP = 0.5


def _char_overlap(left: str, right: str) -> float:
    left_chars = set(normalize_text(left).casefold())
    right_chars = set(normalize_text(right).casefold())
    if not left_chars or not right_chars:
        return 0.0
    return len(left_chars & right_chars) / min(len(left_chars), len(right_chars))


def _is_near_synonym(left: str, right: str) -> bool:
    """确定性近义候选判据：包含关系或字符重合达到阈值。"""

    left_value = normalize_text(left).casefold()
    right_value = normalize_text(right).casefold()
    if not left_value or not right_value:
        return False
    if left_value in right_value or right_value in left_value:
        return True
    return _char_overlap(left_value, right_value) >= _SYNONYM_CHAR_OVERLAP


def _is_goal_mention(left: str, right: str) -> bool:
    """目标定位用严格判据：相等或包含，不用字符重合。

    目标名常共用一个字（考研/考公/考编），按字符重合定位会把「暂时不考公」
    误伤成「考研」，因此目标只承认相等与包含。
    """

    left_value = normalize_text(left).casefold()
    right_value = normalize_text(right).casefold()
    if not left_value or not right_value:
        return False
    return left_value == right_value or left_value in right_value or right_value in left_value


class ProfileRevocationListener(Protocol):
    """撤回传播的消费方接缝（工单 18）。

    删除/忘掉/纠正提交后由原子画像服务调用；实现方按来源消息定位会话，
    失效依赖该事实的摘要等派生物。监听方失败不影响用户可见的撤回结果。
    """

    def on_profile_revocation(
        self, account_id: str, *, message_ids: list[str], reason: str
    ) -> None: ...


def _item_from_record(
    account_id: str,
    record: FourDimensionProfileRecord,
    *,
    write_origin: AtomicProfileWriteOrigin,
    evidence_message_id: str | None = None,
    migration_run_id: str | None = None,
) -> AtomicProfileItem:
    text = normalize_text(record.content)
    # 旧维度不证明具体关系：裸值按陈述迁入，不能猜成喜欢或正在学习。
    identity = parse_fact_identity(text)
    source = evidence_message_id or record.evidence_message_id
    # 迁移沿用旧记录的首次稳定时间作相对时间的来源锚（工单 18）：一年前的
    # 「下周考试」迁入时即解析为当时的下周，不再按迁移当天重解释。
    validity = parse_validity_window(text, record.first_stable_recorded_at)
    return AtomicProfileItem(
        profile_item_id=_new_item_id(),
        owner_account_id=account_id,
        text=text,
        identity_key=identity_key(account_id, text),
        fact_subject=identity.subject,
        fact_relation=identity.relation,
        fact_object=identity.object,
        fact_scope=identity.scope,
        fact_key=fact_identity_key(account_id, identity),
        evidence_quote=record.evidence_quote,
        valid_from=validity.valid_from if validity else None,
        valid_until=validity.valid_until if validity else None,
        validity_anchor_at=record.first_stable_recorded_at if validity else None,
        validity_phrase=validity.phrase if validity else None,
        source_record_id=record.record_id,
        source_message_ids=[source] if source else [],
        topic_hint=record.dimension.value,
        status=AtomicProfileItemStatus.ACTIVE,
        write_origin=write_origin,
        confidence=record.confidence,
        version=1,
        created_at=record.first_stable_recorded_at,
        updated_at=record.updated_at,
        user_edited_at=None,
        migration_run_id=migration_run_id,
    )


def _dimension_hint(topic_hint: str | None) -> FourDimension | None:
    """把内部 topic_hint 还原为 FourDimension 解析提示；未知值返回空。"""

    if not topic_hint:
        return None
    try:
        return FourDimension(topic_hint)
    except ValueError:
        return None


def _apply_fact_identity(
    item: AtomicProfileItem, identity: AtomicProfileFactIdentity, account_id: str
) -> None:
    """把事实身份写入条目（原地设置四个身份字段与身份键）。"""

    item.fact_subject = identity.subject
    item.fact_relation = identity.relation
    item.fact_object = identity.object
    item.fact_scope = identity.scope
    item.fact_key = fact_identity_key(account_id, identity)


def _apply_validity(
    item: AtomicProfileItem,
    window: ValidityWindow | None,
    anchor: datetime,
) -> None:
    """把新解析出的期限写到条目上；没有新时间表达时保留原期限。"""

    if window is None:
        return
    item.valid_from = window.valid_from
    item.valid_until = window.valid_until
    item.validity_anchor_at = anchor
    item.validity_phrase = window.phrase


def _identity_for_item(item: AtomicProfileItem) -> AtomicProfileFactIdentity:
    """读取条目已存的完整事实身份；旧条目缺身份时按正文重新解析。"""

    if item.fact_key and item.fact_object:
        return AtomicProfileFactIdentity(
            subject=item.fact_subject,
            relation=item.fact_relation,
            object=item.fact_object,
            scope=item.fact_scope,
        )
    return parse_fact_identity(
        item.text, dimension=_dimension_hint(item.topic_hint)
    )


class AtomicProfileRepository(ABC):
    """原子条目与迁移报告的持久化端口。"""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """打开全有或全无的写入事务。"""

    @abstractmethod
    def save_item(self, item: AtomicProfileItem) -> AtomicProfileItem:
        """插入或替换一条账户所有的原子条目。"""

    @abstractmethod
    def get_item(self, owner_id: str, item_id: str) -> AtomicProfileItem:
        """返回原子条目；不存在或越权时使用不泄漏信息的错误。"""

    @abstractmethod
    def find_item_by_identity(
        self, owner_id: str, key: str
    ) -> AtomicProfileItem | None:
        """按文本去重键查找条目（含墓碑，用于抑制复活）。"""

    @abstractmethod
    def find_item_by_fact_key(
        self, owner_id: str, key: str
    ) -> AtomicProfileItem | None:
        """按事实身份键查找条目（含被替代与墓碑，用于合并与抑制）。"""

    @abstractmethod
    def find_item_by_source(
        self, owner_id: str, source_record_id: str
    ) -> AtomicProfileItem | None:
        """按来源四维记录查找镜像条目（含墓碑）。"""

    @abstractmethod
    def list_items(
        self, owner_id: str, *, include_withdrawn: bool = False
    ) -> list[AtomicProfileItem]:
        """列出单个账户内的原子条目。"""

    @abstractmethod
    def delete_item(self, owner_id: str, item_id: str) -> None:
        """物理删除一条条目（值被更新时退休旧条目）。"""

    @abstractmethod
    def delete_items_by_run(self, owner_id: str, run_id: str) -> list[str]:
        """删除某个迁移批次新建的条目，返回被删除的条目标识。"""

    @abstractmethod
    def save_feedback(self, feedback: AtomicProfileFeedback) -> AtomicProfileFeedback:
        """保存一条画像依据反馈（同账户同条目同类别幂等由调用方保证）。"""

    @abstractmethod
    def find_feedback(
        self, owner_id: str, item_id: str, kind: AtomicProfileFeedbackKind
    ) -> AtomicProfileFeedback | None:
        """按账户、条目与类别查找已有反馈（幂等重放返回既有记录）。"""

    @abstractmethod
    def list_feedback(
        self, owner_id: str, item_id: str
    ) -> list[AtomicProfileFeedback]:
        """列出某条目的反馈，最新的在前。"""

    @abstractmethod
    def save_migration_report(
        self, report: AtomicProfileMigrationReport
    ) -> AtomicProfileMigrationReport:
        """保存账户级迁移报告。"""

    @abstractmethod
    def get_migration_report(
        self, owner_id: str, run_id: str
    ) -> AtomicProfileMigrationReport | None:
        """按批次返回迁移报告。"""

    @abstractmethod
    def get_latest_migration_report(
        self, owner_id: str
    ) -> AtomicProfileMigrationReport | None:
        """返回单个账户最新的迁移报告。"""

    def snapshot_before_migration(self) -> str | None:
        """迁移前落一份一致性快照，返回可恢复的备份标识。

        默认实现不做任何事：内存仓库没有需要保护的文件。可持久化实现覆盖
        本方法，让「迁移前有还原点」成为仓库端口的一部分，而不是调用方的
        约定。
        """

        return None


class InMemoryAtomicProfileRepository(AtomicProfileRepository):
    """供领域与 API 测试使用的确定性内存仓库。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], AtomicProfileItem] = {}
        self._reports: dict[tuple[str, str], AtomicProfileMigrationReport] = {}
        self._feedback: dict[tuple[str, str], AtomicProfileFeedback] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = (
            copy.deepcopy(self._items),
            copy.deepcopy(self._reports),
            copy.deepcopy(self._feedback),
        )
        try:
            yield
        except BaseException:
            self._items, self._reports, self._feedback = snapshot
            raise

    @staticmethod
    def _key(owner_id: str, object_id: str) -> tuple[str, str]:
        return owner_id, object_id

    def save_item(self, item: AtomicProfileItem) -> AtomicProfileItem:
        self._items[self._key(item.owner_account_id, item.profile_item_id)] = item
        return item

    def get_item(self, owner_id: str, item_id: str) -> AtomicProfileItem:
        item = self._items.get(self._key(owner_id, item_id))
        if item is None:
            raise AtomicProfileError("对象不存在或没有访问权限。")
        return item

    def find_item_by_identity(
        self, owner_id: str, key: str
    ) -> AtomicProfileItem | None:
        return next(
            (
                item
                for item in self._items.values()
                if item.owner_account_id == owner_id and item.identity_key == key
            ),
            None,
        )

    def find_item_by_fact_key(
        self, owner_id: str, key: str
    ) -> AtomicProfileItem | None:
        return next(
            (
                item
                for item in self._items.values()
                if item.owner_account_id == owner_id
                and item.fact_key
                and item.fact_key == key
            ),
            None,
        )

    def find_item_by_source(
        self, owner_id: str, source_record_id: str
    ) -> AtomicProfileItem | None:
        return max(
            (
                item
                for item in self._items.values()
                if item.owner_account_id == owner_id
                and item.source_record_id == source_record_id
            ),
            key=lambda item: (item.status == AtomicProfileItemStatus.ACTIVE, item.updated_at),
            default=None,
        )

    def list_items(
        self, owner_id: str, *, include_withdrawn: bool = False
    ) -> list[AtomicProfileItem]:
        items = [
            item
            for item in self._items.values()
            if item.owner_account_id == owner_id
            and (include_withdrawn or item.status == AtomicProfileItemStatus.ACTIVE)
        ]
        items.sort(
            key=lambda item: (item.updated_at, item.profile_item_id), reverse=True
        )
        return items

    def delete_item(self, owner_id: str, item_id: str) -> None:
        self._items.pop(self._key(owner_id, item_id), None)

    def delete_items_by_run(self, owner_id: str, run_id: str) -> list[str]:
        removed = [
            item.profile_item_id
            for item in self._items.values()
            if item.owner_account_id == owner_id and item.migration_run_id == run_id
        ]
        for item_id in removed:
            self._items.pop(self._key(owner_id, item_id), None)
        return removed

    def save_feedback(self, feedback: AtomicProfileFeedback) -> AtomicProfileFeedback:
        # 与 SQLite 的唯一约束保持一致：同账户同条目同类别只保留第一条。
        if (
            self.find_feedback(
                feedback.owner_account_id, feedback.profile_item_id, feedback.kind
            )
            is not None
        ):
            return feedback
        self._feedback[self._key(feedback.owner_account_id, feedback.feedback_id)] = (
            feedback
        )
        return feedback

    def find_feedback(
        self, owner_id: str, item_id: str, kind: AtomicProfileFeedbackKind
    ) -> AtomicProfileFeedback | None:
        return next(
            (
                feedback
                for feedback in self._feedback.values()
                if feedback.owner_account_id == owner_id
                and feedback.profile_item_id == item_id
                and feedback.kind == kind
            ),
            None,
        )

    def list_feedback(
        self, owner_id: str, item_id: str
    ) -> list[AtomicProfileFeedback]:
        feedback = [
            entry
            for entry in self._feedback.values()
            if entry.owner_account_id == owner_id
            and entry.profile_item_id == item_id
        ]
        feedback.sort(
            key=lambda entry: (entry.created_at, entry.feedback_id), reverse=True
        )
        return feedback

    def save_migration_report(
        self, report: AtomicProfileMigrationReport
    ) -> AtomicProfileMigrationReport:
        self._reports[self._key(report.owner_account_id, report.run_id)] = report
        return report

    def get_migration_report(
        self, owner_id: str, run_id: str
    ) -> AtomicProfileMigrationReport | None:
        return self._reports.get(self._key(owner_id, run_id))

    def get_latest_migration_report(
        self, owner_id: str
    ) -> AtomicProfileMigrationReport | None:
        """返回账户最近一次写入的迁移报告（按写入顺序，不依赖系统时钟精度）。"""

        for key in reversed(list(self._reports)):
            report = self._reports[key]
            if report.owner_account_id == owner_id:
                return report
        return None


class SqliteAtomicProfileRepository(AtomicProfileRepository):
    """SQLite 原子画像仓库；每条语句都按账户作用域执行。"""

    def __init__(self, database: BridgesDatabase, *, initialize: bool = True) -> None:
        self._db = database
        if initialize:
            self._db.initialize()

    def transaction(self) -> AbstractContextManager[None]:
        """事务边界；已在调用方事务内时并入外层，不重复 BEGIN。

        单连接 SQLite 不支持嵌套事务：自动抽取在写四维记录的事务里镜像原子
        条目，因此这里的写入必须与调用方同属一次提交（失败一起回滚）。
        """

        return joined_transaction(self._db)

    def snapshot_before_migration(self) -> str | None:
        """用 SQLite 在线备份 API 落一份迁移前快照，返回备份文件路径。

        与库级迁移门同一套原语（``snapshot_lock`` + ``snapshot_to``）：WAL
        库用文件拷贝会读到过期快照，因此必须在快照锁内走备份 API。文件名带
        固定前缀与秒级时间戳，重复迁移只保留最新一份，避免每次点按都堆积
        几 MB 的副本；快照在清理旧副本之前落盘，失败时降级为告警并返回空值
        ——单批迁移本身仍受事务保护（失败整体回滚），调用方据空值知道本次
        没有额外还原点。
        """

        path = self._db.path
        if path == ":memory:":
            return None
        target = Path(path).with_name(
            f"{Path(path).name}.backup-before-atomic-profile-"
            f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )
        try:
            with self._db.snapshot_lock():
                self._db.snapshot_to(target)
        except (OSError, StorageError) as exc:
            logger.warning(
                "atomic_profile_migration_snapshot_failed",
                extra={"target": target.name, "error": str(exc)},
            )
            return None
        for stale in Path(path).parent.glob(
            f"{Path(path).name}.backup-before-atomic-profile-*"
        ):
            if stale != target:
                try:
                    stale.unlink()
                except OSError:
                    continue
        return str(target)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _item_from_row(row: object) -> AtomicProfileItem:
        return AtomicProfileItem(
            profile_item_id=str(row["profile_item_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            text=str(row["text"]),  # type: ignore[index]
            identity_key=str(row["identity_key"]),  # type: ignore[index]
            fact_subject=str(row["fact_subject"]),  # type: ignore[index]
            fact_relation=AtomicProfileFactRelation(
                str(row["fact_relation"])  # type: ignore[index]
            ),
            fact_object=str(row["fact_object"]),  # type: ignore[index]
            fact_scope=AtomicProfileFactScope(str(row["fact_scope"])),  # type: ignore[index]
            fact_key=str(row["fact_key"]),  # type: ignore[index]
            evidence_quote=(
                str(row["evidence_quote"])  # type: ignore[index]
                if row["evidence_quote"] is not None  # type: ignore[index]
                else None
            ),
            valid_from=(
                SqliteAtomicProfileRepository._dt(str(row["valid_from"]))  # type: ignore[index]
                if row["valid_from"] is not None  # type: ignore[index]
                else None
            ),
            valid_until=(
                SqliteAtomicProfileRepository._dt(str(row["valid_until"]))  # type: ignore[index]
                if row["valid_until"] is not None  # type: ignore[index]
                else None
            ),
            validity_anchor_at=(
                SqliteAtomicProfileRepository._dt(
                    str(row["validity_anchor_at"])  # type: ignore[index]
                )
                if row["validity_anchor_at"] is not None  # type: ignore[index]
                else None
            ),
            validity_phrase=(
                str(row["validity_phrase"])  # type: ignore[index]
                if row["validity_phrase"] is not None  # type: ignore[index]
                else None
            ),
            goal_state=AtomicProfileGoalState(
                str(row["goal_state"])  # type: ignore[index]
            ),
            supersedes_id=(
                str(row["supersedes_id"])  # type: ignore[index]
                if row["supersedes_id"] is not None  # type: ignore[index]
                else None
            ),
            superseded_by_id=(
                str(row["superseded_by_id"])  # type: ignore[index]
                if row["superseded_by_id"] is not None  # type: ignore[index]
                else None
            ),
            source_record_id=(
                str(row["source_record_id"])  # type: ignore[index]
                if row["source_record_id"] is not None  # type: ignore[index]
                else None
            ),
            source_message_ids=[
                str(value)
                for value in json.loads(str(row["source_message_ids_json"]))  # type: ignore[index]
            ],
            topic_hint=(
                str(row["topic_hint"])  # type: ignore[index]
                if row["topic_hint"] is not None  # type: ignore[index]
                else None
            ),
            status=AtomicProfileItemStatus(str(row["status"])),  # type: ignore[index]
            write_origin=AtomicProfileWriteOrigin(str(row["write_origin"])),  # type: ignore[index]
            confidence=FourDimensionConfidence(str(row["confidence"])),  # type: ignore[index]
            version=int(row["version"]),  # type: ignore[index]
            created_at=SqliteAtomicProfileRepository._dt(
                str(row["created_at"])  # type: ignore[index]
            ),
            updated_at=SqliteAtomicProfileRepository._dt(
                str(row["updated_at"])  # type: ignore[index]
            ),
            user_edited_at=(
                SqliteAtomicProfileRepository._dt(str(row["user_edited_at"]))  # type: ignore[index]
                if row["user_edited_at"] is not None  # type: ignore[index]
                else None
            ),
            migration_run_id=(
                str(row["migration_run_id"])  # type: ignore[index]
                if row["migration_run_id"] is not None  # type: ignore[index]
                else None
            ),
        )

    def save_item(self, item: AtomicProfileItem) -> AtomicProfileItem:
        self._db.scoped(item.owner_account_id).execute(
            "INSERT INTO profile_items ("
            "profile_item_id, account_id, text, identity_key, fact_subject, "
            "fact_relation, fact_object, fact_scope, fact_key, evidence_quote, "
            "valid_from, valid_until, validity_anchor_at, validity_phrase, goal_state, "
            "supersedes_id, superseded_by_id, source_record_id, "
            "source_message_ids_json, topic_hint, status, write_origin, confidence, "
            "version, created_at, updated_at, user_edited_at, migration_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_item_id) DO UPDATE SET text = excluded.text, "
            "identity_key = excluded.identity_key, fact_subject = excluded.fact_subject, "
            "fact_relation = excluded.fact_relation, fact_object = excluded.fact_object, "
            "fact_scope = excluded.fact_scope, fact_key = excluded.fact_key, "
            "evidence_quote = excluded.evidence_quote, "
            "valid_from = excluded.valid_from, "
            "valid_until = excluded.valid_until, "
            "validity_anchor_at = excluded.validity_anchor_at, "
            "validity_phrase = excluded.validity_phrase, "
            "goal_state = excluded.goal_state, "
            "supersedes_id = excluded.supersedes_id, "
            "superseded_by_id = excluded.superseded_by_id, "
            "source_record_id = excluded.source_record_id, "
            "source_message_ids_json = excluded.source_message_ids_json, "
            "topic_hint = excluded.topic_hint, status = excluded.status, "
            "write_origin = excluded.write_origin, confidence = excluded.confidence, "
            "version = excluded.version, created_at = excluded.created_at, "
            "updated_at = excluded.updated_at, user_edited_at = excluded.user_edited_at, "
            "migration_run_id = excluded.migration_run_id "
            "WHERE account_id = excluded.account_id",
            (
                item.profile_item_id,
                item.owner_account_id,
                item.text,
                item.identity_key,
                item.fact_subject,
                item.fact_relation.value,
                item.fact_object,
                item.fact_scope.value,
                item.fact_key,
                item.evidence_quote,
                self._iso(item.valid_from) if item.valid_from else None,
                self._iso(item.valid_until) if item.valid_until else None,
                self._iso(item.validity_anchor_at) if item.validity_anchor_at else None,
                item.validity_phrase,
                item.goal_state.value,
                item.supersedes_id,
                item.superseded_by_id,
                item.source_record_id,
                json.dumps(item.source_message_ids, ensure_ascii=False),
                item.topic_hint,
                item.status.value,
                item.write_origin.value,
                item.confidence.value,
                item.version,
                self._iso(item.created_at),
                self._iso(item.updated_at),
                self._iso(item.user_edited_at) if item.user_edited_at else None,
                item.migration_run_id,
            ),
        )
        return item

    def get_item(self, owner_id: str, item_id: str) -> AtomicProfileItem:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ? AND profile_item_id = ?",
            (owner_id, item_id),
        ).fetchone()
        if row is None:
            raise AtomicProfileError("对象不存在或没有访问权限。")
        return self._item_from_row(row)

    def find_item_by_identity(self, owner_id: str, key: str) -> AtomicProfileItem | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ? AND identity_key = ?",
            (owner_id, key),
        ).fetchone()
        return self._item_from_row(row) if row is not None else None

    def find_item_by_fact_key(self, owner_id: str, key: str) -> AtomicProfileItem | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ? AND fact_key = ? "
            "AND fact_key <> '' ORDER BY updated_at DESC LIMIT 1",
            (owner_id, key),
        ).fetchone()
        return self._item_from_row(row) if row is not None else None

    def find_item_by_source(
        self, owner_id: str, source_record_id: str
    ) -> AtomicProfileItem | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ? AND source_record_id = ? "
            "ORDER BY (status = 'active') DESC, updated_at DESC LIMIT 1",
            (owner_id, source_record_id),
        ).fetchone()
        return self._item_from_row(row) if row is not None else None

    def list_items(
        self, owner_id: str, *, include_withdrawn: bool = False
    ) -> list[AtomicProfileItem]:
        status_clause = "" if include_withdrawn else " AND status = 'active'"
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ?"
            + status_clause
            + " ORDER BY updated_at DESC, profile_item_id DESC",
            (owner_id,),
        ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def delete_item(self, owner_id: str, item_id: str) -> None:
        self._db.scoped(owner_id).execute(
            "DELETE FROM profile_items WHERE account_id = ? AND profile_item_id = ?",
            (owner_id, item_id),
        )

    def delete_items_by_run(self, owner_id: str, run_id: str) -> list[str]:
        scoped = self._db.scoped(owner_id)
        rows = scoped.execute(
            "SELECT profile_item_id FROM profile_items "
            "WHERE account_id = ? AND migration_run_id = ?",
            (owner_id, run_id),
        ).fetchall()
        removed = [str(row["profile_item_id"]) for row in rows]
        scoped.execute(
            "DELETE FROM profile_items WHERE account_id = ? AND migration_run_id = ?",
            (owner_id, run_id),
        )
        return removed

    @staticmethod
    def _feedback_from_row(row: object) -> AtomicProfileFeedback:
        return AtomicProfileFeedback(
            feedback_id=str(row["feedback_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
            profile_item_id=str(row["profile_item_id"]),  # type: ignore[index]
            item_version=int(row["item_version"]),  # type: ignore[index]
            kind=AtomicProfileFeedbackKind(str(row["kind"])),  # type: ignore[index]
            note=(
                str(row["note"])  # type: ignore[index]
                if row["note"] is not None  # type: ignore[index]
                else None
            ),
            created_at=SqliteAtomicProfileRepository._dt(
                str(row["created_at"])  # type: ignore[index]
            ),
        )

    def save_feedback(self, feedback: AtomicProfileFeedback) -> AtomicProfileFeedback:
        self._db.scoped(feedback.owner_account_id).execute(
            "INSERT INTO profile_item_feedback ("
            "feedback_id, account_id, profile_item_id, kind, note, item_version, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, profile_item_id, kind) DO NOTHING",
            (
                feedback.feedback_id,
                feedback.owner_account_id,
                feedback.profile_item_id,
                feedback.kind.value,
                feedback.note,
                feedback.item_version,
                self._iso(feedback.created_at),
            ),
        )
        return feedback

    def find_feedback(
        self, owner_id: str, item_id: str, kind: AtomicProfileFeedbackKind
    ) -> AtomicProfileFeedback | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_item_feedback "
            "WHERE account_id = ? AND profile_item_id = ? AND kind = ?",
            (owner_id, item_id, kind.value),
        ).fetchone()
        return self._feedback_from_row(row) if row is not None else None

    def list_feedback(
        self, owner_id: str, item_id: str
    ) -> list[AtomicProfileFeedback]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_item_feedback "
            "WHERE account_id = ? AND profile_item_id = ? "
            "ORDER BY created_at DESC, feedback_id DESC",
            (owner_id, item_id),
        ).fetchall()
        return [self._feedback_from_row(row) for row in rows]

    def _report_from_row(self, row: object) -> AtomicProfileMigrationReport:
        """还原完整报告：计数字段来自报告行，逐条明细来自同批次台账。"""

        run_id = str(row["run_id"])  # type: ignore[index]
        owner_id = str(row["account_id"])  # type: ignore[index]
        return AtomicProfileMigrationReport(
            run_id=run_id,
            owner_account_id=owner_id,
            migration_version=str(row["migration_version"]),  # type: ignore[index]
            status=AtomicProfileMigrationStatus(str(row["status"])),  # type: ignore[index]
            migrated=int(row["migrated"]),  # type: ignore[index]
            duplicated=int(row["duplicated"]),  # type: ignore[index]
            tombstoned=int(row["tombstoned"]),  # type: ignore[index]
            skipped=int(row["skipped"]),  # type: ignore[index]
            failed=int(row["failed"]),  # type: ignore[index]
            created_item_ids=[
                str(value)
                for value in json.loads(str(row["created_item_ids_json"]))  # type: ignore[index]
            ],
            source_record_ids=[
                str(value)
                for value in json.loads(str(row["source_record_ids_json"]))  # type: ignore[index]
            ],
            reconciliation_digest=str(row["reconciliation_digest"]),  # type: ignore[index]
            reconciliation=self._reconciliation_for_run(owner_id, run_id),
            retryable=bool(int(row["retryable"])),  # type: ignore[index]
            identity_backfilled=int(row["identity_backfilled"]),  # type: ignore[index]
            created_at=SqliteAtomicProfileRepository._dt(
                str(row["created_at"])  # type: ignore[index]
            ),
            undone_at=(
                SqliteAtomicProfileRepository._dt(str(row["undone_at"]))  # type: ignore[index]
                if row["undone_at"] is not None  # type: ignore[index]
                else None
            ),
        )

    @staticmethod
    def _reconciliation_from_row(row: object) -> AtomicProfileReconciliationEntry:
        return AtomicProfileReconciliationEntry(
            source_record_id=str(row["source_record_id"]),  # type: ignore[index]
            outcome=AtomicProfileReconciliationOutcome(str(row["outcome"])),  # type: ignore[index]
            reason_code=str(row["reason_code"]),  # type: ignore[index]
            profile_item_id=(
                str(row["profile_item_id"])  # type: ignore[index]
                if row["profile_item_id"] is not None  # type: ignore[index]
                else None
            ),
        )

    def _reconciliation_for_run(
        self, owner_id: str, run_id: str
    ) -> list[AtomicProfileReconciliationEntry]:
        """按来源记录顺序返回某批次的逐条对账明细。

        顺序即写入顺序：同一批次内 source_record_id 不会重复（主键约束），
        因此按 rowid 读取即可复现迁移时的逐条顺序。
        """

        rows = self._db.scoped(owner_id).execute(
            "SELECT source_record_id, outcome, reason_code, profile_item_id "
            "FROM profile_item_migration_records "
            "WHERE account_id = ? AND run_id = ? ORDER BY rowid",
            (owner_id, run_id),
        ).fetchall()
        return [
            SqliteAtomicProfileRepository._reconciliation_from_row(row) for row in rows
        ]

    def save_migration_report(
        self, report: AtomicProfileMigrationReport
    ) -> AtomicProfileMigrationReport:
        scoped = self._db.scoped(report.owner_account_id)
        scoped.execute(
            "INSERT INTO profile_item_migrations ("
            "run_id, account_id, migration_version, status, migrated, duplicated, "
            "tombstoned, skipped, failed, created_item_ids_json, source_record_ids_json, "
            "reconciliation_digest, retryable, identity_backfilled, created_at, "
            "undone_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
            "migrated = excluded.migrated, duplicated = excluded.duplicated, "
            "tombstoned = excluded.tombstoned, skipped = excluded.skipped, "
            "failed = excluded.failed, "
            "created_item_ids_json = excluded.created_item_ids_json, "
            "source_record_ids_json = excluded.source_record_ids_json, "
            "reconciliation_digest = excluded.reconciliation_digest, "
            "retryable = excluded.retryable, "
            "identity_backfilled = excluded.identity_backfilled, "
            "undone_at = excluded.undone_at "
            "WHERE account_id = excluded.account_id",
            (
                report.run_id,
                report.owner_account_id,
                report.migration_version,
                report.status.value,
                report.migrated,
                report.duplicated,
                report.tombstoned,
                report.skipped,
                report.failed,
                json.dumps(report.created_item_ids, ensure_ascii=False),
                json.dumps(report.source_record_ids, ensure_ascii=False),
                report.reconciliation_digest,
                1 if report.retryable else 0,
                report.identity_backfilled,
                self._iso(report.created_at),
                self._iso(report.undone_at) if report.undone_at else None,
            ),
        )
        # 逐条对账明细与报告同事务落盘：报告被回滚时明细一起消失，不会出现
        # 「有明细没有报告」的半状态。同一批次重复保存（如回滚改写状态）用
        # UPSERT 覆盖，不产生重复行。
        for entry in report.reconciliation:
            scoped.execute(
                "INSERT INTO profile_item_migration_records ("
                "run_id, account_id, source_record_id, outcome, reason_code, "
                "profile_item_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, account_id, source_record_id) DO UPDATE SET "
                "outcome = excluded.outcome, reason_code = excluded.reason_code, "
                "profile_item_id = excluded.profile_item_id",
                (
                    report.run_id,
                    report.owner_account_id,
                    entry.source_record_id,
                    entry.outcome.value,
                    entry.reason_code,
                    entry.profile_item_id,
                    self._iso(report.created_at),
                ),
            )
        return report

    def get_migration_report(
        self, owner_id: str, run_id: str
    ) -> AtomicProfileMigrationReport | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_item_migrations WHERE account_id = ? AND run_id = ?",
            (owner_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return self._report_from_row(row)

    def get_latest_migration_report(
        self, owner_id: str
    ) -> AtomicProfileMigrationReport | None:
        """返回账户最近一次写入的迁移报告（按写入顺序，不依赖系统时钟精度）。"""

        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_item_migrations "
            "WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
            (owner_id,),
        ).fetchone()
        if row is None:
            return None
        return self._report_from_row(row)


class AtomicProfileService:
    """原子条目的读写、记忆指令、切片编译、账户级迁移与来源撤回恢复。

    失败是否越过墓碑保护点、能否重试、恢复后的可见结果由提交 module 定义
    （见 :mod:`bridges.profiles.commit`）：墓碑提交之后的来源撤回失败不回滚
    删除，:meth:`recover_source_withdrawals` 只凭持久墓碑幂等补齐。
    """

    def __init__(
        self,
        four_dimensions: FourDimensionProfileService,
        repository: AtomicProfileRepository,
        profile_commit: ProfileCommit | None = None,
        message_reader: ProfileEvidenceSourceReader | None = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self._four_dimensions = four_dimensions
        self._repository = repository
        # 改进工单 20：依据展开按账户读取来源消息快照（组合根注入聊天仓库
        # 适配器）；未注入时来源如实标记为不可读，不伪造定位与原话。
        self._message_reader = message_reader
        self._observability = observability_service
        # 改进工单 18：撤回传播的消费方（摘要等派生物失效）在组合根注入；
        # 未注入时撤回本身照常成立，只是没有下游通知。
        self._revocation_listener: ProfileRevocationListener | None = None
        # Issue 05：用户权威操作的跨记录顺序（墓碑先落地、再撤回来源）由
        # 提交 module 归属；自动提取编排器注入同一个实例时，两侧共用同一
        # 份提交规则。未注入时自建只挂载跨记录两方的实例。
        self._profile_commit = profile_commit or ProfileCommit(
            records=four_dimensions,
            items=self,
        )

    def set_revocation_listener(
        self, listener: ProfileRevocationListener | None
    ) -> None:
        """挂载/替换撤回传播监听（组合根在摘要服务就绪后注入）。"""

        self._revocation_listener = listener

    @staticmethod
    def _revocation_version_for(items: Iterable[AtomicProfileItem]) -> str:
        """由一组条目折算撤回版本；编译切片时与切片共用同一份快照。"""

        pairs = [
            (item.profile_item_id, f"{item.version}:{item.status.value}")
            for item in items
        ]
        return _digest(pairs)[:32]

    def revocation_version(self, account_id: str) -> str:
        """账户级撤回版本：任何条目写入/撤回/替代都会改变它。

        编译切片时捕获该值；后续调用或复用前重新核对，撤回后旧切片立即
        失效。版本由持久条目确定性折算，不新增时钟或计数器状态。
        """

        return self._revocation_version_for(
            self._repository.list_items(account_id, include_withdrawn=True)
        )

    def is_slice_current(
        self, account_id: str, revocation_version: str | None
    ) -> bool:
        """切片编译时捕获的撤回版本是否仍然一致；空值视为无版本约束。"""

        if revocation_version is None:
            return True
        return self.revocation_version(account_id) == revocation_version

    def transaction(self) -> AbstractContextManager[None]:
        """为批量提交暴露原子仓库的事务边界。

        单元写入自己开事务；跨记录提交（自动提取的四维记录 + 原子镜像）
        需要由调用方在同一个边界内写入，因此这里把仓库边界交给提交 module。
        """

        return self._repository.transaction()

    def is_adopted_slice_current(
        self, account_id: str, adopted: AdoptedProfileSlice, *, now: datetime | None = None
    ) -> bool:
        """只核对冻结版本中的条目；新增不改变本轮，删除/纠正/到期使其失效。"""

        if (
            adopted.owner_account_id != account_id
            or adopted.compiled_policy_version != "purpose-slice-1.1"
        ):
            return False
        snapshot = self._repository.list_items(account_id, include_withdrawn=True)
        current = {item.profile_item_id: item for item in snapshot}
        for item_id, version, status in adopted.snapshot_versions:
            item = current.get(item_id)
            if item is None or (item.version, item.status.value) != (version, status):
                return False
        moment = now or _now()
        return all(
            entry.profile_item_id in current
            and _recall_exclusion(current[entry.profile_item_id], moment) is None
            for entry in adopted.adopted_items
        )

    # -- 读 ---------------------------------------------------------------

    def list_items(self, account_id: str) -> list[AtomicProfileItem]:
        return self._repository.list_items(account_id)

    def get_item(self, account_id: str, item_id: str) -> AtomicProfileItem:
        return self._repository.get_item(account_id, item_id)

    def projections(self, account_id: str) -> list[AtomicProfileItemProjection]:
        """页面投影列表（无类别、无分组、无内部哈希）。"""

        return [self.project(item) for item in self.list_items(account_id)]

    @staticmethod
    def project(item: AtomicProfileItem) -> AtomicProfileItemProjection:
        """把单条条目收敛为页面投影。"""

        return AtomicProfileItemProjection(
            profile_item_id=item.profile_item_id,
            text=item.text,
            version=item.version,
            updated_at=item.updated_at,
            user_edited_at=item.user_edited_at,
            source_message_ids=list(item.source_message_ids),
            # 原话仅由按需依据接口核验后返回，列表不复制可能已失效的来源内容。
            evidence_quote=None,
            write_origin=item.write_origin,
            supersedes_id=item.supersedes_id,
            # 工单 18：页面按需展示明示期限与目标状态（长期偏好两项都为空/
            # active，不因没有 TTL 被误读为已过期）。
            valid_from=item.valid_from,
            valid_until=item.valid_until,
            validity_phrase=item.validity_phrase,
            goal_state=item.goal_state,
        )

    # -- 依据展开与反馈（工单 20） ----------------------------------------

    @staticmethod
    def feedback_projection(
        feedback: AtomicProfileFeedback,
    ) -> AtomicProfileFeedbackProjection:
        """把反馈记录折算为页面投影；文案与效果是确定性映射。"""

        return AtomicProfileFeedbackProjection(
            feedback_id=feedback.feedback_id,
            profile_item_id=feedback.profile_item_id,
            kind=feedback.kind,
            effect=feedback_effect_for(feedback.kind),
            message=feedback_message_for(feedback.kind),
            note=feedback.note,
            created_at=feedback.created_at,
        )

    def item_evidence(
        self,
        account_id: str,
        item_id: str,
        *,
        now: datetime | None = None,
    ) -> AtomicProfileItemEvidenceProjection:
        """按需展开一条画像信息的依据、时效与反馈。

        只读取本条数据：条目的来源消息逐个按账户查可得状态；消息已删除或
        不可读时如实标注。合同要求删除聊天原文时同步失效其派生原话副本，
        因此没有可读来源时不再回传保存的原话（标为 ``source_unavailable``），
        绝不伪造引语或跨账户定位。
        """

        item = self._repository.get_item(account_id, item_id)
        if item.status != AtomicProfileItemStatus.ACTIVE:
            raise AtomicProfileError("画像条目已删除，没有可展开的依据。")
        moment = now or _now()
        messages = {
            message_id: self._message_reader(account_id, message_id)
            if self._message_reader is not None else None
            for message_id in item.source_message_ids
        }
        sources = [
            self._evidence_source(message_id, message)
            for message_id, message in messages.items()
        ]
        feedback = [
            self.feedback_projection(entry)
            for entry in self._repository.list_feedback(account_id, item_id)
        ]
        evidence_quote = item.evidence_quote
        has_matching_source = any(
            source.status == AtomicProfileEvidenceSourceStatus.AVAILABLE
            and (message := messages[source.message_id]) is not None
            and evidence_quote in message.content
            for source in sources
        ) if evidence_quote else False
        if evidence_quote and not has_matching_source:
            # 必须核对原话仍在可读来源中；其他来源存活不能保留已删消息的副本。
            evidence_quote = None
            evidence_quote_status = (
                AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
            )
        elif evidence_quote:
            evidence_quote_status = AtomicProfileEvidenceQuoteStatus.RECORDED
        else:
            evidence_quote_status = AtomicProfileEvidenceQuoteStatus.NOT_RECORDED
        return AtomicProfileItemEvidenceProjection(
            profile_item_id=item.profile_item_id,
            version=item.version,
            text=item.text,
            write_origin=item.write_origin,
            user_edited_at=item.user_edited_at,
            updated_at=item.updated_at,
            fact_scope=item.fact_scope,
            goal_state=item.goal_state,
            valid_from=item.valid_from,
            valid_until=item.valid_until,
            validity_phrase=item.validity_phrase,
            validity_status=_validity_status(item, moment),
            evidence_quote=evidence_quote,
            evidence_quote_status=evidence_quote_status,
            sources=sources,
            feedback=feedback,
        )

    def _evidence_source(
        self, message_id: str, message: ProfileSourceMessage | None
    ) -> AtomicProfileEvidenceSource:
        """解析一条来源消息的可得状态；只有可读用户消息给出定位信息。"""

        if self._message_reader is None:
            return AtomicProfileEvidenceSource(
                message_id=message_id,
                status=AtomicProfileEvidenceSourceStatus.UNREADABLE,
            )
        if message is None:
            return AtomicProfileEvidenceSource(
                message_id=message_id,
                status=AtomicProfileEvidenceSourceStatus.DELETED,
            )
        readable = (
            message.role == "user"
            and message.status == "done"
            and bool(message.content.strip())
        )
        if not readable:
            return AtomicProfileEvidenceSource(
                message_id=message_id,
                status=AtomicProfileEvidenceSourceStatus.UNREADABLE,
                created_at=message.created_at,
            )
        return AtomicProfileEvidenceSource(
            message_id=message_id,
            status=AtomicProfileEvidenceSourceStatus.AVAILABLE,
            conversation_id=message.conversation_id,
            created_at=message.created_at,
        )

    def record_feedback(
        self,
        account_id: str,
        item_id: str,
        request: AtomicProfileItemFeedbackRequest,
    ) -> AtomicProfileFeedbackProjection:
        """记录一条四类反馈；反馈本身绝不修改或删除条目。"""

        note = normalize_text(request.note) if request.note else None
        with self._repository.transaction():
            item = self._repository.get_item(account_id, item_id)
            if item.status != AtomicProfileItemStatus.ACTIVE:
                raise AtomicProfileError("画像条目已删除，不能继续反馈。")
            existing = self._repository.find_feedback(
                account_id, item_id, request.kind
            )
            if existing is None:
                candidate = AtomicProfileFeedback(
                    feedback_id=_new_feedback_id(),
                    owner_account_id=account_id,
                    profile_item_id=item_id,
                    item_version=item.version,
                    kind=request.kind,
                    note=note or None,
                    created_at=_now(),
                )
                self._repository.save_feedback(candidate)
                # 并发重复提交由唯一约束兜底：以实际落库的既有行为准。
                stored = self._repository.find_feedback(
                    account_id, item_id, request.kind
                )
                feedback = stored or candidate
            else:
                feedback = existing
        self._audit_feedback(account_id, feedback)
        return self.feedback_projection(feedback)

    def _audit_feedback(
        self, account_id: str, feedback: AtomicProfileFeedback
    ) -> None:
        """反馈审计：只记条目与类别，不含正文与用户补充说明。"""

        if self._observability is None:
            return
        try:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.PROFILE_EVIDENCE_FEEDBACK,
                result=AuditResult.SUCCESS,
                reason="profile_evidence_feedback",
                details={
                    "profile_item_id": feedback.profile_item_id,
                    "kind": feedback.kind.value,
                    "effect": feedback_effect_for(feedback.kind).value,
                },
            )
        except Exception:  # noqa: BLE001 - 审计失败不回收已记录的反馈
            return

    # -- 用户操作 ---------------------------------------------------------

    def modify_item(
        self,
        account_id: str,
        item_id: str,
        request: AtomicProfileItemModifyRequest,
    ) -> AtomicProfileItem:
        """行内编辑：用户正文是权威版本，自动抽取以后不得改写。"""

        text = normalize_text(request.text)
        if not text:
            raise AtomicProfileError("画像内容不合法。")
        with self._repository.transaction():
            item = self._repository.get_item(account_id, item_id)
            if item.status != AtomicProfileItemStatus.ACTIVE:
                raise AtomicProfileError("画像条目已删除，不能继续修改。")
            if request.version != item.version:
                raise AtomicProfileError("版本冲突，请刷新后重试。")
            key = identity_key(account_id, text)
            previous_key = item.identity_key
            previous_text = item.text
            previous_fact_key = item.fact_key
            previous_sources = list(item.source_message_ids)
            identity = parse_fact_identity(
                text, dimension=_dimension_hint(item.topic_hint)
            )
            new_fact_key = fact_identity_key(account_id, identity)
            if key != previous_key:
                duplicate = self._repository.find_item_by_identity(account_id, key)
                if duplicate is not None and duplicate.profile_item_id != item_id:
                    raise AtomicProfileError(
                        "已存在内容相同的信息，请先删除其中一条。"
                    )
            if new_fact_key != previous_fact_key:
                fact_duplicate = self._repository.find_item_by_fact_key(
                    account_id, new_fact_key
                )
                if (
                    fact_duplicate is not None
                    and fact_duplicate.profile_item_id != item_id
                    and fact_duplicate.status == AtomicProfileItemStatus.ACTIVE
                ):
                    raise AtomicProfileError(
                        "已存在内容相同的信息，请先删除其中一条。"
                    )
            now = _now()
            item.text = text
            item.identity_key = key
            _apply_fact_identity(item, identity, account_id)
            if key != previous_key:
                # 用户页面编辑是独立主动来源，不是聊天原话证据：换值后不保留
                # 旧值的原话引用，避免把用户新写的正文伪称为聊天里说过。
                item.evidence_quote = None
            # 用户编辑里明示的新期限以本次编辑时间为锚更新；没有时间表达时
            # 保留原期限（用户改措辞不等于取消期限，也不自动续期）。
            window = parse_validity_window(text, now)
            if window is not None:
                item.valid_from = window.valid_from
                item.valid_until = window.valid_until
                item.validity_anchor_at = now
                item.validity_phrase = window.phrase
            item.version += 1
            item.updated_at = now
            item.user_edited_at = now
            item.write_origin = AtomicProfileWriteOrigin.USER
            saved = self._repository.save_item(item)
            revoked: list[AtomicProfileItem] = []
            if key != previous_key or new_fact_key != previous_fact_key:
                # 旧正文转为抑制键：用户改掉的值不会因为旧消息或旧记录再次
                # 被抽取而作为新条目回来（用户编辑优先于自动提取）。旧事实
                # 身份一并以墓碑保留，普通近义提及也不会复活旧值。必须在
                # 条目换键之后再写，否则抑制键会被本条自己的旧键占住。
                self._write_suppression(
                    account_id,
                    previous_text,
                    topic_hint=item.topic_hint,
                    suppress_fact_identity=new_fact_key != previous_fact_key,
                )
                revoked = [
                    saved.model_copy(update={"source_message_ids": previous_sources})
                ]
        if revoked:
            # 用户纠正改变事实身份：依赖旧值的切片与摘要按依赖失效。通知在
            # 事务提交之后发出——摘要失效各自持有事务，事务内通知会嵌套失败。
            self._notify_revocation(account_id, revoked, reason="edited")
        return saved

    def delete_item(self, account_id: str, item_id: str, version: int) -> None:
        """删除条目：条目先转墓碑，再撤回底层记录。

        墓碑先写：两次写入之间的失败只会留下「已删条目 + 仍活动的旧记录」，
        镜像会因墓碑拒绝复活；反过来则会留下用户仍能看到的活动条目。墓碑
        提交后来源撤回失败时原样上抛（墓碑不回滚，条目保持不可召回），
        重复执行同一撤回由提交 module 的幂等撤回语义保证。
        """

        with self._profile_commit.transaction():
            item = self._repository.get_item(account_id, item_id)
            if item.status != AtomicProfileItemStatus.ACTIVE:
                raise AtomicProfileError("画像条目已删除，不能重复删除。")
            if version != item.version:
                raise AtomicProfileError("版本冲突，请刷新后重试。")
            self._write_tombstone(item)
        # 墓碑已提交：依赖该事实的切片与摘要立即按依赖失效（已发送的云端
        # 上下文无法收回，通知只阻止后续调用继续使用）。
        self._notify_revocation(account_id, [item], reason="deleted")
        # 撤回在墓碑边界之外逐条执行（见提交 module）；失败如实上抛，
        # 已提交的墓碑保持有效。
        outcome = self.withdraw_item_sources(account_id, [item])[0]
        if outcome.status is SourceWithdrawalStatus.FAILED and outcome.error is not None:
            raise outcome.error

    # -- 记住／忘掉 -------------------------------------------------------

    def remember(
        self,
        account_id: str,
        text: str,
        *,
        source_message_id: str | None = None,
        source_at: datetime | None = None,
    ) -> AtomicProfileItem:
        """用户明确要求记住：本轮立即成为用户权威条目，并解除同键墓碑。

        ``source_at`` 是来源消息时间：相对时间（如「下周」）以它为锚解析成
        绝对有效期；未提供时用当前时间，绝不按以后的使用当天重解释。
        """

        normalized = normalize_text(text)
        if not normalized or len(normalized) > _MAX_ITEM_TEXT_LENGTH:
            raise AtomicProfileError("画像内容不合法。")
        now = _now()
        anchor = _as_aware(source_at) if source_at is not None else now
        # 明确的暂停/完成/恢复信号先改变现有目标的生命周期；没有可对账的
        # 目标时按普通事实保存（用户说的是明确记住，不丢弃内容）。
        signal = parse_goal_lifecycle_signal(normalized)
        if signal is not None:
            applied = self._apply_goal_lifecycle(
                account_id,
                signal,
                text=normalized,
                source_at=anchor,
                source_message_id=source_message_id,
                now=now,
            )
            if applied is not None:
                return applied[0]
        record = self._record_for_user_text(account_id, normalized)
        dimension = record.dimension if record is not None else None
        identity = parse_fact_identity(normalized, dimension=dimension)
        key = identity_key(account_id, normalized)
        fact_key = fact_identity_key(account_id, identity)
        window = parse_validity_window(normalized, anchor)
        with self._repository.transaction():
            existing = self._repository.find_item_by_identity(account_id, key)
            if existing is None:
                existing = self._repository.find_item_by_fact_key(account_id, fact_key)
            if existing is not None:
                if (
                    existing.status == AtomicProfileItemStatus.ACTIVE
                    and existing.write_origin == AtomicProfileWriteOrigin.USER
                    and existing.text == normalized
                    and (
                        source_message_id is None
                        or source_message_id in existing.source_message_ids
                    )
                ):
                    superseded_items = self._reconcile_explicit_change(
                        account_id, normalized, existing
                    )
                    saved = existing
                else:
                    if existing.text != normalized:
                        # 用户写入新正文：旧原话不再对应当前事实，不伪称来源。
                        existing.evidence_quote = None
                    existing.text = normalized
                    existing.identity_key = key
                    existing.status = AtomicProfileItemStatus.ACTIVE
                    existing.superseded_by_id = None
                    existing.goal_state = AtomicProfileGoalState.ACTIVE
                    _apply_fact_identity(existing, identity, account_id)
                    _apply_validity(existing, window, anchor)
                    existing.write_origin = AtomicProfileWriteOrigin.USER
                    existing.confidence = FourDimensionConfidence.HIGH
                    existing.user_edited_at = now
                    existing.updated_at = now
                    existing.version += 1
                    existing.migration_run_id = None
                    if source_message_id is not None:
                        existing.source_message_ids = list(
                            dict.fromkeys(
                                [*existing.source_message_ids, source_message_id]
                            )
                        )
                    if existing.source_record_id is None and record is not None:
                        existing.source_record_id = record.record_id
                        existing.topic_hint = record.dimension.value
                    saved = self._repository.save_item(existing)
                    superseded_items = self._reconcile_explicit_change(
                        account_id, normalized, saved
                    )
            else:
                item = AtomicProfileItem(
                    profile_item_id=_new_item_id(),
                    owner_account_id=account_id,
                    text=normalized,
                    identity_key=key,
                    fact_subject=identity.subject,
                    fact_relation=identity.relation,
                    fact_object=identity.object,
                    fact_scope=identity.scope,
                    fact_key=fact_key,
                    valid_from=window.valid_from if window else None,
                    valid_until=window.valid_until if window else None,
                    validity_anchor_at=anchor if window else None,
                    validity_phrase=window.phrase if window else None,
                    source_record_id=record.record_id if record is not None else None,
                    source_message_ids=[source_message_id] if source_message_id else [],
                    topic_hint=record.dimension.value if record is not None else None,
                    status=AtomicProfileItemStatus.ACTIVE,
                    write_origin=AtomicProfileWriteOrigin.USER,
                    confidence=FourDimensionConfidence.HIGH,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    user_edited_at=now,
                    migration_run_id=None,
                )
                saved = self._repository.save_item(item)
                superseded_items = self._reconcile_explicit_change(
                    account_id, normalized, saved
                )
        if superseded_items:
            # 明确收回/替代也是撤回：依赖旧事实的切片与摘要按依赖失效。通知
            # 在事务提交后发出——摘要失效自有事务，事务内通知会嵌套失败。
            self._notify_revocation(
                account_id, superseded_items, reason="superseded"
            )
        return saved

    def forget(self, account_id: str, target: str) -> AtomicProfileMemoryResult:
        """用户明确要求忘掉：删除命中的活动条目；没找到就如实返回未完成。"""

        normalized = normalize_text(target)
        if len(normalized) < _MIN_FORGET_TARGET_LENGTH:
            return AtomicProfileMemoryResult(
                kind=AtomicProfileMemoryKind.FORGET,
                status=AtomicProfileMemoryStatus.UNRESOLVED,
            )
        candidates = self.list_items(account_id)
        # 完整对象优先：不能因共有「我平时喜欢」等措辞把其他事实一起删除。
        exact = [
            item for item in candidates
            if normalize_text(item.text).casefold() == normalized.casefold()
        ]
        contained = [
            item for item in candidates
            if normalize_text(item.text).casefold() in normalized.casefold()
            or normalized.casefold() in normalize_text(item.text).casefold()
        ]
        matched = exact or contained
        # 多对象只有逐项完整指定时才批量执行；关键词共享或模糊相似需澄清。
        if not matched or (
            len(matched) > 1
            and not all(
                normalize_text(item.text).casefold() in normalized.casefold()
                for item in matched
            )
        ):
            return AtomicProfileMemoryResult(
                kind=AtomicProfileMemoryKind.FORGET,
                status=AtomicProfileMemoryStatus.UNRESOLVED,
            )
        with self._profile_commit.transaction():
            for item in matched:
                self._write_tombstone(item)
        # 墓碑已提交：依赖该事实的切片与摘要立即按依赖失效（已发送的云端
        # 上下文无法收回，通知只阻止后续调用继续使用）。
        self._notify_revocation(account_id, matched, reason="forgotten")
        # 墓碑已提交：用户可见的删除在本轮成立。来源撤回逐条隔离——部分
        # 失败不中断其余条目，失败明细由提交 module 返回并记入日志，可
        # 幂等重试（重启后的恢复闭环由 06 完成），已删除条目不会因此复活。
        self.withdraw_item_sources(account_id, matched)
        return AtomicProfileMemoryResult(
            kind=AtomicProfileMemoryKind.FORGET,
            status=AtomicProfileMemoryStatus.FORGOTTEN,
            matched_count=len(matched),
        )

    # -- 目标生命周期（工单 18） ------------------------------------------

    def apply_lifecycle_signal(
        self,
        account_id: str,
        text: str,
        *,
        source_message_id: str | None = None,
        source_at: datetime | None = None,
    ) -> GoalLifecycleOutcome | None:
        """按用户明确信号暂停/完成/恢复目标；没有信号或没有可对账目标时为空。"""

        normalized = normalize_text(text)
        if not normalized:
            return None
        signal = parse_goal_lifecycle_signal(normalized)
        if signal is None:
            return None
        now = _now()
        anchor = _as_aware(source_at) if source_at is not None else now
        applied = self._apply_goal_lifecycle(
            account_id,
            signal,
            text=normalized,
            source_at=anchor,
            source_message_id=source_message_id,
            now=now,
        )
        if applied is None:
            return None
        return GoalLifecycleOutcome(kind=signal.kind, matched_count=len(applied))

    def _apply_goal_lifecycle(
        self,
        account_id: str,
        signal: GoalLifecycleSignal,
        *,
        text: str,
        source_at: datetime,
        source_message_id: str | None,
        now: datetime,
    ) -> list[AtomicProfileItem] | None:
        """把一条生命周期信号落到对账得到的目标条目上。"""

        goals = [
            item
            for item in self._repository.list_items(account_id)
            if item.fact_relation == AtomicProfileFactRelation.GOAL
        ]
        matched = self._match_goal_targets(goals, signal)
        if not matched:
            return None
        window = parse_validity_window(text, source_at)
        changed: list[AtomicProfileItem] = []
        with self._repository.transaction():
            for item in matched:
                if signal.kind is GoalLifecycleKind.PAUSE:
                    if item.goal_state == AtomicProfileGoalState.COMPLETED:
                        continue
                    item.goal_state = AtomicProfileGoalState.PAUSED
                elif signal.kind is GoalLifecycleKind.COMPLETE:
                    item.goal_state = AtomicProfileGoalState.COMPLETED
                else:
                    # 恢复解除暂停；已过期目标不会仅凭「继续」续期——期限只有
                    # 新日期才更新，过期条目仍被召回门以「已过期」排除。
                    item.goal_state = AtomicProfileGoalState.ACTIVE
                    _apply_validity(item, window, source_at)
                item.updated_at = now
                item.version += 1
                if source_message_id is not None:
                    item.source_message_ids = list(
                        dict.fromkeys([*item.source_message_ids, source_message_id])
                    )
                self._repository.save_item(item)
                changed.append(item)
        return changed or None

    @staticmethod
    def _match_goal_targets(
        goals: list[AtomicProfileItem], signal: GoalLifecycleSignal
    ) -> list[AtomicProfileItem]:
        """按对象定位目标；信号没有对象时只处理唯一的候选，避免误改。"""

        if signal.target:
            return [
                item
                for item in goals
                if _is_goal_mention(item.fact_object or item.text, signal.target)
            ]
        if signal.kind is GoalLifecycleKind.RESUME:
            paused = [
                item
                for item in goals
                if item.goal_state == AtomicProfileGoalState.PAUSED
            ]
            if len(paused) == 1:
                return paused
            # 「到期 → 继续」：没有暂停中的目标时，唯一的活动目标可作为
            # 恢复对象，但不会延长已过的期限。
            active = [
                item
                for item in goals
                if item.goal_state == AtomicProfileGoalState.ACTIVE
            ]
            return active if len(active) == 1 else []
        active = [
            item for item in goals if item.goal_state == AtomicProfileGoalState.ACTIVE
        ]
        return active if len(active) == 1 else []

    def tombstone_audit(self, account_id: str) -> list[AtomicProfileTombstoneEntry]:
        """墓碑/被替代版本的审计投影（不含正文；账户作用域）。"""

        entries: list[AtomicProfileTombstoneEntry] = []
        for item in self._repository.list_items(account_id, include_withdrawn=True):
            if item.status == AtomicProfileItemStatus.ACTIVE:
                continue
            entries.append(
                AtomicProfileTombstoneEntry(
                    profile_item_id=item.profile_item_id,
                    status=item.status,
                    fact_relation=item.fact_relation,
                    fact_scope=item.fact_scope,
                    has_fact_suppression=bool(item.fact_key),
                    superseded_by_id=item.superseded_by_id,
                    source_message_ids=list(item.source_message_ids),
                    updated_at=item.updated_at,
                )
            )
        entries.sort(key=lambda entry: entry.updated_at, reverse=True)
        return entries

    # -- 来源撤回与恢复 ---------------------------------------------------

    def tombstoned_items_with_sources(self, account_id: str) -> list[AtomicProfileItem]:
        """列出仍指向来源记录的已删除条目（跨重启恢复的扫描依据）。

        墓碑保留 ``source_record_id``，所以「用户已删除、来源还没撤回」这件
        事可以只从持久记录里看出来——这正是恢复不需要进程内对象的原因。
        没有关联来源的条目不在其中：未匹配到四维记录的「记住」条目只剩
        旧正文抑制键，没有可撤回的来源。扫描按账户作用域，不涉及其他账户
        的条目。
        """

        return [
            item
            for item in self._repository.list_items(account_id, include_withdrawn=True)
            if item.status == AtomicProfileItemStatus.WITHDRAWN and item.source_record_id
        ]

    def withdraw_item_sources(
        self, account_id: str, items: Iterable[AtomicProfileItem]
    ) -> list[SourceWithdrawal]:
        """在墓碑提交之后撤回这些条目的来源（提交 module 的公开入口）。

        逐条隔离与幂等语义见 :meth:`ProfileCommit.withdraw_item_sources`；
        本方法只是把撤回一步的归属地暴露给入口与恢复流程，不复制规则。
        """

        return self._profile_commit.withdraw_item_sources(account_id, items)

    def recover_source_withdrawals(self, account_id: str) -> list[SourceWithdrawal]:
        """重启后的恢复入口：扫描墓碑条目，幂等补齐未完成的来源撤回。

        结论逐条返回：``WITHDRAWN`` 是本次补齐的，``FAILED`` 是仍未完成的
        （可再调用本方法重试，也可等下次重启），``ALREADY_WITHDRAWN``／
        ``NO_SOURCE`` 表示此前已经收敛、本次没有写入。整个恢复只读存储里
        的墓碑与来源记录，不依赖失败时的进程内对象。
        """

        return self._profile_commit.recover_source_withdrawals(account_id)

    # -- 自动抽取镜像 -----------------------------------------------------

    def mirror_record(
        self,
        account_id: str,
        record: FourDimensionProfileRecord,
        *,
        evidence_message_id: str | None = None,
        fact_text: str | None = None,
        source_at: datetime | None = None,
    ) -> AtomicProfileItem | None:
        """把一条四维记录镜像成原子条目（事实身份写入）。

        去重先按事实身份（主体/关系/对象/范围），再兼容旧的正文文本键：
        同一事实补充证据，不同事实并存；同一底层来源记录换值时旧版本被
        替代并保留对账。返回 ``None`` 表示本条不写入：正文为空、用户已
        删除（墓碑抑制复活）、或该条目已被用户编辑成别的内容（用户优先）。

        ``fact_text`` 是抽取侧提供的完整事实正文（关系会从 ``record.content``
        中丢失时使用，如「喜欢 Python」与「正在学习 Python」）；缺失时沿用
        来源记录正文，绝不伪造原话。

        工单 18 补充：明确的生命周期信号（「暂时不考研」「考完了」「继续准备」）
        只改变已对账目标的状态，不写成新事实；与已撤回事实近义、身份又无法
        确定的自动新说法暂缓写入（明确重新记住走 :meth:`remember`）；明示
        时间按 ``source_at`` 来源消息时间锚解析为绝对有效期。
        """

        text = normalize_text(fact_text or record.content)
        if not text or len(text) > _MAX_ITEM_TEXT_LENGTH:
            return None
        now = _now()
        anchor = _as_aware(source_at) if source_at is not None else now
        signal = parse_goal_lifecycle_signal(text)
        if signal is not None:
            applied = self._apply_goal_lifecycle(
                account_id,
                signal,
                text=text,
                source_at=anchor,
                source_message_id=evidence_message_id,
                now=now,
            )
            if applied:
                return applied[0]
            # 没有可对账目标：不作为生命周期信号消化，按普通事实继续写入
            # （「作业搞定了」是状态描述，不是对既有目标的完成声明）。
        identity = parse_fact_identity(text, dimension=record.dimension)
        key = identity_key(account_id, text)
        fact_key = fact_identity_key(account_id, identity)
        evidence_quote = normalize_text(record.evidence_quote or "") or None
        window = parse_validity_window(text, anchor)
        if not self._has_active_identity(account_id, fact_key, key):
            _suppressed = self._find_related_suppression(account_id, identity)
            if _suppressed is not None:
                # 不确定近义候选暂缓写入和长期使用（R06）：只留原运行审计，
                # 不把该值作为长期事实写回；明确重新记住才恢复指定范围。
                logger.info(
                    "atomic_profile_near_synonym_deferred",
                    extra={
                        "account_id": account_id,
                        "source_record_id": record.record_id,
                        "suppressed_item_id": _suppressed.profile_item_id,
                    },
                )
                return None
        superseded_items: list[AtomicProfileItem] = []
        with self._repository.transaction():
            predecessor: AtomicProfileItem | None = None
            by_source = self._repository.find_item_by_source(account_id, record.record_id)
            if by_source is not None:
                if by_source.status != AtomicProfileItemStatus.ACTIVE:
                    return None
                if by_source.user_edited_at is not None and by_source.text != text:
                    # 用户编辑优先：底层来源的自动变化不改写用户版本。
                    return by_source
                if by_source.fact_key == fact_key or by_source.identity_key == key:
                    return self._repository.save_item(
                        self._merge_item(
                            by_source,
                            record=record,
                            text=text,
                            identity=identity,
                            evidence_message_id=evidence_message_id,
                            evidence_quote=evidence_quote,
                            validity=window,
                            anchor=anchor,
                            now=now,
                        )
                    )
                # 同一底层记录的值被更新：旧版本退休（保留正文供对账），
                # 新值按事实身份去重后再写。
                predecessor = by_source
            existing = self._repository.find_item_by_fact_key(account_id, fact_key)
            if existing is not None:
                if existing.status != AtomicProfileItemStatus.ACTIVE:
                    return None
                if existing.user_edited_at is not None and existing.text != text:
                    # 用户编辑优先：同一事实的自动新说法不改写用户版本。
                    if predecessor is not None:
                        self._supersede_item(predecessor, existing.profile_item_id)
                    return existing
                if predecessor is not None:
                    self._supersede_item(predecessor, existing.profile_item_id)
                merged = self._merge_item(
                    existing,
                    record=record,
                    text=text,
                    identity=identity,
                    evidence_message_id=evidence_message_id,
                    evidence_quote=evidence_quote,
                    validity=window,
                    anchor=anchor,
                    now=now,
                )
                if predecessor is not None and merged.supersedes_id is None:
                    merged.supersedes_id = predecessor.profile_item_id
                return self._repository.save_item(merged)
            text_hit = self._repository.find_item_by_identity(account_id, key)
            if text_hit is not None and text_hit.status != AtomicProfileItemStatus.ACTIVE:
                # 同文本的墓碑或被替代版本：旧消息重放不得复活。
                if predecessor is not None:
                    self._supersede_item(predecessor, None)
                return None
            new_id = _new_item_id()
            if predecessor is not None:
                self._supersede_item(predecessor, new_id)
            item = AtomicProfileItem(
                profile_item_id=new_id,
                owner_account_id=account_id,
                text=text,
                identity_key=key,
                fact_subject=identity.subject,
                fact_relation=identity.relation,
                fact_object=identity.object,
                fact_scope=identity.scope,
                fact_key=fact_key,
                # 原话只来自本次来源记录：旧版本的原话不对应新值，绝不继承。
                evidence_quote=evidence_quote,
                valid_from=window.valid_from if window else None,
                valid_until=window.valid_until if window else None,
                validity_anchor_at=anchor if window else None,
                validity_phrase=window.phrase if window else None,
                supersedes_id=(
                    predecessor.profile_item_id if predecessor is not None else None
                ),
                source_record_id=record.record_id,
                source_message_ids=[evidence_message_id] if evidence_message_id else [],
                topic_hint=record.dimension.value,
                status=AtomicProfileItemStatus.ACTIVE,
                write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
                confidence=record.confidence,
                version=1,
                created_at=(
                    predecessor.created_at if predecessor is not None else now
                ),
                updated_at=now,
                user_edited_at=None,
                migration_run_id=None,
            )
            item.source_message_ids = list(dict.fromkeys(item.source_message_ids))
            saved = self._repository.save_item(item)
            superseded_items = self._reconcile_explicit_change(
                account_id, text, saved
            )
        if superseded_items:
            # 自动写入的明确收回/替代同样是撤回：依赖失效在事务提交后发出，
            # 避免摘要失效各自的事务与本事务嵌套。
            self._notify_revocation(
                account_id, superseded_items, reason="superseded"
            )
        return saved

    # -- 切片 -------------------------------------------------------------

    def compile_chat_slice(
        self,
        account_id: str,
        *,
        run_id: str,
        current_question: str | None = None,
        project_id: str | None = None,
        current_user_message_id: str | None = None,
        now: datetime | None = None,
        purpose: ProfileSlicePurpose | None = None,
    ) -> ProfileSlice:
        """只把当前任务必要的少量条目编译成本轮切片（用途感知）。

        ``current_user_message_id`` 是本轮用户消息：本轮刚由普通消息自动整理
        出的条目下一轮才生效（设计口径「普通异步提取从下一轮生效」），因此带
        着本轮证据的自动条目这一轮先排除；用户明确「记住」的条目不受影响，
        必须本轮就能用。

        工单 18：召回前先过有效性门——已撤回/被替代、仅适用于当时那轮、
        目标暂停/完成、过期、可靠度不足（且非用户编辑）的条目只记录排除
        原因，不进入本轮上下文；``now`` 供测试与控制面提供可控时钟。

        改进工单 19：``purpose`` 缺省时从当前问题确定性推导任务种类；
        跨主题默认表达偏好不要求共享字词，背景/目标/约束按任务召回，
        无关爱好不注入。返回的既有 ``ProfileSlice`` 只是采用快照的渲染
        折算，生成链以 :meth:`compile_adopted_slice` 的同一快照为准。
        """

        adopted = self.compile_adopted_slice(
            account_id,
            purpose=purpose
            or build_purpose(mode="companion", query=current_question),
            run_id=run_id,
            current_user_message_id=current_user_message_id,
            now=now,
        )
        included = [
            ProfileSliceItem(
                assertion_id=entry.profile_item_id,
                dimension="",
                value_or_rule=entry.fact_text,
                inclusion_reason=entry.adoption_reason,
                sensitivity_class=_slice_sensitivity_for(entry.relation),
                expires_at=entry.expires_at,
            )
            for entry in adopted.adopted_items
        ]
        unused = [
            UnusedSliceItem(
                assertion_id=entry.profile_item_id,
                dimension="",
                value_or_rule=entry.fact_text,
                exclusion_reason=entry.exclusion_reason,
            )
            for entry in adopted.excluded_items
        ]
        return ProfileSlice(
            slice_id=adopted.slice_id,
            owner_account_id=account_id,
            run_id=run_id,
            purpose="chat:atomic",
            project_id=project_id,
            included_items=included,
            unused_items=unused,
            sensitivity_classes_allowed=[
                ProfileSensitivityClass.PREFERENCE,
                ProfileSensitivityClass.LEARNING,
            ],
            compiled_policy_version=ATOMIC_PROFILE_MIGRATION_VERSION,
            revocation_version=adopted.revocation_version,
            length_budget=adopted.length_budget,
            compiled_at=adopted.compiled_at,
        )

    def compile_adopted_slice(
        self,
        account_id: str,
        *,
        run_id: str,
        purpose: ProfileSlicePurpose | None = None,
        current_user_message_id: str | None = None,
        now: datetime | None = None,
    ) -> AdoptedProfileSlice:
        """生成前编译唯一的采用快照（改进工单 19）。

        一次读取当轮已提交的有效条目并冻结：只包含活动、未删除、未过期、
        证据充分且范围适用的条目；本轮刚自动整理出的条目下一轮才生效，不
        等待后台新提取。采用结果、排除原因、适用条件与撤回版本都进入同一
        对象，同轮后续节点只能 :meth:`AdoptedProfileSlice.select_subset`。
        """

        moment = now or _now()
        effective = purpose or build_purpose(mode="companion")
        query = effective.query if effective.query is not None else None
        # 条目与撤回版本取自同一快照：编译中途发生的撤回要么已反映、要么
        # 使版本落后而在发送前被 `is_slice_current` 拦下（工单 18）。
        snapshot = self._repository.list_items(account_id, include_withdrawn=True)
        candidates: list[_AdoptedCandidate] = []
        exclusions: list[ProfileSliceExclusion] = []
        same_turn_reason = "本轮刚整理，下一轮才使用"
        unrelated_reason = "与当前问题无关"
        overridden_reason = "本轮明确要求优先，默认偏好本轮不采用"
        for item in snapshot:
            if item.status != AtomicProfileItemStatus.ACTIVE:
                continue
            if _is_same_turn_extraction(item, current_user_message_id):
                exclusions.append(
                    ProfileSliceExclusion(
                        profile_item_id=item.profile_item_id,
                        fact_text=item.text,
                        exclusion_reason=same_turn_reason,
                    )
                )
                continue
            gate_reason = _recall_exclusion(item, moment)
            if gate_reason is not None:
                exclusions.append(
                    ProfileSliceExclusion(
                        profile_item_id=item.profile_item_id,
                        fact_text=item.text,
                        exclusion_reason=gate_reason,
                    )
                )
                continue
            tags = expression_preference_tags(item.text)
            decisions = tuple(
                applicability(
                    relation=item.fact_relation,
                    text=item.text,
                    task_kind=effective.task_kind,
                )
            )
            topic_matched = _item_matches_question(item.text, query)
            generic_constraint = is_resource_constraint(item.text) and not has_task_topic(item.text)
            if decisions and not tags and not generic_constraint:
                # 学科背景和具体目标需有主题依据；年级等通用背景除外。
                generic_background = item.fact_relation in {
                    AtomicProfileFactRelation.IDENTITY,
                    AtomicProfileFactRelation.GRADE,
                } and not has_task_topic(item.text)
                if not (
                    generic_background or topic_matched or task_topic_matches(item.text, effective)
                ):
                    decisions = ()
            if decisions:
                tier = 0
            elif topic_matched:
                tier = 1
            else:
                exclusions.append(
                    ProfileSliceExclusion(
                        profile_item_id=item.profile_item_id,
                        fact_text=item.text,
                        exclusion_reason=unrelated_reason,
                    )
                )
                continue
            if tags and is_overridden(tags, effective.explicit_request):
                # 本轮明确要求优先：只影响本轮默认值，不写回长期画像。
                exclusions.append(
                    ProfileSliceExclusion(
                        profile_item_id=item.profile_item_id,
                        fact_text=item.text,
                        exclusion_reason=overridden_reason,
                    )
                )
                continue
            candidates.append(
                _AdoptedCandidate(
                    item=item,
                    tier=tier,
                    decisions=decisions,
                    reason=adoption_reason(
                        relation=item.fact_relation,
                        text=item.text,
                        task_kind=effective.task_kind,
                    ),
                    conditions=tuple(
                        constraint_conditions(
                            text=item.text,
                            task_kind=effective.task_kind,
                            has_explicit_expiry=item.valid_until is not None,
                        )
                    ),
                    is_default=bool(tags),
                )
            )
        # 排序：任务作用（直接可用的规则 > 词面相关）→ 来源权威（用户优先）
        # → 时效（新者优先）→ 证据充分度。整条采用，不做 80 字机械截断。
        candidates.sort(
            key=lambda candidate: (
                candidate.tier,
                _authority_rank(candidate.item),
                -candidate.item.updated_at.timestamp(),
                -confidence_rank(candidate.item.confidence),
            )
        )
        adopted = candidates[:MAX_SLICE_ITEMS]
        for candidate in candidates[MAX_SLICE_ITEMS:]:
            exclusions.append(
                ProfileSliceExclusion(
                    profile_item_id=candidate.item.profile_item_id,
                    fact_text=candidate.item.text,
                    exclusion_reason="超出本轮最小切片预算",
                )
            )
        return AdoptedProfileSlice(
            slice_id=_stable_id("slice", account_id, run_id),
            owner_account_id=account_id,
            run_id=run_id,
            purpose=effective,
            adopted_items=tuple(_adopted_item(candidate) for candidate in adopted),
            excluded_items=tuple(exclusions),
            snapshot_versions=tuple(
                (item.profile_item_id, item.version, item.status.value) for item in snapshot
            ),
            revocation_version=self._revocation_version_for(snapshot),
            length_budget=MAX_SLICE_ITEMS,
            compiled_at=moment,
        )

    # -- 迁移 -------------------------------------------------------------

    def migrate_account(self, account_id: str) -> AtomicProfileMigrationReport:
        """把旧四类记录一次性转成无类别的原子列表。

        只读旧记录、不原位改写：重复执行是幂等的（新增 0、重复 N），回滚按
        批次删除本批次新建的条目即可恢复。迁移前先经仓库端口落一份一致性快照
        （内存仓库没有需要保护的文件时为空值），写入全部在一次事务内完成：
        任一条失败即整体回滚，只留下可重试的对账报告与失败那一条的原因，不产生
        半迁移状态。每条旧记录的结论与原因码逐条入账，页面据此说明「哪条为什么
        没迁入」，而不是只给一个计数。
        """

        snapshot = self._repository.snapshot_before_migration()
        logger.info(
            "atomic_profile_migration_started",
            extra={
                "account_id": account_id,
                "backup": Path(snapshot).name if snapshot else None,
            },
        )
        run_id = f"atomic-{secrets.token_urlsafe(12)}"
        now = _now()
        records = self._four_dimensions.list_records(account_id, include_withdrawn=True)
        counts = {outcome.value: 0 for outcome in AtomicProfileReconciliationOutcome}
        created: list[str] = []
        source_ids = [record.record_id for record in records]
        reconciliation: list[AtomicProfileReconciliationEntry] = []
        failing_record_id: str | None = None
        digest = _digest((record.record_id, record.content_hash) for record in records)
        try:
            with self._repository.transaction():
                for record in records:
                    failing_record_id = record.record_id
                    entry = self._reconcile_record(account_id, record, run_id, now)
                    reconciliation.append(entry)
                    counts[entry.outcome.value] += 1
                    if entry.profile_item_id is not None and (
                        entry.outcome == AtomicProfileReconciliationOutcome.MIGRATED
                    ):
                        created.append(entry.profile_item_id)
                failing_record_id = None
                # 新形态并存、逐步迁移、最后收敛：旧版本已写入的原子条目在
                # 本批次补齐事实身份（不伪造原话，墓碑无正文则保持文本键），
                # 并按自身创建时间锚补齐明示期限（不重解释、不编造日期），
                # 重复执行幂等；补齐失败与逐条迁移同受外层事务保护。
                backfilled = self._backfill_item_identities(account_id)
                validity_backfilled = self._backfill_item_validity(account_id)
                if validity_backfilled:
                    logger.info(
                        "atomic_profile_validity_backfilled",
                        extra={
                            "account_id": account_id,
                            "count": validity_backfilled,
                        },
                    )
                report = AtomicProfileMigrationReport(
                    run_id=run_id,
                    owner_account_id=account_id,
                    migration_version=ATOMIC_PROFILE_MIGRATION_VERSION,
                    status=AtomicProfileMigrationStatus.COMPLETED,
                    migrated=counts["migrated"],
                    duplicated=counts["duplicated"],
                    tombstoned=counts["tombstoned"],
                    skipped=counts["skipped"],
                    failed=0,
                    created_item_ids=created,
                    source_record_ids=source_ids,
                    reconciliation_digest=digest,
                    reconciliation=reconciliation,
                    retryable=False,
                    identity_backfilled=backfilled,
                    created_at=now,
                )
                return self._repository.save_migration_report(report)
        except ProfileError as exc:
            # 事务已整体回滚：本批次没有提交任何条目或墓碑。失败前已判定的结论
            # 随回滚一起作废——把它们写进台账会让台账声称迁入了并不存在的条目，
            # 因此计数全部归零、逐条明细只留失败那一条；重试会重新判定每一条。
            logger.warning(
                "atomic_profile_migration_failed",
                extra={
                    "account_id": account_id,
                    "run_id": run_id,
                    "record_id": failing_record_id,
                    "error_type": type(exc).__name__,
                },
            )
            report = AtomicProfileMigrationReport(
                run_id=run_id,
                owner_account_id=account_id,
                migration_version=ATOMIC_PROFILE_MIGRATION_VERSION,
                status=AtomicProfileMigrationStatus.RETRYABLE,
                migrated=0,
                duplicated=0,
                tombstoned=0,
                skipped=0,
                failed=1,
                created_item_ids=[],
                source_record_ids=source_ids,
                reconciliation_digest=digest,
                reconciliation=(
                    [
                        AtomicProfileReconciliationEntry(
                            source_record_id=failing_record_id,
                            outcome=AtomicProfileReconciliationOutcome.FAILED,
                            reason_code=MIGRATION_REASON_MIGRATION_FAILED,
                        )
                    ]
                    if failing_record_id is not None
                    else []
                ),
                retryable=True,
                created_at=now,
            )
            with self._repository.transaction():
                return self._repository.save_migration_report(report)

    def _reconcile_record(
        self,
        account_id: str,
        record: FourDimensionProfileRecord,
        run_id: str,
        now: datetime,
    ) -> AtomicProfileReconciliationEntry:
        """判定单条旧四维记录能否原子化，返回带原因码的对账结论。

        判定顺序即用户可见语义的顺序：先看有没有可迁移正文，再看来源是否已
        撤回（墓碑只写不复活），然后按来源记录去重，最后按正文身份去看用户
        权威——用户改过或删过的正文一律不覆盖，只如实记下原因。
        """

        if not normalize_text(record.content):
            return AtomicProfileReconciliationEntry(
                source_record_id=record.record_id,
                outcome=AtomicProfileReconciliationOutcome.SKIPPED,
                reason_code=MIGRATION_REASON_EMPTY_TEXT,
            )
        if record.status == FourDimensionRecordStatus.WITHDRAWN:
            tombstone_id = self._write_migration_tombstone(
                account_id, record, run_id, now
            )
            return AtomicProfileReconciliationEntry(
                source_record_id=record.record_id,
                outcome=AtomicProfileReconciliationOutcome.TOMBSTONED,
                reason_code=MIGRATION_REASON_SOURCE_WITHDRAWN,
                profile_item_id=tombstone_id,
            )
        by_source = self._repository.find_item_by_source(account_id, record.record_id)
        if by_source is not None:
            return AtomicProfileReconciliationEntry(
                source_record_id=record.record_id,
                outcome=AtomicProfileReconciliationOutcome.DUPLICATED,
                reason_code=MIGRATION_REASON_SOURCE_ALREADY_MIGRATED,
                profile_item_id=by_source.profile_item_id,
            )
        text = normalize_text(record.content)
        identity = parse_fact_identity(text)
        existing = self._repository.find_item_by_identity(
            account_id, identity_key(account_id, text)
        )
        if existing is None:
            # 同一事实可能已由另一条旧记录迁入（正文不同但身份相同）：
            # 只补充来源，不新建副本，也不改写用户权威条目。
            existing = self._repository.find_item_by_fact_key(
                account_id, fact_identity_key(account_id, identity)
            )
        if existing is not None:
            if existing.status == AtomicProfileItemStatus.WITHDRAWN:
                return AtomicProfileReconciliationEntry(
                    source_record_id=record.record_id,
                    outcome=AtomicProfileReconciliationOutcome.DUPLICATED,
                    reason_code=MIGRATION_REASON_IDENTITY_TOMBSTONED,
                    profile_item_id=existing.profile_item_id,
                )
            if existing.write_origin == AtomicProfileWriteOrigin.USER:
                # 用户权威：用户记住或改写的同一条事实不回填、不改写。
                return AtomicProfileReconciliationEntry(
                    source_record_id=record.record_id,
                    outcome=AtomicProfileReconciliationOutcome.DUPLICATED,
                    reason_code=MIGRATION_REASON_USER_ITEM_KEPT,
                    profile_item_id=existing.profile_item_id,
                )
            existing.source_record_id = existing.source_record_id or record.record_id
            existing.topic_hint = existing.topic_hint or record.dimension.value
            self._repository.save_item(existing)
            return AtomicProfileReconciliationEntry(
                source_record_id=record.record_id,
                outcome=AtomicProfileReconciliationOutcome.DUPLICATED,
                reason_code=MIGRATION_REASON_IDENTITY_EXISTS,
                profile_item_id=existing.profile_item_id,
            )
        item = _item_from_record(
            account_id,
            record,
            write_origin=AtomicProfileWriteOrigin.MIGRATION,
            migration_run_id=run_id,
        )
        self._repository.save_item(item)
        return AtomicProfileReconciliationEntry(
            source_record_id=record.record_id,
            outcome=AtomicProfileReconciliationOutcome.MIGRATED,
            reason_code=MIGRATION_REASON_MIGRATED,
            profile_item_id=item.profile_item_id,
        )

    def _backfill_item_identities(self, account_id: str) -> int:
        """为既有原子条目补齐事实身份；幂等，不伪造正文或原话。

        旧版本只按正文去重，没有主体/关系/对象/范围。本方法按已保存正文
        与内部 ``topic_hint`` 解析身份，让旧条目立即获得同事实合并与
        单值槽替代能力；墓碑没有正文可解析，保持文本键与删除抑制不变。
        补齐不改变用户的乐观锁版本、正文、来源与编辑权威。
        """

        count = 0
        for item in self._repository.list_items(account_id, include_withdrawn=True):
            if item.fact_key and item.fact_object:
                continue
            if not normalize_text(item.text):
                continue
            identity = parse_fact_identity(item.text)
            _apply_fact_identity(item, identity, account_id)
            self._repository.save_item(item)
            count += 1
        return count

    def _backfill_item_validity(self, account_id: str) -> int:
        """为既有条目按自身创建时间锚补齐期限；幂等，不重解释、不编造日期。

        升级前写入的「下周考试」等条目没有绝对期限。本方法只在正文确有时
        间表达时，用条目创建时间（即当时的来源锚）解析一次并落库；此后按绝
        对区间判定，不再按使用当天重解释。无时间表达的长期偏好保持无期限。
        """

        count = 0
        for item in self._repository.list_items(account_id, include_withdrawn=True):
            if item.validity_anchor_at is not None or item.valid_until is not None:
                continue
            if not normalize_text(item.text):
                continue
            window = parse_validity_window(item.text, item.created_at)
            if window is None:
                continue
            item.valid_from = window.valid_from
            item.valid_until = window.valid_until
            item.validity_anchor_at = item.created_at
            item.validity_phrase = window.phrase
            self._repository.save_item(item)
            count += 1
        return count

    def rollback_migration(
        self, account_id: str, run_id: str
    ) -> AtomicProfileMigrationReport:
        """回滚某个迁移批次：只删除本批次新建的条目，旧四维记录保持不动。"""

        report = self._repository.get_migration_report(account_id, run_id)
        if report is None:
            raise AtomicProfileError("对象不存在或没有访问权限。")
        if report.status == AtomicProfileMigrationStatus.UNDONE:
            return report
        with self._repository.transaction():
            self._repository.delete_items_by_run(account_id, run_id)
            undone = report.model_copy(
                update={
                    "status": AtomicProfileMigrationStatus.UNDONE,
                    "undone_at": _now(),
                }
            )
            return self._repository.save_migration_report(undone)

    def latest_migration_report(
        self, account_id: str
    ) -> AtomicProfileMigrationReport | None:
        return self._repository.get_latest_migration_report(account_id)

    # -- 内部辅助 ---------------------------------------------------------

    def _write_tombstone(self, item: AtomicProfileItem) -> None:
        """删除墓碑：保留抑制键、清空正文，旧消息重放不得复活该条目。"""

        now = _now()
        self._repository.save_item(
            item.model_copy(
                update={
                    "text": "",
                    "status": AtomicProfileItemStatus.WITHDRAWN,
                    "write_origin": AtomicProfileWriteOrigin.USER,
                    "version": item.version + 1,
                    "updated_at": now,
                    "user_edited_at": now,
                }
            )
        )

    def _write_suppression(
        self,
        account_id: str,
        text: str,
        *,
        topic_hint: str | None = None,
        suppress_fact_identity: bool = True,
    ) -> None:
        """为用户改掉或被替代的旧值留下抑制键（墓碑），只保留键、不留正文。

        同时保留旧正文的文本键与事实身份键：普通同义提及或旧记录重放都
        不能把用户明确改掉/替代的值作为新条目写回来。``topic_hint`` 是旧
        条目的维度提示：正文本身看不出关系时（如裸值「考研」），用它可以
        解析出与原条目一致的事实身份，抑制键才不会落空。
        ``suppress_fact_identity=False`` 用于同一事实只是换措辞的编辑：事实
        仍在活动列表里，只抑制旧正文，不能把整个事实身份也封掉。
        """

        text_key = identity_key(account_id, text)
        identity = parse_fact_identity(text, dimension=_dimension_hint(topic_hint))
        fact_key = (
            fact_identity_key(account_id, identity) if suppress_fact_identity else ""
        )
        duplicate = self._repository.find_item_by_identity(account_id, text_key)
        if duplicate is not None:
            return
        now = _now()
        self._repository.save_item(
            AtomicProfileItem(
                profile_item_id=_new_item_id(),
                owner_account_id=account_id,
                text="",
                identity_key=text_key,
                fact_subject=identity.subject,
                fact_relation=identity.relation,
                fact_object=identity.object,
                fact_scope=identity.scope,
                fact_key=fact_key,
                source_record_id=None,
                source_message_ids=[],
                topic_hint=None,
                status=AtomicProfileItemStatus.WITHDRAWN,
                write_origin=AtomicProfileWriteOrigin.USER,
                confidence=FourDimensionConfidence.HIGH,
                version=1,
                created_at=now,
                updated_at=now,
                user_edited_at=now,
                migration_run_id=None,
            )
        )

    def _supersede_item(
        self, item: AtomicProfileItem, superseded_by_id: str | None
    ) -> None:
        """把活动条目退休为被替代版本；正文与来源保留供对账。"""

        now = _now()
        self._repository.save_item(
            item.model_copy(
                update={
                    "status": AtomicProfileItemStatus.SUPERSEDED,
                    "superseded_by_id": superseded_by_id,
                    "version": item.version + 1,
                    "updated_at": now,
                }
            )
        )

    def _reconcile_explicit_change(
        self, account_id: str, text: str, item: AtomicProfileItem
    ) -> list[AtomicProfileItem]:
        """明确变更的对账：单值属性槽换值与「明确收回 + 转向」只替代对应事实。

        返回被替代的旧条目（含正文与来源），供调用方在事务提交后发出撤回
        通知——通知消费方各自持有事务，在事务内发出会嵌套失败。普通追加
        （不同对象、不同目标）不经过这里，因此「新增六级不覆盖考研」。被
        替代版本保留在仓库中，供对账与审计。
        """

        identity = _identity_for_item(item)
        superseded: list[str] = []
        superseded_items: list[AtomicProfileItem] = []
        if identity.relation.is_single_valued:
            for old in self._active_slot_conflicts(account_id, item, identity):
                self._supersede_item(old, item.profile_item_id)
                superseded.append(old.profile_item_id)
                superseded_items.append(old)
        negated, _replacement = parse_explicit_change(text)
        if negated:
            # 明确收回（可带转向）：只替代对象完全一致的旧事实。用精确相等而
            # 不是包含匹配——「我不喜欢晨跑时听音乐」收回的是整句，不能把
            # 「晨跑」这条无关事实一起替代。纯否定（无转向）在身份解析里
            # 退化为整句陈述，但收回本身仍是明确变更，对应旧事实必须退休。
            targets = {
                fact_identity_key(
                    account_id,
                    parse_fact_identity(
                        value, dimension=FourDimension.STAGE_GOAL
                    ).model_copy(update={"scope": identity.scope}),
                )
                for value in negated
                if value
            }
            for old in self._repository.list_items(account_id):
                if old.profile_item_id == item.profile_item_id:
                    continue
                if old.fact_key and old.fact_key in targets:
                    self._supersede_item(old, item.profile_item_id)
                    superseded.append(old.profile_item_id)
                    superseded_items.append(old)
        if superseded and item.supersedes_id is None:
            item.supersedes_id = superseded[0]
            self._repository.save_item(item)
        return superseded_items

    def _active_slot_conflicts(
        self,
        account_id: str,
        item: AtomicProfileItem,
        identity: AtomicProfileFactIdentity,
    ) -> list[AtomicProfileItem]:
        """同主体、同关系、同范围的单值槽活动条目（对象不同即冲突）。"""

        return [
            old
            for old in self._repository.list_items(account_id)
            if old.profile_item_id != item.profile_item_id
            and old.fact_subject == identity.subject
            and old.fact_relation == identity.relation
            and old.fact_scope == identity.scope
            and old.fact_object != identity.object
        ]

    def _merge_item(
        self,
        item: AtomicProfileItem,
        *,
        record: FourDimensionProfileRecord,
        text: str | None = None,
        identity: AtomicProfileFactIdentity | None = None,
        evidence_message_id: str | None,
        evidence_quote: str | None = None,
        validity: ValidityWindow | None = None,
        anchor: datetime | None = None,
        now: datetime,
    ) -> AtomicProfileItem:
        """同一事实再次出现：补充来源、证据与把握度，不产生副本。

        生命周期状态与旧期限不因普通补证据被重置；只有本次原文明示新期限
        时才按新锚更新。
        """

        resolved_text = normalize_text(text or record.content)
        resolved_identity = identity or parse_fact_identity(
            resolved_text, dimension=record.dimension
        )
        if evidence_message_id is not None:
            item.source_message_ids = list(
                dict.fromkeys([*item.source_message_ids, evidence_message_id])
            )
        item.source_record_id = item.source_record_id or record.record_id
        item.topic_hint = item.topic_hint or record.dimension.value
        item.text = resolved_text
        item.identity_key = identity_key(item.owner_account_id, resolved_text)
        _apply_fact_identity(item, resolved_identity, item.owner_account_id)
        if evidence_quote:
            item.evidence_quote = evidence_quote
        if validity is not None:
            _apply_validity(item, validity, anchor or now)
        item.version += 1
        item.updated_at = now
        if is_recallable_confidence(record.confidence) and confidence_rank(
            record.confidence
        ) > confidence_rank(item.confidence):
            item.confidence = record.confidence
        return item

    def _has_active_identity(
        self, account_id: str, fact_key: str, text_key: str
    ) -> bool:
        """账户下是否已有同事实身份或同正文的活动条目（决定是否新建事实）。"""

        fact_hit = self._repository.find_item_by_fact_key(account_id, fact_key)
        if fact_hit is not None and fact_hit.status == AtomicProfileItemStatus.ACTIVE:
            return True
        text_hit = self._repository.find_item_by_identity(account_id, text_key)
        return text_hit is not None and text_hit.status == AtomicProfileItemStatus.ACTIVE

    def _find_related_suppression(
        self, account_id: str, identity: AtomicProfileFactIdentity
    ) -> AtomicProfileItem | None:
        """找同一关系/范围内的近义撤回墓碑（不确定身份不得自动恢复）。"""

        if identity.relation not in _SYNONYM_SCOPED_RELATIONS:
            return None
        for item in self._repository.list_items(account_id, include_withdrawn=True):
            if item.status != AtomicProfileItemStatus.WITHDRAWN:
                continue
            if (
                item.fact_subject != identity.subject
                or item.fact_relation != identity.relation
                or item.fact_scope != identity.scope
            ):
                continue
            if not item.fact_object:
                continue
            if item.fact_object.casefold() == identity.object.casefold():
                continue
            if identity.relation is AtomicProfileFactRelation.GOAL:
                # 目标只承认相等/包含：考研与考公共用一个字，不是近义替换。
                matched = _is_goal_mention(item.fact_object, identity.object)
            else:
                matched = _is_near_synonym(item.fact_object, identity.object)
            if matched:
                return item
        return None

    def _notify_revocation(
        self,
        account_id: str,
        items: Iterable[AtomicProfileItem],
        *,
        reason: str,
    ) -> None:
        """撤回提交后通知消费方（摘要等派生依据失效）；失败不回收撤回结果。

        已发送给模型的上下文无法收回：通知只用于阻止后续调用继续使用旧
        依据，不宣称物理删除；没有来源消息或没有挂载监听时静默跳过。
        """

        listener = self._revocation_listener
        if listener is None:
            return
        message_ids = list(
            dict.fromkeys(
                message_id
                for item in items
                for message_id in item.source_message_ids
                if message_id
            )
        )
        if not message_ids:
            return
        try:
            listener.on_profile_revocation(
                account_id, message_ids=message_ids, reason=reason
            )
        except Exception as exc:  # noqa: BLE001 - 传播失败不回收已完成的撤回
            logger.warning(
                "atomic_profile_revocation_listener_failed",
                extra={
                    "account_id": account_id,
                    "reason": reason,
                    "error": str(exc),
                },
            )

    def _record_for_user_text(
        self, account_id: str, text: str
    ) -> FourDimensionProfileRecord | None:
        """找出「记住」正文对应的现有四维记录；没有就返回 ``None``。

        只做确定性匹配：正文相同或互相包含时视为同一件事。找不到时不臆造
        分类，只写用户权威的原子条目。
        """

        normalized = normalize_text(text).casefold()
        for record in self._four_dimensions.list_records(account_id):
            content = normalize_text(record.content).casefold()
            if content == normalized or normalized in content or content in normalized:
                return record
        return None

    def _write_migration_tombstone(
        self,
        account_id: str,
        record: FourDimensionProfileRecord,
        run_id: str,
        now: datetime,
    ) -> str | None:
        """为已撤回的旧记录补墓碑：抑制键已存在时只把活动条目转为墓碑。

        返回墓碑条目标识（已有条目被转墓碑时返回该条目），供逐条对账说明
        「这条已撤回的旧记录对应哪个抑制键」。
        """

        text = normalize_text(record.content)
        key = identity_key(account_id, text)
        identity = parse_fact_identity(text, dimension=record.dimension)
        existing = self._repository.find_item_by_identity(account_id, key)
        if existing is not None:
            if existing.status == AtomicProfileItemStatus.ACTIVE:
                self._write_tombstone(existing)
            return existing.profile_item_id
        tombstone = AtomicProfileItem(
            profile_item_id=_new_item_id(),
            owner_account_id=account_id,
            text="",
            identity_key=key,
            fact_subject=identity.subject,
            fact_relation=identity.relation,
            fact_object=identity.object,
            fact_scope=identity.scope,
            fact_key=fact_identity_key(account_id, identity),
            source_record_id=record.record_id,
            source_message_ids=[],
            topic_hint=record.dimension.value,
            status=AtomicProfileItemStatus.WITHDRAWN,
            write_origin=AtomicProfileWriteOrigin.MIGRATION,
            confidence=record.confidence,
            version=1,
            created_at=now,
            updated_at=now,
            user_edited_at=None,
            migration_run_id=run_id,
        )
        self._repository.save_item(tombstone)
        return tombstone.profile_item_id


__all__ = [
    "ATOMIC_PROFILE_MIGRATION_VERSION",
    "MAX_SLICE_ITEMS",
    "MIGRATION_REASON_EMPTY_TEXT",
    "MIGRATION_REASON_IDENTITY_EXISTS",
    "MIGRATION_REASON_IDENTITY_TOMBSTONED",
    "MIGRATION_REASON_MIGRATED",
    "MIGRATION_REASON_SOURCE_ALREADY_MIGRATED",
    "MIGRATION_REASON_SOURCE_WITHDRAWN",
    "MIGRATION_REASON_MIGRATION_FAILED",
    "MIGRATION_REASON_USER_ITEM_KEPT",
    "AtomicProfileError",
    "AtomicProfileRepository",
    "AtomicProfileService",
    "InMemoryAtomicProfileRepository",
    "MemoryDirective",
    "ProfileEvidenceSourceReader",
    "ProfileSourceMessage",
    "SqliteAtomicProfileRepository",
    "fact_identity_key",
    "identity_key",
    "normalize_text",
    "parse_explicit_change",
    "parse_fact_identity",
    "parse_memory_directive",
]
