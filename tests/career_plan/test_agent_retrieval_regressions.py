"""用户原话中的 Agent 目标与公开详情必须进入职业规划。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridges.career_plan.collecting import HttpJobPageReader, parse_job_page
from bridges.career_plan.contracts import CareerPlanProjection, CareerPlanStatus
from bridges.career_plan.kernel import build_career_recipe
from bridges.career_plan.parsing import parse_career_request
from bridges.career_plan.planning import build_plan
from bridges.career_plan.presenting import render_result_content
from bridges.career_plan.searching import CareerSearchHit, CareerSearchOutcome, classify_hits
from bridges.kernel.contracts import KernelStatus
from bridges.storage.database import BridgesDatabase
from tests.career_plan.test_career_plan_kernel_acceptance import (
    _inputs,
    _kernel,
    _Ports,
    _seed,
)

PUBLIC_DETAIL = (
    '<title>Agent实习生 - 上海人工智能实验室</title>'
    '<div id="header"><p>2024.05.09</p><p>联合培养博士</p>'
    + '<a href="/research">科学研究与项目导航</a>' * 20
    + '</div><h1>大模型算法实习生（AI Agent方向）</h1>'
    '<p>智能体系统中心｜实习｜工程通道｜上海</p><p>2026-09-24</p>'
    '<div>岗位职责</div><p>负责 Agent 工具调用，建设大模型评测体系。</p>'
    '<div>岗位要求</div><p>熟悉 Python 与 RAG。</p>'
    '<div>热招职位</div><p>北京Java工程师，要求博士学历</p>'
)


def test_original_agent_request_is_searchable_without_generic_clarification() -> None:
    request = "我想做agent开发相关工作，可以以给我规划一下吗"
    analysis = parse_career_request(request)
    assert analysis.clarification is None
    assert analysis.job_terms == ["agent开发"]
    assert all("agent" in item.query.lower() for item in build_plan(analysis))


def test_resumed_algorithm_answer_preserves_original_agent_goal() -> None:
    analysis = parse_career_request(
        "算法工程师；大三",
        pending={"original_request": "我想做agent开发相关工作，可以以给我规划一下吗"},
    )
    assert all("agent" in item.query.lower() for item in build_plan(analysis))
    assert analysis.stage == "大三"


def test_detail_results_take_precedence_over_listing_pages() -> None:
    listing = "https://www.zhipin.com/zhaopin/abc"
    detail = "https://www.zhipin.com/job_detail/real.html"
    hits, _ = classify_hits(
        [(listing, "算法工程师招聘信息", ""), (detail, "Agent算法工程师", "")],
        source="boss",
    )
    assert [hit.url for hit in hits] == [detail, listing]


def test_employer_search_keeps_official_campus_detail_with_actual_source() -> None:
    url = "https://careers.example.com/campus/job/123"
    hits, dropped = classify_hits([(url, "Agent研发实习生", "")], source="corporate")
    assert dropped == 0
    assert len(hits) == 1
    assert hits[0].source == "campus"


def test_plain_public_detail_reads_work_location_from_body() -> None:
    page = parse_job_page(
        "<html><title>Agent开发工程师招聘</title><body>"
        "<h1>Agent开发工程师</h1><p>工作地点：上海</p>"
        "<h2>岗位职责</h2><p>负责智能体开发，熟悉 Python 与 RAG。</p>"
        "<h2>任职要求</h2><p>熟悉大模型应用开发。</p></body></html>",
        reference=datetime(2026, 10, 7, tzinfo=UTC),
    )
    assert page.is_job_posting
    assert page.city == "上海"
    assert page.requirements


def test_job_requirements_and_dates_exclude_navigation_and_related_jobs() -> None:
    page = parse_job_page(PUBLIC_DETAIL, reference=datetime(2026, 10, 7, tzinfo=UTC))
    assert page.published_raw == "2026-09-24"
    assert page.city == "上海"
    assert page.education is None
    assert any("工具调用" in line for line in page.requirements)
    assert all("导航" not in line and "北京" not in line for line in page.requirements)


@pytest.mark.parametrize("search_seconds", [0, 24])
def test_full_agent_flow_reads_detail_after_lists_and_slow_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, search_seconds: int,
) -> None:
    """真实 HTTP 解析、SQLite 节点与质量门；只替换外部响应和时钟。"""
    import bridges.career_plan.kernel as kernel_module

    elapsed = [0.0]
    monkeypatch.setattr(kernel_module.time, "monotonic", lambda: elapsed[0])
    detail = "https://www.zhipin.com/job_detail/agent.html"
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=PUBLIC_DETAIL)

    class Ports(_Ports):
        def search_public(self, *args: Any, **kwargs: Any) -> CareerSearchOutcome:
            outcome = super().search_public(*args, **kwargs)
            elapsed[0] += search_seconds
            if kwargs["source"] != "boss":
                return outcome
            hits = tuple(
                CareerSearchHit(
                    url=f"https://www.zhipin.com/zhaopin/list{index}",
                    title="算法工程师招聘信息", snippet="", source="boss",
                )
                for index in range(3)
            ) + (CareerSearchHit(url=detail, title="Agent开发实习生", snippet="", source="boss"),)
            return CareerSearchOutcome(record=outcome.record, hits=hits)

        def read(self, url: str, **kwargs: Any) -> Any:
            return reader.read(url, **kwargs)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        reader = HttpJobPageReader(client=http)
        database = BridgesDatabase(tmp_path / "agent-flow.db")
        database.initialize()
        _seed(database)
        ports = Ports()
        kernel, _ = _kernel(database, ports)
        result = kernel.execute(
            recipe=build_career_recipe(),
            inputs=_inputs("我想做agent开发相关工作，可以以给我规划一下吗；大三"),
        )
        assert result.status is KernelStatus.COMPLETED
        assert result.delivery is not None
        projection = CareerPlanProjection.model_validate(result.delivery.payload["projection"])
        assert projection.status is CareerPlanStatus.SUCCESS
        assert [sample.url for sample in projection.samples] == [detail]
        assert requests == [detail]
        assert all("agent" in query.lower() for query, _ in ports.queries)
        assert "大三" not in projection.plan[-1].query
        if search_seconds == 0:
            assert "实习" in projection.plan[-1].query
        assert len(projection.plan) == len(ports.queries), "展示实际执行过的计划"
        assert len(projection.candidate_links) == 3
        content = render_result_content(projection)
        assert "能运行、能评测的 Agent 项目" in content
        assert content.index("可执行建议") < content.index("检索计划")
        assert all(
            "导航" not in line for sample in projection.samples for line in sample.requirements
        )
        database.close()
