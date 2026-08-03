"""Issue 18：全局本地知识库测试基建。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

SECRET_KEY = "knowledge-base-test-secret-key"


@pytest.fixture()
def storage(tmp_path: Path) -> dict[str, Any]:
    """临时数据目录上的权威数据库 + 加密对象库。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = BridgesObjectRepository(
        database,
        EncryptedFileObjectStore(
            tmp_path / "objects", encryption_key=SecretStr(SECRET_KEY)
        ),
    )
    account_a = repository.register_account("33333333@qq.com")
    account_b = repository.register_account("44444444@qq.com")
    return {
        "database": database,
        "repository": repository,
        "account_a": account_a,
        "account_b": account_b,
        "path": tmp_path,
    }
