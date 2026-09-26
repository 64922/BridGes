"""``career.parse``：提取岗位锚点、阶段、城市与约束。

原词逐字保留：检索锚点由用户说出的岗位词组成，绝不把「算法工程师」换成
「数据分析师」这类相邻岗位。**只在岗位意图真的不足或含糊时**追问一个问题，
并把恢复载荷写回消息，下一条回复从该处继续。
"""

from __future__ import annotations

from typing import Any

from bridges.career_plan.contracts import CareerClarification, CareerRequestAnalysis
from bridges.career_plan.lexicon import (
    adjacent_terms,
    detect_cities,
    detect_experience,
    detect_graduation_year,
    detect_stage,
    extract_job_terms,
    family_for,
    is_job_intent_ambiguous,
    synonym_terms,
)

#: 澄清问题的恢复载荷键（随消息持久化，跨会话重开仍然有效）。
PENDING_ORIGINAL_REQUEST = "original_request"

MISSING_JOB_QUESTION = (
    "你想找什么岗位？（例如：Java 后端开发、算法工程师、数据分析师、产品经理）"
)

AMBIGUOUS_JOB_QUESTION = (
    "「{term}」可能指几个不同的岗位，你想找哪一个？"
    "（例如：数据分析师、数据开发工程师、算法工程师）"
)

#: 请求里其他常见约束的原话特征（只记录，不解释成别的条件）。
CONSTRAINT_TERMS: tuple[str, ...] = (
    "不加班",
    "双休",
    "周末双休",
    "965",
    "955",
    "包吃住",
    "包住宿",
    "提供住宿",
    "转正",
    "可转正",
    "远程",
    "线上",
    "不出差",
    "不接受出差",
    "离家近",
    "国企",
    "央企",
    "事业单位",
    "大厂",
    "外企",
)


def parse_career_request(
    content: str,
    *,
    pending: dict[str, Any] | None = None,
) -> CareerRequestAnalysis:
    """解析本轮请求；``pending`` 是上一轮等待中的恢复载荷。

    恢复轮里这一条消息就是用户对上一轮提问的回答：岗位取自它，而原始请求
    与阶段／城市仍以首次提问为准（首次已给的信息不会被覆盖掉）。
    """
    current = content.strip()
    resumed = ""
    if pending:
        resumed = str(pending.get(PENDING_ORIGINAL_REQUEST) or "").strip()
    original = resumed or current

    terms = extract_job_terms(current) or extract_job_terms(original)
    family = family_for(terms[0]) if terms else None
    stage = detect_stage(original) or detect_stage(current)
    graduation_year = detect_graduation_year(original) or detect_graduation_year(current)
    cities = detect_cities(original) or detect_cities(current)
    experience_hint = detect_experience(original) or detect_experience(current)

    analysis = CareerRequestAnalysis(
        original_request=original,
        job_terms=terms,
        job_title=terms[0] if terms else None,
        family_title=family.title if family is not None else None,
        synonyms=synonym_terms(terms),
        adjacent_jobs=adjacent_terms(terms),
        stage=stage,
        graduation_year=graduation_year,
        cities=cities,
        experience_hint=experience_hint,
        constraints=_constraints(original, current),
    )
    clarification = _clarification(terms)
    if clarification is None:
        return analysis
    return analysis.model_copy(update={"clarification": clarification})


def _clarification(terms: list[str]) -> CareerClarification | None:
    """岗位意图不足或含糊时给出唯一要问的问题。"""
    if not terms:
        return CareerClarification(
            question=MISSING_JOB_QUESTION,
            reason="没有可检索的岗位锚点，先确认目标岗位才能发起检索。",
        )
    if is_job_intent_ambiguous(terms):
        return CareerClarification(
            question=AMBIGUOUS_JOB_QUESTION.format(term=terms[0]),
            reason=f"「{terms[0]}」跨多个岗位方向，先确认具体岗位才不会检索到别的岗位。",
        )
    return None


def _constraints(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for term in CONSTRAINT_TERMS:
            if term in text and term not in found:
                found.append(term)
    return found


def pending_payload(analysis: CareerRequestAnalysis) -> dict[str, Any]:
    """把等待中的请求写成可持久化的恢复载荷。"""
    return {PENDING_ORIGINAL_REQUEST: analysis.original_request}
