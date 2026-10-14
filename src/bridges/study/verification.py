"""Issue 03：嵌入式理解验证与即时纠错。

本模块拥有学习对话的即时验证：待答问题（含展示前冻结的私有评价
依据）、实际提示历史、作答评价结果与学习证据。职责边界：

- 即时反馈与正式诊断分开：这里只保存证据语义（独立完成/提示后完成/
  未验证/无法判断），不写长期掌握、能力标签或人格结论；正式诊断由
  后续独立切片消费证据。
- 待答问题在助手消息成功终态与消息同事务激活，停止、失败与未展示
  的问题绝不提前激活；重试沿用同一评价快照，不重复记账。
- 评价依据只在服务端与模型之间流转，不进入用户投影。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from bridges.chat.repository import (
    ConversationRepository,
    GenerationRunRecord,
    MessageRecord,
)
from bridges.contracts.study_source_verification import StudyVerificationSourceRef
from bridges.contracts.study_verification import (
    VERIFICATION_CONTRACT_VERSION,
    StudyCheckAssistance,
    StudyCheckDraft,
    StudyCheckEvaluation,
    StudyCheckKind,
    StudyEvaluationBasis,
    StudyEvidenceAssistance,
    StudyEvidenceOutcome,
    StudyEvidenceSemantics,
    StudyPendingCheckProjection,
    StudyUserIntent,
)
from bridges.storage.database import BridgesDatabase
from bridges.study.demand_verification import snapshot_incomplete, snapshot_source_refs
from bridges.study.local_progress import StudyLocalProgressRepository
from bridges.study.tutor_prompt import (
    STUDY_VERIFICATION_PLAN_JSON_SCHEMA,
    STUDY_VERIFICATION_PLANNER_SYSTEM_PROMPT,
)

_VERIFICATION_CAPABILITY = "qwen_structured_output"
_VERIFICATION_CAPABILITY_VERSION = "1"
_MAX_OUTPUT_TOKENS = 1200
_QUESTION_LIMIT = 300
_KNOWLEDGE_CONTEXT_LIMIT = 600
_SOURCE_MESSAGE_LIMIT = 32
_RECENT_EVIDENCE_LIMIT = 4
_RECENT_MESSAGE_LIMIT = 6
_STUCK_STREAK_LIMIT = 2
_STUCK_HINT_LIMIT = 2
_SOURCE_REF_LIMIT = 8

_ACTIONS = frozenset({"evaluate", "help", "pose_check", "none"})
_OUTCOMES = frozenset(
    {
        StudyEvidenceOutcome.CORRECT.value,
        StudyEvidenceOutcome.PARTIAL.value,
        StudyEvidenceOutcome.MISCONCEPTION.value,
        StudyEvidenceOutcome.UNDETERMINED.value,
    }
)
_INTENTS = frozenset(item.value for item in StudyUserIntent)
_KINDS = frozenset(item.value for item in StudyCheckKind)


@dataclass(frozen=True)
class StudyHintEvent:
    """一次确实提供过的提示（只在成功终态追加）。"""

    message_id: str
    run_id: str
    at: str


@dataclass(frozen=True)
class StudyCheckRecord:
    """待答问题的内部记录；``basis`` 是私有评价依据。"""

    check_id: str
    kind: StudyCheckKind
    question: str
    knowledge_context: str
    basis: StudyEvaluationBasis
    hint_history: list[StudyHintEvent]
    source_message_id: str
    version: int
    created_at: datetime
    updated_at: datetime

    def assistance(self) -> StudyCheckAssistance:
        return (
            StudyCheckAssistance.HINTED
            if self.hint_history
            else StudyCheckAssistance.NONE
        )

    def projection(self) -> StudyPendingCheckProjection:
        return StudyPendingCheckProjection(
            check_id=self.check_id,
            kind=self.kind,
            question=self.question,
            knowledge_context=self.knowledge_context,
            assistance=self.assistance(),
            version=self.version,
            source_message_id=self.source_message_id,
            created_at=self.created_at,
        )


class StudyEvidenceRecord(BaseModel):
    """一条学习证据；语义不是能力等级，供后续诊断消费。"""

    evidence_id: str
    account_id: str
    conversation_id: str
    run_id: str
    check_id: str
    check_kind: StudyCheckKind
    check_version: int
    question: str
    knowledge_context: str = ""
    basis_protocol_version: str = ""
    answered: bool
    outcome: StudyEvidenceOutcome
    semantics: StudyEvidenceSemantics
    assistance: StudyEvidenceAssistance
    reasoning_note: str = ""
    supporting: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    hint_message_ids: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    evidence_contract_version: str = ""
    answer_message_id: str | None = None
    feedback_message_id: str | None = None
    created_at: datetime


class _PlannerOutput(BaseModel):
    """结构化规划输出；空字符串表示未提供该字段。"""

    action: str
    intent: str = ""
    outcome: str = ""
    equivalent_accepted: bool = False
    reasoning_note: str = ""
    supporting: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    feedback_direction: str = ""
    check_kind: str = ""
    check_question: str = ""
    check_knowledge_context: str = ""
    evaluation_points: list[str] = Field(default_factory=list)
    accepted_equivalents: list[str] = Field(default_factory=list)
    key_misconceptions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    expected_answer: str = ""
    teaching_direction: str = ""


@dataclass(frozen=True)
class _NormalizedPlan:
    action: str
    intent: StudyUserIntent
    evaluation: StudyCheckEvaluation | None
    posed_check: StudyCheckDraft | None
    posed_check_id: str | None
    teaching_direction: str


class StudyVerificationRepository:
    """按账户读取待答问题/证据，并在终态事务内提交本轮验证结果。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_pending(
        self, account_id: str, conversation_id: str
    ) -> StudyCheckRecord | None:
        row = self._database.scoped(account_id).execute(
            "SELECT check_id, kind, question, knowledge_context, basis_json,"
            " hint_history_json, source_message_id, version, created_at, updated_at"
            " FROM study_pending_checks"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return StudyCheckRecord(
                check_id=row["check_id"],
                kind=StudyCheckKind(row["kind"]),
                question=row["question"],
                knowledge_context=row["knowledge_context"],
                basis=StudyEvaluationBasis.model_validate(
                    json.loads(row["basis_json"])
                ),
                hint_history=[
                    StudyHintEvent(**item)
                    for item in json.loads(row["hint_history_json"])
                ],
                source_message_id=row["source_message_id"],
                version=int(row["version"]),
                created_at=_parse_time(row["created_at"]),
                updated_at=_parse_time(row["updated_at"]),
            )
        except (TypeError, ValueError, KeyError, ValidationError):
            return None

    def pending_projection(
        self, account_id: str, conversation_id: str
    ) -> StudyPendingCheckProjection | None:
        record = self.get_pending(account_id, conversation_id)
        return record.projection() if record is not None else None

    def list_recent_evidence(
        self,
        account_id: str,
        conversation_id: str,
        *,
        limit: int = _RECENT_EVIDENCE_LIMIT,
    ) -> list[StudyEvidenceRecord]:
        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (account_id, conversation_id, limit),
        ).fetchall()
        return _evidence_records(rows)

    def list_evidence(
        self, account_id: str, conversation_id: str
    ) -> list[StudyEvidenceRecord]:
        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at ASC, rowid ASC",
            (account_id, conversation_id),
        ).fetchall()
        return _evidence_records(rows)

    # ------------------------------------------------------------------
    # 终态提交（在消息 streaming→done 的同一事务内调用）
    # ------------------------------------------------------------------

    def commit_turn_in_transaction(
        self,
        repository: ConversationRepository,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        assistant_message_id: str,
        content: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """按规划快照提交本轮：评价证据、提示记录与新待答问题激活。

        快照来自运行配置（规划时写入，重试沿用）；缺少或损坏时不做
        任何验证写入，不伪造评价。停止/失败轮次不会进入本方法。
        """

        latest = repository.get_generation_run(run.account_id, run.run_id)
        config = (latest.config if latest is not None else run.config) or {}
        snapshot = config.get("study_verification")
        if not isinstance(snapshot, dict):
            return
        # Issue 11：同一轮公开查证快照。查证未完成（空命中、不可达、
        # 冲突、仅摘要、用户限制或预算耗尽）时不再冻结依赖外部证据的
        # 标准答案与误解判定；实际采用的来源版本随评价依据一起冻结。
        source_snapshot = config.get("study_source_verification")
        verification_incomplete = snapshot_incomplete(source_snapshot)
        source_refs = _source_refs_from_snapshot(source_snapshot)
        timestamp = now or datetime.now(UTC)
        action = str(snapshot.get("action") or "")
        intent = str(snapshot.get("intent") or "")
        bound = snapshot.get("bound_check")
        if intent == StudyUserIntent.SKIP.value and isinstance(bound, dict):
            # 学生明确跳过或转向：解除等待，如实记录「未验证」允许继续。
            self._release_skipped_check(run, user_message, bound, timestamp)
        if action == "evaluate" and isinstance(bound, dict):
            self._write_answer_evidence(
                run,
                user_message,
                assistant_message_id,
                bound,
                snapshot,
                timestamp,
                verification_incomplete=verification_incomplete,
            )
        elif (
            action == "help"
            and intent == StudyUserIntent.HINT_REQUEST.value
            and isinstance(bound, dict)
        ):
            self._append_hint(run, assistant_message_id, bound, timestamp)
        posed = snapshot.get("posed_check")
        if isinstance(posed, dict):
            self._activate_posed_check(
                run,
                assistant_message_id,
                posed,
                content,
                timestamp,
                source_refs=source_refs,
                verification_incomplete=verification_incomplete,
            )

    # ------------------------------------------------------------------
    # 内部写入
    # ------------------------------------------------------------------

    def _write_answer_evidence(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        assistant_message_id: str,
        bound: dict[str, Any],
        snapshot: dict[str, Any],
        now: datetime,
        *,
        verification_incomplete: bool = False,
    ) -> None:
        evaluation = snapshot.get("evaluation")
        if not isinstance(evaluation, dict):
            return
        try:
            kind = StudyCheckKind(str(bound.get("kind") or ""))
            outcome = StudyEvidenceOutcome(str(evaluation.get("outcome") or ""))
        except ValueError:
            return
        if outcome == StudyEvidenceOutcome.NO_ANSWER:
            return
        if outcome == StudyEvidenceOutcome.MISCONCEPTION and verification_incomplete:
            # Issue 11：本轮公开查证未完成——学生是否确实答错外部事实
            # 无从核实，如实记录待确认，不把学生判成错。
            outcome = StudyEvidenceOutcome.UNDETERMINED
        elif outcome == StudyEvidenceOutcome.MISCONCEPTION and self._basis_incomplete(
            run, str(bound.get("check_id") or "")
        ):
            # Issue 11：待答问题在冻结时公开查证未完成（依据标记为不完整）：
            # 依据不可靠，评价不得据此判学生错。
            outcome = StudyEvidenceOutcome.UNDETERMINED
        assistance_value = str(
            snapshot.get("assistance") or StudyEvidenceAssistance.INDEPENDENT.value
        )
        try:
            assistance = StudyEvidenceAssistance(assistance_value)
        except ValueError:
            assistance = StudyEvidenceAssistance.INDEPENDENT
        if assistance == StudyEvidenceAssistance.NONE:
            assistance = StudyEvidenceAssistance.INDEPENDENT
        semantics = _semantics_for(kind, outcome, assistance)
        evidence = StudyEvidenceRecord(
            evidence_id=secrets.token_urlsafe(16),
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            check_id=str(bound.get("check_id") or ""),
            check_kind=kind,
            check_version=max(1, int(bound.get("version") or 1)),
            question=_short_text(str(bound.get("question") or ""), _QUESTION_LIMIT),
            knowledge_context=_short_text(
                str(bound.get("knowledge_context") or ""), _KNOWLEDGE_CONTEXT_LIMIT
            ),
            basis_protocol_version=str(bound.get("basis_protocol_version") or ""),
            answered=True,
            outcome=outcome,
            semantics=semantics,
            assistance=assistance,
            reasoning_note=_short_text(
                str(evaluation.get("reasoning_note") or ""), 400
            ),
            supporting=_text_list(evaluation.get("supporting")),
            missing=_text_list(evaluation.get("missing")),
            hint_message_ids=_text_list(snapshot.get("hint_message_ids")),
            source_message_ids=[
                user_message.message_id,
                assistant_message_id,
                *(
                    [str(bound.get("source_message_id"))]
                    if bound.get("source_message_id")
                    else []
                ),
            ][:_SOURCE_MESSAGE_LIMIT],
            source_refs=_ref_list(snapshot.get("source_refs")),
            evidence_contract_version=VERIFICATION_CONTRACT_VERSION,
            answer_message_id=user_message.message_id,
            feedback_message_id=assistant_message_id,
            created_at=now,
        )
        self._insert_evidence(evidence)
        self._close_pending_if_current(
            run.account_id, run.conversation_id, evidence.check_id
        )

    def _basis_incomplete(self, run: GenerationRunRecord, check_id: str) -> bool:
        """当前待答问题是否冻结于未完成的公开查证（依据不可判错）。"""

        if not check_id:
            return False
        current = self.get_pending(run.account_id, run.conversation_id)
        return (
            current is not None
            and current.check_id == check_id
            and current.basis.evidence_incomplete
        )

    def _append_hint(
        self,
        run: GenerationRunRecord,
        assistant_message_id: str,
        bound: dict[str, Any],
        now: datetime,
    ) -> None:
        current = self.get_pending(run.account_id, run.conversation_id)
        if current is None or current.check_id != str(bound.get("check_id") or ""):
            return
        if any(
            event.message_id == assistant_message_id for event in current.hint_history
        ):
            return
        history = [
            {
                "message_id": event.message_id,
                "run_id": event.run_id,
                "at": event.at,
            }
            for event in current.hint_history
        ]
        history.append(
            {
                "message_id": assistant_message_id,
                "run_id": run.run_id,
                "at": now.isoformat(),
            }
        )
        self._database.scoped(run.account_id).execute(
            "UPDATE study_pending_checks SET hint_history_json = ?, updated_at = ?"
            " WHERE account_id = ? AND conversation_id = ? AND check_id = ?",
            (
                json.dumps(history, ensure_ascii=False),
                now.isoformat(),
                run.account_id,
                run.conversation_id,
                current.check_id,
            ),
        )

    def _activate_posed_check(
        self,
        run: GenerationRunRecord,
        assistant_message_id: str,
        posed: dict[str, Any],
        content: str,
        now: datetime,
        *,
        source_refs: list[StudyVerificationSourceRef] | None = None,
        verification_incomplete: bool = False,
    ) -> None:
        try:
            kind = StudyCheckKind(str(posed.get("kind") or ""))
            question = _short_text(str(posed.get("question") or ""), _QUESTION_LIMIT)
        except ValueError:
            return
        if not question or not _question_displayed(question, content):
            # 未实际展示（失配、截断或改写）：不激活未展示的问题。
            return
        expected_answer = _short_text(str(posed.get("expected_answer") or ""), 300)
        if verification_incomplete and kind == StudyCheckKind.FORMAL_CHECK:
            # Issue 11：本轮查证未完成时不得冻结依赖外部证据的标准答案；
            # 正式练习降级为开放探查（不判分、不设唯一答案）。
            kind = StudyCheckKind.OPEN_PROBE
            expected_answer = ""
        current = self.get_pending(run.account_id, run.conversation_id)
        if current is not None and current.check_id != str(posed.get("check_id") or ""):
            self._write_unverified_evidence(run, current, now)
        version = self._next_version(run.account_id, run.conversation_id, current)
        basis = StudyEvaluationBasis(
            evaluation_points=_text_list(posed.get("evaluation_points")),
            accepted_equivalents=_text_list(posed.get("accepted_equivalents")),
            key_misconceptions=_text_list(posed.get("key_misconceptions")),
            assumptions=_text_list(posed.get("assumptions")),
            expected_answer=expected_answer,
            source_refs=list(source_refs or []),
            evidence_incomplete=verification_incomplete,
        )
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_pending_checks"
            " (account_id, conversation_id, check_id, kind, question,"
            " knowledge_context, basis_json, hint_history_json, source_message_id,"
            " version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_id, conversation_id) DO UPDATE SET"
            " check_id = excluded.check_id, kind = excluded.kind,"
            " question = excluded.question,"
            " knowledge_context = excluded.knowledge_context,"
            " basis_json = excluded.basis_json,"
            " hint_history_json = excluded.hint_history_json,"
            " source_message_id = excluded.source_message_id,"
            " version = excluded.version, updated_at = excluded.updated_at",
            (
                run.account_id,
                run.conversation_id,
                str(posed.get("check_id") or secrets.token_urlsafe(12)),
                kind.value,
                question,
                _short_text(
                    str(posed.get("knowledge_context") or ""),
                    _KNOWLEDGE_CONTEXT_LIMIT,
                ),
                json.dumps(basis.model_dump(mode="json"), ensure_ascii=False),
                "[]",
                assistant_message_id,
                version,
                now.isoformat(),
                now.isoformat(),
            ),
        )

    def _release_skipped_check(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        bound: dict[str, Any],
        now: datetime,
    ) -> None:
        """明确跳过：记录未验证并解除当前等待（不判错、不算缺口）。"""

        current = self.get_pending(run.account_id, run.conversation_id)
        if current is None or current.check_id != str(bound.get("check_id") or ""):
            return
        self._write_unverified_evidence(
            run, current, now, extra_source_ids=[user_message.message_id]
        )
        self._close_pending_if_current(
            run.account_id, run.conversation_id, current.check_id
        )

    def _write_unverified_evidence(
        self,
        run: GenerationRunRecord,
        check: StudyCheckRecord,
        now: datetime,
        *,
        extra_source_ids: list[str] | None = None,
    ) -> None:
        """问题被替换或跳过且没有作答：如实记录「未验证」。"""

        evidence = StudyEvidenceRecord(
            evidence_id=secrets.token_urlsafe(16),
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            check_id=check.check_id,
            check_kind=check.kind,
            check_version=check.version,
            question=check.question,
            knowledge_context=check.knowledge_context,
            basis_protocol_version=check.basis.contract_version,
            answered=False,
            outcome=StudyEvidenceOutcome.NO_ANSWER,
            semantics=StudyEvidenceSemantics.UNVERIFIED,
            assistance=StudyEvidenceAssistance.NONE,
            hint_message_ids=[event.message_id for event in check.hint_history],
            source_message_ids=[
                check.source_message_id,
                *(extra_source_ids or []),
            ][:_SOURCE_MESSAGE_LIMIT],
            evidence_contract_version=VERIFICATION_CONTRACT_VERSION,
            created_at=now,
        )
        self._insert_evidence(evidence)

    def _insert_evidence(self, evidence: StudyEvidenceRecord) -> None:
        payload = evidence.model_dump(mode="json")
        self._database.scoped(evidence.account_id).execute(
            "INSERT INTO study_evidence"
            " (evidence_id, account_id, conversation_id, run_id, check_id,"
            " check_kind, check_version, question, knowledge_context,"
            " basis_protocol_version, answered, outcome, semantics, assistance,"
            " reasoning_note, supporting_json, missing_json, hint_message_ids_json,"
            " source_message_ids_json, source_refs_json, evidence_contract_version,"
            " answer_message_id, feedback_message_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?)"
            " ON CONFLICT(account_id, answer_message_id) DO NOTHING",
            (
                payload["evidence_id"],
                payload["account_id"],
                payload["conversation_id"],
                payload["run_id"],
                payload["check_id"],
                payload["check_kind"],
                payload["check_version"],
                payload["question"],
                payload["knowledge_context"],
                payload["basis_protocol_version"],
                1 if payload["answered"] else 0,
                payload["outcome"],
                payload["semantics"],
                payload["assistance"],
                payload["reasoning_note"],
                json.dumps(payload["supporting"], ensure_ascii=False),
                json.dumps(payload["missing"], ensure_ascii=False),
                json.dumps(payload["hint_message_ids"], ensure_ascii=False),
                json.dumps(payload["source_message_ids"], ensure_ascii=False),
                json.dumps(payload["source_refs"], ensure_ascii=False),
                payload["evidence_contract_version"],
                payload["answer_message_id"],
                payload["feedback_message_id"],
                payload["created_at"],
            ),
        )

    def _close_pending_if_current(
        self, account_id: str, conversation_id: str, check_id: str
    ) -> None:
        self._database.scoped(account_id).execute(
            "DELETE FROM study_pending_checks"
            " WHERE account_id = ? AND conversation_id = ? AND check_id = ?",
            (account_id, conversation_id, check_id),
        )

    def _next_version(
        self,
        account_id: str,
        conversation_id: str,
        current: StudyCheckRecord | None,
    ) -> int:
        """版本在对话内单调递增；旧问题已删除时从证据版本续接。"""

        if current is not None:
            return current.version + 1
        row = self._database.scoped(account_id).execute(
            "SELECT MAX(check_version) AS version FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        latest = row["version"] if row is not None and row["version"] is not None else 0
        return int(latest) + 1


class StudyVerificationPlanner:
    """本轮验证规划：评价作答、记录帮助、提出新检查或不做验证。

    规划失败（能力未注册、服务失败、输出不合约）一律静默降级为
    「不做验证」：回答照常，既有待答问题保持，不伪造评价。
    """

    def __init__(
        self,
        repository: ConversationRepository,
        verification: StudyVerificationRepository,
        local_progress: StudyLocalProgressRepository,
    ) -> None:
        self._repository = repository
        self._verification = verification
        self._local_progress = local_progress

    def plan(
        self,
        *,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message: MessageRecord | None,
        history: list[dict[str, Any]],
        run_context: Any,
        gateway: Any,
        model_override: str | None,
        model_quota: Any,
        budget: Any,
        retrieval_round: Any | None,
        source_verification: Mapping[str, Any] | None = None,
    ) -> str | None:
        # 重试（或租约恢复重放）沿用本轮冻结的评价快照：旧作答始终绑定
        # 当时的待答问题与版本，不会被判到之后的新问题上，也不重复记账。
        existing = self._run_snapshot(account_id, run_context.run_id)
        if existing is not None:
            replay = _snapshot_plan(existing)
            if replay is not None:
                question, has_hint = _snapshot_binding(existing)
                return _directive(
                    replay,
                    question=question,
                    has_hint=has_hint,
                    stuck=bool(existing.get("stuck")),
                )
        pending = self._verification.get_pending(account_id, conversation_id)
        recent = self._verification.list_recent_evidence(account_id, conversation_id)
        stuck = _stuck_state(recent, pending)
        request = self._planner_request(
            account_id,
            conversation_id,
            pending,
            recent,
            history,
            user_message,
            stuck,
        )
        output = self._invoke(request, run_context, gateway, model_override, model_quota, budget)
        if output is None:
            return None
        # Issue 11：本轮查证未完成，或待答问题本身冻结于未完成的查证
        # （依据标记不完整）时：不冻结伪标准答案，也不判学生错。
        verification_incomplete = snapshot_incomplete(source_verification) or (
            pending is not None and pending.basis.evidence_incomplete
        )
        plan = _normalize_plan(
            output,
            pending,
            stuck,
            verification_incomplete=verification_incomplete,
        )
        snapshot = _build_snapshot(
            plan,
            pending,
            retrieval_round,
            assistant_message_id,
            stuck,
            history,
        )
        self._persist_snapshot(account_id, run_context.run_id, snapshot)
        return _directive(
            plan,
            question=pending.question if pending is not None else None,
            has_hint=bool(pending.hint_history) if pending is not None else False,
            stuck=stuck,
        )

    # ------------------------------------------------------------------
    # 模型调用
    # ------------------------------------------------------------------

    def _planner_request(
        self,
        account_id: str,
        conversation_id: str,
        pending: StudyCheckRecord | None,
        recent: list[StudyEvidenceRecord],
        history: list[dict[str, Any]],
        user_message: MessageRecord | None,
        stuck: bool,
    ) -> dict[str, Any]:
        progress = self._local_progress.get(account_id, conversation_id)
        knowledge_context = (
            pending.knowledge_context
            if pending is not None and pending.knowledge_context
            else (progress.knowledge_context if progress is not None else "")
        )
        return {
            "protocol_version": VERIFICATION_CONTRACT_VERSION,
            "student_message": user_message.content if user_message else "",
            "pending_check": (
                {
                    "check_id": pending.check_id,
                    "kind": pending.kind.value,
                    "question": pending.question,
                    "knowledge_context": pending.knowledge_context,
                    "evaluation_points": pending.basis.evaluation_points,
                    "accepted_equivalents": pending.basis.accepted_equivalents,
                    "key_misconceptions": pending.basis.key_misconceptions,
                    "assumptions": pending.basis.assumptions,
                    "expected_answer": pending.basis.expected_answer,
                    "hint_count": len(pending.hint_history),
                }
                if pending is not None
                else None
            ),
            "recent_evidence": [
                {
                    "outcome": item.outcome.value,
                    "semantics": item.semantics.value,
                    "question": item.question,
                    "reasoning_note": item.reasoning_note,
                }
                for item in recent
            ],
            "knowledge_context": knowledge_context,
            "stuck": stuck,
            "recent_messages": [
                {
                    "role": str(message.get("role") or ""),
                    "content": _short_text(str(message.get("content") or ""), 600),
                }
                for message in history
                if message.get("role") in {"user", "assistant"}
            ][-_RECENT_MESSAGE_LIMIT:],
        }

    def _invoke(
        self,
        request: dict[str, Any],
        run_context: Any,
        gateway: Any,
        model_override: str | None,
        model_quota: Any,
        budget: Any,
    ) -> _PlannerOutput | None:
        payload = {
            "messages": [
                {"role": "system", "content": STUDY_VERIFICATION_PLANNER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "json_schema": STUDY_VERIFICATION_PLAN_JSON_SCHEMA,
            "temperature": 0,
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        try:
            result = gateway.invoke(
                _VERIFICATION_CAPABILITY,
                _VERIFICATION_CAPABILITY_VERSION,
                run_context,
                payload=payload,
                model_override=model_override,
                model_quota=model_quota,
                budget=budget,
            )
        except Exception:  # noqa: BLE001 - 辅助规划失败不阻断回答
            return None
        if getattr(result.status, "value", "") not in {"success", "degraded"}:
            return None
        if not isinstance(result.output, dict):
            return None
        try:
            return _PlannerOutput.model_validate(result.output)
        except ValidationError:
            return None

    def _persist_snapshot(
        self, account_id: str, run_id: str, snapshot: dict[str, Any]
    ) -> None:
        current = self._repository.get_generation_run(account_id, run_id)
        if current is None:
            return
        try:
            config = dict(current.config or {})
            config["study_verification"] = snapshot
            self._repository.update_generation_config(account_id, run_id, config)
        except Exception:  # noqa: BLE001 - 快照落库失败不阻断回答
            return

    def _run_snapshot(self, account_id: str, run_id: str) -> dict[str, Any] | None:
        current = self._repository.get_generation_run(account_id, run_id)
        if current is None:
            return None
        snapshot = (current.config or {}).get("study_verification")
        return snapshot if isinstance(snapshot, dict) else None


# ---------------------------------------------------------------------------
# 规划结果的确定性归一化
# ---------------------------------------------------------------------------


def _normalize_plan(
    output: _PlannerOutput,
    pending: StudyCheckRecord | None,
    stuck: bool,
    *,
    verification_incomplete: bool = False,
) -> _NormalizedPlan:
    action = output.action if output.action in _ACTIONS else "none"
    intent = (
        StudyUserIntent(output.intent)
        if output.intent in _INTENTS
        else StudyUserIntent.OTHER
    )
    evaluation: StudyCheckEvaluation | None = None
    if action == "evaluate":
        if pending is None or intent != StudyUserIntent.ANSWER:
            # 没有待答问题或消息不是作答：不评价、不判错。
            action = "help" if pending is not None else "none"
        else:
            outcome = (
                StudyEvidenceOutcome(output.outcome)
                if output.outcome in _OUTCOMES
                else StudyEvidenceOutcome.UNDETERMINED
            )
            if (
                outcome == StudyEvidenceOutcome.MISCONCEPTION
                and verification_incomplete
            ):
                # Issue 11：本轮公开查证未完成，外部事实对错无从核实：
                # 不判误解，保留待确认。
                outcome = StudyEvidenceOutcome.UNDETERMINED
            evaluation = StudyCheckEvaluation(
                outcome=outcome,
                equivalent_accepted=bool(output.equivalent_accepted),
                reasoning_note=_short_text(output.reasoning_note, 400),
                supporting=_text_list(output.supporting),
                missing=_text_list(output.missing),
                feedback_direction=_short_text(output.feedback_direction, 400),
            )
    posed: StudyCheckDraft | None = None
    if output.check_kind in _KINDS and output.check_question.strip():
        kind = StudyCheckKind(output.check_kind)
        expected_answer = _short_text(output.expected_answer, 300)
        if verification_incomplete and kind == StudyCheckKind.FORMAL_CHECK:
            # Issue 11：查证未完成时不冻结伪标准答案；正式练习降级为
            # 开放探查，由模型在回复中作为理解检查提出。
            kind = StudyCheckKind.OPEN_PROBE
            expected_answer = ""
        posed = StudyCheckDraft(
            kind=kind,
            question=_short_text(output.check_question, _QUESTION_LIMIT),
            knowledge_context=_short_text(
                output.check_knowledge_context, _KNOWLEDGE_CONTEXT_LIMIT
            ),
            evaluation_points=_text_list(output.evaluation_points),
            accepted_equivalents=_text_list(output.accepted_equivalents),
            key_misconceptions=_text_list(output.key_misconceptions),
            assumptions=_text_list(output.assumptions),
            expected_answer=expected_answer,
        )
        # 一次只推进一个检查：存在待答问题时，只有「评价并推进」或
        # 「学生明确跳过/转向」才允许提出下一题；否则新题被抑制，
        # 旧问题保持待答（回复中始终只有一个需要作答的问题）。
        allowed = (
            pending is None
            or evaluation is not None
            or intent == StudyUserIntent.SKIP
        )
        if not allowed or posed.kind == StudyCheckKind.FORMAL_CHECK and stuck:
            posed = None
    if action == "pose_check" and posed is None:
        action = "none"
    return _NormalizedPlan(
        action=action,
        intent=intent,
        evaluation=evaluation,
        posed_check=posed,
        posed_check_id=secrets.token_urlsafe(12) if posed is not None else None,
        teaching_direction=_short_text(output.teaching_direction, 300),
    )


def _build_snapshot(
    plan: _NormalizedPlan,
    pending: StudyCheckRecord | None,
    retrieval_round: Any | None,
    assistant_message_id: str,
    stuck: bool,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    assistance: str | None = None
    if pending is not None and plan.evaluation is not None:
        assistance = (
            StudyEvidenceAssistance.AFTER_HINT.value
            if pending.hint_history
            else StudyEvidenceAssistance.INDEPENDENT.value
        )
    return {
        "protocol_version": VERIFICATION_CONTRACT_VERSION,
        "action": plan.action,
        "intent": plan.intent.value,
        "assistance": assistance,
        "stuck": stuck,
        "planned_at": datetime.now(UTC).isoformat(),
        "teaching_direction": plan.teaching_direction,
        "bound_check": (
            {
                "check_id": pending.check_id,
                "kind": pending.kind.value,
                "question": pending.question,
                "knowledge_context": pending.knowledge_context,
                "basis_protocol_version": pending.basis.contract_version,
                "version": pending.version,
                "source_message_id": pending.source_message_id,
            }
            if pending is not None
            else None
        ),
        "hint_message_ids": (
            [event.message_id for event in pending.hint_history]
            if pending is not None
            else []
        ),
        "evaluation": (
            plan.evaluation.model_dump(mode="json")
            if plan.evaluation is not None
            else None
        ),
        "posed_check": (
            {
                **plan.posed_check.model_dump(mode="json"),
                "check_id": plan.posed_check_id,
            }
            if plan.posed_check is not None
            else None
        ),
        "source_refs": _source_refs(retrieval_round),
        "source_message_id": assistant_message_id,
        "recent_message_count": len(history),
    }


def _directive(
    plan: _NormalizedPlan,
    *,
    question: str | None,
    has_hint: bool,
    stuck: bool,
) -> str:
    parts = ["【本轮验证合同（内部依据，不得原样展示给用户）】"]
    if plan.action == "evaluate" and question and plan.evaluation is not None:
        evaluation = plan.evaluation
        parts.append(f"学生正在回答待答问题：「{question}」。")
        if evaluation.reasoning_note:
            parts.append(f"内部判定线索：{evaluation.reasoning_note}")
        if evaluation.feedback_direction:
            parts.append(f"反馈方向：{evaluation.feedback_direction}")
        if evaluation.outcome == StudyEvidenceOutcome.CORRECT:
            parts.append("认可正确的等价表述，简要说明判断成立的原因，不要展示评分。")
        elif evaluation.outcome == StudyEvidenceOutcome.PARTIAL:
            parts.append("先肯定已正确部分，再针对缺失环节给一个具体提示或追问。")
        elif evaluation.outcome == StudyEvidenceOutcome.MISCONCEPTION:
            parts.append("指出具体误解所在环节，用一个反例或更小的问题帮助辨别。")
        else:
            parts.append("无法判断时保留待确认，说明还需要哪一步依据，不要判错。")
        if has_hint:
            parts.append(
                "学生作答前已经获得过实际提示：本次不属于独立完成，"
                "反馈中如实体现帮助记录。"
            )
        else:
            parts.append("学生作答前没有提示：本次按独立作答反馈。")
    elif plan.action == "help":
        labels = {
            StudyUserIntent.HINT_REQUEST: (
                "学生请求提示，不是作答：给出一个能推进思考的提示，"
                "不要直接给出完整答案，也不要判对错。"
            ),
            StudyUserIntent.QUESTION: (
                "学生在提问，不是作答：先回答问题，再自然回到当前问题，"
                "不要判对错。"
            ),
            StudyUserIntent.EXPLAIN_REQUEST: (
                "学生要求再讲一遍，不是作答：可以换例子或降低粒度重新讲解，"
                "不要判对错。"
            ),
            StudyUserIntent.DIGRESSION: (
                "学生暂时岔题，不是作答：简短回应岔题，再确认是否回到当前问题，"
                "不要判对错。"
            ),
            StudyUserIntent.SKIP: (
                "学生想跳过当前问题，不是作答：不要判错，简要说明该问题保持未验证，"
                "再按学生意愿继续。"
            ),
        }
        parts.append(
            labels.get(
                plan.intent,
                "学生本轮消息不是作答：不要判对错，也不要写成作答证据。",
            )
        )
        if question:
            parts.append(f"当前待答问题保持：「{question}」。")
    else:
        if question and plan.posed_check is None:
            parts.append(
                f"当前有待答问题尚未作答：「{question}」；"
                "不要替学生作答，也不要重复追问同一个问题。"
            )
        elif plan.posed_check is None:
            parts.append("本轮不做验证，只讲解或回应。")
    if plan.posed_check is not None:
        parts.append(
            "本轮在回复中必须原样包含以下需要学生作答的问题（可加引导语，不要改写）："
            f"「{plan.posed_check.question}」"
        )
        if plan.posed_check.kind == StudyCheckKind.FORMAL_CHECK:
            parts.append(
                "这是有明确评价要求的练习：题目中给出必要假设与条件；"
                "不要在回复里展示评价要点、答案或评分表。"
            )
        else:
            parts.append(
                "这是开放性理解探查：不要预设唯一正确答案，也不要在回复中判分。"
            )
        parts.append(
            "这是本轮唯一需要学生作答的问题，不要再提出别的问题要求学生作答。"
        )
        if plan.intent == StudyUserIntent.SKIP:
            parts.append(
                "学生明确跳过上一个问题：不要判错，简要说明它保持未验证，"
                "再自然提出本轮问题。"
            )
    elif plan.intent == StudyUserIntent.SKIP:
        parts.append(
            "学生明确跳过当前问题：不要判错，简要说明该问题保持未验证，"
            "再按学生意愿继续。"
        )
    if stuck:
        parts.append(
            "学生近期连续未完成或存在同类误解：改用更小粒度、反例或补充前置知识，"
            "不要重复同类追问，本轮不要提出新的正式练习。"
        )
    return "\n".join(parts)


def _snapshot_plan(snapshot: dict[str, Any]) -> _NormalizedPlan | None:
    """从冻结快照重建规划（重试/恢复重放，不重新调用模型）。"""

    try:
        action = str(snapshot.get("action") or "none")
        if action not in _ACTIONS:
            return None
        raw_intent = str(snapshot.get("intent") or "")
        intent = (
            StudyUserIntent(raw_intent) if raw_intent in _INTENTS
            else StudyUserIntent.OTHER
        )
        evaluation = None
        raw_evaluation = snapshot.get("evaluation")
        if isinstance(raw_evaluation, dict):
            evaluation = StudyCheckEvaluation.model_validate(raw_evaluation)
        posed = None
        posed_id = None
        raw_posed = snapshot.get("posed_check")
        if isinstance(raw_posed, dict):
            kind_value = str(raw_posed.get("kind") or "")
            posed_question = str(raw_posed.get("question") or "").strip()
            if kind_value in _KINDS and posed_question:
                posed = StudyCheckDraft(
                    kind=StudyCheckKind(kind_value),
                    question=posed_question,
                    knowledge_context=str(raw_posed.get("knowledge_context") or ""),
                    evaluation_points=_text_list(raw_posed.get("evaluation_points")),
                    accepted_equivalents=_text_list(
                        raw_posed.get("accepted_equivalents")
                    ),
                    key_misconceptions=_text_list(
                        raw_posed.get("key_misconceptions")
                    ),
                    assumptions=_text_list(raw_posed.get("assumptions")),
                    expected_answer=str(raw_posed.get("expected_answer") or ""),
                )
                posed_id = str(raw_posed.get("check_id") or "") or secrets.token_urlsafe(12)
        return _NormalizedPlan(
            action=action,
            intent=intent,
            evaluation=evaluation,
            posed_check=posed,
            posed_check_id=posed_id,
            teaching_direction=str(snapshot.get("teaching_direction") or ""),
        )
    except (TypeError, ValueError, ValidationError):
        return None


def _snapshot_binding(snapshot: dict[str, Any]) -> tuple[str | None, bool]:
    bound = snapshot.get("bound_check")
    question = (
        str(bound.get("question") or "")
        if isinstance(bound, dict) and bound.get("question")
        else None
    )
    return question, bool(snapshot.get("hint_message_ids"))


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _semantics_for(
    kind: StudyCheckKind,
    outcome: StudyEvidenceOutcome,
    assistance: StudyEvidenceAssistance,
) -> StudyEvidenceSemantics:
    if kind == StudyCheckKind.OPEN_PROBE:
        return StudyEvidenceSemantics.UNDERSTANDING_CLUE
    if outcome == StudyEvidenceOutcome.UNDETERMINED:
        return StudyEvidenceSemantics.UNDETERMINED
    if outcome == StudyEvidenceOutcome.CORRECT:
        return (
            StudyEvidenceSemantics.HINTED_COMPLETION
            if assistance == StudyEvidenceAssistance.AFTER_HINT
            else StudyEvidenceSemantics.INDEPENDENT_COMPLETION
        )
    if outcome == StudyEvidenceOutcome.PARTIAL:
        return StudyEvidenceSemantics.PARTIAL_UNDERSTANDING
    if outcome == StudyEvidenceOutcome.MISCONCEPTION:
        return StudyEvidenceSemantics.MISCONCEPTION
    return StudyEvidenceSemantics.UNVERIFIED


def _stuck_state(
    recent: list[StudyEvidenceRecord], pending: StudyCheckRecord | None
) -> bool:
    streak = 0
    for record in recent:
        if record.outcome in {
            StudyEvidenceOutcome.PARTIAL,
            StudyEvidenceOutcome.MISCONCEPTION,
        }:
            streak += 1
        else:
            break
    hints = len(pending.hint_history) if pending is not None else 0
    return streak >= _STUCK_STREAK_LIMIT or hints >= _STUCK_HINT_LIMIT


def _source_refs(retrieval_round: Any | None) -> list[dict[str, Any]]:
    citations = getattr(retrieval_round, "citations", None) or []
    index_version = getattr(retrieval_round, "index_version_id", None)
    return [
        {
            "object_id": citation.object_id,
            "filename": citation.filename,
            "page_number": citation.page_number,
            "section_title": citation.section_title,
            "index_version_id": index_version,
        }
        for citation in citations[:_SOURCE_REF_LIMIT]
    ]


def _source_refs_from_snapshot(
    snapshot: Mapping[str, Any] | None,
) -> list[StudyVerificationSourceRef]:
    """从查证快照取「实际采用」来源的版本引用；损坏项跳过，不伪造引用。"""

    refs: list[StudyVerificationSourceRef] = []
    for item in snapshot_source_refs(snapshot):
        try:
            refs.append(StudyVerificationSourceRef.model_validate(item))
        except ValidationError:
            continue
    return refs


def _evidence_records(rows: Any) -> list[StudyEvidenceRecord]:
    records: list[StudyEvidenceRecord] = []
    for row in rows:
        try:
            records.append(
                StudyEvidenceRecord(
                    evidence_id=row["evidence_id"],
                    account_id=row["account_id"],
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    check_id=row["check_id"],
                    check_kind=StudyCheckKind(row["check_kind"]),
                    check_version=int(row["check_version"]),
                    question=row["question"],
                    knowledge_context=row["knowledge_context"],
                    basis_protocol_version=row["basis_protocol_version"],
                    answered=bool(row["answered"]),
                    outcome=StudyEvidenceOutcome(row["outcome"]),
                    semantics=StudyEvidenceSemantics(row["semantics"]),
                    assistance=StudyEvidenceAssistance(row["assistance"]),
                    reasoning_note=row["reasoning_note"],
                    supporting=json.loads(row["supporting_json"]),
                    missing=json.loads(row["missing_json"]),
                    hint_message_ids=json.loads(row["hint_message_ids_json"]),
                    source_message_ids=json.loads(row["source_message_ids_json"]),
                    source_refs=json.loads(row["source_refs_json"]),
                    evidence_contract_version=row["evidence_contract_version"],
                    answer_message_id=row["answer_message_id"],
                    feedback_message_id=row["feedback_message_id"],
                    created_at=_parse_time(row["created_at"]),
                )
            )
        except (TypeError, ValueError, KeyError, ValidationError):
            continue
    return records


def _question_displayed(question: str, content: str) -> bool:
    """展示判定：问题按空白归一化后必须完整出现在回复正文中。"""

    normalized_question = " ".join(question.split())
    if len(normalized_question) < 2:
        return False
    return normalized_question in " ".join(content.split())


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:160] for item in value[:6] if str(item).strip()]


def _ref_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in value[:_SOURCE_REF_LIMIT]:
        if isinstance(item, dict):
            refs.append(
                {
                    "object_id": str(item.get("object_id") or ""),
                    "filename": str(item.get("filename") or ""),
                    "page_number": item.get("page_number"),
                    "section_title": item.get("section_title"),
                    "index_version_id": item.get("index_version_id"),
                }
            )
    return refs


def _short_text(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
