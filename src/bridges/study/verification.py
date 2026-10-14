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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from bridges.chat.repository import (
    ConversationRepository,
    GenerationRunRecord,
    MessageRecord,
)
from bridges.contracts.study_controls import StudySessionPauseProjection
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
    StudyPendingSourceStatus,
    StudyUserIntent,
)
from bridges.storage.database import BridgesDatabase
from bridges.study.demand_verification import snapshot_incomplete, snapshot_source_refs
from bridges.study.diagnosis_repository import (
    StudyDiagnosisCandidate,
    evidence_input_version,
)
from bridges.study.local_progress import StudyLocalProgressRepository
from bridges.study.session_summary import (
    SummaryEvidenceItem,
    build_summary_basis,
    render_summary_content,
)
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
#: Issue 10：一条证据可携带的资料引用上限（提问依据 + 本轮读取 + 检索）。
_MATERIAL_REF_LIMIT = 12

#: 题目作废的内部原因（资料失效 / 学生补拍更新）。
_STALE_REASON = "本题所依据的资料已不可用或已更新，题目作废，未按过期依据评价。"
_MATERIAL_UPDATE_REASON = (
    "学生补拍或更新了资料，原题按旧资料版本作废，未按旧依据评价。"
)

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
#: 明确跳过/直接讲解：解除绑定问题的等待并记录未验证（Issue 04）。
_RELEASING_INTENTS = frozenset(
    {StudyUserIntent.SKIP, StudyUserIntent.FULL_EXPLANATION}
)
_RELEASING_INTENT_VALUES = frozenset(item.value for item in _RELEASING_INTENTS)
#: 控制意图：不是作答；其中三项不得在同一轮提出新问题。
_CONTROL_INTENTS = frozenset(
    {
        StudyUserIntent.SKIP,
        StudyUserIntent.FULL_EXPLANATION,
        StudyUserIntent.PAUSE,
        StudyUserIntent.RESUME,
        StudyUserIntent.SUMMARY_REQUEST,
    }
)
_NO_POSE_INTENTS = frozenset(
    {
        StudyUserIntent.FULL_EXPLANATION,
        StudyUserIntent.PAUSE,
        StudyUserIntent.SUMMARY_REQUEST,
    }
)
#: 控制意图 → 小结触发来源（暂停与主动要求各自收尾）。
_SUMMARY_TRIGGERS = {
    StudyUserIntent.SUMMARY_REQUEST: "explicit_request",
    StudyUserIntent.PAUSE: "pause",
}


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

    def projection(
        self,
        *,
        source_status: StudyPendingSourceStatus = (
            StudyPendingSourceStatus.NOT_APPLICABLE
        ),
    ) -> StudyPendingCheckProjection:
        return StudyPendingCheckProjection(
            check_id=self.check_id,
            kind=self.kind,
            question=self.question,
            knowledge_context=self.knowledge_context,
            assistance=self.assistance(),
            source_status=source_status,
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
    wrap_up: bool = False


@dataclass(frozen=True)
class _NormalizedPlan:
    action: str
    intent: StudyUserIntent
    evaluation: StudyCheckEvaluation | None
    posed_check: StudyCheckDraft | None
    posed_check_id: str | None
    teaching_direction: str
    wrap_up: bool = False
    summary_block: str | None = None
    summary_trigger: str | None = None


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
        if record is None:
            return None
        return record.projection(
            source_status=self.source_status(
                account_id, conversation_id, record.basis.material_refs
            )
        )

    # ------------------------------------------------------------------
    # 资料依据的可访问性（Issue 10）
    # ------------------------------------------------------------------

    def source_status(
        self,
        account_id: str,
        conversation_id: str,
        source_refs: list[dict[str, Any]] | None,
    ) -> StudyPendingSourceStatus:
        """题目所依据资料当前是否仍可访问（读取时解析，不写回）。"""

        refs = [ref for ref in (source_refs or []) if isinstance(ref, dict)]
        if not refs:
            return StudyPendingSourceStatus.NOT_APPLICABLE
        if all(
            self._ref_available(account_id, conversation_id, ref) for ref in refs
        ):
            return StudyPendingSourceStatus.AVAILABLE
        return StudyPendingSourceStatus.UNAVAILABLE

    def resolve_source_refs(
        self,
        account_id: str,
        conversation_id: str,
        source_refs: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """为每条引用补齐 ``available`` 标记（证据冻结与失效判定共用）。"""

        resolved: list[dict[str, Any]] = []
        for ref in source_refs or []:
            if not isinstance(ref, dict):
                continue
            item = dict(ref)
            item["available"] = self._ref_available(
                account_id, conversation_id, item
            )
            resolved.append(item)
        return resolved

    def _ref_available(
        self, account_id: str, conversation_id: str, ref: dict[str, Any]
    ) -> bool:
        """引用的对象仍有效且（照片/附件引用）绑定行仍存在。"""

        object_id = str(ref.get("object_id") or "")
        if not object_id:
            # 无对象标识的引用（旧格式）没有可核验的失效证据：不据此判失效。
            return True
        row = self._database.scoped(account_id).execute(
            "SELECT status FROM objects WHERE account_id = ? AND object_id = ?",
            (account_id, object_id),
        ).fetchone()
        if row is None or str(row["status"]) != "active":
            return False
        message_id = str(ref.get("message_id") or "")
        if message_id:
            bound = self._database.scoped(account_id).execute(
                "SELECT 1 FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND object_id = ? AND message_id = ?",
                (account_id, conversation_id, object_id, message_id),
            ).fetchone()
            if bound is None:
                return False
        return True

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

    def get_evidence(
        self, account_id: str, evidence_id: str
    ) -> StudyEvidenceRecord | None:
        """按证据身份读取单条证据；供后台诊断独立核验。"""

        row = self._database.scoped(account_id).execute(
            "SELECT * FROM study_evidence"
            " WHERE account_id = ? AND evidence_id = ?",
            (account_id, evidence_id),
        ).fetchone()
        if row is None:
            return None
        records = _evidence_records([row])
        return records[0] if records else None

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
    ) -> StudyDiagnosisCandidate | None:
        """按规划快照提交本轮：评价证据、提示记录与新待答问题激活。

        快照来自运行配置（规划时写入，重试沿用）；缺少或损坏时不做
        任何验证写入，不伪造评价。停止/失败轮次不会进入本方法。

        真正写入一条可诊断证据时返回诊断候选，供调用方在同一事务内
        登记后台诊断任务；重复作答（防重复记账命中）与非作答返回 None。
        """

        latest = repository.get_generation_run(run.account_id, run.run_id)
        config = (latest.config if latest is not None else run.config) or {}
        snapshot = config.get("study_verification")
        if not isinstance(snapshot, dict):
            return None
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
        stale = snapshot.get("stale_check")
        candidate: StudyDiagnosisCandidate | None = None
        if isinstance(bound, dict) and isinstance(stale, dict):
            # Issue 10：所依据资料已不可用（删除/不可访问）——题目作废，
            # 记未验证并解除等待，绝不按过期依据评价，也不改写历史判定。
            self._release_void_check(
                run,
                user_message,
                bound,
                timestamp,
                reason=str(stale.get("reason") or _STALE_REASON),
            )
        elif (
            isinstance(bound, dict)
            and intent == StudyUserIntent.MATERIAL_UPDATE.value
        ):
            # Issue 10：学生补拍/更新资料，不是作答——原题按旧版本作废。
            self._release_void_check(
                run,
                user_message,
                bound,
                timestamp,
                reason=_MATERIAL_UPDATE_REASON,
            )
        elif intent in _RELEASING_INTENT_VALUES and isinstance(bound, dict):
            # 明确跳过或要求直接完整讲解：解除绑定问题的等待，如实记录
            # 「未验证」允许继续；旧控制重试只作用于其绑定的问题（Issue 04）。
            self._release_skipped_check(run, user_message, bound, timestamp)
        if action == "evaluate" and isinstance(bound, dict):
            candidate = self._write_answer_evidence(
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
        return candidate

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
    ) -> StudyDiagnosisCandidate | None:
        evaluation = snapshot.get("evaluation")
        if not isinstance(evaluation, dict):
            return None
        try:
            kind = StudyCheckKind(str(bound.get("kind") or ""))
            outcome = StudyEvidenceOutcome(str(evaluation.get("outcome") or ""))
        except ValueError:
            return None
        if outcome == StudyEvidenceOutcome.NO_ANSWER:
            return None
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
            # Issue 04：证据标识在规划时冻结（小结依据引用同一标识），
            # 重试沿用；缺失时为兼容旧快照现场生成。
            evidence_id=str(snapshot.get("evidence_id") or "")
            or secrets.token_urlsafe(16),
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
            source_refs=self.resolve_source_refs(
                run.account_id,
                run.conversation_id,
                _merge_refs(
                    bound.get("material_refs"),
                    snapshot.get("material_refs"),
                    snapshot.get("source_refs"),
                ),
            ),
            evidence_contract_version=VERIFICATION_CONTRACT_VERSION,
            answer_message_id=user_message.message_id,
            feedback_message_id=assistant_message_id,
            created_at=now,
        )
        inserted = self._insert_evidence(evidence)
        # 诊断候选携带展示前冻结的私有评价依据：取自已绑定待答问题的
        # 冻结行（当前回合快照的 posed_check 属于下一题，不能混用）。
        pending = self.get_pending(run.account_id, run.conversation_id)
        frozen_basis = (
            pending.basis.model_dump(mode="json")
            if pending is not None and pending.check_id == evidence.check_id
            else _basis_payload(snapshot)
        )
        self._close_pending_if_current(
            run.account_id, run.conversation_id, evidence.check_id
        )
        if not inserted or evidence.semantics == StudyEvidenceSemantics.UNVERIFIED:
            # 重复作答（防重复记账命中）或未验证语义：不重复登记诊断。
            return None
        return StudyDiagnosisCandidate(
            account_id=evidence.account_id,
            conversation_id=evidence.conversation_id,
            evidence_id=evidence.evidence_id,
            question=evidence.question,
            knowledge_context=evidence.knowledge_context,
            input_version=evidence_input_version(evidence),
            basis=frozen_basis,
            assistance=evidence.assistance.value,
            answer_message_id=user_message.message_id,
            check_kind=evidence.check_kind.value,
            source_refs=list(evidence.source_refs),
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
        # Issue 11：本轮查证未完成时不得冻结依赖外部证据的标准答案；
        # 正式练习降级为开放探查（不判分、不设唯一答案）。
        expected_answer = _short_text(str(posed.get("expected_answer") or ""), 300)
        if verification_incomplete and kind == StudyCheckKind.FORMAL_CHECK:
            kind = StudyCheckKind.OPEN_PROBE
            expected_answer = ""
        posed_check_id = str(posed.get("check_id") or "")
        current = self.get_pending(run.account_id, run.conversation_id)
        if current is not None and current.check_id == posed_check_id:
            # Issue 04：该问题已经在展示轮激活且未被推进——重试重放不
            # 升级版本、不重复记账。
            return
        if posed_check_id and self._check_has_evidence(
            run.account_id, run.conversation_id, posed_check_id
        ):
            # 该问题已经被作答/跳过/替换过：旧重试不能复活它，也不影响
            # 后来生成的新待答问题。
            return
        if current is not None and current.check_id != posed_check_id:
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
            material_refs=_ref_list(posed.get("material_refs")),
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

    def _release_void_check(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        bound: dict[str, Any],
        now: datetime,
        *,
        reason: str,
    ) -> None:
        """资料更新/失效致题目作废：记录未验证并解除等待，不判对错。"""

        current = self.get_pending(run.account_id, run.conversation_id)
        if current is None or current.check_id != str(bound.get("check_id") or ""):
            return
        self._write_unverified_evidence(
            run,
            current,
            now,
            extra_source_ids=[user_message.message_id],
            source_refs=self.resolve_source_refs(
                run.account_id, run.conversation_id, current.basis.material_refs
            ),
            reasoning_note=reason,
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
        source_refs: list[dict[str, Any]] | None = None,
        reasoning_note: str = "",
    ) -> None:
        """问题被替换、跳过或作废且没有作答：如实记录「未验证」。

        Issue 04：同一问题最多一条未验证证据——重试重放、跳过与替换都
        不会重复记账或在小结里出现两次。
        """

        existing = self._database.scoped(run.account_id).execute(
            "SELECT 1 AS present FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ? AND check_id = ?"
            " LIMIT 1",
            (run.account_id, run.conversation_id, check.check_id),
        ).fetchone()
        if existing is not None:
            return
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
            reasoning_note=_short_text(reasoning_note, 400),
            hint_message_ids=[event.message_id for event in check.hint_history],
            source_message_ids=[
                check.source_message_id,
                *(extra_source_ids or []),
            ][:_SOURCE_MESSAGE_LIMIT],
            source_refs=list(source_refs or [])[:_MATERIAL_REF_LIMIT],
            evidence_contract_version=VERIFICATION_CONTRACT_VERSION,
            created_at=now,
        )
        self._insert_evidence(evidence)

    def _insert_evidence(self, evidence: StudyEvidenceRecord) -> bool:
        """写入证据；重复作答（同账户同回答消息）命中时不重复记账。"""

        payload = evidence.model_dump(mode="json")
        cursor = self._database.scoped(evidence.account_id).execute(
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
        return cursor.rowcount > 0

    def _close_pending_if_current(
        self, account_id: str, conversation_id: str, check_id: str
    ) -> None:
        self._database.scoped(account_id).execute(
            "DELETE FROM study_pending_checks"
            " WHERE account_id = ? AND conversation_id = ? AND check_id = ?",
            (account_id, conversation_id, check_id),
        )

    def _check_has_evidence(
        self, account_id: str, conversation_id: str, check_id: str
    ) -> bool:
        """该问题是否已经产生过证据（作答或未验证）：旧重试不复活它。"""

        row = self._database.scoped(account_id).execute(
            "SELECT 1 AS present FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ? AND check_id = ?"
            " LIMIT 1",
            (account_id, conversation_id, check_id),
        ).fetchone()
        return row is not None

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
        material_refs: list[dict[str, Any]] | None = None,
        has_new_material: bool = False,
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
                    stale_reason=_snapshot_stale_reason(existing),
                )
        refs = _material_ref_list(material_refs)
        pending = self._verification.get_pending(account_id, conversation_id)
        recent = self._verification.list_recent_evidence(account_id, conversation_id)
        stuck = _stuck_state(recent, pending)
        stale_reason = self._stale_reason(account_id, conversation_id, pending)
        pause = self._active_pause(account_id, conversation_id)
        request = self._planner_request(
            account_id,
            conversation_id,
            pending,
            recent,
            history,
            user_message,
            stuck,
            refs,
            has_new_material,
            pause,
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
            has_new_material=has_new_material,
            stale_reason=stale_reason,
        )
        summary = None
        if plan.summary_trigger is not None:
            summary = self._build_summary(
                account_id, conversation_id, plan, pending
            )
            if summary is None:
                plan = _strip_summary(plan)
            else:
                # 规范小结原文随本轮导师回复交付（指令要求逐行原样包含）。
                plan = replace(plan, summary_block=str(summary["content"]))
        snapshot = _build_snapshot(
            plan,
            pending,
            retrieval_round,
            assistant_message_id,
            stuck,
            history,
            material_refs=refs,
            stale_reason=stale_reason,
            pause=pause,
            summary=summary,
        )
        self._persist_snapshot(account_id, run_context.run_id, snapshot)
        return _directive(
            plan,
            question=pending.question if pending is not None else None,
            has_hint=bool(pending.hint_history) if pending is not None else False,
            stuck=stuck,
            stale_reason=stale_reason,
        )

    def _stale_reason(
        self,
        account_id: str,
        conversation_id: str,
        pending: StudyCheckRecord | None,
    ) -> str | None:
        """待答问题的资料依据是否已失效（删除/不可访问）。

        Issue 10：任何一条提问依据不再可访问时，整题按过期依据处理——
        不能确定题目依赖哪一条，保守地不在旧依据上评价。
        """

        if pending is None or not pending.basis.material_refs:
            return None
        status = self._verification.source_status(
            account_id, conversation_id, pending.basis.material_refs
        )
        if status == StudyPendingSourceStatus.UNAVAILABLE:
            return _STALE_REASON
        return None

    def _active_pause(
        self, account_id: str, conversation_id: str
    ) -> StudySessionPauseProjection | None:
        from bridges.study.controls import StudyControlRepository

        return StudyControlRepository(self._repository.database).active_pause(
            account_id, conversation_id
        )

    def _build_summary(
        self,
        account_id: str,
        conversation_id: str,
        plan: _NormalizedPlan,
        pending: StudyCheckRecord | None,
    ) -> dict[str, Any] | None:
        """规划时冻结小结依据与规范原文（终态事务按实际证据重建引用）。

        小结只使用实际记录：既有证据 + 本轮规划的评价（其语义必然与终态
        写入一致，因为两者都由同一冻结依据推导）；正式诊断尚未提供时
        保持待确认。任何一步读不到依据都返回 None，本轮不做小结也不
        伪造。
        """

        try:
            evidence = self._verification.list_evidence(account_id, conversation_id)
        except Exception:  # noqa: BLE001 - 辅助小结失败不阻断回答
            return None
        progress = self._local_progress.get(account_id, conversation_id)
        items = [
            SummaryEvidenceItem(
                evidence_id=item.evidence_id,
                question=item.question,
                kind=item.check_kind.value,
                semantics=item.semantics.value,
            )
            for item in evidence
        ]
        if plan.evaluation is not None and pending is not None:
            assistance = (
                StudyEvidenceAssistance.AFTER_HINT
                if pending.hint_history
                else StudyEvidenceAssistance.INDEPENDENT
            )
            items.append(
                SummaryEvidenceItem(
                    evidence_id="",
                    question=pending.question,
                    kind=pending.kind.value,
                    semantics=_semantics_for(
                        pending.kind, plan.evaluation.outcome, assistance
                    ).value,
                    source="current_turn",
                )
            )
        pending_after = None
        if (
            pending is not None
            and plan.evaluation is None
            and plan.intent not in _RELEASING_INTENTS
        ):
            pending_after = {
                "check_id": pending.check_id,
                "question": pending.question,
                "version": pending.version,
            }
        basis = build_summary_basis(
            trigger=plan.summary_trigger or "",
            items=items,
            pending_after=pending_after,
            progress=(
                {
                    "version": progress.version,
                    "topic": progress.topic,
                    "current_focus": progress.current_focus,
                }
                if progress is not None
                else None
            ),
            generated_at=datetime.now(UTC).isoformat(),
        )
        return {
            "summary_id": secrets.token_urlsafe(16),
            "trigger": plan.summary_trigger,
            "content": render_summary_content(basis),
            "basis": basis,
        }

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
        material_refs: list[dict[str, Any]],
        has_new_material: bool,
        pause: StudySessionPauseProjection | None,
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
            "paused": pause is not None,
            "pause_position": (pause.position or pause.topic) if pause is not None else "",
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
                    "material_grounded": bool(pending.basis.material_refs),
                }
                if pending is not None
                else None
            ),
            "material_context": {
                "student_sent_new_material": has_new_material,
                "material_reads": [
                    {
                        "kind": ref.get("kind"),
                        "filename": ref.get("filename"),
                        "read_range": ref.get("read_range"),
                        "source_version": _short_text(
                            str(ref.get("source_version") or ""), 64
                        ),
                    }
                    for ref in material_refs
                ],
            },
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
    has_new_material: bool = False,
    stale_reason: str | None = None,
) -> _NormalizedPlan:
    action = output.action if output.action in _ACTIONS else "none"
    intent = (
        StudyUserIntent(output.intent)
        if output.intent in _INTENTS
        else StudyUserIntent.OTHER
    )
    # Issue 10：material_update 只在学生确实携带新材料、且待答问题基于
    # 资料时成立；否则按普通意图处理，避免误作废真实作答。
    material_update = (
        intent == StudyUserIntent.MATERIAL_UPDATE
        and has_new_material
        and pending is not None
        and bool(pending.basis.material_refs)
    )
    if intent == StudyUserIntent.MATERIAL_UPDATE and not material_update:
        intent = StudyUserIntent.OTHER
    evaluation: StudyCheckEvaluation | None = None
    if action == "evaluate":
        if (
            pending is None
            or intent != StudyUserIntent.ANSWER
            or stale_reason is not None
        ):
            # 没有待答问题、消息不是作答或题目依据已失效：不评价、不判错。
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
        # 一次只推进一个检查：存在待答问题时，只有「评价并推进」「学生
        # 明确跳过/转向」「学生补拍更新资料」或「旧依据已失效」才允许
        # 提出下一题；否则新题被抑制，旧问题保持待答。
        allowed = (
            pending is None
            or evaluation is not None
            or intent == StudyUserIntent.SKIP
            or material_update
            or stale_reason is not None
        )
        if not allowed or posed.kind == StudyCheckKind.FORMAL_CHECK and stuck:
            posed = None
        # Issue 04：直接讲解、暂停与要求小结不借本轮附加强制测验。
        if intent in _NO_POSE_INTENTS:
            posed = None
    # Issue 04：自然收尾只在问题已完成（本轮评价）或当前没有待答问题时
    # 成立；收尾轮不自动增加考试，同轮规划的新检查一律抑制。
    wrap_up = (
        bool(output.wrap_up)
        and intent not in _CONTROL_INTENTS
        and (evaluation is not None or pending is None)
    )
    if wrap_up:
        posed = None
    if action == "pose_check" and posed is None:
        action = "none"
    trigger = _SUMMARY_TRIGGERS.get(intent)
    if trigger is None and wrap_up:
        trigger = "natural_completion"
    return _NormalizedPlan(
        action=action,
        intent=intent,
        evaluation=evaluation,
        posed_check=posed,
        posed_check_id=secrets.token_urlsafe(12) if posed is not None else None,
        teaching_direction=_short_text(output.teaching_direction, 300),
        wrap_up=wrap_up,
        summary_trigger=trigger,
    )


def _strip_summary(plan: _NormalizedPlan) -> _NormalizedPlan:
    """小结依据构建失败：本轮不做小结，也不虚报已收尾。"""

    return _NormalizedPlan(
        action=plan.action,
        intent=plan.intent,
        evaluation=plan.evaluation,
        posed_check=plan.posed_check,
        posed_check_id=plan.posed_check_id,
        teaching_direction=plan.teaching_direction,
        wrap_up=False,
        summary_block=None,
        summary_trigger=None,
    )


def _build_snapshot(
    plan: _NormalizedPlan,
    pending: StudyCheckRecord | None,
    retrieval_round: Any | None,
    assistant_message_id: str,
    stuck: bool,
    history: list[dict[str, Any]],
    *,
    material_refs: list[dict[str, Any]] | None = None,
    stale_reason: str | None = None,
    pause: StudySessionPauseProjection | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assistance: str | None = None
    if pending is not None and plan.evaluation is not None:
        assistance = (
            StudyEvidenceAssistance.AFTER_HINT.value
            if pending.hint_history
            else StudyEvidenceAssistance.INDEPENDENT.value
        )
    bound_refs = _ref_list(pending.basis.material_refs) if pending is not None else []
    turn_refs = _material_ref_list(material_refs)
    # 新题的依据：优先本轮实际读取的资料；本轮没有新读取时沿用在答问题
    # 的依据（同一份资料的后续追问不凭空失去来源）。
    question_refs = _merge_refs(turn_refs, bound_refs if not turn_refs else [])
    return {
        "protocol_version": VERIFICATION_CONTRACT_VERSION,
        "action": plan.action,
        "intent": plan.intent.value,
        "assistance": assistance,
        "stuck": stuck,
        "planned_at": datetime.now(UTC).isoformat(),
        "teaching_direction": plan.teaching_direction,
        "material_refs": turn_refs,
        "stale_check": (
            {"reason": stale_reason, "material_refs": bound_refs}
            if stale_reason is not None and pending is not None
            else None
        ),
        # Issue 04：作答证据标识在规划时冻结，重试沿用同一标识；小结依据
        # 引用因此始终指向同一条真实证据。
        "evidence_id": (
            secrets.token_urlsafe(16) if plan.evaluation is not None else None
        ),
        "control": _control_snapshot(plan, pending, pause),
        "summary": summary,
        "bound_check": (
            {
                "check_id": pending.check_id,
                "kind": pending.kind.value,
                "question": pending.question,
                "knowledge_context": pending.knowledge_context,
                "basis_protocol_version": pending.basis.contract_version,
                "version": pending.version,
                "source_message_id": pending.source_message_id,
                "material_refs": bound_refs,
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
                "material_refs": question_refs,
            }
            if plan.posed_check is not None
            else None
        ),
        "source_refs": _source_refs(retrieval_round),
        "source_message_id": assistant_message_id,
        "recent_message_count": len(history),
    }


def _control_snapshot(
    plan: _NormalizedPlan,
    pending: StudyCheckRecord | None,
    pause: StudySessionPauseProjection | None,
) -> dict[str, Any] | None:
    """冻结本轮用户学习控制（Issue 04）：来源、作用问题与暂停绑定。"""

    kind: str | None = None
    if plan.intent == StudyUserIntent.FULL_EXPLANATION:
        kind = "full_explanation"
    elif plan.intent == StudyUserIntent.SKIP:
        kind = "skip"
    elif plan.intent == StudyUserIntent.PAUSE:
        kind = "pause"
    elif plan.intent == StudyUserIntent.RESUME:
        kind = "resume"
    elif plan.intent == StudyUserIntent.SUMMARY_REQUEST:
        kind = "summary_request"
    if kind is None:
        return None
    control: dict[str, Any] = {
        "kind": kind,
        "released": plan.intent in _RELEASING_INTENTS and pending is not None,
        "bound_check": (
            {
                "check_id": pending.check_id,
                "kind": pending.kind.value,
                "question": pending.question,
                "version": pending.version,
            }
            if pending is not None
            else None
        ),
    }
    if kind == "pause":
        control["pause_id"] = secrets.token_urlsafe(16)
    elif kind == "resume":
        # 只恢复规划时确实在暂停中的那一条记录；旧控制重试不会解除
        # 之后发生的新暂停。
        control["pause_id"] = pause.pause_id if pause is not None else None
    return control


def _directive(
    plan: _NormalizedPlan,
    *,
    question: str | None,
    has_hint: bool,
    stuck: bool,
    stale_reason: str | None = None,
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
        if plan.wrap_up:
            parts.append(
                "本轮作答已评估完成，适合自然收尾：不要追加测验，"
                "只陈述本次实际表现与仍待确认之处。"
            )
    elif plan.intent == StudyUserIntent.FULL_EXPLANATION:
        parts.append(
            "学生要求直接完整讲解、不想作答当前问题，这不是作答："
            "给出完整、准确的说明，允许例子、推导或参考解法，"
            "尊重学生对长度与方式的要求。"
        )
        if question:
            parts.append(
                f"当前问题「{question}」保持未验证：如实简要说明后按学生意愿继续，"
                "不要判错、不要附加强制测验。"
            )
    elif plan.intent == StudyUserIntent.PAUSE:
        parts.append(
            "学生要求暂停学习：确认进展与可继续位置已经保存，"
            "不提出新问题，不要求学生继续作答。"
        )
    elif plan.intent == StudyUserIntent.RESUME:
        parts.append(
            "学生要求继续学习：从暂停位置或当前内容自然接着进行；"
            "未验证内容不阻塞继续，也不重新判定。"
        )
    elif plan.intent == StudyUserIntent.SUMMARY_REQUEST:
        parts.append(
            "学生要求阶段小结：只依据本轮提供的小结内容如实收尾，"
            "不重新判定此前作答。"
        )
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
    if plan.summary_block is not None:
        parts.append("【本轮阶段小结（必须原样包含）】")
        parts.append(
            "必须在回复中原样包含以下小结全文（可以加一句自然的引导语；"
            "逐行包含，不得改写、合并或省略）："
        )
        parts.append(plan.summary_block)
        parts.append(
            "不要追加新的测验或要求作答的问题；不要输出掌握百分比、正确率"
            "或长期能力判断；不要重新判定此前作答。"
        )
    elif plan.wrap_up:
        parts.append("本轮适合自然收尾：用简明小结结束本轮，不要再提出新的问题。")
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
    if plan.intent == StudyUserIntent.MATERIAL_UPDATE:
        parts.append(
            "学生补拍或更新了资料：以本轮实际读取到的新版本为准讲解与出题；"
            "上一题按旧资料版本作废，不要用旧结论评价学生。"
        )
    if stale_reason:
        parts.append(
            f"上一个问题的资料依据已失效（{stale_reason}）："
            "不要按过期依据评价，简要说明该问题保持未验证。"
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
        raw_summary = snapshot.get("summary")
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        summary_block = str(summary.get("content") or "") or None
        summary_trigger = str(summary.get("trigger") or "") or None
        return _NormalizedPlan(
            action=action,
            intent=intent,
            evaluation=evaluation,
            posed_check=posed,
            posed_check_id=posed_id,
            teaching_direction=str(snapshot.get("teaching_direction") or ""),
            wrap_up=summary_trigger == "natural_completion",
            summary_block=summary_block,
            summary_trigger=summary_trigger,
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


def _basis_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """从冻结快照提取诊断用私有评价依据（与展示前依据同源）。"""

    posed = snapshot.get("posed_check")
    if not isinstance(posed, dict):
        return StudyEvaluationBasis().model_dump(mode="json")
    basis = StudyEvaluationBasis(
        evaluation_points=_text_list(posed.get("evaluation_points")),
        accepted_equivalents=_text_list(posed.get("accepted_equivalents")),
        key_misconceptions=_text_list(posed.get("key_misconceptions")),
        assumptions=_text_list(posed.get("assumptions")),
        expected_answer=_short_text(str(posed.get("expected_answer") or ""), 300),
    )
    return basis.model_dump(mode="json")


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
            "kind": "knowledge",
            "citation_id": getattr(citation, "citation_id", None),
            "source_layer": getattr(citation, "source_layer", None),
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


_REF_KEYS = (
    "kind",
    "citation_id",
    "source_layer",
    "object_id",
    "message_id",
    "filename",
    "media_type",
    "source_version",
    "read_range",
    "ordinal",
    "page_number",
    "section_title",
    "index_version_id",
    "available",
)


def _ref_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in value[:_MATERIAL_REF_LIMIT]:
        if not isinstance(item, dict):
            continue
        ref = {key: item.get(key) for key in _REF_KEYS if key in item}
        if ref:
            refs.append(ref)
    return refs


def _material_ref_list(value: Any) -> list[dict[str, Any]]:
    """本轮实际读取的资料引用（只保留已知键）。"""

    return _ref_list(value)


def _merge_refs(*groups: Any) -> list[dict[str, Any]]:
    """按顺序合并引用组并去重（先出现者优先），上限 ``_MATERIAL_REF_LIMIT``。"""

    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for group in groups:
        for ref in _ref_list(group):
            key = (
                str(ref.get("kind") or ""),
                str(ref.get("object_id") or ""),
                str(ref.get("message_id") or ""),
                ref.get("ordinal"),
                ref.get("page_number"),
                str(ref.get("citation_id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref)
            if len(merged) >= _MATERIAL_REF_LIMIT:
                return merged
    return merged


def _snapshot_stale_reason(snapshot: dict[str, Any]) -> str | None:
    """重放快照中的失效说明（无则 None）。"""

    stale = snapshot.get("stale_check")
    if not isinstance(stale, dict):
        return None
    reason = str(stale.get("reason") or "").strip()
    return reason or _STALE_REASON


def _short_text(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
