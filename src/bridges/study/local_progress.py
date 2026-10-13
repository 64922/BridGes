"""学习对话的会话局部进展仓库。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import ValidationError

from bridges.chat.repository import ConversationRepository, GenerationRunRecord, MessageRecord
from bridges.contracts.study_local_progress import StudyLocalProgressProjection
from bridges.storage.database import BridgesDatabase

_TOPIC_LIMIT = 160
_FOCUS_LIMIT = 320
_KNOWLEDGE_CONTEXT_LIMIT = 1600
_SOURCE_MESSAGE_LIMIT = 32


class StudyLocalProgressRepository:
    """按账户和会话读取进展，并在终态事务内保存成功轮次。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def get(
        self, account_id: str, conversation_id: str
    ) -> StudyLocalProgressProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT topic, knowledge_context, current_focus, source_message_ids_json,"
            " version, runtime_run_id, updated_at FROM study_local_progress"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return StudyLocalProgressProjection.model_validate(
                {
                    "topic": row["topic"],
                    "knowledge_context": row["knowledge_context"],
                    "current_focus": row["current_focus"],
                    "source_message_ids": json.loads(row["source_message_ids_json"]),
                    "version": row["version"],
                    "runtime_run_id": row["runtime_run_id"],
                    "updated_at": row["updated_at"],
                }
            )
        except (TypeError, ValueError, ValidationError):
            return None

    def save_completed_turn_in_transaction(
        self,
        repository: ConversationRepository,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        assistant_content: str,
        *,
        now: datetime | None = None,
    ) -> StudyLocalProgressProjection:
        """在消息从 streaming 转为 done 的同一事务内递增本地进展。"""

        current = self.get(run.account_id, run.conversation_id)
        conversation = repository.get_conversation(run.account_id, run.conversation_id)
        topic_source = (
            current.topic
            if current is not None
            else (conversation.title if conversation is not None else "")
            or user_message.content
            or "照片问题"
        )
        source_ids = list(current.source_message_ids) if current is not None else []
        for message_id in (user_message.message_id, run.assistant_message_id):
            if message_id not in source_ids:
                source_ids.append(message_id)
        source_ids = source_ids[-_SOURCE_MESSAGE_LIMIT:]
        updated = StudyLocalProgressProjection(
            topic=_short_text(topic_source, _TOPIC_LIMIT),
            knowledge_context=_short_text(assistant_content, _KNOWLEDGE_CONTEXT_LIMIT),
            current_focus=_short_text(user_message.content, _FOCUS_LIMIT),
            source_message_ids=source_ids,
            version=(current.version + 1) if current is not None else 1,
            runtime_run_id=run.run_id,
            updated_at=now or datetime.now(UTC),
        )
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_local_progress"
            " (account_id, conversation_id, topic, knowledge_context, current_focus,"
            " source_message_ids_json, version, runtime_run_id, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_id, conversation_id) DO UPDATE SET"
            " topic = excluded.topic, knowledge_context = excluded.knowledge_context,"
            " current_focus = excluded.current_focus,"
            " source_message_ids_json = excluded.source_message_ids_json,"
            " version = excluded.version, runtime_run_id = excluded.runtime_run_id,"
            " updated_at = excluded.updated_at",
            (
                run.account_id,
                run.conversation_id,
                updated.topic,
                updated.knowledge_context,
                updated.current_focus,
                json.dumps(updated.source_message_ids, ensure_ascii=False),
                updated.version,
                updated.runtime_run_id,
                updated.updated_at.isoformat(),
            ),
        )
        return updated


def _short_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]
