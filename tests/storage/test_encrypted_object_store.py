"""Issue 05：加密对象库静态落盘、内容完整性与哈希路径测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bridges.storage import EncryptedFileObjectStore, StorageError


def _store(tmp_path: Path) -> EncryptedFileObjectStore:
    return EncryptedFileObjectStore(tmp_path, encryption_key="test-master-key")


def test_object_files_are_encrypted_at_rest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    content_hash = store.put("秘密实验数据：不可直接读取".encode())
    object_file = tmp_path / "objects" / content_hash[:2] / content_hash

    assert object_file.exists()
    raw = object_file.read_bytes()
    assert "秘密实验数据".encode() not in raw  # 静态落盘前已加密
    assert store.get(content_hash) == "秘密实验数据：不可直接读取".encode()


def test_object_read_reports_corruption_in_chinese(tmp_path: Path) -> None:
    store = _store(tmp_path)
    content_hash = store.put(b"integrity-checked-content")
    object_file = tmp_path / "objects" / content_hash[:2] / content_hash
    object_file.write_bytes(b"corrupted-bytes")

    with pytest.raises(StorageError) as exc_info:
        store.get(content_hash)
    message = str(exc_info.value)
    assert "损坏" in message
    assert str(tmp_path) not in message  # 不输出宿主敏感绝对路径


def test_object_deduplication_by_content_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.put(b"same content twice")
    second = store.put(b"same content twice")

    assert first == second
    assert len(list((tmp_path / "objects").rglob(first))) == 1


def test_object_files_keyed_by_generated_hash_not_filename(tmp_path: Path) -> None:
    store = _store(tmp_path)
    content_hash = store.put(b"filename must not leak")
    object_file = tmp_path / "objects" / content_hash[:2] / content_hash

    # 落盘路径只含内容哈希，不含原文件名、用户名或账户信息。
    assert object_file.name == content_hash
    assert "report.pdf" not in object_file.name
    assert "paper.doc" not in object_file.name
