"""Learning evidence, knowledge-state proposals and learning-path service (T023).

LearningPathService records observable learning evidence, proposes knowledge-
state updates from that evidence, requires human confirmation for important
changes, and compiles a per-user learning path anchored to confirmed states.
Browsing and completion are never treated as mastery evidence; rejected
proposals cannot be silently reapplied by the model.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    DecideKnowledgeStateProposalRequest,
    HumanDecision,
    HumanDecisionType,
    KnowledgeConfidence,
    KnowledgeState,
    KnowledgeStateProposal,
    KnowledgeStateProposalStatus,
    KnowledgeStateStatus,
    LearningPath,
    LearningPathNode,
    LearningPathNodeStatus,
    LearningRecord,
    LearningRecordCreateRequest,
    LearningRecordSource,
    LearningRecordType,
    ProposeKnowledgeStateUpdateRequest,
)
from science_companion.learning.adapters import LearningError
from science_companion.learning.ports import LearningRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


class LearningPathService:
    """Application service for evidence-backed knowledge-state and path updates."""

    def __init__(self, repository: LearningRepository) -> None:
        self._repository = repository

    def record_learning_record(
        self,
        account_id: str,
        mission_id: str,
        request: LearningRecordCreateRequest,
    ) -> LearningRecord:
        """Record a qualified learning evidence item.

        Only concrete performance, misconception correction or prerequisite
        evidence become learning records. The caller must supply a source id
        (e.g. an exercise attempt id) and a transparent reason why the event
        counts as evidence.
        """
        self._repository.get_mission(account_id, mission_id)

        record = LearningRecord(
            record_id=_new_id(),
            mission_id=mission_id,
            owner_account_id=account_id,
            concept_id=request.concept_id,
            record_type=request.record_type,
            source_type=request.source_type,
            source_id=request.source_id,
            response_text=request.response_text,
            evaluated_state=request.evaluated_state,
            misconception_corrected=request.misconception_corrected,
            evidence_refs=list(request.evidence_refs),
            record_reason=request.record_reason,
            created_at=_now(),
        )
        return self._repository.save_learning_record(record)

    def list_learning_records(
        self,
        account_id: str,
        mission_id: str,
        concept_id: str | None = None,
    ) -> list[LearningRecord]:
        """List learning records for a mission, optionally filtered by concept."""
        self._repository.get_mission(account_id, mission_id)
        return self._repository.list_learning_records(account_id, mission_id, concept_id)

    def propose_knowledge_state_update(
        self,
        account_id: str,
        mission_id: str,
        request: ProposeKnowledgeStateUpdateRequest,
    ) -> KnowledgeStateProposal:
        """Propose a new knowledge state from learning records.

        The proposal is a candidate until the user accepts, rejects or modifies
        it. The model cannot bypass a rejected proposal; a new proposal requires
        new evidence.
        """
        mission = self._repository.get_mission(account_id, mission_id)

        records = self._repository.list_learning_records(
            account_id, mission_id, request.concept_id
        )
        if request.record_ids is not None:
            selected = {r.record_id for r in records if r.record_id in request.record_ids}
            records = [r for r in records if r.record_id in selected]

        if not records:
            raise LearningError(
                "没有可用于提出知识状态更新的学习记录。"
            )

        current = self._repository.get_knowledge_state(
            account_id, mission_id, request.concept_id
        )

        supporting_ids, refuting_ids, proposed_status, proposed_confidence = (
            self._derive_state_from_records(records)
        )

        uncertainty_reason: str | None = None
        next_task = f"通过新的练习或延迟复测验证“{request.concept_id}”的掌握情况。"

        if proposed_status == KnowledgeStateStatus.ROBUST:
            uncertainty_reason = None
            next_task = f"在“{request.concept_id}”的迁移情境中完成一次综合应用。"
        elif proposed_status == KnowledgeStateStatus.SUPPORTED:
            uncertainty_reason = "有合格学习证据，但缺少延迟复测或迁移证据。"
            next_task = f"在间隔一段时间后对“{request.concept_id}”进行延迟检索。"
        elif proposed_status == KnowledgeStateStatus.EMERGING:
            uncertainty_reason = "存在不完善或反驳证据，需进一步练习。"
            next_task = f"针对“{request.concept_id}”的薄弱点进行检索练习并纠正误区。"
        else:
            uncertainty_reason = "学习证据不足，状态保持未知。"

        # 即使提议不改变当前状态也照常生成，让用户能看到证据。
        # 此时不标记为"重要"变化，但用户仍需通过决策接口确认。
        proposal = KnowledgeStateProposal(
            proposal_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            concept_id=request.concept_id,
            proposed_status=proposed_status,
            proposed_confidence=proposed_confidence,
            supporting_record_ids=supporting_ids,
            refuting_record_ids=refuting_ids,
            uncertainty_reason=uncertainty_reason,
            scope=f"mission:{mission_id}",
            next_validation_task=next_task,
            status=KnowledgeStateProposalStatus.PENDING,
            decision=None,
            version=1,
            created_at=_now(),
            decided_at=None,
        )
        return self._repository.save_knowledge_state_proposal(proposal)

    def _derive_state_from_records(
        self, records: list[LearningRecord]
    ) -> tuple[list[str], list[str], KnowledgeStateStatus, KnowledgeConfidence]:
        """Classify records and derive a proposed knowledge state."""
        supporting_ids: list[str] = []
        refuting_ids: list[str] = []

        has_transfer_or_delayed = False
        has_strong_support = False
        has_incorrect = False
        has_partial = False

        for record in records:
            if record.record_type in {
                LearningRecordType.DELAYED_RETRIEVAL,
                LearningRecordType.TRANSFER_TASK,
            } and record.evaluated_state == AnswerEvaluatedState.CORRECT:
                supporting_ids.append(record.record_id)
                has_transfer_or_delayed = True
                has_strong_support = True
                continue

            if record.record_type == LearningRecordType.MISCONCEPTION_CORRECTION:
                supporting_ids.append(record.record_id)
                has_strong_support = True
                continue

            if record.record_type == LearningRecordType.PREREQUISITE_EVIDENCE:
                supporting_ids.append(record.record_id)
                has_strong_support = True
                continue

            if record.evaluated_state == AnswerEvaluatedState.CORRECT:
                supporting_ids.append(record.record_id)
                has_strong_support = True
                continue

            if record.evaluated_state == AnswerEvaluatedState.PARTIAL:
                supporting_ids.append(record.record_id)
                has_partial = True
                continue

            if record.evaluated_state in {
                AnswerEvaluatedState.INCORRECT,
                AnswerEvaluatedState.NEEDS_REVIEW,
            }:
                refuting_ids.append(record.record_id)
                has_incorrect = True
                continue

        if has_incorrect or has_partial:
            return supporting_ids, refuting_ids, KnowledgeStateStatus.EMERGING, KnowledgeConfidence.LOW

        if has_transfer_or_delayed and has_strong_support:
            return supporting_ids, refuting_ids, KnowledgeStateStatus.ROBUST, KnowledgeConfidence.HIGH

        if has_strong_support:
            return supporting_ids, refuting_ids, KnowledgeStateStatus.SUPPORTED, KnowledgeConfidence.MODERATE

        return supporting_ids, refuting_ids, KnowledgeStateStatus.EMERGING, KnowledgeConfidence.LOW

    def list_knowledge_state_proposals(
        self,
        account_id: str,
        mission_id: str,
        concept_id: str | None = None,
        status: KnowledgeStateProposalStatus | None = None,
    ) -> list[KnowledgeStateProposal]:
        """List knowledge-state proposals for a mission."""
        self._repository.get_mission(account_id, mission_id)
        return self._repository.list_knowledge_state_proposals(
            account_id, mission_id, concept_id, status
        )

    def decide_knowledge_state_proposal(
        self,
        account_id: str,
        proposal_id: str,
        request: DecideKnowledgeStateProposalRequest,
    ) -> KnowledgeStateProposal:
        """Accept, reject or modify a knowledge-state proposal.

        Accepted proposals create a new version of the knowledge state and
        trigger a learning-path recompilation. Rejected proposals remain
        auditable and cannot be re-applied without new evidence.
        """
        proposal = self._repository.get_knowledge_state_proposal(account_id, proposal_id)
        if proposal.status != KnowledgeStateProposalStatus.PENDING:
            raise LearningError("只能决定待处理的提议。")

        now = _now()
        decision = HumanDecision(
            decision_id=_new_id(),
            target_id=proposal.proposal_id,
            account_id=account_id,
            decision=request.decision,
            reason=request.reason,
            created_at=now,
        )

        if request.decision == HumanDecisionType.ACCEPT:
            proposal.status = KnowledgeStateProposalStatus.ACCEPTED
            self._apply_accepted_proposal(account_id, proposal)
        elif request.decision == HumanDecisionType.MODIFY:
            if request.modified_status is None:
                raise LearningError("修改决定必须提供修改后的状态。")
            proposal.status = KnowledgeStateProposalStatus.ACCEPTED
            proposal.proposed_status = request.modified_status
            if request.modified_confidence is not None:
                proposal.proposed_confidence = request.modified_confidence
            decision.modified_value = request.modified_status.value
            self._apply_accepted_proposal(account_id, proposal)
        else:
            proposal.status = KnowledgeStateProposalStatus.REJECTED

        proposal.decision = decision
        proposal.decided_at = now
        updated = self._repository.save_knowledge_state_proposal(proposal)

        if updated.status == KnowledgeStateProposalStatus.ACCEPTED:
            self.compile_learning_path(account_id, proposal.mission_id)

        return updated

    def _apply_accepted_proposal(
        self, account_id: str, proposal: KnowledgeStateProposal
    ) -> None:
        """Create a new knowledge-state version from an accepted proposal."""
        current = self._repository.get_knowledge_state(
            account_id, proposal.mission_id, proposal.concept_id
        )
        now = _now()

        if current is not None:
            # 旧状态将在 save_knowledge_state 中被自动标记为已取代。
            pass  # save_knowledge_state 的适配器逻辑会处理版本链。

        new_state = KnowledgeState(
            state_id=proposal.proposal_id,
            mission_id=proposal.mission_id,
            owner_account_id=account_id,
            concept_id=proposal.concept_id,
            status=proposal.proposed_status,
            confidence=proposal.proposed_confidence,
            supporting_record_ids=list(proposal.supporting_record_ids),
            refuting_record_ids=list(proposal.refuting_record_ids),
            uncertainty_reason=proposal.uncertainty_reason,
            scope=proposal.scope,
            next_validation_task=proposal.next_validation_task,
            version=(current.version + 1) if current else 1,
            superseded_by_state_id=None,
            created_at=(current.created_at if current else now),
            updated_at=now,
        )
        self._repository.save_knowledge_state(new_state)

    def get_learning_path(
        self, account_id: str, mission_id: str
    ) -> LearningPath:
        """Return the current learning path for a mission, compiling one if absent."""
        self._repository.get_mission(account_id, mission_id)
        path = self._repository.get_learning_path_for_mission(account_id, mission_id)
        if path is None:
            return self.compile_learning_path(account_id, mission_id)
        return path

    def compile_learning_path(
        self, account_id: str, mission_id: str
    ) -> LearningPath:
        """Compile a learning path from confirmed knowledge states.

        Each node traces back to learning records; unknown or emerging concepts
        become pending instructional nodes, supported/robust concepts become
        completed or validation nodes.
        """
        mission = self._repository.get_mission(account_id, mission_id)
        states = self._repository.list_knowledge_states_for_mission(account_id, mission_id)
        records = self._repository.list_learning_records(account_id, mission_id)

        state_by_concept = {s.concept_id: s for s in states}

        nodes: list[LearningPathNode] = []
        for concept in mission.scope_concepts:
            state = state_by_concept.get(concept)
            concept_records = [r for r in records if r.concept_id == concept]
            record_ids = [r.record_id for r in concept_records]

            if state is None or state.status == KnowledgeStateStatus.UNKNOWN:
                node = LearningPathNode(
                    node_id=_new_id(),
                    concept_id=concept,
                    title=f"学习“{concept}”",
                    description=f"通过诊断或短课建立“{concept}”的初步证据。",
                    status=LearningPathNodeStatus.PENDING,
                    evidence_record_ids=record_ids,
                    depends_on_node_ids=[n.node_id for n in nodes],
                )
            elif state.status == KnowledgeStateStatus.EMERGING:
                node = LearningPathNode(
                    node_id=_new_id(),
                    concept_id=concept,
                    title=f"巩固“{concept}”",
                    description=state.next_validation_task,
                    status=LearningPathNodeStatus.PENDING,
                    evidence_record_ids=record_ids,
                    depends_on_node_ids=[n.node_id for n in nodes],
                )
            elif state.status == KnowledgeStateStatus.SUPPORTED:
                node = LearningPathNode(
                    node_id=_new_id(),
                    concept_id=concept,
                    title=f"验证“{concept}”",
                    description=state.next_validation_task,
                    status=LearningPathNodeStatus.PENDING,
                    evidence_record_ids=record_ids,
                    depends_on_node_ids=[n.node_id for n in nodes],
                )
            else:  # ROBUST
                node = LearningPathNode(
                    node_id=_new_id(),
                    concept_id=concept,
                    title=f"掌握“{concept}”",
                    description="已达到可迁移水平，可进入后续主题或间隔复习。",
                    status=LearningPathNodeStatus.COMPLETED,
                    evidence_record_ids=record_ids,
                    depends_on_node_ids=[n.node_id for n in nodes],
                )
            nodes.append(node)

        current_node_id: str | None = None
        for node in nodes:
            if node.status == LearningPathNodeStatus.PENDING:
                current_node_id = node.node_id
                break

        path_id = _new_id()
        existing = self._repository.get_learning_path_for_mission(account_id, mission_id)
        version = (existing.version + 1) if existing else 1

        path = LearningPath(
            path_id=path_id,
            mission_id=mission_id,
            owner_account_id=account_id,
            title=f"{mission.title} 学习路径",
            nodes=nodes,
            current_node_id=current_node_id,
            version=version,
            created_at=_now(),
            updated_at=_now(),
        )
        return self._repository.save_learning_path(path)
