"""Issue 06：学习档案的用户纠正、删除与迟到结果保护。

职责边界：

- 纠正与删除是账户级学习档案的用户操作：纠正保留用户主动来源、被取代
  结论与操作时的记录版本；删除写入撤回标记，停止该判断及关联旧证据在
  学情归档/个性化中的采用，原聊天历史保持可读，不自动删除整段对话。
- 诊断提交守卫（``diagnosis_repository.commit_result``）在迟到结果落库前
  校验纠正、删除与来源有效性；同一旧证据不能被任何入口复活。
- 本模块提供后续消费者唯一的有效档案读取边界（``effective_records`` /
  ``record_boundary``）：返回当前可用版本与失效原因，已删除记录不进入
  正式采用。
- 只读投影只含用户可见字段，不暴露私有评价依据；来源已删除或不可读时
  如实标注，不伪造引文。

用户自评与已核验表现严格区分：纠正是用户主动来源（未经独立核验），
绝不包装成独立完成或长期掌握；纠正后的记录必须经新的实际作答与核验
才能重新形成已核验判断，且与用户纠正并存而非互相覆盖。
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.study_diagnosis import (
    StudyDiagnosisVerdict,
    StudyLearningRecordProjection,
    StudyRecordBoundaryEntry,
    StudyRecordEvidenceProjection,
    StudyRecordState,
)
from bridges.contracts.study_verification import StudyEvidenceAssistance
from bridges.storage.database import BridgesDatabase

#: 每个档案条目保留的已归并证据上限（有界，避免无限档案行）。
_RECORD_EVIDENCE_LIMIT = 20
#: 单个对话投影返回的档案条目上限。
_RECORD_QUERY_LIMIT = 20
#: 有效读取边界返回的账户级档案条目上限。
_BOUNDARY_QUERY_LIMIT = 100

#: 有效读取边界的失效/不完全可用原因；完全可用时为空字符串。
RECORD_REASON_USER_CORRECTED = "user_corrected"
RECORD_REASON_USER_DELETED = "user_deleted"
RECORD_REASON_SOURCE_UNAVAILABLE = "source_unavailable"

#: 来源不可读的真实说明（不伪造引文）。
_MESSAGE_UNAVAILABLE_NOTE = "来源消息已不可读（可能随原对话删除）。"
_EVIDENCE_UNAVAILABLE_NOTE = "依据已不可读（可能随原对话删除）。"


class StudyRecordControlError(Exception):
    """档案用户操作的域错误（API 层映射为 404/409/422）。"""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class StudyRecordControlService:
    """档案纠正/删除与有效读取边界；账户作用域强制。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 用户操作：纠正与删除（按记录版本乐观并发）
    # ------------------------------------------------------------------

    def correct_record(
        self,
        account_id: str,
        conversation_id: str,
        record_id: str,
        note: str,
        expected_version: int,
    ) -> StudyLearningRecordProjection:
        """保存用户主动纠正；保留被取代结论与操作版本，不冒充已核验。"""

        cleaned = " ".join(str(note or "").split())[:500]
        if not cleaned:
            raise StudyRecordControlError(
                "record_note_required", "请输入纠正内容后再保存。", 422
            )
        now = datetime.now(UTC)
        with self._database.transaction():
            row = self._load_record(account_id, record_id)
            self._ensure_visible(account_id, conversation_id, record_id, row)
            assert row is not None  # 由 _ensure_visible 保证
            state = str(row["state"] or StudyRecordState.ACTIVE.value)
            if state == StudyRecordState.DELETED.value:
                raise StudyRecordControlError(
                    "record_deleted", "该学习记录已删除，无法再纠正。", 409
                )
            current_version = int(row["record_version"])
            if int(expected_version) != current_version:
                raise StudyRecordControlError(
                    "record_version_conflict",
                    "记录已在其他位置更新，请刷新后重试；当前输入已保留。",
                    409,
                )
            new_version = current_version + 1
            self._insert_control(
                account_id,
                record_id,
                kind="correction",
                user_note=cleaned,
                superseded_verdict=str(row["verdict"]),
                superseded_performance=str(row["performance"] or ""),
                superseded_record_version=current_version,
                record_version=new_version,
                now=now,
            )
            cursor = self._database.scoped(account_id).execute(
                "UPDATE study_learning_records SET state = 'corrected',"
                " user_note = ?, corrected_at = ?, record_version = ?,"
                " updated_at = ?"
                " WHERE account_id = ? AND record_id = ? AND record_version = ?"
                " AND state != 'deleted'",
                (
                    cleaned,
                    now.isoformat(),
                    new_version,
                    now.isoformat(),
                    account_id,
                    record_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudyRecordControlError(
                    "record_version_conflict",
                    "记录已在其他位置更新，请刷新后重试；当前输入已保留。",
                    409,
                )
        updated = self._load_record(account_id, record_id)
        assert updated is not None  # 刚在同一账户事务内更新
        return self._build_projection(account_id, updated)

    def delete_record(
        self,
        account_id: str,
        conversation_id: str,
        record_id: str,
        expected_version: int,
    ) -> None:
        """删除一条记录并写入撤回标记；旧证据不再被任何入口复活。"""

        now = datetime.now(UTC)
        with self._database.transaction():
            row = self._load_record(account_id, record_id)
            self._ensure_visible(account_id, conversation_id, record_id, row)
            assert row is not None  # 由 _ensure_visible 保证
            state = str(row["state"] or StudyRecordState.ACTIVE.value)
            if state == StudyRecordState.DELETED.value:
                raise StudyRecordControlError(
                    "record_deleted", "该学习记录已删除。", 409
                )
            current_version = int(row["record_version"])
            if int(expected_version) != current_version:
                raise StudyRecordControlError(
                    "record_version_conflict",
                    "记录已在其他位置更新，请刷新后确认再删除。",
                    409,
                )
            new_version = current_version + 1
            retracted = _json_str_list(str(row["retracted_evidence_ids_json"]))
            for evidence_id in _json_str_list(str(row["evidence_ids_json"])):
                if evidence_id not in retracted:
                    retracted.append(evidence_id)
            self._insert_control(
                account_id,
                record_id,
                kind="deletion",
                user_note="",
                superseded_verdict=str(row["verdict"]),
                superseded_performance=str(row["performance"] or ""),
                superseded_record_version=current_version,
                record_version=new_version,
                now=now,
            )
            cursor = self._database.scoped(account_id).execute(
                "UPDATE study_learning_records SET state = 'deleted',"
                " deleted_at = ?, retracted_evidence_ids_json = ?,"
                " record_version = ?, updated_at = ?"
                " WHERE account_id = ? AND record_id = ? AND record_version = ?"
                " AND state != 'deleted'",
                (
                    now.isoformat(),
                    json.dumps(retracted, ensure_ascii=False),
                    new_version,
                    now.isoformat(),
                    account_id,
                    record_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudyRecordControlError(
                    "record_version_conflict",
                    "记录已在其他位置更新，请刷新后确认再删除。",
                    409,
                )

    # ------------------------------------------------------------------
    # 有效档案读取边界（后续消费者的唯一入口）
    # ------------------------------------------------------------------

    def effective_records(
        self, account_id: str, *, limit: int = _BOUNDARY_QUERY_LIMIT
    ) -> list[StudyRecordBoundaryEntry]:
        """返回账户级档案的当前可用版本与失效原因（含已删除）。

        ``effective=False`` 的条目（用户删除）不进入正式采用；``verified``
        为 true 的条目才可当作已核验学情；用户纠正与来源不可读分别以
        ``reason`` 说明，消费者不得绕过本边界直接读取业务行。
        """

        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_learning_records"
            " WHERE account_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (account_id, max(1, int(limit))),
        ).fetchall()
        return [self._build_boundary(account_id, row) for row in rows]

    def record_boundary(
        self, account_id: str, record_id: str
    ) -> StudyRecordBoundaryEntry | None:
        """单条记录的有效性复查（供采用快照在调用前复验）。"""

        row = self._load_record(account_id, record_id)
        if row is None:
            return None
        return self._build_boundary(account_id, row)

    # ------------------------------------------------------------------
    # 用户可见投影（会话详情）
    # ------------------------------------------------------------------

    def record_projections(
        self,
        account_id: str,
        conversation_id: str,
        *,
        limit: int = _RECORD_QUERY_LIMIT,
    ) -> list[StudyLearningRecordProjection]:
        """返回与本对话核验证据相关的可见档案；已删除记录不显示。"""

        rows = self._database.scoped(account_id).execute(
            "SELECT r.* FROM study_learning_records AS r"
            " WHERE r.account_id = ? AND r.state != 'deleted' AND EXISTS ("
            "   SELECT 1 FROM study_diagnosis_results AS d"
            "   WHERE d.account_id = r.account_id AND d.record_id = r.record_id"
            "   AND d.conversation_id = ?)"
            " ORDER BY r.updated_at DESC, r.rowid DESC LIMIT ?",
            (account_id, conversation_id, limit),
        ).fetchall()
        return [self._build_projection(account_id, row) for row in rows]

    # ------------------------------------------------------------------
    # 内部读取
    # ------------------------------------------------------------------

    def _load_record(self, account_id: str, record_id: str) -> Any | None:
        return self._database.scoped(account_id).execute(
            "SELECT * FROM study_learning_records"
            " WHERE account_id = ? AND record_id = ?",
            (account_id, record_id),
        ).fetchone()

    def _ensure_visible(
        self,
        account_id: str,
        conversation_id: str,
        record_id: str,
        row: Any | None,
    ) -> None:
        """记录必须存在且与本对话核验证据相关；否则按不存在处理。"""

        if row is None:
            raise StudyRecordControlError(
                "record_not_found", "学习记录不存在或没有访问权限。", 404
            )
        linked = self._database.scoped(account_id).execute(
            "SELECT 1 AS present FROM study_diagnosis_results"
            " WHERE account_id = ? AND record_id = ? AND conversation_id = ?"
            " LIMIT 1",
            (account_id, record_id, conversation_id),
        ).fetchone()
        if linked is None:
            raise StudyRecordControlError(
                "record_not_found", "学习记录不存在或没有访问权限。", 404
            )

    def _insert_control(
        self,
        account_id: str,
        record_id: str,
        *,
        kind: str,
        user_note: str,
        superseded_verdict: str,
        superseded_performance: str,
        superseded_record_version: int,
        record_version: int,
        now: datetime,
    ) -> None:
        self._database.scoped(account_id).execute(
            "INSERT INTO study_record_controls"
            " (control_id, account_id, record_id, kind, user_note,"
            " superseded_verdict, superseded_performance,"
            " superseded_record_version, record_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"rc-{secrets.token_urlsafe(16)}",
                account_id,
                record_id,
                kind,
                user_note,
                superseded_verdict,
                superseded_performance,
                superseded_record_version,
                record_version,
                now.isoformat(),
            ),
        )

    # ------------------------------------------------------------------
    # 投影构建
    # ------------------------------------------------------------------

    def _build_projection(
        self, account_id: str, row: Any
    ) -> StudyLearningRecordProjection:
        evidences, source_available = self._evidence_projections(account_id, row)
        superseded_verdict: StudyDiagnosisVerdict | None = None
        superseded_performance = ""
        if str(row["state"] or "") == StudyRecordState.CORRECTED.value:
            latest = self._latest_correction(account_id, str(row["record_id"]))
            if latest is not None:
                try:
                    superseded_verdict = StudyDiagnosisVerdict(
                        str(latest["superseded_verdict"])
                    )
                except ValueError:
                    superseded_verdict = None
                superseded_performance = str(latest["superseded_performance"] or "")
        return StudyLearningRecordProjection(
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
            evidence_count=len(_json_str_list(str(row["evidence_ids_json"]))),
            evidences=evidences,
            created_at=_parse_time(str(row["created_at"])),
            updated_at=_parse_time(str(row["updated_at"])),
            record_version=int(row["record_version"]),
            state=StudyRecordState(str(row["state"])),
            user_note=str(row["user_note"] or ""),
            corrected_at=_parse_optional_time(row["corrected_at"]),
            superseded_verdict=superseded_verdict,
            superseded_performance=superseded_performance,
            source_available=source_available,
        )

    def _build_boundary(
        self, account_id: str, row: Any
    ) -> StudyRecordBoundaryEntry:
        state = StudyRecordState(str(row["state"]))
        _, source_available = self._evidence_projections(account_id, row)
        deleted = state == StudyRecordState.DELETED
        if deleted:
            reason = RECORD_REASON_USER_DELETED
        elif state == StudyRecordState.CORRECTED:
            reason = RECORD_REASON_USER_CORRECTED
        elif not source_available:
            reason = RECORD_REASON_SOURCE_UNAVAILABLE
        else:
            reason = ""
        return StudyRecordBoundaryEntry(
            record_id=str(row["record_id"]),
            record_version=int(row["record_version"]),
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
            state=state,
            effective=not deleted,
            verified=state == StudyRecordState.ACTIVE and source_available,
            reason=reason,
            user_note=str(row["user_note"] or ""),
            source_available=source_available,
            latest_evidence_at=_parse_optional_time(row["latest_evidence_at"]),
            updated_at=_parse_time(str(row["updated_at"])),
        )

    def _latest_correction(self, account_id: str, record_id: str) -> Any | None:
        return self._database.scoped(account_id).execute(
            "SELECT superseded_verdict, superseded_performance"
            " FROM study_record_controls"
            " WHERE account_id = ? AND record_id = ? AND kind = 'correction'"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (account_id, record_id),
        ).fetchone()

    def _evidence_projections(
        self, account_id: str, row: Any
    ) -> tuple[list[StudyRecordEvidenceProjection], bool]:
        """按证据发生顺序构建定位；来源不可读时如实标注。"""

        evidence_ids = _json_str_list(str(row["evidence_ids_json"]))[
            -_RECORD_EVIDENCE_LIMIT:
        ]
        if not evidence_ids:
            return [], False
        placeholders = ",".join("?" * len(evidence_ids))
        evidence_rows = self._database.scoped(account_id).execute(
            "SELECT evidence_id, question, knowledge_context, assistance,"
            " answer_message_id, feedback_message_id, created_at"
            f" FROM study_evidence WHERE account_id = ? AND evidence_id IN ({placeholders})",
            (account_id, *evidence_ids),
        ).fetchall()
        evidence_map = {str(item["evidence_id"]): item for item in evidence_rows}
        verdicts = {
            str(item["evidence_id"]): str(item["verdict"])
            for item in self._database.scoped(account_id).execute(
                "SELECT evidence_id, verdict FROM study_diagnosis_results"
                " WHERE account_id = ? AND record_id = ?",
                (account_id, str(row["record_id"])),
            ).fetchall()
        }
        message_ids: set[str] = set()
        for item in evidence_rows:
            for key in ("answer_message_id", "feedback_message_id"):
                value = item[key]
                if value:
                    message_ids.add(str(value))
        available_messages = self._available_messages(account_id, message_ids)

        projections: list[StudyRecordEvidenceProjection] = []
        for evidence_id in evidence_ids:
            item = evidence_map.get(evidence_id)
            if item is None:
                projections.append(
                    StudyRecordEvidenceProjection(
                        evidence_id=evidence_id,
                        verdict=_verdict_or_none(verdicts.get(evidence_id)),
                        occurred_at=None,
                        source_available=False,
                        source_note=_EVIDENCE_UNAVAILABLE_NOTE,
                    )
                )
                continue
            answer_message_id = (
                str(item["answer_message_id"]) if item["answer_message_id"] else None
            )
            feedback_message_id = (
                str(item["feedback_message_id"])
                if item["feedback_message_id"]
                else None
            )
            source_available = (
                answer_message_id in available_messages
                if answer_message_id
                else True
            ) and (
                feedback_message_id in available_messages
                if feedback_message_id
                else True
            )
            try:
                assistance = StudyEvidenceAssistance(str(item["assistance"]))
            except ValueError:
                assistance = StudyEvidenceAssistance.NONE
            projections.append(
                StudyRecordEvidenceProjection(
                    evidence_id=evidence_id,
                    question=str(item["question"]),
                    knowledge_context=str(item["knowledge_context"]),
                    assistance=assistance,
                    verdict=_verdict_or_none(verdicts.get(evidence_id)),
                    answer_message_id=answer_message_id,
                    feedback_message_id=feedback_message_id,
                    occurred_at=_parse_time(str(item["created_at"])),
                    source_available=source_available,
                    source_note="" if source_available else _MESSAGE_UNAVAILABLE_NOTE,
                )
            )
        projections.sort(
            key=lambda entry: (
                entry.occurred_at is None,
                entry.occurred_at or datetime.min.replace(tzinfo=UTC),
            )
        )
        source_available = all(
            entry.source_available for entry in projections
        ) and len(projections) == len(evidence_ids)
        return projections, source_available

    def _available_messages(self, account_id: str, message_ids: set[str]) -> set[str]:
        if not message_ids:
            return set()
        ordered = sorted(message_ids)
        placeholders = ",".join("?" * len(ordered))
        rows = self._database.scoped(account_id).execute(
            "SELECT message_id FROM messages"
            f" WHERE account_id = ? AND message_id IN ({placeholders})",
            (account_id, *ordered),
        ).fetchall()
        return {str(item["message_id"]) for item in rows}


def _verdict_or_none(value: str | None) -> StudyDiagnosisVerdict | None:
    if not value:
        return None
    try:
        return StudyDiagnosisVerdict(str(value))
    except ValueError:
        return None


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


def _parse_optional_time(value: Any) -> datetime | None:
    if value is None or str(value) == "":
        return None
    return _parse_time(str(value))
