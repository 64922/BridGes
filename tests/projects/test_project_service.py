"""Module-interface tests for the project service.

The seam under test: an authenticated subject can create, list, get, rename,
and archive scientific project spaces; ownership, object domain, role, and
version are always present and correct.
"""

import pytest

from bridges.contracts.projects import (
    ObjectDomain,
    ProjectCreateRequest,
    ProjectRole,
    ProjectStatus,
    ProjectUpdateRequest,
)
from bridges.projects import ProjectError, ProjectService


@pytest.fixture
def service() -> ProjectService:
    return ProjectService()


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def test_create_project_returns_owned_projection(service: ProjectService, alice_id: str) -> None:
    request = ProjectCreateRequest(name="量子纠缠科普", description="为大学生解释贝尔不等式")
    project = service.create_project(account_id=alice_id, request=request)

    assert project.name == "量子纠缠科普"
    assert project.account_id == alice_id
    assert project.object_domain == ObjectDomain.PERSONAL_VAULT
    assert project.role == ProjectRole.OWNER
    assert project.status == ProjectStatus.ACTIVE
    assert project.version == 1
    assert project.description == "为大学生解释贝尔不等式"


def test_list_projects_includes_only_own_projects(service: ProjectService, alice_id: str, bob_id: str) -> None:
    alice_project = service.create_project(account_id=alice_id, request=ProjectCreateRequest(name="Alice 项目"))
    service.create_project(account_id=bob_id, request=ProjectCreateRequest(name="Bob 项目"))

    projection = service.list_projects(account_id=alice_id)

    assert len(projection.active) == 1
    assert projection.active[0].name == "Alice 项目"
    assert projection.active[0].ref.object_id == alice_project.id
    assert projection.active[0].ref.domain == ObjectDomain.PERSONAL_VAULT
    assert projection.active[0].ref.owner_id == alice_id


def test_get_project_returns_same_projection(service: ProjectService, alice_id: str) -> None:
    created = service.create_project(account_id=alice_id, request=ProjectCreateRequest(name="可重复项目"))

    fetched = service.get_project(account_id=alice_id, project_id=created.id)

    assert fetched.id == created.id
    assert fetched.name == created.name
    assert fetched.account_id == created.account_id
    assert fetched.object_domain == ObjectDomain.PERSONAL_VAULT
    assert fetched.role == ProjectRole.OWNER


def test_rename_project_bumps_version(service: ProjectService, alice_id: str) -> None:
    created = service.create_project(account_id=alice_id, request=ProjectCreateRequest(name="旧名称"))
    original_version = created.version

    updated = service.update_project(
        account_id=alice_id,
        project_id=created.id,
        request=ProjectUpdateRequest(name="新名称"),
    )

    assert updated.name == "新名称"
    assert updated.version == original_version + 1


def test_archive_project_moves_to_archived_list(service: ProjectService, alice_id: str) -> None:
    created = service.create_project(account_id=alice_id, request=ProjectCreateRequest(name="归档项目"))

    archived = service.archive_project(account_id=alice_id, project_id=created.id)
    assert archived.status == ProjectStatus.ARCHIVED
    assert archived.archived_at is not None

    projection = service.list_projects(account_id=alice_id)
    assert len(projection.active) == 0
    assert len(projection.archived) == 1
    assert projection.archived[0].name == "归档项目"


def test_get_unknown_project_raises_not_found(service: ProjectService, alice_id: str) -> None:
    with pytest.raises(ProjectError, match="项目不存在"):
        service.get_project(account_id=alice_id, project_id="missing-id")


def test_cross_account_get_is_rejected(service: ProjectService, alice_id: str, bob_id: str) -> None:
    created = service.create_project(account_id=alice_id, request=ProjectCreateRequest(name="私有项目"))

    with pytest.raises(ProjectError, match="项目不存在"):
        service.get_project(account_id=bob_id, project_id=created.id)


def test_update_unknown_project_raises_not_found(service: ProjectService, alice_id: str) -> None:
    with pytest.raises(ProjectError, match="项目不存在"):
        service.update_project(
            account_id=alice_id,
            project_id="missing-id",
            request=ProjectUpdateRequest(name="新名称"),
        )
