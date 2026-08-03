"""Issue 17：后台执行器摄取轮——真实 worker 接缝的文档处理与恢复。"""

from __future__ import annotations

from pathlib import Path

from conftest import SECRET_KEY, upload_text
from pydantic import SecretStr

from bridges.config import Settings
from bridges.runtime import BackgroundExecutor
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)


def _settings(tmp: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{tmp}/bridges.db"),
        secret_key=SecretStr(SECRET_KEY),
    )


def test_worker_tick_processes_queued_documents_and_cleans(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(tmp_path / "objects", encryption_key=SecretStr(SECRET_KEY)),
    )
    account_id = repository.register_account("11111111@qq.com")
    object_id = upload_text(
        {"database": database, "repository": repository, "account_a": account_id},
        account_id,
        "材料.txt",
        "后台执行器测试内容。\n\n第二个段落。\n".encode(),
    )

    executor = BackgroundExecutor(settings)
    # 入队由 API 上传路径触发；此处直接经执行器服务入队后跑一轮 worker
    executor._ensure_ingestion()  # type: ignore[attr-defined]
    executor._ingestion.enqueue(account_id, object_id, "conversation-1")  # type: ignore[attr-defined]
    summary = executor.run_tick()
    assert "摄取完成" in summary
    assert "处理 1 份文档" in summary

    row = database.connection.execute(
        "SELECT status FROM document_records WHERE account_id = ? AND object_id = ?",
        (account_id, object_id),
    ).fetchone()
    assert row is not None and str(row["status"]) == "ready"
    fts = database.connection.execute(
        "SELECT count(*) AS count FROM fts_chunks WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert int(fts["count"]) == 1
    # 对象清理照常执行
    assert "清理完成 0 个待清理对象" in summary
    executor._database.close()  # type: ignore[attr-defined]


def test_worker_tick_recovers_after_restart(tmp_path: Path) -> None:
    """重启语义：新执行器实例继续处理上次中断（租约过期）的任务。"""
    settings = _runtime_settings_with_secret(tmp_path)
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(tmp_path / "objects", encryption_key=SecretStr(SECRET_KEY)),
    )
    account_id = repository.register_account("22222222@qq.com")
    object_id = upload_text(
        {"database": database, "repository": repository, "account_a": account_id},
        account_id,
        "恢复.txt",
        "恢复测试内容。\n".encode(),
    )

    # 第一次执行器：入队后只领取不处理（模拟进程在完成前崩溃）
    first = BackgroundExecutor(settings)
    first._ensure_ingestion()  # type: ignore[attr-defined]
    first._ingestion.enqueue(account_id, object_id, "conversation-1")  # type: ignore[attr-defined]
    first._ingestion._claim(account_id, 5)  # type: ignore[attr-defined]
    row = database.connection.execute(
        "SELECT status FROM document_records WHERE object_id = ?", (object_id,)
    ).fetchone()
    assert row is not None and str(row["status"]) == "parsing"
    first._database.close()  # type: ignore[attr-defined]

    # 第二次执行器（重启）：租约仍在有效期内 → 不重复领取；过租约后恢复
    second = BackgroundExecutor(settings)
    summary = second.run_tick()
    assert "处理 0 份文档" in summary  # 租约未过期，幂等不重复领取
    database.connection.execute(
        "UPDATE document_records SET lease_expires_at = '2020-01-01T00:00:00+00:00'"
        " WHERE object_id = ?",
        (object_id,),
    )
    database.connection.commit()
    summary = second.run_tick()
    assert "处理 1 份文档" in summary
    row = database.connection.execute(
        "SELECT status FROM document_records WHERE object_id = ?", (object_id,)
    ).fetchone()
    assert row is not None and str(row["status"]) == "ready"
    second._database.close()  # type: ignore[attr-defined]


def _runtime_settings_with_secret(tmp: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{tmp}/bridges.db"),
        secret_key=SecretStr(SECRET_KEY),
    )
