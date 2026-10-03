"""Issue 20：在简洁画像列表中按需查看依据与时效，并记录四类反馈。

覆盖验收：每条可展开真实依据、时间、范围与有效期；来源已删/不可读时
如实说明且不伪造引语、不跨账户定位；四类反馈可区分且不自动删除仍正确
的事实；反馈按账户隔离、幂等且可持久化。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.atomic_profile import (
    AtomicProfileEvidenceQuoteStatus,
    AtomicProfileEvidenceSourceStatus,
    AtomicProfileFactRelation,
    AtomicProfileFactScope,
    AtomicProfileFeedbackEffect,
    AtomicProfileFeedbackKind,
    AtomicProfileGoalState,
    AtomicProfileItem,
    AtomicProfileItemFeedbackRequest,
    AtomicProfileItemStatus,
    AtomicProfileValidityStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    AtomicProfileError,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    ProfileSourceMessage,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase

ACCOUNT = "account-evidence"
OTHER_ACCOUNT = "account-other"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def _service(
    reader: Any = None,
) -> tuple[AtomicProfileService, InMemoryAtomicProfileRepository]:
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = InMemoryAtomicProfileRepository()
    return AtomicProfileService(four, repository, message_reader=reader), repository


def _message(
    message_id: str,
    *,
    account_id: str = ACCOUNT,
    conversation_id: str = "conversation-1",
    role: str = "user",
    status: str = "done",
    content: str = "我计划下周通过英语六级",
    created_at: datetime = NOW,
) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        role=role,
        status=status,
        content=content,
        created_at=created_at,
    )


def _reader(**messages: ProfileSourceMessage) -> Any:
    table = {(message.message_id): message for message in messages.values()}

    def read(account_id: str, message_id: str) -> ProfileSourceMessage | None:
        message = table.get(message_id)
        if message is None:
            # 账户作用域由适配器保证：不属于该账户的消息查不到。
            return None
        if account_id != ACCOUNT:
            return None
        return message

    return read


def _item(
    *,
    item_id: str = "item-1",
    account_id: str = ACCOUNT,
    text: str = "我计划下周通过英语六级",
    sources: list[str] | None = None,
    evidence_quote: str | None = "我计划下周通过英语六级",
    valid_from: datetime | None = NOW,
    valid_until: datetime | None = NOW + timedelta(days=7),
    validity_phrase: str | None = "下周",
    goal_state: AtomicProfileGoalState = AtomicProfileGoalState.ACTIVE,
    scope: AtomicProfileFactScope = AtomicProfileFactScope.LONG_TERM,
    status: AtomicProfileItemStatus = AtomicProfileItemStatus.ACTIVE,
) -> AtomicProfileItem:
    return AtomicProfileItem(
        profile_item_id=item_id,
        owner_account_id=account_id,
        text=text,
        identity_key=f"identity-{item_id}",
        fact_relation=AtomicProfileFactRelation.GOAL,
        fact_object="通过英语六级",
        fact_scope=scope,
        fact_key=f"fact-{item_id}",
        evidence_quote=evidence_quote,
        valid_from=valid_from,
        valid_until=valid_until,
        validity_phrase=validity_phrase,
        goal_state=goal_state,
        source_message_ids=list(sources or []),
        status=status,
        write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
        confidence=FourDimensionConfidence.HIGH,
        version=1,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# 依据展开：原话、时间、范围、有效期与来源定位
# ---------------------------------------------------------------------------


def test_evidence_returns_quote_time_scope_validity_and_locator() -> None:
    reader = _reader(message=_message("message-1"))
    service, repository = _service(reader)
    repository.save_item(_item(sources=["message-1"]))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert evidence.evidence_quote == "我计划下周通过英语六级"
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.RECORDED
    )
    assert evidence.fact_scope == AtomicProfileFactScope.LONG_TERM
    assert evidence.validity_phrase == "下周"
    assert evidence.valid_from == NOW
    assert evidence.valid_until == NOW + timedelta(days=7)
    assert evidence.validity_status == AtomicProfileValidityStatus.ACTIVE
    assert len(evidence.sources) == 1
    source = evidence.sources[0]
    assert source.status == AtomicProfileEvidenceSourceStatus.AVAILABLE
    assert source.conversation_id == "conversation-1"
    assert source.created_at == NOW


def test_deleted_and_unreadable_sources_are_reported_without_fabricating() -> None:
    reader = _reader(
        alive=_message("alive"),
        blank=_message("blank", content="   "),
        assistant=_message("assistant", role="assistant"),
        streaming=_message("streaming", status="streaming"),
    )
    service, repository = _service(reader)
    repository.save_item(
        _item(
            sources=["alive", "gone", "blank", "assistant", "streaming"],
            evidence_quote="我计划下周通过英语六级",
        )
    )

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    statuses = {source.message_id: source.status for source in evidence.sources}
    assert statuses == {
        "alive": AtomicProfileEvidenceSourceStatus.AVAILABLE,
        "gone": AtomicProfileEvidenceSourceStatus.DELETED,
        "blank": AtomicProfileEvidenceSourceStatus.UNREADABLE,
        "assistant": AtomicProfileEvidenceSourceStatus.UNREADABLE,
        "streaming": AtomicProfileEvidenceSourceStatus.UNREADABLE,
    }
    # 来源数量不被当作“全部依据都可用”：只有真正可读的那条给定位。
    assert [
        source.conversation_id
        for source in evidence.sources
        if source.conversation_id is not None
    ] == ["conversation-1"]
    # 仍有可读来源时，保存的原话可以核对，不被部分来源失效抹掉。
    assert evidence.evidence_quote == "我计划下周通过英语六级"
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.RECORDED
    )


def test_quote_is_invalidated_when_no_source_is_readable() -> None:
    reader = _reader(blank=_message("blank", content="   "))
    service, repository = _service(reader)
    repository.save_item(_item(sources=["gone", "blank"]))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert {source.status for source in evidence.sources} == {
        AtomicProfileEvidenceSourceStatus.DELETED,
        AtomicProfileEvidenceSourceStatus.UNREADABLE,
    }
    # 删除聊天原文时同步失效其派生原话副本：没有可读来源就不回传原话。
    assert evidence.evidence_quote is None
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
    )


@pytest.mark.parametrize("sources", [["gone", "alive"], ["alive"], []])
def test_quote_requires_a_matching_readable_source(sources: list[str]) -> None:
    service, repository = _service(
        _reader(alive=_message("alive", content="我仍在准备英语考试"))
    )
    repository.save_item(_item(sources=sources))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert evidence.evidence_quote is None
    assert evidence.evidence_quote_status == AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE


def test_missing_quote_is_marked_not_recorded() -> None:
    service, repository = _service(_reader())
    repository.save_item(_item(evidence_quote=None, sources=["gone"]))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert evidence.evidence_quote is None
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.NOT_RECORDED
    )


def test_unwired_source_reader_reports_unreadable() -> None:
    service, repository = _service(None)
    repository.save_item(_item(sources=["message-1"]))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert evidence.sources[0].status == (
        AtomicProfileEvidenceSourceStatus.UNREADABLE
    )
    assert evidence.sources[0].conversation_id is None
    # 没有读取接缝就无法核对原话，如实失效派生副本，不把记录当现行依据。
    assert evidence.evidence_quote is None
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
    )


def test_evidence_is_account_scoped() -> None:
    reader = _reader(message=_message("message-1"))
    service, repository = _service(reader)
    repository.save_item(_item(sources=["message-1"]))

    with pytest.raises(AtomicProfileError):
        service.item_evidence(OTHER_ACCOUNT, "item-1", now=NOW)

    repository.save_item(
        _item(item_id="item-2", account_id=OTHER_ACCOUNT, sources=["message-1"])
    )
    evidence = service.item_evidence(OTHER_ACCOUNT, "item-2", now=NOW)
    # 其他账户的同名消息标识按作用域查不到：如实标为删除，不返回定位，
    # 原话副本随之失效。
    assert evidence.sources[0].status == AtomicProfileEvidenceSourceStatus.DELETED
    assert evidence.sources[0].conversation_id is None
    assert evidence.evidence_quote is None
    assert evidence.evidence_quote_status == (
        AtomicProfileEvidenceQuoteStatus.SOURCE_UNAVAILABLE
    )


def test_deleted_item_has_no_disclosure() -> None:
    service, repository = _service(_reader())
    repository.save_item(
        _item(status=AtomicProfileItemStatus.WITHDRAWN, text="", evidence_quote=None)
    )

    with pytest.raises(AtomicProfileError):
        service.item_evidence(ACCOUNT, "item-1", now=NOW)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"valid_until": NOW - timedelta(seconds=1)}, AtomicProfileValidityStatus.EXPIRED),
        ({"valid_from": NOW + timedelta(days=1)}, AtomicProfileValidityStatus.SCHEDULED),
        ({}, AtomicProfileValidityStatus.UNBOUNDED),
        (
            {"goal_state": AtomicProfileGoalState.PAUSED},
            AtomicProfileValidityStatus.PAUSED,
        ),
        (
            {
                "goal_state": AtomicProfileGoalState.COMPLETED,
                "valid_until": NOW - timedelta(days=1),
            },
            AtomicProfileValidityStatus.COMPLETED,
        ),
    ],
)
def test_validity_status_reflects_period_and_lifecycle(
    overrides: dict[str, Any], expected: AtomicProfileValidityStatus
) -> None:
    service, repository = _service(_reader())
    defaults: dict[str, Any] = {
        "valid_from": None,
        "valid_until": None,
        "validity_phrase": None,
    }
    defaults.update(overrides)
    repository.save_item(_item(**defaults))

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)

    assert evidence.validity_status == expected


# ---------------------------------------------------------------------------
# 四类反馈：可区分、幂等、绝不自动删除事实
# ---------------------------------------------------------------------------


def test_four_feedback_kinds_are_recorded_and_never_delete_facts() -> None:
    service, repository = _service(_reader())
    repository.save_item(_item())

    effects: dict[AtomicProfileFeedbackKind, AtomicProfileFeedbackEffect] = {}
    for kind in AtomicProfileFeedbackKind:
        projection = service.record_feedback(
            ACCOUNT, "item-1", AtomicProfileItemFeedbackRequest(kind=kind)
        )
        effects[kind] = projection.effect
        assert projection.kind == kind
        assert projection.message

    assert effects[AtomicProfileFeedbackKind.FACT_WRONG] == (
        AtomicProfileFeedbackEffect.SUGGEST_FACT_CORRECTION
    )
    assert effects[AtomicProfileFeedbackKind.EXPIRED] == (
        AtomicProfileFeedbackEffect.SUGGEST_VALIDITY_REVIEW
    )
    # 后两类不改变事实，记错/过期也只给修改建议，绝不自动删除。
    assert effects[AtomicProfileFeedbackKind.SCOPE_INAPPLICABLE] == (
        AtomicProfileFeedbackEffect.NO_FACT_CHANGE
    )
    assert effects[AtomicProfileFeedbackKind.PREFERENCE_NOT_FOLLOWED] == (
        AtomicProfileFeedbackEffect.NO_FACT_CHANGE
    )
    assert [item.profile_item_id for item in service.list_items(ACCOUNT)] == ["item-1"]

    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)
    assert {entry.kind for entry in evidence.feedback} == set(
        AtomicProfileFeedbackKind
    )


def test_repeated_feedback_of_same_kind_is_idempotent() -> None:
    service, repository = _service(_reader())
    repository.save_item(_item())
    request = AtomicProfileItemFeedbackRequest(
        kind=AtomicProfileFeedbackKind.EXPIRED, note="期限已经过了"
    )

    first = service.record_feedback(ACCOUNT, "item-1", request)
    second = service.record_feedback(ACCOUNT, "item-1", request)

    assert first.feedback_id == second.feedback_id
    assert second.note == "期限已经过了"
    evidence = service.item_evidence(ACCOUNT, "item-1", now=NOW)
    assert len(evidence.feedback) == 1


def test_feedback_is_account_scoped_and_rejected_after_delete() -> None:
    service, repository = _service(_reader())
    repository.save_item(_item())

    with pytest.raises(AtomicProfileError):
        service.record_feedback(
            OTHER_ACCOUNT,
            "item-1",
            AtomicProfileItemFeedbackRequest(
                kind=AtomicProfileFeedbackKind.FACT_WRONG
            ),
        )

    repository.save_item(
        _item(status=AtomicProfileItemStatus.WITHDRAWN, text="", evidence_quote=None)
    )
    with pytest.raises(AtomicProfileError):
        service.record_feedback(
            ACCOUNT,
            "item-1",
            AtomicProfileItemFeedbackRequest(
                kind=AtomicProfileFeedbackKind.FACT_WRONG
            ),
        )


def test_feedback_survives_sqlite_round_trip() -> None:
    database = BridgesDatabase(":memory:")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = SqliteAtomicProfileRepository(database)
    service = AtomicProfileService(four, repository)
    repository.save_item(_item())

    created = service.record_feedback(
        ACCOUNT,
        "item-1",
        AtomicProfileItemFeedbackRequest(
            kind=AtomicProfileFeedbackKind.SCOPE_INAPPLICABLE, note="只在当时有用"
        ),
    )

    reloaded = SqliteAtomicProfileRepository(database)
    stored = reloaded.list_feedback(ACCOUNT, "item-1")
    assert [entry.feedback_id for entry in stored] == [created.feedback_id]
    assert stored[0].note == "只在当时有用"
    assert stored[0].kind == AtomicProfileFeedbackKind.SCOPE_INAPPLICABLE


# ---------------------------------------------------------------------------
# API：权限、契约与幂等
# ---------------------------------------------------------------------------


def _register(client: TestClient, username: str, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": email, "password": "correct-horse-20"},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())["account"]["id"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue20-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_evidence_and_feedback_api_permissions_and_contract(
    client: TestClient,
) -> None:
    owner = _register(client, "issue20-owner", "200001@qq.com")
    service = client.app.state.atomic_profile_service
    item = service.remember(owner, "我计划下周通过英语六级", source_message_id="m1")

    own = client.get(f"/profiles/items/{item.profile_item_id}/evidence")
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["evidence_quote"] is None
    assert body["evidence_quote_status"] == "not_recorded"
    assert body["validity_phrase"] == "下周"
    assert body["validity_status"] in {"active", "scheduled"}
    assert body["sources"][0]["message_id"] == "m1"
    assert body["feedback"] == []
    # 页面合同不暴露内部哈希、类别或账户字段。
    for forbidden in ("identity_key", "topic_hint", "fact_key", "owner_account_id"):
        assert forbidden not in body

    first = client.post(
        f"/profiles/items/{item.profile_item_id}/feedback",
        json={"kind": "fact_wrong"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["kind"] == "fact_wrong"
    assert first.json()["effect"] == "suggest_fact_correction"

    scope = client.post(
        f"/profiles/items/{item.profile_item_id}/feedback",
        json={"kind": "scope_inapplicable", "note": "范围不对"},
    )
    assert scope.status_code == 201
    replay = client.post(
        f"/profiles/items/{item.profile_item_id}/feedback",
        json={"kind": "scope_inapplicable", "note": "范围不对"},
    )
    assert replay.status_code == 201
    assert replay.json()["feedback_id"] == scope.json()["feedback_id"]
    assert replay.json()["effect"] == "no_fact_change"

    still_there = client.get("/profiles/items")
    assert [entry["profile_item_id"] for entry in still_there.json()] == [
        item.profile_item_id
    ]

    refreshed = client.get(f"/profiles/items/{item.profile_item_id}/evidence")
    assert {entry["kind"] for entry in refreshed.json()["feedback"]} == {
        "fact_wrong",
        "scope_inapplicable",
    }

    # 另一账户看不到该条目：展开与反馈都不可达，列表为空。
    other_client = TestClient(client.app)
    _register(other_client, "issue20-other", "200002@qq.com")
    assert other_client.get("/profiles/items").json() == []
    assert (
        other_client.get(f"/profiles/items/{item.profile_item_id}/evidence").status_code
        == 404
    )
    assert (
        other_client.post(
            f"/profiles/items/{item.profile_item_id}/feedback",
            json={"kind": "expired"},
        ).status_code
        == 404
    )


def test_feedback_api_rejects_unknown_kind(client: TestClient) -> None:
    owner = _register(client, "issue20-invalid", "200003@qq.com")
    service = client.app.state.atomic_profile_service
    item = service.remember(owner, "我喜欢跑步")

    response = client.post(
        f"/profiles/items/{item.profile_item_id}/feedback",
        json={"kind": "personality_guess"},
    )

    assert response.status_code == 422
