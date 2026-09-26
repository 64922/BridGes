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
from bridges.tieba.official import STATUS_VERIFIED
from bridges.tieba.searching import SearchOutcome, query_record
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
        from bridges.tieba.searching import classify_hits

        candidates, rejected = classify_hits(hits)
        return SearchOutcome(
            record=record, hits=hits, candidates=candidates, rejected=rejected
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
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """其他未接入模块仍被显式拒绝，且不产生任何检索。"""
    _register(client)
    port = _FakeSearchPort(hits=[])
    _install_tieba_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    # 反例取调用时尚未接入的 github（Issue 15 合并后 career 已可用）。
    _send(client, conversation_id, "帮我推荐几个开源项目", module_id="github")
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
