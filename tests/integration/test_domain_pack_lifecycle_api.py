"""T047 领域包失效、撤销、重验证与受信回滚 API 集成测试。

建议接缝：撤销一个已被多项目使用的包版本，验证新运行闭锁、影响带完整、
重验证推进且回滚不能复活不受信版本。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

PACK_ID = "mathematics.formal-proof"
PACK_VERSION = "1.0.0"


@pytest.fixture
def maintainer(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    test_client = TestClient(client.app)
    account = registered_user(
        test_client, "lfc-maintainer", "200001@qq.com", "correct-horse-12"
    )
    return test_client, cast(dict[str, Any], account["account"])


@pytest.fixture
def reviewer(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    test_client = TestClient(client.app)
    account = registered_user(
        test_client, "lfc-reviewer", "200002@qq.com", "correct-horse-12"
    )
    return test_client, cast(dict[str, Any], account["account"])


@pytest.fixture
def releaser(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    test_client = TestClient(client.app)
    account = registered_user(
        test_client, "lfc-releaser", "200003@qq.com", "correct-horse-12"
    )
    return test_client, cast(dict[str, Any], account["account"])


@pytest.fixture
def secadmin(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    test_client = TestClient(client.app)
    account = registered_user(
        test_client, "lfc-secadmin", "200004@qq.com", "correct-horse-12"
    )
    return test_client, cast(dict[str, Any], account["account"])


def _register_pack(maintainer: tuple[TestClient, dict[str, Any]]) -> dict[str, Any]:
    maintainer_client, account = maintainer
    response = maintainer_client.post(
        "/domain-packs/workbench/register",
        json={"pack_id": PACK_ID, "version": PACK_VERSION},
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["maintainer_id"] == account["id"]
    return record


def _declare_conflicts(clients: list[tuple[TestClient, dict[str, Any]]]) -> None:
    for test_client, _ in clients:
        response = test_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/conflict-of-interest",
            json={"disclosures": ["无相关利益"]},
        )
        assert response.status_code == 200, response.text


def _qualify_reviewer(reviewer: tuple[TestClient, dict[str, Any]]) -> None:
    reviewer_client, account = reviewer
    response = reviewer_client.post(
        "/domain-packs/qualifications",
        json={
            "qualification_id": "q-lfc-reviewer",
            "person_id": account["id"],
            "qualification_type": "formal_proof",
            "verifier_id": "verifier",
            "disciplines": ["mathematics", "formal_logic", "theorem_proving"],
            "verified_at": "2026-07-31T00:00:00Z",
            "valid_until": "2027-07-31T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text


def _release_pack(
    maintainer: tuple[TestClient, dict[str, Any]],
    reviewer: tuple[TestClient, dict[str, Any]],
    releaser: tuple[TestClient, dict[str, Any]],
) -> dict[str, Any]:
    """三签、灰度与发行，返回发行记录。"""
    maintainer_client, _ = maintainer
    reviewer_client, reviewer_account = reviewer
    releaser_client, releaser_account = releaser
    _register_pack(maintainer)
    _qualify_reviewer(reviewer)
    _declare_conflicts([maintainer, reviewer, releaser])

    response = maintainer_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/content-signature",
        json={"opinion": "内容完整", "conclusion": "approve"},
    )
    assert response.status_code == 200, response.text
    maintainer_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/reviewer",
        json={"person_id": reviewer_account["id"]},
    )
    maintainer_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/releaser",
        json={"person_id": releaser_account["id"]},
    )
    response = reviewer_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/independent-signature",
        json={"opinion": "独立复核通过", "conclusion": "approve"},
    )
    assert response.status_code == 200, response.text
    response = reviewer_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/gray-release"
    )
    assert response.status_code == 200, response.text
    response = releaser_client.post(
        f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/release"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_project(
    test_client: TestClient, name: str
) -> dict[str, Any]:
    response = test_client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _submit_run(
    test_client: TestClient, project_id: str
) -> dict[str, Any]:
    response = test_client.post(
        f"/projects/{project_id}/work-orders",
        json={
            "workflow_name": "generic_science_task",
            "workflow_version": "1",
            "project_id": project_id,
            "objective": "验证领域包失效处置",
            "success_criteria": "运行成功",
            "risk_statement": "无",
            "object_refs": [],
            "domain_pack_refs": [f"{PACK_ID}@{PACK_VERSION}"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestPackInvalidationLifecycle:
    def test_revoke_blocks_new_runs_and_impact_is_complete(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
        secadmin: tuple[TestClient, dict[str, Any]],
    ) -> None:
        release = _release_pack(maintainer, reviewer, releaser)
        assert release["platform_attestation"]["role"] == "platform"

        # 两个项目各提交一次使用该包的运行。
        maintainer_client, _ = maintainer
        project_a = _create_project(maintainer_client, "领域包失效 A")
        project_b = _create_project(maintainer_client, "领域包失效 B")
        run_a = _submit_run(maintainer_client, project_a["id"])
        run_b = _submit_run(maintainer_client, project_b["id"])

        # 安全管理员登记并紧急撤销。
        secadmin_client, secadmin_account = secadmin
        response = secadmin_client.post("/domain-packs/security-admins")
        assert response.status_code == 204, response.text
        response = secadmin_client.get("/domain-packs/security-admins")
        assert response.json() == {"is_security_admin": True}

        response = secadmin_client.post(
            "/domain-packs/revocations",
            json={
                "pack_id": PACK_ID,
                "version": PACK_VERSION,
                "trigger": "security_event",
                "reason": "发现恶意依赖，紧急撤销",
                "second_factor": "totp-778899",
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        event = payload["event"]
        revocation = payload["revocation"]
        assert event["stage"] == "contained"
        assert revocation["blocks_new_runs"] is True
        assert revocation["follow_up_required"] is True
        assert revocation["revoked_by"] == secadmin_account["id"]

        record = maintainer_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}"
        ).json()
        assert record["lifecycle_status"] == "revoked"

        # 新运行闭锁：提交返回 400 且不产生运行。
        response = maintainer_client.post(
            f"/projects/{project_a['id']}/work-orders",
            json={
                "workflow_name": "generic_science_task",
                "workflow_version": "1",
                "project_id": project_a["id"],
                "objective": "撤销后提交",
                "success_criteria": "应被拒绝",
                "risk_statement": "无",
                "domain_pack_refs": [f"{PACK_ID}@{PACK_VERSION}"],
            },
        )
        assert response.status_code == 400, response.text
        assert "领域包已撤销或失效" in response.json()["detail"]["message"]

        # 已提交草稿的确认也被闭锁。
        response = maintainer_client.post(
            f"/projects/{project_a['id']}/runs/{run_a['run_id']}/confirm",
            json={"confirmed": True},
        )
        assert response.status_code == 400, response.text

        # 影响集覆盖包、运行、项目和用户动作（尚未派生 Claim/产物）。
        response = secadmin_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/resolve-impact"
        )
        assert response.status_code == 200, response.text
        impact = response.json()
        categories = {item["category"] for item in impact["items"]}
        assert {"pack", "run", "project", "user_action"} <= categories
        run_refs = {
            item["ref_id"]
            for item in impact["items"]
            if item["category"] == "run"
        }
        assert {run_a["run_id"], run_b["run_id"]} <= run_refs
        project_refs = {
            item["ref_id"]
            for item in impact["items"]
            if item["category"] == "project"
        }
        assert {project_a["id"], project_b["id"]} <= project_refs

        # 阶段推进：紧急撤销已先进入 CONTAINED，继续定位影响 → 修复。
        for stage in (
            "impacted_objects_found",
            "remediating",
        ):
            response = secadmin_client.post(
                f"/domain-packs/invalidations/{event['event_id']}/advance",
                json={"to_stage": stage},
            )
            assert response.status_code == 200, response.text

        # 后续双人复核义务：进入重验证的推进必须由包参与者（非安全管理员）完成。
        response = maintainer_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/advance",
            json={"to_stage": "revalidating"},
        )
        assert response.status_code == 200, response.text

        # 重验证未完成前不能关闭。
        response = secadmin_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/advance",
            json={"to_stage": "closed"},
        )
        assert response.status_code == 409, response.text

        # 逐对象登记重验证推进。
        for run_id in (run_a["run_id"], run_b["run_id"]):
            response = secadmin_client.post(
                f"/domain-packs/invalidations/{event['event_id']}/revalidate",
                json={"area": "run", "ref_ids": [run_id]},
            )
            assert response.status_code == 200, response.text
        report = secadmin_client.get(
            f"/domain-packs/invalidations/{event['event_id']}/revalidation"
        ).json()
        assert report["status"] == "completed"

        response = secadmin_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/advance",
            json={"to_stage": "closed"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "closed"

        # 撤销后的包版本不能用于新运行，也不能通过回滚复活（无受信旧版）。
        response = maintainer_client.post(
            "/domain-packs/rollbacks",
            json={
                "pack_id": PACK_ID,
                "from_version": PACK_VERSION,
                "reason": "尝试回滚",
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"] == "no_trusted_rollback_target"
        response = maintainer_client.post(
            f"/projects/{project_b['id']}/work-orders",
            json={
                "workflow_name": "generic_science_task",
                "workflow_version": "1",
                "project_id": project_b["id"],
                "objective": "撤销后再次提交",
                "success_criteria": "应被拒绝",
                "risk_statement": "无",
                "domain_pack_refs": [f"{PACK_ID}@{PACK_VERSION}"],
            },
        )
        assert response.status_code == 400, response.text

    def test_revoke_requires_security_admin_and_second_factor(
        self,
        client: TestClient,
        registered_user: Any,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
    ) -> None:
        _release_pack(maintainer, reviewer, releaser)
        maintainer_client, _ = maintainer

        # 非安全管理员不能紧急撤销。
        response = maintainer_client.post(
            "/domain-packs/revocations",
            json={
                "pack_id": PACK_ID,
                "version": PACK_VERSION,
                "trigger": "security_event",
                "reason": "越权撤销",
                "second_factor": "totp-123456",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_required"

        # 安全管理员缺少二次认证时被拒绝。
        secadmin_client = TestClient(client.app)
        registered_user(secadmin_client, "lfc-secadmin-2", "200005@qq.com", "correct-horse-12")
        response = secadmin_client.post("/domain-packs/security-admins")
        assert response.status_code == 204, response.text
        response = secadmin_client.post(
            "/domain-packs/revocations",
            json={
                "pack_id": PACK_ID,
                "version": PACK_VERSION,
                "trigger": "security_event",
                "reason": "缺少二次认证",
                "second_factor": "   ",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "second_factor_required"

    def test_invalidation_event_progression_gates(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
    ) -> None:
        """失效事件按检测→分诊→控制→定位影响→修复→重验证→关闭推进。"""
        _release_pack(maintainer, reviewer, releaser)
        maintainer_client, _ = maintainer

        response = maintainer_client.post(
            "/domain-packs/invalidations",
            json={
                "pack_id": PACK_ID,
                "version": PACK_VERSION,
                "trigger": "source_retracted",
                "reason": "权威来源撤回",
            },
        )
        assert response.status_code == 201, response.text
        event = response.json()
        assert event["stage"] == "detected"

        # 未生成影响集前不能定位影响。
        response = maintainer_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/advance",
            json={"to_stage": "impacted_objects_found"},
        )
        assert response.status_code == 409, response.text

        # 影响集生成后按序推进到关闭。
        response = maintainer_client.post(
            f"/domain-packs/invalidations/{event['event_id']}/resolve-impact"
        )
        assert response.status_code == 200, response.text
        for stage in (
            "triaged",
            "contained",
            "impacted_objects_found",
            "remediating",
            "revalidating",
            "closed",
        ):
            response = maintainer_client.post(
                f"/domain-packs/invalidations/{event['event_id']}/advance",
                json={"to_stage": stage},
            )
            assert response.status_code == 200, response.text
        assert response.json()["closed_at"] is not None

    def test_outsider_cannot_view_invalidation(
        self,
        client: TestClient,
        registered_user: Any,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
    ) -> None:
        """普通用户不进入失效处置工作台。"""
        _release_pack(maintainer, reviewer, releaser)
        maintainer_client, _ = maintainer
        response = maintainer_client.post(
            "/domain-packs/invalidations",
            json={
                "pack_id": PACK_ID,
                "version": PACK_VERSION,
                "trigger": "evaluation_regression",
                "reason": "评测回归",
            },
        )
        assert response.status_code == 201, response.text
        event_id = response.json()["event_id"]

        outsider_client = TestClient(client.app)
        registered_user(
            outsider_client, "lfc-outsider", "200006@qq.com", "correct-horse-12"
        )
        response = outsider_client.get(f"/domain-packs/invalidations/{event_id}")
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_required"
        response = outsider_client.get("/domain-packs/invalidations")
        assert response.status_code == 200, response.text
        assert response.json() == []  # 非参与者看不到任何失效事件
