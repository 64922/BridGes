"""复合计划的登记目录：能力、配方版本与合法组合的唯一事实源（工单 37）。

计划只能引用这里登记的模块描述符；描述符绑定各领域真实配方的
``recipe_id``/``recipe_version`` 与入口能力版本，模型或理解输出不能
凭空创建模块、能力或组合。允许的组合以显式表登记（有限跨模块计划），
未登记组合在理解层保持澄清，在计划校验层被拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    """一个可组合模块的登记描述符。"""

    module_id: str
    recipe_id: str
    recipe_version: str
    capabilities: Mapping[str, str]
    entry_capability: str
    projection_field: str
    wait_reason: str
    public_network: bool
    purpose: str
    #: 提供「已确认身份」的模块（论文选定结果）；消费者在未确认时不得声称对应。
    identity_provider: bool = False
    #: 需要上游确认身份的模块（GitHub 对应实现声称）。
    identity_consumer: bool = False
    #: 私人参数名（绝不进入公网步骤）。
    private_parameters: tuple[str, ...] = ()
    #: 允许进入公网查询的最小参数名。
    public_parameters: tuple[str, ...] = ()
    #: 条件依赖键：任务条件变化时随输入依赖失效。
    condition_keys: tuple[str, ...] = ()
    #: 该模块是否已支持复合调度的无终态执行（否则只允许单模块派发）。
    composite_ready: bool = True


class ModuleRegistry:
    """受控模块目录：只回答已登记模块与已登记组合。"""

    def __init__(self, descriptors: Mapping[str, ModuleDescriptor]) -> None:
        self._descriptors = dict(descriptors)

    @property
    def modules(self) -> frozenset[str]:
        return frozenset(self._descriptors)

    def get(self, module_id: str) -> ModuleDescriptor:
        descriptor = self._descriptors.get(module_id)
        if descriptor is None:
            raise KeyError(module_id)
        return descriptor

    def known(self, module_id: str) -> bool:
        return module_id in self._descriptors

    def capability_version(self, module_id: str, capability: str) -> str | None:
        descriptor = self._descriptors.get(module_id)
        if descriptor is None:
            return None
        return descriptor.capabilities.get(capability)

    def combination(self, module_ids: frozenset[str]) -> dict[str, tuple[str, ...]] | None:
        """返回登记组合的依赖表（module_id → 上游 module_id 元组）。

        未登记组合返回 ``None``：理解层保持澄清，计划校验层拒绝。
        """
        if len(module_ids) < 2:
            return None
        rule = COMPOSITE_COMBINATIONS.get(module_ids)
        if rule is None:
            return None
        result: dict[str, tuple[str, ...]] = {}
        for module_id, dependencies in rule.items():
            result[module_id] = tuple(dependencies)
        return result

    @classmethod
    def production(cls) -> ModuleRegistry:
        """绑定真实领域配方的生产登记表。"""
        from bridges.commute.kernel import (
            COMMUTE_CAPABILITY_VERSIONS,
            COMMUTE_RECIPE_ID,
            COMMUTE_RECIPE_VERSION,
        )
        from bridges.paper.kernel import (
            PAPER_CAPABILITY_VERSIONS,
            PAPER_RECIPE_ID,
            PAPER_RECIPE_VERSION,
        )
        from bridges.tieba.recipe import (
            TIEBA_CAPABILITY_VERSIONS,
            TIEBA_RECIPE_ID,
            TIEBA_RECIPE_VERSION,
        )

        return cls(
            {
                "paper": ModuleDescriptor(
                    module_id="paper",
                    recipe_id=PAPER_RECIPE_ID,
                    recipe_version=PAPER_RECIPE_VERSION,
                    capabilities=PAPER_CAPABILITY_VERSIONS,
                    entry_capability="paper.parse_request",
                    projection_field="paper_search",
                    wait_reason="paper_clarification",
                    public_network=True,
                    purpose="按研究意图检索、筛选并交付可核实的论文选择。",
                    identity_provider=True,
                    private_parameters=("prior_context", "profile_slice"),
                    public_parameters=("topic", "year_range", "count"),
                    condition_keys=("topic", "year_range"),
                ),
                "tieba": ModuleDescriptor(
                    module_id="tieba",
                    recipe_id=TIEBA_RECIPE_ID,
                    recipe_version=TIEBA_RECIPE_VERSION,
                    capabilities=TIEBA_CAPABILITY_VERSIONS,
                    entry_capability="tieba.parse_request",
                    projection_field="tieba_research",
                    wait_reason="tieba_clarification",
                    public_network=True,
                    purpose="按问题类型编排规定与体验证据的贴吧取证。",
                    private_parameters=("prior_context", "profile_slice"),
                    public_parameters=("event", "topic", "time_range"),
                    condition_keys=("topic", "time_range"),
                    composite_ready=False,
                ),
                "commute": ModuleDescriptor(
                    module_id="commute",
                    recipe_id=COMMUTE_RECIPE_ID,
                    recipe_version=COMMUTE_RECIPE_VERSION,
                    capabilities=COMMUTE_CAPABILITY_VERSIONS,
                    entry_capability="commute.parse_request",
                    projection_field="commute_route",
                    wait_reason="commute_clarification",
                    public_network=True,
                    purpose="在校内范围内用真实路线数据解释通勤时间。",
                    private_parameters=("profile_slice",),
                    public_parameters=("origin", "destination", "mode"),
                    condition_keys=("origin", "destination", "mode"),
                    composite_ready=False,
                ),
            }
        )


#: 登记的跨模块组合（模块集合 → 模块依赖表）。
#: 原有组合均包含已退役模块；当前三个模块没有登记跨模块执行组合。
COMPOSITE_COMBINATIONS: dict[frozenset[str], dict[str, tuple[str, ...]]] = {}


#: 每个模块在复合计划中的默认目的与参数绑定说明（供计划构造与审计）。
DEFAULT_PURPOSES: dict[str, str] = {
    "paper": "检索并核实用户主题下的入门论文候选。",
    "tieba": "按问题类型编排规定与体验证据。",
    "commute": "用真实路线数据解释校内通勤。",
}


__all__ = [
    "COMPOSITE_COMBINATIONS",
    "DEFAULT_PURPOSES",
    "ModuleDescriptor",
    "ModuleRegistry",
]
