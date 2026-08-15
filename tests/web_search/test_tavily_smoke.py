"""Issue 01/07：真实 Tavily smoke（显式 opt-in，默认单测不联网）。

显式设置 ``BRIDGES_TAVILY_SMOKE=1`` 才运行；真实调用 ``api.tavily.com``
并用一个稳定低成本查询验证 Key、端点与解析合同。Issue 07（ADR-0029）起
smoke 同时包含**最小成本真实正文获取**（Tavily Extract 单 URL）：搜索与
正文获取都通过才计 ``passed``。缺 Key 或无网络时报告 ``inconclusive``
（跳过且不计数通过），绝不伪通过；解析/合同漂移报告 ``failed``。输出
脱敏证据行（provider、状态、耗时、结果数、正文获取），不含任何正文与
Key 值。
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

from bridges.closeout.release_gate import _probe_web_body_fetch
from bridges.config import Settings
from bridges.web_search.client import WebSearchError
from bridges.web_search.tavily import (
    TAVILY_SEARCH_PROVIDER,
    TAVILY_SEARCH_PROVIDER_VERSION,
    TavilySearchClient,
)

SMOKE_ENV = "BRIDGES_TAVILY_SMOKE"
_SMOKE_QUERY = "Transformer architecture"


def _evidence(
    provider: str,
    status: str,
    started: float,
    result_count: int | None,
    body_fetch: str,
) -> str:
    return json.dumps(
        {
            "provider": provider,
            "status": status,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "result_count": result_count,
            "provider_version": TAVILY_SEARCH_PROVIDER_VERSION,
            "body_fetch": body_fetch,
        },
        ensure_ascii=False,
    )


@pytest.mark.skipif(
    os.getenv(SMOKE_ENV) != "1",
    reason=f"显式设置 {SMOKE_ENV}=1 才运行真实 Tavily 冒烟",
)
def test_explicit_tavily_smoke_reports_passed_failed_or_inconclusive() -> None:
    started = time.monotonic()
    api_key = Settings().tavily_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        print(
            "SMOKE "
            + _evidence(TAVILY_SEARCH_PROVIDER, "inconclusive", started, None, "not_run")
        )
        pytest.skip("inconclusive: 缺 Tavily Key（BRIDGES_TAVILY_API_KEY）")

    with httpx.Client(trust_env=False, timeout=10.0) as http_client:
        client = TavilySearchClient(
            api_key=api_key, http_client=http_client, timeout=10.0
        )
        try:
            results = client.search(_SMOKE_QUERY, timeout=10.0, fetch_sources=False)
        except WebSearchError as error:
            status = "failed" if error.code in {
                "web_search_contract",
                "web_search_parse",
                "web_search_redirect",
                "web_search_response_too_large",
            } else "inconclusive"
            print(
                "SMOKE "
                + _evidence(TAVILY_SEARCH_PROVIDER, status, started, None, "not_run")
            )
            if status == "failed":
                pytest.fail(f"Tavily 冒烟失败：{error.code}")
            pytest.skip(f"inconclusive: {error.code}")

        if not results:
            print(
                "SMOKE "
                + _evidence(TAVILY_SEARCH_PROVIDER, "failed", started, 0, "not_run")
            )
            pytest.fail("Tavily 冒烟失败：无结果")
        if any(
            result.provider != TAVILY_SEARCH_PROVIDER
            or result.provider_version != TAVILY_SEARCH_PROVIDER_VERSION
            or not result.url.startswith(("https://", "http://"))
            for result in results
        ):
            print(
                "SMOKE "
                + _evidence(
                    TAVILY_SEARCH_PROVIDER, "failed", started, len(results), "not_run"
                )
            )
            pytest.fail("Tavily 冒烟失败：结果合同漂移")

        # Issue 07：最小成本真实正文获取（Extract 单 URL，与发布门探针同源）。
        body_status, body_error = _probe_web_body_fetch(
            http_client, api_key, results[0].url
        )
        if body_status == "failed":
            print(
                "SMOKE "
                + _evidence(
                    TAVILY_SEARCH_PROVIDER, "failed", started, len(results), body_status
                )
            )
            pytest.fail(f"Tavily 冒烟失败：正文获取失败（{body_error or 'unknown'}）")
        if body_status == "inconclusive":
            print(
                "SMOKE "
                + _evidence(
                    TAVILY_SEARCH_PROVIDER,
                    "inconclusive",
                    started,
                    len(results),
                    body_status,
                )
            )
            pytest.skip(f"inconclusive: 正文获取不可判定（{body_error or 'unknown'}）")

    print(
        "SMOKE "
        + _evidence(
            TAVILY_SEARCH_PROVIDER, "passed", started, len(results), body_status
        )
    )
