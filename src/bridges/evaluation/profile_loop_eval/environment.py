"""工单 18：评测环境装配（真实模型 / 脚本替身）与账户注册。

- ``real``：``development`` 环境 + 操作系统凭据库 + 生产模型网关（真实
  Qwen）；不注册任何替身，缺凭据时如实失败。
- ``scripted``：``test`` 环境 + 生产入口接线，聊天与抽取替换为确定性替身，
  召回索引换用确定性向量端口；只用于冒烟与故障注入，结果不得冒充真实模型。

评测使用隔离的临时数据目录与独立数据库；不接触正式数据库，不记录任何
凭据正文。代理仅在 ``real`` 模式下经 HTTP(S)_PROXY 交给底层客户端。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridges.evaluation.profile_loop_eval.types import MODE_REAL, MODE_SCRIPTED

_CREDENTIAL_ENV_KEYS = (
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_TAVILY_API_KEY_FILE",
)


@dataclass(frozen=True, slots=True)
class LoopAccount:
    """一个评测账户：稳定账户 ID 与登录会话令牌。"""

    account_id: str
    session_token: str
    tag: str


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"


class LoopEnvironment:
    """评测应用装配与生命周期（含重启恢复）。"""

    def __init__(
        self,
        *,
        repo_root: Path,
        run_dir: Path,
        mode: str,
        proxy: str = "",
    ) -> None:
        self.repo_root = repo_root
        self.run_dir = run_dir
        self.mode = mode
        self.proxy = proxy
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._accounts = 0
        self._app: Any = None
        self._client: Any = None
        self._stack: Any = None
        self.splitter: Any = None
        self.scripted_chat_calls: list[dict[str, Any]] = []
        self._document_worker: Any = None
        self._prepare_environment()

    # ------------------------------------------------------------------
    # 装配与生命周期
    # ------------------------------------------------------------------

    def _prepare_environment(self) -> None:
        for key in list(os.environ):
            if key.upper() in _CREDENTIAL_ENV_KEYS:
                os.environ.pop(key, None)
        os.environ["BRIDGES_ENVIRONMENT"] = "test" if self.mode == MODE_SCRIPTED else "development"
        os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{self.database_path}"
        os.environ["BRIDGES_SECRET_KEY"] = f"issue18-{uuid.uuid4().hex}"
        os.environ["BRIDGES_GENERATION_EXECUTOR"] = "0"
        if self.mode == MODE_REAL and self.proxy:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ[name] = self.proxy

    @property
    def database_path(self) -> Path:
        return self.run_dir / "bridges.db"

    def start(self) -> None:
        """（重新）装配应用与测试客户端；重启恢复场景复用同一数据库。"""

        from contextlib import ExitStack

        from fastapi.testclient import TestClient

        if self._stack is not None:
            self._stack.close()
            self._stack = None
        from bridges.config import get_settings

        get_settings.cache_clear()
        app = self._build_app()
        if self.mode == MODE_SCRIPTED:
            self._install_scripted_adapters(app)
        stack = ExitStack()
        client = stack.enter_context(TestClient(app))
        self._app = app
        self._client = client
        self._stack = stack
        self._document_worker = None

    def _build_app(self) -> Any:
        from bridges.api.main import create_app

        if self.mode == MODE_SCRIPTED:
            return create_app()
        from bridges.credentials.store import OsCredentialStore

        store = OsCredentialStore(data_dir=_local_app_data(), namespace="runtime")
        return create_app(runtime_credential_store=store)

    def _install_scripted_adapters(self, app: Any) -> None:
        """脚本模式：确定性聊天回答 + 回声抽取 + 确定性向量端口。"""

        from bridges.ai.adapters import StreamChunk
        from bridges.evaluation.profile_journey_runner import (
            install_deterministic_profile_extractor,
        )
        from bridges.ingestion.embedding import DeterministicEmbeddingPort
        from bridges.profiles.recall import ProfileRecallIndex

        class _ScriptedChatAdapter:
            def __init__(self, calls: list[dict[str, Any]]) -> None:
                self._calls = calls
                self.answer = "脚本化回答（确定性替身，不是真实模型输出）。"

            def stream_call(self, capability: Any, run_context: Any, payload: Any) -> Any:
                self._calls.append(
                    {
                        "run_id": run_context.run_id,
                        "message_count": len(payload.get("messages") or []),
                    }
                )
                yield StreamChunk(kind="delta", delta=self.answer)
                yield StreamChunk(kind="done", usage={"prompt_tokens": 0, "completion_tokens": 0})

        app.state.model_gateway.register_adapter(
            "qwen_text_chat", "1", _ScriptedChatAdapter(self.scripted_chat_calls)
        )
        # 与工单 01 确定性模式一致的替换接缝：生产接线的网关抽取器 +
        # 回声提案替身，再经生产证据核验提交。
        install_deterministic_profile_extractor(app)
        atomic = getattr(app.state, "atomic_profile_service", None)
        database = getattr(app.state, "bridges_database", None)
        if atomic is not None and database is not None:
            atomic.set_recall_index(
                ProfileRecallIndex(database=database, embedding=DeterministicEmbeddingPort())
            )

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None
        self._app = None
        self._client = None

    def restart(self) -> None:
        """关闭并按同一数据库重新装配（进程重启恢复语义）。"""

        self.start()

    # ------------------------------------------------------------------
    # 访问面
    # ------------------------------------------------------------------

    @property
    def app(self) -> Any:
        assert self._app is not None, "环境尚未启动"
        return self._app

    @property
    def client(self) -> Any:
        assert self._client is not None, "环境尚未启动"
        return self._client

    def use_account(self, account: LoopAccount) -> None:
        from bridges.api.auth import SESSION_COOKIE_NAME

        # 账户切换先清理旧会话 Cookie：httpx 的 CookieJar 会按域累积同名
        # Cookie，直接 get() 会抛 CookieConflict。
        jar = self.client.cookies.jar
        for cookie in list(jar):
            if cookie.name == SESSION_COOKIE_NAME:
                jar.clear(cookie.domain, cookie.path, cookie.name)
        self.client.cookies.set(SESSION_COOKIE_NAME, account.session_token)

    def register_account(self, tag: str) -> LoopAccount:
        """经真实注册入口创建独立账户并返回会话。"""

        import hashlib

        from bridges.api.auth import SESSION_COOKIE_NAME

        digest = hashlib.sha1(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()
        self._accounts += 1
        response = self.client.post(
            "/auth/register",
            json={
                "username": f"i18_{digest[:12]}",
                "qq_email": f"138{int(digest[:8], 16) % 10**8:08d}@qq.com",
                "password": "Passw0rd123!",
            },
        )
        assert response.status_code == 201, response.text
        token = response.cookies.get(SESSION_COOKIE_NAME)
        assert token, "注册未返回会话 Cookie"
        account_id = str(self.app.state.identity_service.resolve_session(token).subject.account_id)
        account = LoopAccount(account_id=account_id, session_token=str(token), tag=tag)
        self.use_account(account)
        return account

    def process_ingestion(self) -> str:
        """驱动文档摄取后台任务（API 进程只入队，处理装配在此补齐）。

        ``bridges.api`` 的组合根不挂版本化索引（生产由后台执行器进程承担
        解析/分块/向量化）；评测环境按执行器同一接线补建 worker，复用
        应用已有的 Embedding 端口与统一运行锁 recorder。
        """

        worker = self._document_worker
        if worker is None:
            from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
            from bridges.ingestion.index import VersionedIndex
            from bridges.ingestion.ocr import QwenOcrPort
            from bridges.ingestion.service import IngestionService

            database = getattr(self.app.state, "bridges_database", None)
            embedding = getattr(self.app.state, "embedding_port", None)
            object_repository = getattr(self.app.state, "object_repository", None)
            if database is None or embedding is None or object_repository is None:
                return "worker: 摄取处理未启用（缺少数据库、向量端口或对象仓库）。"
            ocr = QwenOcrPort(
                gateway=self.app.state.model_gateway,
                recorder=SqliteModelRunLockRecorder(database),
            )
            worker = IngestionService(
                database=database,
                object_repository=object_repository,
                embedding=embedding,
                ocr=ocr,
                index=VersionedIndex(database, embedding),
            )
            self._document_worker = worker
        return str(worker.process_pending())

    def model_run_locks(self) -> list[dict[str, Any]]:
        """全部模型调用锁（用量与延迟核对的唯一持久来源）。"""

        import json

        rows = self.app.state.bridges_database.connection.execute(
            "SELECT lock_id, account_id, run_id, capability_name, capability_version,"
            " actual_model_id, status, retry_count, error_code, usage, parameters,"
            " created_at FROM model_run_locks ORDER BY created_at"
        ).fetchall()
        locks: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for raw_key in ("usage", "parameters"):
                value = item.pop(raw_key, None)
                parsed: Any = None
                if value:
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        parsed = {"unparsable": True}
                item[raw_key] = parsed
            locks.append(item)
        return locks

    def extraction_tasks_summary(self) -> dict[str, int]:
        rows = self.app.state.bridges_database.connection.execute(
            "SELECT status, COUNT(*) AS count FROM profile_extraction_tasks GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}


__all__ = ["LoopAccount", "LoopEnvironment"]
