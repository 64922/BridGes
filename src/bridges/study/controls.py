"""Issue 04：用户学习控制、暂停与阶段小结的账户级仓库。

职责边界：

- 控制识别由导师规划（语义判断）完成并冻结在本轮运行快照中；本仓库在
  消息成功终态事务内按冻结快照落库，重试沿用同一快照，不重复记录。
- 跳过/直接讲解解除等待的验证写入由 Issue 03 的验证仓库完成；这里只
  记录控制本身与真实的作用问题、是否解除等待。
- 暂停保存可继续位置与暂停时的问题处置；旧控制的迟到重试只能作用于
  其绑定的暂停/问题，不影响之后生成的新状态。
- 小结保存规范原文、实际证据依据引用与生成时进展；失败/部分交付如实
  记录状态，重试只补写同一小结，不重判、不覆盖历史。
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from bridges.chat.repository import ConversationRepository, GenerationRunRecord, MessageRecord
from bridges.contracts.study_controls import (
    CONTROLS_CONTRACT_VERSION,
    StudyControlKind,
    StudyControlProjection,
    StudySessionPauseProjection,
    StudySessionSummaryProjection,
    StudySummaryStatus,
    StudySummaryTrigger,
)
from bridges.storage.database import BridgesDatabase
from bridges.study.session_summary import (
    semantics_verification,
    summary_display_status,
)

_QUESTION_LIMIT = 200
_POSITION_LIMIT = 320
_CONTENT_LIMIT = 6000
_RELEASING_KINDS = frozenset(
    {StudyControlKind.SKIP, StudyControlKind.FULL_EXPLANATION}
)


class StudyControlRepository:
    """学习控制、暂停与小结的读取与终态提交。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 读取（会话投影与详情）
    # ------------------------------------------------------------------

    def latest_control(
        self, account_id: str, conversation_id: str
    ) -> StudyControlProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT control_id, kind, bound_question, released, created_at"
            " FROM study_session_controls"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return StudyControlProjection(
                control_id=row["control_id"],
                kind=StudyControlKind(row["kind"]),
                question=row["bound_question"],
                released=bool(row["released"]),
                created_at=_parse_time(row["created_at"]),
            )
        except (TypeError, ValueError, ValidationError):
            return None

    def active_pause(
        self, account_id: str, conversation_id: str
    ) -> StudySessionPauseProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT pause_id, topic, position, pending_question, progress_version,"
            " source_message_id, paused_at FROM study_session_pauses"
            " WHERE account_id = ? AND conversation_id = ? AND resumed_at IS NULL"
            " ORDER BY rowid DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return StudySessionPauseProjection(
                pause_id=row["pause_id"],
                topic=row["topic"],
                position=row["position"],
                pending_question=row["pending_question"],
                progress_version=max(1, int(row["progress_version"])),
                source_message_id=row["source_message_id"],
                paused_at=_parse_time(row["paused_at"]),
            )
        except (TypeError, ValueError, ValidationError):
            return None

    def latest_summary(
        self, account_id: str, conversation_id: str
    ) -> StudySessionSummaryProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT summary_id, trigger, status, content, source_message_id,"
            " assistant_message_id, created_at FROM study_session_summaries"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return StudySessionSummaryProjection(
                summary_id=row["summary_id"],
                trigger=StudySummaryTrigger(row["trigger"]),
                status=StudySummaryStatus(row["status"]),
                content=row["content"],
                source_message_id=row["source_message_id"],
                assistant_message_id=row["assistant_message_id"],
                created_at=_parse_time(row["created_at"]),
            )
        except (TypeError, ValueError, ValidationError):
            return None

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
        """按冻结快照落库本轮控制、暂停与小结；缺快照时不做任何写入。"""

        latest = repository.get_generation_run(run.account_id, run.run_id)
        config = (latest.config if latest is not None else run.config) or {}
        snapshot = config.get("study_verification")
        if not isinstance(snapshot, dict):
            return
        control = snapshot.get("control")
        summary = snapshot.get("summary")
        if not isinstance(control, dict) and not isinstance(summary, dict):
            return
        timestamp = now or datetime.now(UTC)
        progress = self._progress_row(run.account_id, run.conversation_id)
        if isinstance(control, dict):
            self._apply_control(
                run, user_message, control, progress, timestamp
            )
        if isinstance(summary, dict):
            self._save_summary(
                run, user_message, assistant_message_id, content, summary, timestamp
            )

    # ------------------------------------------------------------------
    # 内部写入
    # ------------------------------------------------------------------

    def _apply_control(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        control: dict[str, Any],
        progress: dict[str, Any],
        now: datetime,
    ) -> None:
        try:
            kind = StudyControlKind(str(control.get("kind") or ""))
        except ValueError:
            return
        bound = control.get("bound_check")
        bound = bound if isinstance(bound, dict) else None
        released = self._released(kind, run, bound)
        progress_version = int(progress.get("version") or 0)
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_session_controls"
            " (control_id, account_id, conversation_id, run_id, source_message_id,"
            " kind, bound_check_id, bound_check_version, bound_question, released,"
            " progress_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_id, source_message_id, kind) DO NOTHING",
            (
                secrets.token_urlsafe(16),
                run.account_id,
                run.conversation_id,
                run.run_id,
                user_message.message_id,
                kind.value,
                str(bound.get("check_id") or "") if bound is not None else "",
                int(bound.get("version") or 0) if bound is not None else 0,
                _short(str(bound.get("question") or ""), _QUESTION_LIMIT)
                if bound is not None
                else "",
                1 if released else 0,
                progress_version,
                now.isoformat(),
            ),
        )
        if kind == StudyControlKind.PAUSE:
            self._save_pause(run, user_message, control, progress, now)
        elif kind == StudyControlKind.RESUME:
            self._resume_pause(run, control, now)

    def _released(
        self,
        kind: StudyControlKind,
        run: GenerationRunRecord,
        bound: dict[str, Any] | None,
    ) -> bool:
        """等待是否真的被解除：以终态事务中的当前待答问题为准。"""

        if kind not in _RELEASING_KINDS or bound is None:
            return False
        row = self._database.scoped(run.account_id).execute(
            "SELECT check_id FROM study_pending_checks"
            " WHERE account_id = ? AND conversation_id = ?",
            (run.account_id, run.conversation_id),
        ).fetchone()
        bound_id = str(bound.get("check_id") or "")
        return bound_id != "" and (row is None or row["check_id"] != bound_id)

    def _save_pause(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        control: dict[str, Any],
        progress: dict[str, Any],
        now: datetime,
    ) -> None:
        """保存暂停：可继续位置与未解除的当前问题如实留存。"""

        pending = self._database.scoped(run.account_id).execute(
            "SELECT check_id, question FROM study_pending_checks"
            " WHERE account_id = ? AND conversation_id = ?",
            (run.account_id, run.conversation_id),
        ).fetchone()
        topic = _short(str(progress.get("topic") or ""), 160)
        position = _short(
            str(progress.get("current_focus") or "") or topic, _POSITION_LIMIT
        )
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_session_pauses"
            " (pause_id, account_id, conversation_id, run_id, source_message_id,"
            " topic, position, pending_check_id, pending_question, progress_version,"
            " paused_at, resumed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)"
            " ON CONFLICT(account_id, source_message_id) DO NOTHING",
            (
                str(control.get("pause_id") or "") or secrets.token_urlsafe(16),
                run.account_id,
                run.conversation_id,
                run.run_id,
                user_message.message_id,
                topic,
                position,
                str(pending["check_id"]) if pending is not None else "",
                _short(str(pending["question"]), _QUESTION_LIMIT)
                if pending is not None
                else "",
                int(progress.get("version") or 0),
                now.isoformat(),
            ),
        )

    def _resume_pause(
        self, run: GenerationRunRecord, control: dict[str, Any], now: datetime
    ) -> None:
        """只恢复本控制绑定的暂停；旧控制迟到重试不解除之后的新暂停。"""

        pause_id = str(control.get("pause_id") or "")
        if not pause_id:
            return
        self._database.scoped(run.account_id).execute(
            "UPDATE study_session_pauses SET resumed_at = ?"
            " WHERE account_id = ? AND conversation_id = ? AND pause_id = ?"
            " AND resumed_at IS NULL",
            (now.isoformat(), run.account_id, run.conversation_id, pause_id),
        )

    def _save_summary(
        self,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        assistant_message_id: str,
        content: str,
        summary: dict[str, Any],
        now: datetime,
    ) -> None:
        try:
            trigger = StudySummaryTrigger(str(summary.get("trigger") or ""))
        except ValueError:
            return
        summary_content = str(summary.get("content") or "")[:_CONTENT_LIMIT]
        if not summary_content:
            return
        status = summary_display_status(summary_content, content)
        basis = self._rebuild_basis(run, summary)
        progress = self._progress_row(run.account_id, run.conversation_id)
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_session_summaries"
            " (summary_id, account_id, conversation_id, run_id, source_message_id,"
            " assistant_message_id, trigger, status, content, basis_json,"
            " progress_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_id, source_message_id) DO UPDATE SET"
            " assistant_message_id = excluded.assistant_message_id,"
            " run_id = excluded.run_id,"
            " status = CASE WHEN study_session_summaries.status = 'complete'"
            "   THEN 'complete' ELSE excluded.status END,"
            " updated_at = excluded.updated_at",
            (
                str(summary.get("summary_id") or "") or secrets.token_urlsafe(16),
                run.account_id,
                run.conversation_id,
                run.run_id,
                user_message.message_id,
                assistant_message_id,
                trigger.value,
                status,
                summary_content,
                json.dumps(basis, ensure_ascii=False),
                int(progress.get("version") or 0),
                now.isoformat(),
                now.isoformat(),
            ),
        )

    def _rebuild_basis(
        self, run: GenerationRunRecord, summary: dict[str, Any]
    ) -> dict[str, Any]:
        """依据引用按终态事务中的实际证据重建，保证只引用真实记录。"""

        basis = summary.get("basis")
        rebuilt = dict(basis) if isinstance(basis, dict) else {}
        rows = self._database.scoped(run.account_id).execute(
            "SELECT evidence_id, question, check_kind, semantics FROM study_evidence"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at ASC, rowid ASC",
            (run.account_id, run.conversation_id),
        ).fetchall()
        rebuilt["items"] = [
            {
                "evidence_id": row["evidence_id"],
                "question": str(row["question"]),
                "kind": str(row["check_kind"]),
                "semantics": str(row["semantics"]),
                "verification": semantics_verification(str(row["semantics"])),
            }
            for row in rows
        ]
        rebuilt["controls_contract_version"] = CONTROLS_CONTRACT_VERSION
        return rebuilt

    def _progress_row(self, account_id: str, conversation_id: str) -> dict[str, Any]:
        row = self._database.scoped(account_id).execute(
            "SELECT topic, current_focus, version FROM study_local_progress"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return {"version": 0, "topic": "", "current_focus": ""}
        return {
            "version": int(row["version"]),
            "topic": str(row["topic"]),
            "current_focus": str(row["current_focus"]),
        }


def _short(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
