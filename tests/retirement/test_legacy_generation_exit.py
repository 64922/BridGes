"""Issue 21：旧图片/视频生成、回答朗读与科学创作的退役契约。

ADR-0030 把图片生成、视频生成、回答朗读与旧科学文章创作路径列为退役
能力。本文件固定退役后的对外行为：

- 全部写入口（生成提交、取消、重试、替代文本、说明、删除、朗读生成与
  清理、科学来源与主张图写入）稳定返回 410 与稳定错误码，不落任何新行，
  也不触发任何供应商调用；
- 历史只读面保留：任务与资产投影、版本字节流、朗读投影仍按账户可查，
  跨账户一律 404 不泄漏存在性；
- 历史任务消息的消息级重试不再指向已退役的任务卡按钮，同样返回 410，
  历史链接不会误触发新执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult
from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)

IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"
VIDEO_MODEL = "wan2.7-t2v-2026-06-12"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
_RESULT_URL = "http://media.local/result"


def _capability(name: str, model_id: str, in_schema: str, out_schema: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen" if name != "qwen_wan" else "wan",
        region="cn-beijing",
        model_id=model_id,
        input_schema_version=in_schema,
        output_schema_version=out_schema,
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
    )


class _ProgrammableAdapter:
    """按脚本推进 submit/poll/fetch 的供应商替身；记录全部调用载荷。"""

    def __init__(self, model_id: str, script: list[dict[str, Any]]) -> None:
        self._model_id = model_id
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
        if step.get("error") is not None:
            raise step["error"]
        return AdapterResult(
            actual_model_id=self._model_id,
            output=dict(step.get("output") or {}),
        )


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天、图片、视频与语音服务）。"""
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"legacy_exit_{tag}",
            "qq_email": f"1357{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None
    subject = client.app.state.identity_service.resolve_session(session_token).subject
    return client.app.state.chat_service.create_conversation(
        subject.account_id
    ).conversation_id


def _send_plain_message(client: TestClient, conversation_id: str) -> str:
    """发一条普通消息，返回助手消息 id（历史任务要绑定到助手消息）。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "这是一轮普通对话"},
    )
    assert response.status_code == 200, response.text
    return response.json()["assistant_message"]["message_id"]


def _account_id(client: TestClient) -> str:
    session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None
    return client.app.state.identity_service.resolve_session(
        session_token
    ).subject.account_id


def _row_count(sqlite_app: Any, account_id: str, table: str) -> int:
    row = sqlite_app.state.bridges_database.scoped(account_id).execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?", (account_id,)
    ).fetchone()
    return int(row["count"])


def _image_adapter() -> _ProgrammableAdapter:
    return _ProgrammableAdapter(
        IMAGE_MODEL,
        [
            {"output": {"cloud_task_id": "cloud-1"}},
            {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
            {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
        ],
    )


def _video_adapter() -> _ProgrammableAdapter:
    return _ProgrammableAdapter(
        VIDEO_MODEL,
        [
            {"output": {"cloud_task_id": "cloud-1"}},
            {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
            {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
        ],
    )


def _install_media_adapters(sqlite_app: Any) -> tuple[Any, Any]:
    """给图片/视频服务装入可编程供应商替身，返回两个适配器。"""
    image_registry = CapabilityRegistry()
    image_registry.register(
        _capability("qwen_image", IMAGE_MODEL, "image-prompt-v1", "image-task-v1")
    )
    image_adapter = _image_adapter()
    image_gateway = ModelGateway(image_registry)
    image_gateway.register_adapter("qwen_image", "1", image_adapter)
    sqlite_app.state.image_service._gateway = image_gateway

    video_registry = CapabilityRegistry()
    video_registry.register(
        _capability("qwen_wan", VIDEO_MODEL, "video-prompt-v1", "video-task-v1")
    )
    video_adapter = _video_adapter()
    video_gateway = ModelGateway(video_registry)
    video_gateway.register_adapter("qwen_wan", "1", video_adapter)
    sqlite_app.state.video_service._gateway = video_gateway
    return image_adapter, video_adapter


def _assert_retired(response: Any, *, code: str, replacement: str) -> None:
    assert response.status_code == 410, response.text
    detail = response.json()["detail"]
    assert detail["error"] == code
    assert detail["replacement_path"] == replacement
    assert detail["service_version"]
    assert any("\u4e00" <= char <= "\u9fff" for char in detail["message"])


def test_generation_payloads_are_rejected_without_creating_anything(
    sqlite_app: Any, client: TestClient
) -> None:
    """聊天发送里的图片/视频载荷一律 410，不落消息、不建任务、不调供应商。"""
    account = _register(client)
    image_adapter, video_adapter = _install_media_adapters(sqlite_app)
    conversation_id = _create_conversation(client)
    messages_before = len(
        client.get(f"/chat/conversations/{conversation_id}").json()["messages"]
    )

    cases = (
        (
            "/chat/first-turn",
            {
                "content": "生成一张桥的素描",
                "image": {"kind": "generate", "prompt": "桥"},
                "idempotency_key": "legacy-image-first-turn",
            },
            "legacy_image_retired",
        ),
        (
            f"/chat/conversations/{conversation_id}/messages",
            {"content": "生成一张桥的素描", "image": {"kind": "generate", "prompt": "桥"}},
            "legacy_image_retired",
        ),
        (
            "/chat/first-turn",
            {
                "content": "生成一条河的视频",
                "video": {"prompt": "河"},
                "idempotency_key": "legacy-video-first-turn",
            },
            "legacy_video_retired",
        ),
        (
            f"/chat/conversations/{conversation_id}/messages",
            {"content": "生成一条河的视频", "video": {"prompt": "河"}},
            "legacy_video_retired",
        ),
    )
    for path, payload, code in cases:
        _assert_retired(client.post(path, json=payload), code=code, replacement="/")

    assert (
        len(client.get(f"/chat/conversations/{conversation_id}").json()["messages"])
        == messages_before
    )
    assert _row_count(sqlite_app, account["id"], "image_tasks") == 0
    assert _row_count(sqlite_app, account["id"], "video_tasks") == 0
    assert image_adapter.calls == []
    assert video_adapter.calls == []


def test_image_writes_are_retired_while_history_stays_readable(
    sqlite_app: Any, client: TestClient
) -> None:
    """旧图片任务只能看：投影/字节流只读可查，取消、重试、改文本、删除全 410。"""
    account = _register(client)
    _install_media_adapters(sqlite_app)
    conversation_id = _create_conversation(client)
    message_id = _send_plain_message(client, conversation_id)
    task = sqlite_app.state.image_service.submit_generation(
        account["id"], conversation_id, message_id, "一座桥的素描"
    )
    for _ in range(3):
        sqlite_app.state.image_service.process_pending()

    task_path = f"/chat/conversations/{conversation_id}/image-tasks/{task.task_id}"
    task_read = client.get(task_path)
    assert task_read.status_code == 200, task_read.text
    assert task_read.json()["status"] == "succeeded"
    asset_id = task_read.json()["asset_id"]
    assert asset_id is not None

    asset_path = f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
    asset = client.get(asset_path).json()
    assert asset["version_count"] == 1
    assert "一座桥的素描" in asset["alt_text"]

    version_id = asset["current_version_id"]
    bytes_read = client.get(f"{asset_path}/versions/{version_id}/image")
    assert bytes_read.status_code == 200
    assert bytes_read.content == _IMAGE_BYTES
    assert "no-store" in bytes_read.headers["cache-control"]

    _assert_retired(
        client.post(f"{task_path}/cancel"), code="legacy_image_retired", replacement="/"
    )
    _assert_retired(
        client.post(f"{task_path}/retry"), code="legacy_image_retired", replacement="/"
    )
    _assert_retired(
        client.put(f"{asset_path}/alt-text", json={"alt_text": "改过的替代文本"}),
        code="legacy_image_retired",
        replacement="/",
    )
    _assert_retired(
        client.delete(asset_path), code="legacy_image_retired", replacement="/"
    )

    # 只读面在 410 之后仍不变：任务成功、资产与替代文本保持原样。
    assert client.get(task_path).json()["status"] == "succeeded"
    assert client.get(asset_path).json()["alt_text"] == asset["alt_text"]


def test_video_writes_are_retired_while_history_stays_readable(
    sqlite_app: Any, client: TestClient
) -> None:
    """旧视频任务只能看：投影/字节流只读可查，取消、重试、改说明、删除全 410。"""
    account = _register(client)
    _install_media_adapters(sqlite_app)
    conversation_id = _create_conversation(client)
    message_id = _send_plain_message(client, conversation_id)
    task = sqlite_app.state.video_service.submit(
        account["id"], conversation_id, message_id, "一条河的视频"
    )
    for _ in range(3):
        sqlite_app.state.video_service.process_pending()

    task_path = f"/chat/conversations/{conversation_id}/video-tasks/{task.task_id}"
    task_read = client.get(task_path)
    assert task_read.status_code == 200, task_read.text
    assert task_read.json()["status"] == "succeeded"
    asset_id = task_read.json()["asset_id"]
    assert asset_id is not None

    asset_path = f"/chat/conversations/{conversation_id}/video-assets/{asset_id}"
    assert client.get(asset_path).status_code == 200
    bytes_read = client.get(f"{asset_path}/video")
    assert bytes_read.status_code == 200
    assert bytes_read.content == _VIDEO_BYTES

    _assert_retired(
        client.post(f"{task_path}/cancel"), code="legacy_video_retired", replacement="/"
    )
    _assert_retired(
        client.post(f"{task_path}/retry"), code="legacy_video_retired", replacement="/"
    )
    _assert_retired(
        client.put(f"{asset_path}/description", json={"description": "改过的说明"}),
        code="legacy_video_retired",
        replacement="/",
    )
    _assert_retired(
        client.delete(asset_path), code="legacy_video_retired", replacement="/"
    )

    assert client.get(task_path).json()["status"] == "succeeded"
    assert client.get(asset_path).status_code == 200


def test_media_history_reads_stay_account_scoped(
    sqlite_app: Any, client: TestClient
) -> None:
    """历史只读面跨账户一律 404：任务、资产与字节流都不泄漏存在性。"""
    account = _register(client)
    _install_media_adapters(sqlite_app)
    conversation_id = _create_conversation(client)
    message_id = _send_plain_message(client, conversation_id)
    task = sqlite_app.state.image_service.submit_generation(
        account["id"], conversation_id, message_id, "一座桥的素描"
    )
    for _ in range(3):
        sqlite_app.state.image_service.process_pending()
    asset_id = client.get(
        f"/chat/conversations/{conversation_id}/image-tasks/{task.task_id}"
    ).json()["asset_id"]

    _register(client, tag="2")
    other_conversation = _create_conversation(client)
    for path in (
        f"/chat/conversations/{other_conversation}/image-tasks/{task.task_id}",
        f"/chat/conversations/{other_conversation}/image-assets/{asset_id}",
        f"/chat/conversations/{other_conversation}/image-assets/{asset_id}"
        f"/versions/v-missing/image",
    ):
        assert client.get(path).status_code == 404, path


def test_historical_media_message_retry_does_not_reenqueue(
    sqlite_app: Any, client: TestClient
) -> None:
    """历史图片/视频消息的消息级重试同样 410，不重新入队供应商调用。"""
    account = _register(client)
    image_adapter, video_adapter = _install_media_adapters(sqlite_app)
    conversation_id = _create_conversation(client)
    other_conversation = _create_conversation(client)
    image_message = _send_plain_message(client, conversation_id)
    video_message = _send_plain_message(client, other_conversation)
    sqlite_app.state.image_service.submit_generation(
        account["id"], conversation_id, image_message, "一座桥的素描"
    )
    sqlite_app.state.video_service.submit(
        account["id"], other_conversation, video_message, "一条河的视频"
    )
    image_calls = len(image_adapter.calls)
    video_calls = len(video_adapter.calls)

    _assert_retired(
        client.post(
            f"/chat/conversations/{conversation_id}/messages/{image_message}/retry"
        ),
        code="legacy_image_retired",
        replacement="/",
    )
    _assert_retired(
        client.post(
            f"/chat/conversations/{other_conversation}/messages/{video_message}/retry"
        ),
        code="legacy_video_retired",
        replacement="/",
    )
    assert len(image_adapter.calls) == image_calls
    assert len(video_adapter.calls) == video_calls


def test_read_aloud_generation_is_retired_but_history_stays_readable(
    sqlite_app: Any, client: TestClient
) -> None:
    """朗读生成与清理 410；历史朗读投影仍可读，音频未就绪返回 404。"""
    _register(client)
    conversation_id = _create_conversation(client)
    message_id = _send_plain_message(client, conversation_id)
    read_aloud_path = (
        f"/chat/conversations/{conversation_id}/messages/{message_id}/read-aloud"
    )

    _assert_retired(
        client.post(read_aloud_path),
        code="legacy_read_aloud_retired",
        replacement="/",
    )
    _assert_retired(
        client.delete(read_aloud_path),
        code="legacy_read_aloud_retired",
        replacement="/",
    )

    projection = client.get(read_aloud_path)
    assert projection.status_code == 200, projection.text
    assert projection.json()["state"] == "not_generated"
    audio = client.get(f"{read_aloud_path}/audio")
    assert audio.status_code == 404
    assert audio.json()["detail"]["error"] == "audio_not_ready"


def test_science_writes_are_retired_while_reads_stay_available(
    sqlite_app: Any, client: TestClient
) -> None:
    """科学创作的 8 个写入口全部 410；只读路由保留且不再是可执行入口。"""
    account = _register(client)
    writable = (
        "/science/sources",
        f"/science/projects/{account['id']}/sources",
        "/science/sources/s-1/versions",
        "/science/sources/s-1/revoke",
        "/science/search",
        f"/science/projects/{account['id']}/search",
        "/science/claim-graphs",
        f"/science/projects/{account['id']}/claim-graphs",
    )
    for path in writable:
        _assert_retired(
            client.post(path, json={}),
            code="legacy_science_retired",
            replacement="/knowledge-base",
        )
    for path in ("/science/sources", "/science/claim-graphs"):
        assert client.get(path).status_code != 410, path
