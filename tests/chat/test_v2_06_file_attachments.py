"""V2 Issue 06：聊天文件附件——支持类型、解析状态、可定位引用与隔离。

覆盖验收项：
1. 文件选择/拖入/发送草稿沿用会话附件行为；实际支持类型与 10 MB 上限由
   代表材料解析测试确定，界面说明与解析流水线同源；
2. 解析中、可用、失败与无法识别状态可见；不支持或过大的单个文件给出中文
   原因，不清空其他草稿；
3. 回答只引用本轮确实解析成功的内容与可定位片段；未就绪的文件必须如实
   告知，绝不声称已读取；
4. 文件按账户、会话与消息隔离，不自动进入全局知识库；删除与导出经过验证。
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import fitz
import pytest
from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.api.main import create_app
from bridges.chat.attachments import (
    CHAT_ATTACHMENT_MEDIA_TYPES,
    FILE_MEDIA_TYPES,
    MAX_ATTACHMENT_BYTES,
    PHOTO_MEDIA_TYPES,
    SUPPORTED_MEDIA_TYPE_HINT,
)
from bridges.config import get_settings
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import SUPPORTED_MEDIA_TYPES, IngestionService
from bridges.lifecycle.catalog import delete_account_rows, export_rows

#: 最小合法 PNG 魔数（嗅探只校验魔数与扩展名一致性）。
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR-test-photo-bytes"
#: 扩展名与内容不符的 PDF（魔数对但正文损坏 → 解析器判定失败）。
BROKEN_PDF_BYTES = b"%PDF-1.4\nthis is not a real pdf body"


def _app(tmp_path: Path, monkeypatch: Any) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "v206-file-attachment-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"98765{sum(bytearray(tag.encode('utf-8'))) % 10 ** 8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _upload_draft(
    client: TestClient,
    *,
    filename: str = "notes.txt",
    upload_id: str = "upload-1",
    content: bytes = "第一段内容。".encode(),
) -> Any:
    return client.post(
        "/chat/attachment-drafts",
        headers={
            "X-Bridges-Filename": quote(filename),
            "X-Bridges-Upload-Id": upload_id,
        },
        content=content,
    )


def _pdf_bytes(pages: list[str]) -> bytes:
    """构造多页 PDF（每页一段英文正文，用于页码定位断言）。"""
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 96), text)
    content = document.tobytes()
    document.close()
    return content


def _blank_pdf_bytes() -> bytes:
    """构造可解析但没有文字的 PDF（扫描件/空页的确定性替代）。"""
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    return content


_DOCX_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
    "<w:r><w:t>第一节 概率论</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>样本空间是所有可能结果的集合。</w:t></w:r></w:p>"
    "<w:p><w:pPr><w:pStyle w:val=\"Heading2\"/></w:pPr>"
    "<w:r><w:t>第二节 置信区间</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>置信水平描述区间覆盖真值的频率。</w:t></w:r></w:p>"
    "</w:body></w:document>"
)

_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _docx_bytes() -> bytes:
    """构造带标题样式的 DOCX（用于章节定位断言）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", _DOCX_DOCUMENT_XML)
    return buffer.getvalue()


_MARKDOWN_BYTES = (
    "# 概率论笔记\n\n"
    "样本空间与事件是概率的基础概念。\n\n"
    "## 置信区间\n\n"
    "置信水平描述区间覆盖真值的频率，常用 95%。\n"
).encode()


def _worker(app: Any) -> IngestionService:
    """构造与 API 进程共享同一数据库的 worker 侧摄取服务（确定性向量）。"""
    database = app.state.bridges_database
    embedding = DeterministicEmbeddingPort()
    return IngestionService(
        database=database,
        object_repository=app.state.object_repository,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )


def _drive_ingestion(app: Any, *, timeout: float = 60.0) -> None:
    """反复领取解析任务直到没有新文档被处理（含失败文档与孤儿清理）。"""
    worker = _worker(app)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "本轮处理 0 份文档" in worker.process_pending():
            return
    raise AssertionError("摄取队列未在超时前清空。")


def _draft_status(client: TestClient, object_id: str) -> str:
    drafts = client.get("/chat/attachment-drafts").json()
    return next(item["ingestion_status"] for item in drafts if item["object_id"] == object_id)


def _start_conversation(client: TestClient, app: Any, content: str = "先聊两句") -> str:
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


def _send(
    client: TestClient,
    app: Any,
    generation_helpers: Any,
    conversation_id: str,
    content: str,
    attachment_ids: list[str],
) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "attachment_ids": attachment_ids},
    )
    assert response.status_code == 200, response.text
    generation_helpers["drive"](app)
    return response.json()


def _last_assistant(client: TestClient, conversation_id: str) -> dict[str, Any]:
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    assistants = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert assistants, "会话中没有助手消息"
    return assistants[-1]


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
            actual_model_id=capability.model_id, output={"content": "已收到文件。"}
        )

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="已收到文件。")
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _gateway_with(adapter: _CapturingAdapter) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway


def _system_blocks(adapter: _CapturingAdapter) -> list[str]:
    payload = adapter.payloads[-1]
    return [
        str(message["content"])
        for message in payload["messages"]
        if message["role"] == "system"
    ]


# ---------------------------------------------------------------------------
# AC1：支持类型与体积上限（代表材料解析测试）
# ---------------------------------------------------------------------------

def test_supported_types_match_parse_pipeline_and_ui_hint() -> None:
    """支持类型集合与解析流水线严格一致；界面说明覆盖全部类型。

    界面文案与解析能力不允许漂移：写进提示的每一种类型都必须真能解析，
    解析器支持的每一种类型也都必须出现在提示里。
    """
    assert CHAT_ATTACHMENT_MEDIA_TYPES == SUPPORTED_MEDIA_TYPES
    assert FILE_MEDIA_TYPES | PHOTO_MEDIA_TYPES == CHAT_ATTACHMENT_MEDIA_TYPES
    for label in ("PDF", "DOCX", "TXT", "Markdown", "PNG", "JPEG", "GIF", "WebP"):
        assert label in SUPPORTED_MEDIA_TYPE_HINT


def test_representative_materials_parse_ready_within_size_limit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """代表材料（多页 PDF / DOCX / Markdown / 接近上限的 TXT）均解析为可用。

    体积上限沿用 10 MB：代表材料压测确认该上限下解析、分块与索引都能在
    可接受的资源内完成，因此界面承诺按 10 MB 说明。
    """
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "materials")
        cases = {
            "lecture-notes.pdf": _pdf_bytes(
                [
                    "Preface: this handout covers interval estimation.",
                    "Chapter 2 confidence level: coverage frequency of the interval.",
                    "Chapter 3 practice problems and answers.",
                ]
            ),
            "statistics.docx": _docx_bytes(),
            "probability-notes.md": _MARKDOWN_BYTES,
            # 接近上限的讲义：10 MB 以内必须真能解析（而非仅通过体积校验）。
            "big-notes.txt": (
                "第 1 节 熵与热力学第二定律\n孤立系统的熵永不减少。\n\n" * 30000
            ).encode(),
        }
        for filename, content in cases.items():
            assert len(content) <= MAX_ATTACHMENT_BYTES
            uploaded = _upload_draft(
                client, filename=filename, upload_id=f"u-{filename}", content=content
            )
            assert uploaded.status_code == 201, uploaded.text
            assert uploaded.json()["ingestion_status"] == "queued"
        assert max(len(content) for content in cases.values()) > 1024 * 1024

        _drive_ingestion(app)
        drafts = client.get("/chat/attachment-drafts").json()
        assert {item["ingestion_status"] for item in drafts} == {"ready"}

        database = app.state.bridges_database
        rows = database.connection.execute(
            "SELECT r.source, r.page_count, r.section_count, r.chunk_count"
            " FROM document_records r ORDER BY r.object_id"
        ).fetchall()
        assert {str(row["source"]) for row in rows} == {"chat_attachment"}
        by_pages = [row for row in rows if int(row["page_count"]) == 3]
        assert len(by_pages) == 1, "多页 PDF 未解析出页码"
        assert int(by_pages[0]["chunk_count"]) >= 3
        assert any(int(row["section_count"]) >= 2 for row in rows), "DOCX/Markdown 未解析出章节"


def test_oversize_and_unsupported_file_keep_other_drafts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """超过 10 MB 与无解析能力的类型被逐个拒绝（中文原因），其他草稿保留。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "limits")
        kept = _upload_draft(client, upload_id="u-kept").json()

        oversize = _upload_draft(
            client,
            filename="big-notes.txt",
            upload_id="u-oversize",
            content=b"a" * (MAX_ATTACHMENT_BYTES + 1),
        )
        assert oversize.status_code == 413, oversize.text
        assert oversize.json()["detail"]["error"] == "file_too_large"
        assert "10 MB" in oversize.json()["detail"]["message"]

        unsupported = _upload_draft(
            client,
            filename="scores.csv",
            upload_id="u-csv",
            content="姓名,分数\n甲,90\n".encode(),
        )
        assert unsupported.status_code == 400, unsupported.text
        detail = unsupported.json()["detail"]
        assert detail["error"] == "invalid_file_type"
        assert "PDF" in detail["message"] and "Markdown" in detail["message"]

        drafts = client.get("/chat/attachment-drafts").json()
        assert [item["object_id"] for item in drafts] == [kept["object_id"]]


# ---------------------------------------------------------------------------
# AC2：解析中 / 可用 / 失败 / 无法识别
# ---------------------------------------------------------------------------

def test_draft_states_visible_sent_attachment_states_visible(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """草稿与已发送附件都呈现解析中、可用、失败、无法识别四种状态。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "states")
        ready = _upload_draft(
            client, filename="notes.md", upload_id="u-ready", content=_MARKDOWN_BYTES
        ).json()
        broken = _upload_draft(
            client,
            filename="broken.pdf",
            upload_id="u-broken",
            content=BROKEN_PDF_BYTES,
        ).json()
        blank = _upload_draft(
            client,
            filename="scan.pdf",
            upload_id="u-blank",
            content=_blank_pdf_bytes(),
        ).json()

        # 发送前（排队中）：解析中状态可见，界面据此显示进度。
        assert _draft_status(client, ready["object_id"]) == "queued"
        assert _draft_status(client, broken["object_id"]) == "queued"

        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "先看看这些文件",
            [ready["object_id"], broken["object_id"], blank["object_id"]],
        )
        _drive_ingestion(app)

        detail = client.get(f"/chat/conversations/{conversation_id}").json()
        user_message = [m for m in detail["messages"] if m["role"] == "user"][-1]
        states = {
            item["original_filename"]: (
                item["ingestion_status"],
                item["ingestion_error"],
            )
            for item in user_message["attachments"]
        }
        assert states["notes.md"][0] == "ready"
        assert states["broken.pdf"][0] == "error"
        assert states["broken.pdf"][1] and "PDF" in states["broken.pdf"][1]
        assert states["scan.pdf"][0] == "empty"


def test_ready_material_is_retrieved_with_location(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """已解析材料进入本轮检索：引用带章节/页码定位与可核对片段。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "cite")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        docx = _upload_draft(
            client, filename="statistics.docx", upload_id="u-cite", content=_docx_bytes()
        ).json()
        pdf = _upload_draft(
            client,
            filename="lecture-notes.pdf",
            upload_id="u-pdf",
            content=_pdf_bytes(
                [
                    "Preface: this handout covers interval estimation.",
                    "Chapter 2 confidence level: coverage frequency of the interval.",
                    "Chapter 3 practice problems and answers.",
                ]
            ),
        ).json()
        _drive_ingestion(app)

        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "根据我的文件 置信水平 是什么意思？",
            [docx["object_id"]],
        )
        assistant = _last_assistant(client, conversation_id)
        assert assistant["status"] == "done"
        citations = assistant["retrieval"]["citations"]
        assert citations, "已解析附件未产生引用"
        citation = citations[0]
        assert citation["source_layer"] == "attachment"
        assert citation["filename"] == "statistics.docx"
        assert citation["section_title"] == "第二节 置信区间"
        assert "置信水平描述区间覆盖真值的频率" in citation["snippet"]

        blocks = _system_blocks(adapter)
        assert any(
            "以下是本轮检索到的本地材料" in block
            and "statistics.docx" in block
            and "章节：第二节 置信区间" in block
            for block in blocks
        ), blocks

        # 页码定位：PDF 第二页命中时引用带页码，而不是只报文件名。
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "根据我的文件 confidence level 的定义是什么？",
            [pdf["object_id"]],
        )
        assistant = _last_assistant(client, conversation_id)
        pdf_citations = [
            item
            for item in assistant["retrieval"]["citations"]
            if item["filename"] == "lecture-notes.pdf"
        ]
        assert pdf_citations, "PDF 未产生引用"
        assert any(item["page_number"] == 2 for item in pdf_citations)


# ---------------------------------------------------------------------------
# AC3：未就绪或无可引用片段的文件必须如实告知（绝不声称已读取）
# ---------------------------------------------------------------------------

def test_unparsed_attachment_reported_honestly_to_model(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """解析失败与仍在解析的文件在模型上下文中如实说明，且无伪造引用。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "honest")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        broken = _upload_draft(
            client,
            filename="broken.pdf",
            upload_id="u-honest-broken",
            content=BROKEN_PDF_BYTES,
        ).json()
        processing = _upload_draft(
            client,
            filename="pending.pdf",
            upload_id="u-honest-pending",
            content=_pdf_bytes(["Chapter 1 introduction to statistics."]),
        ).json()

        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "这个文件讲了什么？",
            [broken["object_id"], processing["object_id"]],
        )
        # 未驱动摄取：pending.pdf 仍是排队/解析中。
        blocks = _system_blocks(adapter)
        note = next(
            (block for block in blocks if "本轮用户附加的文件处理结果" in block), None
        )
        assert note is not None, blocks
        assert "broken.pdf" in note
        assert "仍在解析中" in note or "尚未开始解析" in note
        assert "不要猜测或声称已经读取" in note

        assistant = _last_assistant(client, conversation_id)
        assert assistant["status"] == "done"
        citations = (assistant["retrieval"] or {}).get("citations") or []
        assert all(item["filename"] not in {"broken.pdf", "pending.pdf"} for item in citations)


def test_ready_but_unmatched_attachment_is_not_described(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """已解析但本轮没有匹配片段时明确说明「本轮无法基于该文件作答」。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "unmatched")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(
            client, filename="notes.md", upload_id="u-unmatched", content=_MARKDOWN_BYTES
        ).json()
        _drive_ingestion(app)
        assert _draft_status(client, draft["object_id"]) == "ready"

        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "根据我的文件 傅里叶变换 的定义是什么？",
            [draft["object_id"]],
        )
        blocks = _system_blocks(adapter)
        note = next(
            (block for block in blocks if "本轮用户附加的文件处理结果" in block), None
        )
        assert note is not None, blocks
        assert "没有检索到与当前问题相关的片段" in note
        assert "不要描述或断言文件内容" in note

        assistant = _last_assistant(client, conversation_id)
        citations = (assistant["retrieval"] or {}).get("citations") or []
        assert citations == []


# ---------------------------------------------------------------------------
# AC4：隔离、知识库边界、删除与导出
# ---------------------------------------------------------------------------

def test_attachment_isolated_between_accounts_and_conversations(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """他人不可见；其他会话不检索本会话附件（跨会话不泄漏）。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "owner")
        draft = _upload_draft(
            client, filename="notes.md", upload_id="u-iso", content=_MARKDOWN_BYTES
        ).json()
        _drive_ingestion(app)
        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "根据我的文件 置信水平 是什么？",
            [draft["object_id"]],
        )
        assert (client.get("/chat/attachment-drafts").json()) == []

        # 同账户另一会话：附件层未启用，不产生该附件引用。
        other = _start_conversation(client, app, content="换个话题")
        _send(
            client,
            app,
            generation_helpers,
            other,
            "根据我的文件 置信水平 是什么？",
            [],
        )
        assistant = _last_assistant(client, other)
        citations = (assistant["retrieval"] or {}).get("citations") or []
        assert all(item["filename"] != "notes.md" for item in citations)
        layer = next(
            item
            for item in assistant["retrieval"]["layers"]
            if item["layer"] == "attachment"
        )
        assert layer["status"] == "disabled"

        token = client.cookies.get("bridges_session")
        client.cookies.clear()
        with TestClient(app) as intruder:
            _register(intruder, "intruder")
            assert intruder.get("/chat/attachment-drafts").json() == []
            assert (
                intruder.delete(f"/chat/attachment-drafts/{draft['object_id']}").status_code
                == 404
            )
            assert intruder.get(f"/chat/conversations/{conversation_id}").status_code == 404
        client.cookies.set("bridges_session", token or "")


def test_chat_file_stays_out_of_knowledge_base_and_deletion_cascades(
    tmp_path: Path, monkeypatch: Any, generation_helpers: Any
) -> None:
    """聊天文件不进知识库材料列表；删除会话后解析数据与对象一并清理。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        account = _register(client, "kb-boundary")
        draft = _upload_draft(
            client, filename="notes.md", upload_id="u-kb", content=_MARKDOWN_BYTES
        ).json()
        _drive_ingestion(app)
        conversation_id = _start_conversation(client, app)
        _send(
            client,
            app,
            generation_helpers,
            conversation_id,
            "看看这份笔记",
            [draft["object_id"]],
        )

        assert client.get("/knowledge-base/materials").json() == []
        database = app.state.bridges_database
        record = database.connection.execute(
            "SELECT source FROM document_records WHERE object_id = ?",
            (draft["object_id"],),
        ).fetchone()
        assert record is not None and str(record["source"]) == "chat_attachment"

        # 导出：附件元数据随账户导出可见（只出元数据与绑定关系）。
        account_id = str(account["id"])
        rows = export_rows(database, account_id, "chat_attachments")
        assert any(
            str(row["object_id"]) == draft["object_id"]
            and str(row["conversation_id"]) == conversation_id
            for row in rows
        )
        drafts_export = export_rows(database, account_id, "chat_attachment_drafts")
        assert all(str(row["object_id"]) != draft["object_id"] for row in drafts_export)

        deleted = client.delete(f"/chat/conversations/{conversation_id}")
        assert deleted.status_code in {200, 204}, deleted.text
        assert database.connection.execute(
            "SELECT COUNT(*) AS n FROM chat_attachments WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["n"] == 0

        _drive_ingestion(app)
        remaining = database.connection.execute(
            "SELECT COUNT(*) AS n FROM document_records WHERE object_id = ?",
            (draft["object_id"],),
        ).fetchone()["n"]
        chunks = database.connection.execute(
            "SELECT COUNT(*) AS n FROM document_chunks WHERE document_id = ?",
            (f"doc-{draft['object_id']}",),
        ).fetchone()["n"]
        assert remaining == 0 and chunks == 0

        # 账户级删除同时清空附件与草稿表。
        delete_account_rows(database, account_id)
        assert export_rows(database, account_id, "chat_attachments") == []
        assert export_rows(database, account_id, "chat_attachment_drafts") == []


def test_photo_drafts_are_not_parsed_as_documents(tmp_path: Path, monkeypatch: Any) -> None:
    """照片草稿不入解析队列：没有摄取记录，仍走多模态直读。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "photo-scope")
        photo = _upload_draft(
            client, filename="photo.png", upload_id="u-photo", content=PNG_BYTES
        ).json()
        assert photo["ingestion_status"] == "none"
        _drive_ingestion(app)
        database = app.state.bridges_database
        count = database.connection.execute(
            "SELECT COUNT(*) AS n FROM document_records WHERE object_id = ?",
            (photo["object_id"],),
        ).fetchone()["n"]
        assert count == 0
        assert _draft_status(client, photo["object_id"]) == "none"


@pytest.mark.parametrize("filename", ["notes.txt", "notes.md", "notes.pdf", "notes.docx"])
def test_file_draft_parse_enqueue_is_idempotent(
    tmp_path: Path, monkeypatch: Any, filename: str
) -> None:
    """同一上传标识重放不重复入队；重放时也会补齐解析任务。"""
    app = _app(tmp_path, monkeypatch)
    content = {
        "notes.txt": "第一段。".encode(),
        "notes.md": _MARKDOWN_BYTES,
        "notes.pdf": _pdf_bytes(["Chapter 1 introduction."]),
        "notes.docx": _docx_bytes(),
    }[filename]
    with TestClient(app) as client:
        _register(client, "idem")
        first = _upload_draft(
            client, filename=filename, upload_id="u-idem", content=content
        )
        assert first.status_code == 201, first.text
        replay = _upload_draft(
            client, filename=filename, upload_id="u-idem", content=content
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["object_id"] == first.json()["object_id"]

        database = app.state.bridges_database
        count = database.connection.execute(
            "SELECT COUNT(*) AS n FROM document_records WHERE object_id = ?",
            (first.json()["object_id"],),
        ).fetchone()["n"]
        assert count == 1
        _drive_ingestion(app)
        assert _draft_status(client, first.json()["object_id"]) == "ready"
