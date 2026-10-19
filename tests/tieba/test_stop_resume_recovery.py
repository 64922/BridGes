"""工单 10：停止、故障恢复、任务版本与模型失败后的局部重试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.kernel.contracts import (
    NodeInvocation,
)
from bridges.kernel.repository import NodeKernelRepository
from bridges.storage.database import BridgesDatabase
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaResearchStatus,
)
from bridges.tieba.node_flow_resume import resume_payload
from bridges.tieba.node_synthesis import _summary_incomplete
from bridges.tieba.recipe import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    TIEBA_RECIPE_VERSION,
    build_tieba_recipe,
)
from bridges.tieba.summary import TiebaSummaryGenerator
from tests.kernel.support import (
    ACCOUNT,
    ASSISTANT,
    CONVERSATION,
    NOW,
    RUN,
    seed_generation_run,
)
from tests.tieba.stop_resume_support import (
    _artifact,
    _hit,
    _inputs,
    _quota,
    _read_result,
    _run,
    _service,
    _summary_post,
    _SummaryGateway,
)
from tests.tieba.support import (
    CONFIRMED_URL,
    _create_conversation,
    _FakeReader,
    _FakeSearchPort,
    _register,
    _run_and_read,
    _send,
    _wire,
)


def test_resume_payload_uses_saved_evidence_outside_authentication_wait(
    tmp_path: Any,
) -> None:
    database = BridgesDatabase(tmp_path / "resume.db")
    database.initialize()
    repository = NodeKernelRepository(database)
    try:
        old_parse = _artifact(
            node=NODE_PARSE,
            inputs=_inputs(run_id="run-old"),
            dependencies={},
            payload={"analysis": {"original_question": "宿舍条件"}},
            run_id="run-old",
        )
        old_plan = _artifact(
            node=NODE_PLAN,
            inputs=_inputs(run_id="run-old", artifacts={NODE_PARSE: old_parse}),
            dependencies={NODE_PARSE: old_parse},
            payload={"plan": {"official_required": False}},
            run_id="run-old",
        )
        old_search_inputs = _inputs(
            run_id="run-old", artifacts={NODE_PARSE: old_parse, NODE_PLAN: old_plan}
        )
        partial = _artifact(
            node=NODE_SEARCH,
            inputs=old_search_inputs,
            dependencies={NODE_PARSE: old_parse, NODE_PLAN: old_plan},
            payload={"partial": True, "candidates": [_hit().model_dump(mode="json")]},
            run_id="run-old",
        )
        with repository.transaction():
            repository.save_artifact(partial)

        parse = _artifact(
            node=NODE_PARSE,
            inputs=_inputs(run_id="run-new"),
            dependencies={},
            payload={"analysis": {"original_question": "宿舍条件"}},
            run_id="run-new",
        )
        plan = _artifact(
            node=NODE_PLAN,
            inputs=_inputs(run_id="run-new", artifacts={NODE_PARSE: parse}),
            dependencies={NODE_PARSE: parse},
            payload={"plan": {"official_required": False}},
            run_id="run-new",
        )
        inputs = _inputs(run_id="run-new", artifacts={NODE_PARSE: parse, NODE_PLAN: plan})
        invocation = NodeInvocation(
            spec={item.name: item for item in build_tieba_recipe().nodes}[NODE_SEARCH],
            inputs=inputs,
            dependencies={NODE_PARSE: parse, NODE_PLAN: plan},
            remaining_budget_ms=None,
        )
        resumed = resume_payload(repository, invocation, NODE_SEARCH)
        assert resumed is not None
        assert resumed["candidates"] == partial.payload["candidates"]
    finally:
        database.close()


@pytest.mark.parametrize("change", ["task_version", "recipe_version"])
def test_resume_payload_rejects_incompatible_saved_evidence(
    tmp_path: Any, change: str
) -> None:
    database = BridgesDatabase(tmp_path / f"resume-{change}.db")
    database.initialize()
    repository = NodeKernelRepository(database)
    try:
        task_version = 1
        recipe_version = (
            "tieba-recipe-v0"
            if change == "recipe_version"
            else TIEBA_RECIPE_VERSION
        )
        old_parse = _artifact(
            node=NODE_PARSE,
            inputs=_inputs(run_id="run-old", task_id="task-1", task_version=task_version),
            dependencies={},
            payload={"analysis": {"original_question": "宿舍条件"}},
            run_id="run-old",
            recipe_version=recipe_version,
        )
        old_plan = _artifact(
            node=NODE_PLAN,
            inputs=_inputs(
                run_id="run-old",
                task_id="task-1",
                task_version=task_version,
                artifacts={NODE_PARSE: old_parse},
            ),
            dependencies={NODE_PARSE: old_parse},
            payload={"plan": {"official_required": False}},
            run_id="run-old",
            recipe_version=recipe_version,
        )
        partial = _artifact(
            node=NODE_SEARCH,
            inputs=_inputs(
                run_id="run-old",
                task_id="task-1",
                task_version=task_version,
                artifacts={NODE_PARSE: old_parse, NODE_PLAN: old_plan},
            ),
            dependencies={NODE_PARSE: old_parse, NODE_PLAN: old_plan},
            payload={"partial": True, "candidates": []},
            run_id="run-old",
            recipe_version=recipe_version,
        )
        with repository.transaction():
            repository.save_artifact(partial)

        new_version = task_version + (1 if change == "task_version" else 0)
        parse = _artifact(
            node=NODE_PARSE,
            inputs=_inputs(
                run_id="run-new", task_id="task-1", task_version=new_version
            ),
            dependencies={},
            payload={"analysis": {"original_question": "宿舍条件"}},
            run_id="run-new",
        )
        plan = _artifact(
            node=NODE_PLAN,
            inputs=_inputs(
                run_id="run-new",
                task_id="task-1",
                task_version=new_version,
                artifacts={NODE_PARSE: parse},
            ),
            dependencies={NODE_PARSE: parse},
            payload={"plan": {"official_required": False}},
            run_id="run-new",
        )
        invocation = NodeInvocation(
            spec={item.name: item for item in build_tieba_recipe().nodes}[NODE_SEARCH],
            inputs=_inputs(
                run_id="run-new",
                task_id="task-1",
                task_version=new_version,
                artifacts={NODE_PARSE: parse, NODE_PLAN: plan},
            ),
            dependencies={NODE_PARSE: parse, NODE_PLAN: plan},
            remaining_budget_ms=None,
        )
        assert resume_payload(repository, invocation, NODE_SEARCH) is None
    finally:
        database.close()


def test_task_version_is_part_of_parse_input_key() -> None:
    spec = {item.name: item for item in build_tieba_recipe().nodes}[NODE_PARSE]
    first = _inputs(run_id="run-1", task_id="task-1", task_version=1)
    second = _inputs(run_id="run-2", task_id="task-1", task_version=2)
    assert spec.input_key(first) != spec.input_key(second)


def test_stop_event_prevents_further_summary_model_calls() -> None:
    import threading

    stop = threading.Event()

    class Gateway:
        calls = 0

        def invoke(self, *args: Any, **kwargs: Any) -> ModelCallResult:
            self.calls += 1
            stop.set()
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "claims": [
                        {
                            "kind": "finding",
                            "text": "一条宿舍经历提到有空调。",
                            "citation_ids": ["E1"],
                        }
                    ]
                },
            )

    gateway = Gateway()
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_summary_post()],
        topic="宿舍条件",
        model_id="verified-model",
        model_quota=_quota(),
        stop_event=stop,
    )
    assert gateway.calls == 1
    assert outcome.summary is None
    assert outcome.claims == []
    assert "停止" in (outcome.note or "")




def test_model_failure_retry_reuses_saved_pages_and_redoes_summary(
    tmp_path: Any,
) -> None:
    from bridges.chat.repository import ConversationRepository

    database = BridgesDatabase(tmp_path / "retry.db")
    database.initialize()
    try:
        seed_generation_run(database)
        with database.transaction():
            database.connection.execute(
                "INSERT INTO messages (message_id, conversation_id, account_id, role,"
                " status, content, created_at, updated_at)"
                " VALUES ('msg-user', ?, ?, 'user', 'done', ?, ?, ?)",
                (
                    CONVERSATION,
                    ACCOUNT,
                    "华东交通大学吧 宿舍条件怎么样",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        repo = ConversationRepository(database)
        search = _FakeSearchPort(hits=[_hit()])
        reader = _FakeReader({CONFIRMED_URL: _read_result(CONFIRMED_URL)})
        gateway = _SummaryGateway()
        service = _service(search=search, reader=reader, gateway=gateway)
        _run(service, repo, run_id=RUN, assistant_id=ASSISTANT)

        message = repo.get_message(ACCOUNT, ASSISTANT)
        assert message is not None and message.tieba_research is not None
        first = message.tieba_research
        assert first["status"] == TiebaResearchStatus.SUCCESS.value
        assert first["summary"] is None
        assert first["recovery"]["action"] == "retry_incomplete_stage"
        assert first["recovery"]["reuses_saved_evidence"] is True
        assert first["recovery"]["failed_stage"] == NODE_SYNTHESIZE
        artifacts = NodeKernelRepository(database).list_artifacts(ACCOUNT, CONVERSATION)
        synthesis = [item for item in artifacts if item.node == NODE_SYNTHESIZE]
        assert synthesis[-1].payload["partial"] is True
        assert _summary_incomplete(
            SimpleNamespace(
                summary=None,
                claims=[],
                metrics=SimpleNamespace(failed_batch_count=1, model_call_count=1),
            )
        )
        search_calls = len(search.queries)
        read_calls = list(reader.read_urls)

        gateway.fail = False
        gateway.calls = 0
        seed_generation_run(
            database,
            run_id="run-2",
            assistant_message_id="msg-assistant-2",
        )
        retry_service = _service(search=search, reader=reader, gateway=gateway)
        _run(retry_service, repo, run_id="run-2", assistant_id="msg-assistant-2")

        retried = repo.get_message(ACCOUNT, "msg-assistant-2")
        assert retried is not None and retried.tieba_research is not None
        second = retried.tieba_research
        assert second["summary"] == "一条宿舍经历提到有空调。"
        assert second["recovery"] is None
        assert len(search.queries) == search_calls
        assert reader.read_urls == read_calls
        artifacts = NodeKernelRepository(database).list_artifacts(ACCOUNT, CONVERSATION)
        assert not [
            item
            for item in artifacts
            if item.node == NODE_SEARCH and item.run_id == "run-2"
        ]
        assert not [
            item for item in artifacts if item.node == NODE_READ and item.run_id == "run-2"
        ]
        synthesis = [item for item in artifacts if item.node == NODE_SYNTHESIZE]
        assert synthesis[-1].run_id == "run-2"
        assert synthesis[-1].payload["partial"] is False
    finally:
        database.close()


def test_auth_wait_is_not_reused_after_task_version_or_topic_change(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    _register(client, "910")
    account_id = client.get("/me").json()["account_id"]
    conversation = _create_conversation(client)
    task_service = sqlite_app.state.task_service
    task_service.repository.create_task(
        account_id=account_id,
        conversation_id=conversation,
        goal="华东交通大学吧 宿舍条件怎么样",
    )
    auth_result = _read_result(CONFIRMED_URL).model_copy(
        update={
            "status": ReadStatus.ACCESS_RESTRICTED,
            "error_code": "tieba_browser_auth_required",
            "error_message": "请完成贴吧登录",
            "forum_name": None,
            "title": None,
            "replies": [],
        }
    )

    def task_ref(account_id: str, conversation_id: str) -> Any:
        context = task_service.current_reference_context(account_id, conversation_id)
        return (context.task_id, context.version) if context is not None else None

    _wire(
        sqlite_app,
        _FakeSearchPort(hits=[_hit()]),
        reader=_FakeReader({CONFIRMED_URL: auth_result}),
    )
    sqlite_app.state.tieba_research_service._task_version_provider = task_ref  # noqa: SLF001

    _send(client, conversation, "华东交通大学吧 宿舍条件怎么样", module_id="tieba")
    waiting = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation
    )
    first = waiting["tieba_research"]
    assert first["status"] == "waiting_auth"
    stored_task_id = first["pending"]["context"]["task_id"]
    assert stored_task_id
    assert first["pending"]["context"]["task_version"] is not None
    current = task_ref(account_id, conversation)
    assert current is not None
    current_task_id = current[0]

    with sqlite_app.state.bridges_database.transaction():
        sqlite_app.state.bridges_database.connection.execute(
            "UPDATE conversation_tasks SET goal = ?, current_version = current_version + 1,"
            " updated_at = ? WHERE task_id = ?",
            ("华东交通大学吧 图书馆开放时间", NOW.isoformat(), current_task_id),
        )

    _send(client, conversation, "继续", module_id="tieba")
    resumed = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation
    )
    second = resumed["tieba_research"]
    assert second["status"] == "waiting_auth"
    assert (
        second["pending"]["context"]["original_question"]
        != first["pending"]["context"]["original_question"]
    )
    assert any(
        "图书馆" in item.get("execution_terms", "")
        or "图书馆" in item.get("query", "")
        for item in second["query_rounds"]
    )
