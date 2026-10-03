"""``career.parse``：提取岗位锚点、阶段、城市与约束。

原词逐字保留：检索锚点由用户说出的岗位词组成，绝不把「算法工程师」换成
「数据分析师」这类相邻岗位。**只在岗位意图真的不足或含糊时**追问一个问题，
并把恢复载荷写回消息，下一条回复从该处继续。

改进工单 29：parse 只做确定性标注，不读画像；本轮额外区分「只查岗位」
与「个人准备」两条目的分支，并从**当前陈述**提取每天可用时间（长期默认
只在 ``career.background`` 节点读取）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from bridges.career_plan.contracts import (
    CareerBranch,
    CareerClarification,
    CareerRequestAnalysis,
)
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

#: 明示「只要岗位情报、不要个人建议」的覆盖要求：个人分支不启动。
_JOB_ONLY_RE = re.compile(
    r"只(?:看|查|要|想要|需要|给).{0,4}(?:岗位|招聘|职位|薪资|信息|分析)"
)

#: 个人准备意图的确定性特征（保守：只在明确表达准备/差距/规划时命中）。
_PERSONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:该|如何|怎么|怎样).{0,6}(?:准备|提升|补强|补齐|上手|入行)"),
    re.compile(r"(?:适合|匹配).{0,2}(?:我|自己)"),
    re.compile(r"我(?:的|目前|现在)?.{0,4}(?:差距|短板|欠缺|不足)"),
    re.compile(r"我(?:还|下一步|现在)?(?:需要|要|得|应该).{0,4}(?:学|补|提升|准备|练)"),
    re.compile(r"(?:个人|自己|我的).{0,4}(?:准备|规划|发展|提升)"),
    re.compile(r"给(?:我|自己).{0,6}(?:建议|规划|方案)"),
    re.compile(r"准备(?:一?下)?.{0,6}(?:求职|应聘|面试|实习|校招|秋招|春招|工作)"),
    re.compile(r"(?:求职|应聘|面试|校招|秋招|春招|实习)(?:该|要|怎么|如何)?.{0,4}准备"),
    re.compile(r"从哪(?:里|儿).{0,2}(?:开始|下手)"),
    re.compile(r"能(?:拿到|找到|进|上岸)"),
    re.compile(r"(?:简历|个人材料).{0,6}(?:改|优化|建议|怎么写)"),
    re.compile(r"帮我(?:规划|安排|制定).{0,10}(?:学习|准备|提升|路线)"),
)

_CN_DIGITS: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

#: 每天可用时间：只认按「天/日」给出的时段，不把每周/每月折算成每天。
_TIME_BUDGET_RE = re.compile(
    r"每(?:天|日)(?:大概|大约|约|可以|能|最多|至少|只有|可用|抽出|安排|有)?"
    r"[^\d一二两三四五六七八九十半]{0,4}?"
    r"(?P<value>\d+(?:\.\d+)?|半|[一二两三四五六七八九十]+)"
    r"\s*(?:个)?(?P<unit>小时|钟头|分钟|min(?:ute)?s?|h)"
)


def detect_personal_planning(text: str) -> bool:
    """是否明确要求个人准备建议；显式「只要岗位」优先。"""

    value = (text or "").strip()
    if not value or _JOB_ONLY_RE.search(value):
        return False
    return any(pattern.search(value) for pattern in _PERSONAL_PATTERNS)


def detect_time_budget(text: str) -> int | None:
    """从原话提取每天可用分钟数；无法确定时返回 None（不猜）。"""

    match = _TIME_BUDGET_RE.search(text or "")
    if match is None:
        return None
    amount = _cn_number(match.group("value"))
    if amount is None or amount <= 0:
        return None
    unit = match.group("unit")
    minutes = amount * 60 if unit in {"小时", "钟头", "h"} else amount
    rounded = int(round(minutes))
    return rounded if 1 <= rounded <= 24 * 60 else None


def _cn_number(text: str) -> float | None:
    """解析阿拉伯数字、半与十以内的中文数字（每天时段量级）。"""

    value = text.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    if value == "半":
        return 0.5
    if "十" in value:
        head, _, tail = value.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        units = _CN_DIGITS.get(tail, 0) if tail else 0
        return float(tens * 10 + units)
    if len(value) == 1 and value in _CN_DIGITS:
        return float(_CN_DIGITS[value])
    return None


def parse_career_request(
    content: str,
    *,
    pending: dict[str, Any] | None = None,
    task_texts: Sequence[str] = (),
) -> CareerRequestAnalysis:
    """解析本轮请求；``pending`` 是上一轮等待中的恢复载荷。

    恢复轮里这一条消息就是用户对上一轮提问的回答：岗位取自它，而原始请求
    与阶段／城市仍以首次提问为准（首次已给的信息不会被覆盖掉）。

    ``task_texts`` 是当前任务已确认的目标与条件原话（目标在前）。当前消息
    给出新的岗位词时按**新目标**解析，不把旧任务的城市／阶段悄悄套到新目标
    上；当前消息只是修订条件（如「换成杭州」）时，才回退到任务上下文补齐
    岗位与尚未改写的条件。任务条件的版本变化会进入 parse 的输入键，旧产物
    因此不会被当成当前数据复用。
    """
    current = content.strip()
    resumed = ""
    if pending:
        resumed = str(pending.get(PENDING_ORIGINAL_REQUEST) or "").strip()

    current_terms = extract_job_terms(current)
    if current_terms:
        # 新岗位目标：只从当前消息与恢复请求里取信息，旧任务条件不自动继承。
        terms = current_terms
        original = resumed or current
        cities = _first(detect_cities, current, resumed) or []
        stage = _first(detect_stage, current, resumed)
        graduation_year = detect_graduation_year(current) or detect_graduation_year(resumed)
        experience_hint = _first(detect_experience, current, resumed)
    else:
        # 条件修订／续接：岗位与未改写条件回退到已确认的任务目标与条件。
        contexts = [text for text in (*task_texts, resumed, current) if text]
        terms = next(
            (found for found in (extract_job_terms(text) for text in contexts) if found),
            [],
        )
        original = resumed or (task_texts[0] if task_texts else "") or current
        cities = _first(detect_cities, current, resumed, *task_texts) or []
        stage = _first(detect_stage, current, resumed, *task_texts)
        graduation_year = next(
            (
                year
                for year in (
                    detect_graduation_year(text)
                    for text in (current, resumed, *task_texts)
                )
                if year is not None
            ),
            None,
        )
        experience_hint = _first(detect_experience, current, resumed, *task_texts)

    family = family_for(terms[0]) if terms else None
    branch = (
        CareerBranch.PERSONAL_PLANNING
        if detect_personal_planning(current) or detect_personal_planning(resumed)
        else CareerBranch.JOB_INTEL
    )
    budget = detect_time_budget(current)
    if budget is None:
        budget = detect_time_budget(resumed)
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
        duty_intent=family.title if family is not None else (terms[0] if terms else None),
        branch=branch,
        time_budget_minutes=budget,
    )
    clarification = _clarification(terms)
    if clarification is None:
        return analysis
    return analysis.model_copy(update={"clarification": clarification})


def _first(detector: Any, *texts: str) -> Any:
    """按顺序取第一个有结果的原话检测（空文本跳过）。"""
    for text in texts:
        if not text:
            continue
        found = detector(text)
        if found:
            return found
    return None


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
