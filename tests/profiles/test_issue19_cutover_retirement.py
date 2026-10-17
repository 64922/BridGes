"""画像工单 19：四维路径退出与旧队列/重试合同测试。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bridges.contracts.atomic_profile import AtomicProfileFactRelation
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.profiles.atomic import (
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.automatic import AutomaticProfileService, InMemoryAutomaticProfileRepository
from bridges.profiles.cutover import audit_profile_retirement
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from tests.profiles.test_issue16_history_preview import (
    ACCOUNT,
    _open_database,
    _seed_account,
    _seed_item,
)


def _output(items: list[dict[str, Any]]) -> ProfileExtractionOutput:
    return ProfileExtractionOutput.model_validate({"items": items})


class _FixedExtractor:
    def __init__(self, version: str = "issue19-fixed-v1") -> None:
        self.version = version
        self.calls = 0

    def extract(
        self,
        *,
        message_id: str,
        content: str,
        **_ignored: object,
    ) -> ProfileExtractionOutput:
        self.calls += 1
        if "猫" in content:
            value = "养了一只猫叫团子"
        elif "跑步" in content:
            value = "喜欢跑步"
        else:
            return _output([])
        return _output(
            [
                {
                    "dimension": None,
                    "normalized_value": value,
                    "evidence_ref": message_id,
                    "reliability": 0.9,
                    "action": "create",
                }
            ]
        )


def _services(
    extractor: _FixedExtractor,
    *,
    atomic: AtomicProfileService | None = None,
    dimensions: FourDimensionProfileService | None = None,
    repository: InMemoryAutomaticProfileRepository | None = None,
) -> tuple[FourDimensionProfileService, AutomaticProfileService, AtomicProfileService]:
    dimension_service = dimensions or FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    atomic_service = atomic or AtomicProfileService(
        dimension_service, InMemoryAtomicProfileRepository()
    )
    return (
        dimension_service,
        AutomaticProfileService(
            four_dimension_service=dimension_service,
            repository=repository or InMemoryAutomaticProfileRepository(),
            extractor=extractor,
            atomic_profile_service=atomic_service,
        ),
        atomic_service,
    )


def test_active_sources_have_no_four_dimension_write_or_old_frontend_path() -> None:
    repo = Path(__file__).resolve().parents[2]
    automatic = (repo / "src" / "bridges" / "profiles" / "automatic.py").read_text(encoding="utf-8")
    calls = set(re.findall(r"self\._four_dimensions\.([A-Za-z_]+)\(", automatic))
    assert calls <= {"list_records", "get_record"}

    for relative in (
        "src/bridges/profiles/automatic.py",
        "src/bridges/profiles/atomic.py",
        "src/bridges/profiles/commit.py",
    ):
        source = (repo / relative).read_text(encoding="utf-8")
        assert "INSERT INTO profile_four_dimension_records" not in source
        assert "UPDATE profile_four_dimension_records" not in source
        assert "DELETE FROM profile_four_dimension_records" not in source

    assert not (
        repo / "apps/web/src/components/account/profile/FourDimensionProfileCenter.tsx"
    ).exists()
    profile_client = (repo / "apps/web/src/lib/api/profile.ts").read_text(encoding="utf-8")
    assert "four-dimensions" not in profile_client
    page = (repo / "apps/web/src/app/(app)/account/profile/page.tsx").read_text(encoding="utf-8")
    assert "AtomicProfileCenter" in page
    assert "FourDimensionProfileCenter" not in page


def test_retirement_audit_flags_tombstone_body(tmp_path: Path) -> None:
    database_path, database = _open_database(tmp_path)
    _seed_account(database, ACCOUNT)
    _seed_item(database, ACCOUNT, "item-active", "我喜欢跑步")
    _seed_item(
        database,
        ACCOUNT,
        "item-tombstone",
        "",
        status="withdrawn",
        stored_identity_key="withdrawn-identity",
    )
    audit = audit_profile_retirement(database_path, account_id=ACCOUNT)
    assert audit.passed
    assert audit.counts["atomic_items"] == 2
    assert audit.counts["legacy_records"] == 0

    with database.transaction():
        database.connection.execute(
            "UPDATE profile_items SET text = '不应保留的墓碑正文' "
            "WHERE account_id = ? AND profile_item_id = 'item-tombstone'",
            (ACCOUNT,),
        )
    failed = audit_profile_retirement(database_path, account_id=ACCOUNT)
    assert not failed.passed
    assert {check.check_id for check in failed.checks if not check.passed} == {
        "withdrawn_items_have_no_body"
    }
    database.close()


def test_old_queues_and_tombstones_do_not_revive_facts() -> None:
    old_extractor = _FixedExtractor("issue19-old-v1")
    repository = InMemoryAutomaticProfileRepository()
    dimensions, old_service, atomic = _services(old_extractor, repository=repository)
    old_service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-old",
        content="我养了一只猫叫团子",
        run_id="run-old",
    )

    new_extractor = _FixedExtractor("issue19-new-v1")
    _, new_service, _ = _services(
        new_extractor, atomic=atomic, dimensions=dimensions, repository=repository
    )
    new_service.run_retry_tick()
    assert new_extractor.calls == 0
    assert atomic.list_items(ACCOUNT) == []
    assert dimensions.list_records(ACCOUNT) == []


def test_retry_after_tombstone_writes_nothing_and_open_fact_stays_atomic() -> None:
    extractor = _FixedExtractor()
    dimensions, service, atomic = _services(extractor)
    service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-deleted",
        content="我养了一只猫叫团子",
        run_id="run-deleted",
    )
    service.mark_message_tombstone(ACCOUNT, "message-deleted")
    service.run_retry_tick()
    assert extractor.calls == 0
    assert atomic.list_items(ACCOUNT) == []
    assert dimensions.list_records(ACCOUNT) == []
    assert {task.status for task in service.list_retry_tasks(ACCOUNT)} == {
        ProfileExtractionStatus.EXHAUSTED
    }

    service.schedule_message_extraction(
        ACCOUNT,
        conversation_id="conversation-1",
        message_id="message-open",
        content="我喜欢跑步",
        run_id="run-open",
    )
    service.run_retry_tick()
    items = atomic.list_items(ACCOUNT)
    assert len(items) == 1
    assert "跑步" in items[0].text
    assert items[0].fact_relation == AtomicProfileFactRelation.INTEREST
    assert dimensions.list_records(ACCOUNT) == []
