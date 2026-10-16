"""进行中事项的纯投影层（画像工单 13）。

从原子条目派生事项分组与条目投影，并给出上下文新鲜度与时间基准；不持有
存储、控制或领域依赖，供 :mod:`bridges.profiles.ongoing` 与测试直接复用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from bridges.contracts.atomic_profile import (
    AtomicProfileGoalState,
    AtomicProfileItem,
)
from bridges.contracts.ongoing_profile import (
    ONGOING_CONTEXT_CONTRACT_VERSION,
    OngoingDomainReferenceProjection,
    OngoingEntryProjection,
    OngoingItemProjection,
    OngoingItemRole,
)
from bridges.profiles.atomic import (
    is_ongoing_goal_item,
    ongoing_entry_role,
    validity_status_for,
)
from bridges.profiles.ongoing_repository import StoredOngoingContext

#: 使用开关关闭时的页面提示（稳定文案，前端直接展示）。
ONGOING_USAGE_DISABLED_NOTICE = (
    "长期记忆使用已关闭：回答不会使用这些事项，管理操作仍然可用。"
)


@dataclass(frozen=True)
class _OngoingGroup:
    """一个事项的原子视图：目标条目 + 归组到它的条目。"""

    goal: AtomicProfileItem
    entries: tuple[AtomicProfileItem, ...]


def _group_items(items: Sequence[AtomicProfileItem]) -> list[_OngoingGroup]:
    """按归组引用聚合事项：目标按最近更新倒序，条目挂在唯一目标下。"""

    entries_by_goal: dict[str, list[AtomicProfileItem]] = {}
    goals: list[AtomicProfileItem] = []
    for item in items:
        if is_ongoing_goal_item(item):
            if item.goal_state is not AtomicProfileGoalState.COMPLETED:
                goals.append(item)
            continue
        if item.related_goal_item_id is not None:
            entries_by_goal.setdefault(item.related_goal_item_id, []).append(item)
    goals.sort(key=lambda goal: (-goal.updated_at.timestamp(), goal.profile_item_id))
    return [
        _OngoingGroup(goal=goal, entries=tuple(entries_by_goal.get(goal.profile_item_id, ())))
        for goal in goals
    ]


def _entry_projection(
    item: AtomicProfileItem,
    *,
    now: datetime,
    goal_state: AtomicProfileGoalState | None = None,
) -> OngoingEntryProjection:
    """条目投影；关联领域的目标可传入领域实时状态作为有效状态。

    只有目标的展示状态可以取领域实时值（画像工单 14）；时效仍按同一
    规则折算，不新增事实、不修改条目本身。
    """

    effective = item
    if goal_state is not None and item.goal_state is not goal_state:
        effective = item.model_copy(update={"goal_state": goal_state})
    return OngoingEntryProjection(
        role=ongoing_entry_role(item),
        profile_item_id=effective.profile_item_id,
        version=effective.version,
        text=effective.text,
        goal_state=effective.goal_state,
        validity_status=validity_status_for(effective, now),
        updated_at=effective.updated_at,
        source_message_ids=list(effective.source_message_ids),
    )


def _item_projection(
    group: _OngoingGroup,
    *,
    now: datetime,
    reference: OngoingDomainReferenceProjection | None,
    effective_goal_state: AtomicProfileGoalState,
) -> OngoingItemProjection:
    """一个事项的页面投影；领域引用只展示实时解析结果，不复制领域状态。"""

    progress: list[OngoingEntryProjection] = []
    pending: list[OngoingEntryProjection] = []
    for entry in sorted(
        group.entries,
        key=lambda item: (-item.updated_at.timestamp(), item.profile_item_id),
    ):
        projection = _entry_projection(entry, now=now)
        if projection.role is OngoingItemRole.PENDING_QUESTION:
            pending.append(projection)
        else:
            progress.append(projection)
    return OngoingItemProjection(
        goal=_entry_projection(
            group.goal, now=now, goal_state=effective_goal_state
        ),
        progress=progress,
        pending_questions=pending,
        domain_references=[reference] if reference is not None else [],
    )


def _is_fresh(stored: StoredOngoingContext, fingerprint: str) -> bool:
    return (
        stored.is_current
        and stored.context_version == ONGOING_CONTEXT_CONTRACT_VERSION
        and stored.dependency_fingerprint == fingerprint
    )


def _now() -> datetime:
    return datetime.now(UTC)
