"""工单 42：列表字段不能成为岗位详情的真实可用证明。"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from bridges.career_plan.collecting import HttpJobPageReader
from bridges.career_plan.contracts import JobReadStatus
from bridges.evaluation.external_probe_contracts import ProbeContext, ProbeStatus
from bridges.evaluation.external_probes_public_pages import probe_public_jobs


@pytest.mark.parametrize("redirect", [False, True])
def test_reader_rejects_liepin_listing_even_with_job_like_fields(redirect: bool) -> None:
    listing = "https://www.liepin.com/city-sh/zpshujufenxishixisheng/"
    detail = "https://www.liepin.com/job/123.shtml"
    body = "<title>上海数据分析实习生招聘</title><p>3-4k</p><p>岗位职责：分析数据</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        if redirect and str(request.url) == detail:
            return httpx.Response(302, headers={"location": listing})
        return httpx.Response(200, text=body)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        result = HttpJobPageReader(client=http).read(detail if redirect else listing)
    assert result.status is JobReadStatus.UNRECOGNIZED
    assert result.error_code == "career_read_listing"
    assert not result.page.is_job_posting
    assert result.page.salary_raw is None


@pytest.mark.parametrize(
    ("company", "city", "duties", "ok"),
    [
        ("某某科技", "上海", "岗位职责：负责数据分析和业务数据报表", True),
        (None, "上海", "岗位职责：负责数据分析和业务数据报表", False),
        ("某某科技", "北京", "岗位职责：负责数据分析和业务数据报表", False),
        ("某某科技", "上海", "", False),
    ],
)
def test_job_probe_requires_individual_verified_company_city_and_duties(
    company: str | None,
    city: str,
    duties: str,
    ok: bool,
) -> None:
    posting = {
        "@type": "JobPosting",
        "title": "数据分析实习生",
        "description": duties,
        "jobLocation": {"name": city},
        "hiringOrganization": {"name": company} if company else {},
    }
    listing = "https://www.liepin.com/city-sh/zpshujufenxishixisheng/"
    detail = "https://www.liepin.com/job/123.shtml"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.tavily.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "列表", "url": listing, "content": "3-4k"},
                        {"title": "岗位", "url": detail, "content": "数据分析"},
                    ]
                },
            )
        requested.append(str(request.url))
        return httpx.Response(
            200, text=('<script type="application/ld+json">' + json.dumps(posting) + "</script>")
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = probe_public_jobs(ProbeContext(http=http, tavily_key="test-key"))
    assert requested == [detail]
    assert (result.status is ProbeStatus.PASSED) is ok
    if ok:
        assert result.measurements["read"]["company"] == company
        assert result.measurements["read"]["city"] == city
        assert result.measurements["read"]["requirements"]


def test_declared_job_item_list_is_not_an_individual_posting() -> None:
    from bridges.career_plan.collecting import parse_job_page

    metadata = {
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "JobPosting",
                "title": "数据分析实习生",
                "description": "岗位职责：分析数据",
                "hiringOrganization": {"name": "某某科技"},
                "jobLocation": {"name": "上海"},
            }
        ],
    }
    html = '<script type="application/ld+json">' + json.dumps(metadata) + "</script>"
    page = parse_job_page(html, reference=datetime.now(UTC))
    assert not page.is_job_posting
    assert not page.structure_found
    assert page.company is None
