"""Issue 14 端到端合同：显式派发、确认归属、仅帖链降级、官方核验、等待与失败。

全部走真实 HTTP + SQLite + 后台执行器（假搜索服务与假页面读取，无外网）：

- 只有逐消息 ``module_id=tieba`` 或点击建议才启动，其他未接入模块仍被拒绝；
- 只有真的读到帖子页面并确认属于目标贴吧的帖子才进入确认结果，楼层与时间逐条引用；
- 读不到页面时如实降级为「仅帖链」，明确写出归属未确认与未取得回复内容；
- 证据指向其他贴吧的同名帖被剔除，剔除依据留在投影里；
- 涉及校规／费用／流程时追加官方核验，官方原文摘录与吧友经历分开展示；
- 澄清等待跨轮次恢复，停止与失败如实写回同一条消息。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.official import STATUS_VERIFIED, official_query
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import SearchOutcome, plan_queries, query_record
from bridges.tieba.service import TiebaResearchService
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

TARGET_FORUM = "华东交通大学吧"
CONFIRMED_URL = "https://tieba.baidu.com/p/10745250786"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


class _SilentAdapter:
    """普通聊天用的静默流式适配器；贴吧轮不调用模型。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        del capability, run_context, payload
        self.calls += 1
        yield StreamChunk(kind="delta", delta="好的。")


class _FakeSearchPort:
    """允许的搜索服务替身：按查询词返回脚本化结果，并记录收到的查询词。"""

    def __init__(
        self,
        *,
        hits: list[TiebaSearchHit] | None = None,
        status: ModuleQueryStatus = ModuleQueryStatus.SUCCESS,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = True,
        per_query: dict[str, list[TiebaSearchHit]] | None = None,
        on_call: Any | None = None,
    ) -> None:
        self.queries: list[str] = []
        self._hits = hits or []
        self._status = status
        self._error_code = error_code
        self._error_message = error_message
        self._retryable = retryable
        self._per_query = per_query or {}
        self._on_call = on_call

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        del account_id, reason, deadline
        self.queries.append(query)
        if self._on_call is not None:
            self._on_call(stop_event)
        hits = tuple(self._hits) if not self._per_query else tuple(
            self._per_query.get(query, [])
        )
        record = query_record(
            query=query,
            status=self._status,
            evidence_count=len(hits),
            error_code=self._error_code,
            error_message=self._error_message,
            retryable=self._retryable,
        )
        from bridges.tieba.searching import classify_hits_with_diagnostics

        candidates, rejected, diagnostics = classify_hits_with_diagnostics(hits)
        return SearchOutcome(
            record=record,
            hits=hits,
            candidates=candidates,
            rejected=rejected,
            diagnostics=diagnostics,
        )


class _FakeReader:
    """帖子页面读取替身：按链接返回脚本化读取结果，并记录读取过的链接。"""

    def __init__(self, results: dict[str, TiebaReadResult] | None = None) -> None:
        self.read_urls: list[str] = []
        self._results = results or {}

    def read(
        self,
        url: str,
        *,
        pages_limit: int = 2,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        del pages_limit, deadline
        from bridges.tieba.searching import canonical_url

        canonical = canonical_url(url)
        self.read_urls.append(canonical)
        if stop_event is not None and stop_event.is_set():
            return _read_result(url=canonical, status=ReadStatus.CANCELLED)
        return self._results.get(
            canonical, _read_result(url=canonical, status=ReadStatus.ACCESS_RESTRICTED)
        )


class _FakeOfficialReader:
    """官方页面读取替身：记录读取过的官方链接。"""

    def __init__(self, checks: dict[str, TiebaOfficialCheck] | None = None) -> None:
        self.fetched: list[str] = []
        self._checks = checks or {}

    def fetch(
        self,
        url: str,
        *,
        terms: tuple[str, ...],
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaOfficialCheck:
        del terms, stop_event, deadline
        self.fetched.append(url)
        check = self._checks.get(url)
        if check is not None:
            return check
        return TiebaOfficialCheck(
            title="转专业管理办法-教务处",
            url=url,
            host="jwc.ecjtu.edu.cn",
            fetched_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
            status=STATUS_VERIFIED,
            excerpt="学生转专业应当在大一学年结束前提出书面申请，经所在学院同意后报教务处审批。",
            matched_terms=["转专业"],
        )


def _read_result(
    *,
    url: str,
    status: ReadStatus,
    forum_name: str | None = TARGET_FORUM,
    title: str | None = "东北电力和华东交通哪个好",
    replies: tuple[TiebaReply, ...] = (),
    pages_read: int = 1,
    total_pages: int | None = 3,
) -> TiebaReadResult:
    floors = [reply.floor for reply in replies if reply.floor is not None]
    error_messages = {
        ReadStatus.ACCESS_RESTRICTED: "页面要求登录或触发了访问验证，未取得回复内容。",
        ReadStatus.UNRECOGNIZED: "页面结构与预期不符，未取得可核实的回复内容。",
        ReadStatus.TIMEOUT: "读取超时，未取得回复内容。",
        ReadStatus.CANCELLED: "已停止读取，未继续读取后续页面。",
    }
    return TiebaReadResult(
        url=url,
        thread_id=url.rsplit("/", 1)[-1],
        status=status,
        forum_name=forum_name if status in {ReadStatus.READ, ReadStatus.PARTIAL} else None,
        title=title if status in {ReadStatus.READ, ReadStatus.PARTIAL} else None,
        pages_read=pages_read if status in {ReadStatus.READ, ReadStatus.PARTIAL} else 0,
        pages_limit=2,
        total_pages=total_pages if status in {ReadStatus.READ, ReadStatus.PARTIAL} else None,
        floor_min=min(floors) if floors else None,
        floor_max=max(floors) if floors else None,
        replies=list(replies),
        error_code=f"tieba_read_{status.value}",
        error_message=error_messages.get(status),
        retrieved_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


REAL_REPLIES = (
    TiebaReply(
        floor=1,
        posted_at="2025-05-25 21:03",
        is_original_poster=True,
        content="求助东北电力和华东交通哪个更值得报，分数差不多。",
    ),
    TiebaReply(
        floor=7,
        posted_at="2025-05-26 08:11",
        content="我是过来人，电气方向就业更稳，但要看你自己的兴趣。",
    ),
    TiebaReply(
        floor=12,
        posted_at="2025-05-27 19:40",
        content="但是宿舍条件一般，听说新校区会好一点，不确定什么时候搬。",
    ),
)


def _install_tieba_service(
    app: Any,
    *,
    port: _FakeSearchPort,
    reader: _FakeReader | None = None,
    official_reader: _FakeOfficialReader | None = None,
) -> TiebaResearchService:
    """把贴吧模块的搜索与读取边界换成替身（模块编排与父图派发保持真实）。"""
    service = TiebaResearchService(
        search=port,
        reader=reader or _FakeReader(),
        official_reader=official_reader,
    )
    app.state.tieba_research_service = service
    app.state.chat_service._tieba_research = service  # noqa: SLF001
    return service


def _send(
    client: TestClient,
    conversation_id: str,
    content: str,
    *,
    module_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content}
    if module_id is not None:
        body["module_id"] = module_id
    response = client.post(f"/chat/conversations/{conversation_id}/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _run_and_read(
    app: Any, client: TestClient, drive: Any, conversation_id: str
) -> dict[str, Any]:
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]
    return assistant


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

    _send(
        client, conversation_id, "华东交通大学吧 东北电力和华东交通哪个好", module_id="tieba"
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "success"
    assert tieba["original_question"] == "华东交通大学吧 东北电力和华东交通哪个好"
    assert port.queries == ["tieba.baidu.com 华东交通大学吧 东北电力 华东交通"]
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
            "tieba.baidu.com 华东交通大学吧 转专业 条件": [
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
    _install_tieba_service(
        sqlite_app, port=port, reader=reader, official_reader=official_reader
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 条件", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    _install_tieba_service(
        sqlite_app, port=port, reader=reader, official_reader=official_reader
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 食堂 怎么样", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert first["tieba_research"]["status"] == "clarification"
    assert first["tieba_research"]["pending"]["module_id"] == "tieba"
    assert port.queries == []  # 提问阶段不发起任何外部检索

    _send(client, conversation_id, "宿舍 条件", module_id="tieba")
    second = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert second["tieba_research"]["status"] == "success"
    assert second["tieba_research"]["original_question"] == "贴吧里大家都怎么说？"
    assert port.queries == ["tieba.baidu.com 华东交通大学吧 宿舍 条件"]
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "web_search_provider"
    tieba = assistant["tieba_research"]
    assert tieba["status"] == "error"
    assert tieba["queries"][0]["query"] == "tieba.baidu.com 华东交通大学吧 转专业 条件"
    assert tieba["retryable"] is True


_RAW_NON_THREAD_HITS: list[tuple[str, str]] = [
    ("https://www.ecjtu.edu.cn/index.htm", "华东交通大学"),
    ("https://nani.baidu.com/home/main?id=tb.1.ce8f0206", "华东交大小益君的贴吧"),
    (
        "https://tieba.baidu.com/hottopic/browse/hottopic?topic_id=5674439",
        "中秋节放假安排专题",
    ),
    ("https://baike.baidu.com/item/中秋节", "中秋节"),
    ("https://zhidao.baidu.com/question/1", "中秋节放假几天"),
    ("https://www.ecjtu.edu.cn/xysh.htm", "校园生活"),
    ("https://zhidao.baidu.com/question/2", "中秋节期间食堂开放吗"),
    ("https://www.zhihu.com/question/1", "中秋节怎么安排"),
    ("https://tieba.baidu.com/f?kw=华东交通大学", "华东交通大学吧首页"),
]


def _raw_non_thread_hits(count: int, *, offset: int = 0) -> list[TiebaSearchHit]:
    """真实运行记录里的形态：搜索结果页面都不是贴吧帖子页。

    ``offset`` 用来取另一批链接，让「两轮各自有独立命中」与「两轮命中重复」
    两种情形都能构造。
    """
    return [
        TiebaSearchHit(url=url, title=title, snippet="")
        for url, title in _RAW_NON_THREAD_HITS[offset : offset + count]
    ]


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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert len(port.queries) == 1
    assert any(
        "超出本轮检索预算" in note
        for note in assistant["tieba_research"]["evidence_boundary"]
    )


def test_unreadable_pages_are_listed_with_their_read_reason(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """候选帖读不到页面时只给帖链，并写明「页面不可读」的具体原因。"""
    _register(client)
    blocked_url = "https://tieba.baidu.com/p/10745250786"
    port = _FakeSearchPort(
        hits=[TiebaSearchHit(url=blocked_url, title="中秋放假通知", snippet="")]
    )
    reader = _FakeReader()  # 默认全部命中访问限制
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    _install_tieba_service(
        sqlite_app, port=port, reader=reader, official_reader=official_reader
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 中秋节放假", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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
    # 六个日常模块已全部接入，这里把 career 临时从可用集合摘掉，复现
    # 「请求契约合法、子图尚未接入」的构造（拒绝路径与具体模块无关）。
    monkeypatch.setattr(
        "bridges.chat.graph.AVAILABLE_MODULE_IDS",
        frozenset({"paper", "commute", "resources", "tieba", "github"}),
    )
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "帮我推荐几个开源项目", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"
    assert port.queries == []


def test_plain_chat_suggests_tieba_without_searching(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天里的贴吧请求只给一键建议，不暗中检索。"""
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧里大家都在说宿舍条件吗")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    suggestion = assistant["module_suggestion"]
    assert suggestion["module_id"] == "tieba"
    assert suggestion["text"] == "华东交通大学吧里大家都在说宿舍条件吗"
    assert suggestion["needs_disambiguation"] is False
    assert assistant["tieba_research"] is None
    assert port.queries == []


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
