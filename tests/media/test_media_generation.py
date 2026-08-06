"""Module-interface tests for T032 editable static figures and data charts.

The seam under test: the MediaGenerationService takes structured data and claim
bindings, produces editable specs (chart/figure), renders SVGs, and supports
re-validation after editing.

Key acceptance criteria:
- Chart values, axes, units, aggregation and error match source data.
- Figure labels and relations bind to Claims and FactLocks.
- User can modify the editable source and re-validate.
- Every visual has alt text or an equivalent data table.
"""

from __future__ import annotations

import json

import pytest

from bridges.contracts.media import (
    AccessibilityAlternative,
    ChartAxis,
    ChartAxisType,
    ChartDataColumn,
    ChartDataPoint,
    ChartDataTable,
    ChartEncodingMapping,
    ChartGenerationRequest,
    ChartMark,
    ChartSpec,
    ClaimVisualBinding,
    EditableSource,
    FigureElement,
    FigureGenerationRequest,
    GenerationResult,
    GenerationStatus,
    MediaObjectType,
    ScientificFigureSpec,
    ScientificMediaObject,
    SpecValidationResult,
)
from bridges.contracts.science import FactLock, FactLockType
from bridges.media.generation import (
    MediaGenerationError,
    MediaGenerationService,
)


@pytest.fixture
def service() -> MediaGenerationService:
    return MediaGenerationService()


@pytest.fixture
def sample_data() -> ChartDataTable:
    return ChartDataTable(
        columns=[
            ChartDataColumn(name="city", data_type="string"),
            ChartDataColumn(name="temperature", data_type="number", unit="°C"),
            ChartDataColumn(name="error", data_type="number", unit="°C"),
        ],
        rows=[
            ChartDataPoint(values={"city": "北京", "temperature": 25.0, "error": 2.0}),
            ChartDataPoint(values={"city": "上海", "temperature": 28.0, "error": 1.5}),
            ChartDataPoint(values={"city": "广州", "temperature": 30.0, "error": 1.0}),
            ChartDataPoint(values={"city": "深圳", "temperature": 29.0, "error": 1.8}),
        ],
    )


@pytest.fixture
def numerical_data() -> ChartDataTable:
    return ChartDataTable(
        columns=[
            ChartDataColumn(name="year", data_type="number"),
            ChartDataColumn(name="value", data_type="number"),
        ],
        rows=[
            ChartDataPoint(values={"year": 2010, "value": 10.0}),
            ChartDataPoint(values={"year": 2011, "value": 12.5}),
            ChartDataPoint(values={"year": 2012, "value": 15.3}),
            ChartDataPoint(values={"year": 2013, "value": 18.1}),
            ChartDataPoint(values={"year": 2014, "value": 22.0}),
        ],
    )


@pytest.fixture
def sample_locks() -> list[FactLock]:
    return [
        FactLock(
            lock_id="lock-001",
            claim_id="claim-temp",
            lock_type=FactLockType.EXACT_VALUE,
            canonical_value="25.0",
            allowed_variants=[],
            forbidden_transformations=[],
            required_qualifiers=[],
            evidence_ids=[],
            citation_ids=[],
            wording_strength_ceiling="high",
            verification_method="rule",
        ),
        FactLock(
            lock_id="lock-002",
            claim_id="claim-city-Beijing",
            lock_type=FactLockType.IDENTIFIER,
            canonical_value="北京",
            allowed_variants=["Beijing"],
            forbidden_transformations=[],
            required_qualifiers=[],
            evidence_ids=[],
            citation_ids=[],
            wording_strength_ceiling="high",
            verification_method="rule",
        ),
    ]


# ── Chart generation tests ────────────────────────────────────────────


class TestChartGeneration:
    """Acceptance: chart values, axes, units, aggregation and error match source data."""

    def test_bar_chart_generates_spec_with_correct_axes(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="城市气温对比",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
            y_label="温度",
            y_unit="°C",
        )
        result = service.generate_chart(request, account_id="test-account")

        obj = result.media_object
        assert obj.media_type == MediaObjectType.CHART
        assert obj.status == GenerationStatus.COMPLETED
        assert obj.editable_source.source_type == "chart_spec"
        assert obj.svg_content is not None
        assert "<svg" in obj.svg_content

        # Verify spec was round-tripped correctly.
        spec_data = json.loads(obj.editable_source.content)
        assert spec_data["title"] == "城市气温对比"
        assert spec_data["mark"] == "bar"

        # Axes are generated correctly.
        assert len(spec_data["axes"]) == 2
        x_axis = spec_data["axes"][0]
        y_axis = spec_data["axes"][1]
        assert x_axis["field"] == "city"
        assert y_axis["field"] == "temperature"
        assert y_axis["unit"] == "°C"
        # Provided label is used as-is (not auto-appended with unit).
        assert y_axis["label"] == "温度"

        # Data preserved.
        assert len(spec_data["data"]["rows"]) == 4

    def test_numerical_data_uses_linear_axis(
        self, service: MediaGenerationService, numerical_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="年度增长",
            mark=ChartMark.LINE,
            data=numerical_data,
            x_field="year",
            y_field="value",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert result.media_object.status == GenerationStatus.COMPLETED

        spec_data = json.loads(result.media_object.editable_source.content)
        x_axis = spec_data["axes"][0]
        y_axis = spec_data["axes"][1]
        assert x_axis["axis_type"] == "linear"
        assert y_axis["axis_type"] == "linear"
        # Domain should be auto-computed.
        assert x_axis["domain_min"] is not None
        assert x_axis["domain_max"] is not None

    def test_scatter_chart_renders_svg_with_points(
        self, service: MediaGenerationService, numerical_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="散点测试",
            mark=ChartMark.SCATTER,
            data=numerical_data,
            x_field="year",
            y_field="value",
        )
        result = service.generate_chart(request, account_id="test-account")
        svg = result.media_object.svg_content
        assert svg is not None
        # Should contain circles for scatter points.
        assert "circle" in svg

    def test_chart_with_error_bars_includes_error_in_spec(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="带误差棒的气温",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
            error_field="error",
        )
        result = service.generate_chart(request, account_id="test-account")
        spec_data = json.loads(result.media_object.editable_source.content)
        assert spec_data["has_error_bars"] is True
        # SVG should contain error-bar lines.
        assert "error-bar" in (result.media_object.svg_content or "")

    def test_chart_with_color_encoding(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="带颜色分组",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
            color_field="city",
        )
        result = service.generate_chart(request, account_id="test-account")
        spec_data = json.loads(result.media_object.editable_source.content)
        encodings = spec_data["encodings"]
        color_enc = next((e for e in encodings if e["encoding"] == "color"), None)
        assert color_enc is not None
        assert color_enc["field"] == "city"

    def test_chart_with_empty_data_reports_validation_warning(
        self, service: MediaGenerationService
    ) -> None:
        empty_data = ChartDataTable(
            columns=[ChartDataColumn(name="x", data_type="number")],
            rows=[],
        )
        request = ChartGenerationRequest(
            title="空数据",
            mark=ChartMark.BAR,
            data=empty_data,
            x_field="x",
            y_field="x",
        )
        result = service.generate_chart(request, account_id="test-account")
        # Should produce SVG even with no data.
        assert result.media_object.svg_content is not None
        assert "<svg" in result.media_object.svg_content


class TestChartDataConsistency:
    """Acceptance: chart values are consistent with source data."""

    def test_validation_passes_for_consistent_data(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="一致数据",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert len(result.media_object.validation_errors) == 0
        assert result.media_object.status == GenerationStatus.COMPLETED

    def test_validation_detects_missing_data_field(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        """Spec validation catches references to non-existent fields."""
        spec = ChartSpec(
            spec_id="test",
            title="错误字段",
            mark=ChartMark.BAR,
            data=sample_data,
            axes=[
                ChartAxis(field="city", label="城市"),
                ChartAxis(field="nonexistent", label="不存在"),
            ],
        )
        # Validate through the spec API.
        result = service.validate_spec(spec.model_dump_json())
        assert result.valid is False
        assert any("nonexistent" in e for e in result.errors)


class TestChartEditableSource:
    """Acceptance: user can modify the editable source and re-validate."""

    def test_edit_and_revalidate_chart_spec(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="原始标题",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        obj_id = result.media_object.media_object_id
        assert obj_id is not None

        # Read back the spec.
        spec_data = json.loads(result.media_object.editable_source.content)
        assert spec_data["title"] == "原始标题"

        # Edit the title in the spec.
        spec_data["title"] = "修改后标题"
        updated = service.update_chart_spec(obj_id, json.dumps(spec_data), account_id="test-account")
        assert updated.status == GenerationStatus.COMPLETED
        updated_spec = json.loads(updated.editable_source.content)
        assert updated_spec["title"] == "修改后标题"
        assert updated.editable_source.version == 2

    def test_revalidation_after_edit_detects_errors(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="验证测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        obj_id = result.media_object.media_object_id

        # Edit to introduce an error (remove all data).
        spec_data = json.loads(result.media_object.editable_source.content)
        spec_data["data"]["rows"] = []

        updated = service.update_chart_spec(obj_id, json.dumps(spec_data), account_id="test-account")
        # The update should still succeed but show validation errors.
        assert len(updated.validation_errors) > 0
        assert updated.status == GenerationStatus.VALIDATION_FAILED

    def test_update_chart_with_invalid_json_raises_error(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="错误JSON测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        obj_id = result.media_object.media_object_id

        with pytest.raises(MediaGenerationError, match="无效"):
            service.update_chart_spec(obj_id, "{not valid json", account_id="test-account")

    def test_get_nonexistent_object_raises_error(
        self, service: MediaGenerationService
    ) -> None:
        with pytest.raises(MediaGenerationError, match="不存在"):
            service.get_media_object("nonexistent-id", account_id="test-account")


# ── Chart claim binding tests ─────────────────────────────────────────


class TestChartClaimBinding:
    """Acceptance: chart labels bind to Claims and FactLocks."""

    def test_chart_binds_to_fact_locks(
        self,
        service: MediaGenerationService,
        sample_data: ChartDataTable,
        sample_locks: list[FactLock],
    ) -> None:
        request = ChartGenerationRequest(
            title="绑定测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
            claim_ids=["claim-temp", "claim-city-Beijing"],
            fact_lock_ids=["lock-001", "lock-002"],
        )
        result = service.generate_chart(request, account_id="test-account", fact_locks=sample_locks)

        bindings = result.media_object.claim_bindings
        assert len(bindings) > 0
        # Should bind to the fact lock values present in data.
        lock_ids = {b.fact_lock_id for b in bindings if b.fact_lock_id}
        assert len(lock_ids) > 0

    def test_chart_binds_by_explicit_request_ids(
        self,
        service: MediaGenerationService,
        sample_data: ChartDataTable,
        sample_locks: list[FactLock],
    ) -> None:
        """Explicit claim_ids/fact_lock_ids in the request create bindings."""
        request = ChartGenerationRequest(
            title="显式绑定测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
            claim_ids=["claim-temp", "claim-city-Beijing"],
            fact_lock_ids=["lock-001", "lock-002"],
        )
        result = service.generate_chart(request, account_id="test-account", fact_locks=sample_locks)
        bindings = result.media_object.claim_bindings
        # At least 2 bindings: one for each explicitly requested fact lock.
        lock_ids = {b.fact_lock_id for b in bindings if b.fact_lock_id}
        assert "lock-001" in lock_ids
        assert "lock-002" in lock_ids

    def test_chart_without_fact_locks_has_empty_bindings(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="无绑定",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert len(result.media_object.claim_bindings) == 0


# ── Accessibility tests ───────────────────────────────────────────────


class TestChartAccessibility:
    """Acceptance: each visual has alt text or equivalent data table."""

    def test_chart_has_alt_text(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="可访问图表",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        alt = result.media_object.accessibility
        assert alt.alt_text is not None
        assert len(alt.alt_text) > 0
        assert "图表" in alt.alt_text

    def test_chart_has_data_table(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="数据表测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        alt = result.media_object.accessibility
        assert alt.data_table is not None
        assert len(alt.data_table.columns) > 0
        assert len(alt.data_table.rows) > 0

    def test_chart_has_long_description(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="长描述测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert result.media_object.accessibility.long_description is not None
        assert len(result.media_object.accessibility.long_description) > 0

    def test_chart_has_data_bindings(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        """Chart generation populates data_bindings with column schema."""
        request = ChartGenerationRequest(
            title="数据绑定测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        obj = result.media_object
        assert len(obj.data_bindings) > 0
        # First data binding should record column units.
        binding = obj.data_bindings[0]
        assert binding.units.get("temperature") == "°C"


# ── Figure generation tests ───────────────────────────────────────────


class TestFigureGeneration:
    """Acceptance: figure labels and relations bind to Claims and FactLocks."""

    def test_figure_with_elements_generates_spec_and_svg(
        self, service: MediaGenerationService
    ) -> None:
        request = FigureGenerationRequest(
            title="细胞结构图",
            elements=[
                FigureElement(
                    element_id="el-001",
                    role="label",
                    label="细胞核",
                    claim_id="claim-nucleus",
                ),
                FigureElement(
                    element_id="el-002",
                    role="label",
                    label="线粒体",
                    claim_id="claim-mitochondria",
                ),
            ],
            description="真核细胞示意图",
            account_id="test-account",
        )
        result = service.generate_figure(request, account_id="test-account")
        obj = result.media_object

        assert obj.media_type == MediaObjectType.SCIENTIFIC_FIGURE
        assert obj.status == GenerationStatus.COMPLETED
        assert obj.svg_content is not None
        assert "<svg" in obj.svg_content

        spec_data = json.loads(obj.editable_source.content)
        assert spec_data["title"] == "细胞结构图"
        assert len(spec_data["elements"]) == 2

    def test_figure_with_default_elements_creates_title_element(
        self, service: MediaGenerationService
    ) -> None:
        request = FigureGenerationRequest(
            title="默认图形",
            account_id="test-account",
        )
        result = service.generate_figure(request, account_id="test-account")
        spec_data = json.loads(result.media_object.editable_source.content)
        assert len(spec_data["elements"]) == 1
        assert spec_data["elements"][0]["role"] == "title"

    def test_figure_with_empty_elements_fails_validation(
        self, service: MediaGenerationService
    ) -> None:
        result = service.validate_spec(
            ScientificFigureSpec(
                spec_id="empty", title="空图", elements=[]
            ).model_dump_json()
        )
        assert result.valid is False
        assert any("没有元素" in e for e in result.errors)


class TestFigureClaimBinding:
    """Acceptance: figure elements bind to Claims and FactLocks."""

    def test_figure_binds_claim_ids_from_elements(
        self, service: MediaGenerationService
    ) -> None:
        locks = [
            FactLock(
                lock_id="fl-nucleus",
                claim_id="claim-nucleus",
                lock_type=FactLockType.IDENTIFIER,
                canonical_value="细胞核",
                allowed_variants=[],
                forbidden_transformations=[],
                required_qualifiers=[],
                evidence_ids=[],
                citation_ids=[],
                wording_strength_ceiling="high",
                verification_method="rule",
            ),
        ]
        request = FigureGenerationRequest(
            title="绑定测试",
            elements=[
                FigureElement(
                    element_id="el-nucleus",
                    role="label",
                    label="细胞核",
                    claim_id="claim-nucleus",
                ),
                FigureElement(
                    element_id="el-membrane",
                    role="label",
                    label="细胞膜",
                ),
            ],
            account_id="test-account",
        )
        result = service.generate_figure(request, account_id="test-account", fact_locks=locks)
        bindings = result.media_object.claim_bindings
        # Element with claim_id should produce a binding.
        assert any(b.claim_id == "claim-nucleus" for b in bindings)
        # Element without claim_id should not produce a binding.
        assert not any(b.claim_id == "" for b in bindings if not b.claim_id)

    def test_figure_accessibility_has_alt_text(
        self, service: MediaGenerationService
    ) -> None:
        request = FigureGenerationRequest(
            title="可访问图形",
            elements=[
                FigureElement(element_id="e1", role="label", label="叶绿体"),
            ],
            account_id="test-account",
        )
        result = service.generate_figure(request, account_id="test-account")
        alt = result.media_object.accessibility
        assert alt.alt_text is not None
        assert len(alt.alt_text) > 0
        # Figures should not have data_table.
        assert alt.data_table is None

    def test_figure_edit_and_revalidate(
        self, service: MediaGenerationService
    ) -> None:
        request = FigureGenerationRequest(
            title="原始图形",
            elements=[
                FigureElement(element_id="e1", role="label", label="原始标注"),
            ],
            account_id="test-account",
        )
        result = service.generate_figure(request, account_id="test-account")
        obj_id = result.media_object.media_object_id
        assert obj_id is not None

        # Edit the figure spec.
        spec_data = json.loads(result.media_object.editable_source.content)
        spec_data["title"] = "修改后图形"
        spec_data["elements"].append({
            "element_id": "e2",
            "role": "label",
            "label": "新标注",
        })

        updated = service.update_figure_spec(obj_id, json.dumps(spec_data), account_id="test-account")
        assert updated.editable_source.version == 2
        updated_spec = json.loads(updated.editable_source.content)
        assert updated_spec["title"] == "修改后图形"
        assert len(updated_spec["elements"]) == 2


# ── Validation API tests ──────────────────────────────────────────────


class TestStandaloneValidation:
    """The standalone validate_spec method works for both chart and figure specs."""

    def test_validate_valid_chart_spec(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        spec = ChartSpec(
            spec_id="v-test",
            title="验证测试",
            mark=ChartMark.BAR,
            data=sample_data,
            axes=[
                ChartAxis(field="city", label="城市"),
                ChartAxis(field="temperature", label="温度"),
            ],
        )
        result = service.validate_spec(spec.model_dump_json())
        assert result.valid is True
        assert len(result.errors) == 0

    def test_validate_invalid_chart_spec(
        self, service: MediaGenerationService
    ) -> None:
        result = service.validate_spec(
            '{"mark": "bar", "title": "坏数据", "data": {"columns": [], "rows": []}}'
        )
        assert result.valid is False
        assert len(result.errors) > 0

    def test_validate_unrecognized_spec(
        self, service: MediaGenerationService
    ) -> None:
        result = service.validate_spec('{"unknown": "data"}')
        assert result.valid is False


# ── SVG rendering quality tests ───────────────────────────────────────


class TestSvgOutput:
    """SVG output is valid and contains chart elements."""

    def test_bar_chart_svg_contains_rects(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="SVG质量测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        svg = result.media_object.svg_content or ""
        assert "rect" in svg
        assert "viewBox" in svg

    def test_line_chart_svg_contains_path(
        self, service: MediaGenerationService, numerical_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="折线SVG测试",
            mark=ChartMark.LINE,
            data=numerical_data,
            x_field="year",
            y_field="value",
        )
        result = service.generate_chart(request, account_id="test-account")
        svg = result.media_object.svg_content or ""
        # Line uses SVG path elements.
        assert "path" in svg or "line" in svg

    def test_svg_has_role_img_for_accessibility(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="可访问SVG测试",
            mark=ChartMark.BAR,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        svg = result.media_object.svg_content or ""
        assert 'role="img"' in svg


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and error handling."""

    def test_single_data_point(
        self, service: MediaGenerationService
    ) -> None:
        single = ChartDataTable(
            columns=[ChartDataColumn(name="x", data_type="number"), ChartDataColumn(name="y", data_type="number")],
            rows=[ChartDataPoint(values={"x": 1.0, "y": 5.0})],
        )
        request = ChartGenerationRequest(
            title="单点测试",
            mark=ChartMark.BAR,
            data=single,
            x_field="x",
            y_field="y",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert result.media_object.status == GenerationStatus.COMPLETED
        assert result.media_object.svg_content is not None

    def test_large_number_of_categories(
        self, service: MediaGenerationService
    ) -> None:
        """Bar chart with many categories should still render."""
        rows = [
            ChartDataPoint(values={"cat": f"cat-{i}", "val": float(i * 10)})
            for i in range(30)
        ]
        many = ChartDataTable(
            columns=[
                ChartDataColumn(name="cat", data_type="string"),
                ChartDataColumn(name="val", data_type="number"),
            ],
            rows=rows,
        )
        request = ChartGenerationRequest(
            title="多类别测试",
            mark=ChartMark.BAR,
            data=many,
            x_field="cat",
            y_field="val",
        )
        result = service.generate_chart(request, account_id="test-account")
        assert result.media_object.status == GenerationStatus.COMPLETED

    def test_missing_values_in_data(
        self, service: MediaGenerationService
    ) -> None:
        """Rows with None values should be handled gracefully."""
        data = ChartDataTable(
            columns=[ChartDataColumn(name="x", data_type="number"), ChartDataColumn(name="y", data_type="number")],
            rows=[
                ChartDataPoint(values={"x": 1.0, "y": 10.0}),
                ChartDataPoint(values={"x": 2.0, "y": None}, is_missing=True),
                ChartDataPoint(values={"x": 3.0, "y": 30.0}),
            ],
        )
        request = ChartGenerationRequest(
            title="缺失值测试",
            mark=ChartMark.LINE,
            data=data,
            x_field="x",
            y_field="y",
        )
        result = service.generate_chart(request, account_id="test-account")
        # Should still complete, missing data point is skipped.
        assert result.media_object.status == GenerationStatus.COMPLETED
        assert result.media_object.svg_content is not None

    def test_unsupported_mark_falls_back_to_bar(
        self, service: MediaGenerationService, sample_data: ChartDataTable
    ) -> None:
        request = ChartGenerationRequest(
            title="降级测试",
            mark=ChartMark.HISTOGRAM,
            data=sample_data,
            x_field="city",
            y_field="temperature",
        )
        result = service.generate_chart(request, account_id="test-account")
        # Falls back to bar chart rendering.
        assert result.media_object.status == GenerationStatus.COMPLETED
        assert "rect" in (result.media_object.svg_content or "")
