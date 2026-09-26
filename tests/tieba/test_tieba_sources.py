"""Issue 14 来源层合同：页面解析与分类、官方域名门、查询记录映射。

读取路径用可控 HTTP 替身驱动真实分类代码；解读器只用公开页面结构，
找不到结构就如实报不可识别。官方核验只用学校官方域名页面。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import ReadStatus
from bridges.tieba.official import (
    STATUS_DOMAIN_REJECTED,
    STATUS_EXCERPT_NOT_FOUND,
    STATUS_FETCH_FAILED,
    STATUS_VERIFIED,
    HttpOfficialSiteReader,
    extract_page_text,
    is_official_url,
    locate_excerpt,
    official_fallback_query,
    official_query,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.reading import (
    MAX_REPLIES,
    HttpTiebaThreadReader,
    parse_thread_page,
)
from bridges.tieba.searching import WebSearchServiceAdapter

TARGET_FORUM = "华东交通大学吧"


def _page_data(
    *,
    forum: str = "华东交通大学",
    title: str = "东北电力和华东交通哪个好",
    posts: list[dict[str, object]] | None = None,
    total_page: int = 3,
) -> str:
    data = {
        "forum": {"name": forum},
        "thread": {"title": title, "author": {"name": "楼主甲"}},
        "page": {"total_page": total_page},
        "post_list": posts
        if posts is not None
        else [
            {
                "floor": 1,
                "time": "2025-05-25 21:03",
                "author": {"name": "楼主甲"},
                "content": "求助东北电力和华东交通哪个好",
            },
            {
                "floor": 2,
                "time": "2025-05-26 08:11",
                "author": {"name": "吧友乙"},
                "content": [{"type": "text", "text": "我是过来人，电气方向更好。"}],
            },
        ],
    }
    return (
        "<html><head><title>帖子</title></head><body>"
        f"<script>var PageData = {json.dumps(data, ensure_ascii=False)};</script>"
        "</body></html>"
    )


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_parse_thread_page_reads_forum_title_floors_and_times() -> None:
    """页面自身给出吧名、标题、楼层与时间（归属确认只靠这个）。"""
    parsed = parse_thread_page(_page_data())

    assert parsed.structure_found is True
    assert parsed.forum_name == TARGET_FORUM
    assert parsed.title == "东北电力和华东交通哪个好"
    assert parsed.total_pages == 3
    assert [(reply.floor, reply.posted_at) for reply in parsed.replies] == [
        (1, "2025-05-25 21:03"),
        (2, "2025-05-26 08:11"),
    ]
    assert parsed.replies[0].is_original_poster is True
    assert parsed.replies[1].is_original_poster is False
    assert parsed.replies[1].content == "我是过来人，电气方向更好。"


def test_parse_thread_page_without_structure_is_unrecognized() -> None:
    """页面结构与预期不符：如实报不可识别，不做任何猜测。"""
    parsed = parse_thread_page("<html><body>登录后查看</body></html>")

    assert parsed.structure_found is False
    assert parsed.replies == ()


def test_reader_reports_access_restriction_without_retry_tricks() -> None:
    """403 访问限制：只记受限，不做任何绕过尝试（一次请求）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("pn", ""))
        return httpx.Response(403, text="百度安全验证")

    reader = HttpTiebaThreadReader(client=_client(handler))
    result = reader.read("https://tieba.baidu.com/p/10745250786")

    assert result.status is ReadStatus.ACCESS_RESTRICTED
    assert result.error_code == "tieba_read_access_restricted"
    assert result.forum_name is None
    assert result.forum_matches_target is False
    assert calls == ["1"]


def test_reader_reads_replies_and_reports_pagination_scope() -> None:
    """真实读取：页数、楼层范围、总页数都按实际结果记录。"""
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("pn", "1")
        pages.append(page)
        return httpx.Response(200, text=_page_data(total_page=2))

    reader = HttpTiebaThreadReader(client=_client(handler))
    result = reader.read(
        "https://c.tieba.baidu.com/p/10745250786?lp=home_main_thread_pb"
    )

    assert result.status is ReadStatus.READ
    assert result.url == "https://tieba.baidu.com/p/10745250786"
    assert result.forum_matches_target is True
    assert result.pages_read == 2
    assert result.pages_limit == 2
    assert result.floor_min == 1 and result.floor_max == 2
    assert pages == ["1", "2"]


def test_reader_stops_at_page_limit_and_reports_limits() -> None:
    """分页受限：只读允许的页数，并如实记录页面声明的总页数。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page_data(total_page=50))

    reader = HttpTiebaThreadReader(client=_client(handler), pages_limit=1)
    result = reader.read("https://tieba.baidu.com/p/10745250786")

    assert result.status is ReadStatus.READ
    assert result.pages_read == 1
    assert result.pages_limit == 1
    assert result.total_pages == 50


def test_reader_caps_recorded_replies() -> None:
    """楼层记录有上限，不承诺完整抓取。"""
    posts = [
        {
            "floor": index,
            "time": "2025-05-25 21:03",
            "author": {"name": "甲"},
            "content": f"第{index}层",
        }
        for index in range(1, MAX_REPLIES + 4)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page_data(posts=posts, total_page=1))

    reader = HttpTiebaThreadReader(client=_client(handler))
    result = reader.read("https://tieba.baidu.com/p/10745250786")

    assert len(result.replies) == MAX_REPLIES


def test_reader_reports_timeout_and_other_forum() -> None:
    """超时如实报超时；读到页面但属于其他贴吧时归属不匹配。"""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    reader = HttpTiebaThreadReader(client=_client(timeout_handler))
    timed_out = reader.read("https://tieba.baidu.com/p/1")
    assert timed_out.status is ReadStatus.TIMEOUT
    assert timed_out.error_code == "tieba_read_timeout"

    def other_forum_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page_data(forum="上海交通大学研究生"))

    reader = HttpTiebaThreadReader(client=_client(other_forum_handler))
    other = reader.read("https://tieba.baidu.com/p/2")
    assert other.status is ReadStatus.READ
    assert other.forum_name == "上海交通大学研究生吧"
    assert other.forum_matches_target is False


def test_official_domain_gate() -> None:
    """官方页面只认学校域名（含二级学院子域），其他域名一律不算官方。"""
    assert is_official_url("https://www.ecjtu.edu.cn/xyfw/xxgk1/jbxx.htm") is True
    assert is_official_url("https://jwc.ecjtu.edu.cn/info/1041/4572.htm") is True
    assert is_official_url("https://www.ecjtu.edu.cn.evil.com/x") is False
    assert is_official_url("https://www.zhihu.com/question/1") is False


def test_official_reader_quotes_real_page_text() -> None:
    """官方页面文本里定位到原始名词时给出真实摘录与命中名词。"""
    html = (
        "<html><head><title>转专业管理办法-教务处</title></head><body>"
        "<p>第一章 总则</p><p>学生转专业应当在大一学年结束前提出书面申请，"
        "经所在学院同意后报教务处审批。</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    reader = HttpOfficialSiteReader(client=_client(handler))
    check = reader.fetch(
        "https://jwc.ecjtu.edu.cn/info/1041/4572.htm", terms=("转专业", "审批")
    )

    assert check.status == STATUS_VERIFIED
    assert check.host == "jwc.ecjtu.edu.cn"
    assert "转专业" in (check.excerpt or "")
    assert check.matched_terms == ["转专业", "审批"]
    assert check.title == "转专业管理办法-教务处"
    assert check.fetched_at.tzinfo is not None


def test_official_reader_marks_pages_without_matching_text() -> None:
    """官方页面取到了但没有相关段落：标注未定位，不引用无关内容。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body><p>学校简介</p></body></html>")

    reader = HttpOfficialSiteReader(client=_client(handler))
    check = reader.fetch("https://www.ecjtu.edu.cn/", terms=("转专业",))

    assert check.status == STATUS_EXCERPT_NOT_FOUND
    assert check.excerpt is None


def test_official_reader_refuses_non_official_domain() -> None:
    """非官方域名直接拒收，并说明不作为官方依据。"""
    reader = HttpOfficialSiteReader(client=_client(lambda request: httpx.Response(200)))
    check = reader.fetch("https://www.zhihu.com/question/1", terms=("转专业",))

    assert check.status == STATUS_DOMAIN_REJECTED
    assert check.error_code == "tieba_official_not_official_domain"


def test_official_reader_reports_fetch_failure() -> None:
    """官方页面取不到时如实报失败，不给任何官方结论。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    reader = HttpOfficialSiteReader(client=_client(handler))
    check = reader.fetch("https://www.ecjtu.edu.cn/", terms=("学费",))

    assert check.status == STATUS_FETCH_FAILED
    assert check.error_code == "tieba_official_unavailable"


def test_official_queries_name_original_terms() -> None:
    """官方查询词带原始名词与触发词，且默认限定官方域名。"""
    analysis = parse_tieba_request("华东交通大学吧 转专业 学费 标准")

    assert official_query(analysis).startswith("site:ecjtu.edu.cn")
    assert "转专业" in official_query(analysis)
    assert "site:" not in official_fallback_query(analysis)


def test_extract_page_text_and_locate_excerpt_ignore_markup() -> None:
    """正文提取去掉脚本与标签，摘录只取命中原词的真实窗口。"""
    text = extract_page_text(
        "<html><script>var a=1</script><p>第一条</p><p>学费标准为每年五千元</p></html>"
    )

    excerpt, matched = locate_excerpt(text, ("学费",))
    assert excerpt is not None and "每年五千元" in excerpt
    assert matched == ("学费",)
    assert "var a=1" not in text


class _FakeWebSearchProjection:
    def __init__(self, results: list[object], status: object) -> None:
        self.results = results
        self.status = status
        self.searched_at = datetime(2026, 9, 25, tzinfo=UTC)
        self.error_code = None
        self.error_message = None
        self.can_retry = False


class _FakeResult:
    def __init__(self, url: str, title: str, content: str) -> None:
        self.url = url
        self.title = title
        self.content = content


class _FakeWebSearch:
    """允许的搜索服务的替身：记录收到的查询词并返回可控结果。"""

    def __init__(self, results: list[object], status_name: str = "fetch_error") -> None:
        self.queries: list[str] = []
        self._results = results
        self._status = type("S", (), {"value": status_name})()

    def search(self, account_id: str, plan: object, **_: object) -> object:
        del account_id
        self.queries.append(plan.query)  # type: ignore[attr-defined]
        return _FakeWebSearchProjection(self._results, self._status)


def test_adapter_sends_exact_minimal_query_and_maps_pages_unfetched() -> None:
    """适配器逐字发送本模块构造的查询词；结果页抓不到不算检索失败。"""
    results = [
        _FakeResult(
            "https://c.tieba.baidu.com/p/10745250786?lp=home_main_thread_pb",
            "东北电力和华东交通哪个好",
            "华东交通大学吧. 关注18.9w贴子786.1w. App内查看.",
        ),
        _FakeResult(
            "https://c.tieba.baidu.com/p/5668552372",
            "上海交通大学研究生吧",
            "上海交通大学研究生吧・ 华东交通大学吧关注19w",
        ),
    ]
    service = _FakeWebSearch(results)
    adapter = WebSearchServiceAdapter(service)

    outcome = adapter.search_public(
        "account-1",
        query="tieba.baidu.com 华东交通大学吧 转专业",
        reason="贴吧信息搜集",
    )

    assert service.queries == ["tieba.baidu.com 华东交通大学吧 转专业"]
    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.record.evidence_count == 2
    assert outcome.record.detail is not None and "正文" in outcome.record.detail
    assert [hit.url for hit in outcome.candidates] == [
        "https://tieba.baidu.com/p/10745250786"
    ]
    assert len(outcome.rejected) == 1
    assert outcome.hits[1].title == "上海交通大学研究生吧"


def test_adapter_maps_empty_and_error_statuses() -> None:
    """零结果与失败分别映射为零结果与可重试错误记录。"""
    empty = WebSearchServiceAdapter(_FakeWebSearch([], status_name="empty"))
    outcome = empty.search_public(
        "account-1", query="tieba.baidu.com 华东交通大学吧 冷门话题", reason="贴吧信息搜集"
    )
    assert outcome.record.status is ModuleQueryStatus.EMPTY
    assert outcome.candidates == ()

    failure = WebSearchServiceAdapter(_FakeWebSearch([], status_name="error"))
    outcome = failure.search_public(
        "account-1", query="tieba.baidu.com 华东交通大学吧 话题", reason="贴吧信息搜集"
    )
    assert outcome.record.status is ModuleQueryStatus.ERROR
