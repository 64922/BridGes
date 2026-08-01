"""Issue 05：双账户端到端隔离测试。

覆盖创建、读取、重启恢复、猜测 ID、删除与派生清理：两个账户分别保存对象，
重启服务后各自只读回自己的数据，任何数据与对象均不串号。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
    StorageError,
)


def _build(tmp_path: Path) -> BridgesObjectRepository:
    database = BridgesDatabase(tmp_path / "bridges.db")
    return BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(tmp_path, encryption_key="test-master-key"),
    )


def _secret_message(name: str) -> bytes:
    return f"{name} 的私人科学笔记内容".encode()


def test_two_accounts_isolation_survives_restart_and_guess_id(
    tmp_path: Path,
) -> None:
    repository = _build(tmp_path)
    alice = repository.register_account("alice@example.com")
    bob = repository.register_account("bob@example.com")

    alice_note = repository.create_object(alice, "笔记.docx", _secret_message("爱丽丝"))
    alice_paper = repository.create_object(alice, "论文.pdf", b"alice paper bytes")
    bob_note = repository.create_object(bob, "笔记.docx", _secret_message("鲍勃"))

    # 重启服务：关闭并重新打开同一数据目录。
    repository.close()
    repository = _build(tmp_path)

    # 重启恢复：各自读回自己的记录与对象。
    reloaded = repository.get_object(alice, alice_note.object_id)
    assert reloaded.original_filename == "笔记.docx"
    assert reloaded.content_hash == alice_note.content_hash
    assert repository.get_content(alice, alice_note.object_id) == _secret_message(
        "爱丽丝"
    )
    assert repository.get_content(bob, bob_note.object_id) == _secret_message("鲍勃")

    # 猜测 ID：跨账户读取返回与不存在相同的错误，不泄露对象是否存在。
    with pytest.raises(StorageError) as exc_info:
        repository.get_object(bob, alice_paper.object_id)
    assert "对象不存在或没有访问权限" in str(exc_info.value)
    with pytest.raises(StorageError) as exc_info:
        repository.get_content(bob, alice_note.object_id)
    assert "对象不存在或没有访问权限" in str(exc_info.value)
    with pytest.raises(StorageError) as exc_info:
        repository.delete_object(bob, alice_note.object_id)
    assert "对象不存在或没有访问权限" in str(exc_info.value)

    # 列表不串号。
    alice_list = {obj.object_id for obj in repository.list_objects(alice)}
    bob_list = {obj.object_id for obj in repository.list_objects(bob)}
    assert alice_note.object_id in alice_list
    assert bob_note.object_id in bob_list
    assert alice_list.isdisjoint(bob_list)

    # 删除 + 派生清理：元数据行与物理文件都完整删除，鲍勃数据不受影响。
    repository.delete_object(alice, alice_paper.object_id)
    assert repository.list_pending_cleanups() == []
    with pytest.raises(StorageError):
        repository.get_object(alice, alice_paper.object_id)
    object_file = (
        tmp_path / "objects" / alice_paper.content_hash[:2] / alice_paper.content_hash
    )
    assert not object_file.exists()
    assert repository.get_content(bob, bob_note.object_id) == _secret_message("鲍勃")
    assert repository.get_content(alice, alice_note.object_id) == _secret_message(
        "爱丽丝"
    )


def test_delete_failure_keeps_pending_object_cleanup_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _build(tmp_path)
    owner = repository.register_account("retry@example.com")
    stored = repository.create_object(owner, "待清理.txt", b"to-be-cleaned")

    # 模拟物理删除失败：对象进入可观察、可重试的待清理状态。
    def failing_remove(self: EncryptedFileObjectStore, content_hash: str) -> None:
        raise StorageError("模拟对象文件删除失败（中文原因）。")

    monkeypatch.setattr(EncryptedFileObjectStore, "remove", failing_remove)
    marked = repository.delete_object(owner, stored.object_id)
    monkeypatch.undo()

    assert marked.status == "pending_cleanup"  # 返回标记后的记录，而非旧 active 行
    pending = repository.list_pending_cleanups()
    assert len(pending) == 1
    assert pending[0].object_id == stored.object_id
    assert pending[0].status == "pending_cleanup"
    assert pending[0].cleanup_retry_count == 1
    assert "删除失败" in (pending[0].last_cleanup_error or "")

    # 重试成功：整行与物理文件均消失，实现完整删除。
    cleaned = repository.run_pending_cleanups()
    assert cleaned == 1
    assert repository.list_pending_cleanups() == []
    with pytest.raises(StorageError):
        repository.get_object(owner, stored.object_id)
    object_file = (
        tmp_path / "objects" / stored.content_hash[:2] / stored.content_hash
    )
    assert not object_file.exists()


def test_orphan_object_files_are_observable_and_retryable(tmp_path: Path) -> None:
    repository = _build(tmp_path)
    owner = repository.register_account("orphan@example.com")
    stored = repository.create_object(owner, "孤儿.txt", b"orphan-content")

    # 模拟元数据写入失败留下的孤立文件：文件仍在但元数据行已消失。
    with sqlite3.connect(tmp_path / "bridges.db") as connection:
        connection.execute(
            "DELETE FROM objects WHERE object_id = ?", (stored.object_id,)
        )

    orphans = repository.find_orphans()
    assert stored.content_hash in orphans
    assert repository.cleanup_orphans() == 1
    assert repository.find_orphans() == []


def test_database_write_failure_rolls_back_object_file(tmp_path: Path) -> None:
    repository = _build(tmp_path)
    owner = repository.register_account("rollback@example.com")
    with sqlite3.connect(tmp_path / "bridges.db") as connection:
        connection.execute("DROP TABLE objects")

    # 元数据写入失败时刚落盘的对象文件被回收，且报中文运维错误而非原始
    # sqlite 异常，数据库与对象保持一致。
    with pytest.raises(StorageError) as exc_info:
        repository.create_object(owner, "失败.txt", b"never-stored")
    assert "数据目录" in str(exc_info.value)
    remaining_files = [
        path
        for path in (tmp_path / "objects").rglob("*")
        if path.is_file()
    ]
    assert remaining_files == []


def test_two_accounts_shared_content_delete_keeps_other_account_readable(
    tmp_path: Path,
) -> None:
    """内容哈希去重：一个账户删除共享对象不得破坏另一账户的同一内容。"""
    repository = _build(tmp_path)
    alice = repository.register_account("alice-share@example.com")
    bob = repository.register_account("bob-share@example.com")
    shared_bytes = b"identical scientific notes"

    alice_object = repository.create_object(alice, "共享.txt", shared_bytes)
    bob_object = repository.create_object(bob, "共享.txt", shared_bytes)
    # 去重：同一内容只落盘一个文件。
    assert alice_object.content_hash == bob_object.content_hash
    assert len(list((tmp_path / "objects").rglob(alice_object.content_hash))) == 1

    # 爱丽丝删除她的记录：文件仍被鲍勃引用，物理文件必须保留。
    repository.delete_object(alice, alice_object.object_id)
    object_file = (
        tmp_path / "objects" / alice_object.content_hash[:2] / alice_object.content_hash
    )
    assert object_file.exists()
    assert repository.get_content(bob, bob_object.object_id) == shared_bytes

    # 鲍勃删除他的记录后：无任何引用，物理文件才被完整清理。
    repository.delete_object(bob, bob_object.object_id)
    assert not object_file.exists()
    assert repository.find_orphans() == []


def test_object_creation_requires_registered_account(tmp_path: Path) -> None:
    repository = _build(tmp_path)
    with pytest.raises(StorageError) as exc_info:
        repository.create_object("ghost-account", "x.txt", b"x")
    assert "账户" in str(exc_info.value)


def test_account_sqlite_registration_rejects_duplicate_email(tmp_path: Path) -> None:
    repository = _build(tmp_path)
    repository.register_account("dup@example.com")
    with pytest.raises(StorageError) as exc_info:
        repository.register_account("dup@example.com")
    assert "已注册" in str(exc_info.value)
