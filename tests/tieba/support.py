"""贴吧端到端验收共用的脚本化适配器与聊天助手。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.official import STATUS_VERIFIED
from bridges.tieba.searching import SearchOutcome, query_record
from bridges.tieba.service import TiebaResearchService
from tests.chat.test_chat_api import (
    _create_conversation as _create_conversation,
)
from tests.chat.test_chat_api import (
    _gateway_with,
)
from tests.chat.test_chat_api import (
    _register as _register,
)

TARGET_FORUM = "华东交通大学吧"

CONFIRMED_URL = "https://tieba.baidu.com/p/10745250786"

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


class _SilentAdapter:
    """普通聊天用的静默流式适配器；贴吧轮不调用模型。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
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
        hits = tuple(self._hits) if not self._per_query else tuple(self._per_query.get(query, []))
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


def _run_and_read(app: Any, client: TestClient, drive: Any, conversation_id: str) -> dict[str, Any]:
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [message for message in projection["messages"] if message["role"] == "assistant"][
        -1
    ]
    return assistant


def _raw_non_thread_hits(count: int, *, offset: int = 0) -> list[TiebaSearchHit]:
    """真实运行记录里的形态：搜索结果页面都不是贴吧帖子页。

    ``offset`` 用来取另一批链接，让「两轮各自有独立命中」与「两轮命中重复」
    两种情形都能构造。
    """
    return [
        TiebaSearchHit(url=url, title=title, snippet="")
        for url, title in _RAW_NON_THREAD_HITS[offset : offset + count]
    ]


OFFICIAL_URL = "https://jwc.ecjtu.edu.cn/info/1041/4572.htm"


def _wire(
    sqlite_app: Any,
    port: _FakeSearchPort,
    *,
    reader: _FakeReader | None = None,
    official_reader: _FakeOfficialReader | None = None,
) -> None:
    _install_tieba_service(sqlite_app, port=port, reader=reader, official_reader=official_reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001


def _official_hit() -> TiebaSearchHit:
    return TiebaSearchHit(url=OFFICIAL_URL, title="转专业管理办法-教务处", snippet="")


def _post(
    *replies: TiebaReply,
    url: str = "https://tieba.baidu.com/p/10745250786",
    title: str = "东北电力和华东交通哪个好",
    pages_read: int = 1,
) -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="10745250786",
        url=url,
        title=title,
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.READ,
        pages_read=pages_read,
        pages_limit=2,
        total_pages=3,
        floor_min=replies[0].floor if replies else None,
        floor_max=replies[-1].floor if replies else None,
        replies_obtained=bool(replies),
        replies=list(replies),
        retrieved_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
