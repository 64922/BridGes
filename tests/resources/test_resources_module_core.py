"""Issue 13 资料模块：解析、计划与排序的单元合同。

覆盖验收要点：
- 检索主题保持本轮原始专业名词（译名只作扩展）；
- 学习层次确实影响推荐且上下文不足时只追问这一项，并能从等待状态恢复；
- 默认尝试两本书 + 三条哔哩哔哩视频，逐项核对可取得的元数据；
- 展示由浅入深的顺序与选择理由；条目不足时说明实际数量，不凑数；
- 未看过的视频只写公开元数据，不描述不可验证的具体内容。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.modules import ModuleWaitState
from bridges.resources.contracts import ResourcesLevel
from bridges.resources.parsing import parse_resources_request, pending_payload
from bridges.resources.planning import plan_resources
from bridges.resources.ranking import cover_original_phrase, rank_resources
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
) -> BookCandidate:
    return BookCandidate(
        title=title,
        creators=["周志华"],
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
    assert analysis.clarification is None
    assert analysis.level is ResourcesLevel.BASIC
    assert analysis.level_basis is not None and "备考" in analysis.level_basis


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
    # 澄清时仍保留原词与最终查询词（用户能看到会用什么词去检索）。
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


def test_plan_keeps_term_in_both_queries_and_targets_two_plus_three() -> None:
    """计划默认两本书 + 三条视频；原词同时出现在图书查询与视频发现查询里。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    assert plan.target_books == 2
    assert plan.target_videos == 3
    assert plan.book_query == "机器学习 machine learning"
    assert plan.video_query.startswith("机器学习")
    assert "零基础" in plan.video_query
    assert plan.expansions_used == ["machine learning"]
    assert "机器学习" in plan.rationale


def test_plan_without_level_uses_plain_video_suffix() -> None:
    """层次被交给系统时不加层次后缀，视频查询只保留主题与通用后缀。"""
    analysis = parse_resources_request("我想学 Transformer，随便")
    plan = plan_resources(analysis)
    assert plan.book_query == "Transformer"
    assert plan.video_query == "Transformer 教程"


# ---------------------------------------------------------------------------
# resources.rank
# ---------------------------------------------------------------------------


def test_rank_orders_shallow_to_deep_with_reasons() -> None:
    """清单按由浅入深排列，每条都带适用阶段、理由与核对依据。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis,
        plan,
        [
            _book("机器学习", publisher="清华大学出版社", isbn="9787302423287"),
            _book(
                "Advanced Machine Learning Handbook",
                source="openalex",
                publisher="Springer",
                isbn="9781111111111",
                url="https://doi.org/10.1/adv",
            ),
        ],
        [
            _video("BV1", "机器学习零基础入门教程", duration=1500),
            _video("BV2", "机器学习进阶：深入原理与实战", duration=5400),
            _video("BV3", "机器学习速览", duration=300, description="机器学习简介"),
        ],
    )
    assert outcome.topic_mismatch is False
    assert [item.order for item in outcome.items] == [1, 2, 3, 4, 5]
    kinds = [item.kind for item in outcome.items]
    assert kinds.count("book") == 2
    assert kinds.count("video") == 3
    stages = [item.stage for item in outcome.items]
    assert stages == sorted(stages, key=["入门", "打基础", "进阶"].index)
    for item in outcome.items:
        assert item.reason_zh.strip()
        assert item.match_basis.strip()
        assert item.url.startswith("https://")
    assert any("零基础" in note for note in outcome.notes)


def test_rank_excludes_offtopic_books_and_counts_them() -> None:
    """主题门：标题不覆盖原词或扩展词的书目被排除，并在证据边界里报数。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis,
        plan,
        [
            _book("机器学习"),
            _book("Cooking with Python", source="openalex", publisher=None, isbn=None),
        ],
        [],
    )
    assert [item.title for item in outcome.items] == ["机器学习"]
    assert any("1 条书目" in note for note in outcome.notes)


def test_rank_reports_actual_counts_when_sources_fall_short() -> None:
    """条目不足时按实际数量收敛并说明，绝不虚构补足 2+3。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(analysis, plan, [_book("机器学习")], [])
    assert len(outcome.items) == 1
    assert any("实际取得 1 本书、0 条视频" in note for note in outcome.notes)
    assert any("我没有虚构补足" in note for note in outcome.notes)


def test_rank_stops_on_topic_mismatch_without_filling_quota() -> None:
    """标题都没覆盖原词时判定主题不匹配，停止推荐而不是凑数量。"""
    analysis = parse_resources_request("我想学量子纠缠，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis,
        plan,
        [_book("Cooking with Python", source="openalex", publisher=None, isbn=None)],
        [_video("BV9", "PyTorch 安装教程")],
    )
    assert outcome.topic_mismatch is True
    assert outcome.items == []
    assert any("量子纠缠" in note for note in outcome.notes)


def test_rank_dedupes_same_book_keeping_richer_bibliography() -> None:
    """同一本书出现在两个来源时只保留书目信息更完整的一条。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis,
        plan,
        [
            _book(
                "Machine Learning",
                source="openalex",
                publisher=None,
                isbn=None,
                url="https://doi.org/10.1/ml",
            ),
            _book(
                "Machine Learning",
                source="openlibrary",
                publisher="MIT Press",
                isbn="9780262018029",
            ),
        ],
        [],
    )
    assert len(outcome.items) == 1
    assert outcome.items[0].source == "openlibrary"
    assert outcome.items[0].isbn == "9780262029" or outcome.items[0].isbn == "9780262018029"
    assert any("重复" in note for note in outcome.notes)


def test_rank_level_alignment_shifts_which_books_are_chosen() -> None:
    """层次对齐：候选多于目标数时，零基础与进阶用户选到的书不同。"""
    beginner = parse_resources_request("我想学机器学习，零基础")
    advanced = parse_resources_request("我想学机器学习，进阶")
    books = [
        _book("机器学习入门与基础", isbn="9787302423287"),
        _book("机器学习进阶原理", isbn="9787111111111", url="https://openlibrary.org/works/OL2W"),
        _book(
            "Advanced Machine Learning Handbook",
            source="openalex",
            publisher="Springer",
            isbn="9781111111111",
            url="https://doi.org/10.1/adv",
        ),
    ]
    beginner_titles = [
        item.title
        for item in rank_resources(beginner, plan_resources(beginner), books, []).items
    ]
    advanced_titles = [
        item.title
        for item in rank_resources(advanced, plan_resources(advanced), books, []).items
    ]
    assert beginner_titles == ["机器学习入门与基础", "机器学习进阶原理"]
    assert sorted(advanced_titles) == sorted(
        ["机器学习进阶原理", "Advanced Machine Learning Handbook"]
    )
    assert "Advanced Machine Learning Handbook" not in beginner_titles
    assert "机器学习入门与基础" not in advanced_titles


def test_rank_notes_stage_level_conflict_when_sources_fall_short() -> None:
    """候选不足时仍给出条目，但如实说明有一条与层次不完全匹配。"""
    beginner = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(beginner)
    outcome = rank_resources(
        beginner,
        plan,
        [
            _book(
                "Advanced Machine Learning Handbook",
                source="openalex",
                publisher="Springer",
                isbn="9781111111111",
                url="https://doi.org/10.1/adv",
            )
        ],
        [],
    )
    assert [item.stage for item in outcome.items] == ["进阶"]
    assert any("不完全匹配" in note for note in outcome.notes)


def test_video_items_only_claim_public_metadata() -> None:
    """视频条目带未观看的未核实项，不描述讲授质量；元数据来自公开接口。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis, plan, [], [_video("BV1", "机器学习入门教程", duration=1500)]
    )
    item = outcome.items[0]
    assert item.kind == "video"
    assert item.duration_seconds == 1500
    assert item.creator == "某 UP 主"
    assert any("未观看" in note for note in item.unverified)
    assert "机器学习" in item.match_basis


def test_video_public_counters_are_reported_as_weak_evidence() -> None:
    """公开计数（播放/点赞）如实呈现并标注平台计数，不当成质量结论。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(
        analysis,
        plan,
        [],
        [_video("BV1", "机器学习入门教程", view_count=1815690, like_count=45780)],
    )
    item = outcome.items[0]
    assert item.view_count == 1815690
    assert item.like_count == 45780
    assert "播放 约 181.6 万 次" in item.reason_zh
    assert "点赞 约 4.6 万 次" in item.reason_zh
    assert "平台计数，不代表质量结论" in item.reason_zh


def test_popular_video_breaks_ties_within_the_same_stage() -> None:
    """口碑证据只是弱信号：条数上限内同阶段取舍时，有公开反馈的排前面。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    popular = _video("BV1", "机器学习入门教程（上）", like_count=500)
    quiet = _video("BV2", "机器学习入门教程（下）", like_count=5)
    outcome = rank_resources(analysis, plan, [], [quiet, popular])
    titles = [item.title for item in outcome.items]
    assert titles[0] == popular.title
    # 未给计数时不参与该弱信号（接口没给就没有这段理由）。
    bare = rank_resources(analysis, plan, [], [_video("BV3", "机器学习入门教程（补）")])
    assert "平台计数" not in bare.items[0].reason_zh


def test_cover_original_phrase_guards_the_final_projection() -> None:
    """最后一道门：清单必须至少覆盖原词本身。"""
    analysis = parse_resources_request("我想学机器学习，零基础")
    plan = plan_resources(analysis)
    outcome = rank_resources(analysis, plan, [_book("机器学习")], [])
    assert cover_original_phrase(analysis, outcome.items) is True
    assert cover_original_phrase(analysis, []) is False
