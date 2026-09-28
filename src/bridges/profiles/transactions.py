"""画像仓库的事务边界工具。

四维记录仓库与原子条目仓库常常共用同一个 ``BridgesDatabase``（单连接
SQLite），而单连接不允许嵌套 ``BEGIN``。把「已在事务内就并入外层」这条
规则放在这里，让两个 SQLite adapter 用同一份实现，而不是各自判断连接
状态，或由调用方记住谁和谁共库。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from bridges.storage.database import BridgesDatabase


@contextmanager
def joined_transaction(database: BridgesDatabase) -> Iterator[None]:
    """数据库事务边界；已在外层事务内时并入外层，不重复 ``BEGIN``。

    同一连接上的多次写入因此天然属于同一次提交（失败一起回滚）；独立
    调用时自己开启事务。
    """

    if database.connection.in_transaction:
        yield
        return
    with database.transaction():
        yield
