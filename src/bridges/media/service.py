"""Multimodal media ingestion and correction domain service for T030.

The MediaIngestionService is the deep module boundary for importing scientific
images, scans, formulas and tables. It enforces scope isolation, input quality
gates, immutable original assets, versioned derived assets, human corrections and
invalidation integration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.ai import ModelGateway
from bridges.chat.attachments import validate_filename
from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.invalidation import (
    AffectedDownstream,
    ImpactResolver,
    InvalidationEvent,
    InvalidationEventType,
)
from bridges.contracts.media import (
    AudioVideoDerivedData,
    DerivedAsset,
    FormulaAsset,
    ImageDerivedData,
    MediaAssetKind,
    MediaAssetStatus,
    MediaCorrectionRequest,
    MediaCorrectionType,
    MediaGateResult,
    MediaIngestionRunRef,
    MediaIngestionStatus,
    MediaManifest,
    MediaProjection,
    MediaQualityGate,
    MediaUploadRequest,
    SourceAsset,
    SpeakerSegment,
    TableAsset,
    TableCell,
    TranscriptSegment,
)
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.science import (
    ClaimRequest,
    LicenseState,
    MediaType,
    SourceLicense,
)
from bridges.contracts.scope import ScopeAction, ScopeEnvelope, ScopeIsolationError
from bridges.invalidation import InvalidationError, InvalidationService
from bridges.media.extraction import ExtractionError, ExtractionPort, select_extractor
from bridges.media.qwen_extraction import (
    QwenAsrExtractor,
    QwenOcrExtractor,
    build_run_context_for_asr,
    build_run_context_for_ocr,
)
from bridges.scope import ScopeEnforcer


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_ref_for_asset(asset: SourceAsset) -> ObjectRef:
    domain = ObjectDomain.SHARED_PROJECT if asset.project_id else ObjectDomain.PERSONAL_VAULT
    owner_id = asset.project_id if asset.project_id else asset.account_id
    return ObjectRef(domain=domain, owner_id=owner_id, object_id=asset.asset_id, version=1)


class MediaError(Exception):
    """Domain exception for media ingestion failures."""


@dataclass
class _StoredAsset:
    source_asset: SourceAsset
    manifests: dict[int, MediaManifest]
    derived_assets: dict[str, DerivedAsset]
    gate_results: dict[MediaQualityGate, MediaGateResult] | None = None


class MediaIngestionService:
    """In-memory media ingestion service for T030."""

    def __init__(
        self,
        scope_enforcer: ScopeEnforcer | None = None,
        invalidation_service: InvalidationService | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self._assets: dict[str, _StoredAsset] = {}
        self._ingestion_runs: dict[str, MediaIngestionRunRef] = {}
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation = invalidation_service
        self._model_gateway = model_gateway

    def _subject(self, account_id: str) -> SubjectContext:
        return SubjectContext(
            account_id=account_id,
            session_id="media-service",
            auth_method=AuthMethod.SERVICE,
        )

    def _authorize_asset(
        self, account_id: str, asset: SourceAsset, action: ScopeAction
    ) -> ScopeEnvelope:
        """Authorize an action against a source asset."""
        if account_id != asset.account_id:
            raise MediaError("媒体资产不存在或没有访问权限。")
        subject = self._subject(account_id)
        asset_ref = _object_ref_for_asset(asset)
        try:
            return self._scope_enforcer.authorize(subject, action, asset_ref)
        except ScopeIsolationError as exc:
            raise MediaError(str(exc)) from exc

    def _authorize_asset_id(
        self, account_id: str, asset_id: str, action: ScopeAction
    ) -> tuple[SourceAsset, ScopeEnvelope]:
        """Look up a source asset and authorize the action."""
        stored = self._assets.get(asset_id)
        if stored is None:
            raise MediaError("媒体资产不存在或没有访问权限。")
        scope = self._authorize_asset(account_id, stored.source_asset, action)
        return stored.source_asset, scope

    def _require_active(self, asset_ref: ObjectRef) -> None:
        """Fail closed if the asset is revoked or tombstoned."""
        if self._invalidation is None:
            return
        try:
            self._invalidation.require_active(asset_ref)
        except InvalidationError as exc:
            raise MediaError(str(exc)) from exc

    def _select_extractor(
        self, source_asset: SourceAsset, media_type: MediaType
    ) -> ExtractionPort:
        """Choose a model-backed extractor when a gateway is available.

        Image, formula-image, table-image, audio and video assets are routed to
        real Qwen pipelines so the ingestion seam produces actual model-derived
        structures. Other media types keep their deterministic extractors so
        local tests remain stable without an API key.
        """
        if self._model_gateway is None:
            return select_extractor(media_type)

        if media_type in {
            MediaType.IMAGE_PNG,
            MediaType.IMAGE_JPEG,
            MediaType.IMAGE_WEBP,
            MediaType.APPLICATION_X_LATEX,
            MediaType.APPLICATION_X_TEX,
        }:
            return QwenOcrExtractor(
                self._model_gateway, build_run_context_for_ocr(source_asset)
            )

        if media_type in {
            MediaType.AUDIO_MPEG,
            MediaType.AUDIO_WAV,
            MediaType.AUDIO_OGG,
            MediaType.VIDEO_MP4,
            MediaType.VIDEO_WEBM,
            MediaType.VIDEO_OGG,
        }:
            return QwenAsrExtractor(
                self._model_gateway, build_run_context_for_asr(source_asset)
            )

        return select_extractor(media_type)

    def _next_manifest_version(self, asset_id: str) -> int:
        stored = self._assets[asset_id]
        if not stored.manifests:
            return 1
        return max(stored.manifests.keys()) + 1

    def _create_manifest(
        self,
        asset: SourceAsset,
        derived_asset_ids: list[str],
        base_manifest: MediaManifest | None = None,
    ) -> MediaManifest:
        now = _now()
        version = self._next_manifest_version(asset.asset_id)
        if base_manifest is not None:
            return base_manifest.model_copy(
                update={
                    "manifest_id": base_manifest.manifest_id,
                    "version": version,
                    "derived_asset_ids": derived_asset_ids,
                    "created_at": now,
                }
            )
        return MediaManifest(
            manifest_id=secrets.token_urlsafe(16),
            version=version,
            account_id=asset.account_id,
            project_id=asset.project_id,
            source_asset_id=asset.asset_id,
            derived_asset_ids=derived_asset_ids,
            created_at=now,
        )

    def ingest_upload(
        self,
        account_id: str,
        project_id: str | None,
        request: MediaUploadRequest,
        *,
        object_storage_ref: str | None = None,
    ) -> MediaIngestionRunRef:
        """Ingest a media asset, run input quality gates and extract structures."""
        run_id = secrets.token_urlsafe(16)
        now = _now()
        run_ref = MediaIngestionRunRef(
            run_id=run_id,
            status=MediaIngestionStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        self._ingestion_runs[run_id] = run_ref

        # Issue 39 AC4：文件名与对话附件/知识库共用同一套路径安全校验
        # （拒绝穿越、控制字符与保留名），即使当前存储为内存也提前闭锁。
        try:
            safe_filename = validate_filename(request.filename)
        except Exception as exc:
            run_ref.status = MediaIngestionStatus.FAILED
            run_ref.error = f"文件名不合法：{exc}"
            run_ref.gate_results[MediaQualityGate.SCOPE] = MediaGateResult.FAIL
            run_ref.updated_at = _now()
            return run_ref

        try:
            content = base64.b64decode(request.content)
        except Exception as exc:
            run_ref.status = MediaIngestionStatus.FAILED
            run_ref.error = f"base64 decode failed: {exc}"
            run_ref.gate_results[MediaQualityGate.PARSE] = MediaGateResult.FAIL
            run_ref.updated_at = _now()
            return run_ref

        asset_id = secrets.token_urlsafe(16)

        license_state = request.license_state or LicenseState.USER_OWNED
        source_asset = SourceAsset(
            asset_id=asset_id,
            account_id=account_id,
            project_id=project_id,
            media_type=request.media_type,
            detected_format=request.media_type.value,
            original_filename=safe_filename,
            byte_size=len(content),
            content_hash=_sha256(content),
            created_at=now,
            acquired_at=now,
            updated_at=now,
            license=SourceLicense(state=license_state, rights_statement="用户上传"),
            status=MediaAssetStatus.DISCOVERED,
            object_storage_ref=object_storage_ref,
        )
        self._assets[asset_id] = _StoredAsset(
            source_asset=source_asset,
            manifests={},
            derived_assets={},
        )
        run_ref.asset_id = asset_id

        # Scope gate.
        try:
            self._authorize_asset(account_id, source_asset, ScopeAction.CREATE)
        except MediaError as exc:
            run_ref.status = MediaIngestionStatus.FAILED
            run_ref.error = str(exc)
            run_ref.gate_results[MediaQualityGate.SCOPE] = MediaGateResult.FAIL
            run_ref.updated_at = _now()
            return run_ref

        # License gate.
        if license_state == LicenseState.UNKNOWN:
            run_ref.gate_results[MediaQualityGate.LICENSE] = MediaGateResult.WAIT
        else:
            run_ref.gate_results[MediaQualityGate.LICENSE] = MediaGateResult.PASS

        # Select extractor and run gates.
        try:
            extractor = self._select_extractor(source_asset, request.media_type)
            gate_results = extractor.gate_results(content)
            run_ref.gate_results.update(gate_results)
        except ExtractionError as exc:
            run_ref.status = MediaIngestionStatus.FAILED
            run_ref.error = str(exc)
            run_ref.gate_results[MediaQualityGate.PARSE] = MediaGateResult.FAIL
            source_asset.status = MediaAssetStatus.BLOCKED
            source_asset.updated_at = _now()
            run_ref.updated_at = _now()
            return run_ref

        failed_gates = [
            g for g, r in run_ref.gate_results.items() if r == MediaGateResult.FAIL
        ]
        if failed_gates:
            if MediaQualityGate.PARSE in failed_gates:
                source_asset.status = MediaAssetStatus.BLOCKED
                run_ref.status = MediaIngestionStatus.FAILED
            else:
                source_asset.status = MediaAssetStatus.QUARANTINED
                run_ref.status = MediaIngestionStatus.QUARANTINED
            source_asset.updated_at = _now()
            run_ref.updated_at = _now()
            return run_ref

        # Extract derived assets.
        try:
            derived_assets = extractor.extract(source_asset, content)
        except ExtractionError as exc:
            run_ref.status = MediaIngestionStatus.FAILED
            run_ref.error = str(exc)
            run_ref.gate_results[MediaQualityGate.PARSE] = MediaGateResult.FAIL
            source_asset.status = MediaAssetStatus.BLOCKED
            source_asset.updated_at = _now()
            run_ref.updated_at = _now()
            return run_ref

        stored = self._assets[asset_id]
        derived_asset_ids: list[str] = []
        for derived in derived_assets:
            stored.derived_assets[derived.derived_asset_id] = derived
            derived_asset_ids.append(derived.derived_asset_id)

        manifest = self._create_manifest(source_asset, derived_asset_ids)
        stored.manifests[manifest.version] = manifest

        source_asset.status = MediaAssetStatus.PARSED
        source_asset.updated_at = _now()
        run_ref.status = MediaIngestionStatus.COMPLETED
        run_ref.updated_at = _now()
        stored.gate_results = dict(run_ref.gate_results)
        return run_ref

    def get_asset(self, account_id: str, asset_id: str) -> MediaProjection:
        """Return a media projection including source, manifest and derived assets."""
        source_asset, _scope = self._authorize_asset_id(account_id, asset_id, ScopeAction.READ)
        self._require_active(_object_ref_for_asset(source_asset))

        stored = self._assets[asset_id]
        latest_manifest = stored.manifests[max(stored.manifests.keys())]
        derived_assets = [
            stored.derived_assets[derived_id]
            for derived_id in latest_manifest.derived_asset_ids
            if derived_id in stored.derived_assets
        ]

        gate_results: dict[MediaQualityGate, MediaGateResult] = {}
        if stored.gate_results:
            gate_results = dict(stored.gate_results)
        else:
            gate_results[MediaQualityGate.PARSE] = MediaGateResult.PASS
            gate_results[MediaQualityGate.LICENSE] = (
                MediaGateResult.PASS
                if source_asset.license.state != LicenseState.UNKNOWN
                else MediaGateResult.WAIT
            )

        can_enter_evidence = (
            source_asset.status == MediaAssetStatus.PARSED
            and source_asset.license.state
            not in (LicenseState.UNKNOWN, LicenseState.PENDING_REVIEW)
        )

        return MediaProjection(
            source_asset=source_asset,
            manifest=latest_manifest,
            derived_assets=derived_assets,
            version_count=len(stored.manifests),
            can_enter_evidence=can_enter_evidence,
            gate_results=gate_results,
        )

    def get_derived_asset(
        self, account_id: str, asset_id: str, derived_asset_id: str
    ) -> DerivedAsset:
        """Return a single derived asset."""
        self._authorize_asset_id(account_id, asset_id, ScopeAction.READ)
        self._require_active(_object_ref_for_asset(self._assets[asset_id].source_asset))
        stored = self._assets[asset_id]
        derived = stored.derived_assets.get(derived_asset_id)
        if derived is None:
            raise MediaError("派生资产不存在或没有访问权限。")
        return derived

    def list_assets(
        self,
        account_id: str,
        project_id: str | None = None,
    ) -> list[SourceAsset]:
        """List source assets visible to the account."""
        results: list[SourceAsset] = []
        for stored in self._assets.values():
            asset = stored.source_asset
            if asset.account_id != account_id:
                continue
            if project_id is not None and asset.project_id != project_id:
                continue
            if project_id is None and asset.project_id is not None:
                continue
            try:
                self._authorize_asset(account_id, asset, ScopeAction.READ)
            except MediaError:
                continue
            results.append(asset)
        results.sort(key=lambda a: a.created_at, reverse=True)
        return results

    def correct_derived_asset(
        self,
        account_id: str,
        asset_id: str,
        request: MediaCorrectionRequest,
        subject: SubjectContext | None = None,
    ) -> DerivedAsset:
        """Apply a human correction to a derived asset, creating a new version.

        The original derived asset is preserved; a new DerivedAsset is created and
        the manifest is updated to include it.
        """
        source_asset, _scope = self._authorize_asset_id(account_id, asset_id, ScopeAction.UPDATE)
        self._require_active(_object_ref_for_asset(source_asset))
        stored = self._assets[asset_id]

        base_derived = stored.derived_assets.get(request.derived_asset_id)
        if base_derived is None:
            raise MediaError("派生资产不存在或没有访问权限。")

        new_payload = self._apply_correction(base_derived, request)
        new_derived = DerivedAsset(
            derived_asset_id=secrets.token_urlsafe(16),
            source_asset_id=asset_id,
            derived_from_asset_id=base_derived.derived_asset_id,
            derivation_type=self._correction_kind(request.correction_type),
            tool="human_correction",
            tool_version="1",
            parameters={
                "base_derived_asset_id": base_derived.derived_asset_id,
                "correction_type": request.correction_type.value,
                "target_ref": request.target_ref,
                "reason": request.reason,
            },
            content_hash=_sha256(
                json.dumps(new_payload, sort_keys=True).encode("utf-8")
            ),
            payload=new_payload,
            locator=base_derived.locator,
            confidence=1.0,
            human_corrected=True,
            status=MediaAssetStatus.CORRECTED,
            correction_reason=request.reason,
            created_at=_now(),
        )
        stored.derived_assets[new_derived.derived_asset_id] = new_derived

        # Update manifest: replace the corrected derived asset with the new version.
        latest_manifest = stored.manifests[max(stored.manifests.keys())]
        new_derived_asset_ids = [
            new_derived.derived_asset_id
            if derived_id == base_derived.derived_asset_id
            else derived_id
            for derived_id in latest_manifest.derived_asset_ids
        ]
        # Keep the old derived asset id in the chain if not already present.
        if base_derived.derived_asset_id not in new_derived_asset_ids:
            new_derived_asset_ids.append(base_derived.derived_asset_id)

        new_manifest = self._create_manifest(
            source_asset,
            new_derived_asset_ids,
            base_manifest=latest_manifest,
        )
        stored.manifests[new_manifest.version] = new_manifest

        # Record a version-superseded invalidation event so downstream claim graphs
        # and fact lock sets can be revalidated.
        if self._invalidation is not None:
            actor = subject or self._subject(account_id)
            asset_ref = _object_ref_for_asset(source_asset)
            self._invalidation.record_invalidation_event(
                actor,
                asset_ref,
                InvalidationEventType.SOURCE_VERSION_SUPERSEDED,
                (
                    f"派生资产 {base_derived.derived_asset_id}"
                    f" 被人工校正为 {new_derived.derived_asset_id}"
                ),
            )

        return new_derived

    def _correction_kind(self, correction_type: MediaCorrectionType) -> MediaAssetKind:
        mapping: dict[MediaCorrectionType, MediaAssetKind] = {
            MediaCorrectionType.OCR_TEXT: MediaAssetKind.IMAGE_OCR,
            MediaCorrectionType.REGION_LABEL: MediaAssetKind.IMAGE_REGIONS,
            MediaCorrectionType.LEGEND: MediaAssetKind.IMAGE_LEGEND,
            MediaCorrectionType.SCALE: MediaAssetKind.IMAGE_SCALE,
            MediaCorrectionType.FORMULA_LATEX: MediaAssetKind.FORMULA,
            MediaCorrectionType.FORMULA_SYMBOL: MediaAssetKind.FORMULA,
            MediaCorrectionType.TABLE_CELL: MediaAssetKind.TABLE,
            MediaCorrectionType.TABLE_SCHEMA: MediaAssetKind.TABLE,
            MediaCorrectionType.TRANSCRIPT_TERM: MediaAssetKind.AUDIO_TRANSCRIPT,
            MediaCorrectionType.SPEAKER_SEGMENT: MediaAssetKind.AUDIO_SEGMENT,
            MediaCorrectionType.CAPTION_TEXT: MediaAssetKind.CAPTION_TRACK,
            MediaCorrectionType.KEYFRAME_INTERPRETATION: MediaAssetKind.VIDEO_KEYFRAME,
        }
        return mapping.get(correction_type, MediaAssetKind.CORRECTION)

    def _apply_correction(
        self, derived: DerivedAsset, request: MediaCorrectionRequest
    ) -> dict[str, Any]:
        """Apply a correction to a derived asset payload."""
        payload = dict(derived.payload)

        if request.correction_type == MediaCorrectionType.OCR_TEXT:
            tokens = [
                token.model_copy(update={"text": request.corrected_value})
                if token.text == request.target_ref
                else token
                for token in ImageDerivedData(**payload).ocr_tokens
            ]
            payload["ocr_tokens"] = [t.model_dump(mode="json") for t in tokens]
        elif request.correction_type == MediaCorrectionType.REGION_LABEL:
            regions = [
                region.model_copy(update={"label": request.corrected_value})
                if region.region_id == request.target_ref
                else region
                for region in ImageDerivedData(**payload).regions
            ]
            payload["regions"] = [r.model_dump(mode="json") for r in regions]
        elif request.correction_type == MediaCorrectionType.LEGEND:
            payload["legend"] = request.corrected_value
        elif request.correction_type == MediaCorrectionType.SCALE:
            payload["scale"] = request.corrected_value
        elif request.correction_type == MediaCorrectionType.FORMULA_LATEX:
            formula = FormulaAsset(**payload)
            formula.latex = request.corrected_value
            payload = formula.model_dump(mode="json")
        elif request.correction_type == MediaCorrectionType.FORMULA_SYMBOL:
            formula = FormulaAsset(**payload)
            for symbol in formula.symbol_table:
                if symbol.symbol == request.target_ref:
                    symbol.definition = request.corrected_value
            payload = formula.model_dump(mode="json")
        elif request.correction_type in {
            MediaCorrectionType.TABLE_CELL,
            MediaCorrectionType.TABLE_SCHEMA,
        }:
            table = TableAsset(**payload)
            if request.correction_type == MediaCorrectionType.TABLE_SCHEMA:
                # target_ref is expected as "column:{index}".
                if request.target_ref.startswith("column:"):
                    idx = int(request.target_ref.split(":", 1)[1])
                    if 0 <= idx < len(table.table_schema.columns):
                        table.table_schema.columns[idx].name = request.corrected_value
            else:
                # target_ref is expected as "{row}:{col}".
                parts = request.target_ref.split(":")
                if len(parts) == 2:
                    row_idx, col_idx = int(parts[0]), int(parts[1])
                    if 0 <= row_idx < len(table.rows):
                        cells = list(table.rows[row_idx].cells)
                        if 0 <= col_idx < len(cells):
                            cells[col_idx] = TableCell(
                                value=request.corrected_value,
                                is_missing=request.corrected_value.strip() == "",
                            )
                            table.rows[row_idx].cells = cells
            payload = table.model_dump(mode="json")
        elif request.correction_type == MediaCorrectionType.TRANSCRIPT_TERM:
            av_data = AudioVideoDerivedData(**payload)
            new_segments: list[TranscriptSegment] = []
            for segment in av_data.transcript_segments:
                if segment.segment_id == request.target_ref:
                    new_segments.append(
                        segment.model_copy(
                            update={
                                "text": request.corrected_value,
                                "low_confidence": False,
                                "confidence": 1.0,
                                "words": [],
                            }
                        )
                    )
                else:
                    new_segments.append(segment)
            av_data.transcript_segments = new_segments
            payload = av_data.model_dump(mode="json")
        elif request.correction_type == MediaCorrectionType.SPEAKER_SEGMENT:
            av_data = AudioVideoDerivedData(**payload)
            new_speakers: list[SpeakerSegment] = []
            for speaker in av_data.speaker_segments:
                if speaker.segment_id == request.target_ref:
                    new_speakers.append(
                        speaker.model_copy(update={"speaker_id": request.corrected_value})
                    )
                else:
                    new_speakers.append(speaker)
            av_data.speaker_segments = new_speakers
            payload = av_data.model_dump(mode="json")
        elif request.correction_type == MediaCorrectionType.CAPTION_TEXT:
            av_data = AudioVideoDerivedData(**payload)
            new_captions = [
                caption.model_copy(update={"text": request.corrected_value})
                if caption.caption_id == request.target_ref
                else caption
                for caption in av_data.captions
            ]
            av_data.captions = new_captions
            payload = av_data.model_dump(mode="json")
        elif request.correction_type == MediaCorrectionType.KEYFRAME_INTERPRETATION:
            av_data = AudioVideoDerivedData(**payload)
            new_keyframes = [
                keyframe.model_copy(update={"interpretation": request.corrected_value})
                if keyframe.keyframe_id == request.target_ref
                else keyframe
                for keyframe in av_data.keyframes
            ]
            av_data.keyframes = new_keyframes
            payload = av_data.model_dump(mode="json")

        return payload

    def revoke_asset(
        self,
        account_id: str,
        asset_id: str,
        reason: str,
        subject: SubjectContext | None = None,
    ) -> tuple[ObjectRef, InvalidationEvent | None]:
        """Revoke a media asset so it cannot be used in new evidence."""
        source_asset, _scope = self._authorize_asset_id(account_id, asset_id, ScopeAction.DELETE)
        source_asset.status = MediaAssetStatus.REVOKED
        source_asset.updated_at = _now()
        asset_ref = _object_ref_for_asset(source_asset)

        event: InvalidationEvent | None = None
        if self._invalidation is not None:
            actor = subject or self._subject(account_id)
            event = self._invalidation.record_invalidation_event(
                actor,
                asset_ref,
                InvalidationEventType.SOURCE_RETRACTED,
                reason,
            )

        return asset_ref, event

    def get_ingestion_run(self, account_id: str, run_id: str) -> MediaIngestionRunRef:
        """Return an ingestion run if it belongs to the account's assets."""
        run = self._ingestion_runs.get(run_id)
        if run is None or run.asset_id is None:
            raise MediaError("摄入运行不存在或没有访问权限。")
        self._authorize_asset_id(account_id, run.asset_id, ScopeAction.READ)
        return run

    def build_claim_request(
        self,
        account_id: str,
        asset_id: str,
        query: str,
        project_id: str | None = None,
    ) -> ClaimRequest:
        """Build a ClaimRequest from the derived assets of a media asset.

        The generated request binds the media-derived structures as evidence
        candidates so that the claim-evidence service can produce locatable claims.
        """
        projection = self.get_asset(account_id, asset_id)
        object_domain = (
            ObjectDomain.SHARED_PROJECT
            if projection.source_asset.project_id
            else ObjectDomain.PERSONAL_VAULT
        )
        return ClaimRequest(
            query=query,
            project_id=project_id or projection.source_asset.project_id,
            object_domain=object_domain,
            top_k=5,
            include_refutations=True,
        )


def build_media_impact_resolver(
    media_service: MediaIngestionService,
) -> ImpactResolver:
    """Build an impact resolver for media asset invalidation events."""

    def _resolver(event: InvalidationEvent) -> list[AffectedDownstream]:
        asset_id = event.object_ref.object_id
        affected: list[AffectedDownstream] = []
        affected.append(
            AffectedDownstream(
                downstream_id=f"media_index:{asset_id}",
                downstream_type="index_projection",
                object_refs=[asset_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        )
        affected.append(
            AffectedDownstream(
                downstream_id=f"media_cache:{asset_id}",
                downstream_type="cache",
                object_refs=[asset_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        )
        affected.append(
            AffectedDownstream(
                downstream_id=f"media_run:{asset_id}",
                downstream_type="workflow_run",
                object_refs=[asset_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        )
        return affected

    return _resolver
