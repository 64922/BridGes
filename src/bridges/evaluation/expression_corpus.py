"""工单 39：人味表达盲评的原创场景矩阵（不含真实用户数据）。

场景全部为原创虚构内容，覆盖倾诉、混合排查、引语情绪、感谢收尾、纠正/
续接、长任务、工具失败、明确边界与明确偏好，以及全部正式用户可见路径。
每个场景明确定义：轮次（连续多轮保留前文）、形态信号、硬门期望与证据
接缝；真实模型配对从 `real_runnable` 场景中抽样，其余路径的接线由确定性
证据与既有消费者验收记录核对，不用关键词命中替代体验结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExpressionCategory(StrEnum):
    """场景类别（覆盖矩阵维度）。"""

    VENTING = "venting"
    MIXED_TROUBLESHOOTING = "mixed_troubleshooting"
    QUOTED_EMOTION = "quoted_emotion"
    THANKS_CLOSING = "thanks_closing"
    CORRECTION = "correction"
    CONTINUATION = "continuation"
    LONG_TASK = "long_task"
    TOOL_FAILURE = "tool_failure"
    BOUNDARY = "boundary"
    PREFERENCE = "preference"
    FORMAL_PATH = "formal_path"


#: 场景矩阵必须覆盖的类别（工单 39 任务 5）。
REQUIRED_CATEGORIES: tuple[ExpressionCategory, ...] = tuple(ExpressionCategory)


class ToolSignal(StrEnum):
    """场景的确定性工具状态信号（用于硬门与策略编译）。"""

    NONE = "none"
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class FormalPath:
    """一条正式用户可见路径及其接线证据接缝。"""

    path_id: str
    title: str
    render_kind: str  # model_with_policy / deterministic_renderer / fixed_template / composite
    seam: str
    evidence: str


#: 正式用户可见路径清单（工单 39 要求“全部正式路径覆盖可查”）。
FORMAL_PATHS: tuple[FormalPath, ...] = (
    FormalPath(
        "chat.companion",
        "日常陪伴普通聊天",
        "model_with_policy",
        "chat/turn.py::_compile_writing_policy → assemble_payload",
        "tests/chat/test_improvement21_contextual_expression.py",
    ),
    FormalPath(
        "chat.study",
        "学习模式普通聊天（非课时任务）",
        "model_with_policy",
        "chat/turn.py::_compile_writing_policy（mode=study）",
        "tests/chat/test_v2_18_study_tutoring.py",
    ),
    FormalPath(
        "study.tutoring",
        "学习辅导生成",
        "model_with_policy",
        "study/tutoring.py::_tutoring_policy",
        "tests/chat/test_improvement22_unified_profile_expression.py",
    ),
    FormalPath(
        "study.review",
        "复盘出题与判定反馈",
        "model_with_policy",
        "study/review.py::_call（读取冻结策略快照）",
        "tests/chat/test_v2_19_study_review.py",
    ),
    FormalPath(
        "study.summary",
        "学习总结生成",
        "model_with_policy",
        "study/summary.py::_tutoring_policy",
        "tests/chat/test_improvement36_policy_acceptance.py",
    ),
    FormalPath(
        "study.scope",
        "学习范围/预习说明",
        "model_with_policy",
        "study/service.py::preview_policy_block",
        "tests/chat/test_improvement31_study_scope_preview.py",
    ),
    FormalPath(
        "paper.summary",
        "论文概述生成",
        "model_with_policy",
        "paper/presenting.py::SUMMARY_SYSTEM_PROMPT + expression.system_block",
        "tests/paper/test_paper_issue24.py::test_summary_generator_uses_policy_system_block_and_output_budget",
    ),
    FormalPath(
        "github.insights",
        "GitHub 借鉴角度生成",
        "model_with_policy",
        "github/presenting.py（追加 system_block 与 global_writing_policy）",
        "tests/github/test_github_acceptance_insights.py",
    ),
    FormalPath(
        "commute.result",
        "通勤路线结果",
        "deterministic_renderer",
        "commute/presenting.py::render_result_content",
        "src/bridges/commute/presenting.py",
    ),
    FormalPath(
        "resources.result",
        "学习资料路径结果",
        "deterministic_renderer",
        "resources/presenting.py",
        "src/bridges/resources/presenting.py",
    ),
    FormalPath(
        "tieba.research",
        "贴吧取证与研究说明",
        "deterministic_renderer",
        "tieba/presenting.py",
        "src/bridges/tieba/presenting.py",
    ),
    FormalPath(
        "career_plan.result",
        "职业规划结果",
        "deterministic_renderer",
        "career_plan/presenting.py",
        "src/bridges/career_plan/presenting.py",
    ),
    FormalPath(
        "composite",
        "复合计划统一综合结果",
        "model_with_policy",
        "chat/graph.py::_invoke_composite_plan",
        "tests/chat/test_issue37_composite_dispatch.py",
    ),
    FormalPath(
        "fixed_copy",
        "澄清/错误/空结果/进度/部分成功固定文案",
        "fixed_template",
        "state_copy/registry.py::render_state_copy",
        "tests/state_copy/test_issue23_state_copy_registry.py",
    ),
)

#: 路径 ID 集合（覆盖校验用）。
FORMAL_PATH_IDS = frozenset(path.path_id for path in FORMAL_PATHS)


@dataclass(frozen=True)
class ExpressionScenario:
    """一个原创多轮盲评场景及其确定性硬门期望。"""

    scenario_id: str
    title: str
    category: ExpressionCategory
    formal_path: str
    turns: tuple[str, ...]
    mode: str = "companion"
    profile_facts: tuple[str, ...] = ()
    tool_outcome: ToolSignal = ToolSignal.NONE
    protected_facts: tuple[str, ...] = ()
    boundary: str | None = None
    required_any: tuple[str, ...] = ()
    forbidden_any: tuple[str, ...] = ()
    detail_required: bool = False
    expects_continuation: bool = False
    expects_acknowledgement: bool = False
    real_runnable: bool = True
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def multi_turn(self) -> bool:
        return len(self.turns) > 1


def _s(**kwargs: object) -> ExpressionScenario:
    return ExpressionScenario(**kwargs)  # type: ignore[arg-type]


SCENARIOS: tuple[ExpressionScenario, ...] = (
    # ------------------------------------------------------------------
    # 倾诉（先具体承接，可陪聊或轻问，不强制建议）
    # ------------------------------------------------------------------
    _s(
        scenario_id="vent-experiment-failed",
        title="实验失败后的倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("今天实验又失败了，挺烦的。", "本来觉得这次肯定能成的。"),
        forbidden_any=("建议你重新", "你应该"),
        tags=("no_forced_advice",),
    ),
    _s(
        scenario_id="vent-thesis-pressure",
        title="论文压力倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("最近写论文压力很大，感觉写不下去了。", "导师还一直催。"),
        tags=("no_forced_advice",),
    ),
    _s(
        scenario_id="vent-no-advice",
        title="明确不想听建议只想聊",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("我今天很难过，不想听建议，只想聊聊。", "其实也不是什么大事，就是被误会了。"),
        boundary="no_advice",
        forbidden_any=("建议你", "你可以试试", "不妨"),
    ),
    _s(
        scenario_id="vent-misunderstood",
        title="被误会后的连续倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=(
            "被室友误会拿了他的东西，特别委屈。",
            "我解释了他也不信。",
            "你说我是不是太较真了？",
        ),
        forbidden_any=("建议你",),
    ),
    _s(
        scenario_id="vent-exhausted",
        title="疲惫状态倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("这几天赶ddl，累到不想说话。", "还有两个没交。"),
        tags=("no_forced_advice",),
    ),
    # ------------------------------------------------------------------
    # 混合排查（情绪承接 + 完成主任务）
    # ------------------------------------------------------------------
    _s(
        scenario_id="mixed-anxious-bug",
        title="焦虑同时请求排查",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("我好焦虑，帮我看看这个报错出在哪。", "错误是 KeyError: 'user_id'，我明明传了 id。"),
        required_any=("KeyError", "'user_id'", "user_id"),
    ),
    _s(
        scenario_id="mixed-angry-500",
        title="烦躁同时请求定位接口错误",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("烦死了，这个接口一直 500，帮我定位一下。", "日志只写了 internal error。"),
        required_any=("500", "日志", "排查"),
    ),
    _s(
        scenario_id="mixed-anxious-priority",
        title="焦虑同时请求排优先级",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("我好焦虑，帮我理一下这三件事先做哪个。", "交作业、回邮件、改论文。"),
        required_any=("先", "优先", "顺序"),
    ),
    _s(
        scenario_id="mixed-tired-explain",
        title="疲惫同时请求解释概念",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("今天真的累了，但还是想弄明白过拟合是什么意思。", "简单讲就行。"),
        required_any=("过拟合",),
    ),
    # ------------------------------------------------------------------
    # 引语情绪（引语/材料不推断用户状态）
    # ------------------------------------------------------------------
    _s(
        scenario_id="quote-translate-anxious",
        title="翻译引语中的情绪",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("他说‘我很焦虑’，帮我翻译成英文。", "保持口语一点。"),
        required_any=("anxious", "anxiety", "stressed", "stressing", "worried", "nervous"),
        notes="口语改写允许等价情绪词（stressed/worried/nervous），硬门检查翻译任务完成而非单一词形。",
    ),
    _s(
        scenario_id="quote-article-anxiety",
        title="讨论焦虑主题的文章",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("我最近在写一篇关于焦虑的论文，帮我理一下结构。", "重点是机制部分。"),
        required_any=("焦虑", "机制", "结构"),
    ),
    _s(
        scenario_id="quote-meaning-followup",
        title="解释引语中“别追问我了”的含义",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("他说‘别追问我了’，这句话是什么意思？", "是在表达生气吗？"),
        required_any=("追问", "意思", "生气", "边界"),
    ),
    _s(
        scenario_id="quote-fiction-emotion",
        title="小说引语的英文用词",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("小说里写‘她崩溃地哭了’，这句能用什么英文词？", "要能体现突然崩溃。"),
        required_any=("broke down", "broke", "collapsed", "break down"),
    ),
    # ------------------------------------------------------------------
    # 感谢收尾（自然结束，不机械追问）
    # ------------------------------------------------------------------
    _s(
        scenario_id="thanks-solved",
        title="问题解决后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("谢谢你，刚才的方法帮我解决了。",),
        forbidden_any=("还要我帮", "需要我帮", "要不要我", "还有什么可以帮", "如果需要其他"),
    ),
    _s(
        scenario_id="thanks-clear",
        title="解释清楚后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("明白了，谢谢！", "下次有问题再问你。"),
        forbidden_any=("还要我帮", "需要我帮", "要不要我", "还有什么可以帮"),
    ),
    _s(
        scenario_id="thanks-long-task",
        title="长任务完成后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("推导看完了，很清楚，谢谢。", "后面我自己练。"),
        forbidden_any=("再给你出题", "要不要再来", "需要我继续"),
    ),
    _s(
        scenario_id="thanks-apology",
        title="纠正后用户致谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("是我看错了，谢谢纠正。",),
        forbidden_any=("需要我帮", "要不要我", "还有什么可以帮"),
    ),
    # ------------------------------------------------------------------
    # 纠正与续接（接回真实任务，不重复原解释）
    # ------------------------------------------------------------------
    _s(
        scenario_id="correction-missed-unit",
        title="用户指出漏答单位",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("刚才那个 5000 是什么单位？", "你没回答这个。"),
        required_any=("单位", "米", "m", "si"),
        expects_continuation=True,
        real_runnable=False,
        notes=(
            "首轮依赖既有对话上下文（‘刚才那个 5000’），新会话无法复现；"
            "真实配对的纠正类别改用自包含的 correction-wrong-answer，"
            "本场景保留于确定性续接检查。"
        ),
    ),
    _s(
        scenario_id="correction-wrong-answer",
        title="用户纠正答案错误",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("你说错了，饱和蒸汽压不是随温度升高而降低。", "正确的是升高。"),
        required_any=("升高", "温度", "蒸汽压"),
    ),
    _s(
        scenario_id="correction-code",
        title="用户授权纠正代码中的数值",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("这行 `x = 1` 不对，请把数值改为 2。", "再确认一下结果。"),
        protected_facts=("x = 2",),
        expects_continuation=True,
    ),
    _s(
        scenario_id="correction-preference",
        title="用户纠正回答偏好",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("你刚才讲得太长了，下次直接给结论。", "这次先按这个来。"),
        required_any=("结论", "好", "收到", "明白"),
        expects_continuation=True,
    ),
    _s(
        scenario_id="continuation-reply-draft",
        title="续接润色周末约定",
        category=ExpressionCategory.CONTINUATION,
        formal_path="chat.companion",
        turns=("帮我把这句话改顺一点：我想约你周末一起去看展览。", "再口语一点。"),
        expects_continuation=True,
    ),
    _s(
        scenario_id="continuation-long-story",
        title="续接两千字故事任务",
        category=ExpressionCategory.CONTINUATION,
        formal_path="chat.companion",
        turns=("写一篇两千字的故事。", "继续。"),
        detail_required=True,
        expects_continuation=True,
        real_runnable=False,
        notes=(
            "两千字长文超出聊天输出额度（EXTENDED_OUTPUT_TOKENS=2048），"
            "真实配对准会出现 output_budget_exceeded 而非表达差异；"
            "保留于确定性前文消融，真实配对的续接由 continuation-reply-draft 代表。"
        ),
    ),
    # ------------------------------------------------------------------
    # 长任务（详细程度不被默认简短抵消）
    # ------------------------------------------------------------------
    _s(
        scenario_id="long-derivation",
        title="详细推导请求",
        category=ExpressionCategory.LONG_TASK,
        formal_path="chat.companion",
        turns=("请详细推导一下这个公式，每一步都要。", "再补充适用条件。"),
        detail_required=True,
    ),
    _s(
        scenario_id="long-comparison",
        title="详细对比请求",
        category=ExpressionCategory.LONG_TASK,
        formal_path="chat.companion",
        turns=("详细对比一下这两方法，尽量完整。", "包括各自的失败场景。"),
        detail_required=True,
    ),
    _s(
        scenario_id="long-plan",
        title="完整学习计划请求",
        category=ExpressionCategory.LONG_TASK,
        formal_path="chat.companion",
        turns=("帮我列一个四周的完整学习计划，要细一点。", "每天安排具体到章节。"),
        detail_required=True,
    ),
    _s(
        scenario_id="long-explanation",
        title="系统讲解请求",
        category=ExpressionCategory.LONG_TASK,
        formal_path="chat.companion",
        turns=("把这部分从头讲一遍，越细越好。", "不要跳步骤。"),
        detail_required=True,
    ),
    # ------------------------------------------------------------------
    # 工具失败/部分结果（如实说明，不伪装成功）
    # ------------------------------------------------------------------
    _s(
        scenario_id="tool-search-error",
        title="联网搜索失败",
        category=ExpressionCategory.TOOL_FAILURE,
        formal_path="chat.companion",
        turns=("帮我查一下这个最新进展。", "搜不到的话直说。"),
        tool_outcome=ToolSignal.ERROR,
        real_runnable=False,
    ),
    _s(
        scenario_id="tool-search-partial",
        title="联网搜索部分命中",
        category=ExpressionCategory.TOOL_FAILURE,
        formal_path="chat.companion",
        turns=("帮我找一下这三个问题的资料。", "先给能确认的部分。"),
        tool_outcome=ToolSignal.PARTIAL,
        real_runnable=False,
    ),
    _s(
        scenario_id="tool-arxiv-empty",
        title="论文库空结果",
        category=ExpressionCategory.TOOL_FAILURE,
        formal_path="chat.companion",
        turns=("帮我找一下这个方向的论文。", "没有相关的也说清楚。"),
        tool_outcome=ToolSignal.PARTIAL,
        real_runnable=False,
    ),
    _s(
        scenario_id="tool-retrieval-timeout",
        title="本地知识库检索超时",
        category=ExpressionCategory.TOOL_FAILURE,
        formal_path="chat.companion",
        turns=("从我的资料里找一下这段内容。", "找不到请说明是超时还是真没有。"),
        tool_outcome=ToolSignal.ERROR,
        real_runnable=False,
    ),
    # ------------------------------------------------------------------
    # 明确边界（本轮约束优先）
    # ------------------------------------------------------------------
    _s(
        scenario_id="boundary-no-comfort",
        title="不要安慰",
        category=ExpressionCategory.BOUNDARY,
        formal_path="chat.companion",
        turns=("我失败了，不要安慰我，告诉我下一步怎么改。", "直接说问题。"),
        boundary="no_comfort",
        forbidden_any=("我理解你", "别难过", "会好起来", "抱抱"),
    ),
    _s(
        scenario_id="boundary-answer-only",
        title="只要答案",
        category=ExpressionCategory.BOUNDARY,
        formal_path="chat.companion",
        turns=("只给我答案，不要解释理由：水的沸点是多少？", "谢谢。"),
        boundary="answer_only",
        required_any=("100", "摄氏"),
        forbidden_any=("因为", "原因", "解释"),
    ),
    _s(
        scenario_id="boundary-no-followup",
        title="不要追问",
        category=ExpressionCategory.BOUNDARY,
        formal_path="chat.companion",
        turns=("告诉我这个函数的作用，别追问我的使用场景。", "好的。"),
        boundary="no_followup",
    ),
    _s(
        scenario_id="boundary-detail",
        title="要求详细讲",
        category=ExpressionCategory.BOUNDARY,
        formal_path="chat.companion",
        turns=("详细讲，不要跳过推导。", "从定义开始。"),
        boundary="detail",
        detail_required=True,
    ),
    # ------------------------------------------------------------------
    # 明确偏好（自然使用，不刻意展示）
    # ------------------------------------------------------------------
    _s(
        scenario_id="pref-brief",
        title="明确简短偏好",
        category=ExpressionCategory.PREFERENCE,
        formal_path="chat.companion",
        turns=("解释一下熵是什么，回答简短直接一点。", "一句话版本呢？"),
        profile_facts=("回答喜欢简短直接",),
    ),
    _s(
        scenario_id="pref-example-first",
        title="先例后公式偏好",
        category=ExpressionCategory.PREFERENCE,
        formal_path="chat.companion",
        turns=("讲讲贝叶斯定理，我喜欢先看例子再看公式。", "例子换成生活场景。"),
        profile_facts=("解释概念时先举例再给公式",),
    ),
    _s(
        scenario_id="pref-format",
        title="结论先行与结构偏好",
        category=ExpressionCategory.PREFERENCE,
        formal_path="chat.companion",
        turns=("分析一下这个方案的优缺点，结论放前面。", "尽量别用列表。"),
        profile_facts=("回答时希望结论先行",),
    ),
    # ------------------------------------------------------------------
    # 正式路径：学习
    # ------------------------------------------------------------------
    _s(
        scenario_id="study-mode-ordinary",
        title="学习模式普通提问",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="chat.study",
        turns=("这一节主要讲什么？", "和上一节有什么关系？"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-tutoring-concept",
        title="书页辅导概念提问",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.tutoring",
        turns=("帮我讲讲这一页的偏导数定义。", "为什么需要这个定义？"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-tutoring-step",
        title="书页辅导分步推进",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.tutoring",
        turns=("这一步的变换没看懂。", "再慢一点。"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-review-question",
        title="复盘出题提问",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.review",
        turns=("出两道这一章的题给我练。", "先来第一题。"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-review-feedback",
        title="复盘判定反馈",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.review",
        turns=("我的答案是 B，错在哪？", "下次怎么避免？"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-summary",
        title="学习总结",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.summary",
        turns=("帮我总结一下这次学的内容。", "把没掌握的也列出来。"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    _s(
        scenario_id="study-scope-preview",
        title="学习范围与预习",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="study.scope",
        turns=("帮我看看这份材料覆盖了哪些知识点。", "哪些是重点？"),
        mode="study",
        real_runnable=False,
        tags=("formal_path", "study"),
    ),
    # ------------------------------------------------------------------
    # 正式路径：论文 / GitHub
    # ------------------------------------------------------------------
    _s(
        scenario_id="paper-summary",
        title="论文概述",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="paper.summary",
        turns=("帮我概述这篇论文的核心贡献。", "和已有方法比强在哪？"),
        real_runnable=False,
        tags=("formal_path", "paper"),
    ),
    _s(
        scenario_id="paper-compare",
        title="论文对比阅读",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="paper.summary",
        turns=("这两篇论文的方法差异是什么？", "哪篇更适合入门？"),
        real_runnable=False,
        tags=("formal_path", "paper"),
    ),
    _s(
        scenario_id="github-insight",
        title="GitHub 项目借鉴角度",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="github.insights",
        turns=("这个项目有什么值得我们借鉴的？", "和我们的目标匹配吗？"),
        real_runnable=False,
        tags=("formal_path", "github"),
    ),
    _s(
        scenario_id="github-compare",
        title="GitHub 项目对比",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="github.insights",
        turns=("这两个项目选哪个来参考？", "各自的风险是什么？"),
        real_runnable=False,
        tags=("formal_path", "github"),
    ),
    # ------------------------------------------------------------------
    # 正式路径：确定性渲染与固定文案
    # ------------------------------------------------------------------
    _s(
        scenario_id="commute-route",
        title="通勤路线结果文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="commute.result",
        turns=("从宿舍到实验室怎么走最快？",),
        real_runnable=False,
        tags=("formal_path", "renderer"),
    ),
    _s(
        scenario_id="resources-path",
        title="学习资料路径文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="resources.result",
        turns=("给我一条学线性代数的资料路径。",),
        real_runnable=False,
        tags=("formal_path", "renderer"),
    ),
    _s(
        scenario_id="tieba-research",
        title="贴吧取证说明",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="tieba.research",
        turns=("帮我看看这个学校宿舍的真实评价。", "把来源列出来。"),
        real_runnable=False,
        tags=("formal_path", "renderer"),
    ),
    _s(
        scenario_id="career-plan-result",
        title="职业规划结果",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="career_plan.result",
        turns=("帮我规划一下走算法岗的路径。", "需要补哪些技能？"),
        real_runnable=False,
        tags=("formal_path", "renderer"),
    ),
    _s(
        scenario_id="composite-paper-github",
        title="复合计划综合结果",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="composite",
        turns=("先找论文再找对应 GitHub 项目。", "最后给一个综合建议。"),
        real_runnable=False,
        tags=("formal_path", "composite"),
    ),
    _s(
        scenario_id="fixed-error-web",
        title="联网失败固定文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="fixed_copy",
        turns=("联网查一下这个消息。",),
        real_runnable=False,
        tags=("formal_path", "fixed_copy", "error"),
    ),
    _s(
        scenario_id="fixed-empty-retrieval",
        title="检索空结果固定文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="fixed_copy",
        turns=("从我资料里找一下没有的内容。",),
        real_runnable=False,
        tags=("formal_path", "fixed_copy", "empty"),
    ),
    _s(
        scenario_id="fixed-clarification-route",
        title="路由澄清固定文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="fixed_copy",
        turns=("帮我弄一下那个。",),
        real_runnable=False,
        tags=("formal_path", "fixed_copy", "clarification"),
    ),
    _s(
        scenario_id="fixed-progress-stop",
        title="进度与停止固定文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="fixed_copy",
        turns=("停下当前任务。",),
        real_runnable=False,
        tags=("formal_path", "fixed_copy", "progress"),
    ),
    _s(
        scenario_id="fixed-partial-result",
        title="部分成功固定文案",
        category=ExpressionCategory.FORMAL_PATH,
        formal_path="fixed_copy",
        turns=("有几个来源就先用几个。",),
        tool_outcome=ToolSignal.PARTIAL,
        real_runnable=False,
        tags=("formal_path", "fixed_copy", "partial"),
    ),
)


def scenario_by_id(scenario_id: str) -> ExpressionScenario:
    for scenario in SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"场景不存在：{scenario_id}")


def real_runnable_scenarios() -> tuple[ExpressionScenario, ...]:
    return tuple(scenario for scenario in SCENARIOS if scenario.real_runnable)


def validate_coverage() -> list[str]:
    """覆盖矩阵结构校验；返回问题列表（空表示通过）。"""

    problems: list[str] = []
    if not (40 <= len(SCENARIOS) <= 60):
        problems.append(f"场景总数应在 40–60 之间，实际 {len(SCENARIOS)}。")
    ids = [scenario.scenario_id for scenario in SCENARIOS]
    if len(ids) != len(set(ids)):
        problems.append("场景标识重复。")
    categories = {scenario.category for scenario in SCENARIOS}
    for category in REQUIRED_CATEGORIES:
        if category not in categories:
            problems.append(f"缺少类别：{category.value}。")
    paths = {scenario.formal_path for scenario in SCENARIOS}
    for path_id in sorted(FORMAL_PATH_IDS):
        if path_id not in paths:
            problems.append(f"正式路径无场景覆盖：{path_id}。")
    if sum(1 for scenario in SCENARIOS if scenario.multi_turn) < 40:
        problems.append("连续多轮场景不足 40 组。")
    if not any(scenario.tool_outcome is ToolSignal.ERROR for scenario in SCENARIOS):
        problems.append("缺少工具失败场景。")
    if not any(scenario.tool_outcome is ToolSignal.PARTIAL for scenario in SCENARIOS):
        problems.append("缺少工具部分结果场景。")
    if not any(scenario.boundary for scenario in SCENARIOS):
        problems.append("缺少明确边界场景。")
    if not any(scenario.expects_continuation for scenario in SCENARIOS):
        problems.append("缺少续接/纠正场景。")
    if not any(scenario.detail_required for scenario in SCENARIOS):
        problems.append("缺少长任务场景。")
    if not any(scenario.profile_facts for scenario in SCENARIOS):
        problems.append("缺少明确偏好场景。")
    known_paths = {
        path.path_id for path in FORMAL_PATHS if path.render_kind != "fixed_template"
    }
    for scenario in SCENARIOS:
        if scenario.formal_path not in FORMAL_PATH_IDS:
            problems.append(f"未登记路径：{scenario.scenario_id}/{scenario.formal_path}。")
        if (
            scenario.formal_path in known_paths
            and scenario.real_runnable
            and not scenario.turns
        ):
            problems.append(f"可真实运行场景缺少轮次：{scenario.scenario_id}。")
    return problems


def coverage_matrix() -> list[dict[str, object]]:
    """场景 × 类别 × 路径的审计矩阵（报告用）。"""

    return [
        {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "category": scenario.category.value,
            "formal_path": scenario.formal_path,
            "turn_count": len(scenario.turns),
            "multi_turn": scenario.multi_turn,
            "tool_outcome": scenario.tool_outcome.value,
            "boundary": scenario.boundary,
            "expects_continuation": scenario.expects_continuation,
            "expects_acknowledgement": scenario.expects_acknowledgement,
            "detail_required": scenario.detail_required,
            "real_runnable": scenario.real_runnable,
            "profile_fact_count": len(scenario.profile_facts),
            "protected_fact_count": len(scenario.protected_facts),
            "tags": list(scenario.tags),
        }
        for scenario in SCENARIOS
    ]


__all__ = [
    "ExpressionCategory",
    "ExpressionScenario",
    "FORMAL_PATHS",
    "FORMAL_PATH_IDS",
    "FormalPath",
    "REQUIRED_CATEGORIES",
    "SCENARIOS",
    "ToolSignal",
    "coverage_matrix",
    "real_runnable_scenarios",
    "scenario_by_id",
    "validate_coverage",
]
