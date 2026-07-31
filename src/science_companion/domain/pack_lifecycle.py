"""领域包失效、撤销、重验证与受信回滚服务。

本模块实现 T047：领域包或依赖失效后建立影响集、阻断新运行、重验证下游，
并只回滚到仍受信且兼容的版本。核心不变量：

1. 失效事件按 DETECTED → TRIAGED → CONTAINED → IMPACTED_OBJECTS_FOUND →
   REMEDIATING → REVALIDATING → CLOSED 推进；紧急撤销可以先进入
   CONTAINED，但不能跳过影响报告和重验证关闭条件。
2. 安全管理员可以紧急撤销，但不能编辑规则或直接发布替代版本；撤销事件
   不可修改并保留后续双人复核义务。
3. 影响集覆盖包、运行、Claim、Evidence、Wording、产物、项目和用户动作，
   由各下游服务通过注册的影响源贡献，不能扩大作用域。
4. 回滚只允许选择仍受信、签名有效、依赖兼容且通过当前平台下限的旧版；
   已撤销、签名无效或不兼容版本不能通过回滚复活。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from science_companion.contracts.domain import (
    AttestationConclusion,
    DomainPackManifest,
    DomainPackStatus,
    DomainPackUpgradeReport,
    DomainPackValidationRun,
    PackImpactAction,
    PackImpactCategory,
    PackImpactItem,
    PackImpactSet,
    PackInvalidationEvent,
    PackInvalidationStage,
    PackInvalidationTrigger,
    PackRollbackConfirmation,
    PackRollbackRecord,
    PackRollbackStatus,
    PackStageTransition,
    RevalidationAreaProgress,
    RevalidationReport,
    RevalidationReportStatus,
    ReviewRole,
    RevocationEvent,
    WorkbenchPackRecord,
)
from science_companion.domain.loader import DomainPackLoader, _parse_version
from science_companion.domain.protocol import (
    DomainPackRegistryError,
    LoadedDomainPack,
)
from science_companion.domain.runtime import DomainPackValidationRuntime
from science_companion.domain.workbench import DomainPackWorkbenchService

__all__ = [
    "DomainPackLifecycleError",
    "DomainPackLifecycleService",
]

_STAGE_ORDER = [
    PackInvalidationStage.DETECTED,
    PackInvalidationStage.TRIAGED,
    PackInvalidationStage.CONTAINED,
    PackInvalidationStage.IMPACTED_OBJECTS_FOUND,
    PackInvalidationStage.REMEDIATING,
    PackInvalidationStage.REVALIDATING,
    PackInvalidationStage.CLOSED,
]

# 紧急撤销允许跳过分诊直接控制。
_EMERGENCY_SKIP = {PackInvalidationStage.DETECTED, PackInvalidationStage.TRIAGED}

# 新运行可使用的治理状态；草案、复核、暂停、撤销与弃用版本不能进入生产。
# 弃用包不得成为新项目默认（研究 4.4），故 DEPRECATED 不在可用集内。
_USABLE_LIFECYCLE = {
    DomainPackStatus.SIGNED,
    DomainPackStatus.ACTIVE,
}

# 影响带中可推进重验证的类别（包、项目、用户动作只定位，不重验证）。
_REVALIDATION_CATEGORIES = {
    PackImpactCategory.RUN,
    PackImpactCategory.CLAIM,
    PackImpactCategory.EVIDENCE,
    PackImpactCategory.WORDING,
    PackImpactCategory.ARTIFACT,
}


class DomainPackLifecycleError(ValueError):
    """失效、撤销、重验证或回滚流程违反状态机、职责或信任门。"""

    def __init__(self, message: str, *, code: str = "pack_lifecycle_rejected") -> None:
        super().__init__(message)
        self.code = code


class DomainPackLifecycleService:
    """领域包失效事件、紧急撤销、影响解析、重验证与受信回滚。"""

    def __init__(
        self,
        workbench: DomainPackWorkbenchService,
        loader: DomainPackLoader | None = None,
        runtime: DomainPackValidationRuntime | None = None,
    ) -> None:
        self._workbench = workbench
        self._loader = loader or DomainPackLoader()
        self._runtime = runtime or DomainPackValidationRuntime(self._loader)
        self._security_admins: set[str] = set()
        self._impact_sources: dict[
            PackImpactCategory, Callable[[str, str], Sequence[PackImpactItem]]
        ] = {}
        self._events: dict[str, PackInvalidationEvent] = {}
        self._impact_sets: dict[str, PackImpactSet] = {}
        self._revocations: dict[str, RevocationEvent] = {}
        self._revalidation_reports: dict[str, RevalidationReport] = {}
        self._rollbacks: dict[str, PackRollbackRecord] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    # ------------------------------------------------------------------
    # 安全管理员
    # ------------------------------------------------------------------

    def register_security_admin(self, person_id: str) -> None:
        """登记安全管理员身份。

        安全管理员可以隔离、暂停、紧急撤销，但不能编辑科学规则、
        代替复核者或直接发布替代版本。
        """
        if not person_id:
            raise DomainPackLifecycleError(
                "安全管理员标识不能为空。",
                code="role_required",
            )
        self._security_admins.add(person_id)

    def is_security_admin(self, person_id: str) -> bool:
        return person_id in self._security_admins

    # ------------------------------------------------------------------
    # 影响源注册
    # ------------------------------------------------------------------

    def register_pack_impact_source(
        self,
        category: PackImpactCategory,
        source: Callable[[str, str], Sequence[PackImpactItem]],
    ) -> None:
        """注册一个影响类别的影响源。

        影响源按 (pack_id, version) 返回该类别受影响的下游对象；领域服务
        通过应用装配线注册，生命周期服务不读取任何下游私有状态。
        """
        if category in _REVALIDATION_CATEGORIES or category in {
            PackImpactCategory.PROJECT,
            PackImpactCategory.USER_ACTION,
        }:
            self._impact_sources[category] = source
            return
        raise DomainPackLifecycleError(
            f"影响类别 {category.value} 由核心解析，不需要注册影响源。",
            code="invalid_impact_source",
        )

    # ------------------------------------------------------------------
    # 失效事件
    # ------------------------------------------------------------------

    def _participant_ids(self, record: WorkbenchPackRecord) -> set[str]:
        return {
            record.maintainer_id,
            *(item for item in (record.reviewer_id, record.releaser_id) if item),
        }

    def _require_actor(self, person_id: str, record: WorkbenchPackRecord) -> None:
        """失效处置允许包参与者或安全管理员；普通用户不进入本工作台。"""
        if person_id in self._participant_ids(record) or person_id in self._security_admins:
            return
        raise DomainPackLifecycleError(
            "只有该包版本的维护者、独立复核者、平台发行者或安全管理员可以处置失效事件。",
            code="role_required",
        )

    def record_invalidation_event(
        self,
        person_id: str,
        pack_id: str,
        version: str,
        trigger: PackInvalidationTrigger,
        reason: str,
        *,
        emergency: bool = False,
    ) -> PackInvalidationEvent:
        """记录一次失效事件，进入 DETECTED；紧急失效直接进入 CONTAINED。"""
        record = self._workbench.get_record(pack_id, version)
        self._require_actor(person_id, record)
        event = PackInvalidationEvent(
            event_id=f"pack-invalidation-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=record.canonical_digest,
            trigger=trigger,
            reason=reason,
            initiated_by=person_id,
            initiated_at=self._now(),
            emergency=emergency,
            stage_log=[
                PackStageTransition(
                    from_stage=PackInvalidationStage.DETECTED,
                    to_stage=(
                        PackInvalidationStage.CONTAINED
                        if emergency
                        else PackInvalidationStage.DETECTED
                    ),
                    transitioned_at=self._now(),
                    by=person_id,
                    note="紧急失效，立即控制" if emergency else "检测到失效，待分诊",
                )
            ],
        )
        if emergency:
            if not self.is_security_admin(person_id):
                raise DomainPackLifecycleError(
                    "只有安全管理员可以发起紧急失效。",
                    code="role_required",
                )
            event.stage = PackInvalidationStage.CONTAINED
        self._events[event.event_id] = event
        return event

    def get_invalidation_event(self, event_id: str) -> PackInvalidationEvent:
        event = self._events.get(event_id)
        if event is None:
            raise DomainPackLifecycleError(
                "失效事件不存在。",
                code="not_found",
            )
        return event

    def list_invalidation_events(
        self, person_id: str, pack_id: str | None = None
    ) -> list[PackInvalidationEvent]:
        """列出当前自然人可以处置的失效事件。"""
        visible: list[PackInvalidationEvent] = []
        for event in self._events.values():
            if pack_id is not None and event.pack_id != pack_id:
                continue
            try:
                record = self._workbench.get_record(event.pack_id, event.pack_version)
            except Exception:
                continue
            if person_id in self._participant_ids(record) or person_id in self._security_admins:
                visible.append(event)
        return sorted(visible, key=lambda item: item.initiated_at)

    def advance_stage(
        self,
        person_id: str,
        event_id: str,
        to_stage: PackInvalidationStage,
        *,
        note: str = "",
    ) -> PackInvalidationEvent:
        """按失效状态机推进；紧急跳过、影响报告和重验证为硬门。"""
        event = self.get_invalidation_event(event_id)
        record = self._workbench.get_record(event.pack_id, event.pack_version)
        self._require_actor(person_id, record)
        current = event.stage
        if to_stage == current:
            raise DomainPackLifecycleError(
                f"失效事件已处于 {to_stage.value} 阶段。",
                code="invalid_transition",
            )
        current_index = _STAGE_ORDER.index(current)
        target_index = _STAGE_ORDER.index(to_stage)
        emergency_skip = (
            current in _EMERGENCY_SKIP
            and to_stage == PackInvalidationStage.CONTAINED
            and self.is_security_admin(person_id)
        )
        if not (target_index == current_index + 1 or emergency_skip):
            raise DomainPackLifecycleError(
                f"不允许从 {current.value} 推进到 {to_stage.value}。",
                code="invalid_transition",
            )
        if to_stage == PackInvalidationStage.IMPACTED_OBJECTS_FOUND and not event.impact_set_id:
            raise DomainPackLifecycleError(
                "定位影响前必须先生成影响集；影响报告不能跳过。",
                code="impact_required",
            )
        if to_stage == PackInvalidationStage.REVALIDATING:
            self._ensure_revalidation_started(event)
        if to_stage == PackInvalidationStage.CLOSED:
            report = self._revalidation_reports.get(event.event_id)
            if report is None or report.status != RevalidationReportStatus.COMPLETED:
                raise DomainPackLifecycleError(
                    "重验证未完成前不能关闭失效事件。",
                    code="revalidation_pending",
                )
            if event.emergency and not self._emergency_follow_up_done(event):
                raise DomainPackLifecycleError(
                    "紧急撤销事件的后续双人复核未完成："
                    "需要至少一名包参与者（非安全管理员）参与过阶段推进或影响处置。",
                    code="follow_up_required",
                )
        transition = PackStageTransition(
            from_stage=current,
            to_stage=to_stage,
            transitioned_at=self._now(),
            by=person_id,
            note=note,
        )
        updated = event.model_copy(
            update={
                "stage": to_stage,
                "stage_log": list(event.stage_log) + [transition],
                "closed_at": self._now() if to_stage == PackInvalidationStage.CLOSED else None,
            }
        )
        self._events[event_id] = updated
        return updated

    def _emergency_follow_up_done(self, event: PackInvalidationEvent) -> bool:
        """紧急撤销事件的关闭要求包参与者（非安全管理员）参与过处置。

        决策 2.3：紧急撤销允许先进入 CONTAINED，但不能跳过后续双人复核。
        阶段推进记录中至少有一条由非安全管理员的参与者完成，才认为
        复核义务已履行；安全管理员不能单人全程闭环。
        """
        record = self._workbench.get_record(event.pack_id, event.pack_version)
        participants = self._participant_ids(record)
        return any(
            transition.by in participants and transition.by not in self._security_admins
            for transition in event.stage_log
        )

    def _ensure_revalidation_started(self, event: PackInvalidationEvent) -> None:
        """进入 REVALIDATING 前建立重验证报告（按影响集各类别初始化）。

        没有任何受影响对象（无运行、无 Claim、无产物）时报告立即完成，
        关闭门只对真正需要重验证的失效事件生效。
        """
        if event.event_id in self._revalidation_reports:
            return
        impact_set = self.get_impact_set(event.event_id)
        areas: list[RevalidationAreaProgress] = []
        for category in _REVALIDATION_CATEGORIES:
            items = [item for item in impact_set.items if item.category == category]
            if not items:
                continue
            areas.append(
                RevalidationAreaProgress(
                    area=category,
                    total=len(items),
                    revalidated=[],
                    failed=[],
                )
            )
        report = RevalidationReport(
            report_id=f"revalidation-{secrets.token_urlsafe(10)}",
            event_id=event.event_id,
            pack_id=event.pack_id,
            pack_version=event.pack_version,
            areas=areas,
            status=(
                RevalidationReportStatus.COMPLETED
                if not areas
                else RevalidationReportStatus.IN_PROGRESS
            ),
            started_at=self._now(),
            completed_at=self._now() if not areas else None,
        )
        self._revalidation_reports[event.event_id] = report

    # ------------------------------------------------------------------
    # 影响集
    # ------------------------------------------------------------------

    def _dependent_packs(self, pack_id: str, version: str) -> list[LoadedDomainPack]:
        """依赖失效包版本的其他已登记包版本。"""
        dependent: list[LoadedDomainPack] = []
        for loaded in self._workbench.list_loaded_packs():
            if loaded.pack_id == pack_id:
                continue
            if any(
                dependency.pack_id == pack_id and dependency.version == version
                for dependency in loaded.manifest.dependencies
            ):
                dependent.append(loaded)
        return dependent

    def resolve_impact(
        self,
        person_id: str,
        event_id: str,
    ) -> PackImpactSet:
        """构建影响集：包（含依赖包）→ 运行 → Claim/Evidence/Wording → 产物 → 项目 → 用户动作。"""
        event = self.get_invalidation_event(event_id)
        record = self._workbench.get_record(event.pack_id, event.pack_version)
        self._require_actor(person_id, record)
        items: list[PackImpactItem] = [
            PackImpactItem(
                item_id=f"pack:{event.pack_id}@{event.pack_version}",
                category=PackImpactCategory.PACK,
                ref_id=event.pack_id,
                label=f"领域包 {event.pack_id}@{event.pack_version}",
                action=PackImpactAction.BLOCK_NEW_USE,
                details={"canonical_digest": event.canonical_digest},
            )
        ]
        for dependent in self._dependent_packs(event.pack_id, event.pack_version):
            items.append(
                PackImpactItem(
                    item_id=f"pack:{dependent.pack_id}@{dependent.pack_version}",
                    category=PackImpactCategory.PACK,
                    ref_id=dependent.pack_id,
                    label=f"依赖包 {dependent.pack_id}@{dependent.pack_version}",
                    action=PackImpactAction.REVALIDATE,
                    details={"dependency": f"{event.pack_id}@{event.pack_version}"},
                )
            )
        for category in (
            PackImpactCategory.RUN,
            PackImpactCategory.CLAIM,
            PackImpactCategory.EVIDENCE,
            PackImpactCategory.WORDING,
            PackImpactCategory.ARTIFACT,
            PackImpactCategory.PROJECT,
            PackImpactCategory.USER_ACTION,
        ):
            source = self._impact_sources.get(category)
            if source is None:
                continue
            items.extend(source(event.pack_id, event.pack_version))
        impact_set = PackImpactSet(
            impact_set_id=f"pack-impact-{secrets.token_urlsafe(10)}",
            event_id=event_id,
            pack_id=event.pack_id,
            pack_version=event.pack_version,
            items=items,
            created_at=self._now(),
        )
        self._impact_sets[impact_set.impact_set_id] = impact_set
        self._events[event_id] = event.model_copy(
            update={"impact_set_id": impact_set.impact_set_id}
        )
        return impact_set

    def get_impact_set(self, event_id: str) -> PackImpactSet:
        event = self.get_invalidation_event(event_id)
        if event.impact_set_id is None:
            raise DomainPackLifecycleError(
                "该失效事件尚未生成影响集。",
                code="impact_required",
            )
        impact_set = self._impact_sets.get(event.impact_set_id)
        if impact_set is None:
            raise DomainPackLifecycleError(
                "影响集不存在，状态不一致。",
                code="impact_required",
            )
        return impact_set

    # ------------------------------------------------------------------
    # 紧急撤销与运行闭锁
    # ------------------------------------------------------------------

    def emergency_revoke(
        self,
        admin_id: str,
        pack_id: str,
        version: str,
        trigger: PackInvalidationTrigger,
        reason: str,
        *,
        second_factor: str = "",
    ) -> tuple[PackInvalidationEvent, RevocationEvent]:
        """安全管理员紧急撤销：二次认证、阻止新运行、保留后续复核义务。

        安全管理员不能在同一操作中上传修复包或激活旧版；撤销事件不可修改。
        """
        if not self.is_security_admin(admin_id):
            raise DomainPackLifecycleError(
                "只有安全管理员可以紧急撤销领域包。",
                code="role_required",
            )
        if not second_factor.strip():
            raise DomainPackLifecycleError(
                "紧急撤销要求二次认证。",
                code="second_factor_required",
            )
        if any(
            item.pack_id == pack_id and item.pack_version == version
            for item in self._revocations.values()
        ):
            raise DomainPackLifecycleError(
                f"{pack_id}@{version} 已经撤销，不能重复撤销。",
                code="already_revoked",
            )
        record = self._workbench.get_record(pack_id, version)
        event = self.record_invalidation_event(
            admin_id,
            pack_id,
            version,
            trigger,
            reason,
            emergency=True,
        )
        revocation = RevocationEvent(
            revocation_id=f"revocation-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            pack_version=version,
            canonical_digest=record.canonical_digest,
            revoked_by=admin_id,
            reason=reason,
            trigger=trigger,
            occurred_at=self._now(),
            blocks_new_runs=True,
            follow_up_required=True,
        )
        self._revocations[revocation.revocation_id] = revocation
        self._workbench.set_lifecycle_status(
            pack_id, version, DomainPackStatus.REVOKED
        )
        return event, revocation

    def _is_visible(self, person_id: str, pack_id: str, version: str) -> bool:
        """普通用户不进入本工作台：参与者或安全管理员可见。"""
        if person_id in self._security_admins:
            return True
        try:
            record = self._workbench.get_record(pack_id, version)
        except Exception:
            return False
        return person_id in self._participant_ids(record)

    def list_revocations(self, person_id: str) -> list[RevocationEvent]:
        """列出当前自然人可以处置的撤销事件。"""
        return sorted(
            (
                item
                for item in self._revocations.values()
                if self._is_visible(person_id, item.pack_id, item.pack_version)
            ),
            key=lambda item: item.occurred_at,
        )

    def _has_active_contained_event(self, pack_id: str, version: str) -> bool:
        """该版本是否有已进入控制阶段（CONTAINED 及之后）的活跃失效事件。

        控制意味着失效版本不能开始新使用；事件关闭后恢复可用。
        """
        contained_index = _STAGE_ORDER.index(PackInvalidationStage.CONTAINED)
        return any(
            event.pack_id == pack_id
            and event.pack_version == version
            and event.stage != PackInvalidationStage.CLOSED
            and _STAGE_ORDER.index(event.stage) >= contained_index
            for event in self._events.values()
        )

    def is_pack_usable(self, pack_id: str, version: str | None = None) -> bool:
        """新运行是否可以引用该包版本。

        未知、草案、复核中、已撤销、暂停、过期、弃用或已进入控制阶段的
        失效版本一律失败闭锁；CLOSED 的失效事件不阻断，REVOKED 永远阻断。
        """
        if version is None:
            version = self._workbench.get_default_active_version(pack_id)
            if version is None:
                return False
        try:
            record = self._workbench.get_record(pack_id, version)
        except Exception:
            return False
        if record.lifecycle_status not in _USABLE_LIFECYCLE:
            return False
        if any(
            item.pack_id == pack_id and item.pack_version == version
            for item in self._revocations.values()
        ):
            return False
        return not self._has_active_contained_event(pack_id, version)

    def require_packs_usable(self, pack_refs: Sequence[str]) -> None:
        """新运行前检查全部领域包引用；任一失效即拒绝启动。"""
        unusable: list[str] = []
        for pack_ref in pack_refs:
            parts = pack_ref.partition("@")
            pack_id = parts[0]
            version: str | None = parts[2] if parts[1] else None
            if not self.is_pack_usable(pack_id, version):
                unusable.append(pack_ref)
        if unusable:
            raise DomainPackLifecycleError(
                f"领域包已撤销或失效，不能开始新运行：{', '.join(unusable)}。",
                code="pack_revoked",
            )

    # ------------------------------------------------------------------
    # 重验证
    # ------------------------------------------------------------------

    def report_revalidated(
        self,
        person_id: str,
        event_id: str,
        area: PackImpactCategory,
        ref_ids: Sequence[str],
        *,
        failed: Sequence[str] = (),
    ) -> RevalidationReport:
        """登记一个影响类别中已完成重验证（或确认失败）的对象。"""
        event = self.get_invalidation_event(event_id)
        record = self._workbench.get_record(event.pack_id, event.pack_version)
        self._require_actor(person_id, record)
        if area not in _REVALIDATION_CATEGORIES:
            raise DomainPackLifecycleError(
                f"类别 {area.value} 不需要重验证。",
                code="invalid_area",
            )
        self._ensure_revalidation_started(event)
        impact_set = self.get_impact_set(event.event_id)
        allowed = {
            item.ref_id for item in impact_set.items if item.category == area
        }
        for ref_id in [*ref_ids, *failed]:
            if ref_id not in allowed:
                raise DomainPackLifecycleError(
                    f"对象 {ref_id} 不在该失效事件的 {area.value} 影响带中。",
                    code="ref_not_in_impact",
                )
        report = self._revalidation_reports[event.event_id]
        updated_areas: list[RevalidationAreaProgress] = []
        for progress in report.areas:
            if progress.area != area:
                updated_areas.append(progress)
                continue
            revalidated = list(dict.fromkeys([*progress.revalidated, *ref_ids]))
            # 修复后重做：重新登记为重验证通过的对象解除失败状态，
            # 避免单条误报永久锁死事件关闭门。
            remaining_failed = [
                item for item in progress.failed if item not in revalidated
            ]
            failed_list = list(dict.fromkeys([*remaining_failed, *failed]))
            updated_areas.append(
                progress.model_copy(
                    update={
                        "revalidated": revalidated,
                        "failed": failed_list,
                    }
                )
            )
        status = RevalidationReportStatus.IN_PROGRESS
        completed_at = None
        if any(progress.failed for progress in updated_areas):
            status = RevalidationReportStatus.FAILED
        elif all(progress.done for progress in updated_areas):
            status = RevalidationReportStatus.COMPLETED
            completed_at = self._now()
        self._revalidation_reports[event.event_id] = report.model_copy(
            update={
                "areas": updated_areas,
                "status": status,
                "completed_at": completed_at,
            }
        )
        return self._revalidation_reports[event.event_id]

    def get_revalidation_report(self, event_id: str) -> RevalidationReport | None:
        return self._revalidation_reports.get(event_id)

    # ------------------------------------------------------------------
    # 受信回滚
    # ------------------------------------------------------------------

    def _trusted_rollback_targets(
        self,
        pack_id: str,
        from_version: str,
    ) -> list[LoadedDomainPack]:
        """候选回滚目标：仍受信、签名有效、依赖兼容且在回滚白名单内。"""
        try:
            from_manifest = self._workbench.get_loaded(pack_id, from_version).manifest
        except DomainPackRegistryError:
            return []
        from_manifest = DomainPackManifest.model_validate(from_manifest.model_dump(mode="python"))
        trusted_whitelist = set(from_manifest.rollback_versions) | set(
            from_manifest.rollback_policy.trusted_versions
        )
        targets: list[LoadedDomainPack] = []
        for version in self._workbench.list_registered_versions(pack_id):
            if version == from_version:
                continue
            try:
                record = self._workbench.get_record(pack_id, version)
                loaded = self._workbench.get_loaded(pack_id, version)
            except Exception:
                continue
            if record.lifecycle_status not in _USABLE_LIFECYCLE:
                continue
            if not self.is_pack_usable(pack_id, version):
                continue
            preflight = self._loader.validate_manifest(loaded.manifest)
            if not preflight.accepted:
                continue
            if version not in trusted_whitelist:
                continue
            upgrade: DomainPackUpgradeReport = self._loader.validate_upgrade(
                loaded.manifest, from_manifest
            )
            if not upgrade.compatible:
                continue
            if any(
                not self.is_pack_usable(dependency.pack_id, dependency.version)
                for dependency in loaded.manifest.dependencies
            ):
                continue
            targets.append(loaded)
        return targets

    def propose_rollback(
        self,
        person_id: str,
        pack_id: str,
        from_version: str,
        reason: str,
    ) -> PackRollbackRecord:
        """提议回滚到仍受信、依赖兼容且通过平台下限的最高旧版。"""
        record = self._workbench.get_record(pack_id, from_version)
        self._require_actor(person_id, record)
        if not any(
            item.pack_id == pack_id and item.pack_version == from_version
            for item in self._revocations.values()
        ):
            raise DomainPackLifecycleError(
                "只有已撤销版本需要回滚；未撤销版本不能发起回滚。",
                code="revocation_required",
            )
        targets = self._trusted_rollback_targets(pack_id, from_version)
        if not targets:
            raise DomainPackLifecycleError(
                "没有可用的受信回滚目标：旧版已撤销、签名无效、不兼容或未在白名单中。",
                code="no_trusted_rollback_target",
            )
        target = max(targets, key=lambda loaded: _parse_version(loaded.pack_version) or (0, 0, 0))
        fixture_run: DomainPackValidationRun = self._runtime.run(target)
        # passed 已经包含预期状态比较；预期 blocked/needs_human 夹具不算失败。
        fixture_passed = not any(
            not result.passed for result in fixture_run.fixture_results
        )
        rollback = PackRollbackRecord(
            rollback_id=f"rollback-{secrets.token_urlsafe(10)}",
            pack_id=pack_id,
            from_version=from_version,
            to_version=target.pack_version,
            to_digest=target.manifest_digest,
            proposed_by=person_id,
            reason=reason,
            status=(
                PackRollbackStatus.PROPOSED
                if fixture_passed
                else PackRollbackStatus.REVIEW_REQUIRED
            ),
            fixture_report=fixture_run,
            fixture_passed=fixture_passed,
            created_at=self._now(),
        )
        self._rollbacks[rollback.rollback_id] = rollback
        return rollback

    def get_rollback(self, rollback_id: str) -> PackRollbackRecord:
        rollback = self._rollbacks.get(rollback_id)
        if rollback is None:
            raise DomainPackLifecycleError(
                "回滚记录不存在。",
                code="not_found",
            )
        return rollback

    def list_rollbacks(self, person_id: str) -> list[PackRollbackRecord]:
        """列出当前自然人可以处置的回滚记录。"""
        return sorted(
            (
                item
                for item in self._rollbacks.values()
                if self._is_visible(person_id, item.pack_id, item.from_version)
            ),
            key=lambda item: item.created_at,
        )

    def confirm_rollback(
        self,
        person_id: str,
        rollback_id: str,
        *,
        role: ReviewRole,
        conclusion: AttestationConclusion,
        opinion: str = "",
    ) -> PackRollbackRecord:
        """独立复核者或平台发行者确认回滚影响与目标版本。"""
        rollback = self.get_rollback(rollback_id)
        if rollback.status in {PackRollbackStatus.EXECUTED, PackRollbackStatus.REJECTED}:
            raise DomainPackLifecycleError(
                f"回滚已处于 {rollback.status.value}，不能再次确认。",
                code="rollback_closed",
            )
        record = self._workbench.get_record(rollback.pack_id, rollback.from_version)
        if role == ReviewRole.INDEPENDENT:
            expected_id = record.reviewer_id
            role_label = "独立复核者"
        elif role == ReviewRole.PLATFORM:
            expected_id = record.releaser_id
            role_label = "平台发行者"
        else:
            raise DomainPackLifecycleError(
                "回滚确认只允许独立复核者或平台发行者参与，内容签名角色不能确认回滚。",
                code="role_required",
            )
        if person_id != expected_id:
            raise DomainPackLifecycleError(
                f"只有被分配的{role_label}可以确认回滚。",
                code="role_required",
            )
        existing = next(
            (item for item in rollback.confirmations if item.role == role),
            None,
        )
        if existing is not None:
            raise DomainPackLifecycleError(
                f"{role.value} 角色已经确认过本次回滚。",
                code="already_confirmed",
            )
        confirmations = list(rollback.confirmations) + [
            PackRollbackConfirmation(
                role=role,
                person_id=person_id,
                conclusion=conclusion,
                opinion=opinion,
                confirmed_at=self._now(),
            )
        ]
        status: PackRollbackStatus = rollback.status
        if conclusion == AttestationConclusion.REJECT:
            status = PackRollbackStatus.REJECTED
        elif all(
            item.conclusion == AttestationConclusion.APPROVE
            for item in confirmations
        ) and {item.role for item in confirmations} >= {
            ReviewRole.INDEPENDENT,
            ReviewRole.PLATFORM,
        }:
            status = PackRollbackStatus.APPROVED
        updated = rollback.model_copy(
            update={
                "confirmations": confirmations,
                "status": status,
            }
        )
        self._rollbacks[rollback_id] = updated
        return updated

    def execute_rollback(self, person_id: str, rollback_id: str) -> PackRollbackRecord:
        """平台发行者执行回滚：把受信旧版重新设为项目可选版本。

        被撤销版本不会被复活；回滚只切换包的可选默认版本。
        """
        rollback = self.get_rollback(rollback_id)
        record = self._workbench.get_record(rollback.pack_id, rollback.from_version)
        if person_id != record.releaser_id:
            raise DomainPackLifecycleError(
                "只有平台发行者可以执行回滚。",
                code="role_required",
            )
        if rollback.status != PackRollbackStatus.APPROVED:
            raise DomainPackLifecycleError(
                "回滚必须经独立复核者与平台发行者双方确认后才能执行。",
                code="approval_required",
            )
        if not rollback.fixture_passed:
            raise DomainPackLifecycleError(
                "回滚目标必须通过夹具重放后才能执行。",
                code="fixture_required",
            )
        if not self.is_pack_usable(rollback.pack_id, rollback.to_version):
            raise DomainPackLifecycleError(
                "回滚目标已撤销或进入失效控制，执行时信任门不满足，不能执行回滚。",
                code="no_trusted_rollback_target",
            )
        target = self._workbench.get_loaded(rollback.pack_id, rollback.to_version)
        self._workbench.set_default_active(rollback.pack_id, rollback.to_version, target)
        updated = rollback.model_copy(
            update={
                "status": PackRollbackStatus.EXECUTED,
                "executed_at": self._now(),
            }
        )
        self._rollbacks[rollback_id] = updated
        return updated
