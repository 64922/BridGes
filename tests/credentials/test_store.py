"""凭据存储层单元测试。

覆盖内存替身、加密凭据卷（主密钥自动生成/复用、密文不落明文、跨账户
隔离、损坏处理）与 OS 凭据库（keyring 路径、Windows DPAPI 兜底路径、
无凭据库时报错不落明文）。
"""

from __future__ import annotations

import base64

import pytest
from pydantic import SecretStr

from bridges.credentials.store import (
    CredentialStoreError,
    EncryptedVolumeCredentialStore,
    InMemoryCredentialStore,
    OsCredentialStore,
)


class _FakeKeyring:
    """模拟 keyring 的进程内实现。"""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._entries[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._entries.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._entries.pop((service, username), None)


def test_in_memory_store_is_account_scoped() -> None:
    store = InMemoryCredentialStore()
    store.save("alice", SecretStr("sk-alice-0000"))
    assert store.get("alice") is not None
    assert store.get("alice") == SecretStr("sk-alice-0000")  # type: ignore[operator]
    assert store.get("bob") is None

    store.delete("alice")
    assert store.get("alice") is None
    # 删除不存在的账户静默成功。
    store.delete("missing")


def test_encrypted_volume_master_key_auto_generated_and_reused(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = EncryptedVolumeCredentialStore(tmp_path)
    store.save("alice", SecretStr("sk-alice-0000"))

    volume = tmp_path / "credentials"
    master_key = volume / "master.key"
    assert master_key.exists()
    assert len(master_key.read_bytes()) > 0

    # 磁盘上不存在 Key 明文。
    for blob in (volume / "keys").glob("*.enc"):
        assert b"sk-alice" not in blob.read_bytes()

    # 同一数据目录的新实例复用主密钥并能解密。
    reopened = EncryptedVolumeCredentialStore(tmp_path)
    assert reopened.get("alice") == SecretStr("sk-alice-0000")  # type: ignore[operator]

    # 账户间互不可见。
    assert reopened.get("bob") is None


def test_encrypted_volume_roundtrip_and_delete(tmp_path: pytest.TempPathFactory) -> None:
    store = EncryptedVolumeCredentialStore(tmp_path)
    store.save("alice", SecretStr("sk-alice-0000"))
    assert store.get("alice") == SecretStr("sk-alice-0000")  # type: ignore[operator]

    store.save("alice", SecretStr("sk-alice-new-1111"))
    assert store.get("alice") == SecretStr("sk-alice-new-1111")  # type: ignore[operator]

    store.delete("alice")
    assert store.get("alice") is None


def test_encrypted_volume_failed_atomic_replace_keeps_old_credential(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EncryptedVolumeCredentialStore(tmp_path)
    store.save("alice", SecretStr("sk-alice-old-0000"))

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr("bridges.credentials.store.os.replace", fail_replace)

    with pytest.raises(CredentialStoreError):
        store.save("alice", SecretStr("sk-alice-new-1111"))

    assert store.get("alice") == SecretStr("sk-alice-old-0000")  # type: ignore[operator]


def test_encrypted_volume_rejects_corrupted_master_key(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = EncryptedVolumeCredentialStore(tmp_path)
    store.save("alice", SecretStr("sk-alice-0000"))
    (tmp_path / "credentials" / "master.key").write_bytes(b"not-a-fern")

    with pytest.raises(CredentialStoreError):
        store.get("alice")


def test_encrypted_volume_rejects_corrupted_blob(
    tmp_path: pytest.TempPathFactory,
) -> None:
    store = EncryptedVolumeCredentialStore(tmp_path)
    store.save("alice", SecretStr("sk-alice-0000"))
    blob = next((tmp_path / "credentials" / "keys").glob("*.enc"))
    blob.write_bytes(b"tampered")

    with pytest.raises(CredentialStoreError):
        store.get("alice")


def test_os_store_uses_keyring_when_available(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr("bridges.credentials.store.keyring", fake)
    store = OsCredentialStore(data_dir=tmp_path)

    store.save("alice", SecretStr("sk-alice-0000"))
    assert store.get("alice") == SecretStr("sk-alice-0000")  # type: ignore[operator]
    # Keyring 按账户命名空间隔离。
    assert store.get("bob") is None
    assert fake.get_password("BridGes", "account:alice") == "sk-alice-0000"

    store.save("alice", SecretStr("sk-alice-new-1111"))
    assert store.get("alice") == SecretStr("sk-alice-new-1111")  # type: ignore[operator]

    store.delete("alice")
    assert store.get("alice") is None
    assert store.get("bob") is None


def test_os_store_dpapi_fallback_on_windows(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 keyring 时 Windows 走 DPAPI 加密的账户密文，磁盘无明文。"""
    monkeypatch.setattr("bridges.credentials.store.keyring", None)
    monkeypatch.setattr(
        "bridges.credentials.store._dpapi_available", lambda: True
    )
    monkeypatch.setattr(
        "bridges.credentials.store._dpapi_protect",
        lambda plaintext: base64.b64encode(plaintext),
    )
    monkeypatch.setattr(
        "bridges.credentials.store._dpapi_unprotect",
        lambda ciphertext: base64.b64decode(ciphertext),
    )
    store = OsCredentialStore(data_dir=tmp_path)

    store.save("alice", SecretStr("sk-alice-0000"))
    assert store.get("alice") == SecretStr("sk-alice-0000")  # type: ignore[operator]

    # 磁盘上只有 DPAPI 密文（这里为 base64），没有 Key 明文。
    blob = next((tmp_path / "credentials" / "keys").glob("*.enc"))
    assert b"sk-alice" not in blob.read_bytes()

    store.delete("alice")
    assert store.get("alice") is None


def test_os_store_raises_without_credential_backend(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有凭据库的环境必须报错，绝不写入明文文件。"""
    monkeypatch.setattr("bridges.credentials.store.keyring", None)
    monkeypatch.setattr(
        "bridges.credentials.store._dpapi_available", lambda: False
    )
    store = OsCredentialStore(data_dir=tmp_path)

    with pytest.raises(CredentialStoreError):
        store.save("alice", SecretStr("sk-alice-0000"))
    with pytest.raises(CredentialStoreError):
        store.get("alice")
    with pytest.raises(CredentialStoreError):
        store.delete("alice")

    # 数据目录不落任何凭据文件。
    assert not (tmp_path / "credentials").exists()
