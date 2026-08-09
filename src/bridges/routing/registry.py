"""可追加的能力路由注册表。"""

from __future__ import annotations

from dataclasses import dataclass

from bridges.routing.contracts import MainCapability


class CapabilityRouteRegistryError(ValueError):
    """能力路由注册冲突或未知能力。"""


@dataclass(frozen=True)
class CapabilityDefinition:
    """能力分类元数据；注册后不可覆盖。"""

    capability: str
    version: str
    priority: int
    description: str


class CapabilityRouteRegistry:
    """只允许追加注册，同一版本重复注册必须是完全相同的定义。"""

    def __init__(self, definitions: tuple[CapabilityDefinition, ...] = ()) -> None:
        self._definitions: dict[tuple[str, str], CapabilityDefinition] = {}
        for definition in definitions:
            self.register(definition)

    @classmethod
    def builtin(cls) -> CapabilityRouteRegistry:
        return cls(
            (
                CapabilityDefinition(
                    MainCapability.ORDINARY_CHAT.value,
                    "1.0.0",
                    0,
                    "普通日常陪伴或学习回答",
                ),
                CapabilityDefinition(
                    MainCapability.PAPER_SEARCH.value,
                    "1.0.0",
                    100,
                    "受控 arXiv 论文搜索",
                ),
                CapabilityDefinition(
                    MainCapability.VIDEO.value,
                    "1.0.0",
                    100,
                    "固定 Wan 文生视频",
                ),
                CapabilityDefinition(
                    MainCapability.CAREER.value,
                    "1.0.0",
                    100,
                    "确定性生涯规划路由",
                ),
            )
        )

    def register(self, definition: CapabilityDefinition) -> None:
        key = (definition.capability, definition.version)
        if key in self._definitions and self._definitions[key] != definition:
            raise CapabilityRouteRegistryError(
                f"能力路由已注册且不能覆盖：{definition.capability}@{definition.version}。"
            )
        self._definitions[key] = definition

    def get(self, capability: str, version: str) -> CapabilityDefinition:
        try:
            return self._definitions[(capability, version)]
        except KeyError as exc:
            raise CapabilityRouteRegistryError(
                f"未知能力路由：{capability}@{version}。"
            ) from exc

    def list(self) -> list[CapabilityDefinition]:
        return list(self._definitions.values())
