"""Issue 02：记忆提交与历史读取边界（行为保持的先行准备重构）。

覆盖本票新增的三条边界合同，不改变任何业务可见行为：

1. 构造与读取不启动业务执行——只构造服务、调用画像读接口或导出，不会补建
   任务、不会重新入队抽取任务、不会写业务记录。
2. 跨重启恢复是显式启动动作：组合根在启动时调用
   ``AutomaticProfileService.recover_inflight_runs()``，语义与原构造期恢复
   完全一致（RUNNING 且缺任务 → PENDING + 入队）。
3. 用户权威写入（记住、行内编辑、删除、忘掉）统一在提交 module 的事务边界
   内执行：事务参与只由 ``ProfileCommit`` 声明，各入口不再自行开业务事务。

判据以存储与接口的真实结果为准：SQLite 直接开只读连接数行数；内存组合用
仓库公开读方法比对；恢复用运行/任务记录的状态与条数核对。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemModifyRequest,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionRun,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.automatic import (
    PROFILE_EXTRACTION_QUEUE,
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.commit import ProfileCommit
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.storage import BridgesDatabase

ACCOUNT = "account-alice"
#: 只读行数核对针对的画像/记账表；读操作不应新增任何一行。
_PROFILE_TABLES = (
    "profile_extraction_runs",
    "profile_extraction_tasks",
    "profile_extraction_observations",
    "task_claims",
    "profile_four_dimension_records",
    "profile_items",
)


def _seed_inflight_run(repository: SqliteAutomaticProfileRepository) -> None:
    """落一条「进程中断」的 RUNNING 运行：没有任务、没有队列行。"""

    now = datetime.now(UTC)
    repository.save_run(
        ProfileExtractionRun(
            extraction_id="inflight-run",
            account_id=ACCOUNT,
            message_id="message-1",
            extractor_version="issue02-boundary-v1",
            source_hash="source-hash",
            source_snapshot="我的阶段目标是今年通过雅思考试",
            status=ProfileExtractionStatus.RUNNING,
            outcome=ProfileExtractionOutcome.PENDING_RETRY,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
    )


def _counts(path: Path) -> dict[str, int]:
    """直接以只读连接统计画像相关表的行数（不经任何服务层）。"""

    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - 表名来自固定清单
            ).fetchone()[0]
            for table in _PROFILE_TABLES
        }
    finally:
        connection.close()


def _use_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue02-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-02",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["account"]["id"])


def _local_rule_extractor() -> RuleBasedAutomaticProfileExtractor:
    return RuleBasedAutomaticProfileExtractor()


# -- 1. 构造与读取不启动业务执行 -------------------------------------------


def test_construction_alone_does_not_start_business_execution(
    tmp_path: Path,
) -> None:
    """只构造服务：中断的运行保持原状，不补任务、不入队、不写业务记录。"""

    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    _seed_inflight_run(SqliteAutomaticProfileRepository(database))
    before = _counts(path)
    database.close()

    database = BridgesDatabase(path)
    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(database),
        ),
        repository=SqliteAutomaticProfileRepository(database),
        extractor=_local_rule_extractor(),
    )
    try:
        assert service.list_retry_tasks(ACCOUNT) == []
        assert _counts(path) == before
        run = service._repository.get_run(  # noqa: SLF001 - 夹具核对持久状态
            ACCOUNT, "message-1", "issue02-boundary-v1", "source-hash"
        )
        assert run is not None
        assert run.status == ProfileExtractionStatus.RUNNING
    finally:
        database.close()


def test_explicit_recovery_preserves_restart_semantics(tmp_path: Path) -> None:
    """显式恢复动作与原构造期恢复语义一致：补任务、重置运行、重新入队。"""

    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    _seed_inflight_run(SqliteAutomaticProfileRepository(database))
    database.close()

    database = BridgesDatabase(path)
    repository = SqliteAutomaticProfileRepository(database)
    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(database),
        ),
        repository=repository,
        extractor=_local_rule_extractor(),
    )
    try:
        service.recover_inflight_runs()

        tasks = service.list_retry_tasks(ACCOUNT)
        assert len(tasks) == 1
        assert tasks[0].status == ProfileExtractionStatus.PENDING
        run = repository.get_run(
            ACCOUNT, "message-1", "issue02-boundary-v1", "source-hash"
        )
        assert run is not None
        assert run.status == ProfileExtractionStatus.PENDING
        assert run.last_error == "profile_extraction_recovered_after_restart"
        queue = repository.durable_queue()
        assert queue is not None
        claim = queue.claim_next(PROFILE_EXTRACTION_QUEUE, "issue02-test-worker")
        assert claim is not None
        assert claim.task_key == tasks[0].task_id
    finally:
        database.close()


def test_app_startup_recovers_inflight_runs_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """组合根在启动时显式恢复：创建应用后中断的运行已重新入队。"""

    path = tmp_path / "bridges.db"
    database = BridgesDatabase(path)
    database.initialize()
    _seed_inflight_run(SqliteAutomaticProfileRepository(database))
    database.close()

    _use_database(monkeypatch, path)
    app = create_app()

    tasks = app.state.automatic_profile_service.list_retry_tasks(ACCOUNT)
    assert len(tasks) == 1
    assert tasks[0].status == ProfileExtractionStatus.PENDING


def test_profile_reads_and_export_do_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画像读接口与导出：不新增运行/任务/队列/记录/条目，不启动抽取。"""

    path = tmp_path / "bridges.db"
    _use_database(monkeypatch, path)
    app = create_app()
    client = TestClient(app)
    account_id = _register(client, "issue02-reader", "010102@qq.com")

    app.state.four_dimension_profile_service.upsert_automatic_record(
        account_id,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        content="喜欢看天体物理科普",
        action="create",
        migration_version="profile-auto-v1",
    )
    item = app.state.atomic_profile_service.remember(account_id, "我喜欢天文学")
    before = _counts(path)

    # 全部画像 GET 读接口（未迁移过时迁移报告如实 404）。
    reads = [
        client.get("/profiles/four-dimensions"),
        client.get("/profiles/status"),
        client.get("/profiles/controls"),
        client.get("/profiles/items"),
        client.get(f"/profiles/items/{item.profile_item_id}/evidence"),
        client.get("/profiles/items/migration"),
    ]
    assert [response.status_code for response in reads] == [200, 200, 200, 200, 200, 404]
    assert [entry["content"] for entry in reads[0].json()] == ["喜欢看天体物理科普"]
    assert [entry["text"] for entry in reads[3].json()] == ["我喜欢天文学"]

    # 导出预览走 HTTP，导出本体走服务入口（再认证属另一条路由的职责）。
    preview = client.get("/data/export-preview")
    assert preview.status_code == 200, preview.text
    filename, payload = app.state.export_service.export_data(account_id)
    assert filename
    assert "我喜欢天文学" in payload.decode("utf-8")

    assert _counts(path) == before
    assert app.state.automatic_profile_service.list_retry_tasks(account_id) == []


def test_memory_automatic_service_has_no_recovery_side_effects() -> None:
    """内存组合不挂跨重启队列：构造与显式恢复都不写任何记录。"""

    service = AutomaticProfileService(
        four_dimension_service=FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=InMemoryFourDimensionProfileRepository(),
        ),
        repository=InMemoryAutomaticProfileRepository(),
        extractor=_local_rule_extractor(),
    )

    service.recover_inflight_runs()

    assert service.list_retry_tasks(ACCOUNT) == []


# -- 3. 用户权威写入统一在提交边界内执行 ------------------------------------


class _RecordingCommit(ProfileCommit):
    """记录一次事务边界被打开的次数；不改变任何提交语义。"""

    def __init__(
        self,
        *,
        records: FourDimensionProfileService,
        items: AtomicProfileService | None = None,
    ) -> None:
        super().__init__(records=records, items=items)
        self.opened = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.opened += 1
        with super().transaction():
            yield


def test_user_authority_writes_open_the_single_commit_boundary() -> None:
    """记住、编辑、删除、忘掉各恰好打开一次提交 module 的事务边界。"""

    four = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    recording = _RecordingCommit(records=four, items=atomic)
    atomic._profile_commit = recording  # noqa: SLF001 - 夹具注入提交边界

    marks: list[int] = []
    item = atomic.remember(ACCOUNT, "我喜欢天文学")
    marks.append(recording.opened)
    edited = atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我更喜欢天文学了", version=item.version),
    )
    marks.append(recording.opened)
    atomic.delete_item(ACCOUNT, edited.profile_item_id, edited.version)
    marks.append(recording.opened)
    atomic.remember(ACCOUNT, "我每天写代码")
    marks.append(recording.opened)
    result = atomic.forget(ACCOUNT, "每天写代码")
    marks.append(recording.opened)

    # 每个用户权威动作恰好一次边界；没有入口自行另开业务事务。
    assert marks == [1, 2, 3, 4, 5]
    assert result.matched_count == 1
    assert atomic.list_items(ACCOUNT) == []


def test_user_authority_failure_rolls_back_within_the_boundary() -> None:
    """记住在边界内失败：整批回滚，不留下半条条目或墓碑。"""

    four = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    repository = InMemoryAtomicProfileRepository()
    atomic = AtomicProfileService(four, repository)
    original = repository.save_item
    calls = {"count": 0}

    def flaky_save(item: AtomicProfileItem) -> AtomicProfileItem:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("条目保存失败")
        return original(item)

    repository.save_item = flaky_save  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="条目保存失败"):
        atomic.remember(ACCOUNT, "我喜欢天文学")

    assert atomic.list_items(ACCOUNT) == []
    assert atomic.tombstone_audit(ACCOUNT) == []
