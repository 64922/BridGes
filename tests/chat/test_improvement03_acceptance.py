"""工单 03 验收补充：真实接线、闭锁、升级幂等与账户导出。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.model_quota import RunModelQuota, build_run_model_quota, resolve_run_quota
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.chat.service import ChatDomainError, ChatService
from bridges.contracts.ai import BusinessRef, ModelCallStatus, ModelRunLock
from bridges.contracts.chat import ChatMessageRole
from bridges.storage.database import BridgesDatabase
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register
from tests.chat.test_improvement03_model_quota import (
    _MAX_INPUT_A,
    _MODEL_A,
    _MODEL_B,
    _WINDOW_A,
    _activate,
    _chat_registry,
    _ProgrammableAdapter,
    _record,
    _snapshot,
)
from tests.chat.test_issue02_durable_generation import client as client
from tests.chat.test_issue02_durable_generation import sqlite_app as sqlite_app


@pytest.mark.parametrize("locked_model", [None, _MODEL_A])
def test_retry_preserves_unreadable_quota(locked_model: str | None) -> None:
    service = ChatService.__new__(ChatService)
    service._model_config_provider = SimpleNamespace(
        snapshot=lambda: _snapshot(
            _MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A
        )
    )
    previous: dict[str, Any] = {"model_quota": {"quota_version": "model-quota-v99"}}
    if locked_model is not None:
        previous["run_model_id"] = locked_model
    config: dict[str, Any] = {}
    service._apply_run_model_lock(config, previous_config=previous)
    assert config["model_quota"] == previous["model_quota"]
    assert not resolve_run_quota(
        config, current_snapshot=service._model_config_provider.snapshot()
    ).resolved


def test_legacy_compat_is_saved_before_gateway_and_survives_reopen(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _register(client)
    adapter = _ProgrammableAdapter()
    provider = sqlite_app.state.run_model_config_provider
    service = sqlite_app.state.chat_service
    gateway = ModelGateway(_chat_registry(), model_config_provider=provider)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service._gateway = gateway
    _activate(sqlite_app, _MODEL_A, window=_WINDOW_A, max_input=_MAX_INPUT_A)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="你好")
    repo = service._repo
    run = repo.get_generation_run(account["id"], created["run_id"])
    config = dict(run.config)
    config.pop("model_quota")
    repo.update_generation_config(account["id"], run.run_id, config)
    compile_context = service.compile_turn_context

    def compile_then_switch(*args: Any, **kwargs: Any) -> Any:
        result = compile_context(*args, **kwargs)
        _activate(sqlite_app, _MODEL_B, window=200_000, max_input=199_000)
        return result

    monkeypatch.setattr(service, "compile_turn_context", compile_then_switch)
    seen_limits: list[int | None] = []
    stream_call = adapter.stream_call

    def capture(capability: Any, *args: Any, **kwargs: Any) -> Any:
        seen_limits.append(capability.max_input_tokens)
        yield from stream_call(capability, *args, **kwargs)

    monkeypatch.setattr(adapter, "stream_call", capture)
    generation_helpers["drive"](sqlite_app)
    assert adapter.models == [_MODEL_A]
    assert seen_limits == [_MAX_INPUT_A]
    database = BridgesDatabase(repo._db.path)
    database.initialize()
    from bridges.chat.repository import ConversationRepository

    try:
        recovered = ConversationRepository(database).get_generation_run(account["id"], run.run_id)
        quota = recovered.config["model_quota"]
        assert quota["verification_basis"] == "runtime_config_snapshot"
        assert quota["model_id"] == _MODEL_A
        assert quota["max_input_tokens"] == _MAX_INPUT_A
    finally:
        database.close()
    # 新进程重新装配仓库与出厂配置，仍从持久运行读取旧自定义额度。
    child_code = """
import json, sys
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.storage.database import BridgesDatabase
db = BridgesDatabase(sys.argv[1])
db.initialize()
repo = ConversationRepository(db)
run = repo.get_generation_run(sys.argv[2], sys.argv[3])
service = ChatService(repository=repo, gateway=ModelGateway(CapabilityRegistry()),
                      model_config_provider=RunModelConfigProvider())
_, record = service.compile_turn_context(run)
print(json.dumps(record))
db.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code, repo._db.path, account["id"], run.run_id],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    record = json.loads(result.stdout)
    assert record["model_id"] == _MODEL_A
    assert record["context_window"] == _MAX_INPUT_A


def test_photo_run_still_checks_unknown_quota() -> None:
    service = ChatService.__new__(ChatService)
    service._repo = SimpleNamespace(
        get_message=lambda *_: _record("u", ChatMessageRole.USER, "照片"),
        get_conversation=lambda *_: SimpleNamespace(mode="companion"),
    )
    service._attachments = SimpleNamespace(
        list_for_message=lambda *_: [SimpleNamespace(media_type="image/png")]
    )
    service._model_config_provider = None
    run = SimpleNamespace(
        account_id="alice",
        conversation_id="c",
        user_message_id="u",
        config={"run_model_id": _MODEL_A},
    )
    with pytest.raises(ChatDomainError, match="额度"):
        service.compile_turn_context(run)


def test_snapshot_without_override_selects_its_own_model_and_limit() -> None:
    gateway = ModelGateway(_chat_registry(), model_config_provider=RunModelConfigProvider())
    adapter = _ProgrammableAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    quota = build_run_model_quota(_snapshot(_MODEL_A, context_window=8000, max_input_tokens=None))
    capability = gateway._effective_capability(
        _chat_registry().get("qwen_text_chat", "1"), None, quota
    )
    assert capability.model_id == _MODEL_A
    assert capability.max_input_tokens == 8000


def test_quota_with_conflicting_locked_model_is_closed() -> None:
    quota = build_run_model_quota(
        _snapshot(_MODEL_A, context_window=_WINDOW_A, max_input_tokens=_MAX_INPUT_A)
    )
    assert not resolve_run_quota(
        {"run_model_id": _MODEL_B, "model_quota": quota.to_config()}, current_snapshot=None
    ).resolved


def test_legacy_lock_hash_remains_idempotent_after_upgrade(tmp_path: Path) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    recorder = SqliteModelRunLockRecorder(database)
    lock = ModelRunLock(
        lock_id="legacy-replay",
        run_id="r",
        account_id="alice",
        project_id="c",
        capability_name="qwen_text_chat",
        capability_version="1",
        actual_model_id=_MODEL_A,
        region="cn-beijing",
        parameters={},
        prompt_version="1",
        input_output_contract="chat:in->out",
        status=ModelCallStatus.SUCCESS,
        created_at=datetime.now(UTC),
    )
    ref = BusinessRef(object_type="message", object_id="m", operation="generate")
    recorder.record(lock, business_ref=ref)
    payload = lock.model_dump(exclude={"created_at", "call_contract"})
    legacy_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with database.transaction():
        database.connection.execute(
            "UPDATE model_run_locks SET canonical_hash = ? WHERE lock_id = ?",
            (legacy_hash, lock.lock_id),
        )
    recorder.record(lock, business_ref=ref)
    assert len(recorder.list_locks_by_run("alice", "r")) == 1


def test_official_export_includes_only_scoped_quota_metadata(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    account = _register(client)
    service = sqlite_app.state.chat_service
    service._gateway = _gateway_with(_ProgrammableAdapter())
    _activate(sqlite_app, _MODEL_A, window=_WINDOW_A, max_input=_MAX_INPUT_A)
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](client, conversation_id, content="你好")
    run = service.generation_run(account["id"], created["run_id"])
    config = dict(run.config)
    config["secret_test_canary"] = "私人配置不应整包导出"
    service._repo.update_generation_config(account["id"], run.run_id, config)
    from bridges.lifecycle.catalog import export_rows

    export = sqlite_app.state.export_service
    preview = {item.category: item.item_count for item in export.preview(account["id"]).categories}
    assert preview["generation_runs"] == 1
    _, document = export.export_data(account["id"])
    rows = json.loads(document)["categories"]["generation_runs"]["items"]
    assert rows[0]["model_quota"]["max_input_tokens"] == _MAX_INPUT_A
    assert "私人配置不应整包导出" not in json.dumps(rows, ensure_ascii=False)
    assert export_rows(service._repo._db, "other-account", "generation_runs") == []


def test_positive_window_without_verification_evidence_is_closed() -> None:
    quota = RunModelQuota(model_id=_MODEL_A, context_window=4096)
    assert not quota.is_verified
    assert not resolve_run_quota({"model_quota": quota.to_config()}, current_snapshot=None).resolved


def test_factory_quota_is_saved_without_provider() -> None:
    service = ChatService.__new__(ChatService)
    service._model_config_provider = None
    config: dict[str, Any] = {}
    service._apply_run_model_lock(config)
    assert config["model_quota"]["model_id"] == config["run_model_id"]
    assert config["model_quota"]["verification_basis"] == "factory_matrix"


def test_future_call_contract_reads_as_unknown(tmp_path: Path) -> None:
    from bridges.ai.sqlite_recorder import _load_call_contract

    assert (
        _load_call_contract(
            {"call_contract_json": json.dumps({"contract_version": "call-contract-v99"})}
        )
        is None
    )


def test_gateway_closes_unverified_quota_for_invoke_and_stream() -> None:
    gateway = ModelGateway(_chat_registry())
    adapter = _ProgrammableAdapter()
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    quota = RunModelQuota(model_id=_MODEL_A, context_window=4096)
    from tests.chat.test_improvement03_model_quota import _context

    result = gateway.invoke("qwen_text_chat", "1", _context(), model_quota=quota)
    assert result.status == ModelCallStatus.BLOCKED
    events = list(gateway.stream("qwen_text_chat", "1", _context(), model_quota=quota))
    assert events[0].error_code == "model_quota_unverified"
    assert adapter.models == []


def test_upgrade_from_v59_preserves_legacy_lock(tmp_path: Path) -> None:
    import sqlite3

    from bridges.storage.database import MIGRATIONS, SCHEMA_VERSION

    path = tmp_path / "upgrade.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for version in range(1, 60):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT INTO schema_meta VALUES ('version', '59')")
        connection.execute(
            "INSERT INTO model_run_locks(lock_id, account_id, capability_name, capability_version,"
            " actual_model_id, region, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old",
                "alice",
                "qwen_text_chat",
                "1",
                _MODEL_A,
                "cn-beijing",
                "success",
                datetime.now(UTC).isoformat(),
            ),
        )
    database = BridgesDatabase(path)
    try:
        assert database.initialize() == SCHEMA_VERSION
        assert database.migration_backup_path is not None
        lock = SqliteModelRunLockRecorder(database).get_lock("old", "alice")
        assert lock is not None and lock.call_contract is None
        assert lock.actual_model_id == _MODEL_A
    finally:
        database.close()
