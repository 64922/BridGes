"""Issue 16 来源层合同：API 客户端分类、检索归一、证据读取与额度退避。

全部走可控 HTTP 替身（``httpx.MockTransport``）驱动真实代码，不发外网请求：
错误分类、缓存命中、限流退避、README／许可／实现文件的三层证据判定。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx

from bridges.contracts.modules import ModuleQueryStatus
from bridges.github.client import GithubApiClient
from bridges.github.contracts import GithubReadmeStatus
from bridges.github.inspecting import (
    IMPLEMENTATION_READ_LIMIT,
    INSPECT_LIMIT,
    GithubRepositoryReader,
)
from bridges.github.parsing import parse_github_request
from bridges.github.searching import (
    MAX_DESCRIPTION_CHARS,
    GithubApiSearchAdapter,
    plan_queries,
)

SEARCH_PATH = "/search/repositories"


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _rate_limit_headers(remaining: int, reset: int = 4102444800) -> dict[str, str]:
    return {
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-limit": "60",
        "x-ratelimit-reset": str(reset),
    }


def test_search_adapter_maps_fields_and_keeps_original_query_words() -> None:
    """检索归一：只用原词查询，候选字段全部来自上游响应。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "full_name": "253936563/huanshu",
                        "html_url": "https://github.com/253936563/huanshu",
                        "description": "校园二手书交换",
                        "topics": ["campus"],
                        "language": "Java",
                        "stargazers_count": 7,
                        "forks_count": 2,
                        "open_issues_count": 1,
                        "pushed_at": "2026-09-01T10:00:00Z",
                        "created_at": "2025-03-01T10:00:00Z",
                        "archived": False,
                        "fork": False,
                        "default_branch": "main",
                        "license": {"spdx_id": "MIT", "name": "MIT License"},
                    }
                ],
            },
            headers=_rate_limit_headers(9),
        )

    adapter = GithubApiSearchAdapter(GithubApiClient(client=_client(handler)))
    outcome = adapter.search_repositories(
        "account-1", query="校园二手书交换", reason="测试检索"
    )

    assert outcome.record.status is ModuleQueryStatus.SUCCESS
    assert outcome.record.evidence_count == 1
    candidate = outcome.candidates[0]
    assert candidate.full_name == "253936563/huanshu"
    assert candidate.description == "校园二手书交换"
    assert candidate.license_spdx_id == "MIT"
    assert candidate.stars == 7
    assert candidate.matched_query == "校园二手书交换"
    assert candidate.source == "github_search"
    request = seen[0]
    assert request.url.params["q"] == "校园二手书交换"
    assert request.url.params["per_page"] == "10"
    # 不发送排序参数：召回按上游 best match，避免被 star 数带偏。
    assert "sort" not in request.url.params
    assert request.headers["x-github-api-version"] == "2022-11-28"


def test_malformed_items_are_skipped_without_losing_valid_ones() -> None:
    """结构不符的上游记录跳过，同一批里的正常记录仍然保留。"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "items": [
                    {"full_name": "no-url/repo"},
                    "not-an-object",
                    {
                        "full_name": "ok/repo",
                        "html_url": "https://github.com/ok/repo",
                        "description": "x" * (MAX_DESCRIPTION_CHARS + 50),
                    },
                ]
            },
            headers=_rate_limit_headers(8),
        )

    adapter = GithubApiSearchAdapter(GithubApiClient(client=_client(handler)))
    outcome = adapter.search_repositories("account-1", query="q", reason="测试")

    assert [item.full_name for item in outcome.candidates] == ["ok/repo"]
    description = outcome.candidates[0].description
    assert description is not None
    assert len(description) == MAX_DESCRIPTION_CHARS + 1
    assert description.endswith("…")


def test_query_plan_uses_the_compacted_whole_query_first() -> None:
    """检索词计划：整体查询剥掉末尾品类词，要点逐字保留，上限三条。"""
    analysis = parse_github_request(
        "我想做一个校园二手书交换平台，学生可以发布想卖的书，搜索想要的书，线下交换"
    )

    queries = plan_queries(analysis)

    assert queries[0] == "校园二手书交换"
    assert queries[1:] == ("学生可以发布想卖的书", "搜索想要的书")


def test_rate_limit_is_classified_and_no_further_request_is_sent() -> None:
    """额度用尽：分类为限流、可重试，且之后不再外发（不空等一小时）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded for 1.2.3.4."},
            headers=_rate_limit_headers(0),
        )

    client = GithubApiClient(client=_client(handler))
    first = client.get("/repos/a/b/readme", account_id="account-1")
    second = client.get("/repos/a/b/contents", account_id="account-1")

    assert first.error_code == "github_rate_limit"
    assert first.retryable is True
    assert client.limited is True
    assert first.error_message is not None and "额度已用尽" in first.error_message
    assert first.reset_at is not None
    # 第二次调用直接返回限流结论，没有真的再发请求。
    assert second.error_code == "github_rate_limit"
    assert len(calls) == 1


def test_search_bucket_and_core_bucket_are_tracked_separately() -> None:
    """检索额度与仓库读取额度分开记账：一边用尽不影响另一边。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/search/"):
            return httpx.Response(
                403,
                json={"message": "rate limit exceeded"},
                headers=_rate_limit_headers(0),
            )
        return httpx.Response(200, json={}, headers=_rate_limit_headers(55))

    client = GithubApiClient(client=_client(handler))
    search = client.get(SEARCH_PATH, account_id="account-1")
    core = client.get("/repos/a/b", account_id="account-1")

    assert search.error_code == "github_rate_limit"
    assert core.ok is True
    assert client.search_remaining == 0
    assert client.core_remaining == 55


def test_responses_are_cached_within_ttl_and_not_refetched() -> None:
    """同一 URL 与参数在 TTL 内命中进程内缓存，不再外发。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True}, headers=_rate_limit_headers(50))

    client = GithubApiClient(client=_client(handler))
    first = client.get("/repos/a/b/readme", account_id="account-1")
    second = client.get("/repos/a/b/readme", account_id="account-1")

    assert first.from_cache is False
    assert second.from_cache is True
    assert len(calls) == 1

    client.clear_cache()
    client.get("/repos/a/b/readme", account_id="account-1")
    assert len(calls) == 2


def test_query_record_carries_no_internal_log_on_cache_hits() -> None:
    """缓存命中是检索内部日志（interaction.md §4），不进查询记录的面向用户字段。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "full_name": "demo/bookswap",
                        "html_url": "https://github.com/demo/bookswap",
                        "description": "校园二手书交换",
                    }
                ],
            },
            headers=_rate_limit_headers(50),
        )

    adapter = GithubApiSearchAdapter(GithubApiClient(client=_client(handler)))
    first = adapter.search_repositories("account-1", query="校园二手书交换", reason="首次")
    second = adapter.search_repositories("account-1", query="校园二手书交换", reason="再次")

    assert len(calls) == 1
    assert first.record.detail is None
    assert second.record.detail is None


def test_error_classification_covers_not_found_too_large_timeout_and_server_error() -> None:
    """错误分类稳定：404／超限／超时／5xx 各有稳定的 error_code 与可重试性。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/missing"):
            return httpx.Response(
                404, json={"message": "Not Found"}, headers=_rate_limit_headers(50)
            )
        if path.endswith("/big"):
            return httpx.Response(
                403,
                json={
                    "message": (
                        "This API returns blobs up to 1 MB in size. The requested blob "
                        "is too large to fetch via the API."
                    )
                },
                headers=_rate_limit_headers(49),
            )
        if path.endswith("/boom"):
            return httpx.Response(500, json={}, headers=_rate_limit_headers(48))
        raise httpx.ConnectTimeout("超时")

    client = GithubApiClient(client=_client(handler))

    assert client.get("/repos/a/b/missing", account_id="a").error_code == "github_not_found"
    too_large = client.get("/repos/a/b/big", account_id="a")
    assert too_large.error_code == "github_too_large"
    assert too_large.retryable is False
    boom = client.get("/repos/a/b/boom", account_id="a")
    assert boom.error_code == "github_http_500"
    assert boom.retryable is True
    timeout = client.get("/repos/a/b/other", account_id="a")
    assert timeout.error_code == "github_timeout"
    assert timeout.retryable is True


def test_audit_records_fingerprint_without_query_text() -> None:
    """披露审计只留查询指纹与长度：请求正文（原词）不进审计明细。"""
    from bridges.contracts.observability import AuditAction
    from bridges.observability.service import ObservabilityService

    observability = ObservabilityService()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={}, headers=_rate_limit_headers(30))

    client = GithubApiClient(client=_client(handler), observability=observability)
    response = client.get(
        SEARCH_PATH,
        params={"q": "校园二手书交换"},
        account_id="account-1",
        reason="测试检索",
    )
    assert response.ok is True

    events = observability.list_audit_events(
        account_id="account-1", action=AuditAction.GITHUB_REPOSITORY_LOOKUP
    )
    assert len(events) == 1
    details = events[0].details
    assert "校园二手书交换" not in json.dumps(details, ensure_ascii=False)
    assert details.get("query_fingerprint")
    assert details.get("query_length") == len("校园二手书交换")
    assert details.get("endpoint_kind") == "search"


def _repo_files() -> dict[str, dict[str, object]]:
    """一个结构完整的仓库：README 点名了顶层文件、嵌套目录与一个不存在的路径。"""
    return {
        "/repos/demo/bookswap/readme": {
            "content": base64.b64encode(
                (
                    "# 校园二手书交换平台\n\n"
                    "入口见 app.py 与 src/api，部署见 docs/deploy.md。"
                ).encode()
            ).decode(),
            "encoding": "base64",
            "path": "README.md",
            "html_url": "https://github.com/demo/bookswap/blob/main/README.md",
        },
        "/repos/demo/bookswap/contents": [
            {"name": "README.md", "path": "README.md", "type": "file", "size": 120},
            {"name": "app.py", "path": "app.py", "type": "file", "size": 10},
            {"name": "LICENSE", "path": "LICENSE", "type": "file", "size": 1000},
            {"name": "src", "path": "src", "type": "dir"},
            {"name": "package.json", "path": "package.json", "type": "file", "size": 300},
        ],
        "/repos/demo/bookswap/contents/LICENSE": {
            "content": base64.b64encode(b"MIT License\n\nCopyright (c) 2026").decode(),
            "encoding": "base64",
            "path": "LICENSE",
        },
        "/repos/demo/bookswap/contents/src/api": [
            {"name": "main.py", "path": "src/api/main.py", "type": "file", "size": 500}
        ],
    }


def test_inspection_reads_readme_license_and_confirms_paths_from_the_listing() -> None:
    """读取证据：README、许可正文、根目录清单与实现路径的存在性核对。"""
    calls: list[str] = []
    files = _repo_files()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path in files:
            return httpx.Response(200, json=files[path], headers=_rate_limit_headers(40))
        return httpx.Response(404, json={"message": "Not Found"}, headers=_rate_limit_headers(39))

    reader = GithubRepositoryReader(GithubApiClient(client=_client(handler)))
    analysis = parse_github_request("我想做一个校园二手书交换平台")
    candidate = _candidate("demo/bookswap")
    outcome = reader.inspect_candidates("account-1", [candidate])

    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.readme_status is GithubReadmeStatus.READ
    assert evidence.readme_text is not None and "校园二手书交换平台" in evidence.readme_text
    # 许可：在根目录清单里发现许可文件，并真的读了正文；元数据没给名称时，
    # 名称取自许可文件正文首行（报的是文件里写着什么）。
    assert evidence.license.detected is True
    assert evidence.license.name == "MIT License"
    assert evidence.license.path == "LICENSE"
    assert evidence.license.file_read is True
    assert evidence.license.excerpt is not None and "MIT License" in evidence.license.excerpt
    assert evidence.runnable_hints == ["package.json"]
    # 三层结论都只来自真实读取：顶层文件由根目录清单直接确认（不额外发请求），
    # 嵌套目录真的读了一次，顶层目录不存在的路径从清单就能判定缺失（不发请求）。
    checks = {check.path: check for check in evidence.implementation_checks}
    assert checks["app.py"].status == "confirmed"
    assert "/repos/demo/bookswap/contents/app.py" not in calls
    assert checks["src/api"].status == "confirmed"
    assert "/repos/demo/bookswap/contents/src/api" in calls
    assert checks["docs/deploy.md"].status == "missing"
    assert "/repos/demo/bookswap/contents/docs/deploy.md" not in calls
    assert evidence.matched_query == candidate.matched_query
    assert analysis.whole_idea is True


def test_missing_readme_is_a_fact_not_an_error() -> None:
    """README 不存在是事实：状态为 not_found，仍留下元数据证据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(
                404, json={"message": "Not Found"}, headers=_rate_limit_headers(30)
            )
        if path.endswith("/contents"):
            return httpx.Response(
                200,
                json=[{"name": "src", "path": "src", "type": "dir"}],
                headers=_rate_limit_headers(29),
            )
        return httpx.Response(404, json={}, headers=_rate_limit_headers(28))

    reader = GithubRepositoryReader(GithubApiClient(client=_client(handler)))
    outcome = reader.inspect_candidates("account-1", [_candidate("demo/empty")])

    evidence = outcome.evidence[0]
    assert evidence.readme_status is GithubReadmeStatus.NOT_FOUND
    assert evidence.license.detected is False
    statuses = [record.status for record in outcome.records]
    assert ModuleQueryStatus.ERROR not in statuses


def test_rate_limit_stops_inspection_and_keeps_partial_evidence() -> None:
    """读取中途撞上额度：停止继续读，保留已取得的证据并标记限流。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if "second" in path and path.endswith("/readme"):
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers=_rate_limit_headers(0),
            )
        if path.endswith("/readme"):
            return httpx.Response(
                200,
                json={
                    "content": base64.b64encode(b"# hello").decode(),
                    "encoding": "base64",
                    "path": "README.md",
                },
                headers=_rate_limit_headers(2),
            )
        if path.endswith("/contents"):
            return httpx.Response(
                200, json=[{"name": "src", "path": "src", "type": "dir"}],
                headers=_rate_limit_headers(1),
            )
        return httpx.Response(404, json={}, headers=_rate_limit_headers(0))

    reader = GithubRepositoryReader(GithubApiClient(client=_client(handler)))
    outcome = reader.inspect_candidates(
        "account-1", [_candidate("demo/first"), _candidate("demo/second")]
    )

    assert outcome.rate_limited is True
    assert [item.full_name for item in outcome.evidence] == ["demo/first"]
    # 第二个仓库只读到 README 就被额度挡住，不再继续读它的目录。
    assert not any("second/contents" in path for path in calls)


def test_inspection_is_bounded_by_candidate_and_read_limits() -> None:
    """读取数量有界：候选数与每个仓库的实现文件读取数都有上限。"""
    readme = {
        "content": base64.b64encode(
            "见 a.py、b.py、c.py、d.py、e.py、f.py、g.py".encode()
        ).decode(),
        "encoding": "base64",
        "path": "README.md",
    }
    root = [
        {"name": "a.py", "path": "a.py", "type": "file", "size": 10},
        {"name": "b.py", "path": "b.py", "type": "file", "size": 10},
        {"name": "c.py", "path": "c.py", "type": "file", "size": 10},
        {"name": "d.py", "path": "d.py", "type": "file", "size": 10},
        {"name": "e.py", "path": "e.py", "type": "file", "size": 10},
        {"name": "f.py", "path": "f.py", "type": "file", "size": 10},
        {"name": "g.py", "path": "g.py", "type": "file", "size": 10},
    ]
    file_payload = {"content": base64.b64encode(b"print('x')").decode(), "encoding": "base64"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/readme"):
            return httpx.Response(200, json=readme, headers=_rate_limit_headers(50))
        if path.endswith("/contents"):
            return httpx.Response(200, json=root, headers=_rate_limit_headers(49))
        return httpx.Response(200, json=file_payload, headers=_rate_limit_headers(48))

    reader = GithubRepositoryReader(GithubApiClient(client=_client(handler)))
    candidates = [_candidate(f"demo/repo-{index}") for index in range(INSPECT_LIMIT + 3)]
    outcome = reader.inspect_candidates("account-1", candidates)

    assert len(outcome.evidence) == INSPECT_LIMIT
    for evidence in outcome.evidence:
        assert len(evidence.files_read) <= 1 + IMPLEMENTATION_READ_LIMIT


def _candidate(full_name: str) -> object:
    from bridges.github.contracts import GithubRepositoryCandidate

    return GithubRepositoryCandidate(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description="校园二手书交换平台",
        matched_query="校园二手书交换",
        source="github_search",
    )


def test_headers_and_base_url_are_fixed_for_every_request() -> None:
    """每个请求都用固定主机、固定 API 版本与固定 UA。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []}, headers=_rate_limit_headers(9))

    adapter = GithubApiSearchAdapter(GithubApiClient(client=_client(handler)))
    adapter.search_repositories("account-1", query="q", reason="测试")

    request = seen[0]
    assert str(request.url).startswith("https://api.github.com/search/repositories")
    assert request.headers["user-agent"].startswith("BridGes/")
    assert request.headers["accept"] == "application/vnd.github+json"


def test_json_payload_round_trip_keeps_utc_timestamps() -> None:
    """上游时间戳按 UTC 解析（时区信息不丢，供维护度判定使用）。"""
    payload = json.loads(
        '{"pushed_at": "2026-09-01T10:00:00Z", "created_at": "2025-01-01T00:00:00Z"}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "full_name": "a/b",
                        "html_url": "https://github.com/a/b",
                        "pushed_at": payload["pushed_at"],
                        "created_at": payload["created_at"],
                    }
                ]
            },
            headers=_rate_limit_headers(9),
        )

    adapter = GithubApiSearchAdapter(GithubApiClient(client=_client(handler)))
    candidate = adapter.search_repositories("account-1", query="q", reason="测试").candidates[0]

    assert candidate.pushed_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert candidate.created_at == datetime(2025, 1, 1, tzinfo=UTC)
