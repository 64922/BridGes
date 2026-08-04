"""学习模式证据门与聊天教学轮次编排。"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.contracts.learning import AnswerEvaluatedState
from bridges.contracts.retrieval import RetrievalRoundProjection, RetrievalSufficiency
from bridges.contracts.teaching import (
    TeachingAnswerEvidence,
    TeachingCardStatus,
    TeachingEvidenceGate,
    TeachingEvidenceSource,
    TeachingEvidenceSourceType,
    TeachingEvidenceStatus,
    TeachingKnowledgeState,
    TeachingQuiz,
    TeachingSearchSource,
    TeachingTurnProjection,
)
from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus

_PAPER_QUERY = re.compile(r"arxiv|论文|文献|期刊|研究综述", re.IGNORECASE)
_PUBLIC_QUERY = re.compile(r"最新|公开|网页|新闻|现状|政策|规范|官方|事实", re.IGNORECASE)
_SKIP_ANSWER = re.compile(r"^(跳过|跳过这题|先跳过|不答|暂时不答|skip)\s*[。.!！]?$", re.I)
_UNCERTAIN_ANSWER = re.compile(r"不懂|不知道|不会|不清楚|没学会|不明白|不理解|不会做")
_FOLLOW_UP = re.compile(
    r"(?:为什么|为何|怎么|如何|能不能|可不可以|请再解释|再讲|举例|什么意思|"
    r"哪里|继续讲|追问)"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(10)}"


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
        for citation in retrieval.citations
    ]


def _web_sources(web_search: WebSearchProjection | None) -> list[TeachingEvidenceSource]:
    if web_search is None or web_search.status != WebSearchStatus.SUCCESS:
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
    ) -> TeachingTurnProjection:
        gate = self._gate.assess(query, retrieval, web_search, arxiv_search)
        answer = None
        evidence = list(previous_turn.evidence) if previous_turn is not None else []
        if (
            previous_turn is not None
            and previous_turn.quiz is not None
            and answer_text is not None
            and not _is_follow_up(answer_text)
        ):
            answer = self.evaluate_answer(
                previous_turn,
                answer_text,
                answer_message_id or "unknown-message",
            )
            evidence.append(answer)
        return self._turn(
            status=self._status_for(gate),
            topic=self._topic(query),
            query=query,
            gate=gate,
            previous_evidence=evidence,
            answer=answer,
        )

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
            if _UNCERTAIN_ANSWER.search(normalized):
                matched = []
            ratio = len(matched) / len(quiz.expected_focus) if quiz.expected_focus else 0
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
        cleaned = re.sub(r"^(请|帮我|我想|想要|教我|讲讲|解释一下)\s*", "", query.strip())
        return cleaned.rstrip("。！？?!")[:40] or "当前概念"


def _web_status(status: WebSearchStatus) -> TeachingCardStatus:
    return {
        WebSearchStatus.SUCCESS: TeachingCardStatus.READY,
        WebSearchStatus.EMPTY: TeachingCardStatus.EMPTY,
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
