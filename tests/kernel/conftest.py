"""节点内核测试共享的 SQLite 数据库夹具。"""

from pathlib import Path

import pytest

from bridges.storage.database import BridgesDatabase


@pytest.fixture
def database(tmp_path: Path) -> BridgesDatabase:
    db = BridgesDatabase(tmp_path / "bridges.db")
    assert db.initialize() > 0
    return db
