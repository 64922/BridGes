"""V2 Issue 08：原子画像（无固定类别的长期信息列表）。

四维记录仍是自动抽取与冲突消解的写入引擎（``four_dimensions``）；本模块把
它的结果镜像成用户看到、修改和删除的原子条目，并承担旧四类数据的一次性
原子化迁移。

不变量：

- **去重**：``identity_key``（账户 + 规范化正文）决定条目身份，同一事实只
  保留一条活动条目；同键再次出现只补充来源与把握度。
- **用户权威**：用户编辑过的条目不再被自动抽取改写（正文不同即跳过），
  删除后转为墓碑，旧消息重放与自动抽取都不会让它复活。
- **账户隔离**：持久化实现全部经 ``BridgesDatabase.scoped`` 或按账户过滤，
  跨账户读写在领域层即不可达。
- **可对账、可恢复**：迁移按批次记账并给出确定性对账摘要，回滚只删除该
  批次新建的条目，旧四维记录本身不被改写。

本模块不调用语言模型：记住／忘掉、迁移与切片编译全部是确定性规则。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
    AtomicProfileItemProjection,
    AtomicProfileItemStatus,
    AtomicProfileMemoryKind,
    AtomicProfileMemoryResult,
    AtomicProfileMemoryStatus,
    AtomicProfileMigrationReport,
    AtomicProfileMigrationStatus,
    AtomicProfileWriteOrigin,
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
from bridges.profiles.adapters import ProfileError
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    confidence_rank,
    is_recallable_confidence,
)
from bridges.storage.database import BridgesDatabase

ATOMIC_PROFILE_MIGRATION_VERSION = "profile-atomic-v1"
#: 单轮注入模型的最大条目数（最小切片：只取当前任务必要的几条）。
MAX_SLICE_ITEMS = 4
_MAX_ITEM_TEXT_LENGTH = 1000
#: 忘掉指令的目标最短长度：过短的目标会误删无关条目，宁可返回「没找到」。
_MIN_FORGET_TARGET_LENGTH = 2
#: 目标与条目正文的二元组重合度下限（互含关系另有独立判定）。
_FORGET_SIMILARITY_THRESHOLD = 0.6
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


@contextmanager
def _joined_transaction(database: BridgesDatabase) -> Iterator[None]:
    """数据库事务边界；已在事务内时并入外层。

    单连接 SQLite 不允许嵌套 ``BEGIN``。镜像写入常发生在调用方（自动抽取）
    的四维记录事务里，此时并入外层事务才能保证两者同生共死；独立调用则自己
    开启事务。
    """

    if database.connection.in_transaction:
        yield
        return
    with database.transaction():
        yield


class MemoryDirective:
    """一条「记住／忘掉」指令（含目标正文，仅在本轮内存中使用）。"""

    def __init__(self, kind: AtomicProfileMemoryKind, target: str) -> None:
        self.kind = kind
        self.target = target


def normalize_text(text: str) -> str:
    """折叠空白后的条目正文（去重的规范化基础）。"""

    return " ".join(text.split())


def identity_key(account_id: str, text: str) -> str:
    """账户 + 规范化正文构成的去重与抑制键。"""

    payload = f"{account_id}|{normalize_text(text).casefold()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


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


def _now() -> datetime:
    return datetime.now(UTC)


def _stable_id(prefix: str, account_id: str, key: str) -> str:
    payload = "|".join([ATOMIC_PROFILE_MIGRATION_VERSION, prefix, account_id, key])
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _new_item_id() -> str:
    return f"item-{secrets.token_urlsafe(16)}"


def _digest(pairs: Iterable[tuple[str, str]]) -> str:
    payload = "|".join(sorted(f"{left}:{right}" for left, right in pairs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bigrams(text: str) -> set[str]:
    value = normalize_text(text).casefold()
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _matches_target(text: str, target: str) -> bool:
    """忘掉目标的确定性匹配：相等、互含，或二元组重合度足够高。"""

    left = normalize_text(text).casefold()
    right = normalize_text(target).casefold()
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    if not left_grams or not right_grams:
        return False
    shared = len(left_grams & right_grams)
    return shared / min(len(left_grams), len(right_grams)) >= _FORGET_SIMILARITY_THRESHOLD


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


def _sensitivity_for(item: AtomicProfileItem) -> ProfileSensitivityClass:
    """切片敏感度档位：学习相关条目按学习类材料标注，其余按偏好。"""

    if item.topic_hint in {
        FourDimension.ACADEMIC_STATUS.value,
        FourDimension.KNOWLEDGE_INTEREST.value,
        FourDimension.STAGE_GOAL.value,
    }:
        return ProfileSensitivityClass.LEARNING
    return ProfileSensitivityClass.PREFERENCE


def _item_from_record(
    account_id: str,
    record: FourDimensionProfileRecord,
    *,
    write_origin: AtomicProfileWriteOrigin,
    evidence_message_id: str | None = None,
    migration_run_id: str | None = None,
) -> AtomicProfileItem:
    text = normalize_text(record.content)
    source = evidence_message_id or record.evidence_message_id
    return AtomicProfileItem(
        profile_item_id=_new_item_id(),
        owner_account_id=account_id,
        text=text,
        identity_key=identity_key(account_id, text),
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
        """按去重键查找条目（含墓碑，用于抑制复活）。"""

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


class InMemoryAtomicProfileRepository(AtomicProfileRepository):
    """供领域与 API 测试使用的确定性内存仓库。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], AtomicProfileItem] = {}
        self._reports: dict[tuple[str, str], AtomicProfileMigrationReport] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = (copy.deepcopy(self._items), copy.deepcopy(self._reports))
        try:
            yield
        except BaseException:
            self._items, self._reports = snapshot
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

    def find_item_by_source(
        self, owner_id: str, source_record_id: str
    ) -> AtomicProfileItem | None:
        return next(
            (
                item
                for item in self._items.values()
                if item.owner_account_id == owner_id
                and item.source_record_id == source_record_id
            ),
            None,
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

        return _joined_transaction(self._db)

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
            "profile_item_id, account_id, text, identity_key, source_record_id, "
            "source_message_ids_json, topic_hint, status, write_origin, confidence, "
            "version, created_at, updated_at, user_edited_at, migration_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_item_id) DO UPDATE SET text = excluded.text, "
            "identity_key = excluded.identity_key, source_record_id = excluded.source_record_id, "
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

    def find_item_by_source(
        self, owner_id: str, source_record_id: str
    ) -> AtomicProfileItem | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_items WHERE account_id = ? AND source_record_id = ?",
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
    def _report_from_row(row: object) -> AtomicProfileMigrationReport:
        return AtomicProfileMigrationReport(
            run_id=str(row["run_id"]),  # type: ignore[index]
            owner_account_id=str(row["account_id"]),  # type: ignore[index]
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
            retryable=bool(int(row["retryable"])),  # type: ignore[index]
            created_at=SqliteAtomicProfileRepository._dt(
                str(row["created_at"])  # type: ignore[index]
            ),
            undone_at=(
                SqliteAtomicProfileRepository._dt(str(row["undone_at"]))  # type: ignore[index]
                if row["undone_at"] is not None  # type: ignore[index]
                else None
            ),
        )

    def save_migration_report(
        self, report: AtomicProfileMigrationReport
    ) -> AtomicProfileMigrationReport:
        self._db.scoped(report.owner_account_id).execute(
            "INSERT INTO profile_item_migrations ("
            "run_id, account_id, migration_version, status, migrated, duplicated, "
            "tombstoned, skipped, failed, created_item_ids_json, source_record_ids_json, "
            "reconciliation_digest, retryable, created_at, undone_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
            "migrated = excluded.migrated, duplicated = excluded.duplicated, "
            "tombstoned = excluded.tombstoned, skipped = excluded.skipped, "
            "failed = excluded.failed, "
            "created_item_ids_json = excluded.created_item_ids_json, "
            "source_record_ids_json = excluded.source_record_ids_json, "
            "reconciliation_digest = excluded.reconciliation_digest, "
            "retryable = excluded.retryable, undone_at = excluded.undone_at "
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
                self._iso(report.created_at),
                self._iso(report.undone_at) if report.undone_at else None,
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
        return self._report_from_row(row) if row is not None else None

    def get_latest_migration_report(
        self, owner_id: str
    ) -> AtomicProfileMigrationReport | None:
        """返回账户最近一次写入的迁移报告（按写入顺序，不依赖系统时钟精度）。"""

        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_item_migrations "
            "WHERE account_id = ? ORDER BY rowid DESC LIMIT 1",
            (owner_id,),
        ).fetchone()
        return self._report_from_row(row) if row is not None else None


class AtomicProfileService:
    """原子条目的读写、记忆指令、切片编译与账户级迁移。"""

    def __init__(
        self,
        four_dimensions: FourDimensionProfileService,
        repository: AtomicProfileRepository,
    ) -> None:
        self._four_dimensions = four_dimensions
        self._repository = repository

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
            write_origin=item.write_origin,
        )

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
            if key != item.identity_key:
                duplicate = self._repository.find_item_by_identity(account_id, key)
                if duplicate is not None and duplicate.profile_item_id != item_id:
                    raise AtomicProfileError(
                        "已存在内容相同的信息，请先删除其中一条。"
                    )
            item.text = text
            item.identity_key = key
            item.version += 1
            item.updated_at = _now()
            item.user_edited_at = item.updated_at
            item.write_origin = AtomicProfileWriteOrigin.USER
            return self._repository.save_item(item)

    def delete_item(self, account_id: str, item_id: str, version: int) -> None:
        """删除条目：条目先转墓碑，再撤回底层记录。

        墓碑先写：两次写入之间的失败只会留下「已删条目 + 仍活动的旧记录」，
        镜像会因墓碑拒绝复活；反过来则会留下用户仍能看到的活动条目。
        """

        with self._repository.transaction():
            item = self._repository.get_item(account_id, item_id)
            if item.status != AtomicProfileItemStatus.ACTIVE:
                raise AtomicProfileError("画像条目已删除，不能重复删除。")
            if version != item.version:
                raise AtomicProfileError("版本冲突，请刷新后重试。")
            self._write_tombstone(item)
        # 四维仓库自己开事务（单连接不支持嵌套），因此在原子事务之外撤回。
        self._withdraw_source_record(account_id, item)

    # -- 记住／忘掉 -------------------------------------------------------

    def remember(
        self,
        account_id: str,
        text: str,
        *,
        source_message_id: str | None = None,
    ) -> AtomicProfileItem:
        """用户明确要求记住：本轮立即成为用户权威条目，并解除同键墓碑。"""

        normalized = normalize_text(text)
        if not normalized or len(normalized) > _MAX_ITEM_TEXT_LENGTH:
            raise AtomicProfileError("画像内容不合法。")
        key = identity_key(account_id, normalized)
        now = _now()
        with self._repository.transaction():
            existing = self._repository.find_item_by_identity(account_id, key)
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
                    return existing
                record = self._record_for_user_text(account_id, normalized)
                existing.text = normalized
                existing.status = AtomicProfileItemStatus.ACTIVE
                existing.write_origin = AtomicProfileWriteOrigin.USER
                existing.confidence = FourDimensionConfidence.HIGH
                existing.user_edited_at = now
                existing.updated_at = now
                existing.version += 1
                existing.migration_run_id = None
                if source_message_id is not None:
                    existing.source_message_ids = list(
                        dict.fromkeys([*existing.source_message_ids, source_message_id])
                    )
                if existing.source_record_id is None and record is not None:
                    existing.source_record_id = record.record_id
                    existing.topic_hint = record.dimension.value
                return self._repository.save_item(existing)
            record = self._record_for_user_text(account_id, normalized)
            item = AtomicProfileItem(
                profile_item_id=_new_item_id(),
                owner_account_id=account_id,
                text=normalized,
                identity_key=key,
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
            return self._repository.save_item(item)

    def forget(self, account_id: str, target: str) -> AtomicProfileMemoryResult:
        """用户明确要求忘掉：删除命中的活动条目；没找到就如实返回未完成。"""

        normalized = normalize_text(target)
        if len(normalized) < _MIN_FORGET_TARGET_LENGTH:
            return AtomicProfileMemoryResult(
                kind=AtomicProfileMemoryKind.FORGET,
                status=AtomicProfileMemoryStatus.UNRESOLVED,
            )
        matched = [
            item
            for item in self.list_items(account_id)
            if _matches_target(item.text, normalized)
        ]
        if not matched:
            return AtomicProfileMemoryResult(
                kind=AtomicProfileMemoryKind.FORGET,
                status=AtomicProfileMemoryStatus.UNRESOLVED,
            )
        with self._repository.transaction():
            for item in matched:
                self._write_tombstone(item)
        # 墓碑先落地；底层四维记录的撤回各自开事务（见 ``delete_item``）。
        for item in matched:
            self._withdraw_source_record(account_id, item)
        return AtomicProfileMemoryResult(
            kind=AtomicProfileMemoryKind.FORGET,
            status=AtomicProfileMemoryStatus.FORGOTTEN,
            matched_count=len(matched),
        )

    # -- 自动抽取镜像 -----------------------------------------------------

    def mirror_record(
        self,
        account_id: str,
        record: FourDimensionProfileRecord,
        *,
        evidence_message_id: str | None = None,
    ) -> AtomicProfileItem | None:
        """把一条四维记录镜像成原子条目。

        去重键是正文本身，因此同一事实的重复抽取只更新一条条目。返回 ``None``
        表示本条不写入：正文为空、用户已删除同键条目（墓碑抑制复活），或该
        条目已被用户编辑成别的内容（用户版本优先）。
        """

        text = normalize_text(record.content)
        if not text or len(text) > _MAX_ITEM_TEXT_LENGTH:
            return None
        key = identity_key(account_id, text)
        now = _now()
        with self._repository.transaction():
            by_source = self._repository.find_item_by_source(account_id, record.record_id)
            recovered: AtomicProfileItem | None = None
            if by_source is not None:
                if by_source.status == AtomicProfileItemStatus.WITHDRAWN:
                    return None
                if by_source.user_edited_at is not None and by_source.text != text:
                    return by_source
                if by_source.identity_key == key:
                    return self._repository.save_item(
                        self._merge_item(
                            by_source,
                            record=record,
                            evidence_message_id=evidence_message_id,
                            now=now,
                        )
                    )
                # 同一底层记录的值被更新：旧正文退休，再按新正文去重写入。
                self._repository.delete_item(account_id, by_source.profile_item_id)
                recovered = by_source
            existing = self._repository.find_item_by_identity(account_id, key)
            if existing is not None:
                if existing.status == AtomicProfileItemStatus.WITHDRAWN:
                    return None
                return self._repository.save_item(
                    self._merge_item(
                        existing,
                        record=record,
                        evidence_message_id=evidence_message_id,
                        now=now,
                    )
                )
            if recovered is not None:
                recovered.text = text
                recovered.identity_key = key
                recovered.version += 1
                recovered.updated_at = now
                if evidence_message_id is not None:
                    recovered.source_message_ids = list(
                        dict.fromkeys(
                            [*recovered.source_message_ids, evidence_message_id]
                        )
                    )
                return self._repository.save_item(recovered)
            return self._repository.save_item(
                _item_from_record(
                    account_id,
                    record,
                    write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
                    evidence_message_id=evidence_message_id,
                )
            )

    # -- 切片 -------------------------------------------------------------

    def compile_chat_slice(
        self,
        account_id: str,
        *,
        run_id: str,
        current_question: str | None = None,
        project_id: str | None = None,
    ) -> ProfileSlice:
        """只把当前任务必要的少量条目编译成本轮切片。"""

        related: list[AtomicProfileItem] = []
        unrelated: list[AtomicProfileItem] = []
        for item in self.list_items(account_id):
            (
                related
                if _item_matches_question(item.text, current_question)
                else unrelated
            ).append(item)
        related.sort(
            key=lambda item: (confidence_rank(item.confidence), item.updated_at),
            reverse=True,
        )
        # 原子条目对模型也不带类别：内部 topic_hint 只用于迁移对账，页面、
        # 切片渲染与披露说明都按无类别呈现（模型不得复述系统内部标签）。
        included = [
            ProfileSliceItem(
                assertion_id=item.profile_item_id,
                dimension="",
                value_or_rule=item.text[:80],
                inclusion_reason="与你当前任务相关的已记住信息",
                sensitivity_class=_sensitivity_for(item),
            )
            for item in related[:MAX_SLICE_ITEMS]
        ]
        unused = [
            UnusedSliceItem(
                assertion_id=item.profile_item_id,
                dimension="",
                value_or_rule=item.text[:80],
                exclusion_reason="与当前问题无关",
            )
            for item in unrelated
        ]
        unused.extend(
            UnusedSliceItem(
                assertion_id=item.profile_item_id,
                dimension="",
                value_or_rule=item.text[:80],
                exclusion_reason="超出本轮最小切片预算",
            )
            for item in related[MAX_SLICE_ITEMS:]
        )
        return ProfileSlice(
            slice_id=_stable_id("slice", account_id, run_id),
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
            length_budget=MAX_SLICE_ITEMS,
            compiled_at=_now(),
        )

    # -- 迁移 -------------------------------------------------------------

    def migrate_account(self, account_id: str) -> AtomicProfileMigrationReport:
        """把旧四类记录一次性转成无类别的原子列表。

        只读旧记录、不原位改写：重复执行是幂等的（新增 0、重复 N），回滚按
        批次删除本批次新建的条目即可恢复。
        """

        run_id = f"atomic-{secrets.token_urlsafe(12)}"
        now = _now()
        records = self._four_dimensions.list_records(account_id, include_withdrawn=True)
        counts = {"migrated": 0, "duplicated": 0, "tombstoned": 0, "skipped": 0}
        created: list[str] = []
        source_ids: list[str] = []
        digest = _digest((record.record_id, record.content_hash) for record in records)
        try:
            with self._repository.transaction():
                for record in records:
                    source_ids.append(record.record_id)
                    if not normalize_text(record.content):
                        counts["skipped"] += 1
                        continue
                    if record.status == FourDimensionRecordStatus.WITHDRAWN:
                        self._write_migration_tombstone(account_id, record, run_id, now)
                        counts["tombstoned"] += 1
                        continue
                    key = identity_key(account_id, record.content)
                    if (
                        self._repository.find_item_by_source(
                            account_id, record.record_id
                        )
                        is not None
                    ):
                        counts["duplicated"] += 1
                        continue
                    existing = self._repository.find_item_by_identity(account_id, key)
                    if existing is not None:
                        if existing.status == AtomicProfileItemStatus.ACTIVE:
                            existing.source_record_id = record.record_id
                            existing.topic_hint = (
                                existing.topic_hint or record.dimension.value
                            )
                            self._repository.save_item(existing)
                        counts["duplicated"] += 1
                        continue
                    item = _item_from_record(
                        account_id,
                        record,
                        write_origin=AtomicProfileWriteOrigin.MIGRATION,
                        migration_run_id=run_id,
                    )
                    self._repository.save_item(item)
                    created.append(item.profile_item_id)
                    counts["migrated"] += 1
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
                    retryable=False,
                    created_at=now,
                )
                return self._repository.save_migration_report(report)
        except ProfileError:
            report = AtomicProfileMigrationReport(
                run_id=run_id,
                owner_account_id=account_id,
                migration_version=ATOMIC_PROFILE_MIGRATION_VERSION,
                status=AtomicProfileMigrationStatus.RETRYABLE,
                migrated=0,
                duplicated=counts["duplicated"],
                tombstoned=0,
                skipped=counts["skipped"],
                failed=1,
                created_item_ids=[],
                source_record_ids=source_ids,
                reconciliation_digest=digest,
                retryable=True,
                created_at=now,
            )
            with self._repository.transaction():
                return self._repository.save_migration_report(report)

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

    def _withdraw_source_record(
        self, account_id: str, item: AtomicProfileItem
    ) -> None:
        """撤回底层四维记录：自动抽取不会复活已撤回的记录。"""

        if item.source_record_id is None:
            return
        try:
            record = self._four_dimensions.get_record(account_id, item.source_record_id)
        except ProfileError:
            return
        if record.status != FourDimensionRecordStatus.ACTIVE:
            return
        self._four_dimensions.withdraw_record(account_id, record.record_id)

    def _merge_item(
        self,
        item: AtomicProfileItem,
        *,
        record: FourDimensionProfileRecord,
        evidence_message_id: str | None,
        now: datetime,
    ) -> AtomicProfileItem:
        """同键再次出现：补充来源与把握度，不产生副本。"""

        text = normalize_text(record.content)
        if evidence_message_id is not None:
            item.source_message_ids = list(
                dict.fromkeys([*item.source_message_ids, evidence_message_id])
            )
        item.source_record_id = item.source_record_id or record.record_id
        item.topic_hint = item.topic_hint or record.dimension.value
        item.text = text
        item.identity_key = identity_key(item.owner_account_id, text)
        item.version += 1
        item.updated_at = now
        if is_recallable_confidence(record.confidence) and confidence_rank(
            record.confidence
        ) > confidence_rank(item.confidence):
            item.confidence = record.confidence
        return item

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
    ) -> None:
        """为已撤回的旧记录补墓碑：抑制键已存在时只把活动条目转为墓碑。"""

        key = identity_key(account_id, record.content)
        existing = self._repository.find_item_by_identity(account_id, key)
        if existing is not None:
            if existing.status == AtomicProfileItemStatus.ACTIVE:
                self._write_tombstone(existing)
            return
        tombstone = AtomicProfileItem(
            profile_item_id=_new_item_id(),
            owner_account_id=account_id,
            text="",
            identity_key=key,
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


__all__ = [
    "ATOMIC_PROFILE_MIGRATION_VERSION",
    "MAX_SLICE_ITEMS",
    "AtomicProfileError",
    "AtomicProfileRepository",
    "AtomicProfileService",
    "InMemoryAtomicProfileRepository",
    "MemoryDirective",
    "SqliteAtomicProfileRepository",
    "identity_key",
    "normalize_text",
    "parse_memory_directive",
]
