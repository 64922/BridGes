"""学习模式证据门与聊天教学轮次编排（Issue 08：有状态对话式教学循环）。

状态机：``mission_setup``（确认目标/用途/已有水平，不过证据门）→
``micro_lesson``（基于合格来源一次讲一个概念）→ ``understanding_check``
（每轮最多一道低负担问题）→ ``adaptation``（依据回答证据选择补讲、换
例子或下一概念）；来源受阻时进入 ``blocked`` 保留 mission 与恢复动作。
mission 随 ``TeachingTurnProjection.mission`` 在会话中持久化，刷新/离开
后继续同一教学进度。
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256

from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.contracts.learning import AnswerEvaluatedState
from bridges.contracts.retrieval import RetrievalRoundProjection, RetrievalSufficiency
from bridges.contracts.teaching import (
    TeachingAnswerEvidence,
    TeachingArtifactStatus,
    TeachingCardStatus,
    TeachingEvidenceGate,
    TeachingEvidenceSource,
    TeachingEvidenceSourceType,
    TeachingEvidenceStatus,
    TeachingIntent,
    TeachingKnowledgeState,
    TeachingLessonProjection,
    TeachingMission,
    TeachingPlanProjection,
    TeachingPlanSession,
    TeachingProfileUsage,
    TeachingQuiz,
    TeachingSearchSource,
    TeachingStage,
    TeachingTurnProjection,
)
from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus

_PAPER_QUERY = re.compile(r"arxiv|论文|文献|期刊|研究综述", re.IGNORECASE)
_PUBLIC_QUERY = re.compile(r"最新|公开|网页|新闻|现状|政策|规范|官方|事实", re.IGNORECASE)
_SKIP_ANSWER = re.compile(
    r"^(?:跳过|跳过这题|跳过这道理解检查(?:，|,)?.*|先跳过|不答|暂时不答|skip)\s*[。.!！]?$",
    re.I,
)
#: 整句立场即否定（"我不懂 X"/"我完全不懂"）：直接清空关键点匹配，判 incorrect。
#: 不使用 \b（CJK 字符都是 \w，词边界对中文无效）。
_STRONG_UNKNOWN = re.compile(
    r"^(?:我就是|我(?:完全|一点也|根本|实在)?|完全|一点也|根本|实在)?"
    r"(?:不懂|不知道|不会|没学会|不明白|不理解|不清楚)",
    re.I,
)
#: 句中自认缺口（"其他细节我还不清楚"）：覆盖关键点时打折为 partial。
_UNCERTAIN_ANSWER = re.compile(r"不清楚|不知道|没学会|不明白|不理解|不确定|还没弄懂|不太会|还不会")
_FOLLOW_UP = re.compile(
    r"(?:为什么|为何|怎么|如何|能不能|可不可以|请再解释|再讲|举例|什么意思|"
    r"哪里|继续讲|追问)"
)
#: 学习意图（建立目标）动词；无 mission 时的教学对话默认进入目标确认。
_LEARNING_INTENT = re.compile(
    r"^(?:请|帮我)?(?:我想|我想要|希望|想|要|教我|教教|讲讲|学|学习|了解|认识|入门|弄懂|搞清楚)\s*[:：]?\s*\S+|"
    r"^(?:学习目标|目标)\s*(?:是|为)?\s*[:：]?\s*\S+",
    re.I,
)
_LEARNING_GOAL_MARKER = re.compile(
    r"学习|学一下|教我|入门|弄懂|搞清楚|学习目标|目标是|制定学习计划",
    re.I,
)
_MISSING_GOAL = re.compile(
    r"^(?:请|帮我|我想要?|希望|想要?|要)?\s*"
    r"(?:(?:学习目标|目标)\s*(?:是|为)?|"
    r"(?:学习|学一下|学|教我|教教我|讲讲|制定学习计划|学习计划))"
    r"\s*(?:一下|吧|呢)?[。！？!?，,：:]*$",
    re.I,
)
_VAGUE_GOAL = re.compile(r"^(?:点东西|一些东西|某个东西|什么|某个主题|某个概念)$")
#: 主题提取的主/次动词词表（与 _LEARNING_INTENT/_MODIFY_MISSION 共用语义）。
_TOPIC_VERB_PREFIX = re.compile(
    r"^(?:请|帮我)?(?:我想要|我想|想要|想|要|教我|教教|讲讲|解释一下|介绍一下|解释|"
    r"换个主题|换|改成|重新学|重新)?"
    r"(?:学习|学一下|学|了解|认识|入门|弄懂|搞清楚)?\s*",
    re.I,
)
#: mission 确认阶段的「按初学者开始」一键动作。
_BEGINNER_START = re.compile(
    r"^(?:按|就以|就按)?(?:初学者|零基础|新手)(?:开始|起步|来吧|水平|处理)?\s*[。.!！]?$",
    re.I,
)
#: 修改学习目标（有 mission 时换主题）。
_MODIFY_MISSION = re.compile(
    r"^(?:换|改|换个|改成|重新|重学|不想学|不学|退出)\s*.*(?:主题|目标|概念|学|学习)?|"
    r"^(?:我想|想要|想)(?:学|学习|了解|认识)\s*\S+",
    re.I,
)
#: 已有基础声明的识别（确认阶段回答/作答中的水平信息）。
_KNOWN_LEVEL = re.compile(r"学过|知道|了解|会|熟悉|用过|接触过|懂|明白", re.I)
#: 切换模式的显式表达。
_SWITCH_MODE = re.compile(r"切回日常|切换模式|回到日常|日常陪伴|不要教学|退出学习", re.I)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(10)}"


def _snapshot_id(prefix: str, *values: str) -> str:
    payload = "\x1f".join(values).encode("utf-8")
    return f"{prefix}-{sha256(payload).hexdigest()[:16]}"


def _is_follow_up(text: str) -> bool:
    """把追问与理解检查作答分开，避免把普通问题写成答题证据。"""

    normalized = text.strip()
    return normalized.endswith(("？", "?")) or bool(_FOLLOW_UP.search(normalized))


def _local_sources(retrieval: RetrievalRoundProjection | None) -> list[TeachingEvidenceSource]:
    if retrieval is None:
        return []
    return [
        TeachingEvidenceSource(
            source_type=TeachingEvidenceSourceType(citation.source_layer.value),
            source_id=citation.citation_id,
            title=citation.filename,
            locator=(
                " · ".join(
                    part
                    for part in (
                        f"第 {citation.page_number} 页" if citation.page_number else None,
                        citation.section_title,
                    )
                    if part
                )
                or None
            ),
            accessed_at=retrieval.created_at,
        )
        # Issue 08：图片没有可用 OCR/文本证据时不能成为教学引用。
        for citation in retrieval.citations
        if not citation.media_type.startswith("image/")
    ]


def _web_sources(web_search: WebSearchProjection | None) -> list[TeachingEvidenceSource]:
    if web_search is None or web_search.status not in {
        WebSearchStatus.SUCCESS,
        WebSearchStatus.PARTIAL,
    }:
        return []
    return [
        TeachingEvidenceSource(
            source_type=TeachingEvidenceSourceType.DUCKDUCKGO,
            source_id=result.result_id,
            title=result.title,
            locator=result.url,
            url=result.url,
            accessed_at=result.accessed_at,
        )
        for result in web_search.results
        if result.verification in {"verified", "cross_verified"}
    ]


def _arxiv_sources(arxiv_search: ArxivSearchProjection | None) -> list[TeachingEvidenceSource]:
    if arxiv_search is None or arxiv_search.status != ArxivSearchStatus.SUCCESS:
        return []
    return [
        TeachingEvidenceSource(
            source_type=TeachingEvidenceSourceType.ARXIV,
            source_id=paper.citation_id,
            title=paper.title,
            locator=f"arXiv:{paper.arxiv_id}",
            url=paper.abs_url,
            alternate_url=paper.pdf_url,
            accessed_at=arxiv_search.searched_at or _now(),
        )
        for paper in arxiv_search.papers
    ]


def _external_sources(
    required: TeachingSearchSource,
    web_search: WebSearchProjection | None,
    arxiv_search: ArxivSearchProjection | None,
) -> list[TeachingEvidenceSource]:
    """只把本轮证据门实际要求且成功返回的公开来源纳入证据。"""

    sources: list[TeachingEvidenceSource] = []
    if required in {TeachingSearchSource.DUCKDUCKGO, TeachingSearchSource.BOTH}:
        sources.extend(_web_sources(web_search))
    if required in {TeachingSearchSource.ARXIV, TeachingSearchSource.BOTH}:
        sources.extend(_arxiv_sources(arxiv_search))
    return sources


def _local_is_stale(retrieval: RetrievalRoundProjection | None) -> bool:
    """识别检索层返回的过时/取代提示，并强制进入公开补充流程。"""

    if retrieval is None:
        return False
    notes = [retrieval.note or ""]
    notes.extend(layer.note or "" for layer in retrieval.layers)
    return bool(re.search(r"过时|已取代|旧版|stale|superseded|outdated", " ".join(notes), re.I))


class TeachingEvidenceGateService:
    """把三层本地检索和公开搜索结果收敛为教学裁决。"""

    def assess(
        self,
        query: str,
        retrieval: RetrievalRoundProjection | None,
        web_search: WebSearchProjection | None = None,
        arxiv_search: ArxivSearchProjection | None = None,
    ) -> TeachingEvidenceGate:
        local = _local_sources(retrieval)
        local_status = retrieval.sufficiency if retrieval is not None else None
        local_stale = _local_is_stale(retrieval)
        # Issue 08：本地命中全部是没有文本证据的图片时，视为无本地命中，
        # 强制公开补充——图片不能支撑教学断言。
        if retrieval is not None and retrieval.citations and not local:
            local_status = RetrievalSufficiency.NO_HITS
            local_stale = False
        required = self.required_search(query, None if local_stale else local_status)
        external = _external_sources(required, web_search, arxiv_search)

        if local_status == RetrievalSufficiency.SUFFICIENT and local and not local_stale:
            return TeachingEvidenceGate(
                status=TeachingEvidenceStatus.SUFFICIENT,
                reason="当前附件、项目文件或授权知识库已覆盖本轮教学目标。",
                local_sources=local,
                required_search=TeachingSearchSource.NONE,
                search_status=None,
                checked_at=_now(),
            )

        if local_stale:
            local_reason = "本地材料被标记为过时或已取代，不能直接作为当前教学依据。"
            base_status = TeachingEvidenceStatus.INSUFFICIENT
        elif local_status == RetrievalSufficiency.CONFLICT:
            local_reason = "本地材料存在冲突，不能直接合并为单一结论。"
            base_status = TeachingEvidenceStatus.CONFLICT
        elif local_status == RetrievalSufficiency.INDEX_UNAVAILABLE:
            local_reason = "本地索引不可用，无法确认材料是否覆盖教学目标。"
            base_status = TeachingEvidenceStatus.UNAVAILABLE
        elif local_status is None or local_status == RetrievalSufficiency.NO_HITS:
            local_reason = "当前附件、项目文件和授权知识库没有可用命中。"
            base_status = TeachingEvidenceStatus.INSUFFICIENT
        else:
            local_reason = "本地材料存在，但不足以覆盖本轮教学目标。"
            base_status = TeachingEvidenceStatus.INSUFFICIENT

        search_status = self._search_status(web_search, arxiv_search, required)
        if search_status in {TeachingCardStatus.ERROR, TeachingCardStatus.PERMISSION}:
            return TeachingEvidenceGate(
                status=TeachingEvidenceStatus.UNAVAILABLE,
                reason=f"{local_reason}公开补充检索未完成，不能用模型记忆替代来源。",
                local_sources=local,
                external_sources=external,
                required_search=required,
                search_status=search_status,
                gap="公开补充来源当前不可用，因此本轮不能可靠断言关键科学结论。",
                recovery_steps=["检查网络或权限后重试；也可以上传或选择一份可用材料。"],
                checked_at=_now(),
            )

        if search_status is None:
            return TeachingEvidenceGate(
                status=TeachingEvidenceStatus.UNAVAILABLE,
                reason=f"{local_reason}公开补充检索尚未完成，不能用模型记忆替代来源。",
                local_sources=local,
                external_sources=external,
                required_search=required,
                search_status=None,
                gap="公开补充来源尚未完成，因此本轮不能可靠断言关键科学结论。",
                recovery_steps=["重试公开检索，或上传一份与目标直接相关的材料。"],
                checked_at=_now(),
            )

        if search_status == TeachingCardStatus.EMPTY:
            return TeachingEvidenceGate(
                status=base_status,
                reason=f"{local_reason}公开补充检索没有返回可用结果。",
                local_sources=local,
                external_sources=external,
                required_search=required,
                search_status=search_status,
                gap="没有找到能覆盖本轮目标的公开来源，暂不能可靠断言关键结论。",
                recovery_steps=["重试公开检索，或上传一份与目标直接相关的材料。"],
                checked_at=_now(),
            )

        if external:
            resolved_status = (
                TeachingEvidenceStatus.CONFLICT
                if base_status == TeachingEvidenceStatus.CONFLICT
                else TeachingEvidenceStatus.SUFFICIENT
            )
            return TeachingEvidenceGate(
                status=resolved_status,
                reason=(
                    f"{local_reason}已补充公开来源；"
                    "冲突材料仍需按来源分别核对。"
                    if resolved_status == TeachingEvidenceStatus.CONFLICT
                    else "本地证据不足，已使用公开来源补充教学必需知识。"
                ),
                local_sources=local,
                external_sources=external,
                required_search=required,
                search_status=search_status,
                gap=(
                    "本地材料存在冲突；请分别核对来源的定义、适用范围和时间。"
                    if resolved_status == TeachingEvidenceStatus.CONFLICT
                    else None
                ),
                recovery_steps=["展开每条来源核对原文，再继续回答。"],
                checked_at=_now(),
            )

        return TeachingEvidenceGate(
            status=base_status,
            reason=f"{local_reason}需要先补充公开来源。",
            local_sources=local,
            required_search=required,
            search_status=search_status,
            gap=(
                "没有找到能覆盖本轮目标的公开来源，暂不能可靠断言关键结论。"
                if search_status is not None and search_status.value == "empty"
                else None
            ),
            recovery_steps=["重试公开检索，或上传一份与目标直接相关的材料。"],
            checked_at=_now(),
        )

    @staticmethod
    def required_search(
        query: str, local_status: RetrievalSufficiency | None
    ) -> TeachingSearchSource:
        if local_status == RetrievalSufficiency.SUFFICIENT:
            return TeachingSearchSource.NONE
        has_paper = bool(_PAPER_QUERY.search(query))
        has_public = bool(_PUBLIC_QUERY.search(query))
        if has_paper and has_public:
            return TeachingSearchSource.BOTH
        return (
            TeachingSearchSource.ARXIV
            if has_paper
            else TeachingSearchSource.DUCKDUCKGO
        )

    @staticmethod
    def _search_status(
        web_search: WebSearchProjection | None,
        arxiv_search: ArxivSearchProjection | None,
        required: TeachingSearchSource,
    ) -> TeachingCardStatus | None:
        projections: list[TeachingCardStatus] = []
        if required in {TeachingSearchSource.DUCKDUCKGO, TeachingSearchSource.BOTH}:
            if web_search is None:
                return None
            projections.append(_web_status(web_search.status))
        if required in {TeachingSearchSource.ARXIV, TeachingSearchSource.BOTH}:
            if arxiv_search is None:
                return None
            projections.append(_arxiv_status(arxiv_search.status))
        if any(status == TeachingCardStatus.PERMISSION for status in projections):
            return TeachingCardStatus.PERMISSION
        if any(status == TeachingCardStatus.ERROR for status in projections):
            return TeachingCardStatus.ERROR
        if any(status == TeachingCardStatus.EMPTY for status in projections):
            return TeachingCardStatus.EMPTY
        return projections[0] if projections else None


class TeachingTurnService:
    """生成统一聊天流中的目标、步骤、理解检查和回答证据。"""

    def __init__(self, gate: TeachingEvidenceGateService | None = None) -> None:
        self._gate = gate or TeachingEvidenceGateService()

    def required_search(
        self, query: str, retrieval: RetrievalRoundProjection | None
    ) -> TeachingSearchSource:
        """在本地检索后确定公开来源，避免为决定来源重复构造教学轮次。"""

        local_status = (
            None
            if _local_is_stale(retrieval)
            else retrieval.sufficiency
            if retrieval is not None
            else None
        )
        return self._gate.required_search(
            query, local_status
        )

    # ------------------------------------------------------------------
    # Issue 08：意图分类与有状态教学循环
    # ------------------------------------------------------------------

    def classify_intent(
        self, text: str, mission: TeachingMission | None
    ) -> TeachingIntent:
        """分类用户消息意图，不用单一关键词正则决定全部教学行为。"""
        stripped = text.strip()
        if _SWITCH_MODE.search(stripped):
            return TeachingIntent.SWITCH_MODE
        if mission is None:
            if self.has_executable_goal(stripped) or self.is_missing_goal(stripped):
                return TeachingIntent.ESTABLISH_MISSION
            return TeachingIntent.FACT_QUESTION
        if mission.stage == TeachingStage.MISSION_SETUP:
            # 确认阶段：换主题应重新确认目标，而不是被当作原主题的确认。
            if _MODIFY_MISSION.search(stripped):
                return TeachingIntent.MODIFY_MISSION
            # 按初学者开始或回答澄清问题都是确认输入。
            return TeachingIntent.ANSWER
        if _SKIP_ANSWER.fullmatch(stripped):
            return TeachingIntent.SKIP
        if _MODIFY_MISSION.search(stripped):
            return TeachingIntent.MODIFY_MISSION
        if _is_follow_up(stripped):
            return TeachingIntent.FOLLOW_UP
        return TeachingIntent.ANSWER

    def mission_setup(
        self,
        query: str,
        *,
        previous_mission: TeachingMission | None = None,
        mission_id: str | None = None,
    ) -> TeachingTurnProjection:
        """建立或修改目标：确认目标/用途/已有水平，不过证据门、不检索。"""
        topic = self._topic(query)
        goal = f"学习“{topic}”并理解其核心机制"
        if previous_mission is not None and previous_mission.stage != TeachingStage.MISSION_SETUP:
            goal = f"把目标调整为：学习“{topic}”并理解其核心机制"
        has_goal = self.has_executable_goal(query)
        starts_new_goal = has_goal and (
            previous_mission is None or bool(_MODIFY_MISSION.search(query))
        )
        mission = TeachingMission(
            mission_id=(
                mission_id
                if starts_new_goal and mission_id is not None
                else (
                    previous_mission.mission_id
                    if previous_mission is not None
                    else mission_id or _new_id("mission")
                )
            ),
            stage=TeachingStage.MISSION_SETUP,
            goal=goal if has_goal else "等待补充学习目标",
            user_intent=query,
            current_concept=None,
            level_assumption="暂按初学者处理；你的回答会调整深度与例子。",
            level_basis="尚未确认，默认按初学者开始。",
            taught_concepts=(
                previous_mission.taught_concepts if previous_mission is not None else []
            ),
            next_action=(
                "请先告诉我你想学习的主题或可执行目标。"
                if not has_goal
                else "可以直接按初学者开始，我会根据证据生成计划和第一课。"
            ),
        )
        gate = TeachingEvidenceGate(
            status=TeachingEvidenceStatus.SUFFICIENT,
            reason="目标确认阶段不生成正式教学回答，无需通过事实证据门。",
            required_search=TeachingSearchSource.NONE,
            search_status=None,
            gap=None,
            recovery_steps=[],
            checked_at=_now(),
        )
        return TeachingTurnProjection(
            status=TeachingCardStatus.READY,
            mission=mission,
            goal=goal,
            level_assumption=mission.level_assumption,
            steps=["确认学习目标", "确认你的已有水平", "开始第一个概念"],
            check_method="本轮只问一个关键问题；也可以直接按初学者开始。",
            evidence_gate=gate,
            quiz=None,
            evidence=[],
            next_prompt=(
                "你想学习什么主题？请给出一个具体目标，例如“学习 Transformer 的核心机制”。"
                if not has_goal
                else "我会先按中性初学者起点开始；如果你已有基础，后续可以再调整难度。"
            ),
            gap_response=None,
            can_answer_reliably=False,
            can_cancel=True,
            can_retry=False,
            can_skip=True,
            can_follow_up=True,
            can_switch_mode=True,
        )

    def has_executable_goal(self, text: str) -> bool:
        """判断当前消息是否已经包含可执行的学习目标。"""

        normalized = text.strip()
        if self.is_missing_goal(normalized):
            return False
        topic = self._topic(normalized)
        return (
            topic != "当前概念"
            and not _VAGUE_GOAL.fullmatch(topic.strip())
            and bool(
                _LEARNING_INTENT.search(normalized)
                or _MODIFY_MISSION.search(normalized)
            )
            and len(topic.strip()) > 1
        )

    @staticmethod
    def is_missing_goal(text: str) -> bool:
        """识别“想学但没有主题”的消息，且不触发检索或模型。"""

        normalized = text.strip()
        if not _LEARNING_GOAL_MARKER.search(normalized):
            return False
        return bool(_MISSING_GOAL.fullmatch(normalized))

    def confirm_mission(self, text: str, mission: TeachingMission) -> TeachingMission:
        """确认阶段输入：解析水平假设，确定首概念，推进到 micro_lesson。"""
        # 否定（"不知道/没学过"）优先于关键词（"知道"）——"我不知道" 不算已有基础。
        has_level = bool(_KNOWN_LEVEL.search(text)) and not _STRONG_UNKNOWN.search(text)
        if _BEGINNER_START.fullmatch(text.strip()) or not has_level:
            level_assumption = "初学者"
            level_basis = "用户选择按初学者开始（或未声明已有基础）。"
        else:
            level_assumption = "已有基础"
            level_basis = "用户声明接触过该主题；讲解会更快并预留迁移练习。"
        topic = self._topic(mission.user_intent)
        return mission.model_copy(
            update={
                "stage": TeachingStage.MICRO_LESSON,
                "current_concept": topic,
                "level_assumption": level_assumption,
                "level_basis": level_basis,
                "next_action": f"讲解“{topic}”并做一次理解检查。",
            }
        )

    @staticmethod
    def micro_lesson_query(mission: TeachingMission) -> str:
        """micro_lesson 的检索查询：规范主题 + 本轮概念，不用整句意图。"""
        return mission.current_concept or mission.goal

    def blocked_mission(
        self, mission: TeachingMission, gate: TeachingEvidenceGate
    ) -> TeachingMission:
        """来源受阻时保留 mission，标记 blocked 并给出恢复动作。"""
        return mission.model_copy(
            update={
                "stage": TeachingStage.BLOCKED,
                "blocked_reason": gate.reason,
                "recovery_steps": list(gate.recovery_steps),
                "next_action": "当前来源不足；重试、上传材料或更换主题后继续同一进度。",
            }
        )

    def after_answer(
        self, mission: TeachingMission, answer: TeachingAnswerEvidence | None
    ) -> TeachingMission:
        """依据本轮作答证据推进阶段（adaptation），不因一次回答宣称掌握。"""
        if answer is None:
            return mission
        if answer.evaluated_state == AnswerEvaluatedState.CORRECT.value:
            next_action = "补充一个迁移情境，再检查能否应用。"
            streak = 0
        elif answer.evaluated_state == AnswerEvaluatedState.PARTIAL.value:
            next_action = "用更小的例子补齐缺口，再问一个更聚焦的问题。"
            streak = mission.difficulty_streak
        elif answer.evaluated_state == AnswerEvaluatedState.NEEDS_REVIEW.value:
            next_action = "跳过本题；可追问、换例子或继续下一概念。"
            streak = mission.difficulty_streak
        else:
            # 连续困难：达到阈值时自动缩小概念或换例子。
            streak = mission.difficulty_streak + 1
            next_action = (
                "连续几次遇到困难，先换一个更小的概念或例子，再进行一次新的理解检查。"
                if streak >= 2
                else "先回到更小的概念或例子，再进行一次新的理解检查。"
            )
        return mission.model_copy(
            update={
                "stage": TeachingStage.ADAPTATION,
                "difficulty_streak": streak,
                "next_action": next_action,
            }
        )

    def record_taught_concept(
        self, mission: TeachingMission, concept: str
    ) -> TeachingMission:
        """把已完成讲解的概念记入进度（当前概念推进时）。"""
        if concept in mission.taught_concepts:
            return mission
        return mission.model_copy(
            update={"taught_concepts": [*mission.taught_concepts, concept]}
        )

    def compose_first_plan_and_lesson(
        self,
        turn: TeachingTurnProjection,
        mission: TeachingMission,
        *,
        profile_items: Sequence[tuple[str, str]] = (),
        profile_slice_id: str | None = None,
        owner_account_id: str = "",
        object_domain: str = "personal_vault",
    ) -> TeachingTurnProjection:
        """在一次教学编排中准备计划和第一课，共享目标与证据快照。"""

        if not turn.can_answer_reliably:
            return turn
        topic = mission.current_concept or self._topic(mission.goal)
        evidence_refs = [
            source.source_id
            for source in [
                *turn.evidence_gate.local_sources,
                *turn.evidence_gate.external_sources,
            ]
        ]
        if not evidence_refs:
            return turn

        categories = sorted(
            {
                dimension
                for dimension, _value in profile_items
                if dimension
                in {"academic_status", "knowledge_interest", "hobby", "stage_goal"}
            }
        )
        usage = TeachingProfileUsage(
            used_categories=categories,
            applied_to=(
                [
                    *(["difficulty"] if "academic_status" in categories else []),
                    *(
                        ["examples"]
                        if {"knowledge_interest", "hobby"} & set(categories)
                        else []
                    ),
                    *(["path"] if "stage_goal" in categories else []),
                ]
                or ["difficulty", "examples", "path"]
            ),
            defaulted=not categories,
        )
        target_snapshot_id = _snapshot_id(
            "target", mission.mission_id, mission.goal, topic
        )
        evidence_snapshot_id = _snapshot_id("evidence", *evidence_refs)
        plan_id = f"plan-{mission.mission_id}-v1"
        difficulty = (
            "按当前学业情况调整起点"
            if "academic_status" in categories
            else "中性基础起点"
        )
        path = (
            "按阶段目标安排迁移练习"
            if "stage_goal" in categories
            else "安排一个迁移练习"
        )
        example_value = next(
            (
                value[:24]
                for dimension, value in profile_items
                if dimension in {"knowledge_interest", "hobby"} and value.strip()
            ),
            None,
        )
        example = (
            f"用与“{example_value}”相关的情境选择例子"
            if example_value is not None
            else "使用不依赖个人经历的中性例子"
        )
        objective = f"理解“{topic}”的核心机制，并能用一个新例子说明。"
        sessions = [
            TeachingPlanSession(
                lesson_number=1,
                title=f"{topic}：核心概念",
                objective=objective,
                checkpoint="能够复述核心定义、机制和一个边界。",
            ),
            TeachingPlanSession(
                lesson_number=2,
                title=f"{topic}：例子与迁移",
                objective=f"把“{topic}”用于一个新情境。",
                checkpoint="能够说明例子为什么符合该机制。",
            ),
            TeachingPlanSession(
                lesson_number=3,
                title=f"{topic}：综合检查",
                objective=f"独立解释“{topic}”并识别常见混淆。",
                checkpoint="完成一次有依据的综合理解检查。",
            ),
        ]
        plan = TeachingPlanProjection(
            plan_id=plan_id,
            owner_account_id=owner_account_id,
            object_domain=object_domain,
            artifact_status=TeachingArtifactStatus.STAGED,
            content_hash=_snapshot_id(
                "plan-content", plan_id, mission.goal, topic, *evidence_refs
            ),
            version=1,
            goal_snapshot=mission.goal,
            target_concepts=[topic],
            prerequisite_assumptions=[mission.level_assumption, difficulty],
            sessions=sessions,
            stage_checkpoints=[path, *(session.checkpoint for session in sessions)],
            completion_criteria=[
                f"能够独立解释“{topic}”的核心机制。",
                f"能够在新情境中正确应用“{topic}”。",
                "理解检查答案始终绑定可核验来源。",
            ],
            target_snapshot_id=target_snapshot_id,
            evidence_snapshot_id=evidence_snapshot_id,
            profile_slice_id=profile_slice_id,
            profile_usage=usage,
        )
        lesson = TeachingLessonProjection(
            lesson_id=f"lesson-{mission.mission_id}-1",
            plan_id=plan.plan_id,
            owner_account_id=owner_account_id,
            object_domain=object_domain,
            artifact_status=TeachingArtifactStatus.STAGED,
            content_hash=_snapshot_id("lesson-content", plan.plan_id, topic),
            lesson_number=1,
            title=sessions[0].title,
            objective=objective,
            examples=[example, f"用一个新的“{topic}”情境做迁移练习。"],
            summary=[
                f"本课聚焦“{topic}”的一个核心学习胜利。",
                "正式结论只使用本轮证据门允许的来源。",
            ],
            understanding_check=turn.quiz,
            evidence_refs=evidence_refs,
            target_snapshot_id=target_snapshot_id,
            evidence_snapshot_id=evidence_snapshot_id,
            profile_usage=usage,
        )
        return turn.model_copy(update={"plan": plan, "lesson": lesson})

    @staticmethod
    def publish_lesson_content(
        turn: TeachingTurnProjection, content: str
    ) -> TeachingTurnProjection:
        """把唯一主模型的正文写入第一课投影，仍只发布一次。"""

        if turn.lesson is None:
            return turn
        return turn.model_copy(
            update={
                "plan": (
                    turn.plan.model_copy(
                        update={"artifact_status": TeachingArtifactStatus.PUBLISHED}
                    )
                    if turn.plan is not None
                    else None
                ),
                "lesson": turn.lesson.model_copy(
                    update={
                        "explanation": content,
                        "artifact_status": TeachingArtifactStatus.PUBLISHED,
                        "content_hash": sha256(content.encode("utf-8")).hexdigest(),
                    }
                ),
            }
        )

    @staticmethod
    def discard_first_plan(turn: TeachingTurnProjection) -> TeachingTurnProjection:
        """失败、停止或重试时丢弃尚未发布的计划和第一课。"""

        return turn.model_copy(update={"plan": None, "lesson": None})

    def initial(self, query: str, *, recovery: bool = False) -> TeachingTurnProjection:
        topic = self._topic(query)
        gate = TeachingEvidenceGate(
            status=TeachingEvidenceStatus.INSUFFICIENT,
            reason="正在检查当前附件、项目文件和授权知识库。",
            required_search=self._gate.required_search(query, None),
            search_status=TeachingCardStatus.RECOVERY if recovery else TeachingCardStatus.LOADING,
            gap=None,
            recovery_steps=[],
            checked_at=_now(),
        )
        return self._turn(
            status=TeachingCardStatus.RECOVERY if recovery else TeachingCardStatus.LOADING,
            topic=topic,
            query=query,
            gate=gate,
            previous_evidence=[],
            answer=None,
        )

    def prepare(
        self,
        query: str,
        *,
        retrieval: RetrievalRoundProjection | None,
        web_search: WebSearchProjection | None = None,
        arxiv_search: ArxivSearchProjection | None = None,
        previous_turn: TeachingTurnProjection | None = None,
        answer_text: str | None = None,
        answer_message_id: str | None = None,
        mission: TeachingMission | None = None,
        intent: TeachingIntent | None = None,
    ) -> TeachingTurnProjection:
        gate = self._gate.assess(query, retrieval, web_search, arxiv_search)
        answer = None
        evidence = list(previous_turn.evidence) if previous_turn is not None else []
        if (
            previous_turn is not None
            and previous_turn.quiz is not None
            and answer_text is not None
            and intent
            not in {
                TeachingIntent.FOLLOW_UP,
                TeachingIntent.FACT_QUESTION,
                TeachingIntent.SWITCH_MODE,
            }
            and not _is_follow_up(answer_text)
        ):
            answer = self.evaluate_answer(
                previous_turn,
                answer_text,
                answer_message_id or "unknown-message",
            )
            evidence.append(answer)
        turn = self._turn(
            status=self._status_for(gate),
            topic=self._topic(query),
            query=query,
            gate=gate,
            previous_evidence=evidence,
            answer=answer,
        )
        if mission is None:
            return turn
        # 来源受阻时保留 mission 并明确受阻（不回退成无关回答）；
        # 重试成功后从 blocked 恢复教学（不永久卡在受阻状态）。
        if not turn.can_answer_reliably:
            mission = self.blocked_mission(mission, gate)
        elif mission.stage == TeachingStage.BLOCKED:
            mission = mission.model_copy(
                update={
                    "stage": TeachingStage.MICRO_LESSON,
                    "blocked_reason": None,
                    "recovery_steps": [],
                    "next_action": f"讲解“{mission.current_concept or mission.goal}”并做一次理解检查。",
                }
            )
        if answer is not None and turn.can_answer_reliably:
            mission = self.after_answer(mission, answer)
            if answer.evaluated_state == AnswerEvaluatedState.CORRECT.value:
                mission = self.record_taught_concept(mission, mission.current_concept or mission.goal)
        # 出题（quiz 非空）即进入理解检查阶段；作答/跳过由 after_answer 推进。
        if (
            turn.quiz is not None
            and turn.can_answer_reliably
            and mission.stage
            in {TeachingStage.MICRO_LESSON, TeachingStage.BLOCKED}
        ):
            mission = mission.model_copy(
                update={
                    "stage": TeachingStage.UNDERSTANDING_CHECK,
                    "next_action": "回答这道理解检查题，或跳过、追问。",
                }
            )
        return turn.model_copy(update={"mission": mission})

    def evaluate_answer(
        self,
        previous_turn: TeachingTurnProjection,
        answer_text: str,
        answer_message_id: str,
    ) -> TeachingAnswerEvidence:
        quiz = previous_turn.quiz
        if quiz is None:
            raise ValueError("上一轮没有可评价的理解检查题。")
        normalized = answer_text.strip()
        if _SKIP_ANSWER.fullmatch(normalized):
            state = AnswerEvaluatedState.NEEDS_REVIEW
            basis = "用户主动跳过本题；跳过不形成掌握或未掌握结论。"
            knowledge_state = TeachingKnowledgeState.UNKNOWN
            reason = "没有本轮作答证据；可追问、换例子或稍后复测。"
        else:
            matched = [
                term
                for term in quiz.expected_focus
                if term and term.lower() in normalized.lower()
            ]
            ratio = len(matched) / len(quiz.expected_focus) if quiz.expected_focus else 0
            if _STRONG_UNKNOWN.search(normalized):
                # 整句立场即否定：即使提到主题词也不能判正确。
                matched = []
                ratio = 0
            elif _UNCERTAIN_ANSWER.search(normalized):
                # 覆盖关键点但自认缺口：最多到 partial，不宣称掌握。
                ratio *= 0.5
            if ratio >= 0.6:
                state = AnswerEvaluatedState.CORRECT
                basis = f"回答覆盖关键点：{'、'.join(matched)}。"
                knowledge_state = TeachingKnowledgeState.SUPPORTED_CANDIDATE
                reason = "本轮回答支持该概念，但仍需迁移或延迟复测，不能直接标记已掌握。"
            elif ratio > 0:
                state = AnswerEvaluatedState.PARTIAL
                basis = f"回答只覆盖部分关键点：{'、'.join(matched)}。"
                knowledge_state = TeachingKnowledgeState.EMERGING_CANDIDATE
                reason = "需要补充解释和反例；当前证据不足以确认掌握。"
            else:
                state = AnswerEvaluatedState.INCORRECT
                basis = "回答未覆盖当前题目要求的关键点。"
                knowledge_state = TeachingKnowledgeState.UNKNOWN
                reason = "先回到更小的概念或例子，再进行一次新的理解检查。"
        return TeachingAnswerEvidence(
            answer_id=_new_id("answer"),
            question_id=quiz.question_id,
            source_message_id=answer_message_id,
            response_text=answer_text,
            evaluated_state=state,
            evaluation_basis=basis,
            evidence_refs=list(quiz.evidence_refs),
            knowledge_state=knowledge_state,
            knowledge_state_reason=reason,
            requires_confirmation=True,
            mastery_claim_allowed=False,
        )

    def _turn(
        self,
        *,
        status: TeachingCardStatus,
        topic: str,
        query: str,
        gate: TeachingEvidenceGate,
        previous_evidence: list[TeachingAnswerEvidence],
        answer: TeachingAnswerEvidence | None,
    ) -> TeachingTurnProjection:
        if answer is None:
            steps = ["确认本轮目标与水平假设", "用解释、例子或类比讲解", "提出一个理解检查问题"]
            next_prompt = "你可以直接回答这道题，也可以跳过、追问或切回日常陪伴。"
        elif answer.evaluated_state == AnswerEvaluatedState.CORRECT.value:
            steps = ["确认回答中的关键点", "增加一个迁移情境", "用一道简短题检查能否应用"]
            next_prompt = "如果你愿意，试着把这个概念应用到一个新情境。"
        elif answer.evaluated_state == AnswerEvaluatedState.PARTIAL.value:
            steps = ["指出已覆盖的部分", "用更小的例子补齐缺口", "再问一个更聚焦的问题"]
            next_prompt = "我会先补一个更直观的例子；你也可以告诉我哪一步最不清楚。"
        else:
            steps = ["确认当前回答的困难点", "回到更小的概念", "用低负担问题重新检查"]
            next_prompt = "我们先缩小范围；你可以说出目前最确定的一点，或直接追问。"

        quiz = None
        if (
            status == TeachingCardStatus.READY
            and gate.status == TeachingEvidenceStatus.SUFFICIENT
        ):
            refs = [source.source_id for source in [*gate.local_sources, *gate.external_sources]]
            if answer is None:
                expected_focus = [topic]
                question = f"请用自己的话解释“{topic}”的核心含义，并举一个边界清楚的例子。"
            elif answer.evaluated_state == AnswerEvaluatedState.CORRECT.value:
                expected_focus = [topic, "应用"]
                question = f"请把“{topic}”应用到一个新情境，并说明你的推理依据。"
            else:
                expected_focus = [topic]
                question = f"先不追求完整：你现在能用一句话说出“{topic}”是什么吗？"
            quiz = TeachingQuiz(
                question_id=_new_id("question"),
                concept=topic,
                question=question,
                expected_focus=expected_focus,
                evidence_refs=refs,
            )

        gap_response = None
        if gate.gap:
            gap_response = (
                f"这轮我先不把不确定内容说成结论：{gate.gap} "
                "你可以重试检索、上传材料，或让我先解释如何核对来源。"
            )
        return TeachingTurnProjection(
            status=status,
            goal=f"本轮目标：理解“{topic}”，并能用自己的话说明其核心机制。",
            level_assumption="暂按初学者处理；你的回答会调整深度、例子和下一问。",
            steps=steps,
            check_method="每轮最多一道理解检查题；可跳过、追问或切换模式。",
            evidence_gate=gate,
            quiz=quiz,
            evidence=previous_evidence,
            next_prompt=next_prompt,
            gap_response=gap_response,
            can_answer_reliably=(
                gate.status == TeachingEvidenceStatus.SUFFICIENT
                and status == TeachingCardStatus.READY
            ),
            can_cancel=True,
            can_retry=(
                status
                in {
                    TeachingCardStatus.EMPTY,
                    TeachingCardStatus.ERROR,
                    TeachingCardStatus.PERMISSION,
                    TeachingCardStatus.RECOVERY,
                }
            ),
        )

    @staticmethod
    def _status_for(gate: TeachingEvidenceGate) -> TeachingCardStatus:
        if gate.search_status == TeachingCardStatus.ERROR:
            return TeachingCardStatus.ERROR
        if gate.search_status == TeachingCardStatus.PERMISSION:
            return TeachingCardStatus.PERMISSION
        if gate.search_status == TeachingCardStatus.EMPTY:
            return TeachingCardStatus.EMPTY
        if gate.status == TeachingEvidenceStatus.CONFLICT:
            return TeachingCardStatus.RECOVERY
        if gate.status == TeachingEvidenceStatus.SUFFICIENT:
            return TeachingCardStatus.READY
        return TeachingCardStatus.EMPTY if gate.gap else TeachingCardStatus.READY

    @staticmethod
    def _topic(query: str) -> str:
        """从用户表述提取规范主题（主/次动词一次组合匹配，避免残留）。"""
        cleaned = _TOPIC_VERB_PREFIX.sub("", query.strip())
        cleaned = cleaned.split("：", 1)[0].split(":", 1)[0]
        return cleaned.rstrip("。！？?!")[:40] or "当前概念"


def _web_status(status: WebSearchStatus) -> TeachingCardStatus:
    return {
        WebSearchStatus.SUCCESS: TeachingCardStatus.READY,
        WebSearchStatus.PARTIAL: TeachingCardStatus.READY,
        WebSearchStatus.EMPTY: TeachingCardStatus.EMPTY,
        WebSearchStatus.FETCH_ERROR: TeachingCardStatus.ERROR,
        WebSearchStatus.EVIDENCE_INSUFFICIENT: TeachingCardStatus.EMPTY,
        WebSearchStatus.SOURCE_CONFLICT: TeachingCardStatus.RECOVERY,
        WebSearchStatus.PERMISSION: TeachingCardStatus.PERMISSION,
        WebSearchStatus.ERROR: TeachingCardStatus.ERROR,
        WebSearchStatus.LOADING: TeachingCardStatus.LOADING,
        WebSearchStatus.RECOVERY: TeachingCardStatus.RECOVERY,
        WebSearchStatus.CANCELLED: TeachingCardStatus.ERROR,
    }[status]


def _arxiv_status(status: ArxivSearchStatus) -> TeachingCardStatus:
    return {
        ArxivSearchStatus.SUCCESS: TeachingCardStatus.READY,
        ArxivSearchStatus.EMPTY: TeachingCardStatus.EMPTY,
        ArxivSearchStatus.PERMISSION: TeachingCardStatus.PERMISSION,
        ArxivSearchStatus.ERROR: TeachingCardStatus.ERROR,
        ArxivSearchStatus.LOADING: TeachingCardStatus.LOADING,
        ArxivSearchStatus.RECOVERY: TeachingCardStatus.RECOVERY,
        ArxivSearchStatus.CANCELLED: TeachingCardStatus.ERROR,
    }[status]


__all__ = ["TeachingEvidenceGateService", "TeachingTurnService"]
