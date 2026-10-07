"""中文论文请求回归：主题完整、数量独立、实际查询可召回结果。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleWaitState
from bridges.paper.parsing import parse_paper_request, pending_payload
from bridges.paper.planning import plan_queries
from bridges.paper.topics import extract_topic_phrase
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register
from tests.paper.test_paper_module_flow import (
    _candidate,
    _FakePaperSource,
    _install_paper_source,
    _run_and_read,
    _send,
    _SilentAdapter,
    client,  # noqa: F401 - 复用真实 API 测试夹具
    sqlite_app,  # noqa: F401 - 复用真实 SQLite 应用夹具
)


@pytest.mark.parametrize(
    ("content", "topic", "query", "count"),
    [
        ("给我找三篇关于机器学习的论文", "机器学习", "machine learning", 3),
        ("给我找3篇关于机器学习的论文", "机器学习", "machine learning", 3),
        ("帮我找两篇深度学习论文", "深度学习", "deep learning", 2),
        ("找 machine learning 论文", "machine learning", "machine learning", 5),
    ],
)
def test_topic_and_quantity_are_separate(
    content: str, topic: str, query: str, count: int
) -> None:
    analysis = parse_paper_request(content)
    assert analysis.original_phrase == topic
    assert analysis.final_query == query
    assert analysis.clarification is None
    assert all(plan.target_count == count for plan in plan_queries(analysis))


def test_unknown_learning_topic_is_not_cut_or_polluted_by_quantity() -> None:
    assert extract_topic_phrase("帮我找三篇关于流形学习的论文") == "流形学习"
    assert extract_topic_phrase("给我找三篇论文") is None


def test_broad_domain_does_not_replace_specific_topic() -> None:
    analysis = parse_paper_request("给我找三篇关于机器学习中的异常检测的论文")
    assert analysis.original_phrase == "异常检测"
    assert analysis.final_query == "anomaly detection"


def test_quantity_survives_topic_clarification() -> None:
    first = parse_paper_request("给我找三篇论文")
    pending = ModuleWaitState(
        module_id="paper", kind="clarification", question="想找哪个研究主题的论文？",
        origin_message_id="assistant-1", context=pending_payload(first, ambiguous_term=None),
        created_at=datetime.now(UTC),
    )
    resumed = parse_paper_request("机器学习", pending=pending)
    assert resumed.final_query == "machine learning"
    assert plan_queries(resumed)[0].target_count == 3


@pytest.mark.parametrize(
    ("content", "count"),
    [("给我找三篇关于机器学习的论文", 3), ("找3篇 machine learning 论文", 3),
     ("找两篇机器学习论文", 2), ("给我找三篇论文", 3)],
)
def test_machine_learning_request_returns_requested_papers_through_chat(
    sqlite_app: Any,  # noqa: F811 - pytest 通过同名参数注入导入的夹具
    client: TestClient,  # noqa: F811 - pytest 通过同名参数注入导入的夹具
    generation_helpers: dict[str, Any],
    content: str,
    count: int,
) -> None:
    """走模块菜单对应的逐消息派发、父图、论文子图及持久化结果。"""
    _register(client)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    source = _install_paper_source(
        sqlite_app,
        _FakePaperSource(candidates=[
            _candidate(
                f"2401.0000{index}",
                f"Machine Learning Study {index}",
                "A study of machine learning methods and applications.",
            )
            for index in range(1, 6)
        ]),
    )
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, content, module_id="paper")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id, created
    )
    if content == "给我找三篇论文":
        assert assistant["paper_search"]["status"] == "clarification"
        created = _send(client, conversation_id, "机器学习", module_id="paper")
        assistant = _run_and_read(
            sqlite_app, client, generation_helpers["drive"], conversation_id, created
        )
    paper = assistant["paper_search"]
    assert paper["status"] == "success"
    assert source.queries == ["machine learning"]
    assert paper["final_query"] == "machine learning"
    assert paper["requested_count"] == count
    assert len(paper["papers"]) == count
    assert all(item["abs_url"].startswith("https://arxiv.org/abs/") for item in paper["papers"])
    assert "没有找到匹配的论文" not in assistant["content"]
