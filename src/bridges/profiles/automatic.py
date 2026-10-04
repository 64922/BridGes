"""Issue 15：消息级自动画像预处理、四维写入与有界重试。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, Protocol

from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.ai.model_quota import MODEL_QUOTA_VERSION, build_run_model_quota
from bridges.ai.payload_budget import TOKEN_ESTIMATE_VERSION
from bridges.ai.ports import ModelRunLockRecorder
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.contracts.ai import (
    BusinessRef,
    CallContractVersions,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileMemoryKind,
    AtomicProfileMemoryResult,
    AtomicProfileMemoryStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profile_extraction import (
    PROFILE_CONTROLS_VERSION,
    PROFILE_HYBRID_EXPLANATION,
    AutomaticProfileObservation,
    ProfileAccountControlsProjection,
    ProfileCorrectionResult,
    ProfileCorrectionStatus,
    ProfileExtractionAction,
    ProfileExtractionItem,
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionRetryTask,
    ProfileExtractionRun,
    ProfileExtractionSource,
    ProfileExtractionStatus,
    ProfilePageStatus,
    ProfilePreprocessResult,
    ProfilePrivacyNotice,
    ProfileStatusProjection,
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
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles.atomic import (
    AtomicProfileService,
    GoalLifecycleOutcome,
    MemoryDirective,
    parse_fact_identity,
    parse_goal_lifecycle_signal,
    parse_memory_directive,
)
from bridges.profiles.commit import ProfileCommit, ProfileRecordSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    confidence_rank,
    is_recallable_confidence,
)
from bridges.profiles.ports import ProfileRepository
from bridges.profiles.signals import (
    HARD_FORBIDDEN_REASONS,
    ProfileSignalCategory,
    ProfileSignalClassification,
    ProfileSignalClassifier,
    SegmentClassification,
)
from bridges.profiles.transactions import joined_transaction
from bridges.runtime.queue import RetryKind, TaskQueue
from bridges.storage.database import BridgesDatabase

AUTOMATIC_EXTRACTOR_VERSION = "profile-auto-v2"
AUTOMATIC_PRIVACY_NOTICE_VERSION = "profile-privacy-v2"
#: 首次自动记录说明：诚实披露混合策略（Issue 13）。明确自述、学习目标、
#: 行为观察与更正由本机规则处理；含义不够明确的表述可能使用全局配置的
#: Qwen 辅助识别。禁止用模糊的"AI 自动提取"覆盖两种来源。
AUTOMATIC_PRIVACY_NOTICE_TEXT = (
    "BridGes 会默认从你明确介绍自己的稳定信息中整理四维画像，"
    "仅用于后续相关回答。整理采用混合策略：明确的自我描述、学习目标、"
    "行为观察和更正由本机规则识别，不会调用模型；含义不够明确的表述"
    "可能使用全局配置的 Qwen 辅助识别。第三方、假设、敏感信息和一次性"
    "情绪不会写入。你可以随时查看、修改、撤回或关闭自动记录。"
)
PROFILE_EXTRACTION_QUEUE = "profile-extraction"
PROFILE_REPLAY_QUEUE = "profile-replay-v2"
PROFILE_REPLAY_SOURCE_HASH_PREFIX = ":replay-v1:"
PROFILE_CORRECTION_RULES_VERSION = "profile_correction_v1"
PROFILE_EXTRACTION_MAX_RETRIES = 3
#: 改进工单 17：每次真实模型调用的版本合同（Issue 03 接缝）。提示词与
#: 输入合同按精确证据区间升级；输出 Schema 保持 v2 的向后兼容可选字段。
#: 工单 41 真实探针发现模型不遵守字段枚举与 evidence_ref 约定（合同失败
#: 关闭），提示词升级 v4：显式给出字段/枚举/类型与 evidence_ref 取值。
PROFILE_EXTRACTION_PROMPT_VERSION = "profile-extraction-prompt-v4"
PROFILE_EXTRACTION_RECIPE_VERSION = "profile-extraction-recipe-v1"
PROFILE_EXTRACTION_CONTEXT_VERSION = "profile-extraction-context-v1"
PROFILE_EXTRACTION_QUALITY_POLICY_VERSION = "profile-extraction-quality-v1"
#: 邻近用户原文的最大条数：只用于解析回指，按实测预算可调。
PROFILE_EXTRACTION_MAX_NEIGHBORS = 2
#: 改进工单 07：账户级画像控制表（长期画像使用开关的持久状态）。
PROFILE_CONTROLS_TABLE = "profile_account_controls"
#: 控制更新请求两个字段都缺省时的统一错误文案（服务层与 API 层共用）。
PROFILE_CONTROLS_EMPTY_UPDATE_MESSAGE = "至少指定 recording_enabled 或 usage_enabled 之一。"


class ProfileUsageState(NamedTuple):
    """账户级长期画像使用开关的持久状态。

    ``version`` 从 1 起计：每一次真实变化递增，同值重复写入保持不变；
    从未有持久行时按 ``(True, 0, None)`` 读取（0 表示「从未变更」）。
    """

    enabled: bool
    version: int
    updated_at: datetime | None

# Issue 13 来源守卫稳定名：错误码与观测指标共用同一字面量，改名必须
# 同步两处，否则"错误码 ↔ 指标"的审计关联会静默漂移。
PROFILE_GUARD_LOCAL_UNEXPECTED_MODEL_CALL = "profile_local_unexpected_model_call"
PROFILE_GUARD_QWEN_MISSING_RUN_LOCK = "profile_qwen_missing_run_lock"
PROFILE_GUARD_SOURCE_MISMATCH = "profile_source_mismatch"
PROFILE_GUARD_LOCK_PERSIST_FAILED = "profile_lock_persist_failed"
_TRANSIENT_PROFILE_ERROR_CODES = frozenset(
    {
        "network_error",
        "region_error",
        "rate_limit",
        "transient",
        "profile_extraction_transient_failure",
        "profile_extraction_unexpected",
    }
)
_PERMANENT_PROFILE_ERROR_CODES = frozenset(
    {
        "auth_error",
        "client_error_400",
        "empty_response",
        "capability_not_verified",
        "no_adapter",
        "profile_extraction_contract_invalid",
        "profile_extraction_evidence_mismatch",
        "profile_extraction_failed",
        "profile_extraction_forbidden_value",
        "profile_extraction_observation_classification_invalid",
        "profile_extraction_self_statement_missing",
        "profile_extraction_privacy_blocked",
        "profile_extraction_source_invalidated",
        "profile_extraction_source_missing",
        "profile_extraction_version_unavailable",
        "safety_refusal",
        "structured_output_contract_invalid",
        "structured_output_parse_failed",
        "invalid_response_format",
        "unregistered_capability",
        "unsupported_structured_output_format",
        # Issue 13 来源守卫：本地分支出现模型调用、Qwen 分支缺锁、
        # 来源与锁证据漂移、锁持久化失败均失败关闭（不可重试）。
        PROFILE_GUARD_LOCAL_UNEXPECTED_MODEL_CALL,
        PROFILE_GUARD_QWEN_MISSING_RUN_LOCK,
        PROFILE_GUARD_SOURCE_MISMATCH,
        PROFILE_GUARD_LOCK_PERSIST_FAILED,
    }
)
_KNOWLEDGE_PROMOTION_WINDOW = timedelta(days=90)
_MAX_SLICE_ITEMS = 6
_MAX_EVIDENCE_QUOTE_LENGTH = 240
#: 改进工单 17：逐候选精确证据区间的长度上界；超界的候选不算精确证据。
_MAX_EVIDENCE_SPAN_LENGTH = 240
_MIN_PROMOTION_RELIABILITY = 0.6
_NEGATION_MARKERS = re.compile(r"(?:不|没|无|别|未|拒绝|避免|否)")
_TIME_EXPRESSIONS = re.compile(
    r"(?:下|本|这|上|明|后|昨|去)(?:周|月|年|天|日|次|轮)"
    r"|每天|每周|每月|每年|每次|每轮|以后|之后|下次|今后|今晚|明天|明年"
    r"|\d+\s*(?:分钟|小时|天|周|个月|年)"
)

_CONFIRMATION_SIGNAL = re.compile(
    r"(?:^|[，。；：\s])(?:对|是的|没错|确实)[，,：:]?\s*我"
)
_HOBBY_TERMS = frozenset(
    {
        "跑步",
        "游泳",
        "运动",
        "音乐",
        "乐器",
        "绘画",
        "画画",
        "摄影",
        "旅行",
        "旅游",
        "游戏",
        "烘焙",
        "做饭",
        "园艺",
        "电影",
        "追剧",
        "书法",
        "手工",
        "宠物",
        "爬山",
    }
)


class AutomaticProfileError(RuntimeError):
    """自动抽取失败；调用方应保留聊天主流程并安排重试。"""

    def __init__(
        self, code: str, message: str | None = None, *, retryable: bool = True
    ) -> None:
        self.code = code
        self.message = message or code
        self.retryable = retryable
        super().__init__(self.message)


class AutomaticProfileExtractor(Protocol):
    """一次只处理一条消息的抽取器接缝。"""

    version: str

    def extract(
        self,
        *,
        account_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        signal_classification: ProfileSignalClassification | None = None,
        lock_sink: Callable[[ModelRunLock], None] | None = None,
    ) -> ProfileExtractionOutput: ...


class AutomaticProfileRepository(ABC):
    """自动画像状态、观察、重试与首次说明的持久化端口。"""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]: ...

    def durable_queue(self) -> TaskQueue | None:
        """返回跨重启可恢复的重试队列；无持久化的实现返回空值。

        重试要跨进程恢复就必须有持久队列，因此「有没有队列」由仓库自己
        声明，调用方不再判断存储实现类型。默认实现没有队列。
        """

        return None

    @abstractmethod
    def get_run(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRun | None: ...

    @abstractmethod
    def save_run(self, run: ProfileExtractionRun) -> ProfileExtractionRun: ...

    @abstractmethod
    def list_runs(self, account_id: str | None = None) -> list[ProfileExtractionRun]: ...

    @abstractmethod
    def get_task(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRetryTask | None: ...

    @abstractmethod
    def save_task(
        self, task: ProfileExtractionRetryTask
    ) -> ProfileExtractionRetryTask: ...

    @abstractmethod
    def list_tasks(
        self, account_id: str | None = None
    ) -> list[ProfileExtractionRetryTask]: ...

    @abstractmethod
    def save_observation(self, observation: AutomaticProfileObservation) -> None: ...

    @abstractmethod
    def list_observations(
        self,
        account_id: str,
        dimension: FourDimension,
        normalized_value: str,
        *,
        since: datetime,
    ) -> list[AutomaticProfileObservation]: ...

    @abstractmethod
    def claim_privacy_notice(
        self, account_id: str, now: datetime
    ) -> ProfilePrivacyNotice | None: ...

    @abstractmethod
    def mark_message_tombstone(self, account_id: str, message_id: str) -> None: ...

    @abstractmethod
    def is_message_tombstoned(self, account_id: str, message_id: str) -> bool: ...

    @abstractmethod
    def block_recording(
        self, account_id: str, normalized_value: str | None, now: datetime
    ) -> None:
        """记录账户级或内容级的停止记录边界。"""

    @abstractmethod
    def is_recording_blocked(
        self, account_id: str, normalized_value: str | None = None
    ) -> bool:
        """检查当前账户或内容是否已被用户禁止记录。"""

    @abstractmethod
    def unblock_recording(self, account_id: str) -> bool:
        """解除账户级停止记录；返回是否确实移除了账户级阻止。"""

    @abstractmethod
    def get_profile_usage_state(self, account_id: str) -> ProfileUsageState:
        """返回账户的长期画像使用开关状态。

        没有持久化行时按默认 ``(True, 0, None)`` 读取：关闭使用是显式的
        用户控制，默认状态始终允许使用已记录的有效信息。
        """

    @abstractmethod
    def set_profile_usage_enabled(
        self, account_id: str, enabled: bool, now: datetime
    ) -> ProfileUsageState:
        """持久化长期画像使用开关；同值重复写入保持序号与时间（幂等）。"""

    @abstractmethod
    def delete_observations_for_record(
        self, account_id: str, dimension: FourDimension, normalized_value: str
    ) -> None:
        """删除与四维记录对应的观察。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_hash(account_id: str, message_id: str, content: str) -> str:
    return _hash("|".join((account_id, message_id, content)))


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{_hash('|'.join(parts))[:32]}"


def _signal_audit_version(
    pipeline_version: str,
    classification: ProfileSignalClassification,
    source: ProfileExtractionSource,
) -> str:
    """把受控分类元数据与稳定来源绑定到每条自动画像写入的版本字段。"""

    return (
        f"{pipeline_version}|category={classification.category.value}"
        f"|reason={classification.reason_code}"
        f"|source={source.value}"
    )


class _AttemptLockCollector:
    """收集一次抽取尝试中网关产生的全部模型运行锁。

    真实 ``ModelGateway.invoke`` 在成功与失败路径都会返回不可变
    ``ModelRunLock``；服务在提交业务写的同时按 attempt 序号持久化这些
    锁（Issue 13）。本地规则分支永不调用网关，收集器必须保持为空。
    """

    __slots__ = ("locks",)

    def __init__(self) -> None:
        self.locks: list[ModelRunLock] = []

    def __call__(self, lock: ModelRunLock) -> None:
        self.locks.append(lock)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n，。；：:、,;.!！？?"))


def _redact(text: str) -> str:
    """遮住常见联系信息和密钥样式，保留其余原话。"""

    quote = text.strip()
    quote = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<邮箱>", quote)
    quote = re.sub(r"(?<!\d)1\d{10}(?!\d)", "<手机号>", quote)
    quote = re.sub(
        r"(?i)(密码|密钥|token|api[_-]?key)\s*[:：=]\s*\S+",
        r"\1：<已隐藏>",
        quote,
    )
    return quote


def _evidence_quote(content: str, normalized_value: str) -> str:
    """保存有限长度的用户原话，并遮住常见联系信息和密钥样式。"""

    quote = _redact(content)
    if len(quote) <= _MAX_EVIDENCE_QUOTE_LENGTH:
        return quote
    position = quote.find(normalized_value)
    if position >= 0:
        start = max(0, position - 80)
        end = min(len(quote), start + _MAX_EVIDENCE_QUOTE_LENGTH - 1)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(quote) else ""
        return prefix + quote[start:end] + suffix
    return quote[: _MAX_EVIDENCE_QUOTE_LENGTH - 1] + "…"


def _privacy_directive(content: str) -> tuple[str, str | None] | None:
    """解析本 Issue 需要的全局/局部停止记录指令。"""

    text = _normalize(content)
    if re.fullmatch(r"(?:不要记录|不记录|不要再记录|不要记住)", text):
        return "account", None
    local = re.search(
        r"(?:这个|这条|这件事|这段).{0,12}?(?:不用记|不要记|不要记录|别记)",
        text,
    )
    if local is not None:
        remainder = _normalize(text[local.end() :])
        return "content", remainder or None
    suffix = re.search(r"(?:不用记|不要记|不要记录|别记)[，,：:]\s*(.+)$", text)
    if suffix is not None:
        return "content", _normalize(suffix.group(1)) or None
    return None


def has_probable_profile_signal(content: str) -> bool:
    """确定性预检：明显无画像信号的机器/空载荷零次调用。"""

    return ProfileSignalClassifier().classify(content).should_process


def _record_matches_question(
    record: FourDimensionProfileRecord, current_question: str | None
) -> bool:
    if not current_question or not current_question.strip():
        return True
    question = _normalize(current_question)
    content = _normalize(record.content)
    if content in question or question in content:
        return True
    terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", content)
    if any(term in question for term in terms):
        return True
    keyword_groups = {
        FourDimension.ACADEMIC_STATUS: ("学习", "课程", "考试", "升学", "学校"),
        FourDimension.KNOWLEDGE_INTEREST: ("知识", "学习", "研究", "物理", "化学"),
        FourDimension.HOBBY: ("兴趣", "爱好", "休闲", "运动", "喜欢"),
        FourDimension.STAGE_GOAL: ("目标", "计划", "安排", "考试", "职业"),
    }
    return any(keyword in question for keyword in keyword_groups[record.dimension])


def _extract_observation_topic(content: str) -> str | None:
    """从搜索/提问观察中去掉动作词和文档类型后保留主题。"""

    text = _normalize(content)
    text = re.sub(
        r"^(?:找|搜索|查找|检索|查|看|阅读|推荐|了解|介绍)\s*"
        r"(?:几篇|一些|相关的?|一份)?\s*",
        "",
        text,
    )
    text = re.sub(
        r"^(?:如何|怎么)\s*学(?:习)?\s*|^(?:什么是|为什么|能否|请问)\s*",
        "",
        text,
    )
    text = re.sub(
        r"\s*(?:的)?(?:论文|文献|文章|资料|教程|课程|书籍|是什么|是啥)$",
        "",
        text,
    )
    text = _normalize(text)
    return text if len(text) > 1 else None


def _is_hobby_value(value: str) -> bool:
    return any(term in value for term in _HOBBY_TERMS)


#: 兴趣/学习/研究句的本地规则；同一条消息允许多个匹配各自成事实（工单 17）。
_INTEREST_PATTERNS: tuple[str, ...] = (
    r"我对\s*([^。！？!?；;，,]+?)\s*(?:很|比较|特别)?感兴趣",
    r"我(?:现在|目前)?更喜欢\s*([^。！？!?；;，,]+)",
    r"我(?:很|比较|特别)?喜欢\s*([^。！？!?；;，,]+)",
    r"我(?:想|要|准备)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
    r"我(?:正在|在)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
    r"(?:^|[，,。；;])\s*(?:我\s*)?(?:也|还|又|同时)?\s*"
    r"(?:想|要|准备|正在|在)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
    r"(?:^|[，,。；;])\s*(?:我\s*)?(?:也|还|又|同时)\s*(?:很|比较|特别)?喜欢\s*"
    r"([^。！？!?；;，,]+)",
    r"(?:我\s*)?(?:正在|在)?\s*研究\s*([^。！？!?；;，,]+)",
    r"我(?:现在|目前)?不(?:太)?(?:喜欢|爱)\s*([^。！？!?；;，,]+)",
)
_GOAL_PATTERNS: tuple[str, ...] = (
    r"(?:我的|我这阶段的|我目前的)?目标(?:是|为)?\s*([^。！？!?；;，,]+)",
    r"我(?:计划|打算)\s*([^。！？!?；;，,]+)",
    r"(?:给|帮)我规划(?:一下|一份|一个)?\s*([^。！？!?；;，,]+)",
    r"(?:^|[，。；;])(?:目标|计划|打算|规划)(?:是|为|：|:)?\s*([^。！？!?；;，,]+)",
)
_ACADEMIC_PATTERNS: tuple[str, ...] = (
    r"我(?:现在|目前)?(?:在读|就读|是)\s*([^。！？!?；;，,]+)",
)


def _pattern_span(text: str, match: re.Match[str]) -> tuple[int, int]:
    """匹配片段在原文的码点区间；去掉首尾分隔符但保留关系词与否定。"""

    start = match.start()
    while start < match.end() and text[start] in "，。；;、, \t\r\n":
        start += 1
    end = match.end()
    while end > start and text[end - 1] in "，。；;、, \t\r\n":
        end -= 1
    return start, end


def _iter_pattern_matches(
    text: str, patterns: tuple[str, ...]
) -> Iterator[tuple[re.Match[str], str]]:
    """按模式顺序产出不重叠的匹配，保留每个匹配的完整分句。"""

    seen: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if not match.group(1):
                continue
            span = match.span()
            if any(
                span[0] < end and span[1] > start for start, end in seen
            ):
                continue
            seen.append(span)
            start, end = _pattern_span(text, match)
            yield match, _normalize(text[start:end])


def _span_is_hard_forbidden(
    start: int, end: int, segments: list[SegmentClassification]
) -> bool:
    """区间是否落在硬禁止片段内（引用/假设/第三方/敏感）。"""

    for segment in segments:
        if segment.classification.reason_code not in HARD_FORBIDDEN_REASONS:
            continue
        if start < segment.end and end > segment.start:
            return True
    return False


class RuleBasedAutomaticProfileExtractor:
    """高置信自述的本地抽取器；生产可替换为网关抽取器。

    Issue 13：本地规则分支绝不产生模型运行锁；``lock_sink`` 仅为满足
    公共抽取器接缝而接受，但永远不会被调用。改进工单 17：一条消息可产出
    零条或多条事实，每条携带精确原话区间；落在硬禁止片段内的匹配被丢弃。
    """

    version = AUTOMATIC_EXTRACTOR_VERSION

    def __init__(self, classifier: ProfileSignalClassifier | None = None) -> None:
        self._classifier = classifier or ProfileSignalClassifier()

    def extract(
        self,
        *,
        account_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        signal_classification: ProfileSignalClassification | None = None,
        lock_sink: Callable[[ModelRunLock], None] | None = None,
    ) -> ProfileExtractionOutput:
        del account_id, conversation_id, run_id, lock_sink
        classification = signal_classification or self._classifier.classify(content)
        if not classification.should_process:
            return ProfileExtractionOutput(items=[])
        body = content.strip()
        base = content.find(body) if body else 0
        segments = self._classifier.classify_segments(content)
        hard_forbidden = [
            segment
            for segment in segments
            if segment.classification.reason_code in HARD_FORBIDDEN_REASONS
        ]

        def _add(
            items: list[dict[str, Any]],
            *,
            dimension: FourDimension,
            value: str,
            fact_text: str | None,
            start: int,
            end: int,
        ) -> None:
            if not (1 < len(value) <= 200) or end <= start:
                return
            if _span_is_hard_forbidden(start, end, hard_forbidden):
                return
            items.append(
                {
                    "dimension": dimension,
                    "normalized_value": value,
                    "fact_text": fact_text,
                    "evidence_ref": message_id,
                    "reliability": 0.99,
                    "action": ProfileExtractionAction.CREATE,
                    "evidence_start": start,
                    "evidence_end": end,
                }
            )

        if classification.category == ProfileSignalCategory.BEHAVIOR_OBSERVATION:
            topic = _extract_observation_topic(content)
            if topic is None:
                return ProfileExtractionOutput(items=[])
            start = content.find(topic)
            if start < 0:
                return ProfileExtractionOutput(items=[])
            return ProfileExtractionOutput.model_validate(
                {
                    "items": [
                        {
                            "dimension": FourDimension.KNOWLEDGE_INTEREST,
                            "normalized_value": topic,
                            "action": ProfileExtractionAction.OBSERVE,
                            "evidence_ref": message_id,
                            "reliability": 0.99,
                            "evidence_start": start,
                            "evidence_end": start + len(topic),
                        }
                    ]
                }
            )
        if not classification.is_self_statement:
            return ProfileExtractionOutput(items=[])

        items: list[dict[str, Any]] = []
        for match, _clause in _iter_pattern_matches(body, _GOAL_PATTERNS):
            start, end = _pattern_span(body, match)
            _add(
                items,
                dimension=FourDimension.STAGE_GOAL,
                value=_normalize(match.group(1)),
                # 目标/学业的关系在规范化值里已可解析：镜像正文沿用规范值，
                # 与基线公开事实文本保持一致。
                fact_text=None,
                start=base + start,
                end=base + end,
            )
        for match, clause in _iter_pattern_matches(body, _INTEREST_PATTERNS):
            start, end = _pattern_span(body, match)
            interest = _normalize(match.group(1))
            dimension = (
                FourDimension.HOBBY
                if _is_hobby_value(interest)
                and not re.search(r"(?:学习|学|研究|专业|论文|知识|技术)", clause)
                else FourDimension.KNOWLEDGE_INTEREST
            )
            _add(
                items,
                dimension=dimension,
                # 否定偏好不能丢否定后写成肯定值（「我不喜欢长篇回答」≠
                # 「喜欢长篇回答」）：完整分句作为规范值保留否定。
                value=clause if _NEGATION_MARKERS.search(clause) else interest,
                # 完整分句保留关系词与否定：同一对象「喜欢」与「正在学习」
                # 是两条事实；「我不喜欢长篇回答」保留否定含义。
                fact_text=clause,
                start=base + start,
                end=base + end,
            )
        for match, _clause in _iter_pattern_matches(body, _ACADEMIC_PATTERNS):
            start, end = _pattern_span(body, match)
            _add(
                items,
                dimension=FourDimension.ACADEMIC_STATUS,
                value=_normalize(match.group(1)),
                fact_text=None,
                start=base + start,
                end=base + end,
            )
        if classification.reason_code.split("|")[0] == (
            "subject_omitted_academic_statement"
        ) and not any(
            item["dimension"] == FourDimension.ACADEMIC_STATUS for item in items
        ):
            first_clause = _normalize(re.split(r"[，。；;,]", body, maxsplit=1)[0])
            start = body.find(first_clause) if first_clause else -1
            if first_clause and start >= 0:
                _add(
                    items,
                    dimension=FourDimension.ACADEMIC_STATUS,
                    value=first_clause,
                    fact_text=None,
                    start=base + start,
                    end=base + start + len(first_clause),
                )

        if not items:
            return ProfileExtractionOutput(items=[])
        return ProfileExtractionOutput.model_validate({"items": items})


class GatewayAutomaticProfileExtractor:
    """经固定结构化能力执行一次画像抽取。

    Issue 13：每次真实 ``ModelGateway.invoke`` 返回的不可变运行锁通过
    ``lock_sink`` 交给调用方持久化——成功与失败路径都产生锁，绝不丢弃
    供应商调用证据。

    改进工单 17：调用携带运行额度快照（配置提供者存在时）与每次调用版本
    合同；必要邻近用户原文只作回指线索，不作为事实证据，当前用户消息
    才是抽取主体。
    """

    version = AUTOMATIC_EXTRACTOR_VERSION

    def __init__(
        self,
        gateway: ModelGateway,
        classifier: ProfileSignalClassifier | None = None,
        *,
        model_config_provider: RunModelConfigProvider | None = None,
        neighbor_reader: Callable[[str, str, str], list[str]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._classifier = classifier or ProfileSignalClassifier()
        # 改进工单 03/04：有配置提供者时按已验证运行额度执行最终载荷硬门；
        # 没有配置的内存/测试组合不伪造额度，只记录调用版本合同。
        self._model_config_provider = model_config_provider
        # 改进工单 17：邻近用户原文只用于解析「这个专业」等回指。
        self._neighbor_reader = neighbor_reader

    def extract(
        self,
        *,
        account_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        signal_classification: ProfileSignalClassification | None = None,
        lock_sink: Callable[[ModelRunLock], None] | None = None,
    ) -> ProfileExtractionOutput:
        classification = signal_classification or self._classifier.classify(content)
        if not classification.should_process:
            return ProfileExtractionOutput(items=[])
        neighbors: list[str] = []
        if self._neighbor_reader is not None:
            try:
                neighbors = [
                    neighbor
                    for neighbor in self._neighbor_reader(
                        account_id, conversation_id, message_id
                    )
                    if neighbor and neighbor.strip()
                ][:PROFILE_EXTRACTION_MAX_NEIGHBORS]
            except Exception:  # noqa: BLE001 - 回指线索失败不阻断抽取
                neighbors = []
        quota = (
            build_run_model_quota(self._model_config_provider.snapshot())
            if self._model_config_provider is not None
            else None
        )
        context = RunContextEnvelope(
            run_id=run_id,
            account_id=account_id,
            project_id=conversation_id,
            workflow_name="profile-extraction",
            workflow_version="2",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=_now(),
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "只抽取当前用户消息里用户关于自己的明确稳定信号。禁止第三方、"
                    "引用、假设、角色扮演、敏感信息与一次性情绪；邻近用户消息只用于"
                    "理解『这个专业』等回指，不能作为事实依据。"
                    '只输出一个 JSON 对象 {"items": [...]}，不要输出其他顶层字段。'
                    "每个元素必须且只能严格包含以下字段："
                    "dimension 只能是 academic_status/knowledge_interest/hobby/"
                    "stage_goal 之一；normalized_value 为字符串；fact_text 为字符串，"
                    "使用用户原话或原句片段（保留关系、否定、范围与原文明示时间），"
                    "不要以『用户』开头；"
                    f'evidence_ref 固定填写 "{message_id}"；'
                    "reliability 为 0 到 1 之间的小数（禁止 high/medium/low 等文字）；"
                    "action 只能是 create/update/observe/ignore 之一；"
                    "evidence_start 与 evidence_end 为整数，是支持该候选的当前消息 "
                    "Unicode 码点半开区间 [start, end)（区间原话必须真实支持事实；"
                    "模糊或行为线索用 observe）。"
                    '没有可抽取信号时输出 {"items": []}。'
                    f"本条消息的确定性分类为 {classification.category.value}，"
                    f"原因码为 {classification.reason_code}。"
                ),
            },
        ]
        if neighbors:
            messages.append(
                {
                    "role": "user",
                    "content": "（仅供回指的邻近用户消息，不得作为事实证据）\n"
                    + "\n".join(neighbors),
                }
            )
        messages.append({"role": "user", "content": content})
        result = self._gateway.invoke(
            "qwen_profile_extraction",
            "1",
            context,
            payload={
                "messages": messages,
                "output_contract": "profile-extraction-v2",
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 512,
            },
            model_quota=quota,
            call_contract=CallContractVersions(
                prompt_version=PROFILE_EXTRACTION_PROMPT_VERSION,
                input_schema_version="profile-message-v2",
                output_schema_version="profile-extraction-v2",
                recipe_version=PROFILE_EXTRACTION_RECIPE_VERSION,
                context_compile_version=PROFILE_EXTRACTION_CONTEXT_VERSION,
                quality_policy_version=PROFILE_EXTRACTION_QUALITY_POLICY_VERSION,
                estimate_version=TOKEN_ESTIMATE_VERSION,
                quota_version=MODEL_QUOTA_VERSION,
            ),
        )
        # 每次真实调用（无论成败）的不可变锁都必须交给调用方持久化。
        if result.lock is not None and lock_sink is not None:
            lock_sink(result.lock)
        if result.status == ModelCallStatus.RETRYABLE_FAIL:
            error_code = result.error_code or "profile_extraction_transient_failure"
            raise AutomaticProfileError(
                error_code,
                result.error_message or error_code,
                retryable=True,
            )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "profile_extraction_failed"
            retryable = error_code in {
                "network_error",
                "region_error",
                "rate_limit",
                "transient",
            }
            raise AutomaticProfileError(
                error_code,
                f"{error_code}: {result.error_message or error_code}",
                retryable=retryable,
            )
        try:
            return ProfileExtractionOutput.model_validate(result.output)
        except Exception as exc:  # noqa: BLE001 - 合同错误不重复发送相同请求
            raise AutomaticProfileError(
                "profile_extraction_contract_invalid",
                "Profile extraction output did not match its contract.",
                retryable=False,
            ) from exc


class InMemoryAutomaticProfileRepository(AutomaticProfileRepository):
    """领域测试与无数据库开发模式使用的持久化替身。"""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str, str, str], ProfileExtractionRun] = {}
        self._tasks: dict[tuple[str, str, str, str], ProfileExtractionRetryTask] = {}
        self._observations: dict[str, AutomaticProfileObservation] = {}
        self._disclosures: set[str] = set()
        self._tombstones: set[tuple[str, str]] = set()
        self._account_recording_blocks: set[str] = set()
        self._content_recording_blocks: set[tuple[str, str]] = set()
        # Issue 07：账户 → 长期画像使用开关的持久状态。
        self._profile_usage_states: dict[str, ProfileUsageState] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = (
            copy.deepcopy(self._runs),
            copy.deepcopy(self._tasks),
            copy.deepcopy(self._observations),
            copy.deepcopy(self._disclosures),
            copy.deepcopy(self._tombstones),
            copy.deepcopy(self._account_recording_blocks),
            copy.deepcopy(self._content_recording_blocks),
            copy.deepcopy(self._profile_usage_states),
        )
        try:
            yield
        except BaseException:
            (
                self._runs,
                self._tasks,
                self._observations,
                self._disclosures,
                self._tombstones,
                self._account_recording_blocks,
                self._content_recording_blocks,
                self._profile_usage_states,
            ) = snapshot
            raise

    @staticmethod
    def _key(
        account_id: str, message_id: str, version: str, source_hash: str
    ) -> tuple[str, str, str, str]:
        return account_id, message_id, version, source_hash

    def get_run(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRun | None:
        return self._runs.get(self._key(account_id, message_id, version, source_hash))

    def save_run(self, run: ProfileExtractionRun) -> ProfileExtractionRun:
        self._runs[
            self._key(
                run.account_id, run.message_id, run.extractor_version, run.source_hash
            )
        ] = run
        return run

    def list_runs(self, account_id: str | None = None) -> list[ProfileExtractionRun]:
        return sorted(
            (
                run
                for run in self._runs.values()
                if account_id is None or run.account_id == account_id
            ),
            key=lambda run: run.created_at,
        )

    def get_task(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRetryTask | None:
        return self._tasks.get(self._key(account_id, message_id, version, source_hash))

    def save_task(self, task: ProfileExtractionRetryTask) -> ProfileExtractionRetryTask:
        self._tasks[
            self._key(
                task.account_id,
                task.message_id,
                task.extractor_version,
                task.source_hash,
            )
        ] = task
        return task

    def list_tasks(
        self, account_id: str | None = None
    ) -> list[ProfileExtractionRetryTask]:
        tasks = [
            task
            for task in self._tasks.values()
            if account_id is None or task.account_id == account_id
        ]
        return sorted(tasks, key=lambda task: task.created_at)

    def save_observation(self, observation: AutomaticProfileObservation) -> None:
        self._observations[observation.observation_id] = observation

    def list_observations(
        self,
        account_id: str,
        dimension: FourDimension,
        normalized_value: str,
        *,
        since: datetime,
    ) -> list[AutomaticProfileObservation]:
        return [
            observation
            for observation in self._observations.values()
            if observation.account_id == account_id
            and observation.dimension == dimension
            and observation.normalized_value == normalized_value
            and observation.created_at >= since
        ]

    def claim_privacy_notice(
        self, account_id: str, now: datetime
    ) -> ProfilePrivacyNotice | None:
        if account_id in self._disclosures:
            return None
        self._disclosures.add(account_id)
        return ProfilePrivacyNotice(
            version=AUTOMATIC_PRIVACY_NOTICE_VERSION,
            text=AUTOMATIC_PRIVACY_NOTICE_TEXT,
            shown_at=now,
        )

    def mark_message_tombstone(self, account_id: str, message_id: str) -> None:
        self._tombstones.add((account_id, message_id))

    def is_message_tombstoned(self, account_id: str, message_id: str) -> bool:
        return (account_id, message_id) in self._tombstones

    def block_recording(
        self, account_id: str, normalized_value: str | None, now: datetime
    ) -> None:
        del now
        if normalized_value is None:
            self._account_recording_blocks.add(account_id)
        else:
            self._content_recording_blocks.add((account_id, _normalize(normalized_value)))

    def is_recording_blocked(
        self, account_id: str, normalized_value: str | None = None
    ) -> bool:
        if account_id in self._account_recording_blocks:
            return True
        if normalized_value is None:
            return False
        value = _normalize(normalized_value)
        return any(
            blocked == value or blocked in value or value in blocked
            for blocked_account, blocked in self._content_recording_blocks
            if blocked_account == account_id
        )

    def unblock_recording(self, account_id: str) -> bool:
        if account_id not in self._account_recording_blocks:
            return False
        self._account_recording_blocks.discard(account_id)
        return True

    def get_profile_usage_state(self, account_id: str) -> ProfileUsageState:
        return self._profile_usage_states.get(
            account_id, ProfileUsageState(enabled=True, version=0, updated_at=None)
        )

    def set_profile_usage_enabled(
        self, account_id: str, enabled: bool, now: datetime
    ) -> ProfileUsageState:
        current = self.get_profile_usage_state(account_id)
        if current.enabled == enabled:
            return current
        state = ProfileUsageState(
            enabled=enabled,
            version=current.version + 1,
            updated_at=now,
        )
        self._profile_usage_states[account_id] = state
        return state

    def delete_observations_for_record(
        self, account_id: str, dimension: FourDimension, normalized_value: str
    ) -> None:
        self._observations = {
            observation_id: observation
            for observation_id, observation in self._observations.items()
            if not (
                observation.account_id == account_id
                and observation.dimension == dimension
                and observation.normalized_value == normalized_value
            )
        }


class SqliteAutomaticProfileRepository(AutomaticProfileRepository):
    """SQLite 持久化实现；所有查询显式绑定账户。"""

    def __init__(self, database: BridgesDatabase, *, initialize: bool = True) -> None:
        self.database = database
        if initialize:
            self.database.initialize()

    def transaction(self) -> AbstractContextManager[None]:
        """事务边界；同一连接上已有事务时并入外层。

        抽取记账、四维记录与原子条目共用一个 ``BridgesDatabase``，因此并入
        规则与另外两个画像仓库一致：跨记录写入落在同一个提交里，单连接
        SQLite 也不会出现嵌套 ``BEGIN``。
        """

        return joined_transaction(self.database)

    def durable_queue(self) -> TaskQueue | None:
        """持久化的提取重试队列；重试任务与运行记录同库同事务。"""

        return TaskQueue(self.database, default_lease_seconds=60)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _run(row: Any) -> ProfileExtractionRun:
        return ProfileExtractionRun(
            extraction_id=str(row["extraction_id"]),
            account_id=str(row["account_id"]),
            message_id=str(row["message_id"]),
            extractor_version=str(row["extractor_version"]),
            source_hash=str(row["source_hash"]),
            source_snapshot=str(row["source_snapshot"]),
            status=ProfileExtractionStatus(str(row["status"])),
            outcome=ProfileExtractionOutcome(str(row["outcome"])),
            attempts=int(row["attempts"]),
            committed_record_ids=json.loads(str(row["record_ids_json"])),
            observed_count=int(row["observed_count"]),
            last_error=row["last_error"],
            source=(
                ProfileExtractionSource(str(row["source"]))
                if row["source"] is not None
                else None
            ),
            created_at=SqliteAutomaticProfileRepository._dt(str(row["created_at"])),
            updated_at=SqliteAutomaticProfileRepository._dt(str(row["updated_at"])),
        )

    @staticmethod
    def _task(row: Any) -> ProfileExtractionRetryTask:
        return ProfileExtractionRetryTask(
            task_id=str(row["task_id"]),
            account_id=str(row["account_id"]),
            message_id=str(row["message_id"]),
            extractor_version=str(row["extractor_version"]),
            source_hash=str(row["source_hash"]),
            status=ProfileExtractionStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            last_error=row["last_error"],
            created_at=SqliteAutomaticProfileRepository._dt(str(row["created_at"])),
            updated_at=SqliteAutomaticProfileRepository._dt(str(row["updated_at"])),
        )

    def get_run(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRun | None:
        row = (
            self.database.scoped(account_id)
            .execute(
                "SELECT * FROM profile_extraction_runs WHERE account_id = ? AND message_id = ? AND extractor_version = ? AND source_hash = ?",
                (account_id, message_id, version, source_hash),
            )
            .fetchone()
        )
        return self._run(row) if row is not None else None

    def save_run(self, run: ProfileExtractionRun) -> ProfileExtractionRun:
        self.database.scoped(run.account_id).execute(
            "INSERT INTO profile_extraction_runs ("
            "extraction_id, account_id, message_id, extractor_version, "
            "source_hash, source_snapshot, status, outcome, attempts, "
            "record_ids_json, observed_count, last_error, source, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(extraction_id) DO UPDATE SET "
            "status=excluded.status, outcome=excluded.outcome, "
            "attempts=excluded.attempts, record_ids_json=excluded.record_ids_json, "
            "observed_count=excluded.observed_count, last_error=excluded.last_error, "
            "updated_at=excluded.updated_at",
            (
                run.extraction_id,
                run.account_id,
                run.message_id,
                run.extractor_version,
                run.source_hash,
                run.source_snapshot,
                run.status.value,
                run.outcome.value,
                run.attempts,
                json.dumps(run.committed_record_ids),
                run.observed_count,
                run.last_error,
                run.source.value if run.source is not None else None,
                self._iso(run.created_at),
                self._iso(run.updated_at),
            ),
        )
        return run

    def list_runs(self, account_id: str | None = None) -> list[ProfileExtractionRun]:
        if account_id is None:
            rows = self.database.connection.execute(
                "SELECT * FROM profile_extraction_runs ORDER BY created_at"
            ).fetchall()
        else:
            rows = (
                self.database.scoped(account_id)
                .execute(
                    "SELECT * FROM profile_extraction_runs WHERE account_id = ? ORDER BY created_at",
                    (account_id,),
                )
                .fetchall()
            )
        return [self._run(row) for row in rows]

    def get_task(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRetryTask | None:
        row = (
            self.database.scoped(account_id)
            .execute(
                "SELECT * FROM profile_extraction_tasks WHERE account_id = ? AND message_id = ? AND extractor_version = ? AND source_hash = ?",
                (account_id, message_id, version, source_hash),
            )
            .fetchone()
        )
        return self._task(row) if row is not None else None

    def save_task(self, task: ProfileExtractionRetryTask) -> ProfileExtractionRetryTask:
        self.database.scoped(task.account_id).execute(
            "INSERT INTO profile_extraction_tasks (task_id, account_id, message_id, extractor_version, source_hash, status, attempts, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts, last_error=excluded.last_error, updated_at=excluded.updated_at",
            (
                task.task_id,
                task.account_id,
                task.message_id,
                task.extractor_version,
                task.source_hash,
                task.status.value,
                task.attempts,
                task.last_error,
                self._iso(task.created_at),
                self._iso(task.updated_at),
            ),
        )
        return task

    def list_tasks(
        self, account_id: str | None = None
    ) -> list[ProfileExtractionRetryTask]:
        if account_id is None:
            rows = self.database.connection.execute(
                "SELECT * FROM profile_extraction_tasks ORDER BY created_at"
            ).fetchall()
        else:
            rows = (
                self.database.scoped(account_id)
                .execute(
                    "SELECT * FROM profile_extraction_tasks WHERE account_id = ? ORDER BY created_at",
                    (account_id,),
                )
                .fetchall()
            )
        return [self._task(row) for row in rows]

    def save_observation(self, observation: AutomaticProfileObservation) -> None:
        self.database.scoped(observation.account_id).execute(
            "INSERT INTO profile_extraction_observations (observation_id, account_id, message_id, extractor_version, dimension, normalized_value, evidence_ref, reliability, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, message_id, extractor_version, dimension, normalized_value) DO NOTHING",
            (
                observation.observation_id,
                observation.account_id,
                observation.message_id,
                observation.extractor_version,
                observation.dimension.value,
                observation.normalized_value,
                observation.evidence_ref,
                observation.reliability,
                (
                    observation.source.value
                    if observation.source is not None
                    else None
                ),
                self._iso(observation.created_at),
            ),
        )

    def list_observations(
        self,
        account_id: str,
        dimension: FourDimension,
        normalized_value: str,
        *,
        since: datetime,
    ) -> list[AutomaticProfileObservation]:
        rows = (
            self.database.scoped(account_id)
            .execute(
                "SELECT * FROM profile_extraction_observations WHERE account_id = ? AND dimension = ? AND normalized_value = ? AND created_at >= ? ORDER BY created_at",
                (account_id, dimension.value, normalized_value, self._iso(since)),
            )
            .fetchall()
        )
        return [
            AutomaticProfileObservation(
                observation_id=str(row["observation_id"]),
                account_id=str(row["account_id"]),
                message_id=str(row["message_id"]),
                extractor_version=str(row["extractor_version"]),
                dimension=FourDimension(str(row["dimension"])),
                normalized_value=str(row["normalized_value"]),
                evidence_ref=str(row["evidence_ref"]),
                reliability=float(row["reliability"]),
                source=(
                    ProfileExtractionSource(str(row["source"]))
                    if row["source"] is not None
                    else None
                ),
                created_at=self._dt(str(row["created_at"])),
            )
            for row in rows
        ]

    def claim_privacy_notice(
        self, account_id: str, now: datetime
    ) -> ProfilePrivacyNotice | None:
        cursor = self.database.scoped(account_id).execute(
            "INSERT INTO profile_privacy_disclosures (account_id, disclosure_version, ever_shown, shown_at) VALUES (?, ?, 1, ?) ON CONFLICT(account_id) DO NOTHING",
            (account_id, AUTOMATIC_PRIVACY_NOTICE_VERSION, self._iso(now)),
        )
        if cursor.rowcount != 1:
            return None
        return ProfilePrivacyNotice(
            version=AUTOMATIC_PRIVACY_NOTICE_VERSION,
            text=AUTOMATIC_PRIVACY_NOTICE_TEXT,
            shown_at=now,
        )

    def mark_message_tombstone(self, account_id: str, message_id: str) -> None:
        self.database.scoped(account_id).execute(
            "INSERT INTO profile_extraction_tombstones (account_id, message_id, created_at) VALUES (?, ?, ?) ON CONFLICT(account_id, message_id) DO NOTHING",
            (account_id, message_id, self._iso(_now())),
        )

    def is_message_tombstoned(self, account_id: str, message_id: str) -> bool:
        return (
            self.database.scoped(account_id)
            .execute(
                "SELECT 1 FROM profile_extraction_tombstones WHERE account_id = ? AND message_id = ?",
                (account_id, message_id),
            )
            .fetchone()
            is not None
        )

    def block_recording(
        self, account_id: str, normalized_value: str | None, now: datetime
    ) -> None:
        scope = "account" if normalized_value is None else "content"
        stored_value = "" if normalized_value is None else _normalize(normalized_value)
        block_id = _stable_id("profile-privacy-block", account_id, scope, stored_value)
        self.database.scoped(account_id).execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id, scope, normalized_value) DO NOTHING",
            (block_id, account_id, scope, stored_value, self._iso(now)),
        )

    def is_recording_blocked(
        self, account_id: str, normalized_value: str | None = None
    ) -> bool:
        value = "" if normalized_value is None else _normalize(normalized_value)
        return (
            self.database.scoped(account_id)
            .execute(
                "SELECT 1 FROM profile_extraction_privacy_blocks "
                "WHERE account_id = ? AND scope = 'account' LIMIT 1",
                (account_id,),
            )
            .fetchone()
            is not None
            or (
                normalized_value is not None
                and self.database.scoped(account_id)
                .execute(
                    "SELECT 1 FROM profile_extraction_privacy_blocks "
                    "WHERE account_id = ? AND scope = 'content' AND "
                    "(normalized_value = ? OR instr(?, normalized_value) > 0 "
                    "OR instr(normalized_value, ?) > 0) LIMIT 1",
                    (account_id, value, value, value),
                )
                .fetchone()
                is not None
            )
        )

    def unblock_recording(self, account_id: str) -> bool:
        cursor = self.database.scoped(account_id).execute(
            "DELETE FROM profile_extraction_privacy_blocks "
            "WHERE account_id = ? AND scope = 'account'",
            (account_id,),
        )
        return cursor.rowcount > 0

    def get_profile_usage_state(self, account_id: str) -> ProfileUsageState:
        row = self.database.scoped(account_id).execute(
            "SELECT profile_usage_enabled, usage_control_version, updated_at "
            f"FROM {PROFILE_CONTROLS_TABLE} WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return ProfileUsageState(enabled=True, version=0, updated_at=None)
        return ProfileUsageState(
            enabled=bool(row["profile_usage_enabled"]),
            version=int(row["usage_control_version"]),
            updated_at=self._dt(str(row["updated_at"])),
        )

    def set_profile_usage_enabled(
        self, account_id: str, enabled: bool, now: datetime
    ) -> ProfileUsageState:
        # 单条原子 upsert：值不变时 WHERE 不命中，序号与时间保持不变（幂等）；
        # 值变化时序号在既有行上递增，不依赖先读后写的窗口。
        self.database.scoped(account_id).execute(
            f"INSERT INTO {PROFILE_CONTROLS_TABLE} "
            "(account_id, profile_usage_enabled, controls_version, "
            " usage_control_version, updated_at) "
            "SELECT ?, ?, ?, 1, ? WHERE ? = 0 OR EXISTS ("
            f"SELECT 1 FROM {PROFILE_CONTROLS_TABLE} WHERE account_id = ?) "
            "ON CONFLICT(account_id) DO UPDATE SET "
            "profile_usage_enabled = excluded.profile_usage_enabled, "
            f"usage_control_version = {PROFILE_CONTROLS_TABLE}"
            ".usage_control_version + 1, "
            "updated_at = excluded.updated_at "
            "WHERE profile_usage_enabled != excluded.profile_usage_enabled",
            (
                account_id, int(enabled), PROFILE_CONTROLS_VERSION,
                self._iso(now), int(enabled), account_id,
            ),
        )
        state = self.get_profile_usage_state(account_id)
        if state.enabled != enabled:
            raise RuntimeError(
                f"长期画像使用开关写入后未能按原值读回：{account_id}"
            )
        return state

    def delete_observations_for_record(
        self, account_id: str, dimension: FourDimension, normalized_value: str
    ) -> None:
        self.database.scoped(account_id).execute(
            "DELETE FROM profile_extraction_observations "
            "WHERE account_id = ? AND dimension = ? AND normalized_value = ?",
            (account_id, dimension.value, normalized_value),
        )


class AutomaticProfileService:
    """消息预处理的单一编排入口。"""

    def __init__(
        self,
        *,
        four_dimension_service: FourDimensionProfileService,
        repository: AutomaticProfileRepository,
        extractor: AutomaticProfileExtractor | None = None,
        slice_repository: ProfileRepository | None = None,
        message_reader: Callable[[str, str], Any | None] | None = None,
        classifier: ProfileSignalClassifier | None = None,
        observability_service: ObservabilityService | None = None,
        queue_name: str = PROFILE_EXTRACTION_QUEUE,
        lock_recorder: ModelRunLockRecorder | None = None,
        atomic_profile_service: AtomicProfileService | None = None,
    ) -> None:
        self._four_dimensions = four_dimension_service
        # V2 Issue 08：原子条目是用户可见的长期信息列表；挂载后自动抽取
        # 在写四维记录的同时镜像成无类别条目，并处理本轮「记住／忘掉」。
        self._atomic_profiles = atomic_profile_service
        # Issue 04：四维来源与原子镜像的提交边界归属；本类不再判断仓库
        # 类型、连接或事务开关，只决定抽取、证据校验与有界重试。
        self._commit = ProfileCommit(
            state=repository,
            records=four_dimension_service,
            items=atomic_profile_service,
        )
        self._repository = repository
        self._classifier = classifier or ProfileSignalClassifier()
        self._extractor = extractor or RuleBasedAutomaticProfileExtractor(self._classifier)
        self._slice_repository = slice_repository
        self._message_reader = message_reader
        self._observability = observability_service
        self._queue_name = queue_name
        # Issue 13：仅 qwen_model 分支持久化模型运行锁；缺 recorder 的
        # 组合（内存测试/无审计存储）不伪造锁，也不产生假调用证据。
        self._lock_recorder = lock_recorder
        self._capability_degraded_reason: str | None = None
        self._queue = repository.durable_queue()
        if self._queue is not None:
            self._queue.set_lease_seconds(self._queue_name, 60)
            self._recover_inflight_runs()

    @property
    def extractor_version(self) -> str:
        """返回绑定分类策略的不可变抽取流水线版本。"""

        return f"{self._extractor.version}+signal-{self.classifier_version}"

    @property
    def classifier_version(self) -> str:
        return self._classifier.version

    @property
    def capability_degraded_reason(self) -> str | None:
        """返回最近一次稳定的网关降级码；没有则返回空值。"""

        return self._capability_degraded_reason

    @staticmethod
    def _error_code(error: BaseException | str) -> str:
        if isinstance(error, str):
            return "profile_extraction_unexpected"
        code = getattr(error, "code", None)
        return str(code) if code else "profile_extraction_unexpected"

    @classmethod
    def _is_retryable_error(cls, error: BaseException | str) -> bool:
        if isinstance(error, AutomaticProfileError):
            return error.retryable
        if isinstance(error, str):
            return True
        code = cls._error_code(error)
        if code in _PERMANENT_PROFILE_ERROR_CODES:
            return False
        if code in _TRANSIENT_PROFILE_ERROR_CODES:
            return True
        return True

    @staticmethod
    def _is_permanent_code(code: str) -> bool:
        return code in _PERMANENT_PROFILE_ERROR_CODES or code.startswith("client_error_")

    @classmethod
    def _safe_error_code(cls, error: BaseException | str) -> str:
        return cls._error_code(error)

    def _audit_outcome(
        self,
        run: ProfileExtractionRun,
        *,
        result: AuditResult,
        reason: str | None = None,
    ) -> None:
        if self._observability is None:
            return
        self._observability.record_profile_outcome(
            outcome=run.outcome.value,
            reason=reason or run.outcome.value,
            exhausted=run.status == ProfileExtractionStatus.EXHAUSTED,
            source=run.source.value if run.source is not None else None,
        )
        self._observability.record_profile_queue_depth(
            sum(
                task.status
                in {ProfileExtractionStatus.PENDING, ProfileExtractionStatus.RUNNING}
                for task in self._repository.list_tasks(run.account_id)
            )
        )
        self._observability.log_audit(
            actor_account_id=run.account_id,
            action=AuditAction.PROFILE_AUTO_WRITE,
            result=result,
            reason=reason or run.outcome.value,
            details={
                "extraction_id": run.extraction_id,
                "message_id": run.message_id,
                "outcome": run.outcome.value,
                "attempts": run.attempts,
                "observed_count": run.observed_count,
                "committed_record_count": len(run.committed_record_ids),
                "source": run.source.value if run.source is not None else None,
            },
        )

    def _audit_correction_outcome(
        self, run: ProfileExtractionRun, status: ProfileCorrectionStatus
    ) -> None:
        """记录纠正状态指标，不把账户、消息或画像正文送入观测审计。"""

        if self._observability is None:
            return
        self._observability.record_profile_outcome(
            outcome=run.outcome.value,
            reason=f"{PROFILE_CORRECTION_RULES_VERSION}_{status.value}",
            exhausted=run.status == ProfileExtractionStatus.EXHAUSTED,
            source=run.source.value if run.source is not None else None,
        )
        self._observability.record_profile_queue_depth(
            sum(
                task.status
                in {ProfileExtractionStatus.PENDING, ProfileExtractionStatus.RUNNING}
                for task in self._repository.list_tasks(run.account_id)
            )
        )

    def _recover_inflight_runs(self) -> None:
        """恢复进程中断时尚未落入重试任务的运行记录。"""

        if self._queue is None:
            return
        for run in self._repository.list_runs():
            if run.status != ProfileExtractionStatus.RUNNING:
                continue
            if (
                self._queue_name == PROFILE_REPLAY_QUEUE
                and run.extractor_version != self.extractor_version
            ):
                continue
            task = self._repository.get_task(
                run.account_id, run.message_id, run.extractor_version, run.source_hash
            )
            if task is not None:
                continue
            now = _now()
            recovery_code = "profile_extraction_recovered_after_restart"
            task = ProfileExtractionRetryTask(
                task_id=_stable_id(
                    "profile-retry",
                    run.account_id,
                    run.message_id,
                    run.extractor_version,
                    run.source_hash,
                ),
                account_id=run.account_id,
                message_id=run.message_id,
                extractor_version=run.extractor_version,
                source_hash=run.source_hash,
                status=ProfileExtractionStatus.PENDING,
                attempts=run.attempts,
                last_error=recovery_code,
                created_at=now,
                updated_at=now,
            )
            run.status = ProfileExtractionStatus.PENDING
            run.outcome = ProfileExtractionOutcome.PENDING_RETRY
            run.last_error = recovery_code
            run.updated_at = now
            with self._repository.transaction():
                self._repository.save_run(run)
                self._repository.save_task(task)
                self._queue.enqueue(
                    self._queue_name,
                    task.task_id,
                    payload={
                        "account_id": run.account_id,
                        "message_id": run.message_id,
                        "extractor_version": run.extractor_version,
                        "source_hash": run.source_hash,
                    },
                )

    @staticmethod
    def _success_outcome(
        committed_record_ids: list[str], observed_count: int
    ) -> ProfileExtractionOutcome:
        if committed_record_ids:
            return ProfileExtractionOutcome.SUCCEEDED_WRITTEN
        if observed_count:
            return ProfileExtractionOutcome.SUCCEEDED_OBSERVED
        return ProfileExtractionOutcome.SUCCEEDED_EMPTY

    def _decide_source(
        self, signal_classification: ProfileSignalClassification
    ) -> ProfileExtractionSource:
        """把分类结果映射为稳定抽取来源（Issue 13）。

        - ``should_process=False``（no_signal/forbidden）与 ``is_local``
          高置信信号永远走本地规则，绝不调用网关；
        - 只有网关抽取器 + 非本地（歧义）信号才进入 ``qwen_model``。
        """

        if not signal_classification.should_process:
            return ProfileExtractionSource.LOCAL_RULE
        if (
            isinstance(self._extractor, GatewayAutomaticProfileExtractor)
            and not signal_classification.is_local
        ):
            return ProfileExtractionSource.QWEN_MODEL
        return ProfileExtractionSource.LOCAL_RULE

    def _gateway_attempted(
        self, signal_classification: ProfileSignalClassification
    ) -> bool:
        return self._decide_source(signal_classification) == (
            ProfileExtractionSource.QWEN_MODEL
        )

    def _record_source_guard(self, guard: str) -> None:
        """记录来源守卫告警指标（不携带账户、消息或正文）。"""

        if self._observability is None:
            return
        self._observability.record_profile_source_guard(guard)

    def _enforce_source_evidence(
        self,
        *,
        source: ProfileExtractionSource,
        attempt_locks: list[ModelRunLock],
    ) -> None:
        """来源守卫：本地分支出现模型调用或 Qwen 分支缺锁即失败关闭。

        ``local_rule`` 分支的调用数与锁数都必须为 0；``qwen_model``
        分支每次真实尝试必须至少一条运行锁。守卫失败抛出不可重试的
        稳定错误码，由既有状态机把对应 run 置为 EXHAUSTED。
        """

        if source == ProfileExtractionSource.LOCAL_RULE:
            if attempt_locks:
                self._record_source_guard(PROFILE_GUARD_LOCAL_UNEXPECTED_MODEL_CALL)
                self._record_source_guard(PROFILE_GUARD_SOURCE_MISMATCH)
                raise AutomaticProfileError(
                    PROFILE_GUARD_LOCAL_UNEXPECTED_MODEL_CALL,
                    "本地规则分支不应产生模型调用",
                    retryable=False,
                )
            return
        if not attempt_locks:
            self._record_source_guard(PROFILE_GUARD_QWEN_MISSING_RUN_LOCK)
            self._record_source_guard(PROFILE_GUARD_SOURCE_MISMATCH)
            raise AutomaticProfileError(
                PROFILE_GUARD_QWEN_MISSING_RUN_LOCK,
                "Qwen 分支缺少模型运行锁",
                retryable=False,
            )

    def _persist_attempt_locks(
        self,
        attempt_locks: list[ModelRunLock],
        *,
        run: ProfileExtractionRun,
        attempt_ordinal: int,
        strict: bool = True,
    ) -> None:
        """把一次 qwen_model 尝试的全部运行锁按序号持久化（Issue 10/13）。

        调用方必须持有事务（成功路径与业务写同事务提交；失败/重试路径与
        run/task 状态同事务提交），保证"声称调用已完成则锁必然存在"。
        ``strict=True``（成功路径）时锁持久化失败抛出稳定错误码并失败
        关闭；``strict=False``（失败/重试路径）时只记录告警，保留既有
        可恢复状态，避免把证据存储故障扩散成整条消息的不可恢复失败。
        """

        if self._lock_recorder is None or not attempt_locks:
            return
        # 来源不变量：local_rule 分支的锁绝不落库（0 调用 0 锁）。即使
        # 上游守卫失败关闭路径把收集到的锁带到这里，也不得写入审计库。
        if run.source != ProfileExtractionSource.QWEN_MODEL:
            self._record_source_guard(PROFILE_GUARD_SOURCE_MISMATCH)
            return
        for lock in attempt_locks:
            try:
                self._lock_recorder.record(
                    lock,
                    business_ref=BusinessRef(
                        object_type="profile_extraction_run",
                        object_id=run.extraction_id,
                        operation="extract",
                        attempt_ordinal=attempt_ordinal,
                        is_primary=attempt_ordinal == 1,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - 锁证据写入失败须显式处理
                self._record_source_guard(PROFILE_GUARD_LOCK_PERSIST_FAILED)
                if not strict:
                    return
                raise AutomaticProfileError(
                    PROFILE_GUARD_LOCK_PERSIST_FAILED,
                    f"画像模型运行锁持久化失败：{self._safe_error_code(exc)}",
                    retryable=False,
                ) from exc

    def preprocess_message(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        mode: str,
    ) -> ProfilePreprocessResult:
        """同步预处理入口：控制指令 + 立即执行一次普通抽取。

        改进工单 17：生产聊天路径改为回答前只做同步控制
        （``process_synchronous_controls``），普通提取由
        ``schedule_message_extraction`` 在回答正常完成后异步提交。本入口
        保留「控制 + 立即执行一次」语义，供离线重放、脚本与既有测试使用；
        两条路径共用同一执行核心，模型调用都在写事务外完成。
        """

        control_result = self.process_synchronous_controls(
            account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            run_id=run_id,
            mode=mode,
        )
        if control_result is not None:
            return control_result
        return self._run_scheduled_now(
            account_id=account_id,
            message_id=message_id,
            content=content,
        )

    def process_synchronous_controls(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        mode: str,
    ) -> ProfilePreprocessResult | None:
        """回答生成前必须同步完成的画像控制（改进工单 17）。

        顺序与合同保持不变：停止记录指令 → 记录许可 → 墓碑 → 显式
        「记住/忘掉」→ 明确纠正。普通自然表达返回空值，由回答正常完成后
        的可恢复队列异步提取，不在提交前做模型调用或业务写入。
        """

        del conversation_id, run_id, mode
        now = _now()
        source_hash = _source_hash(account_id, message_id, content)
        signal_classification = self._classifier.classify(content)
        # 用户可用引号指定管理对象；只有消息开头的直接命令才去除目标引号
        # 再检查来源，转述中的命令不因目标可解析而获得管理权限。
        management_classification = signal_classification
        if re.match(
            r"^(?:请|麻烦)?(?:帮我)?(?:记住|忘掉|忘记|删掉|删除)", content.strip()
        ):
            management_classification = self._classifier.classify(
                re.sub(r'[“”"「」『』]', "", content)
            )
        management_allowed = management_classification.reason_code not in {
            "quoted_or_relayed_text", "third_party_statement", "hypothetical_or_role_play",
        }
        directive = _privacy_directive(content) if management_allowed else None
        if directive is not None:
            scope, normalized_value = directive
            if scope == "content" and normalized_value is None:
                normalized_value = _normalize(content)
            self._repository.block_recording(
                account_id,
                normalized_value if scope == "content" else None,
                now,
            )
            self._repository.mark_message_tombstone(account_id, message_id)
            return self._blocked_result(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                now=now,
                reason="用户已停止记录此消息",
            )
        existing = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
        )
        # 改进工单 07：先解析并受控执行显式管理意图，再判断普通自动记录
        # 是否允许。账户级停止记录只阻止「自动新增/更新」；显式「记住/忘掉」
        # 与明确纠正属于主动管理，在停止记录期间仍即时生效（否则用户要求
        # 忘掉的信息会因阻止检查先于指令解析而残留）。
        memory_directive = (
            parse_memory_directive(content)
            if self._atomic_profiles is not None
            and management_allowed
            else None
        )
        # 改进工单 18：明确的暂停/完成/恢复信号是目标生命周期变更，与「记住/
        # 忘掉」同属用户主动管理，在停止记录期间仍可生效。只在消息尚未终态
        # 记账时解析一次，重放不重复改状态。
        lifecycle_signal = (
            parse_goal_lifecycle_signal(content)
            if self._atomic_profiles is not None
            and management_allowed
            and memory_directive is None
            and (
                existing is None
                or existing.status
                not in {
                    ProfileExtractionStatus.SUCCEEDED,
                    ProfileExtractionStatus.EXHAUSTED,
                    ProfileExtractionStatus.PENDING,
                }
            )
            else None
        )
        recording_blocked = self._repository.is_recording_blocked(account_id)
        if recording_blocked and lifecycle_signal is not None:
            # 停止记录期间明确的暂停/完成/恢复仍即时生效；只有确实改到目标
            # 才算处理完成。没有可对账目标时撤销信号，按下方普通阻止处理，
            # 不给这条消息留下「未阻止也未落墓碑」的空档。
            atomic = self._atomic_profiles
            assert atomic is not None
            outcome = atomic.apply_lifecycle_signal(
                account_id,
                content,
                source_message_id=message_id,
                source_at=now,
            )
            if outcome is not None:
                return self._lifecycle_result(
                    account_id=account_id,
                    message_id=message_id,
                    content=content,
                    source_hash=source_hash,
                    now=now,
                    outcome=outcome,
                    existing=existing,
                )
            lifecycle_signal = None
        if (
            recording_blocked
            and memory_directive is None
            and lifecycle_signal is None
            and signal_classification.category != ProfileSignalCategory.CORRECTION
        ):
            self._repository.mark_message_tombstone(account_id, message_id)
            return self._blocked_result(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                now=now,
                reason="用户已停止产生新的记录",
            )
        if self._repository.is_message_tombstoned(account_id, message_id):
            if existing is None:
                now = _now()
                existing = ProfileExtractionRun(
                    extraction_id=_stable_id(
                        "profile-extract",
                        account_id,
                        message_id,
                        self.extractor_version,
                        source_hash,
                    ),
                    account_id=account_id,
                    message_id=message_id,
                    extractor_version=self.extractor_version,
                    source_hash=source_hash,
                    source_snapshot=content,
                    status=ProfileExtractionStatus.EXHAUSTED,
                    outcome=ProfileExtractionOutcome.NO_SIGNAL,
                    attempts=0,
                    last_error="原消息已撤回或被墓碑阻止",
                    source=ProfileExtractionSource.LOCAL_RULE,
                    created_at=now,
                    updated_at=now,
                )
                self._repository.save_run(existing)
            elif existing.status not in {
                ProfileExtractionStatus.SUCCEEDED,
                ProfileExtractionStatus.EXHAUSTED,
            }:
                existing.status = ProfileExtractionStatus.EXHAUSTED
                existing.outcome = ProfileExtractionOutcome.NO_SIGNAL
                existing.last_error = "原消息已撤回或被墓碑阻止"
                existing.updated_at = _now()
                self._repository.save_run(existing)
            return ProfilePreprocessResult(
                run=existing,
                committed_record_ids=existing.committed_record_ids,
                observed_count=existing.observed_count,
            )
        if memory_directive is not None:
            return self._memory_directive_result(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                now=now,
                directive=memory_directive,
                existing=existing,
            )
        if lifecycle_signal is not None:
            atomic = self._atomic_profiles
            assert atomic is not None
            outcome = atomic.apply_lifecycle_signal(
                account_id,
                content,
                source_message_id=message_id,
                source_at=now,
            )
            if outcome is not None:
                return self._lifecycle_result(
                    account_id=account_id,
                    message_id=message_id,
                    content=content,
                    source_hash=source_hash,
                    now=now,
                    outcome=outcome,
                    existing=existing,
                )
        if existing is not None and existing.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
            ProfileExtractionStatus.PENDING,
        }:
            # 纠正重放复用既有终态结果，绝不第二次改写记录。
            return ProfilePreprocessResult(
                run=existing,
                committed_record_ids=existing.committed_record_ids,
                observed_count=existing.observed_count,
                correction=self._replayed_correction_result(
                    existing, signal_classification
                ),
            )
        if signal_classification.category == ProfileSignalCategory.CORRECTION:
            source = self._decide_source(signal_classification)
            run = existing or self._new_run(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                status=ProfileExtractionStatus.RUNNING,
                outcome=ProfileExtractionOutcome.PENDING_RETRY,
                source=source,
                now=now,
            )
            # 只有真正进入画像判定（明确自述、普通知识提问或纠正请求）时才展示一次说明。
            notice = self._repository.claim_privacy_notice(account_id, now)
            return self._preprocess_correction(
                account_id=account_id,
                run=run,
                notice=notice,
                signal_classification=signal_classification,
            )
        return None

    def schedule_message_extraction(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
    ) -> ProfileExtractionRun | None:
        """回答正常完成后登记一条消息的可恢复异步提取任务（工单 17）。

        以 ``(账户, 消息, 抽取器版本, 原文哈希)`` 为幂等单位：消息重试或
        租约恢复重复调度返回既有 run，不重复计数证据。控制指令、墓碑、
        停止记录与无可复用信号的消息不创建模型任务；一切持久化随入队同一
        短事务提交，模型调用只在后台执行时发生且不在写事务内。
        """

        run, _notice = self._schedule_ordinary(
            account_id=account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            run_id=run_id,
        )
        return run

    def _schedule_ordinary(
        self,
        *,
        account_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        run_id: str,
        atomic_scheduling: bool = True,
    ) -> tuple[ProfileExtractionRun | None, ProfilePrivacyNotice | None]:
        del conversation_id, run_id
        now = _now()
        source_hash = _source_hash(account_id, message_id, content)
        existing = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
        )
        if existing is not None and existing.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
            ProfileExtractionStatus.PENDING,
        }:
            return existing, None
        if self._repository.is_message_tombstoned(account_id, message_id):
            return (
                self._blocked_result(
                    account_id=account_id,
                    message_id=message_id,
                    content=content,
                    source_hash=source_hash,
                    now=now,
                    reason="原消息已撤回或被墓碑阻止",
                ).run,
                None,
            )
        if self._repository.is_recording_blocked(
            account_id
        ) or self._repository.is_recording_blocked(account_id, content):
            return (
                self._blocked_result(
                    account_id=account_id,
                    message_id=message_id,
                    content=content,
                    source_hash=source_hash,
                    now=now,
                    reason="用户已停止产生新的记录",
                ).run,
                None,
            )
        classification = self._classifier.extraction_classification(content)
        if not classification.should_process or not self._classifier.has_reusable_signal(
            content
        ):
            return None, None
        notice = self._repository.claim_privacy_notice(account_id, now)
        source = self._decide_source(classification)
        run = existing or self._new_run(
            account_id=account_id,
            message_id=message_id,
            content=content,
            source_hash=source_hash,
            status=ProfileExtractionStatus.PENDING,
            outcome=ProfileExtractionOutcome.PENDING_RETRY,
            source=source,
            now=now,
        )
        if run.source is None:
            run.source = source
        task = self._repository.get_task(
            account_id, message_id, self.extractor_version, source_hash
        )
        if task is None:
            task = ProfileExtractionRetryTask(
                task_id=_stable_id(
                    "profile-retry",
                    account_id,
                    message_id,
                    self.extractor_version,
                    source_hash,
                ),
                account_id=account_id,
                message_id=message_id,
                extractor_version=self.extractor_version,
                source_hash=source_hash,
                status=ProfileExtractionStatus.PENDING,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        task.status = ProfileExtractionStatus.PENDING
        task.last_error = None
        task.updated_at = now
        if atomic_scheduling:
            with self._repository.transaction():
                self._repository.save_run(run)
                self._repository.save_task(task)
                if self._queue is not None:
                    self._queue.enqueue(
                        self._queue_name,
                        task.task_id,
                        payload={
                            "account_id": account_id,
                            "message_id": message_id,
                            "extractor_version": self.extractor_version,
                            "source_hash": source_hash,
                        },
                    )
        else:
            # 同步入口保留基线语义：调度账目先落地，失败重试在同一次失败
            # 记账里入队（见 `_run_scheduled_now`），模型调用不在写事务内。
            self._repository.save_run(run)
            self._repository.save_task(task)
        return run, notice

    def _run_scheduled_now(
        self,
        *,
        account_id: str,
        message_id: str,
        content: str,
    ) -> ProfilePreprocessResult:
        """同步入口的普通路径：调度并就地执行一次（工单 17 前语义保留）。"""

        now = _now()
        source_hash = _source_hash(account_id, message_id, content)
        classification = self._classifier.extraction_classification(content)
        existing = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
        )
        if existing is not None and existing.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
            ProfileExtractionStatus.PENDING,
        }:
            return ProfilePreprocessResult(
                run=existing,
                committed_record_ids=existing.committed_record_ids,
                observed_count=existing.observed_count,
                correction=self._replayed_correction_result(
                    existing, self._classifier.classify(content)
                ),
            )
        if not classification.should_process or not self._classifier.has_reusable_signal(
            content
        ):
            no_signal_run = existing or self._new_run(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                status=ProfileExtractionStatus.SUCCEEDED,
                outcome=ProfileExtractionOutcome.NO_SIGNAL,
                source=ProfileExtractionSource.LOCAL_RULE,
                now=now,
            )
            no_signal_run.status = ProfileExtractionStatus.SUCCEEDED
            no_signal_run.outcome = ProfileExtractionOutcome.NO_SIGNAL
            no_signal_run.updated_at = now
            self._repository.save_run(no_signal_run)
            self._audit_outcome(no_signal_run, result=AuditResult.SUCCESS)
            return ProfilePreprocessResult(run=no_signal_run)
        run, notice = self._schedule_ordinary(
            account_id=account_id,
            conversation_id="sync",
            message_id=message_id,
            content=content,
            run_id="sync",
            atomic_scheduling=False,
        )
        if run is None:
            # 调度前置检查在两次读取之间改变了状态（并发墓碑/停止记录）：
            # 复用 _schedule_ordinary 的阻断结果重新读取。
            blocked = self._blocked_result(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                now=now,
                reason="原消息已撤回或被墓碑阻止",
            )
            return blocked
        if run.status == ProfileExtractionStatus.PENDING:
            task = self._repository.get_task(
                account_id, message_id, self.extractor_version, source_hash
            )
            if task is not None:
                # 首次（回答前/同步入口的）尝试记作第 0 次重试：锁序号从 1
                # 开始，后续后台重试从 1 依次递增，重试上限只统计重试次数。
                self._run_retry_task(task, 0, initial=True)
                # 同步入口的失败重试在这里入队，保持基线「失败才登记重试」
                # 的可恢复接缝（后台异步入口由调度事务直接入队）。
                if (
                    task.status == ProfileExtractionStatus.PENDING
                    and self._queue is not None
                ):
                    self._queue.enqueue(
                        self._queue_name,
                        task.task_id,
                        payload={
                            "account_id": account_id,
                            "message_id": message_id,
                            "extractor_version": self.extractor_version,
                            "source_hash": source_hash,
                        },
                    )
        refreshed = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
        ) or run
        if (
            refreshed.status == ProfileExtractionStatus.SUCCEEDED
            and self._gateway_attempted(classification)
        ):
            self._capability_degraded_reason = None
        return ProfilePreprocessResult(
            run=refreshed,
            privacy_notice=notice,
            committed_record_ids=refreshed.committed_record_ids,
            observed_count=refreshed.observed_count,
        )

    def _new_run(
        self,
        *,
        account_id: str,
        message_id: str,
        content: str,
        source_hash: str,
        status: ProfileExtractionStatus,
        outcome: ProfileExtractionOutcome,
        source: ProfileExtractionSource,
        now: datetime,
    ) -> ProfileExtractionRun:
        return ProfileExtractionRun(
            extraction_id=_stable_id(
                "profile-extract",
                account_id,
                message_id,
                self.extractor_version,
                source_hash,
            ),
            account_id=account_id,
            message_id=message_id,
            extractor_version=self.extractor_version,
            source_hash=source_hash,
            source_snapshot=content,
            status=status,
            outcome=outcome,
            attempts=0,
            source=source,
            created_at=now,
            updated_at=now,
        )

    def _apply_correction(
        self,
        account_id: str,
        *,
        dimension: FourDimension,
        content: str,
    ) -> tuple[ProfileCorrectionResult, ProfileExtractionOutcome, list[str]]:
        """执行一次更正写入，返回用户可见结果、运行结论与已提交记录标识。

        首次处理与后台重试的唯一归属地：两条路径的差别只在「谁先决定要不要
        写」，写完之后的映射（无活动记录／受保护／已写入）只有这一份实现，
        不会再出现只更新其中一条路径的分叉。
        """

        record, changed = self._commit.write_correction(
            account_id,
            dimension=dimension,
            content=content,
        )
        if record is None:
            return (
                ProfileCorrectionResult(
                    status=ProfileCorrectionStatus.NO_ACTIVE_RECORD,
                    dimension=dimension,
                ),
                ProfileExtractionOutcome.SUCCEEDED_CORRECTION_NO_ACTIVE,
                [],
            )
        if not changed:
            return (
                ProfileCorrectionResult(
                    status=ProfileCorrectionStatus.PROTECTED,
                    dimension=dimension,
                    record_id=record.record_id,
                ),
                ProfileExtractionOutcome.SUCCEEDED_CORRECTION_PROTECTED,
                [],
            )
        return (
            ProfileCorrectionResult(
                status=ProfileCorrectionStatus.WRITTEN,
                dimension=dimension,
                record_id=record.record_id,
            ),
            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN,
            [record.record_id],
        )

    def _preprocess_correction(
        self,
        *,
        account_id: str,
        run: ProfileExtractionRun,
        notice: ProfilePrivacyNotice | None,
        signal_classification: ProfileSignalClassification,
    ) -> ProfilePreprocessResult:
        intent = signal_classification.correction_intent
        if intent is None or intent.dimension is None or not intent.new_value:
            run.status = ProfileExtractionStatus.SUCCEEDED
            run.attempts += 1
            run.outcome = ProfileExtractionOutcome.SUCCEEDED_CORRECTION_UNRESOLVED
            run.updated_at = _now()
            self._repository.save_run(run)
            self._audit_correction_outcome(run, ProfileCorrectionStatus.UNRESOLVED)
            return ProfilePreprocessResult(
                run=run,
                privacy_notice=notice,
                correction=ProfileCorrectionResult(
                    status=ProfileCorrectionStatus.UNRESOLVED,
                    dimension=intent.dimension if intent is not None else None,
                ),
            )

        correction: ProfileCorrectionResult
        try:
            with self._commit.transaction():
                # Issue 05：更正的成对写入（纠正四维记录 + 镜像原子条目）
                # 由提交 module 归属；Issue 06 把「写入 → 结果与结论」的映射
                # 也收成一份，首次处理与后台重试不再各写一遍。
                correction, run.outcome, record_ids = self._apply_correction(
                    account_id,
                    dimension=intent.dimension,
                    content=intent.new_value,
                )
                run.status = ProfileExtractionStatus.SUCCEEDED
                run.attempts += 1
                run.committed_record_ids = record_ids
                run.observed_count = 0
                run.last_error = None
                run.updated_at = _now()
                self._repository.save_run(run)
        except Exception as exc:  # noqa: BLE001 - 纠正是聊天辅助路径
            return self._schedule_retry(
                run,
                notice,
                exc,
                correction=ProfileCorrectionResult(
                    status=ProfileCorrectionStatus.FAILED,
                    dimension=intent.dimension,
                ),
            )

        self._audit_correction_outcome(run, correction.status)
        return ProfilePreprocessResult(
            run=run,
            privacy_notice=notice,
            committed_record_ids=record_ids,
            correction=correction,
        )

    def _replayed_correction_result(
        self,
        run: ProfileExtractionRun,
        classification: ProfileSignalClassification,
    ) -> ProfileCorrectionResult | None:
        if classification.category != ProfileSignalCategory.CORRECTION:
            return None
        intent = classification.correction_intent
        if intent is None or intent.dimension is None or not intent.new_value:
            return ProfileCorrectionResult(
                status=ProfileCorrectionStatus.UNRESOLVED,
                dimension=intent.dimension if intent is not None else None,
            )
        outcome_status = {
            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_WRITTEN: ProfileCorrectionStatus.WRITTEN,
            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_PROTECTED: ProfileCorrectionStatus.PROTECTED,
            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_UNRESOLVED: ProfileCorrectionStatus.UNRESOLVED,
            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_NO_ACTIVE: ProfileCorrectionStatus.NO_ACTIVE_RECORD,
            ProfileExtractionOutcome.CORRECTION_FAILED: ProfileCorrectionStatus.FAILED,
        }.get(run.outcome)
        if outcome_status is not None:
            return ProfileCorrectionResult(
                status=outcome_status,
                dimension=intent.dimension,
                record_id=run.committed_record_ids[0]
                if run.committed_record_ids
                else None,
            )
        if run.status in {
            ProfileExtractionStatus.PENDING,
            ProfileExtractionStatus.RUNNING,
            ProfileExtractionStatus.EXHAUSTED,
        }:
            return ProfileCorrectionResult(
                status=ProfileCorrectionStatus.FAILED,
                dimension=intent.dimension,
            )
        return None

    def _blocked_result(
        self,
        *,
        account_id: str,
        message_id: str,
        content: str,
        source_hash: str,
        now: datetime,
        reason: str,
    ) -> ProfilePreprocessResult:
        run = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
        )
        if run is None:
            run = ProfileExtractionRun(
                extraction_id=_stable_id(
                    "profile-extract",
                    account_id,
                    message_id,
                    self.extractor_version,
                    source_hash,
                ),
                account_id=account_id,
                message_id=message_id,
                extractor_version=self.extractor_version,
                source_hash=source_hash,
                source_snapshot=content,
                status=ProfileExtractionStatus.EXHAUSTED,
                outcome=ProfileExtractionOutcome.NO_SIGNAL,
                attempts=0,
                last_error=reason,
                # 隐私阻断是本地决策：不调用模型，来源恒为 local_rule。
                source=ProfileExtractionSource.LOCAL_RULE,
                created_at=now,
                updated_at=now,
            )
        else:
            run.status = ProfileExtractionStatus.EXHAUSTED
            run.outcome = ProfileExtractionOutcome.NO_SIGNAL
            run.last_error = reason
            if run.source is None:
                run.source = ProfileExtractionSource.LOCAL_RULE
            run.updated_at = now
        self._repository.save_run(run)
        self._audit_outcome(run, result=AuditResult.SUCCESS, reason="privacy_blocked")
        return ProfilePreprocessResult(
            run=run,
            committed_record_ids=[],
            observed_count=0,
        )

    def _memory_directive_result(
        self,
        *,
        account_id: str,
        message_id: str,
        content: str,
        source_hash: str,
        now: datetime,
        directive: MemoryDirective,
        existing: ProfileExtractionRun | None,
    ) -> ProfilePreprocessResult:
        """同步处理本轮「记住／忘掉」，并终结该消息的抽取记账。

        Issue 08：显式记忆指令在本轮回答生成前生效（``preprocess_message``
        由请求路径调用），因此本轮切片立即包含或排除对应条目。指令消息不再
        进入自动抽取，避免同一轮既按用户原话写入又按规则推断。
        """

        atomic = self._atomic_profiles
        assert atomic is not None
        if directive.kind == AtomicProfileMemoryKind.REMEMBER:
            atomic.remember(
                account_id, directive.target, source_message_id=message_id
            )
            memory = AtomicProfileMemoryResult(
                kind=AtomicProfileMemoryKind.REMEMBER,
                status=AtomicProfileMemoryStatus.REMEMBERED,
                matched_count=1,
            )
        else:
            memory = atomic.forget(account_id, directive.target)
        run = existing or ProfileExtractionRun(
            extraction_id=_stable_id(
                "profile-extract",
                account_id,
                message_id,
                self.extractor_version,
                source_hash,
            ),
            account_id=account_id,
            message_id=message_id,
            extractor_version=self.extractor_version,
            source_hash=source_hash,
            source_snapshot=content,
            status=ProfileExtractionStatus.SUCCEEDED,
            outcome=ProfileExtractionOutcome.SUCCEEDED_MEMORY_DIRECTIVE,
            attempts=1,
            source=ProfileExtractionSource.LOCAL_RULE,
            created_at=now,
            updated_at=now,
        )
        run.status = ProfileExtractionStatus.SUCCEEDED
        run.outcome = ProfileExtractionOutcome.SUCCEEDED_MEMORY_DIRECTIVE
        run.attempts = max(1, run.attempts)
        run.last_error = None
        if run.source is None:
            run.source = ProfileExtractionSource.LOCAL_RULE
        run.updated_at = now
        self._repository.save_run(run)
        self._audit_outcome(run, result=AuditResult.SUCCESS, reason="memory_directive")
        return ProfilePreprocessResult(run=run, memory=memory)

    def _lifecycle_result(
        self,
        *,
        account_id: str,
        message_id: str,
        content: str,
        source_hash: str,
        now: datetime,
        outcome: GoalLifecycleOutcome,
        existing: ProfileExtractionRun | None,
    ) -> ProfilePreprocessResult:
        """记录一次已生效的目标生命周期变更（暂停/完成/恢复）。"""

        run = existing or ProfileExtractionRun(
            extraction_id=_stable_id(
                "profile-extract",
                account_id,
                message_id,
                self.extractor_version,
                source_hash,
            ),
            account_id=account_id,
            message_id=message_id,
            extractor_version=self.extractor_version,
            source_hash=source_hash,
            source_snapshot=content,
            status=ProfileExtractionStatus.SUCCEEDED,
            outcome=ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL,
            attempts=1,
            source=ProfileExtractionSource.LOCAL_RULE,
            created_at=now,
            updated_at=now,
        )
        run.status = ProfileExtractionStatus.SUCCEEDED
        run.outcome = ProfileExtractionOutcome.SUCCEEDED_LIFECYCLE_SIGNAL
        run.attempts = max(1, run.attempts)
        run.last_error = None
        # 生命周期判定是纯本地规则；即便复用了一次失败尝试的运行记录，也不
        # 能把来源标成 Qwen（那会要求模型锁证据，而本次没有真实模型调用）。
        run.source = ProfileExtractionSource.LOCAL_RULE
        run.updated_at = now
        self._repository.save_run(run)
        self._audit_outcome(
            run,
            result=AuditResult.SUCCESS,
            reason=f"goal_{outcome.kind.value}",
        )
        return ProfilePreprocessResult(run=run)

    def _extract_once(
        self,
        *,
        signal_classification: ProfileSignalClassification,
        lock_sink: Callable[[ModelRunLock], None] | None = None,
        **kwargs: Any,
    ) -> ProfileExtractionOutput:
        extractor = self._extractor
        if (
            isinstance(extractor, GatewayAutomaticProfileExtractor)
            and signal_classification.is_local
        ):
            extractor = RuleBasedAutomaticProfileExtractor(self._classifier)
        try:
            output = extractor.extract(
                signal_classification=signal_classification,
                lock_sink=lock_sink,
                **kwargs,
            )
            if not isinstance(output, ProfileExtractionOutput):
                output = ProfileExtractionOutput.model_validate(output)
            return output
        except AutomaticProfileError:
            raise
        except ValidationError as exc:
            raise AutomaticProfileError(
                "profile_extraction_contract_invalid",
                "Profile extraction output did not match its contract.",
                retryable=False,
            ) from exc

    def _schedule_retry(
        self,
        run: ProfileExtractionRun,
        notice: ProfilePrivacyNotice | None,
        error: BaseException | str,
        correction: ProfileCorrectionResult | None = None,
        attempt_locks: list[ModelRunLock] | None = None,
        attempt_ordinal: int = 1,
    ) -> ProfilePreprocessResult:
        now = _now()
        error_code = self._safe_error_code(error)
        if self._is_permanent_code(error_code) or error_code in _TRANSIENT_PROFILE_ERROR_CODES:
            self._capability_degraded_reason = error_code
        if not self._is_retryable_error(error):
            run.status = ProfileExtractionStatus.EXHAUSTED
            run.outcome = (
                ProfileExtractionOutcome.CORRECTION_FAILED
                if correction is not None
                else ProfileExtractionOutcome.PERMANENT_FAILURE
            )
            run.attempts += 1
            run.last_error = error_code
            run.updated_at = now
            # 永久失败同样保留本次真实调用的锁证据（失败锁不覆盖、不丢失）。
            with self._repository.transaction():
                self._persist_attempt_locks(
                    attempt_locks or [],
                    run=run,
                    attempt_ordinal=attempt_ordinal,
                    strict=False,
                )
                self._repository.save_run(run)
            if correction is not None:
                self._audit_correction_outcome(run, correction.status)
            else:
                self._audit_outcome(run, result=AuditResult.BLOCKED, reason=error_code)
            return ProfilePreprocessResult(
                run=run, privacy_notice=notice, correction=correction
            )

        run.status = ProfileExtractionStatus.PENDING
        run.outcome = (
            ProfileExtractionOutcome.CORRECTION_FAILED
            if correction is not None
            else ProfileExtractionOutcome.PENDING_RETRY
        )
        run.attempts += 1
        run.last_error = error_code
        run.updated_at = now
        task = self._repository.get_task(
            run.account_id, run.message_id, run.extractor_version, run.source_hash
        )
        if task is None:
            task = ProfileExtractionRetryTask(
                task_id=_stable_id(
                    "profile-retry",
                    run.account_id,
                    run.message_id,
                    run.extractor_version,
                    run.source_hash,
                ),
                account_id=run.account_id,
                message_id=run.message_id,
                extractor_version=run.extractor_version,
                source_hash=run.source_hash,
                status=ProfileExtractionStatus.PENDING,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        task.status = ProfileExtractionStatus.PENDING
        task.last_error = error_code
        task.updated_at = now
        with self._repository.transaction():
            # 首次失败锁与 run/task 状态同一事务提交，重试后新增下一序号
            # 锁，绝不覆盖前次证据。
            self._persist_attempt_locks(
                attempt_locks or [],
                run=run,
                attempt_ordinal=attempt_ordinal,
                strict=False,
            )
            self._repository.save_run(run)
            self._repository.save_task(task)
            if self._queue is not None:
                self._queue.enqueue(
                    self._queue_name,
                    task.task_id,
                    payload={
                        "account_id": run.account_id,
                        "message_id": run.message_id,
                        "extractor_version": run.extractor_version,
                        "source_hash": run.source_hash,
                    },
                )
        if correction is not None:
            self._audit_correction_outcome(run, correction.status)
        else:
            self._audit_outcome(run, result=AuditResult.RETRYABLE_FAIL, reason=error_code)
        return ProfilePreprocessResult(
            run=run, privacy_notice=notice, correction=correction
        )

    def run_retry_tick(self) -> str:
        if self._queue is not None:
            claim = self._queue.claim_next(
                self._queue_name, "profile-extractor"
            )
            if claim is None:
                return "profile-extraction: 无待处理任务。"
            task_id = claim.task_key
            task = next(
                (
                    item
                    for item in self._repository.list_tasks()
                    if item.task_id == task_id
                ),
                None,
            )
            if task is None:
                self._queue.complete(claim)
                return "profile-extraction: 任务已不存在。"
            result = self._run_retry_task(task, claim.attempt + 1)
            if result == ProfileExtractionStatus.SUCCEEDED:
                self._queue.complete(claim)
            elif result == ProfileExtractionStatus.EXHAUSTED:
                self._queue.fail(claim, task.last_error or "画像抽取重试耗尽")
            else:
                self._queue.requeue(
                    claim,
                    retry_kind=RetryKind.FIXED,
                    reason=task.last_error or "画像抽取失败",
                    max_attempts=PROFILE_EXTRACTION_MAX_RETRIES,
                    backoff_seconds=0,
                )
            return f"profile-extraction: {result.value}。"

        task = next(
            (
                item
                for item in self._repository.list_tasks()
                if item.status == ProfileExtractionStatus.PENDING
            ),
            None,
        )
        if task is None:
            return "profile-extraction: 无待处理任务。"
        result = self._run_retry_task(task, task.attempts + 1)
        return f"profile-extraction: {result.value}。"

    def _run_retry_task(
        self,
        task: ProfileExtractionRetryTask,
        attempt: int,
        *,
        initial: bool = False,
    ) -> ProfileExtractionStatus:
        run = self._repository.get_run(
            task.account_id, task.message_id, task.extractor_version, task.source_hash
        )
        if run is None:
            task.status = ProfileExtractionStatus.EXHAUSTED
            task.last_error = "画像预处理记录不存在"
            task.updated_at = _now()
            self._repository.save_task(task)
            return task.status
        if run.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
        }:
            task.status = run.status
            task.last_error = run.last_error
            task.updated_at = _now()
            self._repository.save_task(task)
            return task.status
        if task.extractor_version != self.extractor_version:
            return self._exhaust_task(
                task, run, "profile_extraction_version_unavailable"
            )
        if self._repository.is_message_tombstoned(
            task.account_id, task.message_id
        ) or not self._message_snapshot_is_current(
            task.account_id, task.message_id, task.source_hash
        ):
            return self._exhaust_task(task, run, "profile_extraction_source_invalidated")
        if self._repository.is_recording_blocked(task.account_id):
            return self._exhaust_task(task, run, "profile_extraction_privacy_blocked")
        current_message = (
            self._message_reader(task.account_id, task.message_id)
            if self._message_reader is not None
            else None
        )
        if self._message_reader is not None:
            if current_message is None:
                return self._exhaust_task(
                    task, run, "profile_extraction_source_invalidated"
                )
            if self._repository.is_recording_blocked(
                task.account_id, str(current_message.content)
            ):
                return self._exhaust_task(
                    task, run, "profile_extraction_privacy_blocked"
                )
        source_at = getattr(current_message, "created_at", None)
        task.status = ProfileExtractionStatus.RUNNING
        task.attempts = attempt
        task.updated_at = _now()
        self._repository.save_task(task)
        run.status = ProfileExtractionStatus.RUNNING
        # 首次尝试的持久化次数记 1；后台重试以重试序号为准，与基线语义一致。
        run.attempts = attempt + 1 if initial else attempt
        run.updated_at = _now()
        self._repository.save_run(run)
        # Issue 13：本次尝试的锁序号 = 重试序号 + 1（首次预处理为 1，
        # 其后每次真实重试依次递增），保证锁证据按 attempt 严格排序。
        attempt_ordinal = attempt + 1
        signal_classification: ProfileSignalClassification | None = None
        correction: ProfileCorrectionResult | None = None
        # Issue 13：重试同样收集本次尝试的锁；来源以 run 既有值为准，
        # 重试绝不改写来源（历史 NULL 行按本次实际路径回填）。
        collector = _AttemptLockCollector()
        try:
            content = self._message_content(
                task.account_id, task.message_id, run.source_snapshot
            )
            signal_classification = self._classifier.extraction_classification(
                content
            )
            if signal_classification.category == ProfileSignalCategory.CORRECTION:
                with self._commit.transaction():
                    intent = signal_classification.correction_intent
                    record_ids: list[str] = []
                    observed_count = 0
                    if (
                        intent is None
                        or intent.dimension is None
                        or not intent.new_value
                    ):
                        correction = ProfileCorrectionResult(
                            status=ProfileCorrectionStatus.UNRESOLVED,
                            dimension=intent.dimension if intent is not None else None,
                        )
                        run.outcome = (
                            ProfileExtractionOutcome.SUCCEEDED_CORRECTION_UNRESOLVED
                        )
                    else:
                        # Issue 05/06：重试与首次处理共用成对写入与结果映射
                        # （纠正成功时原子条目同步镜像）。
                        correction, run.outcome, record_ids = self._apply_correction(
                            task.account_id,
                            dimension=intent.dimension,
                            content=intent.new_value,
                        )
                    source = (
                        run.source
                        if run.source is not None
                        else self._decide_source(signal_classification)
                    )
                    if run.source is None:
                        run.source = source
                    task.status = ProfileExtractionStatus.SUCCEEDED
                    task.last_error = None
                    task.updated_at = _now()
                    run.status = ProfileExtractionStatus.SUCCEEDED
                    run.last_error = None
                    run.committed_record_ids = record_ids
                    run.observed_count = observed_count
                    run.updated_at = task.updated_at
                    self._repository.save_task(task)
                    self._repository.save_run(run)
            else:
                source = (
                    run.source
                    if run.source is not None
                    else self._decide_source(signal_classification)
                )
                run.source = source
                # 改进工单 17：网络模型调用在写事务外完成；提交前再由
                # `_recheck_source_before_commit` 在同一短事务里复核原文、
                # 许可、墓碑、版本与用户编辑权威。
                output = self._extract_once(
                    account_id=task.account_id,
                    conversation_id="retry",
                    message_id=task.message_id,
                    content=content,
                    run_id=run.extraction_id,
                    signal_classification=signal_classification,
                    lock_sink=collector,
                )
                with self._commit.transaction():
                    self._recheck_source_before_commit(task, run, content)
                    self._enforce_source_evidence(
                        source=source, attempt_locks=collector.locks
                    )
                    if source == ProfileExtractionSource.QWEN_MODEL:
                        self._persist_attempt_locks(
                            collector.locks,
                            run=run,
                            attempt_ordinal=attempt_ordinal,
                            strict=True,
                        )
                    record_ids, observed_count = self._commit_output(
                        task.account_id,
                        conversation_id="retry",
                        message_id=task.message_id,
                        content=content,
                        mode="companion",
                        output=output,
                        now=_now(),
                        signal_classification=signal_classification,
                        source=source,
                        # 重放任务（来源哈希内嵌重放标记，见
                        # replay._replay_source_hash 的真实形状）不得把用户
                        # 纠正过的记录改回旧值；普通重试保持既有证据阶梯。
                        from_replay=PROFILE_REPLAY_SOURCE_HASH_PREFIX
                        in task.source_hash,
                        # 工单 18：期限以原消息时间为锚，后台重试不会把
                        # 「下周考试」按重试当天重新解释。
                        source_at=source_at,
                    )
                    task.status = ProfileExtractionStatus.SUCCEEDED
                    task.last_error = None
                    task.updated_at = _now()
                    run.status = ProfileExtractionStatus.SUCCEEDED
                    run.last_error = None
                    run.committed_record_ids = record_ids
                    run.observed_count = observed_count
                    run.outcome = self._success_outcome(record_ids, observed_count)
                    run.updated_at = task.updated_at
                    self._repository.save_task(task)
                    self._repository.save_run(run)
        except Exception as exc:  # noqa: BLE001 - 留在有界重试状态机
            error_code = self._safe_error_code(exc)
            if self._is_permanent_code(error_code) or error_code in _TRANSIENT_PROFILE_ERROR_CODES:
                self._capability_degraded_reason = error_code
            with self._repository.transaction():
                # 本次真实尝试的锁与 run/task 状态同事务落库；即使重试
                # 耗尽，失败锁也保留为不可覆盖的审计证据。
                self._persist_attempt_locks(
                    collector.locks,
                    run=run,
                    attempt_ordinal=attempt_ordinal,
                    strict=False,
                )
                if not self._is_retryable_error(exc) or attempt >= PROFILE_EXTRACTION_MAX_RETRIES:
                    return self._exhaust_task(task, run, error_code)
                task.status = ProfileExtractionStatus.PENDING
                task.last_error = error_code
                task.updated_at = _now()
                run.status = ProfileExtractionStatus.PENDING
                run.outcome = (
                    ProfileExtractionOutcome.CORRECTION_FAILED
                    if (
                        signal_classification is not None
                        and signal_classification.category
                        == ProfileSignalCategory.CORRECTION
                    )
                    else ProfileExtractionOutcome.PENDING_RETRY
                )
                run.last_error = task.last_error
                run.updated_at = task.updated_at
                self._repository.save_task(task)
                self._repository.save_run(run)
            if correction is not None:
                self._audit_correction_outcome(run, correction.status)
            else:
                self._audit_outcome(
                    run, result=AuditResult.RETRYABLE_FAIL, reason=error_code
                )
            return task.status
        if signal_classification is not None and self._gateway_attempted(
            signal_classification
        ):
            self._capability_degraded_reason = None
        if (
            signal_classification is not None
            and signal_classification.category == ProfileSignalCategory.CORRECTION
            and correction is not None
        ):
            self._audit_correction_outcome(run, correction.status)
        else:
            self._audit_outcome(run, result=AuditResult.SUCCESS)
        return task.status

    def _recheck_source_before_commit(
        self,
        task: ProfileExtractionRetryTask,
        run: ProfileExtractionRun,
        content: str,
    ) -> None:
        """最终短事务内的提交前复核（改进工单 17）。

        模型调用在写事务外完成，提交前必须再次确认：抽取记录未被其他提交
        终结、原消息未被墓碑或删除、原文哈希仍与本次一致、账户与内容级
        记录许可仍开启。任何一项失效都失败关闭，不写迟到结果；用户编辑的
        权威由原子写入层在同一事务内继续保护（见 Issue 16）。
        """

        if task.extractor_version != self.extractor_version:
            # 部署升级后旧版本的迟到结果不再提交：按新版本的提示词与
            # 质量策略重抽，避免混入过期语义。
            raise AutomaticProfileError(
                "profile_extraction_source_invalidated",
                "抽取器版本已变化",
                retryable=False,
            )
        current = self._repository.get_run(
            task.account_id, task.message_id, task.extractor_version, task.source_hash
        )
        if current is None or current.status in {
            ProfileExtractionStatus.SUCCEEDED,
            ProfileExtractionStatus.EXHAUSTED,
        }:
            raise AutomaticProfileError(
                "profile_extraction_source_invalidated",
                "抽取记录已被其他提交终结",
                retryable=False,
            )
        if self._repository.is_message_tombstoned(task.account_id, task.message_id):
            raise AutomaticProfileError(
                "profile_extraction_source_invalidated",
                "原消息已撤回或被墓碑阻止",
                retryable=False,
            )
        if not self._message_snapshot_is_current(
            task.account_id, task.message_id, task.source_hash
        ):
            raise AutomaticProfileError(
                "profile_extraction_source_invalidated",
                "原消息内容已变化",
                retryable=False,
            )
        if self._repository.is_recording_blocked(
            task.account_id
        ) or self._repository.is_recording_blocked(task.account_id, content):
            raise AutomaticProfileError(
                "profile_extraction_privacy_blocked",
                "用户已停止记录",
                retryable=False,
            )

    def _exhaust_task(
        self, task: ProfileExtractionRetryTask, run: ProfileExtractionRun, error: str
    ) -> ProfileExtractionStatus:
        now = _now()
        task.status = ProfileExtractionStatus.EXHAUSTED
        task.last_error = error[:500]
        task.updated_at = now
        run.status = ProfileExtractionStatus.EXHAUSTED
        run.outcome = (
            ProfileExtractionOutcome.CORRECTION_FAILED
            if run.outcome == ProfileExtractionOutcome.CORRECTION_FAILED
            else ProfileExtractionOutcome.PERMANENT_FAILURE
        )
        run.last_error = task.last_error
        run.updated_at = now
        self._repository.save_task(task)
        self._repository.save_run(run)
        if run.outcome == ProfileExtractionOutcome.CORRECTION_FAILED:
            self._audit_correction_outcome(run, ProfileCorrectionStatus.FAILED)
        else:
            self._audit_outcome(run, result=AuditResult.BLOCKED, reason=error)
        return task.status

    def _message_snapshot_is_current(
        self, account_id: str, message_id: str, source_hash: str
    ) -> bool:
        if self._message_reader is None:
            return True
        message = self._message_reader(account_id, message_id)
        expected_source_hash = _source_hash(
            account_id, message_id, "" if message is None else str(message.content)
        )
        return (
            message is not None
            and (
                expected_source_hash == source_hash
                or source_hash.startswith(
                    f"{expected_source_hash}{PROFILE_REPLAY_SOURCE_HASH_PREFIX}"
                )
            )
        )

    def _message_content(self, account_id: str, message_id: str, snapshot: str) -> str:
        if self._message_reader is None:
            return snapshot
        message = self._message_reader(account_id, message_id)
        if message is None:
            raise AutomaticProfileError(
                "profile_extraction_source_missing",
                "原消息不存在",
                retryable=False,
            )
        return str(message.content)

    def _commit_output(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        mode: str,
        output: ProfileExtractionOutput,
        now: datetime,
        signal_classification: ProfileSignalClassification,
        source: ProfileExtractionSource,
        from_replay: bool = False,
        source_at: datetime | None = None,
    ) -> tuple[list[str], int]:
        observed_count = 0
        submissions: list[ProfileRecordSubmission] = []
        audit_version = _signal_audit_version(
            self.extractor_version, signal_classification, source
        )
        for item in output.items:
            if self._repository.is_message_tombstoned(account_id, message_id):
                continue
            action = self._resolve_candidate_action(
                item,
                message_id=message_id,
                content=content,
                signal_classification=signal_classification,
            )
            if action == ProfileExtractionAction.IGNORE:
                continue
            if self._repository.is_recording_blocked(
                account_id, item.normalized_value
            ):
                continue
            observation = AutomaticProfileObservation(
                observation_id=_stable_id(
                    "profile-observation",
                    account_id,
                    message_id,
                    audit_version,
                    item.dimension.value,
                    item.normalized_value,
                ),
                account_id=account_id,
                message_id=message_id,
                extractor_version=audit_version,
                dimension=item.dimension,
                normalized_value=item.normalized_value,
                evidence_ref=item.evidence_ref,
                reliability=(
                    min(item.reliability, _MIN_PROMOTION_RELIABILITY - 0.01)
                    if action == ProfileExtractionAction.OBSERVE
                    else item.reliability
                ),
                source=source,
                created_at=now,
            )
            self._repository.save_observation(observation)
            observed_count += 1
            if (
                action == ProfileExtractionAction.OBSERVE
                or item.reliability < _MIN_PROMOTION_RELIABILITY
            ):
                continue
            observations = self._repository.list_observations(
                account_id,
                item.dimension,
                item.normalized_value,
                since=now - _KNOWLEDGE_PROMOTION_WINDOW,
            )
            unique_messages = {
                entry.message_id
                for entry in observations
                if entry.reliability >= _MIN_PROMOTION_RELIABILITY
                or (
                    signal_classification.is_self_statement
                    and entry.reliability > 0
                    and "reason=search_or_question_observation"
                    in entry.extractor_version
                )
            }
            explicit_self_statement = self._is_explicit_self_statement(
                content, signal_classification
            ) or self._candidate_is_explicit_fact(item, content)
            if not explicit_self_statement and len(unique_messages) < 2:
                continue
            if self._repository.is_message_tombstoned(account_id, message_id):
                continue
            confidence = (
                FourDimensionConfidence.HIGH
                if len(unique_messages) >= 2 or self._is_user_confirmation(content)
                else FourDimensionConfidence.MEDIUM
            )
            resolved = action.value
            if (
                resolved == ProfileExtractionAction.CREATE.value
                and re.search(r"我(?:现在|目前)?更喜欢", content)
            ):
                # 「更喜欢」是明确的偏好变更信号，不是同维度后值覆盖：
                # 只在这条消息自身携带变更词时更新，其他维度冲突靠事实身份并存。
                resolved = ProfileExtractionAction.UPDATE.value
            fact_text = item.fact_text or item.normalized_value
            identity = parse_fact_identity(fact_text, dimension=item.dimension)
            identity_discriminator = (
                None
                if identity.relation is AtomicProfileFactRelation.STATEMENT
                else f"{identity.relation.value}:{identity.object.casefold()}"
            )
            submissions.append(
                ProfileRecordSubmission(
                    dimension=item.dimension,
                    content=item.normalized_value,
                    action=resolved,
                    confidence=confidence,
                    evidence_quote=self._candidate_evidence_quote(item, content),
                    evidence_message_id=message_id,
                    change_note=(
                        "用户明确确认，可靠程度已提高"
                        if self._is_user_confirmation(content)
                        else "多次对话中再次出现，可靠程度已提高"
                        if confidence == FourDimensionConfidence.HIGH
                        else "首次明确表达，等待再次确认"
                    ),
                    migration_version=audit_version,
                    fact_text=item.fact_text,
                    identity_discriminator=identity_discriminator,
                )
            )
        # Issue 04：四维记录与原子镜像由提交 module 在同一事务里成对写入。
        # 镜像不写入（用户已删除同键条目或已改成别的正文）是正常结果，不算
        # 提交失败，因此这里只回报真正落库的记录标识。工单 18：来源消息时间
        # 作为相对时间的锚随提交传入，后台重试不会按重试当天重解释期限。
        return (
            self._commit.write_records(
                account_id,
                submissions,
                from_replay=from_replay,
                source_at=source_at,
            ),
            observed_count,
        )

    def _candidate_evidence_span(
        self, item: Any, content: str
    ) -> tuple[str, ProfileSignalClassification] | None:
        """解析候选的精确证据区间；无效区间按合同错误失败关闭。"""

        start = item.evidence_start
        end = item.evidence_end
        if start is None and end is None:
            return None
        if (
            start is None
            or end is None
            or start >= end
            or end > len(content)
        ):
            raise AutomaticProfileError(
                "profile_extraction_evidence_mismatch",
                "画像抽取证据区间无效",
                retryable=False,
            )
        segment = content[start:end]
        if not segment.strip():
            raise AutomaticProfileError(
                "profile_extraction_evidence_mismatch",
                "画像抽取证据区间为空",
                retryable=False,
            )
        if len(segment) > _MAX_EVIDENCE_SPAN_LENGTH:
            raise AutomaticProfileError(
                "profile_extraction_evidence_mismatch",
                "画像抽取证据区间超出精确证据长度",
                retryable=False,
            )
        return segment, self._classifier.classify(segment)

    def _candidate_evidence_quote(self, item: Any, content: str) -> str:
        """写入事实的原话依据：有精确区间时原样使用该区间，否则沿用旧截断。"""

        evidence = self._candidate_evidence_span(item, content)
        if evidence is None:
            return _evidence_quote(content, item.normalized_value)
        segment, _classification = evidence
        return _redact(segment)

    def _evidence_supports_candidate(
        self, segment: str, item: Any
    ) -> bool:
        """核对片段确实支持主体关系、否定与时间，而非仅字符串存在。"""

        if not str(item.normalized_value).strip():
            return False
        candidate_text = f"{item.fact_text or ''} {item.normalized_value}"
        if _NEGATION_MARKERS.search(segment) and not _NEGATION_MARKERS.search(
            candidate_text
        ):
            # 片段是否定表述而候选丢掉了否定：不作为肯定事实写入。
            return False
        for match in _TIME_EXPRESSIONS.finditer(segment):
            if match.group(0) not in candidate_text:
                # 原文明示时间未保留：不写入，避免把限时信息当成长期事实。
                return False
        return True

    def _resolve_candidate_action(
        self,
        item: ProfileExtractionItem,
        *,
        message_id: str,
        content: str,
        signal_classification: ProfileSignalClassification,
    ) -> ProfileExtractionAction:
        """逐候选证据验证并给出实际动作（改进工单 17 第 8 条）。

        - 有精确区间时以片段自身分类判断是否明确自述；模糊/行为候选只保留
          观察，不再被整消息的旧分类整批否决；
        - 硬禁止片段（引用/假设/第三方/敏感）零写入；
        - 抽取器凭空发明禁止值仍失败关闭，原话本身即敏感内容则零写入。
        """

        if item.evidence_ref != message_id:
            raise AutomaticProfileError(
                "profile_extraction_evidence_mismatch",
                "画像抽取证据引用不匹配",
                retryable=False,
            )
        if item.action == ProfileExtractionAction.IGNORE:
            return ProfileExtractionAction.IGNORE
        evidence = self._candidate_evidence_span(item, content)
        if evidence is None:
            segment = content
            segment_classification = signal_classification
        else:
            segment, segment_classification = evidence
            if segment_classification.reason_code in HARD_FORBIDDEN_REASONS:
                return ProfileExtractionAction.IGNORE
        candidate_text = f"{item.fact_text or ''} {item.normalized_value}"
        value_classification = self._classifier.classify(candidate_text)
        if value_classification.category == ProfileSignalCategory.FORBIDDEN:
            if evidence is not None and item.normalized_value in segment:
                # 原话本身就是引用/第三方/敏感内容：该候选零写入，不影响
                # 同一条消息里的其他合规片段。
                return ProfileExtractionAction.IGNORE
            raise AutomaticProfileError(
                "profile_extraction_forbidden_value",
                "画像抽取值包含禁止内容",
                retryable=False,
            )
        if item.action == ProfileExtractionAction.OBSERVE:
            if (
                segment_classification.category
                not in {
                    ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                    ProfileSignalCategory.AMBIGUOUS,
                }
                and signal_classification.category
                not in {
                    ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                    ProfileSignalCategory.AMBIGUOUS,
                }
            ):
                raise AutomaticProfileError(
                    "profile_extraction_observation_classification_invalid",
                    "内部观察缺少行为观察分类",
                    retryable=False,
                )
            return ProfileExtractionAction.OBSERVE
        if segment_classification.is_self_statement:
            if not self._evidence_supports_candidate(segment, item):
                return ProfileExtractionAction.IGNORE
            if evidence is not None and not self._value_supported_by_span(
                segment, item
            ):
                # 精确区间必须支持将要写入的规范值：防止把「摄影」挂到
                # 「我喜欢跑步」这样的无关原话区间上（任务 4）。
                return ProfileExtractionAction.IGNORE
            return item.action
        if segment_classification.category in {
            ProfileSignalCategory.BEHAVIOR_OBSERVATION,
            ProfileSignalCategory.AMBIGUOUS,
        }:
            # 模糊/行为候选保留观察：不自动晋升偏好或能力。
            return ProfileExtractionAction.OBSERVE
        # 改进工单 17 第 8 条：本地词表未命中的明确偏好/约束（「以后先给
        # 结论」「每天 30 分钟」）由语义模型提候选；候选在精确区间上通过
        # 主体、否定、时间与取值校验即可提交，不被旧的 no_signal 分类
        # 整批否决。候选文本自身必须是可复用偏好/约束，避免把无关片段
        # 的模型猜测写成用户事实。
        if self._semantic_candidate_is_explicit(segment, item):
            return item.action
        return ProfileExtractionAction.IGNORE

    def _semantic_candidate_is_explicit(
        self, segment: str, item: ProfileExtractionItem
    ) -> bool:
        """模型候选在精确区间上是否构成明确偏好/约束（工单 17 第 8 条）。"""

        candidate_text = f"{item.fact_text or ''} {item.normalized_value}"
        if not self._classifier.has_reusable_signal(candidate_text):
            return False
        if not self._evidence_supports_candidate(segment, item):
            return False
        return self._value_supported_by_span(segment, item)

    def _candidate_is_explicit_fact(
        self, item: ProfileExtractionItem, content: str
    ) -> bool:
        """有精确区间的语义创建候选按明确事实计入写入阶梯（工单 17）。"""

        if item.action not in {
            ProfileExtractionAction.CREATE,
            ProfileExtractionAction.UPDATE,
        }:
            return False
        evidence = self._candidate_evidence_span(item, content)
        if evidence is None:
            return False
        segment, classification = evidence
        if classification.reason_code in HARD_FORBIDDEN_REASONS:
            return False
        if classification.category in {
            ProfileSignalCategory.BEHAVIOR_OBSERVATION,
            ProfileSignalCategory.AMBIGUOUS,
        }:
            return False
        return self._semantic_candidate_is_explicit(segment, item)

    @staticmethod
    def _value_supported_by_span(segment: str, item: ProfileExtractionItem) -> bool:
        """将要写入的规范值是否真实出现在精确证据区间里。"""

        collapsed_segment = re.sub(r"\s+", "", segment)
        collapsed_value = re.sub(r"\s+", "", str(item.normalized_value))
        return not collapsed_value or collapsed_value in collapsed_segment

    def _is_explicit_self_statement(
        self,
        content: str,
        signal_classification: ProfileSignalClassification | None = None,
    ) -> bool:
        classification = signal_classification or self._classifier.classify(content)
        return classification.is_self_statement

    @staticmethod
    def _is_user_confirmation(content: str) -> bool:
        return bool(_CONFIRMATION_SIGNAL.search(content))

    def compile_chat_slice(
        self,
        account_id: str,
        *,
        mode: str,
        run_id: str,
        project_id: str | None = None,
        current_question: str | None = None,
    ) -> ProfileSlice:
        allowed = (
            {
                FourDimension.ACADEMIC_STATUS,
                FourDimension.KNOWLEDGE_INTEREST,
                FourDimension.STAGE_GOAL,
            }
            if mode == "study"
            else {
                FourDimension.ACADEMIC_STATUS,
                FourDimension.KNOWLEDGE_INTEREST,
                FourDimension.HOBBY,
                FourDimension.STAGE_GOAL,
            }
        )
        all_records = [
            record
            for record in self._four_dimensions.list_records(account_id)
            if record.status == FourDimensionRecordStatus.ACTIVE
            and record.dimension in allowed
        ]
        low_confidence = [
            record
            for record in all_records
            if not is_recallable_confidence(record.confidence)
        ]
        records = [
            record for record in all_records if is_recallable_confidence(record.confidence)
        ]
        related: list[FourDimensionProfileRecord] = []
        unrelated: list[FourDimensionProfileRecord] = []
        for record in records:
            (
                related
                if _record_matches_question(record, current_question)
                else unrelated
            ).append(record)
        related.sort(
            key=lambda record: (confidence_rank(record.confidence), record.updated_at),
            reverse=True,
        )
        unrelated.sort(key=lambda record: record.updated_at, reverse=True)
        included = [
            ProfileSliceItem(
                assertion_id=record.record_id,
                dimension=record.dimension.value,
                value_or_rule=record.content[:80],
                inclusion_reason="当前模式下与你当前任务相关的已授权信息",
                sensitivity_class=(
                    ProfileSensitivityClass.LEARNING
                    if record.dimension
                    in {
                        FourDimension.ACADEMIC_STATUS,
                        FourDimension.KNOWLEDGE_INTEREST,
                        FourDimension.STAGE_GOAL,
                    }
                    else ProfileSensitivityClass.PREFERENCE
                ),
            )
            for record in related[:_MAX_SLICE_ITEMS]
        ]
        unused = [
            UnusedSliceItem(
                assertion_id=record.record_id,
                dimension=record.dimension.value,
                value_or_rule=record.content[:80],
                exclusion_reason=(
                    "与当前问题不相关"
                    if record in unrelated
                    else "超出本轮最小切片预算或模式范围"
                ),
            )
            for record in related[_MAX_SLICE_ITEMS:] + unrelated
        ]
        unused.extend(
            UnusedSliceItem(
                assertion_id=record.record_id,
                dimension=record.dimension.value,
                value_or_rule=record.content[:80],
                exclusion_reason="可靠程度不足，暂不用于当前回答",
            )
            for record in low_confidence
        )
        slice_ = ProfileSlice(
            slice_id=_stable_id("profile-slice", account_id, run_id),
            owner_account_id=account_id,
            run_id=run_id,
            purpose=f"chat:{mode}",
            project_id=project_id,
            included_items=included,
            unused_items=unused,
            authorization_snapshot="profile-auto-authz-v1",
            key_epoch="epoch-0",
            expires_at=now_plus_hour(),
            sensitivity_classes_allowed=[
                ProfileSensitivityClass.PUBLIC,
                ProfileSensitivityClass.PREFERENCE,
                ProfileSensitivityClass.LEARNING,
            ],
            compiled_policy_version="profile-auto-slice-v1",
            length_budget=_MAX_SLICE_ITEMS,
            compiled_at=_now(),
        )
        if self._slice_repository is not None:
            self._slice_repository.save_slice(slice_)
        return slice_

    def get_record(self, account_id: str, record_id: str) -> FourDimensionProfileRecord:
        return self._four_dimensions.get_record(account_id, record_id)

    def profile_status(self, account_id: str) -> ProfileStatusProjection:
        """仅投影当前账户的画像就绪状态与混合来源说明。"""

        records = self._four_dimensions.list_records(account_id)
        runs = self._repository.list_runs(account_id)
        tasks = self._repository.list_tasks(account_id)
        pending = any(
            run.status in {
                ProfileExtractionStatus.PENDING,
                ProfileExtractionStatus.RUNNING,
            }
            for run in runs
        ) or any(
            task.status
            in {ProfileExtractionStatus.PENDING, ProfileExtractionStatus.RUNNING}
            for task in tasks
        )
        failed_runs = [
            run
            for run in runs
            if run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
            or (
                run.status == ProfileExtractionStatus.EXHAUSTED
                and run.outcome != ProfileExtractionOutcome.NO_SIGNAL
                and run.last_error is not None
            )
        ]
        # V2 Issue 08：挂载原子条目后，「有没有信息」以用户可见的无类别
        # 列表为准（用户单独记住的条目没有对应的四维记录）。
        has_records = bool(records)
        if self._atomic_profiles is not None:
            has_records = bool(self._atomic_profiles.list_items(account_id))
        if pending:
            status = ProfilePageStatus.PENDING
        elif failed_runs:
            status = ProfilePageStatus.FAILED
        elif has_records:
            status = ProfilePageStatus.READY
        else:
            status = ProfilePageStatus.EMPTY
        can_retry = bool(
            failed_runs
            and any(
                not self._is_permanent_code(run.last_error or "")
                for run in failed_runs
            )
        )
        # Issue 13：按稳定来源统计抽取 run；历史 NULL 来源不计数也不伪造。
        source_counts: dict[str, int] = {}
        for run in runs:
            if run.source is None:
                continue
            source_counts[run.source.value] = source_counts.get(run.source.value, 0) + 1
        return ProfileStatusProjection(
            status=status,
            has_records=has_records,
            can_retry=can_retry,
            extraction_sources=dict(sorted(source_counts.items())),
            source_explanation=PROFILE_HYBRID_EXPLANATION,
        )

    def mark_message_tombstone(self, account_id: str, message_id: str) -> None:
        self._repository.mark_message_tombstone(account_id, message_id)

    def list_retry_tasks(
        self, account_id: str | None = None
    ) -> list[ProfileExtractionRetryTask]:
        return self._repository.list_tasks(account_id)

    # ------------------------------------------------------------------
    # 改进工单 07：账户级画像控制。记录与使用分开持久化——记录开关的
    # 权威状态是隐私阻止规则（账户级行），使用开关在本票新增的控制表中；
    # 两个开关组合的行为互相独立。19 的用途切片与回答上下文编译共同读取
    # ``is_profile_usage_enabled``，17 的队列在重试前复查记录许可。
    # ------------------------------------------------------------------

    def is_profile_usage_enabled(self, account_id: str) -> bool:
        """当前账户是否允许回答读取长期画像正文。"""

        return self._repository.get_profile_usage_state(account_id).enabled

    def account_controls(self, account_id: str) -> ProfileAccountControlsProjection:
        """组合投影当前账户的两个控制开关状态。"""

        usage = self._repository.get_profile_usage_state(account_id)
        return ProfileAccountControlsProjection(
            recording_enabled=not self._repository.is_recording_blocked(account_id),
            usage_enabled=usage.enabled,
            usage_control_version=usage.version,
            usage_updated_at=usage.updated_at,
        )

    def set_account_controls(
        self,
        account_id: str,
        *,
        recording_enabled: bool | None = None,
        usage_enabled: bool | None = None,
    ) -> ProfileAccountControlsProjection:
        """更新账户级控制开关；至少指定一项，同值写入幂等。

        停止记录写入账户级隐私阻止规则（与聊天指令「不要记录」同一份
        状态）；重新开启记录移除该账户级阻止（内容级阻止规则不受影响）。
        关闭/开启使用只改使用开关，从不删除或恢复任何画像条目。
        """

        if recording_enabled is None and usage_enabled is None:
            raise ValueError(PROFILE_CONTROLS_EMPTY_UPDATE_MESSAGE)
        now = _now()
        with self._repository.transaction():
            if recording_enabled is False:
                self._repository.block_recording(account_id, None, now)
            elif recording_enabled is True:
                self._repository.unblock_recording(account_id)
            if usage_enabled is not None:
                self._repository.set_profile_usage_enabled(account_id, usage_enabled, now)
            controls = self.account_controls(account_id)
        self._audit_controls_update(
            account_id,
            controls=controls,
            recording_target=recording_enabled,
            usage_target=usage_enabled,
        )
        return controls

    def _audit_controls_update(
        self,
        account_id: str,
        *,
        controls: ProfileAccountControlsProjection,
        recording_target: bool | None,
        usage_target: bool | None,
    ) -> None:
        if self._observability is None:
            return
        try:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.PROFILE_CONTROLS_UPDATE,
                result=AuditResult.SUCCESS,
                reason="profile_controls_update",
                details={
                    "recording_target": (
                        recording_target if recording_target is not None else "unchanged"
                    ),
                    "usage_target": (
                        usage_target if usage_target is not None else "unchanged"
                    ),
                    "recording_enabled": controls.recording_enabled,
                    "usage_enabled": controls.usage_enabled,
                    "usage_control_version": controls.usage_control_version,
                },
            )
        except Exception:  # noqa: BLE001 - 审计失败不改变已生效的控制状态
            return


def now_plus_hour() -> datetime:
    return _now() + timedelta(hours=1)
