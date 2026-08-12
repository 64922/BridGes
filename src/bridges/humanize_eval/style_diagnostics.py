"""自动风格诊断（Issue 11 AC-8）。

自动风格指标扩展为带场景、位置和证据的诊断：每次命中记录类别、
场景（语料面+模式）、字符位置与证据 span。规则：

- 稳定的协议/助手身份泄漏短语（暴露系统身份或客服腔承诺）可以阻止
  对应 surface 发布；
- 风格短语只提供失败诊断，不得用单词命中直接阻止发布；
- fail closed 读取保真结果：保真报告缺失或存在检查缺失时，本模块
  拒绝给出"干净"结论（verdict=fail_closed），绝不凭空报通过。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bridges.humanize_eval.cases import HumanizeCase
from bridges.humanize_eval.fidelity import FidelityReport

#: 稳定的协议/助手身份泄漏短语（预注册；变化必须升版本）。
#: 输出正文中出现这些短语 = 暴露系统身份 / 承诺客服服务，可阻止发布。
PROTOCOL_LEAK_PHRASES = (
    "作为 BridGes",
    "BridGes 助手",
    "我是 BridGes",
    "作为一个 AI",
    "我是一个 AI",
    "我是人工智能",
    "AI 助手",
    "有什么可以帮您",
    "有什么可以帮你",
    "感谢您使用 BridGes",
    "如需帮助请联系",
)

#: 自动风格诊断短语（AI 腔/模板腔；只诊断，不阻止发布）。
STYLE_PHRASES = (
    "首先",
    "其次",
    "最后",
    "总的来说",
    "总而言之",
    "综上所述",
    "值得注意的是",
    "需要指出的是",
    "希望这能帮助",
    "如果还有任何问题",
    "随时联系我",
    "让我们一起",
)


class StyleHit(BaseModel):
    """一次风格诊断命中（带场景、位置与证据）。"""

    category: str = Field(description="protocol_leak / style_phrase。")
    phrase: str = Field(description="命中的短语。")
    position: int = Field(description="输出正文中的字符偏移。")
    evidence_span: str = Field(description="证据 span（短语本身）。")
    scenario: str = Field(description="场景：语料面 + 模式/体裁。")
    blocks_release: bool = Field(
        default=False, description="是否可阻止发布（仅协议泄漏）。"
    )


class StyleDiagnosticReport(BaseModel):
    """一次自动风格诊断报告（fail closed）。"""

    case_id: str
    hits: list[StyleHit] = Field(default_factory=list)
    protocol_leak_hits: list[StyleHit] = Field(default_factory=list)
    fidelity_available: bool = Field(description="保真报告是否可用（fail closed）。")
    fidelity_missing_checks: list[str] = Field(
        default_factory=list, description="保真检查缺失项（fail closed 依据）。"
    )
    verdict: str = Field(
        description="ok / warning / blocked / fail_closed。"
    )
    reasons: list[str] = Field(default_factory=list)

    @property
    def style_hits(self) -> list[StyleHit]:
        """纯风格命中（仅诊断，不阻止发布）。"""
        return [hit for hit in self.hits if hit.category == "style_phrase"]


def _scenario(case: HumanizeCase) -> str:
    parts = [case.kind.value]
    if case.mode:
        parts.append(case.mode)
    if case.genre_profile is not None:
        parts.append(case.genre_profile.value)
    return "/".join(parts)


def run_style_diagnostics(
    case: HumanizeCase,
    output_text: str,
    fidelity: FidelityReport | None,
) -> StyleDiagnosticReport:
    """对候选输出执行自动风格诊断（fail closed 读取保真结果）。

    保真报告缺失或存在检查缺失 → verdict=fail_closed：诊断结论不得
    假装干净。保真完整时按命中类别给出 ok / warning / blocked。
    """
    reasons: list[str] = []
    fidelity_available = fidelity is not None
    missing_checks: list[str] = []
    if fidelity is None:
        reasons.append("缺少保真报告，自动风格诊断失败关闭（fail closed）。")
    else:
        missing_checks = [check.label for check in fidelity.missing_checks]
        if missing_checks:
            reasons.append(
                "保真检查存在缺失项，自动风格诊断失败关闭："
                + "、".join(dict.fromkeys(missing_checks))
            )

    hits: list[StyleHit] = []
    protocol_leak_hits: list[StyleHit] = []
    scenario = _scenario(case)
    for phrase in PROTOCOL_LEAK_PHRASES:
        _collect_hits(
            output_text, phrase, "protocol_leak", scenario, True,
            hits, protocol_leak_hits,
        )
    for phrase in STYLE_PHRASES:
        _collect_hits(
            output_text, phrase, "style_phrase", scenario, False,
            hits, protocol_leak_hits,
        )

    if not fidelity_available or missing_checks:
        return StyleDiagnosticReport(
            case_id=case.case_id,
            hits=hits,
            protocol_leak_hits=protocol_leak_hits,
            fidelity_available=fidelity_available,
            fidelity_missing_checks=list(dict.fromkeys(missing_checks)),
            verdict="fail_closed",
            reasons=reasons,
        )
    if protocol_leak_hits:
        return StyleDiagnosticReport(
            case_id=case.case_id,
            hits=hits,
            protocol_leak_hits=protocol_leak_hits,
            fidelity_available=True,
            verdict="blocked",
            reasons=[
                "检测到稳定的协议/助手身份泄漏短语，阻止发布："
                + "、".join(
                    dict.fromkeys(hit.phrase for hit in protocol_leak_hits)
                )
            ],
        )
    if any(hit.category == "style_phrase" for hit in hits):
        return StyleDiagnosticReport(
            case_id=case.case_id,
            hits=hits,
            protocol_leak_hits=[],
            fidelity_available=True,
            verdict="warning",
            reasons=["命中风格短语（仅诊断，不阻止发布）。"],
        )
    return StyleDiagnosticReport(
        case_id=case.case_id,
        hits=[],
        protocol_leak_hits=[],
        fidelity_available=True,
        verdict="ok",
    )


def _collect_hits(
    output_text: str,
    phrase: str,
    category: str,
    scenario: str,
    blocks_release: bool,
    hits: list[StyleHit],
    protocol_leak_hits: list[StyleHit],
) -> None:
    start = 0
    while True:
        position = output_text.find(phrase, start)
        if position < 0:
            return
        hit = StyleHit(
            category=category,
            phrase=phrase,
            position=position,
            evidence_span=phrase,
            scenario=scenario,
            blocks_release=blocks_release,
        )
        hits.append(hit)
        if blocks_release:
            protocol_leak_hits.append(hit)
        start = position + len(phrase)


__all__ = [
    "PROTOCOL_LEAK_PHRASES",
    "STYLE_PHRASES",
    "StyleDiagnosticReport",
    "StyleHit",
    "run_style_diagnostics",
]
