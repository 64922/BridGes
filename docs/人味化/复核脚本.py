"""只读复核当前表达策略，运行时不调用模型、不联网、不修改业务数据。

运行：conda run --no-capture-output -n agent python docs/人味化/复核脚本.py
这些是用来揭示边界的反例，不是用户体验评分或完整回归测试。

Issue 05 起保护区复核改为按保留意图的精确绑定：多片段/换序/遗漏/纠错/
计算/合法引用各有独立反例，并单列裸数字、中文单位、否定、条件、因果与
结论强度的语义参考判断（只报告，不替换）。

Issue 06 起流式案例改用正式追加式装配器（``StreamProtectionAssembler``）
模拟：同一 delta 序列同时驱动前端累加与落库正文，受保护片段闭合前暂缓，
不再出现「整段正文当伪增量重发」。真实浏览器生命周期验收由 e2e 承担。

改进工单 21 起形态反例补充续接（“继续”沿用上一轮详细任务）与工具失败/
部分结果两档，用于复核有分寸表达的任务承接与如实降级。

改进工单 22 起「原子画像适配」改用真实原子服务编译采用快照：复核无类别
偏好进入策略、无关爱好不进入、策略正文不再同时声称没有画像。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bridges.chat.fact_protection import (
    FACT_PROTECTION_PROTOCOL_VERSION,
    assess_semantic_reference,
    plan_fragment_protection,
)
from bridges.chat.lightweight_policy import (
    ChatLightweightPolicyCompiler,
    ToolOutcome,
    detect_response_form,
)
from bridges.chat.stream_protection import (
    STREAM_CONSISTENCY_PROTOCOL_VERSION,
    StreamProtectionAssembler,
)
from bridges.contracts.chat import ChatMode
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose


def _atomic_profile_service() -> AtomicProfileService:
    """与测试同形的只读原子服务（内存仓库，不触碰业务数据）。"""

    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=InMemoryFourDimensionProfileRepository(),
    )
    return AtomicProfileService(four, InMemoryAtomicProfileRepository())


def main() -> None:
    questions = (
        "解释焦虑的生理机制",
        "我今天很难过，不想听建议，只想聊聊",
        "写一篇两千字的故事",
        "他说‘我很焦虑’，帮我翻译成英文",
        "你说得对吗？我觉得这里有问题",
        "我很焦虑，怎么办",
        "谢谢你，刚才的方法帮我解决了",
        "继续",
    )
    form_cases = [(question, question, {}) for question in questions] + [
        # 改进工单 21：续接沿用上一轮真实任务，工具失败/部分结果如实降级；
        # 否定句式与引语不冒充本轮强限制。
        (
            "继续（承接“写一篇两千字的故事”）",
            "继续",
            {"continuation_text": "写一篇两千字的故事"},
        ),
        ("工具失败（联网超时）", "你好", {"tool_outcome": ToolOutcome.ERROR}),
        ("工具部分结果（部分命中）", "你好", {"tool_outcome": ToolOutcome.PARTIAL}),
        ("不用详细讲，简单说一下", "不用详细讲，简单说一下", {}),
        ("他说‘别追问我了’，这句话是什么意思", "他说‘别追问我了’，这句话是什么意思", {}),
        # 验收补充：焦虑又请求排查完成主请求；文章材料话题不推断用户情绪。
        ("焦虑又请求排查（完成任务）", "我好焦虑，帮我看看这个问题出在哪", {}),
        ("关于焦虑的论文（话题非情绪）", "我最近在写一篇关于焦虑的论文", {}),
    ]
    forms = [
        {
            "输入": label,
            "实际形态": detect_response_form(text, ChatMode.COMPANION, **options).value,
        }
        for label, text, options in form_cases
    ]
    protection_cases = (
        ("多片段恢复", "甲 10 ms；乙 20 ms", "甲 11 ms；乙 21 ms"),
        ("换序不串对象", "甲 10 ms；乙 20 ms", "乙 21 ms；甲 11 ms"),
        ("已消费目标不复用", "甲 10 ms；乙 20 ms", "甲 11 ms"),
        ("纯数字与否定", "样本量为30，未发现显著差异。", "样本量为300，发现显著差异。"),
        ("用户要求纠正代码", "这行 `x = 1` 不对，请把数值改为 2。", "改为 `x = 2`。"),
        ("任务计算新值", "已知 `x = 1`，请计算 `y = x + 1`。", "已知 `x = 1`，所以 `y = 2`。"),
        ("跨来源链接", "", "应看甲的证据 https://example.com/a"),
        ("对象释义后的正确换序", "甲 10 ms；乙 20 ms", "乙的值为 20 ms；甲的值为 10 ms"),
        ("对象释义后拒绝猜测", "甲 10 ms；乙 20 ms", "乙的值为 21 ms；甲的值为 11 ms"),
        ("普通换算答案", "1 s 等于多少 ms？", "1000 ms。"),
        ("同句混合授权", "把 `x = 1` 改为 `x = 2` 并保留 `y = 3` 不变。", "`x = 2`，`y = 3`。"),
        ("关键遗漏", "请原样保留：甲 10 ms；乙 20 ms", "甲 10 ms"),
        ("清单外引用不猜测换链", "", "答案见 https://example.com/changed"),
    )
    protection = []
    for name, original, candidate in protection_cases:
        sources = ("https://example.com/a", "https://example.com/b") if name == "跨来源链接" else ()
        if name == "清单外引用不猜测换链":
            sources = ("https://example.com/source",)
        result = plan_fragment_protection(
            original, candidate, additional_sources=sources
        )
        protection.append(
            {
                "案例": name,
                "原始输入": original,
                "候选": candidate,
                "恢复后": result.content,
                "保留意图": result.intent.value,
                "不一致": [item.kind for item in result.inconsistencies],
                "关键不一致": result.has_critical_inconsistency,
            }
        )

    semantic_cases = (
        ("裸数字与否定", "样本量为30，未发现显著差异。", "样本量为300，发现显著差异。"),
        ("对象之间转移否定", "甲不会提高，乙会提高。", "甲会提高，乙不会提高。"),
        (
            "中文单位/条件/结论强度",
            "如果温度升高 10 米，则必然显著。",
            "温度升高 20 米，可能显著。",
        ),
    )
    semantic = [
        {
            "案例": name,
            "源": source,
            "候选": candidate,
            "参考判断": dataclasses.asdict(assess_semantic_reference(source, candidate)),
        }
        for name, source, candidate in semantic_cases
    ]

    compiler = ChatLightweightPolicyCompiler()
    # 改进工单 22：真实原子服务编译采用快照，策略与画像块消费同一结果。
    atomic = _atomic_profile_service()
    atomic.remember("复核账户", "回答喜欢简短直接", source_message_id="复核-偏好")
    atomic.remember("复核账户", "我平时喜欢跑步", source_message_id="复核-爱好")
    adopted = atomic.compile_adopted_slice(
        "复核账户",
        run_id="复核-运行",
        purpose=build_purpose(mode="companion", query="这个概念是什么意思"),
    )
    snapshot = compiler.compile(
        ChatMode.COMPANION,
        user_text="这个概念是什么意思",
        adopted_slice=adopted,
    )

    # 对照 turn.py 当前的追加式装配协议：delta 只表示新内容，同一序列同时
    # 驱动前端累加与落库正文。原片段为 `x = 1`，模型先流出被改写的
    # `x = 0 再补全，闭合确认前该片段暂缓，终态只追加恢复后的正确片段。
    assembler = StreamProtectionAssembler("值 `x = 1`。")
    raw = ""
    deltas: list[str] = []
    for chunk in ("值 `x = 0", "`。"):
        raw += chunk
        delta = assembler.update(raw)
        if delta:
            deltas.append(delta)
    flush, result = assembler.finish()
    if flush:
        deltas.append(flush)
    stored = result.content
    received = "".join(deltas)

    report = {
        "说明": "实际执行当前仓库纯函数；流式案例使用正式追加式装配器模拟，不是真实浏览器验收。",
        "可绑定片段协议版本": FACT_PROTECTION_PROTOCOL_VERSION,
        "流式正文协议版本": STREAM_CONSISTENCY_PROTOCOL_VERSION,
        "策略版本": snapshot.version,
        "形态反例": forms,
        "保护区反例": protection,
        "语义参考判断": semantic,
        "原子画像适配": {
            "输入条目数": 2,
            "采用条目数": len(adopted.adopted_items),
            "策略采用条目数": len(snapshot.profile_items),
            "策略正文声称无画像": "本轮没有可用画像信息" in snapshot.system_block,
            "采用决策标签": list(snapshot.profile_decisions),
            "采用规则": [
                rule_id
                for rule_id in snapshot.rule_ids
                if rule_id.startswith("adopted-")
            ],
            "无关爱好进入策略": "喜欢跑步" in snapshot.system_block,
            "切片标识一致": snapshot.profile_slice_id == adopted.slice_id,
        },
        "流式恢复模拟": {"发送增量": deltas, "落库正文": stored, "前端累加正文": received},
        "规则冲突核查": {
            "解释档规则": compiler.compile(ChatMode.COMPANION, user_text="解释一下熵").rule_ids,
            "情绪档规则": compiler.compile(ChatMode.COMPANION, user_text="今天好难过").rule_ids,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
