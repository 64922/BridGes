"""Issue 08/11 真实本地 API smoke（Test plan 3/5，默认跳过）。

显式启用（``BRIDGES_HUMANIZER_REAL_SMOKE=1`` 且配置
``BRIDGES_QWEN_API_KEY``）时，经真实本地 API（TestClient + 真实 Qwen
适配器）跑一个无风险改写与一个证据安全任务，验证流式过程、终态投影、
正文首屏一致性与刷新恢复读取。无凭据时跳过而非失败（与
``test_chat_policy_real_smoke`` 同一显式启用约定）。

Issue 11：真实任务结束后从 SQLite 统一锁仓库核对模型运行锁——每次真实
写作调用（首稿/修订）都有持久化锁，锁数与业务投影的 writing_call_count
一致、可关联账户/消息/业务 run/阶段/调用序号与固定模型 ID；重建数据库
实例（模拟进程重启）后锁仍可查询。测试代码不读取、不打印、不持久化
Key，只检查「已配置/未配置」。

真实模型输出不确定，断言只锁定契约层不变量：终态事件、投影存在、
delivered 时正文与投影一致、failed 时不把候选当最终稿。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.skills.humanizer.contract_compiler import (
    CompileRequest,
    compile_task_contract,
)
from bridges.skills.humanizer.intent import route_humanizer_message
from bridges.skills.humanizer.service import (
    HUMANIZER_STAGE_DRAFT,
    HUMANIZER_STAGE_REVISION,
)
from bridges.storage import BridgesDatabase

_REAL_SMOKE_ENV = "BRIDGES_HUMANIZER_REAL_SMOKE"

_CORPUS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "fixtures"
    / "rewrite_corpus.md"
).read_text(encoding="utf-8")
_SOURCE = _CORPUS.split("## 原文")[1].split("## 事实锁清单")[0].strip()

pytestmark = pytest.mark.skipif(
    os.environ.get(_REAL_SMOKE_ENV, "") != "1"
    or not os.environ.get("BRIDGES_QWEN_API_KEY", ""),
    reason="真实 humanizer smoke 需显式启用（BRIDGES_HUMANIZER_REAL_SMOKE=1）"
    "并配置 BRIDGES_QWEN_API_KEY。",
)


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 人味化编排）。"""
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _verify_run_locks(
    app: Any,
    *,
    account_id: str,
    message_id: str,
    run_id: str,
    writing_call_count: int,
) -> list[Any]:
    """按业务 run 核对持久化锁：数量、顺序、阶段、序号与固定模型 ID。

    真实模型可能触发或不触发修订，因此只锁定契约不变量：锁数等于业务
    投影的写作调用数（每个真实调用恰一条锁）；首条锁必为首稿
    （humanizer_draft:1）；第二条（若存在）必为修订（humanizer_revision:2）。
    """
    database = app.state.bridges_database
    assert database is not None
    recorder = SqliteModelRunLockRecorder(database)
    locks = recorder.list_locks_by_run(account_id, run_id)
    assert len(locks) == writing_call_count, (
        f"锁数 {len(locks)} 与写作调用数 {writing_call_count} 不一致"
        "（灰度告警 humanizer_missing_run_lock）。"
    )
    assert locks, "真实调用必须至少有一条锁。"
    first = locks[0]
    assert first.account_id == account_id
    assert first.run_id == run_id
    assert first.capability_name == "qwen_structured_output"
    assert first.actual_model_id == CHAT_MODEL_ID, "锁必须记录固定批准模型 ID。"
    message_refs = [ref for ref in first.business_refs if ref.object_type == "message"]
    assert message_refs and message_refs[0].object_id == message_id
    assert first.business_refs[0].operation == HUMANIZER_STAGE_DRAFT
    assert first.business_refs[0].attempt_ordinal == 1
    if len(locks) == 2:
        second = locks[1]
        assert second.lock_id != first.lock_id
        assert second.business_refs[0].operation == HUMANIZER_STAGE_REVISION
        assert second.business_refs[0].attempt_ordinal == 2
    return locks


def _restart_database(database: Any) -> BridgesDatabase:
    """模拟进程重启：全新 BridgesDatabase 实例读取同一 SQLite 文件。"""
    reopened = BridgesDatabase(database.path)
    reopened.initialize()
    return reopened


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"real_smoke_{tag}",
            "qq_email": f"88886666{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def test_real_clean_rewrite_delivers_text_first(
    client: TestClient,
    sqlite_app: object,
    generation_helpers: dict[str, object],
) -> None:
    """真实本地 API 无风险改写：终态投影 delivered，正文优先且一致。"""
    account = _register(client, "1")
    account_id = str(account["id"])
    conversation_id = client.post("/chat/conversations", json={}).json()[
        "conversation_id"
    ]
    routed = route_humanizer_message(f"请把下面这段文字改得更自然：\n{_SOURCE}")
    assert routed is not None
    payload = routed.skill_input.model_dump(mode="json")
    created = generation_helpers["send"](
        client, conversation_id, skill=payload
    )  # type: ignore[index]
    generation_helpers["drive"](sqlite_app)  # type: ignore[index]
    events = generation_helpers["subscribe"](  # type: ignore[index]
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    terminal = events[-1][1]  # type: ignore[index]
    # 流式过程：过程事件（humanizer/started）与终态事件都在事件序列中
    assert any(kind in ("started", "humanizer") for kind, _ in events), [
        kind for kind, _ in events
    ]
    # 终态事件：result 投影挂到历史读取（刷新恢复即重新 GET 历史），正文与投影一致
    assert terminal["kind"] == "done", terminal
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message
        for message in history["messages"]
        if message["role"] == "assistant"
        and message.get("humanizer") is not None
    ][-1]
    projection = assistant["humanizer"]
    article = projection["article"]
    assert article is not None
    assert article["projection_version"] == "1"
    if article["delivery_status"] == "delivered":
        # 正文优先：消息正文即最终正文，首屏无固定「已完成人味化」前言
        assert article["final_text"] == assistant["content"]
        assert article["final_text"].strip()
    else:
        # 硬门失败：不把候选当最终稿，正文区域不交付
        assert assistant["content"] is None or article["final_text"] is None
    # Issue 11：真实调用锁——锁数与写作调用数一致、可关联账户/消息/run/
    # 阶段/序号/固定模型 ID；重启后仍可查
    assert projection["model_run_id"] is not None
    locks = _verify_run_locks(
        sqlite_app,
        account_id=account_id,
        message_id=created["assistant_message"]["message_id"],
        run_id=projection["model_run_id"],
        writing_call_count=int(projection["writing_call_count"] or 0),
    )
    assert projection["run_lock_id"] == locks[0].lock_id
    assert projection["run_lock_id"] in {lock.lock_id for lock in locks}
    # 模拟进程重启：新数据库实例读取同一文件，锁仍在
    reopened = _restart_database(sqlite_app.state.bridges_database)  # type: ignore[union-attr]
    restart_locks = SqliteModelRunLockRecorder(reopened).list_locks_by_run(
        account_id, projection["model_run_id"]
    )
    assert [lock.lock_id for lock in restart_locks] == [
        lock.lock_id for lock in locks
    ]


def test_real_evidence_safe_task_projection_shape(
    client: TestClient,
    sqlite_app: object,
    generation_helpers: dict[str, object],
) -> None:
    """真实证据安全任务：投影携带 evidence 字段，不伪装已修正。"""
    account = _register(client, "2")
    account_id = str(account["id"])
    conversation_id = client.post("/chat/conversations", json={}).json()[
        "conversation_id"
    ]
    risky_source = (
        "实验显示服用该提取物后受试者的血压显著下降，可以证明这种提取物"
        "具有降压作用；所有高血压患者都应服用它。"
    )
    compiled = compile_task_contract(
        CompileRequest(
            content="请把这段改写得更自然",
            source_material=risky_source,
            explicit_evidence_safe=True,
        )
    )
    payload = {
        "skill_id": "bridges-humanizer",
        "contract": {
            "path": "rewrite",
            "source_text": risky_source,
        },
        "expression_contract": compiled.contract.model_dump(mode="json"),
    }
    created = generation_helpers["send"](
        client, conversation_id, skill=payload
    )  # type: ignore[index]
    generation_helpers["drive"](sqlite_app)  # type: ignore[index]
    events = generation_helpers["subscribe"](  # type: ignore[index]
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    terminal = events[-1][1]  # type: ignore[index]
    assert terminal["kind"] in ("done", "error"), terminal
    history = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message
        for message in history["messages"]
        if message["role"] == "assistant"
        and message.get("humanizer") is not None
    ][-1]
    article = assistant["humanizer"]["article"]
    assert article is not None
    # 证据投影字段结构稳定（真实模型是否产生风险项不确定，但结构必须存在）
    assert "evidence" in article
    assert "revision" in article
    assert "confirmations" in article
    # Issue 11：证据安全任务的真实调用锁（首稿 1 条；触发证据安全修订则
    # 恰 2 条，第二条为修订阶段）——重启后仍可查，数量与业务计数一致
    projection = assistant["humanizer"]
    assert projection["model_run_id"] is not None
    writing_call_count = int(projection["writing_call_count"] or 0)
    assert 1 <= writing_call_count <= 2
    locks = _verify_run_locks(
        sqlite_app,
        account_id=account_id,
        message_id=created["assistant_message"]["message_id"],
        run_id=projection["model_run_id"],
        writing_call_count=writing_call_count,
    )
    reopened = _restart_database(sqlite_app.state.bridges_database)  # type: ignore[union-attr]
    restart_locks = SqliteModelRunLockRecorder(reopened).list_locks_by_run(
        account_id, projection["model_run_id"]
    )
    assert [lock.lock_id for lock in restart_locks] == [
        lock.lock_id for lock in locks
    ]
