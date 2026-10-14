"""Issue 05：后台独立诊断与账户级学习档案的持久化。

职责边界：

- 诊断任务业务状态、结果版本与账户级知识判断由学习领域拥有；共享队列
  只负责领取、租约与重试调度（业务行与队列行在同一事务登记）。
- 知识判断按「证据发生顺序」归并：迟到的旧证据不覆盖更新证据；方向
  冲突保留为待确认，不伪造成单一结论。
- 单次完成只是当前证据下的判断，不写长期掌握、百分比或能力标签。
- 来源失效（证据被删除）后结果不再写入；呈现层按仍可读取的证据投影，
  不显示悬空引用。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.study_diagnosis import (
    DIAGNOSIS_CONTRACT_VERSION,
    StudyDiagnosisStatus,
    StudyDiagnosisTaskProjection,
    StudyDiagnosisVerdict,
    StudyLearningRecordProjection,
    StudyRecordEvidenceProjection,
)
from bridges.contracts.study_verification import (
    StudyEvaluationBasis,
    StudyEvidenceAssistance,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError

#: 每个档案条目保留的已归并证据上限（有界，避免无限档案行）。
_RECORD_EVIDENCE_LIMIT = 20
#: 单个对话投影返回的档案条目上限。
_RECORD_QUERY_LIMIT = 20
#: 单个对话投影返回的诊断任务上限。
_TASK_QUERY_LIMIT = 20

_SUPPORT_VERDICTS = frozenset(
    {
        StudyDiagnosisVerdict.INDEPENDENT_COMPLETION,
        StudyDiagnosisVerdict.HINTED_COMPLETION,
    }
)
_GAP_VERDICTS = frozenset({StudyDiagnosisVerdict.MISCONCEPTION})


@dataclass(frozen=True)
class StudyDiagnosisCandidate:
    """一次作答产生的诊断候选（在回复终态事务内登记）。"""

    account_id: str
    conversation_id: str
    evidence_id: str
    question: str
    knowledge_context: str
    input_version: str
    basis: dict[str, Any]
    assistance: str
    answer_message_id: str
    check_kind: str
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StudyDiagnosisResult:
    """一次独立诊断的结论（已通过输出合同校验）。"""

    subject: str
    title: str
    usage_context: str
    verdict: StudyDiagnosisVerdict
    performance: str
    supporting: list[str]
    refuting: list[str]
    uncertainty: str
    next_validation: str


@dataclass(frozen=True)
class StudyDiagnosisTaskRecord:
    """诊断任务业务行；``basis`` 是私有评价依据，不进用户投影。"""

    task_id: str
    account_id: str
    conversation_id: str
    evidence_id: str
    answer_message_id: str
    question: str
    knowledge_context: str
    input_version: str
    protocol_version: str
    basis: StudyEvaluationBasis
    status: StudyDiagnosisStatus
    attempt: int
    generation: int
    failure_reason: str
    result_id: str | None
    result_version: int
    created_at: datetime
    updated_at: datetime

    def projection(self) -> StudyDiagnosisTaskProjection:
        return StudyDiagnosisTaskProjection(
            task_id=self.task_id,
            status=self.status,
            question=self.question,
            knowledge_context=self.knowledge_context,
            evidence_message_id=self.answer_message_id or None,
            failure_reason=self.failure_reason,
            attempts=self.attempt,
            retryable=self.status == StudyDiagnosisStatus.FAILED,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def diagnosis_task_id(account_id: str, evidence_id: str, input_version: str) -> str:
    """幂等单元：账户 + 证据身份 + 输入版本 + 诊断协议版本。"""

    digest = hashlib.sha256(
        "\x1f".join(
            (account_id, evidence_id, input_version, DIAGNOSIS_CONTRACT_VERSION)
        ).encode("utf-8")
    ).hexdigest()
    return f"dx-{digest[:32]}"


def evidence_input_version(evidence: Any) -> str:
    """诊断输入版本：检查版本 + 依据协议 + 证据合同版本。"""

    return "|".join(
        (
            str(evidence.check_version),
            str(evidence.basis_protocol_version),
            str(evidence.evidence_contract_version),
        )
    )


def knowledge_key(subject: str, title: str, usage_context: str) -> str:
    """知识点身份：学科 + 标题 + 用途上下文；仅标题相同不合并。"""

    parts = [
        _normalize_identity(subject),
        _normalize_identity(title),
        _normalize_identity(usage_context),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


class StudyDiagnosisRepository:
    """诊断任务、结果与账户级档案的读写（账户作用域强制）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 任务登记与状态
    # ------------------------------------------------------------------

    def create_task_in_transaction(
        self,
        candidate: StudyDiagnosisCandidate,
        *,
        now: datetime | None = None,
    ) -> str:
        """登记诊断任务行（幂等）；调用方在同一事务内入队。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        task_id = diagnosis_task_id(
            candidate.account_id, candidate.evidence_id, candidate.input_version
        )
        self._database.scoped(candidate.account_id).execute(
            "INSERT INTO study_diagnosis_tasks"
            " (task_id, account_id, conversation_id, evidence_id,"
            " answer_message_id, question, knowledge_context, input_version,"
            " protocol_version, basis_json, status, attempt, generation,"
            " failure_reason, result_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, '', 0, ?, ?)"
            " ON CONFLICT(task_id) DO NOTHING",
            (
                task_id,
                candidate.account_id,
                candidate.conversation_id,
                candidate.evidence_id,
                candidate.answer_message_id,
                candidate.question,
                candidate.knowledge_context,
                candidate.input_version,
                DIAGNOSIS_CONTRACT_VERSION,
                json.dumps(candidate.basis, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return task_id

    def get_task(
        self, account_id: str, task_id: str
    ) -> StudyDiagnosisTaskRecord | None:
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM study_diagnosis_tasks"
            " WHERE account_id = ? AND task_id = ?",
            (account_id, task_id),
        ).fetchone()
        return _task_record(row) if row is not None else None

    def list_tasks(
        self,
        account_id: str,
        conversation_id: str,
        *,
        limit: int = _TASK_QUERY_LIMIT,
    ) -> list[StudyDiagnosisTaskProjection]:
        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_diagnosis_tasks"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (account_id, conversation_id, limit),
        ).fetchall()
        projections: list[StudyDiagnosisTaskProjection] = []
        for row in rows:
            record = _task_record(row)
            if record is not None:
                projections.append(record.projection())
        return projections

    def mark_running(
        self, account_id: str, task_id: str, *, now: datetime | None = None
    ) -> int | None:
        """把待处理/运行中任务标记为本轮执行，返回新的执行代次。

        代次守卫让租约恢复后的迟到提交无法覆盖新一轮结果；已成功或
        已失效的任务不再执行。
        """

        timestamp = (now or datetime.now(UTC)).isoformat()
        with self._database.transaction():
            row = self._database.scoped(account_id).execute(
                "SELECT status, generation FROM study_diagnosis_tasks"
                " WHERE account_id = ? AND task_id = ?",
                (account_id, task_id),
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status not in {
                StudyDiagnosisStatus.PENDING.value,
                StudyDiagnosisStatus.RUNNING.value,
            }:
                return None
            generation = int(row["generation"]) + 1
            cursor = self._database.scoped(account_id).execute(
                "UPDATE study_diagnosis_tasks SET status = 'running',"
                " generation = ?, attempt = attempt + 1, updated_at = ?"
                " WHERE account_id = ? AND task_id = ?"
                " AND status IN ('pending', 'running')",
                (generation, timestamp, account_id, task_id),
            )
            if cursor.rowcount == 0:
                return None
        return generation

    def mark_pending_after_failure(
        self, account_id: str, task_id: str, *, now: datetime | None = None
    ) -> None:
        """可重试失败：回到待处理等待下一次自动领取，不显示为终态失败。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        self._database.scoped(account_id).execute(
            "UPDATE study_diagnosis_tasks SET status = 'pending',"
            " failure_reason = '', updated_at = ?"
            " WHERE account_id = ? AND task_id = ? AND status = 'running'",
            (timestamp, account_id, task_id),
        )

    def mark_failed(
        self,
        account_id: str,
        task_id: str,
        reason: str,
        *,
        completed: bool = True,
        now: datetime | None = None,
    ) -> None:
        """记录真实失败状态；失败保留原档案，不判学生错。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        self._database.scoped(account_id).execute(
            "UPDATE study_diagnosis_tasks SET status = 'failed',"
            " failure_reason = ?, completed_at = ?, updated_at = ?"
            " WHERE account_id = ? AND task_id = ?"
            " AND status IN ('pending', 'running')",
            (
                reason[:300],
                timestamp if completed else None,
                timestamp,
                account_id,
                task_id,
            ),
        )

    def mark_invalidated(
        self,
        account_id: str,
        task_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """来源失效或输入不再有效：不生成诊断，也不覆盖既有档案。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        self._database.scoped(account_id).execute(
            "UPDATE study_diagnosis_tasks SET status = 'invalidated',"
            " failure_reason = ?, completed_at = ?, updated_at = ?"
            " WHERE account_id = ? AND task_id = ?"
            " AND status IN ('pending', 'running')",
            (reason[:300], timestamp, timestamp, account_id, task_id),
        )

    def reset_for_retry(
        self, account_id: str, task_id: str, *, now: datetime | None = None
    ) -> bool:
        """手动重试：仅失败任务可回到待处理，等待执行器再次领取。"""

        timestamp = (now or datetime.now(UTC)).isoformat()
        cursor = self._database.scoped(account_id).execute(
            "UPDATE study_diagnosis_tasks SET status = 'pending',"
            " failure_reason = '', completed_at = NULL, updated_at = ?"
            " WHERE account_id = ? AND task_id = ? AND status = 'failed'",
            (timestamp, account_id, task_id),
        )
        return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # 结果提交与档案归并（在单个事务内完成）
    # ------------------------------------------------------------------

    def commit_result(
        self,
        task: StudyDiagnosisTaskRecord,
        generation: int,
        result: StudyDiagnosisResult,
        *,
        evidence_at: datetime,
        now: datetime | None = None,
    ) -> bool:
        """提交诊断结果并归并档案；返回是否写入（迟到/重复提交为 False）。"""

        timestamp = now or datetime.now(UTC)
        with self._database.transaction():
            result_version = task.result_version + 1
            result_id = f"dr-{secrets.token_urlsafe(16)}"
            cursor = self._database.scoped(task.account_id).execute(
                "UPDATE study_diagnosis_tasks SET status = 'succeeded',"
                " result_id = ?, result_version = ?, failure_reason = '',"
                " completed_at = ?, updated_at = ?"
                " WHERE account_id = ? AND task_id = ?"
                " AND status = 'running' AND generation = ?",
                (
                    result_id,
                    result_version,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    task.account_id,
                    task.task_id,
                    generation,
                ),
            )
            if cursor.rowcount == 0:
                return False
            record_id = self._apply_record(
                task,
                result,
                result_id=result_id,
                evidence_at=evidence_at,
                now=timestamp,
            )
            self._database.scoped(task.account_id).execute(
                "INSERT INTO study_diagnosis_results"
                " (result_id, task_id, account_id, conversation_id, evidence_id,"
                " record_id, protocol_version, input_version, result_version,"
                " verdict, subject, knowledge_title, usage_context, performance,"
                " supporting_json, refuting_json, uncertainty, next_validation,"
                " created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    task.task_id,
                    task.account_id,
                    task.conversation_id,
                    task.evidence_id,
                    record_id,
                    task.protocol_version,
                    task.input_version,
                    result_version,
                    result.verdict.value,
                    result.subject,
                    result.title,
                    result.usage_context,
                    result.performance,
                    json.dumps(result.supporting, ensure_ascii=False),
                    json.dumps(result.refuting, ensure_ascii=False),
                    result.uncertainty,
                    result.next_validation,
                    timestamp.isoformat(),
                ),
            )
        return True

    def _apply_record(
        self,
        task: StudyDiagnosisTaskRecord,
        result: StudyDiagnosisResult,
        *,
        result_id: str,
        evidence_at: datetime,
        now: datetime,
    ) -> str:
        """按证据发生顺序归并知识判断，返回档案条目标识。"""

        key = knowledge_key(result.subject, result.title, result.usage_context)
        scoped = self._database.scoped(task.account_id)
        for _ in range(3):
            row = scoped.execute(
                "SELECT * FROM study_learning_records"
                " WHERE account_id = ? AND knowledge_key = ?",
                (task.account_id, key),
            ).fetchone()
            if row is None:
                record_id = f"lr-{secrets.token_urlsafe(16)}"
                scoped.execute(
                    "INSERT INTO study_learning_records"
                    " (record_id, account_id, knowledge_key, subject, title,"
                    " usage_context, verdict, performance, supporting_json,"
                    " refuting_json, uncertainty, next_validation,"
                    " pending_confirmation, latest_evidence_id, latest_result_id,"
                    " latest_evidence_at, evidence_ids_json, record_version,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        record_id,
                        task.account_id,
                        key,
                        result.subject,
                        result.title,
                        result.usage_context,
                        result.verdict.value,
                        result.performance,
                        json.dumps(result.supporting, ensure_ascii=False),
                        json.dumps(result.refuting, ensure_ascii=False),
                        result.uncertainty,
                        result.next_validation,
                        task.evidence_id,
                        result_id,
                        evidence_at.isoformat(),
                        json.dumps([task.evidence_id], ensure_ascii=False),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                return record_id
            record_id = str(row["record_id"])
            current_verdict = StudyDiagnosisVerdict(str(row["verdict"]))
            conflict = _verdict_conflict(current_verdict, result.verdict)
            newer = _parse_time(str(row["latest_evidence_at"])) < evidence_at
            if newer:
                values: dict[str, Any] = {
                    "verdict": result.verdict.value,
                    "performance": result.performance,
                    "supporting_json": json.dumps(
                        result.supporting, ensure_ascii=False
                    ),
                    "refuting_json": json.dumps(result.refuting, ensure_ascii=False),
                    "uncertainty": result.uncertainty,
                    "next_validation": result.next_validation,
                    "pending_confirmation": 1 if conflict else 0,
                    "latest_evidence_id": task.evidence_id,
                    "latest_result_id": result_id,
                    "latest_evidence_at": evidence_at.isoformat(),
                }
            else:
                values = {
                    "pending_confirmation": (
                        1
                        if conflict or int(row["pending_confirmation"]) == 1
                        else 0
                    )
                }
            evidence_ids = _json_str_list(str(row["evidence_ids_json"]))
            if task.evidence_id not in evidence_ids:
                evidence_ids.append(task.evidence_id)
            values["evidence_ids_json"] = json.dumps(
                evidence_ids[-_RECORD_EVIDENCE_LIMIT:], ensure_ascii=False
            )
            assignments = ", ".join(f"{column} = ?" for column in values)
            cursor = scoped.execute(
                "UPDATE study_learning_records SET "
                + assignments
                + ", record_version = record_version + 1, updated_at = ?"
                " WHERE account_id = ? AND record_id = ? AND record_version = ?",
                (
                    *values.values(),
                    now.isoformat(),
                    task.account_id,
                    record_id,
                    int(row["record_version"]),
                ),
            )
            if cursor.rowcount == 1:
                return record_id
        raise StorageError("学习档案版本冲突：诊断结果未覆盖更新证据，请重试。")

    # ------------------------------------------------------------------
    # 用户可见投影
    # ------------------------------------------------------------------

    def record_projections(
        self,
        account_id: str,
        conversation_id: str,
        *,
        limit: int = _RECORD_QUERY_LIMIT,
    ) -> list[StudyLearningRecordProjection]:
        """返回与本对话核验证据相关的档案条目（无证据的悬空条目不显示）。"""

        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            "SELECT r.* FROM study_learning_records AS r"
            " WHERE r.account_id = ? AND EXISTS ("
            "   SELECT 1 FROM study_diagnosis_results AS d"
            "   WHERE d.account_id = r.account_id AND d.record_id = r.record_id"
            "   AND d.conversation_id = ?)"
            " ORDER BY r.updated_at DESC, r.rowid DESC LIMIT ?",
            (account_id, conversation_id, limit),
        ).fetchall()
        projections: list[StudyLearningRecordProjection] = []
        for row in rows:
            evidences = self._record_evidences(account_id, str(row["record_id"]))
            if not evidences:
                # 来源已失效（证据被删除）：不显示悬空档案条目。
                continue
            projections.append(
                StudyLearningRecordProjection(
                    record_id=str(row["record_id"]),
                    subject=str(row["subject"]),
                    title=str(row["title"]),
                    usage_context=str(row["usage_context"]),
                    verdict=StudyDiagnosisVerdict(str(row["verdict"])),
                    performance=str(row["performance"]),
                    supporting=_json_str_list(str(row["supporting_json"])),
                    refuting=_json_str_list(str(row["refuting_json"])),
                    uncertainty=str(row["uncertainty"]),
                    next_validation=str(row["next_validation"]),
                    pending_confirmation=bool(int(row["pending_confirmation"])),
                    evidence_count=len(evidences),
                    evidences=evidences,
                    created_at=_parse_time(str(row["created_at"])),
                    updated_at=_parse_time(str(row["updated_at"])),
                )
            )
        return projections

    def _record_evidences(
        self, account_id: str, record_id: str
    ) -> list[StudyRecordEvidenceProjection]:
        rows = self._database.scoped(account_id).execute(
            "SELECT e.evidence_id, e.question, e.knowledge_context, e.assistance,"
            " e.answer_message_id, e.feedback_message_id, e.created_at,"
            " d.verdict FROM study_evidence AS e"
            " JOIN study_diagnosis_results AS d"
            " ON d.account_id = e.account_id AND d.evidence_id = e.evidence_id"
            " WHERE e.account_id = ? AND d.record_id = ?"
            " ORDER BY e.created_at ASC, e.rowid ASC LIMIT ?",
            (account_id, record_id, _RECORD_EVIDENCE_LIMIT),
        ).fetchall()
        evidences: list[StudyRecordEvidenceProjection] = []
        for row in rows:
            try:
                assistance = StudyEvidenceAssistance(str(row["assistance"]))
                verdict = StudyDiagnosisVerdict(str(row["verdict"]))
            except ValueError:
                continue
            evidences.append(
                StudyRecordEvidenceProjection(
                    evidence_id=str(row["evidence_id"]),
                    question=str(row["question"]),
                    knowledge_context=str(row["knowledge_context"]),
                    assistance=assistance,
                    verdict=verdict,
                    answer_message_id=row["answer_message_id"],
                    feedback_message_id=row["feedback_message_id"],
                    occurred_at=_parse_time(str(row["created_at"])),
                )
            )
        return evidences


def _task_record(row: Any) -> StudyDiagnosisTaskRecord | None:
    try:
        basis = StudyEvaluationBasis.model_validate(json.loads(row["basis_json"]))
        return StudyDiagnosisTaskRecord(
            task_id=str(row["task_id"]),
            account_id=str(row["account_id"]),
            conversation_id=str(row["conversation_id"]),
            evidence_id=str(row["evidence_id"]),
            answer_message_id=str(row["answer_message_id"] or ""),
            question=str(row["question"]),
            knowledge_context=str(row["knowledge_context"]),
            input_version=str(row["input_version"]),
            protocol_version=str(row["protocol_version"]),
            basis=basis,
            status=StudyDiagnosisStatus(str(row["status"])),
            attempt=int(row["attempt"]),
            generation=int(row["generation"]),
            failure_reason=str(row["failure_reason"]),
            result_id=(
                str(row["result_id"]) if row["result_id"] is not None else None
            ),
            result_version=int(row["result_version"]),
            created_at=_parse_time(str(row["created_at"])),
            updated_at=_parse_time(str(row["updated_at"])),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _verdict_conflict(a: StudyDiagnosisVerdict, b: StudyDiagnosisVerdict) -> bool:
    """方向冲突：一方支持理解、另一方指认具体误解。"""

    return (a in _SUPPORT_VERDICTS and b in _GAP_VERDICTS) or (
        a in _GAP_VERDICTS and b in _SUPPORT_VERDICTS
    )


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def _json_str_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
