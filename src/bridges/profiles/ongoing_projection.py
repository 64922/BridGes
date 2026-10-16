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
    OngoingEntryProjection,
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
    item: AtomicProfileItem, *, now: datetime
) -> OngoingEntryProjection:
    return OngoingEntryProjection(
        role=ongoing_entry_role(item),
        profile_item_id=item.profile_item_id,
        version=item.version,
        text=item.text,
        goal_state=item.goal_state,
        validity_status=validity_status_for(item, now),
        updated_at=item.updated_at,
        source_message_ids=list(item.source_message_ids),
    )


def _is_fresh(stored: StoredOngoingContext, fingerprint: str) -> bool:
    return (
        stored.is_current
        and stored.context_version == ONGOING_CONTEXT_CONTRACT_VERSION
        and stored.dependency_fingerprint == fingerprint
    )


def _now() -> datetime:
    return datetime.now(UTC)
