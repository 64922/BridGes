"""Issue 14: 四维画像合同、确定性迁移与账户隔离。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bridges.contracts.profiles import (
    AssertionStatus,
    FourDimension,
    FourDimensionProfileModifyRequest,
    FourDimensionRecordStatus,
    ProfileAssertion,
    ProfileSensitivityClass,
)
from bridges.profiles import (
    FourDimensionContractGate,
    FourDimensionMigrationGateError,
    FourDimensionProfileError,
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
    InMemoryProfileRepository,
    ProfileService,
)
from bridges.api.main import create_app


@pytest.fixture
def old_repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def old_service(old_repository: InMemoryProfileRepository) -> ProfileService:
    return ProfileService(repository=old_repository)


@pytest.fixture
def repository() -> InMemoryFourDimensionProfileRepository:
    return InMemoryFourDimensionProfileRepository()


@pytest.fixture
def service(
    old_repository: InMemoryProfileRepository,
    repository: InMemoryFourDimensionProfileRepository,
) -> FourDimensionProfileService:
    return FourDimensionProfileService(
        source_repository=old_repository,
        repository=repository,
    )


def _old_assertion(
    account_id: str,
    assertion_id: str,
    dimension: str,
    value: str,
    *,
    status: AssertionStatus = AssertionStatus.ACTIVE,
) -> ProfileAssertion:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    return ProfileAssertion(
        assertion_id=assertion_id,
        owner_account_id=account_id,
        canonical_dimension=dimension,
        value_or_rule=value,
        authorization_scope="general",
        status=status,
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        version=1,
        created_at=now,
        updated_at=now,
    )


def test_four_dimension_contract_has_stable_labels() -> None:
    assert list(FourDimension) == [
        FourDimension.ACADEMIC_STATUS,
        FourDimension.KNOWLEDGE_INTEREST,
        FourDimension.HOBBY,
        FourDimension.STAGE_GOAL,
    ]
    assert FourDimension.ACADEMIC_STATUS.label == "学业情况"
    assert FourDimension.KNOWLEDGE_INTEREST.label == "感兴趣的知识"
    assert FourDimension.HOBBY.label == "兴趣爱好"
    assert FourDimension.STAGE_GOAL.label == "阶段目标"


def test_migration_is_deterministic_and_keeps_knowledge_out_of_profile(
    old_repository: InMemoryProfileRepository,
    service: FourDimensionProfileService,
) -> None:
    account_id = "account-alice"
    old_repository.save_assertion(
        _old_assertion(account_id, "old-goal", "stage_goal", "完成物理实验报告")
    )
    old_repository.save_assertion(
        _old_assertion(account_id, "old-school", "basic_information", "我现在读高中")
    )
    old_repository.save_assertion(
        _old_assertion(account_id, "old-topic", "interest_preference", "我对物理很感兴趣")
    )
    old_repository.save_assertion(
        _old_assertion(account_id, "old-hobby", "interest_preference", "我喜欢跑步")
    )
    old_repository.save_assertion(
        _old_assertion(account_id, "old-knowledge", "knowledge_state", "已经掌握光合作用")
    )
    old_repository.save_assertion(
        _old_assertion(account_id, "old-ambiguous", "interest_preference", "我喜欢学习和旅行")
    )

    first = service.migrate_account(account_id)
    records = service.list_records(account_id)
    learning_records = service.list_learning_records(account_id)
    legacy_records = service.list_legacy_records(account_id)

    assert first.four_dimension_migrated == 4
    assert first.teaching_records_migrated == 1
    assert first.legacy_preserved == 1
    assert first.failed == 0
    assert {record.dimension for record in records} == set(FourDimension)
    assert all(record.status == FourDimensionRecordStatus.ACTIVE for record in records)
    assert len(learning_records) == 1
    assert learning_records[0].source_record_id == "old-knowledge"
    assert {record.source_record_id for record in legacy_records} == {"old-ambiguous"}

    first_ids = [record.record_id for record in records]
    first_hashes = [record.content_hash for record in records]
    second = service.migrate_account(account_id)

    assert second.four_dimension_migrated == 0
    assert len(service.list_records(account_id)) == len(records)
    assert [record.record_id for record in service.list_records(account_id)] == first_ids
    assert [record.content_hash for record in service.list_records(account_id)] == first_hashes


def test_user_edit_and_withdraw_win_over_a_later_migration(
    old_repository: InMemoryProfileRepository,
    old_service: ProfileService,
    service: FourDimensionProfileService,
) -> None:
    account_id = "account-alice"
    old = _old_assertion(account_id, "old-goal", "stage_goal", "完成第一版报告")
    old_repository.save_assertion(old)
    service.migrate_account(account_id)
    record = service.list_records(account_id)[0]

    edited = service.modify_record(
        account_id,
        record.record_id,
        FourDimensionProfileModifyRequest(
            content="完成第二版报告",
            version=record.version,
        ),
    )
    old_service.modify_assertion(
        account_id,
        old.assertion_id,
        "完成第三版报告",
        [],
        "旧记录变更",
    )

    rerun = service.migrate_account(account_id)
    current = service.get_record(account_id, edited.record_id)

    assert rerun.four_dimension_migrated == 0
    assert current.content == "完成第二版报告"
    assert current.version == edited.version

    withdrawn = service.withdraw_record(account_id, current.record_id, current.version)
    assert withdrawn.status == FourDimensionRecordStatus.WITHDRAWN
    with pytest.raises(FourDimensionProfileError, match="已撤回"):
        service.modify_record(
            account_id,
            current.record_id,
            FourDimensionProfileModifyRequest(content="覆盖", version=current.version),
        )


def test_stale_version_and_cross_account_access_are_stable_conflicts(
    old_repository: InMemoryProfileRepository,
    service: FourDimensionProfileService,
) -> None:
    old_repository.save_assertion(
        _old_assertion("account-alice", "old-goal", "stage_goal", "完成实验")
    )
    service.migrate_account("account-alice")
    record = service.list_records("account-alice")[0]

    with pytest.raises(FourDimensionProfileError, match="对象不存在"):
        service.get_record("account-bob", record.record_id)
    with pytest.raises(FourDimensionProfileError, match="版本冲突"):
        service.modify_record(
            "account-alice",
            record.record_id,
            FourDimensionProfileModifyRequest(content="过期写入", version=99),
        )


def test_invalid_migration_rolls_back_target_records(
    old_repository: InMemoryProfileRepository,
    service: FourDimensionProfileService,
) -> None:
    old_repository.save_assertion(
        _old_assertion("account-alice", "old-good", "stage_goal", "有效目标")
    )
    old_repository.save_assertion(
        _old_assertion("account-alice", "old-bad", "stage_goal", "")
    )

    report = service.migrate_account("account-alice")

    assert report.status == "retryable"
    assert report.failed == 1
    assert service.list_records("account-alice") == []


def test_four_dimension_slice_reads_current_records_and_respects_withdrawal(
    old_repository: InMemoryProfileRepository,
    service: FourDimensionProfileService,
) -> None:
    account_id = "account-alice"
    old_repository.save_assertion(
        _old_assertion(account_id, "old-goal", "stage_goal", "当前学习目标")
    )
    service.migrate_account(account_id)

    first = service.compile_chat_slice(
        account_id, mode="companion", run_id="run-1"
    )
    assert [item.value_or_rule for item in first.included_items] == ["当前学习目标"]

    record = service.list_records(account_id)[0]
    edited = service.modify_record(
        account_id,
        record.record_id,
        FourDimensionProfileModifyRequest(content="修改后的目标", version=record.version),
    )
    second = service.compile_chat_slice(
        account_id, mode="companion", run_id="run-2"
    )
    assert [item.value_or_rule for item in second.included_items] == ["修改后的目标"]

    service.withdraw_record(account_id, edited.record_id, edited.version)
    third = service.compile_chat_slice(
        account_id, mode="companion", run_id="run-3"
    )
    assert third.included_items == []


def test_contract_gate_blocks_old_writers_and_is_idempotent(
    old_repository: InMemoryProfileRepository,
    service: FourDimensionProfileService,
) -> None:
    account_id = "account-alice"
    old_repository.save_assertion(
        _old_assertion(account_id, "old-goal", "stage_goal", "当前学习目标")
    )
    gate = FourDimensionContractGate(service)

    blocked = gate.preflight(
        account_id,
        legacy_write_callers=["legacy-chat-writer"],
        backup_id="backup-before-check",
    )
    assert blocked.can_contract is False
    assert blocked.legacy_write_callers == ("legacy-chat-writer",)

    with pytest.raises(FourDimensionMigrationGateError, match="旧画像写入调用方"):
        gate.contract(
            account_id,
            backup=lambda: "backup-should-not-be-created",
            legacy_write_callers=["legacy-chat-writer"],
        )

    calls = 0

    def backup() -> str:
        nonlocal calls
        calls += 1
        return "backup-16"

    result = gate.contract(account_id, backup=backup)
    repeated = gate.contract(account_id, backup=backup)
    assert result == repeated
    assert calls == 1
    assert gate.legacy_writes_stopped is True


def test_api_exposes_read_modify_withdraw_but_not_manual_create() -> None:
    app = create_app()
    routes = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    assert ("/profiles/four-dimensions", "GET") in routes
    assert ("/profiles/four-dimensions/{record_id}", "PATCH") in routes
    assert ("/profiles/four-dimensions/{record_id}/withdraw", "POST") in routes
    assert ("/profiles/four-dimensions", "POST") not in routes
    public_fields = set(
        app.openapi()["components"]["schemas"]["FourDimensionProfileProjection"][
            "properties"
        ]
    )
    assert public_fields == {
        "record_id",
        "dimension",
        "label",
        "content",
        "first_stable_recorded_at",
        "version",
        "status",
    }
