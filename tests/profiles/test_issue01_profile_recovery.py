"""工单 01：旧库画像恢复后的页面路径、逐条对账与抽取镜像回归。

配套的数据库级夹具与迁移证据在 ``tests/storage/test_issue01_profile_schema_recovery.py``。
本文件覆盖用户可见的一侧：修复后画像接口返回 200 而不是 500、空画像与服务
失败可区分、历史四维记录逐条对账（迁入/跳过/拒绝原因）、行内编辑与删除、
跨账户不可访问，以及自动抽取继续镜像到原子画像。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.atomic_profile import (
    AtomicProfileItemStatus,
    AtomicProfileMigrationStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.profiles import FourDimension, FourDimensionProfileRecord
from bridges.profiles.adapters import InMemoryProfileRepository, ProfileError
from bridges.profiles.atomic import (
    MIGRATION_REASON_EMPTY_TEXT,
    MIGRATION_REASON_IDENTITY_TOMBSTONED,
    MIGRATION_REASON_MIGRATED,
    MIGRATION_REASON_MIGRATION_FAILED,
    MIGRATION_REASON_SOURCE_ALREADY_MIGRATED,
    MIGRATION_REASON_SOURCE_WITHDRAWN,
    MIGRATION_REASON_USER_ITEM_KEPT,
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage import SCHEMA_VERSION, BridgesDatabase
from bridges.storage.database import MIGRATIONS, SCHEMA_INTEGRITY_ERROR_CODE
from scripts.retry_exhausted_profile_extractions import (
    MISSING_TABLE_ERROR_CODE,
    cleanup_exhausted_profile_extractions,
)

#: 漂移发生的版本（运行库标记到此版本时画像对象已缺失）。
DRIFT_VERSION = 58
DRIFTED_EMAIL = "010001@qq.com"


def _replay_drifted(path: Path) -> None:
    """建出「标记为最新版本但缺画像对象」的真实文件库（复刻运行库漂移）。"""

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for target in range(1, DRIFT_VERSION + 1):
            for statement in MIGRATIONS[target]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(DRIFT_VERSION),),
        )
        connection.execute("DROP TABLE IF EXISTS profile_items")
        connection.execute("DROP TABLE IF EXISTS profile_item_migrations")
        connection.commit()
    finally:
        connection.close()


def _use_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue01-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "qq_email": email,
            "password": "correct-horse-01",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["account"]["id"])


def _seed_legacy_record(
    app: Any, account_id: str, content: str, *, dimension: FourDimension
) -> Any:
    """按旧四维引擎写入一条历史记录（模拟升级前的长期信息）。"""

    return app.state.four_dimension_profile_service.upsert_automatic_record(
        account_id,
        dimension=dimension,
        content=content,
        action="create",
        evidence_message_id="legacy-message",
        migration_version="profile-auto-v1",
    )


def _sqlite_service(
    path: Path, four_dimensions: FourDimensionProfileService
) -> AtomicProfileService:
    repository = SqliteAtomicProfileRepository(
        BridgesDatabase(path), initialize=False
    )
    return AtomicProfileService(four_dimensions, repository)


def _record_with_content(
    four_dimensions: FourDimensionProfileService,
    account_id: str,
    content: str,
    *,
    record_id: str,
    status: str = "active",
) -> FourDimensionProfileRecord:
    """直接落一条四维记录（含空正文与撤回态），用于构造边界对账场景。"""

    repository = four_dimensions._repository  # noqa: SLF001 - 夹具直接造旧数据
    record = FourDimensionProfileRecord(
        record_id=record_id,
        owner_account_id=account_id,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        label="兴趣",
        content=content,
        first_stable_recorded_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        version=1,
        status=status,
        source_record_id=f"src-{record_id}",
        source_version=1,
        content_hash=f"hash-{record_id}",
        write_origin="automatic",
        migration_version="profile-auto-v1",
    )
    repository.save_record(record)
    return record


def test_repaired_drifted_database_serves_profile_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 1/2/5：修复后画像接口 200，空画像与成功列表可区分，行内改删可用。"""

    path = tmp_path / "bridges.db"
    _replay_drifted(path)
    _use_database(monkeypatch, path)
    app = create_app()
    client = TestClient(app)

    account_id = _register(client, "issue01-repaired", DRIFTED_EMAIL)
    record = _seed_legacy_record(
        app, account_id, "我在准备雅思考试", dimension=FourDimension.STAGE_GOAL
    )

    # 修复后：空画像返回 200 + 空列表（页面显示「还没有长期信息」而不是报错）。
    empty = client.get("/profiles/items")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []
    assert client.get("/profiles/status").json()["status"] == "empty"

    # 一次性迁移：历史记录逐条对账后迁入，报告只带标识与原因码，不带正文。
    migrated = client.post("/profiles/items/migration")
    assert migrated.status_code == 200, migrated.text
    report = migrated.json()
    assert report["status"] == "completed"
    assert (report["migrated"], report["duplicated"], report["skipped"]) == (1, 0, 0)
    assert "我在准备雅思考试" not in migrated.text
    assert [entry["source_record_id"] for entry in report["reconciliation"]] == [
        record.record_id
    ]
    assert report["reconciliation"][0]["reason_code"] == MIGRATION_REASON_MIGRATED

    listed = client.get("/profiles/items")
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert [item["text"] for item in items] == ["我在准备雅思考试"]
    assert items[0]["write_origin"] == "migration"

    # 页面区分「成功列表」与「空画像」：状态接口给出 READY 且确实有条目。
    ready = client.get("/profiles/status").json()
    assert ready["status"] == "ready"
    assert ready["has_records"] is True

    item = items[0]

    # 跨账户不可访问：另一个账户既看不到这条历史信息，也不能改删它。
    other = _register(client, "issue01-other", "010002@qq.com")
    assert other != account_id
    assert client.get("/profiles/items").json() == []
    denied = client.patch(
        f"/profiles/items/{item['profile_item_id']}",
        json={"text": "越权修改", "version": item["version"]},
    )
    assert denied.status_code == 404, denied.text
    assert app.state.atomic_profile_service.list_items(account_id)[0].text == (
        "我在准备雅思考试"
    )

    # 回到原账户继续页面操作。
    relogin = client.post(
        "/auth/login",
        json={"identifier": DRIFTED_EMAIL, "password": "correct-horse-01"},
    )
    assert relogin.status_code == 200, relogin.text
    # 重复启动与重复迁移都不重复创建条目。
    again = client.post("/profiles/items/migration").json()
    assert (again["migrated"], again["duplicated"]) == (0, 1)
    assert len(client.get("/profiles/items").json()) == 1

    # 行内编辑：用户正文成为权威版本。
    edited = client.patch(
        f"/profiles/items/{item['profile_item_id']}",
        json={"text": "我今年准备雅思考试", "version": item["version"]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == "我今年准备雅思考试"
    assert edited.json()["write_origin"] == "user"

    # 删除后旧记录不再复活：再迁移一次仍是已删除状态。
    removed = client.request(
        "DELETE",
        f"/profiles/items/{item['profile_item_id']}",
        json={"version": edited.json()["version"]},
    )
    assert removed.status_code == 204, removed.text
    assert client.get("/profiles/items").json() == []
    resurrect = client.post("/profiles/items/migration").json()
    assert resurrect["migrated"] == 0
    assert client.get("/profiles/items").json() == []


def test_unrepairable_drift_blocks_pages_without_claiming_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 3：版本已到当前值却缺画像对象时，拒绝服务而不是返回空画像。"""

    path = tmp_path / "bridges.db"
    BridgesDatabase(path).initialize()
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE profile_items")
        connection.commit()
    finally:
        connection.close()
    _use_database(monkeypatch, path)

    app = create_app()
    client = TestClient(app)

    blocked = client.get("/profiles/items")
    assert blocked.status_code == 503, blocked.text
    # 页面看到的是「服务失败」，不是空画像；响应体不泄漏结构诊断。
    assert blocked.json() == {"detail": "持久化不可用，当前实例拒绝数据读写。"}
    assert app.state.persistence_error is not None

    health = client.get("/health/ready")
    assert health.status_code == 503, health.text
    assert health.json()["ready"] == "fail"
    dependencies = {
        item["name"]: item for item in health.json()["dependencies"]
    }
    persistence = dependencies["persistence"]
    assert persistence["status"] == "fail"
    assert persistence["required"] is True
    message = persistence["message"] or ""
    # 稳定错误码 + 脱敏诊断：只描述缺失对象，不带文件路径。
    assert SCHEMA_INTEGRITY_ERROR_CODE in message
    assert "profile_items" in message
    assert str(path) not in message
    assert app.state.persistence_error == message


def test_reconciliation_reasons_cover_skip_tombstone_and_user_authority(
    tmp_path: Path,
) -> None:
    """验收 4：逐条对账说明迁入/跳过/拒绝原因，用户编辑与删除都不被覆盖。"""

    path = tmp_path / "bridges.db"
    BridgesDatabase(path).initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = _sqlite_service(path, four_dimensions)
    account = "acc-reconcile"

    migrated_record = _record_with_content(
        four_dimensions, account, "喜欢看科普", record_id="fdr-migrated"
    )
    withdrawn_record = _record_with_content(
        four_dimensions,
        account,
        "想去上海工作",
        record_id="fdr-withdrawn",
        status="withdrawn",
    )
    blank_record = _record_with_content(
        four_dimensions, account, "   ", record_id="fdr-blank"
    )
    # 用户已经手动记住过同一条事实：迁移不得覆盖用户正文。
    user_item = service.remember(account, "我住在南昌")
    user_record = _record_with_content(
        four_dimensions, account, "我住在南昌", record_id="fdr-user"
    )
    # 用户删掉过的正文：迁移只能留下抑制键，不得复活。
    deleted = service.remember(account, "我养了一只猫")
    service.delete_item(account, deleted.profile_item_id, deleted.version)
    tombstoned_record = _record_with_content(
        four_dimensions, account, "我养了一只猫", record_id="fdr-deleted"
    )

    report = service.migrate_account(account)

    assert report.status == AtomicProfileMigrationStatus.COMPLETED
    reasons = {entry.source_record_id: entry for entry in report.reconciliation}
    assert reasons[migrated_record.record_id].reason_code == MIGRATION_REASON_MIGRATED
    assert reasons[migrated_record.record_id].outcome == "migrated"
    assert (
        reasons[withdrawn_record.record_id].reason_code
        == MIGRATION_REASON_SOURCE_WITHDRAWN
    )
    assert reasons[withdrawn_record.record_id].outcome == "tombstoned"
    assert reasons[blank_record.record_id].reason_code == MIGRATION_REASON_EMPTY_TEXT
    assert reasons[blank_record.record_id].outcome == "skipped"
    assert reasons[user_record.record_id].reason_code == MIGRATION_REASON_USER_ITEM_KEPT
    assert (
        reasons[tombstoned_record.record_id].reason_code
        == MIGRATION_REASON_IDENTITY_TOMBSTONED
    )
    # 报告的计数与逐条明细必须自洽：计数就是明细的归类结果。
    counted = {"migrated": 0, "duplicated": 0, "tombstoned": 0, "skipped": 0}
    for entry in report.reconciliation:
        counted[entry.outcome] += 1
    assert (report.migrated, report.duplicated, report.tombstoned, report.skipped) == (
        counted["migrated"],
        counted["duplicated"],
        counted["tombstoned"],
        counted["skipped"],
    )

    items = service.list_items(account)
    assert "我住在南昌" in {item.text for item in items}
    kept = service.get_item(account, user_item.profile_item_id)
    assert kept.write_origin == AtomicProfileWriteOrigin.USER
    assert kept.version == user_item.version
    assert "我养了一只猫" not in {item.text for item in items}
    assert "想去上海工作" not in {item.text for item in items}

    # 删除信息不复活：再次迁移仍是墓碑抑制，且不重复创建条目。
    before_texts = sorted(item.text for item in items)
    second = service.migrate_account(account)
    assert second.migrated == 0
    assert sorted(item.text for item in service.list_items(account)) == before_texts
    second_reasons = {
        entry.source_record_id: entry.reason_code for entry in second.reconciliation
    }
    assert (
        second_reasons[migrated_record.record_id]
        == MIGRATION_REASON_SOURCE_ALREADY_MIGRATED
    )
    assert second_reasons[user_record.record_id] == MIGRATION_REASON_USER_ITEM_KEPT
    assert (
        second_reasons[tombstoned_record.record_id]
        == MIGRATION_REASON_IDENTITY_TOMBSTONED
    )

    # 逐条明细与报告同事务落盘：重开连接仍能说明每条的原因。
    reopened = _sqlite_service(path, four_dimensions)
    latest = reopened.latest_migration_report(account)
    assert latest is not None
    expected_reasons = {
        entry.source_record_id: entry.reason_code for entry in second.reconciliation
    }
    assert {
        entry.source_record_id: entry.reason_code for entry in latest.reconciliation
    } == expected_reasons
    assert len(expected_reasons) == 5
    # 台账只存标识与原因码：表结构里没有任何可承载画像正文的列。
    connection = sqlite3.connect(path)
    try:
        ledger_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(profile_item_migration_records)"
            )
        }
    finally:
        connection.close()
    assert ledger_columns == {
        "run_id",
        "account_id",
        "source_record_id",
        "outcome",
        "reason_code",
        "profile_item_id",
        "recorded_at",
    }


def test_migration_snapshot_is_written_and_rolled_back_by_transaction(
    tmp_path: Path,
) -> None:
    """验收 6：迁移前落一致性快照，注入失败时事务回滚且不留下条目。"""

    path = tmp_path / "bridges.db"
    BridgesDatabase(path).initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = _sqlite_service(path, four_dimensions)
    account = "acc-snapshot"
    _record_with_content(four_dimensions, account, "喜欢看科普", record_id="fdr-snap")

    report = service.migrate_account(account)

    assert report.migrated == 1
    snapshots = sorted(path.parent.glob(f"{path.name}.backup-before-atomic-profile-*"))
    assert len(snapshots) == 1
    # 快照是迁移前的状态：结构与版本齐全，但当时还没有原子条目。
    connection = sqlite3.connect(snapshots[0])
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM profile_items").fetchone()[0] == 0
        )
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert int(version) == SCHEMA_VERSION

    # 重复迁移只保留最新一份快照，不随点击次数堆积。
    service.migrate_account(account)
    assert (
        len(sorted(path.parent.glob(f"{path.name}.backup-before-atomic-profile-*"))) == 1
    )

    # 注入写入失败：条目表内不会留下半迁移数据，旧四维记录保持可用。
    class _FailingRepository(SqliteAtomicProfileRepository):
        def save_item(self, item: Any) -> Any:
            if item.write_origin == AtomicProfileWriteOrigin.MIGRATION:
                raise RuntimeError("注入失败：模拟写入中断")
            return super().save_item(item)

    failing = AtomicProfileService(
        four_dimensions, _FailingRepository(BridgesDatabase(path), initialize=False)
    )
    second_account = "acc-failed"
    _record_with_content(
        four_dimensions, second_account, "喜欢看天体物理", record_id="fdr-fail"
    )
    with pytest.raises(RuntimeError):
        failing.migrate_account(second_account)

    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM profile_items WHERE account_id = ?",
                (second_account,),
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()
    # 旧四维记录不被改写，仍可再次尝试迁移。
    assert [
        record.record_id
        for record in four_dimensions.list_records(second_account)
    ] == ["fdr-fail"]


def test_retryable_report_keeps_only_the_failed_record_in_the_ledger(
    tmp_path: Path,
) -> None:
    """可重试报告与台账不得声称迁入过已回滚的条目。

    失败前已成功写入的条目会随事务一起回滚，若把它们按原结论写进台账，台账
    就会出现指向不存在条目的「已迁入」行——审计者据此会以为画像里有这条信息。
    """

    path = tmp_path / "bridges.db"
    BridgesDatabase(path).initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    account = "acc-retryable"
    _record_with_content(four_dimensions, account, "喜欢看科普", record_id="fdr-ok")
    _record_with_content(
        four_dimensions, account, "喜欢看天体物理", record_id="fdr-fail"
    )
    # 前置条件：第一条先被成功迁入，第二条才失败（否则复现不到已回滚的结论）。
    assert [
        record.record_id for record in four_dimensions.list_records(account)
    ] == ["fdr-ok", "fdr-fail"]

    class _FailingRepository(SqliteAtomicProfileRepository):
        def save_item(self, item: Any) -> Any:
            if item.write_origin == AtomicProfileWriteOrigin.MIGRATION and (
                item.text == "喜欢看天体物理"
            ):
                raise ProfileError("注入失败：模拟单条对账失败")
            return super().save_item(item)

    failing = AtomicProfileService(
        four_dimensions, _FailingRepository(BridgesDatabase(path), initialize=False)
    )
    report = failing.migrate_account(account)

    assert report.status == AtomicProfileMigrationStatus.RETRYABLE
    assert report.retryable is True
    assert (
        report.migrated,
        report.duplicated,
        report.tombstoned,
        report.skipped,
        report.failed,
    ) == (0, 0, 0, 0, 1)
    assert report.created_item_ids == []
    assert [entry.outcome for entry in report.reconciliation] == ["failed"]
    failed_entry = report.reconciliation[0]
    assert failed_entry.source_record_id == "fdr-fail"
    assert failed_entry.reason_code == MIGRATION_REASON_MIGRATION_FAILED
    assert failed_entry.profile_item_id is None

    # 台账与计数自洽：只有失败那一条，没有任何指向不存在条目的迁移行。
    connection = sqlite3.connect(path)
    try:
        ledger = connection.execute(
            "SELECT source_record_id, outcome, reason_code, profile_item_id "
            "FROM profile_item_migration_records WHERE account_id = ? ORDER BY rowid",
            (account,),
        ).fetchall()
        items = connection.execute(
            "SELECT COUNT(*) FROM profile_items WHERE account_id = ?", (account,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert ledger == [("fdr-fail", "failed", MIGRATION_REASON_MIGRATION_FAILED, None)]
    assert items == 0

    # 重试重新逐条判定：同一批记录随后完整迁入，台账被覆盖为真实结论。
    retry = _sqlite_service(path, four_dimensions).migrate_account(account)
    assert retry.status == AtomicProfileMigrationStatus.COMPLETED
    assert retry.migrated == 2
    assert {entry.source_record_id: entry.outcome for entry in retry.reconciliation} == {
        "fdr-ok": "migrated",
        "fdr-fail": "migrated",
    }
    assert (retry.migrated, retry.failed) == (2, 0)


def test_automatic_extraction_still_mirrors_after_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 5：修复后的库上，新对话的自动抽取继续镜像到原子画像。"""

    path = tmp_path / "bridges.db"
    _replay_drifted(path)
    _use_database(monkeypatch, path)
    app = create_app()
    client = TestClient(app)
    account_id = _register(client, "issue01-extraction", "010003@qq.com")
    conversation = app.state.chat_service.create_conversation(
        account_id, mode=ChatMode.COMPANION
    )

    created = client.post(
        f"/chat/conversations/{conversation.conversation_id}/messages",
        json={"content": "我的目标是今年通过雅思考试"},
    )
    assert created.status_code == 200, created.text
    # 改进工单 17：普通抽取在回答正常完成后由可恢复队列异步执行；
    # 测试显式驱动一次 worker，验证修复后的库上队列仍能镜像。
    app.state.generation_executor.run_tick()

    items = client.get("/profiles/items").json()
    assert [item["text"] for item in items] == ["今年通过雅思考试"]
    records = client.get("/profiles/four-dimensions").json()
    assert [record["content"] for record in records] == ["今年通过雅思考试"]
    assert items[0]["write_origin"] == "automatic"
    assert client.get("/profiles/status").json()["has_records"] is True


def test_repair_keeps_tombstones_written_before_the_drift(tmp_path: Path) -> None:
    """结构恢复不得让历史删除信息复活（撤回态旧记录只补墓碑）。"""

    path = tmp_path / "bridges.db"
    BridgesDatabase(path).initialize()
    four_dimensions = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    service = _sqlite_service(path, four_dimensions)
    account = "acc-tombstone"
    _record_with_content(
        four_dimensions,
        account,
        "我住在南昌",
        record_id="fdr-tombstone",
        status="withdrawn",
    )

    report = service.migrate_account(account)

    assert report.tombstoned == 1
    assert service.list_items(account) == []
    repository = SqliteAtomicProfileRepository(
        BridgesDatabase(path), initialize=False
    )
    withdrawn = [
        item
        for item in repository.list_items(account, include_withdrawn=True)
        if item.status == AtomicProfileItemStatus.WITHDRAWN
    ]
    assert len(withdrawn) == 1
    assert withdrawn[0].text == ""
    assert withdrawn[0].source_record_id == "fdr-tombstone"


def test_exhausted_extraction_is_re_armed_only_after_the_cause_is_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 5/6：缺表导致的耗尽行，在结构恢复后用既有受控方式重排并重新镜像。

    复刻运行库里的真实形状：抽取本身跑得通，镜像写 profile_items 时缺表，
    于是同一个 run 以 ``profile_extraction_unexpected`` 被重试到 exhausted
    （记录字段与运行库逐列一致：attempts=3、record_ids 为空、四维写入随事务
    回滚）。修复结构之后，默认清理**不碰**这类行——必须先确认缺表是原因，
    再显式按该错误码重排；重排只处理这些行，不顺带重跑其他聊天。
    """

    path = tmp_path / "bridges.db"
    _use_database(monkeypatch, path)
    app = create_app()
    client = TestClient(app)
    database: BridgesDatabase = app.state.bridges_database
    account_id = _register(client, "issue01-exhausted", "010004@qq.com")
    conversation = app.state.chat_service.create_conversation(
        account_id, mode=ChatMode.COMPANION
    )
    service = app.state.automatic_profile_service

    # 缺表形状：镜像读取直接抛「no such table: profile_items」。
    def _missing_table(self: Any, owner_id: str, source_record_id: str) -> Any:
        raise sqlite3.OperationalError("no such table: profile_items")

    monkeypatch.setattr(
        SqliteAtomicProfileRepository, "find_item_by_source", _missing_table
    )

    created = client.post(
        f"/chat/conversations/{conversation.conversation_id}/messages",
        json={"content": "我的目标是今年通过雅思考试"},
    )
    assert created.status_code == 200, created.text
    # 改进工单 17：先让回答正常完成并登记异步抽取任务，再驱动失败重试
    # 直到耗尽；缺表注入在抽取提交时才被触发。
    app.state.generation_executor.run_tick()
    for _ in range(4):
        service.run_retry_tick()
        row = database.connection.execute(
            "SELECT status, attempts, last_error, record_ids_json, observed_count "
            "FROM profile_extraction_runs WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is not None and str(row["status"]) == "exhausted":
            break

    assert row is not None
    assert (str(row["status"]), int(row["attempts"])) == ("exhausted", 3)
    assert str(row["last_error"]) == MISSING_TABLE_ERROR_CODE
    assert str(row["record_ids_json"]) == "[]"
    assert int(row["observed_count"]) == 0
    # 镜像失败与四维写入同事务：这条消息当时没有留下任何长期信息。
    assert client.get("/profiles/four-dimensions").json() == []
    assert client.get("/profiles/items").json() == []

    # 未确认成因时默认不处理：不会自动重跑耗尽的历史抽取。
    untouched = cleanup_exhausted_profile_extractions(database)
    assert (
        untouched.requeued_runs,
        untouched.requeued_tasks,
        untouched.queued_tasks,
    ) == (0, 0, 0)
    still = database.connection.execute(
        "SELECT status FROM profile_extraction_runs WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert str(still["status"]) == "exhausted"

    # 结构已由修复迁移补齐（这里等价于撤掉「缺表」注入）后，按已确认的错误码
    # 走既有受控重排；只重排这些行，不重跑全量聊天。
    monkeypatch.undo()
    summary = cleanup_exhausted_profile_extractions(
        database, error_prefix=MISSING_TABLE_ERROR_CODE
    )
    assert (summary.requeued_runs, summary.requeued_tasks) == (1, 1)
    assert summary.queued_tasks == 1
    assert summary.skipped_runs == 0

    processed = [service.run_retry_tick() for _ in range(3)]

    assert any("succeeded" in result for result in processed), processed
    recovered = database.connection.execute(
        "SELECT status, attempts, record_ids_json FROM profile_extraction_runs "
        "WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert str(recovered["status"]) == "succeeded"
    assert str(recovered["record_ids_json"]) != "[]"
    # 自动抽取继续镜像到原子画像：页面重新看到这条长期信息。
    assert [item["text"] for item in client.get("/profiles/items").json()] == [
        "今年通过雅思考试"
    ]
    assert [record["content"] for record in client.get("/profiles/four-dimensions").json()] == [
        "今年通过雅思考试"
    ]
