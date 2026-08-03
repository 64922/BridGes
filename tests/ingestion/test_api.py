"""Issue 17：摄取 API——上传入队、详情状态、失败重试、越权隔离与索引状态。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from conftest import seed_probe_for_account
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.storage import BridgesDatabase


def _app(tmp_path: Path, monkeypatch: Any) -> TestClient:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "ingestion-api-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    client = TestClient(create_app())
    client._tmp_path = tmp_path  # type: ignore[attr-defined]
    return client


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"{tag}_user",
            "qq_email": f"{ord(tag[0]) % 10}{ord(tag[1]) % 10}"
            f"{ord(tag[2]) % 10}{len(tag):03d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _upload(
    client: TestClient,
    conversation_id: str,
    filename: str,
    content: bytes,
    *,
    content_type: str = "text/plain",
    upload_id: str = "upload-17",
) -> Any:
    return client.post(
        f"/chat/conversations/{conversation_id}/attachments",
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Bridges-Filename": quote(filename, safe=""),
            "X-Bridges-Upload-Id": upload_id,
        },
    )


def _worker_service(
    app: Any, *, embedding_available: bool = True
) -> tuple[IngestionService, CapabilityProbeService]:
    """构造与 API 进程共享同一数据库的 worker 侧摄取服务（确定性向量）。

    返回 (服务, 探测服务)：探测状态按需播种，模拟 worker 的能力门。
    """
    database: BridgesDatabase = app.state.bridges_database
    probe_service = CapabilityProbeService(state_store=None)
    embedding = DeterministicEmbeddingPort()
    service = IngestionService(
        database=database,
        object_repository=app.state.object_repository,
        probe_service=probe_service,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    return service, probe_service


def _seed_embedding_available(app: Any, account_id: str) -> None:
    """在 API 进程的探测服务上标记 Embedding 可用（用于投影原因展示）。"""
    probe_service: CapabilityProbeService = app.state.ingestion_service._probes  # type: ignore[attr-defined]
    probe_service._put_record(
        account_id,
        ProbeRecord(
            probe_id=f"probe-{account_id}",
            capability_id="embedding",
            model_id="text-embedding-v4",
            region="cn-beijing",
            parameters={"dimensions": 1024},
            status=ProbeStatus.AVAILABLE,
            probed_at=datetime.now(UTC),
        ),
    )


def test_upload_enqueues_and_detail_shows_queued_then_ready(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "ing")
    conversation_id = _create_conversation(client)

    uploaded = _upload(client, conversation_id, "课程笔记.txt", "第一段。\n\n第二段。".encode())
    assert uploaded.status_code == 201, uploaded.text
    projection = uploaded.json()
    assert projection["ingestion_status"] == "queued"

    detail = client.get(
        f"/chat/conversations/{conversation_id}/attachments/{projection['object_id']}/ingestion"
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "queued"
    assert body["parser_version"] == "text-utf8-v1"
    assert body["chunk_count"] == 0

    # worker 侧处理一轮后，API 投影呈现 ready（同一数据库）
    _seed_embedding_available(client.app, account["id"])
    worker, worker_probes = _worker_service(client.app, embedding_available=True)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    summary = worker.process_pending()
    assert "处理 1 份文档" in summary

    detail = client.get(
        f"/chat/conversations/{conversation_id}/attachments/{projection['object_id']}/ingestion"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "ready"
    assert body["chunk_count"] == 1
    assert body["title"] == "第一段。"
    assert body["vector_indexed"] is True


def test_detail_and_retry_for_failed_document(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    _register(client, "fail")
    conversation_id = _create_conversation(client)
    # 内容嗅探接受 PDF 头，但文件本身损坏 → 摄取在解析阶段失败
    bad_content = b"%PDF-1.7\ngarbage not a real pdf"
    uploaded = _upload(
        client, conversation_id, "坏文件.pdf", bad_content,
        content_type="application/pdf",
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    worker, _ = _worker_service(client.app)
    worker.process_pending()

    detail = client.get(
        f"/chat/conversations/{conversation_id}/attachments/{object_id}/ingestion"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "error"
    assert body["failure_stage"] == "parse"
    assert "PDF 解析失败" in (body["failure_reason"] or "")

    # 重试：失败文档重新入队
    retry = client.post(
        f"/chat/conversations/{conversation_id}/attachments/{object_id}/ingestion/retry"
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "queued"


def test_cross_account_detail_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    _register(client, "aaa")
    _register(client, "bbb")
    conversation_a = _create_conversation(client)
    uploaded = _upload(client, conversation_a, "私有.txt", "私有内容".encode())
    object_id = uploaded.json()["object_id"]

    # B 访问 A 的摄取详情：与不存在相同的 404，不泄漏存在性
    _register(client, "ccc")
    _create_conversation(client)
    # 切换到 B 会话：B 的会话访问 A 的对象
    conversation_b = _create_conversation(client)
    detail = client.get(
        f"/chat/conversations/{conversation_b}/attachments/{object_id}/ingestion"
    )
    assert detail.status_code == 404
    assert detail.json()["detail"]["error"] == "attachment_not_found"

    retry = client.post(
        f"/chat/conversations/{conversation_b}/attachments/{object_id}/ingestion/retry"
    )
    assert retry.status_code == 404
    assert retry.json()["detail"]["error"] == "attachment_not_found"


def test_index_status_endpoint(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    _register(client, "idx")
    status = client.get("/chat/ingestion/index")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["active_version"] is None
    assert body["versions"] == []
    assert "尚未探测" in (body["vector_unavailable_reason"] or "")


def test_unsupported_type_shows_none_status(tmp_path: Path, monkeypatch: Any) -> None:
    from io import BytesIO
    from zipfile import ZipFile

    client = _app(tmp_path, monkeypatch)
    _register(client, "uns")
    conversation_id = _create_conversation(client)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    fake_xlsx = buffer.getvalue()
    uploaded = _upload(
        client,
        conversation_id,
        "表格.xlsx",
        fake_xlsx,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert uploaded.status_code == 201, uploaded.text
    projection = uploaded.json()
    assert projection["ingestion_status"] == "none"

    detail = client.get(
        f"/chat/conversations/{conversation_id}/attachments/{projection['object_id']}/ingestion"
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "none"


def test_index_status_shows_real_versions_after_worker_build(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """API 进程的索引状态端点展示 worker 构建的真实版本链（非"未启用"）。"""
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "idxv")
    conversation_id = _create_conversation(client)
    uploaded = _upload(client, conversation_id, "材料.txt", "索引状态测试。\n".encode())
    assert uploaded.status_code == 201, uploaded.text

    worker, worker_probes = _worker_service(client.app, embedding_available=True)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    worker.process_pending()

    status = client.get("/chat/ingestion/index")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["active_version"] is not None
    assert body["active_version"]["contract"]["model_id"] == "text-embedding-v4"
    assert body["active_version"]["contract"]["dimensions"] == 1024
    assert body["active_version"]["chunk_count"] == 1
    assert body["versions"]
