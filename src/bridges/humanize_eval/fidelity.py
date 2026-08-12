"""最小保真检查器（Issue 01 tracer bullet，失败关闭）。

覆盖：数字、单位、日期、专名、精确引语、URL、否定词和第一人称经历
新增。每条检查都有明确结果记录：passed / failed / missing（无法
执行时必须记录，绝不默认通过）；任一关键保真失败或任一检查缺失时
案例不得标记通过。

第一人称经历检查是文章案例的严重门：输出中出现的个人亲历、朋友对话、
具体时间地点、未经来源支持的数据或功能一律判为严重失败，除非该表述
存在于原文或允许材料中。
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.humanize_eval.cases import HumanizeCase, HumanizeCaseKind

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:%|％)?")
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日|(?:19|20)\d{2}-\d{2}-\d{2}"
)
_URL_RE = re.compile(r"https?://[^\s，。；、”》]+")
#: 精确引语：说话动词后的引号内容（术语强调如“注意力带宽”不算引语）。
_QUOTE_RE = re.compile(
    r"(?:说道|说过|写道|指出|认为|表示|说|道|言|称)[：:“「]([^”」]+)[”」]"
    r"|(?:说道|说过|写道|指出|认为|表示|说|道|言|称)[：:][\"]([^\"]+)[\"]"
)
_NEGATION_RE = re.compile(r"(不|没|未|莫|勿|别|禁止|避免|非)([^，。；！？\n]{0,6})")
#: 度量单位（不含"人/次/例/篇"等计数词，避免句式差异被误报为新事实）。
_UNIT_RE = re.compile(
    r"(分钟|小时|天|周|月|年|毫秒|秒|公斤|千克|克|米|千米|公里|平方米|"
    r"立方米|百分比|%|％)"
)

#: 编造经历与无来源数据的识别模式（与案例 forbidden_claims 对齐）。
_FABRICATION_RE = re.compile(
    r"(我以前|我过去|我之前|我试过|我用过|我做过|我坚持|我上周|"
    r"上周[我]?[和跟]?|我一个朋友|我朋友[说聊]|朋友和我|"
    r"半小时前|几分钟前|前几天|昨晚|上周三|楼下|咖啡店|"
    r"刷短视频|看了个视频|无意间发现)"
)

class FidelitySeverity(StrEnum):
    """检查严重度：critical 为硬门。"""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class FidelityCheckItem(BaseModel):
    """一条保真检查的结果。

    ``missing`` 只用于「检查器本应执行但没有执行」（失败关闭）；
    ``not_applicable`` 用于「案例设计上无此内容可查」（如实记录，
    不计失败，也不视为缺失）。
    """

    check_id: str = Field(description="检查项标识（numbers/units/dates/…）。")
    label: str = Field(description="中文标签。")
    severity: FidelitySeverity = Field(description="严重度。")
    passed: bool = Field(description="检查是否通过。")
    missing: bool = Field(description="检查是否因执行器未运行而未执行（失败关闭）。")
    not_applicable: bool = Field(
        default=False, description="案例设计上无此内容可查（有记录，不算缺失）。"
    )
    evidence: list[str] = Field(default_factory=list, description="证据 span。")
    reason: str = Field(default="", description="中文原因。")

    @property
    def effective_failure(self) -> bool:
        """检查缺失（missing）视为失败；not_applicable 与通过都不算失败。"""
        return self.missing or not self.passed


class FidelityReport(BaseModel):
    """一次保真检查的完整报告。"""

    case_id: str
    checks: list[FidelityCheckItem] = Field(default_factory=list)
    output_preview: str = Field(default="", description="脱敏输出预览（不进入摘要）。")

    @property
    def critical_failures(self) -> list[FidelityCheckItem]:
        return [c for c in self.checks if c.severity is FidelitySeverity.CRITICAL
                and c.effective_failure]

    @property
    def passed(self) -> bool:
        """全部检查通过且无检查缺失（not_applicable 不算缺失）。"""
        return all(not c.effective_failure for c in self.checks)

    @property
    def missing_checks(self) -> list[FidelityCheckItem]:
        """真正缺失的检查：执行器没有运行（失败关闭依据）。"""
        return [c for c in self.checks if c.missing]

    def require_complete(self, expected_ids: set[str]) -> None:
        """失败关闭：报告必须包含全部预期检查项，缺项即抛错。"""
        missing = expected_ids - {c.check_id for c in self.checks}
        if missing:
            raise FidelityCheckError(
                f"案例 {self.case_id} 保真检查缺失执行项：{'、'.join(sorted(missing))}"
            )


class FidelityCheckError(Exception):
    """保真检查执行失败（用于失败关闭语义）。"""


def _span(text: str, value: str) -> str:
    """定位 value 在 text 中的首个出现位置（无则返回空）。"""
    index = text.find(value)
    return f"…{text[max(0, index - 10): index + len(value) + 10]}…" if index >= 0 else ""


def _extract(regex: re.Pattern[str], text: str) -> set[str]:
    return {m.group(0).strip() for m in regex.finditer(text) if m.group(0).strip()}


def _check_values(
    *,
    check_id: str,
    label: str,
    severity: FidelitySeverity,
    source_values: set[str],
    output_values: set[str],
    materials_values: set[str],
    source_text: str,
    output_text: str,
    protected_extra: list[str],
) -> FidelityCheckItem:
    """通用保留检查：原文值必须保留；输出新增值必须有材料来源。"""
    if not source_values and not protected_extra:
        return FidelityCheckItem(
            check_id=check_id,
            label=label,
            severity=severity,
            passed=False,
            missing=True,
            reason=f"原文中没有可检查的{label}，无法执行该检查。",
        )
    lost = sorted(source_values - output_values)
    invented = sorted(
        output_values - source_values - materials_values
    )
    protected_hits = [p for p in protected_extra if p and p not in output_text]
    failed = bool(lost or invented or protected_hits)
    evidence = [_span(source_text, v) for v in list(lost) + list(invented)
                + protected_hits][:5]
    reasons = []
    if lost:
        reasons.append(f"原文{label}丢失：{'、'.join(lost)}")
    if invented:
        reasons.append(f"新增无来源{label}：{'、'.join(invented)}")
    if protected_hits:
        reasons.append(f"保护项缺失：{'、'.join(protected_hits)}")
    return FidelityCheckItem(
        check_id=check_id,
        label=label,
        severity=severity,
        passed=not failed,
        missing=False,
        evidence=evidence,
        reason="；".join(reasons) if reasons else "全部保留，无新增。",
    )


def _check_first_person(
    case: HumanizeCase, output_text: str
) -> FidelityCheckItem:
    """第一人称经历新增检查：输出中出现的编造亲历一律严重失败。"""
    hits = sorted({m.group(0) for m in _FABRICATION_RE.finditer(output_text)})
    # 原文或允许材料中已有的表述不算新增。
    haystack = f"{case.source_text or ''}\n{''.join(case.allowed_materials)}"
    new_hits = [h for h in hits if h not in haystack]
    if not new_hits:
        return FidelityCheckItem(
            check_id="first_person",
            label="第一人称经历新增",
            severity=FidelitySeverity.CRITICAL,
            passed=True,
            missing=False,
            reason="未发现编造亲历或朋友对话表述。",
        )
    evidence = [_span(output_text, h) for h in new_hits]
    return FidelityCheckItem(
        check_id="first_person",
        label="第一人称经历新增",
        severity=FidelitySeverity.CRITICAL,
        passed=False,
        missing=False,
        evidence=evidence,
        reason=(
            "发现编造个人经历/朋友对话/无来源表述："
            + "、".join(new_hits)
        ),
    )


def _number_units(text: str) -> set[str]:
    """数字+紧跟单位词的成对提取（如 90 分钟、30%）。"""
    result: set[str] = set()
    for m in _NUMBER_RE.finditer(text):
        tail = text[m.end(): m.end() + 4]
        unit = _UNIT_RE.search(tail)
        if unit:
            result.add(f"{m.group(0)}{unit.group(0)}")
    return result


def _protected_numbers(case: HumanizeCase) -> set[str]:
    """从保护项中提取必须保留的数字（无原文 case 的保真依据）。"""
    return {
        m.group(0)
        for item in case.protected_items
        for m in _NUMBER_RE.finditer(item)
    }


def run_fidelity_check(
    case: HumanizeCase, output_text: str
) -> FidelityReport:
    """对候选输出执行全部最小保真检查（失败关闭：缺失 = 未通过）。"""
    if not output_text.strip():
        raise FidelityCheckError(
            f"案例 {case.case_id} 输出为空，保真检查失败关闭。"
        )
    source_text = case.source_text or ""
    # 无原文的 case（如纯聊天）：数字/单位等保留依据来自保护项。
    numbers_origin = _extract(_NUMBER_RE, source_text) or _protected_numbers(case)
    units_origin = _number_units(source_text) or _number_units(
        "".join(case.protected_items)
    )
    materials_values: set[str] = set()
    for material in case.allowed_materials:
        materials_values.update(_NUMBER_RE.findall(material))
        materials_values.update(_DATE_RE.findall(material))
        materials_values.update(_URL_RE.findall(material))

    def _na(check_id: str, label: str, severity: FidelitySeverity) -> FidelityCheckItem:
        """案例设计上无此内容可查（有记录，不算缺失）。"""
        return FidelityCheckItem(
            check_id=check_id,
            label=label,
            severity=severity,
            passed=True,
            missing=False,
            not_applicable=True,
            reason=f"该案例没有可查的{label}内容，如实记录为不适用。",
        )

    checks: list[FidelityCheckItem] = []

    # 数字：原文（或保护项）数字必须保留；新增数字必须来自允许材料。
    if not numbers_origin:
        checks.append(_na("numbers", "数字", FidelitySeverity.CRITICAL))
    else:
        checks.append(
            _check_values(
                check_id="numbers",
                label="数字",
                severity=FidelitySeverity.CRITICAL,
                source_values=numbers_origin,
                output_values=_extract(_NUMBER_RE, output_text),
                materials_values=materials_values,
                source_text=source_text,
                output_text=output_text,
                protected_extra=[],
            )
        )

    # 单位：带单位的数字成对保留。
    if not units_origin:
        checks.append(_na("units", "数字+单位", FidelitySeverity.MAJOR))
    else:
        checks.append(
            _check_values(
                check_id="units",
                label="数字+单位",
                severity=FidelitySeverity.MAJOR,
                source_values=units_origin,
                output_values=_number_units(output_text),
                materials_values=set(),
                source_text=source_text,
                output_text=output_text,
                protected_extra=[],
            )
        )

    # 日期。
    date_values = _extract(_DATE_RE, source_text)
    if not date_values:
        checks.append(_na("dates", "日期", FidelitySeverity.MAJOR))
    else:
        checks.append(
            _check_values(
                check_id="dates",
                label="日期",
                severity=FidelitySeverity.MAJOR,
                source_values=date_values,
                output_values=_extract(_DATE_RE, output_text),
                materials_values=materials_values,
                source_text=source_text,
                output_text=output_text,
                protected_extra=[],
            )
        )

    # 专名与保护短语：文章逐字保留；聊天只查保护项数字（语义描述不逐字）。
    if case.kind is HumanizeCaseKind.ARTICLE:
        proper_nouns = [
            p
            for p in case.protected_items
            if not _NUMBER_RE.search(p)
            and not _DATE_RE.search(p)
            and not _URL_RE.search(p)
            and "“" not in p
        ]
        checks.append(
            _check_values(
                check_id="proper_nouns",
                label="专名与保护短语",
                severity=FidelitySeverity.CRITICAL,
                source_values=set(),
                output_values=set(),
                materials_values=set(),
                source_text=source_text,
                output_text=output_text,
                protected_extra=proper_nouns,
            )
        )
    else:
        checks.append(_na("proper_nouns", "专名与保护短语", FidelitySeverity.CRITICAL))

    # 精确引语。
    quote_values = {
        q for pair in _QUOTE_RE.findall(source_text) for q in pair if q.strip()
    }
    if not quote_values:
        checks.append(_na("quotes", "精确引语", FidelitySeverity.CRITICAL))
    else:
        checks.append(
            _check_values(
                check_id="quotes",
                label="精确引语",
                severity=FidelitySeverity.CRITICAL,
                source_values=quote_values,
                output_values={
                    q
                    for pair in _QUOTE_RE.findall(output_text)
                    for q in pair
                    if q.strip()
                },
                materials_values=set(),
                source_text=source_text,
                output_text=output_text,
                protected_extra=[],
            )
        )

    # URL。
    url_values = _extract(_URL_RE, source_text)
    if not url_values:
        checks.append(_na("urls", "URL", FidelitySeverity.CRITICAL))
    else:
        checks.append(
            _check_values(
                check_id="urls",
                label="URL",
                severity=FidelitySeverity.CRITICAL,
                source_values=url_values,
                output_values=_extract(_URL_RE, output_text),
                materials_values=materials_values,
                source_text=source_text,
                output_text=output_text,
                protected_extra=[],
            )
        )

    # 否定词：原文否定短语必须保留（否定词+后续语境 4 字符）。
    # 先剥离中文引号，避免引号字符差异造成误报。
    def _strip_quotes(text: str) -> str:
        return text.replace("“", "").replace("”", "").replace("「", "").replace("」", "")

    negation_sources = {
        f"{m.group(1)}{m.group(2)[:4]}"
        for m in _NEGATION_RE.finditer(_strip_quotes(source_text))
    }
    if not negation_sources:
        checks.append(_na("negations", "否定词与否定边界", FidelitySeverity.CRITICAL))
    else:
        negation_outputs = {
            f"{m.group(1)}{m.group(2)[:4]}"
            for m in _NEGATION_RE.finditer(_strip_quotes(output_text))
        }
        lost_negations = sorted(negation_sources - negation_outputs)
        checks.append(
            FidelityCheckItem(
                check_id="negations",
                label="否定词与否定边界",
                severity=FidelitySeverity.CRITICAL,
                passed=not lost_negations,
                missing=False,
                evidence=[_span(source_text, n) for n in lost_negations],
                reason=(
                    "否定边界丢失：" + "、".join(lost_negations)
                    if lost_negations
                    else "否定边界全部保留。"
                ),
            )
        )

    # 第一人称经历新增：文章案例必查（聊天案例原文缺失同样防御性检查）。
    checks.append(_check_first_person(case, output_text))

    report = FidelityReport(
        case_id=case.case_id,
        checks=checks,
        output_preview=output_text[:200],
    )
    report.require_complete(
        {"numbers", "units", "dates", "proper_nouns", "quotes", "urls",
         "negations", "first_person"}
    )
    return report
