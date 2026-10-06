"""工单 43：本批 COVERAGE 全部决策/问题/脚本/场景的最终对账注册表。

本模块把 ``.scratch/2/COVERAGE.md`` 的 127 条需求映射与 39 个固定工作流
场景收敛为可校验的覆盖账本：每条覆盖项给出负责票、可执行证据锚点与真实
状态（已验证 / 机制 / 如实降级 / 不放行 / 已记录缺口）。``validate`` 同时
与来源文档逐行对账，任何一边缺失都判失败——对账不以文档或 ready 状态
宣布上线，只把已核对的实现与验收落点登记出来。

状态语义（不互相升级）：

- ``verified``：该范围有真实模型或独立验收证据，范围按报告开放；
- ``mechanism``：确定性/集成机制通过，真实效果或外部门另票报告；
- ``degraded``：能力层次未全通过，产品按合同如实降级，不宣称深读/全量；
- ``not_released``：机制证据在，但放行门未过（如 39 人工盲评票数不足），
  该范围保持不开放，也不因合并改写结论；
- ``gap``：核对后确认的未覆盖缺口，保留在报告与工单限制里，不静默遗漏。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bridges.evaluation.integration_coverage_context import CONTEXT_COVERAGE
from bridges.evaluation.integration_coverage_expression import EXPRESSION_COVERAGE
from bridges.evaluation.integration_coverage_profile import PROFILE_COVERAGE
from bridges.evaluation.integration_coverage_types import (
    CoverageItem,
    CoverageStatus,
)
from bridges.evaluation.integration_coverage_workflow import WORKFLOW_COVERAGE
from bridges.evaluation.workflow_scenarios import WORKFLOW_SCENARIOS

#: COVERAGE.md 表格中登记的四个需求族（A/L/R 由工作流场景注册表校验）。
_DOC_ID_PATTERN = re.compile(
    r"^(H-D\d|H-\d{2}|C-D\d|C-\d{2}|"
    r"P-D\d{2}|P-Q\d{2}|P-\d{2}|"
    r"W-D\d{2}|W-\d{2}|"
    r"A\d{2}|L\d{2}|R\d{2})$"
)

#: 工作流场景的状态：真实模型配对覆盖的方向按已验证，其余按机制。
_REAL_MODEL_SCENARIOS = frozenset({"A01", "A02", "A03", "A11", "R07"})
#: 外部门如实降级的场景（tieba/jobs/github 探针结论，见工单 42 记录）。
_DEGRADED_SCENARIOS = frozenset({"A14", "A15", "A16", "A17", "A18"})
#: 明确需要真实外部门、当前只到机制层的场景。
_EXTERNAL_SCENARIOS = frozenset({"A08", "A09", "A12", "A13", "L04"})


def _expression_and_family_items() -> tuple[CoverageItem, ...]:
    return EXPRESSION_COVERAGE + CONTEXT_COVERAGE + PROFILE_COVERAGE + WORKFLOW_COVERAGE


def scenario_coverage() -> tuple[CoverageItem, ...]:
    """由 42 的场景注册表派生 A/L/R 覆盖项（不重复维护场景清单）。"""

    items: list[CoverageItem] = []
    for scenario in WORKFLOW_SCENARIOS:
        if scenario.scenario_id in _DEGRADED_SCENARIOS:
            status = CoverageStatus.DEGRADED
            note = "真实外部门按探针结论如实降级，未通过层次不宣称。"
        elif scenario.scenario_id in _REAL_MODEL_SCENARIOS:
            status = CoverageStatus.VERIFIED
            note = "工单 42 真实模型配对覆盖（每任务重复 2 次，只证方向）。"
        else:
            status = CoverageStatus.MECHANISM
            note = "确定性行为断言；真实外部门按 42 探针报告。"
        if scenario.scenario_id in _EXTERNAL_SCENARIOS:
            note += " 依赖真实外部门，当前证据到机制层。"
        evidence = tuple(scenario.tests) or ("docs/workflow/delivery-and-validation.md",)
        items.append(
            CoverageItem(
                item_id=scenario.scenario_id,
                requirement=scenario.title,
                owner_tickets=(42,),
                evidence=evidence,
                status=status,
                note=note,
            )
        )
    return tuple(items)


def coverage_items() -> tuple[CoverageItem, ...]:
    """全部覆盖项（127 条需求映射 + 39 个场景）。"""

    return _expression_and_family_items() + scenario_coverage()


def expected_coverage_ids() -> tuple[str, ...]:
    """应登记的完整编号集（与 COVERAGE.md 和场景合同逐项对应）。"""

    ids: list[str] = []
    ids += [f"H-D{i}" for i in range(1, 7)]
    ids += [f"H-{i:02d}" for i in range(7, 20)]
    ids += [f"C-D{i}" for i in range(1, 10)]
    ids += [f"C-{i}" for i in range(10, 23)]
    ids += [f"P-D{i:02d}" for i in range(1, 10)]
    ids += ["P-Q09"]
    ids += [f"P-{i}" for i in range(10, 34)]
    ids += [f"W-D{i:02d}" for i in range(1, 18)]
    ids += [f"W-{i}" for i in range(18, 53)]
    ids += [f"A{i:02d}" for i in range(1, 19)]
    ids += [f"L{i:02d}" for i in range(1, 14)]
    ids += [f"R{i:02d}" for i in range(1, 9)]
    return tuple(ids)


def _document_ids(repo_root: Path) -> set[str]:
    """从 COVERAGE.md 表格首列提取需求编号（逐行对账的唯一解析口径）。"""

    path = repo_root / ".scratch" / "2" / "COVERAGE.md"
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first = line.split("|", 2)[1].strip()
        if _DOC_ID_PATTERN.match(first):
            ids.add(first)
    return ids


def _test_node_exists(repo_root: Path, ref: str) -> bool:
    """证据锚点存在性：文件必须存在；带 ``::`` 的节点名必须在文件中。"""

    path_part, _, node = ref.partition("::")
    path = repo_root / path_part
    if not path.exists():
        return False
    if not node:
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = rf"^\s*(async\s+)?def\s+{re.escape(node)}\s*\("
    return re.search(pattern, text, re.MULTILINE) is not None


def validate_coverage_reconciliation(repo_root: Path | None = None) -> list[str]:
    """结构、来源文档与证据锚点三层校验；返回问题列表。"""

    root = repo_root or Path(__file__).resolve().parents[3]
    problems: list[str] = []
    items = coverage_items()
    by_id = {item.item_id: item for item in items}

    expected = set(expected_coverage_ids())
    if len(items) != len(by_id):
        duplicates = sorted(
            item_id for item_id in by_id if sum(1 for i in items if i.item_id == item_id) > 1
        )
        problems.append(f"覆盖编号重复：{', '.join(duplicates)}")
    for missing in sorted(expected - set(by_id)):
        problems.append(f"注册表缺少覆盖项：{missing}")
    for unknown in sorted(set(by_id) - expected):
        problems.append(f"注册表登记了未定义编号：{unknown}")

    document_ids = _document_ids(root)
    for missing in sorted(document_ids - set(by_id)):
        problems.append(f"COVERAGE.md 有但注册表没有：{missing}")
    for unknown in sorted(set(by_id) - document_ids):
        problems.append(f"注册表有但 COVERAGE.md 没有：{unknown}")

    for item in items:
        for ticket in item.owner_tickets:
            if ticket < 1 or ticket > 43:
                problems.append(f"{item.item_id} 负责票越界：{ticket}")
        for ref in item.evidence:
            if not _test_node_exists(root, ref):
                problems.append(f"{item.item_id} 证据锚点不存在：{ref}")
    return problems


@dataclass
class CoverageReport:
    """覆盖对账报告：状态分布、逐项明细与全部问题。"""

    total: int
    status_counts: dict[str, int]
    items: tuple[CoverageItem, ...] = field(default_factory=tuple)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "status_counts": dict(self.status_counts),
            "problems": list(self.problems),
            "items": [
                {
                    "item_id": item.item_id,
                    "requirement": item.requirement,
                    "owner_tickets": list(item.owner_tickets),
                    "evidence": list(item.evidence),
                    "status": item.status.value,
                    "note": item.note,
                }
                for item in self.items
            ],
        }


def build_coverage_report(repo_root: Path | None = None) -> CoverageReport:
    """构建并校验覆盖对账报告（问题为空才表示对账通过）。"""

    items = coverage_items()
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return CoverageReport(
        total=len(items),
        status_counts=counts,
        items=items,
        problems=validate_coverage_reconciliation(repo_root),
    )


__all__ = [
    "CONTEXT_COVERAGE",
    "CoverageItem",
    "CoverageReport",
    "CoverageStatus",
    "EXPRESSION_COVERAGE",
    "PROFILE_COVERAGE",
    "WORKFLOW_COVERAGE",
    "build_coverage_report",
    "coverage_items",
    "expected_coverage_ids",
    "scenario_coverage",
    "validate_coverage_reconciliation",
]
