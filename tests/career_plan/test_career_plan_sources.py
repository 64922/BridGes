"""Issue 15 来源合同：岗位页解析、读取分类与日期换算。

只用页面自己公开的结构（``JobPosting`` 结构化数据、元数据、职位卡标记）；
解析不出来就如实记「不可识别」，绝不猜测填充。读取分类覆盖访问受限、不存在、
超时、非岗位页与已过期。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from bridges.career_plan.collecting import (
    HttpJobPageReader,
    parse_job_page,
    skills_of,
)
from bridges.career_plan.contracts import JobReadStatus

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

JOB_POSTING_HTML = """
<html><head><title>Java后端开发工程师-南昌-某某科技</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Java后端开发工程师",
  "datePosted": "2026-09-20",
  "validThrough": "2026-12-31",
  "hiringOrganization": {"@type": "Organization", "name": "某某科技有限公司"},
  "jobLocation": {"@type": "Place", "address": {
     "@type": "PostalAddress", "addressLocality": "南昌", "addressRegion": "江西省"}},
  "baseSalary": {"@type": "MonetaryAmount", "currency": "CNY",
     "value": {"@type": "QuantitativeValue", "minValue": 15000,
               "maxValue": 25000, "unitText": "MONTH"}},
  "experienceRequirements": "1-3年",
  "educationRequirements": {"@type": "EducationalOccupationalCredential",
     "credentialCategory": "本科"},
  "description": "<p>服务端接口开发与维护</p><p>熟悉 Java、Spring Boot、MySQL、Redis、Docker</p>"
}
</script></head><body></body></html>
"""

INDEX_HTML = """
<html><head><title>招聘 - 某某公司官网</title></head>
<body><h1>加入我们</h1><p>我们在招很多岗位，请点击查看。</p></body></html>
"""


def test_json_ld_job_posting_is_parsed_faithfully() -> None:
    page = parse_job_page(JOB_POSTING_HTML, reference=NOW)
    assert page.structure_found and page.is_job_posting
    assert page.title == "Java后端开发工程师"
    assert page.company == "某某科技有限公司"
    assert page.city == "南昌"
    assert page.salary_raw and "15000-25000 元/月" in page.salary_raw
    assert "结构化薪资" in page.salary_raw, "结构化薪资必须标注来源，不当成页面原文"
    assert page.published_raw == "2026-09-20"
    assert page.published_date == date(2026, 9, 20)
    assert page.experience == "1-3年"
    assert page.education == "本科"
    assert any("Spring Boot" in line for line in page.requirements)
    assert page.expired is False
    assert page.structure_note and "JobPosting" in page.structure_note


def test_skills_come_only_from_requirement_text() -> None:
    page = parse_job_page(JOB_POSTING_HTML, reference=NOW)
    skills = skills_of(page.requirements)
    assert "Java" in skills and "MySQL" in skills and "Redis" in skills
    assert "Docker" in skills
    assert "Kubernetes" not in skills, "词表外的技能不得凭印象补上"


def test_metadata_fallback_marks_job_posting_only_with_duty_text() -> None:
    """只有标题没有职责／薪资的页面不算岗位详情页。"""
    index = parse_job_page(INDEX_HTML, reference=NOW)
    assert index.is_job_posting is False
    card_html = """
    <html><head><title>测试工程师</title>
    <meta property="og:title" content="测试工程师" />
    <meta property="og:site_name" content="某某科技" /></head>
    <body><h2>9-12K</h2><p>岗位职责：负责产品测试与质量保障</p>
    <p>任职要求：熟悉自动化测试</p></body></html>
    """
    card = parse_job_page(card_html, reference=NOW)
    assert card.is_job_posting is True
    assert card.title == "测试工程师"
    assert card.company == "某某科技"


def test_page_without_usable_structure_is_unrecognized() -> None:
    page = parse_job_page("<html><body>nothing here</body></html>", reference=NOW)
    assert page.structure_found is False
    assert page.is_job_posting is False


def test_salary_raw_is_the_matching_line_not_a_cut_window() -> None:
    """薪资原文取命中行的整行：不切出半个词，也不把相邻岗位的薪资带进来。"""
    html = """
    <html><head><title>后端开发工程师</title>
    <meta property="og:title" content="后端开发工程师" /></head>
    <body><h2>18-26K * 16薪</h2>
    <p>岗位职责：负责服务端接口开发</p>
    <p>薪资范围：面议</p></body></html>
    """
    page = parse_job_page(html, reference=NOW)
    assert page.salary_raw == "18-26K * 16薪", "命中行只保留该行原文"
    assert "面议" not in (page.salary_raw or ""), "不得跨行拼接其他薪资说法"


def test_salary_raw_keeps_the_whole_line_when_salary_sits_mid_line() -> None:
    html = """
    <html><head><title>后端开发工程师</title>
    <meta property="og:title" content="后端开发工程师" /></head>
    <body><p>薪资待遇：15-25K·15薪 五险一金</p>
    <p>岗位职责：负责服务端接口开发</p></body></html>
    """
    page = parse_job_page(html, reference=NOW)
    assert page.salary_raw == "薪资待遇：15-25K·15薪 五险一金"


def test_expired_marker_is_recorded_with_evidence() -> None:
    html = JOB_POSTING_HTML.replace("<body></body>", "<body>职位已下线</body>")
    page = parse_job_page(html, reference=NOW)
    assert page.expired is True
    assert page.expired_evidence and "职位已下线" in page.expired_evidence


def test_valid_through_in_the_past_marks_expired() -> None:
    html = JOB_POSTING_HTML.replace('"validThrough": "2026-12-31"', '"validThrough": "2026-01-31"')
    page = parse_job_page(html, reference=NOW)
    assert page.expired is True
    assert page.expired_evidence and "有效期" in page.expired_evidence


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-20", date(2026, 9, 20)),
        ("2026年9月20日发布", date(2026, 9, 20)),
        ("今天发布", date(2026, 9, 26)),
        ("昨天更新", date(2026, 9, 25)),
        ("3天前发布", date(2026, 9, 23)),
        ("1周前发布", date(2026, 9, 19)),
        ("09-20发布", date(2026, 9, 20)),
    ],
)
def test_publish_date_conversion_keeps_original_text(raw: str, expected: date) -> None:
    page = parse_job_page(JOB_POSTING_HTML, reference=NOW)
    assert page.published_raw, "原文必须保留"
    from bridges.career_plan.collecting import _parse_date  # noqa: PLC0415

    assert _parse_date(raw, reference=NOW) == expected


def test_future_month_day_falls_back_to_previous_year() -> None:
    from bridges.career_plan.collecting import _parse_date  # noqa: PLC0415

    assert _parse_date("12-01发布", reference=NOW) == date(2025, 12, 1)
    assert _parse_date("没有日期", reference=NOW) is None


class _FakeClient:
    """httpx.Client 的最小替身：按脚本返回状态码与响应体。"""

    def __init__(self, *, status_code: int = 200, body: str = "") -> None:
        self._status_code = status_code
        self._body = body
        self.calls: list[str] = []

    def get(self, url: str, timeout: float | None = None) -> httpx.Response:
        del timeout
        self.calls.append(url)
        return httpx.Response(
            status_code=self._status_code,
            content=self._body.encode("utf-8"),
            request=httpx.Request("GET", url),
        )


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def get(self, url: str, timeout: float | None = None) -> httpx.Response:
        del url, timeout
        raise self._error


def _reader(client: object) -> HttpJobPageReader:
    return HttpJobPageReader(client=client)  # type: ignore[arg-type]


def test_public_read_returns_parsed_job_page() -> None:
    reader = _reader(_FakeClient(body=JOB_POSTING_HTML))
    result = reader.read("https://careers.example.com/job/1")
    assert result.status is JobReadStatus.READ
    assert result.readable is True
    assert result.page.title == "Java后端开发工程师"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (403, JobReadStatus.ACCESS_RESTRICTED),
        (401, JobReadStatus.ACCESS_RESTRICTED),
        (404, JobReadStatus.NOT_FOUND),
        (500, JobReadStatus.ERROR),
    ],
)
def test_http_statuses_map_to_honest_read_status(
    status_code: int, expected: JobReadStatus
) -> None:
    result = _reader(_FakeClient(status_code=status_code)).read("https://a/1")
    assert result.status is expected
    assert result.error_message, "每种失败都要给可读的中文原因"
    assert result.page.title is None, "失败时不得产出岗位字段"


def test_login_wall_text_is_detected_without_bypassing_it() -> None:
    client = _FakeClient(body="<html><body>请先登录后查看该职位</body></html>")
    result = _reader(client).read("https://www.zhipin.com/job_detail/x.html")
    assert result.status is JobReadStatus.ACCESS_RESTRICTED
    assert len(client.calls) == 1, "被访问限制挡住时只发一次请求，不做绕过尝试"


def test_unrecognized_page_keeps_status_without_content() -> None:
    result = _reader(_FakeClient(body="<html><body>hello</body></html>")).read(
        "https://a/1"
    )
    assert result.status is JobReadStatus.UNRECOGNIZED
    assert result.page.title is None


def test_timeout_is_recorded_as_timeout() -> None:
    result = _reader(_FailingClient(httpx.TimeoutException("slow"))).read("https://a/1")
    assert result.status is JobReadStatus.TIMEOUT


def test_connection_error_is_recorded_as_error() -> None:
    result = _reader(_FailingClient(httpx.ConnectError("nope"))).read("https://a/1")
    assert result.status is JobReadStatus.ERROR


def test_expired_deadline_skips_the_request_entirely() -> None:
    client = _FakeClient(body=JOB_POSTING_HTML)
    result = _reader(client).read("https://a/1", deadline=0.0)
    assert result.status is JobReadStatus.TIMEOUT
    assert client.calls == [], "预算已耗尽时不应再发出真实请求"


def test_stop_signal_cancels_before_request() -> None:
    import threading  # noqa: PLC0415

    client = _FakeClient(body=JOB_POSTING_HTML)
    stop = threading.Event()
    stop.set()
    result = _reader(client).read("https://a/1", stop_event=stop)
    assert result.status is JobReadStatus.CANCELLED
    assert client.calls == []
