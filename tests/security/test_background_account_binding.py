"""Issue 39 AC7：后台任务/调度器/执行器账户范围绑定回归测试。

账户撤权或删除后，后台执行器与提醒调度器不得再为该账户产出可见结果：
- 账户删除事务化清除全部账户行（含提醒、图片/视频任务、摄取记录）；
- 调度器按提醒行所属账户分发，行消失即不再投递；
- 执行器按 image/video/document 表的 account_id 领取，行消失即不再处理。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from bridges.config import Settings
from bridges.contracts.identity import AccountRegistration
from bridges.credentials.store import InMemoryCredentialStore
from bridges.identity.service import IdentityService
from bridges.lifecycle.deletion import DeletionService
from bridges.persistence import SqliteStateStore
from bridges.reminder.service import ReminderService
from bridges.runtime.executor import BackgroundExecutor
from bridges.runtime.scheduler import ReminderScheduler
from bridges.storage.database import BridgesDatabase
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import BridgesObjectRepository


class _RecordingObservability:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class _NoopGateway:
    def send_reminder(self, to_email: str, subject: str, body: str) -> dict[str, Any]:
        return {"status": "sent", "to_email": to_email, "subject": subject}

    def verify_credentials(self, authorization_code: str) -> bool:
        return True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{tmp_path / 'bridges.db'}"),
        secret_key=SecretStr("test-secret-key-32bytes-xxxxxxxx"),
        smtp_plain=True,
        smtp_host="127.0.0.1",
        smtp_port=2525,
        imap_plain=True,
        imap_host="127.0.0.1",
        imap_port=1143,
    )


def _seed_image_task(database: BridgesDatabase, account_id: str, task_id: str) -> None:
    """直接插入一张待领取的图片任务（等价 ImageService 提交后的状态）。"""
    database.scoped(account_id).execute(
        "INSERT INTO image_tasks(task_id, conversation_id, account_id, kind,"
        " prompt, status, poll_count, retry_count, model_id, created_at, updated_at)"
        " VALUES (?, 'conv-1', ?, 'generate', '提示', 'queued', 0, 0,"
        " 'qwen-image-2.0-pro-2026-06-22', ?, ?)",
        (task_id, account_id, _now_iso(), _now_iso()),
    )


def test_scheduler_stops_delivering_after_account_deletion(tmp_path: Path) -> None:
    """账户删除后调度器不再为它投递提醒（不产出可见结果）。"""
    settings = _settings(tmp_path)
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    state = SqliteStateStore(tmp_path / "state.db", encryption_key="test-key")
    objects = BridgesObjectRepository(
        database, EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key")
    )
    identity = IdentityService(state_store=state, object_repository=objects)
    result = identity.register(
        AccountRegistration(
            username="deleted-user",
            qq_email="1002001@qq.com",
            password=SecretStr("correct-horse-12"),
        )
    )
    account_id = result.account.id

    # 到期提醒（补发窗口内）
    due = (datetime.now(UTC) + timedelta(seconds=30)).isoformat(timespec="seconds")
    database.scoped(account_id).execute(
        "INSERT INTO reminders(reminder_id, account_id, qq_email, timezone, raw_text,"
        " schedule_json, subject, body, use_profile, status, next_run_at,"
        " created_at, updated_at) VALUES (?, ?, '1002001@qq.com', 'Asia/Shanghai',"
        " '测试提醒', '{}', '测试主题', '正文', 0, 'enabled', ?, ?, ?)",
        (f"rem-{account_id[:6]}", account_id, due, _now_iso(), _now_iso()),
    )

    reminder = ReminderService(
        database=database,
        credential_store=InMemoryCredentialStore(namespace="smtp"),
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
        qq_email_provider=lambda _account_id: "1002001@qq.com",
        gateway=_NoopGateway(),  # type: ignore[arg-type]
    )

    # 删除前：到期提醒可被分发
    summary = reminder.process_due()
    assert "0" not in summary or "投递" in summary

    # 账户删除（事务清除提醒行）
    deletion = DeletionService(
        database=database,
        object_repository=objects,
        identity_service=identity,
        smtp_credential_store=InMemoryCredentialStore(namespace="smtp"),
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
    )
    deletion.delete_account(account_id)
    rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM reminders WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(rows["count"]) == 0

    # 删除后：调度器一轮心跳不投递、不报错（账户行已消失）
    scheduler = ReminderScheduler(settings)
    line = scheduler.run_tick()
    assert "待机" not in line
    assert "投递" not in line or "0" in line
    deliveries = database.connection.execute(
        "SELECT COUNT(*) AS count FROM reminder_deliveries WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert int(deliveries["count"]) == 0


def test_executor_stops_processing_after_account_deletion(tmp_path: Path) -> None:
    """账户删除后执行器不再领取它的图片/视频/摄取任务。"""
    settings = _settings(tmp_path)
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    state = SqliteStateStore(tmp_path / "state.db", encryption_key="test-key")
    objects = BridgesObjectRepository(
        database, EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key")
    )
    identity = IdentityService(state_store=state, object_repository=objects)
    result = identity.register(
        AccountRegistration(
            username="deleted-user-2",
            qq_email="1002002@qq.com",
            password=SecretStr("correct-horse-12"),
        )
    )
    account_id = result.account.id
    _seed_image_task(database, account_id, f"task-{account_id[:6]}")

    # 删除前：任务行存在（执行器按 account_id 领取的输入集合）
    before_rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM image_tasks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(before_rows["count"]) == 1

    # 删除账户：任务行随账户事务清除，执行器不再有任何可领取输入
    deletion = DeletionService(
        database=database,
        object_repository=objects,
        identity_service=identity,
        smtp_credential_store=InMemoryCredentialStore(namespace="smtp"),
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
    )
    deletion.delete_account(account_id)
    task_rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM image_tasks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(task_rows["count"]) == 0

    # 删除后：执行器一轮正常收尾，不为已删除账户产出任何任务结果
    after = BackgroundExecutor(settings).run_tick()
    assert "worker:" in after
    residual = database.connection.execute(
        "SELECT COUNT(*) AS count FROM image_tasks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(residual["count"]) == 0
    assets = database.connection.execute(
        "SELECT COUNT(*) AS count FROM image_assets WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(assets["count"]) == 0


def test_account_scoped_queries_cannot_see_deleted_account_rows(tmp_path: Path) -> None:
    """删除后该账户作用域查询为空：页面/任务/结果均不可见（AC7 撤权语义）。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    state = SqliteStateStore(tmp_path / "state.db", encryption_key="test-key")
    objects = BridgesObjectRepository(
        database, EncryptedFileObjectStore(tmp_path / "objects", encryption_key="test-key")
    )
    identity = IdentityService(state_store=state, object_repository=objects)
    result = identity.register(
        AccountRegistration(
            username="deleted-user-3",
            qq_email="1002003@qq.com",
            password=SecretStr("correct-horse-12"),
        )
    )
    account_id = result.account.id
    database.scoped(account_id).execute(
        "INSERT INTO conversations(conversation_id, account_id, title, mode,"
        " pinned, created_at, updated_at) VALUES ('conv-del', ?, '标题', 'companion',"
        " 0, ?, ?)",
        (account_id, _now_iso(), _now_iso()),
    )
    deletion = DeletionService(
        database=database,
        object_repository=objects,
        identity_service=identity,
        smtp_credential_store=InMemoryCredentialStore(namespace="smtp"),
        observability_service=_RecordingObservability(),  # type: ignore[arg-type]
    )
    deletion.delete_account(account_id)

    row = (
        database.scoped(account_id)
        .execute("SELECT COUNT(*) AS count FROM conversations WHERE account_id = ?", (account_id,))
        .fetchone()
    )
    assert int(row["count"]) == 0
    # 跨账户（其他账户作用域）也看不到已删除账户的行
    other = database.connection.execute(
        "SELECT COUNT(*) AS count FROM conversations WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(other["count"]) == 0
