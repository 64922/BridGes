"""工单 31 独立验收：核验遗漏、迁移和持久提交故障回归。"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.study import StudyState, upgrade_legacy_study_state
from bridges.study.scope import StudyScopeRecognition
from tests.chat.test_improvement31_study_scope_preview import ScopeGateway, _retry
from tests.chat.test_v2_05_photo_attachments import PNG_BYTES, _app, _register, _upload_draft
from tests.chat.test_v2_17_study_pages import _first


def _mapping(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
    return {"units": [{"title": "温度", "kind": "concept",
                       "fragment_ids": [item["id"] for item in fragments], "core": True}]}


def test_legacy_same_title_uses_question_evidence() -> None:
    state = upgrade_legacy_study_state(StudyState.model_validate({
        "subsection_id": "legacy", "stage": "review",
        "units": [{"title": "温度", "fragment_ids": ["f1"]},
                  {"title": "温度", "fragment_ids": ["f2"]}],
        "questions": [{"question": "温度是什么？", "unit_titles": ["温度"]}],
        "review": {"questions": [{"question_id": "q2", "question": "第二页是什么？",
                                  "coverage_units": ["温度", "旧未知项"],
                                  "fragment_ids": ["f2"]}]},
    }))
    assert state.review.questions[0].coverage_units == [state.units[1].unit_id, "旧未知项"]
    # 旧预习没有片段依据，保留标题，不伪造唯一关联。
    assert state.questions[0].unit_ids == []
    assert state.questions[0].unit_titles == ["温度"]


@pytest.mark.parametrize("changed_policy", [False, True])
def test_preview_commit_failure_rolls_back_and_reuses_valid_nodes(
    tmp_path: Any, monkeypatch: Any, changed_policy: bool,
) -> None:
    from bridges.study.service import StudyRepository

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(["温度是表示物体冷热程度的物理量。"], map_fn=_mapping)
    app.state.chat_service._gateway = gateway
    original_save = StudyRepository.save_in_transaction
    fail_once = True

    def fail_after_write(self: Any, account: str, conversation: str, state: StudyState) -> None:
        nonlocal fail_once
        original_save(self, account, conversation, state)
        if state.stage == "tutoring" and fail_once:
            fail_once = False
            raise RuntimeError("独立验收：领域保存后注入提交失败")

    monkeypatch.setattr(StudyRepository, "save_in_transaction", fail_after_write)
    with TestClient(app) as client:
        account = _register(client, "accept31commit")
        photo = _upload_draft(client, upload_id="commit31").json()
        first = _first(client, [photo["object_id"]], "commit31-first")
        cid = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        failed = client.get(f"/chat/conversations/{cid}").json()
        assert failed["study"]["stage"] == "preview"
        assert failed["study"]["questions"] == []
        assert "预习时可以" not in (failed["messages"][-1]["content"] or "")
        assert failed["messages"][-1]["status"] == "error"
        assert len(gateway.verify_calls) == 1  # 自然语言定义必须核验。
        messages = gateway.preview_calls[0]["messages"]
        assert '"questions"' in messages[0]["content"]
        assert '"unit_ids"' in messages[0]["content"]
        if changed_policy:
            original_preview = StudyScopeRecognition.preview_scope

            def new_policy(self: Any, scope: Any, *, policy_block: str = "") -> Any:
                return original_preview(self, scope, policy_block=policy_block + "\n用简短措辞。")

            monkeypatch.setattr(StudyScopeRecognition, "preview_scope", new_policy)
        _retry(client, app, cid, "commit31-retry")
        result = client.get(f"/chat/conversations/{cid}").json()
        assert result["study"]["stage"] == "tutoring"
        assert len(gateway.map_calls) == len(gateway.verify_calls) == 1
        assert len(gateway.preview_calls) == (2 if changed_policy else 1)
        rows = app.state.chat_service._repo.database.scoped(account["id"]).execute(
            "SELECT node, input_deps_json FROM node_artifacts WHERE account_id = ?",
            (account["id"],),
        ).fetchall()
        assert rows
        assert all(json.loads(row["input_deps_json"]) for row in rows
                   if row["node"] in {"study.map", "study.verify_scope", "study.preview"})
        verification = next(row for row in rows if row["node"] == "study.verify_scope")
        dependency = json.loads(verification["input_deps_json"])[0]
        assert dependency["node"] == "study.map"
        assert dependency["artifact_id"] and dependency["content_hash"]


def test_stop_after_scope_receipt_rejects_domain_write(tmp_path: Any, monkeypatch: Any) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(["温度是物理量。"], map_fn=_mapping)
    app.state.chat_service._gateway = gateway
    original = StudyScopeRecognition.map_scope
    with TestClient(app) as client:
        _register(client, "accept31stop")
        photo = _upload_draft(client, upload_id="stop31").json()
        first = _first(client, [photo["object_id"]], "stop31-first")
        cid = first.json()["conversation"]["conversation_id"]
        mid = first.json()["assistant_message"]["message_id"]

        def stop_after_receipt(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            assert result.scope is not None
            assert client.post(f"/chat/conversations/{cid}/messages/{mid}/stop").status_code == 200
            return result

        monkeypatch.setattr(StudyScopeRecognition, "map_scope", stop_after_receipt)
        app.state.generation_executor.run_tick()
        result = client.get(f"/chat/conversations/{cid}").json()
        assert result["study"]["stage"] == "recognizing"
        assert result["study"]["scope"] is None
        assert gateway.preview_calls == []


@pytest.mark.parametrize("kind", ["concept", "relation", "application", "misconception"])
def test_scope_repair_respects_shared_adjustment_budget(
    tmp_path: Any, monkeypatch: Any, kind: str,
) -> None:
    from bridges.chat.budget import RunBudget

    def mapping(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        result = _mapping(fragments, repair)
        result["units"][0]["kind"] = kind
        return result

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(
        ["温度是物理量。"], map_fn=mapping,
        checks_fn=lambda ids: [{"unit_id": ref, "status": "conflict"} for ref in ids],
    )
    app.state.chat_service._gateway = gateway
    monkeypatch.setattr(RunBudget, "begin_adjustment", lambda self, **kwargs: False)
    with TestClient(app) as client:
        _register(client, "accept31budget")
        photo = _upload_draft(client, upload_id="budget31").json()
        first = _first(client, [photo["object_id"]], "budget31-first")
        cid = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        result = client.get(f"/chat/conversations/{cid}").json()
        assert result["messages"][-1]["error_code"] == "study_scope_content_conflict"
        assert len(gateway.map_calls) == len(gateway.verify_calls) == 1
        assert gateway.preview_calls == []


def test_old_graph_is_closed_and_explicit_retry_uses_new_graph(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(["温度是物理量。"], map_fn=_mapping)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        account = _register(client, "accept31oldgraph")
        photo = _upload_draft(client, upload_id="oldgraph31").json()
        first = _first(client, [photo["object_id"]], "oldgraph31-first")
        cid = first.json()["conversation"]["conversation_id"]
        database = app.state.chat_service._repo.database
        database.scoped(account["id"]).execute(
            "UPDATE generation_runs SET graph_version = 'study-pages-v1' WHERE account_id = ?",
            (account["id"],),
        )
        app.state.generation_executor.run_tick()
        failed = client.get(f"/chat/conversations/{cid}").json()
        assert failed["messages"][-1]["error_code"] == "study_graph_version_changed"
        assert gateway.calls == [] and gateway.map_calls == []
        _retry(client, app, cid, "oldgraph31-retry")
        result = client.get(f"/chat/conversations/{cid}").json()
        assert result["study"]["stage"] == "tutoring"


@pytest.mark.parametrize("status", ["conflict", "omitted", "duplicate"])
def test_excluded_formula_requires_supported_reason(
    tmp_path: Any, monkeypatch: Any, status: str,
) -> None:
    def exclude(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        return {"units": [{"title": "温度", "fragment_ids": [fragments[0]["id"]]}],
                "exclusions": [{"fragment_id": fragments[1]["id"], "reason": "这是页眉信息"}]}

    class ExclusionGateway(ScopeGateway):
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            result = super().invoke(*args, **kwargs)
            payload = kwargs.get("payload", {})
            if payload.get("task") == "study.verify_scope":
                if status == "omitted":
                    result.output["exclusions"] = []
                elif status == "duplicate":
                    result.output["exclusions"] = [
                        {**item, "status": verdict}
                        for item in result.output["exclusions"]
                        for verdict in ("conflict", "consistent")
                    ]
                else:
                    for item in result.output["exclusions"]:
                        item["status"] = "conflict"
                        item["detail"] = "原文是教学公式，不是页眉。"
            return result

    app = _app(tmp_path, monkeypatch)
    gateway = ExclusionGateway(["温度是物理量。", "质能公式 E=mc²。"], map_fn=exclude)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "accept31exclude")
        photos = [_upload_draft(client, upload_id=f"exclude31-{i}",
                                content=PNG_BYTES + str(i).encode()).json()["object_id"]
                  for i in range(2)]
        first = _first(client, photos, "exclude31-first")
        cid = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        result = client.get(f"/chat/conversations/{cid}").json()
        assert result["messages"][-1]["error_code"] == (
            "study_scope_content_conflict"
            if status == "conflict" else "study_scope_content_unverified"
        )
        assert gateway.preview_calls == []
        assert len(gateway.map_calls) == len(gateway.verify_calls) == 2


def test_appended_page_preserves_more_than_eight_scope_versions(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from bridges.study.service import StudyRepository

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(["温度是物理量。", "温度计测量温度。"], map_fn=_mapping)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        account = _register(client, "accept31history")
        photo = _upload_draft(client, upload_id="history31").json()
        first = _first(client, [photo["object_id"]], "history31-first")
        cid = first.json()["conversation"]["conversation_id"]
        app.state.generation_executor.run_tick()
        repository = StudyRepository(app.state.chat_service._repo.database)
        state = repository.get(account["id"], cid)
        state.scope_history = [state.scope.model_copy(update={"scope_version_id": f"old-{i}"})
                               for i in range(9)]
        old_version = state.questions[0].scope_version_id
        repository.save(account["id"], cid, state)
        extra = _upload_draft(
            client, upload_id="history31-extra", content=PNG_BYTES + b"extra",
        ).json()
        response = client.post(f"/chat/conversations/{cid}/messages", json={
            "content": "追加本节书页", "attachment_ids": [extra["object_id"]],
            "idempotency_key": "history31-append",
        })
        assert response.status_code == 200, response.text
        app.state.generation_executor.run_tick()
        state = repository.get(account["id"], cid)
        assert len(state.scope_history) == 10
        assert old_version in {item.scope_version_id for item in state.scope_history}
        assert state.questions[0].scope_version_id == old_version
        history = json.loads(state.model_dump_json())["scope_history"]
        assert history[-1]["scope_version_id"] == "old-8"
