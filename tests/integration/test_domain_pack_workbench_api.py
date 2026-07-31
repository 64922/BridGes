"""T046 领域包专家工作台 API 的集成测试。

建议接缝：维护者、独立复核者和发行者分别完成同一包版本的编辑、语义
Diff、夹具、签名和灰度，职责冲突被拒绝。
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
    maintainer_client = TestClient(client.app)
    account = registered_user(
        maintainer_client, "wb-maintainer@example.com", "correct-horse-12"
    )
    return maintainer_client, cast(dict[str, Any], account["account"])


@pytest.fixture
def reviewer(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    reviewer_client = TestClient(client.app)
    account = registered_user(
        reviewer_client, "wb-reviewer@example.com", "correct-horse-12"
    )
    return reviewer_client, cast(dict[str, Any], account["account"])


@pytest.fixture
def releaser(
    client: TestClient, registered_user: Any
) -> tuple[TestClient, dict[str, Any]]:
    releaser_client = TestClient(client.app)
    account = registered_user(
        releaser_client, "wb-releaser@example.com", "correct-horse-12"
    )
    return releaser_client, cast(dict[str, Any], account["account"])


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


def _declare_conflicts(
    clients: list[tuple[TestClient, dict[str, Any]]],
) -> None:
    for test_client, _ in clients:
        response = test_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/conflict-of-interest",
            json={"disclosures": ["无相关利益"]},
        )
        assert response.status_code == 200, response.text


def _qualify_reviewer(
    reviewer: tuple[TestClient, dict[str, Any]],
) -> None:
    reviewer_client, account = reviewer
    response = reviewer_client.post(
        "/domain-packs/qualifications",
        json={
            "qualification_id": "q-integration-reviewer",
            "person_id": account["id"],
            "qualification_type": "formal_proof",
            "verifier_id": "verifier",
            "disciplines": ["mathematics", "formal_logic", "theorem_proving"],
            "verified_at": "2026-07-31T00:00:00Z",
            "valid_until": "2027-07-31T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text


class TestWorkbenchLifecycle:
    def test_three_signatures_and_gray_release(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
    ) -> None:
        maintainer_client, maintainer_account = maintainer
        reviewer_client, reviewer_account = reviewer
        releaser_client, releaser_account = releaser

        record = _register_pack(maintainer)
        assert record["stage"] == "drafting"
        assert record["lifecycle_status"] == "draft"

        _qualify_reviewer(reviewer)
        _declare_conflicts([maintainer, reviewer, releaser])

        # 内容签名
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/content-signature",
            json={"opinion": "内容完整", "conclusion": "approve"},
        )
        assert response.status_code == 200, response.text
        content_attestation = response.json()
        assert content_attestation["role"] == "content"
        assert content_attestation["person_id"] == maintainer_account["id"]

        # 分配复核者与发行者
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/reviewer",
            json={"person_id": reviewer_account["id"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["reviewer_id"] == reviewer_account["id"]
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/releaser",
            json={"person_id": releaser_account["id"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["releaser_id"] == releaser_account["id"]

        # 独立验证签名
        response = reviewer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/independent-signature",
            json={"opinion": "独立复核通过", "conclusion": "approve"},
        )
        assert response.status_code == 200, response.text
        independent_attestation = response.json()
        assert independent_attestation["role"] == "independent"
        assert independent_attestation["person_id"] == reviewer_account["id"]

        # 语义 Diff 可见
        response = maintainer_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/semantic-diff"
        )
        assert response.status_code == 200, response.text
        diff = response.json()
        assert diff["pack_id"] == PACK_ID
        assert diff["digest"]

        # 灰度生成可发行候选，不自动激活
        response = reviewer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/gray-release"
        )
        assert response.status_code == 200, response.text
        candidate = response.json()
        assert candidate["status"] == "ready_to_release"
        assert candidate["canonical_digest"] == content_attestation["canonical_digest"]
        record = maintainer_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}"
        ).json()
        assert record["stage"] == "gray_release_ready"
        assert record["lifecycle_status"] == "draft"  # 灰度不激活

        # 维护者不能追加发行签名
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/release"
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_required"

        # 发行者验证三签与灰度门后发行并激活
        response = releaser_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/release"
        )
        assert response.status_code == 200, response.text
        release = response.json()
        assert len(release["attestations"]) == 3
        assert all(
            item["canonical_digest"] == release["canonical_digest"]
            for item in release["attestations"]
        )
        assert release["platform_attestation"]["person_id"] == releaser_account["id"]

        record = maintainer_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}"
        ).json()
        assert record["stage"] == "released"
        assert record["lifecycle_status"] == "active"

    def test_same_person_cannot_be_maintainer_and_reviewer(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
    ) -> None:
        maintainer_client, maintainer_account = maintainer
        _register_pack(maintainer)
        _declare_conflicts([maintainer])
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/content-signature",
            json={"opinion": "内容完整"},
        )
        assert response.status_code == 200, response.text

        # 维护者把自己分配为独立复核者被拒绝
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/reviewer",
            json={"person_id": maintainer_account["id"]},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_conflict"

        # 维护者直接提交独立验证签名被拒绝
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/independent-signature",
            json={"opinion": "冒充复核"},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_conflict"

    def test_signature_requires_conflict_declaration(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
    ) -> None:
        maintainer_client, _ = maintainer
        _register_pack(maintainer)
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/content-signature",
            json={"opinion": "未声明利益冲突"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"] == "conflict_required"

    def test_release_requires_all_three_signatures(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
        reviewer: tuple[TestClient, dict[str, Any]],
        releaser: tuple[TestClient, dict[str, Any]],
    ) -> None:
        maintainer_client, _ = maintainer
        reviewer_client, reviewer_account = reviewer
        releaser_client, _ = releaser

        _register_pack(maintainer)
        _qualify_reviewer(reviewer)
        _declare_conflicts([maintainer, reviewer, releaser])
        maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/content-signature",
            json={"opinion": "内容完整"},
        )
        maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/reviewer",
            json={"person_id": reviewer_account["id"]},
        )
        maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/releaser",
            json={"person_id": releaser[1]["id"]},
        )

        # 只有内容签名时灰度被拒绝
        response = reviewer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/gray-release"
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"] == "signature_missing"

    def test_non_participant_cannot_view_workbench_record(
        self,
        client: TestClient,
        registered_user: Any,
        maintainer: tuple[TestClient, dict[str, Any]],
    ) -> None:
        """普通用户不进入本工作台：非参与者查看治理记录被拒绝。"""
        outsider_client = TestClient(client.app)
        registered_user(outsider_client, "wb-outsider@example.com", "correct-horse-12")
        _register_pack(maintainer)
        response = outsider_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}"
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_required"
        response = outsider_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/semantic-diff"
        )
        assert response.status_code == 403, response.text
        response = outsider_client.get("/domain-packs/workbench")
        assert response.status_code == 200, response.text
        assert response.json() == []  # 非参与者看不到任何记录

    def test_cannot_register_qualification_for_another_person(
        self,
        reviewer: tuple[TestClient, dict[str, Any]],
    ) -> None:
        """只能为当前账户登记资质，不能为他人越权登记。"""
        reviewer_client, account = reviewer
        response = reviewer_client.post(
            "/domain-packs/qualifications",
            json={
                "qualification_id": "q-forged",
                "person_id": "someone-else",
                "qualification_type": "formal_proof",
                "verifier_id": "verifier",
                "disciplines": ["mathematics"],
                "verified_at": "2026-07-31T00:00:00Z",
                "valid_until": "2027-07-31T00:00:00Z",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "role_required"

    def test_conflict_disclosure_route(
        self,
        maintainer: tuple[TestClient, dict[str, Any]],
    ) -> None:
        """参与者登记少数意见并逐版本保存。"""
        maintainer_client, _ = maintainer
        _register_pack(maintainer)
        response = maintainer_client.post(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}/conflict-disclosure",
            json={
                "item_ref": "rule.definition",
                "minority_opinion": "该规则措辞过强",
                "basis": ["来源未覆盖该结论"],
            },
        )
        assert response.status_code == 200, response.text
        disclosure = response.json()
        assert disclosure["item_ref"] == "rule.definition"
        record = maintainer_client.get(
            f"/domain-packs/workbench/{PACK_ID}/{PACK_VERSION}"
        ).json()
        assert any(
            item["disclosure_id"] == disclosure["disclosure_id"]
            for item in record["disclosures"]
        )
