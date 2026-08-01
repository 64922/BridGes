"""Module-interface tests for media asset correction and invalidation (T030).

The seam under test: human corrections create new DerivedAsset versions while
preserving the original SourceAsset and the previous DerivedAsset. Revocation
propagates through the invalidation foundation.
"""

from __future__ import annotations

import base64

import pytest

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.invalidation import InvalidationState
from bridges.contracts.media import (
    MediaAssetStatus,
    MediaCorrectionRequest,
    MediaCorrectionType,
    MediaIngestionStatus,
    MediaType,
    MediaUploadRequest,
)
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.science import LicenseState
from bridges.invalidation import InvalidationService
from bridges.media import MediaError, MediaIngestionService


@pytest.fixture
def invalidation_service() -> InvalidationService:
    return InvalidationService()


@pytest.fixture
def service(invalidation_service: InvalidationService) -> MediaIngestionService:
    svc = MediaIngestionService(invalidation_service=invalidation_service)
    from bridges.media.service import build_media_impact_resolver

    invalidation_service.register_impact_resolver(
        "media_asset", build_media_impact_resolver(svc)
    )
    return svc


@pytest.fixture
def alice() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _upload_formula(
    service: MediaIngestionService,
    account_id: str,
) -> tuple[str, str]:
    request = MediaUploadRequest(
        filename="mass-energy-formula.tex",
        media_type=MediaType.APPLICATION_X_LATEX,
        content=base64.b64encode(b"E = mc^2").decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload(account_id, None, request)
    assert run.asset_id is not None
    assert run.status == MediaIngestionStatus.COMPLETED
    projection = service.get_asset(account_id, run.asset_id)
    derived_id = projection.derived_assets[0].derived_asset_id
    return run.asset_id, derived_id


def _upload_image(
    service: MediaIngestionService,
    account_id: str,
    filename: str = "formula-scan.png",
) -> tuple[str, str]:
    request = MediaUploadRequest(
        filename=filename,
        media_type=MediaType.IMAGE_PNG,
        content=base64.b64encode(_png_bytes()).decode("ascii"),
        license_state=LicenseState.USER_OWNED,
    )
    run = service.ingest_upload(account_id, None, request)
    assert run.asset_id is not None
    projection = service.get_asset(account_id, run.asset_id)
    derived_id = projection.derived_assets[0].derived_asset_id
    return run.asset_id, derived_id


class TestDerivedAssetCorrection:
    def test_ocr_correction_creates_new_derived_version(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id, derived_id = _upload_image(service, alice.account_id, "formula-scan.png")
        original_projection = service.get_asset(alice.account_id, asset_id)
        original_source_hash = original_projection.source_asset.content_hash
        original_manifest_version = original_projection.manifest.version

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.OCR_TEXT,
            target_ref="mc",
            corrected_value="mc²",
            reason="superscript recognition error",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )

        assert new_derived.derived_asset_id != derived_id
        assert new_derived.derived_from_asset_id == derived_id
        assert new_derived.human_corrected is True
        assert new_derived.status == MediaAssetStatus.CORRECTED

        updated_projection = service.get_asset(alice.account_id, asset_id)
        # Source asset is preserved.
        assert updated_projection.source_asset.content_hash == original_source_hash
        # Manifest version bumped.
        assert updated_projection.manifest.version == original_manifest_version + 1
        # New derived asset is active in the manifest.
        assert new_derived.derived_asset_id in updated_projection.manifest.derived_asset_ids
        # Old derived asset is preserved in the chain.
        assert derived_id in updated_projection.manifest.derived_asset_ids

        # Verify the correction was applied.
        tokens = new_derived.payload["ocr_tokens"]
        assert any(t["text"] == "mc²" for t in tokens)

    def test_formula_latex_correction_updates_latex(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id, derived_id = _upload_formula(service, alice.account_id)

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.FORMULA_LATEX,
            target_ref="latex",
            corrected_value="E = mc^2 + 0",
            reason="add constant term for demonstration",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, asset_id, correction
        )

        assert new_derived.payload["latex"] == "E = mc^2 + 0"

    def test_table_cell_correction_preserves_missing_value_flag(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        request = MediaUploadRequest(
            filename="data.csv",
            media_type=MediaType.TEXT_CSV,
            content=base64.b64encode(b"A,B\n1,\n2,3").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        run = service.ingest_upload(alice.account_id, None, request)
        assert run.asset_id is not None
        projection = service.get_asset(alice.account_id, run.asset_id)
        derived_id = projection.derived_assets[0].derived_asset_id

        correction = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.TABLE_CELL,
            target_ref="0:1",
            corrected_value="",
            reason="value still missing after review",
        )
        new_derived = service.correct_derived_asset(
            alice.account_id, run.asset_id, correction
        )

        rows = new_derived.payload["rows"]
        assert rows[0]["cells"][1]["is_missing"] is True

        correction2 = MediaCorrectionRequest(
            derived_asset_id=derived_id,
            correction_type=MediaCorrectionType.TABLE_CELL,
            target_ref="0:1",
            corrected_value="1.5",
            reason="fill missing value",
        )
        new_derived2 = service.correct_derived_asset(
            alice.account_id, run.asset_id, correction2
        )
        rows2 = new_derived2.payload["rows"]
        assert rows2[0]["cells"][1]["value"] == "1.5"
        assert rows2[0]["cells"][1]["is_missing"] is False


class TestInvalidationIntegration:
    def test_revoked_media_asset_blocks_new_reads(
        self,
        service: MediaIngestionService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        asset_id, _derived_id = _upload_image(service, alice.account_id)
        asset_ref, event = service.revoke_asset(
            alice.account_id, asset_id, "撤权", subject=alice
        )
        assert event is not None

        plan = invalidation_service.plan_invalidation(event.event_id)
        downstream_types = {d.downstream_type for d in plan.impact_set.affected_downstreams}
        assert "index_projection" in downstream_types
        assert "cache" in downstream_types
        assert "workflow_run" in downstream_types

        with pytest.raises(MediaError, match="失效"):
            service.get_asset(alice.account_id, asset_id)

    def test_tombstoned_media_asset_blocks_new_use(
        self,
        service: MediaIngestionService,
        invalidation_service: InvalidationService,
        alice: SubjectContext,
    ) -> None:
        asset_id, _derived_id = _upload_image(service, alice.account_id)
        source_asset = service._assets[asset_id].source_asset
        asset_ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=source_asset.account_id,
            object_id=source_asset.asset_id,
            version=1,
        )

        invalidation_service.record_tombstone(alice, asset_ref, "删除")
        state = invalidation_service.check_state(asset_ref)
        assert state.state == InvalidationState.TOMBSTONED

        with pytest.raises(MediaError, match="删除"):
            service.get_asset(alice.account_id, asset_id)
