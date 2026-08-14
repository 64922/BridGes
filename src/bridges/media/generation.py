"""Scientific media generation service for T032.

Generates editable static scientific figures and data charts from source data
and claim bindings. The service produces:

- Declarative chart/figure specs (editable source)
- Rendered SVG output (derived)
- Claim bindings linking visual elements to claims and fact locks
- Accessibility alternatives (alt text, data tables)

The service works deterministically for testability and can optionally use a
ModelGateway for intelligent spec generation from natural language descriptions.
"""

from __future__ import annotations

import json
import math
import secrets
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, Protocol

from bridges.ai import ModelGateway
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
    DataBinding,
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
from bridges.contracts.science import FactLock


class MediaGenerationError(Exception):
    """Domain error for media generation failures."""


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_id() -> str:
    return secrets.token_urlsafe(16)


# ── Chart spec generation ──────────────────────────────────────────────


CHART_MARGIN = {"top": 40, "right": 20, "bottom": 50, "left": 60}
"""Standard chart margin used by all SVG renderers."""


def _pick_label(field: str, provided: str | None, unit: str | None) -> str:
    if provided:
        return provided
    if unit:
        return f"{field} ({unit})"
    return field


def _compute_domain(
    data: ChartDataTable, field: str, axis_type: ChartAxisType,
) -> tuple[float, float]:
    values: list[float] = []
    for row in data.rows:
        val = row.values.get(field)
        if val is not None and isinstance(val, (int, float)):
            values.append(float(val))
    if not values:
        return 0.0, 100.0
    mn = min(values)
    mx = max(values)
    if axis_type == ChartAxisType.LINEAR:
        margin = max(abs(mx - mn) * 0.1, 1.0) if mx != mn else 10.0
        return math.floor(mn - margin), math.ceil(mx + margin)
    if mn == mx:
        return 0.0, mx * 2 or 10.0
    return mn, mx


def _chart_data_alt_text(spec: ChartSpec) -> str:
    """Generate a concise alt text for a chart."""
    n_rows = len(spec.data.rows)
    x_axis = spec.axes[0] if spec.axes else None
    y_axis = spec.axes[1] if len(spec.axes) > 1 else None
    x_label = x_axis.label if x_axis else "?"
    y_label = y_axis.label if y_axis else "?"
    return (
        f"图表：{spec.title}。"
        f"{spec.mark.value}图，{n_rows} 个数据点，"
        f"x 轴：{x_label}，y 轴：{y_label}。"
    )


def _build_chart_spec(request: ChartGenerationRequest) -> ChartSpec:
    """Build a ChartSpec from a generation request.

    This is the deterministic core: given structured data and field bindings,
    it produces a complete spec with auto-computed domains and labels.
    """
    spec_id = _generate_id()
    x_label = _pick_label(request.x_field, request.x_label, request.x_unit)
    y_label = _pick_label(request.y_field, request.y_label, request.y_unit)

    x_type = ChartAxisType.CATEGORICAL
    for row in request.data.rows:
        val = row.values.get(request.x_field)
        if isinstance(val, (int, float)):
            x_type = ChartAxisType.LINEAR
            break

    y_type = ChartAxisType.LINEAR
    for row in request.data.rows:
        val = row.values.get(request.y_field)
        if isinstance(val, (int, float)):
            y_type = ChartAxisType.LINEAR
            break

    y_min, y_max = _compute_domain(request.data, request.y_field, y_type)
    x_min, x_max = None, None
    if x_type == ChartAxisType.LINEAR:
        x_min, x_max = _compute_domain(request.data, request.x_field, x_type)

    axes: list[ChartAxis] = [
        ChartAxis(
            field=request.x_field,
            label=x_label,
            unit=request.x_unit,
            axis_type=x_type,
            domain_min=x_min,
            domain_max=x_max,
        ),
        ChartAxis(
            field=request.y_field,
            label=y_label,
            unit=request.y_unit,
            axis_type=y_type,
            domain_min=y_min,
            domain_max=y_max,
        ),
    ]

    encodings: list[ChartEncodingMapping] = [
        ChartEncodingMapping(encoding="x", field=request.x_field, label=x_label),
        ChartEncodingMapping(encoding="y", field=request.y_field, label=y_label),
    ]
    if request.color_field:
        encodings.append(
            ChartEncodingMapping(encoding="color", field=request.color_field)
        )
    if request.error_field:
        encodings.append(
            ChartEncodingMapping(encoding="y_error", field=request.error_field)
        )

    return ChartSpec(
        spec_id=spec_id,
        title=request.title,
        mark=request.mark,
        data=request.data,
        axes=axes,
        encodings=encodings,
        has_error_bars=request.error_field is not None,
        aggregation=request.aggregation,
        color_legend_title=request.color_field,
    )


# ── SVG Rendering ─────────────────────────────────────────────────────


def _svg_root(width: int, height: int) -> tuple[ET.Element, ET.Element]:
    """Create an SVG root element with standard attributes."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ns = "http://www.w3.org/2000/svg"
    root = ET.Element(f"{{{ns}}}svg")
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("viewBox", f"0 0 {width} {height}")
    root.set("xmlns", ns)
    root.set("role", "img")
    # Defs for standard elements.
    defs = ET.SubElement(root, f"{{{ns}}}defs")
    style = ET.SubElement(defs, f"{{{ns}}}style")
    style.text = (
        "text { font-family: 'Noto Sans', Arial, sans-serif; font-size: 12px; "
        "fill: #333; }\n"
        ".title { font-size: 14px; font-weight: bold; fill: #111; }\n"
        ".axis-label { font-size: 11px; fill: #444; }\n"
        ".tick { stroke: #ccc; stroke-width: 1; }\n"
        ".bar { fill: #4a90d9; }\n"
        ".line { fill: none; stroke: #4a90d9; stroke-width: 2; }\n"
        ".point { fill: #4a90d9; }\n"
        ".error-bar { stroke: #666; stroke-width: 1; }\n"
        ".grid { stroke: #eee; stroke-width: 1; }\n"
        ".legend-text { font-size: 10px; fill: #555; }\n"
    )
    return root, defs


def _ns(tag: str) -> str:
    return f"{{http://www.w3.org/2000/svg}}{tag}"


def _add_text(
    parent: ET.Element, x: float, y: float, text: str,
    css_class: str = "",
    anchor: str = "middle",
) -> None:
    el = ET.SubElement(parent, _ns("text"))
    el.set("x", str(x))
    el.set("y", str(y))
    el.set("text-anchor", anchor)
    if css_class:
        el.set("class", css_class)
    el.text = text


def _render_bar_chart(spec: ChartSpec, width: int, height: int) -> str:
    """Render a bar chart as SVG."""
    root, defs = _svg_root(width, height)
    margin = CHART_MARGIN
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    x_axis = spec.axes[0] if len(spec.axes) > 0 else ChartAxis(field="x", label="X")
    y_axis = spec.axes[1] if len(spec.axes) > 1 else ChartAxis(field="y", label="Y")

    # Group data by x field.
    groups: dict[str, list[float]] = {}
    for row in spec.data.rows:
        x_val = str(row.values.get(x_axis.field, ""))
        y_val = row.values.get(y_axis.field)
        if y_val is not None and isinstance(y_val, (int, float)):
            groups.setdefault(x_val, []).append(float(y_val))

    if not groups:
        _add_text(root, width / 2, height / 2, "无数据", "title")
        return ET.tostring(root, encoding="unicode")

    categories = list(groups.keys())
    if len(categories) > 20:
        categories = categories[:20]

    # Compute bar layout.
    y_min = y_axis.domain_min if y_axis.domain_min is not None else 0.0
    y_max = y_axis.domain_max if y_axis.domain_max is not None else max(
        max(vs) * 1.1 for vs in groups.values() if vs
    ) or 100.0
    if y_max <= y_min:
        y_max = y_min + 100.0
    y_range = y_max - y_min or 1.0

    n = len(categories)
    bar_width = max(8, min(40, plot_w / n * 0.7))
    gap = (plot_w - bar_width * n) / (n + 1)

    # Grid lines.
    n_grid = 5
    for i in range(n_grid + 1):
        y_pix = margin["top"] + plot_h * (1 - i / n_grid)
        line = ET.SubElement(root, _ns("line"))
        line.set("x1", str(margin["left"]))
        line.set("y1", str(y_pix))
        line.set("x2", str(margin["left"] + plot_w))
        line.set("y2", str(y_pix))
        line.set("class", "grid")
        val = y_min + y_range * i / n_grid
        _add_text(
            root, margin["left"] - 5, y_pix + 4,
            f"{val:.1f}" if abs(val) >= 1 else f"{val:.2f}",
            "tick", anchor="end",
        )

    # Title.
    _add_text(root, width / 2, 20, spec.title, "title")

    # Y-axis label.
    _add_text(
        root, 14, margin["top"] + plot_h / 2,
        y_axis.label, "axis-label", anchor="middle",
    )
    _add_text(root, width / 2, height - 8, x_axis.label, "axis-label", anchor="middle")

    # Bars.
    errors: dict[str, float] = {}
    if spec.has_error_bars and len(spec.encodings) > 2:
        err_field = spec.encodings[2].field if len(spec.encodings) > 2 else None
        if err_field:
            for row in spec.data.rows:
                xv = str(row.values.get(x_axis.field, ""))
                ev = row.values.get(err_field)
                if ev is not None and isinstance(ev, (int, float)):
                    errors[xv] = float(ev)

    for idx, cat in enumerate(categories):
        vals = groups[cat]
        mean_val = sum(vals) / len(vals) if vals else 0.0
        x0 = margin["left"] + gap + idx * (bar_width + gap)
        y0 = margin["top"] + plot_h - (mean_val - y_min) / y_range * plot_h
        h = max(1, (mean_val - y_min) / y_range * plot_h)

        bar = ET.SubElement(root, _ns("rect"))
        bar.set("x", str(x0))
        bar.set("y", str(y0))
        bar.set("width", str(bar_width))
        bar.set("height", str(h))
        bar.set("class", "bar")

        # X tick label.
        _add_text(
            root, x0 + bar_width / 2, margin["top"] + plot_h + 16,
            cat, anchor="end" if bar_width < 30 else "middle",
        )

        # Error bar.
        if cat in errors:
            err_val = errors[cat]
            ey = margin["top"] + plot_h - (mean_val + err_val - y_min) / y_range * plot_h
            eb = ET.SubElement(root, _ns("line"))
            eb.set("x1", str(x0 + bar_width / 2))
            eb.set("y1", str(ey))
            eb.set("x2", str(x0 + bar_width / 2))
            eb.set("y2", str(y0))
            eb.set("class", "error-bar")

    return ET.tostring(root, encoding="unicode")


def _render_line_chart(spec: ChartSpec, width: int, height: int) -> str:
    """Render a line chart as SVG."""
    root, defs = _svg_root(width, height)
    margin = CHART_MARGIN
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    x_axis = spec.axes[0] if spec.axes else ChartAxis(field="x", label="X")
    y_axis = spec.axes[1] if len(spec.axes) > 1 else ChartAxis(field="y", label="Y")

    # Collect points sorted by x.
    points: list[tuple[float, float]] = []
    for row in spec.data.rows:
        xv = row.values.get(x_axis.field)
        yv = row.values.get(y_axis.field)
        if (xv is not None and isinstance(xv, (int, float))
                and yv is not None and isinstance(yv, (int, float))):
            points.append((float(xv), float(yv)))

    if not points:
        _add_text(root, width / 2, height / 2, "无数据", "title")
        return ET.tostring(root, encoding="unicode")

    points.sort(key=lambda p: p[0])

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    x_min = x_axis.domain_min if x_axis.domain_min is not None else min(xs)
    x_max = x_axis.domain_max if x_axis.domain_max is not None else max(xs)
    y_min = y_axis.domain_min if y_axis.domain_min is not None else min(ys)
    y_max = y_axis.domain_max if y_axis.domain_max is not None else max(ys)

    if x_max <= x_min:
        x_max = x_min + 10.0
    if y_max <= y_min:
        y_max = y_min + 10.0

    x_range = x_max - x_min or 1.0
    y_range = y_max - y_min or 1.0

    def tx(x: float) -> float:
        return margin["left"] + (x - x_min) / x_range * plot_w

    def ty(y: float) -> float:
        return margin["top"] + plot_h - (y - y_min) / y_range * plot_h

    # Grid.
    n_grid = 5
    for i in range(n_grid + 1):
        y_pix = margin["top"] + plot_h * (1 - i / n_grid)
        g = ET.SubElement(root, _ns("line"))
        g.set("x1", str(margin["left"]))
        g.set("y1", str(y_pix))
        g.set("x2", str(margin["left"] + plot_w))
        g.set("y2", str(y_pix))
        g.set("class", "grid")
        val = y_min + y_range * i / n_grid
        _add_text(root, margin["left"] - 5, y_pix + 4,
                  f"{val:.1f}", "tick", anchor="end")

    # Title.
    _add_text(root, width / 2, 20, spec.title, "title")
    _add_text(root, 14, margin["top"] + plot_h / 2, y_axis.label, "axis-label", anchor="middle")
    _add_text(root, width / 2, height - 8, x_axis.label, "axis-label", anchor="middle")

    # X ticks.
    for p in points:
        _add_text(root, tx(p[0]), margin["top"] + plot_h + 16,
                  f"{p[0]:.1f}" if isinstance(p[0], float) and p[0] != int(p[0]) else str(int(p[0])),
                  anchor="middle")

    # Line.
    path_d = " ".join(
        f"{'M' if i == 0 else 'L'}{tx(x):.1f},{ty(y):.1f}"
        for i, (x, y) in enumerate(points)
    )
    line = ET.SubElement(root, _ns("path"))
    line.set("d", path_d)
    line.set("class", "line")

    # Points.
    for x, y in points:
        circ = ET.SubElement(root, _ns("circle"))
        circ.set("cx", f"{tx(x):.1f}")
        circ.set("cy", f"{ty(y):.1f}")
        circ.set("r", "3")
        circ.set("class", "point")

    return ET.tostring(root, encoding="unicode")


def _render_scatter_chart(spec: ChartSpec, width: int, height: int) -> str:
    """Render a scatter chart as SVG (same as line but no connecting line)."""
    root, defs = _svg_root(width, height)
    margin = CHART_MARGIN
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    x_axis = spec.axes[0] if spec.axes else ChartAxis(field="x", label="X")
    y_axis = spec.axes[1] if len(spec.axes) > 1 else ChartAxis(field="y", label="Y")

    points: list[tuple[float, float, str | None]] = []
    for row in spec.data.rows:
        xv = row.values.get(x_axis.field)
        yv = row.values.get(y_axis.field)
        color_val = None
        for enc in spec.encodings:
            if enc.encoding == "color":
                cv = row.values.get(enc.field)
                color_val = str(cv) if cv is not None else None
                break
        if (xv is not None and isinstance(xv, (int, float))
                and yv is not None and isinstance(yv, (int, float))):
            points.append((float(xv), float(yv), color_val))

    if not points:
        _add_text(root, width / 2, height / 2, "无数据", "title")
        return ET.tostring(root, encoding="unicode")

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max <= x_min:
        x_max = x_min + 10.0
    if y_max <= y_min:
        y_max = y_min + 10.0
    x_range = x_max - x_min or 1.0
    y_range = y_max - y_min or 1.0

    def tx(x: float) -> float:
        return margin["left"] + (x - x_min) / x_range * plot_w

    def ty(y: float) -> float:
        return margin["top"] + plot_h - (y - y_min) / y_range * plot_h

    # Grid.
    n_grid = 5
    for i in range(n_grid + 1):
        y_pix = margin["top"] + plot_h * (1 - i / n_grid)
        g = ET.SubElement(root, _ns("line"))
        g.set("x1", str(margin["left"]))
        g.set("y1", str(y_pix))
        g.set("x2", str(margin["left"] + plot_w))
        g.set("y2", str(y_pix))
        g.set("class", "grid")
        val = y_min + y_range * i / n_grid
        _add_text(root, margin["left"] - 5, y_pix + 4, f"{val:.1f}", "tick", anchor="end")

    _add_text(root, width / 2, 20, spec.title, "title")
    _add_text(root, 14, margin["top"] + plot_h / 2, y_axis.label, "axis-label", anchor="middle")
    _add_text(root, width / 2, height - 8, x_axis.label, "axis-label", anchor="middle")

    colors = ["#4a90d9", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6"]
    color_idx: dict[str, int] = {}

    for x, y, color_val in points:
        circ = ET.SubElement(root, _ns("circle"))
        circ.set("cx", f"{tx(x):.2f}")
        circ.set("cy", f"{ty(y):.2f}")
        circ.set("r", "4")
        if color_val is not None:
            if color_val not in color_idx:
                color_idx[color_val] = len(color_idx)
            ci = color_idx[color_val] % len(colors)
            circ.set("fill", colors[ci])
            circ.set("stroke", colors[ci])
        else:
            circ.set("class", "point")

    return ET.tostring(root, encoding="unicode")


def _render_chart_svg(spec: ChartSpec) -> str:
    """Render a ChartSpec to SVG based on the mark type."""
    width, height = 600, 400

    if spec.mark == ChartMark.BAR:
        return _render_bar_chart(spec, width, height)
    if spec.mark == ChartMark.LINE:
        return _render_line_chart(spec, width, height)
    if spec.mark in (ChartMark.SCATTER, ChartMark.POINT):
        return _render_scatter_chart(spec, width, height)
    if spec.mark in (ChartMark.AREA, ChartMark.ERROR_BAR):
        return _render_line_chart(spec, width, height)

    return _render_bar_chart(spec, width, height)


# ── Figure spec generation ─────────────────────────────────────────────


def _build_figure_spec(request: FigureGenerationRequest) -> ScientificFigureSpec:
    """Build a ScientificFigureSpec from a generation request.

    When no elements are provided, creates a minimal default figure with a
    centered title label.
    """
    spec_id = _generate_id()
    elements = list(request.elements) if request.elements else [
        FigureElement(
            element_id=_generate_id(),
            role="title",
            label=request.title,
        ),
    ]
    return ScientificFigureSpec(
        spec_id=spec_id,
        title=request.title,
        elements=elements,
        description=request.description or f"科学示意图：{request.title}。",
    )


def _render_figure_svg(spec: ScientificFigureSpec) -> str:
    """Render a ScientificFigureSpec to SVG."""
    root, defs = _svg_root(600, 400)
    _add_text(root, 300, 25, spec.title, "title")

    y_pos = 60
    for el in spec.elements:
        if el.svg_fragment:
            # Append the raw SVG fragment.
            try:
                fragment_root = ET.fromstring(el.svg_fragment)
                root.append(fragment_root)
            except ET.ParseError:
                _add_text(root, 30, y_pos, f"{el.label} (SVG片段解析失败)", anchor="start")
        else:
            label = f"[{el.role}] {el.label}"
            if el.claim_id:
                label += f" — Claim: {el.claim_id[:8]}…"
            _add_text(root, 30, y_pos, label, "axis-label", anchor="start")
        y_pos += 24

    if spec.description:
        _add_text(root, 300, 380, spec.description[:80], "legend-text")

    return ET.tostring(root, encoding="unicode")


# ── Validation ─────────────────────────────────────────────────────────


def _validate_chart_spec(
    spec: ChartSpec,
    fact_locks: list[FactLock] | None = None,
) -> SpecValidationResult:
    """Validate a chart spec against its data and optional fact locks."""
    errors: list[str] = []
    warnings: list[str] = []

    if not spec.data.columns:
        errors.append("数据列定义为空。")
    if not spec.data.rows:
        errors.append("数据行为空。")
    if not spec.axes:
        errors.append("至少需要一个轴。")

    # Check that all axes reference valid data fields.
    for axis in spec.axes:
        col_names = {c.name for c in spec.data.columns}
        if axis.field not in col_names:
            errors.append(f"轴字段 '{axis.field}' 在数据中不存在。")
        else:
            # Check for missing values in this field.
            missing_count = sum(
                1 for row in spec.data.rows
                if row.values.get(axis.field) is None
            )
            if missing_count > 0:
                warnings.append(
                    f"字段 '{axis.field}' 有 {missing_count} 个缺失值。"
                )

    # Check domain validity.
    for axis in spec.axes:
        if axis.axis_type == ChartAxisType.LINEAR:
            if axis.domain_min is not None and axis.domain_max is not None:
                if axis.domain_min >= axis.domain_max:
                    errors.append(f"轴 '{axis.field}' 的域范围无效：最小值≥最大值。")

    # Check data values consistency against fact locks.
    if fact_locks:
        for lock in fact_locks:
            if lock.lock_type in ("exact_value", "identifier"):
                # Check if any data field matches the lock's canonical value.
                matched = False
                for row in spec.data.rows:
                    for val in row.values.values():
                        if val is not None and str(val) == lock.canonical_value:
                            matched = True
                            break
                if not matched:
                    warnings.append(
                        f"事实锁 '{lock.lock_id[:8]}…' 的值 '{lock.canonical_value}' "
                        "在数据中未找到。"
                    )

    valid = len(errors) == 0
    return SpecValidationResult(
        valid=valid,
        errors=errors,
        warnings=warnings,
        data_consistent=len([e for e in errors if "数据" in e]) == 0,
        claims_consistent=len([e for e in errors if "事实锁" in e]) == 0,
    )


def _validate_figure_spec(
    spec: ScientificFigureSpec,
    fact_locks: list[FactLock] | None = None,
) -> SpecValidationResult:
    """Validate a figure spec."""
    errors: list[str] = []
    warnings: list[str] = []

    if not spec.elements:
        errors.append("图中没有元素。")

    seen_ids: set[str] = set()
    for el in spec.elements:
        if el.element_id in seen_ids:
            errors.append(f"元素ID重复: {el.element_id}。")
        seen_ids.add(el.element_id)
        if not el.label.strip():
            errors.append(f"元素 '{el.element_id}' 缺少标签。")

    if fact_locks:
        bound_ids = {el.claim_id for el in spec.elements if el.claim_id}
        lock_claim_ids = {lock.claim_id for lock in fact_locks}
        for bid in bound_ids:
            if bid and bid not in lock_claim_ids:
                warnings.append(
                    f"元素绑定的 Claim '{bid[:8]}…' 不在提供的事实锁集中。"
                )

    valid = len(errors) == 0
    return SpecValidationResult(
        valid=valid,
        errors=errors,
        warnings=warnings,
        data_consistent=True,
        claims_consistent=len(errors) == 0,
    )


# ── Accessibility generation ──────────────────────────────────────────


def _build_chart_accessibility(spec: ChartSpec) -> AccessibilityAlternative:
    """Build an accessibility alternative for a chart."""
    alt = _chart_data_alt_text(spec)
    desc_lines: list[str] = [f"标题：{spec.title}"]
    for axis in spec.axes:
        unit = f" ({axis.unit})" if axis.unit else ""
        desc_lines.append(f"{axis.label}: {axis.field}{unit}")
    desc_lines.append(f"数据点数：{len(spec.data.rows)}")
    for row in spec.data.rows[:20]:
        pairs = [f"{k}={v}" for k, v in row.values.items() if v is not None]
        desc_lines.append("  " + ", ".join(pairs))
    if len(spec.data.rows) > 20:
        desc_lines.append(f"… 共 {len(spec.data.rows)} 行")

    return AccessibilityAlternative(
        alt_text=alt,
        long_description="\n".join(desc_lines),
        data_table=spec.data,
    )


def _build_figure_accessibility(spec: ScientificFigureSpec) -> AccessibilityAlternative:
    """Build an accessibility alternative for a figure."""
    alt = f"科学示意图：{spec.title}。包含 {len(spec.elements)} 个标注元素。"
    desc_lines = [f"标题：{spec.title}"]
    for el in spec.elements:
        claim = f" [绑定: {el.claim_id[:8]}…]" if el.claim_id else ""
        desc_lines.append(f"  [{el.role}] {el.label}{claim}")

    return AccessibilityAlternative(
        alt_text=alt,
        long_description="\n".join(desc_lines),
        data_table=None,
    )


# ── Main service ───────────────────────────────────────────────────────


class SpecGenerator(Protocol):
    """Protocol for spec generation strategies (stub or model-backed)."""

    def generate_chart_spec(self, request: ChartGenerationRequest) -> ChartSpec:
        ...

    def generate_figure_spec(self, request: FigureGenerationRequest) -> ScientificFigureSpec:
        ...


class DeterministicSpecGenerator:
    """Deterministic spec generator used for testing and default operation."""

    def generate_chart_spec(self, request: ChartGenerationRequest) -> ChartSpec:
        return _build_chart_spec(request)

    def generate_figure_spec(self, request: FigureGenerationRequest) -> ScientificFigureSpec:
        return _build_figure_spec(request)


class MediaGenerationService:
    """Service for generating editable static scientific figures and data charts.

    The service follows a deterministic-by-default pattern: chart/figure specs
    are built from structured input data and rendered to SVG without any model
    dependency. A ModelGateway can be injected for intelligent generation from
    natural language descriptions.

    Generation flow:
    1. Build or generate the spec (chart or figure)
    2. Validate the spec against source data and fact locks
    3. Render SVG from the spec
    4. Build accessibility alternative
    5. Create claim bindings
    6. Return the completed ScientificMediaObject
    """

    def __init__(
        self,
        model_gateway: ModelGateway | None = None,
        spec_generator: SpecGenerator | None = None,
    ) -> None:
        self._model_gateway = model_gateway
        self._spec_generator = spec_generator
        self._media_objects: dict[str, ScientificMediaObject] = {}

    def _require_spec_generator(self) -> SpecGenerator:
        if self._spec_generator is None:
            raise MediaGenerationError(
                "媒体生成器已退役：生产组合不再实例化确定性图表/图形生成器。"
            )
        return self._spec_generator

    def generate_chart(
        self,
        request: ChartGenerationRequest,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> GenerationResult:
        """Generate a chart from structured data and field mappings."""
        spec = self._require_spec_generator().generate_chart_spec(request)
        return self._finalize_chart(
            spec, account_id, request.project_id, fact_locks,
            request_claim_ids=request.claim_ids,
            request_fact_lock_ids=request.fact_lock_ids,
        )

    def generate_figure(
        self,
        request: FigureGenerationRequest,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> GenerationResult:
        """Generate a scientific figure from element definitions."""
        spec = self._require_spec_generator().generate_figure_spec(request)
        return self._finalize_figure(
            spec, account_id, request.project_id, fact_locks,
            request_claim_ids=request.claim_ids,
            request_fact_lock_ids=request.fact_lock_ids,
        )

    def _finalize_chart(
        self,
        spec: ChartSpec,
        account_id: str,
        project_id: str | None,
        fact_locks: list[FactLock] | None,
        request_claim_ids: list[str] | None = None,
        request_fact_lock_ids: list[str] | None = None,
    ) -> GenerationResult:
        validate_result = _validate_chart_spec(spec, fact_locks=fact_locks)
        svg = _render_chart_svg(spec)
        accessibility = _build_chart_accessibility(spec)

        media_object_id = _generate_id()
        now = _now()

        # Build claim bindings: first by explicit request IDs, then by content matching.
        claim_bindings: list[ClaimVisualBinding] = []
        if fact_locks:
            requested_fact_lock_ids = set(request_fact_lock_ids or [])
            requested_claim_ids_set = set(request_claim_ids or [])

            for lock in fact_locks:
                is_explicit = (
                    lock.lock_id in requested_fact_lock_ids
                    or lock.claim_id in requested_claim_ids_set
                )

                # Find which axis (if any) has data matching this lock's value.
                matched_axis: ChartAxis | None = None
                for axis in spec.axes:
                    if lock.canonical_value and any(
                        str(row.values.get(axis.field, "")) == lock.canonical_value
                        for row in spec.data.rows
                    ):
                        matched_axis = axis
                        break

                if is_explicit or matched_axis:
                    element_ref = (
                        f"axis:{matched_axis.field}"
                        if matched_axis
                        else f"axis:{spec.axes[0].field if spec.axes else 'unknown'}"
                    )
                    claim_bindings.append(
                        ClaimVisualBinding(
                            binding_id=_generate_id(),
                            element_ref=element_ref,
                            claim_id=lock.claim_id,
                            fact_lock_id=lock.lock_id,
                        )
                    )

        # Build data bindings from column schema.
        data_bindings: list[DataBinding] = []
        if spec.data.columns:
            data_bindings.append(
                DataBinding(
                    binding_id=_generate_id(),
                    derived_asset_id="",
                    source_data_ref=spec.data.source_note,
                    transformation=spec.aggregation,
                    units={c.name: c.unit for c in spec.data.columns},
                    missing_value_policy="explicit",
                )
            )

        media_object = ScientificMediaObject(
            media_object_id=media_object_id,
            account_id=account_id,
            project_id=project_id,
            media_type=MediaObjectType.CHART,
            editable_source=EditableSource(
                source_id=_generate_id(),
                source_type="chart_spec",
                content=spec.model_dump_json(indent=2),
                format="application/json",
                version=1,
            ),
            svg_content=svg,
            claim_bindings=claim_bindings,
            data_bindings=data_bindings,
            fact_lock_set_id=None,
            accessibility=accessibility,
            validation_errors=list(validate_result.errors),
            status=(
                GenerationStatus.COMPLETED
                if validate_result.valid
                else GenerationStatus.VALIDATION_FAILED
            ),
            created_at=now,
            updated_at=now,
        )

        self._media_objects[media_object_id] = media_object
        return GenerationResult(media_object=media_object)

    def _finalize_figure(
        self,
        spec: ScientificFigureSpec,
        account_id: str,
        project_id: str | None,
        fact_locks: list[FactLock] | None,
        request_claim_ids: list[str] | None = None,
        request_fact_lock_ids: list[str] | None = None,
    ) -> GenerationResult:
        validate_result = _validate_figure_spec(spec, fact_locks=fact_locks)
        svg = _render_figure_svg(spec)
        accessibility = _build_figure_accessibility(spec)

        media_object_id = _generate_id()
        now = _now()

        claim_bindings: list[ClaimVisualBinding] = []
        if fact_locks:
            requested_fact_lock_ids = set(request_fact_lock_ids or [])
            requested_claim_ids_set = set(request_claim_ids or [])

            for el in spec.elements:
                if el.claim_id:
                    for lock in fact_locks:
                        # Match by explicit request or by element claim_id.
                        if (
                            lock.claim_id == el.claim_id
                            or lock.claim_id in requested_claim_ids_set
                            or lock.lock_id in requested_fact_lock_ids
                        ):
                            claim_bindings.append(
                                ClaimVisualBinding(
                                    binding_id=_generate_id(),
                                    element_ref=f"element:{el.element_id}",
                                    claim_id=el.claim_id,
                                    fact_lock_id=lock.lock_id,
                                )
                            )

        media_object = ScientificMediaObject(
            media_object_id=media_object_id,
            account_id=account_id,
            project_id=project_id,
            media_type=MediaObjectType.SCIENTIFIC_FIGURE,
            editable_source=EditableSource(
                source_id=_generate_id(),
                source_type="figure_spec",
                content=spec.model_dump_json(indent=2),
                format="application/json",
                version=1,
            ),
            svg_content=svg,
            claim_bindings=claim_bindings,
            data_bindings=[],
            fact_lock_set_id=None,
            accessibility=accessibility,
            validation_errors=list(validate_result.errors),
            status=(
                GenerationStatus.COMPLETED
                if validate_result.valid
                else GenerationStatus.VALIDATION_FAILED
            ),
            created_at=now,
            updated_at=now,
        )

        self._media_objects[media_object_id] = media_object
        return GenerationResult(media_object=media_object)

    def get_media_object(
        self, media_object_id: str, *, account_id: str
    ) -> ScientificMediaObject:
        """Retrieve a generated media object by ID (owner-scoped, Issue 39 AC9).

        直接对象标识不能作为授权依据：跨账户猜测标识一律按不存在处理，
        不泄漏对象是否存在或属于哪个账户。
        """
        obj = self._media_objects.get(media_object_id)
        if obj is None or obj.account_id != account_id:
            raise MediaGenerationError(f"媒体对象 {media_object_id} 不存在。")
        return obj

    def update_chart_spec(
        self,
        media_object_id: str,
        spec_json: str,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> ScientificMediaObject:
        """Update the editable source of a chart and re-validate.

        Users can modify the spec JSON directly and submit it for re-validation.
        The service re-renders the SVG and re-checks consistency.
        """
        obj = self.get_media_object(media_object_id, account_id=account_id)
        if obj.media_type != MediaObjectType.CHART:
            raise MediaGenerationError(f"媒体对象不是图表类型。")

        try:
            spec_data = json.loads(spec_json)
            spec = ChartSpec(**spec_data)
        except (json.JSONDecodeError, Exception) as exc:
            raise MediaGenerationError(f"无效的图表规格 JSON: {exc}") from exc

        validate_result = _validate_chart_spec(spec, fact_locks=fact_locks)
        svg = _render_chart_svg(spec)
        accessibility = _build_chart_accessibility(spec)

        now = _now()
        updated = obj.model_copy(update={
            "editable_source": EditableSource(
                source_id=obj.editable_source.source_id,
                source_type="chart_spec",
                content=spec.model_dump_json(indent=2),
                format="application/json",
                version=obj.editable_source.version + 1,
            ),
            "svg_content": svg,
            "accessibility": accessibility,
            "validation_errors": list(validate_result.errors),
            "status": (
                GenerationStatus.COMPLETED
                if validate_result.valid
                else GenerationStatus.VALIDATION_FAILED
            ),
            "updated_at": now,
        })
        self._media_objects[media_object_id] = updated
        return updated

    def update_figure_spec(
        self,
        media_object_id: str,
        figure_json: str,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> ScientificMediaObject:
        """Update the editable source of a figure and re-validate."""
        obj = self.get_media_object(media_object_id, account_id=account_id)
        if obj.media_type != MediaObjectType.SCIENTIFIC_FIGURE:
            raise MediaGenerationError(f"媒体对象不是图形类型。")

        try:
            spec_data = json.loads(figure_json)
            spec = ScientificFigureSpec(**spec_data)
        except (json.JSONDecodeError, Exception) as exc:
            raise MediaGenerationError(f"无效的图形规格 JSON: {exc}") from exc

        validate_result = _validate_figure_spec(spec, fact_locks=fact_locks)
        svg = _render_figure_svg(spec)
        accessibility = _build_figure_accessibility(spec)

        now = _now()
        updated = obj.model_copy(update={
            "editable_source": EditableSource(
                source_id=obj.editable_source.source_id,
                source_type="figure_spec",
                content=spec.model_dump_json(indent=2),
                format="application/json",
                version=obj.editable_source.version + 1,
            ),
            "svg_content": svg,
            "accessibility": accessibility,
            "validation_errors": list(validate_result.errors),
            "status": (
                GenerationStatus.COMPLETED
                if validate_result.valid
                else GenerationStatus.VALIDATION_FAILED
            ),
            "updated_at": now,
        })
        self._media_objects[media_object_id] = updated
        return updated

    def validate_spec(
        self,
        spec_json: str,
        *,
        fact_locks: list[FactLock] | None = None,
    ) -> SpecValidationResult:
        """Validate a spec JSON without generating output.

        This is the re-validation entry point: users edit the spec and call
        this to check consistency before updating.
        """
        try:
            data = json.loads(spec_json)
        except json.JSONDecodeError as exc:
            return SpecValidationResult(
                valid=False,
                errors=[f"JSON 解析失败: {exc}"],
            )

        if "mark" in data:
            try:
                chart_spec = ChartSpec(**data)
            except Exception as exc:
                return SpecValidationResult(
                    valid=False, errors=[f"图表规格无效: {exc}"],
                )
            return _validate_chart_spec(chart_spec, fact_locks=fact_locks)
        if "elements" in data or "spec_id" in data:
            try:
                figure_spec = ScientificFigureSpec(**data)
            except Exception as exc:
                return SpecValidationResult(
                    valid=False, errors=[f"图形规格无效: {exc}"],
                )
            return _validate_figure_spec(figure_spec, fact_locks=fact_locks)

        return SpecValidationResult(
            valid=False,
            errors=["无法识别规格类型：请提供图表或图形规格。"],
        )
