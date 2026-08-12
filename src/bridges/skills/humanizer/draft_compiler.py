"""首稿正向生成 profile 与提示编译（人味化改造 Issue 04）。

每次首稿只编译当前 profile 的 6—10 条高优先级正向规则，从版本化
``ExpressionTaskContract`` 与来源账本取得任务、权限与材料边界；提示不
包含内部方法 ID、全量检测清单或为审计而写给模型的冗长标签。轻度尽量
保留词汇、段落与作者声音；标准允许调整段落推进与句式顺序；深度允许
重构组织与叙述；三档共享来源硬门与不伤害原则（原文已经自然时不得强行
加场景、故事、比喻、第一人称或金句）。

模型生成合同以候选正文为中心：结构化输出只要求 ``final_text``，修改
清单、保真结果与模式命中由程序在服务层生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bridges.contracts.expression import Genre
from bridges.contracts.expression_task import (
    ExpressionTaskContract,
    MaterialSufficiency,
    Operation,
    RewriteIntensity,
)
from bridges.contracts.humanizer import SourceLedger
from bridges.skills.humanizer.genre_rules import genre_rule_set
from bridges.skills.humanizer.method_rules import METHOD_RULES, MethodRule

#: 首稿提示编译版本（审计与观测用）。
DRAFT_PROMPT_VERSION = "article-draft-v1"

#: 结构化输出合同：以候选正文为中心，不要求模型产出审计元数据。
DRAFT_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"final_text": {"type": "string"}},
    "required": ["final_text"],
}

#: 模型输出 token 上限（首稿正文）。
DRAFT_MAX_TOKENS = 4096

#: 三档共享的不伤害原则：原文已经自然时不得强行加场景、故事、比喻、
#: 第一人称或金句（Issue 04 验收：三档共享来源硬门与不伤害）。
_NO_HARM_INSTRUCTION = (
    "不伤害：原文已经自然时不得强行加场景、故事、比喻、第一人称、金句或"
    "升华结尾；不虚构作者或用户的亲历、朋友对话与具体场景。"
)


@dataclass(frozen=True)
class DraftProfile:
    """一档正向生成 profile：当前场景真正相关的少量高优先级规则。"""

    intensity: RewriteIntensity
    label: str
    summary: str
    rule_ids: tuple[str, ...]


def _rule_map() -> dict[str, MethodRule]:
    return {rule.rule_id: rule for rule in METHOD_RULES}


_RULES = _rule_map()


def _profile(
    intensity: RewriteIntensity,
    label: str,
    summary: str,
    rule_ids: tuple[str, ...],
) -> DraftProfile:
    for rule_id in rule_ids:
        if rule_id not in _RULES:
            raise AssertionError(f"未知方法规则 ID：{rule_id}")
    return DraftProfile(
        intensity=intensity,
        label=label,
        summary=summary,
        rule_ids=rule_ids,
    )


_PROFILES: dict[RewriteIntensity, DraftProfile] = {
    RewriteIntensity.LIGHT: _profile(
        RewriteIntensity.LIGHT,
        "轻度",
        "尽量保留原文词汇、段落结构与作者声音，只做必要收敛，不做结构调整。",
        (
            # 保留声音：允许必要重复、名词化还原动词
            "rewrite.allow-natural-repeat",
            "rewrite.restore-verbs",
            # 轻量收敛：压缩填充、删去不承担逻辑的连词
            "detect.filler-phrase",
            "rewrite.trim-connectors",
            # 事实护栏（共享硬门）
            "fact.relevance-not-causality",
            "fact.no-significance-without-test",
            # 观点与依据相邻、承认复杂性
            "voice.evidence-near-opinion",
            "voice.admit-uncertainty",
        ),
    ),
    RewriteIntensity.STANDARD: _profile(
        RewriteIntensity.STANDARD,
        "标准",
        "允许调整段落推进与句式顺序，让读者沿下一问读下去；不改变任务边界与材料。",
        (
            # 推进与句式
            "rewrite.main-clause-first",
            "rewrite.tail-connection",
            "rewrite.split-attributive",
            "rewrite.restore-verbs",
            "rewrite.rhythm-contrast",
            "detect.filler-phrase",
            # 事实护栏（共享硬门）
            "fact.relevance-not-causality",
            "fact.metric-not-real-world",
            # 观点与依据相邻
            "voice.evidence-near-opinion",
        ),
    ),
    RewriteIntensity.DEEP: _profile(
        RewriteIntensity.DEEP,
        "深度",
        "允许重构组织与叙述，消除翻案句、包装词与夸大意义；材料边界与来源硬门不变。",
        (
            "rewrite.main-clause-first",
            "rewrite.tail-connection",
            "rewrite.rhythm-contrast",
            "detect.synonym-loop",
            "detect.negation-parallel",
            "rewrite.direct-judgment",
            "detect.false-range",
            "detect.significance-hype",
            "fact.sample-not-extrapolation",
            "voice.no-fake-detail",
        ),
    ),
}


@dataclass(frozen=True)
class DraftPromptResult:
    """一次首稿提示编译的结果：提示文本 + 脱敏编译元数据（观测用）。"""

    system_prompt: str
    profile: RewriteIntensity
    label: str
    rule_count: int
    compiled_rule_ids: tuple[str, ...]
    genre: Genre | None
    scene_profile: str


def _reader_next_question(contract: ExpressionTaskContract) -> str:
    """按材料充分度与操作派生「读者下一问」推进指令（确定性，无模型调用）。"""
    if contract.material_sufficiency == MaterialSufficiency.ASK_ONE_QUESTION:
        return "材料不足：先回答用户追问的问题，或交付边界明确的短稿，不得补编材料之外的事实。"
    if contract.material_sufficiency == MaterialSufficiency.SHORTEN:
        return "材料不足：只处理已有材料，交付边界明确的短稿，不扩大事实范围。"
    if contract.material_sufficiency == MaterialSufficiency.USE_PLACEHOLDERS:
        return "材料缺失：空缺以「［待补充：……］」占位呈现，不编造内容。"
    if contract.operation == Operation.REWRITE:
        return (
            "沿读者下一问推进：改写后每段结束处，先设想读者最可能追问的一个问题"
            "（为什么/然后呢/怎么做），再用下一段回答；不要在读者没有问的地方展开。"
        )
    return (
        "沿读者下一问推进：每段先回答读者基于前文最可能提出的问题，再引出下一段的"
        "材料；材料支持到哪里就写到哪里，读者追问超出材料的部分明确写「目前材料里"
        "没有答案」或用占位标注。"
    )


def _material_boundary(contract: ExpressionTaskContract, ledger: SourceLedger | None) -> str:
    """材料边界：正文只能消费账本材料，新增可核查内容必须可绑定来源。"""
    entry_count = len(ledger.entries) if ledger is not None else 0
    scope_label = {
        "original_only": "只使用用户提供的原文",
        "knowledge_base": "只使用原文与当前账户知识库材料",
        "web": "只使用原文与证据合同列出的外部来源",
    }.get(contract.source_scope.value, "只使用用户提供的原文")
    base = (
        f"正文只能使用上方材料（{scope_label}，账本条目 {entry_count} 条）中的事实；"
        "新增可核查内容必须来自材料，超出材料的部分只能明确标注为待确认，不得编造。"
    )
    if contract.operation == Operation.GENERATE_BY_TOPIC and entry_count == 0:
        base += "本次没有用户材料：正文只能做通用性说明，涉及具体事实、数字、案例时必须留占位。"
    return base


def _permission_line(contract: ExpressionTaskContract) -> str:
    """第一人称与假设权限：无权限时明确要求模型不得加入生活场景或亲历。"""
    if contract.first_person_permission:
        first_person = "允许：可使用第一人称，但只用于当下判断或材料中已有的亲历，不虚构亲历。"
    else:
        first_person = (
            "不使用：不写第一人称亲历（时间、事件、体验）；不要求加入生活场景；"
            "当下判断（我认为/在我看来）只在确实需要时使用。"
        )
    if contract.hypothetical_permission:
        hypothetical = "允许：可使用假设，但必须明确标注（比如/假设/设想），且不承载事实。"
    else:
        hypothetical = "不使用：材料没有的内容不要推测，假设内容一律不写。"
    return f"第一人称：{first_person}\n假设：{hypothetical}"


def _genre_block(genre: Genre | None) -> str:
    """体裁 profile 只规定任务目标、风险与可选表达方式（无必现条件）。"""
    profile = genre_rule_set(genre)
    lines = [
        f"任务目标：{profile.task_goal}",
    ]
    if profile.risks:
        lines.append("风险：" + "；".join(profile.risks) + "。")
    if profile.optional_devices:
        lines.append("可选表达：" + "；".join(profile.optional_devices) + "。")
    if profile.human_responsibility:
        lines.append("责任：" + profile.human_responsibility)
    return "\n".join(lines)


def compile_draft_prompt(
    contract: ExpressionTaskContract,
    *,
    source_text: str = "",
    source_label: str = "",
    ledger: SourceLedger | None = None,
) -> DraftPromptResult:
    """编译一次首稿提示：只含当前 profile 的 6—10 条正向规则与任务边界。

    调用方负责在模型调用前完成契约版本校验与账本编译；本函数只做确定性
    文本编译，不访问模型、账户或知识库。
    """
    profile = _PROFILES[contract.rewrite_intensity]
    rules = [_RULES[rule_id] for rule_id in profile.rule_ids]
    rule_lines = "\n".join(f"- {rule.label}：{rule.instruction}" for rule in rules)
    genre = contract.genre

    speaker = contract.speaker_position or "用户本人（以作者身份）"
    permission = _permission_line(contract)
    material_boundary = _material_boundary(contract, ledger)
    reader_question = _reader_next_question(contract)
    material_section = (
        "【材料】" + (source_label or "原文") + "：\n" + source_text
        if source_text.strip()
        else "【材料】（无用户材料：正文只能做通用性说明，具体事实与案例留占位）"
    )

    system_prompt = f"""你是 BridGes 文章表达助手，以作者身份起草文章正文
（提示编译版本 {DRAFT_PROMPT_VERSION}）。

【档位】{profile.label}：{profile.summary}
{_NO_HARM_INSTRUCTION}

【说话位置】{speaker}

【读者下一问】{reader_question}

【材料边界】{material_boundary}

【权限】
{permission}

【体裁任务】
{_genre_block(genre)}

【本次写作要点】（只执行以下 {len(rule_lines.splitlines())} 条，不再叠加其他规则）
{rule_lines}

{material_section}

【输出要求】只输出 JSON，不得输出 JSON 之外的任何内容：
{{"final_text": 候选正文全文}}"""

    return DraftPromptResult(
        system_prompt=system_prompt,
        profile=profile.intensity,
        label=profile.label,
        rule_count=len(profile.rule_ids),
        compiled_rule_ids=profile.rule_ids,
        genre=genre,
        scene_profile=f"{(genre.value if genre else 'generic')}:{contract.rewrite_intensity.value}",
    )


__all__ = [
    "DRAFT_PROMPT_VERSION",
    "DRAFT_OUTPUT_JSON_SCHEMA",
    "DRAFT_MAX_TOKENS",
    "DraftProfile",
    "DraftPromptResult",
    "compile_draft_prompt",
]
