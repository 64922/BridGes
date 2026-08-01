"""账户隔离的加密对象库。

对象以应用生成的对象 ID（数据库主键）与内容哈希（落盘路径）保存，原文件名
仅作为元数据；对象内容在静态落盘前用主密钥派生的 Fernet 密钥加密，数据库、
日志与 API 响应中均不出现密钥引用或明文。
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import InvalidToken
from pydantic import SecretStr

from bridges.persistence import derive_fernet
from bridges.storage.errors import StorageError


class EncryptedFileObjectStore:
    """基于内容哈希路径的加密文件对象库。

    布局：``<root>/objects/<hash 前两位>/<hash>``。写入走临时文件 + 原子替换，
    读取时校验内容哈希，损坏立即报中文错误。路径只含内容哈希，不包含账户名、
    用户名或原文件名，静态路径无法绕过授权。
    """

    def __init__(self, root: str | Path, encryption_key: SecretStr | str) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        try:
            self.objects_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                "无法创建对象存储目录，请检查数据目录权限。"
            ) from exc
        self._fernet = derive_fernet(encryption_key)

    def put(self, content: bytes) -> str:
        """加密并落盘对象内容，返回内容 SHA-256 哈希。"""
        content_hash = hashlib.sha256(content).hexdigest()
        ciphertext = self._fernet.encrypt(content)
        target = self._path_for(content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp-{secrets.token_hex(8)}"
        try:
            with tmp.open("wb") as handle:
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(
                "对象写入失败，请检查数据目录可用空间与权限。"
            ) from exc
        return content_hash

    def get(self, content_hash: str) -> bytes:
        """读取并解密对象内容；内容损坏或密钥不匹配时报中文错误。"""
        target = self._path_for(content_hash)
        try:
            ciphertext = target.read_bytes()
        except OSError as exc:
            raise StorageError("对象内容缺失，请检查数据目录。") from exc
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise StorageError(
                "对象内容损坏或加密密钥不匹配，无法读取。"
            ) from exc
        if hashlib.sha256(plaintext).hexdigest() != content_hash:
            raise StorageError("对象内容损坏：内容哈希与记录不一致。")
        return plaintext

    def remove(self, content_hash: str) -> None:
        """删除一个内容哈希对应的对象文件；不存在时静默成功。

        调用方（仓库层）必须确保没有其他记录引用该哈希后才可调用。
        """
        self._path_for(content_hash).unlink(missing_ok=True)

    def list_files(self) -> set[str]:
        """返回对象目录下全部内容哈希，用于孤立对象检测。"""
        hashes: set[str] = set()
        if not self.objects_dir.exists():
            return hashes
        for directory in self.objects_dir.iterdir():
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.name.startswith(".tmp-"):
                    continue
                hashes.add(path.name)
        return hashes

    def _path_for(self, content_hash: str) -> Path:
        return self.objects_dir / content_hash[:2] / content_hash
