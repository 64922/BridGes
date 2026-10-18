"""工单 02：自然语言话题生成有效且不重复的检索计划。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.task_materials import ModuleTaskContext
from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser import BrowserTiebaSearch, browser_terms
from bridges.tieba.evidence_search import search_candidates
from bridges.tieba.evidence_verification import build_evidence_plan
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    execution_terms,
    plan_queries,
    plan_query_rounds,
    query_record,
)
from tests.tieba.support import (
    CONFIRMED_URL,
    _create_conversation,
    _FakeReader,
    _FakeSearchPort,
    _gateway_with,
    _install_tieba_service,
    _register,
    _run_and_read,
    _send,
    _SilentAdapter,
)


def _task_context(topic: str = "宿舍条件") -> ModuleTaskContext:
    return ModuleTaskContext(
        version="module-context-v2",
        module_id="tieba",
        purpose="tieba",
        task_id="current-task",
        task_goal=topic,
        topic_hint=topic,
        effective_conditions=(),
        excluded_condition_ids=(),
        prior_messages=(),
        source_message_ids=("current-source",),
        evidence_scope="任务主题、时间与校区条件",
        used_task_scope=True,
    )


@pytest.mark.parametrize(
    ("question", "expected_terms"),
    [
        ("我想获取关于宿舍的讨论", ["宿舍"]),
        ("帮我收集关于宿舍的讨论", ["宿舍"]),
        ("想了解宿舍", ["宿舍"]),
    ],
)
def test_action_request_extracts_the_topic_not_the_instruction(
    question: str, expected_terms: list[str]
) -> None:
    """动作表达不进入主题；“获取”“帮我收集”“想了解”覆盖同一行为。"""
    analysis = parse_tieba_request(question)

    assert analysis.topic_terms == expected_terms
    assert analysis.clarification is None
    assert all("获取" not in query for query in plan_queries(analysis))


@pytest.mark.parametrize("question", ["获取", "帮我获取一下", "帮我收集一下"])
def test_action_only_request_asks_for_a_topic(question: str) -> None:
    """只有动作词、没有话题时请求澄清，不拿动作词发起检索。"""
    analysis = parse_tieba_request(question)

    assert analysis.topic_terms == []
    assert analysis.clarification is not None
    assert plan_queries(analysis) == ()


def test_action_only_request_uses_the_existing_task_and_new_topic_replaces_it() -> None:
    """任务上下文里：只有动作词时续接原话题；自带名词的新话题不被旧主题污染。"""
    context = _task_context()

    continued = parse_tieba_request("帮我获取一下", module_context=context)
    assert continued.topic_terms == ["宿舍", "条件"]
    assert continued.clarification is None

    changed = parse_tieba_request("食堂怎么样", module_context=context)
    assert changed.topic_terms == ["食堂"]
    assert "宿舍" not in changed.topic_terms


def test_alias_and_relaxed_queries_keep_campus_and_time_conditions() -> None:
    """同义表达只在原话题内替换，不静默放宽点名校区或时间条件。"""
    analysis = parse_tieba_request("我想获取北区宿舍近三个月的讨论")

    assert "北区" in analysis.topic_terms
    assert "宿舍" in analysis.topic_terms
    assert "获取" not in analysis.topic_terms
    assert "近三" not in analysis.topic_terms
    assert analysis.campus_terms == ["北区"]
    assert analysis.time_requirement == "近三个月"

    rounds = plan_query_rounds(analysis)
    assert len(rounds) >= 2
    for item in rounds:
        assert "北区" in item.execution_terms
        assert "宿舍" in item.execution_terms or "寝室" in item.execution_terms
        assert "获取" not in item.execution_terms
    assert all("北区" in query for query in plan_queries(analysis))


def test_plan_dedupes_the_same_browser_expression_and_records_goals() -> None:
    """去掉固定来源前缀与“贴吧”装饰词后相同的查询只保留一次。"""
    analysis = parse_tieba_request("我想获取关于宿舍的讨论")

    rounds = plan_query_rounds(analysis)
    expressions = [item.execution_terms for item in rounds]

    assert expressions == ["宿舍", "寝室"]
    assert [item.goal for item in rounds] == ["原词查询", "常用说法查询"]
    assert len(expressions) == len(set(expressions))
    plan = build_evidence_plan(analysis, official_blocked_reason=None)
    assert [item.execution_terms for item in plan.query_rounds] == expressions


def test_execution_terms_remove_only_source_prefix_and_decorations() -> None:
    assert (
        execution_terms("site:tieba.baidu.com/p/ 华东交通大学吧 宿舍 获取 贴吧")
        == "宿舍 获取"
    )
    assert browser_terms("site:tieba.baidu.com/p/ 华东交通大学吧 国庆节 假期") == "国庆 假期"
    with pytest.raises(ValueError):
        browser_terms("site:douyin.com 华东交通大学吧 国庆")


def test_zero_result_adjustment_changes_the_executed_expression() -> None:
    """首轮零结果后的下一轮必须真正改变检索表达，不重复同一句话。"""
    analysis = parse_tieba_request("我想获取关于宿舍的讨论")
    rounds = plan_query_rounds(analysis)
    calls: list[str] = []

    class Search:
        def search_public(
            self, account_id: str, *, query: str, **kwargs: object
        ) -> SearchOutcome:
            del account_id, kwargs
            calls.append(query)
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                    evidence_count=0,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )

    result = search_candidates(
        Search(),
        "account-1",
        analysis,
        stop_event=None,
        deadline_seconds=10,
    )

    assert calls == [item.query for item in rounds]
    assert [browser_terms(call) for call in calls] == ["宿舍", "寝室"]
    assert len({browser_terms(call) for call in calls}) == len(calls)
    assert len(result.records) == len(rounds)


def test_browser_payload_and_search_records_use_the_same_actual_terms() -> None:
    """从计划到浏览器参数再到检索记录：真实执行词一致且不含动作词。"""
    analysis = parse_tieba_request("我想获取关于宿舍的讨论")
    payloads: list[dict[str, Any]] = []

    class Runtime:
        def request(
            self, account_id: str, payload: dict[str, Any], **kwargs: object
        ) -> dict[str, Any]:
            del account_id, kwargs
            payloads.append(dict(payload))
            if len(payloads) == 1:
                return {"hits": []}
            return {
                "hits": [
                    {
                        "url": CONFIRMED_URL,
                        "title": "宿舍条件讨论",
                        "snippet": "华东交通大学吧",
                    }
                ]
            }

    class UnusedPublic:
        def search_public(self, *args: object, **kwargs: object) -> SearchOutcome:
            raise AssertionError("浏览器原生检索可用时不应回退公开索引")

    attempts = search_candidates(
        BrowserTiebaSearch(Runtime(), UnusedPublic()),  # type: ignore[arg-type]
        "account-1",
        analysis,
        stop_event=None,
        deadline_seconds=10,
    )

    assert [payload["terms"] for payload in payloads] == ["宿舍", "寝室"]
    assert all("获取" not in str(payload["terms"]) for payload in payloads)
    assert len(attempts.records) == 2
    assert "实际吧内检索词：宿舍" in (attempts.records[0].detail or "")
    assert "实际吧内检索词：寝室" in (attempts.records[1].detail or "")


@pytest.mark.parametrize(
    ("question", "expected_expressions"),
    [
        ("我想获取关于宿舍的讨论", ["宿舍", "寝室"]),
        ("帮我收集关于宿舍的讨论", ["宿舍", "寝室"]),
        ("想了解宿舍", ["宿舍", "寝室"]),
    ],
)
def test_natural_language_samples_share_the_same_execution_plan(
    question: str, expected_expressions: list[str]
) -> None:
    """参数化自然语言样例得到同一组真实执行词。"""
    rounds = plan_query_rounds(parse_tieba_request(question))

    assert [item.execution_terms for item in rounds] == expected_expressions
    assert all("获取" not in item.execution_terms for item in rounds)
    assert all("贴吧" not in item.execution_terms for item in rounds)


def test_chat_projection_shows_plan_goals_and_actual_terms(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """聊天输入 → 检索端口参数 → 聊天投影三处使用同一组执行词。"""
    _register(client)
    question = "我想获取关于宿舍的讨论"
    analysis = parse_tieba_request(question)
    rounds = plan_query_rounds(analysis)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, question, module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["status"] == "empty"
    assert tieba["topic_terms"] == ["宿舍"]
    assert [item["goal"] for item in tieba["query_rounds"]] == [
        item.goal for item in rounds
    ]
    assert [item["execution_terms"] for item in tieba["query_rounds"]] == [
        item.execution_terms for item in rounds
    ]
    assert port.queries == [item.query for item in rounds]
    assert all("获取" not in query for query in port.queries)
    assert "检索计划：" in assistant["content"]
    assert "原词查询「宿舍」" in assistant["content"]
    assert "常用说法查询「寝室」" in assistant["content"]


def test_chat_action_only_request_asks_for_a_topic_without_searching(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "帮我获取一下", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["tieba_research"]["status"] == "clarification"
    assert assistant["tieba_research"]["pending"]["kind"] == "clarification"
    assert port.queries == []


def test_zero_result_chat_projection_reports_the_changed_expression(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """零结果后的调整在聊天投影中可见，且确实换成另一条检索表达。"""
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想获取关于宿舍的讨论", module_id="tieba")
    _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert len(port.queries) == 2
    assert "宿舍" in port.queries[0]
    assert "寝室" in port.queries[1]
    assert "获取" not in port.queries[0] and "获取" not in port.queries[1]
