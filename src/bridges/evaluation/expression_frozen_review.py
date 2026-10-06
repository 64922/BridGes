"""工单 39：对已冻结的真实材料评分，不重新调用模型或替换回答。

材料摘要把匿名会话内容绑定到提交；机器关键词硬门只是诊断，真实放行
还须逐项人工核查六类硬失败。旧材料没有摘要时使用其冻结集合标识。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bridges.evaluation.expression_gates import HardGateId
from bridges.evaluation.expression_release import evaluate_release
from bridges.evaluation.expression_review import (
    CURRENT_ARM_ID,
    BlindPairItem,
    aggregate_review,
)
from bridges.evaluation.expression_submission import (
    REVIEW_PROTOCOL_VERSION,
    material_digest,
    parse_submissions,
)


def manual_hard_review(
    submission: dict[str, Any], mapping: dict[str, dict[str, str]]
) -> tuple[list[str], list[str]]:
    """核查每位实际投票者的六门标记；失败与不确定均阻止候选放行。"""
    failures: list[str] = []
    covered: set[tuple[str, str, str]] = set()
    for reviewer in submission.get("reviewers", []):
        reviewer_id = reviewer["reviewer_id"]
        checked = reviewer.get("hard_gates") or {}
        if set(checked) - set(mapping):
            raise ValueError("人工硬门引用未知材料。")
        for item_id, labels in checked.items():
            info = mapping[item_id]
            for label in ("label_a", "label_b"):
                if set(labels) - {"label_a", "label_b"}:
                    raise ValueError("人工硬门的会话标签不合法。")
                gates = labels.get(label) or {}
                if set(gates) - {gate.value for gate in HardGateId}:
                    raise ValueError("人工硬门类别未登记。")
                for gate in HardGateId:
                    verdict = gates.get(gate.value, "")
                    if verdict not in ("", "pass", "fail", "uncertain"):
                        raise ValueError("人工硬门只允许 pass / fail / uncertain。")
                    key = f"{reviewer_id}/{item_id}/{label}/{gate.value}"
                    if verdict:
                        covered.add((item_id, label, gate.value))
                    if verdict in {"fail", "uncertain"} and info[f"{label}_arm"] == CURRENT_ARM_ID:
                        failures.append(f"{key}:{verdict}")
    missing = [
        f"{item_id}/{label}/{gate.value}"
        for item_id in mapping
        for label in ("label_a", "label_b")
        for gate in HardGateId
        if (item_id, label, gate.value) not in covered
    ]
    return failures, missing


def score_frozen_report(
    report: dict[str, Any], submission: dict[str, Any]
) -> dict[str, Any]:
    """返回独立评分产物，原始真实测量与材料保持冻结。"""
    review = report["blind_review"]
    if submission.get("review_protocol_version") != REVIEW_PROTOCOL_VERSION:
        raise ValueError("评审提交协议版本不匹配，请使用冻结材料的新模板。")
    items = [BlindPairItem(**item) for item in review["items"]]
    digest = material_digest(items)
    if submission.get("material_digest") != digest:
        raise ValueError("评审提交材料摘要不匹配，请使用该冻结报告的模板。")
    choices = parse_submissions(submission, items=items)
    aggregate = aggregate_review(items, review["mapping"], choices)
    manual_failures, missing = manual_hard_review(submission, review["mapping"])
    machine_failures = [
        f"{entry['scenario_id']}#{entry['turn_index']}:{entry['gate']}"
        for entry in report["hard_gates"]["failures"]
        if entry["arm"] == CURRENT_ARM_ID
    ]
    blockers = [*machine_failures, *manual_failures]
    if missing:
        blockers.append(f"人工硬门核查未完成：{len(missing)} 项。")
    if not report["cost"]["humanization_specific_calls_zero"]:
        blockers.append("人味专属新增调用非零。")
    scope = report.get("validation_scope") or {}
    for key, label in (
        ("real_ablations_executed", "真实三项消融"),
        ("formal_paths_executed", "正式路径执行"),
        ("deployment_thresholds_defined", "部署限额"),
    ):
        if scope.get(key) is not True:
            blockers.append(f"缺少{label}验收证据。")
    release = evaluate_release(aggregate, hard_gate_failures=tuple(blockers))
    return {
        "source_code_commit": report["environment"]["code_commit"],
        "review_protocol_version": REVIEW_PROTOCOL_VERSION,
        "source_run_lock_digest": report["run_lock_digest"],
        "review_set_id": items[0].review_set_id if items else None,
        "material_digest": digest,
        "aggregate": aggregate,
        "anonymous_submissions": submission,
        "manual_hard_gate_failures": manual_failures,
        "manual_hard_gate_missing": missing,
        "release": release,
        "limitations": [
            "机器硬门仅为关键词诊断，须人工核查任务完整性、事实与边界。",
            "Wilson 区间按投票计算；同场景及同评审者相关性未校正，不能代表总体收益。",
            "评分不会补齐原始报告缺少的真实消融与正式路径执行证据。",
        ],
    }


def load_frozen_report(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))
