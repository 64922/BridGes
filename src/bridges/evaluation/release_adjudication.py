"""工单 43：四张评测票的最终放行裁决台账（任务 6 / 验收 6）。

裁决只依据跟踪在库的独立验收记录与 42 的真实探针产物，不因合并或文档
ready 状态升级结论；未通过范围明确停用或按合同降级，不包装成功。

- 39 人味化表达：``not_released``（4 项人工硬门候选失败，帮助/分寸有效
  票不足 10，无法判非劣）；收益范围保持不开放。
- 40 上下文连续性：``released``（新侧 36/36，失败门身份与 main 基线一致）。
- 41 画像：``released_with_limits``（真实抽取/配对方向性通过；样本小，
  桌面 E2E 未进入页面断言）。
- 42 工作流：``released_with_degradation``（最终 39 场景执行通过；tieba/GitHub
  探针失败、jobs 探针不可判定，按合同不宣称）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from bridges.evaluation.external_probe_contracts import PRODUCT_CLAIMS
from bridges.evaluation.workflow_scenarios import WORKFLOW_SCENARIOS

DECISION_NOT_RELEASED = "not_released"
DECISION_RELEASED = "released"
DECISION_RELEASED_WITH_LIMITS = "released_with_limits"
DECISION_RELEASED_WITH_DEGRADATION = "released_with_degradation"

_39_ACCEPTANCE = ".scratch/2/validation/39-independent/acceptance.md"
_40_ACCEPTANCE = (
    ".scratch/2/validation/40-context-continuity/independent-acceptance.md"
)
_41_ACCEPTANCE = ".scratch/2/validation/41-independent/acceptance.md"
_39_SCORE = ".scratch/2/validation/39-independent/scored-review-v2/review-result.json"
_40_ROOT = ".scratch/2/validation/40-context-continuity/"
_40_NEW = _40_ROOT + "tree-40-context-continuity-and-cost-evaluation-20261005T075002Z.json"
_40_OLD = _40_ROOT + "tree-40-baseline-7818c34-20261005T075002Z.json"
_41_PAIR = ".scratch/2/validation/41-independent/rejudged/pairing-report.json"
_41_RAW = ".scratch/2/validation/41-independent/final-real/pairing-report.json"
# 最终独立验收证据的脱敏副本；旧 42-workflow-evaluation 报告不是最终裁决依据。
_42_ROOT = ".scratch/2/validation/43-independent/evidence/42/"
_42_PROBES = _42_ROOT + "external-probes.json"
_42_EVIDENCE = _42_ROOT + "workflow-evidence.json"
_42_PAIR = _42_ROOT + "workflow-pairing.json"
_42_SEMANTICS = _42_ROOT + "workflow-semantics.json"


@dataclass(frozen=True)
class ReleaseDecision:
    """一张评测票的最终裁决：范围、结论、证据、开放范围与限制。"""

    ticket: int
    scope: str
    decision: str
    evidence: tuple[str, ...]
    open_scope: str
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ticket": self.ticket,
            "scope": self.scope,
            "decision": self.decision,
            "evidence": list(self.evidence),
            "open_scope": self.open_scope,
            "limitations": list(self.limitations),
        }


RELEASE_DECISIONS: tuple[ReleaseDecision, ...] = (
    ReleaseDecision(
        ticket=39,
        scope="人味化表达与交流边界",
        decision=DECISION_NOT_RELEASED,
        evidence=(_39_ACCEPTANCE, _39_SCORE),
        open_scope="不开放：保持既有基线表达，不宣称人味收益。",
        limitations=(
            "4 项人工硬门候选失败；帮助/分寸有效票 6/3 与 7/4，不足 10 票。",
            "无人工盲评提交时判 inconclusive；重评分明确 not_released。",
        ),
    ),
    ReleaseDecision(
        ticket=40,
        scope="上下文连续性与成本",
        decision=DECISION_RELEASED,
        evidence=(_40_ACCEPTANCE, _40_NEW, _40_OLD),
        open_scope="开放：同模型同预算连续性与成本报告范围。",
        limitations=(
            "旧侧多门失败属基线身份，同 main 全新隔离目录一致。",
        ),
    ),
    ReleaseDecision(
        ticket=41,
        scope="画像提取、治理与实际回答改善",
        decision=DECISION_RELEASED_WITH_LIMITS,
        evidence=(_41_ACCEPTANCE, _41_PAIR, _41_RAW),
        open_scope="开放：有效事实/撤回机制/实际内容改善的评测范围。",
        limitations=(
            "真实抽取 14 次调用、配对 8/8 只证方向，不宣称准确率。",
            "真实桌面 E2E 前端启动超时，未进入页面断言。",
            "不宣称所有模型回答均正确或全仓测试通过。",
        ),
    ),
    ReleaseDecision(
        ticket=42,
        scope="工作流质量、预算与外部真实可得性",
        decision=DECISION_RELEASED_WITH_DEGRADATION,
        evidence=(_42_EVIDENCE, _42_PROBES, _42_PAIR, _42_SEMANTICS),
        open_scope="开放：39 场景机制执行与预算初值轴；外部门按探针逐层开放。",
        limitations=(
            "tieba 读取不可用：只交付线索，不总结未读回复。",
            "GitHub 受共享出口配额失败：不据此宣称产品能力通过。",
            "最终 jobs 探针为 inconclusive；arxiv 产品保持摘要+书目层。",
            "7 个配对场景、10 个语义量表各重复 2 次；只证明已测范围，不代表完整教学质量。",
        ),
    ),
)

@dataclass(frozen=True)
class AdjudicationReport:
    """裁决报告：决定台账与一致性校验问题。"""

    decisions: tuple[ReleaseDecision, ...]
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems

    def by_ticket(self, ticket: int) -> ReleaseDecision:
        for decision in self.decisions:
            if decision.ticket == ticket:
                return decision
        raise KeyError(ticket)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "problems": list(self.problems),
        }


def _load(root: Path, ref: str) -> dict[str, Any]:
    payload = json.loads((root / ref).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("报告必须是 JSON 对象")
    return payload


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _check_39(root: Path) -> None:
    score = _load(root, _39_SCORE)
    release = score["release"]
    _require(
        release["status"] == DECISION_NOT_RELEASED and release["released"] is False,
        "人工评分不得被升级为放行",
    )
    _require(bool(release["blockers"]) and bool(score["manual_hard_gate_failures"]),
             "缺少真实人工硬门失败/放行阻塞证据")
    submissions = score["anonymous_submissions"]
    _require(
        submissions["review_set_id"] == score["review_set_id"]
        and submissions["material_digest"] == score["material_digest"]
        and bool(submissions["reviewers"])
        and bool(score["source_run_lock_digest"]),
        "人工评分、冻结材料和原运行锁不一致",
    )
    dimensions = score["aggregate"]["dimensions"]
    required = {(comparison, dimension) for comparison in
                ("current-vs-baseline", "current-vs-legacy")
                for dimension in ("help", "boundary")}
    observed = {(row["comparison_id"], row["dimension_id"]) for row in dimensions}
    _require(required <= observed, "缺少帮助/分寸非劣量表")


def _check_40(root: Path) -> None:
    old, new = _load(root, _40_OLD), _load(root, _40_NEW)
    for key in ("evaluation_sha256", "corpus_sha256", "model_ids", "repeats", "scenarios"):
        _require(bool(new[key]) and old[key] == new[key], f"配对运行锁不一致：{key}")
    _require(new["inconclusive"] is False and old["inconclusive"] is False,
             "配对报告不可判定")
    _require(bool(new["commit"]) and bool(new["source_snapshot"]), "缺少真实源码锁")
    cases = new["cases"]
    expected = {(scenario, repeat) for scenario in new["scenarios"]
                for repeat in range(1, new["repeats"] + 1)}
    _require(len(cases) == 36 and len(expected) == 36
             and {(case["scenario_id"], case["repeat"]) for case in cases} == expected,
             "36 个配对执行样本缺失或重复")
    hard_checks = {"old_photo_reread", "provider_actual_bounds", "long_chat_summary_observed",
                   "short_chat_no_summary", "summary_source_bound", "summary_output_bound",
                   "summary_attempt_bound"}
    for case in cases:
        _require(case["passed"] is True and case["content_passed"] is True
                 and not case["quality_gated"], "新侧配对内容或终态硬门失败")
        _require(set(case["hard_checks"]) == hard_checks
                 and all(value is True for value in case["hard_checks"].values()),
                 "新侧配对硬门缺失或失败")
        _require(bool(case["provider_calls"]) and all(
            call["request_sha256"] and call["model"] in new["model_ids"]
            for call in case["provider_calls"]), "缺少逐调用请求/模型锁")
    dimensions = new["totals"]["by_dimension"]
    _require(set(dimensions) == {"constraint_retention", "correction_override",
                                "object_resolution", "reference_correctness",
                                "honest_clarification_gap", "isolation"},
             "连续性量表维度不完整")
    _require(all(row["total"] > 0 and row["passed"] == row["total"]
                 for row in dimensions.values()), "连续性维度未全部通过")


def _check_41(root: Path) -> None:
    pair = _load(root, _41_PAIR)
    _require(pair["passed"] is True and not pair["failed_runs"], "画像配对未通过")
    _require(pair["environment"]["raw_report_sha256"] ==
             sha256((root / _41_RAW).read_bytes()).hexdigest(), "画像复评原始报告指纹不符")
    runs = pair["runs"]
    expected = {(task, condition) for task in ("bayes-explain", "study-plan")
                for condition in ("no_profile", "correct_profile", "wrong_profile",
                                  "outdated_profile")}
    _require(pair["run_count"] == 8 and len(runs) == 8
             and {(run["task_id"], run["condition"]) for run in runs} == expected,
             "画像四条件配对缺失或重复")
    for run in runs:
        _require(run["status"] == "done" and run["passed"] is True
                 and bool(run["answer"]) and bool(run["model_id"])
                 and run["input_tokens"] > 0 and run["output_tokens"] > 0,
                 "画像样本不是已完成的真实回答")
        _require(bool(run["checkpoints"]) and all(check["passed"] is True
                 for check in run["checkpoints"]), "画像检查点缺失或失败")
    _require(len({run["model_id"] for run in runs}) == 1, "画像配对模型不一致")


def _check_42(root: Path) -> None:
    evidence = _load(root, _42_EVIDENCE)
    _require(evidence["kind"] == "workflow-evidence" and evidence["pytest_exit_code"] == 0
             and evidence["scenario_count"] == 39 and not evidence["problems"],
             "工作流执行报告不完整或失败")
    scenarios = evidence["scenarios"]
    _require(len(scenarios) == 39 and {row["scenario_id"] for row in scenarios}
             == {scenario.scenario_id for scenario in WORKFLOW_SCENARIOS},
             "工作流场景执行缺失或重复")
    expected_tests = {scenario.scenario_id: set(scenario.tests) for scenario in WORKFLOW_SCENARIOS}
    for row in scenarios + evidence["zero_tolerance"]:
        _require(row["ok"] is True and not row["missing"] and not row["failed"]
                 and bool(row["evidence"]) and all(value == "passed"
                 for value in row["evidence"].values()), "工作流行为/发布不变量证据失败")
        if "scenario_id" in row:
            _require(expected_tests[row["scenario_id"]] <= set(row["evidence"]),
                     "工作流场景漏掉正式登记的行为断言")
    _require({row["kind"] for row in evidence["zero_tolerance"]} ==
             {"cross_account", "hard_condition_bypass", "system_failure_as_student_error",
              "duplicate_judgement", "write_after_stop"}, "发布不变量不完整")
    _check_42_probes(root)
    _check_42_pair_and_semantics(root)


def _check_42_probes(root: Path) -> None:
    report = _load(root, _42_PROBES)
    _require(report["kind"] == "external-probes" and not report["consistency_problems"],
             "外部门报告类型/一致性失败")
    probes = report["probes"]
    by_gate = {probe["gate"]: probe for probe in probes}
    _require(len(probes) == len(PRODUCT_CLAIMS) and set(by_gate) == set(PRODUCT_CLAIMS)
             and set(report["claims"]) == set(PRODUCT_CLAIMS), "外部门探针/声明缺失或重复")
    for gate, probe in by_gate.items():
        _require(probe["status"] in {"passed", "failed", "inconclusive"}
                 and bool(probe["checked_at"]) and bool(probe["summary"])
                 and (bool(probe["measurements"]) or probe["status"] != "passed"),
                 "探针缺少实测状态/时钟/测量")
        _require(report["claims"][gate]["declared_level"] == PRODUCT_CLAIMS[gate]
                 and probe["declared_level"] == PRODUCT_CLAIMS[gate], "探针与产品声明不一致")
        if probe["status"] != "passed":
            _require(probe["degradation_contract"] is True and bool(probe["degradation"]),
                     "未通过外部门缺少降级合同")
    _require(by_gate["model.configured_capabilities"]["status"] == "passed",
             "配置模型能力门未通过")
    _require(all(by_gate[gate]["status"] == "failed" for gate in ("tieba.replies", "github.files"))
             and by_gate["jobs.public_detail"]["status"] == "inconclusive",
             "最终受限外部门状态被改写")


def _check_42_pair_and_semantics(root: Path) -> None:
    pair = _load(root, _42_PAIR)
    _require(pair["kind"] == "workflow-pairing" and not pair["problems"]
             and bool(pair["corpus_sha256"]), "最终工作流配对失败或缺少语料锁")
    _require(pair["old"]["model_ids"] == pair["new"]["model_ids"]
             and bool(pair["new"]["model_ids"]) and bool(pair["new"]["source_lock"]),
             "工作流配对模型/源码锁不完整")
    cases = pair["cases"]
    _require(len(cases) == 7 and len({row["case_id"] for row in cases}) == 7,
             "最终工作流配对 7 场景缺失或重复")
    for case in cases:
        checks = case["check_results"]
        _require(bool(checks) and all(bool(row["new"]) and all(value is True
                 for value in row["new"]) for row in checks.values()), "工作流配对行为断言失败")
    semantics = _load(root, _42_SEMANTICS)
    _require(semantics["kind"] == "real-model-production-role-semantics"
             and semantics["passed"] is True and semantics["repeats"] == 2
             and bool(semantics["source_sha256"]) and bool(semantics["code_commit"]),
             "最终真实语义报告失败或缺少源码锁")
    _require(len(semantics["reports"]) == 2 and len(semantics["calls"]) == 16,
             "真实语义重复轮次/调用证据不完整")
    for report in semantics["reports"]:
        _require(report["passed"] is True and len(report["cases"]) == 10
                 and bool(report["corpus_sha256"]), "真实语义量表缺失或失败")
        _require(all(bool(case["checks"]) and all(value is True for value
                 in case["checks"].values()) for case in report["cases"]), "真实语义断言失败")
    for call in semantics["calls"]:
        _require(bool(call["request_sha256"]) and bool(call["schema_sha256"])
                 and bool(call["actual_model"]) and bool(call["usage"])
                 and call["finish_reason"] != "length", "真实语义调用锁/用量缺失或输出耗尽")


def build_release_adjudication(
    repo_root: Path | None = None,
) -> AdjudicationReport:
    """构建裁决报告并校验结论与产物一致。"""

    root = repo_root or Path(__file__).resolve().parents[3]
    problems: list[str] = []
    decisions = RELEASE_DECISIONS

    if tuple(decision.ticket for decision in decisions) != (39, 40, 41, 42):
        problems.append("裁决台账必须恰好覆盖 39/40/41/42。")
    if decisions[0].decision != DECISION_NOT_RELEASED:
        problems.append("工单 39 不得被改写为放行。")
    if decisions[3].decision != DECISION_RELEASED_WITH_DEGRADATION:
        problems.append("工单 42 必须按降级裁决登记。")

    checked: list[ReleaseDecision] = []
    checks = (_check_39, _check_40, _check_41, _check_42)
    for decision, check in zip(decisions, checks, strict=True):
        try:
            _require(all((root / ref).is_file() for ref in decision.evidence),
                     "登记的独立验收记录或结构化报告缺失")
            check(root)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # 缺失/损坏证据只登记类型和校验原因，不能沿用预设放行决定。
            reason = f"工单 {decision.ticket} 结构化证据校验失败：{type(exc).__name__}：{exc}"
            problems.append(reason)
            decision = replace(
                decision, decision=DECISION_NOT_RELEASED,
                open_scope="证据校验失败，暂停开放，待补齐真实证据后复验。",
                limitations=(*decision.limitations, reason),
            )
        checked.append(decision)
    return AdjudicationReport(decisions=tuple(checked), problems=tuple(problems))


__all__ = [
    "AdjudicationReport",
    "DECISION_NOT_RELEASED",
    "DECISION_RELEASED",
    "DECISION_RELEASED_WITH_DEGRADATION",
    "DECISION_RELEASED_WITH_LIMITS",
    "RELEASE_DECISIONS",
    "ReleaseDecision",
    "build_release_adjudication",
]
