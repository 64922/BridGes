"""贴吧逐轮搜索、回退与候选覆盖行为。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaSearchHit,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from bridges.tieba.service import TiebaResearchService
from tests.tieba.support import (
    CONFIRMED_URL,
    REAL_REPLIES,
    _create_conversation,
    _FakeReader,
    _FakeSearchPort,
    _gateway_with,
    _install_tieba_service,
    _raw_non_thread_hits,
    _read_result,
    _register,
    _run_and_read,
    _send,
    _SilentAdapter,
)


def test_success_without_usable_candidates_runs_the_planned_fallback_query(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """首轮检索成功但 5 条原始结果都不是帖子页：继续执行计划内的备用放宽词。

    原缺陷：成功记录 ``retryable=false`` 让编排提前退出，计划里的第二条查询
    从不执行（模拟调用计数为 1，预期为有界的 2）。
    """
    _register(client)
    primary = plan_queries(parse_tieba_request("华东交通大学吧 中秋节放假"))[0]
    relaxed = plan_queries(parse_tieba_request("华东交通大学吧 中秋节放假"))[1]
    port = _FakeSearchPort(
        per_query={
            primary: _raw_non_thread_hits(5),
            relaxed: [
                TiebaSearchHit(
                    url="https://tieba.baidu.com/p/10745250786",
                    title="中秋节放假安排讨论",
                    snippet="华东交通大学吧. 关注18.9w贴子786.1w. App内查看.",
                )
            ],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="中秋节放假安排讨论",
                replies=REAL_REPLIES,
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == [primary, relaxed]
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "success"
    assert tieba["confirmed_posts"][0]["url"] == CONFIRMED_URL
    assert len(tieba["queries"]) == 2
    # 首轮那 5 条非帖子链接逐条留痕，且计入证据边界。
    assert [item["evidence"] for item in tieba["rejected_candidates"]] == [
        "搜索结果不是帖子页面链接"
    ] * 5
    assert any("非帖子链接 5 条" in note for note in tieba["evidence_boundary"])
    assert "备用放宽词查询" in assistant["content"]


def test_two_rounds_without_usable_candidates_explain_the_empty_state(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """两轮都没有可用候选：有界终止（2 次查询），空态给出可核对的过滤理由。"""
    _register(client)
    queries = plan_queries(parse_tieba_request("华东交通大学吧 中秋节放假"))
    port = _FakeSearchPort(
        per_query={
            queries[0]: _raw_non_thread_hits(5),
            queries[1]: _raw_non_thread_hits(3, offset=5),
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == list(queries)
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "empty"
    assert tieba["confirmed_posts"] == []
    assert tieba["candidate_links"] == []
    # 原始命中数（8）不等于确认帖子数（0），空态必须说清 8 条都去了哪里。
    assert "共取得 8 条原始搜索结果" in tieba["empty_reason"]
    assert "非帖子链接 8 条" in tieba["empty_reason"]
    assert "原始命中数不等于确认帖子数" in " ".join(tieba["evidence_boundary"])
    assert "没有可确认属于" in assistant["content"]
    assert tieba["retryable"] is False


def test_two_rounds_with_zero_hits_explain_the_empty_state(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """两轮都零结果的空态：说明「本轮没有返回公开帖子」，不编造过滤理由。"""
    _register(client)
    queries = plan_queries(parse_tieba_request("华东交通大学吧 中秋节放假"))
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == list(queries)
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "empty"
    assert tieba["empty_reason"] == "本轮检索没有返回可确认属于「华东交通大学吧」的公开帖子。"
    assert tieba["rejected_candidates"] == []
    assert tieba["candidate_links"] == []
    assert "本轮检索没有返回可确认属于" in assistant["content"]


def test_repeated_links_across_rounds_are_listed_once_and_counted_as_duplicates(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """两轮返回同一条链接：剔除记录只列一次，多出来的那次算作重复链接。"""
    _register(client)
    queries = plan_queries(parse_tieba_request("华东交通大学吧 中秋节放假"))
    repeated = _raw_non_thread_hits(5)
    port = _FakeSearchPort(per_query={queries[0]: repeated, queries[1]: repeated})
    _install_tieba_service(sqlite_app, port=port, reader=_FakeReader())
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == list(queries)
    tieba = assistant["tieba_research"]
    # 原始命中 10 条，其中 5 条是重复链接：剔除记录只有 5 条唯一链接。
    assert len(tieba["rejected_candidates"]) == 5
    assert len({item["url"] for item in tieba["rejected_candidates"]}) == 5
    assert "共取得 10 条原始搜索结果" in tieba["empty_reason"]
    assert "重复链接 5 条" in tieba["empty_reason"]
    assert any("重复链接只列一次" in note for note in tieba["evidence_boundary"])
    # 正文里同一条链接不会出现两遍。
    for item in tieba["rejected_candidates"]:
        assert assistant["content"].count(item["url"]) == 1


def test_permanent_error_stops_without_running_the_fallback_query(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """永久错误直接终止：不因为存在备用词就无条件重试。"""
    _register(client)
    port = _FakeSearchPort(
        status=ModuleQueryStatus.ERROR,
        error_code="web_search_unauthorized",
        error_message="搜索凭据无效，请先在设置中更新。",
        retryable=False,
    )
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert len(port.queries) == 1
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "web_search_unauthorized"
    assert assistant["tieba_research"]["status"] == "error"
    assert assistant["tieba_research"]["retryable"] is False


def test_exhausted_search_budget_skips_the_fallback_query(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """超出检索预算就停止：备用词不执行，并把原因写进证据边界。"""
    _register(client)
    port = _FakeSearchPort(hits=_raw_non_thread_hits(5))
    service = TiebaResearchService(
        search=port, reader=_FakeReader(), official_reader=None, search_deadline_seconds=0.0
    )
    sqlite_app.state.tieba_research_service = service
    sqlite_app.state.chat_service._tieba_research = service  # noqa: SLF001
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert len(port.queries) == 1
    assert any(
        "超出本轮检索预算" in note for note in assistant["tieba_research"]["evidence_boundary"]
    )
