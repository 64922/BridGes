"""来源账本与保真硬门测试（Issue 02）。

表驱动覆盖：来源类型、保护项提取（中文标点/全半角单位/日期/引用编号/
多段引语）、保留检查硬失败（否定/因果/结论/数字单位/专名/引语）、新增
claim 来源检查（亲历/时间地点/样本/功能）、假设契约、不误判与失败关闭。
"""

from __future__ import annotations

import pytest

from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    FIDELITY_CHECKER_VERSION,
    SOURCE_LEDGER_VERSION,
    FidelityFailureCode,
    FidelitySeverity,
    HumanizerPath,
    HumanizerTaskContract,
    ProtectedSpanKind,
    SourceType,
)
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.humanizer.source_ledger import (
    FidelityCheckError,
    compile_source_ledger,
    extract_protected_spans,
    run_fidelity_check,
)

# ---------------------------------------------------------------------------
# 1. 来源类型与账本编译
# ---------------------------------------------------------------------------


def test_compile_records_all_source_types() -> None:
    ledger = compile_source_ledger(
        "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。",
        supplements=[("补充材料", "实验收集了 5000 例样本。")],
        account_scoped=[("知识库文档", "账户授权材料内容。")],
        external_allowed=[("外部网页", "该地区年降雨量为 500 mm。")],
        common_knowledge=["水在标准大气压下 100°C 沸腾。"],
    )
    types = {entry.source_type for entry in ledger.entries}
    assert types == {
        SourceType.USER_ORIGINAL,
        SourceType.USER_SUPPLEMENT,
        SourceType.ACCOUNT_SCOPED,
        SourceType.EXTERNAL_ALLOWED,
        SourceType.COMMON_KNOWLEDGE,
    }
    assert ledger.ledger_version == SOURCE_LEDGER_VERSION
    assert ledger.ledger_hash
    assert ledger.compile_summary is not None
    assert ledger.compile_summary.entry_count == 5


def test_ledger_hash_is_stable_for_same_input() -> None:
    a = compile_source_ledger("原文：数字 42 与单位 35°C。")
    b = compile_source_ledger("原文：数字 42 与单位 35°C。")
    assert a.ledger_hash == b.ledger_hash


def test_ledger_hash_changes_when_source_changes() -> None:
    a = compile_source_ledger("原文：数字 42。")
    b = compile_source_ledger("原文：数字 43。")
    assert a.ledger_hash != b.ledger_hash


def test_default_usage_is_rewrite_only() -> None:
    ledger = compile_source_ledger("原文内容。")
    original = next(e for e in ledger.entries if e.source_type == SourceType.USER_ORIGINAL)
    assert original.usage == ["rewrite"]


# ---------------------------------------------------------------------------
# 2. 保护项提取（表驱动：中文标点/全半角/日期/引用编号/多段引语/代码/URL）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ('他说："数据必须保持原样"。', ProtectedSpanKind.QUOTE),
        ("「精确引语」不得改动。", ProtectedSpanKind.QUOTE),
        ("『书中原话』保持。", ProtectedSpanKind.QUOTE),
        ("引用了 'exact phrase' 内容。", ProtectedSpanKind.QUOTE),
        ("```python\nx = 1\n```", ProtectedSpanKind.CODE),
        ("6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂", ProtectedSpanKind.FORMULA),
        ("参见 https://example.com/paper?a=1", ProtectedSpanKind.URL),
        ("(Zhang et al., 2021)", ProtectedSpanKind.CITATION),
        ("[3, 5-6] 引用编号", ProtectedSpanKind.CITATION),
        ("参见（Wang, 2020）", ProtectedSpanKind.CITATION),
        ("他说：“第一段引语。第二段引语。”", ProtectedSpanKind.QUOTE),
    ],
)
def test_protected_span_extraction(text: str, kind: ProtectedSpanKind) -> None:
    kinds = {span.kind for span in extract_protected_spans(text)}
    assert kind in kinds
    for span in extract_protected_spans(text):
        assert span.text_hash
        assert span.location.end > span.location.start


def test_user_phrase_is_protected_span() -> None:
    ledger = compile_source_ledger(
        "必须保留「每临大事有静气」这句话。",
        user_phrases=["每临大事有静气"],
    )
    spans = [
        span
        for entry in ledger.entries
        for span in entry.protected_spans
    ]
    assert any(span.kind == ProtectedSpanKind.USER_PHRASE for span in spans)


def test_compile_summary_counts_protected_kinds() -> None:
    text = (
        '他说："数据保持原样"。URL 见 https://example.com。'
        "公式 6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂。(Zhang et al., 2021)"
    )
    ledger = compile_source_ledger(text)
    assert ledger.compile_summary is not None
    by_kind = ledger.compile_summary.protected_spans_by_kind
    assert by_kind.get("quote", 0) >= 1
    assert by_kind.get("url", 0) >= 1
    assert by_kind.get("formula", 0) >= 1
    assert by_kind.get("citation", 0) >= 1


def test_compile_extracts_numbers_dates_and_units() -> None:
    ledger = compile_source_ledger(
        "2019—2023 年测量 25 μmol·m⁻²·s⁻¹，全角 ３５℃ 等价。"
    )
    entry = ledger.entries[0]
    assert any(n.split("|")[0] == "25" for n in entry.numbers)
    assert any(n.split("|")[0] == "35" for n in entry.numbers)  # 全角统一
    assert any("2019" in d or "2023" in d for d in entry.dates)


# ---------------------------------------------------------------------------
# Issue 01 缺陷 c：数字+单位的规范化键对空白/全半角/紧邻变体稳定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "original_variant",
    [
        "Transformer2017年谷歌团队提出注意力机制。",      # 紧邻字母（无空格）
        "Transformer 2017年谷歌团队提出注意力机制。",     # 数字后紧邻单位
        "Transformer2017 年谷歌团队提出注意力机制。",     # 数字前紧邻字母
        "Transformer 2017 年谷歌团队提出注意力机制。",    # 标准空格变体
        "Transformer２０１７年谷歌团队提出注意力机制。",   # 全角数字
        "Transformer ２０１７ 年谷歌团队提出注意力机制。", # 全角 + 空格
    ],
)
def test_year_number_ledger_keys_stable_across_whitespace_variants(
    original_variant: str,
) -> None:
    """「2017 年/2017年/２０１７年」必须提取同一规范化键（2017|年）。"""
    ledger = compile_source_ledger(original_variant)
    entry = ledger.entries[0]
    assert "2017|年" in entry.numbers, entry.numbers
    assert "2017" in entry.dates, entry.dates


@pytest.mark.parametrize(
    ("original", "candidate"),
    [
        # 原文紧邻无空格，候选带空格（模型常见输出）：不得误判新增无来源
        (
            "Transformer2017年谷歌团队提出注意力机制。",
            "Transformer 2017 年谷歌团队提出注意力机制。",
        ),
        # 原文带空格，候选紧邻无空格：不得误判数字被改动
        (
            "Transformer 2017 年谷歌团队提出注意力机制。",
            "Transformer2017年谷歌团队提出注意力机制。",
        ),
        # 原文全角，候选半角空格
        (
            "Transformer２０１７年谷歌团队提出注意力机制。",
            "Transformer 2017 年谷歌团队提出注意力机制。",
        ),
        # 原文半角，候选全角空格
        (
            "Transformer 2017 年谷歌团队提出注意力机制。",
            "Transformer２０１７年谷歌团队提出注意力机制。",
        ),
    ],
)
def test_year_number_whitespace_variants_do_not_misjudge(
    original: str, candidate: str
) -> None:
    """空白/全半角变体下，沿用原文年份不再误判 UNATTRIBUTED_CLAIM/NUMBER_CHANGED。"""
    ledger = compile_source_ledger(original)
    result = run_fidelity_check(ledger, candidate)
    assert result.passed, [
        (f.code, f.note) for f in result.blocking_failures
    ]
    assert not any(
        f.code in (FidelityFailureCode.UNATTRIBUTED_CLAIM, FidelityFailureCode.NUMBER_CHANGED)
        for f in result.blocking_failures
    )


@pytest.mark.parametrize(
    "original",
    [
        # 生产场景：原文含「2017 年」，用户授权假设后合规候选应通过硬门。
        # 空格变体在修复前即通过（基线不误判），紧邻字母/全角变体是修复
        # 前真实误判 UNATTRIBUTED_CLAIM 的复现输入（Issue 01 缺陷 c）。
        "Transformer 2017 年谷歌团队提出的注意力机制成为基础架构。",
        "Transformer2017年谷歌团队提出的注意力机制成为基础架构。",
        "Transformer２０１７年谷歌团队提出的注意力机制成为基础架构。",
    ],
)
def test_user_scenario_year_number_with_assumption_passes(original: str) -> None:
    """原文含「2017 年」类数字，授权假设后合规候选应通过硬门。"""
    ledger = compile_source_ledger(original, primary_label="聊天内原文")
    candidate = (
        original
        + "比如，假设这一机制继续演进，模型可能更高效。"
    )
    result = run_fidelity_check(ledger, candidate, allow_assumptions=True)
    assert result.passed, [
        (f.code, f.note) for f in result.blocking_failures
    ]
    assert not any(
        f.code in (FidelityFailureCode.UNATTRIBUTED_CLAIM, FidelityFailureCode.NUMBER_CHANGED)
        for f in result.blocking_failures
    )


# ---------------------------------------------------------------------------
# 3. 保留检查：关键项破坏 → 硬门失败
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "candidate", "code"),
    [
        # 删除否定
        ("他不吸烟。", "他吸烟。", FidelityFailureCode.DIRECTION_FLIPPED),
        # 反转因果
        ("高温会抑制光合效率。", "高温会促进光合效率。", FidelityFailureCode.DIRECTION_FLIPPED),
        # 放大结论
        ("尚不能证明其普遍适用。", "证明其普遍适用。", FidelityFailureCode.STRENGTH_UPGRADED),
        # 改变数字单位
        ("约为 25 μmol·m⁻²·s⁻¹。", "约为 30 μmol·m⁻²·s⁻¹。", FidelityFailureCode.NUMBER_CHANGED),
        ("25 μmol·m⁻²·s⁻¹", "25 mmol·m⁻²·s⁻¹", FidelityFailureCode.NUMBER_CHANGED),
        # 替换专名
        ("张伟教授分析了样本。", "李强教授分析了样本。", FidelityFailureCode.PROPER_NOUN_CHANGED),
        # 改写精确引语
        ('他说："数据必须保持原样"。', '他说："数据可以改变"。', FidelityFailureCode.QUOTE_CHANGED),
        # 日期改变
        ("该项目于 2020 年启动。", "该项目于 2021 年启动。", FidelityFailureCode.DATE_CHANGED),
        # 引用删除
        ("结果见 (Zhang et al., 2021)。", "结果如下。", FidelityFailureCode.CITATION_CHANGED),
        # URL 改变
        ("参见 https://example.com/paper。", "参见 https://example.com/other。",
         FidelityFailureCode.URL_CHANGED),
        # 公式改变
        ("6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂", "6CO₂ + 12H₂O → C₆H₁₂O₆ + 6O₂",
         FidelityFailureCode.FORMULA_CHANGED),
        # 用户指定措辞被改
        ("必须保留「每临大事有静气」。", "必须保留「大事面前要安静」。",
         FidelityFailureCode.QUOTE_CHANGED),
    ],
)
def test_fidelity_gate_blocks_breakage(
    original: str, candidate: str, code: FidelityFailureCode
) -> None:
    ledger = compile_source_ledger(original, user_phrases=["每临大事有静气"])
    result = run_fidelity_check(ledger, candidate)
    assert not result.passed
    assert any(
        f.code == code and f.severity == FidelitySeverity.BLOCKING
        for f in result.blocking_failures
    ), result.blocking_failures


def test_qualifier_removal_is_needs_confirmation() -> None:
    original = "目前只能说初步结果支持。"
    candidate = "只能说初步结果支持。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed  # 非关键不阻断
    assert any(f.severity == FidelitySeverity.NEEDS_USER_CONFIRMATION
               for f in result.needs_confirmation)


def test_strength_downgrade_is_needs_confirmation() -> None:
    original = "该机制已被证实有效。"
    candidate = "该机制可能有效。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed
    assert any(f.code == FidelityFailureCode.STRENGTH_DOWNGRADED
               for f in result.needs_confirmation)


# ---------------------------------------------------------------------------
# 4. 新增 claim 必须绑定账本来源
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "appended",
    [
        "上周朋友告诉我同样如此。",              # 时间地点 + 事件
        "2019—2023 年收集了 5000 例样本。",       # 日期 + 数字 + 样本
        "新版本新增了离线模式。",                # 功能
        "该项目于 2021 年在杭州启动。",           # 日期 + 地点
    ],
)
def test_unattributed_additive_claims_block(appended: str) -> None:
    original = "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。"
    ledger = compile_source_ledger(original)
    result = run_fidelity_check(ledger, original + appended)
    assert not result.passed
    assert any(
        f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM
        for f in result.blocking_failures
    ), result.blocking_failures


def test_additive_first_person_is_intercepted() -> None:
    original = "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。"
    result = run_fidelity_check(
        compile_source_ledger(original),
        original + "我以前也这样做过，确实有效。",
    )
    assert not result.passed
    assert any(
        f.code == FidelityFailureCode.FIRST_PERSON_UNBOUND
        for f in result.blocking_failures
    )


def test_new_claim_bound_to_supplement_passes() -> None:
    original = "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。"
    ledger = compile_source_ledger(
        original, supplements=[("补充材料", "实验收集了 5000 例样本。")]
    )
    candidate = "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。实验收集了 5000 例样本。"
    result = run_fidelity_check(ledger, candidate)
    assert result.passed, result.blocking_failures
    assert result.summary.new_claim_count >= 1
    assert result.summary.attributed_claim_count >= 1


def test_new_claim_bound_to_external_source_passes() -> None:
    original = "当地气候湿润。"
    ledger = compile_source_ledger(
        original, external_allowed=[("外部网页", "该地区年降雨量为 500 mm。")]
    )
    candidate = "当地气候湿润，该地区年降雨量为 500 mm。"
    result = run_fidelity_check(ledger, candidate)
    assert result.passed, result.blocking_failures


def test_new_citation_without_source_blocks() -> None:
    original = "结果是可靠的。"
    candidate = "结果是可靠的 (Smith, 2024)。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert not result.passed
    assert any(f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM
               for f in result.blocking_failures)


# ---------------------------------------------------------------------------
# 5. 第一人称：当下判断与亲历分开建模
# ---------------------------------------------------------------------------


def test_present_judgment_not_intercepted() -> None:
    original = "该方案可能更有效。"
    candidate = "我认为该方案可能更有效。我更倾向于采用它。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed, result.blocking_failures


def test_unbound_past_experience_is_intercepted() -> None:
    original = "该方案可能更有效。"
    candidate = "我以前也这样做过，确实更有效。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert not result.passed
    assert any(f.code == FidelityFailureCode.FIRST_PERSON_UNBOUND
               for f in result.blocking_failures)
    assert result.summary.first_person_interception_count >= 1


def test_past_experience_bound_to_original_passes() -> None:
    original = "我曾经在实验室做过类似实验，该方案确实有效。"
    candidate = "我曾经在实验室做过类似实验。我认为该方案确实有效。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed, result.blocking_failures


# ---------------------------------------------------------------------------
# 6. 显式假设：契约允许才可用，且不得冒充亲历/承载高风险事实
# ---------------------------------------------------------------------------


def test_allowed_low_risk_assumption_passes() -> None:
    original = "每周五上午统一处理邮件。"
    candidate = "每周五上午统一处理邮件，比如统一回复客户邮件。"
    result = run_fidelity_check(
        compile_source_ledger(original), candidate,
        allow_assumptions=True,
    )
    assert result.passed, result.blocking_failures
    assert result.summary.assumption_count >= 1


def test_assumption_without_contract_permission_blocks() -> None:
    original = "每周五上午统一处理邮件。"
    candidate = "每周五上午统一处理邮件，比如统一回复客户邮件。"
    result = run_fidelity_check(
        compile_source_ledger(original), candidate,
        allow_assumptions=False,
    )
    assert not result.passed
    assert any(f.code == FidelityFailureCode.ASSUMPTION_NOT_ALLOWED
               for f in result.blocking_failures)


def test_assumption_pretending_first_person_blocks() -> None:
    original = "每周五上午统一处理邮件。"
    candidate = "每周五上午统一处理邮件，比如我以前也亲自做过这件事。"
    result = run_fidelity_check(
        compile_source_ledger(original), candidate,
        allow_assumptions=True,
    )
    assert not result.passed
    assert any(f.code in (
        FidelityFailureCode.FIRST_PERSON_UNBOUND,
        FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
    ) for f in result.blocking_failures)


def test_assumption_carrying_high_risk_fact_blocks() -> None:
    original = "每周五上午统一处理邮件。"
    candidate = "每周五上午统一处理邮件，比如收集了 5000 例样本。"
    result = run_fidelity_check(
        compile_source_ledger(original), candidate,
        allow_assumptions=True,
    )
    assert not result.passed
    assert any(f.code == FidelityFailureCode.ASSUMPTION_CARRIES_FACT
               for f in result.blocking_failures)


# ---------------------------------------------------------------------------
# 7. 不误判：合法术语、原文比喻、被保护引语
# ---------------------------------------------------------------------------


def test_legal_terminology_not_flagged_as_new_claim() -> None:
    original = "光合作用在叶绿体中进行，产生氧气。"
    candidate = "光合作用在叶绿体中进行，产生氧气，这是植物的重要生理过程。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed, result.blocking_failures


def test_original_metaphor_and_quote_not_flagged() -> None:
    original = '时间就像流水，一去不复返。他说："数据必须保持原样"。'
    candidate = '时间就像流水，一去不复返。他说："数据必须保持原样"。'
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed
    # 原文比喻与保护引语均不被误判为新增作者事实
    assert result.summary.unattributed_claim_count == 0
    assert result.summary.preserved_count >= 1


def test_self_comparison_preserves_everything() -> None:
    original = (
        "2019—2023 年测量 25 μmol·m⁻²·s⁻¹，全角 ３５℃ 等价。"
        '他说："多段引语。第二段。"'
    )
    result = run_fidelity_check(compile_source_ledger(original), original)
    assert result.passed
    assert result.summary.unattributed_claim_count == 0
    assert result.summary.blocking_count == 0


def test_vague_time_is_undetermined_not_blocking() -> None:
    original = "光合速率约为 25 μmol·m⁻²·s⁻¹。"
    candidate = "光合速率约为 25 μmol·m⁻²·s⁻¹。最近有研究显示该速率可能更高。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert result.passed  # 非关键无法判定 → 不阻断
    assert any(f.code == FidelityFailureCode.UNDETERMINED
               for f in result.needs_confirmation)


# ---------------------------------------------------------------------------
# 8. 失败关闭：版本不匹配、账本哈希变化、空结果
# ---------------------------------------------------------------------------


def test_version_mismatch_fails_closed() -> None:
    ledger = compile_source_ledger("原文。")
    ledger.ledger_version = "1"
    with pytest.raises(FidelityCheckError):
        run_fidelity_check(ledger, "原文。")


def test_ledger_hash_tamper_fails_closed() -> None:
    ledger = compile_source_ledger("原文。")
    ledger.ledger_hash = "tampered-hash"
    with pytest.raises(FidelityCheckError):
        run_fidelity_check(ledger, "原文。")


def test_empty_candidate_fails_closed() -> None:
    ledger = compile_source_ledger("原文。")
    with pytest.raises(FidelityCheckError):
        run_fidelity_check(ledger, "   ")


def test_checker_version_is_stable() -> None:
    ledger = compile_source_ledger("原文。")
    result = run_fidelity_check(ledger, "原文。")
    assert result.checker_version == FIDELITY_CHECKER_VERSION
    assert result.ledger_version == SOURCE_LEDGER_VERSION
    assert result.ledger_hash == ledger.ledger_hash


def test_generate_path_checks_new_claims_only() -> None:
    constraints = "主题：睡眠时长。不得声称睡眠时长决定健康水平。"
    ledger = compile_source_ledger(constraints)
    candidate = (
        "睡眠时长是个体差异很大的参考指标，并不决定健康水平。"
        "2019 年一项研究收集了 5000 例样本。"  # 新增无来源
    )
    result = run_fidelity_check(
        ledger, candidate,
        contract_path=HumanizerPath.GENERATE,
    )
    assert not result.passed
    assert any(f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM
               for f in result.blocking_failures)


def test_quote_and_number_with_same_key_coexist() -> None:
    """引语「35」与正文数字 35 不得互相遮挡（跨类别去重）。"""
    original = '他说："35"。而实际数值为 35。'
    ledger = compile_source_ledger(original)
    entry = ledger.entries[0]
    assert any(q == "35" for q in entry.quotes)
    assert any(n.split("|")[0] == "35" for n in entry.numbers)
    result = run_fidelity_check(ledger, original)
    assert result.passed


def test_friend_told_event_is_unattributed() -> None:
    """「朋友告诉我」属外部事件 claim，无来源即拦截。"""
    original = "水稻光合速率约为 25 μmol·m⁻²·s⁻¹。"
    candidate = original + "朋友告诉我同样如此。"
    result = run_fidelity_check(compile_source_ledger(original), candidate)
    assert not result.passed
    assert any(f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM
               for f in result.blocking_failures)


def test_existing_vague_time_is_bound_not_confirmation() -> None:
    """原文已有的模糊时间不构成新增，自比较不产生需确认项。"""
    original = "最近有研究显示该速率可能更高。"
    result = run_fidelity_check(compile_source_ledger(original), original)
    assert result.passed
    assert not any(f.code == FidelityFailureCode.UNDETERMINED
                   for f in result.needs_confirmation)


def test_past_experience_cannot_bind_without_permission() -> None:
    """外部材料即使含亲历，无第一人称权限也不得绑定候选亲历。"""
    ledger = compile_source_ledger(
        "该方案可能更有效。",
        external_allowed=[("外部", "我曾经做过类似的实验。")],
    )
    result = run_fidelity_check(
        ledger, "该方案可能更有效。我以前也这样做过，确实有效。",
    )
    assert not result.passed
    assert any(f.code == FidelityFailureCode.FIRST_PERSON_UNBOUND
               for f in result.blocking_failures)


def test_assumption_smuggling_method_is_blocked() -> None:
    """「比如采用深度学习方法」在假设中走私研究方法 → 假设承载事实。"""
    original = "每周五上午统一处理邮件。"
    candidate = "每周五上午统一处理邮件，比如采用深度学习方法。"
    result = run_fidelity_check(
        compile_source_ledger(original), candidate,
        allow_assumptions=True,
    )
    assert not result.passed
    assert any(f.code == FidelityFailureCode.ASSUMPTION_CARRIES_FACT
               for f in result.blocking_failures)


def test_user_phrase_from_constraints_protected() -> None:
    """硬约束「必须保留「每临大事有静气」」应提取为用户指定措辞保护。"""
    contract = HumanizerTaskContract(
        path=HumanizerPath.REWRITE,
        genre=Genre.POPULAR_SCIENCE,
        source_text="原文。",
        hard_constraints=["必须保留「每临大事有静气」这句话。"],
    )
    phrases = HumanizerService._user_phrases_from_constraints(contract)
    assert "每临大事有静气" in phrases
