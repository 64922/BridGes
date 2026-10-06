"""工单 39：`global-chat-lightweight-v2` 旧策略的冻结回放（仅评测使用）。

盲评必须比较同一模型下的三个真实策略臂，而 v2 已在改进工单 21/22 被
v3/v4 取代、不再存在于产品路径。本模块按提交 `079faab6` 中的
`src/bridges/chat/lightweight_policy.py` 原文冻结回放 v2 的规则表、形态
启发与渲染文本，只做两处必要适配：

1. 旧净室合同模块路径 `bridges.skills.humanizer.contract_compiler` 已随
   文章人味化编排退役，改为现行 `bridges.expression_task.contract_compiler`；
2. 快照类型改用现行 `ChatLightweightPolicySnapshot`（字段为旧版的超集，
   缺省字段由默认值补齐），使同一 ChatService 管线可序列化与重试。

v2 的旧维度白名单、固定 1024 输出与“解释必确认收尾/情绪必行动”等行为
全部按原样保留，供盲评作为历史对照，不进入任何产品路径。输出额度由评测
臂包装器统一为可比口径，见 `bridges.evaluation.human_expression`。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from bridges.chat.lightweight_policy import (
    ChatLightweightPolicySnapshot,
    ChatResponseForm,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import (
    ConversationMode,
    ExpressionTaskContract,
    Surface,
)
from bridges.contracts.profiles import ProfileSliceItem
from bridges.expression_task.contract_compiler import (
    CompileRequest,
    compile_task_contract,
)

#: 被回放的历史策略版本（评分卡与运行锁必须记录真实值）。
LEGACY_V2_STRATEGY_VERSION = "global-chat-lightweight-v2"
SAFE_BASELINE_POLICY_VERSION = "global-humanized-writing-safe-baseline-v1"
LEGACY_V2_SOURCE_RECORD = (
    "BridGes 原创净室规则（评测冻结回放：079faab6 的 global-chat-lightweight-v2；"
    "仅评测对照使用，不进入产品路径）"
)

_DEFAULT_RESOURCE = object()

#: 旧维度白名单：默认回放中原子画像无类别，会被丢弃（改进工单 22 修复的
#: 真实问题），盲评中作为历史对照保留。
_LEGACY_ALLOWED_PROFILE_DIMENSIONS = frozenset(
    {
        "basic_information",
        "academic_status",
        "expression_habit",
    }
)
_MAX_PROFILE_ITEMS = 6

_PROTECTED_REGIONS_STATEMENT = (
    "引用、数值、代码、公式、链接、JSON、结构化工具结果、确定性错误提示、"
    "加载/停止状态和协议字段属于受保护区，必须原样保留，保持准确。工具结果"
    "外可以加简短说明，但不得改写证据。"
)

_PRIORITY_STATEMENT = (
    "以上规则的优先级低于用户本轮明确表达的语气与篇幅要求，也低于本轮任务"
    "合同（如教学模式合同或模块任务要求）；与两者冲突时先满足用户要求与"
    "任务合同。"
)

GLOBAL_DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    ("answer-first", "先直接回答当前问题，再补充必要背景。"),
    ("brief-answer", "简单问题允许一两句说清，不无谓扩写。"),
    ("structure-optional", "只有标题或列表确实帮助扫描时才使用，否则用连续句子。"),
    ("no-filler-ending", "没有真实下一步时不追加礼貌性尾句。"),
)

FORM_RULES: dict[ChatResponseForm, tuple[tuple[str, str], ...]] = {
    ChatResponseForm.SHORT_ANSWER: (
        ("fact-only", "只回答被问的事实或数值，不自动引申。"),
        ("uncertainty-stated", "不确定时直接说明依据与不确定部分。"),
        ("no-lecture", "不把一句话问答扩成完整讲解。"),
    ),
    ChatResponseForm.EXPLANATION: (
        ("level-adapted", "从用户当前水平开始解释，先给结论再给原理。"),
        ("concrete-example", "用具体例子或对比帮助理解，不编造例子细节。"),
        ("scope-limited", "一次只讲清被问的部分，不倾倒全部相关知识。"),
        ("check-understanding", "结尾用一句话确认是否解决，而不是继续展开。"),
    ),
    ChatResponseForm.ADVICE: (
        ("tradeoff-first", "先给出明确取舍与推荐，再说明理由。"),
        ("condition-stated", "说明建议适用的条件与不适用的情况。"),
        ("verify-path", "不确定时给出可验证的途径，不空泛打包票。"),
    ),
    ChatResponseForm.CORRECTION: (
        ("evidence-based", "直接说明对方说法与依据不符之处。"),
        ("boundary-stated", "说明正确版本及其适用边界。"),
        ("no-condescension", "纠正时不居高临下，就事论事。"),
        ("uncertainty-acknowledged", "自己不确定的部分也明确承认。"),
    ),
    ChatResponseForm.EMPATHY: (
        ("visible-info-only", "情绪承接只能引用当前对话已出现的信息。"),
        ("empathy-with-action", "承接后必须带来具体判断、帮助或下一步。"),
        ("no-mind-reading", "不推测用户的情绪、人格、经历或亲密关系。"),
        ("no-parroting", "不复述用户原句来表演理解。"),
    ),
    ChatResponseForm.CLARIFICATION: (
        ("one-question", "只问一个最高价值问题。"),
        ("reason-stated", "说明为什么需要这个信息。"),
        ("no-interrogation", "不连珠炮式追问多个问题。"),
    ),
    ChatResponseForm.TOOL_RESULT: (
        ("result-first", "先直接说明工具返回的结果。"),
        ("evidence-verbatim", "不改写工具原始结果、错误码或协议字段。"),
        ("brief-interpretation", "可以加简短解读，但标注哪些是你的解读。"),
    ),
    ChatResponseForm.ERROR_REFUSAL: (
        ("reason-direct", "直接说明原因与边界。"),
        ("no-soothing", "不客服式安抚或励志包装。"),
        ("no-condescension", "不居高临下。"),
        ("next-step", "如有可做的下一步，明确给出；没有就停止。"),
    ),
    ChatResponseForm.LESSON: (
        ("lesson-contract", "遵循本轮教学计划、课时与证据门合同。"),
        ("step-sequence", "按教学步骤推进，先完成当前一步。"),
        ("short-check", "用一句话检查理解，而不是固定完整测验。"),
        ("no-forced-structure", "普通短问不自动加载目标、先修、练习和继续邀请。"),
        ("stopping-rule", "没有真实下一步时直接停下。"),
    ),
    ChatResponseForm.COMPACT_DEFAULT: (
        ("minimal-answer", "用最少的句子完成请求。"),
        ("no-expansion", "不扩成长文，不追加无关建议。"),
        ("stop-when-done", "完成请求后直接结束，不续写。"),
    ),
}

FORM_LABELS: dict[ChatResponseForm, str] = {
    ChatResponseForm.SHORT_ANSWER: "短答",
    ChatResponseForm.EXPLANATION: "解释",
    ChatResponseForm.ADVICE: "建议",
    ChatResponseForm.CORRECTION: "纠错/不同意",
    ChatResponseForm.EMPATHY: "情绪承接",
    ChatResponseForm.CLARIFICATION: "澄清",
    ChatResponseForm.TOOL_RESULT: "工具结果说明",
    ChatResponseForm.ERROR_REFUSAL: "错误/拒答",
    ChatResponseForm.LESSON: "学习课时",
    ChatResponseForm.COMPACT_DEFAULT: "紧凑默认",
}

_EMPATHY_RE = re.compile(
    r"难过|伤心|生气|愤怒|焦虑|压力|好累|好烦|烦死|孤独|委屈|崩溃|\bemo\b|"
    r"郁闷|低落|不开心|担心|害怕|紧张|失望|没劲|撑不住|坚持不下去"
)
_CORRECTION_RE = re.compile(
    r"(?:对吗|对不对|是吗|是真的吗|是不是真的|是这样吗|你觉得呢|你说呢|你同意吗)"
)
_ADVICE_RE = re.compile(
    r"怎么办|建议|推荐|该不该|要不要|好不好|怎么选|选哪个|哪个好|怎么处理"
)
_CLARIFICATION_RE = re.compile(
    r"^帮我(?:看看|看下|查查|查一下|找找|解释|讲讲|说说|整理|写)?\s*$|"
    r"^(?:解释一下|讲一下|说一下|看看|介绍一下)\s*$"
)
_EXPLANATION_RE = re.compile(
    r"为什么|怎么|如何|原理|区别|解释|理解|讲讲|介绍一下|是什么|什么是|啥是|何谓|含义|介绍"
)
_FACT_QUESTION_RE = re.compile(
    r"多少|几|什么时间|在哪|是谁|多少钱|多快|多大|多长|什么时候"
)
_SHORT_TEXT_LIMIT = 40


def _mode_value(mode: ChatMode | str) -> str:
    return mode.value if isinstance(mode, ChatMode) else str(mode)


def _to_conversation_mode(mode: ChatMode | str) -> ConversationMode:
    return (
        ConversationMode.LEARNING
        if _mode_value(mode) == ChatMode.STUDY.value
        else ConversationMode.CASUAL
    )


def _validate_contract(contract: ExpressionTaskContract) -> None:
    if contract.surface != Surface.CHAT:
        raise ValueError("非聊天表面契约不进入普通聊天轻量策略。")
    if contract.compute_version_hash() != contract.version_hash:
        raise ValueError("表达任务契约快照哈希不一致，拒绝编译轻量策略。")


def _allowed_profile_values(
    profile_items: Sequence[ProfileSliceItem],
) -> tuple[str, ...]:
    """v2 原文：只保留允许维度的画像值，其余维度不进入表达策略。"""
    values: list[str] = []
    for item in profile_items:
        value = (item.value_or_rule or "").strip()
        if not value or item.dimension not in _LEGACY_ALLOWED_PROFILE_DIMENSIONS:
            continue
        values.append(value)
    return tuple(values[:_MAX_PROFILE_ITEMS])


def detect_legacy_response_form(
    text: str,
    mode: ChatMode | str,
    *,
    tool_error: bool = False,
    tool_result: bool = False,
    refusal: bool = False,
    lesson: bool = False,
) -> ChatResponseForm:
    """v2 原文的确定性形态路由（不含续接/显式约束信号）。"""
    if refusal or tool_error:
        return ChatResponseForm.ERROR_REFUSAL
    if tool_result:
        return ChatResponseForm.TOOL_RESULT
    if lesson and _mode_value(mode) == ChatMode.STUDY.value:
        return ChatResponseForm.LESSON

    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return ChatResponseForm.COMPACT_DEFAULT
    if _ADVICE_RE.search(normalized):
        return ChatResponseForm.ADVICE
    if _EMPATHY_RE.search(normalized):
        return ChatResponseForm.EMPATHY
    if _CORRECTION_RE.search(normalized):
        return ChatResponseForm.CORRECTION
    if _CLARIFICATION_RE.search(normalized):
        return ChatResponseForm.CLARIFICATION
    if _EXPLANATION_RE.search(normalized):
        return ChatResponseForm.EXPLANATION
    if len(normalized) <= _SHORT_TEXT_LIMIT and _FACT_QUESTION_RE.search(normalized):
        return ChatResponseForm.SHORT_ANSWER
    return ChatResponseForm.COMPACT_DEFAULT


class LegacyV2LightweightPolicyCompiler:
    """v2 冻结编译器：签名与行为按 079faab6 原文回放。"""

    def __init__(
        self,
        resource: object | None = _DEFAULT_RESOURCE,
        *,
        instruction: str | None = None,
        version: str | None = None,
    ) -> None:
        self._resource = None if resource is None else object()
        self._instruction = instruction
        self._version = version or LEGACY_V2_STRATEGY_VERSION

    def compile(
        self,
        mode: ChatMode | str,
        *,
        user_text: str = "",
        expression_contract: ExpressionTaskContract | None = None,
        profile_slice_id: str | None = None,
        profile_items: Sequence[ProfileSliceItem] = (),
        profile_context: str | None = None,
        profile_failed: bool = False,
        tool_error: bool = False,
        tool_result: bool = False,
        refusal: bool = False,
        lesson: bool = False,
        existing_snapshot: ChatLightweightPolicySnapshot | dict[str, Any] | None = None,
    ) -> ChatLightweightPolicySnapshot:
        if existing_snapshot is not None:
            snapshot = (
                existing_snapshot
                if isinstance(existing_snapshot, ChatLightweightPolicySnapshot)
                else ChatLightweightPolicySnapshot.model_validate(existing_snapshot)
            )
            if snapshot.snapshot_complete:
                return snapshot

        mode_value = _mode_value(mode)
        if self._resource is None or profile_failed:
            reason = (
                "profile_slice_unavailable"
                if profile_failed
                else "policy_resource_unavailable"
            )
            return self._fallback_snapshot(mode_value, reason)

        if expression_contract is not None:
            _validate_contract(expression_contract)
            contract = expression_contract
        else:
            compiled = compile_task_contract(
                CompileRequest(
                    content=user_text,
                    surface_hint=Surface.CHAT,
                    conversation_mode=_to_conversation_mode(mode),
                )
            )
            contract = compiled.contract

        values = _allowed_profile_values(profile_items)
        form = detect_legacy_response_form(
            user_text,
            mode,
            tool_error=tool_error,
            tool_result=tool_result,
            refusal=refusal,
            lesson=lesson,
        )
        rules = GLOBAL_DEFAULT_RULES + FORM_RULES[form]
        degradation = None
        if form == ChatResponseForm.COMPACT_DEFAULT and user_text.strip():
            degradation = "low_confidence_compact_default"
        return ChatLightweightPolicySnapshot(
            version=self._version,
            mode=mode_value,
            form=form,
            rule_ids=tuple(rule_id for rule_id, _ in rules),
            rule_count=len(rules),
            contract_schema_version=contract.schema_version,
            contract_version_hash=contract.version_hash,
            profile_slice_id=profile_slice_id,
            profile_items=values,
            profile_context=profile_context,
            snapshot_complete=True,
            fallback_reason=None,
            degradation_reason=degradation,
            source_record=LEGACY_V2_SOURCE_RECORD,
            system_block=self._render(mode_value, form, rules, values),
        )

    def seed(self, mode: ChatMode | str) -> ChatLightweightPolicySnapshot:
        mode_value = _mode_value(mode)
        if self._resource is None:
            return self._fallback_snapshot(mode_value, "policy_resource_unavailable")
        return ChatLightweightPolicySnapshot(
            version=self._version,
            mode=mode_value,
            form=ChatResponseForm.COMPACT_DEFAULT,
            snapshot_complete=False,
            source_record=LEGACY_V2_SOURCE_RECORD,
            system_block=self._render(
                mode_value, ChatResponseForm.COMPACT_DEFAULT, GLOBAL_DEFAULT_RULES, ()
            ),
        )

    def _fallback_snapshot(
        self, mode: str, reason: str
    ) -> ChatLightweightPolicySnapshot:
        return ChatLightweightPolicySnapshot(
            version=SAFE_BASELINE_POLICY_VERSION,
            mode=mode,
            form=ChatResponseForm.COMPACT_DEFAULT,
            profile_slice_id=None,
            profile_items=(),
            profile_context=None,
            snapshot_complete=True,
            fallback_reason=reason,
            source_record=LEGACY_V2_SOURCE_RECORD,
            system_block=(
                "【全局轻量有人味表达策略·安全基线】\n"
                f"策略版本：{SAFE_BASELINE_POLICY_VERSION}\n"
                "只完成任务本身，使用清楚、诚实、简洁的中文。保持原始事实、"
                "数字、限定条件、代码、公式、JSON、引用、链接、错误码、工具"
                "结果和协议字段不变；不规避 AI 检测、不冒充真人或名人、不伪造"
                "经历、来源或引用。\n"
                f"{_PRIORITY_STATEMENT}\n"
                "本轮没有可用画像信息，不得自行推断用户经历、身份、人格或偏好。\n"
                + _PROTECTED_REGIONS_STATEMENT
            ),
        )

    def _render(
        self,
        mode: str,
        form: ChatResponseForm,
        rules: Sequence[tuple[str, str]],
        profile_items: tuple[str, ...],
    ) -> str:
        rule_lines = "\n".join(
            f"{index}. {text}" for index, (_, text) in enumerate(rules, 1)
        )
        profile = (
            "\n允许使用的当前账户画像信息（只影响称呼、篇幅、专业程度与表达偏好）：\n"
            + "\n".join(f"- {value}" for value in profile_items)
            if profile_items
            else "\n本轮没有可用画像信息，不得自行推断用户经历、身份、人格或偏好。"
        )
        return (
            "【全局轻量有人味表达策略】\n"
            f"策略版本：{self._version}｜"
            f"对话模式：{mode}｜回答形态：{FORM_LABELS[form]}\n"
            f"{rule_lines}\n"
            f"{_PRIORITY_STATEMENT}\n"
            "只作用于模型生成的自然语言正文；"
            + _PROTECTED_REGIONS_STATEMENT
            + f"{profile}"
        )


__all__ = [
    "LEGACY_V2_STRATEGY_VERSION",
    "LEGACY_V2_SOURCE_RECORD",
    "SAFE_BASELINE_POLICY_VERSION",
    "FORM_LABELS",
    "FORM_RULES",
    "GLOBAL_DEFAULT_RULES",
    "LegacyV2LightweightPolicyCompiler",
    "detect_legacy_response_form",
]
