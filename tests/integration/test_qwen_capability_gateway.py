"""Integration tests for T009: Qwen capability registry and model run locks.

The seam: an authenticated user starts a built-in workflow whose nodes declare
Qwen capabilities, advances the run, and sees immutable model run locks in the
task-stage projection. Unregistered capabilities and regional failures lead to
BLOCKED runs with explainable locks.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult, RegionError
from bridges.api.main import create_app
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
)
from bridges.contracts.workflows import WorkflowRunStatus


def _register(client: TestClient, username: str, qq_email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": password},
    )
    assert response.status_code == 201
    return response.json()  # type: ignore[no-any-return]


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]  # type: ignore[no-any-return]


def _submit_work_order(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "为大学生解释贝尔不等式",
            "success_criteria": "生成带引用和事实锁的科普草稿",
            "risk_statement": "量子基础解释可能过度简化",
        },
    )
    assert response.status_code == 201
    return response.json()  # type: ignore[no-any-return]


def test_successful_qwen_task_records_model_run_locks() -> None:
    client = TestClient(create_app())
    _register(client, "qwen-success", "110001@qq.com", "correct-horse-12")
    project_id = _create_project(client, "Qwen 能力项目")

    draft = _submit_work_order(client, project_id)
    run_id = draft["run_id"]

    confirm = client.post(f"/projects/{project_id}/runs/{run_id}/confirm", json={"confirmed": True})
    assert confirm.status_code == 200
    confirmed = confirm.json()
    assert confirmed["run_status"] == "running"
    assert confirmed["model_run_locks"] == []

    advance1 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance1.status_code == 200
    after_first = advance1.json()
    assert after_first["run_status"] == "running"
    assert len(after_first["model_run_locks"]) == 1
    lock1 = after_first["model_run_locks"][0]
    assert lock1["capability_name"] == "qwen_text_chat"
    assert lock1["status"] == "success"
    assert lock1["actual_model_id"] == "qwen3.7-plus"
    assert lock1["region"] == "cn-beijing"

    advance2 = client.post(f"/_test/runs/{run_id}/advance")
    assert advance2.status_code == 200
    succeeded = advance2.json()
    assert succeeded["run_status"] == "succeeded"
    assert len(succeeded["model_run_locks"]) == 2
    lock2 = succeeded["model_run_locks"][1]
    assert lock2["capability_name"] == "qwen_structured_output"


def test_unregistered_capability_blocks_run_via_api() -> None:
    client = TestClient(create_app())
    _register(client, "qwen-unregistered", "110002@qq.com", "correct-horse-12")
    project_id = _create_project(client, "未注册能力项目")

    workflow_service = client.app.state.workflow_service  # type: ignore[attr-defined]

    workflow_service.register_workflow(
        name="unknown_cap_task",
        version="1",
        nodes=[
            {
                "node_id": "bad_call",
                "node_name": "非法调用",
                "human_gate": False,
                "capability_name": "not_registered",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED],
    )

    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "unknown_cap_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "调用未注册能力",
            "success_criteria": "被阻止",
            "risk_statement": "测试未注册能力",
        },
    )
    assert response.status_code == 201
    draft = response.json()
    run_id = draft["run_id"]

    confirm = client.post(f"/projects/{project_id}/runs/{run_id}/confirm", json={"confirmed": True})
    assert confirm.status_code == 200

    advance = client.post(f"/_test/runs/{run_id}/advance")
    assert advance.status_code == 200
    blocked = advance.json()
    assert blocked["run_status"] == "blocked"
    assert len(blocked["model_run_locks"]) == 1
    assert blocked["model_run_locks"][0]["status"] == "blocked"
    assert blocked["model_run_locks"][0]["error_code"] == "unregistered_capability"


def test_region_error_blocks_run_without_cross_region_fallback() -> None:
    client = TestClient(create_app())
    _register(client, "qwen-region", "110003@qq.com", "correct-horse-12")
    project_id = _create_project(client, "区域错误项目")

    registry = client.app.state.capability_registry  # type: ignore[attr-defined]
    gateway = client.app.state.model_gateway  # type: ignore[attr-defined]
    workflow_service = client.app.state.workflow_service  # type: ignore[attr-defined]

    registry.register(
        CapabilityRecord(
            name="region_locked_model",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="region-model",
            input_schema_version="in-v1",
            output_schema_version="out-v1",
            status=CapabilityStatus.VERIFIED,
        )
    )

    class _RegionErrorAdapter:
        def call(
            self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
        ) -> AdapterResult:
            raise RegionError("cn-beijing unavailable")

    gateway.register_adapter("region_locked_model", "1", _RegionErrorAdapter())


    workflow_service.register_workflow(
        name="region_task",
        version="1",
        nodes=[
            {
                "node_id": "regional_call",
                "node_name": "区域调用",
                "human_gate": False,
                "capability_name": "region_locked_model",
                "capability_version": "1",
            }
        ],
        terminal_states=[WorkflowRunStatus.BLOCKED],
    )

    response = client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "region_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "测试区域错误",
            "success_criteria": "区域错误导致阻塞",
            "risk_statement": "区域错误不应跨区",
        },
    )
    assert response.status_code == 201
    draft = response.json()
    run_id = draft["run_id"]

    confirm = client.post(f"/projects/{project_id}/runs/{run_id}/confirm", json={"confirmed": True})
    assert confirm.status_code == 200

    advance = client.post(f"/_test/runs/{run_id}/advance")
    assert advance.status_code == 200
    blocked = advance.json()
    assert blocked["run_status"] == "blocked"
    assert len(blocked["model_run_locks"]) == 1
    lock = blocked["model_run_locks"][0]
    assert lock["status"] == "blocked"
    assert lock["error_code"] == "region_error"
    assert lock["retry_count"] == 0
