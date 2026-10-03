"""资料模块的单元合同（改进工单 25 更新）。

覆盖验收要点：
- 检索主题保持本轮原始专业名词（译名只作扩展）；
- 三类学习目的决定默认数量与媒介条件；用户明确的数量/媒介是硬条件；
- 学习层次确实影响推荐且上下文不足时只追问这一项，并能从等待状态恢复；
- 匹配按证据分层：目录/简介支持主线，标题/时长/点赞只作弱信号；
- 组织按目标分主线/补充，主线未确认时不称完整路径；不足如实报差。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.modules import ModuleWaitState
from bridges.resources.contracts import (
    ResourceEvidenceLevel,
    ResourceRole,
    ResourcesGoalKind,
    ResourcesLevel,
    ResourcesStatus,
)
from bridges.resources.matching import match_resources
from bridges.resources.organizing import organize_resources
from bridges.resources.parsing import parse_resources_request, pending_payload
from bridges.resources.planning import plan_resources
from bridges.resources.reading import WORK_PAGE_SCOPE
from bridges.resources.sources import BookCandidate, VideoCandidate

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _book(
    title: str,
    *,
    source: str = "openlibrary",
    year: int | None = 2016,
    publisher: str | None = "清华大学出版社",
    isbn: str | None = "9787302423287",
    url: str = "https://openlibrary.org/works/OL1W",
    creators: list[str] | None = None,
) -> BookCandidate:
    return BookCandidate(
        title=title,
        creators=creators or ["周志华"],
        year=year,
        publisher=publisher,
        isbn=isbn,
        source=source,
        url=url,
    )


def _video(
    video_id: str,
    title: str,
    *,
    duration: int | None = 1800,
    description: str = "",
    published_at: datetime | None = NOW,
    view_count: int | None = None,
    like_count: int | None = None,
) -> VideoCandidate:
    return VideoCandidate(
        video_id=video_id,
        title=title,
        uploader="某 UP 主",
        duration_seconds=duration,
        published_at=published_at,
        url=f"https://www.bilibili.com/video/{video_id}",
        description=description,
        view_count=view_count,
        like_count=like_count,
    )


def _evidence(
    url: str,
    *,
    catalog: list[str] | None = None,
    description: str = "",
    subjects: list[str] | None = None,
):
    from bridges.resources.contracts import BookReadEvidence

    return BookReadEvidence(
        url=url,
        scope=WORK_PAGE_SCOPE,
        catalog=catalog or [],
        description=description,
        subjects=subjects or [],
    )


def _pipeline(
    text: str,
    books: list[BookCandidate],
    videos: list[VideoCandidate],
    *,
    evidence: dict | None = None,
    prior: list[str] | None = None,
    records: dict | None = None,
):
    analysis = parse_resources_request(text, prior_context=prior or [])
    plan = plan_resources(analysis)
    match = match_resources(analysis, books, videos, book_evidence=evidence or {})
    outcome = organize_resources(
        analysis,
        plan,
        match,
        book_records=(records or {}).get("books", []),
        video_records=(records or {}).get("videos", []),
    )
    return analysis, plan, match, outcome


def _pending(question: str, original: str, goal: str | None = None) -> ModuleWaitState:
    return ModuleWaitState(
        module_id="resources",
        kind="clarification",
        question=question,
        origin_message_id="assistant-1",
        context={"original_phrase": original, "goal": goal, "missing": "level"},
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# resources.parse
# ---------------------------------------------------------------------------


def test_original_english_term_is_kept_verbatim() -> None:
    """英文专业名词逐字保留，不翻译、不替换。"""
    analysis = parse_resources_request("我想学 Transformer")
    assert analysis.original_phrase == "Transformer"
    assert analysis.normalized_term == "Transformer"
    assert analysis.expansions == []
    assert analysis.final_query == "Transformer"


def test_chinese_term_keeps_original_and_adds_english_expansion() -> None:
    """中文术语保留原词，英文写法只作为扩展词进入查询。"""
    analysis = parse_resources_request("推荐几本机器学习的书")
    assert analysis.original_phrase == "机器学习"
    assert analysis.expansions == ["machine learning"]
    assert analysis.final_query == "机器学习 machine learning"


def test_learn_goal_and_level_do_not_leak_into_topic() -> None:
    """「强化 Python 基础，准备课程考试」：主题是 Python，目的是备考。"""
    analysis = parse_resources_request("强化 Python 基础，准备课程考试")
    assert analysis.original_phrase == "Python"
    assert analysis.goal == "备考"
    assert analysis.goal_kind is ResourcesGoalKind.EXAM_PREP
    assert analysis.clarification is None
    assert analysis.level is ResourcesLevel.BASIC
    assert analysis.level_basis is not None and "备考" in analysis.level_basis


def test_conditions_are_parsed_from_one_sentence() -> None:
    """一句话里的目的/媒介/时间/语言/基础与实践项目都被读出。"""
    analysis = parse_resources_request(
        "我是零基础，想系统学习机器学习，只要视频，两周后考试，看英文资料，要练手项目"
    )
    assert analysis.goal_kind is ResourcesGoalKind.EXAM_PREP, "取最靠后的目的"
    assert analysis.media is not None and analysis.media.value == "videos"
    assert analysis.time_budget == "两周"
    assert analysis.language == "英文"
    assert analysis.basis_evidence == "零基础"
    assert analysis.needs_practice_project is True


def test_missing_level_asks_exactly_one_question() -> None:
    """层次未知且影响推荐时只问这一项，并逐项列出三个层次候选。"""
    analysis = parse_resources_request("我想学 Transformer")
    assert analysis.clarification is not None
    assert analysis.clarification.missing == "level"
    assert analysis.clarification.question.count("？") == 1
    assert [candidate.key for candidate in analysis.clarification.candidates] == [
        "beginner",
        "basic",
        "advanced",
    ]
    assert analysis.original_phrase == "Transformer"
    assert analysis.normalized_term == "Transformer"


def test_level_from_prior_context_does_not_ask_again() -> None:
    """前文已经说明过层次时不再追问，并如实标出判定依据。"""
    analysis = parse_resources_request(
        "我想学 Transformer", prior_context=["我是零基础，刚开始学编程"]
    )
    assert analysis.clarification is None
    assert analysis.level is ResourcesLevel.BEGINNER
    assert analysis.level_basis is not None and "前文" in analysis.level_basis


def test_goal_derives_beginner_level_without_asking() -> None:
    """目的是入门了解时推定为零基础，不追问。"""
    analysis = parse_resources_request("我想入门了解一下深度学习")
    assert analysis.clarification is None
    assert analysis.level is ResourcesLevel.BEGINNER


def test_quick_concept_without_level_does_not_ask() -> None:
    """快速概念路线不因缺少层次而追问：低影响缺项按明确假设标注。"""
    analysis = parse_resources_request("我想概览量子计算")
    assert analysis.clarification is None
    assert analysis.goal_kind is ResourcesGoalKind.QUICK_CONCEPT
    assert analysis.level is None
    assert analysis.assumptions, "低影响缺项必须留下明确假设"


def test_deferred_level_does_not_ask_again() -> None:
    """用户把层次交给我们（随便/都行）时按通用顺序继续，不再追问。"""
    analysis = parse_resources_request("我想学 Transformer，层次随便")
    assert analysis.clarification is None
    assert analysis.level is None
    assert analysis.level_basis is not None


def test_missing_topic_asks_for_direction_only() -> None:
    """只有诉求词、没有可检索主题时，问的是方向而不是层次。"""
    analysis = parse_resources_request("帮我找点资料")
    assert analysis.clarification is not None
    assert analysis.clarification.missing == "topic"
    assert analysis.clarification.candidates == []


def test_answer_resumes_from_wait_state() -> None:
    """澄清回答从等待状态恢复：原词与目的沿用，层次取回答里的那一档。"""
    analysis = parse_resources_request(
        "零基础",
        pending=_pending("你现在的学习层次是哪一档？", "Transformer")
    )
    assert analysis.clarification is None
    assert analysis.original_phrase == "Transformer"
    assert analysis.level is ResourcesLevel.BEGINNER
    assert analysis.level_basis is not None and "零基础" in analysis.level_basis


def test_answer_can_add_learning_goal() -> None:
    """回答里补充的学习目的同样采纳：层次与目的都从回答里取到。"""
    analysis = parse_resources_request(
        "有点基础，主要是想应付期末",
        pending=_pending("你现在的学习层次是哪一档？", "Transformer")
    )
    assert analysis.clarification is None
    assert analysis.original_phrase == "Transformer"
    assert analysis.level is ResourcesLevel.BASIC
    assert analysis.goal == "备考"


def test_unusable_answer_keeps_original_and_asks_once_more() -> None:
    """回答既没给出层次、也没把决定权交出来时，保留原词再问一次。"""
    analysis = parse_resources_request(
        "再帮我看看别的",
        pending=_pending("你现在的学习层次是哪一档？", "Transformer")
    )
    assert analysis.clarification is not None
    assert analysis.clarification.missing == "level"
    assert analysis.original_phrase == "Transformer"


def test_pending_payload_is_serializable_and_keeps_original() -> None:
    """等待载荷只存可序列化值，恢复后仍能拿到原词。"""
    analysis = parse_resources_request("我想学 Transformer")
    payload = pending_payload(analysis, missing="level")
    assert payload["original_phrase"] == "Transformer"
    assert payload["missing"] == "level"
    resumed = parse_resources_request(
        "进阶",
        pending=ModuleWaitState(
            module_id="resources",
            kind="clarification",
            question=str(payload["original_phrase"]),
            origin_message_id="assistant-9",
            context=dict(payload),
            created_at=NOW,
        )
    )
    assert resumed.original_phrase == "Transformer"
    assert resumed.level is ResourcesLevel.ADVANCED


# ---------------------------------------------------------------------------
# resources.plan
# ---------------------------------------------------------------------------


def test_plan_targets_follow_goal_kind() -> None:
    """三类目的给不同默认数量；目的未知时保守 2+2，不默认凑 2+3。"""
    quick = plan_resources(parse_resources_request("我想快速了解一下量子计算"))
    assert (quick.target_books, quick.target_videos) == (1, 1)
    exam = plan_resources(
        parse_resources_request("我是零基础，准备期末考试，帮我找机器学习的资料")
    )
    assert exam.goal_kind is ResourcesGoalKind.EXAM_PREP
    assert (exam.target_books, exam.target_videos) == (1, 2)
    systematic = plan_resources(
        parse_resources_request("我有点基础，想系统学习机器学习")
    )
    assert systematic.goal_kind is ResourcesGoalKind.SYSTEMATIC
    assert (systematic.target_books, systematic.target_videos) == (2, 2)
    unknown = plan_resources(parse_resources_request("我想学机器学习，零基础"))
    assert unknown.goal_kind is None
    assert (unknown.target_books, unknown.target_videos) == (2, 2)


def test_plan_keeps_term_in_both_queries() -> None:
    """原词同时出现在图书查询与视频发现查询里；并行与读取上限随计划。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    assert plan.book_query == "机器学习 machine learning"
    assert plan.video_query == "机器学习 零基础 入门 教程"
    assert plan.expansions_used == ["machine learning"]
    assert "机器学习" in plan.rationale
    assert plan.parallel_limit == 2
    assert plan.read_limit_books >= 1 and plan.read_limit_videos >= 1


def test_plan_applies_media_and_explicit_counts() -> None:
    """媒介裁剪另一路；用户明确的数量覆盖目标推导（硬条件）。"""
    videos_only = plan_resources(
        parse_resources_request("我是零基础，只要视频，推荐机器学习资料")
    )
    assert videos_only.media.value == "videos"
    assert (videos_only.target_books, videos_only.target_videos) == (0, 2)
    books_only = plan_resources(
        parse_resources_request("我是零基础，只要书，推荐机器学习资料")
    )
    assert books_only.media.value == "books"
    assert (books_only.target_books, books_only.target_videos) == (2, 0)
    explicit = plan_resources(parse_resources_request("我是零基础，要三本机器学习的书"))
    assert explicit.target_books == 3


def test_plan_without_level_uses_plain_video_suffix() -> None:
    """层次被交给系统时不加层次后缀，视频查询只保留主题与通用后缀。"""
    analysis = parse_resources_request("我想学 Transformer，随便")
    plan = plan_resources(analysis)
    assert plan.book_query == "Transformer"
    assert plan.video_query == "Transformer 教程"


# ---------------------------------------------------------------------------
# resources.match（证据分层）
# ---------------------------------------------------------------------------


def test_match_assigns_evidence_levels_from_actual_reads() -> None:
    """目录 > 简介 > 标题：只有真实读取到的内容才能证明覆盖与先修。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    book_catalog = _book("机器学习", url="https://openlibrary.org/works/OL1W")
    book_bare = _book(
        "机器学习实战",
        url="https://openlibrary.org/works/OL2W",
        isbn=None,
        publisher=None,
    )
    video_intro = _video("BV1", "机器学习入门教程", description="机器学习公开简介")
    video_bare = _video("BV2", "机器学习速览", description="")
    match = match_resources(
        analysis,
        [book_catalog, book_bare],
        [video_intro, video_bare],
        book_evidence={
            book_catalog.url: _evidence(
                book_catalog.url,
                catalog=["第 1 章 基础"],
                subjects=["machine learning"],
            )
        },
    )
    levels = {
        (entry.candidate.title): entry.evidence_level for entry in match.books
    }
    assert levels["机器学习"] is ResourceEvidenceLevel.CATALOG
    assert levels["机器学习实战"] is ResourceEvidenceLevel.TITLE
    video_levels = {
        entry.candidate.title: entry.evidence_level for entry in match.videos
    }
    assert video_levels["机器学习入门教程"] is ResourceEvidenceLevel.INTRO
    assert video_levels["机器学习速览"] is ResourceEvidenceLevel.TITLE
    assert all(entry.read_scope for entry in [*match.books, *match.videos])


def test_match_accepts_coverage_from_read_content() -> None:
    """候选换了说法（标题不含原词）但已读简介覆盖需求时同样通过。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    renamed = _book("统计学习导论", url="https://openlibrary.org/works/OL3W")
    match = match_resources(
        analysis,
        [renamed],
        [],
        book_evidence={
            renamed.url: _evidence(
                renamed.url, description="本书是 machine learning 的入门教材"
            )
        },
    )
    assert len(match.books) == 1
    assert match.books[0].covered is True
    assert "已读内容命中" in match.books[0].match_basis


def test_match_excludes_offtopic_and_dedupes_versions() -> None:
    """主题门 + 重复版本受控：同题同作者保留书目更完整的一条。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    thin = _book(
        "Machine Learning",
        source="openalex",
        publisher=None,
        isbn=None,
        url="https://doi.org/10.1/ml",
    )
    rich = _book("Machine Learning", publisher="MIT Press", isbn="9780262018029")
    outcome = match_resources(
        analysis, [thin, rich, _book("Cooking with Python", source="openalex")], []
    )
    assert [entry.candidate.title for entry in outcome.books] == ["Machine Learning"]
    assert outcome.books[0].candidate.publisher == "MIT Press"
    assert any("重复版本受控" in note for note in outcome.notes)
    assert outcome.book_excluded >= 1


def test_match_stops_on_topic_mismatch_without_filling_quota() -> None:
    """标题与已读内容都没覆盖原词时判定主题不匹配，停止推荐而不是凑数量。"""
    analysis = parse_resources_request("我想学量子纠缠，零基础")
    match = match_resources(
        analysis,
        [_book("Cooking with Python", source="openalex")],
        [_video("BV9", "PyTorch 安装教程")],
    )
    assert match.topic_mismatch is True
    assert match.books == [] and match.videos == []
    assert any("量子纠缠" in note for note in match.notes)


# ---------------------------------------------------------------------------
# resources.organize（主线/补充与数量）
# ---------------------------------------------------------------------------


def test_organize_systematic_goal_builds_main_path_with_evidence() -> None:
    """系统学习：两书一视频进主线（有证据），其余按补充排在目标数内。"""
    analysis, plan, match, outcome = _pipeline(
        "我有点基础，想系统学习机器学习",
        [
            _book("机器学习", url="https://openlibrary.org/works/OL1W"),
            _book("机器学习实战", url="https://openlibrary.org/works/OL2W"),
        ],
        [
            _video("BV1", "机器学习系统讲解", description="公开简介"),
            _video("BV2", "机器学习速览", description="公开简介"),
        ],
        evidence={
            "https://openlibrary.org/works/OL1W": _evidence(
                "https://openlibrary.org/works/OL1W",
                catalog=["第 1 章 基础"],
                subjects=["machine learning"],
            ),
            "https://openlibrary.org/works/OL2W": _evidence(
                "https://openlibrary.org/works/OL2W",
                catalog=["第 1 章 实践"],
                description="machine learning 实践",
            ),
        },
    )
    assert (plan.target_books, plan.target_videos) == (2, 2)
    assert outcome.path_verified is True
    mains = [item for item in outcome.items if item.role is ResourceRole.MAIN]
    assert len(mains) == 3
    assert sum(1 for item in mains if item.kind.value == "book") == 2
    orders = [item.order for item in outcome.items]
    assert orders == list(range(1, len(outcome.items) + 1))
    assert all(item.purpose_zh for item in outcome.items)
    assert all(item.read_scope for item in outcome.items)


def test_organize_quick_goal_keeps_single_main_item() -> None:
    """快速理解概念：只有一条主线，其余按目标数进补充。"""
    analysis, plan, match, outcome = _pipeline(
        "我想快速了解一下量子计算",
        [_book("量子计算入门", url="https://openlibrary.org/works/OL5W")],
        [_video("BV1", "量子计算速览", description="公开简介")],
        evidence={
            "https://openlibrary.org/works/OL5W": _evidence(
                "https://openlibrary.org/works/OL5W", description="量子计算简介"
            )
        },
    )
    assert plan.goal_kind is ResourcesGoalKind.QUICK_CONCEPT
    mains = [item for item in outcome.items if item.role is ResourceRole.MAIN]
    assert len(mains) == 1
    assert mains[0].kind.value == "video", "快速概念优先一条讲解视频"


def test_organize_title_only_candidates_never_enter_main_path() -> None:
    """只有标题/时长等弱信号的条目只作补充，且明确不称完整路径已核实。"""
    _, _, _, outcome = _pipeline(
        "我想学机器学习，零基础",
        [_book("机器学习", url="https://openlibrary.org/works/OL1W")],
        [_video("BV1", "机器学习入门教程")],
    )
    assert outcome.path_verified is False
    assert all(item.role is ResourceRole.SUPPLEMENT for item in outcome.items)
    assert any("不称完整路径已核实" in note for note in outcome.notes)
    assert any(
        item.evidence_level is ResourceEvidenceLevel.TITLE for item in outcome.items
    )


def test_organize_reports_actual_counts_against_goal() -> None:
    """条目不足时按实际数量收敛并说明差多少，绝不虚构补足。"""
    _, plan, _, outcome = _pipeline(
        "我想学机器学习，零基础",
        [_book("机器学习", url="https://openlibrary.org/works/OL1W")],
        [],
        evidence={
            "https://openlibrary.org/works/OL1W": _evidence(
                "https://openlibrary.org/works/OL1W", description="machine learning 简介"
            )
        },
    )
    assert plan.target_books == 2 and plan.target_videos == 2
    assert any("图书还差 1 本" in note for note in outcome.notes)
    assert any("视频还差 2 条" in note for note in outcome.notes)


def test_organize_reports_hard_failure_without_guessing() -> None:
    """两条来源都没有可用答复且至少一处硬失败：整轮如实失败，不凑条目。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    match = match_resources(analysis, [], [])
    outcome = organize_resources(
        analysis,
        plan,
        match,
        book_records=[
            {
                "source": "openlibrary",
                "query": plan.book_query,
                "status": "error",
                "error_code": "openlibrary_offline",
                "error_message": "当前无法连接 Open Library，请检查网络后重试。",
                "retryable": True,
            }
        ],
        video_records=[],
    )
    assert outcome.items == []
    assert outcome.failure is not None
    assert outcome.failure["node"] == "resources.search_books"
    assert outcome.failure["code"] == "openlibrary_offline"
    assert outcome.failure["retryable"] is True


def test_organize_keeps_one_side_when_other_has_gap() -> None:
    """一侧失败只标注自己的缺口：另一侧的有用条目照常给出。"""
    _, plan, _, outcome = _pipeline(
        "我想学机器学习，零基础",
        [],
        [_video("BV1", "机器学习入门教程", description="公开简介")],
        records={
            "books": [
                {
                    "source": "openlibrary",
                    "query": "机器学习 machine learning",
                    "status": "timeout",
                    "error_code": "openlibrary_timeout",
                    "error_message": "Open Library 检索超时。",
                    "retryable": True,
                }
            ]
        },
    )
    assert outcome.failure is None
    assert [item.kind.value for item in outcome.items] == ["video"]
    assert any("图书还差" in note for note in outcome.notes)


# ---------------------------------------------------------------------------
# 条目字段与弱信号
# ---------------------------------------------------------------------------


def test_video_items_only_claim_public_metadata_and_counters() -> None:
    """视频条目只写公开元数据与公开计数；未观看、不下质量结论。"""
    _, _, _, outcome = _pipeline(
        "我想学机器学习，零基础",
        [],
        [
            _video(
                "BV1",
                "机器学习入门教程",
                view_count=1815690,
                like_count=45780,
                description="公开简介",
            )
        ],
    )
    item = outcome.items[0]
    assert item.kind.value == "video"
    assert item.duration_seconds == 1800
    assert item.creator == "某 UP 主"
    assert any("未观看" in note for note in item.unverified)
    assert "播放 约 181.6 万 次" in item.reason_zh
    assert "点赞 约 4.6 万 次" in item.reason_zh
    assert "平台计数，不代表质量结论" in item.reason_zh
    assert "机器学习" in item.match_basis


def test_book_items_record_read_scope_and_purpose() -> None:
    """图书条目记录实际读取范围与角色用途，不足的证据如实标注。"""
    _, _, _, outcome = _pipeline(
        "我有点基础，想系统学习机器学习",
        [_book("机器学习", url="https://openlibrary.org/works/OL1W")],
        [],
        evidence={
            "https://openlibrary.org/works/OL1W": _evidence(
                "https://openlibrary.org/works/OL1W", catalog=["第 1 章 基础"]
            )
        },
    )
    item = outcome.items[0]
    assert item.role is ResourceRole.MAIN
    assert item.evidence_level is ResourceEvidenceLevel.CATALOG
    assert item.read_scope == WORK_PAGE_SCOPE
    assert item.purpose_zh
    assert "未阅读正文" in "；".join(item.unverified)


def test_success_projection_never_leaves_failed_status_behind() -> None:
    """有证据的成功交付：状态可标 success，且主线确认标志为真。"""
    analysis, plan, match, outcome = _pipeline(
        "我有点基础，想系统学习机器学习",
        [_book("机器学习", url="https://openlibrary.org/works/OL1W")],
        [_video("BV1", "机器学习系统讲解", description="公开简介")],
        evidence={
            "https://openlibrary.org/works/OL1W": _evidence(
                "https://openlibrary.org/works/OL1W", description="machine learning 教材"
            )
        },
    )
    assert match.topic_mismatch is False
    assert outcome.items
    assert outcome.path_verified is True
    assert ResourcesStatus.SUCCESS.value == "success"
