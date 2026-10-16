"""工单 01：确定性基线旅程的入口级复现测试。

这些测试固定 2026-10-15 的确定性实际结果：六个问题类别的现状（已知
失败单独记录）与正向对照的可用路径。拥有票（03/04/05/09/11）落地后，
更新对应旅程的基线观测并在工单中说明；不得把目标预期改成现状来让
测试变绿。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.evaluation.profile_journey_evidence import (
    attach_recording_calls,
    build_run_lock,
    evaluate_journey,
)
from bridges.evaluation.profile_journey_runner import (
    ProfileJourneyCollector,
    install_deterministic_profile_extractor,
    run_baseline_journey,
)
from bridges.evaluation.profile_journeys import (
    BASELINE_JOURNEYS_BY_ID,
    corpus_digest,
)

_JOURNEY_IDS = (
    "t01-name",
    "t01-pet",
    "t01-habit",
    "t02-psychology",
    "t03-time-limit",
    "t04-cross-turn",
    "positive-academic-control",
    "recall-academic-control",
)


@pytest.fixture
def baseline_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "issue01-baseline-test-secret")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    yield app, client
    client.close()
    app.state.bridges_database.close()
    app.state.state_store.close()


def _register(client: TestClient, tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue01_{digest[:12]}",
            "qq_email": f"138{int(digest[:8], 16) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    return str(
        client.app.state.identity_service.resolve_session(token).subject.account_id
    )


def _run_all_journeys(baseline_env: Any, tag: str) -> dict[str, dict[str, Any]]:
    app, client = baseline_env
    adapter = install_deterministic_profile_extractor(app)
    journeys: list[dict[str, Any]] = []
    for journey_id in _JOURNEY_IDS:
        # 每条旅程独立账户：快照与修订链不互相污染。
        account_id = _register(client, f"{tag}-{journey_id}")
        collector = ProfileJourneyCollector(
            app=app,
            client=client,
            database=app.state.bridges_database,
            account_id=account_id,
        )
        journeys.append(
            run_baseline_journey(
                collector, BASELINE_JOURNEYS_BY_ID[journey_id], tag=tag
            )
        )
    attach_recording_calls(journeys, adapter)
    for journey in journeys:
        journey["evaluation"] = evaluate_journey(
            BASELINE_JOURNEYS_BY_ID[journey["journey_id"]], journey
        )
    return {journey["journey_id"]: journey for journey in journeys}


def test_deterministic_extractor_replaces_rule_extractor_with_gateway_wiring(
    baseline_env: Any,
) -> None:
    from bridges.profiles import GatewayAutomaticProfileExtractor

    app, client = baseline_env
    _register(client, "wiring")
    adapter = install_deterministic_profile_extractor(app)
    service = app.state.automatic_profile_service
    assert isinstance(service._extractor, GatewayAutomaticProfileExtractor)  # noqa: SLF001
    assert (
        app.state.model_gateway.get_adapter("qwen_profile_extraction", "1") is adapter
    )


def test_name_pet_and_habit_statements_extract_through_semantic_branch(
    baseline_env: Any,
) -> None:
    """工单 04：自然自述经语义分支提出候选、证据核验后写入原子条目。"""

    journeys = _run_all_journeys(baseline_env, "personal-facts")
    needle_by_journey = {
        "t01-name": "小林",
        "t01-pet": "团子",
        "t01-habit": "爬山",
    }
    for journey_id, needle in needle_by_journey.items():
        journey = journeys[journey_id]
        runs = journey["profile_snapshot"]["extraction_runs"]
        assert [run["status"] for run in runs] == ["succeeded"], journey_id
        assert all(run["source"] == "qwen_model" for run in runs), journey_id
        item_texts = [
            item["text"] for item in journey["profile_snapshot"]["items"]
        ]
        assert any(needle in text for text in item_texts), (journey_id, item_texts)
        assert journey["evaluation"]["observed_status"] == "pass", journey_id
        assert journey["evaluation"]["baseline_match"] is True, journey_id


def test_psychology_interest_is_extracted_not_blocked_by_sensitive_keyword(
    baseline_env: Any,
) -> None:
    journeys = _run_all_journeys(baseline_env, "sensitive-keyword")
    journey = journeys["t02-psychology"]
    runs = journey["profile_snapshot"]["extraction_runs"]
    assert [run["status"] for run in runs] == ["succeeded"]
    assert any(
        "心理学" in item["text"] for item in journey["profile_snapshot"]["items"]
    )
    assert journey["evaluation"]["observed_status"] == "pass"
    assert journey["evaluation"]["baseline_match"] is True


def test_time_limit_and_learning_content_both_survive(baseline_env: Any) -> None:
    journeys = _run_all_journeys(baseline_env, "time-limit")
    journey = journeys["t03-time-limit"]
    runs = journey["profile_snapshot"]["extraction_runs"]
    assert [run["status"] for run in runs] == ["succeeded"]
    item_texts = [item["text"] for item in journey["profile_snapshot"]["items"]]
    assert any("线性代数" in text for text in item_texts)
    assert any("半小时" in text for text in item_texts)
    assert journey["evaluation"]["observed_status"] == "pass"
    assert journey["evaluation"]["baseline_match"] is True


def test_cross_turn_gateway_call_carries_real_conversation_and_window(
    baseline_env: Any,
) -> None:
    journeys = _run_all_journeys(baseline_env, "cross-turn")
    journey = journeys["t04-cross-turn"]
    calls = journey["all_recording_calls"]
    assert calls, "网关提取替身未被调用"
    # 工单 05：抽取携带真实会话标识，不再传字面量 retry。
    conversation_ids = {call["project_id"] for call in calls}
    assert conversation_ids == {journey["conversation_id"]}
    # 第一轮没有前文（合法为空）；跨轮那一轮必须携带含首轮原文的窗口提示。
    assert calls[-1]["neighbor_hint_present"] is True
    assert any(
        "我在读计算机专业，大二。" in hint
        for hint in calls[-1]["neighbor_hint_content"]
    )
    # 窗口按位置取当前消息之前的有界前文（含助手回复），不含当前消息。
    probe = journey["neighbor_reader_probe"]
    assert any("我在读计算机专业，大二。" in entry for entry in probe)
    assert any(entry.startswith("助手：") for entry in probe)
    assert all(
        "以后先给我结论，再展开解释。" not in entry for entry in probe
    )
    assert journey["evaluation"]["observed_status"] == "pass", journey["evaluation"]
    assert journey["evaluation"]["baseline_match"] is True


def test_positive_academic_control_passes_through_real_entry(baseline_env: Any) -> None:
    journeys = _run_all_journeys(baseline_env, "positive-control")
    journey = journeys["positive-academic-control"]
    evaluation = journey["evaluation"]
    assert evaluation["observed_status"] == "pass"
    assert all(check["passed"] for check in evaluation["checks"])
    # 工单 03 起原子条目是唯一写入权威；四维记录不再参与写入。
    assert journey["profile_snapshot"]["items"]


def test_recall_academic_control_adopts_stored_fact(baseline_env: Any) -> None:
    journeys = _run_all_journeys(baseline_env, "recall-control")
    journey = journeys["recall-academic-control"]
    second_run = journey["turns"][1]["generation_run"]
    slice_payload = second_run["adopted_profile_slice"]
    assert slice_payload is not None
    # 工单 11：属性词（「专业」）命中条目的关系槽，下一轮直接采用该事实。
    assert [
        item["fact_text"] for item in slice_payload["adopted_items"]
    ] == ["我是计算机专业的大二学生"]
    assert slice_payload["adopted_items"][0]["recall_basis"] == "keyword"
    evaluation = journey["evaluation"]
    assert evaluation["observed_status"] == "pass"
    assert evaluation["baseline_match"] is True
    assert all(check["passed"] for check in evaluation["checks"])


def test_run_lock_freezes_corpus_code_and_response_mode(baseline_env: Any) -> None:
    from bridges.evaluation.learning_journey_runner import CredentialPresence
    from bridges.evaluation.profile_journey_runner import RESPONSE_MODE_STUB

    app, client = baseline_env
    _register(client, "run-lock")
    install_deterministic_profile_extractor(app)
    lock = build_run_lock(
        Path(__file__).resolve().parents[2],
        app=app,
        journey_ids=list(_JOURNEY_IDS),
        response_mode=RESPONSE_MODE_STUB,
        credentials=CredentialPresence(
            qwen_configured=False,
            qwen_source="test",
            tavily_configured=False,
            tavily_source="test",
        ),
        stub_profile_extraction=True,
        stub_chat=True,
    )
    assert lock["corpus_version"] == "profile-loop-corpus-v2"
    assert lock["corpus_sha256"] == corpus_digest()
    assert lock["response_mode"] == RESPONSE_MODE_STUB
    assert lock["injected_models_overridden"] is True
    assert lock["contract_versions"]["signal_classifier"].startswith("profile-signal")
    assert lock["budget_constants"]["profile_extraction_max_neighbors"] == 4
