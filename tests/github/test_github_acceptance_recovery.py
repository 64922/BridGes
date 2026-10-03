"""工单 26 独立验收：限流/超时的局部恢复（重试只补缺口，不伪装完整核验）。

覆盖交接要求的三个回归：

1. 首次检索超时/限流：部分失败产物被标失效，重试真正再次发出未完成的查询；
2. 多候选读取时第三个限流：重试只读取缺口，已完成候选不重复外发；
3. 历史 partial 交付不能冒充恢复后的完整核验（旧核验链被拒绝）。
"""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import Any

import httpx
from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from bridges.github.client import GithubApiClient
from bridges.github.contracts import GithubRepositoryCandidate
from bridges.github.inspecting import GithubRepositoryReader
from bridges.github.searching import SearchOutcome, query_record
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register
from tests.github.test_github_module_flow import (
    WHOLE_IDEA,
    WHOLE_QUERY,
    _candidate,
    _evidence,
    _FakeReader,
    _FakeSearchPort,
    _install_github_service,
    _run_and_read,
    _send,
    _SilentAdapter,
)
from tests.github.test_github_requirement_matrix import _retry

README_TEXT = "学生可以发布想卖的书，也可以搜索想要的书，然后线下交换。"


class _FailThenSucceedSearch:
    """脚本化检索：前 ``failures`` 次按失败返回，其后按查询词返回候选。"""

    def __init__(
        self,
        *,
        failures: int,
        status: ModuleQueryStatus = ModuleQueryStatus.TIMEOUT,
        per_query: dict[str, list[GithubRepositoryCandidate]] | None = None,
    ) -> None:
        self.queries: list[str] = []
        self._failures = failures
        self._status = status
        self._per_query = per_query or {}

    def search_repositories(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        del account_id, reason, stop_event, deadline
        self.queries.append(query)
        if len(self.queries) <= self._failures:
            return SearchOutcome(
                query=query,
                candidates=[],
                record=query_record(
                    query=query,
                    status=self._status,
                    evidence_count=0,
                    error_code="github_timeout",
                    error_message="GitHub 接口超时，本轮未取得结果。",
                    retryable=True,
                ),
            )
        candidates = list(self._per_query.get(query, []))
        return SearchOutcome(
            query=query,
            candidates=candidates,
            record=query_record(
                query=query,
                status=(
                    ModuleQueryStatus.SUCCESS if candidates else ModuleQueryStatus.EMPTY
                ),
                evidence_count=len(candidates),
            ),
        )


def _trust_states(app: Any, node: str) -> list[str]:
    rows = app.state.bridges_database.connection.execute(
        "SELECT trust_state FROM node_artifacts WHERE node = ? ORDER BY rowid",
        (node,),
    ).fetchall()
    return [str(row["trust_state"]) for row in rows]


def test_search_timeout_retry_requeries_and_delivers(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """首次检索超时：保留真实查询记录；重试从缺口续查并交付成功。"""
    _register(client)
    port = _FailThenSucceedSearch(
        failures=1,
        per_query={WHOLE_QUERY: [_candidate("demo/a", description="校园二手书交换")]},
    )
    _install_github_service(
        sqlite_app,
        port=port,
        reader=_FakeReader({"demo/a": _evidence("demo/a")}),
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    projects = first["github_projects"]
    assert projects["status"] == "error"
    assert projects["error_code"] == "github_timeout"
    assert projects["retryable"] is True
    assert port.queries == [WHOLE_QUERY]
    # 部分失败的检索产物被标失效：不能作为完成收据被长期复用。
    assert _trust_states(sqlite_app, "github.search") == ["invalidated"]

    _retry(client, conversation_id, first["message_id"])
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["github_projects"]["status"] == "success"
    assert latest["github_projects"]["recommendations"][0]["full_name"] == "demo/a"
    # 未完成的查询在重试时真正再次外发；成功后的产物可复用。
    assert port.queries.count(WHOLE_QUERY) == 2, "重试必须重新发出未完成的查询"
    assert _trust_states(sqlite_app, "github.search")[-1] == "evidence_bound"


def _content(text: str) -> dict[str, str]:
    return {
        "content": base64.b64encode(text.encode()).decode(),
        "encoding": "base64",
        "sha": "blob",
    }


def _counting_reader(paths: list[str]) -> GithubRepositoryReader:
    """真实读取器：所有候选都成功读取，记录真实外发的路径。"""

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/readme"):
            return httpx.Response(200, json=_content(README_TEXT))
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "README.md", "type": "file"}])
        return httpx.Response(404, json={"message": "Not Found"})

    return GithubRepositoryReader(
        GithubApiClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    )


def _install_three_candidates(
    app: Any, reader: Any
) -> tuple[Any, _FakeSearchPort]:
    port = _FakeSearchPort(
        per_query={
            WHOLE_QUERY: [
                _candidate("demo/one", description="校园二手书交换"),
                _candidate("demo/two", description="校园二手书交换"),
                _candidate("demo/third", description="校园二手书交换"),
            ]
        }
    )
    service = _install_github_service(app, port=port, reader=reader)
    app.state.chat_service._gateway = _gateway_with(_SilentAdapter())
    return service, port


def _partial_reader() -> _FakeReader:
    """首轮读取：前两个候选完整，第三个撞限流。"""
    return _FakeReader(
        {
            "demo/one": _evidence("demo/one"),
            "demo/two": _evidence("demo/two"),
        },
        limited_after="demo/third",
    )


def test_rate_limited_read_retry_only_reads_the_gap(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """第三个候选限流：重试只读取缺口，已完成候选不重复外发。"""
    _register(client)
    service, port = _install_three_candidates(sqlite_app, _partial_reader())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    projects = first["github_projects"]
    assert projects["status"] == "success"
    assert projects["rate_limit"]["limited"] is True
    assert projects["retryable"] is True
    assert {item["full_name"] for item in projects["recommendations"]} == {
        "demo/one",
        "demo/two",
    }
    rejected = {item["full_name"]: item["reason"] for item in projects["rejected"]}
    assert "未完成检查" in rejected["demo/third"]

    # 恢复读取边界后重试：只应外发缺口候选（第三个）的读取请求。
    paths: list[str] = []
    service._reader = _counting_reader(paths)  # noqa: SLF001 - 测试替换读取边界
    _retry(client, conversation_id, first["message_id"])
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["github_projects"]["status"] == "success"
    assert {item["full_name"] for item in latest["github_projects"]["recommendations"]} \
        == {"demo/one", "demo/two", "demo/third"}
    # 检索收据复用：重试不再检索；前两个候选的读取也不重复。
    assert port.queries == [WHOLE_QUERY]
    assert paths == [
        "/repos/demo/third/readme",
        "/repos/demo/third/contents",
    ], "重试只补第三个候选的读取缺口"


def test_partial_delivery_cannot_verify_recovered_result(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any],
    monkeypatch: Any,
) -> None:
    """恢复后的新结果不能被历史 partial 核验链背书，只能按真实新链核验。"""
    _register(client)
    service, _ = _install_three_candidates(sqlite_app, _partial_reader())
    original_run = service.run
    deliveries: list[Any] = []

    def capture(**kwargs: Any) -> Any:
        outcome = original_run(**kwargs)
        deliveries.append(outcome.delivery)
        return outcome

    monkeypatch.setattr(service, "run", capture)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    first = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    assert first["github_projects"]["status"] == "success"
    assert first["github_projects"]["rate_limit"]["limited"] is True

    paths: list[str] = []
    service._reader = _counting_reader(paths)  # noqa: SLF001 - 测试替换读取边界

    def replay_partial(**kwargs: Any) -> Any:
        outcome = original_run(**kwargs)
        old = deliveries[0]
        delivery = outcome.delivery.model_copy(update={
            "verification_artifact_id": old.verification_artifact_id,
            "present_artifact_id": old.present_artifact_id,
            "projection": old.projection,
            "content": old.content,
        })
        return replace(outcome, delivery=delivery)

    monkeypatch.setattr(service, "run", replay_partial)
    _retry(client, conversation_id, first["message_id"])
    generation_helpers["drive"](sqlite_app)
    final = client.get(f"/chat/conversations/{conversation_id}").json()
    latest = [m for m in final["messages"] if m["role"] == "assistant"][-1]
    assert latest["error_code"] == "github_delivery_unverified"
    assert latest["github_projects"] is None
