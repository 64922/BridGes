"""进行中事项的领域引用注册、解析与状态路由（画像工单 13/14）。

事项里的领域引用只存「哪个领域、哪个对象」；当前状态由组合根注册的
提供方在读取时实时解析，个人记忆库不复制领域状态。关联领域的目标在
页面上的暂停/继续/完成经领域状态提供方在所属领域内执行，个人记忆页
不写画像条目冒充第二份状态。

本模块不持有存储与控制依赖，供 :mod:`bridges.profiles.ongoing` 复用；
学习领域的实现见 :mod:`bridges.study.profile_links`。
"""

from __future__ import annotations

import logging
from typing import Protocol

from bridges.contracts.atomic_profile import (
    AtomicProfileDomainReference,
    AtomicProfileGoalState,
    AtomicProfileItem,
)
from bridges.contracts.ongoing_profile import (
    OngoingDomainReferenceProjection,
    OngoingDomainReferenceState,
    OngoingGoalAction,
)

logger = logging.getLogger(__name__)


class OngoingDomainReferenceProvider(Protocol):
    """领域引用解析端口：按领域注册，读取时实时解析当前状态。"""

    def resolve(
        self, account_id: str, reference: AtomicProfileDomainReference
    ) -> OngoingDomainReferenceState | None:
        """返回领域状态；对象不存在或不可读时返回 ``None``（未解析）。"""


class OngoingDomainStateProvider(Protocol):
    """领域状态操作端口：页面在关联条目上的暂停/继续/完成由所属领域执行。

    领域内对象的状态不复制进画像；个人记忆页只把「对哪个对象做什么操作」
    与页面读到的领域版本交给所属领域，由领域按其守卫执行，冲突时抛出
    :class:`OngoingDomainReferenceError`（如实拒绝，不覆盖领域新状态）。
    """

    def set_state(
        self,
        account_id: str,
        reference: AtomicProfileDomainReference,
        action: OngoingGoalAction,
        *,
        expected_version: int,
    ) -> OngoingDomainReferenceState:
        """在所属领域内执行状态操作；冲突/不可用时抛出领域错误。"""


class OngoingDomainRegistry:
    """领域提供方注册表：解析实时状态、路由领域状态操作（账户作用域）。"""

    def __init__(self) -> None:
        self._providers: dict[str, OngoingDomainReferenceProvider] = {}
        self._state_providers: dict[str, OngoingDomainStateProvider] = {}

    def register_provider(
        self, domain: str, provider: OngoingDomainReferenceProvider
    ) -> None:
        """注册/替换一个领域的引用解析提供方（学习对接在组合根接线）。"""

        self._providers[domain] = provider

    def register_state_provider(
        self, domain: str, provider: OngoingDomainStateProvider
    ) -> None:
        """注册/替换一个领域的状态操作提供方（画像工单 14；学习目标）。"""

        self._state_providers[domain] = provider

    def state_provider(self, domain: str) -> OngoingDomainStateProvider | None:
        return self._state_providers.get(domain)

    def resolve(
        self, account_id: str, reference: AtomicProfileDomainReference
    ) -> OngoingDomainReferenceProjection:
        """解析一个引用；领域故障/未接通只降级为未解析，绝不伪造状态。"""

        provider = self._providers.get(reference.domain)
        state: OngoingDomainReferenceState | None = None
        if provider is not None:
            try:
                state = provider.resolve(account_id, reference)
            except Exception:  # noqa: BLE001 - 领域故障只降级为未解析
                logger.warning(
                    "进行中事项领域引用解析失败，按未解析展示",
                    exc_info=True,
                    extra={"domain": reference.domain},
                )
                state = None
        return OngoingDomainReferenceProjection(
            domain=reference.domain,
            reference_id=reference.reference_id,
            resolved=state is not None,
            display=state.display if state is not None else None,
            state_code=state.state_code if state is not None else None,
            version=state.version if state is not None else None,
        )


def effective_goal_state(
    item: AtomicProfileItem,
    reference: OngoingDomainReferenceProjection | None,
) -> AtomicProfileGoalState:
    """目标的有效状态：关联领域且解析成功时取领域实时状态，否则取画像自身。

    领域状态码只透传解析，不推断；无法映射为目标生命周期时保守回落画像
    状态并保留未解析/未知标记，绝不伪造领域结论。
    """

    if reference is not None and reference.resolved and reference.state_code:
        try:
            return AtomicProfileGoalState(reference.state_code)
        except ValueError:
            return item.goal_state
    return item.goal_state


__all__ = [
    "OngoingDomainReferenceProvider",
    "OngoingDomainRegistry",
    "OngoingDomainStateProvider",
    "effective_goal_state",
]
