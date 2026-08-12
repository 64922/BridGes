"""Issue 03：版本化表达任务契约编译器的表驱动测试。

覆盖测试计划 7 项：
1. surface × operation × reality × intensity × evidence mode 表驱动，含用户显式选择。
2. 邮件/报告/教程/观点文/演讲/科普/科研段落/未声明体裁不误落科普必现模板。
3. 扩写材料不足只问一个最高价值问题；不补材料降级 shorten / use_placeholders。
4. 三个强度档都携带不伤害约束；轻度不自动升级。
5. 第一人称允许/禁止、显式虚构、混合文本、假设例子权限回归。
6. 同一任务跨规则版本重试快照与哈希不变；未知版本执行前稳定拒绝。
7. 画像缺失/撤回场景不推测用户，不改变事实标准。
"""

from __future__ import annotations

import pytest

from bridges.contracts.expression import Genre
from bridges.contracts.expression_task import (
    EvidenceRevisionMode,
    ExpressionTaskContract,
    MaterialSufficiency,
    Operation,
    RealityMode,
    RewriteIntensity,
    SourceScope,
    Surface,
)
from bridges.skills.humanizer.contract_compiler import (
    ContractVersionError,
    CompileRequest,
    compile_task_contract,
)
from bridges.skills.humanizer.genre_rules import check_genre, genre_rule_set
from bridges.skills.humanizer.intent import route_humanizer_message

# ---------------------------------------------------------------------------
# 测试计划 1：surface × operation × reality × intensity × evidence mode 表驱动
# ---------------------------------------------------------------------------

_EXPECTED = [
    # 改写原文：现实、标准强度、保留证据
    {
        "content": "润色这段文字",
        "source": "数据表明，实验组平均反应时间缩短了 15%。",
        "surface": Surface.ARTICLE,
        "operation": Operation.REWRITE,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.STANDARD,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.SUFFICIENT,
    },
    # 扩写：现实、默认标准
    {
        "content": "把这句扩写到 300 字",
        "source": "研究显示睡眠不足影响记忆。",
        "surface": Surface.ARTICLE,
        "operation": Operation.EXPAND,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.STANDARD,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.ASK_ONE_QUESTION,
    },
    # 按主题生成：现实
    {
        "content": "帮我写一篇关于深度学习的文章",
        "source": None,
        "surface": Surface.ARTICLE,
        "operation": Operation.GENERATE_BY_TOPIC,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.STANDARD,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.ASK_ONE_QUESTION,
    },
    # 显式虚构故事
    {
        "content": "写一个科幻故事，设定在火星殖民时代",
        "source": None,
        "surface": Surface.ARTICLE,
        "operation": Operation.GENERATE_BY_TOPIC,
        "reality": RealityMode.FICTIONAL,
        "intensity": RewriteIntensity.STANDARD,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.ASK_ONE_QUESTION,
    },
    # 显式深度改写 + 证据安全修订
    {
        "content": "深度改写这篇报告，可以调整结论强度",
        "source": "本次实验未观察到显著差异，结论需谨慎解读。",
        "surface": Surface.ARTICLE,
        "operation": Operation.REWRITE,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.DEEP,
        "evidence": EvidenceRevisionMode.EVIDENCE_SAFE,
        "material": MaterialSufficiency.SUFFICIENT,
    },
    # 显式轻度 + 用户显式强度选择优先
    {
        "content": "轻度改一下即可",
        "source": "你好，感谢你的帮助。",
        "surface": Surface.ARTICLE,
        "operation": Operation.REWRITE,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.LIGHT,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.SUFFICIENT,
    },
    # 普通聊天：surface=chat（无文章词）
    {
        "content": "你觉得今天天气怎么样",
        "source": None,
        "surface": Surface.CHAT,
        "operation": Operation.GENERATE_BY_TOPIC,
        "reality": RealityMode.REAL,
        "intensity": RewriteIntensity.STANDARD,
        "evidence": EvidenceRevisionMode.PRESERVE,
        "material": MaterialSufficiency.ASK_ONE_QUESTION,
    },
]


@pytest.mark.parametrize(
    "case",
    _EXPECTED,
    ids=[c["content"][:12] for c in _EXPECTED],
)
def test_table_driven_contract_compilation(case: dict) -> None:
    result = compile_task_contract(
        CompileRequest(content=case["content"], source_material=case["source"])
    )
    contract = result.contract
    assert contract.surface == case["surface"]
    assert contract.operation == case["operation"]
    assert contract.reality_mode == case["reality"]
    assert contract.rewrite_intensity == case["intensity"]
    assert contract.evidence_revision_mode == case["evidence"]
    assert contract.material_sufficiency == case["material"]
    # 每个合同都带不可变版本哈希，且哈希覆盖全部语义字段。
    assert contract.version_hash
    assert contract.compute_version_hash() == contract.version_hash
    assert contract.schema_version == "expression-task-v1"


def test_explicit_intensity_overrides_automatic_recommendation() -> None:
    """用户显式强度选择优先于自动推荐，且裁决链记录优先级层。"""
    result = compile_task_contract(
        CompileRequest(
            content="深度改写这篇文章",
            source_material="这是一段原文。",
            explicit_intensity=RewriteIntensity.LIGHT,
        )
    )
    assert result.contract.rewrite_intensity == RewriteIntensity.LIGHT
    layers = [a.priority_layer.value for a in result.record.adjudications]
    assert "user_and_safety" in layers


def test_chat_conversation_mode_and_profile_metadata() -> None:
    """普通聊天编译携带模式与最小画像条目数，不读画像正文。"""
    result = compile_task_contract(
        CompileRequest(
            content="帮我解释一下光合作用",
            surface_hint=Surface.CHAT,
            conversation_mode=None,
            profile_slice_id="slice-1",
            profile_item_count=3,
        )
    )
    contract = result.contract
    assert contract.surface == Surface.CHAT
    assert contract.profile_slice_id == "slice-1"
    assert contract.profile_item_count == 3
    # 审计记录不含画像正文，只含条目数。
    assert result.record.profile_item_count == 3
    assert result.record.profile_slice_id == "slice-1"


# ---------------------------------------------------------------------------
# 测试计划 2：各类请求不误落科普必现模板
# ---------------------------------------------------------------------------

_GENRE_ROUTING = [
    ("帮我润色这封邮件", None),
    ("改写这份季度报告", None),
    ("帮我写一篇教程", None),
    ("润色我的观点文章", None),
    ("帮我写演讲稿", None),
    ("帮我改改这个通知", None),
    ("润色一下简历", None),
    ("科普一下黑洞", Genre.POPULAR_SCIENCE),
    ("写一段科研汇报", Genre.RESEARCH_REPORT),
    ("润色这段论文摘要", Genre.PAPER_ASSIST),
    ("写课堂讲稿", Genre.LECTURE_SCRIPT),
]


@pytest.mark.parametrize(
    ("content", "expected_genre"),
    _GENRE_ROUTING,
    ids=[c[0][:10] for c in _GENRE_ROUTING],
)
def test_no_popular_science_default(content: str, expected_genre) -> None:
    """未识别体裁使用通用文章 profile；科普/科研/论文/讲稿正确识别。"""
    result = compile_task_contract(
        CompileRequest(content=content, source_material="这是待处理的内容。")
    )
    assert result.contract.genre == expected_genre
    if expected_genre is None:
        # 通用 profile 无必现/禁止元素，体裁复核必然通过。
        genre_check = check_genre("任何文本", None)
        assert genre_check.passed
        assert genre_rule_set(None).display_name == "通用文章"
        # 通用 profile 的 required/prohibited 均为空，不注入科普必现句型。
        generic = genre_rule_set(None)
        assert not generic.required
        assert not generic.prohibited
        # 裁决链记录未识别体裁使用通用 profile。
        assert any(
            a.rule_id == "genre-generic-profile" for a in result.record.adjudications
        )


def test_mail_is_not_forced_into_popular_science_templates() -> None:
    """旧行为：邮件默认落入科普；新行为：通用 profile，不注入必含/禁止行。"""
    result = compile_task_contract(
        CompileRequest(content="帮我润色这封邮件", source_material="尊敬的客户：感谢您选择我们。")
    )
    contract = result.contract
    assert contract.genre is None
    # 从契约派生的旧契约也保持 None，而不是科普兜底。
    route = route_humanizer_message("帮我润色这封邮件：尊敬的客户：感谢您选择我们。")
    assert route is not None
    assert route.skill_input.contract.genre is None
    assert route.skill_input.expression_contract is not None
    assert route.skill_input.expression_contract.version_hash


# ---------------------------------------------------------------------------
# 测试计划 3：材料不足只问一个问题；不补材料降级
# ---------------------------------------------------------------------------

def test_expand_with_single_opinion_asks_one_question() -> None:
    """扩写到 3000 字但只有一句观点 → 只返回一个最高价值问题。"""
    result = compile_task_contract(
        CompileRequest(
            content="扩写到 3000 字",
            source_material="我觉得读书很重要。",
        )
    )
    contract = result.contract
    assert contract.operation == Operation.EXPAND
    assert contract.material_sufficiency == MaterialSufficiency.ASK_ONE_QUESTION
    assert contract.one_question is not None
    assert len(contract.one_question) > 5
    # 审计记录标记追问，且问题只有一个。
    assert result.record.asked_question
    assert any(
        a.rule_id == "material-ask-one-question" for a in result.record.adjudications
    )


def test_downgrade_to_shorten_when_user_refuses_more_material() -> None:
    """模拟用户不补材料：有部分材料 → shorten。"""
    result = compile_task_contract(
        CompileRequest(
            content="扩写到 3000 字",
            source_material="我觉得读书很重要。",
            downgrade_material=True,
        )
    )
    assert result.contract.material_sufficiency == MaterialSufficiency.SHORTEN
    assert result.record.degradation_reason
    assert result.contract.one_question is None


def test_downgrade_to_placeholders_when_no_material() -> None:
    """模拟用户不补材料：完全无材料 → use_placeholders。"""
    result = compile_task_contract(
        CompileRequest(
            content="写一篇关于量子计算的文章",
            downgrade_material=True,
        )
    )
    assert result.contract.material_sufficiency == MaterialSufficiency.USE_PLACEHOLDERS
    assert result.record.degradation_reason


def test_rewrite_never_expands_facts_due_to_missing_material() -> None:
    """纯改写缺原文 → 追问原文；且不因材料不足扩大事实范围。"""
    result = compile_task_contract(
        CompileRequest(content="帮我润色一下", source_material=None)
    )
    assert result.contract.operation == Operation.REWRITE
    assert result.contract.material_sufficiency == MaterialSufficiency.ASK_ONE_QUESTION
    assert result.contract.one_question == "请提供需要改写的原文。"
    assert any(
        a.rule_id == "material-rewrite-no-expand" for a in result.record.adjudications
    )


# ---------------------------------------------------------------------------
# 测试计划 4：三个强度档都携带不伤害约束；轻度不自动升级
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("intensity", list(RewriteIntensity))
def test_every_intensity_carries_no_harm_rule(intensity: RewriteIntensity) -> None:
    result = compile_task_contract(
        CompileRequest(
            content="改写这段",
            source_material="原文已经写得很好。",
            explicit_intensity=intensity,
        )
    )
    assert result.contract.rewrite_intensity == intensity
    # 三个档位都必须携带不伤害规则（原文已自然时不强行加场景/比喻/第一人称/金句）。
    assert any(
        a.rule_id == "intensity-no-harm" for a in result.record.adjudications
    )
    # 强度档位不改变来源与第一人称权限（同一来源下稳定）。
    assert result.contract.source_scope.value == "original_only"
    if intensity == RewriteIntensity.LIGHT:
        assert any(
            a.rule_id == "intensity-light-no-upgrade" for a in result.record.adjudications
        )
    # 用户显式选择优先于自动推荐，记录在裁决链。
    assert any(a.rule_id == "intensity-explicit" for a in result.record.adjudications)


def test_light_intensity_never_upgrades_automatically() -> None:
    result = compile_task_contract(
        CompileRequest(content="轻度改一下", source_material="短原文。")
    )
    assert result.contract.rewrite_intensity == RewriteIntensity.LIGHT


# ---------------------------------------------------------------------------
# 测试计划 5：第一人称与虚构/假设权限回归
# ---------------------------------------------------------------------------

def test_first_person_allowed_explicitly() -> None:
    result = compile_task_contract(
        CompileRequest(content="可以写第一人称", source_material="这是原文。")
    )
    assert result.contract.first_person_permission is True


def test_first_person_blocked_explicitly() -> None:
    result = compile_task_contract(
        CompileRequest(content="不要用第一人称", source_material="这是我写的原文。")
    )
    assert result.contract.first_person_permission is False


def test_first_person_preserved_when_original_has_it() -> None:
    result = compile_task_contract(
        CompileRequest(content="润色这段", source_material="我认为实验设计需要改进。")
    )
    assert result.contract.first_person_permission is True


def test_first_person_default_off_for_new_text() -> None:
    result = compile_task_contract(
        CompileRequest(content="帮我写一篇科普", source_material="黑洞的视界。")
    )
    assert result.contract.first_person_permission is False


def test_fictional_mode_marks_creation_scope() -> None:
    """显式虚构：允许假设，且裁决链必须标明创作范围，不得投影为用户事实。"""
    result = compile_task_contract(
        CompileRequest(content="写一个虚构故事", source_material="火星殖民时代。")
    )
    contract = result.contract
    assert contract.reality_mode == RealityMode.FICTIONAL
    assert contract.hypothetical_permission is True
    assert any(
        a.rule_id == "hypothetical-fictional" for a in result.record.adjudications
    )


def test_mixed_text_marks_creation_scope() -> None:
    result = compile_task_contract(
        CompileRequest(content="基于真实事件改编一个故事", source_material="真实历史事件。")
    )
    assert result.contract.reality_mode == RealityMode.MIXED
    assert result.contract.hypothetical_permission is True


def test_real_mode_rejects_invented_experiences() -> None:
    """现实模式：即使允许第一人称，裁决链也锁死不补亲历。"""
    result = compile_task_contract(
        CompileRequest(
            content="可以写第一人称，润色这段",
            source_material="实验结果表明处理组效果更好。",
        )
    )
    contract = result.contract
    assert contract.reality_mode == RealityMode.REAL
    assert contract.first_person_permission is True
    assert any(
        a.rule_id == "first-person-real" for a in result.record.adjudications
    )


def test_hypothetical_allowed_explicitly() -> None:
    result = compile_task_contract(
        CompileRequest(content="允许假设，润色这段", source_material="原文内容。")
    )
    assert result.contract.hypothetical_permission is True


def test_hypothetical_blocked_explicitly() -> None:
    result = compile_task_contract(
        CompileRequest(
            content="写一个虚构故事，不要用假设",
            source_material="某个设定。",
        )
    )
    assert result.contract.hypothetical_permission is False


# ---------------------------------------------------------------------------
# 测试计划 6：快照重试与未知版本拒绝
# ---------------------------------------------------------------------------

def test_retry_reuses_same_contract_and_hash() -> None:
    """同一任务跨规则版本重试：快照与哈希不变，不重新编译漂移。"""
    first = compile_task_contract(
        CompileRequest(
            content="深度改写这篇报告",
            source_material="实验未观察到显著差异。",
            explicit_evidence_safe=True,
        )
    )
    # 模拟规则热更新后同一任务重试：回传旧快照。
    second = compile_task_contract(
        CompileRequest(
            content="深度改写这篇报告",
            source_material="实验未观察到显著差异。",
            previous_contract=first.contract,
        )
    )
    assert second.reused is True
    assert second.contract is first.contract
    assert second.contract.version_hash == first.contract.version_hash
    assert second.contract.evidence_revision_mode == EvidenceRevisionMode.EVIDENCE_SAFE
    assert second.contract.rewrite_intensity == RewriteIntensity.DEEP
    # 重试记录携带同一版本哈希。
    assert second.record.version_hash == first.contract.version_hash


def test_unknown_version_rejected_before_execution() -> None:
    """未知版本执行前稳定拒绝，不允许静默重新编译。"""
    stale = ExpressionTaskContract(
        schema_version="expression-task-v0",
        version_hash="x" * 64,
        surface=Surface.ARTICLE,
        operation=Operation.REWRITE,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=RewriteIntensity.STANDARD,
        speaker_position="用户",
        first_person_permission=False,
        hypothetical_permission=False,
        material_sufficiency=MaterialSufficiency.SUFFICIENT,
        source_scope=SourceScope.ORIGINAL_ONLY,
        evidence_revision_mode=EvidenceRevisionMode.PRESERVE,
    )
    with pytest.raises(ContractVersionError):
        compile_task_contract(
            CompileRequest(content="改写", previous_contract=stale)
        )


def test_tampered_snapshot_rejected() -> None:
    """快照哈希不匹配（被修改过）→ 拒绝重试。"""
    first = compile_task_contract(
        CompileRequest(content="润色这段", source_material="原文内容。")
    )
    tampered = first.contract.model_copy(update={"audience": "被篡改的受众"})
    with pytest.raises(ContractVersionError):
        compile_task_contract(
            CompileRequest(content="润色这段", previous_contract=tampered)
        )


# ---------------------------------------------------------------------------
# 测试计划 7：画像缺失/撤回场景
# ---------------------------------------------------------------------------

def test_profile_missing_does_not_invent_user_traits() -> None:
    """画像缺失：编译不推测用户人格，只记录无切片。"""
    result = compile_task_contract(
        CompileRequest(
            content="帮我润色这段",
            source_material="原文。",
            profile_slice_id=None,
            profile_item_count=0,
        )
    )
    assert result.contract.profile_slice_id is None
    assert result.contract.profile_item_count == 0
    # 不因画像缺失改变事实标准：证据修订模式仍默认保留结论。
    assert result.contract.evidence_revision_mode == EvidenceRevisionMode.PRESERVE
    assert result.contract.reality_mode == RealityMode.REAL


def test_profile_revoked_does_not_change_fact_standard() -> None:
    """画像版本不可用/撤回：调用方不传切片，契约仍稳定编译且事实标准不变。"""
    without_slice = compile_task_contract(
        CompileRequest(content="润色这段", source_material="原文。")
    )
    with_slice = compile_task_contract(
        CompileRequest(
            content="润色这段",
            source_material="原文。",
            profile_slice_id="revoked-slice",
            profile_item_count=2,
        )
    )
    # 事实相关字段不因画像有无而改变。
    for field in (
        "reality_mode",
        "evidence_revision_mode",
        "source_scope",
        "material_sufficiency",
    ):
        assert getattr(without_slice.contract, field) == getattr(
            with_slice.contract, field
        )


def test_audit_record_never_contains_profile_content() -> None:
    """审计记录与契约不保存画像正文或完整私人材料。"""
    result = compile_task_contract(
        CompileRequest(
            content="润色这段",
            source_material="这是用户的私人材料正文。",
            profile_slice_id="slice-9",
            profile_item_count=4,
        )
    )
    record = result.record.model_dump_json()
    assert "slice-9" in record  # 切片标识可审计
    assert "私人材料正文" not in record  # 材料正文不进入审计
    contract_json = result.contract.model_dump_json()
    assert "私人材料正文" not in contract_json


# ---------------------------------------------------------------------------
# 审查修复回归：否定反转、三档不伤害、信息性查询 surface
# ---------------------------------------------------------------------------

def test_negated_evidence_revision_never_flips_to_safe() -> None:
    """“不允许调整结论”不得因包含“允许调整结论”子串被误判为证据安全修订。"""
    result = compile_task_contract(
        CompileRequest(
            content="润色这段报告，不允许调整结论",
            source_material="实验未观察到显著差异。",
        )
    )
    assert result.contract.evidence_revision_mode == EvidenceRevisionMode.PRESERVE
    assert not any(
        a.rule_id == "evidence-safe-explicit" for a in result.record.adjudications
    )


def test_negated_hypothetical_never_flips_to_allowed() -> None:
    """“不可以假设”不得被误判为允许假设。"""
    result = compile_task_contract(
        CompileRequest(
            content="写一个虚构故事，不可以假设",
            source_material="某个设定。",
        )
    )
    assert result.contract.hypothetical_permission is False


def test_informational_question_never_routes_to_article() -> None:
    """信息性提问（解释/含义）即使命中“帮我”也不得误判为文章。"""
    result = compile_task_contract(
        CompileRequest(content="帮我解释一下光合作用", source_material=None)
    )
    assert result.contract.surface == Surface.CHAT
    assert result.contract.material_sufficiency == MaterialSufficiency.ASK_ONE_QUESTION
