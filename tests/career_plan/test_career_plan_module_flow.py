"""Issue 15 端到端合同：显式派发、检索计划、主样本口径、降级、等待与失败。

全部走真实 HTTP + SQLite + 后台执行器（假搜索服务与假岗位页读取，无外网）：

- 只有逐消息 ``module_id=career`` 或点击建议才启动，普通聊天不检索任何来源；
- 计划节点先展示三类来源的实际查询词与筛选条件，采集只读公开页面；
- 只把公开可读、岗位名与城市都匹配的岗位纳入主样本；
- 相邻岗位单列建议，绝不混入样本的技能与薪资统计；
- 读不到页面时降级为「未核实链接」，并展示实际查询与证据缺口；
- 澄清等待跨轮次恢复，停止与失败如实写回同一条消息。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.career_plan.collecting import JobPageReadResult, ParsedJobPage
from bridges.career_plan.contracts import JobReadStatus
from bridges.career_plan.searching import CareerSearchHit, CareerSearchOutcome, query_record
from bridges.career_plan.service import CareerPlanService
from bridges.contracts.modules import ModuleQueryStatus
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

BOSS_URL = "https://www.zhipin.com/job_detail/a.html"
FRONTEND_URL = "https://www.zhipin.com/job_detail/b.html"
CORP_URL = "https://careers.example.com/job/9"


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
    """普通聊天用的静默流式适配器；职业规划轮不调用模型。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        del capability, run_context, payload
        self.calls += 1
        yield StreamChunk(kind="delta", delta="好的。")


class _FakeSearchPort:
    """允许的搜索服务替身：按查询词命中来源返回脚本化结果，并记录查询词。"""

    def __init__(
        self,
        *,
        per_source: dict[str, list[CareerSearchHit]] | None = None,
        status: ModuleQueryStatus = ModuleQueryStatus.SUCCESS,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = True,
        on_call: Any | None = None,
    ) -> None:
        self.queries: list[tuple[str, str]] = []
        # 命中结果与检索状态都是公开可改的：多轮用例要「先成功、后失败、再成功」。
        self.per_source = per_source if per_source is not None else {}
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
        self._on_call = on_call

    @property
    def query_texts(self) -> list[str]:
        return [query for query, _ in self.queries]

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        source: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> CareerSearchOutcome:
        del account_id, reason, deadline
        self.queries.append((query, source))
        if self._on_call is not None:
            self._on_call(stop_event)
        hits = tuple(self.per_source.get(source, []))
        record = query_record(
            query=query,
            status=self.status,
            evidence_count=len(hits),
            error_code=self.error_code,
            error_message=self.error_message,
            retryable=self.retryable,
        )
        return CareerSearchOutcome(record=record, hits=hits)


class _FakeReader:
    """岗位页读取替身：按链接返回脚本化结果，并记录读取过的链接。"""

    def __init__(self, results: dict[str, JobPageReadResult] | None = None) -> None:
        self.read_urls: list[str] = []
        self._results = results or {}

    def read(
        self,
        url: str,
        *,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> JobPageReadResult:
        del deadline
        self.read_urls.append(url)
        if stop_event is not None and stop_event.is_set():
            return _read_result(url=url, status=JobReadStatus.CANCELLED)
        return self._results.get(
            url, _read_result(url=url, status=JobReadStatus.ACCESS_RESTRICTED)
        )


def _read_result(
    *,
    url: str,
    status: JobReadStatus,
    title: str = "Java后端开发工程师",
    city: str | None = "南昌",
    salary: str | None = "15-25K·15薪",
    published: date | None = date(2026, 9, 20),
    is_job: bool = True,
    expired: bool = False,
) -> JobPageReadResult:
    messages = {
        JobReadStatus.ACCESS_RESTRICTED: "岗位页要求登录或触发了访问验证，未取得岗位内容。",
        JobReadStatus.UNRECOGNIZED: "岗位页结构与预期不符，未取得可核实的岗位内容。",
        JobReadStatus.NOT_FOUND: "该岗位页不存在或已下线。",
        JobReadStatus.TIMEOUT: "读取超时，未取得岗位内容。",
        JobReadStatus.CANCELLED: "已停止读取，未取得岗位内容。",
    }
    readable = status in {JobReadStatus.READ, JobReadStatus.PARTIAL}
    page = ParsedJobPage(
        title=title if readable else None,
        company="某某科技" if readable else None,
        city=city if readable else None,
        salary_raw=salary if readable else None,
        published_raw=published.isoformat() if (readable and published) else None,
        published_date=published if readable else None,
        experience="1-3年" if readable else None,
        education="本科" if readable else None,
        requirements=(
            ["熟悉 Java、Spring Boot 与 MySQL", "了解 Redis 与 Docker", "熟悉 Linux"]
            if readable
            else []
        ),
        is_job_posting=is_job if readable else False,
        expired=expired,
        expired_evidence="岗位页出现「职位已下线」" if expired else None,
        structure_found=readable,
    )
    return JobPageReadResult(
        url=url,
        status=status,
        page=page,
        error_code=None if readable else f"career_read_{status.value}",
        error_message=None if readable else messages.get(status, "未取得岗位内容。"),
        retrieved_at=NOW,
    )


def _install_career_service(
    app: Any, *, port: _FakeSearchPort, reader: _FakeReader | None = None
) -> CareerPlanService:
    """把职业规划模块的搜索与读取边界换成替身（编排与父图派发保持真实）。"""
    service = CareerPlanService(search=port, reader=reader or _FakeReader())
    app.state.career_plan_service = service
    app.state.chat_service._career_plan = service  # noqa: SLF001
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


def _hit(url: str, source: str, title: str) -> CareerSearchHit:
    return CareerSearchHit(url=url, title=title, snippet="", source=source)


def test_samples_are_rendered_with_plan_analysis_and_evidence(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1/2/4：显式选择职业模块 → 计划 + 样本 + 统计口径 + 建议。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    _install_career_service(sqlite_app, port=port, reader=reader)
    adapter = _SilentAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(
        client,
        conversation_id,
        "我想找 Java 后端实习，城市南昌",
        module_id="career",
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    career = assistant["career_plan"]
    assert career["status"] == "success"
    assert career["original_request"] == "我想找 Java 后端实习，城市南昌"
    assert career["job_terms"] == ["Java 后端实习"]
    assert career["cities"] == ["南昌"]
    # 三类来源各一条实际查询，计划里逐条展示
    assert [item["source"] for item in career["plan"]] == ["boss", "corporate", "campus"]
    assert port.query_texts[0].startswith("zhipin.com ")
    assert len(career["queries"]) == 3
    # 主样本字段：抓取时间、发布日期、薪资原文、要求与直达链接
    sample = career["samples"][0]
    assert sample["url"] == BOSS_URL
    assert sample["retrieved_at"] is not None
    assert sample["published_raw"] == "2026-09-20"
    assert sample["salary_raw"] == "15-25K·15薪"
    assert sample["requirements"]
    assert sample["title_evidence"] and sample["city_evidence"]
    # 统计口径：样本量、日期、地区、计薪单位
    analysis = career["analysis"]
    assert analysis["sample_count"] == 1
    assert analysis["published_span"] == "2026-09-20"
    assert analysis["city_composition"][0]["city"] == "南昌"
    interval = analysis["salary_intervals"][0]
    assert interval["unit"] == "元/月"
    assert interval["sample_count"] == 1
    assert interval["small_sample"] is True
    assert "不是全国市场均值" in analysis["sample_scope_note"]
    assert analysis["overall_inference_stopped"] is True
    # 建议区分证据与推断
    kinds = {item["kind"]: item for item in career["advices"]}
    assert kinds["skill"]["inference"] is False
    assert kinds["project"]["inference"] is True
    # 本模块不调用模型
    assert adapter.calls == 0
    # 正文与卡片同源：关键事实必须出现在正文里
    content = assistant["content"]
    assert "检索计划" in content and "zhipin.com" in content
    assert "15-25K·15薪" in content and "2026-09-20" in content
    assert "元/月" in content and "不作为市场均值" in content
    assert "相邻岗位" not in content or "单列" in content
    # 正文不出现英文枚举值：查询状态回显中文（卡片与正文同一套口径）
    assert "状态：成功" in content
    assert "success" not in content


def test_adjacent_jobs_are_separated_from_main_sample(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1/4：相邻岗位单列建议，不进主样本统计。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={
            "boss": [
                _hit(BOSS_URL, "boss", "Java后端开发工程师"),
                _hit(FRONTEND_URL, "boss", "前端开发工程师"),
            ]
        }
    )
    reader = _FakeReader(
        {
            BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ),
            FRONTEND_URL: _read_result(
                url=FRONTEND_URL, status=JobReadStatus.READ, title="前端开发工程师"
            ),
        }
    )
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert [sample["url"] for sample in career["samples"]] == [BOSS_URL]
    rejected = {item["url"]: item for item in career["rejected"]}
    assert rejected[FRONTEND_URL]["kind"] == "adjacent"
    assert "相邻岗位" in rejected[FRONTEND_URL]["evidence"]
    suggestions = career["adjacent_suggestions"]
    assert any(
        item["title"] == "前端开发工程师" and item["sample_count"] == 1
        for item in suggestions
    )
    assert "相邻岗位建议" in assistant["content"]


def test_city_mismatch_and_unverifiable_city_are_excluded(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1/2：城市不符与城市无法核对的岗位都不进主样本。"""
    _register(client)
    other_city = "https://careers.example.com/job/other"
    no_city = "https://careers.example.com/job/none"
    port = _FakeSearchPort(
        per_source={
            "corporate": [
                _hit(other_city, "corporate", "Java后端开发工程师"),
                _hit(no_city, "corporate", "Java后端开发工程师"),
            ]
        }
    )
    reader = _FakeReader(
        {
            other_city: _read_result(url=other_city, status=JobReadStatus.READ, city="杭州"),
            no_city: _read_result(url=no_city, status=JobReadStatus.READ, city=None),
        }
    )
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习，城市南昌", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert career["samples"] == []
    kinds = {item["url"]: item["kind"] for item in career["rejected"]}
    assert kinds[other_city] == "city"
    assert kinds[no_city] == "city_unverified"
    assert career["status"] == "empty"
    # 无可用样本时展示实际查询与证据缺口，不给任何市场结论
    assert career["analysis"] is None
    assert career["advices"] == []
    assert "zhipin.com" in assistant["content"]
    assert "证据边界" in assistant["content"]


def test_inaccessible_pages_degrade_to_unverified_links(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 3/5：公开来源不可得时只展示实际取得的链接，不伪造岗位内容。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={
            "boss": [
                _hit(BOSS_URL, "boss", "Java后端开发工程师"),
                _hit(FRONTEND_URL, "boss", "前端开发工程师"),
            ]
        }
    )
    reader = _FakeReader()  # 默认全部按访问受限返回
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习，城市南昌", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert career["status"] == "links_only"
    assert career["samples"] == []
    assert {link["url"] for link in career["candidate_links"]} == {BOSS_URL, FRONTEND_URL}
    assert all("未核实" in link["note"] for link in career["candidate_links"])
    assert all(
        "登录" in link["note"] or "访问" in link["note"]
        for link in career["candidate_links"]
    )
    assert "未纳入样本" in assistant["content"]
    assert career["empty_reason"]
    assert "本轮没有取得公开可读且匹配的" in career["empty_reason"]


def test_read_limit_keeps_extra_candidates_as_unread_links(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """超过本轮读取上限的候选不读页面，只作为未核实链接列出。"""
    _register(client)
    urls = [f"https://www.zhipin.com/job_detail/{index}.html" for index in range(5)]
    port = _FakeSearchPort(
        per_source={
            "boss": [
                _hit(url, "boss", f"Java后端开发工程师{index}")
                for index, url in enumerate(urls)
            ]
        }
    )
    reader = _FakeReader(
        {
            url: _read_result(
                url=url, status=JobReadStatus.READ, title=f"Java后端开发工程师{index}"
            )
            for index, url in enumerate(urls)
        }
    )
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert len(reader.read_urls) == 3, "每条来源最多读三个岗位页"
    assert len(career["samples"]) == 3
    notes = {link["url"]: link["note"] for link in career["candidate_links"]}
    assert set(notes) == set(urls[3:])
    assert all("读取上限" in note for note in notes.values())


def test_all_sources_failing_is_retryable_error_with_actual_queries(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 5：部分来源失效时如实报失败，并保留实际查询词与重试标记。"""
    _register(client)
    port = _FakeSearchPort(
        status=ModuleQueryStatus.ERROR,
        error_code="career_search_failed",
        error_message="岗位检索没有形成结果，请稍后重试。",
    )
    _install_career_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习，城市南昌", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "error"
    assert assistant["error_code"] == "career_search_failed"
    assert "读取公开岗位" in assistant["error_message"]
    career = assistant["career_plan"]
    assert career["status"] == "error"
    assert career["retryable"] is True
    assert len(career["queries"]) == 3
    assert all(record["status"] == "error" for record in career["queries"])
    assert "zhipin.com" in assistant["content"]


def test_clarification_then_resume_keeps_original_request(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：岗位含糊先问一项，回答后沿用原请求继续检索。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "帮我看看有哪些岗位，城市南昌", module_id="career")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    career = first["career_plan"]
    assert career["status"] == "clarification"
    assert career["pending"]["module_id"] == "career"
    assert career["cities"] == ["南昌"]
    assert port.queries == [], "澄清轮不得发起任何检索"

    _send(client, conversation_id, "Java 后端开发", module_id="career")
    second = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    career = second["career_plan"]
    assert career["status"] == "success"
    # 原始请求与城市仍以首次提问为准
    assert career["original_request"] == "帮我看看有哪些岗位，城市南昌"
    assert career["job_terms"] == ["Java 后端开发"]
    assert career["cities"] == ["南昌"]
    assert port.query_texts, "恢复轮必须真的发起检索"


def test_error_turn_ends_the_wait_so_next_message_starts_fresh(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """失败轮同样结束等待：下一条消息不会被接上旧请求的城市。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    # 第一轮岗位含糊 → 澄清等待，原请求里带着城市
    _send(client, conversation_id, "帮我看看有哪些岗位，城市南昌", module_id="career")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert first["career_plan"]["status"] == "clarification"

    # 第二轮回答岗位，但检索整体失败 → 消息收敛为失败
    hits = port.per_source.pop("boss")
    port.status = ModuleQueryStatus.ERROR
    port.error_code = "career_search_failed"
    port.error_message = "岗位检索没有形成结果，请稍后重试。"
    _send(client, conversation_id, "Java 后端开发", module_id="career")
    second = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert second["career_plan"]["status"] == "error"

    # 第三轮换成新岗位且没再给城市：不能把第一轮的南昌接过来
    port.per_source["boss"] = hits
    port.status = ModuleQueryStatus.SUCCESS
    port.error_code = None
    port.error_message = None
    before = len(port.query_texts)
    _send(client, conversation_id, "算法工程师", module_id="career")
    third = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    career = third["career_plan"]
    assert career["job_terms"] == ["算法工程师"]
    assert career["cities"] == []
    assert career["original_request"] == "算法工程师"
    assert all("南昌" not in query for query in port.query_texts[before:])
    assert any("未给出城市" in f for item in career["plan"] for f in item["filters"])


def test_experience_hint_is_disclosed_as_not_filtered(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 3/4：用户提到的经验要求不假装过滤过，写进证据边界逐条说明。"""
    _register(client)
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(
        client,
        conversation_id,
        "我想找 Java 后端开发，城市南昌，经验 1-3年",
        module_id="career",
    )
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    career = assistant["career_plan"]
    assert any(
        "1-3年" in note and "没有做经验过滤" in note
        for note in career["evidence_boundary"]
    )
    assert "没有做经验过滤" in assistant["content"]
    # 经验要求不得偷偷变成筛选条件
    assert all(
        "经验" not in text for item in career["plan"] for text in item["filters"]
    )


def test_stop_during_collect_marks_message_stopped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """停止在节点边界生效：消息收敛为已停止，且不继续读取岗位页。"""
    _register(client)
    reader = _FakeReader()

    def stop_on_call(stop_event: object | None) -> None:
        if stop_event is not None:
            stop_event.set()  # type: ignore[attr-defined]

    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]},
        on_call=stop_on_call,
    )
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我想找 Java 后端实习，城市南昌", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "stopped"
    assert assistant["career_plan"]["status"] == "stopped"
    assert reader.read_urls == [], "已停止后不得继续读取岗位页"
    assert "已停止" in assistant["content"]


def test_plain_chat_suggests_career_without_searching(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """验收 1：普通聊天只给一键建议，不点击就不检索任何来源。"""
    _register(client)
    port = _FakeSearchPort()
    reader = _FakeReader()
    _install_career_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "我在准备找 Java 后端开发的工作")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    suggestion = assistant["module_suggestion"]
    assert suggestion is not None
    assert suggestion["module_id"] == "career"
    assert suggestion["label"] == "使用职业规划"
    assert port.queries == []
    assert reader.read_urls == []
    assert assistant["career_plan"] is None


def test_study_mode_rejects_daily_module(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """学习模式不接收日常模块 ID（职业规划模块也不例外）。"""
    del generation_helpers
    _register(client)
    port = _FakeSearchPort()
    _install_career_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001

    response = client.post(
        "/chat/first-turn",
        json={
            "mode": "study",
            "content": "我想找 Java 后端实习",
            "module_id": "career",
            "idempotency_key": "study-career-1",
        },
    )

    assert response.status_code == 422
    assert port.queries == []


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
    port = _FakeSearchPort()
    _install_career_service(sqlite_app, port=port)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "帮我推荐几个开源项目", module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "module_not_available"
    assert port.queries == []
