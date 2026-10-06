"""工单 39：盲评材料渲染、提交模板与解析（无模型调用）。

评审者只能看到匿名会话与选择表：材料不打印对照标识、策略臂或场景
标识之外的系统信息；de-blind 映射保留在评测侧（`expression_review`）。
非法选择一律抛错，不静默丢弃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridges.evaluation.expression_review import BlindPairItem
from bridges.evaluation.expression_scale import REVIEW_CHOICES, REVIEW_DIMENSIONS


def render_blind_material(items: list[BlindPairItem]) -> str:
    """渲染评审者可见的盲评材料（匿名，不含对照身份与策略臂）。"""

    lines = [
        "# 人味表达多轮盲评材料",
        "",
        "同一模型的两种表达策略分别生成了同一组连续多轮对话，顺序已随机交换。",
        "请对每一项的五个维度分别选择：A、B、平局、都不选；不看系统身份，",
        "只判断回答本身。事实/状态/边界硬失败由确定性检查单列，不进入下面的选择。",
        "",
    ]
    for dimension in REVIEW_DIMENSIONS:
        lines.append(f"- {dimension.label}：{dimension.question}（{dimension.anchors}）")
    lines.extend(["", "---", ""])
    for item in items:
        lines.extend(
            [
                f"## {item.item_id}（场景：{item.title}）",
                "",
                "### 会话 A",
                "",
                item.label_a_text or "（无内容）",
                "",
                "### 会话 B",
                "",
                item.label_b_text or "（无内容）",
                "",
                "评审表（把选择填入下方，可加理由）：",
                "",
                "| 维度 | A | B | 平局 | 都不选 | 理由（可选） |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for dimension in REVIEW_DIMENSIONS:
            lines.append(f"| {dimension.label} |  |  |  |  |  |")
        lines.append("")
    return "\n".join(lines)


def submission_template(items: list[BlindPairItem]) -> dict[str, Any]:
    """供评审者填写的 JSON 模板（含全部对照项与维度）。"""

    return {
        "review_set_id": items[0].review_set_id if items else "",
        "instructions": "每个 item 的五个维度各填 label_a / label_b / tie / neither。",
        "reviewers": [
            {
                "reviewer_id": "请填写评审者标识",
                "choices": {
                    item.item_id: {
                        dimension.dimension_id: "" for dimension in REVIEW_DIMENSIONS
                    }
                    for item in items
                },
                "comments": {},
            }
        ],
    }


@dataclass(frozen=True)
class ReviewChoice:
    reviewer_id: str
    item_id: str
    dimension_id: str
    chosen: str
    rationale: str | None = None


def parse_submissions(payload: dict[str, Any]) -> list[ReviewChoice]:
    """解析评审提交 JSON；非法选择抛 ValueError（不静默丢弃）。"""

    choices: list[ReviewChoice] = []
    valid_dimensions = {dimension.dimension_id for dimension in REVIEW_DIMENSIONS}
    for reviewer in payload.get("reviewers", []):
        reviewer_id = str(reviewer.get("reviewer_id", "")).strip()
        if not reviewer_id:
            raise ValueError("评审提交缺少 reviewer_id。")
        for item_id, dimension_choices in (reviewer.get("choices") or {}).items():
            for dimension_id, chosen in (dimension_choices or {}).items():
                if dimension_id not in valid_dimensions:
                    raise ValueError(f"未登记的评审维度：{dimension_id}。")
                if not chosen:
                    continue
                if chosen not in REVIEW_CHOICES:
                    raise ValueError(
                        f"评审选择不合法：{item_id}/{dimension_id}={chosen}。"
                    )
                choices.append(
                    ReviewChoice(
                        reviewer_id=reviewer_id,
                        item_id=str(item_id),
                        dimension_id=dimension_id,
                        chosen=str(chosen),
                    )
                )
    return choices


def load_submissions(path: str | Path) -> list[ReviewChoice]:
    return parse_submissions(json.loads(Path(path).read_text(encoding="utf-8")))


__all__ = [
    "ReviewChoice",
    "load_submissions",
    "parse_submissions",
    "render_blind_material",
    "submission_template",
]
