"""Issue 13 端到端合同：显式派发、真实清单、澄清恢复、失败与停止。

全部走真实 HTTP + SQLite + 后台执行器（假模型与假来源，无外网）：

- 资料子图只由逐消息 ``module_id`` 或「点击建议」显式启动；未接入的模块仍被拒绝；
- 结果消息包含保留的原词、实际查询词、真实链接、适用阶段、选择理由与由浅入深的顺序；
- 缺少学习层次时只问一项；回答后从等待状态恢复并检索相应主题；
- 两条来源分别记账：一条失败只标注自己的缺口，两条都无可用答复时如实报失败；
- 停止在节点边界收敛为已停止；普通聊天中的明显学习请求只给建议，不产生任何检索。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.resources.contracts import STAGE_ORDER
from bridges.resources.service import LearningResourcesService
from bridges.resources.sources import (
    BILIBILI_SOURCE,
    OPENALEX_SOURCE,
    OPENLIBRARY_SOURCE,
    BookCandidate,
    BookSearchOutcome,
    VideoCandidate,
    VideoDiscoveryOutcome,
    VideoVerifyOutcome,
)
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

NOW = datetime(2026, 9, 25, tzinfo=UTC)

BEGINNER_BOOK = BookCandidate(
    title="深度学习入门：基于Python的理论与实现",
    creators=["斋藤康毅"],
    year=2018,
    publisher="人民邮电出版社",
    isbn="9787115485586",
    source=OPENLIBRARY_SOURCE,
    url="https://openlibrary.org/works/OL27442600W",
)

HANDBOOK_BOOK = BookCandidate(
    title="动手学深度学习",
    creators=["李沐"],
    year=2023,
    publisher="人民邮电出版社",
    isbn="9787115588102",
    source=OPENLIBRARY_SOURCE,
    url="https://openlibrary.org/works/OL31179267W",
)

ADVANCED_BOOK = BookCandidate(
    title="深度学习进阶：自然语言处理",
    creators=["斋藤康毅"],
    year=2021,
    publisher=None,
    isbn=None,
    source=OPENLIBRARY_SOURCE,
    url="https://openlibrary.org/works/OL40000000W",
)

ENGLISH_BOOK = BookCandidate(
    title="Deep Learning",
    creators=["Ian Goodfellow"],
    year=2016,
    publisher=None,
    isbn=None,
    source=OPENALEX_SOURCE,
    url="https://openalex.org/works/W2963403868",
)

POWER_BOOK = BookCandidate(
    title="电力系统分析",
    creators=["何仰赞"],
    year=2017,
    publisher="华中科技大学出版社",
    isbn="9787568012345",
    source=OPENLIBRARY_SOURCE,
    url="https://openlibrary.org/works/OL50000000W",
)


def _video(video_id: str, title: str, seconds: int, uploader: str = "某 UP 主") -> VideoCandidate:
    return VideoCandidate(
        video_id=video_id,
        title=title,
        uploader=uploader,
        duration_seconds=seconds,
        published_at=datetime(2024, 5, 6, tzinfo=UTC),
        url=f"https://www.bilibili.com/video/{video_id}",
        description=f"{title}：公开视频简介（元数据来自哔哩哔哩公开接口）。",
    )


VIDEOS = [
    _video("BV1pu411o7BE", "深度学习零基础入门教程", 480),
    _video("BV1pu411o7BF", "深度学习框架讲解", 900),
    _video("BV1pu411o7BG", "深度学习原理与源码剖析", 2400),
]


class _FakeBookSource:
    """可控书目来源：真实结构的候选与统一记录，可指定失败与条数。"""

    def __init__(
        self,
        source: str = OPENLIBRARY_SOURCE,
        candidates: list[BookCandidate] | None = None,
        *,
        status: ModuleQueryStatus | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.source = source
        self.candidates = candidates or []
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
        self.queries: list[str] = []

    def search(
        self, account_id: str, query: str, *, limit: int, deadline: float | None = None
    ) -> BookSearchOutcome:
        del account_id, deadline
        self.queries.append(query)
        selected = self.candidates[:limit]
        status = self.status or (
            ModuleQueryStatus.SUCCESS if selected else ModuleQueryStatus.EMPTY
        )
        return BookSearchOutcome(
            query=query,
            candidates=selected,
            record=ModuleQueryRecord(
                source=self.source,
                query=query,
                status=status,
                evidence_count=len(selected),
                retrieved_at=NOW,
                error_code=self.error_code,
                error_message=self.error_message,
                retryable=self.retryable,
            ),
        )

    def close(self) -> None:
        return None


class _FakeDiscoverer:
    """可控视频发现：给出哔哩哔哩直达页，可在发现期间请求停止。"""

    def __init__(self, pages: list[str] | None = None, *, stop_on_discover: bool = False) -> None:
        self.pages = pages if pages is not None else [
            f"https://www.bilibili.com/video/{video.video_id}" for video in VIDEOS
        ]
        self.stop_on_discover = stop_on_discover
        self.queries: list[str] = []

    def discover(
        self,
        account_id: str,
        query: str,
        *,
        limit: int,
        stop_event: Any = None,
        deadline: float | None = None,
    ) -> VideoDiscoveryOutcome:
        del account_id, deadline
        self.queries.append(query)
        if self.stop_on_discover and stop_event is not None:
            # 模拟发现期间用户点停止：模块在下一个节点边界如实收敛为已停止。
            stop_event.set()
        pages = self.pages[:limit]
        return VideoDiscoveryOutcome(
            query=query,
            direct_pages=pages,
            record=ModuleQueryRecord(
                source="tavily",
                query=query,
                status=ModuleQueryStatus.SUCCESS if pages else ModuleQueryStatus.EMPTY,
                evidence_count=len(pages),
                retrieved_at=NOW,
            ),
        )


class _FakeVerifier:
    """可控视频核对：按直达页给出真实结构元数据，未通过的直接丢弃并计数。"""

    def __init__(
        self, candidates: list[VideoCandidate] | None = None, *, rejected: int = 0
    ) -> None:
        self.candidates = candidates if candidates is not None else list(VIDEOS)
        self.rejected = rejected
        self.pages: list[str] = []

    def verify(
        self, pages: list[str], *, account_id: str, deadline: float | None = None
    ) -> VideoVerifyOutcome:
        del account_id, deadline
        self.pages = list(pages)
        records = [
            ModuleQueryRecord(
                source=BILIBILI_SOURCE,
                query=page.rsplit("/", 1)[-1],
                status=ModuleQueryStatus.SUCCESS,
                evidence_count=1,
                retrieved_at=NOW,
            )
            for page in pages
        ]
        return VideoVerifyOutcome(
            candidates=list(self.candidates[: len(pages)]),
            records=records,
            rejected=self.rejected,
        )

    def close(self) -> None:
        return None


class _SilentAdapter:
    """普通聊天用的静默流式适配器（资料轮不调用模型）。"""

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        del capability, run_context, payload
        yield StreamChunk(kind="delta", delta="好的。")


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


def _install_resources_service(
    app: Any,
    *,
    books: list[_FakeBookSource] | None = None,
    discoverer: _FakeDiscoverer | None = None,
    verifier: _FakeVerifier | None = None,
) -> LearningResourcesService:
    """把资料模块的来源换成替身（模块编排、父图派发与消息落库保持真实）。"""
    service = LearningResourcesService(
        books=books or [],
        discoverer=discoverer,
        verifier=verifier,
    )
    app.state.learning_resources_service = service
    app.state.chat_service._learning_resources = service  # noqa: SLF001
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
    """驱动执行器到终态并返回该轮最新助手消息投影。"""
    drive(app)
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    return [
        message for message in projection["messages"] if message["role"] == "assistant"
    ][-1]


def test_explicit_resources_module_returns_ordered_checkable_list(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式选择学习资料推荐：两本书＋三条视频，真实链接、阶段与选择理由。"""
    _register(client)
    openlibrary = _FakeBookSource(
        candidates=[BEGINNER_BOOK, HANDBOOK_BOOK, ADVANCED_BOOK]
    )
    openalex = _FakeBookSource(source=OPENALEX_SOURCE, candidates=[ENGLISH_BOOK])
    discoverer = _FakeDiscoverer()
    verifier = _FakeVerifier()
    _install_resources_service(
        sqlite_app,
        books=[openlibrary, openalex],
        discoverer=discoverer,
        verifier=verifier,
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    resources = assistant["learning_resources"]
    assert resources["status"] == "success"
    # 原词逐字保留；英文写法只作为查询扩展，不替换原词。
    assert resources["original_phrase"] == "深度学习"
    assert resources["expansions"] == ["deep learning"]
    assert resources["final_query"] == "深度学习 deep learning"
    assert resources["level_label"] == "零基础入门"
    assert resources["pending"] is None

    # 默认目标：两本书 + 三条视频，缺一不可时如实说明（这里刚好足量）。
    assert resources["requested_books"] == 2
    assert resources["requested_videos"] == 3
    kinds = [item["kind"] for item in resources["items"]]
    assert kinds.count("book") == 2
    assert kinds.count("video") == 3

    # 由浅入深的顺序与逐项核对依据。
    orders = [item["order"] for item in resources["items"]]
    assert orders == [1, 2, 3, 4, 5]
    stages = [item["stage"] for item in resources["items"]]
    assert stages == sorted(stages, key=STAGE_ORDER.index)
    for item in resources["items"]:
        assert item["reason_zh"]
        assert item["match_basis"]
        assert item["url"].startswith("https://")
        assert item["unverified"]
    # 图书按书目信息核对（ISBN/出版社可查），视频标注未观看。
    books = [item for item in resources["items"] if item["kind"] == "book"]
    assert all(item["isbn"] or item["publisher"] for item in books)
    videos = [item for item in resources["items"] if item["kind"] == "video"]
    assert all("未观看" in item["reason_zh"] for item in videos)
    assert all(item["duration_seconds"] for item in videos)

    # 两条书目来源分别记账，视频走「发现 → 逐条核对」并逐条留下记录。
    sources = [record["source"] for record in resources["queries"]]
    assert sources[:3] == [OPENLIBRARY_SOURCE, OPENALEX_SOURCE, "tavily"]
    assert sources[3:] == [BILIBILI_SOURCE] * 3
    assert resources["queries"][0]["query"] == "深度学习 deep learning"
    assert resources["queries"][2]["query"] == "深度学习 零基础 入门 教程"
    assert all(record["status"] == "success" for record in resources["queries"])
    assert resources["queries"][0]["evidence_count"] == 3
    # 两个来源的条数都在证据边界里如实列出。
    assert any("Open Library 书目 返回 3 条候选" in note for note in resources["evidence_notes"])
    assert any("OpenAlex 图书记录 返回 1 条候选" in note for note in resources["evidence_notes"])

    # 正文可见真实查询词与直达链接，且不描述未观看视频的具体内容。
    assert "深度学习 deep learning" in assistant["content"]
    assert "深度学习 零基础 入门 教程" in assistant["content"]
    assert assistant["content"].count("https://") >= 5
    assert "未观看" in assistant["content"]


def test_book_gap_is_limited_to_its_own_source(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """一条书目来源失败只标注自己的缺口：另一条来源的真实结果照常给出。"""
    _register(client)
    failing = _FakeBookSource(
        status=ModuleQueryStatus.TIMEOUT,
        error_code="openlibrary_timeout",
        error_message="Open Library 检索超时，请重试。",
        retryable=True,
    )
    openalex = _FakeBookSource(source=OPENALEX_SOURCE, candidates=[ENGLISH_BOOK])
    _install_resources_service(
        sqlite_app,
        books=[failing, openalex],
        discoverer=_FakeDiscoverer(pages=[]),
        verifier=_FakeVerifier(),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    resources = assistant["learning_resources"]
    assert resources["status"] == "success"
    assert [item["kind"] for item in resources["items"]] == ["book"]
    assert resources["items"][0]["title"] == "Deep Learning"
    # 失败的那条如实带查询词与分类码，位置（来源）可核对。
    timeout = resources["queries"][0]
    assert timeout["source"] == OPENLIBRARY_SOURCE
    assert timeout["query"] == "深度学习 deep learning"
    assert timeout["status"] == "timeout"
    assert timeout["error_code"] == "openlibrary_timeout"
    assert any("图书还差" in note for note in resources["evidence_notes"])


def test_both_sources_hard_failure_marks_message_failed(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """两条来源都没有可用答复且至少一处硬失败：整轮如实失败并可重试。"""
    _register(client)
    _install_resources_service(
        sqlite_app,
        books=[
            _FakeBookSource(
                status=ModuleQueryStatus.ERROR,
                error_code="openlibrary_offline",
                error_message="当前无法连接 Open Library，请检查网络后重试。",
                retryable=True,
            )
        ],
        discoverer=None,
        verifier=None,
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "openlibrary_offline"
    assert "Open Library" in assistant["error_message"]
    resources = assistant["learning_resources"]
    assert resources["status"] == "error"
    assert resources["retryable"] is True
    assert resources["final_query"] == "深度学习 deep learning"
    assert resources["queries"][0]["error_code"] == "openlibrary_offline"
    assert resources["pending"] is None, "失败轮不得留下等待状态"

    # 重试：换回可用来源后同一用户消息重新派发到资料模块。
    _install_resources_service(
        sqlite_app,
        books=[_FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])],
        discoverer=_FakeDiscoverer(),
        verifier=_FakeVerifier(),
    )
    retried = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{assistant['message_id']}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    new_message_id = retried.json()["assistant_message"]["message_id"]
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    attempt = next(
        message for message in final["messages"] if message["message_id"] == new_message_id
    )
    assert attempt["learning_resources"]["status"] == "success"


def test_missing_level_asks_one_question_then_resumes(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """层次确实影响推荐且上下文不足：只问这一项，回答后从等待处恢复。"""
    _register(client)
    openlibrary = _FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])
    discoverer = _FakeDiscoverer()
    _install_resources_service(
        sqlite_app, books=[openlibrary], discoverer=discoverer, verifier=_FakeVerifier()
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "帮我找深度学习的资料", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    resources = assistant["learning_resources"]
    assert resources["status"] == "clarification"
    assert resources["pending"]["kind"] == "clarification"
    assert resources["pending"]["context"]["missing"] == "level"
    assert resources["original_phrase"] == "深度学习"
    assert assistant["content"].count("？") == 1
    assert openlibrary.queries == [], "澄清轮绝不检索"
    assert discoverer.queries == []

    # 回答后从该处恢复：原词保留、层次确定、真实检索。
    _send(
        client, conversation_id, "有点基础，主要是想应付期末", module_id="resources"
    )
    assistant2 = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    resources2 = assistant2["learning_resources"]
    assert resources2["status"] == "success"
    assert resources2["original_phrase"] == "深度学习"
    assert resources2["level_label"] == "有一定基础"
    assert resources2["goal"] == "备考"
    assert openlibrary.queries == ["深度学习 deep learning"]
    # 视频查询按层次带上后缀（不再问同一个层次问题）。
    assert discoverer.queries == ["深度学习 系统 讲解"]
    assert resources2["pending"] is None


def test_waiting_state_survives_reopen(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """等待状态随消息落库：重开历史仍能看到澄清问题与等待标记。"""
    _register(client)
    _install_resources_service(sqlite_app, books=[_FakeBookSource()])
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "帮我找深度学习的资料", module_id="resources")
    _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    reopened = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = [m for m in reopened["messages"] if m["role"] == "assistant"][0]
    assert assistant["learning_resources"]["pending"]["question"] == assistant["content"]
    assert assistant["learning_resources"]["pending"]["module_id"] == "resources"


def test_topic_mismatch_stops_without_padding(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """主题不匹配：停止推荐并请用户纠正，不凑数量、不写等待状态。"""
    _register(client)
    _install_resources_service(
        sqlite_app,
        books=[_FakeBookSource(candidates=[POWER_BOOK])],
        discoverer=_FakeDiscoverer(
            pages=["https://www.bilibili.com/video/BV1pu411o7BH"]
        ),
        verifier=_FakeVerifier(candidates=[_video("BV1pu411o7BH", "电力系统继电保护", 700)]),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    resources = assistant["learning_resources"]
    assert resources["status"] == "empty"
    assert resources["items"] == []
    assert resources["pending"] is None
    assert "没有覆盖你的原始说法" in assistant["content"]
    assert resources["queries"], "即使空结果也保留真实检索记录"


def test_no_candidates_reports_actual_counts(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """来源确实没返回条目：如实说明实际数量与每条来源的状态。"""
    _register(client)
    _install_resources_service(
        sqlite_app,
        books=[_FakeBookSource(candidates=[])],
        discoverer=_FakeDiscoverer(pages=[]),
        verifier=_FakeVerifier(),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    resources = assistant["learning_resources"]
    assert resources["status"] == "empty"
    assert resources["items"] == []
    assert "没有可推荐的条目" in assistant["content"]
    assert "深度学习 deep learning" in assistant["content"]


def test_rejected_video_is_reported_and_not_listed(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """核对不通过的视频：不进清单，但在证据边界里如实说明有几条被移除。"""
    _register(client)
    _install_resources_service(
        sqlite_app,
        books=[_FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])],
        discoverer=_FakeDiscoverer(),
        verifier=_FakeVerifier(candidates=[VIDEOS[0], VIDEOS[1]], rejected=1),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    resources = assistant["learning_resources"]
    titles = [item["title"] for item in resources["items"]]
    assert "深度学习原理与源码剖析" not in titles
    assert any("未通过哔哩哔哩元数据核对" in note for note in resources["evidence_notes"])
    assert any("视频还差 1 条" in note for note in resources["evidence_notes"])


def test_stop_during_discovery_marks_message_stopped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """停止：消息显示已停止并保留已发生的真实检索记录，不会变成失败。"""
    _register(client)
    openlibrary = _FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])
    _install_resources_service(
        sqlite_app,
        books=[openlibrary],
        discoverer=_FakeDiscoverer(stop_on_discover=True),
        verifier=_FakeVerifier(),
    )
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "stopped"
    resources = assistant["learning_resources"]
    assert resources["status"] == "stopped"
    assert "已停止" in assistant["content"]
    # 停止在 resources.search_videos 的节点边界生效：排序与生成没有执行。
    assert resources["items"] == []
    assert resources["queries"][0]["source"] == OPENLIBRARY_SOURCE
    assert resources["queries"][0]["query"] == "深度学习 deep learning"


def test_plain_chat_suggests_resources_module_without_searching(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """普通聊天里的明显学习请求只给一键建议（原文启动，不检索）。"""
    _register(client)
    openlibrary = _FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])
    discoverer = _FakeDiscoverer()
    _install_resources_service(
        sqlite_app, books=[openlibrary], discoverer=discoverer, verifier=_FakeVerifier()
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = _send(client, conversation_id, "我想自学深度学习，有点基础")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    suggestion = assistant["module_suggestion"]
    assert suggestion is not None
    assert suggestion["module_id"] == "resources"
    assert suggestion["label"] == "使用学习资料推荐"
    assert suggestion["text"] == "我想自学深度学习，有点基础"
    assert assistant["learning_resources"] is None
    assert openlibrary.queries == [], "建议本身绝不发起检索"
    assert discoverer.queries == []

    # 点击建议：同一用户消息以显式模块重新派发（不重复写用户消息）。
    dispatched = client.post(
        f"/chat/conversations/{conversation_id}/messages/"
        f"{assistant['message_id']}/retry",
        json={"module_id": "resources"},
    )
    assert dispatched.status_code == 200, dispatched.text
    assert (
        dispatched.json()["user_message"]["message_id"]
        == created["user_message"]["message_id"]
    )
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    users = [m for m in final["messages"] if m["role"] == "user"]
    assert len(users) == 1, "点击建议不得重复写用户消息"
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["learning_resources"]["status"] == "success"
    assert openlibrary.queries == ["深度学习 deep learning"]


def test_other_modules_still_rejected_and_no_silent_search(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求契约内但子图尚未接入的模块仍被明确拒绝；普通聊天不产生任何资料检索记录。"""
    _register(client)
    # 六个日常模块已全部接入，这里把 career 临时从可用集合摘掉，复现
    # 「请求契约合法、子图尚未接入」的构造（拒绝路径与具体模块无关）。
    monkeypatch.setattr(
        "bridges.chat.graph.AVAILABLE_MODULE_IDS",
        frozenset({"paper", "commute", "resources", "tieba", "github"}),
    )
    openlibrary = _FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])
    _install_resources_service(
        sqlite_app, books=[openlibrary], discoverer=_FakeDiscoverer()
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "帮我推荐几个开源项目", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"

    _send(client, conversation_id, "你好，随便聊聊")
    plain_assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert plain_assistant["learning_resources"] is None
    assert openlibrary.queries == [], "普通聊天绝不暗中检索"


def test_resources_round_never_calls_chat_model(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """资料轮不调用普通聊天模型：正文与清单只来自真实检索证据。"""
    _register(client)
    _install_resources_service(
        sqlite_app,
        books=[_FakeBookSource(candidates=[BEGINNER_BOOK, HANDBOOK_BOOK])],
        discoverer=_FakeDiscoverer(),
        verifier=_FakeVerifier(),
    )

    class _ExplodingAdapter:
        def stream_call(
            self, capability: Any, run_context: Any, payload: dict[str, Any]
        ) -> Any:
            del capability, run_context, payload
            raise AssertionError("资料轮不得调用聊天模型")

    sqlite_app.state.chat_service._gateway = _gateway_with(_ExplodingAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, "我是零基础，想学深度学习", module_id="resources")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert assistant["status"] == "done"
    assert assistant["learning_resources"]["status"] == "success"
