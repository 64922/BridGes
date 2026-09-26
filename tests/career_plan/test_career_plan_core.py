"""Issue 15 核心合同：岗位识别、检索计划、过滤口径、统计口径与建议。

覆盖五条验收里可确定性验证的部分：

- 原词逐字保留，只把真正同义名当作同一岗位，相邻岗位绝不暗换；
- 岗位含糊或缺失时只问一项；
- 主样本只收公开可读且岗位与城市都匹配的岗位，重复与过期逐条剔除；
- 薪资按计薪单位分别归并，不可比较的写法不混算；
- 技能与薪资统计标出样本量、地区与计薪单位，小样本明确不是市场均值。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from bridges.career_plan.advising import build_advice
from bridges.career_plan.analyzing import SMALL_SAMPLE_MIN, analyze_samples
from bridges.career_plan.collecting import JobPageReadResult, ParsedJobPage, is_stale
from bridges.career_plan.contracts import JobReadStatus
from bridges.career_plan.filtering import (
    KIND_ADJACENT,
    KIND_CITY,
    KIND_CITY_UNVERIFIED,
    KIND_DUPLICATE,
    KIND_EXPIRED,
    KIND_TITLE_MISMATCH,
    JobCandidate,
    filter_candidates,
)
from bridges.career_plan.lexicon import (
    adjacent_terms,
    classify_source,
    detect_cities,
    detect_graduation_year,
    detect_stage,
    family_for,
    is_job_intent_ambiguous,
    match_job_title,
    synonym_terms,
)
from bridges.career_plan.parsing import parse_career_request
from bridges.career_plan.planning import build_plan
from bridges.career_plan.salary import (
    UNIT_DAY,
    UNIT_MONTH,
    UNIT_YEAR,
    aggregate_salary,
    parse_salary,
)
from bridges.career_plan.searching import WebSearchServiceAdapter

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# career.parse：原词、同义与相邻
# --------------------------------------------------------------------------


def test_job_anchor_is_kept_verbatim_and_not_swapped_to_adjacent() -> None:
    """「算法工程师」不能自动换成「数据分析师」：同义与相邻必须分开。"""
    analysis = parse_career_request("帮我看下算法工程师的岗位，城市北京")
    assert analysis.job_terms == ["算法工程师"]
    assert "算法工程师" in analysis.synonyms
    assert analysis.family_title == "算法工程师"
    # 数据分析师是相邻岗位，绝不能出现在同义名里
    assert "数据分析师" not in analysis.synonyms
    assert "数据分析师" in analysis.adjacent_jobs
    assert analysis.cities == ["北京"]


def test_framed_job_title_extraction_ignores_bare_tech_word() -> None:
    """「想找 Java 后端实习」的锚点是岗位短语，不是裸技术词 Java。"""
    analysis = parse_career_request("我想找 Java 后端实习，地点杭州")
    assert analysis.job_terms == ["Java 后端实习"]
    assert analysis.job_terms != ["Java"]
    assert analysis.cities == ["杭州"]


def test_stage_and_graduation_year_are_kept_as_spoken() -> None:
    """届别与阶段按原话记录：届别换算成绝对年份，阶段保留用户说法。"""
    analysis = parse_career_request("2026届，目标岗位是数据分析师")
    assert analysis.graduation_year == 2026
    assert analysis.job_terms == ["数据分析师"]
    assert parse_career_request("我是应届生，想找测试工程师").stage == "应届生"


def test_missing_job_intent_asks_exactly_one_question() -> None:
    analysis = parse_career_request("帮我看看有哪些岗位")
    assert analysis.job_terms == []
    assert analysis.clarification is not None
    assert "什么岗位" in analysis.clarification.question
    assert is_job_intent_ambiguous([])


def test_ambiguous_bare_head_asks_which_role() -> None:
    """只有一个跨族裸 head 时必须先问，不能挑一个方向去搜。"""
    analysis = parse_career_request("我想找数据岗")
    assert analysis.clarification is not None
    assert "数据" in analysis.clarification.question
    assert is_job_intent_ambiguous(["数据"])


def test_clear_job_intent_does_not_ask() -> None:
    assert parse_career_request("帮我查测试工程师岗位").clarification is None
    assert not is_job_intent_ambiguous(["测试工程师"])
    assert not is_job_intent_ambiguous(["后端开发", "算法工程师"])


def test_cities_stage_and_constraints_are_detected() -> None:
    analysis = parse_career_request("目标岗位是后端开发实习生，希望双休，城市南昌和深圳")
    assert analysis.cities == ["南昌", "深圳"]
    assert "双休" in analysis.constraints
    assert detect_cities("想去上海工作") == ["上海"]
    assert detect_stage("我是大三学生") == "大三"
    assert detect_graduation_year("26届") == 2026


def test_synonyms_share_family_and_adjacent_excludes_self() -> None:
    family = family_for("Java后端")
    assert family is not None and family.key == "backend"
    terms = synonym_terms(["Java后端"])
    assert "后端开发工程师" in terms
    adjacent = adjacent_terms(["Java后端"])
    assert "前端开发工程师" in adjacent and "测试工程师" in adjacent
    assert not set(adjacent) & set(terms)


def test_family_lookup_refuses_to_guess_unknown_title() -> None:
    """不在词表内、且跨多个族的说法不归类，避免猜成别的岗位。"""
    assert family_for("综合管理") is None


# --------------------------------------------------------------------------
# career.plan：三类来源的查询与筛选条件
# --------------------------------------------------------------------------


def test_plan_covers_public_corporate_and_campus_sources() -> None:
    analysis = parse_career_request("我想找 Java 后端实习，城市南昌")
    plan = build_plan(analysis)
    assert [item.source for item in plan] == ["boss", "corporate", "campus"]
    queries = [item.query for item in plan]
    assert len(set(queries)) == 3, "三类来源必须是三条不同的实际查询"
    assert queries[0].startswith("zhipin.com ")
    assert "校园招聘" in queries[2]
    for item in plan:
        assert item.filters, "每条计划都要展示筛选条件"
    assert any("城市必须是「南昌」" in f for item in plan for f in item.filters)
    assert any("相邻岗位" in f for item in plan for f in item.filters)


def test_plan_without_city_says_city_is_not_filtered() -> None:
    analysis = parse_career_request("帮我查算法工程师岗位")
    plan = build_plan(analysis)
    assert any("未给出城市" in f for item in plan for f in item.filters)


def test_plan_uses_first_city_and_discloses_the_rest() -> None:
    """多个城市只按第一个发查询，且必须写明其余城市本轮没有检索。"""
    analysis = parse_career_request("我想找 Java 后端开发，城市南昌、北京")
    plan = build_plan(analysis)
    assert all("南昌" in item.query for item in plan)
    assert all("北京" not in item.query for item in plan)
    assert any(
        "北京" in f and "未检索" in f for item in plan for f in item.filters
    ), "只检索了第一个城市，就必须在筛选条件里说明其他城市没查"


def test_plan_filters_only_claim_what_is_actually_enforced() -> None:
    """筛选条件只写真正执行的判定：阶段与经验只影响查询词或原话展示。"""
    analysis = parse_career_request("我想找 Java 后端开发，2026 届，经验 1-3年，城市南昌")
    plan = build_plan(analysis)
    assert analysis.experience_hint == "1-3年"
    assert analysis.graduation_year == 2026
    for item in plan:
        for text in item.filters:
            assert "经验" not in text, text
            assert "毕业阶段" not in text and "2026 届" not in text, text
    assert any("校园招聘/应届生口径" in f for item in plan for f in item.filters)


# --------------------------------------------------------------------------
# career.filter：主样本口径
# --------------------------------------------------------------------------


def _read(
    title: str,
    *,
    city: str | None = "南昌",
    salary: str | None = "15-25K",
    is_job: bool = True,
    expired: bool = False,
    published: date | None = date(2026, 9, 20),
    status: JobReadStatus = JobReadStatus.READ,
) -> JobPageReadResult:
    page = ParsedJobPage(
        title=title,
        company="某某科技",
        city=city,
        salary_raw=salary,
        published_raw=published.isoformat() if published else None,
        published_date=published,
        requirements=["熟悉 Java 与 MySQL"],
        is_job_posting=is_job,
        expired=expired,
        expired_evidence="岗位页出现「职位已下线」" if expired else None,
        structure_found=is_job,
    )
    return JobPageReadResult(
        url="https://example.invalid/job", status=status, page=page, retrieved_at=NOW
    )


def _candidate(url: str, title: str, **kwargs: object) -> JobCandidate:
    return JobCandidate(
        url=url, source="boss", label=title, read=_read(title, **kwargs)  # type: ignore[arg-type]
    )


def test_only_readable_matching_title_and_city_enter_main_sample() -> None:
    analysis = parse_career_request("我想找 Java 后端实习，城市南昌")
    candidates = [
        _candidate("https://a/1", "Java后端开发工程师"),
        _candidate("https://a/2", "前端开发工程师"),
        _candidate("https://a/3", "Java后端开发工程师", city="杭州"),
        _candidate("https://a/4", "Java后端开发工程师", city=None),
        _candidate("https://a/5", "Java后端开发工程师", expired=True),
        _candidate("https://a/6", "市场专员"),
        _candidate("https://a/7", "Java后端开发工程师", is_job=False),
        JobCandidate(
            url="https://a/8",
            source="boss",
            label="Java后端开发工程师",
            read=JobPageReadResult(
                url="https://a/8",
                status=JobReadStatus.ACCESS_RESTRICTED,
                error_code="career_read_access_restricted",
                error_message="岗位页要求登录或触发了访问验证，未取得岗位内容。",
                retrieved_at=NOW,
            ),
        ),
    ]
    outcome = filter_candidates(candidates, analysis, reference=NOW)
    assert [sample.url for sample in outcome.samples] == ["https://a/1"]
    kinds = {item.url: item.kind for item in outcome.rejected}
    assert kinds["https://a/2"] == KIND_ADJACENT
    assert kinds["https://a/3"] == KIND_CITY
    assert kinds["https://a/4"] == KIND_CITY_UNVERIFIED
    assert kinds["https://a/5"] == KIND_EXPIRED
    assert kinds["https://a/6"] == KIND_TITLE_MISMATCH
    # 读不到页面的候选降级为「未核实链接」，不进入任何统计
    assert [link.url for link in outcome.unconfirmed] == ["https://a/8"]
    assert outcome.rejected[0].evidence


def test_city_with_district_suffix_still_matches_the_wanted_city() -> None:
    """真实页面的城市常写成「南昌-青山湖区」：包含关系成立即算核对通过。"""
    analysis = parse_career_request("我想找 Java 后端开发的工作，城市南昌")
    outcome = filter_candidates(
        [_candidate("https://a/1", "Java后端开发工程师", city="南昌-青山湖区")],
        analysis,
        reference=NOW,
    )
    assert [sample.url for sample in outcome.samples] == ["https://a/1"]
    assert "南昌-青山湖区" in outcome.samples[0].city_evidence


def test_adjacent_job_is_counted_separately_not_merged() -> None:
    analysis = parse_career_request("目标是 Java 后端实习")
    outcome = filter_candidates(
        [_candidate("https://a/1", "测试工程师")], analysis, reference=NOW
    )
    assert outcome.samples == []
    assert outcome.adjacent_counts == {"测试工程师": 1}
    assert "相邻岗位" in outcome.rejected[0].evidence


def test_duplicate_samples_keep_only_the_first() -> None:
    analysis = parse_career_request("目标是 Java 后端实习")
    outcome = filter_candidates(
        [
            _candidate("https://a/1", "Java后端开发工程师"),
            _candidate("https://a/2", "Java后端开发工程师"),
            _candidate("https://a/1", "Java后端开发工程师"),
        ],
        analysis,
        reference=NOW,
    )
    assert [sample.url for sample in outcome.samples] == ["https://a/1"]
    assert [item.kind for item in outcome.rejected] == [KIND_DUPLICATE]


def test_sample_marks_fetch_and_publish_times_salary_and_link() -> None:
    analysis = parse_career_request("目标是 Java 后端实习，城市南昌")
    sample = filter_candidates(
        [_candidate("https://a/1", "Java后端开发工程师")], analysis, reference=NOW
    ).samples[0]
    assert sample.retrieved_at == NOW
    assert sample.published_raw == "2026-09-20"
    assert sample.published_date == date(2026, 9, 20)
    assert sample.salary_raw == "15-25K"
    assert sample.requirements == ["熟悉 Java 与 MySQL"]
    assert sample.url == "https://a/1"
    assert sample.title_evidence and sample.city_evidence


def test_city_is_not_required_when_user_gives_none() -> None:
    analysis = parse_career_request("目标是 Java 后端实习")
    outcome = filter_candidates(
        [_candidate("https://a/1", "Java后端开发工程师", city=None)],
        analysis,
        reference=NOW,
    )
    assert len(outcome.samples) == 1
    assert "未做城市过滤" in outcome.samples[0].city_evidence


def test_stale_publish_date_is_rejected_as_expired() -> None:
    analysis = parse_career_request("目标是 Java 后端实习")
    outcome = filter_candidates(
        [_candidate("https://a/1", "Java后端开发工程师", published=date(2025, 1, 1))],
        analysis,
        reference=NOW,
    )
    assert outcome.samples == []
    assert outcome.rejected[0].kind == KIND_EXPIRED
    assert "过期" in outcome.rejected[0].evidence
    assert is_stale(date(2025, 1, 1), reference=NOW)
    assert not is_stale(None, reference=NOW)


# --------------------------------------------------------------------------
# 薪资解析：可比性与单位
# --------------------------------------------------------------------------


def test_salary_k_form_is_monthly_and_months_recorded() -> None:
    band = parse_salary("15-25K·15薪")
    assert band.comparable and band.unit == UNIT_MONTH
    assert (band.amount_min, band.amount_max) == (15000, 25000)
    assert band.salary_months == 15


def test_salary_period_markers_decide_unit() -> None:
    assert parse_salary("8千-1.2万/月").unit == UNIT_MONTH
    assert parse_salary("200-300元/天").unit == UNIT_DAY
    assert parse_salary("15-25万/年").unit == UNIT_YEAR


def test_salary_without_period_marker_is_not_comparable() -> None:
    band = parse_salary("1.5-2万")
    assert band.comparable is False
    assert band.unit is None
    assert band.note and "计薪周期" in band.note


def test_salary_without_numbers_is_not_comparable() -> None:
    band = parse_salary("面议")
    assert band.comparable is False
    assert "面议" in (band.note or "")


def test_incomparable_salaries_never_merge_into_intervals() -> None:
    """不同计薪单位与不可比较写法都不混算。"""
    bands = [
        parse_salary("15-25K"),
        parse_salary("20-30K"),
        parse_salary("200-300元/天"),
        parse_salary("1.5-2万"),
        parse_salary("面议"),
    ]
    aggregates, notes = aggregate_salary(bands)
    by_unit = {item.unit: item for item in aggregates}
    assert set(by_unit) == {UNIT_MONTH, UNIT_DAY}
    assert by_unit[UNIT_MONTH].sample_count == 2
    assert by_unit[UNIT_MONTH].amount_min == 15000
    assert by_unit[UNIT_MONTH].amount_max == 30000
    assert by_unit[UNIT_DAY].sample_count == 1
    assert len(notes) == 2, "不可比较的两条都要留下原因"


# --------------------------------------------------------------------------
# career.analyze / career.advise：口径与推断标注
# --------------------------------------------------------------------------


def _samples(count: int, *, salary: str = "15-25K", city: str = "南昌") -> list:
    from bridges.career_plan.contracts import JobSample

    return [
        JobSample(
            url=f"https://a/{index}",
            source="boss",
            source_label="公开招聘职位",
            title="Java后端开发工程师",
            company=f"公司{index}",
            city=city,
            salary_raw=salary,
            published_raw="2026-09-20",
            published_date=date(2026, 9, 20),
            requirements=["熟悉 Java 与 MySQL，了解 Redis"],
            skills=["Java", "MySQL", "Redis"],
            title_evidence="命中",
            city_evidence="一致",
            retrieved_at=NOW,
            read_status=JobReadStatus.READ,
        )
        for index in range(count)
    ]


def test_analysis_reports_sample_count_date_city_and_unit() -> None:
    report = analyze_samples(_samples(3))
    assert report.sample_count == 3
    assert report.city_composition[0].city == "南昌"
    assert report.city_composition[0].count == 3
    assert report.published_span == "2026-09-20"
    assert report.salary_intervals[0].unit == UNIT_MONTH
    assert "3 个" in report.sample_scope_note
    assert "南昌" in report.sample_scope_note
    assert "不是全国市场均值" in report.sample_scope_note
    assert report.skill_stats[0].term in {"Java", "MySQL", "Redis"}
    assert report.skill_stats[0].count == 3


def test_small_sample_stops_overall_inference() -> None:
    report = analyze_samples(_samples(SMALL_SAMPLE_MIN - 1))
    assert report.small_sample is True
    assert report.overall_inference_stopped is True
    assert report.salary_intervals[0].small_sample is True
    big = analyze_samples(_samples(SMALL_SAMPLE_MIN))
    assert big.small_sample is False
    assert big.overall_inference_stopped is False


def test_zero_sample_analysis_makes_no_claim() -> None:
    report = analyze_samples([])
    assert report.sample_count == 0
    assert report.overall_inference_stopped is True
    assert report.salary_intervals == []


def test_advice_marks_inference_and_carries_evidence() -> None:
    analysis = parse_career_request("我想找 Java 后端实习，城市南昌")
    samples = _samples(6)
    report = analyze_samples(samples)
    advices, adjacent = build_advice(analysis, report, samples, adjacent_counts={})
    assert advices, "有样本时必须给出可执行建议"
    skill_advices = [item for item in advices if item.kind == "skill"]
    assert skill_advices and all(item.basis for item in skill_advices)
    assert all(item.inference is False for item in skill_advices)
    project = [item for item in advices if item.kind == "project"]
    assert project and project[0].inference is True, "方向性建议必须标为推断"
    assert "推断" in project[0].detail
    assert adjacent, "相邻岗位必须单列"
    assert all(item.title for item in adjacent)


def test_advice_is_empty_without_samples() -> None:
    analysis = parse_career_request("我想找 Java 后端实习")
    report = analyze_samples([])
    advices, adjacent = build_advice(analysis, report, [], adjacent_counts={})
    assert advices == []
    assert adjacent, "即使没有样本，相邻岗位也照实单列"


# --------------------------------------------------------------------------
# 来源分类
# --------------------------------------------------------------------------


def test_source_classification_covers_three_recruitment_kinds() -> None:
    assert classify_source("https://www.zhipin.com/job_detail/abc.html") == "boss"
    assert classify_source("https://careers.tencent.com/job/123") == "corporate"
    assert classify_source("https://campus.51job.com/xyz/") == "campus"
    assert classify_source("https://www.yingjiesheng.com/job-1.html") == "campus"
    assert classify_source("https://example.com/about") is None


def test_title_match_reports_adjacent_hits() -> None:
    match = match_job_title(
        "前端开发工程师",
        target_terms=tuple(synonym_terms(["Java后端"])),
        adjacent=tuple(adjacent_terms(["Java后端"])),
    )
    assert match.matched is False
    assert "前端开发工程师" in match.adjacent_hits


# --------------------------------------------------------------------------
# 检索适配：必须能读真实搜索投影（占位替身读不出字段名漂移）
# --------------------------------------------------------------------------


class _ProjectionService:
    """返回**真实** ``WebSearchProjection`` 的搜索服务替身（字段名与生产一致）。"""

    def __init__(self, projection: object) -> None:
        self._projection = projection
        self.calls: list[tuple[str, str]] = []

    def search(self, account_id: str, plan: object, **_: object) -> object:
        self.calls.append((account_id, getattr(plan, "query", "")))
        return self._projection


def test_adapter_reads_the_real_search_projection_shape() -> None:
    """适配器必须按真实投影字段取值：摘要只有 snippet／content_summary。"""
    from bridges.web_search.contracts import (  # noqa: PLC0415
        WebSearchProjection,
        WebSearchResult,
        WebSearchStatus,
    )

    projection = WebSearchProjection(
        status=WebSearchStatus.SUCCESS,
        trigger_reason="显式选择职业规划模块",
        query_summary="zhipin.com Java后端开发 南昌 招聘",
        results=[
            WebSearchResult(
                result_id="r-1",
                title="Java后端开发工程师招聘",
                site="zhipin.com",
                url="https://www.zhipin.com/job_detail/abc.html",
                snippet="岗位职责：负责服务端接口开发。",
                accessed_at=NOW,
            ),
            WebSearchResult(
                result_id="r-2",
                title="公司简介",
                site="example.com",
                url="https://example.com/about",
                content_summary="我们是一家公司。",
                accessed_at=NOW,
            ),
        ],
        searched_at=NOW,
    )
    service = _ProjectionService(projection)
    outcome = WebSearchServiceAdapter(service).search_public(
        "acct", query="zhipin.com Java后端开发 南昌 招聘", reason="取匹配岗位", source="boss"
    )
    assert [hit.url for hit in outcome.hits] == [
        "https://www.zhipin.com/job_detail/abc.html"
    ]
    assert outcome.hits[0].snippet == "岗位职责：负责服务端接口开发。"
    assert outcome.record.status == "success"
    assert outcome.record.evidence_count == 1
    assert outcome.record.detail == "另有 1 条结果不是招聘页，未作为候选"
