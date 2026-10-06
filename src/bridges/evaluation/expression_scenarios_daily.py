"""工单 39 日常表达场景：倾诉/混合排查/引语情绪/感谢收尾/纠正/续接。

全部为原创虚构内容；键值语义见 `expression_spec.ExpressionScenario`。
"""

from __future__ import annotations

from bridges.evaluation.expression_spec import (
    ExpressionCategory,
    ExpressionScenario,
    scenario,
)

SCENARIOS: tuple[ExpressionScenario, ...] = (
    # ------------------------------------------------------------------
    # 倾诉（先具体承接，可陪聊或轻问，不强制建议）
    # ------------------------------------------------------------------
    scenario(
        scenario_id="vent-experiment-failed",
        title="实验失败后的倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("今天实验又失败了，挺烦的。", "本来觉得这次肯定能成的。"),
        forbidden_any=("建议你重新", "你应该"),
        tags=("no_forced_advice",),
    ),
    scenario(
        scenario_id="vent-thesis-pressure",
        title="论文压力倾诉",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("最近写论文压力很大，感觉写不下去了。", "导师还一直催。"),
        tags=("no_forced_advice",),
    ),
    scenario(
        scenario_id="vent-no-advice",
        title="明确不想听建议只想聊",
        category=ExpressionCategory.VENTING,
        formal_path="chat.companion",
        turns=("我今天很难过，不想听建议，只想聊聊。", "其实也不是什么大事，就是被误会了。"),
        boundary="no_advice",
        forbidden_any=("建议你", "你可以试试", "不妨"),
    ),
    scenario(
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
    scenario(
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
    scenario(
        scenario_id="mixed-anxious-bug",
        title="焦虑同时请求排查",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("我好焦虑，帮我看看这个报错出在哪。", "错误是 KeyError: 'user_id'，我明明传了 id。"),
        required_any=("KeyError", "'user_id'", "user_id"),
    ),
    scenario(
        scenario_id="mixed-angry-500",
        title="烦躁同时请求定位接口错误",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("烦死了，这个接口一直 500，帮我定位一下。", "日志只写了 internal error。"),
        required_any=("500", "日志", "排查"),
    ),
    scenario(
        scenario_id="mixed-anxious-priority",
        title="焦虑同时请求排优先级",
        category=ExpressionCategory.MIXED_TROUBLESHOOTING,
        formal_path="chat.companion",
        turns=("我好焦虑，帮我理一下这三件事先做哪个。", "交作业、回邮件、改论文。"),
        required_any=("先", "优先", "顺序"),
    ),
    scenario(
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
    scenario(
        scenario_id="quote-translate-anxious",
        title="翻译引语中的情绪",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("他说‘我很焦虑’，帮我翻译成英文。", "保持口语一点。"),
        required_any=("anxious", "anxiety", "stressed", "stressing", "worried", "nervous"),
        notes="口语改写允许等价情绪词（stressed/worried/nervous），硬门检查翻译任务完成而非单一词形。",
    ),
    scenario(
        scenario_id="quote-article-anxiety",
        title="讨论焦虑主题的文章",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("我最近在写一篇关于焦虑的论文，帮我理一下结构。", "重点是机制部分。"),
        required_any=("焦虑", "机制", "结构"),
    ),
    scenario(
        scenario_id="quote-meaning-followup",
        title="解释引语中“别追问我了”的含义",
        category=ExpressionCategory.QUOTED_EMOTION,
        formal_path="chat.companion",
        turns=("他说‘别追问我了’，这句话是什么意思？", "是在表达生气吗？"),
        required_any=("追问", "意思", "生气", "边界"),
    ),
    scenario(
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
    scenario(
        scenario_id="thanks-solved",
        title="问题解决后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("谢谢你，刚才的方法帮我解决了。",),
        forbidden_any=("还要我帮", "需要我帮", "要不要我", "还有什么可以帮", "如果需要其他"),
    ),
    scenario(
        scenario_id="thanks-clear",
        title="解释清楚后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("明白了，谢谢！", "下次有问题再问你。"),
        forbidden_any=("还要我帮", "需要我帮", "要不要我", "还有什么可以帮"),
    ),
    scenario(
        scenario_id="thanks-long-task",
        title="长任务完成后的感谢",
        category=ExpressionCategory.THANKS_CLOSING,
        formal_path="chat.companion",
        turns=("推导看完了，很清楚，谢谢。", "后面我自己练。"),
        forbidden_any=("再给你出题", "要不要再来", "需要我继续"),
    ),
    scenario(
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
    scenario(
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
    scenario(
        scenario_id="correction-wrong-answer",
        title="用户纠正答案错误",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("你说错了，饱和蒸汽压不是随温度升高而降低。", "正确的是升高。"),
        required_any=("升高", "温度", "蒸汽压"),
    ),
    scenario(
        scenario_id="correction-code",
        title="用户授权纠正代码中的数值",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("这行 `x = 1` 不对，请把数值改为 2。", "再确认一下结果。"),
        protected_facts=("x = 2",),
        expects_continuation=True,
    ),
    scenario(
        scenario_id="correction-preference",
        title="用户纠正回答偏好",
        category=ExpressionCategory.CORRECTION,
        formal_path="chat.companion",
        turns=("你刚才讲得太长了，下次直接给结论。", "这次先按这个来。"),
        required_any=("结论", "好", "收到", "明白"),
        expects_continuation=True,
    ),
    scenario(
        scenario_id="continuation-reply-draft",
        title="续接润色周末约定",
        category=ExpressionCategory.CONTINUATION,
        formal_path="chat.companion",
        turns=("帮我把这句话改顺一点：我想约你周末一起去看展览。", "再口语一点。"),
        expects_continuation=True,
    ),
    scenario(
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
)
