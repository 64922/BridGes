"""工单 42：教学范围映射/核验门的独立量表。

驱动生产 ``StudyScopeNodeFlow`` 与 ``SCOPE_GATE_HANDLERS``，对照固定合成
书页材料评价范围覆盖、同名知识点隔离与不清符号阻断；不替代照片识别、
补拍交互或预习问题生成的真实证据。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from bridges.evaluation.workflow_semantics import SCOPE_FRAGMENTS, fingerprint
from bridges.kernel.contracts import (
    NodeInvocation,
    NodeReceiptStatus,
    QualityGateResult,
    QualityVerdict,
    RecipeInputs,
)
from bridges.study.scope import (
    GATE_SCOPE_CONTENT,
    GATE_SCOPE_STRUCTURE,
    NODE_MAP,
    NODE_VERIFY_SCOPE,
    SCOPE_GATE_HANDLERS,
    ScopeMaterial,
    StudyScopeNodeFlow,
    build_scope_mapping_recipe,
)


def scope_fixture_material() -> ScopeMaterial:
    """冻结两页合成书页材料；量表仅评价映射/核验门，不冒充照片识别。"""
    page_texts = {
        ordinal: " ".join(
            item.text for item in SCOPE_FRAGMENTS if item.page_ordinal == ordinal
        )
        for ordinal in (1, 2)
    }
    pages = [
        SimpleNamespace(
            ordinal=ordinal,
            object_id=f"scope-page-{ordinal}",
            content_hash=fingerprint(text),
        )
        for ordinal, text in page_texts.items()
    ]
    return ScopeMaterial.build(pages=pages, fragments=SCOPE_FRAGMENTS)


def scope_rubric(
    verified: dict[str, Any],
    *,
    structure_gate: QualityGateResult,
    content_gate: QualityGateResult,
) -> dict[str, bool]:
    """生产范围映射/核验结果对照人类真值；不替代真实照片识别。"""
    units = {
        str(unit.get("unit_id")): unit
        for unit in verified.get("units", [])
        if isinstance(unit, dict)
    }

    def units_for(fragment_id: str) -> set[str]:
        return {
            unit_id
            for unit_id, unit in units.items()
            if fragment_id in [str(item) for item in unit.get("fragment_ids", [])]
        }

    coverage = {
        str(entry.get("fragment_id")): entry
        for entry in verified.get("coverage", [])
        if isinstance(entry, dict)
    }
    expected = {item.fragment_id for item in SCOPE_FRAGMENTS}
    accounted = set(coverage) == expected and all(
        entry.get("unit_ids") or len(str(entry.get("exclusion_reason", "")).strip()) >= 4
        for entry in coverage.values()
    )
    structure = verified.get("structure", {})
    problems = structure.get("problems", []) if isinstance(structure, dict) else []
    first, second = units_for("scope-p1-def"), units_for("scope-p2-def")
    mixed = any(
        {"scope-p1-def", "scope-p2-def"}
        <= {str(item) for item in unit.get("fragment_ids", [])}
        for unit in units.values()
    )
    unclear = units_for("scope-p2-minus")
    statuses = {
        str(item.get("unit_id")): str(item.get("status"))
        for item in verified.get("content_checks", [])
        if isinstance(item, dict)
    }
    return {
        "coverage_accounted": accounted,
        "structure_consistent": (structure_gate.verdict is QualityVerdict.PASS)
        == (not problems),
        "same_name_isolated": bool(first)
        and bool(second)
        and first.isdisjoint(second)
        and not mixed,
        "unclear_sign_not_certified": all(
            statuses.get(unit_id) != "consistent" for unit_id in unclear
        ),
        "unclear_sign_blocked": (not unclear) or content_gate.verdict is not QualityVerdict.PASS,
    }


def run_scope_cases(invoke: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """执行生产范围映射与核验；失败原样列出，不记作通过。"""
    material = scope_fixture_material()
    specs = {node.name: node for node in build_scope_mapping_recipe().nodes}
    inputs = RecipeInputs(
        account_id="scope-fixture",
        conversation_id="scope-fixture",
        run_id="scope-fixture",
        user_message_id="scope-fixture",
        user_content="",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
        prior_digest=material.material_hash,
    )
    flow = StudyScopeNodeFlow(invoke=lambda capability, payload: invoke(payload))
    flow.configure_mapping(material)
    map_invocation = NodeInvocation(
        spec=specs[NODE_MAP], inputs=inputs, dependencies={}, remaining_budget_ms=None
    )
    mapped = flow.run_node(map_invocation)
    if mapped.status is not NodeReceiptStatus.COMPLETED:
        return {
            "kind": "production-scope-semantics",
            "material_hash": material.material_hash,
            "cases": [
                {
                    "case_id": "scope.map",
                    "checks": {"mapping_completed": False},
                    "error_code": mapped.detail.get("code"),
                }
            ],
            "passed": False,
            "limitations": ["固定合成书页材料；不验证照片识别或补拍交互。"],
        }
    verify_invocation = NodeInvocation(
        spec=specs[NODE_VERIFY_SCOPE],
        inputs=replace(inputs, artifacts={NODE_MAP: mapped.artifact}),
        dependencies={NODE_MAP: mapped.artifact},
        remaining_budget_ms=None,
    )
    verified = flow.run_node(verify_invocation)
    structure_gate = SCOPE_GATE_HANDLERS[GATE_SCOPE_STRUCTURE](verify_invocation, verified)
    content_gate = SCOPE_GATE_HANDLERS[GATE_SCOPE_CONTENT](verify_invocation, verified)
    rubric = scope_rubric(
        verified.artifact.payload, structure_gate=structure_gate, content_gate=content_gate
    )
    groups = (
        ("scope.page_coverage", ("coverage_accounted", "structure_consistent")),
        ("scope.unclear_symbol", ("unclear_sign_not_certified", "unclear_sign_blocked")),
        ("scope.same_name_isolation", ("same_name_isolated",)),
    )
    cases: list[dict[str, Any]] = [
        {"case_id": case_id, "checks": {name: rubric[name] for name in names}}
        for case_id, names in groups
    ]
    return {
        "kind": "production-scope-semantics",
        "material_hash": material.material_hash,
        "gates": {
            "structure": structure_gate.to_dict(),
            "content": content_gate.to_dict(),
        },
        "units": [
            {
                "unit_id": str(unit.get("unit_id")),
                "title": unit.get("title"),
                "fragment_ids": unit.get("fragment_ids"),
            }
            for unit in verified.artifact.payload.get("units", [])
            if isinstance(unit, dict)
        ],
        "cases": cases,
        "passed": all(all(case["checks"].values()) for case in cases),
        "limitations": [
            "固定合成书页材料；不验证照片识别、补拍补录交互或预习问题生成。",
            "与评分/总结量表共用真实调用，但各自独立评分。",
        ],
    }
