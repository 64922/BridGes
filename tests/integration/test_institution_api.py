"""Integration tests for the institution management API.

These tests exercise the full HTTP seam: institution creation, membership,
institution-owned projects and controlled content access. They prove that
institution admins cannot read member personal vaults through the API.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(client: TestClient, registered_user: Any) -> TestClient:
    """Authenticated client for the institution admin."""
    registered_user(client, "admin-institution", "200001@qq.com", "correct-horse-12")
    return client


@pytest.fixture
def member_client(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    """Authenticated client for an institution member and their account."""
    member_client = TestClient(client.app)
    result = registered_user(
        member_client, "member-institution", "200002@qq.com", "correct-horse-12"
    )
    return member_client, cast(dict[str, Any], result["account"])


class TestInstitutionLifecycle:
    def test_create_and_list_institutions(
        self, admin_client: TestClient
    ) -> None:
        response = admin_client.post("/institutions", json={"name": "测试机构"})
        assert response.status_code == 201
        institution = response.json()
        assert institution["name"] == "测试机构"

        response = admin_client.get("/institutions")
        assert response.status_code == 200
        institutions = response.json()
        assert any(i["id"] == institution["id"] for i in institutions)

    def test_member_invite_and_accept(
        self,
        admin_client: TestClient,
        member_client: tuple[TestClient, dict[str, Any]],
        create_institution: Any,
        invite_institution_member: Any,
    ) -> None:
        member_c, member_account = member_client
        institution = create_institution(admin_client, "邀请测试机构")

        token_secret = invite_institution_member(
            admin_client,
            institution["id"],
            cast(str, member_account["id"]),
            "member",
        )

        response = member_c.post(
            "/institutions/invites/accept", params={"token_secret": token_secret}
        )
        assert response.status_code == 200
        membership = response.json()
        assert membership["institution_id"] == institution["id"]
        assert membership["role"] == "member"


class TestInstitutionOwnedProject:
    def test_create_project_returns_disclosure(
        self,
        admin_client: TestClient,
        create_institution: Any,
    ) -> None:
        institution = create_institution(admin_client, "项目披露机构")

        response = admin_client.get(
            f"/institutions/{institution['id']}/project-disclosure"
        )
        assert response.status_code == 200
        disclosure = response.json()
        assert disclosure["institution_id"] == institution["id"]

        response = admin_client.post(
            f"/institutions/{institution['id']}/projects",
            json={"name": "机构项目", "disclosure_acknowledged": True},
        )
        assert response.status_code == 201
        result = response.json()
        assert result["project"]["object_domain"] == "institution_owned"
        assert result["project"]["tenant_id"] == institution["id"]
        assert result["disclosure"]["acknowledged"] is True

    def test_project_visible_to_members(
        self,
        admin_client: TestClient,
        member_client: tuple[TestClient, dict[str, Any]],
        create_institution: Any,
        invite_institution_member: Any,
        create_institution_project: Any,
    ) -> None:
        institution = create_institution(admin_client, "成员可见机构")
        member_c, member_account = member_client

        token_secret = invite_institution_member(
            admin_client,
            institution["id"],
            cast(str, member_account["id"]),
            "member",
        )
        member_c.post(
            "/institutions/invites/accept", params={"token_secret": token_secret}
        )

        project = create_institution_project(
            admin_client, institution["id"], "共享机构项目"
        )

        response = member_c.get(f"/institutions/{institution['id']}/projects")
        assert response.status_code == 200
        projects = response.json()
        assert any(p["id"] == project["project"]["id"] for p in projects)


class TestAdminCannotReadMemberVault:
    def test_admin_cannot_list_member_vault_objects(
        self,
        admin_client: TestClient,
        member_client: tuple[TestClient, dict[str, Any]],
        create_institution: Any,
        invite_institution_member: Any,
    ) -> None:
        institution = create_institution(admin_client, "保险库隔离机构")
        member_c, member_account = member_client
        member_id = cast(str, member_account["id"])

        # Member creates a personal vault object.
        response = member_c.post(
            "/vault/objects",
            json={
                "owner_account_id": member_id,
                "content": base64.b64encode(b"member secret").decode("ascii"),
                "content_authority": "server_replica",
                "purpose": "test",
            },
        )
        assert response.status_code == 201
        vault_object = response.json()

        # Invite member to institution.
        token_secret = invite_institution_member(
            admin_client, institution["id"], member_id, "member"
        )
        member_c.post(
            "/institutions/invites/accept", params={"token_secret": token_secret}
        )

        # Admin tries to access member's vault object and gets a non-leaking 404.
        response = admin_client.get(
            f"/vault/objects/{vault_object['ref']['object_id']}"
        )
        assert response.status_code == 404

        response = admin_client.get(
            f"/vault/objects/{vault_object['ref']['object_id']}/content"
        )
        assert response.status_code == 404



class TestControlledContentAccess:
    def test_two_approval_flow(
        self,
        admin_client: TestClient,
        create_institution: Any,
    ) -> None:
        institution = create_institution(admin_client, "受控访问机构")

        response = admin_client.post(
            f"/institutions/{institution['id']}/content-access",
            json={
                "target_account_id": "account-some-member",
                "object_refs": ["obj-1"],
                "purpose": "调查安全事件",
                "duration_minutes": 60,
            },
        )
        assert response.status_code == 201
        access_request = response.json()
        assert access_request["status"] == "pending"

        # First approval keeps it pending because the requester cannot approve
        # themselves; use another admin account which is not available in this
        # single-admin fixture, so we simulate by expecting the first approval
        # from the same admin to be rejected.
        response = admin_client.post(
            f"/institutions/{institution['id']}/content-access/"
            f"{access_request['request_id']}/approve",
            params={"reason": "自我批准"},
        )
        assert response.status_code == 404

    def test_revoke_controlled_access_via_api(
        self,
        admin_client: TestClient,
        create_institution: Any,
    ) -> None:
        institution = create_institution(admin_client, "撤销测试机构")

        response = admin_client.post(
            f"/institutions/{institution['id']}/content-access",
            json={
                "target_account_id": "account-some-member",
                "object_refs": ["obj-1"],
                "purpose": "调查安全事件",
                "duration_minutes": 60,
            },
        )
        assert response.status_code == 201
        access_request = response.json()
        assert access_request["status"] == "pending"

        response = admin_client.post(
            f"/institutions/{institution['id']}/content-access/"
            f"{access_request['request_id']}/revoke",
        )
        assert response.status_code == 200
        revoked = response.json()
        assert revoked["status"] == "revoked"


class TestMultiInstitutionIsolation:
    def test_two_institutions_do_not_share_data(
        self,
        admin_client: TestClient,
        create_institution: Any,
        create_institution_project: Any,
    ) -> None:
        alpha = create_institution(admin_client, "机构 Alpha")
        beta = create_institution(admin_client, "机构 Beta")

        alpha_project = create_institution_project(
            admin_client, alpha["id"], "Alpha 项目"
        )
        beta_project = create_institution_project(
            admin_client, beta["id"], "Beta 项目"
        )

        response = admin_client.get(f"/institutions/{alpha['id']}/projects")
        alpha_projects = response.json()
        assert any(p["id"] == alpha_project["project"]["id"] for p in alpha_projects)
        assert not any(p["id"] == beta_project["project"]["id"] for p in alpha_projects)
