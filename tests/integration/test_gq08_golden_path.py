"""GQ-08 黄金路径：全新数据目录 + 零账户密钥配置的全功能编排。

单一纵向回归验证整条产品承诺（US-01/02/05）：新账户在不存在任何账户
百炼密钥、密钥元数据或探测记录时，可依次走通 注册 → 聊天 → 知识库
向量化/检索 → 听写 → 朗读 → 图片 → 视频 全部已登记 Qwen/Wan 能力。

test 环境由全局确定性适配器驱动（GQ-01：唯一放行机制），不依赖真实
Key 或真实网络；服务级成功路径由各能力自有 harness（可编程适配器 +
固定快照）另行锁定，这里只编排"同一数据目录、同一账户、零 Key 配置"
的整链路。
"""

from __future__ import annotations

import io
import json
import struct
import urllib.parse
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.ai import CapabilityRecord
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService

ASR_MODEL = "qwen3-asr-flash"
TTS_MODEL = "qwen3-tts-flash-2025-11-27"
IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"
VIDEO_MODEL = "wan2.7-t2v-2026-06-12"
_AUDIO_URL = "http://tts.local/audio.wav"
_WAV_BYTES = b"\x52\x49\x46\x46" + b"\x00" * 40
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


def _make_probe_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<h", 0) * 16000)
    return buffer.getvalue()


def _register(client: TestClient, tag: str = "golden") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"golden_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        event_name = None
        data: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


class _ProgrammableAsrAdapter:
    """可编程 ASR 适配器：返回固定转写（成功路径）。"""

    def __init__(self, transcript: str) -> None:
        self._transcript = transcript

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"transcript": self._transcript},
        )


class _ProgrammableTtsAdapter:
    """可编程 TTS 适配器：返回供应商临时 URL（下载由 MockTransport 接管）。"""

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"audio_url": _AUDIO_URL, "mime_type": "audio/wav"},
        )


class _ScriptedTaskAdapter:
    """可编程异步任务适配器（图片/视频共用）：按脚本逐次消费调用。

    脚本元素：``{"output": {...}}``；耗尽后保持失败语义（与各能力
    harness 的脚本适配器同一协议）。
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        step = self.script.pop(0) if self.script else {"output": {}}
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=dict(step.get("output") or {}),
        )


def _image_success_script() -> list[dict[str, Any]]:
    """一轮完整图片任务：提交 → 运行中 → 完成 → 下载。"""
    return [
        {"output": {"cloud_task_id": "cloud-image-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": "http://image.local/1.png"}},
        {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
    ]


def _video_success_script() -> list[dict[str, Any]]:
    """一轮完整视频任务：提交 → 运行中 → 完成 → 下载。"""
    return [
        {"output": {"cloud_task_id": "cloud-video-1"}},
        {"output": {"cloud_status": "RUNNING"}},
        {"output": {"cloud_status": "SUCCEEDED", "result_url": "http://video.local/1.mp4"}},
        {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
    ]


def _drain_pending(service: Any, adapter: _ScriptedTaskAdapter) -> None:
    """连续执行 worker 处理轮直到不再推进（与各能力 harness 同语义）。"""
    for _ in range(60):
        before = len(adapter.calls)
        service.process_pending()
        if len(adapter.calls) == before:
            return


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """全新数据目录 + test 环境：与既有能力 API 测试同一组合根。"""
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def test_fresh_install_golden_path_all_capabilities_zero_key_config(
    client: TestClient, sqlite_app: Any
) -> None:
    """全新数据目录黄金路径：注册 → 聊天 → 知识库 → 听写 → 朗读 → 图片 → 视频。

    全程不创建任何账户百炼密钥、密钥元数据或探测记录；组合根也不持有
    账户 Qwen 凭据存储（GQ-07）。
    """
    account = _register(client, "01")
    assert account["id"]
    assert not hasattr(sqlite_app.state, "credential_store")

    gateway = sqlite_app.state.model_gateway
    conversation_id = _create_conversation(client)

    # ------------------------------------------------------------------
    # 1) 聊天：新账户直接收到确定性流式回答（GQ-02）
    # ------------------------------------------------------------------
    sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好，介绍一下你自己"},
    )
    assert sent.status_code == 200, sent.text
    events = _parse_sse(sent.text)
    done = next((data for name, data in events if name == "done"), None)
    assert done is not None and done["message"]["status"] == "done"
    assert done["message"]["content"]
    assistant_message_id = done["message"]["message_id"]

    # ------------------------------------------------------------------
    # 2) 知识库：上传 → 确定性向量化 → 聊天混合检索命中（GQ-05）
    # ------------------------------------------------------------------
    doc_text = "量子纠缠是量子力学中一种强关联现象。"
    uploaded = client.post(
        "/knowledge-base/materials",
        content=doc_text.encode("utf-8"),
        headers={
            "Content-Type": "text/plain",
            "X-Bridges-Filename": urllib.parse.quote("量子物理笔记.txt"),
            "X-Bridges-Upload-Id": "golden-kb-1",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    material_id = uploaded.json()["object_id"]

    # 与后台执行器同构的摄取 worker：同一数据库 + 确定性 Embedding 端口
    # + 版本化索引（GQ-05：API 与 worker 共享同一全局 Embedding 合同）。
    embedding_port = DeterministicEmbeddingPort()
    db = sqlite_app.state.bridges_database
    objects = sqlite_app.state.object_repository
    worker = IngestionService(
        database=db,
        object_repository=objects,
        embedding=embedding_port,
        index=VersionedIndex(db, embedding_port),
    )
    worker.process_pending()
    detail = client.get(f"/knowledge-base/materials/{material_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["vector_indexed"] is True

    # 检索：聊天发送携带 use_knowledge_base（默认开），引用应命中该材料。
    # 查询词取材料原文短语（既有 trigram FTS 按连续短语匹配，整句查询
    # 不作为本纵向切片边界）。
    searched = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "量子纠缠", "use_knowledge_base": True},
    )
    assert searched.status_code == 200, searched.text
    search_events = _parse_sse(searched.text)
    search_done = next(
        (data for name, data in search_events if name == "done"), None
    )
    assert search_done is not None
    citations = (search_done["message"].get("retrieval") or {}).get("citations") or []
    assert any(c["object_id"] == material_id for c in citations)

    # ------------------------------------------------------------------
    # 3) 听写 ASR：替换全局网关的固定适配器后提交 → 成功转写（GQ-03）
    # ------------------------------------------------------------------
    asr = _ProgrammableAsrAdapter(transcript="黄金路径听写转写文本。")
    gateway.register_adapter("qwen_asr_short", "1", asr)
    dictation = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=_make_probe_wav(),
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert dictation.status_code == 200, dictation.text
    assert dictation.json()["status"] == "success"
    assert dictation.json()["transcript"] == "黄金路径听写转写文本。"

    # ------------------------------------------------------------------
    # 4) 朗读 TTS：替换全局网关的固定适配器 → ready + 音频对象（GQ-03）
    # ------------------------------------------------------------------
    gateway.register_adapter("qwen_tts", "1", _ProgrammableTtsAdapter())
    speech_service = sqlite_app.state.speech_service
    speech_service._download_client = httpx.Client(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=_WAV_BYTES)
        )
    )
    read_aloud = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant_message_id}/read-aloud"
    )
    assert read_aloud.status_code == 200, read_aloud.text
    assert read_aloud.json()["state"] == "ready"
    audio = client.get(
        f"/chat/conversations/{conversation_id}/messages/{assistant_message_id}/read-aloud/audio"
    )
    assert audio.status_code == 200
    assert audio.content == _WAV_BYTES

    # ------------------------------------------------------------------
    # 5) 图片生成：聊天提交 → worker 收敛 → 资产与字节就绪（GQ-04）
    # ------------------------------------------------------------------
    image_adapter = _ScriptedTaskAdapter(_image_success_script())
    gateway.register_adapter("qwen_image", "1", image_adapter)
    image_sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一张桥梁插图", "image": {"kind": "generate", "prompt": "一座桥梁"}},
    )
    assert image_sent.status_code == 200, image_sent.text
    image_events = _parse_sse(image_sent.text)
    image_event = next((data for name, data in image_events if name == "image"), None)
    assert image_event is not None
    image_task_id = image_event["task"]["task_id"]
    assert image_event["task"]["status"] == "queued"

    image_service = sqlite_app.state.image_service
    _drain_pending(image_service, image_adapter)
    task = image_service.get_task(
        account["id"], conversation_id, image_task_id
    )
    assert task.status == "succeeded"
    assert task.asset_id
    image_bytes, image_media_type, _ = image_service.get_version_image_bytes(
        account["id"], conversation_id, task.asset_id, task.result_version_id
    )
    assert image_media_type == "image/png"
    assert image_bytes == _IMAGE_BYTES

    # ------------------------------------------------------------------
    # 6) 视频生成：聊天提交 → worker 收敛 → 资产与字节就绪（GQ-04）
    # ------------------------------------------------------------------
    video_adapter = _ScriptedTaskAdapter(_video_success_script())
    gateway.register_adapter("qwen_wan", "1", video_adapter)
    video_sent = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一段海浪视频", "video": {"prompt": "海浪拍岸"}},
    )
    assert video_sent.status_code == 200, video_sent.text
    video_events = _parse_sse(video_sent.text)
    video_event = next((data for name, data in video_events if name == "video"), None)
    assert video_event is not None
    video_task_id = video_event["task"]["task_id"]
    assert video_event["task"]["status"] == "queued"

    video_service = sqlite_app.state.video_service
    _drain_pending(video_service, video_adapter)
    video_task = video_service.get_task(account["id"], conversation_id, video_task_id)
    assert video_task.status == "succeeded"
    assert video_task.asset_id
    video_bytes, video_media_type, _ = video_service.get_video_bytes(
        account["id"], conversation_id, video_task.asset_id
    )
    assert video_media_type == "video/mp4"
    assert video_bytes == _VIDEO_BYTES
