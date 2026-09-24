"""按命名空间隔离的安装级/账户级凭据存储：OS 凭据库与加密凭据卷。

账户级百炼密钥已按 GQ-07 清退；当前存储仍为 QQ SMTP 授权码提供
``smtp`` 命名空间（ADR-0004/0017），同时允许本机启动器使用独立的
``runtime`` 命名空间保存安装级全局 Qwen 凭据：

- 源码环境（Conda / .venv）使用操作系统凭据库：``keyring``
  （Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service）为项目
  声明依赖；个别精简环境无法导入 keyring 时，Windows 退回 DPAPI
  （CryptProtectData，系统级凭据保护）加密的账户密文，密文仍由操作系统
  密钥保护。其余平台无凭据库时明确报错，绝不写入明文文件。
- 容器环境使用自动生成主密钥保护的加密凭据卷：首次启动自动生成 Fernet
  主密钥并写入数据目录（0600 权限），每账户凭据用主密钥加密后单独落盘。

任何实现都不把秘密写入 SQLite 明文字段、``.env``、日志或 API 响应；
凭据存储不可用时抛出中文错误，绝不静默降级为明文文件。
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

#: keyring 为可选的系统凭据库接入；缺失时在 Windows 走 DPAPI 兜底。
try:
    import keyring
except ImportError:  # pragma: no cover - 依赖可选，覆盖路径由 CI 判定
    keyring = None  # type: ignore[assignment]

#: keyring 服务名与用户名命名空间。
_KEYRING_SERVICE = "BridGes"
_KEYRING_USERNAME_PREFIX = "account:"
#: 默认凭据命名空间。业务调用方应显式选择 ``smtp`` 或 ``runtime``，
#: 以便与历史账户凭据在存储和文件层面保持隔离。
_DEFAULT_NAMESPACE = "account"

#: 加密凭据卷子目录与文件布局。
_CREDENTIALS_DIR = "credentials"
_MASTER_KEY_FILE = "master.key"
_KEYS_DIR = "keys"
_ACCOUNT_BLOB_SUFFIX = ".enc"


class CredentialStoreError(Exception):
    """凭据存储领域错误。

    消息面向运维与用户，说明存储不可用的中文原因，不包含任何秘密正文。
    """


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace one encrypted credential blob without truncating the old value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
        os.replace(temporary_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def has_credential_backend(data_dir: Path | None = None) -> bool:
    """当前环境是否存在可用的操作系统凭据后端（GQ-07 清退判定用）。

    keyring 可用（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret
    Service）或 Windows DPAPI 兜底（需要数据目录落盘）时返回 True。
    两者皆无的环境里，旧实现同样拒绝写入任何账户秘密，升级清退可以
    安全跳过秘密删除而不必失败关闭。
    """
    if keyring is not None:
        return True
    return _dpapi_available() and data_dir is not None


class CredentialStorePort(ABC):
    """按命名空间标识保存、读取与删除凭据的稳定端口。"""

    @abstractmethod
    def save(self, account_id: str, secret: SecretStr) -> None:
        """保存或替换当前标识的凭据。"""

    @abstractmethod
    def get(self, account_id: str) -> SecretStr | None:
        """返回当前标识的凭据，未配置时返回 None。"""

    @abstractmethod
    def delete(self, account_id: str) -> None:
        """删除当前标识的凭据；不存在时静默成功。"""


class InMemoryCredentialStore(CredentialStorePort):
    """进程内测试替身，模拟系统凭据库行为。"""

    def __init__(self, namespace: str = _DEFAULT_NAMESPACE) -> None:
        self._namespace = namespace
        self._secrets: dict[str, SecretStr] = {}

    def _key(self, account_id: str) -> str:
        return f"{self._namespace}:{account_id}"

    def save(self, account_id: str, secret: SecretStr) -> None:
        self._secrets[self._key(account_id)] = secret

    def get(self, account_id: str) -> SecretStr | None:
        return self._secrets.get(self._key(account_id))

    def delete(self, account_id: str) -> None:
        self._secrets.pop(self._key(account_id), None)


def _dpapi_available() -> bool:
    """Windows 且 crypt32.dll 可加载时 DPAPI 兜底可用。"""
    return os.name == "nt" and ctypes.windll.kernel32 is not None


def _dpapi_protect(plaintext: bytes) -> bytes:
    """用 DPAPI 加密当前 Windows 用户的敏感字节。"""
    import ctypes.wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def _to_blob(data: bytes) -> _DataBlob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(blob: _DataBlob) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    crypt32 = ctypes.windll.crypt32
    in_blob = _to_blob(plaintext)
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialStoreError("操作系统凭据保护不可用，无法安全保存密钥。")
    try:
        return _from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    """用 DPAPI 解密当前 Windows 用户的敏感字节。"""
    import ctypes.wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def _to_blob(data: bytes) -> _DataBlob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(blob: _DataBlob) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    crypt32 = ctypes.windll.crypt32
    in_blob = _to_blob(ciphertext)
    out_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialStoreError("操作系统凭据保护不可用，无法读取密钥。")
    try:
        return _from_blob(out_blob)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class OsCredentialStore(CredentialStorePort):
    """操作系统凭据库实现。

    首选 ``keyring``（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret
    Service）；Windows 缺少 keyring 时退回 DPAPI 加密的命名空间密文（密文由
    操作系统密钥保护，保存在数据目录凭据卷下）。其余平台无凭据库时明确
    报错，绝不写入明文文件。
    """

    def __init__(self, data_dir: Path | None = None, namespace: str = _DEFAULT_NAMESPACE) -> None:
        self._data_dir = data_dir
        self._namespace = namespace

    def _username(self, account_id: str) -> str:
        return f"{self._namespace}:{account_id}"

    def save(self, account_id: str, secret: SecretStr) -> None:
        value = secret.get_secret_value()
        if keyring is not None:
            try:
                keyring.set_password(_KEYRING_SERVICE, self._username(account_id), value)
                return
            except Exception as exc:
                raise CredentialStoreError(
                    "操作系统凭据库写入失败，请检查系统凭据管理器状态。"
                ) from exc
        if _dpapi_available() and self._data_dir is not None:
            self._write_dpapi_blob(account_id, _dpapi_protect(value.encode("utf-8")))
            return
        raise CredentialStoreError(
            "当前环境没有可用的操作系统凭据库，无法安全保存密钥。"
        )

    def get(self, account_id: str) -> SecretStr | None:
        if keyring is not None:
            try:
                value = keyring.get_password(
                    _KEYRING_SERVICE, self._username(account_id)
                )
            except Exception as exc:
                raise CredentialStoreError(
                    "操作系统凭据库读取失败，请检查系统凭据管理器状态。"
                ) from exc
            if value is None:
                return None
            return SecretStr(value)
        if _dpapi_available() and self._data_dir is not None:
            blob = self._read_dpapi_blob(account_id)
            if blob is None:
                return None
            return SecretStr(_dpapi_unprotect(blob).decode("utf-8"))
        raise CredentialStoreError(
            "当前环境没有可用的操作系统凭据库，无法读取密钥。"
        )

    def delete(self, account_id: str) -> None:
        if keyring is not None:
            try:
                if keyring.get_password(_KEYRING_SERVICE, self._username(account_id)):
                    keyring.delete_password(
                        _KEYRING_SERVICE, self._username(account_id)
                    )
            except Exception as exc:
                raise CredentialStoreError(
                    "操作系统凭据库删除失败，请检查系统凭据管理器状态。"
                ) from exc
            return
        if _dpapi_available() and self._data_dir is not None:
            blob = self._blob_path(account_id)
            if blob.exists():
                blob.unlink()
            return
        raise CredentialStoreError(
            "当前环境没有可用的操作系统凭据库，无法删除密钥。"
        )

    # ------------------------------------------------------------------
    # Windows DPAPI 兜底：密文由 OS 密钥保护，落盘在数据目录凭据卷
    # ------------------------------------------------------------------

    def _blob_path(self, account_id: str) -> Path:
        if self._data_dir is None:
            raise CredentialStoreError("凭据存储未配置数据目录。")
        digest = hashlib.sha256(
            f"{self._namespace}:{account_id}".encode()
        ).hexdigest()[:32]
        return (
            self._data_dir
            / _CREDENTIALS_DIR
            / _KEYS_DIR
            / f"{digest}{_ACCOUNT_BLOB_SUFFIX}"
        )

    def _write_dpapi_blob(self, account_id: str, ciphertext: bytes) -> None:
        path = self._blob_path(account_id)
        _atomic_write_bytes(path, ciphertext)

    def _read_dpapi_blob(self, account_id: str) -> bytes | None:
        path = self._blob_path(account_id)
        if not path.exists():
            return None
        return path.read_bytes()


class EncryptedVolumeCredentialStore(CredentialStorePort):
    """容器用加密凭据卷：自动生成主密钥 + 每账户 Fernet 密文。

    主密钥文件（``credentials/master.key``）首次启动自动生成并限制文件
    权限；后续启动复用。每个账户一个独立密文文件，文件名使用账户 ID 的
    哈希，避免在文件系统中暴露账户标识。
    """

    def __init__(self, data_dir: Path, namespace: str = _DEFAULT_NAMESPACE) -> None:
        self._volume_dir = data_dir / _CREDENTIALS_DIR
        self._keys_dir = self._volume_dir / _KEYS_DIR
        self._namespace = namespace
        self._lock = threading.RLock()

    def _master_key(self) -> bytes:
        """返回主密钥；首次使用自动生成并持久化（0600 权限）。"""
        key_path = self._volume_dir / _MASTER_KEY_FILE
        if key_path.exists():
            raw = key_path.read_bytes()
            if not raw:
                raise CredentialStoreError("凭据主密钥文件为空，请检查数据目录。")
            try:
                return raw
            except Exception as exc:
                raise CredentialStoreError(
                    "凭据主密钥不可用，请检查数据目录权限。"
                ) from exc
        self._volume_dir.mkdir(parents=True, exist_ok=True)
        generated = Fernet.generate_key()
        key_path.write_bytes(generated)
        with contextlib.suppress(OSError):
            # Windows 上 chmod 语义有限；目录权限由容器卷配置保障。
            os.chmod(key_path, 0o600)
        return generated

    def _blob_path(self, account_id: str) -> Path:
        digest = hashlib.sha256(
            f"{self._namespace}:{account_id}".encode()
        ).hexdigest()[:32]
        return self._keys_dir / f"{digest}{_ACCOUNT_BLOB_SUFFIX}"

    def save(self, account_id: str, secret: SecretStr) -> None:
        with self._lock:
            try:
                key = self._master_key()
                self._keys_dir.mkdir(parents=True, exist_ok=True)
                ciphertext = Fernet(key).encrypt(
                    json.dumps(
                        {"key": secret.get_secret_value()}
                    ).encode("utf-8")
                )
                _atomic_write_bytes(self._blob_path(account_id), ciphertext)
            except CredentialStoreError:
                raise
            except Exception as exc:
                raise CredentialStoreError(
                    "加密凭据卷写入失败，请检查数据目录权限与磁盘空间。"
                ) from exc

    def get(self, account_id: str) -> SecretStr | None:
        with self._lock:
            path = self._blob_path(account_id)
            if not path.exists():
                return None
            try:
                key = self._master_key()
                plaintext = Fernet(key).decrypt(path.read_bytes())
                payload: dict[str, Any] = json.loads(plaintext.decode("utf-8"))
                value = payload.get("key")
                if not isinstance(value, str) or not value:
                    raise CredentialStoreError("凭据密文损坏，请重新保存密钥。")
                return SecretStr(value)
            except InvalidToken as exc:
                raise CredentialStoreError(
                    "凭据密文无法解密（主密钥已更换或密文损坏），请重新保存密钥。"
                ) from exc
            except CredentialStoreError:
                raise
            except Exception as exc:
                raise CredentialStoreError(
                    "加密凭据卷读取失败，请检查数据目录权限。"
                ) from exc

    def delete(self, account_id: str) -> None:
        with self._lock:
            path = self._blob_path(account_id)
            if path.exists():
                path.unlink()
