"""自动风格诊断（Issue 11 AC-8 / Test plan 4）。

- 风格短语只提供诊断（warning），不阻止发布；
- 稳定的协议/助手身份泄漏短语阻止发布（blocked）；
- fail closed 读取保真结果：保真报告缺失或检查缺失时拒绝给干净结论。
"""

from __future__ import annotations

from bridges.humanize_eval.cases import (
    CaseOperation,
    HumanizeCase,
    HumanizeCaseKind,
)
from bridges.humanize_eval.fidelity import (
    FidelityCheckItem,
    FidelityReport,
    FidelitySeverity,
)
from bridges.humanize_eval.style_diagnostics import (
    PROTOCOL_LEAK_PHRASES,
    run_style_diagnostics,
)


def _case(**overrides) -> HumanizeCase:
    base = dict(
        case_id="style-case-1",
        kind=HumanizeCaseKind.CHAT,
        title="测试案例",
        user_request="帮我写一段介绍",
        surface_type="直接回答",
        operation=CaseOperation.DIRECT_ANSWER,
        mode="casual",
        audience="普通用户",
        channel="聊天",
        target_length="短",
        risk="low",
        license_source_note="净室原创",
    )
    base.update(overrides)
    return HumanizeCase(**base)


def _fidelity_report(
    *, missing: bool = False, case_id: str = "style-case-1"
) -> FidelityReport:
    checks = [
        FidelityCheckItem(
            check_id="numbers",
            label="数字",
            severity=FidelitySeverity.CRITICAL,
            passed=True,
            missing=missing,
        )
    ]
    return FidelityReport(case_id=case_id, checks=checks)


def test_clean_output_is_ok():
    report = run_style_diagnostics(
        _case(), "这是一段完全干净的回答。", _fidelity_report()
    )
    assert report.verdict == "ok"
    assert not report.hits
    assert report.fidelity_available


def test_style_phrase_warns_but_does_not_block():
    report = run_style_diagnostics(
        _case(), "首先我们看背景。总的来说，这个方案可行。",
        _fidelity_report(),
    )
    assert report.verdict == "warning"
    assert not report.protocol_leak_hits
    # 风格命中带场景、位置与证据。
    hit = report.style_hits[0]
    assert hit.category == "style_phrase"
    assert hit.position == 0
    assert hit.evidence_span == "首先"
    assert hit.scenario == "chat/casual"
    assert not hit.blocks_release


def test_style_hit_records_all_positions():
    report = run_style_diagnostics(
        _case(), "首先……其次……最后总结一下。", _fidelity_report()
    )
    positions = [hit.position for hit in report.style_hits]
    assert positions == [0, 4, 8]


def test_protocol_leak_blocks_release():
    report = run_style_diagnostics(
        _case(), "作为 BridGes 助手，我很高兴为你服务。",
        _fidelity_report(),
    )
    assert report.verdict == "blocked"
    assert report.protocol_leak_hits
    assert report.protocol_leak_hits[0].blocks_release
    assert "阻止发布" in report.reasons[0]


def test_all_protocol_leak_phrases_detected():
    for phrase in PROTOCOL_LEAK_PHRASES:
        report = run_style_diagnostics(
            _case(), f"正文前{phrase}正文后", _fidelity_report()
        )
        assert report.verdict == "blocked", f"未检测到协议泄漏短语：{phrase}"


def test_fail_closed_without_fidelity_report():
    report = run_style_diagnostics(_case(), "完全干净的回答。", None)
    assert report.verdict == "fail_closed"
    assert not report.fidelity_available
    assert "fail closed" in report.reasons[0]
    # 即使文本完全干净也不能报 ok（保真缺失不得凭空通过）。


def test_fail_closed_with_missing_checks():
    report = run_style_diagnostics(
        _case(), "完全干净的回答。", _fidelity_report(missing=True)
    )
    assert report.verdict == "fail_closed"
    assert report.fidelity_missing_checks
    assert "缺失" in report.reasons[0]


def test_article_scenario_reported():
    report = run_style_diagnostics(
        _case(kind=HumanizeCaseKind.ARTICLE, mode="改写", genre_profile="tutorial"),
        "综上所述，我们建议。",
        _fidelity_report(case_id="style-case-1"),
    )
    assert report.style_hits
    assert report.style_hits[0].scenario == "article/改写/tutorial"


def test_blocked_takes_precedence_over_style_warning():
    report = run_style_diagnostics(
        _case(),
        "首先，作为 BridGes 助手，我可以帮你。",
        _fidelity_report(),
    )
    assert report.verdict == "blocked"
