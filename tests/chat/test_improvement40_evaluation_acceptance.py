"""工单 40 独立验收：评测自身不能把缺失或错误证据判为通过。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts import run_issue40_context_continuity_evaluation as evaluation
from tests.chat.test_chat_api import _create_conversation, _register
from tests.chat.test_improvement03_model_quota import _MODEL_A, _activate
from tests.chat.test_improvement40_context_boundaries import _install, _ReviewAdapter
from tests.chat.test_issue02_durable_generation import client as client  # noqa: F401
from tests.chat.test_issue02_durable_generation import sqlite_app as sqlite_app  # noqa: F401


@pytest.mark.parametrize(("answer", "scenario", "index"), [
    ("不是3000，而是9999", "mid-correction", -1),
    ("明天吃什么？", "missing-reference", 0),
    ("", "tail-constraint", 0),
    ("网页开发不是第二个方向", "ordinal-list", -1),
])
def test_invalid_answer_is_not_a_pass(answer: str, scenario: str, index: int) -> None:
    turn = evaluation._scenario_by_id(scenario).turns[index]
    assert not evaluation._check_turn(answer, turn)["passed"]


@pytest.mark.parametrize("cases", [[], [{"passed": False}], [{"passed": True}, {"passed": False}]])
def test_empty_or_failed_evaluation_exits_nonzero(cases: list[dict[str, Any]]) -> None:
    assert evaluation._result_exit_code({"cases": cases}) != 0


@pytest.mark.parametrize("args", [["--repeats", "0"], ["--scenario", "不存在"]])
def test_invalid_configuration_is_rejected(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        evaluation.main(args)
    assert error.value.code != 0


def test_pairing_rejects_model_and_budget_drift() -> None:
    def side(model: str = "同模型", window: int = 6000) -> dict[str, Any]:
        return {"corpus_sha256": "固定语料哈希", "model_ids": [model], "cases": [{
                "scenario_id": "固定任务", "repeat": 1,
                "passed": True, "turns": [{"quota": {"context_window": window,
                "max_input_tokens": window, "verification_basis": "settings_activation"},
                "output_tokens": 1024, "model_id": model,
                "model_locks": [{"actual_model_id": model}]}],
                "provider_calls": [{"output_contract": "thinking-and-answer",
                                    "output_limit": 1024}]}]}
    assert evaluation._pairing_passed(side(), side())
    assert not evaluation._pairing_passed(side(), side(model="换模型"))
    assert not evaluation._pairing_passed(side(), side(window=12000))
    missing = side()
    del missing["corpus_sha256"]
    assert not evaluation._pairing_passed(missing, missing)
    unverified = side()
    unverified["cases"][0]["turns"][0]["quota"] = {"verification_basis": "unverified"}
    assert not evaluation._pairing_passed(unverified, unverified)


def test_selected_single_case_can_pass_and_missing_usage_cannot() -> None:
    assert evaluation._result_exit_code({"repeats": 1, "scenarios": ["固定任务"],
                                         "cases": [{"passed": True}]}) == 0
    assert not evaluation._provider_bounds([{"completed": True}], 16000, 16000)
    assert not evaluation._provider_bounds([{"completed": True, "output_limit": 1024,
           "usage": {"prompt_tokens": 15900, "completion_tokens": 200}}], 16000, 16000)


def test_provider_observer_equalizes_legacy_and_total_output_limits() -> None:
    class Client:
        def chat_completions(self, body: dict[str, Any]) -> dict[str, Any]:
            assert body["max_completion_tokens"] == 1024
            assert "max_tokens" not in body
            return {"usage": {"prompt_tokens": 100, "completion_tokens": 200}}

        def chat_completions_stream(self, body: dict[str, Any]) -> Any:
            assert body["max_completion_tokens"] == 1024
            assert "max_tokens" not in body
            yield {"usage": {"prompt_tokens": 100, "completion_tokens": 200}}

    calls = evaluation._measure_provider(Client)
    Client().chat_completions({"model": "同模型", "max_tokens": 1024})
    list(Client().chat_completions_stream({"model": "同模型", "max_completion_tokens": 1024}))
    assert len(calls) == 2
    assert calls[0]["request_sha256"] == calls[1]["request_sha256"]
    assert evaluation._provider_bounds(calls, 16000, 16000)


def test_seeded_long_history_really_enters_formal_graph(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any],
) -> None:
    account = _register(client, tag="4041")
    _install(sqlite_app, _ReviewAdapter())
    _activate(sqlite_app, _MODEL_A, window=6000, max_input=6000)
    conversation = _create_conversation(client)
    evaluation._seed_history(sqlite_app, account["id"], conversation,
                             evaluation._scenario_by_id("long-history-summary"))
    repo = sqlite_app.state.chat_service._repo
    assert all(message.created_at < datetime.now(UTC)
               for message in repo.list_messages(account["id"], conversation))
    generation_helpers["send"](client, conversation, content="总结一下。")
    generation_helpers["drive"](sqlite_app)
    from bridges.contracts.observability import AuditAction
    records = sqlite_app.state.observability_service.list_audit_events(
        action=AuditAction.CONTEXT_COMPILED)
    assert records[-1].details["summary_source_range"] is not None


def test_calibration_does_not_pair_shifted_usage() -> None:
    """缺失调用用量时，不能把下一调用的实际值配给上一估算。"""
    side = {"tree": "合成", "model_ids": ["同模型"], "repeats": 1,
            "constants": {"payload_budget": {"safety_margin_tokens": 256}},
            "cases": [{"scenario_id": "固定任务", "repeat": 1, "turns": [],
                       "calibration": {"gate_estimate_tokens": [100, 1000],
                                       "gate_actual_tokens": [900],
                                       "pairs": [{"estimate": 1000, "actual": 900}]}}]}
    report = evaluation._format_pairing_markdown(side, side)
    assert "-100" in report
    assert "800" not in report
