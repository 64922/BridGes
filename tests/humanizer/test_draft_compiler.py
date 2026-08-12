"""首稿正向生成 profile 提示编译测试（人味化改造 Issue 04）。

断言：每次只编译当前 profile 的 6—10 条高优先级规则；提示不含内部方法
ID、全量检测清单或旧体裁必现短语；首稿携带说话位置、读者下一问、材料
边界与第一人称/假设权限；未声明体裁使用通用文章 profile；无权限时不得
要求模型加入生活场景或亲历。
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
from bridges.contracts.humanizer import SourceLedger, SourceType, SourceEntry, SourceUsage
from bridges.skills.humanizer.draft_compiler import (
    DRAFT_OUTPUT_JSON_SCHEMA,
    compile_draft_prompt,
)


def _contract(
    *,
    genre: Genre | None = None,
    intensity: RewriteIntensity = RewriteIntensity.STANDARD,
    first_person: bool = False,
    hypothetical: bool = False,
    operation: Operation = Operation.REWRITE,
    material: MaterialSufficiency = MaterialSufficiency.SUFFICIENT,
    speaker: str = "用户本人（以作者身份）",
    source_scope: SourceScope = SourceScope.ORIGINAL_ONLY,
) -> ExpressionTaskContract:
    c = ExpressionTaskContract(
        schema_version="expression-task-v1",
        version_hash="hash",
        surface=Surface.ARTICLE,
        operation=operation,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=intensity,
        speaker_position=speaker,
        first_person_permission=first_person,
        hypothetical_permission=hypothetical,
        genre=genre,
        material_sufficiency=material,
        source_scope=source_scope,
        evidence_revision_mode=EvidenceRevisionMode.PRESERVE,
    )
    c.version_hash = c.compute_version_hash()
    return c


_SOURCE = "番茄工作法把时间切成 25 分钟的工作块和 5 分钟的休息块。四个工作块后休息 15 分钟。"


def _prompt(
    *,
    genre: Genre | None = None,
    intensity: RewriteIntensity = RewriteIntensity.STANDARD,
    first_person: bool = False,
    hypothetical: bool = False,
    operation: Operation = Operation.REWRITE,
    material: MaterialSufficiency = MaterialSufficiency.SUFFICIENT,
    ledger: SourceLedger | None = None,
    speaker: str = "用户本人（以作者身份）",
) -> str:
    result = compile_draft_prompt(
        _contract(
            genre=genre,
            intensity=intensity,
            first_person=first_person,
            hypothetical=hypothetical,
            operation=operation,
            material=material,
            speaker=speaker,
        ),
        source_text=_SOURCE,
        source_label="原文",
        ledger=ledger,
    )
    return result.system_prompt


def _ledger() -> SourceLedger:
    return SourceLedger(
        ledger_version="2",
        ledger_hash="h",
        entries=[
            SourceEntry(
                entry_id="src-1",
                source_type=SourceType.USER_ORIGINAL,
                content_hash="ch",
                usage=[SourceUsage.REWRITE],
            )
        ],
    )


@pytest.mark.parametrize(
    "intensity",
    [RewriteIntensity.LIGHT, RewriteIntensity.STANDARD, RewriteIntensity.DEEP],
)
def test_each_profile_compiles_six_to_ten_rules(intensity: RewriteIntensity) -> None:
    prompt = _prompt(intensity=intensity)
    section = prompt.split("【本次写作要点】")[1].split("【")[0]
    rules = [line for line in section.splitlines() if line.strip().startswith("- ")]
    assert 6 <= len(rules) <= 10, f"{intensity.value} 档编译规则数：{len(rules)}"


def test_prompt_has_no_internal_method_ids() -> None:
    prompt = _prompt()
    for marker in (
        "rewrite.main-clause-first",
        "detect.filler-phrase",
        "humanizer-method-rules-v1",
        "rule_id",
        "【检测面】",
        "【改写面】",
    ):
        assert marker not in prompt, f"提示泄漏内部标识：{marker}"


def test_prompt_has_no_full_detection_blacklist() -> None:
    prompt = _prompt()
    # 全量检测清单（13 条检测面规则）不应整体注入
    assert "清除协作痕迹" not in prompt
    assert "收敛谄媚开场" not in prompt


def test_prompt_has_no_old_genre_mandatory_phrases() -> None:
    for genre in (
        Genre.POPULAR_SCIENCE,
        Genre.LECTURE_SCRIPT,
        Genre.RESEARCH_REPORT,
        Genre.PAPER_ASSIST,
    ):
        prompt = _prompt(genre=genre)
        for phrase in ("必含", "必须出现", "缺失即复核不通过", "至少一个", "请加入"):
            assert phrase not in prompt, (
                f"{genre.value} 提示仍含旧体裁必现条件：{phrase}"
            )


def test_prompt_carries_speaker_position_and_material_boundary() -> None:
    prompt = _prompt(speaker="用户本人（以作者身份）")
    assert "说话位置" in prompt
    assert "以作者身份" in prompt
    assert "材料边界" in prompt
    assert "只能使用上方材料" in prompt


def test_prompt_carries_reader_next_question() -> None:
    prompt = _prompt()
    assert "读者下一问" in prompt
    assert "为什么" in prompt or "怎么做" in prompt or "然后呢" in prompt


def test_prompt_carries_first_person_and_hypothetical_permissions() -> None:
    prompt = _prompt(first_person=False, hypothetical=False)
    assert "第一人称：不使用" in prompt
    assert "假设：不使用" in prompt
    # 无权限时不得要求模型加入生活场景或亲历
    assert "亲历" in prompt
    assert "生活场景" in prompt
    assert "不虚构" in prompt


def test_prompt_allows_first_person_when_permitted() -> None:
    prompt = _prompt(first_person=True, hypothetical=True)
    assert "第一人称：允许" in prompt
    assert "假设：允许" in prompt
    assert "不虚构亲历" in prompt


def test_generic_profile_used_when_genre_undeclared() -> None:
    prompt = _prompt()
    assert "通用文章" in prompt


def test_named_genre_carries_goal_risk_and_optional_devices() -> None:
    prompt = _prompt(genre=Genre.POPULAR_SCIENCE)
    assert "任务目标" in prompt
    assert "风险" in prompt
    assert "可选表达" in prompt
    # 可选表达是「按需使用」而不是必现条件
    assert "按需使用" in prompt


def test_material_insufficient_uses_placeholder_boundary() -> None:
    prompt = _prompt(
        material=MaterialSufficiency.USE_PLACEHOLDERS,
        operation=Operation.GENERATE_BY_TOPIC,
    )
    assert "占位" in prompt
    assert "不编造" in prompt


def test_light_profile_preserves_voice_and_paragraphs() -> None:
    prompt = _prompt(intensity=RewriteIntensity.LIGHT)
    assert "保留" in prompt or "不改变" in prompt
    assert "作者" in prompt


def test_draft_output_schema_is_candidate_text_centric() -> None:
    assert DRAFT_OUTPUT_JSON_SCHEMA["required"] == ["final_text"]
    assert "edits" not in DRAFT_OUTPUT_JSON_SCHEMA["properties"]
    assert "fact_check" not in DRAFT_OUTPUT_JSON_SCHEMA["properties"]


def test_prompt_snapshot_is_stable_for_same_contract() -> None:
    first = _prompt()
    second = _prompt()
    assert first == second
    # 不同强度编译不同规则
    assert _prompt(intensity=RewriteIntensity.LIGHT) != _prompt(
        intensity=RewriteIntensity.DEEP
    )


def test_rule_count_and_ids_are_exposed_for_observability() -> None:
    result = compile_draft_prompt(
        _contract(intensity=RewriteIntensity.STANDARD),
        source_text=_SOURCE,
        source_label="原文",
        ledger=_ledger(),
    )
    assert 6 <= result.rule_count <= 10
    assert len(result.compiled_rule_ids) == result.rule_count
    assert all("." in rule_id for rule_id in result.compiled_rule_ids)
    assert result.profile == RewriteIntensity.STANDARD
    assert result.genre is None
