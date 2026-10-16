"""进行中事项投影与近期工作上下文（画像工单 13）。

两件事，一个来源：

- **事项投影**：从原子记忆的明确事实派生——目标是用户明确表达且仍在进行
  （或已暂停）的目标条目，进展与待续问题是与该目标有确定性关联的原子条目
  本身（见 :mod:`bridges.contracts.ongoing_profile`）。投影只读事实，不新增
  句子、不把助手建议解释为用户承诺；完成的目标从事项区消失，但其进展条目
  仍保留在记忆里。
- **近期工作上下文**：投影的带依赖指纹派生视图，持久化在
  :mod:`bridges.profiles.ongoing_repository`。依赖 = 事项相关条目的（标识、
  版本）、来源消息的可用性与内容摘要、账户使用控制（开关与版本）与合同
  版本；任一变化都让指纹不匹配，读取方先重建再使用。重建以捕获的世代
  （epoch）做 CAS 提交，失效发生在重建期间时提交失败——迟到重建不能覆盖
  新状态。

编译接缝：本服务实现 :class:`bridges.profiles.atomic.OngoingContinuationProvider`
与 :class:`bridges.profiles.atomic.OngoingContextInvalidator`。用户明确提及
唯一进行中目标（或语义命中其条目）时，同一事项的进展与待续问题按
``ongoing`` 依据进入候选；命中不绕过任何门禁（用途、时效、本轮刚整理、
证据核验与权限全部照旧）。歧义（并行事项同时命中）时不接续、不按主题
批量归属。

领域引用只在目标的 :class:`AtomicProfileDomainReference` 里存「哪个领域、
哪个对象」；当前状态由组合根注册的领域提供方实时解析。没有接通提供方或
解析失败时如实显示未解析（``resolved=False``），绝不伪造领域状态——学习
专门对接由工单 14 在组合根接线。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.contracts.atomic_profile import (
    AtomicProfileDomainReference,
    AtomicProfileGoalState,
    AtomicProfileItem,
)
from bridges.contracts.ongoing_profile import (
    ONGOING_CONTEXT_CONTRACT_VERSION,
    OngoingContextSnapshot,
    OngoingContextState,
    OngoingContinuationGroup,
    OngoingDomainReferenceProjection,
    OngoingDomainReferenceState,
    OngoingEntryProjection,
    OngoingGoalAction,
    OngoingItemProjection,
    OngoingItemRole,
    OngoingItemsProjection,
)
from bridges.contracts.profile_extraction import ProfileAccountControlsProjection
from bridges.profiles.atomic import (
    AtomicProfileService,
    ProfileEvidenceSourceReader,
    is_ongoing_goal_item,
    ongoing_entry_role,
    ongoing_goal_mentioned,
    validity_status_for,
)
from bridges.profiles.ongoing_repository import (
    OngoingContextStore,
    StoredOngoingContext,
)

logger = logging.getLogger(__name__)

#: 使用开关关闭时的页面提示（稳定文案，前端直接展示）。
ONGOING_USAGE_DISABLED_NOTICE = (
    "长期记忆使用已关闭：回答不会使用这些事项，管理操作仍然可用。"
)

#: 重建在竞争/失效下重试的次数；仍不稳定则如实返回「重建中」，绝不回退旧内容。
_ONGOING_REBUILD_ATTEMPTS = 2


class OngoingDomainReferenceProvider(Protocol):
    """领域引用解析端口：按领域注册，读取时实时解析当前状态。"""

    def resolve(
        self, account_id: str, reference: AtomicProfileDomainReference
    ) -> OngoingDomainReferenceState | None:
        """返回领域状态；对象不存在或不可读时返回 ``None``（未解析）。"""


@dataclass(frozen=True)
class _OngoingGroup:
    """一个事项的原子视图：目标条目 + 归组到它的条目。"""

    goal: AtomicProfileItem
    entries: tuple[AtomicProfileItem, ...]


class OngoingItemsService:
    """进行中事项投影与近期工作上下文的服务实现。

    同时实现 :class:`OngoingContextInvalidator`（原子服务写入后让上下文
    失效）与 :class:`OngoingContinuationProvider`（编译时的接续候选）。
    """

    def __init__(
        self,
        *,
        atomic: AtomicProfileService,
        context_store: OngoingContextStore,
        controls_reader: Callable[[str], ProfileAccountControlsProjection],
        message_reader: ProfileEvidenceSourceReader | None = None,
    ) -> None:
        self._atomic = atomic
        self._store = context_store
        # 控制状态复用自动画像服务的同一份持久状态（改进工单 07）。
        self._controls_reader = controls_reader
        # 来源可用性/内容摘要依赖消息读取；未注入时按「不可读」处理，
        # 绝不伪造来源仍存在。
        self._message_reader = message_reader
        self._domain_providers: dict[str, OngoingDomainReferenceProvider] = {}

    # ------------------------------------------------------------------
    # 组合根装配
    # ------------------------------------------------------------------

    def register_domain_provider(
        self, domain: str, provider: OngoingDomainReferenceProvider
    ) -> None:
        """注册/替换一个领域的引用解析提供方（学习对接由工单 14 接线）。"""

        self._domain_providers[domain] = provider

    # ------------------------------------------------------------------
    # 事项投影（页面与管理操作）
    # ------------------------------------------------------------------

    def list_items(
        self, account_id: str, *, now: datetime | None = None
    ) -> OngoingItemsProjection:
        """实时生成事项页投影：加载/空态由前端按 ``items`` 渲染。

        使用开关关闭时仍然列出事项供管理（含说明提示），但不代表回答会
        使用它们；完成的目标不再出现。
        """

        moment = now or _now()
        controls = self._controls_reader(account_id)
        groups = _group_items(self._atomic.list_items(account_id))
        items = [
            self._item_projection(account_id, group, now=moment)
            for group in groups
        ]
        return OngoingItemsProjection(
            usage_enabled=controls.usage_enabled,
            notice=(
                None if controls.usage_enabled else ONGOING_USAGE_DISABLED_NOTICE
            ),
            items=items,
        )

    def set_state(
        self,
        account_id: str,
        item_id: str,
        action: OngoingGoalAction,
        *,
        version: int,
    ) -> AtomicProfileItem:
        """暂停/完成/继续进行中目标：精确条目 + 乐观锁（委托原子服务）。"""

        return self._atomic.set_ongoing_state(
            account_id, item_id, action, version=version
        )

    def set_domain_reference(
        self,
        account_id: str,
        item_id: str,
        reference: AtomicProfileDomainReference | None,
        *,
        version: int,
    ) -> AtomicProfileItem:
        """设置/清除目标的领域引用（委托原子服务，状态仍由领域实时解析）。"""

        return self._atomic.set_domain_reference(
            account_id, item_id, reference, version=version
        )

    def _item_projection(
        self, account_id: str, group: _OngoingGroup, *, now: datetime | None
    ) -> OngoingItemProjection:
        moment = now or _now()
        progress: list[OngoingEntryProjection] = []
        pending: list[OngoingEntryProjection] = []
        for entry in sorted(
            group.entries,
            key=lambda item: (-item.updated_at.timestamp(), item.profile_item_id),
        ):
            projection = _entry_projection(entry, now=moment)
            if projection.role is OngoingItemRole.PENDING_QUESTION:
                pending.append(projection)
            else:
                progress.append(projection)
        return OngoingItemProjection(
            goal=_entry_projection(group.goal, now=moment),
            progress=progress,
            pending_questions=pending,
            domain_references=(
                [self._resolve_domain_reference(account_id, group.goal.domain_reference)]
                if group.goal.domain_reference is not None
                else []
            ),
        )

    def _resolve_domain_reference(
        self, account_id: str, reference: AtomicProfileDomainReference
    ) -> OngoingDomainReferenceProjection:
        provider = self._domain_providers.get(reference.domain)
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
        )

    # ------------------------------------------------------------------
    # 近期工作上下文（失效、守卫与读取）
    # ------------------------------------------------------------------

    def invalidate(self, account_id: str, *, reason: str) -> None:
        """立即失效：世代加一；读取方不再把它当作当前内容（画像工单 13）。"""

        self._store.invalidate(account_id, reason=reason, now=_now())

    def read_context(
        self, account_id: str, *, now: datetime | None = None
    ) -> OngoingContextSnapshot:
        """读取观察用的上下文快照：当前 / 空 / 重建中（绝不回退旧内容）。"""

        moment = now or _now()
        stored, _snapshot = self._ensure_current(account_id, now=moment)
        if stored is None:
            existing = self._store.read(account_id)
            if existing is None:
                return OngoingContextSnapshot(
                    account_id=account_id, state=OngoingContextState.EMPTY
                )
            return OngoingContextSnapshot(
                account_id=account_id,
                state=OngoingContextState.REBUILDING,
                version=existing.epoch,
                built_at=existing.built_at,
                dependency_fingerprint=existing.dependency_fingerprint,
                items=[],
            )
        return OngoingContextSnapshot(
            account_id=account_id,
            state=OngoingContextState.CURRENT,
            version=stored.epoch,
            built_at=stored.built_at,
            dependency_fingerprint=stored.dependency_fingerprint,
            items=[
                OngoingItemProjection.model_validate(payload)
                for payload in json.loads(stored.payload_json)
            ],
        )

    def begin_context_rebuild(
        self, account_id: str
    ) -> tuple[StoredOngoingContext | None, list[AtomicProfileItem]]:
        """捕获当前世代供重建提交做 CAS（观测/验证接缝）。

        返回 ``(当前存储行, 条目快照)``；``None`` 存储行表示从未建立
        （捕获世代为 0）。重建方可在任意时刻调用
        :meth:`commit_context_rebuild` 提交冻结载荷——期间发生的任何失效
        都会让提交失败。
        """

        stored = self._store.read(account_id)
        snapshot = self._atomic.list_items(account_id)
        return stored, snapshot

    def commit_context_rebuild(
        self,
        account_id: str,
        stored: StoredOngoingContext | None,
        snapshot: Sequence[AtomicProfileItem],
        *,
        now: datetime | None = None,
    ) -> bool:
        """以捕获的世代提交重建；返回是否赢得提交（迟到重建为 ``False``）。"""

        moment = now or _now()
        controls = self._controls_reader(account_id)
        groups = _group_items(snapshot)
        fingerprint = self._fingerprint(account_id, groups, controls)
        payload = [
            projection.model_dump(mode="json")
            for projection in (
                self._item_projection(account_id, group, now=moment)
                for group in groups
            )
        ]
        return self._store.commit_rebuild(
            account_id,
            captured_epoch=0 if stored is None else stored.epoch,
            context_version=ONGOING_CONTEXT_CONTRACT_VERSION,
            dependency_fingerprint=fingerprint,
            payload_json=json.dumps(payload, ensure_ascii=False),
            now=moment,
        )

    def continuation_group(
        self,
        account_id: str,
        *,
        query: str,
        semantic_item_ids: Iterable[str] = (),
    ) -> OngoingContinuationGroup | None:
        """「继续某事项」的编译提供方：上下文不新鲜时不给接续依据。"""

        controls = self._controls_reader(account_id)
        if not controls.usage_enabled:
            # 使用关闭：回答一律不采用画像，接续通道同样关闭。
            return None
        stored, snapshot = self._ensure_current(account_id, now=_now())
        if stored is None:
            # 上下文不可新鲜（失效或重建竞争）：本轮不启用接续依据，
            # 其余召回通道照旧；绝不拿旧上下文冒充当前。
            return None
        semantic = set(semantic_item_ids)
        matched: list[_OngoingGroup] = []
        for group in _group_items(snapshot):
            if group.goal.goal_state is not AtomicProfileGoalState.ACTIVE:
                continue
            mentioned = bool(query) and ongoing_goal_mentioned(group.goal, query)
            entry_hit = any(
                entry.profile_item_id in semantic for entry in group.entries
            )
            goal_hit = group.goal.profile_item_id in semantic
            if mentioned or entry_hit or goal_hit:
                matched.append(group)
        if len(matched) != 1:
            # 没有命中或并行事项同时候选：不猜、不按主题批量归属。
            return None
        group = matched[0]
        return OngoingContinuationGroup(
            goal_item_id=group.goal.profile_item_id,
            entry_item_ids=[entry.profile_item_id for entry in group.entries],
        )

    # ------------------------------------------------------------------
    # 上下文内部
    # ------------------------------------------------------------------

    def _ensure_current(
        self, account_id: str, *, now: datetime
    ) -> tuple[StoredOngoingContext | None, list[AtomicProfileItem]]:
        """返回可信的上下文行与条目快照；无法保证新鲜时为 ``(None, 快照)``。"""

        controls = self._controls_reader(account_id)
        if not controls.usage_enabled:
            # 使用关闭时上下文一律视为不可用：不重建、不服务，也不清行
            # （重新开启使用后按当时的依赖重建）。
            return None, self._atomic.list_items(account_id)
        snapshot = self._atomic.list_items(account_id)
        groups = _group_items(snapshot)
        fingerprint = self._fingerprint(account_id, groups, controls)
        stored = self._store.read(account_id)
        if stored is None and not groups:
            # 从未建立且没有事项：如实为空，不落一行空上下文。
            return None, snapshot
        if stored is not None and _is_fresh(stored, fingerprint):
            return stored, snapshot
        for _ in range(_ONGOING_REBUILD_ATTEMPTS):
            if self.commit_context_rebuild(account_id, stored, snapshot, now=now):
                stored = self._store.read(account_id)
                if stored is not None and _is_fresh(stored, fingerprint):
                    return stored, snapshot
            # 提交丢失（重建期间发生失效/竞争）：重读依赖与世代再试一次。
            controls = self._controls_reader(account_id)
            if not controls.usage_enabled:
                return None, snapshot
            snapshot = self._atomic.list_items(account_id)
            groups = _group_items(snapshot)
            fingerprint = self._fingerprint(account_id, groups, controls)
            stored = self._store.read(account_id)
            if stored is not None and _is_fresh(stored, fingerprint):
                return stored, snapshot
        return None, snapshot

    def _fingerprint(
        self,
        account_id: str,
        groups: Sequence[_OngoingGroup],
        controls: ProfileAccountControlsProjection,
    ) -> str:
        """依赖指纹：条目版本、来源消息可用性与内容、控制状态与合同版本。"""

        lines = [
            ONGOING_CONTEXT_CONTRACT_VERSION,
            f"usage|{int(controls.usage_enabled)}|{controls.usage_control_version}",
        ]
        for group in groups:
            goal = group.goal
            lines.append(
                f"g|{goal.profile_item_id}|{goal.version}|{goal.goal_state.value}"
            )
            for entry in sorted(
                group.entries, key=lambda item: item.profile_item_id
            ):
                lines.append(f"e|{entry.profile_item_id}|{entry.version}")
        message_ids = sorted(
            {
                message_id
                for group in groups
                for item in (group.goal, *group.entries)
                for message_id in item.source_message_ids
            }
        )
        for message_id in message_ids:
            message = None
            if self._message_reader is not None:
                try:
                    message = self._message_reader(account_id, message_id)
                except Exception:  # noqa: BLE001 - 读取失败按不可用，不伪造来源
                    message = None
            if message is None:
                lines.append(f"m|{message_id}|absent")
            else:
                digest = hashlib.sha256(
                    message.content.encode("utf-8")
                ).hexdigest()[:16]
                lines.append(f"m|{message_id}|{message.status}|{digest}")
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:32]


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


__all__ = [
    "ONGOING_USAGE_DISABLED_NOTICE",
    "OngoingDomainReferenceProvider",
    "OngoingItemsService",
]
