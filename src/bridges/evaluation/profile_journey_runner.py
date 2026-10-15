"""工单 01：画像闭环基线的入口驱动与证据采集。

本模块是 ``scripts/run_issue01_profile_baseline.py`` 的实现层：

- 经真实聊天 API 路由（TestClient 驱动正式应用装配）发送固定语料，并
  采集每轮的助手回复、耗时、模型调用、画像提取运行、四维记录、原子
  条目与回答采用切片；
- ``deterministic`` 模式在 test 环境下把网关抽取器替换为真实生产接线
  （``GatewayAutomaticProfileExtractor`` + 真实邻居读取器）并注册录制
  替身适配器：模型响应是**替身**，只用于机制复现与邻近消息证据；
- ``real`` 模式使用生产接线与真实主模型，不注册任何替身；
- 真实响应与替身响应在运行锁中以 ``response_mode`` 分开标记。

评测比较、运行锁与文件写入见 ``profile_journey_evidence.py``。只记录
结构化事实与固定合成语料正文；不记录凭据、不导出内部提示词。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from bridges.ai.adapters import AdapterResult
from bridges.evaluation.profile_journeys import (
    BASELINE_JOURNEYS,
    BASELINE_JOURNEYS_BY_ID,
    BaselineJourney,
)

#: 单轮生成 + 后台提取的等待上限（秒）。
JOURNEY_TURN_TIMEOUT_SECONDS = 600.0

#: 空闲判定轮询间隔。
_IDLE_POLL_SECONDS = 0.2

#: 响应来源标记：替身（确定性）与真实模型分开记录。
RESPONSE_MODE_STUB = "stub"
RESPONSE_MODE_REAL = "real"

_PROFILE_EXTRACTION_CAPABILITY = ("qwen_profile_extraction", "1")
_NEIGHBOR_HINT_MARKER = "仅供回指的邻近用户消息"
_RELATED_MEMORY_MARKER = "已知的相关旧记忆"
_EVIDENCE_REF_IN_PROMPT = re.compile(r'evidence_ref 固定填写 "([^"]+)"')
_TRAILING_PUNCTUATION = "，。！？!?,.;；：:、 \t\r\n"


def _rows(database: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in database.connection.execute(sql, params).fetchall()]


def echo_profile_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    """确定性回声提案替身：把当前消息整体作为一条候选事实提出。

    只用于 deterministic 基线的机制复现：声称支持的原话就是整条消息，
    取值锚点为去尾标点后的消息正文。候选仍要经过生产证据核验与提交
    边界；替身不冒充真实模型语义，也不能用来得出质量结论。
    """

    messages = payload.get("messages") or []
    current = str(messages[-1].get("content") or "").strip() if messages else ""
    message_id = ""
    for message in messages:
        if str(message.get("role")) != "system":
            continue
        match = _EVIDENCE_REF_IN_PROMPT.search(str(message.get("content") or ""))
        if match is not None:
            message_id = match.group(1)
            break
    value = current.strip(_TRAILING_PUNCTUATION)
    if not message_id or not value:
        return {"items": []}
    return {
        "items": [
            {
                "normalized_value": value,
                "fact_text": value,
                "evidence_ref": message_id,
                "evidence_quote": current,
                "reliability": 0.99,
                "action": "create",
            }
        ]
    }


class RecordingProfileExtractionAdapter:
    """确定性替身：录制最终载荷并按响应策略返回合同输出。

    只用于 deterministic 模式在真实入口路径上复现「邻近消息/会话标识」与
    「证据核验」机制；``responder`` 生成的是替身响应，不是真实模型结果，
    不得用于质量结论。
    """

    def __init__(
        self,
        output: dict[str, Any] | None = None,
        *,
        responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = output or {"items": []}
        self._responder = responder

    def call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        messages = payload.get("messages") or []
        neighbor_hints = [
            str(message.get("content") or "")
            for message in messages
            if _NEIGHBOR_HINT_MARKER in str(message.get("content") or "")
        ]
        related_memory_hints = [
            str(message.get("content") or "")
            for message in messages
            if _RELATED_MEMORY_MARKER in str(message.get("content") or "")
        ]
        self.calls.append(
            {
                "run_id": run_context.run_id,
                "project_id": run_context.project_id,
                "message_count": len(messages),
                "neighbor_hint_present": bool(neighbor_hints),
                "neighbor_hint_content": neighbor_hints,
                "related_memory_hint_present": bool(related_memory_hints),
                "related_memory_hint_content": related_memory_hints,
                "current_message_content": (
                    str(messages[-1].get("content") or "") if messages else ""
                ),
            }
        )
        output = self._responder(payload) if self._responder is not None else self._output
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=output,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


def install_deterministic_profile_extractor(
    app: Any,
) -> RecordingProfileExtractionAdapter:
    """把 test 环境的规则抽取器替换为生产接线的网关抽取器 + 录制替身。

    邻居读取函数与生产装配完全相同（``_profile_neighbor_reader``）；网关
    抽取器的模型适配器是录制替身，默认响应策略是回声提案（工单 04 起：
    自然自述经语义分支提出候选，再由生产证据核验决定写入）。仅用于确定性
    基线，替身响应不冒充真实模型输出。
    """

    from bridges.api.app_support import _profile_neighbor_reader
    from bridges.profiles import GatewayAutomaticProfileExtractor

    adapter = RecordingProfileExtractionAdapter(responder=echo_profile_proposal)
    app.state.model_gateway.register_adapter(*_PROFILE_EXTRACTION_CAPABILITY, adapter)
    conversation_repository = getattr(app.state, "profile_conversation_repository", None)
    extractor = GatewayAutomaticProfileExtractor(
        app.state.model_gateway,
        model_config_provider=getattr(app.state, "run_model_config_provider", None),
        neighbor_reader=(
            _profile_neighbor_reader(conversation_repository)
            if conversation_repository is not None
            else None
        ),
    )
    # 与 tests/chat/test_improvement19_purpose_aware_slice.py 相同的替换方式：
    # 抽取器是服务的构造接缝，评估运行器在应用创建后替换它。
    app.state.automatic_profile_service._extractor = extractor  # noqa: SLF001
    return adapter


def probe_neighbor_reader(
    app: Any, account_id: str, conversation_id: str, message_id: str
) -> list[str] | None:
    """应用级探测：真实仓库 + 真实邻居读取函数对指定消息的返回。

    仅用于定位与交叉核对；入口证明来自录制替身收到的最终载荷。
    """

    from bridges.api.app_support import _profile_neighbor_reader

    repository = getattr(app.state, "profile_conversation_repository", None)
    if repository is None:
        return None
    reader = _profile_neighbor_reader(repository)
    return reader(account_id, conversation_id, message_id)


class ProfileJourneyCollector:
    """按账户/会话经真实入口采集画像基线证据。"""

    def __init__(
        self,
        *,
        app: Any,
        client: Any,
        database: Any,
        account_id: str,
    ) -> None:
        self._app = app
        self._client = client
        self._database = database
        self._account_id = account_id

    @property
    def app(self) -> Any:
        return self._app

    @property
    def database(self) -> Any:
        return self._database

    @property
    def account_id(self) -> str:
        return self._account_id

    # ------------------------------------------------------------------
    # 对话操作
    # ------------------------------------------------------------------

    def start_conversation(self, content: str, key: str) -> str:
        response = self._client.post(
            "/chat/first-turn",
            json={"content": content, "idempotency_key": key},
        )
        assert response.status_code in (200, 201), response.text
        return str(response.json()["conversation"]["conversation_id"])

    def send_turn(self, conversation_id: str, content: str, key: str) -> dict[str, Any]:
        response = self._client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": content, "idempotency_key": key},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return {
            "run_id": str(body.get("run_id") or ""),
            "user_message_id": body["user_message"]["message_id"],
            "assistant_message_id": body["assistant_message"]["message_id"],
        }

    def conversation_detail(self, conversation_id: str) -> dict[str, Any]:
        response = self._client.get(f"/chat/conversations/{conversation_id}")
        assert response.status_code == 200, response.text
        payload: dict[str, Any] = dict(response.json())
        return payload

    # ------------------------------------------------------------------
    # 运行推进
    # ------------------------------------------------------------------

    def _pending(self, conversation_id: str) -> tuple[int, int]:
        generation = int(
            self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM generation_runs"
                " WHERE account_id = ? AND conversation_id = ?"
                " AND status IN ('queued', 'running')",
                (self._account_id, conversation_id),
            ).fetchone()["count"]
        )
        extraction = int(
            self._database.connection.execute(
                "SELECT COUNT(*) AS count FROM profile_extraction_tasks"
                " WHERE account_id = ? AND status IN ('pending', 'running')",
                (self._account_id,),
            ).fetchone()["count"]
        )
        return generation, extraction

    def _executor_thread_alive(self) -> bool:
        thread = getattr(self._app.state, "generation_executor_thread", None)
        return thread is not None and thread.is_alive()

    def drive_until_idle(
        self, conversation_id: str, *, timeout: float = JOURNEY_TURN_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """推进执行器直到该会话生成与该账户后台提取都空闲。"""

        executor = self._app.state.generation_executor
        started = time.monotonic()
        ticks = 0
        deadline = started + timeout
        while time.monotonic() < deadline:
            if not self._executor_thread_alive():
                executor.run_tick()
                ticks += 1
            generation, extraction = self._pending(conversation_id)
            if generation == 0 and extraction == 0:
                return {
                    "ticks": ticks,
                    "executor_thread": self._executor_thread_alive(),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            time.sleep(_IDLE_POLL_SECONDS)
        raise TimeoutError(
            f"会话 {conversation_id} 在 {timeout:.0f}s 内未完成生成与后台提取。"
        )

    # ------------------------------------------------------------------
    # 证据采集
    # ------------------------------------------------------------------

    def _generation_run(self, run_id: str) -> dict[str, Any] | None:
        rows = _rows(
            self._database,
            "SELECT run_id, status, error_code, duration_ms, created_at, updated_at,"
            " config_json FROM generation_runs WHERE account_id = ? AND run_id = ?",
            (self._account_id, run_id),
        )
        if not rows:
            return None
        row = rows[0]
        config_raw = row.pop("config_json", None)
        adopted: dict[str, Any] | None = None
        if config_raw:
            try:
                config = json.loads(config_raw)
            except json.JSONDecodeError:
                config = {}
            adopted = config.get("adopted_profile_slice")
        row["adopted_profile_slice"] = adopted
        return row

    def _model_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = _rows(
            self._database,
            "SELECT capability_name, actual_model_id, status, retry_count,"
            " error_code, usage, created_at FROM model_run_locks"
            " WHERE account_id = ? AND run_id = ? ORDER BY created_at",
            (self._account_id, run_id),
        )
        calls: list[dict[str, Any]] = []
        for row in rows:
            usage = row.get("usage")
            try:
                usage_payload = json.loads(usage) if usage else None
            except json.JSONDecodeError:
                usage_payload = {"unparsable": True}
            calls.append(
                {
                    "capability": row["capability_name"],
                    "actual_model_id": row["actual_model_id"],
                    "status": row["status"],
                    "retry_count": row["retry_count"],
                    "error_code": row["error_code"],
                    "usage": usage_payload,
                }
            )
        return calls

    def extraction_runs(
        self, message_ids: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT extraction_id, message_id, extractor_version, source, status,"
            " outcome, attempts, observed_count, record_ids_json, last_error,"
            " created_at, updated_at FROM profile_extraction_runs"
            " WHERE account_id = ?"
        )
        params: tuple[Any, ...] = (self._account_id,)
        if message_ids:
            placeholders = ", ".join("?" for _ in message_ids)
            sql += f" AND message_id IN ({placeholders})"
            params += tuple(message_ids)
        rows = _rows(self._database, sql + " ORDER BY created_at", params)
        for row in rows:
            try:
                row["committed_record_ids"] = json.loads(
                    row.pop("record_ids_json") or "[]"
                )
            except json.JSONDecodeError:
                row["committed_record_ids"] = []
        return rows

    def capture_turn(
        self,
        conversation_id: str,
        sent: dict[str, Any],
        *,
        user_content: str,
        expectation: str,
    ) -> dict[str, Any]:
        detail = self.conversation_detail(conversation_id)
        assistant = next(
            (
                message
                for message in detail["messages"]
                if message["message_id"] == sent["assistant_message_id"]
            ),
            None,
        )
        run = self._generation_run(sent["run_id"]) if sent.get("run_id") else None
        return {
            "user_content": user_content,
            "expectation": expectation,
            "user_message_id": sent["user_message_id"],
            "assistant_message_id": sent["assistant_message_id"],
            "assistant_status": (assistant or {}).get("status"),
            "assistant_content": (assistant or {}).get("content") or "",
            "assistant_error_code": (assistant or {}).get("error_code"),
            "generation_run": run,
            "model_calls": (
                self._model_calls(sent["run_id"]) if sent.get("run_id") else []
            ),
            "extraction_runs": self.extraction_runs((sent["user_message_id"],)),
        }

    def profile_snapshot(self) -> dict[str, Any]:
        four_dimensions = _rows(
            self._database,
            "SELECT record_id, dimension, label, content, status, write_origin,"
            " version, updated_at FROM profile_four_dimension_records"
            " WHERE account_id = ? ORDER BY updated_at",
            (self._account_id,),
        )
        items = _rows(
            self._database,
            "SELECT profile_item_id, text, identity_key, status, write_origin,"
            " version, created_at, updated_at FROM profile_items"
            " WHERE account_id = ? ORDER BY created_at",
            (self._account_id,),
        )
        observations = _rows(
            self._database,
            "SELECT observation_id, message_id, dimension, normalized_value,"
            " reliability, created_at FROM profile_extraction_observations"
            " WHERE account_id = ? ORDER BY created_at",
            (self._account_id,),
        )
        tasks = _rows(
            self._database,
            "SELECT task_id, message_id, status, attempts, last_error"
            " FROM profile_extraction_tasks WHERE account_id = ? ORDER BY created_at",
            (self._account_id,),
        )
        return {
            "four_dimensions": four_dimensions,
            "items": items,
            "observations": observations,
            "tasks": tasks,
            "extraction_runs": self.extraction_runs(),
        }


def run_baseline_journey(
    collector: ProfileJourneyCollector,
    journey: BaselineJourney,
    *,
    tag: str,
) -> dict[str, Any]:
    """经真实入口重放一条旅程并采集证据。"""

    started = time.monotonic()
    turns: list[dict[str, Any]] = []
    conversation_id = ""
    for index, content in enumerate(journey.user_turns):
        key = f"issue01-{tag}-{journey.journey_id}-{index}"
        if index == 0:
            conversation_id = collector.start_conversation(content, key)
            detail = collector.conversation_detail(conversation_id)
            latest = [
                message for message in detail["messages"] if message["role"] == "user"
            ]
            sent = {
                "run_id": _latest_run_id(collector, conversation_id),
                "user_message_id": latest[-1]["message_id"],
                "assistant_message_id": _latest_assistant_id(detail),
            }
        else:
            sent = collector.send_turn(conversation_id, content, key)
        idle = collector.drive_until_idle(conversation_id)
        turn = collector.capture_turn(
            conversation_id,
            sent,
            user_content=content,
            expectation=journey.target,
        )
        turn["idle"] = idle
        turns.append(turn)
    last_user_message_id = turns[-1]["user_message_id"] if turns else ""
    probe = (
        probe_neighbor_reader(
            collector.app, collector.account_id, conversation_id, last_user_message_id
        )
        if last_user_message_id
        else None
    )
    return {
        "journey_id": journey.journey_id,
        "scenario_id": journey.scenario_id,
        "problem_class": journey.problem_class,
        "title": journey.title,
        "target": journey.target,
        "expected_baseline_status": journey.baseline_status,
        "expected_baseline_observation": journey.baseline_observation,
        "evidence_note": journey.evidence_note,
        "conversation_id": conversation_id,
        "turns": turns,
        "profile_snapshot": collector.profile_snapshot(),
        "neighbor_reader_probe": probe,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _latest_run_id(collector: ProfileJourneyCollector, conversation_id: str) -> str:
    rows = _rows(
        collector.database,
        "SELECT run_id FROM generation_runs WHERE account_id = ?"
        " AND conversation_id = ? ORDER BY created_at DESC LIMIT 1",
        (collector.account_id, conversation_id),
    )
    return str(rows[0]["run_id"]) if rows else ""


def _latest_assistant_id(detail: dict[str, Any]) -> str:
    for message in reversed(detail["messages"]):
        if message["role"] == "assistant":
            return str(message["message_id"])
    return ""


__all__ = [
    "JOURNEY_TURN_TIMEOUT_SECONDS",
    "RESPONSE_MODE_REAL",
    "RESPONSE_MODE_STUB",
    "RecordingProfileExtractionAdapter",
    "ProfileJourneyCollector",
    "install_deterministic_profile_extractor",
    "probe_neighbor_reader",
    "run_baseline_journey",
    "BASELINE_JOURNEYS",
    "BASELINE_JOURNEYS_BY_ID",
]
