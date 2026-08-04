"""Issue 19：学习项目文件的上传、列表、下载、删除与隔离边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from lp_support import create_project, register_account, upload_project_file


def test_file_upload_list_download_delete_roundtrip(client: TestClient) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    other = create_project(client, "另一个项目")

    content = "第一条笔记\n第二条笔记".encode()
    uploaded = upload_project_file(client, project_id, "笔记.txt", content)
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["filename"] == "笔记.txt"
    assert body["media_type"] == "text/plain"
    assert body["content_length"] == len(content)
    assert body["status"] == "queued"
    assert body["error"] is None
    object_id = body["object_id"]

    # 同名同内容重复上传幂等复用（200，同一对象）
    duplicate = upload_project_file(client, project_id, "笔记.txt", content)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["object_id"] == object_id

    listing = client.get(f"/learning-projects/{project_id}/files")
    assert listing.status_code == 200, listing.text
    files = listing.json()["files"]
    assert [f["object_id"] for f in files] == [object_id]
    assert files[0]["filename"] == "笔记.txt"

    # 文件列表按项目隔离
    assert client.get(f"/learning-projects/{other['project_id']}/files").json()[
        "files"
    ] == []

    download = client.get(f"/learning-projects/{project_id}/files/{object_id}/download")
    assert download.status_code == 200, download.text
    assert download.content == content
    assert download.headers["cache-control"] == "no-store"
    disposition = download.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition

    # 跨项目访问同一对象：统一 404
    assert (
        client.get(
            f"/learning-projects/{other['project_id']}/files/{object_id}/download"
        ).status_code
        == 404
    )
    # 项目文件不进入全局知识库材料列表
    materials = client.get("/knowledge-base/materials")
    assert materials.status_code == 200, materials.text
    assert materials.json() == []

    deleted = client.delete(f"/learning-projects/{project_id}/files/{object_id}")
    assert deleted.status_code == 204, deleted.text
    assert (
        client.get(
            f"/learning-projects/{project_id}/files/{object_id}/download"
        ).status_code
        == 404
    )
    assert client.get(f"/learning-projects/{project_id}/files").json()["files"] == []


def test_file_upload_validation_and_missing_project(client: TestClient) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]

    missing = upload_project_file(client, "missing-project", "笔记.txt", b"hello")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "project_not_found"

    empty = upload_project_file(client, project_id, "空.txt", b"")
    assert empty.status_code == 400
    assert empty.json()["detail"]["error"] == "empty_file"

    no_name = client.post(
        f"/learning-projects/{project_id}/files",
        content=b"hello",
        headers={"Content-Type": "text/plain"},
    )
    assert no_name.status_code == 400
    assert no_name.json()["detail"]["error"] == "invalid_filename"

    oversize = client.post(
        f"/learning-projects/{project_id}/files",
        content=b"x",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": str(11 * 1024 * 1024),
            "X-Bridges-Filename": "big.txt",
        },
    )
    assert oversize.status_code == 413
    assert oversize.json()["detail"]["error"] == "file_too_large"

    assert (
        client.get("/learning-projects/missing-project/files").status_code == 404
    )


def test_file_delete_conflicts_while_processing(
    client: TestClient, sqlite_app: Any
) -> None:
    register_account(client, "1")
    project = create_project(client)
    project_id = project["project_id"]
    uploaded = upload_project_file(client, project_id, "笔记.txt", b"hello world")
    assert uploaded.status_code == 201, uploaded.text
    object_id = uploaded.json()["object_id"]

    # 模拟后台执行器已领取该文档（processing 且租约未过期）
    lease = (datetime.now(UTC) + timedelta(minutes=30)).isoformat(timespec="seconds")
    database = sqlite_app.state.bridges_database
    with database.transaction():
        database.connection.execute(
            "UPDATE document_records SET status = 'processing', lease_expires_at = ?"
            " WHERE object_id = ?",
            (lease, object_id),
        )

    conflict = client.delete(f"/learning-projects/{project_id}/files/{object_id}")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "material_processing"

    # 租约过期后（恢复中）允许删除
    expired = (datetime.now(UTC) - timedelta(minutes=30)).isoformat(timespec="seconds")
    with database.transaction():
        database.connection.execute(
            "UPDATE document_records SET lease_expires_at = ? WHERE object_id = ?",
            (expired, object_id),
        )
    deleted = client.delete(f"/learning-projects/{project_id}/files/{object_id}")
    assert deleted.status_code == 204, deleted.text
