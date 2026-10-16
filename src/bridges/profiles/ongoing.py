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
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

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
    OngoingGoalAction,
    OngoingItemProjection,
    OngoingItemsProjection,
)
from bridges.contracts.profile_extraction import ProfileAccountControlsProjection
from bridges.profiles.atomic import (
    AtomicProfileError,
    AtomicProfileService,
    ProfileEvidenceSourceReader,
    ongoing_goal_mentioned,
)
from bridges.profiles.ongoing_domains import (
    OngoingDomainReferenceProvider,
    OngoingDomainRegistry,
    OngoingDomainStateProvider,
    effective_goal_state,
)
from bridges.profiles.ongoing_projection import (
    ONGOING_USAGE_DISABLED_NOTICE,
    _group_items,
    _is_fresh,
    _item_projection,
    _now,
    _OngoingGroup,
)
from bridges.profiles.ongoing_repository import (
    OngoingContextStore,
    StoredOngoingContext,
)

#: 重建在竞争/失效下重试的次数；仍不稳定则如实返回「重建中」，绝不回退旧内容。
_ONGOING_REBUILD_ATTEMPTS = 2


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
        # 画像工单 13/14：领域引用解析与状态操作注册表；未接通领域时如实
        # 显示未解析，关联条目的状态操作如实拒绝，不写画像条目冒充领域状态。
        self._domains = OngoingDomainRegistry()

    # ------------------------------------------------------------------
    # 组合根装配
    # ------------------------------------------------------------------

    def register_domain_provider(
        self, domain: str, provider: OngoingDomainReferenceProvider
    ) -> None:
        """注册/替换一个领域的引用解析提供方（学习对接由工单 14 接线）。"""

        self._domains.register_provider(domain, provider)

    def register_domain_state_provider(
        self, domain: str, provider: OngoingDomainStateProvider
    ) -> None:
        """注册/替换一个领域的状态操作提供方（画像工单 14；学习目标）。"""

        self._domains.register_state_provider(domain, provider)

    # ------------------------------------------------------------------
    # 事项投影（页面与管理操作）
    # ------------------------------------------------------------------

    def list_items(
        self, account_id: str, *, now: datetime | None = None
    ) -> OngoingItemsProjection:
        """实时生成事项页投影：加载/空态由前端按 ``items`` 渲染。

        使用开关关闭时仍然列出事项供管理（含说明提示），但不代表回答会
        使用它们；完成的目标不再出现（关联领域的目标以领域实时状态为准，
        领域状态完成时不显示，画像条目本身不动）。
        """

        moment = now or _now()
        controls = self._controls_reader(account_id)
        groups = _group_items(self._atomic.list_items(account_id))
        items, _ = self._item_projections(account_id, groups, now=moment)
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
        reference_version: int | None = None,
    ) -> AtomicProfileItem:
        """暂停/完成/继续进行中目标：精确条目 + 乐观锁。

        条目未关联领域时委托原子服务（原有行为）。条目已关联领域（如学习
        目标）时，状态由所属领域拥有：本方法只校验页面读到的条目版本未被
        并发修改，再把操作连同页面读到的领域对象版本交给注册的领域状态
        提供方，由领域按其守卫执行；画像条目不写（不产生第二份状态）。
        领域版本缺失、冲突或不可用时如实报错，不覆盖领域新状态。
        """

        item = self._atomic.get_item(account_id, item_id)
        reference = item.domain_reference
        if reference is None:
            return self._atomic.set_ongoing_state(
                account_id, item_id, action, version=version
            )
        if version != item.version:
            raise AtomicProfileError("版本冲突，请刷新后重试。")
        provider = self._domains.state_provider(reference.domain)
        if provider is None:
            raise AtomicProfileError(
                "该事项由所属领域管理，当前未接通状态操作。"
            )
        if reference_version is None:
            raise AtomicProfileError("缺少所属领域对象版本，请刷新后重试。")
        provider.set_state(
            account_id, reference, action, expected_version=reference_version
        )
        return item

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

    def _item_projections(
        self, account_id: str, groups: Sequence[_OngoingGroup], *, now: datetime
    ) -> tuple[list[OngoingItemProjection], dict[str, OngoingDomainReferenceProjection]]:
        """按组生成投影并返回同一份领域引用解析（指纹与载荷共用）。

        关联领域的目标以领域实时状态参与「完成即隐藏」与展示：领域状态
        未解析时保留画像自身状态并如实标注未解析，绝不伪造领域结论。
        """

        resolutions = self._resolve_references(account_id, groups)
        items: list[OngoingItemProjection] = []
        for group in groups:
            reference = resolutions.get(group.goal.profile_item_id)
            effective = effective_goal_state(group.goal, reference)
            if effective is AtomicProfileGoalState.COMPLETED:
                continue
            items.append(
                _item_projection(
                    group,
                    now=now,
                    reference=reference,
                    effective_goal_state=effective,
                )
            )
        return items, resolutions

    def _resolve_references(
        self, account_id: str, groups: Sequence[_OngoingGroup]
    ) -> dict[str, OngoingDomainReferenceProjection]:
        """解析所有关联领域目标；键为画像条目标识（未关联的组不在其中）。"""

        resolutions: dict[str, OngoingDomainReferenceProjection] = {}
        for group in groups:
            reference = group.goal.domain_reference
            if reference is None:
                continue
            resolutions[group.goal.profile_item_id] = self._domains.resolve(
                account_id, reference
            )
        return resolutions

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
        items, resolutions = self._item_projections(account_id, groups, now=moment)
        fingerprint = self._fingerprint(
            account_id, groups, controls, resolutions
        )
        payload = [projection.model_dump(mode="json") for projection in items]
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
        """「继续某事项」的编译提供方：上下文不新鲜时不给接续依据。

        关联领域的目标以领域实时状态判定是否仍可接续：领域状态暂停/完成
        时不接续；引用无法解析时同样不接续（不能确认领域状态就不伪造）。
        """

        controls = self._controls_reader(account_id)
        if not controls.usage_enabled:
            # 使用关闭：回答一律不采用画像，接续通道同样关闭。
            return None
        stored, snapshot = self._ensure_current(account_id, now=_now())
        if stored is None:
            # 上下文不可新鲜（失效或重建竞争）：本轮不启用接续依据，
            # 其余召回通道照旧；绝不拿旧上下文冒充当前。
            return None
        groups = _group_items(snapshot)
        resolutions = self._resolve_references(account_id, groups)
        semantic = set(semantic_item_ids)
        matched: list[_OngoingGroup] = []
        for group in groups:
            reference = resolutions.get(group.goal.profile_item_id)
            if group.goal.domain_reference is not None and (
                reference is None or not reference.resolved
            ):
                continue
            if effective_goal_state(group.goal, reference) is not (
                AtomicProfileGoalState.ACTIVE
            ):
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
        resolutions = self._resolve_references(account_id, groups)
        fingerprint = self._fingerprint(account_id, groups, controls, resolutions)
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
            resolutions = self._resolve_references(account_id, groups)
            fingerprint = self._fingerprint(
                account_id, groups, controls, resolutions
            )
            stored = self._store.read(account_id)
            if stored is not None and _is_fresh(stored, fingerprint):
                return stored, snapshot
        return None, snapshot

    def _fingerprint(
        self,
        account_id: str,
        groups: Sequence[_OngoingGroup],
        controls: ProfileAccountControlsProjection,
        resolutions: dict[str, OngoingDomainReferenceProjection],
    ) -> str:
        """依赖指纹：条目版本、领域引用实时状态、来源消息、控制与合同版本。"""

        lines = [
            ONGOING_CONTEXT_CONTRACT_VERSION,
            f"usage|{int(controls.usage_enabled)}|{controls.usage_control_version}",
        ]
        for group in groups:
            goal = group.goal
            lines.append(
                f"g|{goal.profile_item_id}|{goal.version}|{goal.goal_state.value}"
            )
            reference = resolutions.get(goal.profile_item_id)
            if reference is not None:
                # 画像工单 14：领域的纠正/删除/暂停/切换/版本变化让上下文
                # 立即失效——领域状态只引用，不复制进画像。
                lines.append(
                    f"d|{goal.profile_item_id}|{reference.domain}"
                    f"|{reference.reference_id}|{int(reference.resolved)}"
                    f"|{reference.state_code or ''}|{reference.version or ''}"
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


__all__ = [
    "ONGOING_USAGE_DISABLED_NOTICE",
    "OngoingDomainReferenceProvider",
    "OngoingItemsService",
]
