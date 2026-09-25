"""普通聊天轻量有人味策略（人味化改造 Issue 07）。

该模块重建普通聊天的全局轻量表达策略：不再把文章清洗规则、全量方法规则
块或通用黑名单注入每次回答，而是根据当前轮意图、固定对话模式、回答形态
和允许的最小画像切片，每轮只编译少量高优先级正向规则。

回答形态路由是确定性文本启发，不调用模型：系统信号（工具失败、工具结果、
拒答、真实课时任务）优先于文本信号；低置信一律落到紧凑默认，不扩成长文。
默认声音是自然、直接、克制——先完成用户此刻的请求，没有真实下一步就停下。

快照与重试：编译结果是不可变 ``ChatLightweightPolicySnapshot``，同一任务
重试必须复用原快照（调用方回传 ``existing_snapshot``），不因规则热更新
漂移形态或规则；旧版快照（不含新字段）反序列化后原样复用。策略编译、
保护恢复与审计异常都不得触发第二次模型生成。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import (
    ConversationMode,
    ExpressionTaskContract,
    Surface,
)
from bridges.contracts.profiles import ProfileSliceItem
from bridges.skills.humanizer.contract_compiler import (
    CompileRequest,
    compile_task_contract,
)

#: 当前轻量策略版本（V2 issue 04：加入优先级声明并把数值列入受保护区）。
#: 重试回传旧快照时版本保持旧值（快照优先，不重编译）。
GLOBAL_CHAT_LIGHTWEIGHT_VERSION = "global-chat-lightweight-v2"
#: 安全基线版本（资源缺失或画像不可用时使用，值保持不变以兼容旧快照）。
SAFE_BASELINE_POLICY_VERSION = "global-humanized-writing-safe-baseline-v1"
GLOBAL_CHAT_LIGHTWEIGHT_SOURCE = (
    "BridGes 原创净室规则（见 src/bridges/skills/humanizer/skill/CLEAN_ROOM.md）"
)

_DEFAULT_RESOURCE = object()

#: 允许进入表达策略的最小画像维度：称呼、篇幅、专业程度与表达偏好
#: （Issue 07 AC9 封闭清单）。其余维度（兴趣、阶段目标、情绪、经历等）
#: 不得影响回答表达。
_ALLOWED_PROFILE_DIMENSIONS = frozenset(
    {
        "basic_information",
        "academic_status",
        "expression_habit",
    }
)
_MAX_PROFILE_ITEMS = 6

#: 受保护区固定句（渲染与安全基线共用，避免字面漂移）。
_PROTECTED_REGIONS_STATEMENT = (
    "引用、数值、代码、公式、链接、JSON、结构化工具结果、确定性错误提示、"
    "加载/停止状态和协议字段属于受保护区，必须原样保留，保持准确。工具结果"
    "外可以加简短说明，但不得改写证据。"
)

#: 优先级固定句（渲染与安全基线共用）：表达规则让位于用户与任务合同。
_PRIORITY_STATEMENT = (
    "以上规则的优先级低于用户本轮明确表达的语气与篇幅要求，也低于本轮任务"
    "合同（如教学模式合同或模块任务要求）；与两者冲突时先满足用户要求与"
    "任务合同。"
)


class ChatResponseForm(StrEnum):
    """原创回答形态：编译器按形态选择少量正向规则。

    低置信时使用紧凑默认，不扩成长文；工具结果、错误/拒答由系统信号
    确定，学习课时只在真实课时任务（teaching 合同）下启用。
    """

    SHORT_ANSWER = "short_answer"
    EXPLANATION = "explanation"
    ADVICE = "advice"
    CORRECTION = "correction"
    EMPATHY = "empathy"
    CLARIFICATION = "clarification"
    TOOL_RESULT = "tool_result"
    ERROR_REFUSAL = "error_refusal"
    LESSON = "lesson"
    COMPACT_DEFAULT = "compact_default"


# ---------------------------------------------------------------------------
# 规则库：每条规则是 (规则 ID, 中文正文)。渲染只输出中文正文；
# 规则 ID 进快照字段供审计与测试，不注入模型提示词（不含内部方法 ID）。
# ---------------------------------------------------------------------------

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

#: 形态中文标签（渲染与审计可读）。
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


# ---------------------------------------------------------------------------
# 确定性形态路由（不调用模型）
# ---------------------------------------------------------------------------

_EMPATHY_RE = re.compile(
    r"难过|伤心|生气|愤怒|焦虑|压力|好累|好烦|烦死|孤独|委屈|崩溃|\bemo\b|"
    r"郁闷|低落|不开心|担心|害怕|紧张|失望|没劲|撑不住|坚持不下去"
)
#: 用户提出具体断言并请求验证 → 纠错/不同意。
_CORRECTION_RE = re.compile(
    r"(?:对吗|对不对|是吗|是真的吗|是不是真的|是这样吗|你觉得呢|你说呢|你同意吗)"
)
#: 需要建议或取舍。
_ADVICE_RE = re.compile(
    r"怎么办|建议|推荐|该不该|要不要|好不好|怎么选|选哪个|哪个好|怎么处理"
)
#: 指代不明或缺少对象的命令 → 澄清（“帮我”必须成词，避免误匹配单字）。
_CLARIFICATION_RE = re.compile(
    r"^帮我(?:看看|看下|查查|查一下|找找|解释|讲讲|说说|整理|写)?\s*$|"
    r"^(?:解释一下|讲一下|说一下|看看|介绍一下)\s*$"
)
_EXPLANATION_RE = re.compile(
    r"为什么|怎么|如何|原理|区别|解释|理解|讲讲|介绍一下|是什么|什么是|啥是|何谓|含义|介绍"
)
#: 事实属性问：短文本 + 明确事实询问词 → 短答。
_FACT_QUESTION_RE = re.compile(
    r"多少|几|什么时间|在哪|是谁|多少钱|多快|多大|多长|什么时候"
)
_SHORT_TEXT_LIMIT = 40


def detect_response_form(
    text: str,
    mode: ChatMode | str,
    *,
    tool_error: bool = False,
    tool_result: bool = False,
    refusal: bool = False,
    lesson: bool = False,
) -> ChatResponseForm:
    """按系统信号与当前轮意图确定回答形态。

    系统信号优先（工具失败/拒答 → 错误/拒答；工具结果 → 工具结果说明；
    学习模式真实课时任务 → 学习课时）；随后按文本启发依次判定情绪承接、
    纠错、建议、澄清、解释与短答；低置信一律落到紧凑默认。
    """
    if refusal or tool_error:
        return ChatResponseForm.ERROR_REFUSAL
    if tool_result:
        return ChatResponseForm.TOOL_RESULT
    if lesson and _mode_value(mode) == ChatMode.STUDY.value:
        return ChatResponseForm.LESSON

    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return ChatResponseForm.COMPACT_DEFAULT
    # 明确的建议/取舍请求优先于纯情绪词（“坚持不下去怎么办”是求助建议，
    # 而“感觉撑不住了”没有建议请求时才是情绪承接）。
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


# ---------------------------------------------------------------------------
# 策略快照与编译器
# ---------------------------------------------------------------------------


class ChatLightweightPolicySnapshot(BaseModel):
    """绑定一次生成尝试的轻量表达策略快照（不可变）。

    新增字段均有默认值：旧版快照（Issue 07 之前）反序列化后原样复用，
    不因字段缺失拒绝重试。``system_block`` 只含渲染后的中文规则与边界，
    不含画像正文全文或用户聊天正文。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(description="全局表达策略版本。")
    mode: str = Field(description="本轮固定对话模式。")
    form: ChatResponseForm = Field(
        default=ChatResponseForm.COMPACT_DEFAULT, description="本轮回答形态。"
    )
    rule_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="本轮编译的规则 ID（不含正文）。"
    )
    rule_count: int = Field(default=0, description="本轮编译的规则条数。")
    contract_schema_version: str | None = Field(
        default=None, description="消费的表达任务契约 Schema 版本。"
    )
    contract_version_hash: str | None = Field(
        default=None, description="消费的契约不可变快照哈希。"
    )
    profile_slice_id: str | None = Field(
        default=None, description="当前账户最小画像切片标识，不含画像全文。"
    )
    profile_items: tuple[str, ...] = Field(
        default_factory=tuple, description="允许影响表达的画像切片值的最小快照。"
    )
    profile_context: str | None = Field(
        default=None, description="本轮最小画像提示片段，用于同一策略快照重试。"
    )
    snapshot_complete: bool = Field(
        default=True, description="是否已经绑定本轮画像切片结果。"
    )
    fallback_reason: str | None = Field(
        default=None, description="降级到安全基线的确定性原因。"
    )
    degradation_reason: str | None = Field(
        default=None, description="形态/规则降级的确定性原因。"
    )
    source_record: str = Field(
        default=GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
        description="策略来源清洁记录标识，不包含第三方正文。",
    )
    system_block: str = Field(description="注入主生成的中文表达合同。")

    def metadata(self) -> dict[str, Any]:
        """返回可写入运行配置/模型运行锁的非秘密元数据。

        只含版本、形态、规则数量与契约哈希；不保存系统提示、画像正文
        或私人聊天正文。
        """
        return {
            "version": self.version,
            "mode": self.mode,
            "form": self.form.value,
            "rule_count": self.rule_count,
            "contract_schema_version": self.contract_schema_version,
            "contract_version_hash": self.contract_version_hash,
            "profile_slice_id": self.profile_slice_id,
            "profile_item_count": len(self.profile_items),
            "snapshot_complete": self.snapshot_complete,
            "fallback_reason": self.fallback_reason,
            "degradation_reason": self.degradation_reason,
            "source_record": self.source_record,
        }


def _mode_value(mode: ChatMode | str) -> str:
    return mode.value if isinstance(mode, ChatMode) else str(mode)


def _to_conversation_mode(mode: ChatMode | str) -> ConversationMode:
    return (
        ConversationMode.LEARNING
        if _mode_value(mode) == ChatMode.STUDY.value
        else ConversationMode.CASUAL
    )


def _validate_contract(contract: ExpressionTaskContract) -> None:
    """校验聊天消费的契约：必须是聊天表面且快照自洽。"""
    if contract.surface != Surface.CHAT:
        raise ValueError("非聊天表面契约不进入普通聊天轻量策略。")
    if contract.compute_version_hash() != contract.version_hash:
        raise ValueError("表达任务契约快照哈希不一致，拒绝编译轻量策略。")


def _allowed_profile_values(
    profile_items: Sequence[ProfileSliceItem],
) -> tuple[str, ...]:
    """只保留允许维度的画像值，其余维度不进入表达策略。"""
    values: list[str] = []
    for item in profile_items:
        value = (item.value_or_rule or "").strip()
        if not value or item.dimension not in _ALLOWED_PROFILE_DIMENSIONS:
            continue
        values.append(value)
    return tuple(values[:_MAX_PROFILE_ITEMS])


class ChatLightweightPolicyCompiler:
    """把表达任务契约、固定对话模式与最小画像切片编译为轻量策略快照。

    ``resource`` 为 ``None`` 或画像失败时降级到安全基线（只完成任务本身），
    不推断补齐画像；``existing_snapshot`` 完整时直接复用（重试路径），
    不因资源热更新改变形态或规则。
    """

    def __init__(
        self,
        resource: object | None = _DEFAULT_RESOURCE,
        *,
        instruction: str | None = None,
        version: str | None = None,
    ) -> None:
        # ``None`` 是启动/测试环境模拟策略资源缺失的显式方式；
        # 省略时使用内置原创资源（自定义 instruction/version 由调用方显式传入）。
        self._resource = None if resource is None else object()
        self._instruction = instruction
        self._version = version or GLOBAL_CHAT_LIGHTWEIGHT_VERSION

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
        """编译策略；完整已有快照优先，保证重试不受热更新影响。"""
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
                "profile_slice_unavailable" if profile_failed else "policy_resource_unavailable"
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
        form = detect_response_form(
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
            source_record=GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
            system_block=self._render(mode_value, form, rules, values),
        )

    def seed(self, mode: ChatMode | str) -> ChatLightweightPolicySnapshot:
        """为 queued 运行创建尚未绑定画像切片的稳定策略种子。"""
        mode_value = _mode_value(mode)
        if self._resource is None:
            return self._fallback_snapshot(mode_value, "policy_resource_unavailable")
        return ChatLightweightPolicySnapshot(
            version=self._version,
            mode=mode_value,
            form=ChatResponseForm.COMPACT_DEFAULT,
            snapshot_complete=False,
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
            source_record=GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
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
        """渲染为只含中文规则与边界的中文表达合同（无内部方法 ID）。"""
        rule_lines = "\n".join(f"{index}. {text}" for index, (_, text) in enumerate(rules, 1))
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
    "GLOBAL_CHAT_LIGHTWEIGHT_VERSION",
    "GLOBAL_CHAT_LIGHTWEIGHT_SOURCE",
    "SAFE_BASELINE_POLICY_VERSION",
    "ChatResponseForm",
    "ChatLightweightPolicyCompiler",
    "ChatLightweightPolicySnapshot",
    "FORM_LABELS",
    "FORM_RULES",
    "GLOBAL_DEFAULT_RULES",
    "detect_response_form",
]
