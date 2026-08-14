"""定向修订提示编译（人味化改造 Issue 05）。

修订请求只包含原任务边界（说话位置/权限/材料边界）、必要原文与保护项、
首稿正文和待修问题清单（code/位置/证据/目标）；不重新注入全量方法规则、
全量检测清单、黑名单或与当前问题无关的体裁规则。修订要求最小修改并
保留首稿中已通过部分；不得添加来源账本之外的事实、经历、例子或新论点。

模型生成合同与首稿一致：结构化输出只要求 ``final_text``；修订后的保真
结果与审稿由程序在服务层重新执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from bridges.contracts.expression_task import (
    ExpressionTaskContract,
    MaterialSufficiency,
)
from bridges.contracts.humanizer import SourceLedger
from bridges.skills.humanizer.draft_compiler import profile_style_block
from bridges.skills.humanizer.revision_policy import RevisionProblem

#: 修订提示编译版本（审计与观测用）。
REVISION_PROMPT_VERSION = "article-revision-v1"

#: 结构化输出合同：修订也只产出完整候选正文。
REVISION_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"final_text": {"type": "string"}},
    "required": ["final_text"],
}

#: 模型输出 token 上限（修订正文，与首稿一致）。
REVISION_MAX_TOKENS = 4096


@dataclass(frozen=True)
class RevisionPromptResult:
    """一次修订提示编译的结果：提示文本 + 脱敏编译元数据（观测用）。"""

    system_prompt: str
    problem_count: int
    protected_span_count: int
    source_text_included: bool


def _material_boundary(contract: ExpressionTaskContract, ledger: SourceLedger | None) -> str:
    """材料边界：修订同样只能消费账本材料，不得扩大事实范围。"""
    entry_count = len(ledger.entries) if ledger is not None else 0
    base = (
        f"正文只能使用原有材料（账本条目 {entry_count} 条）中的事实；"
        "不得添加材料之外的新事实、经历、例子或论点。"
    )
    if (
        contract.material_sufficiency == MaterialSufficiency.USE_PLACEHOLDERS
        and entry_count == 0
    ):
        base += "材料缺失：空缺以「［待补充：……］」占位呈现，不编造内容。"
    return base


def _permission_line(contract: ExpressionTaskContract) -> str:
    """第一人称与假设权限：修订不得扩大权限边界。"""
    if contract.first_person_permission:
        first_person = "允许：可使用第一人称，但只用于当下判断或材料中已有的亲历，不虚构亲历。"
    else:
        first_person = (
            "不使用：不写第一人称亲历（时间、事件、体验）；不要求加入生活场景。"
        )
    if contract.hypothetical_permission:
        hypothetical = "允许：可使用假设，但必须明确标注（比如/假设/设想），且不承载事实。"
    else:
        hypothetical = "不使用：材料没有的内容不要推测，假设内容一律不写。"
    return f"第一人称：{first_person}\n假设：{hypothetical}"


def _problem_lines(problems: Sequence[RevisionProblem]) -> str:
    lines: list[str] = []
    for index, problem in enumerate(problems, start=1):
        location = (
            f"（位置 {problem.location.start}-{problem.location.end}）"
            if problem.location is not None
            else ""
        )
        evidence = f"证据「{problem.evidence}」" if problem.evidence else problem.code
        lines.append(
            f"{index}. {problem.category}（{problem.code}）{location}：{evidence}。"
            f"目标：{problem.target}"
        )
    return "\n".join(lines)


def compile_revision_prompt(
    contract: ExpressionTaskContract,
    *,
    draft_text: str,
    problems: Sequence[RevisionProblem],
    ledger: SourceLedger | None,
    source_text: str = "",
    source_label: str = "",
    profile_context: str | None = None,
) -> RevisionPromptResult:
    """编译一次定向修订提示：只含原任务边界、必要原文/保护项、首稿与待修问题。

    调用方负责裁决（``adjudicate_revision``）与预算/停止/开关检查；本函数
    只做确定性文本编译，不访问模型、账户或知识库。``profile_context``
    （Issue 04）以「风格与背景偏好」用途引用，不作为事实来源，不改变
    材料边界与证据合同。
    """
    speaker = contract.speaker_position or "用户本人（以作者身份）"
    permission = _permission_line(contract)
    material_boundary = _material_boundary(contract, ledger)
    protected_span_count = 0
    if ledger is not None and ledger.compile_summary is not None:
        protected_span_count = sum(
            ledger.compile_summary.protected_spans_by_kind.values()
        )
    protection_note = (
        f"原文中的引语、专名、数字、日期、公式、URL、引用与用户指定措辞"
        f"必须原样保持（账本保护项 {protected_span_count} 个，来源哈希不变）。"
    )
    source_section = (
        "【原文（保护项来源，不得改写的部分必须原样保持）】\n"
        + source_text
        if source_text.strip()
        else "【原文】（本次没有粘贴原文：正文只能使用材料内已有事实。）"
    )
    profile_block = profile_style_block(profile_context)

    system_prompt = f"""你是 BridGes 文章表达助手，正在对首稿做一次定向修订
（修订提示版本 {REVISION_PROMPT_VERSION}）。

【说话位置】{speaker}

【权限】（修订不得扩大权限边界）
{permission}

【材料边界】{material_boundary}
{profile_block}
{protection_note}

【本次只修以下问题】（只动这些位置，其余一律保持）
{_problem_lines(problems)}

【首稿】
{draft_text}

{source_section}

【修订要求】
- 只修上表列出的问题，保留首稿其他全部内容（已通过部分不动）。
- 最小修改：能改一处就不要改整段；不重写与问题无关的句子。
- 不得添加来源账本之外的事实、经历、例子或新论点；不得改变原有引语、
  专名、数字、日期、公式、URL 与引用关系。
- 修订后输出完整候选正文。

【输出要求】只输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 修订后的完整正文}}"""

    return RevisionPromptResult(
        system_prompt=system_prompt,
        problem_count=len(problems),
        protected_span_count=protected_span_count,
        source_text_included=bool(source_text.strip()),
    )


__all__ = [
    "REVISION_PROMPT_VERSION",
    "REVISION_OUTPUT_JSON_SCHEMA",
    "REVISION_MAX_TOKENS",
    "RevisionPromptResult",
    "compile_revision_prompt",
]
