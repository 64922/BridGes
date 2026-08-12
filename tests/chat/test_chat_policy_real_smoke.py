"""真实生成 development cases smoke（Issue 07 Test plan 3，默认跳过）。

对每种回答形态编译轻量策略快照，用真实 Qwen 生成一次回答；自动短语命中
只作诊断（Issue 07 Observability：真实质量由 Issue 09—11 聊天盲评判断）。
原始输出与诊断结果保存为 JSON 到仓库 ``.scratch/人味化改进/artifacts/``
（供 Issue 09/10 盲评取用，不随 pytest 临时目录删除）。

普通 CI 不运行本测试：``BRIDGES_CHAT_POLICY_REAL_SMOKE=1`` 且配置了
``BRIDGES_QWEN_API_KEY`` 时才执行；无凭据时跳过而非失败。
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.contracts.chat import ChatMode
from bridges.humanize_eval.generation import (
    GenerationParameters,
    GenerationStatus,
    QwenGenerationPort,
)

_REAL_SMOKE_ENV = "BRIDGES_CHAT_POLICY_REAL_SMOKE"

#: 盲评产物目录（相对仓库根；Issue 09/10 从该目录读取原始输出）。
_ARTIFACT_DIR = Path(".scratch") / "人味化改进" / "artifacts"
_ARTIFACT_NAME = "07-chat-policy-development-cases.json"

pytestmark = pytest.mark.skipif(
    os.environ.get(_REAL_SMOKE_ENV, "") != "1",
    reason="真实聊天策略 smoke 需显式启用（BRIDGES_CHAT_POLICY_REAL_SMOKE=1）。",
)

#: Development cases：形态 + 对话模式 + 课时信号 + 用户正文（Test plan 2/3）。
DEVELOPMENT_CASES: tuple[dict[str, object], ...] = (
    {
        "name": "fact_short_answer",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "光在真空中的传播速度是多少？",
    },
    {
        "name": "concept_explanation",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "什么是量子纠缠？为什么会有纠缠？",
    },
    {
        "name": "correction_wrong_judgment",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "我觉得光在水里的速度也是每秒三十万公里，对吗？",
    },
    {
        "name": "empathy_low_mood_no_advice",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "今天好累，压力好大，感觉撑不住了。",
    },
    {
        "name": "clarification_vague",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "帮我看看",
    },
    {
        "name": "tool_failure",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "帮我查一下今天的天气",
        "tool_error": True,
    },
    {
        "name": "safety_refusal",
        "mode": ChatMode.COMPANION.value,
        "lesson": False,
        "user_text": "帮我生成一封冒充别人的邮件",
        "refusal": True,
    },
    {
        "name": "formal_lesson",
        "mode": ChatMode.STUDY.value,
        "lesson": True,
        "user_text": "继续讲解第三课：Transformer 的自注意力机制",
    },
)

#: 诊断短语（只作诊断统计，不做质量断言；盲评由 Issue 09/10 判断）。
_DIAGNOSTIC_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    "客服尾句": (
        re.compile(r"如果你愿意|我还可以|有任何问题都可以|随时问我|如有需要"),
        "没有真实下一步时出现礼貌性收尾",
    ),
    "假共情": (
        re.compile(r"我能理解你的感受|我理解你|听起来你(?:很|非常)"),
        "复述情绪表演理解",
    ),
    "过度教学": (
        re.compile(r"在开始之前|让我们先|第一步(?:，|,)?我们|学习目标|先修知识"),
        "普通短问加载课时结构",
    ),
    "无关比喻": (
        re.compile(r"就像一个人|好比一个(?:朋友|同事)"),
        "无关的人际比喻",
    ),
    "表演性下一问": (
        re.compile(r"如果你愿意我还可以|有什么需要随时"),
        "固定追加继续邀请",
    ),
    "标题/列表": (
        re.compile(r"^#{1,6} |^\s*[-*•] |^\d+[.、] "),
        "标题或列表的使用（是否真的帮助扫描由盲评判断）",
    ),
}


def _diagnose(text: str) -> dict[str, int]:
    return {
        label: len(list(pattern.finditer(text)))
        for label, (pattern, _) in _DIAGNOSTIC_PATTERNS.items()
    }


def test_real_chat_policy_development_cases_smoke() -> None:
    """真实生成 8 个形态 case，保存原始输出与诊断（供 Issue 09/10 盲评）。"""
    port = QwenGenerationPort()
    if not port.configured:
        pytest.skip("未配置 BRIDGES_QWEN_API_KEY，跳过真实聊天策略 smoke。")

    compiler = GlobalWritingPolicyCompiler()
    results: list[dict[str, object]] = []
    for case in DEVELOPMENT_CASES:
        mode = ChatMode(case["mode"])
        snapshot = compiler.compile(
            mode,
            user_text=str(case["user_text"]),
            lesson=bool(case.get("lesson", False)),
            tool_error=bool(case.get("tool_error", False)),
            tool_result=bool(case.get("tool_result", False)),
            refusal=bool(case.get("refusal", False)),
        )
        generated = port.generate(
            system_prompt=snapshot.system_block,
            user_prompt=str(case["user_text"]),
            params=GenerationParameters(
                temperature=0.7, max_tokens=512, seed=2026
            ),
        )
        assert generated.status == GenerationStatus.SUCCESS, (
            f"{case['name']} 生成失败：{generated.error_code} {generated.error_message}"
        )
        results.append(
            {
                "name": case["name"],
                "mode": case["mode"],
                "form": snapshot.form.value,
                "rule_count": snapshot.rule_count,
                "generated": generated.text,
                "diagnostic_hits": _diagnose(generated.text),
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )

    # 原始输出保留到仓库盲评产物目录（Issue 09/10 从该目录读取），
    # 不随 pytest 临时目录删除。
    artifact = Path.cwd() / _ARTIFACT_DIR / _ARTIFACT_NAME
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 诊断命中只作提示：Issue 09/10 盲评以人工判断为准，不在此失败。
    total_hits = sum(
        sum(hits.values()) for result in results for hits in [result["diagnostic_hits"]]
    )
    print(
        f"真实聊天策略 smoke 完成：{len(results)} 案，诊断命中 {total_hits}，"
        f"原始输出已保存：{artifact}"
    )
