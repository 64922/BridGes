"""Issue 15：消息级自动画像预处理、四维写入与有界重试。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from bridges.ai import ModelGateway
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.profile_extraction import (
    AutomaticProfileObservation,
    ProfileExtractionAction,
    ProfileExtractionOutput,
    ProfileExtractionRetryTask,
    ProfileExtractionRun,
    ProfileExtractionStatus,
    ProfilePreprocessResult,
    ProfilePrivacyNotice,
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
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    confidence_rank,
    is_recallable_confidence,
)
from bridges.profiles.ports import ProfileRepository
from bridges.profiles.signals import (
    ProfileSignalCategory,
    ProfileSignalClassification,
    ProfileSignalClassifier,
)
from bridges.runtime.queue import RetryKind, TaskQueue
from bridges.storage.database import BridgesDatabase

AUTOMATIC_EXTRACTOR_VERSION = "profile-auto-v1"
AUTOMATIC_PRIVACY_NOTICE_VERSION = "profile-privacy-v1"
AUTOMATIC_PRIVACY_NOTICE_TEXT = (
    "BridGes 会默认从你明确介绍自己的稳定信息中整理四维画像，"
    "仅用于后续相关回答；第三方、假设、敏感信息和一次性情绪不会写入。"
)
PROFILE_EXTRACTION_QUEUE = "profile-extraction"
PROFILE_EXTRACTION_MAX_RETRIES = 3
_KNOWLEDGE_PROMOTION_WINDOW = timedelta(days=90)
_MAX_SLICE_ITEMS = 6
_MAX_EVIDENCE_QUOTE_LENGTH = 240
_MIN_PROMOTION_RELIABILITY = 0.6

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
    }
)


class AutomaticProfileError(RuntimeError):
    """自动抽取失败；调用方应保留聊天主流程并安排重试。"""


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
    ) -> ProfileExtractionOutput: ...


class AutomaticProfileRepository(ABC):
    """自动画像状态、观察、重试与首次说明的持久化端口。"""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]: ...

    @abstractmethod
    def get_run(
        self, account_id: str, message_id: str, version: str, source_hash: str
    ) -> ProfileExtractionRun | None: ...

    @abstractmethod
    def save_run(self, run: ProfileExtractionRun) -> ProfileExtractionRun: ...

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
    pipeline_version: str, classification: ProfileSignalClassification
) -> str:
    """把受控分类元数据绑定到每条自动画像写入的版本字段。"""

    return (
        f"{pipeline_version}|category={classification.category.value}"
        f"|reason={classification.reason_code}"
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n，。；：:、,;.!！？?"))


def _evidence_quote(content: str, normalized_value: str) -> str:
    """保存有限长度的用户原话，并遮住常见联系信息和密钥样式。"""

    quote = content.strip()
    quote = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<邮箱>", quote)
    quote = re.sub(r"(?<!\d)1\d{10}(?!\d)", "<手机号>", quote)
    quote = re.sub(
        r"(?i)(密码|密钥|token|api[_-]?key)\s*[:：=]\s*\S+",
        r"\1：<已隐藏>",
        quote,
    )
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


def _extract_value(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = _normalize(match.group(1))
            if 1 < len(value) <= 200:
                return value
    return None


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


class RuleBasedAutomaticProfileExtractor:
    """高置信自述的本地抽取器；生产可替换为网关抽取器。"""

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
    ) -> ProfileExtractionOutput:
        del account_id, conversation_id, run_id
        text = content.strip()
        classification = signal_classification or self._classifier.classify(text)
        if not classification.should_process:
            return ProfileExtractionOutput(items=[])

        if classification.category == ProfileSignalCategory.BEHAVIOR_OBSERVATION:
            topic = _extract_observation_topic(text)
            if topic is None:
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
                        }
                    ]
                }
            )
        if not classification.is_self_statement:
            return ProfileExtractionOutput(items=[])

        items: list[dict[str, Any]] = []
        goal = _extract_value(
            text,
            (
                r"(?:我的|我这阶段的|我目前的)?目标(?:是|为)?\s*([^。！？!?；;，,]+)",
                r"我(?:计划|打算)\s*([^。！？!?；;，,]+)",
                r"(?:给|帮)我规划(?:一下|一份|一个)?\s*([^。！？!?；;，,]+)",
                r"(?:^|[，。；;])(?:目标|计划|打算|规划)(?:是|为|：|:)?\s*([^。！？!?；;，,]+)",
            ),
        )
        if goal is not None:
            items.append({
                "dimension": FourDimension.STAGE_GOAL,
                "normalized_value": goal,
                "action": ProfileExtractionAction.CREATE,
            })

        interest = _extract_value(
            text,
            (
                r"我对\s*([^。！？!?；;，,]+?)\s*(?:很)?感兴趣",
                r"我(?:现在|目前)?更喜欢\s*([^。！？!?；;，,]+)",
                r"我(?:很|比较|特别)?喜欢\s*([^。！？!?；;，,]+)",
                r"我(?:想|要|准备)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
                r"我(?:正在|在)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
                r"(?:想|要|准备|正在|在)\s*学(?:习)?\s*([^。！？!?；;，,]+)",
                r"(?:我\s*)?(?:正在|在)?\s*研究\s*([^。！？!?；;，,]+)",
            ),
        )
        if interest is not None:
            dimension = (
                FourDimension.HOBBY
                if _is_hobby_value(interest)
                and not re.search(r"(?:学习|学|研究|专业|论文|知识|技术)", text)
                else FourDimension.KNOWLEDGE_INTEREST
            )
            items.append({
                "dimension": dimension,
                "normalized_value": interest,
                "action": ProfileExtractionAction.CREATE,
            })

        academic = _extract_value(
            text,
            (r"我(?:现在|目前)?(?:在读|就读|是)\s*([^。！？!?；;，,]+)",),
        )
        if academic is None and classification.reason_code == (
            "subject_omitted_academic_statement"
        ):
            academic = _normalize(re.split(r"[，。；;]", text, maxsplit=1)[0])
        if academic is not None:
            items.append({
                "dimension": FourDimension.ACADEMIC_STATUS,
                "normalized_value": academic,
                "action": ProfileExtractionAction.CREATE,
            })

        if not items:
            return ProfileExtractionOutput(items=[])
        for item in items:
            item.update({"evidence_ref": message_id, "reliability": 0.99})
        return ProfileExtractionOutput.model_validate({"items": items})


class GatewayAutomaticProfileExtractor:
    """经固定结构化能力执行一次画像抽取。"""

    version = AUTOMATIC_EXTRACTOR_VERSION

    def __init__(
        self,
        gateway: ModelGateway,
        classifier: ProfileSignalClassifier | None = None,
    ) -> None:
        self._gateway = gateway
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
    ) -> ProfileExtractionOutput:
        classification = signal_classification or self._classifier.classify(content)
        if not classification.should_process:
            return ProfileExtractionOutput(items=[])
        context = RunContextEnvelope(
            run_id=run_id,
            account_id=account_id,
            project_id=conversation_id,
            workflow_name="profile-extraction",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=_now(),
        )
        result = self._gateway.invoke(
            "qwen_profile_extraction",
            "1",
            context,
            payload={
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "只抽取用户关于自己的明确稳定信号。禁止第三方、引用、假设、"
                            "角色扮演、敏感信息与一次性情绪。只输出四维枚举、规范化值、"
                            "消息证据引用、可靠度和动作。以 JSON 输出结果。"
                            f"本条消息的确定性分类为 {classification.category.value}，"
                            f"原因码为 {classification.reason_code}。"
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                "json_schema": ProfileExtractionOutput.model_json_schema(),
                "temperature": 0,
                "max_tokens": 512,
            },
        )
        if result.status != ModelCallStatus.SUCCESS or result.output is None:
            error_code = result.error_code or "profile_extraction_failed"
            if result.error_message and error_code not in result.error_message:
                error_code = f"{error_code}: {result.error_message}"
            raise AutomaticProfileError(error_code)
        try:
            return ProfileExtractionOutput.model_validate(result.output)
        except Exception as exc:  # noqa: BLE001 - 合同错误进入持久重试
            raise AutomaticProfileError("profile_extraction_contract_invalid") from exc


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

    def __init__(self, database: BridgesDatabase) -> None:
        self.database = database
        self.database.initialize()

    def transaction(self) -> AbstractContextManager[None]:
        return self.database.transaction()

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
            attempts=int(row["attempts"]),
            committed_record_ids=json.loads(str(row["record_ids_json"])),
            observed_count=int(row["observed_count"]),
            last_error=row["last_error"],
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
            "INSERT INTO profile_extraction_runs (extraction_id, account_id, message_id, extractor_version, source_hash, source_snapshot, status, attempts, record_ids_json, observed_count, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(extraction_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts, record_ids_json=excluded.record_ids_json, observed_count=excluded.observed_count, last_error=excluded.last_error, updated_at=excluded.updated_at",
            (
                run.extraction_id,
                run.account_id,
                run.message_id,
                run.extractor_version,
                run.source_hash,
                run.source_snapshot,
                run.status.value,
                run.attempts,
                json.dumps(run.committed_record_ids),
                run.observed_count,
                run.last_error,
                self._iso(run.created_at),
                self._iso(run.updated_at),
            ),
        )
        return run

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
            "INSERT INTO profile_extraction_observations (observation_id, account_id, message_id, extractor_version, dimension, normalized_value, evidence_ref, reliability, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, message_id, extractor_version, dimension, normalized_value) DO NOTHING",
            (
                observation.observation_id,
                observation.account_id,
                observation.message_id,
                observation.extractor_version,
                observation.dimension.value,
                observation.normalized_value,
                observation.evidence_ref,
                observation.reliability,
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
    ) -> None:
        self._four_dimensions = four_dimension_service
        self._repository = repository
        self._classifier = classifier or ProfileSignalClassifier()
        self._extractor = extractor or RuleBasedAutomaticProfileExtractor(self._classifier)
        self._slice_repository = slice_repository
        self._message_reader = message_reader
        self._queue = (
            TaskQueue(repository.database, default_lease_seconds=60)
            if isinstance(repository, SqliteAutomaticProfileRepository)
            else None
        )
        if self._queue is not None:
            self._queue.set_lease_seconds(PROFILE_EXTRACTION_QUEUE, 60)

    @property
    def extractor_version(self) -> str:
        """返回绑定分类策略的不可变抽取流水线版本。"""

        return f"{self._extractor.version}+signal-{self.classifier_version}"

    @property
    def classifier_version(self) -> str:
        return self._classifier.version

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
        now = _now()
        source_hash = _source_hash(account_id, message_id, content)
        directive = _privacy_directive(content)
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
        if self._repository.is_recording_blocked(account_id):
            self._repository.mark_message_tombstone(account_id, message_id)
            return self._blocked_result(
                account_id=account_id,
                message_id=message_id,
                content=content,
                source_hash=source_hash,
                now=now,
                reason="用户已停止产生新的记录",
            )
        existing = self._repository.get_run(
            account_id, message_id, self.extractor_version, source_hash
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
                    attempts=0,
                    last_error="原消息已撤回或被墓碑阻止",
                    created_at=now,
                    updated_at=now,
                )
                self._repository.save_run(existing)
            elif existing.status not in {
                ProfileExtractionStatus.SUCCEEDED,
                ProfileExtractionStatus.EXHAUSTED,
            }:
                existing.status = ProfileExtractionStatus.EXHAUSTED
                existing.last_error = "原消息已撤回或被墓碑阻止"
                existing.updated_at = _now()
                self._repository.save_run(existing)
            return ProfilePreprocessResult(
                run=existing,
                committed_record_ids=existing.committed_record_ids,
                observed_count=existing.observed_count,
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
            )

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
            status=ProfileExtractionStatus.RUNNING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        signal_classification = self._classifier.classify(content)
        if not signal_classification.should_process:
            run.status = ProfileExtractionStatus.SUCCEEDED
            run.updated_at = now
            self._repository.save_run(run)
            return ProfilePreprocessResult(run=run)

        # 只有真正进入画像判定（明确自述或普通知识提问）时才展示一次说明，
        # 避免对明显无关的寒暄和机器载荷产生打扰。
        notice = self._repository.claim_privacy_notice(account_id, now)

        self._repository.save_run(run)
        try:
            with self._commit_transaction():
                output = self._extract_once(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=content,
                    run_id=run_id,
                    signal_classification=signal_classification,
                )
                record_ids, observed_count = self._commit_output(
                    account_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    content=content,
                    mode=mode,
                    output=output,
                    now=now,
                    signal_classification=signal_classification,
                )
                run.status = ProfileExtractionStatus.SUCCEEDED
                run.attempts += 1
                run.committed_record_ids = record_ids
                run.observed_count = observed_count
                run.last_error = None
                run.updated_at = _now()
                self._repository.save_run(run)
        except Exception as exc:  # noqa: BLE001 - 抽取是聊天辅助路径
            return self._schedule_retry(run, notice, str(exc))

        return ProfilePreprocessResult(
            run=run,
            privacy_notice=notice,
            committed_record_ids=record_ids,
            observed_count=observed_count,
        )

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
                attempts=0,
                last_error=reason,
                created_at=now,
                updated_at=now,
            )
        else:
            run.status = ProfileExtractionStatus.EXHAUSTED
            run.last_error = reason
            run.updated_at = now
        self._repository.save_run(run)
        return ProfilePreprocessResult(
            run=run,
            committed_record_ids=[],
            observed_count=0,
        )

    def _extract_once(
        self,
        *,
        signal_classification: ProfileSignalClassification,
        **kwargs: Any,
    ) -> ProfileExtractionOutput:
        extractor = self._extractor
        if (
            isinstance(extractor, GatewayAutomaticProfileExtractor)
            and signal_classification.is_local
        ):
            extractor = RuleBasedAutomaticProfileExtractor(self._classifier)
        output = extractor.extract(
            signal_classification=signal_classification,
            **kwargs,
        )
        if not isinstance(output, ProfileExtractionOutput):
            output = ProfileExtractionOutput.model_validate(output)
        return output

    def _schedule_retry(
        self,
        run: ProfileExtractionRun,
        notice: ProfilePrivacyNotice | None,
        error: str,
    ) -> ProfilePreprocessResult:
        now = _now()
        run.status = ProfileExtractionStatus.PENDING
        run.attempts += 1
        run.last_error = error[:500]
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
        task.last_error = error[:500]
        task.updated_at = now
        with self._repository.transaction():
            self._repository.save_run(run)
            self._repository.save_task(task)
            if self._queue is not None:
                self._queue.enqueue(
                    PROFILE_EXTRACTION_QUEUE,
                    task.task_id,
                    payload={
                        "account_id": run.account_id,
                        "message_id": run.message_id,
                        "extractor_version": run.extractor_version,
                        "source_hash": run.source_hash,
                    },
                )
        return ProfilePreprocessResult(run=run, privacy_notice=notice)

    def run_retry_tick(self) -> str:
        if self._queue is not None:
            claim = self._queue.claim_next(
                PROFILE_EXTRACTION_QUEUE, "profile-extractor"
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

    @contextmanager
    def _commit_transaction(self) -> Iterator[None]:
        with ExitStack() as stack:
            stack.enter_context(self._repository.transaction())
            # SQLite 两个仓库共用同一连接，自动仓库事务已经覆盖四维写入；
            # 内存仓库则分别建立可回滚快照，确保整批提交失败时零部分写入。
            if not isinstance(self._repository, SqliteAutomaticProfileRepository):
                stack.enter_context(self._four_dimensions.transaction())
            yield

    def _run_retry_task(
        self, task: ProfileExtractionRetryTask, attempt: int
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
        if task.extractor_version != self.extractor_version:
            return self._exhaust_task(
                task, run, "原抽取器版本不可用，禁止改用新版本重放"
            )
        if self._repository.is_message_tombstoned(
            task.account_id, task.message_id
        ) or not self._message_snapshot_is_current(
            task.account_id, task.message_id, task.source_hash
        ):
            return self._exhaust_task(task, run, "原消息已修改、撤回或被墓碑阻止")
        if self._repository.is_recording_blocked(task.account_id):
            return self._exhaust_task(task, run, "用户已停止产生新的记录")
        task.status = ProfileExtractionStatus.RUNNING
        task.attempts = attempt
        task.updated_at = _now()
        self._repository.save_task(task)
        run.status = ProfileExtractionStatus.RUNNING
        run.attempts = attempt
        run.updated_at = _now()
        self._repository.save_run(run)
        try:
            with self._commit_transaction():
                content = self._message_content(
                    task.account_id, task.message_id, run.source_snapshot
                )
                signal_classification = self._classifier.classify(content)
                output = self._extract_once(
                    account_id=task.account_id,
                    conversation_id="retry",
                    message_id=task.message_id,
                    content=content,
                    run_id=run.extraction_id,
                    signal_classification=signal_classification,
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
                )
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
        except Exception as exc:  # noqa: BLE001 - 留在有界重试状态机
            if attempt >= PROFILE_EXTRACTION_MAX_RETRIES:
                return self._exhaust_task(task, run, str(exc))
            task.status = ProfileExtractionStatus.PENDING
            task.last_error = str(exc)[:500]
            task.updated_at = _now()
            run.status = ProfileExtractionStatus.PENDING
            run.last_error = task.last_error
            run.updated_at = task.updated_at
            self._repository.save_task(task)
            self._repository.save_run(run)
            return task.status
        return task.status

    def _exhaust_task(
        self, task: ProfileExtractionRetryTask, run: ProfileExtractionRun, error: str
    ) -> ProfileExtractionStatus:
        now = _now()
        task.status = ProfileExtractionStatus.EXHAUSTED
        task.last_error = error[:500]
        task.updated_at = now
        run.status = ProfileExtractionStatus.EXHAUSTED
        run.last_error = task.last_error
        run.updated_at = now
        self._repository.save_task(task)
        self._repository.save_run(run)
        return task.status

    def _message_snapshot_is_current(
        self, account_id: str, message_id: str, source_hash: str
    ) -> bool:
        if self._message_reader is None:
            return True
        message = self._message_reader(account_id, message_id)
        return (
            message is not None
            and _source_hash(account_id, message_id, str(message.content))
            == source_hash
        )

    def _message_content(self, account_id: str, message_id: str, snapshot: str) -> str:
        if self._message_reader is None:
            return snapshot
        message = self._message_reader(account_id, message_id)
        if message is None:
            raise AutomaticProfileError("原消息不存在")
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
    ) -> tuple[list[str], int]:
        record_ids: list[str] = []
        observed_count = 0
        audit_version = _signal_audit_version(
            self.extractor_version, signal_classification
        )
        for item in output.items:
            if self._repository.is_message_tombstoned(account_id, message_id):
                continue
            self._validate_item(
                item,
                account_id,
                message_id,
                content,
                signal_classification,
            )
            if item.action == ProfileExtractionAction.IGNORE:
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
                    if item.action == ProfileExtractionAction.OBSERVE
                    else item.reliability
                ),
                created_at=now,
            )
            self._repository.save_observation(observation)
            observed_count += 1
            if item.action == ProfileExtractionAction.OBSERVE or item.reliability < 0.6:
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
                )
            }
            explicit_self_statement = self._is_explicit_self_statement(
                content, signal_classification
            )
            if not explicit_self_statement and len(unique_messages) < 2:
                continue
            if self._repository.is_message_tombstoned(account_id, message_id):
                continue
            confidence = (
                FourDimensionConfidence.HIGH
                if len(unique_messages) >= 2 or self._is_user_confirmation(content)
                else FourDimensionConfidence.MEDIUM
            )
            action = item.action.value
            if (
                action == ProfileExtractionAction.CREATE.value
                and item.dimension
                in {
                    FourDimension.ACADEMIC_STATUS,
                    FourDimension.STAGE_GOAL,
                }
                and self._is_explicit_self_statement(
                    content, signal_classification
                )
            ):
                # 学业阶段与阶段目标是当前稳定状态；后来的明确自述
                # 更新现有记录，避免把冲突陈述并列注入模型。
                action = ProfileExtractionAction.UPDATE.value
            if (
                action == ProfileExtractionAction.CREATE.value
                and re.search(r"我(?:现在|目前)?更喜欢", content)
            ):
                action = ProfileExtractionAction.UPDATE.value
            record = self._four_dimensions.upsert_automatic_record(
                account_id,
                dimension=item.dimension,
                content=item.normalized_value,
                action=action,
                confidence=confidence,
                evidence_quote=_evidence_quote(content, item.normalized_value),
                evidence_message_id=message_id,
                change_note=(
                    "用户明确确认，可靠程度已提高"
                    if self._is_user_confirmation(content)
                    else "多次对话中再次出现，可靠程度已提高"
                    if confidence == FourDimensionConfidence.HIGH
                    else "首次明确表达，等待再次确认"
                ),
                migration_version=audit_version,
            )
            record_ids.append(record.record_id)
        return list(dict.fromkeys(record_ids)), observed_count

    def _validate_item(
        self,
        item: Any,
        account_id: str,
        message_id: str,
        content: str,
        signal_classification: ProfileSignalClassification,
    ) -> None:
        del account_id, content
        if item.evidence_ref != message_id:
            raise AutomaticProfileError("画像抽取证据引用不匹配")
        value_classification = self._classifier.classify(item.normalized_value)
        if value_classification.category == ProfileSignalCategory.FORBIDDEN:
            raise AutomaticProfileError("画像抽取值包含禁止内容")
        if item.action == ProfileExtractionAction.OBSERVE:
            if signal_classification.category not in {
                ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                ProfileSignalCategory.AMBIGUOUS,
            }:
                raise AutomaticProfileError("内部观察缺少行为观察分类")
            return
        if not signal_classification.is_self_statement:
            raise AutomaticProfileError("画像抽取缺少明确的用户自述边界")

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

    def mark_message_tombstone(self, account_id: str, message_id: str) -> None:
        self._repository.mark_message_tombstone(account_id, message_id)

    def list_retry_tasks(
        self, account_id: str | None = None
    ) -> list[ProfileExtractionRetryTask]:
        return self._repository.list_tasks(account_id)


def now_plus_hour() -> datetime:
    return _now() + timedelta(hours=1)
