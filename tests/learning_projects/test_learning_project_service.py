"""Issue 19：学习项目服务级测试——摄取集成与 worker 处理项目文件。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.chat import ConversationRepository
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.contracts.ingestion import DocumentIngestionStatus
from bridges.credentials.probes import CapabilityProbeService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.learning_projects import LearningProjectError, LearningProjectService


def _make_services(
    storage: dict[str, Any],
) -> tuple[LearningProjectService, IngestionService]:
    """构造学习项目服务 + 带 worker 组件的摄取服务（确定性向量）。"""
    database = storage["database"]
    repository = storage["repository"]
    probe_service = CapabilityProbeService(state_store=None)
    for account_id in (storage["account_a"], storage["account_b"]):
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
    embedding = DeterministicEmbeddingPort()
    ingestion = IngestionService(
        database=database,
        object_repository=repository,
        probe_service=probe_service,
        embedding=embedding,
        index=VersionedIndex(database, embedding),
    )
    service = LearningProjectService(
        database,
        repository,
        ingestion,
        ConversationRepository(database),
    )
    return service, ingestion


def test_project_file_processed_by_worker_becomes_ready(
    storage: dict[str, Any],
) -> None:
    """项目文件入队后由后台执行器按 source=project_file 领取处理并就绪。"""
    service, ingestion = _make_services(storage)
    account_id = storage["account_a"]
    project = service.create_project(account_id, "数学", None)
    uploaded, created = service.upload_file(
        account_id, project.project_id, "这是一份足够摄取的文本笔记内容。".encode(), "笔记.txt"
    )
    assert created is True
    assert uploaded.status == DocumentIngestionStatus.QUEUED

    result = ingestion.process_pending()
    assert "worker" in result

    files = service.list_files(account_id, project.project_id)
    assert len(files) == 1
    assert files[0].status == DocumentIngestionStatus.READY

    # 项目文件不进入全局知识库列表
    assert ingestion.list_materials(account_id) == []


def test_service_level_project_isolation(storage: dict[str, Any]) -> None:
    """服务层直接调用同样强制账户隔离：跨账户一律 project_not_found。"""
    service, _ = _make_services(storage)
    account_a = storage["account_a"]
    account_b = storage["account_b"]
    project = service.create_project(account_a, "数学", "描述")
    assert project.name == "数学"
    assert project.description == "描述"

    fetched = service.get_project(account_a, project.project_id)
    assert fetched.name == "数学"

    for action in (
        lambda: service.get_project(account_b, project.project_id),
        lambda: service.get_detail(account_b, project.project_id),
        lambda: service.list_files(account_b, project.project_id),
        lambda: service.update_project(account_b, project.project_id, name="越权"),
        lambda: service.delete_project(account_b, project.project_id, contents="keep"),
    ):
        try:
            action()
        except LearningProjectError as exc:
            assert exc.code == "project_not_found"
            assert exc.status_code == 404
        else:  # pragma: no cover - 必须抛出
            raise AssertionError("跨账户访问必须被拒绝")

    # 账户 B 的项目列表为空，且互不可见
    assert service.list_projects(account_b) == []
    assert [p.project_id for p in service.list_projects(account_a)] == [
        project.project_id
    ]
