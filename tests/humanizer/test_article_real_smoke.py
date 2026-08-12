"""Issue 08 真实本地 API smoke（Test plan 3，默认跳过）。

显式启用（``BRIDGES_HUMANIZER_REAL_SMOKE=1`` 且配置
``BRIDGES_QWEN_API_KEY``）时，经真实本地 API（TestClient + 真实 Qwen
适配器）跑一个无风险改写与一个证据安全任务，验证流式过程、终态投影、
正文首屏一致性与刷新恢复读取。无凭据时跳过而非失败（与
``test_chat_policy_real_smoke`` 同一显式启用约定）。

真实模型输出不确定，断言只锁定契约层不变量：终态事件、投影存在、
delivered 时正文与投影一致、failed 时不把候选当最终稿。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bridges.skills.humanizer.contract_compiler import (
    CompileRequest,
    compile_task_contract,
)
from bridges.skills.humanizer.intent import route_humanizer_message

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


def _register(client: TestClient, tag: str) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": f"real_smoke_{tag}",
            "qq_email": f"88886666{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text


def _send_and_drive(
    client: TestClient,
    sqlite_app: object,
    generation_helpers: dict[str, object],
    conversation_id: str,
    payload: dict[str, object],
) -> tuple[list[tuple[str, dict[str, object]]], dict[str, object]]:
    created = generation_helpers["send"](
        client, conversation_id, skill=payload
    )  # type: ignore[index]
    generation_helpers["drive"](sqlite_app)  # type: ignore[index]
    events = generation_helpers["subscribe"](  # type: ignore[index]
        client, conversation_id, created["assistant_message"]["message_id"]
    )
    return events, events[-1][1]  # type: ignore[index]


def test_real_clean_rewrite_delivers_text_first(
    client: TestClient,
    sqlite_app: object,
    generation_helpers: dict[str, object],
) -> None:
    """真实本地 API 无风险改写：终态投影 delivered，正文优先且一致。"""
    _register(client, "1")
    conversation_id = client.post("/chat/conversations", json={}).json()[
        "conversation_id"
    ]
    routed = route_humanizer_message(f"请把下面这段文字改得更自然：\n{_SOURCE}")
    assert routed is not None
    payload = routed.skill_input.model_dump(mode="json")
    events, terminal = _send_and_drive(
        client, sqlite_app, generation_helpers, conversation_id, payload
    )
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


def test_real_evidence_safe_task_projection_shape(
    client: TestClient,
    sqlite_app: object,
    generation_helpers: dict[str, object],
) -> None:
    """真实证据安全任务：投影携带 evidence 字段，不伪装已修正。"""
    _register(client, "2")
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
    _, terminal = _send_and_drive(
        client, sqlite_app, generation_helpers, conversation_id, payload
    )
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
