"""Issue 18：知识库 API——上传、列表、详情、下载、重试、重建、删除与越权隔离。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from kb_support import seed_probe_for_account

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
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "knowledge-base-api-test-secret")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return TestClient(create_app())


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


def _upload(
    client: TestClient,
    filename: str,
    content: bytes,
    *,
    content_type: str = "text/plain",
) -> Any:
    return client.post(
        "/knowledge-base/materials",
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Bridges-Filename": quote(filename, safe=""),
            "X-Bridges-Upload-Id": "upload-18",
        },
    )


def _worker_service(app: Any) -> tuple[IngestionService, CapabilityProbeService]:
    """构造与 API 进程共享同一数据库的 worker 侧摄取服务（确定性向量）。"""
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


def _hold_lease(app: Any, document_id: str) -> None:
    """模拟后台执行器正持有该文档的处理租约。"""
    database: BridgesDatabase = app.state.bridges_database
    with database.transaction():
        database.connection.execute(
            "UPDATE document_records SET status = 'parsing', claimed_at = ?,"
            " lease_expires_at = ? WHERE document_id = ?",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                (datetime.now(UTC) + timedelta(seconds=1800)).isoformat(timespec="seconds"),
                document_id,
            ),
        )


def test_upload_list_detail_download_happy_path(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "kba")

    uploaded = _upload(client, "课程笔记.txt", "第一段。\n\n第二段。".encode())
    assert uploaded.status_code == 201, uploaded.text
    material = uploaded.json()
    assert material["status"] == "queued"
    assert material["filename"] == "课程笔记.txt"
    assert material["media_type"] == "text/plain"
    assert material["source"] == "本地上传"
    assert material["usable_for_chat"] is False
    assert len(material["content_hash"]) == 64
    assert material["content_hash_summary"] == material["content_hash"][:12]
    object_id = material["object_id"]

    # 同名同内容重复上传幂等复用
    duplicate = _upload(client, "课程笔记.txt", "第一段。\n\n第二段。".encode())
    assert duplicate.status_code == 200
    assert duplicate.json()["object_id"] == object_id

    listing = client.get("/knowledge-base/materials")
    assert listing.status_code == 200
    assert [item["object_id"] for item in listing.json()] == [object_id]

    detail = client.get(f"/knowledge-base/materials/{object_id}")
    assert detail.status_code == 200
    assert detail.json()["document_id"] == f"doc-{object_id}"

    # worker 侧处理一轮后呈现 ready（同一数据库）
    _seed_embedding_available(client.app, account["id"])
    worker, worker_probes = _worker_service(client.app)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    assert "处理 1 份文档" in worker.process_pending()

    detail = client.get(f"/knowledge-base/materials/{object_id}")
    body = detail.json()
    assert body["status"] == "ready"
    assert body["usable_for_chat"] is True
    assert body["vector_indexed"] is True
    assert body["index_version_id"] is not None

    download = client.get(f"/knowledge-base/materials/{object_id}/download")
    assert download.status_code == 200
    assert download.content == "第一段。\n\n第二段。".encode()
    assert "UTF-8''" in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"


def test_upload_rejects_unsupported_media_type(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    _register(client, "kbu")
    response = _upload(client, "表格.csv", b"a,b,c", content_type="text/csv")
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unsupported_media_type"


def test_retry_failed_material(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "kbf")
    uploaded = _upload(
        client, "坏文件.pdf", b"%PDF-1.7\ngarbage", content_type="application/pdf"
    )
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    worker, worker_probes = _worker_service(client.app)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    worker.process_pending()

    detail = client.get(f"/knowledge-base/materials/{object_id}")
    assert detail.json()["status"] == "error"
    assert detail.json()["failure_stage"] == "parse"
    assert "PDF 解析失败" in (detail.json()["failure_reason"] or "")

    retry = client.post(f"/knowledge-base/materials/{object_id}/retry")
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "queued"
    # 幂等：再次重试仍返回 queued，不产生重复记录
    assert client.post(f"/knowledge-base/materials/{object_id}/retry").json()[
        "status"
    ] == "queued"


def test_rebuild_endpoint_and_processing_conflict(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "kbr")
    object_id = _upload(client, "重建.txt", "显式重建内容。".encode()).json()["object_id"]

    worker, worker_probes = _worker_service(client.app)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    worker.process_pending()
    version_before = client.get(f"/knowledge-base/materials/{object_id}").json()[
        "index_version_id"
    ]

    rebuild = client.post(f"/knowledge-base/materials/{object_id}/rebuild")
    assert rebuild.status_code == 200, rebuild.text
    assert rebuild.json()["status"] == "queued"

    worker.process_pending()
    body = client.get(f"/knowledge-base/materials/{object_id}").json()
    assert body["status"] == "ready"
    assert body["index_version_id"] != version_before

    # 处理租约有效期间重建返回 409 与中文原因
    _hold_lease(client.app, f"doc-{object_id}")
    conflict = client.post(f"/knowledge-base/materials/{object_id}/rebuild")
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["error"] == "material_processing"
    assert "处理" in detail["message"]


def test_delete_endpoint_and_processing_conflict(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "kbd")
    object_id = _upload(client, "待删除.txt", "删除测试内容。".encode()).json()["object_id"]
    worker, worker_probes = _worker_service(client.app)
    seed_probe_for_account(worker_probes, account["id"], available=True)
    worker.process_pending()

    # 处理中删除：409 且消息可恢复（中文提示稍后重试）
    _hold_lease(client.app, f"doc-{object_id}")
    conflict = client.delete(f"/knowledge-base/materials/{object_id}")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "material_processing"
    assert "稍后重试" in conflict.json()["detail"]["message"]

    # 租约释放（记录回到 ready）后删除成功
    database: BridgesDatabase = client.app.state.bridges_database
    with database.transaction():
        database.connection.execute(
            "UPDATE document_records SET status = 'ready', claimed_at = NULL,"
            " lease_expires_at = NULL WHERE document_id = ?",
            (f"doc-{object_id}",),
        )
    deleted = client.delete(f"/knowledge-base/materials/{object_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/knowledge-base/materials/{object_id}").status_code == 404
    assert client.get("/knowledge-base/materials").json() == []


def test_cross_account_operations_are_uniform_404(tmp_path: Path, monkeypatch: Any) -> None:
    client = _app(tmp_path, monkeypatch)
    _register(client, "kba")
    object_id = _upload(client, "私有.txt", "账户 A 的材料。".encode()).json()["object_id"]

    # 切换到账户 B 的会话
    _register(client, "kbb")
    assert client.get("/knowledge-base/materials").json() == []
    for response in [
        client.get(f"/knowledge-base/materials/{object_id}"),
        client.get(f"/knowledge-base/materials/{object_id}/download"),
        client.post(f"/knowledge-base/materials/{object_id}/retry"),
        client.post(f"/knowledge-base/materials/{object_id}/rebuild"),
        client.delete(f"/knowledge-base/materials/{object_id}"),
    ]:
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "material_not_found"


def test_vector_degraded_presentation(tmp_path: Path, monkeypatch: Any) -> None:
    """Embedding 探测不可用：投影呈现全文就绪/向量不可用与中文原因。"""
    client = _app(tmp_path, monkeypatch)
    account = _register(client, "kbv")
    object_id = _upload(client, "降级.txt", "向量降级内容。".encode()).json()["object_id"]

    worker, worker_probes = _worker_service(client.app)
    seed_probe_for_account(worker_probes, account["id"], available=False)
    worker.process_pending()

    # API 进程探测快照同样标记不可用：投影呈现降级原因而非"尚未探测"
    probe_service: CapabilityProbeService = client.app.state.ingestion_service._probes  # type: ignore[attr-defined]
    probe_service._put_record(
        account["id"],
        ProbeRecord(
            probe_id=f"probe-{account['id']}",
            capability_id="embedding",
            model_id="text-embedding-v4",
            region="cn-beijing",
            parameters={"dimensions": 1024},
            status=ProbeStatus.UNAVAILABLE,
            probed_at=datetime.now(UTC),
            error_message="凭据缺失。",
        ),
    )

    body = client.get(f"/knowledge-base/materials/{object_id}").json()
    assert body["status"] == "ready"
    assert body["usable_for_chat"] is True
    assert body["vector_indexed"] is False
    assert body["embedding_available"] is False
    assert "Embedding" in (body["vector_unavailable_reason"] or "")
    assert body["index_version_id"] is not None
