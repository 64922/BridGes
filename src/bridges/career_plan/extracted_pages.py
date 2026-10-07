"""复用搜索提供方实际提取的公开正文；搜索摘要永远不能进入岗位样本。"""

from __future__ import annotations

import re
from dataclasses import replace
from html import escape
from typing import TYPE_CHECKING

from bridges.career_plan.collecting import JobPageReadResult, parse_job_page
from bridges.career_plan.contracts import JobReadStatus
from bridges.career_plan.lexicon import CITY_TERMS, detect_job_titles

if TYPE_CHECKING:
    from bridges.career_plan.searching import CareerSearchHit

_DUTIES = ("岗位职责", "工作职责", "职位描述", "任职要求", "职位要求", "岗位要求")
_END = ("相似岗位", "相关职位", "推荐职位", "公司简介", "职位百科", "牛客安全提示")


def read_extracted_hit(hit: CareerSearchHit) -> JobPageReadResult | None:
    """仅在实际正文内同时定位岗位名、职责区时形成可继续核验的读取结果。"""
    if not hit.page_content or hit.page_fetched_at is None:
        return None
    lines = [re.sub(r"^[#*\s]+", "", line).strip() for line in hit.page_content.splitlines()]
    duty = next(
        (i for i, line in enumerate(lines) if line.strip("【】[]：: ") in _DUTIES), None,
    )
    if duty is None:
        return None
    title_index = next(
        (i for i, line in enumerate(lines[:duty])
         if len(line) <= 100 and "](" not in line and len(detect_job_titles(line)) == 1),
        None,
    )
    if title_index is None:
        return None
    title = lines[title_index]
    end = next(
        (i for i in range(duty + 1, len(lines)) if any(m in lines[i] for m in _END)),
        len(lines),
    )
    # 提取服务常把编号要求合成一段；恢复原有条款边界，避免一刀截断尾部要求。
    scope = [
        clause.strip() for line in lines[title_index + 1:end]
        for clause in re.split(r"\s+(?=\d{1,2}[、.．])", line)
    ]
    # 如牛客岗位卡单独列出「上海」，只从标题与职责之间的职位元信息读取。
    city = next((line for line in lines[title_index + 1:duty] if line in CITY_TERMS), None)
    html = f"<h1>{escape(title)}</h1>" + "".join(f"<p>{escape(line)}</p>" for line in scope)
    page = parse_job_page(html, reference=hit.page_fetched_at)
    if not page.is_job_posting or not any(
        line not in _DUTIES and len(line) > 8 for line in page.requirements
    ):
        return None
    page = replace(
        page, city=page.city or city,
        structure_note="取自 Tavily Extract 实际抓取的有界公开正文，非搜索摘要",
    )
    return JobPageReadResult(
        url=hit.url, status=JobReadStatus.PARTIAL, page=page,
        retrieved_at=hit.page_fetched_at,
    )
