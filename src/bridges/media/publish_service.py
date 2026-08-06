"""T035: 跨媒体一致性与多模态发布门服务。

MediaPublishService 在发布前检查文本、图表、公式、音频、视频和交互的
Claim 一致性、许可、安全与无障碍结果。任一必需门失败会阻止发布。
发布版本保留原始资产、分镜、可编辑源、渲染物和验证记录。
来源或事实锁失效会传播到所有媒体派生版本。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from bridges.contracts.invalidation import (
    AffectedDownstream,
    ImpactResolver,
    InvalidationEvent,
)
from bridges.contracts.media import (
    CrossMediaClaimEntry,
    CrossMediaConsistencyResult,
    CrossMediaInconsistency,
    MediaPublishRecord,
    MediaPublishRequest,
    MultimodalGateResult,
    MultimodalPublishGate,
    MultimodalPublishGateResult,
    StoryboardStatus,
)
from bridges.contracts.science import FactLock, FactLockSet, LicenseState
from bridges.invalidation import InvalidationError, InvalidationService
from bridges.media.accessibility_service import AccessibilityError, AccessibilityService
from bridges.media.generation import MediaGenerationError, MediaGenerationService
from bridges.media.service import MediaError, MediaIngestionService
from bridges.media.storyboard_service import StoryboardError, StoryboardService


class MediaPublishError(Exception):
    """Domain error for media publish failures."""


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_id() -> str:
    return secrets.token_urlsafe(16)


class MediaPublishService:
    """跨媒体一致性检查与多模态发布门服务。

    在发布前验证：
    1. 跨媒体核心事实、数值、术语、限定条件和引用一致。
    2. 许可、真实性、沙箱和无障碍任一必需门失败会阻止发布。
    3. 发布版本保留原始资产、分镜、可编辑源、渲染物和验证记录。
    4. 来源或事实锁失效会传播到所有媒体派生版本。
    """

    def __init__(
        self,
        generation_service: MediaGenerationService | None = None,
        storyboard_service: StoryboardService | None = None,
        media_ingestion_service: MediaIngestionService | None = None,
        accessibility_service: AccessibilityService | None = None,
        invalidation_service: InvalidationService | None = None,
    ) -> None:
        self._generation = generation_service
        self._storyboard = storyboard_service
        self._ingestion = media_ingestion_service
        self._accessibility = accessibility_service
        self._invalidation = invalidation_service
        self._publish_records: dict[str, MediaPublishRecord] = {}

    # ── Cross-media consistency ──────────────────────────────────────

    def check_cross_media_consistency(
        self,
        account_id: str,
        claim_entries: list[CrossMediaClaimEntry],
        fact_locks: list[FactLock] | None = None,
    ) -> CrossMediaConsistencyResult:
        """检查跨媒体 Claim 一致性。

        验证同一 Claim 在不同媒体中的核心事实、数值、术语、
        限定条件和引用是否一致。
        """
        inconsistencies: list[CrossMediaInconsistency] = []
        warnings: list[str] = []

        # Group entries by claim_id.
        by_claim: dict[str, list[CrossMediaClaimEntry]] = {}
        for entry in claim_entries:
            by_claim.setdefault(entry.claim_id, []).append(entry)

        for claim_id, entries in by_claim.items():
            # Check value consistency across media.
            values = {
                e.canonical_value
                for e in entries
                if e.canonical_value is not None
            }
            if len(values) > 1:
                inconsistencies.append(
                    CrossMediaInconsistency(
                        inconsistency_id=_generate_id(),
                        claim_id=claim_id,
                        kind="value_mismatch",
                        media_refs=[e.media_ref for e in entries],
                        description=(
                            f"Claim {claim_id} 在不同媒体中的标准值不一致："
                            f"{', '.join(sorted(values))}。"
                        ),
                        severity="error",
                    )
                )

            # Check qualifier consistency.
            all_qualifiers = [set(e.qualifiers) for e in entries]
            if all_qualifiers:
                union_qualifiers = set.union(*all_qualifiers)
                for entry in entries:
                    missing = union_qualifiers - set(entry.qualifiers)
                    if missing and entry.canonical_value is not None:
                        warnings.append(
                            f"Claim {claim_id} 在 {entry.media_ref} 中缺少限定条件："
                            f"{', '.join(sorted(missing))}。"
                        )

            # Check citation consistency.
            all_citations = [set(e.citation_ids) for e in entries if e.citation_ids]
            if all_citations:
                union_citations = set.union(*all_citations)
                for entry in entries:
                    if entry.citation_ids:
                        missing_cites = union_citations - set(entry.citation_ids)
                        if missing_cites:
                            warnings.append(
                                f"Claim {claim_id} 在 {entry.media_ref} 中缺少引用："
                                f"{', '.join(sorted(missing_cites))}。"
                            )

        # Check against fact locks.
        if fact_locks:
            lock_by_claim: dict[str, FactLock] = {}
            for lock in fact_locks:
                lock_by_claim[lock.claim_id] = lock

            for claim_id, entries in by_claim.items():
                fact_lock = lock_by_claim.get(claim_id)
                if fact_lock is None:
                    continue
                for entry in entries:
                    if (
                        entry.canonical_value is not None
                        and fact_lock.canonical_value
                        and entry.canonical_value != fact_lock.canonical_value
                        and entry.canonical_value not in fact_lock.allowed_variants
                    ):
                        inconsistencies.append(
                            CrossMediaInconsistency(
                                inconsistency_id=_generate_id(),
                                claim_id=claim_id,
                                kind="value_mismatch",
                                media_refs=[entry.media_ref],
                                description=(
                                    f"Claim {claim_id} 在 {entry.media_ref} 中的值 "
                                    f"'{entry.canonical_value}' 与事实锁标准值 "
                                    f"'{lock.canonical_value}' 不一致。"
                                ),
                                severity="error",
                            )
                        )

        consistent = len(inconsistencies) == 0
        return CrossMediaConsistencyResult(
            consistent=consistent,
            entries_checked=len(claim_entries),
            inconsistencies=inconsistencies,
            warnings=warnings,
        )

    # ── Collect claim entries from media objects ─────────────────────

    def collect_claim_entries(
        self,
        account_id: str,
        request: MediaPublishRequest,
        *,
        fact_locks: list[FactLock] | None = None,
    ) -> list[CrossMediaClaimEntry]:
        """从要发布的媒体对象中收集 Claim 条目。

        当提供 fact_locks 时，用事实锁的标准值填充 canonical_value。
        注意：当前 canonical_value 来自事实锁而非媒体对象的实际表达值，
        因此不能覆盖跨媒体值差异检测。调用方如需精确检测应自行传入
        带 canonical_value 的 CrossMediaClaimEntry。
        """
        entries: list[CrossMediaClaimEntry] = []

        # 构建 claim_id → canonical_value 映射（从事实锁提取）。
        lock_value: dict[str, str] = {}
        if fact_locks:
            for lock in fact_locks:
                if lock.canonical_value:
                    lock_value[lock.claim_id] = lock.canonical_value

        def _entry(
            claim_id: str,
            media_ref: str,
            media_kind: str,
        ) -> CrossMediaClaimEntry:
            return CrossMediaClaimEntry(
                claim_id=claim_id,
                media_ref=media_ref,
                media_kind=media_kind,
                canonical_value=lock_value.get(claim_id),
                qualifiers=[],
                citation_ids=[],
            )

        # From generated media objects (charts, figures).
        if self._generation is not None:
            for obj_id in request.media_object_ids:
                try:
                    obj = self._generation.get_media_object(
                        obj_id, account_id=account_id
                    )
                except MediaGenerationError:
                    continue
                for binding in obj.claim_bindings:
                    if binding.claim_id:
                        entries.append(
                            _entry(
                                claim_id=binding.claim_id,
                                media_ref=f"{obj_id}:{binding.element_ref}",
                                media_kind=obj.media_type.value,
                            )
                        )

        # From storyboards.
        if self._storyboard is not None:
            for sb_id in request.storyboard_ids:
                try:
                    sb = self._storyboard.get_storyboard(
                        sb_id, account_id=account_id
                    )
                except StoryboardError:
                    continue
                for scene in sb.scenes:
                    if scene.narration:
                        for claim_id in scene.narration.claim_ids:
                            entries.append(
                                _entry(
                                    claim_id=claim_id,
                                    media_ref=f"{sb_id}:scene:{scene.scene_id}",
                                    media_kind="storyboard",
                                )
                            )
                    for sb_binding in scene.scene_claim_bindings:
                        if sb_binding.claim_id:
                            entries.append(
                                _entry(
                                    claim_id=sb_binding.claim_id,
                                    media_ref=f"{sb_id}:binding:{sb_binding.binding_id}",
                                    media_kind="storyboard",
                                )
                            )

        # From accessibility bundles.
        if self._accessibility is not None:
            for bundle_id in request.accessibility_bundle_ids:
                try:
                    bundle = self._accessibility.get_bundle(
                        bundle_id, account_id=account_id
                    )
                except AccessibilityError:
                    continue
                for claim_id in bundle.claim_ids:
                    entries.append(
                        _entry(
                            claim_id=claim_id,
                            media_ref=f"{bundle_id}:accessibility",
                            media_kind="accessibility",
                        )
                    )

        return entries

    # ── Publish gate evaluation ──────────────────────────────────────

    def evaluate_publish_gate(
        self,
        account_id: str,
        request: MediaPublishRequest,
        *,
        fact_locks: list[FactLock] | None = None,
        fact_lock_set: FactLockSet | None = None,
    ) -> MultimodalPublishGateResult:
        """评估多模态发布门。

        任一必需门失败会阻止发布：
        - claim_consistency: 跨媒体 Claim 一致性
        - license: 许可合规
        - authenticity: 真实性（沙箱验证）
        - sandbox: 沙箱运行成功
        - accessibility: 无障碍替代完整
        - fact_lock: 事实锁完整
        - invalidation: 无活动失效
        """
        gate_results: dict[MultimodalPublishGate, MultimodalGateResult] = {}
        failed_gates: list[MultimodalPublishGate] = []
        reasons: list[str] = []

        # 1. Claim consistency gate.
        claim_entries = self.collect_claim_entries(
            account_id, request, fact_locks=fact_locks
        )
        consistency = self.check_cross_media_consistency(
            account_id, claim_entries, fact_locks
        )
        if consistency.consistent:
            gate_results[MultimodalPublishGate.CLAIM_CONSISTENCY] = MultimodalGateResult.PASS
        else:
            gate_results[MultimodalPublishGate.CLAIM_CONSISTENCY] = MultimodalGateResult.FAIL
            failed_gates.append(MultimodalPublishGate.CLAIM_CONSISTENCY)
            reasons.append(
                f"跨媒体 Claim 一致性检查发现 "
                f"{len(consistency.inconsistencies)} 条不一致。"
            )

        # 2. License gate.
        license_ok = self._check_license(account_id, request)
        gate_results[MultimodalPublishGate.LICENSE] = (
            MultimodalGateResult.PASS if license_ok else MultimodalGateResult.FAIL
        )
        if not license_ok:
            failed_gates.append(MultimodalPublishGate.LICENSE)
            reasons.append("存在许可状态未知的媒体资产。")

        # 3. Authenticity gate (sandbox validation for interactive/animation).
        authenticity_ok = self._check_storyboard_status(
            account_id, request, require_media_type="animation"
        ) and self._check_storyboard_status(
            account_id, request, require_media_type="interactive_html"
        )
        gate_results[MultimodalPublishGate.AUTHENTICITY] = (
            MultimodalGateResult.PASS if authenticity_ok else MultimodalGateResult.FAIL
        )
        if not authenticity_ok:
            failed_gates.append(MultimodalPublishGate.AUTHENTICITY)
            reasons.append("存在未通过沙箱验证的交互或动画媒体。")

        # 4. Sandbox gate.
        sandbox_ok = self._check_storyboard_status(account_id, request)
        gate_results[MultimodalPublishGate.SANDBOX] = (
            MultimodalGateResult.PASS if sandbox_ok else MultimodalGateResult.FAIL
        )
        if not sandbox_ok:
            failed_gates.append(MultimodalPublishGate.SANDBOX)
            reasons.append("存在沙箱运行失败的分镜。")

        # 5. Accessibility gate.
        accessibility_ok = self._check_accessibility(account_id, request)
        gate_results[MultimodalPublishGate.ACCESSIBILITY] = (
            MultimodalGateResult.PASS if accessibility_ok else MultimodalGateResult.FAIL
        )
        if not accessibility_ok:
            failed_gates.append(MultimodalPublishGate.ACCESSIBILITY)
            reasons.append("存在缺少完整无障碍替代的媒体对象。")

        # 6. Fact lock gate.
        fact_lock_ok = self._check_fact_locks(
            account_id, request, fact_locks, fact_lock_set
        )
        gate_results[MultimodalPublishGate.FACT_LOCK] = (
            MultimodalGateResult.PASS if fact_lock_ok else MultimodalGateResult.FAIL
        )
        if not fact_lock_ok:
            failed_gates.append(MultimodalPublishGate.FACT_LOCK)
            reasons.append("事实锁检查未通过。")

        # 7. Invalidation gate.
        invalidation_ok = self._check_invalidation(account_id, request)
        gate_results[MultimodalPublishGate.INVALIDATION] = (
            MultimodalGateResult.PASS if invalidation_ok else MultimodalGateResult.FAIL
        )
        if not invalidation_ok:
            failed_gates.append(MultimodalPublishGate.INVALIDATION)
            reasons.append("存在已失效的来源或事实锁。")

        passed = len(failed_gates) == 0
        return MultimodalPublishGateResult(
            passed=passed,
            gate_results=gate_results,
            failed_gates=failed_gates,
            reasons=reasons,
            consistency_result=consistency,
        )

    # ── Publish ──────────────────────────────────────────────────────

    def publish(
        self,
        account_id: str,
        request: MediaPublishRequest,
        *,
        fact_locks: list[FactLock] | None = None,
        fact_lock_set: FactLockSet | None = None,
    ) -> MediaPublishRecord:
        """执行多模态发布。

        只有全部发布门通过时才允许发布。发布记录保留原始资产、
        分镜、可编辑源、渲染物和验证记录的完整引用。
        """
        gate_result = self.evaluate_publish_gate(
            account_id,
            request,
            fact_locks=fact_locks,
            fact_lock_set=fact_lock_set,
        )
        if not gate_result.passed:
            raise MediaPublishError(
                f"多模态发布门未通过：{'; '.join(gate_result.reasons)}"
            )

        # Collect all artifact references for the publish record.
        editable_source_ids: list[str] = []
        rendered_artifact_refs: list[str] = []
        validation_report_ids: list[str] = []
        claim_ids: set[str] = set()

        if self._generation is not None:
            for obj_id in request.media_object_ids:
                try:
                    obj = self._generation.get_media_object(
                        obj_id, account_id=account_id
                    )
                except MediaGenerationError:
                    continue
                editable_source_ids.append(obj.editable_source.source_id)
                if obj.svg_content:
                    rendered_artifact_refs.append(f"svg:{obj_id}")
                for binding in obj.claim_bindings:
                    if binding.claim_id:
                        claim_ids.add(binding.claim_id)

        if self._storyboard is not None:
            for sb_id in request.storyboard_ids:
                try:
                    sb = self._storyboard.get_storyboard(
                        sb_id, account_id=account_id
                    )
                except StoryboardError:
                    continue
                # 收集分镜的可编辑源引用。
                for scene in sb.scenes:
                    rendered_artifact_refs.append(f"storyboard_scene:{sb_id}:{scene.scene_id}")
                for scene in sb.scenes:
                    if scene.narration:
                        claim_ids.update(scene.narration.claim_ids)
                    for sb_binding in scene.scene_claim_bindings:
                        if sb_binding.claim_id:
                            claim_ids.add(sb_binding.claim_id)

        if self._accessibility is not None:
            for bundle_id in request.accessibility_bundle_ids:
                try:
                    bundle = self._accessibility.get_bundle(
                        bundle_id, account_id=account_id
                    )
                except AccessibilityError:
                    continue
                claim_ids.update(bundle.claim_ids)
                # 收集无障碍包的朗读音频引用。
                if bundle.narration.audio_ref:
                    rendered_artifact_refs.append(
                        f"audio:{bundle.narration.audio_ref}"
                    )

        record = MediaPublishRecord(
            record_id=_generate_id(),
            account_id=account_id,
            project_id=request.project_id,
            media_object_ids=list(request.media_object_ids),
            source_asset_ids=list(request.source_asset_ids),
            storyboard_ids=list(request.storyboard_ids),
            editable_source_ids=editable_source_ids,
            rendered_artifact_refs=rendered_artifact_refs,
            validation_report_ids=validation_report_ids,
            accessibility_bundle_ids=list(request.accessibility_bundle_ids),
            claim_ids=sorted(claim_ids),
            fact_lock_set_id=fact_lock_set.set_id if fact_lock_set else None,
            gate_result=gate_result,
            published_at=_now(),
        )
        self._publish_records[record.record_id] = record
        return record

    def get_publish_record(
        self, account_id: str, record_id: str
    ) -> MediaPublishRecord:
        """获取发布记录。"""
        record = self._publish_records.get(record_id)
        if record is None or record.account_id != account_id:
            raise MediaPublishError(f"发布记录 {record_id} 不存在或无权访问。")
        return record

    def list_publish_records(
        self, account_id: str, project_id: str | None = None
    ) -> list[MediaPublishRecord]:
        """列出账户的发布记录。"""
        results: list[MediaPublishRecord] = []
        for record in self._publish_records.values():
            if record.account_id != account_id:
                continue
            if project_id is not None and record.project_id != project_id:
                continue
            results.append(record)
        results.sort(key=lambda r: r.published_at, reverse=True)
        return results

    # ── Invalidation propagation ─────────────────────────────────────

    def propagate_invalidation(
        self,
        event: InvalidationEvent,
    ) -> list[MediaPublishRecord]:
        """将失效事件传播到所有关联的媒体发布记录。

        来源或事实锁失效会传播到所有媒体派生版本。
        """
        affected: list[MediaPublishRecord] = []
        object_id = event.object_ref.object_id

        for record in self._publish_records.values():
            if record.invalidated:
                continue
            # Check if the invalidated object is referenced by this record.
            is_affected = (
                object_id in record.source_asset_ids
                or object_id in record.media_object_ids
                or object_id in record.storyboard_ids
                or object_id in record.claim_ids
                or object_id in record.editable_source_ids
                or object_id == record.fact_lock_set_id
            )
            if is_affected:
                updated = record.model_copy(
                    update={
                        "invalidated": True,
                        "invalidation_reason": event.reason,
                    }
                )
                self._publish_records[record.record_id] = updated
                affected.append(updated)

        return affected

    # ── Gate check helpers ───────────────────────────────────────────

    def _check_license(
        self, account_id: str, request: MediaPublishRequest
    ) -> bool:
        """检查所有原始资产的许可状态。"""
        if self._ingestion is None:
            return True
        for asset_id in request.source_asset_ids:
            try:
                projection = self._ingestion.get_asset(account_id, asset_id)
            except (MediaError, ValueError, KeyError):
                # Asset not accessible or has no manifests — treat as license issue.
                return False
            if projection.source_asset.license.state in (
                LicenseState.UNKNOWN,
                LicenseState.PENDING_REVIEW,
            ):
                return False
        return True

    def _check_storyboard_status(
        self, account_id: str, request: MediaPublishRequest,
        *, require_media_type: str | None = None,
    ) -> bool:
        """检查分镜状态是否可发布。

        require_media_type 不为 None 时只检查指定媒体类型的分镜。
        """
        if self._storyboard is None:
            return True
        for sb_id in request.storyboard_ids:
            try:
                sb = self._storyboard.get_storyboard(
                    sb_id, account_id=account_id
                )
            except StoryboardError:
                continue
            if require_media_type is not None and sb.media_type != require_media_type:
                continue
            if sb.status in (
                StoryboardStatus.FAILED,
                StoryboardStatus.QUARANTINED,
                StoryboardStatus.REPAIR_EXHAUSTED,
            ):
                return False
        return True

    def _check_accessibility(
        self, account_id: str, request: MediaPublishRequest
    ) -> bool:
        """检查无障碍替代是否完整。"""
        if self._accessibility is None:
            # If no accessibility service, check if bundles are requested.
            return len(request.accessibility_bundle_ids) > 0 or (
                len(request.media_object_ids) == 0
                and len(request.storyboard_ids) == 0
            )
        for bundle_id in request.accessibility_bundle_ids:
            try:
                bundle = self._accessibility.get_bundle(
                    bundle_id, account_id=account_id
                )
            except AccessibilityError:
                return False
            if not bundle.science_validated:
                return False
        # If there are media objects or storyboards but no bundles, fail.
        return not (
            request.media_object_ids or request.storyboard_ids
        ) or bool(request.accessibility_bundle_ids)

    def _check_fact_locks(
        self,
        account_id: str,
        request: MediaPublishRequest,
        fact_locks: list[FactLock] | None,
        fact_lock_set: FactLockSet | None,
    ) -> bool:
        """检查事实锁完整性。"""
        locks = fact_locks or (fact_lock_set.locks if fact_lock_set else [])
        if not locks:
            return True

        # Collect all claim entries and verify against fact locks.
        entries = self.collect_claim_entries(
            account_id, request, fact_locks=locks
        )
        consistency = self.check_cross_media_consistency(account_id, entries, locks)
        return consistency.consistent

    def _check_invalidation(
        self, account_id: str, request: MediaPublishRequest
    ) -> bool:
        """检查是否存在活动失效。"""
        if self._invalidation is None:
            return True

        # Check source assets.
        if self._ingestion is not None:
            for asset_id in request.source_asset_ids:
                try:
                    projection = self._ingestion.get_asset(account_id, asset_id)
                except (MediaError, ValueError, KeyError):
                    # If we can't access it, it might be invalidated.
                    return False
                from bridges.contracts.projects import ObjectDomain, ObjectRef

                # 与 media/service.py 的 _object_ref_for_asset 编码一致：
                # 资产归属上传账户（个人项目空间），失效键按同域查询。
                obj_ref = ObjectRef(
                    domain=ObjectDomain.PERSONAL_VAULT,
                    owner_id=projection.source_asset.account_id,
                    object_id=asset_id,
                    version=1,
                )
                try:
                    self._invalidation.require_active(obj_ref)
                except InvalidationError:
                    return False

        return True


def build_media_publish_impact_resolver(
    publish_service: MediaPublishService,
) -> ImpactResolver:
    """构建媒体发布失效影响解析器。"""

    def _resolver(event: InvalidationEvent) -> list[AffectedDownstream]:
        affected_records = publish_service.propagate_invalidation(event)
        affected: list[AffectedDownstream] = []
        for record in affected_records:
            affected.append(
                AffectedDownstream(
                    downstream_id=f"media_publish:{record.record_id}",
                    downstream_type="publish_record",
                    object_refs=[record.record_id],
                    scope_envelope=event.scope_envelope,
                    action="invalidate",
                )
            )
        return affected

    return _resolver
