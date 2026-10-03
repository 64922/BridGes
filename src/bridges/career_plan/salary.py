"""薪资原文解析与可比性判定。

只做确定性解析，并且**保守**：只有原文明确给出计薪周期（``/月``、``/天``、
``/年``）或使用国内岗位页约定俗成的 ``K``（月薪千元）写法时才给出计薪单位。
``1.5-2万`` 这类没有周期标记的写法一律标为不可比较，并写明原因——宁可少算，
也不把不可比的薪资混进同一个区间。

薪资区间按**计薪单位与币种**分别归并：``元/月``、``元/天``、``元/年`` 三类互不混算，
``美元/月`` 等非人民币写法单独成组。``·15薪`` 只记录发薪月数，不把年度总额并
入月薪区间。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 币种（与计薪单位一起构成区间键）。
CURRENCY_CNY = "CNY"
CURRENCY_USD = "USD"

#: 币种中文标签（计薪单位前缀）。
_CURRENCY_LABELS: dict[str, str] = {
    CURRENCY_CNY: "元",
    CURRENCY_USD: "美元",
}

#: 计薪单位（互不混算的区间键）。
UNIT_MONTH = "元/月"
UNIT_DAY = "元/天"
UNIT_YEAR = "元/年"
UNIT_HOUR = "元/时"
UNIT_WEEK = "元/周"

#: 全部可比较的计薪单位。
COMPARABLE_UNITS: tuple[str, ...] = (UNIT_MONTH, UNIT_DAY, UNIT_YEAR, UNIT_HOUR, UNIT_WEEK)

#: 周期顺序（归并时的稳定排序）。
_PERIODS: tuple[str, ...] = ("月", "天", "年", "时", "周")

#: 没有数值的写法（不参与区间计算，原话照记）。
NON_NUMERIC_MARKERS: tuple[str, ...] = (
    "面议",
    "待遇从优",
    "薪资面议",
    "详谈",
    "不便透露",
)

#: 周期标记 → 计薪周期（先长后短匹配）。
_PERIOD_MARKERS: tuple[tuple[str, str], ...] = (
    ("元/月", "月"),
    ("/月", "月"),
    ("每月", "月"),
    ("月薪", "月"),
    ("元/天", "天"),
    ("/天", "天"),
    ("每天", "天"),
    ("日薪", "天"),
    ("/日", "天"),
    ("元/年", "年"),
    ("/年", "年"),
    ("每年", "年"),
    ("年薪", "年"),
    ("元/时", "时"),
    ("/时", "时"),
    ("每小时", "时"),
    ("元/周", "周"),
    ("/周", "周"),
    ("每周", "周"),
)

#: 币种标记（先认美元，避免「美元」里的「元」被当成人民币）。
_CURRENCY_MARKERS: tuple[tuple[str, str], ...] = (
    ("美元", CURRENCY_USD),
    ("美金", CURRENCY_USD),
    ("US$", CURRENCY_USD),
    ("USD", CURRENCY_USD),
    ("$", CURRENCY_USD),
    ("人民币", CURRENCY_CNY),
    ("元", CURRENCY_CNY),
    ("¥", CURRENCY_CNY),
    ("￥", CURRENCY_CNY),
)

#: 数值（含可选中文／字母倍率）：位置组为 ``(数值, 倍率)``。同一个模式里
#: 出现两次时按 1/2 与 3/4 两组读取（Python 的 ``re`` 不允许重复的组名）。
_NUMBER = r"(\d+(?:\.\d+)?)\s*([Kk千万])?"
_RANGE = re.compile(rf"{_NUMBER}\s*(?:[-~—－]|到|至)\s*{_NUMBER}")
_SINGLE = re.compile(_NUMBER)

#: 倍率。
_MULTIPLIERS: dict[str, int] = {"k": 1000, "千": 1000, "万": 10_000}

#: 发薪月数（``·15薪``、``13薪``）。
_MONTHS = re.compile(r"(?P<months>\d{1,2})\s*薪")


@dataclass(frozen=True)
class SalaryBand:
    """一条岗位薪资原文的解析结果。"""

    raw: str
    unit: str | None
    amount_min: int | None
    amount_max: int | None
    salary_months: int | None
    comparable: bool
    currency: str = CURRENCY_CNY
    note: str | None = None

    @property
    def midpoint(self) -> int | None:
        if self.amount_min is None:
            return None
        if self.amount_max is None:
            return self.amount_min
        return (self.amount_min + self.amount_max) // 2


def parse_salary(raw: str) -> SalaryBand:
    """解析一条薪资原文；无法比较时如实标注原因。"""
    text = " ".join((raw or "").split())
    currency = _currency(text)
    if not text:
        return SalaryBand(
            raw="", unit=None, amount_min=None, amount_max=None,
            salary_months=None, comparable=False, currency=currency,
            note="岗位页没有给出薪资原文。",
        )
    months = _salary_months(text)
    for marker in NON_NUMERIC_MARKERS:
        if marker in text:
            return SalaryBand(
                raw=text, unit=None, amount_min=None, amount_max=None,
                salary_months=months, comparable=False, currency=currency,
                note=f"薪资原文为「{marker}」，没有可比较的金额。",
            )
    period = _period(text)
    numbers = _numbers(text)
    if not numbers:
        return SalaryBand(
            raw=text, unit=None, amount_min=None, amount_max=None,
            salary_months=months, comparable=False, currency=currency,
            note="薪资原文里没有可解析的金额。",
        )
    low, high, used_k = numbers
    if period is None:
        if used_k:
            # ``15-25K`` 是国内岗位页的月薪约定写法：K 本身即「千元／月」。
            period = "月"
        else:
            return SalaryBand(
                raw=text, unit=None, amount_min=low, amount_max=high,
                salary_months=months, comparable=False, currency=currency,
                note=(
                    f"薪资原文「{text}」没有标注计薪周期（/月、/天、/年），"
                    "无法与其他岗位比较，因此不并入任何薪资区间。"
                ),
            )
    unit = _unit_for(currency, period)
    return SalaryBand(
        raw=text,
        unit=unit,
        amount_min=low,
        amount_max=high,
        salary_months=months,
        comparable=True,
        currency=currency,
        note=_comparable_note(text, unit, months),
    )


def currency_label(currency: str) -> str:
    """币种中文标签（未收录的币种原样返回）。"""
    return _CURRENCY_LABELS.get(currency, currency)


def _comparable_note(text: str, unit: str | None, months: int | None) -> str | None:
    parts = [f"计薪单位：{unit}"]
    if months is not None:
        parts.append(f"发薪月数：{months} 薪（未并入月薪区间）")
    return "；".join(parts)


def _numbers(text: str) -> tuple[int, int, bool] | None:
    """取出区间或单值（单位：元）；返回 ``(低, 高, 是否用了 K)``。"""
    match = _RANGE.search(text)
    if match is not None:
        low, low_mult, high, high_mult = match.group(1, 2, 3, 4)
        # 区间两端的倍率只写一次是常态（``15-25K``、``1.5-2万``），缺失的一端
        # 继承另一端。
        low_value = _to_yuan(low, low_mult or high_mult)
        high_value = _to_yuan(high, high_mult or low_mult)
        if low_value is None or high_value is None:
            return None
        if low_value > high_value:
            low_value, high_value = high_value, low_value
        return low_value, high_value, _is_k(high_mult or low_mult)
    single = _SINGLE.search(text)
    if single is None:
        return None
    value = _to_yuan(single.group(1), single.group(2))
    if value is None:
        return None
    return value, value, _is_k(single.group(2))


def _is_k(mult: str | None) -> bool:
    return (mult or "").casefold() == "k"


def _factor(mult: str | None) -> int | None:
    if not mult:
        return None
    return _MULTIPLIERS.get(mult.casefold())


def _to_yuan(num: str, mult: str | None) -> int | None:
    try:
        value = float(num)
    except ValueError:
        return None
    factor = _factor(mult)
    if factor is None:
        factor = 1
    return int(round(value * factor))


def _period(text: str) -> str | None:
    for marker, period in _PERIOD_MARKERS:
        if marker in text:
            return period
    return None


def _currency(text: str) -> str:
    for marker, currency in _CURRENCY_MARKERS:
        if _marker_present(text, marker):
            return currency
    return CURRENCY_CNY


def _marker_present(text: str, marker: str) -> bool:
    if marker.isascii() and marker.isalpha():
        return re.search(rf"(?<![A-Za-z]){re.escape(marker)}(?![A-Za-z])", text, re.I) is not None
    return marker in text


def _unit_for(currency: str, period: str) -> str:
    return f"{currency_label(currency)}/{period}"


def _salary_months(text: str) -> int | None:
    match = _MONTHS.search(text)
    if match is None:
        return None
    months = int(match.group("months"))
    # 12 薪是默认值（不写也等于 12），只在 13–24 之间才有信息量。
    return months if 13 <= months <= 24 else None


@dataclass(frozen=True)
class SalaryAggregate:
    """同一币种与计薪单位下的薪资区间（样本量、区间与原文都留痕）。"""

    unit: str
    sample_count: int
    amount_min: int
    amount_max: int
    amount_median: int
    raws: tuple[str, ...]
    currency: str = CURRENCY_CNY


def aggregate_salary(bands: list[SalaryBand]) -> tuple[list[SalaryAggregate], list[str]]:
    """按币种与计薪单位分别归并；返回 ``(区间列表, 未并入的原因说明)``。

    不同计薪单位绝不混算；不可比较的原文只留下原因，不进入任何区间。
    """
    grouped: dict[tuple[str, str], list[SalaryBand]] = {}
    notes: list[str] = []
    for band in bands:
        if not band.comparable or band.unit is None:
            if band.note:
                notes.append(f"「{band.raw}」未并入区间：{band.note}")
            continue
        grouped.setdefault((band.currency, band.unit), []).append(band)
    aggregates: list[SalaryAggregate] = []
    currencies = sorted(
        {currency for currency, _ in grouped}, key=lambda item: (item != CURRENCY_CNY, item)
    )
    for currency in currencies:
        for period in _PERIODS:
            items = grouped.get((currency, _unit_for(currency, period)))
            if not items:
                continue
            lows = [item.amount_min for item in items if item.amount_min is not None]
            highs = [item.amount_max for item in items if item.amount_max is not None]
            midpoints = sorted(
                value for item in items if (value := item.midpoint) is not None
            )
            if not lows or not highs:
                continue
            aggregates.append(
                SalaryAggregate(
                    unit=items[0].unit or _unit_for(currency, period),
                    sample_count=len(items),
                    amount_min=min(lows),
                    amount_max=max(highs),
                    amount_median=midpoints[len(midpoints) // 2],
                    raws=tuple(item.raw for item in items),
                    currency=currency,
                )
            )
    return aggregates, notes
