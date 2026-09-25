"""V2 Issue 05：聊天照片附件——草稿、校验、绑定、多模态与隔离。

覆盖验收项：
1. 桌面上传草稿（服务端契约：选择/拖入/粘贴由前端写入草稿接口）；
2. 不支持类型、超限、读取失败与重复内容在服务端给出中文原因；
3. 草稿经类型、体积和账户校验，发送失败保留草稿，成功后幂等绑定
   消息与会话；
4. 图片进入本轮多模态回答；纯附件消息可发送并由助手询问用途，处理
   前不显示为已识别；
5. 他人无法读取附件；聊天照片不进入全局知识库；删除、导出与过期
   草稿处理符合会话数据约束。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.ai import CapabilityKind, CapabilityRecord

#: 最小合法 PNG（魔数 + 结尾）；嗅探只校验魔数与扩展名一致性。
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR-test-photo-bytes"
JPEG_BYTES = b"\xff\xd8\xff\xe0-test-jpeg-bytes"
TEXT_BYTES = "这不是图片内容。".encode()


def _app(tmp_path: Path, monkeypatch: Any) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "v205-photo-attachment-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"987654{sum(bytearray(tag.encode('utf-8'))) % 10 ** 8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _upload_draft(
    client: TestClient,
    *,
    filename: str = "photo.png",
    upload_id: str = "upload-1",
    content: bytes = PNG_BYTES,
) -> Any:
    return client.post(
        "/chat/attachment-drafts",
        headers={
            "X-Bridges-Filename": quote(filename),
            "X-Bridges-Upload-Id": upload_id,
        },
        content=content,
    )


def _start_conversation(
    client: TestClient, app: Any, content: str = "先聊两句"
) -> str:
    """首轮创建会话并同步驱动执行器收敛，避免后续发送被 409 拦截。"""
    response = client.post(
        "/chat/first-turn",
        json={
            "content": content,
            "idempotency_key": f"first-{content[:8]}-{id(client) % 10 ** 8}",
        },
    )
    assert response.status_code == 201, response.text
    app.state.generation_executor.run_tick()
    return response.json()["conversation"]["conversation_id"]


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )


class _CapturingAdapter:
    """记录每次模型载荷的确定性替身；同时产出固定回答。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        self.payloads.append(payload)
        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": "已收到照片。"}
        )

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="已收到照片。")
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _gateway_with(adapter: _CapturingAdapter) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


# ---------------------------------------------------------------------------
# 草稿上传：类型、体积与幂等（AC2/AC3）
# ---------------------------------------------------------------------------

def test_draft_upload_roundtrip_and_listing(tmp_path: Path, monkeypatch: Any) -> None:
    """上传 PNG 草稿 → 201 投影；列表可恢复（页面重开场景）。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "round")
        response = _upload_draft(client, filename="书页1.png", upload_id="u-1")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["original_filename"] == "书页1.png"
        assert body["media_type"] == "image/png"
        assert body["content_length"] == len(PNG_BYTES)
        assert body["content_hash"]

        listing = client.get("/chat/attachment-drafts")
        assert listing.status_code == 200, listing.text
        items = listing.json()
        assert [item["object_id"] for item in items] == [body["object_id"]]


def test_draft_upload_is_idempotent_and_conflicts(tmp_path: Path, monkeypatch: Any) -> None:
    """同标识同内容重放 200 复用；同标识异内容 409；异标识同内容去重。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "idem")
        first = _upload_draft(client, upload_id="u-same")
        assert first.status_code == 201, first.text

        replay = _upload_draft(client, upload_id="u-same")
        assert replay.status_code == 200, replay.text
        assert replay.json()["object_id"] == first.json()["object_id"]

        conflict = _upload_draft(
            client, upload_id="u-same", content=JPEG_BYTES, filename="other.jpg"
        )
        assert conflict.status_code == 409, conflict.text

        dedupe = _upload_draft(client, upload_id="u-other")
        assert dedupe.status_code == 200, dedupe.text
        assert dedupe.json()["object_id"] == first.json()["object_id"]
        listing = client.get("/chat/attachment-drafts")
        assert len(listing.json()) == 1


def test_draft_rejects_unsupported_type_with_chinese_reason(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """文本/PDF 等非照片类型被拒；原因中文且说明支持的类型。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "type")
        response = _upload_draft(
            client, filename="notes.txt", upload_id="u-txt", content=TEXT_BYTES
        )
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_file_type"
        assert "PNG" in detail["message"]


def test_draft_rejects_oversize_and_mismatch_and_empty(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超过 10 MB、扩展名与内容不符、空文件分别给出中文原因。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "big")
        oversize = _upload_draft(
            client,
            filename="big.png",
            upload_id="u-big",
            content=b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1),
        )
        assert oversize.status_code == 413, oversize.text
        assert oversize.json()["detail"]["error"] == "file_too_large"

        mismatch = _upload_draft(
            client, filename="fake.png", upload_id="u-fake", content=TEXT_BYTES
        )
        assert mismatch.status_code == 400, mismatch.text
        assert mismatch.json()["detail"]["error"] == "invalid_file_type"

        empty = _upload_draft(
            client, filename="empty.png", upload_id="u-empty", content=b""
        )
        assert empty.status_code == 400, empty.text
        assert empty.json()["detail"]["error"] == "empty_file"

        # 失败的草稿不清空其他草稿：此前成功的草稿仍在。
        keep = _upload_draft(client, upload_id="u-keep")
        assert keep.status_code == 201, keep.text
        assert len(client.get("/chat/attachment-drafts").json()) == 1


def test_drafts_are_invisible_across_accounts(tmp_path: Path, monkeypatch: Any) -> None:
    """他人无法列出、读取或删除草稿：一律表现为不存在。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "alice")
        created = _upload_draft(client, upload_id="u-a")
        object_id = created.json()["object_id"]

    with TestClient(app) as client:
        _register(client, "bob")
        assert client.get("/chat/attachment-drafts").json() == []
        assert (
            client.get(f"/chat/attachment-drafts/{object_id}/content").status_code
            == 404
        )
        assert (
            client.delete(f"/chat/attachment-drafts/{object_id}").status_code == 404
        )


def test_draft_content_download_serves_bytes(tmp_path: Path, monkeypatch: Any) -> None:
    """草稿内容可按账户读取（发送前缩略图预览）。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "down")
        object_id = _upload_draft(client, upload_id="u-c").json()["object_id"]
        response = client.get(f"/chat/attachment-drafts/{object_id}/content")
        assert response.status_code == 200, response.text
        assert response.content == PNG_BYTES
        assert response.headers["content-type"].startswith("image/png")


# ---------------------------------------------------------------------------
# 发送绑定：成功原子绑定、失败保留草稿（AC3）
# ---------------------------------------------------------------------------

def test_send_binds_drafts_in_requested_order(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """按请求页序绑定：消息投影附件顺序与 ordinal 一致；草稿域清空。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "order")
        first = _upload_draft(client, filename="p1.png", upload_id="u-1").json()
        second = _upload_draft(client, filename="p2.png", upload_id="u-2").json()
        third = _upload_draft(client, filename="p3.png", upload_id="u-3").json()
        conversation_id = _start_conversation(client, app)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "看这三张照片",
                "attachment_ids": [third["object_id"], first["object_id"]],
            },
        )
        assert response.status_code == 200, response.text
        attachments = response.json()["user_message"]["attachments"]
        assert [a["object_id"] for a in attachments] == [
            third["object_id"],
            first["object_id"],
        ]
        assert [a["ordinal"] for a in attachments] == [1, 2]
        assert all(a["status"] == "bound" for a in attachments)
        # 未发送的草稿保留，已发送的从草稿域消失。
        remaining = client.get("/chat/attachment-drafts").json()
        assert [item["object_id"] for item in remaining] == [second["object_id"]]

        generation_helpers["drive"](app)
        detail = client.get(f"/chat/conversations/{conversation_id}").json()
        user_message = next(
            m for m in detail["messages"] if m["role"] == "user" and m["attachments"]
        )
        assert [a["object_id"] for a in user_message["attachments"]] == [
            third["object_id"],
            first["object_id"],
        ]


def test_send_failure_preserves_text_and_drafts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """发送失败（引用不存在的附件）→ 既有草稿不受影响、可重试。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "fail")
        keep = _upload_draft(client, filename="keep.png", upload_id="u-keep").json()
        conversation_id = _start_conversation(client, app)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "带附件",
                "attachment_ids": [keep["object_id"], "ghost-id"],
            },
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"] == "attachment_not_found"
        # 好草稿没被清掉。
        remaining = client.get("/chat/attachment-drafts").json()
        assert [item["object_id"] for item in remaining] == [keep["object_id"]]


def test_bound_draft_cannot_bind_second_message(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """同一草稿只能绑定一条消息：双击/重试不产生第二条绑定。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "twice")
        draft = _upload_draft(client, upload_id="u-t").json()
        conversation_id = _start_conversation(client, app)
        first = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第一次", "attachment_ids": [draft["object_id"]]},
        )
        assert first.status_code == 200, first.text
        generation_helpers["drive"](app)

        second = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "第二次", "attachment_ids": [draft["object_id"]]},
        )
        assert second.status_code == 404, second.text
        database = app.state.bridges_database
        bound_rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments"
            " WHERE object_id = ? AND message_id IS NOT NULL",
            (draft["object_id"],),
        ).fetchone()["n"]
        assert bound_rows == 1


def test_send_rejects_too_many_or_duplicate_ids(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超过 10 个或重复 ID 在发送前被拒，草稿不受影响。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "many")
        draft = _upload_draft(client, upload_id="u-d").json()
        conversation_id = _start_conversation(client, app)

        too_many = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "太多",
                "attachment_ids": [f"ghost-{i}" for i in range(11)],
            },
        )
        assert too_many.status_code == 400, too_many.text
        assert too_many.json()["detail"]["error"] == "too_many_attachments"

        duplicate = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "重复",
                "attachment_ids": [draft["object_id"], draft["object_id"]],
            },
        )
        assert duplicate.status_code == 400, duplicate.text
        assert duplicate.json()["detail"]["error"] == "duplicate_attachment"


def test_idempotent_replay_does_not_double_bind(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """同幂等键重放返回同一运行，不重复绑定或创建消息。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "replay")
        draft = _upload_draft(client, upload_id="u-r").json()
        conversation_id = _start_conversation(client, app)
        body = {
            "content": "幂等发送",
            "attachment_ids": [draft["object_id"]],
            "idempotency_key": "replay-key-0001",
        }
        first = client.post(
            f"/chat/conversations/{conversation_id}/messages", json=body
        )
        assert first.status_code == 200, first.text
        generation_helpers["drive"](app)

        replay = client.post(
            f"/chat/conversations/{conversation_id}/messages", json=body
        )
        assert replay.status_code == 200, replay.text
        assert (
            replay.json()["user_message"]["message_id"]
            == first.json()["user_message"]["message_id"]
        )
        database = app.state.bridges_database
        bound_rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments WHERE object_id = ?",
            (draft["object_id"],),
        ).fetchone()["n"]
        assert bound_rows == 1
        assert database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachment_drafts"
        ).fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# 首轮绑定与纯附件消息（AC3/AC4）
# ---------------------------------------------------------------------------

def test_first_turn_binds_drafts_to_new_conversation(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """新聊天页直发照片：首轮原子建会话并绑定，标题使用照片占位。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "first")
        draft = _upload_draft(client, upload_id="u-f").json()

        response = client.post(
            "/chat/first-turn",
            json={
                "content": "",
                "attachment_ids": [draft["object_id"]],
                "idempotency_key": "first-turn-photo-1",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["conversation"]["title"] == "照片消息"
        user_message = body["user_message"]
        assert user_message["attachments"][0]["object_id"] == draft["object_id"]
        assert user_message["attachments"][0]["ordinal"] == 1
        assert client.get("/chat/attachment-drafts").json() == []
        app.state.generation_executor.run_tick()


def test_empty_message_without_attachments_still_rejected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """纯附件是照片专属豁免：无附件的空消息仍按 empty_message 拒绝。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "empty")
        response = client.post(
            "/chat/first-turn",
            json={"content": "", "idempotency_key": "empty-key-0001"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "empty_message"


# ---------------------------------------------------------------------------
# 多模态回答与「不伪装已识别」（AC4）
# ---------------------------------------------------------------------------

def test_images_enter_model_payload_in_ordinal_order(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """当前轮照片以图片内容部件进入模型载荷；历史轮保持纯文本。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "multi")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        photo_a = _upload_draft(client, filename="a.png", upload_id="u-a").json()
        photo_b = _upload_draft(client, filename="b.png", upload_id="u-b").json()
        conversation_id = _start_conversation(client, app)
        # 先发一条纯文本，作为历史轮对照。
        first = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "历史纯文本"},
        )
        assert first.status_code == 200, first.text
        generation_helpers["drive"](app)

        second = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "这两张照片里是什么？",
                "attachment_ids": [photo_b["object_id"], photo_a["object_id"]],
            },
        )
        assert second.status_code == 200, second.text
        adapter.payloads.clear()
        generation_helpers["drive"](app)

        assert adapter.payloads, "模型未被调用"
        messages = adapter.payloads[-1]["messages"]
        current = messages[-1]
        assert current["role"] == "user"
        parts = current["content"]
        assert isinstance(parts, list)
        image_parts = [p for p in parts if p["type"] == "image_url"]
        assert [p["image_url"]["url"] for p in image_parts] == [
            f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}",
        ] * 2
        text_part = parts[-1]
        assert text_part == {"type": "text", "text": "这两张照片里是什么？"}
        # 历史轮与系统合同仍是纯字符串。
        assert all(isinstance(m["content"], str) for m in messages[:-1])


def test_pure_attachment_message_asks_purpose_and_is_not_recognized(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """纯照片消息可发送：模型被要求询问用途；投影不显示任何「已识别」。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "ask")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-ask").json()
        conversation_id = _start_conversation(client, app)

        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "", "attachment_ids": [draft["object_id"]]},
        )
        assert response.status_code == 200, response.text
        attachment = response.json()["user_message"]["attachments"][0]
        assert attachment["ingestion_status"] == "none"
        generation_helpers["drive"](app)

        assert adapter.payloads, "模型未被调用"
        current = adapter.payloads[-1]["messages"][-1]
        parts = current["content"]
        assert parts[0]["type"] == "image_url"
        assert "询问" in parts[-1]["text"]

        detail = client.get(f"/chat/conversations/{conversation_id}").json()
        assistant = [m for m in detail["messages"] if m["role"] == "assistant"]
        assert assistant[-1]["status"] == "done"
        assert assistant[-1]["content"]


# ---------------------------------------------------------------------------
# 知识库隔离、删除与过期清理（AC5）
# ---------------------------------------------------------------------------

def test_chat_photo_never_enters_global_knowledge_base(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """聊天照片不入知识库：材料列表为空，无摄取记录。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "kb")
        draft = _upload_draft(client, upload_id="u-kb").json()
        conversation_id = _start_conversation(client, app)
        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "看照片", "attachment_ids": [draft["object_id"]]},
        )
        assert response.status_code == 200, response.text
        generation_helpers["drive"](app)

        assert client.get("/knowledge-base/materials").json() == []
        database = app.state.bridges_database
        records = database.connection.execute(
            "SELECT COUNT(*) AS n FROM document_records WHERE object_id = ?",
            (draft["object_id"],),
        ).fetchone()["n"]
        assert records == 0


def test_conversation_delete_removes_bound_photos(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """删除会话连带删除已绑定照片；绑定行不残留。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "del")
        draft = _upload_draft(client, upload_id="u-del").json()
        conversation_id = _start_conversation(client, app)
        response = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "带照片", "attachment_ids": [draft["object_id"]]},
        )
        assert response.status_code == 200, response.text
        generation_helpers["drive"](app)

        deleted = client.delete(f"/chat/conversations/{conversation_id}")
        assert deleted.status_code in {200, 204}, deleted.text
        database = app.state.bridges_database
        assert database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"] == 0


def test_draft_sweep_respects_ttl_and_never_touches_bound(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """过期草稿被清理、期限内保留、已绑定照片绝不误删。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "sweep")
        stale = _upload_draft(client, filename="stale.png", upload_id="u-stale").json()
        fresh = _upload_draft(client, filename="fresh.png", upload_id="u-fresh").json()
        bound = _upload_draft(client, filename="bound.png", upload_id="u-bound").json()
        conversation_id = _start_conversation(client, app)
        sent = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "绑定照片", "attachment_ids": [bound["object_id"]]},
        )
        assert sent.status_code == 200, sent.text
        generation_helpers["drive"](app)

        database = app.state.bridges_database
        database.connection.execute(
            "UPDATE chat_attachment_drafts SET updated_at = ? WHERE object_id = ?",
            ((datetime.now(UTC) - timedelta(days=8)).isoformat(), stale["object_id"]),
        )
        swept = app.state.chat_attachment_service.sweep_drafts(
            datetime.now(UTC) - timedelta(hours=24 * 7)
        )
        assert swept == 1
        remaining_ids = {
            str(row["object_id"])
            for row in database.connection.execute(
                "SELECT object_id FROM chat_attachment_drafts"
            ).fetchall()
        }
        assert remaining_ids == {fresh["object_id"]}
        assert database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments WHERE object_id = ?",
            (bound["object_id"],),
        ).fetchone()["n"] == 1


def test_draft_delete_by_object_and_upload_id(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """按对象或上传标识移除草稿；未知标识幂等成功。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "rm")
        first = _upload_draft(client, upload_id="u-rm1").json()
        second = _upload_draft(
            client, upload_id="u-rm2", filename="b.jpg", content=JPEG_BYTES
        ).json()
        assert first["object_id"] != second["object_id"]

        removed = client.delete(f"/chat/attachment-drafts/{first['object_id']}")
        assert removed.status_code == 204, removed.text
        missing = client.delete(f"/chat/attachment-drafts/{first['object_id']}")
        assert missing.status_code == 404, missing.text

        by_upload = client.delete("/chat/attachment-drafts/by-upload/u-rm2")
        assert by_upload.status_code == 204, by_upload.text
        again = client.delete("/chat/attachment-drafts/by-upload/u-rm2")
        assert again.status_code == 204, again.text
        assert client.get("/chat/attachment-drafts").json() == []
