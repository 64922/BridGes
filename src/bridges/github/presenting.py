"""``github.present``：带来源的中文结果、覆盖范围与证据边界。

正文由**真实证据渲染**（实际查询词、每个仓库的直达链接、逐条要点匹配与命中
原文、维护与许可证据、已读文件），不依赖模型即保证零虚构。可选的「借鉴角度」
由模型在给定证据范围内归纳：只接受本轮真实候选的仓库标识、长度受限、空值丢弃；
模型不可用或输出不合规时省略并如实说明，绝不补造。

失败、空结果、限流与停止都在同一消息里显示真实状态（``无数据`` 表示确实没有取得）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bridges.contracts.ai import ModelCallResult, ModelRunLock
from bridges.github.contracts import (
    GithubCoverage,
    GithubEvidenceKind,
    GithubFeatureMatch,
    GithubProjectsProjection,
    GithubRecommendation,
)
from bridges.github.lexicon import SEARCH_SOURCE

#: 每条「借鉴角度」的长度上限（防止生成失控长文）。
INSIGHT_MAX_CHARS = 160

#: 中文标签（正文与卡片共用同一套取值语义）。
COVERAGE_LABELS: dict[GithubCoverage, str] = {
    GithubCoverage.WHOLE: "整体项目",
    GithubCoverage.COMPONENT: "组件项目（只覆盖一部分）",
}

EVIDENCE_LABELS: dict[GithubEvidenceKind, str] = {
    GithubEvidenceKind.METADATA: "API 元数据",
    GithubEvidenceKind.README: "README 自述",
    GithubEvidenceKind.IMPLEMENTATION: "实际读取的实现文件",
}

README_STATUS_LABELS: dict[str, str] = {
    "read": "已读到 README",
    "not_found": "仓库没有 README",
    "too_large": "README 超出接口单文件上限",
    "not_fetched": "README 未取得（上游额度限制）",
    "error": "README 读取失败",
}

INSIGHT_SYSTEM_PROMPT = (
    "你是开源项目调研助手。只依据给定证据写一句中文「可借鉴角度」，"
    "不得引入未给出的功能、数字、评价或对比，不得猜测内部架构；"
    "证据等级只有「API 元数据」时不要把项目说成已经看过代码。"
    "每项不超过 100 字，直接说可以从哪里借鉴。"
)

INSIGHT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "insight_zh": {"type": "string"},
                },
                "required": ["full_name", "insight_zh"],
            },
        }
    },
    "required": ["insights"],
}


@dataclass(frozen=True)
class InsightOutcome:
    """借鉴角度生成结果：按仓库标识的文本、说明与模型锁（供原子落库）。"""

    insights: dict[str, str] = field(default_factory=dict)
    note: str | None = None
    lock: ModelRunLock | None = None
    dropped: int = 0


def render_clarification_content(projection: GithubProjectsProjection) -> str:
    pending = projection.pending
    return pending.question if pending is not None else ""


def render_result_content(projection: GithubProjectsProjection) -> str:
    """成功结果的正文：查询词、逐仓库证据与证据边界。"""
    lines: list[str] = []
    lines.append(
        f"已按 GitHub 项目推荐检索公开仓库，实际查询词：{_search_queries(projection)}。"
    )
    lines.append(f"核心场景：{projection.scenario}")
    if projection.features:
        lines.append(f"必要功能：{'、'.join(projection.features)}")
    if projection.tech_terms:
        lines.append(f"可选技术词（只用于排序参考）：{'、'.join(projection.tech_terms)}")
    if projection.context_source is not None:
        lines.append(
            f"前文依据：{projection.context_source.label}的原始词"
            f"「{projection.context_source.phrase}」。"
        )
    lines.append("")
    lines.append(f"共 {len(projection.recommendations)} 个仓库，按功能匹配优先：")
    for item in projection.recommendations:
        lines.append("")
        lines.append(
            f"{item.rank}. {item.full_name}（{COVERAGE_LABELS[item.coverage]}）"
        )
        lines.append(f"   链接：{item.html_url}")
        lines.append(f"   覆盖范围：{item.coverage_note}")
        if item.description:
            lines.append(f"   项目介绍（API 元数据）：{item.description}")
        if item.topics:
            lines.append(f"   话题：{'、'.join(item.topics)}")
        lines.append("   功能匹配：")
        lines.extend(f"     - {_feature_line(match)}" for match in item.feature_matches)
        lines.append(f"   借鉴角度：{item.insight_zh or item.borrow_note}")
        lines.append(
            "   维护与许可证据："
            f"许可 {item.license.spdx_id or item.license.name or '未见'}；"
            f"{item.maintenance.note}"
        )
        lines.append(f"   选用理由：{item.reason_zh}")
        lines.append(
            f"   证据等级：{'、'.join(EVIDENCE_LABELS[kind] for kind in item.evidence_kinds)}"
        )
        lines.append(f"   README：{_readme_label(item.readme_status)}")
        if item.files_read:
            paths = "、".join(entry.path for entry in item.files_read)
            lines.append(f"   已读实现文件／目录：{paths}")
        if item.strengths:
            lines.append(f"   优点：{'；'.join(item.strengths)}")
        if item.limitations:
            lines.append(f"   局限：{'；'.join(item.limitations)}")
    if projection.rejected:
        lines.append("")
        lines.append("未纳入推荐的候选：")
        lines.extend(
            f"- {item.full_name}：{item.reason}" for item in projection.rejected
        )
    if projection.evidence_boundary:
        lines.append("")
        lines.append("证据边界：")
        lines.extend(f"- {note}" for note in projection.evidence_boundary)
    return "\n".join(lines)


def render_empty_content(projection: GithubProjectsProjection) -> str:
    lines = [
        "已按 GitHub 项目推荐检索公开仓库，实际查询词："
        + (_search_queries(projection) or "（本轮没有发出查询）")
        + "。",
    ]
    lines.append(projection.empty_reason or "本轮没有可推荐的公开仓库，我不会用记忆补造条目。")
    if projection.rejected:
        lines.append("已检索但未纳入的候选：")
        lines.extend(f"- {item.full_name}：{item.reason}" for item in projection.rejected)
    lines.extend(f"- {note}" for note in projection.evidence_boundary)
    lines.append("可以补充功能描述、换个说法（例如指明技术栈或场景）后重试。")
    return "\n".join(lines)


def render_stopped_content(projection: GithubProjectsProjection) -> str:
    query = _search_queries(projection)
    detail = query or "（未发出查询）"
    return f"GitHub 项目推荐已停止，实际查询词：{detail}。已完成的步骤保留在本条消息内。"


def _search_queries(projection: GithubProjectsProjection) -> str:
    """「实际查询词」只列检索接口发出的词。

    读取仓库（``/repos/...``）也会留下查询记录，但 ``query`` 是仓库标识，不是
    检索词，混进这一行会让「实际查询词」失真。
    """
    return "；".join(
        record.query
        for record in projection.queries
        if record.query and record.source == SEARCH_SOURCE
    )


def _readme_label(status: Any) -> str:
    value = getattr(status, "value", status)
    return README_STATUS_LABELS.get(str(value), str(value))


def _feature_line(match: GithubFeatureMatch) -> str:
    if match.matched:
        return f"{match.feature}：已覆盖（{match.evidence}）"
    return f"{match.feature}：未覆盖（{match.evidence}）"


class GithubInsightGenerator:
    """可选的「借鉴角度」生成：严格门控，模型不得引入候选之外的内容。"""

    def __init__(
        self,
        gateway: Any,
        *,
        capability_name: str = "qwen_structured_output",
        capability_version: str = "1",
    ) -> None:
        self._gateway = gateway
        self._capability_name = capability_name
        self._capability_version = capability_version

    def generate(
        self,
        run_context: Any,
        recommendations: list[GithubRecommendation],
        *,
        model_id: str | None,
    ) -> InsightOutcome:
        if not recommendations:
            return InsightOutcome()
        payload = {
            "messages": [
                {"role": "system", "content": INSIGHT_SYSTEM_PROMPT},
                {"role": "user", "content": _insight_prompt(recommendations)},
            ],
            "json_schema": INSIGHT_JSON_SCHEMA,
            "temperature": 0.3,
            "max_tokens": 900,
        }
        try:
            result: ModelCallResult = self._gateway.invoke(
                self._capability_name,
                self._capability_version,
                run_context,
                payload,
                model_override=model_id,
            )
        except Exception:  # noqa: BLE001 - 归纳失败不影响真实结果呈现
            return InsightOutcome(note="借鉴角度生成失败（模型调用异常），本轮只给证据本身。")
        lock = result.lock
        if result.status.value not in {"success", "degraded"}:
            return InsightOutcome(
                note="借鉴角度未生成（模型能力不可用或失败），本轮只给证据本身。",
                lock=lock,
            )
        allowed = {item.full_name for item in recommendations}
        insights: dict[str, str] = {}
        dropped = 0
        for item in (result.output or {}).get("insights", []):
            if not isinstance(item, dict):
                dropped += 1
                continue
            full_name = str(item.get("full_name") or "")
            text = str(item.get("insight_zh") or "").strip()
            # 门控：只接受本轮真实候选的标识；空值/超长/未知标识一律丢弃。
            if full_name not in allowed or not text or len(text) > INSIGHT_MAX_CHARS:
                dropped += 1
                continue
            insights[full_name] = text
        note = None
        if dropped:
            note = f"借鉴角度中有 {dropped} 条不符合证据约束（仓库标识或长度），已丢弃。"
        return InsightOutcome(insights=insights, note=note, lock=lock, dropped=dropped)


def _insight_prompt(recommendations: list[GithubRecommendation]) -> str:
    lines = ["请为下列仓库各写一句中文「可借鉴角度」（只依据给出的证据）："]
    for item in recommendations:
        lines.append(f"- full_name: {item.full_name}")
        lines.append(f"  覆盖范围: {COVERAGE_LABELS[item.coverage]}（{item.coverage_note}）")
        lines.append(f"  项目介绍: {item.description or '上游没有给出简介'}")
        lines.append(f"  话题: {'、'.join(item.topics) or '无'}")
        lines.append(
            "  功能匹配: "
            + "；".join(
                f"{match.feature}={'命中' if match.matched else '未命中'}"
                for match in item.feature_matches
            )
        )
        lines.append(
            "  证据等级: " + "、".join(EVIDENCE_LABELS[kind] for kind in item.evidence_kinds)
        )
        if item.readme_excerpt:
            lines.append(f"  README 片段: {item.readme_excerpt[:600]}")
        if item.files_read:
            lines.append(f"  已读文件: {'、'.join(entry.path for entry in item.files_read)}")
        lines.append(f"  已读路径核对: {_checks_brief(item)}")
        lines.append(f"  许可: {item.license.spdx_id or item.license.name or '未见许可'}")
        lines.append(
            f"  维护: {'已归档' if item.maintenance.archived else '未归档'}，"
            f"可运行线索 {'、'.join(item.maintenance.runnable_hints) or '未读到'}"
        )
    lines.append('输出 JSON：{"insights": [{"full_name": "...", "insight_zh": "..."}]}')
    return "\n".join(lines)


def _checks_brief(item: GithubRecommendation) -> str:
    if not item.implementation_checks:
        return "无（本轮未核对 README 点名的路径）"
    return "；".join(
        f"{check.path}={check.status}" for check in item.implementation_checks[:5]
    )


__all__ = [
    "COVERAGE_LABELS",
    "EVIDENCE_LABELS",
    "GithubInsightGenerator",
    "INSIGHT_MAX_CHARS",
    "InsightOutcome",
    "README_STATUS_LABELS",
    "render_clarification_content",
    "render_empty_content",
    "render_result_content",
    "render_stopped_content",
]
