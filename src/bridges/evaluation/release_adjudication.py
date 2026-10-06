"""工单 43：四张评测票的最终放行裁决台账（任务 6 / 验收 6）。

裁决只依据跟踪在库的独立验收记录与 42 的真实探针产物，不因合并或文档
ready 状态升级结论；未通过范围明确停用或按合同降级，不包装成功。

- 39 人味化表达：``not_released``（4 项人工硬门候选失败，帮助/分寸有效
  票不足 10，无法判非劣）；收益范围保持不开放。
- 40 上下文连续性：``released``（新侧 36/36，失败门身份与 main 基线一致）。
- 41 画像：``released_with_limits``（真实抽取/配对方向性通过；样本小，
  桌面 E2E 未进入页面断言）。
- 42 工作流：``released_with_degradation``（39 场景执行通过；tieba/GitHub
  探针失败、jobs 声明的 partial 未实测，按合同不宣称）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DECISION_NOT_RELEASED = "not_released"
DECISION_RELEASED = "released"
DECISION_RELEASED_WITH_LIMITS = "released_with_limits"
DECISION_RELEASED_WITH_DEGRADATION = "released_with_degradation"

_39_ACCEPTANCE = ".scratch/2/validation/39-independent/acceptance.md"
_40_ACCEPTANCE = (
    ".scratch/2/validation/40-context-continuity/independent-acceptance.md"
)
_41_ACCEPTANCE = ".scratch/2/validation/41-independent/acceptance.md"
_42_PROBES = ".scratch/2/validation/42-workflow-evaluation/external-probes.json"
_42_EVIDENCE = (
    ".scratch/2/validation/42-workflow-evaluation/workflow-evidence.json"
)


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
        evidence=(_39_ACCEPTANCE,),
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
        evidence=(_40_ACCEPTANCE,),
        open_scope="开放：同模型同预算连续性与成本报告范围。",
        limitations=(
            "旧侧多门失败属基线身份，同 main 全新隔离目录一致。",
        ),
    ),
    ReleaseDecision(
        ticket=41,
        scope="画像提取、治理与实际回答改善",
        decision=DECISION_RELEASED_WITH_LIMITS,
        evidence=(_41_ACCEPTANCE,),
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
        evidence=(_42_EVIDENCE, _42_PROBES),
        open_scope="开放：39 场景机制执行与预算初值轴；外部门按探针逐层开放。",
        limitations=(
            "tieba 读取不可用：只交付线索，不总结未读回复。",
            "GitHub 受共享出口配额失败：不据此宣称产品能力通过。",
            "jobs 声明 partial 未实测；arxiv 产品保持摘要+书目层。",
        ),
    ),
)

_MARKERS: dict[int, tuple[str, ...]] = {
    39: ("not_released", "不放行"),
    40: ("36/36",),
    41: ("8/8",),
}


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


def _read_text(root: Path, ref: str) -> str | None:
    path = root / ref
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _check_markers(root: Path, problems: list[str]) -> None:
    for ticket, markers in _MARKERS.items():
        text = _read_text(root, RELEASE_DECISIONS[ticket - 39].evidence[0])
        if text is None:
            problems.append(f"工单 {ticket} 缺少验收记录。")
            continue
        for marker in markers:
            if marker not in text:
                problems.append(f"工单 {ticket} 验收记录缺少结论标记：{marker}")


def _check_42_probes(root: Path, problems: list[str]) -> None:
    raw = _read_text(root, _42_PROBES)
    if raw is None:
        problems.append("工单 42 缺少外部探针报告。")
        return
    payload = json.loads(raw)
    if payload.get("consistency_problems"):
        problems.append(f"工单 42 探针一致性有问题：{payload['consistency_problems']}")
    statuses = {
        probe["gate"]: probe.get("status") for probe in payload.get("probes", ())
    }
    for gate in ("tieba.replies", "github.files"):
        if statuses.get(gate) == "passed":
            problems.append(f"工单 42 探针 {gate} 状态被升级为 passed。")
    claims = payload.get("claims", {})
    for gate in ("tieba.replies", "github.files"):
        claim = claims.get(gate, {})
        if claim.get("declared_level") == "full":
            problems.append(f"工单 42 声明 {gate} 为 full，与探针结果不符。")


def _check_42_evidence(root: Path, problems: list[str]) -> None:
    raw = _read_text(root, _42_EVIDENCE)
    if raw is None:
        problems.append("工单 42 缺少场景执行报告。")
        return
    payload = json.loads(raw)
    if payload.get("scenario_count") != 39:
        problems.append(
            f"工单 42 场景数不是 39：{payload.get('scenario_count')}"
        )
    if int(payload.get("summary", {}).get("problems", 0)) != 0:
        problems.append("工单 42 场景执行报告登记了问题。")


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

    for decision in decisions:
        for ref in decision.evidence:
            if not (root / ref).exists():
                problems.append(f"工单 {decision.ticket} 证据不存在：{ref}")

    _check_markers(root, problems)
    _check_42_probes(root, problems)
    _check_42_evidence(root, problems)
    return AdjudicationReport(decisions=decisions, problems=tuple(problems))


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
