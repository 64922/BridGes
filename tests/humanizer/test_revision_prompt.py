"""定向修订提示编译测试（人味化改造 Issue 05）。

修订请求只含待修问题（code/位置/证据/目标）与必要原文/保护项/首稿；
不包含全量方法规则 ID、全量检测清单或与当前问题无关的体裁规则；要求
最小修改并保留已通过部分，不得添加账本外事实。
"""

from __future__ import annotations

from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.revision_policy import (
    RevisionProblem,
    RevisionTriggerKind,
)
from bridges.skills.humanizer.revision_prompt import (
    REVISION_PROMPT_VERSION,
    compile_revision_prompt,
)

_SOURCE = "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"


def _expression_contract() -> object:
    routed = route_humanizer_message(f"帮我改写这段话：{_SOURCE}")
    assert routed is not None
    return routed.skill_input.expression_contract


def _problems() -> list[RevisionProblem]:
    return [
        RevisionProblem(
            kind=RevisionTriggerKind.HIGH_CONFIDENCE_EXPRESSION,
            code="business_ppt_metaphor",
            category="商业/PPT 隐喻",
            location=None,
            evidence="打造时间管理的护城河",
            target="去掉「护城河」隐喻，直接说效果",
        )
    ]


def test_revision_prompt_contains_only_targeted_problems() -> None:
    prompt = compile_revision_prompt(
        _expression_contract(),
        draft_text="首稿正文。",
        problems=_problems(),
        ledger=None,
        source_text=_SOURCE,
    )
    assert REVISION_PROMPT_VERSION in prompt.system_prompt
    # 待修问题：code/证据/目标都在提示中
    assert "business_ppt_metaphor" in prompt.system_prompt
    assert "打造时间管理的护城河" in prompt.system_prompt
    assert "去掉「护城河」隐喻" in prompt.system_prompt
    assert prompt.problem_count == 1
    # 不包含全量方法规则 ID（首稿 profile 的规则不重新注入）
    for rule_id in (
        "rewrite.main-clause-first",
        "fact.relevance-not-causality",
        "voice.evidence-near-opinion",
    ):
        assert rule_id not in prompt.system_prompt


def test_revision_prompt_keeps_protection_and_source() -> None:
    prompt = compile_revision_prompt(
        _expression_contract(),
        draft_text="首稿正文。",
        problems=_problems(),
        ledger=None,
        source_text=_SOURCE,
    )
    # 必要原文（保护项来源）在提示中，来源哈希保持语义明确
    assert "25 分钟" in prompt.system_prompt
    assert "引语、专名、数字、日期" in prompt.system_prompt
    assert prompt.source_text_included is True
    # 不得添加账本外事实
    assert "不得添加来源账本之外的事实、经历、例子或新论点" in prompt.system_prompt
    assert "最小修改" in prompt.system_prompt
    # 输出合同仍是 final_text 单一字段
    assert '"final_text"' in prompt.system_prompt


def test_revision_prompt_without_source_is_honest() -> None:
    prompt = compile_revision_prompt(
        _expression_contract(),
        draft_text="首稿正文。",
        problems=_problems(),
        ledger=None,
        source_text="   ",
    )
    assert prompt.source_text_included is False
    assert "本次没有粘贴原文" in prompt.system_prompt


def test_revision_prompt_includes_draft_verbatim() -> None:
    draft = "这是首稿的完整正文，请保留其中已经自然的部分。"
    prompt = compile_revision_prompt(
        _expression_contract(),
        draft_text=draft,
        problems=_problems(),
        ledger=None,
        source_text=_SOURCE,
    )
    assert draft in prompt.system_prompt
    # 只修问题清单，其余保持
    assert "只修上表列出的问题，保留首稿其他全部内容" in prompt.system_prompt
