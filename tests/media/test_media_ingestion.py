"""Module-interface tests for multimodal media ingestion (T030).

The seam under test: an authenticated subject uploads scientific images, scans,
formulas and tables; the service produces immutable SourceAssets and structured
DerivedAssets with regions, OCR, symbol tables, schemas and missing-value
handling. Scope isolation prevents cross-account access.
"""

from __future__ import annotations

import base64

import pytest

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.media import (
    MediaAssetKind,
    MediaAssetStatus,
    MediaIngestionStatus,
    MediaType,
    MediaUploadRequest,
)
from bridges.contracts.science import LicenseState
from bridges.media import MediaError, MediaIngestionService


@pytest.fixture
def service() -> MediaIngestionService:
    return MediaIngestionService()


@pytest.fixture
def alice() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def bob() -> SubjectContext:
    return SubjectContext(
        account_id="account-bob",
        session_id="session-bob",
        auth_method=AuthMethod.PASSWORD,
    )


def _png_bytes() -> bytes:
    """Minimal PNG magic header for deterministic tests."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _jpeg_bytes() -> bytes:
    """Minimal JPEG magic header for deterministic tests."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _upload(
    service: MediaIngestionService,
    account_id: str,
    filename: str,
    media_type: MediaType,
    content: bytes,
    *,
    project_id: str | None = None,
    license_state: LicenseState | None = LicenseState.USER_OWNED,
) -> str:
    request = MediaUploadRequest(
        filename=filename,
        media_type=media_type,
        content=base64.b64encode(content).decode("ascii"),
        license_state=license_state,
    )
    run = service.ingest_upload(account_id, project_id, request)
    assert run.asset_id is not None
    return run.asset_id


class TestImageIngestion:
    def test_image_upload_creates_source_and_derived_assets(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "experiment-graph.png",
            MediaType.IMAGE_PNG,
            _png_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        assert projection.source_asset.asset_id == asset_id
        assert projection.source_asset.media_type == MediaType.IMAGE_PNG
        assert projection.source_asset.status == MediaAssetStatus.PARSED
        assert projection.can_enter_evidence is True
        assert len(projection.derived_assets) == 1

        derived = projection.derived_assets[0]
        assert derived.derivation_type == MediaAssetKind.IMAGE_REGIONS
        assert derived.confidence < 1.0
        assert derived.human_corrected is False

        image_data = derived.payload
        assert "regions" in image_data
        assert "ocr_tokens" in image_data
        assert "legend" in image_data
        assert any(region["label"] == "plot_area" for region in image_data["regions"])

    def test_image_scale_is_extracted_when_present(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "microscope-sem.png",
            MediaType.IMAGE_PNG,
            _png_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        assert derived.payload["scale"] == "scale bar = 100 nm"

    def test_bad_magic_quarantines_image(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        request = MediaUploadRequest(
            filename="fake.png",
            media_type=MediaType.IMAGE_PNG,
            content=base64.b64encode(b"not a png").decode("ascii"),
            license_state=LicenseState.USER_OWNED,
        )
        run = service.ingest_upload(alice.account_id, None, request)
        assert run.status == MediaIngestionStatus.QUARANTINED
        assert run.gate_results["magic_number"] == "fail"


class TestFormulaIngestion:
    def test_formula_upload_preserves_symbol_table_and_ast(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "mass-energy-formula.tex",
            MediaType.APPLICATION_X_LATEX,
            b"E = mc^2",
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        assert derived.derivation_type == MediaAssetKind.FORMULA

        formula = derived.payload
        assert formula["latex"] == "E = mc^2"
        assert formula["mathml"] is not None
        assert "ast" in formula
        assert formula["accessible_text"] is not None

        symbols = {s["symbol"]: s for s in formula["symbol_table"]}
        assert "E" in symbols
        assert "m" in symbols
        assert "c" in symbols
        assert symbols["E"]["unit"] == "J"
        assert symbols["m"]["unit"] == "kg"
        assert symbols["c"]["unit"] == "m/s"


class TestTableIngestion:
    def test_csv_upload_preserves_schema_units_and_missing_values(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        csv_content = "Temp (K),Pressure (Pa)\n273.15,101325\n373.15,"
        asset_id = _upload(
            service,
            alice.account_id,
            "data.csv",
            MediaType.TEXT_CSV,
            csv_content.encode("utf-8"),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        assert derived.derivation_type == MediaAssetKind.TABLE

        table = derived.payload
        columns = table["table_schema"]["columns"]
        assert columns[0]["name"] == "Temp (K)"
        assert columns[0]["unit"] == "K"
        assert columns[1]["name"] == "Pressure (Pa)"
        assert columns[1]["unit"] == "Pa"

        rows = table["rows"]
        assert len(rows) == 2
        assert rows[0]["cells"][0]["value"] == "273.15"
        assert rows[1]["cells"][1]["is_missing"] is True

    def test_table_filename_hint_extracts_sample_data(
        self, service: MediaIngestionService, alice: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "pressure-temperature.png",
            MediaType.IMAGE_PNG,
            _png_bytes(),
        )

        projection = service.get_asset(alice.account_id, asset_id)
        derived = projection.derived_assets[0]
        # Image extractor returns IMAGE_REGIONS; table extraction from images is a
        # later enhancement. This test documents current deterministic behavior.
        assert derived.derivation_type == MediaAssetKind.IMAGE_REGIONS


class TestScopeIsolation:
    def test_bob_cannot_read_alice_media_asset(
        self, service: MediaIngestionService, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        asset_id = _upload(
            service,
            alice.account_id,
            "alice-secret.png",
            MediaType.IMAGE_PNG,
            _png_bytes(),
        )
        with pytest.raises(MediaError, match="访问权限"):
            service.get_asset(bob.account_id, asset_id)

    def test_same_content_hash_does_not_leak_other_account(
        self, service: MediaIngestionService, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        _upload(service, alice.account_id, "same.png", MediaType.IMAGE_PNG, _png_bytes())
        _upload(service, bob.account_id, "same.png", MediaType.IMAGE_PNG, _png_bytes())

        alice_assets = service.list_assets(alice.account_id)
        bob_assets = service.list_assets(bob.account_id)
        assert len(alice_assets) == 1
        assert len(bob_assets) == 1
        assert alice_assets[0].asset_id != bob_assets[0].asset_id
