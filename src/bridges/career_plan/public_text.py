"""公开职位正文的范围与地点；不从导航、公司地址推断工作地点。"""

from __future__ import annotations

import re

from bridges.career_plan.lexicon import CITY_TERMS

_H1 = re.compile(r"<h1\b[^>]*>.*?</h1>", re.IGNORECASE | re.DOTALL)
_RELATED = re.compile(r"<(?:h[1-6]|div|span|p)[^>]*>\s*(?:热招职位|相关职位|推荐职位|其他职位)")


def detail_scope(html: str, *, heading: str) -> str:
    """单岗位 h1 之后才是取证范围，相关岗位区不属于该岗位。"""
    match = _H1.search(html) if heading else None
    scoped = html[match.start():] if match else re.sub(
        r"<(head|nav|header|footer)\b.*?</\1>", " ", html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    related = _RELATED.search(scoped)
    if related is not None:
        scoped = scoped[:related.start()]
    return re.split(r"<footer\b", scoped, maxsplit=1, flags=re.IGNORECASE)[0]


def location_from_text(text: str) -> str | None:
    """只读取地点标签或职位元信息行；多个城市时保留地点原文。"""
    location = re.search(
        r"(?:工作地点|工作城市|工作地址|工作地区|职位地点|地点)\s*[:：]?\s*([^\n]+)",
        text,
    )
    if location:
        cities = [city for city in CITY_TERMS if city in location.group(1)]
        if cities:
            return "、".join(cities)
    for line in text.splitlines():
        # 部门｜实习｜工程通道｜上海：明确的职位元信息，不扫描公司介绍。
        if any(separator in line for separator in ("｜", "|", "·")):
            cities = [city for city in CITY_TERMS if city in line]
            if cities and any(word in line for word in ("实习", "校招", "社招", "招聘")):
                return "、".join(cities)
    return None


def requirements_text(text: str, markers: tuple[str, ...]) -> str:
    """职责标记之后的原文才进入能力提取上限，页头不消耗要求条数。"""
    positions = [text.find(marker) for marker in markers if marker in text]
    return text[min(positions):] if positions else text
