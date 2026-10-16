"""记忆页学习引用区服务（画像工单 14）。

个人记忆页通过 :class:`LearningReferenceReader` 端口读取学习领域的只读
引用投影；端口由组合根注入（学习对接见 :mod:`bridges.study.profile_links`）。
未接通领域时如实报「不可用」，不伪造学习目标或学情。读取不复制领域状态、
不落画像库：学习侧纠正或删除后，下一次读取立即反映。
"""

from __future__ import annotations

from typing import Protocol

from bridges.contracts.learning_references import LearningReferencesProjection


class LearningReferenceReader(Protocol):
    """学习引用投影的读取端口（由所属领域实现，组合根注入）。"""

    def read(self, account_id: str) -> LearningReferencesProjection:
        """返回当前账户的学习引用投影；账户作用域强制。"""


class LearningReferencesUnavailableError(RuntimeError):
    """学习领域读取端口未接通或不可用（路由如实返回 503）。"""


class LearningReferencesService:
    """记忆页读取学习引用投影的薄服务；未接通时不伪造任何条目。"""

    def __init__(self, reader: LearningReferenceReader | None = None) -> None:
        self._reader = reader

    def set_reader(self, reader: LearningReferenceReader | None) -> None:
        """挂载/替换读取端口（组合根在数据库就绪后注入）。"""

        self._reader = reader

    def read(self, account_id: str) -> LearningReferencesProjection:
        reader = self._reader
        if reader is None:
            raise LearningReferencesUnavailableError(
                "学习领域尚未接通，无法读取引用。"
            )
        return reader.read(account_id)


__all__ = [
    "LearningReferencesService",
    "LearningReferencesUnavailableError",
    "LearningReferenceReader",
]
