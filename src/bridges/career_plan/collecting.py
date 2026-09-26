"""``career.collect``：只读取可公开访问的职位卡与详情。

读取是普通的公开 HTTP GET：不做任何登录、Cookie 伪造或签名绕过，被访问限制
挡住就如实记成受限，并按「未核实链接」降级。页面解析按页面自己公开的结构
防御式进行——优先 ``application/ld+json`` 里的 ``JobPosting``（页面自己声明的
结构化岗位数据），其次常见的元数据与职位卡标记；找不到可用结构时返回不可
识别，绝不用猜测内容填补。

发布日期优先取页面原文，绝对日期与相对说法（「3天前」）都按抓取时间换算成
真实日期，同时把原文逐字保留；换算不出来就留空，不臆造。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Any, Protocol

import httpx

from bridges.career_plan.contracts import JobReadStatus
from bridges.career_plan.lexicon import CITY_TERMS, detect_experience, detect_skills

#: 单个岗位页的读取墙钟上限。
READ_DEADLINE_SECONDS = 8.0

#: 单次响应体上限，避免异常页面拖垮进程。
READ_MAX_BYTES = 800_000

#: 记入能力要求的最大条目数与单条字符数。
MAX_REQUIREMENTS = 15
MAX_REQUIREMENT_CHARS = 200

#: 发布日期超过这些天视为过期岗位（过滤节点据此剔除并写明依据）。
STALE_DAYS = 90

#: 公开页面读取使用的普通浏览器标识（不携带任何账户信息）。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: 访问限制页面的特征：只收整句的墙页文案（「验证码」这类泛词会出现在正常
#: 岗位页的脚本里，不能当受限依据）。
_RESTRICTED_MARKERS: tuple[str, ...] = (
    "请先登录",
    "登录后查看",
    "登录后才能查看",
    "安全验证",
    "访问过于频繁",
    "人机验证",
    "访问被拒绝",
    "百度安全验证",
)

#: 岗位已下线的特征（过期剔除的直接证据）。
_EXPIRED_MARKERS: tuple[str, ...] = (
    "职位已下线",
    "该职位已结束",
    "职位已关闭",
    "停止招聘",
    "该职位已过期",
    "职位不存在或已下线",
    "岗位已关闭",
)

#: 职位卡的职责／要求段标记（判断这页是不是真的岗位详情）。
_DUTY_MARKERS: tuple[str, ...] = (
    "岗位职责",
    "职位描述",
    "工作职责",
    "任职要求",
    "岗位要求",
    "任职资格",
    "职位要求",
    "Job Description",
    "Responsibilities",
    "Requirements",
)

#: 薪资文本特征（判断这页是不是岗位卡）。
_SALARY_HINT = re.compile(r"\d+\s*[-~—]\s*\d+\s*[Kk千万]|\d+\s*[Kk]\s*[·・]?\s*\d*\s*薪|面议")

#: 岗位名尾缀（标题像岗位名的判断）。
_TITLE_TAIL = re.compile(
    r"(工程师|开发|实习生?|助理|专员|主管|经理|总监|设计师|分析师|研究员|架构师|"
    r"教师|讲师|顾问|运营|销售|会计|出纳|翻译|编辑|记者)"
)

_LD_JSON = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v\u3000]+")
_TITLE_TAG = re.compile(r"<title[^>]*>(?P<body>.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_TAG = re.compile(r"<h1[^>]*>(?P<body>.*?)</h1>", re.IGNORECASE | re.DOTALL)
_META = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"'](?P<key>[^\"']+)[\"'][^>]*"
    r"content\s*=\s*[\"'](?P<value>[^\"']*)[\"']",
    re.IGNORECASE,
)

#: 绝对日期（2026-09-20／2026/09/20／2026年9月20日）。
_ABS_DATE = re.compile(r"(?P<y>20\d{2})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})")
#: 只有月日的写法（09-20发布）。
_MD_DATE = re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})(?!\d)")
#: 相对日期说法。
_REL_DATE = re.compile(r"(?P<n>\d+)\s*(?P<unit>天|日|小时|周|个月)(?:前|以前)?")
#: ISO 日期（JSON-LD 的 datePosted）。
_ISO_DATE = re.compile(r"(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})")

#: 单元文本 → 计薪单位（JSON-LD 的 ``unitText``）。
_UNIT_TEXT_LABELS: dict[str, str] = {
    "HOUR": "元/时",
    "DAY": "元/天",
    "WEEK": "元/周",
    "MONTH": "元/月",
    "YEAR": "元/年",
}


@dataclass(frozen=True)
class ParsedJobPage:
    """一次岗位页解析出来的真实字段（取不到的一律为 None／空）。"""

    title: str | None = None
    company: str | None = None
    city: str | None = None
    salary_raw: str | None = None
    published_raw: str | None = None
    published_date: date | None = None
    experience: str | None = None
    education: str | None = None
    requirements: tuple[str, ...] = ()
    is_job_posting: bool = False
    expired: bool = False
    expired_evidence: str | None = None
    structure_found: bool = False
    structure_note: str | None = None


@dataclass(frozen=True)
class JobPageReadResult:
    """一次岗位页读取的真实结果。"""

    url: str
    status: JobReadStatus
    page: ParsedJobPage = field(default_factory=ParsedJobPage)
    error_code: str | None = None
    error_message: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def readable(self) -> bool:
        """是否真的读到了页面内容（含结构未识别的页面）。"""
        return self.status in {JobReadStatus.READ, JobReadStatus.PARTIAL}


class JobPageReader(Protocol):
    """岗位页读取边界（测试可注入替身，不发起真实请求）。"""

    def read(
        self,
        url: str,
        *,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> JobPageReadResult: ...


class HttpJobPageReader:
    """公开岗位页读取实现（httpx，固定 UA，有界体积与时间）。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_bytes: int = READ_MAX_BYTES,
        timeout: float = READ_DEADLINE_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        self._max_bytes = max_bytes

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def read(
        self,
        url: str,
        *,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> JobPageReadResult:
        target = url.strip()
        retrieved_at = datetime.now(UTC)
        if _stopped(stop_event):
            return _failure(
                target, JobReadStatus.CANCELLED, "career_read_cancelled",
                "已停止读取，未继续读取该岗位页。", retrieved_at,
            )
        timeout = READ_DEADLINE_SECONDS
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _failure(
                    target, JobReadStatus.TIMEOUT, "career_read_timeout",
                    "读取超时，未取得岗位页内容。", retrieved_at,
                )
            timeout = max(0.5, min(timeout, remaining))
        try:
            response = self._client.get(target, timeout=timeout)
        except httpx.TimeoutException:
            return _failure(
                target, JobReadStatus.TIMEOUT, "career_read_timeout",
                "读取超时，未取得岗位页内容。", retrieved_at,
            )
        except httpx.HTTPError:
            return _failure(
                target, JobReadStatus.ERROR, "career_read_unavailable",
                "当前无法连接该岗位页，未取得内容。", retrieved_at,
            )
        status_code = response.status_code
        if status_code in {401, 403}:
            return _failure(
                target, JobReadStatus.ACCESS_RESTRICTED, "career_read_access_restricted",
                "岗位页要求登录或触发了访问验证，未取得岗位内容。", retrieved_at,
            )
        if status_code == 404:
            return _failure(
                target, JobReadStatus.NOT_FOUND, "career_read_not_found",
                "该岗位页不存在或已下线。", retrieved_at,
            )
        if status_code >= 400:
            return _failure(
                target, JobReadStatus.ERROR, "career_read_unavailable",
                f"岗位页返回 HTTP {status_code}，未取得内容。", retrieved_at,
            )
        body = _bounded_text(response, self._max_bytes)
        if _restricted(body):
            return _failure(
                target, JobReadStatus.ACCESS_RESTRICTED, "career_read_access_restricted",
                "岗位页是访问验证／登录提示，未取得岗位内容。", retrieved_at,
            )
        page = parse_job_page(body, reference=retrieved_at)
        if not page.structure_found:
            return JobPageReadResult(
                url=target,
                status=JobReadStatus.UNRECOGNIZED,
                page=page,
                error_code="career_read_unrecognized",
                error_message="岗位页结构与预期不符，未取得可核实的岗位内容。",
                retrieved_at=retrieved_at,
            )
        return JobPageReadResult(
            url=target, status=JobReadStatus.READ, page=page, retrieved_at=retrieved_at
        )


def _failure(
    url: str, status: JobReadStatus, code: str, message: str, retrieved_at: datetime
) -> JobPageReadResult:
    return JobPageReadResult(
        url=url,
        status=status,
        error_code=code,
        error_message=message,
        retrieved_at=retrieved_at,
    )


def _stopped(stop_event: object | None) -> bool:
    checker = getattr(stop_event, "is_set", None)
    return bool(checker()) if callable(checker) else False


def _bounded_text(response: httpx.Response, max_bytes: int) -> str:
    raw = response.content[:max_bytes]
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _restricted(body: str) -> bool:
    head = body[:4000]
    return any(marker in head for marker in _RESTRICTED_MARKERS)


def _expired_evidence(body: str) -> str | None:
    for marker in _EXPIRED_MARKERS:
        if marker in body:
            return f"岗位页出现「{marker}」，判定为已过期／已下线。"
    return None


def parse_job_page(html: str, *, reference: datetime) -> ParsedJobPage:
    """解析岗位页；优先页面自己的 ``JobPosting`` 结构化数据。"""
    posting = _job_posting(html)
    if posting is not None:
        return _from_posting(posting, html=html, reference=reference)
    return _from_metadata(html, reference=reference)


def _job_posting(html: str) -> dict[str, Any] | None:
    """取 ``application/ld+json`` 里的 ``JobPosting`` 对象。"""
    for match in _LD_JSON.finditer(html):
        raw = match.group("body").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _find_posting(data)
        if found is not None:
            return found
    return None


def _find_posting(data: object) -> dict[str, Any] | None:
    if isinstance(data, dict):
        if _is_posting(data):
            return data
        for key in ("@graph", "itemListElement", "item"):
            if key in data:
                found = _find_posting(data[key])
                if found is not None:
                    return found
        return None
    if isinstance(data, list):
        for item in data:
            found = _find_posting(item)
            if found is not None:
                return found
    return None


def _is_posting(data: dict[str, Any]) -> bool:
    value = data.get("@type")
    if isinstance(value, list):
        return any(str(item).casefold() == "jobposting" for item in value)
    return str(value or "").casefold() == "jobposting"


def _from_posting(
    posting: dict[str, Any], *, html: str, reference: datetime
) -> ParsedJobPage:
    title = _text(posting.get("title"))
    company = _organization_name(posting.get("hiringOrganization"))
    city = _posting_city(posting.get("jobLocation"))
    salary_raw = _posting_salary(posting.get("baseSalary"))
    posted = _text(posting.get("datePosted"))
    published_raw = posted
    published_date = _parse_iso_date(posted)
    if published_date is None:
        published_date = _parse_date(posted, reference=reference)
    experience = _text(posting.get("experienceRequirements")) or None
    education = _education_text(posting.get("educationRequirements"))
    requirements = _requirement_lines(_html_to_text(_text(posting.get("description")) or ""))
    expired_evidence = _expired_evidence(html)
    valid_through = _parse_iso_date(_text(posting.get("validThrough")))
    expired = False
    if expired_evidence is not None:
        expired = True
    elif valid_through is not None and valid_through < reference.date():
        expired = True
        expired_evidence = f"岗位页声明有效期至 {valid_through.isoformat()}，已过期。"
    return ParsedJobPage(
        title=title or None,
        company=company,
        city=city,
        salary_raw=salary_raw,
        published_raw=published_raw,
        published_date=published_date,
        experience=experience,
        education=education,
        requirements=requirements,
        is_job_posting=bool(title),
        expired=expired,
        expired_evidence=expired_evidence,
        structure_found=bool(title),
        structure_note="取自页面自身的 JobPosting 结构化数据",
    )


def _from_metadata(html: str, *, reference: datetime) -> ParsedJobPage:
    """没有结构化数据时，只认页面自己的标题／元数据与职位卡标记。"""
    meta = {
        match.group("key").casefold(): unescape(match.group("value")).strip()
        for match in _META.finditer(html)
    }
    og_title = meta.get("og:title") or meta.get("twitter:title") or ""
    document_title = _clean_text(_first_group(_TITLE_TAG, html))
    heading = _clean_text(_first_group(_H1_TAG, html))
    title = _clean_text(og_title) or heading or document_title
    body_text = _html_to_text(html)
    has_duty = any(marker in body_text for marker in _DUTY_MARKERS)
    has_salary = _SALARY_HINT.search(body_text) is not None
    looks_like_job = bool(title) and _TITLE_TAIL.search(title) is not None
    structure_found = bool(title) and (has_duty or has_salary or looks_like_job)
    published_raw, published_date = _published_from_text(body_text, reference=reference)
    expired_evidence = _expired_evidence(body_text) or _expired_evidence(html)
    return ParsedJobPage(
        title=title or None,
        # 公司名只在页面自己声明雇主时才有（结构化数据里的 hiringOrganization）；
        # 元数据里的 og:site_name 是**站点名**（招聘网站自己的名字），不是雇主，
        # 拿它当公司名会误导用户，也会让去重键把不同公司的岗位判成重复。
        company=None,
        city=_city_from_text(title),
        salary_raw=_salary_hint_text(body_text),
        published_raw=published_raw,
        published_date=published_date,
        experience=detect_experience(body_text),
        education=_education_from_text(body_text),
        requirements=_requirement_lines(body_text),
        is_job_posting=structure_found and has_duty,
        expired=expired_evidence is not None,
        expired_evidence=expired_evidence,
        structure_found=structure_found,
        structure_note=(
            "取自页面的元数据／标题与职位卡标记"
            if structure_found
            else "页面没有可用的岗位结构"
        ),
    )


def _first_group(pattern: re.Pattern[str], html: str) -> str:
    match = pattern.search(html)
    return match.group("body") if match is not None else ""


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@value") or "")
    if isinstance(value, list):
        for item in value:
            found = _text(item)
            if found:
                return found
    return ""


def _organization_name(value: object) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("name")) or None
    return _text(value) or None


def _posting_city(value: object) -> str | None:
    """从 ``jobLocation`` 里取城市（省／市只取最具体的一级）。"""
    locations = value if isinstance(value, list) else [value]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, dict):
            city = _text(address.get("addressLocality")) or _text(
                address.get("addressRegion")
            )
            if city:
                return city
        name = _text(location.get("name"))
        if name:
            return name
    return None


def _posting_salary(value: object) -> str | None:
    """把页面自己声明的结构化薪资渲染成中文原文（附来源标注）。"""
    if not isinstance(value, dict):
        return None
    amount = value.get("value")
    if not isinstance(amount, dict):
        amount = value
    low = _num(amount.get("minValue"))
    high = _num(amount.get("maxValue"))
    single = _num(amount.get("value"))
    unit_label = _UNIT_TEXT_LABELS.get(str(amount.get("unitText") or "").upper(), "")
    if low is None and high is None and single is not None:
        low = high = single
    if low is None and high is None:
        return None
    span = f"{low:g}" if low == high else f"{low:g}-{high:g}"
    suffix = f" {unit_label}" if unit_label else ""
    return f"{span}{suffix}（页面声明的结构化薪资）"


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _education_text(value: object) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("credentialCategory")) or None
    return _text(value) or None


def _education_from_text(text: str) -> str | None:
    for term in ("博士", "硕士", "本科", "大专", "中专", "高中", "学历不限"):
        if term in text:
            return term
    return None


def _city_from_text(title: str) -> str | None:
    """从标题里取城市（如「Java后端开发-南昌-8-13K」）。"""
    for city in CITY_TERMS:
        if city in title:
            return city
    return None


def _salary_hint_text(text: str) -> str | None:
    """取命中薪资的那一整行原文。

    行是页面自身的文本边界，不会像定长窗口那样切出半个词；跨行拼接又容易把
    相邻岗位的薪资带进来，所以只保留命中行。
    """
    match = _SALARY_HINT.search(text)
    if match is None:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip() or None


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAG.sub("\n", text)
    text = unescape(text)
    lines = [_clean_line(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _clean_text(value: str) -> str:
    text = unescape(_TAG.sub(" ", value))
    return _WHITESPACE.sub(" ", text).strip()


def _clean_line(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _requirement_lines(text: str) -> tuple[str, ...]:
    """只保留像能力要求的行（原文，最多 ``MAX_REQUIREMENTS`` 条）。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•*·◆●○■□▶▪ ").strip()
        if len(line) < 4:
            continue
        if len(line) > MAX_REQUIREMENT_CHARS:
            line = line[:MAX_REQUIREMENT_CHARS]
        lines.append(line)
        if len(lines) >= MAX_REQUIREMENTS:
            break
    return tuple(lines)


def skills_of(requirements: list[str] | tuple[str, ...]) -> list[str]:
    """从能力要求原文里提取技能关键词（只认词表内的词）。"""
    return detect_skills("\n".join(requirements))


def _published_from_text(
    text: str, *, reference: datetime
) -> tuple[str | None, date | None]:
    """从页面正文里取发布日期原文并换算（换算不出来就只留原文）。"""
    for line in text.splitlines():
        has_date = bool(
            _ABS_DATE.search(line) or _MD_DATE.search(line) or _REL_DATE.search(line)
        )
        if has_date and any(
            hint in line for hint in ("发布", "更新", "上线", "招聘", "日期")
        ):
            return line.strip(), _parse_date(line, reference=reference)
    match = _ABS_DATE.search(text)
    if match is not None:
        return match.group(0), _parse_date(match.group(0), reference=reference)
    return None, None


def _parse_date(raw: str | None, *, reference: datetime) -> date | None:
    """解析页面上的日期原文；相对说法按抓取时间换算，换算不来返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    absolute = _ABS_DATE.search(text)
    if absolute is not None:
        return _safe_date(
            int(absolute.group("y")), int(absolute.group("m")), int(absolute.group("d"))
        )
    if "今天" in text or "刚刚" in text or "今日" in text:
        return reference.date()
    if "昨天" in text:
        return reference.date() - timedelta(days=1)
    relative = _REL_DATE.search(text)
    if relative is not None:
        count = int(relative.group("n"))
        unit = relative.group("unit")
        if unit in {"天", "日"}:
            return reference.date() - timedelta(days=count)
        if unit == "小时":
            return reference.date() if count < 24 else reference.date() - timedelta(
                days=count // 24
            )
        if unit == "周":
            return reference.date() - timedelta(weeks=count)
        if unit == "个月":
            return reference.date() - timedelta(days=count * 30)
    month_day = _MD_DATE.search(text)
    if month_day is not None:
        # 只有月日时按抓取时间所在年份解读；若落在未来则退回上一年。
        candidate = _safe_date(
            reference.year, int(month_day.group("m")), int(month_day.group("d"))
        )
        if candidate is None:
            return None
        if candidate > reference.date():
            candidate = _safe_date(
                reference.year - 1, int(month_day.group("m")), int(month_day.group("d"))
            )
        return candidate
    return None


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    match = _ISO_DATE.search(raw)
    if match is None:
        return None
    return _safe_date(int(match.group("y")), int(match.group("m")), int(match.group("d")))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_stale(published_date: date | None, *, reference: datetime) -> bool:
    """发布日期是否已超过过期阈值（无日期时不算过期，如实留空）。"""
    if published_date is None:
        return False
    return (reference.date() - published_date).days > STALE_DAYS
