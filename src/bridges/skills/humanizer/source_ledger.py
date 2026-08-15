"""来源账本与保真硬门（Issue 02）。

把「原事实是否还在」加深为版本化来源账本与双向保真硬门：生成前编译
账本（来源条目、内容哈希、允许用途、保护 span、事实/专名/数字/日期/
公式/URL/引语/方向/限定词/结论强度/亲历），生成后既检查原有信息是否
被破坏（保留检查），也检查候选新增可核查 claim 是否有授权来源（新增
检查）。关键失败返回稳定硬失败，非关键无法判定返回
``needs_user_confirmation``；账本版本不受支持、账本哈希不一致或候选
为空时失败关闭。本模块不调用模型，全部确定性、可测试。

审计约定：账本与检查摘要只记录 ID、类型、哈希、计数、位置与失败码，
不把私人正文写入普通日志。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

from bridges.contracts.humanizer import (
    FIDELITY_CHECKER_VERSION,
    SOURCE_LEDGER_VERSION,
    FidelityCheckResult,
    FidelityFailure,
    FidelityFailureCode,
    FidelitySeverity,
    FidelitySummary,
    HumanizerPath,
    LedgerCompileSummary,
    PersonalExperienceMode,
    ProtectedSpan,
    ProtectedSpanKind,
    SourceEntry,
    SourceLedger,
    SourceType,
    SourceUsage,
    SpanLocation,
)
from bridges.skills.humanizer.factlock import (
    _CITATION_RE,
    _FORMULA_RE,
    _NUMBER_CORE,
    _NUMBER_UNIT_RE,
    _QUALIFIER_RE,
    _RELATION_OPPOSITES,
    _RELATION_RE,
    _STRENGTH_RE,
    _UNIT_TOKENS,
    _canonical_formula,
    _canonical_number,
    _canonical_text_key,
    _canonical_unit,
    _normalize_text,
    _strength_tier,
)

# ---------------------------------------------------------------------------
# 内容哈希
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 提取正则（统一在 _normalize_text 之后运行）
# ---------------------------------------------------------------------------

#: Issue 01 缺陷 c：数字+单位紧邻汉字/字母（如「Transformer2017年」「团队2017年」）
#: 时，factlock 的严格 ``_NUMBER_UNIT_RE`` 因左侧 lookbehind 拒绝匹配，导致
#: 「2017 年/2017年」空白变体提取不一致；宽松变体去掉左侧 lookbehind（只拒绝
#: 数字紧邻，避免截断更长数字），仍保留单位与右侧边界，保证规范化键对空白
#: 与全半角变体稳定。
_LOOSE_NUMBER_UNIT_RE = re.compile(
    r"(?<![0-9])(" + _NUMBER_CORE + r")\s*(" + _UNIT_TOKENS + r")"
    r"([A-Za-zμΩ°℃℉%0-9\-()/·（）\s]+)?(?![A-Za-z0-9])"
)

_URL_RE = re.compile(r"https?://[^\s，。；：、！？）)】」』“”\"']+")
_CODE_BLOCK_RE = re.compile(r"```.+?```", re.DOTALL)
_QUOTE_RE = re.compile(
    r"(?:“([^”]{1,200})”|\"([^\"]{1,200})\"|「([^」]{1,200})」|"
    r"『([^』]{1,200})』|'([^']{1,200})')"
)
# factlock 的引用编号模式不接受「[3, 5-6]」这类带空格的编号，这里补充
_NUMBER_CITE_RE = re.compile(r"\[(?:\d+\s*(?:[-–,，]\s*\d+)*)\]")
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"
)
_YEAR_RE = re.compile(r"(?<![\d一-鿿])(?:19|20)\d{2}(?![\d一-鿿])")
_DATE_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}\s*[-—–至]\s*(?:19|20)\d{2}\s*年?"
)

# 专名：组织后缀 / 头衔 / 书名号 / 连续英文专名
_PN_ORG_RE = re.compile(
    r"[一-鿿A-Za-z0-9]{2,14}(?:大学|学院|研究所|研究院|公司|集团|医院|"
    r"实验室|委员会|协会|学会|中心|科学院|银行|出版社)"
)
_PN_TITLE_RE = re.compile(
    r"[一-鿿]{2,4}(?:教授|博士|院士|研究员|医生|工程师|老师|经理|主任|"
    r"校长|院长|所长|作家|记者)"
)
_PN_BOOK_RE = re.compile(r"《([^》]{1,30})》")
_PN_EN_RE = re.compile(r"\b[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})+\b")

# 否定与因果方向（双字动词「吸烟」优先于 {1,3} 前缀，避免被贪婪吃掉）
_NEGATION_RE = re.compile(
    r"(不|没|未|非|别|无|并非|不再)(?:会|能|必|曾|再)?"
    r"((?:吸烟|[一-鿿]{1,3}(?:影响|导致|引起|促进|抑制|降低|增加|提高|改变|"
    r"决定|减缓|加速|诱发|出现|存在|支持|证明|符合|适用|参与|采用)))"
)

# 时间地点与惯例时间（惯例时间不构成 claim）
_ROUTINE_TIME_RE = re.compile(
    r"(?:每周[一-日]|每个[一-日]|每天|每月|每日|每旬)"
)
_RELATIVE_TIME_RE = re.compile(
    r"(?:上周[一-日]|上周|上个月|上月|去年|前年|昨天|前天|大前天|今年|"
    r"本月|上旬|中旬|下旬|过去几年|近几年|前几年)"
)
_VAGUE_TIME_RE = re.compile(r"(?:最近|近期|日前|近来|最近一段时间|近段时间)")
_PLACE_RE = re.compile(
    r"在[一-鿿]{2,6}?(?:省|市|县|区|镇|乡|村|校区|园区|基地|现场|车间|"
    r"实验室|办公室|工厂|工地)"
)

# 事件：朋友/同事/熟人传达的外部消息（无来源即视为新增 claim）
_EVENT_RE = re.compile(
    r"(?:朋友|同事|同学|熟人|家人|亲戚|网友|身边人|别人)"
    r"(?:告诉|提到|发消息|转发|分享|说|讲过|发来)"
)

# 样本 / 功能 / 研究方法（无来源即视为新增 claim）
_SAMPLE_RE = re.compile(
    r"(?:收集|采集|纳入|招募|调查|统计|覆盖|涉及|抽样)[一-鿿]{0,8}?"
    r"(\d+(?:\.\d+)?)(?:万)?(?:例|名|人|份|个|组|样本|位)"
)
_FEATURE_RE = re.compile(
    r"(?:新增|推出|上线|增加了|加入了?)(?:了)?"
    r"([一-鿿]{1,8}(?:模式|功能|能力|选项|入口|板块|模块|服务|产品|特性))"
)
_METHOD_RE = re.compile(
    r"采用(?:了)?([一-鿿]{2,10}(?:方法|技术|方案|模型|算法|框架|协议|策略|"
    r"流程|范式|手段))"
)

# 第一人称：亲历与当下判断分开建模
_PAST_EXP_RE = re.compile(
    r"(?:我|本人)(?:以前|曾经|曾|当年|过去|早年|此前)?(?:也)?[一-鿿]{1,4}过"
    r"|我亲自[一-鿿]{1,5}"
)
_PRESENT_JUDGMENT_RE = re.compile(
    r"(?:我认为|我觉得|在我看来|我更倾向于|我个人认为|我的看法是|我倾向于|"
    r"我的理解是|我的判断是|我想)"
)

# 假设标注与句尾
_ASSUMPTION_MARK_RE = re.compile(
    r"(?:比如|例如|假设|设想|假如|试想|打个比方|举例来说|比方说|若是)"
)
_SENTENCE_END_RE = re.compile(r"[。；！？\n]")


_TIER_RANK = {"weak": 0, "medium": 1, "strong": 2}


def _canonical_url(surface: str) -> str:
    return surface.rstrip(".,;:!?）)】」』").lower()


def _canonical_date(surface: str, *, full: bool = True) -> str:
    """日期规范化：年份与年月组合统一为数字键（2019—2023 → 2019-2023）。"""
    value = surface.replace("年", "-").replace("月", "-").replace("日", "")
    value = value.rstrip("-")
    return _canonical_number(value)


# ---------------------------------------------------------------------------
# 提取辅助
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    """从规范化文本提取的一项：类别 + 规范化键 + 表面 + 位置。"""

    kind: str
    canonical: str
    surface: str
    start: int
    end: int


def _mask(text: str, matches: Iterable[re.Match[str]]) -> str:
    chars = list(text)
    for match in matches:
        for index in range(match.start(), min(match.end(), len(text))):
            chars[index] = " "
    return "".join(chars)


def _scan_items(text: str) -> list[_Item]:
    """从文本提取全部受保护/事实项（位置基于规范化文本）。"""
    normalized = _normalize_text(text)
    items: list[_Item] = []
    seen: set[str] = set()

    def add(kind: str, surface: str, start: int, end: int, key: str | None = None) -> None:
        canonical = key if key is not None else _canonical_text_key(surface)
        # 去重键带类别前缀：引语「35」与数字 35 分属不同保护项，不得互相遮挡
        if f"{kind}:{canonical}" in seen:
            return
        seen.add(f"{kind}:{canonical}")
        items.append(_Item(kind, canonical, surface, start, end))

    # 保护区域：代码 / 引语 / URL / 引用 / 公式（先提取，避免内部数字误判）
    for match in _CODE_BLOCK_RE.finditer(normalized):
        add("code", match.group(0).strip(), match.start(), match.end(),
            key=_normalize_text(match.group(0)))
    for match in _QUOTE_RE.finditer(normalized):
        content = next(g for g in match.groups() if g is not None)
        add("quote", content, match.start(), match.end())
    for match in _URL_RE.finditer(normalized):
        add("url", match.group(0), match.start(), match.end(),
            key=_canonical_url(match.group(0)))
    citation_matches = (
        list(_CITATION_RE.finditer(normalized))
        + list(_NUMBER_CITE_RE.finditer(normalized))
    )
    for match in citation_matches:
        add("citation", match.group(0).strip(), match.start(), match.end())
    for match in _FORMULA_RE.finditer(normalized):
        add("formula", match.group(0).strip(), match.start(), match.end(),
            key=_canonical_formula(match.group(0)))

    # 掩码保护区域内部，避免其数字产生独立数字项
    protected_matches = (
        list(_CODE_BLOCK_RE.finditer(normalized))
        + list(_QUOTE_RE.finditer(normalized))
        + list(_URL_RE.finditer(normalized))
        + list(_CITATION_RE.finditer(normalized))
        + list(_NUMBER_CITE_RE.finditer(normalized))
        + list(_FORMULA_RE.finditer(normalized))
    )
    plain = _mask(normalized, protected_matches)

    # 数字/单位
    def add_number_unit(match: re.Match[str]) -> None:
        number, unit = match.group(1), match.group(2)
        unit = (unit + (match.group(3) or "")).strip()
        surface = f"{number} {unit}".strip()
        add("number", surface, match.start(), match.end(),
            key=_canonical_number(number) + "|" + _canonical_unit(unit))

    for match in _NUMBER_UNIT_RE.finditer(plain):
        add_number_unit(match)
    plain = _mask(plain, _NUMBER_UNIT_RE.finditer(plain))
    # Issue 01 缺陷 c：数字+单位紧邻汉字/字母（如「Transformer2017年」）时
    # 严格正则漏配，补充宽松扫描使「2017 年/2017年/２０１７年」键一致。
    for match in _LOOSE_NUMBER_UNIT_RE.finditer(plain):
        add_number_unit(match)
    plain = _mask(plain, _LOOSE_NUMBER_UNIT_RE.finditer(plain))
    for match in re.finditer(r"(?<![A-Za-z0-9一-鿿])\d+(?:\.\d+)?", plain):
        add("number", match.group(0), match.start(), match.end(),
            key=_canonical_number(match.group(0)))

    # 日期（范围 → 年月 → 年份，逐层掩码）
    for match in _DATE_RANGE_RE.finditer(normalized):
        add("date", match.group(0).strip(), match.start(), match.end(),
            key=_canonical_date(match.group(0)))
    plain = _mask(normalized, _DATE_RANGE_RE.finditer(normalized))
    for match in _DATE_RE.finditer(plain):
        add("date", match.group(0).strip(), match.start(), match.end(),
            key=_canonical_date(match.group(0)))
    plain = _mask(plain, _DATE_RE.finditer(plain))
    for match in _YEAR_RE.finditer(plain):
        add("date", match.group(0), match.start(), match.end(),
            key=_canonical_date(match.group(0)))

    # 专名
    for match in _PN_BOOK_RE.finditer(normalized):
        add("proper_noun", match.group(1), match.start(), match.end())
    for match in _PN_ORG_RE.finditer(normalized):
        add("proper_noun", match.group(0), match.start(), match.end())
    for match in _PN_TITLE_RE.finditer(normalized):
        add("proper_noun", match.group(0), match.start(), match.end())
    for match in _PN_EN_RE.finditer(normalized):
        add("proper_noun", match.group(0), match.start(), match.end())

    # 否定与因果方向
    for match in _RELATION_RE.finditer(normalized):
        add("direction", f"rel:{match.group(2)}", match.start(), match.end(),
            key="rel:" + _canonical_text_key(match.group(2)))
    for match in _NEGATION_RE.finditer(normalized):
        add("direction", f"not:{match.group(2)}", match.start(), match.end(),
            key="not:" + _canonical_text_key(match.group(2)))

    # 限定词
    for match in _QUALIFIER_RE.finditer(normalized):
        add("qualifier", match.group(1).strip(), match.start(), match.end())

    # 结论强度（只取最高档）
    strength_items: list[tuple[int, str]] = []
    for match in _STRENGTH_RE.finditer(normalized):
        tier = _strength_tier(match.group(1))
        if tier is not None:
            strength_items.append((_TIER_RANK[tier], tier))
    if strength_items:
        strength_items.sort(key=lambda t: t[0], reverse=True)
        add("strength", strength_items[0][1], 0, 0, key="strength:" + strength_items[0][1])

    # 时间地点（惯例时间不计）
    for match in _RELATIVE_TIME_RE.finditer(normalized):
        add("time", match.group(0), match.start(), match.end())
    for match in _PLACE_RE.finditer(normalized):
        add("place", match.group(0), match.start(), match.end())
    for match in _VAGUE_TIME_RE.finditer(normalized):
        add("vague_time", match.group(0), match.start(), match.end())

    # 事件 / 样本 / 功能 / 研究方法（新增 claim 模式）
    for match in _EVENT_RE.finditer(normalized):
        add("event", match.group(0), match.start(), match.end())
    for match in _SAMPLE_RE.finditer(normalized):
        add("sample", match.group(0), match.start(), match.end(),
            key="sample:" + _canonical_number(match.group(1)))
    for match in _FEATURE_RE.finditer(normalized):
        add("feature", match.group(1), match.start(), match.end())
    for match in _METHOD_RE.finditer(normalized):
        add("method", match.group(1), match.start(), match.end())

    # 第一人称
    for match in _PAST_EXP_RE.finditer(normalized):
        add("past_experience", match.group(0), match.start(), match.end())
    for match in _PRESENT_JUDGMENT_RE.finditer(normalized):
        add("present_judgment", match.group(0), match.start(), match.end())

    return items


def extract_protected_spans(text: str) -> list[ProtectedSpan]:
    """从文本提取受保护区域（引语/代码/公式/URL/引用），只存哈希与位置。"""
    normalized = _normalize_text(text)
    spans: list[ProtectedSpan] = []
    seen: set[str] = set()
    kind_map = {
        "quote": ProtectedSpanKind.QUOTE,
        "code": ProtectedSpanKind.CODE,
        "formula": ProtectedSpanKind.FORMULA,
        "url": ProtectedSpanKind.URL,
        "citation": ProtectedSpanKind.CITATION,
    }
    for item in _scan_items(normalized):
        kind = kind_map.get(item.kind)
        if kind is None:
            continue
        hash_key = f"{item.kind}:{item.canonical}"
        if hash_key in seen:
            continue
        seen.add(hash_key)
        spans.append(
            ProtectedSpan(
                span_id=f"ps-{secrets.token_urlsafe(8)}",
                kind=kind,
                text_hash=_sha256(item.surface),
                location=SpanLocation(start=item.start, end=item.end),
            )
        )
    return spans


# ---------------------------------------------------------------------------
# 账本编译
# ---------------------------------------------------------------------------


def _build_entry(
    entry_id: str,
    source_type: SourceType,
    text: str,
    *,
    source_label: str = "",
    usage: list[SourceUsage],
    allow_first_person: bool = False,
    user_phrases: Iterable[str] = (),
) -> SourceEntry:
    normalized = _normalize_text(text)
    items = _scan_items(normalized)
    by_kind: dict[str, list[_Item]] = {}
    for item in items:
        by_kind.setdefault(item.kind, []).append(item)

    experiences = [i.canonical for i in by_kind.get("past_experience", [])]
    personal = PersonalExperienceMode.NONE
    if experiences and source_type == SourceType.USER_ORIGINAL:
        personal = PersonalExperienceMode.PAST_EXPERIENCE
        if SourceUsage.EXPERIENCE not in usage:
            usage = [*usage, SourceUsage.EXPERIENCE]
    elif by_kind.get("present_judgment"):
        personal = PersonalExperienceMode.PRESENT_JUDGMENT

    # 保护区域直接由本次扫描派生，避免重复扫描
    kind_to_span = {
        "quote": ProtectedSpanKind.QUOTE,
        "code": ProtectedSpanKind.CODE,
        "formula": ProtectedSpanKind.FORMULA,
        "url": ProtectedSpanKind.URL,
        "citation": ProtectedSpanKind.CITATION,
    }
    spans: list[ProtectedSpan] = []
    for item in items:
        kind = kind_to_span.get(item.kind)
        if kind is None:
            continue
        spans.append(
            ProtectedSpan(
                span_id=f"ps-{secrets.token_urlsafe(8)}",
                kind=kind,
                text_hash=_sha256(item.surface),
                location=SpanLocation(start=item.start, end=item.end),
            )
        )
    quotes = list(by_kind.get("quote", []))
    for phrase in user_phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        spans.append(
            ProtectedSpan(
                span_id=f"ps-{secrets.token_urlsafe(8)}",
                kind=ProtectedSpanKind.USER_PHRASE,
                text_hash=_sha256(_normalize_text(phrase)),
                location=SpanLocation(start=0, end=0),
            )
        )
        # 用户指定措辞按精确引语同等保护（保留检查必须保持）
        key = _canonical_text_key(phrase)
        if not any(q.canonical == key for q in quotes):
            quotes.append(_Item("quote", key, phrase, 0, 0))

    return SourceEntry(
        entry_id=entry_id,
        source_type=source_type,
        content_hash=_sha256(normalized),
        usage=list(usage),
        proper_nouns=[i.canonical for i in by_kind.get("proper_noun", [])],
        numbers=[i.canonical for i in by_kind.get("number", [])],
        dates=[i.canonical for i in by_kind.get("date", [])],
        formulas=[i.canonical for i in by_kind.get("formula", [])],
        codes=[i.canonical for i in by_kind.get("code", [])],
        urls=[i.canonical for i in by_kind.get("url", [])],
        quotes=[i.canonical for i in quotes],
        citations=[i.canonical for i in by_kind.get("citation", [])],
        directions=[i.canonical for i in by_kind.get("direction", [])],
        qualifiers=[i.canonical for i in by_kind.get("qualifier", [])],
        conclusion_strength=(
            by_kind["strength"][0].canonical.removeprefix("strength:")
            if by_kind.get("strength")
            else None
        ),
        personal_experience=personal,
        allow_first_person=allow_first_person or personal == PersonalExperienceMode.PAST_EXPERIENCE,
        protected_spans=spans,
        source_label=source_label,
        experiences=experiences,
        text_key=_canonical_text_key(normalized),
    )


def _ledger_hash(entries: list[SourceEntry]) -> str:
    parts = [SOURCE_LEDGER_VERSION]
    for entry in entries:
        entry_parts = [
            entry.source_type.value,
            entry.content_hash,
            ",".join(u.value for u in entry.usage),
            ",".join(sorted(span.text_hash for span in entry.protected_spans)),
        ]
        parts.append("|".join(entry_parts))
    return _sha256(";".join(parts))


def compile_source_ledger(
    primary_text: str,
    *,
    primary_label: str = "",
    supplements: Iterable[tuple[str, str]] = (),
    account_scoped: Iterable[tuple[str, str]] = (),
    external_allowed: Iterable[tuple[str, str]] = (),
    common_knowledge: Iterable[str] = (),
    explicit_assumptions: Iterable[str] = (),
    user_phrases: Iterable[str] = (),
    allow_first_person: bool = False,
) -> SourceLedger:
    """编译版本化来源账本。

    - ``primary_text``：改写路径的原文，或生成路径的约束文本（用户内容边界）。
    - ``supplements``：用户补充材料（(显示名, 文本)）。
    - ``account_scoped``：账户作用域授权材料（知识库等）。
    - ``external_allowed``：明确允许的外部来源。
    - ``common_knowledge``：用户显式列为普通常识的内容。
    - ``explicit_assumptions``：用户显式声明允许的假设内容。
    - ``user_phrases``：用户指定措辞（保护 span，不得改写）。
    - ``allow_first_person``：用户是否授权代写作者口吻。
    """
    entries: list[SourceEntry] = []
    entries.append(
        _build_entry(
            f"src-{secrets.token_urlsafe(8)}",
            SourceType.USER_ORIGINAL,
            primary_text,
            source_label=primary_label,
            usage=[SourceUsage.REWRITE],
            allow_first_person=allow_first_person,
            user_phrases=user_phrases,
        )
    )
    for label, text in supplements:
        entries.append(
            _build_entry(
                f"src-{secrets.token_urlsafe(8)}",
                SourceType.USER_SUPPLEMENT,
                text,
                source_label=label,
                usage=[SourceUsage.FACT, SourceUsage.QUOTE],
            )
        )
    for label, text in account_scoped:
        entries.append(
            _build_entry(
                f"src-{secrets.token_urlsafe(8)}",
                SourceType.ACCOUNT_SCOPED,
                text,
                source_label=label,
                usage=[SourceUsage.FACT, SourceUsage.QUOTE],
            )
        )
    for label, text in external_allowed:
        entries.append(
            _build_entry(
                f"src-{secrets.token_urlsafe(8)}",
                SourceType.EXTERNAL_ALLOWED,
                text,
                source_label=label,
                usage=[SourceUsage.FACT, SourceUsage.QUOTE],
            )
        )
    for text in common_knowledge:
        entries.append(
            _build_entry(
                f"src-{secrets.token_urlsafe(8)}",
                SourceType.COMMON_KNOWLEDGE,
                text,
                source_label="普通常识",
                usage=[SourceUsage.FACT],
            )
        )
    for text in explicit_assumptions:
        entries.append(
            _build_entry(
                f"src-{secrets.token_urlsafe(8)}",
                SourceType.EXPLICIT_ASSUMPTION,
                text,
                source_label="显式假设",
                usage=[SourceUsage.FACT],
            )
        )

    by_kind: dict[str, int] = {}
    for entry in entries:
        for span in entry.protected_spans:
            by_kind[span.kind.value] = by_kind.get(span.kind.value, 0) + 1
    summary = LedgerCompileSummary(
        entry_count=len(entries),
        protected_spans_by_kind=by_kind,
        proper_noun_count=sum(len(e.proper_nouns) for e in entries),
        number_count=sum(len(e.numbers) for e in entries),
        date_count=sum(len(e.dates) for e in entries),
        quote_count=sum(len(e.quotes) for e in entries),
        experience_entries=sum(1 for e in entries if e.allow_first_person),
    )
    ledger = SourceLedger(
        ledger_version=SOURCE_LEDGER_VERSION,
        ledger_hash="",
        entries=entries,
        compile_summary=summary,
    )
    ledger.ledger_hash = _ledger_hash(entries)
    return ledger


# ---------------------------------------------------------------------------
# 保真检查
# ---------------------------------------------------------------------------


class FidelityCheckError(RuntimeError):
    """保真检查失败关闭错误：版本不支持、账本哈希不一致或候选为空。"""


def _failure(
    code: FidelityFailureCode,
    severity: FidelitySeverity,
    category: str,
    item_type: str,
    note: str,
    location: SpanLocation | None = None,
) -> FidelityFailure:
    return FidelityFailure(
        failure_id=f"ff-{secrets.token_urlsafe(8)}",
        code=code,
        severity=severity,
        category=category,
        item_type=item_type,
        location=location,
        note=note,
    )


def _assumption_spans(normalized_candidate: str) -> list[tuple[int, int]]:
    """候选中的假设标注片段（标注词到句尾）。"""
    spans: list[tuple[int, int]] = []
    for match in _ASSUMPTION_MARK_RE.finditer(normalized_candidate):
        end_match = _SENTENCE_END_RE.search(normalized_candidate, match.end())
        end = end_match.start() if end_match else len(normalized_candidate)
        spans.append((match.start(), end))
    return spans


def _number_bound(item: _Item, number_keys: set[str]) -> bool:
    if item.canonical in number_keys:
        return True
    number_part = item.canonical.split("|")[0]
    return any(v.split("|")[0] == number_part for v in number_keys)


def run_fidelity_check(
    ledger: SourceLedger,
    candidate: str,
    *,
    contract_path: HumanizerPath = HumanizerPath.REWRITE,
    allow_assumptions: bool = False,
) -> FidelityCheckResult:
    """执行双向保真检查：保留检查 + 新增 claim 来源检查。

    失败关闭：账本版本不受支持、账本哈希与条目不一致或候选为空时抛出
    ``FidelityCheckError``，调用方不得标记成功。
    """
    if ledger.ledger_version != SOURCE_LEDGER_VERSION:
        raise FidelityCheckError(
            f"来源账本版本 {ledger.ledger_version} 不受支持（当前 {SOURCE_LEDGER_VERSION}）。"
        )
    if _ledger_hash(ledger.entries) != ledger.ledger_hash:
        raise FidelityCheckError("来源账本哈希与条目不一致，拒绝检查。")
    normalized_candidate = _normalize_text(candidate)
    if not normalized_candidate.strip():
        raise FidelityCheckError("候选正文为空，拒绝检查。")

    candidate_items = _scan_items(normalized_candidate)
    candidate_by_kind: dict[str, list[_Item]] = {}
    for item in candidate_items:
        candidate_by_kind.setdefault(item.kind, []).append(item)

    original_entry = next(
        (e for e in ledger.entries if e.source_type == SourceType.USER_ORIGINAL),
        None,
    )
    all_text_keys = {e.text_key for e in ledger.entries if e.text_key}
    all_number_keys = {k for e in ledger.entries for k in e.numbers}
    all_date_keys = {k for e in ledger.entries for k in e.dates}
    all_quote_keys = {k for e in ledger.entries for k in e.quotes}
    all_pn_keys = {k for e in ledger.entries for k in e.proper_nouns}
    all_url_keys = {k for e in ledger.entries for k in e.urls}
    all_cite_keys = {k for e in ledger.entries for k in e.citations}
    all_formula_keys = {k for e in ledger.entries for k in e.formulas}
    all_code_keys = {k for e in ledger.entries for k in e.codes}
    # 亲历绑定只允许原文已有或用户授权第一人称的条目
    all_experiences = {
        k
        for e in ledger.entries
        if e.allow_first_person
        for k in e.experiences
    }

    blocking: list[FidelityFailure] = []
    confirmation: list[FidelityFailure] = []
    preserved_count = 0

    # ------------------------------------------------------------------
    # 保留检查（改写路径；生成路径的约束语义由 check_requirements 覆盖）
    # ------------------------------------------------------------------
    if contract_path == HumanizerPath.REWRITE:
        blocking, confirmation, preserved_count = _preservation_check(
            ledger, original_entry, candidate_by_kind, normalized_candidate,
            blocking, confirmation,
        )

    # ------------------------------------------------------------------
    # 新增 claim 来源检查（候选相对账本的新增可核查项必须可绑定）
    # ------------------------------------------------------------------
    attributed, assumptions_allowed = _attribution_check(
        ledger, candidate_items, normalized_candidate,
        allow_assumptions, all_text_keys, all_number_keys, all_date_keys,
        all_quote_keys, all_pn_keys, all_url_keys, all_cite_keys,
        all_formula_keys, all_code_keys, all_experiences,
        blocking, confirmation,
    )

    summary = FidelitySummary(
        protected_span_count=sum(len(e.protected_spans) for e in ledger.entries),
        preserved_count=preserved_count,
        new_claim_count=sum(
            1
            for item in candidate_items
            if item.kind
            in ("number", "date", "proper_noun", "quote", "url", "citation",
                "time", "place", "event", "sample", "feature", "method")
        ),
        attributed_claim_count=attributed,
        unattributed_claim_count=sum(
            1
            for f in blocking
            if f.code == FidelityFailureCode.UNATTRIBUTED_CLAIM
        ),
        first_person_interception_count=sum(
            1
            for f in blocking + confirmation
            if f.code == FidelityFailureCode.FIRST_PERSON_UNBOUND
        ),
        assumption_count=assumptions_allowed,
        blocking_count=len(blocking),
        needs_confirmation_count=len(confirmation),
    )

    return FidelityCheckResult(
        check_id=f"fdc-{secrets.token_urlsafe(8)}",
        ledger_version=ledger.ledger_version,
        checker_version=FIDELITY_CHECKER_VERSION,
        ledger_hash=ledger.ledger_hash,
        passed=not blocking,
        blocking_failures=blocking,
        needs_confirmation=confirmation,
        summary=summary,
    )


def _preservation_check(
    ledger: SourceLedger,
    original_entry: SourceEntry | None,
    candidate_by_kind: dict[str, list[_Item]],
    normalized_candidate: str,
    blocking: list[FidelityFailure],
    confirmation: list[FidelityFailure],
) -> tuple[list[FidelityFailure], list[FidelityFailure], int]:
    """保留检查：账本保护项必须在候选中保持。返回 (阻断, 需确认, 保持数)。"""
    preserved_count = 0
    if original_entry is None:
        return blocking, confirmation, preserved_count

    candidate_plain = _canonical_text_key(normalized_candidate)
    candidate_quote_keys = {i.canonical for i in candidate_by_kind.get("quote", [])}
    candidate_url_keys = {i.canonical for i in candidate_by_kind.get("url", [])}
    candidate_cite_keys = {i.canonical for i in candidate_by_kind.get("citation", [])}
    candidate_formula_keys = {i.canonical for i in candidate_by_kind.get("formula", [])}
    candidate_code_keys = {i.canonical for i in candidate_by_kind.get("code", [])}
    candidate_number_keys = {i.canonical for i in candidate_by_kind.get("number", [])}
    candidate_date_keys = {i.canonical for i in candidate_by_kind.get("date", [])}
    candidate_pn_keys = {i.canonical for i in candidate_by_kind.get("proper_noun", [])}
    candidate_direction_keys = {i.canonical for i in candidate_by_kind.get("direction", [])}
    candidate_qualifier_keys = {i.canonical for i in candidate_by_kind.get("qualifier", [])}

    def preserved(kind: str, key: str, in_candidate: bool) -> bool:
        nonlocal preserved_count
        if in_candidate:
            preserved_count += 1
            return True
        return False

    for key in original_entry.quotes:
        if not preserved("quote", key, key in candidate_quote_keys or key in candidate_plain):
            blocking.append(_failure(
                FidelityFailureCode.QUOTE_CHANGED, FidelitySeverity.BLOCKING,
                "精确引语", "quote", f"原文引语「{key}」在结果中未保持。",
            ))
    for key in original_entry.urls:
        if not preserved("url", key, key in candidate_url_keys):
            blocking.append(_failure(
                FidelityFailureCode.URL_CHANGED, FidelitySeverity.BLOCKING,
                "URL", "url", f"原文 URL「{key}」在结果中未保持。",
            ))
    for key in original_entry.formulas:
        if not preserved("formula", key, key in candidate_formula_keys or key in candidate_plain):
            blocking.append(_failure(
                FidelityFailureCode.FORMULA_CHANGED, FidelitySeverity.BLOCKING,
                "公式", "formula", f"原文公式「{key}」在结果中未保持。",
            ))
    for key in original_entry.codes:
        if not preserved("code", key, key in candidate_code_keys or key in candidate_plain):
            blocking.append(_failure(
                FidelityFailureCode.CODE_CHANGED, FidelitySeverity.BLOCKING,
                "代码块", "code", "原文代码块在结果中未保持。",
            ))
    for key in original_entry.citations:
        if not preserved("citation", key, key in candidate_cite_keys or key in candidate_plain):
            blocking.append(_failure(
                FidelityFailureCode.CITATION_CHANGED, FidelitySeverity.BLOCKING,
                "引用", "citation", f"原文引用「{key}」在结果中未保持。",
            ))
    for key in original_entry.numbers:
        if not preserved("number", key, key in candidate_number_keys):
            blocking.append(_failure(
                FidelityFailureCode.NUMBER_CHANGED, FidelitySeverity.BLOCKING,
                "数字与单位", "number", f"原文数值「{key}」在结果中未保持。",
            ))
    for key in original_entry.dates:
        if not preserved("date", key, key in candidate_date_keys):
            blocking.append(_failure(
                FidelityFailureCode.DATE_CHANGED, FidelitySeverity.BLOCKING,
                "日期", "date", f"原文日期「{key}」在结果中未保持。",
            ))
    for key in original_entry.proper_nouns:
        if not preserved("proper_noun", key, key in candidate_pn_keys):
            blocking.append(_failure(
                FidelityFailureCode.PROPER_NOUN_CHANGED, FidelitySeverity.BLOCKING,
                "专名", "proper_noun", f"原文专名「{key}」在结果中未保持。",
            ))
    for key in original_entry.directions:
        if preserved("direction", key, key in candidate_direction_keys):
            continue
        if key.startswith("rel:"):
            verb = key.removeprefix("rel:")
            opposite = _RELATION_OPPOSITES.get(verb)
            if opposite and opposite in candidate_plain:
                blocking.append(_failure(
                    FidelityFailureCode.DIRECTION_FLIPPED, FidelitySeverity.BLOCKING,
                    "因果方向", "direction",
                    f"原文「{verb}」在结果中被反转为「{opposite}」。",
                ))
            else:
                blocking.append(_failure(
                    FidelityFailureCode.DIRECTION_FLIPPED, FidelitySeverity.BLOCKING,
                    "因果方向", "direction", f"原文因果方向「{verb}」在结果中未保持。",
                ))
        elif key.startswith("not:"):
            verb = key.removeprefix("not:")
            if verb in candidate_plain:
                blocking.append(_failure(
                    FidelityFailureCode.DIRECTION_FLIPPED, FidelitySeverity.BLOCKING,
                    "否定", "direction", f"原文否定「{verb}」在结果中被删除。",
                ))
            else:
                blocking.append(_failure(
                    FidelityFailureCode.DIRECTION_FLIPPED, FidelitySeverity.BLOCKING,
                    "否定", "direction", f"原文否定「{verb}」在结果中未保持。",
                ))
    for key in original_entry.qualifiers:
        qualifier_kept = key in candidate_qualifier_keys or key in candidate_plain
        if not preserved("qualifier", key, qualifier_kept):
            confirmation.append(_failure(
                FidelityFailureCode.QUALIFIER_REMOVED,
                FidelitySeverity.NEEDS_USER_CONFIRMATION,
                "限定条件", "qualifier",
                f"原文限定「{key}」在结果中未保持，请确认。",
            ))

    # 结论强度：升级阻断、降级需确认
    candidate_strength = next(
        (i.canonical.removeprefix("strength:") for i in candidate_by_kind.get("strength", [])),
        None,
    )
    if original_entry.conclusion_strength and candidate_strength:
        before = _TIER_RANK[original_entry.conclusion_strength]
        after = _TIER_RANK.get(candidate_strength, 1)
        if after > before:
            blocking.append(_failure(
                FidelityFailureCode.STRENGTH_UPGRADED, FidelitySeverity.BLOCKING,
                "结论强度", "strength",
                f"原文结论强度「{original_entry.conclusion_strength}」被升级为「{candidate_strength}」。",
            ))
        elif after < before:
            confirmation.append(_failure(
                FidelityFailureCode.STRENGTH_DOWNGRADED,
                FidelitySeverity.NEEDS_USER_CONFIRMATION,
                "结论强度", "strength",
                f"原文结论强度「{original_entry.conclusion_strength}」被弱化为「{candidate_strength}」，请确认。",
            ))

    # 亲历保持：原文亲历必须仍在候选
    if original_entry.personal_experience == PersonalExperienceMode.PAST_EXPERIENCE:
        candidate_exps = {i.canonical for i in candidate_by_kind.get("past_experience", [])}
        if not any(
            any(exp in c or c in exp for c in candidate_exps)
            for exp in original_entry.experiences
        ):
            blocking.append(_failure(
                FidelityFailureCode.FIRST_PERSON_UNBOUND, FidelitySeverity.BLOCKING,
                "第一人称经历", "past_experience", "原文亲历在结果中未保持。",
            ))
        else:
            preserved_count += 1

    return blocking, confirmation, preserved_count


def _attribution_check(
    ledger: SourceLedger,
    candidate_items: list[_Item],
    normalized_candidate: str,
    allow_assumptions: bool,
    all_text_keys: set[str],
    all_number_keys: set[str],
    all_date_keys: set[str],
    all_quote_keys: set[str],
    all_pn_keys: set[str],
    all_url_keys: set[str],
    all_cite_keys: set[str],
    all_formula_keys: set[str],
    all_code_keys: set[str],
    all_experiences: set[str],
    blocking: list[FidelityFailure],
    confirmation: list[FidelityFailure],
) -> tuple[int, int]:
    """新增 claim 来源检查 + 假设/亲历契约检查。

    返回 (有来源绑定的新增 claim 数, 允许的标注假设数)。
    """
    user_phrases = {
        span.text_hash
        for entry in ledger.entries
        for span in entry.protected_spans
        if span.kind == ProtectedSpanKind.USER_PHRASE
    }
    assumption_spans = _assumption_spans(normalized_candidate)
    in_assumption = [False] * len(candidate_items)
    for index, item in enumerate(candidate_items):
        for start, end in assumption_spans:
            if start <= item.start < end:
                in_assumption[index] = True
                break

    # 契约不允许假设时：候选新增的假设标注片段（非原文已有）即失败
    if not allow_assumptions:
        for start, end in assumption_spans:
            segment = _canonical_text_key(normalized_candidate[start:end])
            if not segment or any(segment in text_key for text_key in all_text_keys):
                continue
            blocking.append(_failure(
                FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
                FidelitySeverity.BLOCKING,
                "显式假设", "assumption",
                "候选使用了「比如/假设/设想」标注的假设内容，但任务契约不允许。",
            ))

    attributed = 0
    for index, item in enumerate(candidate_items):
        kind = item.kind
        if kind == "present_judgment":
            continue  # 当下判断不冒充亲历，放行
        if kind == "vague_time":
            # 原文/材料已有的模糊时间不构成新增；仅未绑定的才无法判定
            if not any(item.canonical in text_key for text_key in all_text_keys):
                confirmation.append(_failure(
                    FidelityFailureCode.UNDETERMINED,
                    FidelitySeverity.NEEDS_USER_CONFIRMATION,
                    "时间表述", "time",
                    f"候选新增模糊时间表述「{item.surface}」，无法判定来源，请确认。",
                ))
            continue
        if kind in ("code", "qualifier", "strength", "direction"):
            continue
        if kind == "past_experience":
            if in_assumption[index]:
                # 假设伪装成亲历：假设不得承载作者/用户经历
                blocking.append(_failure(
                    FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
                    FidelitySeverity.BLOCKING,
                    "假设承载经历", "past_experience",
                    f"显式假设「{item.surface}」伪装成亲历，不允许。",
                    _loc(item),
                ))
                continue
            bound = any(
                item.canonical in exp or exp in item.canonical
                for exp in all_experiences
            )
            if bound:
                attributed += 1
            else:
                blocking.append(_failure(
                    FidelityFailureCode.FIRST_PERSON_UNBOUND,
                    FidelitySeverity.BLOCKING,
                    "第一人称经历", "past_experience",
                    f"候选新增亲历「{item.surface}」没有可绑定的来源经历。",
                    _loc(item),
                ))
            continue
        if kind not in (
            "number", "date", "proper_noun", "quote", "url", "citation",
            "time", "place", "event", "sample", "feature", "method",
        ):
            continue

        bound = _bound_to_ledger(
            item, all_text_keys, all_number_keys, all_date_keys,
            all_quote_keys, all_pn_keys, all_url_keys, all_cite_keys,
            all_formula_keys, all_code_keys, user_phrases,
        )
        if bound:
            attributed += 1
            continue
        if in_assumption[index]:
            if allow_assumptions:
                if _assumption_carries_high_risk(
                    item, all_number_keys, all_date_keys,
                ):
                    blocking.append(_failure(
                        FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
                        FidelitySeverity.BLOCKING,
                        "假设承载事实", kind,
                        f"显式假设「{item.surface}」承载了高风险事实，不允许。",
                        _loc(item),
                    ))
                continue
            blocking.append(_failure(
                FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
                FidelitySeverity.BLOCKING,
                "显式假设", kind,
                f"候选使用显式假设「{item.surface}」，但任务契约不允许。",
                _loc(item),
            ))
            continue

        blocking.append(_failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            FidelitySeverity.BLOCKING,
            "新增无来源", kind,
            f"候选新增「{item.surface}」（{_kind_cn(kind)}）没有账本来源。",
            _loc(item),
        ))

    # 允许的标注假设计数：非原文已有且片段内未被拦截的假设片段
    assumptions_allowed = 0
    if allow_assumptions:
        for start, end in assumption_spans:
            segment = _canonical_text_key(normalized_candidate[start:end])
            if not segment or any(segment in text_key for text_key in all_text_keys):
                continue
            if any(
                f.location is not None and start <= f.location.start < end
                for f in blocking
            ):
                continue
            assumptions_allowed += 1
    return attributed, assumptions_allowed


def _bound_to_ledger(
    item: _Item,
    all_text_keys: set[str],
    all_number_keys: set[str],
    all_date_keys: set[str],
    all_quote_keys: set[str],
    all_pn_keys: set[str],
    all_url_keys: set[str],
    all_cite_keys: set[str],
    all_formula_keys: set[str],
    all_code_keys: set[str],
    user_phrases: set[str],
) -> bool:
    """候选新增项是否可绑定到账本来源。

    结构化项（引语/专名/URL/引用/公式/代码）按账本条目的同类规范化键
    精确匹配（等价于绑定引用的 span/哈希）；非结构化表述（时间地点/
    功能/方法/事件）按账本条目全文子串判定「原文已有」。
    """
    if item.kind == "number":
        return _number_bound(item, all_number_keys)
    if item.kind == "sample":
        return _number_bound(
            _Item("number", item.canonical.removeprefix("sample:"), "", 0, 0),
            all_number_keys,
        )
    if item.kind == "date":
        # 范围日期（2019—2023）在 text_key 中连接符已被剥离，需去掉连接符比对
        compact = item.canonical.replace("-", "")
        return item.canonical in all_date_keys or any(
            compact in text_key.replace("-", "") for text_key in all_text_keys
        )
    class_keys = {
        "quote": all_quote_keys,
        "proper_noun": all_pn_keys,
        "url": all_url_keys,
        "citation": all_cite_keys,
        "formula": all_formula_keys,
        "code": all_code_keys,
    }
    if item.kind in class_keys:
        return item.canonical in class_keys[item.kind]
    if item.kind in ("time", "place", "event", "feature", "method"):
        return any(item.canonical in text_key for text_key in all_text_keys)
    return False


def _assumption_carries_high_risk(
    item: _Item, all_number_keys: set[str], all_date_keys: set[str]
) -> bool:
    """假设片段内的项是否承载高风险事实（专名/引语/URL/引用/亲历/样本/
    方法/功能/事件/时间地点，或未绑定的数字与日期）。"""
    if item.kind in (
        "proper_noun", "quote", "url", "citation", "past_experience",
        "sample", "time", "place", "event", "feature", "method",
    ):
        return True
    if item.kind in ("number", "date"):
        return not _number_bound(item, all_number_keys)
    return False


def _loc(item: _Item) -> SpanLocation | None:
    return SpanLocation(start=item.start, end=item.end)


def _kind_cn(kind: str) -> str:
    return {
        "number": "数字与单位",
        "date": "日期",
        "proper_noun": "专名",
        "quote": "精确引语",
        "url": "URL",
        "citation": "引用",
        "time": "时间地点",
        "place": "地点",
        "event": "事件",
        "sample": "样本量",
        "feature": "产品功能",
        "method": "研究方法",
    }.get(kind, kind)


__all__ = [
    "FidelityCheckError",
    "compile_source_ledger",
    "extract_protected_spans",
    "run_fidelity_check",
]
