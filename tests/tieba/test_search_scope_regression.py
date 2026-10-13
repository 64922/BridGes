"""国庆讨论检索回归：主题完整、两轮均固定来源、摘录来自正文。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from bridges.tieba.official import (
    STATUS_EXCERPT_NOT_FOUND,
    HttpOfficialSiteReader,
    extract_page_text,
    locate_excerpt,
    official_fallback_query,
    official_query,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from bridges.web_search.tavily import TAVILY_SEARCH_ENDPOINT, TavilySearchClient


def test_holiday_discussion_keeps_the_topic_and_experience_intent() -> None:
    analysis = parse_tieba_request("我想了解国庆节假期的讨论")
    assert analysis.topic_terms == ["国庆节", "假期"]
    assert analysis.question_kind.value == "experience"
    assert not analysis.needs_official_check


def test_both_forum_queries_stay_inside_tieba() -> None:
    analysis = parse_tieba_request("我想了解国庆节假期的讨论")
    queries = plan_queries(analysis)
    assert len(queries) == 2
    assert all(query.startswith("site:tieba.baidu.com/p/ 华东交通大学吧 ") for query in queries)
    assert "国庆节" in queries[0] and "国庆" in queries[1]
    assert all("解国庆节" not in query and "讨论" not in query for query in queries)


def test_official_fallback_keeps_the_official_domain() -> None:
    analysis = parse_tieba_request("国庆节假期")
    assert official_query(analysis).startswith("site:ecjtu.edu.cn ")
    assert official_fallback_query(analysis).startswith("site:ecjtu.edu.cn ")


def test_official_excerpt_prefers_the_notice_body_over_navigation() -> None:
    html = (
        "<html><head><title>国庆节假期安排</title></head><body>"
        "<nav>首页 国庆节假期 馆藏资源 开放时间</nav>"
        "<article><h1>国庆节假期安排</h1>"
        "<p>国庆节假期，图书馆每日开放时间为8:00至22:00。</p></article>"
        "</body></html>"
    )
    excerpt, _ = locate_excerpt(extract_page_text(html), ("假期", "国庆节"))
    assert excerpt is not None and "8:00至22:00" in excerpt
    assert "馆藏资源" not in excerpt


def test_official_title_alone_is_not_a_verified_notice() -> None:
    html = (
        "<html><head><title>国庆节假期安排</title></head><body>"
        "<h1>国庆节假期安排</h1><nav>假期 开放时间</nav>"
        "<p>当前页面没有通知正文。</p></body></html>"
    )
    reader = HttpOfficialSiteReader(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
        )
    )
    check = reader.fetch("https://lib.ecjtu.edu.cn/info/1.htm", terms=("国庆节", "假期"))
    assert check.status == STATUS_EXCERPT_NOT_FOUND
    assert check.excerpt is None


def test_official_excerpt_keeps_the_holiday_topic_instead_of_any_vacation() -> None:
    text = "寒假假期另行安排。" * 25 + "\n国庆节期间图书馆每日开放至22:00。"
    excerpt, matched = locate_excerpt(text, ("国庆节", "假期"))
    assert excerpt == "国庆节期间图书馆每日开放至22:00。"
    assert matched == ("国庆节",)


@pytest.mark.parametrize(
    ("query", "domain", "allowed_url"),
    [
        (
            "site:tieba.baidu.com/p/ 华东交通大学吧 国庆节",
            "tieba.baidu.com",
            "https://c.tieba.baidu.com/p/123",
        ),
        (
            "site:ecjtu.edu.cn 华东交通大学 国庆节",
            "ecjtu.edu.cn",
            "https://lib.ecjtu.edu.cn/info/123.htm",
        ),
    ],
)
def test_provider_enforces_the_scope_before_extracting_pages(
    query: str, domain: str, allowed_url: str
) -> None:
    extracted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if str(request.url) == TAVILY_SEARCH_ENDPOINT:
            assert payload["include_domains"] == [domain]
            assert payload["include_domains_mode"] == "restrict"
            urls = [
                allowed_url,
                "https://www.douyin.com/video/123",
                f"https://{domain}.evil.com/p/123",
                "https://tieba.baidu.com/f?kw=其他",
            ]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"url": url, "title": "国庆节", "content": "国庆假期"} for url in urls
                    ]
                },
            )
        extracted.extend(payload["urls"])
        return httpx.Response(200, json={"results": []})

    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = client.search(query)
    assert [result.url for result in results] == [allowed_url]
    assert extracted == [allowed_url]
