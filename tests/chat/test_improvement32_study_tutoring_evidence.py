"""工单 32 验收：按问题充分性逐层补证并自然辅导。

覆盖 L03（书页足够不联网）、L04（书页不足/禁用/失败仍交付已支持部分）、
L05（辅导不自动复盘）；捕获真实路径的知识库/公网源调用、阶段状态与冲突。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.study import StudyFragment
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import WebSearchResult, WebSearchVerification
from bridges.web_search.service import WebSearchService
from tests.chat.test_v2_05_photo_attachments import _app, _register, _upload_draft
from tests.chat.test_v2_17_study_pages import StudyGateway, _first
from tests.knowledge_base.test_knowledge_base_api import _upload, _worker_service


class EvidenceGateway(StudyGateway):
    """按调用顺序返回评估结果，并记录辅导真实载荷。"""

    def __init__(self) -> None:
        super().__init__(page_numbers=[12])
        self.assess_results: list[dict[str, Any]] = []
        self.assess_payloads: list[dict[str, Any]] = []
        self.tutor_payloads: list[dict[str, Any]] = []
        self.fail_tutor = False

    def invoke(
        self, capability: str, version: str, context: Any, payload: dict[str, Any], **kwargs: Any
    ) -> ModelCallResult:
        task = payload.get("task")
        if task == "study.assess_evidence":
            self.assess_payloads.append(payload)
            if self.assess_results:
                return ModelCallResult(
                    status=ModelCallStatus.SUCCESS, output=self.assess_results.pop(0)
                )
            sources = payload.get("sources", [])
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "key_points": ["本节概念"],
                    "supported": [
                        {
                            "point": "本节概念",
                            "source_ids": [sources[0]["source_id"]] if sources else [],
                        }
                    ],
                    "gaps": [],
                },
            )
        if task == "study.tutor":
            self.tutor_payloads.append(payload)
            if self.fail_tutor:
                return ModelCallResult(status=ModelCallStatus.BLOCKED, error_code="timeout")
            sources = payload["sources"]
            parts: list[dict[str, Any]] = []
            page = next((item for item in sources if item["kind"] == "page"), None)
            if page is not None:
                parts.append(
                    {"text": "a 就是斜率，x 每增加 1，y 就增加 a。",
                     "kind": "page", "source_ids": [page["source_id"]]}
                )
            for source in sources:
                if source["kind"] == "page":
                    continue
                parts.append(
                    {"text": f"这是{source['kind']}的补充解释。",
                     "kind": source["kind"], "source_ids": [source["source_id"]]}
                )
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS, output={"parts": parts, "gap": ""}
            )
        return super().invoke(capability, version, context, payload, **kwargs)


class SearchClient:
    def __init__(
        self,
        results: list[WebSearchResult] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.queries: list[str] = []
        self.fail = fail
        self.results = results or [
            WebSearchResult(
                result_id="web-1",
                title="斜率应用资料",
                site="example.org",
                url="https://example.org/slope",
                snippet="斜率在实际问题中的变化率解释。",
                accessed_at=datetime.now(UTC),
                verification=WebSearchVerification.VERIFIED,
            )
        ]

    def search(self, query: str, **kwargs: Any) -> list[WebSearchResult]:
        self.queries.append(query)
        if self.fail:
            raise WebSearchError("web_search_timeout", "搜索超时", retryable=False)
        return self.results


def _account_id(client: TestClient) -> str:
    from bridges.api.auth import SESSION_COOKIE_NAME

    token = client.cookies.get(SESSION_COOKIE_NAME)
    return client.app.state.identity_service.resolve_session(token).subject.account_id


def _start(client: TestClient, app: Any, name: str) -> str:
    _register(client, name)
    photo = _upload_draft(client).json()
    first = _first(client, [photo["object_id"]], key=f"{name}-first")
    assert first.status_code == 201, first.text
    app.state.generation_executor.run_tick()
    return f"/chat/conversations/{first.json()['conversation']['conversation_id']}"


def _ask(
    client: TestClient, app: Any, endpoint: str, content: str, **fields: Any
) -> dict[str, Any]:
    response = client.post(endpoint + "/messages", json={"content": content, **fields})
    assert response.status_code == 200, response.text
    app.state.generation_executor.run_tick()
    return client.get(endpoint).json()


def _retry(client: TestClient, app: Any, endpoint: str) -> dict[str, Any]:
    message = client.get(endpoint).json()["messages"][-1]
    response = client.post(
        endpoint + f"/messages/{message['message_id']}/retry",
        json={"idempotency_key": f"retry-{message['message_id']}"},
    )
    assert response.status_code == 200, response.text
    app.state.generation_executor.run_tick()
    return client.get(endpoint).json()


def _install_services(app: Any, search: SearchClient | None = None) -> None:
    from bridges.retrieval.service import LayeredRetrievalService

    app.state.chat_service._retrieval = LayeredRetrievalService(
        database=app.state.bridges_database,
        object_repository=app.state.object_repository,
    )
    if search is not None:
        app.state.chat_service._web_search = WebSearchService(
            client=search, sleeper=lambda _: None
        )


def test_sufficient_pages_do_not_search_knowledge_base_or_web(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """L03：书页足够时无额外知识库/联网调用，评估记录充分。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidencesufficient")
        result = _ask(client, app, endpoint, "第12页 y=ax+b 里的 a 是什么意思？")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert search.queries == []
        exchange = result["study"]["tutoring"][0]
        assessment = exchange["assessment"]
        assert assessment["sufficient"] is True
        assert assessment["gaps"] == []
        assert assessment["supplements"] == []
        assert all(
            source["kind"] == "page" for source in gateway.tutor_payloads[-1]["sources"]
        )
        assert all(source["kind"] == "page" for source in exchange["sources"])


def test_real_gaps_fetch_knowledge_base_then_web_with_minimal_public_query(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """书页不足先知识库、仍不足再联网；公开查询只含缺口术语。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidencegaps")
        own = _upload(
            client,
            "斜率笔记.txt",
            "斜率表示变化率，可用于速度与增长分析。".encode(),
        )
        assert own.status_code == 201, own.text
        _worker_service(app).process_pending()
        gateway.assess_results = [
            {
                "key_points": ["斜率的应用"],
                "supported": [],
                "gaps": [
                    {
                        "point": "斜率的速度与增长应用",
                        "reason": "本节书页只有公式",
                        "supplement": "knowledge_base",
                    }
                ],
            },
            {
                "key_points": ["斜率的应用"],
                "supported": [{"point": "斜率应用", "source_ids": []}],
                "gaps": [
                    {
                        "point": "最新实测案例 a@example.com",
                        "reason": "需要时效案例与外部核实",
                        "supplement": "web",
                    }
                ],
            },
        ]
        result = _ask(
            client,
            app,
            endpoint,
            "结合知识库解释斜率应用，再联网核实最新案例。我的邮箱 a@example.com。",
        )
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        exchange = result["study"]["tutoring"][0]
        statuses = [item["status"] for item in exchange["assessment"]["supplements"]]
        assert statuses == ["used", "used"], exchange["assessment"]["supplements"]
        kinds = {source["kind"] for source in exchange["sources"]}
        assert {"page", "knowledge_base", "web"} <= kinds, exchange["sources"]
        assert search.queries
        assert all("example.com" not in query for query in search.queries)
        assert all("y=ax+b" not in query for query in search.queries)
        content = result["messages"][-1]["content"]
        assert "知识库补充" in content and "联网补充" in content


def test_disabled_knowledge_base_and_no_network_keep_gaps_unverified(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """用户关闭知识库/明确不联网：跳过对应层，缺口保持未核实。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidencedisabled")
        gateway.assess_results = [
            {
                "key_points": ["定义"],
                "supported": [],
                "gaps": [
                    {
                        "point": "斜率的严格定义",
                        "reason": "书页没有",
                        "supplement": "knowledge_base",
                    }
                ],
            }
        ]
        result = _ask(
            client, app, endpoint, "解释斜率的严格定义", use_knowledge_base=False
        )
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        exchange = result["study"]["tutoring"][0]
        attempt = exchange["assessment"]["supplements"][-1]
        assert attempt["layer"] == "knowledge_base"
        assert attempt["status"] == "skipped_disabled"
        assert all(source["kind"] == "page" for source in exchange["sources"])
        assert "未核实缺口" in result["messages"][-1]["content"]

        gateway.assess_results = [
            {
                "key_points": ["应用"],
                "supported": [],
                "gaps": [
                    {"point": "2026 年最新案例", "reason": "时效信息", "supplement": "web"}
                ],
            }
        ]
        result = _ask(client, app, endpoint, "不要联网，解释斜率应用")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert search.queries == []
        exchange = result["study"]["tutoring"][-1]
        web_attempt = next(
            item
            for item in exchange["assessment"]["supplements"]
            if item["layer"] == "web"
        )
        assert web_attempt["status"] == "skipped_disabled"
        assert "未核实缺口" in result["messages"][-1]["content"]


def test_conflicting_public_sources_are_listed_not_merged(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """冲突来源分列保留，不作为支持证据，也不隐藏书页结果。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient(
        results=[
            WebSearchResult(
                result_id="web-conflict",
                title="冲突资料",
                site="example.net",
                url="https://example.net/conflict",
                snippet="另一种相反的说法。",
                accessed_at=datetime.now(UTC),
                verification=WebSearchVerification.CONFLICTING,
            )
        ]
    )
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidenceconflict")
        gateway.assess_results = [
            {
                "key_points": ["定义"],
                "supported": [],
                "gaps": [
                    {"point": "斜率的统一定义", "reason": "书页没有", "supplement": "web"}
                ],
            }
        ]
        result = _ask(client, app, endpoint, "解释斜率的统一定义")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        exchange = result["study"]["tutoring"][0]
        assert exchange["assessment"]["conflicts"]
        assert all(source["kind"] != "web" for source in exchange["sources"])
        content = result["messages"][-1]["content"]
        assert "冲突" in content and "未核实缺口" in content
        web_attempt = next(
            item
            for item in exchange["assessment"]["supplements"]
            if item["layer"] == "web"
        )
        assert web_attempt["status"] == "conflict"


def test_assessment_cannot_fabricate_support_sources(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """评估声称支持但引用不存在来源时按未核实缺口处理，不伪造证据。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidencefabricated")
        gateway.assess_results = [
            {
                "key_points": ["定义"],
                "supported": [{"point": "斜率定义", "source_ids": ["invented-source"]}],
                "gaps": [],
            }
        ]
        result = _ask(client, app, endpoint, "解释斜率定义")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        exchange = result["study"]["tutoring"][0]
        gap = exchange["assessment"]["gaps"][0]
        assert gap["status"] == "unverified"
        assert "未核实缺口" in result["messages"][-1]["content"]
        assert search.queries == []
        assert all(source["kind"] == "page" for source in exchange["sources"])


def test_targeted_unclear_page_asks_for_retake_without_external_replacement(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """关键书页不清时要求补拍，外部状态与考查范围不被改动。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        from bridges.study.service import StudyRepository

        endpoint = _start(client, app, "evidenceunclear")
        account_id = _account_id(client)
        conversation_id = endpoint.rsplit("/", 1)[1]
        states = StudyRepository(app.state.bridges_database)
        state = states.get(account_id, conversation_id)
        assert state is not None
        state.pages[0].fragments.append(
            StudyFragment(
                fragment_id="fragment-unclear-1",
                kind="formula",
                position="右下角公式",
                text="E=mc^2",
                confidence=0.4,
            )
        )
        states.save(account_id, conversation_id, state)
        scope_before = client.get(endpoint).json()["study"]["scope"]
        questions_before = client.get(endpoint).json()["study"]["questions"]
        result = _ask(client, app, endpoint, "E=mc^2 是什么意思？")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        content = result["messages"][-1]["content"]
        assert "书页缺口" in content and "补拍" in content
        assert search.queries == []
        study = result["study"]
        assert study["stage"] == "tutoring"
        assert study["review"] is None
        assert study["scope"] == scope_before
        assert study["questions"] == questions_before
        exchange = study["tutoring"][0]
        assert any(gap["status"] == "needs_page" for gap in exchange["assessment"]["gaps"])


def test_retry_reuses_persisted_supplements_without_external_repeat(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """恢复只重做受影响的生成；已固化的知识库/联网补证不重复外呼。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient()
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidenceretry")
        own = _upload(
            client,
            "斜率应用笔记.txt",
            "斜率表示变化率，可用于速度与增长分析。".encode(),
        )
        assert own.status_code == 201, own.text
        _worker_service(app).process_pending()
        first_assessment = {
            "key_points": ["应用"],
            "supported": [],
            "gaps": [
                {
                    "point": "斜率的速度与增长应用",
                    "reason": "书页只有公式",
                    "supplement": "knowledge_base",
                }
            ],
        }
        gateway.assess_results = [
            first_assessment,
            {
                "key_points": ["应用"],
                "supported": [{"point": "斜率应用", "source_ids": []}],
                "gaps": [
                    {"point": "最新实例", "reason": "需要时效", "supplement": "web"}
                ],
            },
        ]
        gateway.fail_tutor = True
        failed = _ask(client, app, endpoint, "解释斜率应用并联网查最新实例")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["study"]["tutoring"] == []
        queries_after_first = list(search.queries)
        assert queries_after_first

        gateway.fail_tutor = False
        gateway.assess_results = [
            {
                "key_points": ["应用"],
                "supported": [],
                "gaps": [
                    {
                        "point": "斜率的速度与增长应用",
                        "reason": "书页只有公式",
                        "supplement": "knowledge_base",
                    }
                ],
            },
            {
                "key_points": ["应用"],
                "supported": [{"point": "斜率应用", "source_ids": []}],
                "gaps": [
                    {"point": "最新实例", "reason": "需要时效", "supplement": "web"}
                ],
            },
        ]
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert search.queries == queries_after_first
        exchange = result["study"]["tutoring"][0]
        kinds = {source["kind"] for source in exchange["sources"]}
        assert {"page", "knowledge_base", "web"} <= kinds, (
            exchange["assessment"]["supplements"],
            exchange["assessment"]["gaps"],
        )
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None


def test_page_only_delivery_without_review_or_scope_expansion(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """补充失败交付书页支持部分，不自动复盘、不扩大考查范围。"""

    app = _app(tmp_path, monkeypatch)
    gateway = EvidenceGateway()
    app.state.chat_service._gateway = gateway
    search = SearchClient(fail=True)
    _install_services(app, search)
    with TestClient(app) as client:
        endpoint = _start(client, app, "evidencepartial")
        before = client.get(endpoint).json()["study"]
        gateway.assess_results = [
            {
                "key_points": ["应用"],
                "supported": [],
                "gaps": [
                    {"point": "斜率应用", "reason": "书页只有公式", "supplement": "web"}
                ],
            }
        ]
        result = _ask(client, app, endpoint, "解释斜率应用")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        study = result["study"]
        assert study["stage"] == "tutoring"
        assert study["review"] is None
        assert study["summary"] == before["summary"]
        assert study["scope"] == before["scope"]
        assert study["questions"] == before["questions"]
        content = result["messages"][-1]["content"]
        assert "本节书页" in content
        assert "联网补充" in content and "失败" in content
        assert "未核实缺口" in content
        assert result["study"]["tutoring"][0]["assessment"]["sufficient"] is False
