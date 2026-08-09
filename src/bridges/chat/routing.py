"""聊天自然语言能力路由（Issue 10）。

路由只做确定性识别和合同编译，不调用语言模型、不检索、不读取画像。
它的输出在任何副作用前落盘，重试时复用同一快照。
"""

from __future__ import annotations

import re
from typing import Any

from bridges.career.intake import assess_intake
from bridges.career.intent import is_career_intent
from bridges.contracts.career import CareerPlanningRouteContract
from bridges.contracts.routing import (
    ROUTING_CONTRACT_VERSION,
    RouteCapability,
    RouteConfidence,
    RouteDecision,
)

_MAX_NORMALIZED_QUERY_LENGTH = 2_000

_CAREER_ACTION_WORDS = (
    "规划",
    "方向",
    "路径",
    "选择",
    "转行",
    "求职",
    "找工作",
    "找实习",
    "该不该",
    "要不要",
    "适合",
    "打算",
    "考虑",
)
_CAREER_INFO_WORDS = ("是什么", "什么意思", "怎么理解", "概念", "介绍一下")
_LEARNING_WORDS = (
    "学习计划",
    "学习规划",
    "学习路线",
    "课程规划",
    "复习计划",
    "怎么复习",
    "怎么学",
    "学习方向",
)
_HUMANIZER_WORDS = (
    "润色",
    "改写",
    "改得自然",
    "更自然",
    "去模板腔",
    "人味",
    "优化简历",
    "润色简历",
)
_PAPER_ACTION_WORDS = (
    "搜索论文",
    "查论文",
    "找论文",
    "检索论文",
    "推荐论文",
    "筛选论文",
)
_IMAGE_WORDS = ("生成图片", "生成一张图", "做一张海报", "画一张", "生成图像")
_VIDEO_WORDS = ("生成视频", "做个视频", "制作视频", "生成短片", "做成短视频")


def classify_message(
    content: str,
    *,
    skill_payload: dict[str, Any] | None = None,
    image_payload: dict[str, Any] | None = None,
    video_payload: dict[str, Any] | None = None,
    mcp_call_payload: dict[str, Any] | None = None,
) -> RouteDecision:
    """为一条新消息编译一个可解释、互斥且可重放的路由快照。"""

    text = (content or "").strip()
    normalized = text[:_MAX_NORMALIZED_QUERY_LENGTH]
    explicit = [
        capability
        for payload, capability in (
            (skill_payload, RouteCapability.HUMANIZER),
            (image_payload, RouteCapability.IMAGE_GENERATION),
            (video_payload, RouteCapability.VIDEO_GENERATION),
            (mcp_call_payload, RouteCapability.MCP_CALL),
        )
        if payload is not None
    ]
    if len(explicit) > 1:
        return _clarification(
            normalized,
            explicit,
            "这条消息包含多个独立任务，本轮先做哪一项？",
            reason_code="multiple_explicit_capabilities",
        )
    if explicit:
        return RouteDecision(
            capability=explicit[0],
            confidence=RouteConfidence.HIGH,
            normalized_query=normalized,
            reason_code="explicit_payload",
        )

    signals = _text_signals(text)
    if len(signals) > 1:
        return _clarification(
            normalized,
            signals,
            "这条消息包含多个独立任务，本轮先做哪一项？",
            reason_code="multiple_text_capabilities",
        )
    if signals == [RouteCapability.CAREER_PLANNING]:
        return RouteDecision(
            capability=RouteCapability.CAREER_PLANNING,
            confidence=RouteConfidence.HIGH,
            normalized_query=normalized,
            reason_code="career_decision_request",
            career_contract=_compile_career_contract(text),
        )
    if signals:
        return RouteDecision(
            capability=signals[0],
            confidence=RouteConfidence.HIGH,
            normalized_query=normalized,
            reason_code=f"{signals[0].value}_request",
        )
    return RouteDecision(
        capability=RouteCapability.CHAT,
        confidence=RouteConfidence.MEDIUM,
        normalized_query=normalized,
        reason_code="no_registered_capability",
    )


def _text_signals(text: str) -> list[RouteCapability]:
    signals: list[RouteCapability] = []
    if _is_career_request(text):
        signals.append(RouteCapability.CAREER_PLANNING)
    if _is_learning_request(text):
        signals.append(RouteCapability.LEARNING_PLAN)
    if any(word in text for word in _HUMANIZER_WORDS):
        signals.append(RouteCapability.HUMANIZER)
    if "论文" in text and any(word in text for word in _PAPER_ACTION_WORDS):
        signals.append(RouteCapability.PAPER_SEARCH)
    if any(word in text for word in _IMAGE_WORDS):
        signals.append(RouteCapability.IMAGE_GENERATION)
    if any(word in text for word in _VIDEO_WORDS):
        signals.append(RouteCapability.VIDEO_GENERATION)
    return signals


def _is_career_request(text: str) -> bool:
    if not text or any(word in text for word in _CAREER_INFO_WORDS):
        return False
    if re.fullmatch(r".{0,20}职业(?:发展|方向)?前景(?:怎么样|如何)[？?。]?", text):
        return False
    if _is_learning_request(text) and not any(
        word in text for word in ("职业", "就业", "求职", "转行", "工作方向")
    ):
        return False
    if is_career_intent(text):
        if text.startswith(("生涯规划助手：", "生涯规划：", "职业规划：")):
            return True
        if any(
            word in text
            for word in _CAREER_ACTION_WORDS
            if word not in {"规划", "方向", "路径", "选择"}
        ):
            return True
        if any(word in text for word in ("怎么", "如何", "比较", "想做", "想规划", "帮我")):
            return True
        if re.search(r"规划.{0,20}(职业|就业|方向|路径|方案)", text):
            return True
        return False
    return bool(
        ("规划" in text and any(word in text for word in ("方向", "职业", "就业", "工作")))
        or
        re.search(r"适合.{0,12}(工作|岗位|职业)", text)
        or re.search(r"(科研|企业).{0,8}(选择|方向|发展)", text)
        or re.search(r"毕业后.{0,12}(去哪|做什么|方向)", text)
    )


def _is_learning_request(text: str) -> bool:
    return any(word in text for word in _LEARNING_WORDS)


def _clarification(
    normalized: str,
    capabilities: list[RouteCapability],
    question: str,
    *,
    reason_code: str,
) -> RouteDecision:
    return RouteDecision(
        capability=RouteCapability.CLARIFY,
        confidence=RouteConfidence.LOW,
        normalized_query=normalized,
        reason_code=reason_code,
        conflict_capabilities=capabilities,
        clarification=question,
    )


def _compile_career_contract(text: str) -> CareerPlanningRouteContract:
    target = "明确当前阶段可验证的职业方向，并比较可选路径"
    if "转行" in text:
        target = "评估是否转行，并比较转行与留在当前方向的可行路径"
    elif "求职" in text or "找工作" in text or "找实习" in text:
        target = "规划求职方向、准备重点和近期验证行动"
    elif "考研" in text and any(word in text for word in ("职业", "就业", "方向")):
        target = "规划考研后的职业方向，并比较继续深造与就业选择"
    elif "科研" in text and "企业" in text:
        target = "比较科研与企业路径，并明确当前阶段的验证行动"

    time_horizon = "未来 1-3 年（先做近 90 天验证）"
    time_match = re.search(
        r"(?:未来|接下来|近|之后)\s*([一二三四五六七八九十百\d]+\s*(?:个)?(?:月|年|周|天))",
        text,
    )
    if time_match:
        time_horizon = f"未来{time_match.group(1)}"
    elif "长期" in text:
        time_horizon = "长期（先做近 90 天验证）"

    constraints: list[str] = []
    location = re.search(
        r"(?:地点|城市|地区)\s*(?:先)?(?:考虑|限定|选择|是|为|：|:)\s*([^，。；,;\s]+)",
        text,
    )
    if location:
        constraints.append(f"地点：{location.group(1)}")
    for marker, label in (("不想", "用户不希望"), ("不能", "用户不能"), ("预算", "预算")):
        index = text.find(marker)
        if index >= 0:
            fragment = text[index : index + 24].rstrip("，。；,;")
            constraints.append(f"{label}：{fragment}")

    evidence_requirements = ["user_statement"]
    if any(word in text for word in ("知识库", "资料", "材料")):
        evidence_requirements.append("authorized_knowledge_base")
    if any(word in text for word in ("最新", "当前", "政策", "趋势", "岗位", "市场", "薪资")):
        evidence_requirements.append("current_search")

    intake = assess_intake(text)
    open_questions = [intake.question] if not intake.enough and intake.question else []
    return CareerPlanningRouteContract(
        target=target,
        time_horizon=time_horizon,
        constraints=constraints,
        evidence_requirements=evidence_requirements,
        image_usage=(
            "task_relevant_minimal_slice"
            if any(word in text for word in ("图片", "画像", "截图"))
            else "none"
        ),
        open_questions=open_questions,
    )


__all__ = [
    "RouteCapability",
    "RouteConfidence",
    "RouteDecision",
    "classify_message",
]
