"""工单 18：单账户会话驱动与证据采集。

复用工单 01 的 ``ProfileJourneyCollector`` 采集每轮回复、采用切片、提取
运行与快照；本模块补充：消息重放（迟到提案）、条目手改/删除、控制开关、
概览与事项读取、聊天附件上传与解析驱动、队列积压采样。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from bridges.evaluation.profile_journey_runner import (
    JOURNEY_TURN_TIMEOUT_SECONDS,
    ProfileJourneyCollector,
)
from bridges.evaluation.profile_loop_eval.environment import LoopAccount, LoopEnvironment

_IDLE_POLL_SECONDS = 0.2


class AccountSession:
    """一个评测账户的会话驱动。"""

    def __init__(self, environment: LoopEnvironment, account: LoopAccount) -> None:
        self.environment = environment
        self.account = account
        environment.use_account(account)
        self.collector = ProfileJourneyCollector(
            app=environment.app,
            client=environment.client,
            database=environment.app.state.bridges_database,
            account_id=account.account_id,
        )
        self.turns: list[dict[str, Any]] = []
        self.queue_depth_samples: list[int] = []
        self.turn_index = 0

    @property
    def account_id(self) -> str:
        return self.account.account_id

    @property
    def database(self) -> Any:
        return self.collector.database

    @property
    def app(self) -> Any:
        return self.collector.app

    # ------------------------------------------------------------------
    # 对话驱动
    # ------------------------------------------------------------------

    def _next_key(self, prefix: str) -> str:
        self.turn_index += 1
        return f"issue18-{prefix}-{self.account.tag}-{self.turn_index}"

    def pending_counts(self, conversation_id: str) -> tuple[int, int]:
        generation = int(
            self.database.connection.execute(
                "SELECT COUNT(*) AS count FROM generation_runs"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND status IN ('queued', 'running')",
                (self.account_id, conversation_id),
            ).fetchone()["count"]
        )
        extraction = int(
            self.database.connection.execute(
                "SELECT COUNT(*) AS count FROM profile_extraction_tasks"
                " WHERE account_id = ? AND status IN ('pending', 'running')",
                (self.account_id,),
            ).fetchone()["count"]
        )
        return generation, extraction

    def drive(
        self,
        conversation_id: str,
        *,
        ingestion: bool = False,
        timeout: float = JOURNEY_TURN_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """推进执行器与该会话生成/后台提取直到空闲（可选驱动解析队列）。"""

        executor = self.app.state.generation_executor
        started = time.monotonic()
        ticks = 0
        deadline = started + timeout
        while time.monotonic() < deadline:
            thread = getattr(self.app.state, "generation_executor_thread", None)
            thread_alive = thread is not None and thread.is_alive()
            if not thread_alive:
                executor.run_tick()
                ticks += 1
            if ingestion:
                self.environment.process_ingestion()
            generation, extraction = self.pending_counts(conversation_id)
            self.queue_depth_samples.append(extraction)
            if generation == 0 and extraction == 0:
                return {
                    "ticks": ticks,
                    "executor_thread": thread_alive,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "executor_summary": str(getattr(executor, "_last_summary", "") or ""),
                }
            time.sleep(_IDLE_POLL_SECONDS)
        raise TimeoutError(f"会话 {conversation_id} 在 {timeout:.0f}s 内未完成生成与后台提取。")

    def chat(
        self,
        conversation_id: str,
        content: str,
        *,
        ingestion: bool = False,
        timeout: float = JOURNEY_TURN_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """发送一轮消息并等待生成与提取空闲，返回该轮证据。

        真实模型偶发工具/生成失败（如 web_search_citation_invalid）时会
        按产品入口重试同一消息，避免把基础设施失败记成画像行为失败。
        """

        if self.pending_counts(conversation_id) != (0, 0):
            self.drive(conversation_id, ingestion=ingestion, timeout=timeout)
        sent = self.collector.send_turn(conversation_id, content, self._next_key("turn"))
        idle = self.drive(conversation_id, ingestion=ingestion, timeout=timeout)
        turn = self._capture_turn(conversation_id, sent, content, ingestion, timeout, idle)
        turn["conversation_id"] = conversation_id
        turn["adopted_items"] = _adopted_items(turn)
        self.turns.append(turn)
        return turn

    def _capture_turn(
        self,
        conversation_id: str,
        sent: dict[str, Any],
        content: str,
        ingestion: bool,
        timeout: float,
        idle: dict[str, Any],
    ) -> dict[str, Any]:
        """抓取一轮证据；生成失败时按产品重试入口重试（最多两次）。"""

        turn = self.collector.capture_turn(
            conversation_id, sent, user_content=content, expectation=""
        )
        attempts = 0
        while turn.get("assistant_status") == "error" and attempts < 2:
            attempts += 1
            response = self.environment.client.post(
                f"/chat/conversations/{conversation_id}/messages/{sent['assistant_message_id']}/retry",
                json={"idempotency_key": self._next_key("retry")},
            )
            assert response.status_code in (200, 201), response.text
            payload = response.json()
            sent = {
                "run_id": str(payload["run_id"]),
                "user_message_id": str(sent["user_message_id"]),
                "assistant_message_id": str(payload["assistant_message"]["message_id"]),
            }
            idle = self.drive(conversation_id, ingestion=ingestion, timeout=timeout)
            turn = self.collector.capture_turn(
                conversation_id, sent, user_content=content, expectation=""
            )
        turn["idle"] = idle
        turn["retry_attempts"] = attempts
        return turn

    def start_chat(self, content: str, *, ingestion: bool = False) -> tuple[str, dict[str, Any]]:
        """经首轮入口创建会话并返回（会话 ID，该轮证据）。"""

        conversation_id = self.collector.start_conversation(content, self._next_key("first"))
        idle = self.drive(conversation_id, ingestion=ingestion)
        detail = self.collector.conversation_detail(conversation_id)
        latest_user = [message for message in detail["messages"] if message["role"] == "user"]
        assistant = [message for message in detail["messages"] if message["role"] == "assistant"]
        sent = {
            "run_id": self._latest_run_id(conversation_id),
            "user_message_id": latest_user[-1]["message_id"],
            "assistant_message_id": assistant[-1]["message_id"] if assistant else "",
        }
        turn = self._capture_turn(
            conversation_id, sent, content, ingestion, JOURNEY_TURN_TIMEOUT_SECONDS, idle
        )
        turn["conversation_id"] = conversation_id
        turn["adopted_items"] = _adopted_items(turn)
        self.turns.append(turn)
        return conversation_id, turn

    def chat_in_new_conversation(
        self, content: str, *, ingestion: bool = False
    ) -> tuple[str, dict[str, Any]]:
        return self.start_chat(content, ingestion=ingestion)

    def _latest_run_id(self, conversation_id: str) -> str:
        rows = self.database.connection.execute(
            "SELECT run_id FROM generation_runs WHERE account_id = ?"
            " AND conversation_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.account_id, conversation_id),
        ).fetchall()
        return str(rows[0]["run_id"]) if rows else ""

    def insert_assistant_message(self, conversation_id: str, content: str) -> str:
        """向会话注入一条助手消息（场景准备：助手先提问或先给建议）。"""

        from datetime import UTC, datetime

        message_id = f"issue18-assistant-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        self.database.connection.execute(
            "INSERT INTO messages (message_id, conversation_id, account_id, role,"
            " attempt_number, status, content, created_at, updated_at)"
            " VALUES (?, ?, ?, 'assistant', 1, 'done', ?, ?, ?)",
            (message_id, conversation_id, self.account_id, content, now, now),
        )
        self.database.connection.commit()
        return message_id

    def reschedule_extraction(
        self, *, conversation_id: str, message_id: str, content: str
    ) -> dict[str, Any]:
        """重放一条历史的普通提取（迟到提案语义），并复用在线幂等键。"""

        service = self.app.state.automatic_profile_service
        run = service.schedule_message_extraction(
            self.account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            run_id=self._latest_run_id(conversation_id),
        )
        queued = False
        if run is not None:
            queued = service.ensure_task_queued(
                self.account_id,
                message_id=run.message_id,
                extractor_version=run.extractor_version,
                source_hash=run.source_hash,
            )
        return {
            "extraction_id": run.extraction_id if run is not None else None,
            "status": (
                str(run.status.value if hasattr(run.status, "value") else run.status)
                if run is not None
                else None
            ),
            "run_kind": (
                str(run.run_kind.value if hasattr(run.run_kind, "value") else run.run_kind)
                if run is not None
                else None
            ),
            "queued": queued,
        }

    # ------------------------------------------------------------------
    # 条目与管理 API
    # ------------------------------------------------------------------

    def profile_snapshot(self) -> dict[str, Any]:
        return self.collector.profile_snapshot()

    def items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.profile_snapshot()["items"]
        return items

    def active_items(self) -> list[dict[str, Any]]:
        return [item for item in self.items() if item.get("status") == "active"]

    def active_texts(self) -> list[str]:
        return [str(item.get("text") or "") for item in self.active_items()]

    def all_item_texts(self) -> list[str]:
        return [str(item.get("text") or "") for item in self.items()]

    def find_item(self, keyword: str, *, active_only: bool = True) -> dict[str, Any] | None:
        source = self.active_items() if active_only else self.items()
        for item in source:
            if keyword in str(item.get("text") or ""):
                return item
        return None

    def active_item_row(self, keyword: str) -> dict[str, Any] | None:
        """按关键词读取一条活动条目的原始持久字段（用途/时效等）。"""

        rows = self.database.connection.execute(
            "SELECT profile_item_id, text, status, usage_purpose, version,"
            " valid_from, valid_until, validity_anchor_at, validity_phrase,"
            " goal_state, created_at FROM profile_items"
            " WHERE account_id = ? AND status = 'active' AND text LIKE ?"
            " ORDER BY created_at DESC LIMIT 1",
            (self.account_id, f"%{keyword}%"),
        ).fetchall()
        return dict(rows[0]) if rows else None

    def patch_item(self, item_id: str, text: str, version: int) -> dict[str, Any]:
        response = self.environment.client.patch(
            f"/profiles/items/{item_id}", json={"text": text, "version": version}
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def delete_item(self, item_id: str, version: int) -> None:
        response = self.environment.client.request(
            "DELETE",
            f"/profiles/items/{item_id}",
            json={"version": version},
        )
        assert response.status_code == 204, response.text

    def item_evidence(self, item_id: str) -> dict[str, Any]:
        response = self.environment.client.get(f"/profiles/items/{item_id}/evidence")
        assert response.status_code == 200, response.text
        return dict(response.json())

    def controls(
        self,
        *,
        recording_enabled: bool | None = None,
        usage_enabled: bool | None = None,
    ) -> dict[str, Any]:
        if recording_enabled is None and usage_enabled is None:
            response = self.environment.client.get("/profiles/controls")
            assert response.status_code == 200, response.text
            return dict(response.json())
        body: dict[str, Any] = {}
        if recording_enabled is not None:
            body["recording_enabled"] = recording_enabled
        if usage_enabled is not None:
            body["usage_enabled"] = usage_enabled
        response = self.environment.client.put("/profiles/controls", json=body)
        assert response.status_code == 200, response.text
        return dict(response.json())

    def overview(self) -> dict[str, Any]:
        response = self.environment.client.get("/profiles/overview")
        assert response.status_code == 200, response.text
        return dict(response.json())

    def ongoing(self) -> dict[str, Any]:
        response = self.environment.client.get("/profiles/ongoing")
        assert response.status_code == 200, response.text
        return dict(response.json())

    # ------------------------------------------------------------------
    # 材料上传（聊天附件域）
    # ------------------------------------------------------------------

    def upload_attachment(
        self, filename: str, content: bytes, *, upload_id: str | None = None
    ) -> dict[str, Any]:
        from urllib.parse import quote

        response = self.environment.client.post(
            "/chat/attachment-drafts",
            content=content,
            headers={
                "X-Bridges-Filename": quote(filename, safe=""),
                **({"X-Bridges-Upload-Id": upload_id} if upload_id else {}),
            },
        )
        assert response.status_code in (200, 201), response.text
        return dict(response.json())

    def start_with_attachment(
        self,
        content: str,
        attachment_ids: list[str],
        *,
        timeout: float = JOURNEY_TURN_TIMEOUT_SECONDS,
    ) -> str:
        """首轮携带聊天附件创建会话并驱动解析。"""

        response = self.environment.client.post(
            "/chat/first-turn",
            json={
                "content": content,
                "idempotency_key": self._next_key("first-attach"),
                "attachment_ids": attachment_ids,
            },
        )
        assert response.status_code in (200, 201), response.text
        conversation_id = str(response.json()["conversation"]["conversation_id"])
        idle = self.drive(conversation_id, ingestion=True, timeout=timeout)
        detail = self.collector.conversation_detail(conversation_id)
        latest_user = [message for message in detail["messages"] if message["role"] == "user"]
        assistant = [message for message in detail["messages"] if message["role"] == "assistant"]
        sent = {
            "run_id": self._latest_run_id(conversation_id),
            "user_message_id": latest_user[-1]["message_id"],
            "assistant_message_id": assistant[-1]["message_id"] if assistant else "",
        }
        turn = self._capture_turn(conversation_id, sent, content, True, timeout, idle)
        turn["conversation_id"] = conversation_id
        turn["adopted_items"] = _adopted_items(turn)
        self.turns.append(turn)
        return conversation_id

    def material_runs(self) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            "SELECT extraction_id, message_id, run_kind, status, outcome, attempts,"
            " last_error, created_at, updated_at FROM profile_extraction_runs"
            " WHERE account_id = ? AND run_kind = 'material' ORDER BY created_at",
            (self.account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def _adopted_items(turn: dict[str, Any]) -> list[str]:
    run = turn.get("generation_run") or {}
    slice_payload = run.get("adopted_profile_slice") or {}
    return [str(item.get("fact_text") or "") for item in slice_payload.get("adopted_items") or []]


__all__ = ["AccountSession"]
