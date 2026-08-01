"""Integration tests for the real Qwen adapters using cassette playback.

These tests exercise the full model gateway + real adapter stack without
requiring network access. The cassettes in ``tests/ai/cassettes`` are synthetic
and safe to commit.

默认以确定性本地替身播放 cassette：凭据与录制开关全部固定，不读取
``get_settings()``，因此仓库 ``.env`` 或用户环境中的真实 Qwen 凭据不会让
本测试发出真实网络请求，也不会覆写提交的 cassette 资产。

真实重录必须显式启用：在测试进程外同时设置
``SCIENCE_COMPANION_QWEN_RECORD_CASSETTES=true`` 与真实
``SCIENCE_COMPANION_QWEN_API_KEY``（环境变量，不读取 ``.env``），本测试
即进入录制模式并覆写 ``tests/ai/cassettes`` 下已提交的资产。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from science_companion.ai import (
    CapabilityRegistry,
    CassetteStore,
    ModelGateway,
    QwenApiClient,
    QwenAsrAdapter,
    QwenStructuredOutputAdapter,
    QwenTextChatAdapter,
)
from science_companion.config import get_settings
from science_companion.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
)
from science_companion.contracts.workflows import RunContextEnvelope

# 提交的合成 cassette 资产，相对测试文件固定定位；不随运行时配置漂移。
_CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-cassette",
        account_id="account-1",
        project_id="project-1",
        workflow_name="generic_science_task",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _build_gateway() -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_structured_output",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.6-flash",
            input_schema_version="structured-messages-v1",
            output_schema_version="json-schema-v1",
        )
    )
    registry.register(
        CapabilityRecord(
            name="qwen_asr_short",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3-asr-flash",
            input_schema_version="audio-upload-v1",
            output_schema_version="transcript-v1",
        )
    )

    # 默认播放模式：api_key=None，缺 cassette 时直接失败，绝不发真实请求。
    # 显式真实录制（见模块 docstring）：RECORD=true 且环境变量给了真实
    # 密钥时才从运行时配置读取凭据，cassette 目录仍固定为本文件旁的资产。
    record = os.environ.get("SCIENCE_COMPANION_QWEN_RECORD_CASSETTES", "").strip().lower()
    explicit_recording = record in {"true", "1", "yes"} and bool(
        os.environ.get("SCIENCE_COMPANION_QWEN_API_KEY")
    )
    if explicit_recording:
        settings = get_settings()
        client = QwenApiClient(
            api_key=settings.qwen_api_key,
            workspace_id=settings.qwen_workspace_id,
            region=settings.qwen_region,
            cassette_store=CassetteStore(_CASSETTE_DIR),
            record_mode=True,
        )
    else:
        client = QwenApiClient(
            api_key=None,
            workspace_id=None,
            region="cn-beijing",
            cassette_store=CassetteStore(_CASSETTE_DIR),
            record_mode=False,
        )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", QwenTextChatAdapter(client))
    gateway.register_adapter(
        "qwen_structured_output", "1", QwenStructuredOutputAdapter(client)
    )
    gateway.register_adapter("qwen_asr_short", "1", QwenAsrAdapter(client))
    return gateway


def test_text_chat_with_cassette() -> None:
    gateway = _build_gateway()
    result = gateway.invoke(
        "qwen_text_chat",
        "1",
        _context(),
        payload={"node_id": "compile_context"},
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.7-plus"
    assert result.lock.region == "cn-beijing"
    assert result.output == {"content": "上下文已编译。"}


def test_structured_output_with_cassette() -> None:
    gateway = _build_gateway()
    result = gateway.invoke(
        "qwen_structured_output",
        "1",
        _context(),
        payload={"node_id": "produce_output"},
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3.6-flash"
    assert result.output == {"summary": "科学解释", "claims": []}


def test_asr_short_with_cassette() -> None:
    gateway = _build_gateway()
    result = gateway.invoke(
        "qwen_asr_short",
        "1",
        _context(),
        payload={
            "audio_base64": "SUQz",
            "mime_type": "audio/mpeg",
            "duration_seconds": 10.0,
            "language": "zh",
        },
    )

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.actual_model_id == "qwen3-asr-flash"
    assert result.lock.region == "cn-beijing"
    assert result.output == {"transcript": "欢迎收听科学讲座。", "language": "zh"}
