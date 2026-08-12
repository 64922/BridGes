"""表达审稿器表驱动测试（人味化改造 Issue 04）。

覆盖 16 类稳定发现 code、按场景解释词语（专业原义/原文已有/精确引语/
protected spans 跳过）、不伤害白名单（单个词语/冒号/破折号不独立造成
硬失败）与自然稿 no_change_recommended。
"""

from __future__ import annotations

from bridges.contracts.expression import Genre
from bridges.contracts.expression_review import ReviewCode, ReviewSeverity
from bridges.skills.humanizer.expression_review import run_expression_review
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


def _contract(
    *,
    genre: Genre | None = None,
    intensity: RewriteIntensity = RewriteIntensity.STANDARD,
    first_person: bool = True,
    operation: Operation = Operation.REWRITE,
) -> ExpressionTaskContract:
    c = ExpressionTaskContract(
        schema_version="expression-task-v1",
        version_hash="hash",
        surface=Surface.ARTICLE,
        operation=operation,
        reality_mode=RealityMode.REAL,
        rewrite_intensity=intensity,
        speaker_position="用户本人（以作者身份）",
        first_person_permission=first_person,
        hypothetical_permission=False,
        genre=genre,
        material_sufficiency=MaterialSufficiency.SUFFICIENT,
        source_scope=SourceScope.ORIGINAL_ONLY,
        evidence_revision_mode=EvidenceRevisionMode.PRESERVE,
    )
    c.version_hash = c.compute_version_hash()
    return c


def _ledger_with(proper_nouns: list[str] | None = None, quotes: list[str] | None = None) -> SourceLedger:
    return SourceLedger(
        ledger_version="2",
        ledger_hash="h",
        entries=[
            SourceEntry(
                entry_id="src-1",
                source_type=SourceType.USER_ORIGINAL,
                content_hash="ch",
                usage=[SourceUsage.REWRITE],
                proper_nouns=proper_nouns or [],
                quotes=quotes or [],
            )
        ],
    )


def _codes(report) -> set[str]:
    return {f.code.value for f in report.findings}


def _find(report, code: ReviewCode):
    return [f for f in report.findings if f.code == code]


# ---------------------------------------------------------------------------
# 16 类发现表驱动
# ---------------------------------------------------------------------------

_CASES: list[tuple[ReviewCode, str]] = [
    (
        ReviewCode.ASSISTANT_IDENTITY_RESIDUE,
        "本助手认为，时间管理的关键是先建立清单。",
    ),
    (
        ReviewCode.MECHANICAL_TRANSITION_CLOSING,
        "时间管理的关键是先建立清单。总而言之，希望这些建议对你有帮助。",
    ),
    (
        ReviewCode.GENRE_SCAFFOLDING,
        "首先，我们介绍什么是番茄工作法。其次，讲解休息节奏。再次，练习停顿。最后总结。",
    ),
    (
        ReviewCode.ABSTRACT_NOUN_CHAIN,
        "任务的整体颗粒度需要提升，带宽也要优化，认知负荷应降低。",
    ),
    (
        ReviewCode.BUSINESS_PPT_METAPHOR,
        "这套方法能够全面赋能用户，形成业务闭环，打造核心竞争力。",
    ),
    (
        ReviewCode.TERM_EXPLAINS_TERM,
        "所谓元认知，就是认知的认知，本质上是思维层面的思维。",
    ),
    (
        ReviewCode.SYNONYM_LOOP,
        "换言之，要提升效率。也就是说，要优化产出。换句话说，要增强效能。",
    ),
    (
        ReviewCode.EMPTY_PARAGRAPH,
        "第一段讲建立清单的方法。\n然而。\n第三段给出具体步骤。",
    ),
    (
        ReviewCode.REPEATED_OPENING,
        "首先，列清单。\n首先，排优先级。\n首先，设截止时间。",
    ),
    (
        ReviewCode.UNIFORM_LENGTH,
        "列清单要固定时间。\n排优先级要看价值。\n设截止要留出余量。",
    ),
    (
        ReviewCode.DENSE_RHETORIC,
        "为什么我们要管理时间？因为时间有限。怎么管理？先列清单。用什么工具？纸笔即可。",
    ),
    (
        ReviewCode.FAKE_CONCRETENESS,
        "很多朋友问过我，怎么才能坚持早起。",
    ),
    (
        ReviewCode.UNAUTHORIZED_FIRST_PERSON,
        "我上个月用番茄钟坚持了三十天，效果很好。",
    ),
    (
        ReviewCode.FORCED_LIFE_SCENE,
        "深夜的咖啡还冒着热气，窗外的雨淅淅沥沥，这时候最适合整理任务。",
    ),
    (
        ReviewCode.PER_PARAGRAPH_MOTIVATION,
        "清单让工作有序。\n秩序本身就是效率。\n坚持就是胜利。",
    ),
    (
        ReviewCode.GENERIC_OPTIMISTIC_ENDING,
        "掌握方法之后，未来可期，让我们共同期待更美好的明天。",
    ),
]


def test_all_review_codes_are_detected() -> None:
    for code, text in _CASES:
        report = run_expression_review(
            text,
            contract=_contract(
                operation=Operation.GENERATE_BY_TOPIC,
                first_person=code != ReviewCode.UNAUTHORIZED_FIRST_PERSON,
            ),
        )
        assert code in _codes(report), f"{code.value} 未检出：{report.findings}"
        finding = _find(report, code)[0]
        # 位置稳定：起止在文本范围内且证据非空
        assert 0 <= finding.location.start < finding.location.end <= len(text)
        assert finding.evidence.strip()
        assert finding.explanation.strip()
        assert finding.suggestion.strip()
        assert finding.scene_profile == "generic:standard"


def test_findings_are_soft() -> None:
    report = run_expression_review(
        "本助手认为，很多朋友需要先列清单。总而言之，希望有帮助。",
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert report.findings
    for finding in report.findings:
        assert finding.severity in {
            ReviewSeverity.INFO,
            ReviewSeverity.SUGGESTION,
            ReviewSeverity.WARNING,
        }


# ---------------------------------------------------------------------------
# 按场景解释词语与白名单
# ---------------------------------------------------------------------------

def test_professional_term_in_ledger_is_skipped() -> None:
    text = "认知负荷是工作记忆的有限容量。"
    report = run_expression_review(
        text,
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
        ledger=_ledger_with(proper_nouns=["认知负荷"]),
    )
    assert ReviewCode.ABSTRACT_NOUN_CHAIN not in _codes(report)


def test_expression_already_in_source_is_skipped() -> None:
    text = "颗粒度决定计划的详细程度。"
    report = run_expression_review(
        text,
        contract=_contract(operation=Operation.REWRITE),
        source_text="原文提到：颗粒度决定计划的详细程度。",
    )
    assert ReviewCode.ABSTRACT_NOUN_CHAIN not in _codes(report)


def test_exact_quote_region_is_skipped() -> None:
    text = "文中写道：「释放大脑内存，才能提升带宽」。这句话值得记住。"
    report = run_expression_review(
        text,
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    # 引号内的抽象包装不得独立触发发现
    assert ReviewCode.ABSTRACT_NOUN_CHAIN not in _codes(report)


def test_user_phrase_constraint_is_skipped() -> None:
    contract = _contract(operation=Operation.GENERATE_BY_TOPIC)
    contract = contract.model_copy(
        update={"user_constraints": ["必须保留「颗粒度」这个说法"]}
    )
    text = "颗粒度是计划详细程度的关键。"
    report = run_expression_review(text, contract=contract)
    assert ReviewCode.ABSTRACT_NOUN_CHAIN not in _codes(report)


def test_single_colon_or_dash_never_causes_finding() -> None:
    report = run_expression_review(
        "时间管理的关键：先列清单——再排优先级。",
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert ReviewCode.DENSE_RHETORIC not in _codes(report)


def test_rhetoric_density_requires_repeated_markers() -> None:
    report = run_expression_review(
        "第一步：列清单。第二步：排优先级。第三步：设截止时间。",
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    # 三个冒号但句式承接信息，不构成装饰性堆叠
    assert ReviewCode.DENSE_RHETORIC not in _codes(report)


def test_first_person_judgment_allowed_when_permission_off() -> None:
    report = run_expression_review(
        "我认为先列清单比直接动手更可靠。",
        contract=_contract(
            operation=Operation.GENERATE_BY_TOPIC,
            first_person=False,
        ),
    )
    assert ReviewCode.UNAUTHORIZED_FIRST_PERSON not in _codes(report)


def test_first_person_experience_blocked_without_permission() -> None:
    report = run_expression_review(
        "我去年靠列清单坚持了三个月。",
        contract=_contract(
            operation=Operation.GENERATE_BY_TOPIC,
            first_person=False,
        ),
    )
    assert ReviewCode.UNAUTHORIZED_FIRST_PERSON in _codes(report)


def test_first_person_experience_present_in_source_is_skipped() -> None:
    text = "我去年靠列清单坚持了三个月，这个方法确实有效。"
    report = run_expression_review(
        text,
        contract=_contract(operation=Operation.REWRITE, first_person=False),
        source_text="我去年靠列清单坚持了三个月。",
    )
    assert ReviewCode.UNAUTHORIZED_FIRST_PERSON not in _codes(report)


# ---------------------------------------------------------------------------
# 场景差异：科研/讲稿不误报常规表达
# ---------------------------------------------------------------------------

def test_research_report_allows_first_next_scaffolding() -> None:
    text = "首先，我们回顾方法；其次，展示结果；最后，讨论局限。综上所述，结果支持初步结论。"
    report = run_expression_review(
        text,
        contract=_contract(
            genre=Genre.RESEARCH_REPORT,
            operation=Operation.GENERATE_BY_TOPIC,
        ),
    )
    assert ReviewCode.GENRE_SCAFFOLDING not in _codes(report)
    assert ReviewCode.MECHANICAL_TRANSITION_CLOSING not in _codes(report)


def test_lecture_script_allows_question_pauses() -> None:
    text = "检查一下：你能说出原料吗？试着回答。再来：产物是什么？想一想。最后：方法记住了吗？"
    report = run_expression_review(
        text,
        contract=_contract(
            genre=Genre.LECTURE_SCRIPT,
            operation=Operation.GENERATE_BY_TOPIC,
        ),
    )
    assert ReviewCode.DENSE_RHETORIC not in _codes(report)
    # 同一文本在通用场景（非讲稿）下应被识别为密集设问
    generic = run_expression_review(
        text,
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert ReviewCode.DENSE_RHETORIC in _codes(generic)


# ---------------------------------------------------------------------------
# 自然稿不伤害
# ---------------------------------------------------------------------------

def test_natural_text_reports_no_change() -> None:
    text = (
        "把大任务拆成小步骤，是缓解拖延的可靠办法。拆分时先看每步能否在"
        "十五分钟内完成，再把先后顺序写下来。执行中如果被打断，就从清单上"
        "的下一步继续，不用从头开始。记录实际用时，一周后就能看出哪些环节"
        "经常超时，可以据此调整计划。"
    )
    report = run_expression_review(
        text,
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert report.no_change_recommended is True
    assert not report.findings


# ---------------------------------------------------------------------------
# Issue 04 测试计划回归：失败型时间管理文章（不得新增亲历/抽象包装）
# ---------------------------------------------------------------------------

_FAILED_TM_SOURCE = (
    "我总是拖延，试过各种时间管理方法都失败了。任务堆积如山，越积越多。"
    "我晚上总是失眠，白天更没精神。"
)


def test_failed_tm_article_regression_abstract_wrap_and_fake_experience() -> None:
    """用户失败型时间管理文章：不得新增亲历、不得保留抽象包装。

    候选把抽象包装换一套说法继续保留（颗粒度/带宽/释放内存/元认知），
    并新增无来源亲历（我上个月……），审稿器应同时捕获。
    """
    candidate = (
        "很多人都有类似的困扰。提升任务颗粒度、释放大脑内存、优化带宽，"
        "元认知是解决拖延的关键。我上个月用了新方法坚持了三十天。"
    )
    report = run_expression_review(
        candidate,
        contract=_contract(operation=Operation.REWRITE, first_person=False),
        source_text=_FAILED_TM_SOURCE,
    )
    codes = _codes(report)
    assert ReviewCode.ABSTRACT_NOUN_CHAIN in codes
    assert ReviewCode.FAKE_CONCRETENESS in codes
    assert ReviewCode.UNAUTHORIZED_FIRST_PERSON in codes
    # 至少一个抽象主张能落到源文已有动作的定向建议
    abstract_findings = _find(report, ReviewCode.ABSTRACT_NOUN_CHAIN)
    assert abstract_findings
    assert all(f.suggestion.strip() for f in abstract_findings)
    assert "落" in abstract_findings[0].suggestion


def test_natural_original_with_blacklist_terms_is_not_harmed() -> None:
    """原文已自然的白名单：原文里的词与用户约束不因审稿被误报。"""
    source = (
        "时间管理的颗粒度取决于任务本身的粒度：把 25 分钟当做一个工作块，"
        "每完成一块就释放一次大脑内存。"
    )
    report = run_expression_review(
        source,
        contract=_contract(operation=Operation.REWRITE),
        source_text=source,
    )
    assert report.no_change_recommended is True
    assert not report.findings


def test_legitimate_three_part_list_and_colon_definition_not_reported() -> None:
    """必要三项清单与含冒号定义不构成误报（不全局禁止标点与列举）。"""
    candidate = (
        "工作块有三个作用：一是固定开始时间，二是限制单块时长，三是"
        "强制安排休息。休息 15 分钟后继续下一块。"
    )
    report = run_expression_review(
        candidate,
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert ReviewCode.DENSE_RHETORIC not in _codes(report)
    assert ReviewCode.GENRE_SCAFFOLDING not in _codes(report)


# ---------------------------------------------------------------------------
# Issue 04 测试计划回归：四体裁不再强加元素
# ---------------------------------------------------------------------------

def test_four_genres_do_not_require_scaffolding_elements() -> None:
    """科普/讲稿/科研/论文正文无需类比、练习、方法、局限、下一步即自然。"""
    cases = [
        (Genre.POPULAR_SCIENCE, "光合作用把光能转化为化学能，速率约 25 单位。"),
        (
            Genre.LECTURE_SCRIPT,
            "光合作用分两步：先是光反应，再是暗反应。我们看第一步。",
        ),
        (
            Genre.RESEARCH_REPORT,
            "我们采集了三个品种，数据显示 35°C 下速率下降。",
        ),
        (
            Genre.PAPER_ASSIST,
            "引言应先综述现状，再提出缺口。请检查引用完整性。",
        ),
    ]
    for genre, text in cases:
        report = run_expression_review(
            text,
            contract=_contract(genre=genre, operation=Operation.GENERATE_BY_TOPIC),
        )
        assert ReviewCode.GENRE_SCAFFOLDING not in _codes(report), genre.value
        # 缺少类比/练习/局限/下一步不构成任何发现
        assert report.no_change_recommended is True, genre.value


def test_review_report_is_versioned_and_summarized() -> None:
    report = run_expression_review(
        "本助手认为，总而言之未来可期。",
        contract=_contract(operation=Operation.GENERATE_BY_TOPIC),
    )
    assert report.review_version == "expression-review-v1"
    assert report.contract_hash
    assert report.summary.finding_count == len(report.findings)
    assert report.summary.by_code
    assert not report.no_change_recommended


def test_finding_ids_are_deterministic() -> None:
    """同一输入两次审稿产生相同 finding ID（审计与 Issue 05 跨运行锚定）。"""
    text = "本助手认为，总而言之未来可期。"
    first = run_expression_review(
        text, contract=_contract(operation=Operation.GENERATE_BY_TOPIC)
    )
    second = run_expression_review(
        text, contract=_contract(operation=Operation.GENERATE_BY_TOPIC)
    )
    assert [f.finding_id for f in first.findings] == [
        f.finding_id for f in second.findings
    ]
    assert all(f.finding_id.startswith(f.code.value) for f in first.findings)
