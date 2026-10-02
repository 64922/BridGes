"""普通聊天轻量有人味策略（人味化改造 Issue 07，改进工单 21 升级 v3）。

该模块重建普通聊天的全局轻量表达策略：不再把文章清洗规则、全量方法规则
块或通用黑名单注入每次回答，而是根据当前轮意图、固定对话模式、回答形态
和允许的最小画像切片，每轮只编译少量高优先级正向规则。

改进工单 21 起按「任务、边界与前文」适配表达：

- 显式交流边界（不想听建议、不要安慰、只给答案、要求详细、不要追问）先于
  默认规则编译，弱分类不强制安慰、建议或提问；
- 引用、翻译与文章材料里的情绪不算用户状态；焦虑又请求排查时完成主请求；
- 续接（「继续」）与纠正（「你漏答了」）按最近相关用户请求的真实任务确定
  形态，不复制完整历史、不额外调用分类模型；
- 实际工具成功/部分/错误与有策略依据的拒答信号由调用方以系统信号传入，
  未发生的模型拒答不得在生成前假定；
- 显式长文/推导使用有界任务上限（:data:`EXTENDED_OUTPUT_TOKENS`），仍要
  通过工单 04 的最终载荷预算门，不无界提高所有回复。

回答形态路由是确定性文本启发，不调用模型：系统信号（工具失败、工具结果、
拒答、真实课时任务）优先于文本信号；低置信一律落到紧凑默认，不扩成长文。

快照与重试：编译结果是不可变 ``ChatLightweightPolicySnapshot``，同一任务
重试必须复用原快照（调用方回传 ``existing_snapshot``），不因规则热更新
漂移形态或规则；旧版快照（不含新字段）反序列化后原样复用。策略编译、
保护恢复与审计异常都不得触发第二次模型生成。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.chat import ChatMessageRole, ChatMode
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

#: 当前轻量策略版本（改进工单 21：显式交流边界、引用/续接适配与有界长文）。
#: 重试回传旧快照时版本保持旧值（快照优先，不重编译）。
GLOBAL_CHAT_LIGHTWEIGHT_VERSION = "global-chat-lightweight-v3"
#: 安全基线版本（资源缺失或画像不可用时使用，值保持不变以兼容旧快照）。
SAFE_BASELINE_POLICY_VERSION = "global-humanized-writing-safe-baseline-v1"
GLOBAL_CHAT_LIGHTWEIGHT_SOURCE = (
    "BridGes 原创净室规则（文章人味化 SKILL 已于 Issue 21 退役，"
    "规则正文保留在本模块与 bridges/expression_task/contract_compiler.py）"
)

#: 主对话默认输出额度（单一事实源；``turn.CHAT_OUTPUT_TOKENS`` 由此别名）。
DEFAULT_OUTPUT_TOKENS = 1024
#: 显式长文/推导的有界任务上限：只提高本类任务的输出预留，仍受工单 04 的
#: 最终载荷门约束（额度放不下时给出明确受限结果），不是无界提高所有回复。
EXTENDED_OUTPUT_TOKENS = 2048

_DEFAULT_RESOURCE = object()

#: 允许进入表达策略的最小画像维度：称呼、篇幅、专业程度与表达偏好
#: （Issue 07 AC9 封闭清单）。其余维度（兴趣、阶段目标、情绪、经历等）
#: 不得影响回答表达。无类别原子画像的统一采用属改进工单 22。
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

#: 严格优先声明（改进工单 21）：事实/权限/任务合同与当前明确要求先于默认偏好。
_STRICT_PRIORITY_STATEMENT = (
    "严格优先：事实准确性、权限边界、当前任务合同与用户本轮明确要求高于以下"
    "默认规则与偏好；用户明确限制（如不想听建议、不要安慰、只要答案、要求"
    "详细、不要追问）必须遵守，不得用默认风格覆盖。"
)


class ToolOutcome(StrEnum):
    """调用方传入的本轮实际工具状态（改进工单 21 的系统信号）。

    ``NONE`` 表示本轮没有触发需要向用户交代的工具；``SUCCESS``/``PARTIAL``
    进入工具结果说明形态，``ERROR`` 进入错误/拒答形态。未发生的模型拒答
    不得在生成前假定为 ``ERROR``。
    """

    NONE = "none"
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


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
    DIRECT_TASK = "direct_task"
    TOOL_RESULT = "tool_result"
    ERROR_REFUSAL = "error_refusal"
    LESSON = "lesson"
    COMPACT_DEFAULT = "compact_default"


# ---------------------------------------------------------------------------
# 显式交流约束（改进工单 21）：先于默认规则的少量强制边界。
# ---------------------------------------------------------------------------

#: 约束标识 → (规则 ID, 中文规则正文)。
CONSTRAINT_RULES: dict[str, tuple[str, str]] = {
    "no_advice": (
        "no-unsolicited-advice",
        "用户明确不想要建议：不提供计划、步骤或行动建议；如涉及感受，具体承接并允许继续聊。",
    ),
    "no_comfort": (
        "no-unsolicited-comfort",
        "用户明确不要安慰：不添加安抚、鼓励或打气式表达。",
    ),
    "answer_only": (
        "answer-only",
        "用户只要答案：直接给出被问的结果，不展开解释、建议或补充说明。",
    ),
    "detail_requested": (
        "detail-follows-request",
        "用户明确要求详细：按需求量展开必要步骤与推导，不受默认简短限制，也不填充无关内容。",
    ),
    "no_follow_up": (
        "no-forced-followup",
        "用户明确不要追问：不追加澄清问题、继续邀请或需求询问；缺少关键信息时如实说明缺口。",
    ),
    "missed_part": (
        "answer-missed-part",
        "用户指出上一轮遗漏：直接接回被漏掉的请求并给出答案，不重复原解释、不机械道歉或责怪用户。",
    ),
    "partial_results": (
        "partial-results-stated",
        "本轮只取得部分结果或遇到超时：如实说明已获得什么、还缺什么以及可否重试，不把部分结果说成完整成功。",
    ),
    "closing": (
        "natural-closing",
        "本轮交流已收尾：自然回应完成感或直接结束，不重启话题、不追问新需求。",
    ),
}

#: 约束渲染/采用的固定优先级（越靠前越先进入提示词；总数仍有上限）。
_CONSTRAINT_ORDER: tuple[str, ...] = (
    "missed_part",
    "no_follow_up",
    "no_advice",
    "no_comfort",
    "answer_only",
    "detail_requested",
    "partial_results",
    "closing",
)
#: 单轮最多编译的强制约束条数（保持「少量」，其余由任务合同与历史承接）。
_MAX_CONSTRAINT_RULES = 3


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
        ("no-forced-check", "解释完成后自然结束，不固定追问是否解决；只有缺少必要信息时再提问。"),
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
        (
            "stay-without-forcing",
            "先具体回应已经发生的事，可以陪聊或轻问；不强制给建议、计划或让用户先做选择。",
        ),
        ("no-mind-reading", "不推测用户的情绪、人格、经历或亲密关系。"),
        ("no-parroting", "不复述用户原句来表演理解。"),
        (
            "honest-disagreement",
            "可以有依据地表达不同意见；承接感受不等于认同错误事实或自我贬低，"
            "也不编造共同经历、亲密关系或自己的经历。",
        ),
    ),
    ChatResponseForm.CLARIFICATION: (
        ("one-question", "只问一个最高价值问题。"),
        ("reason-stated", "说明为什么需要这个信息。"),
        ("no-interrogation", "不连珠炮式追问多个问题。"),
    ),
    ChatResponseForm.DIRECT_TASK: (
        (
            "task-output-first",
            "直接完成用户请求的任务（翻译、改写、计算、创作、详细推导等），输出即任务结果。",
        ),
        (
            "length-follows-request",
            "篇幅服从任务与用户明确要求：要求详细或指定长度时按需求量展开，"
            "不因默认简短而压缩必要内容；未要求时不无谓扩写。",
        ),
        ("no-extra-commentary", "只做任务范围内的内容，不追加无关建议、情绪承接或固定收尾。"),
        ("source-completeness", "源文本、数据和受保护区按任务合同完整保留，不遗漏必要内容。"),
    ),
    ChatResponseForm.TOOL_RESULT: (
        ("result-first", "先直接说明工具返回的结果。"),
        ("evidence-verbatim", "不改写工具原始结果、错误码或协议字段。"),
        ("brief-interpretation", "可以加简短解读，但标注哪些是你的解读。"),
        (
            "status-accurate",
            "按真实工具状态说明成功、部分或失败；部分结果不得说成完整成功，"
            "未取得的结果不得声称已取得。",
        ),
    ),
    ChatResponseForm.ERROR_REFUSAL: (
        ("reason-direct", "直接说明原因与边界。"),
        ("no-soothing", "不客服式安抚或励志包装。"),
        (
            "failure-not-feigned",
            "失败、超时或未核实时如实说明未取得的结果与影响；不把未核实内容说成工具已成功。",
        ),
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
    ChatResponseForm.DIRECT_TASK: "直接任务",
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
#: 明确请求助手查看/排查具体对象的任务：焦虑又请求排查时完成主请求，
#: 情绪承接只作可选补充，不把需求二选一丢掉。只认“帮我”类显式请求并
#: 排除已发生的叙述（“帮我看了……”是陈述，不是本轮请求）。
_TASK_REQUEST_RE = re.compile(
    r"(?:帮我|替我|给我)(?:看|查|找|分析|排查|处理|解决|定位|检查)(?!了|过)"
    r"(?:一下|下|看|查|找)?[^，。！？]{0,4}?"
    r"(?:问题|原因|错误|报错|故障|根源|日志|代码|数据|文件|文档|材料|内容"
    r"|论文|文章|报告|怎么回事|哪里|出在哪|出了什么)"
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
#: 直接任务：翻译/改写等，输出即任务结果。
_TRANSLATION_RE = re.compile(
    r"翻译|译成|译为|中译英|英译中|翻译成|translate|润色|改写|转写|校对"
)
#: 材料引用：情绪词出处于引语/材料，不是用户当前状态。
_MATERIAL_REF_RE = re.compile(
    r"这段话|这篇文章|这段文字|引语|原文|材料里|文中|文章里|书里|页面上|摘录"
)
#: 情绪话题词形（概念讨论而非用户状态）：既包括“焦虑的生理机制”这类
#: 概念讨论，也包括“关于焦虑的论文”“文章讲焦虑”这类文章材料话题。
_EMOTION_TOPIC_RE = re.compile(
    r"(?:关于|对于)(?:焦虑|压力|情绪|难过|抑郁|愤怒|生气|紧张|害怕|担心)"
    r"[^，。！？]{0,4}?(?:论文|文章|书|研究|课题|讲座|讨论|话题|内容|材料)"
    r"|(?:论文|文章|书|研究|报告|课程|讲座|材料|内容)"
    r"[^，。！？]{0,4}?(?:讲|说|讨论|研究|提到|分析|介绍|解释)"
    r"[^，。！？]{0,2}?(?:焦虑|压力|情绪|难过|抑郁|愤怒|生气|紧张|害怕|担心)"
    r"|(?:焦虑|压力|情绪|难过|抑郁|愤怒|生气|紧张|害怕|担心)"
    r"(?:症|障碍|的[^，。！？]{0,4}?"
    r"(?:生理|心理|神经|认知|社会|遗传|机制|原理|原因|研究|影响|表现|来源"
    r"|定义|本质|治疗|干预|理论|成因)|是什么|属于|算不算)"
)
#: 引号范围（含直角、双角、弯引号与直引号）。
_QUOTED_SPAN_RE = re.compile(r"[「『“‘\"]([^「」『』”’\"]{0,200})[」』”’\"]")
#: 分句符（判断情绪是否处于用户自身状态）。
_CLAUSE_SPLIT_RE = re.compile(r"[，,。！!？?；;\s]+")
#: 用户自身状态标记（出现在情绪所在分句才算状态）。
_SELF_STATE_MARKERS = (
    "我",
    "自己",
    "今天",
    "最近",
    "这几天",
    "这周",
    "感觉",
    "觉得",
    "有点",
    "有些",
    "心里",
    "整个人",
)

#: 显式边界：不想建议/不要安慰/只要答案/要求详细/不要追问等。
_NO_ADVICE_RE = re.compile(
    r"(?:不(?:想|要|用|必|需要)(?:再)?(?:听|要)?(?:你|您)?(?:的)?建议"
    r"|别(?:再)?(?:给|提|说)(?:我)?(?:什么)?建议"
    r"|(?:不用|无需|不需要)建议"
    r"|只想(?:吐槽|抱怨|聊聊|说说|倾诉)"
    r"|不想(?:被)?(?:教育|说教))"
)
_NO_COMFORT_RE = re.compile(
    r"(?:不(?:想|要|用|需要)|别|无需)(?:再)?(?:安慰|哄|鼓励|打气|心疼)(?:我)?"
)
_ANSWER_ONLY_RE = re.compile(
    r"(?:只|就)(?:要|给|回)(?:我)?(?:个|一个)?(?:答案|结果|结论|数字)"
    r"|直接(?:给|说|告诉)(?:我)?(?:答案|结果|结论)"
    r"|(?:别|不要|不用|无需)(?:再)?解释"
    r"|少(?:说|讲)(?:点)?废话"
)
_DETAIL_RE = re.compile(
    r"(?:详细|具体|深入|完整|展开|逐(?:步|条))[^。！？？！]{0,8}?"
    r"(?:讲|说|解释|推导|分析|展开|描述|写|列出|说明)"
    r"|(?:[一二两三四五六七八九十\d]+千?字)(?:左右|以内|以上)?(?:的)?"
    r"(?:故事|文章|总结|报告|介绍|方案|分析)"
    r"|(?:长篇|长文)"
    r"|(?:推导|证明)(?:一下|一遍|过程|步骤)"
)
_NO_FOLLOW_UP_RE = re.compile(
    r"(?:别|不要|不用|无需)(?:再)?(?:追问|问我|问下去|继续问|问了|问东问西)"
    r"|不要再问"
)
#: 否定句式中的“详细/展开”（“不用详细讲”“别讲得太详细”）不是长文请求。
_NO_DETAIL_RE = re.compile(
    r"(?:不用|不要|别|无需|不必|不需要)"
    r"(?:再|这么|那么|太|过于|讲得|说得|讲的|说的)*"
    r"(?:详细|具体|深入|完整|展开|长篇|逐(?:步|条))"
)
_MISSED_PART_RE = re.compile(
    r"^(?:你|您)?(?:漏答|漏了|没答|没有回答|没回答|答非所问|漏掉|少答|跳过)"
    r"|(?:你|您)(?:漏答|漏了|没答|没有回答|没回答|答非所问|漏掉|少答|跳过)"
    r"|(?:我(?:问|说)的是|我要问的是)"
)
_CLOSING_RE = re.compile(
    r"(?:谢谢|多谢|感谢)(?:你|您)?[^。！？？！]{0,16}?"
    r"(?:解决了|搞定了|弄好了|做完了|完成了|已解决|已经解决|可以了|够用了)"
    r"|^(?:解决了|搞定了|好了|可以了|没问题了|不用了|够用了)[。！!…\s]*$"
)
#: 续接信号（「继续」「接着上次」等）；无相关前文时不改变形态。
_CONTINUATION_RE = re.compile(
    r"^(?:请|那|再)?(?:继续|接着|往下|然后|再来|继续吧|接着说|继续讲|继续写|继续说)"
    r"[^。！？？！]{0,12}[。！!…\s]*$"
    r"|(?:接着|继续)(?:刚才|上次|之前|上面|前面|原来)"
)
#: 纯续接填充：查找相关前文时跳过。
_PURE_CONTINUATION_RE = re.compile(
    r"^(?:请|那|再)?(?:继续|接着|往下|然后|再来|继续吧)[。！!…\s]*$"
)

_CONSTRAINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("no_advice", _NO_ADVICE_RE),
    ("no_comfort", _NO_COMFORT_RE),
    ("answer_only", _ANSWER_ONLY_RE),
    ("detail_requested", _DETAIL_RE),
    ("no_follow_up", _NO_FOLLOW_UP_RE),
    ("missed_part", _MISSED_PART_RE),
    ("closing", _CLOSING_RE),
)

_SHORT_TEXT_LIMIT = 40
#: 续接查找最多回看的用户消息条数（有界读取，避免扫描整段历史）。
_CONTINUATION_MAX_HOPS = 6


@dataclass(frozen=True)
class _TurnClassification:
    """一次请求的确定性分类：回答形态 + 显式交流约束。"""

    form: ChatResponseForm
    constraints: tuple[str, ...]


class _MessageLike(Protocol):
    """``continuation_source_text`` 接受的最小消息投影（仓库/测试替身通用）。"""

    message_id: str
    role: ChatMessageRole
    content: str


def _strip_quoted(text: str) -> str:
    """去掉引号内的内容：引语/翻译材料里的情绪不是用户状态。"""
    return _QUOTED_SPAN_RE.sub(" ", text)


def _user_state_emotion(text: str) -> bool:
    """判断情绪词是否描述用户自身状态（而不是话题、引语或材料）。"""
    stripped = _strip_quoted(text)
    if not _EMPATHY_RE.search(stripped):
        return False
    for clause in _CLAUSE_SPLIT_RE.split(stripped):
        clause = clause.strip()
        if not clause or not _EMPATHY_RE.search(clause):
            continue
        if (
            _EMOTION_TOPIC_RE.search(clause)
            or _EXPLANATION_RE.search(clause)
            or _MATERIAL_REF_RE.search(clause)
            or _TRANSLATION_RE.search(clause)
        ):
            continue
        if any(marker in clause for marker in _SELF_STATE_MARKERS):
            return True
        # 短句直述情绪（「好烦」「撑不住了」）也算用户状态；带问题或对象的
        # 长句（「这样的焦虑有必要吗」）不按用户状态处理。
        if len(clause) <= 8:
            return True
    return False


def _detect_constraint_ids(text: str) -> list[str]:
    """识别本轮强限制；引号/材料内的他人话语不当作本轮限制。"""
    stated = _strip_quoted(text)
    return [
        name
        for name, pattern in _CONSTRAINT_PATTERNS
        if pattern.search(stated)
        and not (name == "detail_requested" and _NO_DETAIL_RE.search(stated))
    ]


def _order_constraints(constraints: Sequence[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for name in _CONSTRAINT_ORDER:
        if name in constraints and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _form_from_text(text: str, constraints: Sequence[str]) -> ChatResponseForm:
    names = set(constraints)
    # 建议与纠错判断只看用户自己的话：引语/材料里的主张不改变本轮形态。
    stated = _strip_quoted(text)
    if "answer_only" in names and "detail_requested" not in names:
        return ChatResponseForm.SHORT_ANSWER
    if "detail_requested" in names:
        return ChatResponseForm.DIRECT_TASK
    user_emotion = _user_state_emotion(text)
    if _ADVICE_RE.search(stated) and "no_advice" not in names:
        return ChatResponseForm.ADVICE
    if _TASK_REQUEST_RE.search(stated):
        # 焦虑又请求排查：完成主请求，情绪承接作为可选补充。
        return ChatResponseForm.DIRECT_TASK
    if user_emotion and "no_comfort" not in names:
        return ChatResponseForm.EMPATHY
    if _CORRECTION_RE.search(stated):
        return ChatResponseForm.CORRECTION
    if _TRANSLATION_RE.search(text):
        return ChatResponseForm.DIRECT_TASK
    if "no_follow_up" not in names and _CLARIFICATION_RE.search(text):
        return ChatResponseForm.CLARIFICATION
    if _EXPLANATION_RE.search(text):
        return ChatResponseForm.EXPLANATION
    if len(text) <= _SHORT_TEXT_LIMIT and _FACT_QUESTION_RE.search(text):
        return ChatResponseForm.SHORT_ANSWER
    return ChatResponseForm.COMPACT_DEFAULT


def _classify_text(
    text: str, continuation_text: str = "", *, depth: int = 0
) -> _TurnClassification:
    """按文本与相关前文做确定性分类（不调用模型、不复制历史）。"""
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return _TurnClassification(ChatResponseForm.COMPACT_DEFAULT, ())
    constraints = _detect_constraint_ids(normalized)
    needs_continuation = bool(_CONTINUATION_RE.search(normalized)) or (
        "missed_part" in constraints
    )
    if depth == 0 and needs_continuation and continuation_text.strip():
        # 续接/纠正：按最近相关用户请求的真实任务确定形态与边界；
        # 只读取该请求的分类结果，不把历史正文复制进提示词。
        base = _classify_text(continuation_text, "", depth=1)
        merged = list(base.constraints)
        for name in constraints:
            if name not in merged:
                merged.append(name)
        return _TurnClassification(base.form, _order_constraints(merged))
    return _TurnClassification(
        _form_from_text(normalized, constraints), _order_constraints(constraints)
    )


def detect_turn_constraints(
    text: str, *, continuation_text: str = ""
) -> tuple[str, ...]:
    """识别本轮显式交流约束（不想建议/不要安慰/只给答案/详细/别追问等）。

    返回固定优先级顺序的约束 ID；``continuation_text`` 是最近相关用户请求，
    用于续接/纠正轮沿用原任务的边界（不复制正文）。
    """
    return _classify_text(text, continuation_text).constraints


def continuation_source_text(
    messages: Sequence[_MessageLike],
    current_user_message_id: str,
) -> str:
    """返回当前用户消息之前最近一条有内容的非填充用户请求。

    只用于续接/纠正的形态路由（本地分类），不进入模型提示词；纯续接
    （「继续」）、遗漏指认（「你漏答了」）与收尾感谢会被跳过，最多回看
    ``_CONTINUATION_MAX_HOPS`` 条，避免无界读取历史。
    """
    found = None
    for index, message in enumerate(messages):
        if message.message_id == current_user_message_id:
            found = index
    if found is None:
        return ""
    hops = 0
    for message in reversed(messages[:found]):
        if message.role != ChatMessageRole.USER:
            continue
        content = (message.content or "").strip()
        if not content:
            continue
        if (
            _PURE_CONTINUATION_RE.match(content)
            or _MISSED_PART_RE.search(content)
            or _CLOSING_RE.search(content)
        ):
            hops += 1
            if hops >= _CONTINUATION_MAX_HOPS:
                break
            continue
        return content
    return ""


def output_tokens_for_request(text: str, continuation_text: str = "") -> int:
    """按任务给出有界输出额度：显式长文/推导用任务上限，其余保持默认。"""
    classification = _classify_text(text, continuation_text)
    if "detail_requested" in classification.constraints:
        return EXTENDED_OUTPUT_TOKENS
    return DEFAULT_OUTPUT_TOKENS


def detect_response_form(
    text: str,
    mode: ChatMode | str,
    *,
    tool_error: bool = False,
    tool_result: bool = False,
    refusal: bool = False,
    lesson: bool = False,
    tool_outcome: ToolOutcome = ToolOutcome.NONE,
    continuation_text: str = "",
) -> ChatResponseForm:
    """按系统信号与当前轮意图确定回答形态。

    系统信号优先（工具失败/拒答 → 错误/拒答；工具结果或部分结果 → 工具结果
    说明；学习模式真实课时任务 → 学习课时）；随后按文本启发依次判定显式
    交流约束、建议、情绪承接、纠错、直接任务、澄清、解释与短答；低置信
    一律落到紧凑默认。``tool_outcome`` 是本轮实际工具状态；``refusal`` 只能
    来自有策略依据的信号，不得在生成前假定尚未发生的模型拒答。
    """
    if refusal or tool_error or tool_outcome is ToolOutcome.ERROR:
        return ChatResponseForm.ERROR_REFUSAL
    if tool_result or tool_outcome in {
        ToolOutcome.SUCCESS,
        ToolOutcome.PARTIAL,
    }:
        return ChatResponseForm.TOOL_RESULT
    if lesson and _mode_value(mode) == ChatMode.STUDY.value:
        return ChatResponseForm.LESSON
    return _classify_text(text, continuation_text).form


# ---------------------------------------------------------------------------
# 策略快照与编译器
# ---------------------------------------------------------------------------


class ChatLightweightPolicySnapshot(BaseModel):
    """绑定一次生成尝试的轻量表达策略快照（不可变）。

    新增字段均有默认值：旧版快照（Issue 07/改进工单 04 之前）反序列化后
    原样复用，不因字段缺失拒绝重试。``system_block`` 只含渲染后的中文规则
    与边界，不含画像正文全文或用户聊天正文。
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
    constraints: tuple[str, ...] = Field(
        default_factory=tuple, description="本轮显式交流约束 ID（不含正文）。"
    )
    output_tokens: int = Field(
        default=DEFAULT_OUTPUT_TOKENS, description="本轮任务的有界输出额度（token）。"
    )
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

        只含版本、形态、规则/约束数量与契约哈希；不保存系统提示、画像正文
        或私人聊天正文。
        """
        return {
            "version": self.version,
            "mode": self.mode,
            "form": self.form.value,
            "rule_count": self.rule_count,
            "constraints": list(self.constraints),
            "output_tokens": self.output_tokens,
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
        continuation_text: str = "",
        tool_outcome: ToolOutcome = ToolOutcome.NONE,
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
        classification = _classify_text(user_text, continuation_text)
        constraints = list(classification.constraints)
        if tool_outcome is ToolOutcome.PARTIAL and "partial_results" not in constraints:
            constraints.append("partial_results")
        # 检测保留全部信号供形态/额度决策；提示词与审计只渲染固定优先级
        # 的前 ``_MAX_CONSTRAINT_RULES`` 条，两者保持同一份快照字段。
        ordered_constraints = _order_constraints(constraints)[:_MAX_CONSTRAINT_RULES]
        form = detect_response_form(
            user_text,
            mode,
            tool_error=tool_error,
            tool_result=tool_result,
            refusal=refusal,
            lesson=lesson,
            tool_outcome=tool_outcome,
            continuation_text=continuation_text,
        )
        constraint_rules = tuple(
            CONSTRAINT_RULES[name] for name in ordered_constraints
        )
        rules = (*constraint_rules, *GLOBAL_DEFAULT_RULES, *FORM_RULES[form])
        degradation = None
        if form == ChatResponseForm.COMPACT_DEFAULT and user_text.strip():
            degradation = "low_confidence_compact_default"
        output_tokens = output_tokens_for_request(user_text, continuation_text)
        return ChatLightweightPolicySnapshot(
            version=self._version,
            mode=mode_value,
            form=form,
            rule_ids=tuple(rule_id for rule_id, _ in rules),
            rule_count=len(rules),
            constraints=ordered_constraints,
            output_tokens=output_tokens,
            contract_schema_version=contract.schema_version,
            contract_version_hash=contract.version_hash,
            profile_slice_id=profile_slice_id,
            profile_items=values,
            profile_context=profile_context,
            snapshot_complete=True,
            fallback_reason=None,
            degradation_reason=degradation,
            source_record=GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
            system_block=self._render(
                mode_value, form, constraint_rules, GLOBAL_DEFAULT_RULES + FORM_RULES[form], values
            ),
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
                mode_value,
                ChatResponseForm.COMPACT_DEFAULT,
                (),
                GLOBAL_DEFAULT_RULES,
                (),
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
                "只完成任务本身，使用清楚、诚实、简洁的中文。"
                "遵守用户本轮明确限制（不想听建议、不要安慰、只要答案、要求详细、"
                "不要追问等）；未被要求时不追加建议、安慰或追问，任务完成即自然结束。\n"
                "保持原始事实、数字、限定条件、代码、公式、JSON、引用、链接、"
                "错误码、工具结果和协议字段不变；不规避 AI 检测、不冒充真人或"
                "名人、不伪造经历、来源或引用。\n"
                f"{_PRIORITY_STATEMENT}\n"
                "本轮没有可用画像信息，不得自行推断用户经历、身份、人格或偏好。\n"
                + _PROTECTED_REGIONS_STATEMENT
            ),
        )

    def _render(
        self,
        mode: str,
        form: ChatResponseForm,
        constraint_rules: Sequence[tuple[str, str]],
        default_rules: Sequence[tuple[str, str]],
        profile_items: tuple[str, ...],
    ) -> str:
        """渲染为只含中文规则与边界的中文表达合同（无内部方法 ID）。"""
        constraint_lines = (
            "\n".join(
                f"{index}. {text}" for index, (_, text) in enumerate(constraint_rules, 1)
            )
            if constraint_rules
            else "本轮没有额外强制限制，按默认规则完成任务。"
        )
        rule_lines = "\n".join(
            f"{index}. {text}" for index, (_, text) in enumerate(default_rules, 1)
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
            f"{_STRICT_PRIORITY_STATEMENT}\n"
            "本轮明确要求：\n"
            f"{constraint_lines}\n"
            "默认表达规则：\n"
            f"{rule_lines}\n"
            f"{_PRIORITY_STATEMENT}\n"
            "只作用于模型生成的自然语言正文；"
            + _PROTECTED_REGIONS_STATEMENT
            + f"{profile}"
        )


__all__ = [
    "DEFAULT_OUTPUT_TOKENS",
    "EXTENDED_OUTPUT_TOKENS",
    "GLOBAL_CHAT_LIGHTWEIGHT_VERSION",
    "GLOBAL_CHAT_LIGHTWEIGHT_SOURCE",
    "SAFE_BASELINE_POLICY_VERSION",
    "CONSTRAINT_RULES",
    "ChatResponseForm",
    "ChatLightweightPolicyCompiler",
    "ChatLightweightPolicySnapshot",
    "FORM_LABELS",
    "FORM_RULES",
    "GLOBAL_DEFAULT_RULES",
    "ToolOutcome",
    "continuation_source_text",
    "detect_response_form",
    "detect_turn_constraints",
    "output_tokens_for_request",
]
