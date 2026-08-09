"""自然语言人味化路由的公开行为测试（Issue 07）。"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import HumanizerPath
from bridges.skills.humanizer.intent import route_humanizer_message


@pytest.mark.parametrize(
    "content",
    [
        "给我润色这篇文章：光合作用是植物把光能转成化学能。",
        "请帮我改写下面这段话：我们采用了新的实验方法。",
        "把这份报告改得更自然：结果显示样本存在差异。",
        "帮我重写这个摘要，保留原意：研究发现温度影响反应速率。",
        "去掉这篇邮件的模板腔：您好，现将会议安排通知如下。",
        "请给这段科普文字去掉 AI 味：黑洞是时空中的极端区域。",
        "让文章更像人写，原文：本研究具有重要的理论与实践意义。",
        "文章人味化：量子纠错可以降低噪声影响。",
        "帮我把演讲稿口语化：今天我想介绍这个实验。",
        "请把这篇论文的表达顺一顺：实验结果支持这一假设。",
    ],
)
def test_explicit_natural_language_rewrite_is_routed(content: str) -> None:
    decision = route_humanizer_message(content)

    assert decision is not None
    assert decision.skill_input.contract.path == HumanizerPath.REWRITE
    assert decision.skill_input.route is not None
    assert decision.skill_input.route.source == "natural_language"
    assert decision.skill_input.route.version == "humanizer-route-v1"


@pytest.mark.parametrize(
    "content",
    [
        "润色是什么意思？",
        "我不想润色这篇文章。",
        "不要改写这段话，只告诉我语法问题。",
        "如何写出更自然的文章？",
        "这篇文章写得自然吗？",
        "我喜欢自然一点的天气。",
        "请解释一下什么是模板腔。",
        "帮我翻译这篇文章：Hello world。",
        "文章人味化这个功能怎么收费？",
        "论文的研究结果是什么？",
        "这段文字有点 AI 味。",
        "这篇文章太模板腔了。",
    ],
)
def test_similar_but_non_action_messages_are_not_routed(content: str) -> None:
    assert route_humanizer_message(content) is None


def test_route_compiles_source_and_optional_constraints() -> None:
    decision = route_humanizer_message(
        "请润色这篇论文，面向研究生，渠道为组会，长度不超过 800 字，"
        "数字、引用、术语不能改：实验结果显示，样本量为 25 个。"
    )

    assert decision is not None
    contract = decision.skill_input.contract
    assert contract.genre == Genre.PAPER_ASSIST
    assert contract.audience == "研究生"
    assert contract.channel == "组会"
    assert contract.length_target == "不超过 800 字"
    assert "数字、引用、术语不能改" in contract.hard_constraints
    assert contract.source_text == "实验结果显示，样本量为 25 个。"
    assert decision.use_knowledge_base is False
    assert decision.skill_input.route is not None
    assert decision.skill_input.route.external_evidence_requested is False


def test_missing_source_stays_on_humanizer_route_without_generation_input() -> None:
    decision = route_humanizer_message("请帮我润色这篇文章，面向普通读者。")

    assert decision is not None
    assert decision.skill_input.contract.source_text is None
    assert decision.skill_input.contract.attachment_ids == []
    assert decision.skill_input.contract.audience == "普通读者"


def test_explicit_knowledge_base_reference_enables_only_exact_evidence_path() -> None:
    decision = route_humanizer_message(
        "请用当前账户知识库文档「实验报告.md」帮我润色这篇报告。"
    )

    assert decision is not None
    contract = decision.skill_input.contract
    assert contract.source_text is None
    assert contract.knowledge_base_reference == "实验报告.md"
    assert decision.use_knowledge_base is True


def test_explicit_external_evidence_request_allows_public_evidence_stage() -> None:
    decision = route_humanizer_message(
        "请润色这篇报告并核验外部事实：结果显示样本存在差异。"
    )

    assert decision is not None
    assert decision.skill_input.route is not None
    assert decision.skill_input.route.external_evidence_requested is True


def test_vague_knowledge_base_request_does_not_enable_generic_retrieval() -> None:
    decision = route_humanizer_message("请润色这篇报告，并从知识库补充一些信息：结果显示样本存在差异。")

    assert decision is not None
    assert decision.skill_input.contract.knowledge_base_reference is None
    assert decision.use_knowledge_base is False


def test_frozen_natural_language_eval_set_has_thirty_original_samples() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "src"
        / "bridges"
        / "skills"
        / "humanizer"
        / "skill"
        / "fixtures"
        / "natural_language_eval.md"
    )
    content = fixture.read_text(encoding="utf-8")
    sample_ids = re.findall(r"^### NL-(\d{2})$", content, flags=re.MULTILINE)

    assert sample_ids == [f"{index:02d}" for index in range(1, 31)]
    assert all(section in content for section in ("说明文", "科普", "邮件", "报告", "演讲稿"))
