"""Issue 16 单元合同：原词抽取、查询词计划、功能匹配、覆盖判定与正文渲染。

这些用例不发起任何外部调用：只验证确定性判断（原词逐字保留、身份相关度闸门、
证据分级匹配、覆盖面与局限的说法）以及真实检索里观察到的形态。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.github.contracts import (
    GithubCoverage,
    GithubEvidenceKind,
    GithubFileRead,
    GithubIdeaAnalysis,
    GithubImplementationCheck,
    GithubLicenseCheck,
    GithubProjectsProjection,
    GithubProjectStatus,
    GithubRateLimitState,
    GithubReadmeStatus,
    GithubRepositoryEvidence,
)
from bridges.github.lexicon import (
    compact_scenario,
    has_own_subject,
    strip_intent_words,
)
from bridges.github.parsing import parse_github_request, pending_payload
from bridges.github.presenting import (
    render_empty_content,
    render_result_content,
    render_stopped_content,
)
from bridges.github.ranking import (
    identity_relevance,
    match_features,
    rank_candidates,
)
from bridges.github.searching import MAX_DESCRIPTION_CHARS, MAX_QUERIES, plan_queries
from bridges.github.suggestion import detect_github_suggestion

WHOLE_IDEA = "我想做一个校园二手书交换平台，学生可以发布想卖的书，搜索想要的书，线下交换"


def _evidence(
    *,
    full_name: str = "demo/bookswap",
    description: str | None = "校园二手书交换平台",
    topics: list[str] | None = None,
    readme_text: str | None = "学生可以发布想卖的书，也可以搜索想要的书，然后线下交换。",
    readme_status: GithubReadmeStatus = GithubReadmeStatus.READ,
    files_read: list[GithubFileRead] | None = None,
    checks: list[GithubImplementationCheck] | None = None,
    license: GithubLicenseCheck | None = None,
    stars: int = 10,
    pushed_at: datetime | None = None,
    archived: bool = False,
) -> GithubRepositoryEvidence:
    return GithubRepositoryEvidence(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description=description,
        topics=topics or [],
        readme_text=readme_text,
        readme_status=readme_status,
        files_read=files_read or [],
        implementation_checks=checks or [],
        license=license or GithubLicenseCheck(detected=False, note="上游元数据没有标注许可。"),
        stars=stars,
        pushed_at=pushed_at or datetime(2026, 9, 1, tzinfo=UTC),
        matched_query="校园二手书交换",
        retrieved_at=datetime(2026, 9, 26, tzinfo=UTC),
    )


def test_idea_words_are_kept_verbatim_and_scenario_is_compact_for_search() -> None:
    """原词逐字保留：场景与要点都是用户写的词，检索词才做末尾品类剥离。"""
    analysis = parse_github_request(WHOLE_IDEA)

    assert analysis.scenario == "校园二手书交换平台"
    assert analysis.features == ["学生可以发布想卖的书", "搜索想要的书", "线下交换"]
    assert analysis.whole_idea is True
    assert compact_scenario(analysis.scenario) == "校园二手书交换"


def test_query_plan_puts_the_whole_query_first_and_stays_bounded() -> None:
    """第一条查整体（剥品类词），其余查要点，总数有界且不重复发原句。"""
    analysis = parse_github_request(WHOLE_IDEA)
    queries = plan_queries(analysis)

    assert queries[0] == "校园二手书交换"
    assert queries == ("校园二手书交换", "学生可以发布想卖的书", "搜索想要的书")
    assert len(queries) <= MAX_QUERIES

    single = parse_github_request("做个交换平台")
    # 要点就是场景本身时不重复发一遍：整句 idea 只发一条整体查询。
    assert plan_queries(single) == ("交换平台",)


def test_component_request_is_recognized_and_not_identity_gated() -> None:
    """明确只要组件时按组件处理，检索词不剥末尾词。"""
    analysis = parse_github_request("找个登录认证组件")

    assert analysis.whole_idea is False
    assert analysis.scenario == "登录认证组件"
    assert plan_queries(analysis) == ("登录认证组件",)


def test_compound_nouns_survive_and_only_edge_particles_are_stripped() -> None:
    """复合名词（深度学习／联邦学习）不能被当成请求壳剥掉，助词碎片才按空处理。"""
    assert strip_intent_words("深度学习") == "深度学习"
    assert strip_intent_words("联邦学习") == "联邦学习"
    assert strip_intent_words("机器学习笔记应用") == "机器学习笔记应用"
    assert strip_intent_words("帮我找个能记账的") == "能记账"
    assert strip_intent_words("推荐几个类似的项目") == ""


def test_vague_request_asks_instead_of_searching_filler_words() -> None:
    """只有填充词的请求先澄清，不拿「几个类似的」去检索。"""
    analysis = parse_github_request("推荐几个类似的项目")

    assert analysis.clarification is not None
    assert analysis.scenario == ""


def test_module_only_request_asks_one_clarification() -> None:
    """整句只有模块用语时问一个问题，并给出可持久化的恢复载荷。"""
    analysis = parse_github_request("有没有人做过类似的项目")

    assert analysis.clarification is not None
    assert "公开仓库" in analysis.clarification.question
    payload = pending_payload(analysis)
    assert payload["original_request"] == "有没有人做过类似的项目"


def test_reference_to_prior_turn_takes_the_anchor_words_verbatim() -> None:
    """「找实现它的项目」指向前文：原词取自前文投影，并带回消息标识。"""
    from bridges.github.contracts import GithubContextSource

    anchor = GithubContextSource(
        kind="paper_search",
        label="上一轮论文搜索",
        phrase="联邦学习",
        message_id="m-9",
    )
    analysis = parse_github_request("帮我找实现它的项目", prior_context=[anchor])

    assert analysis.clarification is None
    assert analysis.scenario == "联邦学习"
    assert analysis.features == ["联邦学习"]
    assert analysis.context_source == anchor
    # 指代词本身不算自己的主题，否则会把「它」当成检索词。
    assert has_own_subject("帮我找实现它的项目") is False


def test_reference_without_anchor_asks_instead_of_searching_a_pronoun() -> None:
    """前文没有可追溯的原词时先问一句，绝不拿指代词去检索。"""
    analysis = parse_github_request("帮我找实现它的项目")

    assert analysis.clarification is not None
    assert analysis.context_source is None
    assert analysis.scenario == ""


def test_feature_matching_prefers_implementation_over_readme_over_metadata() -> None:
    """匹配只在真实取得的证据文本里找关键词，并按证据等级报告依据。"""
    analysis = parse_github_request(WHOLE_IDEA)
    evidence = _evidence(
        readme_text="本仓库是一个二手书交换平台。",
        files_read=[
            GithubFileRead(
                path="src/books/publish.py",
                kind="file",
                excerpt="def publish_book(request):  # 发布想卖的书",
            )
        ],
    )

    matches = {item.feature: item for item in match_features(analysis, evidence)}

    assert matches["学生可以发布想卖的书"].matched is True
    assert matches["学生可以发布想卖的书"].evidence_kind is GithubEvidenceKind.IMPLEMENTATION
    assert "发布" in matches["学生可以发布想卖的书"].matched_terms
    assert matches["线下交换"].matched is False
    assert "没有出现" in matches["线下交换"].evidence


def test_whole_idea_rejects_repositories_without_identity_overlap() -> None:
    """整体项目的候选必须在名称／简介／话题里有词汇重合：正文偶然命中不算。"""
    analysis = parse_github_request(WHOLE_IDEA)
    junk = _evidence(
        full_name="someone/china-dictatorship",
        description="反中共政治宣传库。Anti Chinese government propaganda.",
        topics=["politics"],
        readme_text="校园 二手书 交换 发布 搜索 线下交换 全部出现在这段堆砌长文里。",
    )
    relevant = _evidence(full_name="253936563/huanshu", description="校园二手书交换")

    assert identity_relevance(
        analysis,
        full_name=junk.full_name,
        description=junk.description,
        topics=junk.topics,
    ) == 0

    outcome = rank_candidates(analysis, [junk, relevant])

    assert [item.full_name for item in outcome.recommendations] == ["253936563/huanshu"]
    rejected = {item.full_name: item.reason for item in outcome.rejected}
    assert (
        "名称、简介与话题里都没有出现你整体想法的关键词"
        in rejected["someone/china-dictatorship"]
    )


def test_identity_gate_does_not_fire_on_feature_words_in_unrelated_text() -> None:
    """身份闸门只看核心场景：要点里的辅助词撞上无关正文不算整体相似。

    实测形态：整句 idea 的「搜索想要的书」让新标签页插件的简介
    「更快找到你想要的答案」也算身份命中，于是书签插件被当成整体项目推荐。
    """
    analysis = parse_github_request(WHOLE_IDEA)
    tabmark = _evidence(
        full_name="Alanrk/TabMark-Bookmark-New-Tab",
        description=(
            "TabMark是一款基于书签的新标签页插件，让你收藏的书签一目了然。"
            "搭配智能 AI 搜索，更快找到你想要的答案。"
        ),
        topics=["bookmark-manager", "chrome-extension"],
    )
    similar = _evidence(
        full_name="666bears/usedbook",
        description="基于Java+SSM+Jsp的二手交易平台：校园旧书交易交换平台，含书籍信息管理。",
    )

    assert identity_relevance(
        analysis,
        full_name=tabmark.full_name,
        description=tabmark.description,
        topics=tabmark.topics,
    ) == 0

    outcome = rank_candidates(analysis, [tabmark, similar])

    assert [item.full_name for item in outcome.recommendations] == ["666bears/usedbook"]
    rejected = {item.full_name: item.reason for item in outcome.rejected}
    assert (
        "名称、简介与话题里都没有出现你整体想法的关键词"
        in rejected["Alanrk/TabMark-Bookmark-New-Tab"]
    )


def test_overlapping_fragments_are_merged_back_into_whole_words() -> None:
    """命中片段重叠时并成完整词：「联邦学」+「邦学习」展示为「联邦学习」。"""
    from bridges.github.contracts import GithubContextSource

    anchor = GithubContextSource(
        kind="paper_search",
        label="上一轮论文搜索",
        phrase="联邦学习",
        message_id="m-9",
    )
    analysis = parse_github_request("帮我找实现它的项目", prior_context=[anchor])
    evidence = _evidence(
        full_name="lokinko/Federated-Learning",
        description="联邦学习",
        readme_text="# 联邦学习 Federated Learning",
    )

    matches = match_features(analysis, evidence)

    assert matches[0].matched is True
    assert matches[0].matched_terms == ["联邦学习"]
    assert "命中关键词「联邦学习」" in matches[0].evidence


def test_rejection_reason_says_when_nothing_was_readable() -> None:
    """读不到文件与「读了但没命中」理由不同：空仓库如实说明没有可核对证据。"""
    analysis = parse_github_request(WHOLE_IDEA)
    empty = _evidence(
        full_name="253936563/huanshu",
        description="校园二手书交换",
        readme_text=None,
        readme_status=GithubReadmeStatus.NOT_FOUND,
    )
    silent = _evidence(full_name="demo/bookswap", readme_text="本仓库只放了活动策划文档。")

    outcome = rank_candidates(analysis, [empty, silent])
    rejected = {item.full_name: item.reason for item in outcome.rejected}

    assert "没有读到 README，也没有读到实现文件" in rejected["253936563/huanshu"]
    assert "没有出现你 idea 的要点" in rejected["demo/bookswap"]


def test_component_idea_is_not_identity_gated() -> None:
    """组件项目的仓库不一定在简介里点名组件，因此不设身份闸门。"""
    analysis = parse_github_request("找个登录认证组件")
    evidence = _evidence(
        full_name="opendevops-cn/codo-admin",
        description="CODO 运维管理平台",
        topics=["devops"],
        readme_text="登录认证基于 JWT，支持单点登录与双因素认证。",
    )

    outcome = rank_candidates(analysis, [evidence])

    assert [item.full_name for item in outcome.recommendations] == ["opendevops-cn/codo-admin"]
    assert outcome.recommendations[0].coverage is GithubCoverage.COMPONENT
    assert outcome.recommendations[0].covers_parts == ["登录认证组件"]


def test_coverage_splits_whole_and_component_by_matched_features() -> None:
    """命中要点数决定覆盖面：够了算整体，不够就明说只覆盖哪一部分。"""
    analysis = parse_github_request(WHOLE_IDEA)
    partial = _evidence(readme_text="学生可以发布想卖的书，也可以搜索想要的书。")

    outcome = rank_candidates(analysis, [partial])
    recommendation = outcome.recommendations[0]

    assert recommendation.coverage is GithubCoverage.COMPONENT
    assert recommendation.matched_feature_count == 2
    assert "只覆盖你列出的 3 项要点中的 2 项" in recommendation.coverage_note
    assert any("不能当作完整实现" in item for item in recommendation.limitations)


def test_stars_are_only_a_tie_break_between_equal_matches() -> None:
    """功能匹配优先：命中数相同时才比证据、活跃度，最后才是 star。"""
    analysis = parse_github_request(WHOLE_IDEA)
    popular = _evidence(full_name="demo/popular", stars=9999)
    grounded = _evidence(
        full_name="demo/grounded",
        stars=3,
        files_read=[GithubFileRead(path="src/web/search.py", kind="file", excerpt="搜索想要的书")],
    )

    outcome = rank_candidates(analysis, [popular, grounded])

    assert [item.full_name for item in outcome.recommendations] == [
        "demo/grounded",
        "demo/popular",
    ]


def test_license_and_readme_status_drive_honest_limitations() -> None:
    """未见许可证就不说可自由复用；未读到 README 就明说没有项目自述。"""
    analysis = parse_github_request(WHOLE_IDEA)
    evidence = _evidence(
        description="校园二手书交换平台，学生可以线下交换",
        readme_text=None,
        readme_status=GithubReadmeStatus.TOO_LARGE,
        license=GithubLicenseCheck(
            detected=True,
            spdx_id="MIT",
            file_read=False,
            note="元数据标注 MIT，本轮未读取许可文件正文。",
        ),
    )

    recommendation = rank_candidates(analysis, [evidence]).recommendations[0]

    assert any("没有取得 README" in item for item in recommendation.limitations)
    assert "未读取许可文件正文，许可条款以仓库页面为准。" in recommendation.limitations
    assert "已读取许可文件" not in recommendation.strengths


def test_render_shows_only_search_words_in_the_query_line() -> None:
    """「实际查询词」只列检索词：读取仓库的记录不混进这一行。"""
    projection = _projection(
        status=GithubProjectStatus.SUCCESS,
        queries=[
            {
                "source": "github_search",
                "query": "校园二手书交换",
                "status": "success",
                "evidence_count": 4,
                "retrieved_at": datetime(2026, 9, 26, tzinfo=UTC),
                "retryable": False,
            },
            {
                "source": "github_repository",
                "query": "253936563/huanshu",
                "status": "success",
                "evidence_count": 1,
                "retrieved_at": datetime(2026, 9, 26, tzinfo=UTC),
                "retryable": False,
            },
        ],
    )

    content = render_result_content(projection)

    assert "实际查询词：校园二手书交换。" in content
    assert "253936563/huanshu" not in content.split("\n")[0]


def test_render_states_evidence_boundary_and_never_claims_architecture() -> None:
    """正文只按真实证据说话：未读实现文件就不作内部架构断言。"""
    analysis = parse_github_request(WHOLE_IDEA)
    recommendation = rank_candidates(analysis, [_evidence()]).recommendations[0]
    projection = _projection(
        status=GithubProjectStatus.SUCCESS,
        scenario=analysis.scenario,
        features=list(analysis.features),
        recommendations=[recommendation],
    )

    content = render_result_content(projection)

    assert "链接：https://github.com/demo/bookswap" in content
    assert "功能匹配：" in content
    assert "借鉴角度：" in content
    assert "维护与许可证据：" in content
    assert "不对内部架构与代码质量作断言" in content
    # 只出现「不声称可自由复用」这类否定说法，不出现「可以自由复用」的断言。
    assert "不声称代码可自由复用" in content
    assert "可以自由复用" not in content and "可自由使用的代码" not in content
    # 证据等级如实标注为元数据 + README，不拔高成实现文件。
    assert "API 元数据、README 自述" in content
    assert "实际读取的实现文件" not in content


def test_render_empty_and_stopped_keep_the_real_queries() -> None:
    """空结果与停止都写出实际发过的查询词，不用记忆补造条目。"""
    projection = _projection(
        status=GithubProjectStatus.EMPTY,
        queries=[
            {
                "source": "github_search",
                "query": "校园二手书交换",
                "status": "empty",
                "evidence_count": 0,
                "retrieved_at": datetime(2026, 9, 26, tzinfo=UTC),
                "retryable": False,
            }
        ],
        empty_reason="上游没有返回候选。",
    )

    empty = render_empty_content(projection)
    stopped = render_stopped_content(projection)

    assert "实际查询词：校园二手书交换。" in empty
    assert "上游没有返回候选。" in empty
    assert "实际查询词：校园二手书交换。" in stopped

    # 没有给出具体原因时，正文也不允许用记忆补造条目。
    bare = render_empty_content(
        _projection(status=GithubProjectStatus.EMPTY, empty_reason=None)
    )
    assert "不会用记忆补造条目" in bare


def test_suggestion_detects_github_requests_and_respects_own_subject() -> None:
    """普通聊天里的 GitHub 请求形态给一键建议；带自己主题的请求不猜。"""
    suggestion = detect_github_suggestion("有没有人做过类似的项目")
    assert suggestion is not None
    assert suggestion["module_id"] == "github"
    assert suggestion["needs_disambiguation"] is True

    # 「找实现它的项目」指向前文，建议里明确说要先用前文内容做检索词。
    reference = detect_github_suggestion("找实现它的项目")
    assert reference is not None
    assert reference["needs_disambiguation"] is True
    assert detect_github_suggestion("今天天气不错") is None


def test_description_length_is_bounded_by_the_search_layer() -> None:
    """上游简介长度不受控，归一化时必须截断（正文与卡片只显示截断后的原文）。"""
    from bridges.github.searching import _bounded

    long_text = "反" * (MAX_DESCRIPTION_CHARS + 500)
    bounded = _bounded(long_text)

    assert bounded is not None
    assert len(bounded) == MAX_DESCRIPTION_CHARS + 1
    assert bounded.endswith("…")


def _projection(
    *,
    status: GithubProjectStatus,
    scenario: str = "校园二手书交换平台",
    features: list[str] | None = None,
    queries: list[dict[str, object]] | None = None,
    recommendations: list[object] | None = None,
    empty_reason: str | None = None,
) -> GithubProjectsProjection:
    return GithubProjectsProjection.model_validate(
        {
            "status": status,
            "scenario": scenario,
            "original_request": WHOLE_IDEA,
            "features": features or [],
            "whole_idea": True,
            "queries": queries or [],
            "recommendations": recommendations or [],
            "rate_limit": GithubRateLimitState(),
            "evidence_boundary": ["API 元数据来自 GitHub 接口。"],
            "empty_reason": empty_reason,
        }
    )


def test_analysis_type_is_stable_for_the_query_planner() -> None:
    """查询计划只看 IdeaAnalysis 的字段：类型稳定，便于纯函数测试。"""
    analysis: GithubIdeaAnalysis = parse_github_request("做个记账应用")
    assert analysis.original_request == "做个记账应用"
    assert analysis.tech_terms == []
