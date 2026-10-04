"""工单 33 独立验收：私有错误边界、核验材料、题设与数值工具缺口。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.study.review import evaluate_calculation
from tests.chat.test_improvement33_preverified_questions import (
    Review33Gateway,
    _calculation_plan,
    _default_plan,
    _raw_state,
    _single_unit_map,
    _start_review,
)
from tests.chat.test_v2_05_photo_attachments import _app
from tests.chat.test_v2_18_study_tutoring import _ask, _retry


def _checks(data: dict[str, Any], **changes: Any) -> list[dict[str, Any]]:
    return [
        {
            "question_id": question["question_id"],
            "question_matches_knowledge": True,
            "rubric_supported": True,
            "answer_consistent": True,
            "requires_calculation": False,
            "status": "consistent",
            "detail": "",
            **changes,
        }
        for question in data["questions"]
    ]


def test_failed_verification_hides_private_details_in_api_and_replay(
    tmp_path: Any, monkeypatch: Any
) -> None:
    secret = "私有答案金丝雀：冷热程度"

    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        questions = _default_plan(data)
        questions[0]["question"] = "未呈现题干金丝雀"
        return questions

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度表示冷热程度。"], map_fn=_single_unit_map, plan_fn=plan,
        verify_fn=lambda data: _checks(data, status="conflict", detail=secret),
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        before = client.get(endpoint).json()["study"]
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_verify_conflict"
        assert result["study"] == before
        message_id = result["messages"][-1]["message_id"]
        replay = client.get(endpoint + f"/messages/{message_id}/events")
        assert replay.status_code == 200
        for forbidden in (secret, "未呈现题干金丝雀"):
            assert forbidden not in json.dumps(result, ensure_ascii=False)
            assert forbidden not in client.get(endpoint).text
            assert forbidden not in replay.text
        # 原始反馈仍可供唯一一次内部修复，不用公开错误反推私有信息。
        assert len(gateway.plan_calls) == 2
        assert secret in gateway.plan_prompts[1]


def test_verification_knows_unit_semantics_and_rejects_same_fragment_mismatch(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def mapping(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        return {"units": [
            {"title": title, "kind": "concept", "fragment_ids": [fragments[0]["id"]],
             "core": True}
            for title in ("温度", "温度计")
        ], "exclusions": []}

    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {**_default_plan(data)[0], "question": f"温度表示什么（第{index}个角度）？",
             "coverage_units": [unit["unit_id"]]}
            for index, unit in enumerate(data["units"], 1)
        ]

    def verify(data: dict[str, Any]) -> list[dict[str, Any]]:
        units = {unit["unit_id"]: unit for unit in data["units"]}
        assert {unit["title"] for unit in units.values()} == {"温度", "温度计"}
        checks = _checks(data)
        for question, check in zip(data["questions"], checks, strict=True):
            unit = units[question["coverage_units"][0]]
            assert unit["fragment_ids"] == question["fragment_ids"]
            if unit["title"] == "温度计":
                check.update(question_matches_knowledge=False, status="conflict")
        return checks

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度表示冷热程度，温度计利用热胀冷缩测量温度。"],
        map_fn=mapping, plan_fn=plan, verify_fn=verify,
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_verify_conflict"
        assert result["study"]["review"] is None
        assert len(gateway.question_verify_calls) == 2


@pytest.mark.parametrize("requires_calculation", [False, True])
def test_numeric_question_without_calculation_never_presents(
    tmp_path: Any, monkeypatch: Any, requires_calculation: bool
) -> None:
    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        question = _default_plan(data)[0]
        question.update(question="计算 2+2 的值。", canonical_answer="4", conditions="题设：2+2")
        return [question]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["加法计算规则。"], map_fn=_single_unit_map, plan_fn=plan,
        verify_fn=lambda data: _checks(data, requires_calculation=requires_calculation),
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_calculation"
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None
        assert len(gateway.plan_calls) == 2


def test_conditions_are_explicit_visible_and_retained_when_repeating_current_question(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        question = _default_plan(data)[0]
        question.update(question="比较两种物体的温度。", conditions=(
            "教材条件：甲为固体" if len(gateway.plan_calls) == 1 else "题设：甲为固体"
        ))
        return [question]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["温度表示冷热程度。"], map_fn=_single_unit_map, plan_fn=plan)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done"
        assert len(gateway.plan_calls) == 2
        assert "题设：甲为固体" in result["messages"][-1]["content"]
        assert result["study"]["review"]["questions"][0]["conditions"] == "题设：甲为固体"
        repeated = _ask(client, app, endpoint, "继续复盘")
        assert "题设：甲为固体" in repeated["messages"][-1]["content"]
        raw = _raw_state(app, account["id"], endpoint)
        assert len(raw.review.questions) == 1


def test_unrelated_calculation_result_cannot_verify_frozen_numeric_answer(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        question = _default_plan(data)[0]
        question.update(question="计算 2+2 的值。", canonical_answer="4", conditions="题设：2+2")
        return [question]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["加法规则。"], map_fn=_single_unit_map, plan_fn=plan,
        verify_fn=lambda data: _checks(data, requires_calculation=True, calculation={
            "expression": "1+1", "expected": 2, "variables": {},
        }),
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_calculation"
        assert result["study"]["review"] is None
        assert len(gateway.question_verify_calls) == 2


@pytest.mark.parametrize("expression, answer", [("9-3", "六"), ("9 减 3", "六")])
def test_subtraction_with_chinese_answer_still_requires_calculation(
    tmp_path: Any, monkeypatch: Any, expression: str, answer: str
) -> None:
    def plan(data: dict[str, Any]) -> list[dict[str, Any]]:
        question = _default_plan(data)[0]
        question.update(question=f"计算 {expression} 的值。", canonical_answer=answer,
                        conditions="题设：9 与 3")
        return [question]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["减法规则。"], map_fn=_single_unit_map, plan_fn=plan)
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_calculation"
        assert result["study"]["review"] is None


def test_copied_answer_constant_is_not_calculation_evidence(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["加法规则。"], map_fn=_single_unit_map, plan_fn=_calculation_plan,
        verify_fn=lambda data: _checks(data, requires_calculation=True, calculation={
            "expression": "4", "expected": 4, "variables": {},
        }),
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_verify_unverified"
        assert result["study"]["review"] is None


@pytest.mark.parametrize("expression", ["(-1)**0.5", "1e308**2", "0**-1"])
def test_non_real_or_overflow_calculations_are_bounded_failures(expression: str) -> None:
    with pytest.raises(ValueError):
        evaluate_calculation(expression)


def test_invalid_calculation_gets_one_repair_and_no_question(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度表示冷热程度。"], map_fn=_single_unit_map,
        plan_fn=_calculation_plan,
        verify_fn=lambda data: _checks(data, requires_calculation=True, calculation={
            "expression": "(-1)**0.5", "expected": 4, "variables": {},
        }),
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["error_code"] == "study_review_verify_unverified"
        assert result["study"]["review"] is None
        assert len(gateway.question_verify_calls) == 2


def test_old_graph_run_is_rejected_then_explicit_retry_uses_new_verification(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["温度表示冷热程度。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        before = client.get(endpoint).json()["study"]
        posted = client.post(endpoint + "/messages", json={"content": "开始复盘"})
        assert posted.status_code == 200
        database = app.state.chat_service._repo.database
        database.scoped(account["id"]).execute(
            "UPDATE generation_runs SET graph_version = 'study-scope-v2'"
            " WHERE account_id = ? AND conversation_id = ? AND status = 'queued'",
            (account["id"], endpoint.rsplit("/", 1)[-1]),
        )
        app.state.generation_executor.run_tick()
        failed = client.get(endpoint).json()
        assert failed["messages"][-1]["error_code"] == "study_graph_version_changed"
        assert failed["study"] == before
        assert gateway.plan_calls == []
        assert gateway.question_verify_calls == []
        resumed = _retry(client, app, endpoint)
        assert resumed["messages"][-1]["status"] == "done"
        assert len(gateway.question_verify_calls) == 1


def test_plan_receipt_records_registered_calculation_without_replay_leak(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from bridges.kernel.repository import NodeKernelRepository

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度表示冷热程度。"], map_fn=_single_unit_map,
        plan_fn=_calculation_plan,
        verify_fn=lambda data: _checks(data, requires_calculation=True, calculation={
            "expression": "2+2", "expected": 4, "variables": {},
        }),
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done"
        artifacts = NodeKernelRepository(app.state.chat_service._repo.database).list_artifacts(
            account["id"], endpoint.rsplit("/", 1)[-1],
        )
        plans = [artifact for artifact in artifacts if artifact.node == "study.plan_review"]
        assert len(plans) == 1
        assert plans[0].payload["calculations"] == [{
            "capability": "study.calculate", "version": "study-calculate-v1",
            "expression": "2+2", "variables": {}, "value": 4,
        }]
        message_id = result["messages"][-1]["message_id"]
        replay = client.get(endpoint + f"/messages/{message_id}/events")
        assert '"expression"' not in replay.text
        assert "core_points" not in replay.text
