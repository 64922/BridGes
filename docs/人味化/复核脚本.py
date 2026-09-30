"""只读复核当前表达策略，运行时不调用模型、不联网、不修改业务数据。

运行：conda run --no-capture-output -n agent python docs/人味化/复核脚本.py
这些是用来揭示边界的反例，不是用户体验评分或完整回归测试。

Issue 05 起保护区复核改为按保留意图的精确绑定：多片段/换序/遗漏/纠错/
计算/合法引用各有独立反例，并单列裸数字、中文单位、否定、条件、因果与
结论强度的语义参考判断（只报告，不替换）。
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
from bridges.chat.global_writing_policy import restore_protected_regions
from bridges.chat.lightweight_policy import (
    ChatLightweightPolicyCompiler,
    detect_response_form,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.profiles import ProfileSliceItem


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
    forms = [
        {"输入": question, "实际形态": detect_response_form(question, ChatMode.COMPANION).value}
        for question in questions
    ]
    protection_cases = (
        ("多片段恢复", "甲 10 ms；乙 20 ms", "甲 11 ms；乙 21 ms"),
        ("换序不串对象", "甲 10 ms；乙 20 ms", "乙 21 ms；甲 11 ms"),
        ("已消费目标不复用", "甲 10 ms；乙 20 ms", "甲 11 ms"),
        ("纯数字与否定", "样本量为30，未发现显著差异。", "样本量为300，发现显著差异。"),
        ("用户要求纠正代码", "这行 `x = 1` 不对，请把数值改为 2。", "改为 `x = 2`。"),
        ("任务计算新值", "已知 `x = 1`，请计算 `y = x + 1`。", "已知 `x = 1`，所以 `y = 2`。"),
        ("跨来源链接", "", "应看甲的证据 https://example.com/a"),
    )
    protection = []
    for name, original, candidate in protection_cases:
        sources = ("https://example.com/a", "https://example.com/b") if name == "跨来源链接" else ()
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
            }
        )

    semantic_cases = (
        ("裸数字与否定", "样本量为30，未发现显著差异。", "样本量为300，发现显著差异。"),
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
    profile = ProfileSliceItem(
        assertion_id="只读复核条目",
        dimension="",
        value_or_rule="回答喜欢简短直接",
        inclusion_reason="模拟原子画像切片",
    )
    snapshot = compiler.compile(
        ChatMode.COMPANION,
        user_text="这个概念是什么意思",
        profile_items=[profile],
        profile_context="【本轮画像信息】回答喜欢简短直接",
    )

    # 对照 turn.py 当前的增量恢复和 delta 发送逻辑，模拟前端累加。
    stored = ""
    received = ""
    deltas = []
    for chunk in ("值 `x = 0", "`。"):
        restored = restore_protected_regions("`x = 1`", stored + chunk, append_missing=False)
        delta = restored[len(stored):] if restored.startswith(stored) else restored
        stored = restored
        received += delta
        deltas.append(delta)

    report = {
        "说明": "实际执行当前仓库纯函数；流式案例模拟当前前后端逻辑，不是真实浏览器验收。",
        "可绑定片段协议版本": FACT_PROTECTION_PROTOCOL_VERSION,
        "策略版本": snapshot.version,
        "形态反例": forms,
        "保护区反例": protection,
        "语义参考判断": semantic,
        "原子画像适配": {
            "输入条目数": 1,
            "策略采用条目数": len(snapshot.profile_items),
            "策略正文声称无画像": "本轮没有可用画像信息" in snapshot.system_block,
            "另一个画像上下文仍保留": snapshot.profile_context is not None,
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
