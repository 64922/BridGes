"""BridGes 存储层：版本化事务数据库与账户隔离加密对象库。

Issue 05 交付的桥梁：空的 ``bridges.db``（带版本记录、外键、显式事务边界）、
内容哈希路径的加密对象库、按不可变内部账户 ID 授权的对象仓库。
"""

from __future__ import annotations

from bridges.storage.database import (
    MIGRATIONS,
    SCHEMA_INTEGRITY_ERROR_CODE,
    SCHEMA_VERSION,
    BridgesDatabase,
)
from bridges.storage.errors import StorageError
from bridges.storage.object_store import EncryptedFileObjectStore
from bridges.storage.repository import (
    OBJECT_STATUS_ACTIVE,
    OBJECT_STATUS_PENDING_CLEANUP,
    BridgesObjectRepository,
    StoredObject,
)

__all__ = [
    "BridgesDatabase",
    "BridgesObjectRepository",
    "EncryptedFileObjectStore",
    "MIGRATIONS",
    "OBJECT_STATUS_ACTIVE",
    "OBJECT_STATUS_PENDING_CLEANUP",
    "SCHEMA_INTEGRITY_ERROR_CODE",
    "SCHEMA_VERSION",
    "StorageError",
    "StoredObject",
]
