"""贴吧模块的停止、失败、来源限制和时间边界。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.official import STATUS_VERIFIED, official_query
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from bridges.tieba.service import TiebaResearchService
from tests.tieba.support import (
    CONFIRMED_URL,
    _create_conversation,
    _FakeOfficialReader,
    _FakeReader,
    _FakeSearchPort,
    _gateway_with,
    _install_tieba_service,
    _read_result,
    _register,
    _run_and_read,
    _send,
    _SilentAdapter,
)


def test_unreadable_pages_are_listed_with_their_read_reason(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """候选帖读不到页面时只给帖链，并写明「页面不可读」的具体原因。"""
    _register(client)
    blocked_url = "https://tieba.baidu.com/p/10745250786"
    port = _FakeSearchPort(hits=[TiebaSearchHit(url=blocked_url, title="中秋放假通知", snippet="")])
    reader = _FakeReader()  # 默认全部命中访问限制
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["status"] == "links_only"
    assert tieba["candidate_links"][0]["source"] == "tavily"
    assert "访问受限" in tieba["candidate_links"][0]["unconfirmed_reason"]
    assert "页面不可读 1 条" in " ".join(tieba["evidence_boundary"])
    assert "页面不可读" in assistant["content"]


def test_holiday_question_runs_official_check_without_filling_in_a_year(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """放假安排走既有官方核验路径，与贴吧讨论分列；没有年份就不补年份。"""
    _register(client)
    analysis = parse_tieba_request("华东交通大学吧 中秋节放假")
    official_hit = TiebaSearchHit(
        url="https://www.ecjtu.edu.cn/xysh.htm",
        title="关于中秋节放假安排的通知-华东交通大学",
        snippet="",
    )
    port = _FakeSearchPort(
        per_query={
            plan_queries(analysis)[0]: [
                TiebaSearchHit(
                    url="https://tieba.baidu.com/p/10745250786",
                    title="中秋放假讨论",
                    snippet="",
                )
            ],
            official_query(analysis): [official_hit],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, title="中秋放假讨论"
            )
        }
    )
    official_reader = _FakeOfficialReader(
        {
            official_hit.url: TiebaOfficialCheck(
                title="关于中秋节放假安排的通知-华东交通大学",
                url=official_hit.url,
                host="www.ecjtu.edu.cn",
                fetched_at=datetime(2026, 9, 27, 3, 0, tzinfo=UTC),
                status=STATUS_VERIFIED,
                excerpt="中秋节放假安排：9 月 25 日至 27 日放假调休，共 3 天。",
                matched_terms=["放假", "中秋节"],
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader, official_reader=official_reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["official_check_requested"] is True
    assert official_reader.fetched == [official_hit.url]
    assert tieba["official_checks"][0]["status"] == "verified"
    assert "官方原文摘录" in assistant["content"]
    assert "不能替代官方规定" in assistant["content"]
    # 官方核验限定学校官方域名；原句没有年份，查询词与说明都不补年份。
    assert any(query.startswith("site:ecjtu.edu.cn") for query in port.queries)
    assert all("20" not in query for query in port.queries)
    assert any("不假定年份" in note for note in tieba["evidence_boundary"])


def test_stop_during_search_marks_message_stopped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """停止在节点边界生效：消息收敛为已停止，且不继续读取页面。"""
    _register(client)
    reader = _FakeReader()

    def stop_on_call(stop_event: object | None) -> None:
        if stop_event is not None:
            stop_event.set()  # type: ignore[attr-defined]

    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/10745250786", title="宿舍条件", snippet=""
            )
        ],
        on_call=stop_on_call,
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 宿舍 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "stopped"
    assert assistant["tieba_research"]["status"] == "stopped"
    assert reader.read_urls == []
    assert "已停止" in assistant["content"]


def test_other_modules_still_rejected_and_no_silent_search(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求契约内但子图尚未接入的模块仍被显式拒绝，且不产生任何检索。"""
    _register(client)
    # 三个日常模块已接入，这里把 paper 临时从可用集合摘掉，复现
    # 「请求契约合法、子图尚未接入」的构造（拒绝路径与具体模块无关）。
    monkeypatch.setattr(
        "bridges.chat.graph.AVAILABLE_MODULE_IDS",
        frozenset({"commute", "tieba"}),
    )
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(
        client,
        conversation_id,
        "找深度学习入门论文",
        module_id="paper",
    )
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"
    assert port.queries == []


def test_plain_chat_tieba_request_starts_research_without_a_suggestion(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """聊天入口里的明确贴吧问题直接启动研究，不要求用户再点建议。"""
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧里大家都在说宿舍条件吗")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    assert assistant["route"]["module_id"] == "tieba"
    assert assistant["module_suggestion"] is None
    assert assistant["tieba_research"]["status"] == "empty"
    assert assistant["tieba_research"]["original_question"] == (
        "华东交通大学吧里大家都在说宿舍条件吗"
    )
    assert port.queries


def test_study_mode_rejects_daily_module(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """学习模式不接受日常模块 ID（贴吧模块也不例外）。"""
    del generation_helpers
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001

    response = client.post(
        "/chat/first-turn",
        json={
            "mode": "study",
            "content": "华东交通大学吧 转专业",
            "module_id": "tieba",
            "idempotency_key": "study-tieba-1",
        },
    )

    assert response.status_code == 422
    assert port.queries == []


@pytest.mark.parametrize(
    "status",
    [
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.RATE_LIMITED,
        ModuleQueryStatus.CANCELLED,
    ],
)
def test_terminal_query_states_stop_the_bounded_plan(status: ModuleQueryStatus) -> None:
    """永久错误、超时、限流与取消都只发出计划内的第一条查询。"""
    port = _FakeSearchPort(status=status, retryable=False)
    service = TiebaResearchService(search=port, reader=_FakeReader())
    analysis = parse_tieba_request("华东交通大学吧 中秋节放假")

    attempts = service._search_candidates("account-1", analysis, stop_event=None)

    assert len(port.queries) == 1
    assert attempts.hits == ()
    assert attempts.rounds and "未完成" in attempts.rounds[0]


def test_stop_after_the_first_round_skips_the_fallback_query() -> None:
    """停止发生在首轮之后：备用词不再发出，逐轮说明写明「因你已停止」。"""
    stop_event = threading.Event()
    port = _FakeSearchPort(hits=[], on_call=lambda _event: stop_event.set())
    service = TiebaResearchService(search=port, reader=_FakeReader())
    analysis = parse_tieba_request("华东交通大学吧 中秋节放假")

    attempts = service._search_candidates("account-1", analysis, stop_event=stop_event)

    assert len(port.queries) == 1
    assert attempts.hits == ()
    assert attempts.rounds[-1] == "备用放宽词查询因你已停止而未执行。"


def test_retryable_transient_failure_still_uses_the_fallback_query() -> None:
    """可重试的临时失败不终止计划：备用查询命中后候选进入读取路径。"""
    analysis = parse_tieba_request("华东交通大学吧 中秋节放假")
    primary, relaxed = plan_queries(analysis)
    fallback_hit = TiebaSearchHit(
        url="https://tieba.baidu.com/p/10745250786", title="中秋放假讨论", snippet=""
    )
    port = _FakeSearchPort(
        status=ModuleQueryStatus.TIMEOUT,
        error_message="搜索提供方超时，请稍后重试。",
        retryable=True,
        per_query={primary: [], relaxed: [fallback_hit]},
    )
    service = TiebaResearchService(search=port, reader=_FakeReader())

    attempts = service._search_candidates("account-1", analysis, stop_event=None)

    assert port.queries == [primary, relaxed]
    assert [hit.url for hit in attempts.hits] == [fallback_hit.url]


def test_holiday_discussion_uses_scoped_fallback_and_real_replies(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """用户原句经真实聊天入口执行：两轮均限站，最终引用实际读取的讨论。"""
    _register(client)
    question = "我想了解国庆节假期的讨论"
    analysis = parse_tieba_request(question)
    primary, fallback = plan_queries(analysis)
    port = _FakeSearchPort(
        per_query={
            primary: [],
            fallback: [TiebaSearchHit(url=CONFIRMED_URL, title="国庆假期讨论", snippet="")],
        }
    )
    reply = TiebaReply(floor=2, posted_at="2026-10-01", content="我们国庆假期准备回家。")
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                title="国庆假期讨论",
                status=ReadStatus.READ,
                replies=(reply,),
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    adapter = _SilentAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, question, module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    result = assistant["tieba_research"]
    assert result["status"] == "success"
    assert result["topic_terms"] == ["国庆节", "假期"]
    assert result["question_kind"] == "experience"
    assert result["official_checks"] == []
    assert result["time_filter"]["year"] is None
    assert port.queries == [primary, fallback]
    assert all(query.startswith("site:tieba.baidu.com/p/") for query in port.queries)
    assert result["confirmed_posts"][0]["replies"][0]["content"] == reply.content
    assert reply.content in assistant["content"]
    assert adapter.calls == 0
