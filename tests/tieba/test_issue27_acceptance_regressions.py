"""Issue 27 独立验收反例：证据不足时不能裁决或宣称核验通过。"""

from datetime import UTC, datetime

import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba import evidence
from bridges.tieba.contracts import ReadStatus, TiebaOfficialCheck, TiebaPostProjection, TiebaReply
from bridges.tieba.kernel import build_evidence_plan, run_verification
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.presenting import build_sections
from bridges.tieba.searching import plan_queries, query_record
from tests.tieba.test_tieba_module_flow import _FakeOfficialReader, _FakeSearchPort


def _check(excerpt: str, title: str = "华东交通大学转专业管理办法") -> TiebaOfficialCheck:
    return TiebaOfficialCheck(
        title=title,
        url="https://jwc.ecjtu.edu.cn/info/1/2.htm",
        host="jwc.ecjtu.edu.cn",
        fetched_at=datetime(2026, 10, 3, tzinfo=UTC),
        status="verified",
        excerpt=excerpt,
        matched_terms=["转专业"],
    )


def _post(
    posted_at: str | None = "2026-05-20", content: str = "转专业不是每年一次。"
) -> TiebaPostProjection:
    return TiebaPostProjection(
        url="https://tieba.baidu.com/p/123",
        title="转专业经历",
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.READ,
        pages_read=1,
        pages_limit=2,
        floor_min=2,
        floor_max=2,
        replies_obtained=True,
        replies=[TiebaReply(floor=2, posted_at=posted_at, content=content)],
        retrieved_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "question,title,excerpt",
    [
        (
            "华东交通大学吧 2026年3月20日 转专业 规定",
            "华东交通大学转专业申请通知",
            "转专业申请仅在2026年3月1日受理。",
        ),
        (
            "华东交通大学吧 转专业 规定",
            "华东交通大学转专业申请通知",
            "转专业申请仅在2026年3月1日至3月31日受理，逾期不予办理。",
        ),
        (
            "华东交通大学吧 本科生 转专业 规定",
            "华东交通大学研究生转专业管理办法",
            "研究生转专业申请由教务处统一审批。",
        ),
        (
            "华东交通大学吧 2026年10月 转专业 规定",
            "华东交通大学转专业申请通知",
            "转专业申请仅在2026年3月1日至3月31日受理，逾期不予办理。",
        ),
        ("华东交通大学吧 最近 转专业 规定", "华东交通大学转专业管理办法", "转专业每年申请一次。"),
        (
            "华东交通大学吧 转专业 规定",
            "上海交通大学转专业管理办法",
            "转载上海交通大学：转专业每年申请一次。",
        ),
        (
            "华东交通大学吧 转专业 规定",
            "华东交通大学新闻",
            "我校学生讲述转专业经历，最新校园活动精彩纷呈。",
        ),
    ],
)
def test_unconfirmed_official_scope_cannot_be_applicable(
    question: str, title: str, excerpt: str
) -> None:
    assert not evidence.assess_applicability(
        _check(excerpt, title), parse_tieba_request(question)
    ).applicable


@pytest.mark.parametrize(
    "question,excerpt,posted_at",
    [
        ("华东交通大学吧 转专业 规定", "2026年最新消息：转专业每年申请一次。", "2025-05-20"),
        ("华东交通大学吧 转专业 规定", "自2026年3月1日起执行：转专业每年申请一次。", None),
        (
            "华东交通大学吧 南区 转专业 规定",
            "北区自2026年3月1日起执行：转专业每年申请一次。",
            "2025-05-20",
        ),
        ("华东交通大学吧 转专业 规定", "自2026年3月1日起执行：转专业每年申请一次。", "2026-05-20"),
    ],
)
def test_no_proven_supersession_keeps_both(
    question: str, excerpt: str, posted_at: str | None
) -> None:
    analysis = parse_tieba_request(question)
    check = _check(excerpt)
    check = check.model_copy(
        update={"applicability": evidence.assess_applicability(check, analysis)}
    )
    conflicts = evidence.detect_conflicts(analysis, [check], [_post(posted_at)])
    assert conflicts and conflicts[0].resolution == "kept_both"


def test_matching_negative_statements_are_not_a_conflict() -> None:
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定")
    assert not evidence.detect_conflicts(
        analysis, [_check("转专业不用缴费。")], [_post(content="转专业不用缴费。")]
    )


def test_changed_reply_date_is_rejected_by_verification() -> None:
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定")
    post = _post()
    projected = post.model_copy(
        update={"replies": [post.replies[0].model_copy(update={"posted_at": "2020-01-01"})]}
    )
    projection = evidence.build_projection(
        analysis=analysis,
        records=[],
        confirmed=[projected],
        rejected=[],
        unconfirmed=[],
        sections=[],
        time_filter=evidence.time_filter_state(analysis, [post]),
        official_checks=[],
    ).model_copy(update={"official_unverified_note": "规定未核实。"})
    verification = run_verification(
        analysis,
        build_evidence_plan(analysis, official_blocked_reason=None),
        projection,
        [],
        [post],
    )
    assert not verification.read_scope_consistent


def test_real_search_payload_reuses_successful_empty_query() -> None:
    analysis = parse_tieba_request("华东交通大学吧 食堂 怎么样")
    queries = plan_queries(analysis)
    record = query_record(query=queries[0], status=ModuleQueryStatus.EMPTY, evidence_count=0)
    port = _FakeSearchPort()
    attempts = evidence.search_candidates(
        port,
        "acc-27",
        analysis,
        stop_event=None,
        deadline_seconds=5,
        resume={"records": [record.model_dump(mode="json")], "candidates": [], "partial": True},
    )
    assert port.queries == [queries[1]]
    assert len(attempts.records) == 2


def test_official_resume_reuses_discovery_and_read_pages() -> None:
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定")
    from bridges.tieba.official import official_query

    record = query_record(
        query=official_query(analysis), status=ModuleQueryStatus.SUCCESS, evidence_count=1
    )
    check = _check("转专业申请由教务处统一审批。")
    port = _FakeSearchPort()
    reader = _FakeOfficialReader()
    outcome = evidence.verify_official(
        port,
        reader,
        "acc-27",
        analysis,
        stop_event=None,
        deadline_seconds=5,
        resume={
            "records": [record.model_dump(mode="json")],
            "candidates": [check.url],
            "checks": [check.model_dump(mode="json")],
        },
    )
    assert port.queries == []
    assert reader.fetched == []
    assert outcome.checks == [check]


def test_section_cannot_cite_wrong_post_or_floor() -> None:
    from bridges.tieba.kernel import _quotes_traceable

    post = _post(content="我转专业每年申请一次。")
    sections = build_sections([post])
    assert _quotes_traceable(sections, [post])
    assert not _quotes_traceable([sections[0].replace("第 2 楼", "第 999 楼")], [post])


def test_conflict_cannot_change_resolution_or_quote() -> None:
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定")
    check = _check("转专业申请由教务处统一审批。")
    check = check.model_copy(
        update={"applicability": evidence.assess_applicability(check, analysis)}
    )
    post = _post()
    conflicts = evidence.detect_conflicts(analysis, [check], [post])
    projection = evidence.build_projection(
        analysis=analysis,
        records=[],
        confirmed=[post],
        rejected=[],
        unconfirmed=[],
        sections=build_sections([post]),
        time_filter=evidence.time_filter_state(analysis, [post]),
        official_checks=[check],
        conflicts=[conflicts[0].model_copy(update={"resolution": "official_newer"})],
    )
    assert not run_verification(
        analysis,
        build_evidence_plan(analysis, official_blocked_reason=None),
        projection,
        [check],
        [post],
    ).conflicts_disclosed
