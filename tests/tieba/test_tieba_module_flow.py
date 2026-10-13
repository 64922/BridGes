"""Issue 14：贴吧研究模块基础旅程与结果交付。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from tests.tieba.support import (
    CONFIRMED_URL,
    REAL_REPLIES,
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


def test_confirmed_thread_is_read_with_floors_times_and_sections(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式选择贴吧模块：确认真属该吧的帖子 + 真实楼层与时间 + 分区归纳。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://c.tieba.baidu.com/p/10745250786?lp=home_main_thread_pb",
                title="东北电力和华东交通哪个好",
                snippet="华东交通大学吧. 关注18.9w贴子786.1w. App内查看.",
            )
        ]
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    adapter = _SilentAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 东北电力和华东交通哪个好", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "success"
    assert tieba["original_question"] == "华东交通大学吧 东北电力和华东交通哪个好"
    assert port.queries == list(
        plan_queries(parse_tieba_request("华东交通大学吧 东北电力和华东交通哪个好"))
    )
    assert reader.read_urls == [CONFIRMED_URL]
    post = tieba["confirmed_posts"][0]
    assert post["url"] == CONFIRMED_URL
    assert post["title"] == "东北电力和华东交通哪个好"
    assert post["replies_obtained"] is True
    assert [reply["floor"] for reply in post["replies"]] == [1, 7, 12]
    assert "第 1 楼" in assistant["content"]
    assert "2025-05-26 08:11" in assistant["content"]
    assert "已读取" in assistant["content"] and "楼层 1–12" in assistant["content"]
    assert "可核验的个人经历" in assistant["content"]
    assert "未取得回复内容" not in assistant["content"]
    assert adapter.calls == 0  # 贴吧轮不调用模型


def test_blocked_reads_degrade_to_links_only_with_explicit_missing_note(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """读取被访问限制挡住：只给帖链，并明确归属未确认且未取得回复内容。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/10745250786",
                title="东北电力和华东交通哪个好",
                snippet="",
            )
        ]
    )
    reader = _FakeReader()  # 默认全部命中访问限制
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "links_only"
    assert tieba["confirmed_posts"] == []
    assert tieba["candidate_links"][0]["url"] == "https://tieba.baidu.com/p/10745250786"
    assert "未取得回复内容" in assistant["content"]
    assert "归属未确认" in assistant["content"]
    assert any("搜索摘要不足以确认归属" in note for note in tieba["evidence_boundary"])
    assert assistant["content"].count("https://tieba.baidu.com/p/10745250786") >= 1


def test_snippet_claiming_target_forum_still_needs_a_real_read(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """摘要自称目标吧、但页面读不到：仍然只算帖链，绝不当成已确认归属。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/10745250786",
                title="东北电力和华东交通哪个好",
                snippet="华东交通大学吧关注18.9w 贴子786.1w",
            )
        ]
    )
    reader = _FakeReader()  # 默认全部命中访问限制
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    tieba = assistant["tieba_research"]
    # 摘要里的吧头只是排除他吧用的弱证据，不能反过来确认归属。
    assert tieba["status"] == "links_only"
    assert tieba["confirmed_posts"] == []
    assert tieba["candidate_links"][0]["url"] == "https://tieba.baidu.com/p/10745250786"
    assert "未取得回复内容" in assistant["content"]


def test_other_forum_threads_are_filtered_with_evidence(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """他吧同名帖被过滤：搜索摘要与页面声明两种证据都能剔除。"""
    _register(client)
    other_url = "https://tieba.baidu.com/p/5668552372"
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://c.tieba.baidu.com/p/5668552372",
                title="上海交通大学研究生吧",
                snippet="上海交通大学研究生吧・ 华东交通大学吧关注19w",
            ),
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/6180625581",
                title="轻大17级学长来啦",
                snippet="",
            ),
        ]
    )
    reader = _FakeReader(
        {
            "https://tieba.baidu.com/p/6180625581": _read_result(
                url="https://tieba.baidu.com/p/6180625581",
                status=ReadStatus.READ,
                forum_name="郑州轻工业大学吧",
                title="轻大17级学长来啦",
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 宿舍 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["confirmed_posts"] == []
    assert tieba["candidate_links"] == []
    rejected = {item["url"]: item["evidence"] for item in tieba["rejected_candidates"]}
    assert rejected[other_url] == "搜索结果标题就是其他贴吧的名称"
    assert "郑州轻工业大学吧" in rejected["https://tieba.baidu.com/p/6180625581"]
    assert "郑州轻工业大学吧" in assistant["content"]
    assert "已剔除" in assistant["content"]


def test_official_check_is_separate_from_forum_experience(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """涉及办事流程时追加官方核验，官方原文与吧友经历分开展示。"""
    _register(client)
    official_hit = TiebaSearchHit(
        url="https://jwc.ecjtu.edu.cn/info/1041/4572.htm",
        title="转专业管理办法-教务处",
        snippet="",
    )
    port = _FakeSearchPort(
        per_query={
            "site:tieba.baidu.com/p/ 华东交通大学吧 转专业 条件": [
                TiebaSearchHit(
                    url="https://tieba.baidu.com/p/10745250786",
                    title="转专业经验",
                    snippet="",
                )
            ],
            "site:ecjtu.edu.cn 华东交通大学 转专业 条件": [official_hit],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="转专业经验",
                replies=(
                    TiebaReply(
                        floor=3,
                        posted_at="2025-03-02 10:00",
                        content="我去年转专业了，先找辅导员再报教务处。",
                    ),
                ),
            )
        }
    )
    official_reader = _FakeOfficialReader()
    _install_tieba_service(sqlite_app, port=port, reader=reader, official_reader=official_reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["official_check_requested"] is True
    assert tieba["official_checks"][0]["status"] == "verified"
    assert "官方原文摘录" in assistant["content"]
    assert "不能替代官方规定" in assistant["content"]
    assert "第 3 楼" in assistant["content"]
    # 官方检索只在官方域名上取候选；本用例给了官方链接，读取必须发生。
    assert official_reader.fetched == ["https://jwc.ecjtu.edu.cn/info/1041/4572.htm"]
    assert any("site:ecjtu.edu.cn" in query for query in port.queries)


def test_official_check_skipped_for_plain_topic(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通话题不追加官方核验，也不出现官方结论段落。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/10745250786", title="食堂怎么样", snippet=""
            )
        ]
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    official_reader = _FakeOfficialReader()
    _install_tieba_service(sqlite_app, port=port, reader=reader, official_reader=official_reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 食堂 怎么样", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["tieba_research"]["official_check_requested"] is False
    assert assistant["tieba_research"]["official_checks"] == []
    assert official_reader.fetched == []
    assert "官方" not in assistant["content"]


def test_clarification_then_resume_keeps_original_question_and_boundary(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """没有可检索主题时先问一项；回答后从等待状态恢复并沿用原问题。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url="https://tieba.baidu.com/p/10745250786", title="宿舍条件", snippet=""
            )
        ]
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "贴吧里大家都怎么说？", module_id="tieba")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["tieba_research"]["status"] == "clarification"
    assert first["tieba_research"]["pending"]["module_id"] == "tieba"
    assert port.queries == []  # 提问阶段不发起任何外部检索

    _send(client, conversation_id, "宿舍 条件", module_id="tieba")
    second = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert second["tieba_research"]["status"] == "success"
    assert second["tieba_research"]["original_question"] == "贴吧里大家都怎么说？"
    assert port.queries == list(plan_queries(parse_tieba_request("华东交通大学吧 宿舍 条件")))
    assert second["tieba_research"]["pending"] is None


def test_search_failure_keeps_query_and_is_retryable(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """检索失败如实报失败并保留查询词，消息可重试。"""
    _register(client)
    port = _FakeSearchPort(
        status=ModuleQueryStatus.ERROR,
        error_code="web_search_provider",
        error_message="搜索提供方暂时不可用，请稍后重试。",
    )
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 条件", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "web_search_provider"
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "error"
    assert tieba["queries"][0]["query"] == "site:tieba.baidu.com/p/ 华东交通大学吧 转专业 条件"
    assert tieba["retryable"] is True
