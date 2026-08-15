"""Issue 09 的离线回归门、真实提供方探针和脱敏证据报告。

Issue 07（第 7 轮收口）：本模块同时承担「通用网页搜索 = Tavily」的发布门
合同（ADR-0029）——生产提供方清单断言、真实 Tavily smoke（搜索 + 正文
获取）、产物密钥扫描（``tvly-`` 与 Qwen Key 形态）与金标路由/降级语义
回归的组成项。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from bridges.arxiv_mcp.client import ArxivMcpClient, ArxivMcpError
from bridges.config import Settings
from bridges.storage.database import SCHEMA_VERSION
from bridges.web_search.client import (
    WebSearchError,
    _connection_error_code,
    _read_bounded,
)
from bridges.web_search.tavily import (
    TAVILY_EXTRACT_ENDPOINT,
    TAVILY_EXTRACT_MAX_RESPONSE_BYTES,
    TAVILY_SEARCH_PROVIDER_VERSION,
    TavilySearchClient,
)


class FailureClass(StrEnum):
    """收尾失败的责任边界。"""

    PRODUCT = "product_failure"
    EXTERNAL = "external_unavailable"
    ENVIRONMENT = "environment_misconfigured"


class CheckStatus(StrEnum):
    """发布门单项状态。

    ``INCONCLUSIVE`` 表示外部服务暂时不可达或网络未授权：不能判定通过，
    也不能定性为产品缺陷；发布命令必须非零退出等待人工判定。
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class CheckEvidence:
    """不含命令输出正文的确定性检查证据。"""

    name: str
    status: str
    duration_ms: int = 0
    failure_class: FailureClass | None = None
    error_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "error_category": self.error_category,
        }


@dataclass(frozen=True)
class ProviderProbeEvidence:
    """真实提供方探针的脱敏结果（Issue 01：只登记 tavily 与 arxiv）。

    Issue 07：Tavily 探针增加 ``body_fetch`` 字段——最小成本真实正文获取
    （Extract 单 URL）的三态结果（``passed``/``failed``/``inconclusive``/
    ``not_run``），与搜索状态合并为最终 ``status``；字段值不含任何正文。
    """

    provider: str
    status: str
    semantic_health: str
    checked_at: str = ""
    duration_ms: int = 0
    provider_version: str | None = None
    result_count: int | None = None
    error_category: str | None = None
    failure_class: FailureClass | None = None
    worker_cleanup: bool = True
    body_fetch: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "semantic_health": self.semantic_health,
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
            "provider_version": self.provider_version,
            "result_count": self.result_count,
            "error_category": self.error_category,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "worker_cleanup": self.worker_cleanup,
            "body_fetch": self.body_fetch,
        }


@dataclass
class ReleaseGateReport:
    """可归档的收尾证据摘要，不保存测试 stdout/stderr 或用户正文。"""

    code_version: str
    config_category: str
    schema_version: int
    deterministic_checks: list[CheckEvidence] = field(default_factory=list)
    real_probes: list[ProviderProbeEvidence] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    release_ready: bool = False
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def status(self) -> str:
        if any(check.status == CheckStatus.FAILED for check in self.deterministic_checks):
            return "blocked"
        if any(
            probe.status in {CheckStatus.FAILED, CheckStatus.INCONCLUSIVE}
            for probe in self.real_probes
        ):
            return "blocked"
        return "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "release_ready": self.release_ready,
            "generated_at": self.generated_at,
            "code_version": self.code_version,
            "config_category": self.config_category,
            "schema_version": self.schema_version,
            "deterministic_checks": [check.as_dict() for check in self.deterministic_checks],
            "real_probes": [probe.as_dict() for probe in self.real_probes],
            "risks": list(self.risks),
        }


def classify_failure(name: str, returncode: int, diagnostic_category: str) -> FailureClass | None:
    """根据稳定类别把命令失败映射到产品、外部或环境责任边界。"""

    if returncode == 0:
        return None
    lowered = diagnostic_category.casefold()
    if name.endswith("-probe") or any(
        marker in lowered for marker in ("timeout", "dns", "connection", "rate limit", "upstream")
    ):
        return FailureClass.EXTERNAL
    if any(
        marker in lowered
        for marker in (
            "executable_missing",
            "executable doesn't exist",
            "browser executable",
            "command not found",
            "no such file or directory",
            "no module named",
            "permission denied",
            "not recognized",
        )
    ):
        return FailureClass.ENVIRONMENT
    return FailureClass.PRODUCT


def _error_category(error: BaseException) -> str:
    """只返回稳定错误码，不传播异常正文。"""

    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "executable_missing"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, (OSError, httpx.ConnectError)):
        return "connection"
    return error.__class__.__name__.lower()


def _semantic_web_health(results: Sequence[Any]) -> str:
    for result in results:
        material = " ".join(
            str(value)
            for value in (
                getattr(result, "title", ""),
                getattr(result, "snippet", ""),
                getattr(result, "content_summary", ""),
            )
        ).casefold()
        if "transformer" in material:
            return "ready"
    return "contract_mismatch"


def _semantic_arxiv_health(papers: Sequence[Any]) -> str:
    return (
        "ready"
        if any("transformer" in str(getattr(paper, "title", "")).casefold() for paper in papers)
        else "contract_mismatch"
    )


def run_real_provider_probes() -> list[ProviderProbeEvidence]:
    """显式网络探针：调用者必须主动传入 ``--real-probes`` 才会执行。

    Issue 01/04：真实发布探针只访问 Tavily 与 arXiv；Brave 等备用提供方
    不再探测，配置了备用 Key 反而会使发布门失败（配置漂移）。
    Issue 07：Tavily 探针包含最小成本真实正文获取（ADR-0029 smoke），
    三态结果经 ``body_fetch`` 字段脱敏归档。
    """

    probes: list[ProviderProbeEvidence] = []
    with httpx.Client(trust_env=False, timeout=10.0) as http_client:
        probes.append(_probe_web(http_client))
        probes.append(_probe_arxiv(http_client))
    return probes


def _probe_web(http_client: httpx.Client) -> ProviderProbeEvidence:
    """真实 Tavily 发布探针：可解析结果 + provider/version/错误语义合同校验。

    Issue 07：搜索通过后追加一次**最小成本真实正文获取**（Tavily Extract
    单 URL，ADR-0029 smoke 合同）。只通过以下条件：固定 Tavily 端点返回
    可解析结果、结果语义与固定探针查询匹配、结果 provider/version 与合同
    完全一致、且正文获取成功。缺 Key 或网络未授权、外部服务暂时不可达报告
    ``inconclusive``（不计入通过）；解析/合同漂移、正文获取失败报告
    ``failed``。mock、fixture、cassette 无法让本探针变绿（每次运行都构造
    真实客户端）。
    """
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()
    api_key = Settings().tavily_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        return ProviderProbeEvidence(
            provider="tavily",
            status=CheckStatus.INCONCLUSIVE,
            semantic_health="unavailable",
            checked_at=checked_at,
            duration_ms=_duration_ms(started),
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            result_count=None,
            error_category="web_search_credentials",
            failure_class=FailureClass.ENVIRONMENT,
            worker_cleanup=True,
            body_fetch="not_run",
        )
    client = TavilySearchClient(
        api_key=api_key,
        http_client=http_client,
        timeout=8.0,
        fetch_sources=False,
    )
    try:
        results = client.search("Transformer architecture", timeout=8.0)
    except WebSearchError as error:
        return _web_probe_outcome(started, checked_at, _error_category(error))
    except Exception as error:  # noqa: BLE001 - 探针只输出稳定分类
        return _web_probe_outcome(started, checked_at, _error_category(error))
    result_count = len(results)
    semantic = _semantic_web_health(results)
    provider_set = {getattr(result, "provider", None) for result in results}
    version_set = {getattr(result, "provider_version", None) for result in results}
    contract_ok = (
        semantic == "ready"
        and provider_set == {"tavily"}
        and version_set == {TAVILY_SEARCH_PROVIDER_VERSION}
    )
    if not contract_ok:
        # 解析契约漂移：非空 JSON/HTTP 200 本身不足以判定 READY。
        return ProviderProbeEvidence(
            provider="tavily",
            status=CheckStatus.FAILED,
            semantic_health=semantic,
            checked_at=checked_at,
            duration_ms=_duration_ms(started),
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            result_count=result_count,
            error_category="parse_contract_drift",
            failure_class=FailureClass.PRODUCT,
            worker_cleanup=True,
            body_fetch="not_run",
        )
    body_status, body_error = "not_run", None
    if results:
        body_status, body_error = _probe_web_body_fetch(http_client, api_key, results[0].url)
    if body_status == CheckStatus.FAILED:
        return ProviderProbeEvidence(
            provider="tavily",
            status=CheckStatus.FAILED,
            semantic_health=semantic,
            checked_at=checked_at,
            duration_ms=_duration_ms(started),
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            result_count=result_count,
            error_category=body_error or "web_search_body_fetch_failed",
            failure_class=FailureClass.PRODUCT,
            worker_cleanup=True,
            body_fetch=body_status,
        )
    if body_status == CheckStatus.INCONCLUSIVE:
        return ProviderProbeEvidence(
            provider="tavily",
            status=CheckStatus.INCONCLUSIVE,
            semantic_health=semantic,
            checked_at=checked_at,
            duration_ms=_duration_ms(started),
            provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
            result_count=result_count,
            error_category=body_error or "web_search_body_fetch_inconclusive",
            failure_class=(
                FailureClass.ENVIRONMENT
                if body_error in {"web_search_credentials", "web_search_configuration"}
                else FailureClass.EXTERNAL
            ),
            worker_cleanup=True,
            body_fetch=body_status,
        )
    return ProviderProbeEvidence(
        provider="tavily",
        status=CheckStatus.PASSED,
        semantic_health=semantic,
        checked_at=checked_at,
        duration_ms=_duration_ms(started),
        provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
        result_count=result_count,
        error_category=None,
        failure_class=None,
        worker_cleanup=True,
        body_fetch=body_status,
    )


def _probe_web_body_fetch(
    http_client: httpx.Client,
    api_key: SecretStr,
    url: str,
) -> tuple[str, str | None]:
    """最小成本真实正文获取（Tavily Extract 单 URL）；返回 (状态, 错误码)。

    成功条件：固定 Extract 端点返回 200 且至少一个结果带非空
    ``raw_content``。凭据无效（401/403）、限流（429）、网络不可达与上游
    暂时故障 → ``inconclusive``（不伪通过）；解析/契约漂移与请求类错误 →
    ``failed``。任何路径都不回显 Key、URL 正文与响应正文。
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {api_key.get_secret_value()}",
    }
    payload = {"urls": [url], "extract_depth": "basic"}
    try:
        with http_client.stream(
            "POST",
            TAVILY_EXTRACT_ENDPOINT,
            json=payload,
            headers=headers,
            follow_redirects=False,
            timeout=8.0,
        ) as response:
            status_code = response.status_code
            if status_code in {401, 403}:
                return CheckStatus.INCONCLUSIVE, "web_search_configuration"
            if status_code == 429:
                return CheckStatus.INCONCLUSIVE, "web_search_rate_limit"
            if status_code >= 500:
                return CheckStatus.INCONCLUSIVE, "web_search_provider"
            if status_code != 200:
                return CheckStatus.FAILED, "web_search_request"
            body = _read_bounded(response, TAVILY_EXTRACT_MAX_RESPONSE_BYTES)
    except httpx.TimeoutException:
        return CheckStatus.INCONCLUSIVE, "web_search_timeout"
    except httpx.ConnectError as error:
        return CheckStatus.INCONCLUSIVE, _connection_error_code(error)
    except httpx.HTTPError:
        return CheckStatus.INCONCLUSIVE, "web_search_offline"
    try:
        payload = json.loads(body.decode("utf-8"))
        raw = payload.get("results") if isinstance(payload, dict) else None
        ok = isinstance(raw, list) and any(
            isinstance(item, dict)
            and isinstance(item.get("raw_content"), str)
            and item["raw_content"].strip()
            for item in raw
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return CheckStatus.FAILED, "web_search_contract"
    if not ok:
        return CheckStatus.FAILED, "web_search_contract"
    return CheckStatus.PASSED, None


def _web_probe_outcome(
    started: float, checked_at: str, error_code: str
) -> ProviderProbeEvidence:
    """把稳定错误码映射到 inconclusive/failed 与责任边界。

    缺 Key/凭据无效（credentials/configuration）、网络未授权（permission）
    与外部服务暂时不可达（DNS、connect、offline、timeout、rate_limit、
    5xx upstream、challenge）→ ``inconclusive``；
    解析、重定向、响应过大与请求类错误 → ``failed``。
    """
    inconclusive_codes = {
        "web_search_credentials",
        "web_search_configuration",
        "web_search_permission",
        "web_search_dns",
        "web_search_connect",
        "web_search_offline",
        "web_search_timeout",
        "web_search_rate_limit",
        "web_search_provider",
        "web_search_provider_challenge",
    }
    if error_code in inconclusive_codes:
        status = CheckStatus.INCONCLUSIVE
        failure_class = (
            FailureClass.ENVIRONMENT
            if error_code in {"web_search_credentials", "web_search_configuration"}
            else FailureClass.EXTERNAL
        )
    else:
        status = CheckStatus.FAILED
        failure_class = FailureClass.PRODUCT
    return ProviderProbeEvidence(
        provider="tavily",
        status=status,
        semantic_health="unavailable",
        checked_at=checked_at,
        duration_ms=_duration_ms(started),
        provider_version=TAVILY_SEARCH_PROVIDER_VERSION,
        result_count=None,
        error_category=error_code,
        failure_class=failure_class,
        worker_cleanup=True,
    )


def _probe_arxiv(http_client: httpx.Client) -> ProviderProbeEvidence:
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()
    client = ArxivMcpClient(http_client=http_client, timeout=10.0)
    try:
        papers = client.search("Transformer architecture", max_results=1)
        semantic = _semantic_arxiv_health(papers)
        status = CheckStatus.PASSED if semantic == "ready" else CheckStatus.FAILED
        return ProviderProbeEvidence(
            provider="arxiv",
            status=status,
            semantic_health=semantic,
            checked_at=checked_at,
            duration_ms=_duration_ms(started),
            provider_version=getattr(client, "provider_version", None),
            result_count=len(papers),
            error_category=None if status == CheckStatus.PASSED else "arxiv_semantic_health",
            failure_class=None if status == CheckStatus.PASSED else FailureClass.EXTERNAL,
            worker_cleanup=True,
        )
    except ArxivMcpError as error:
        return _failed_probe(
            "arxiv", started, checked_at, _error_category(error), FailureClass.EXTERNAL
        )
    except Exception as error:  # noqa: BLE001 - 探针只输出稳定分类
        return _failed_probe(
            "arxiv", started, checked_at, _error_category(error), FailureClass.EXTERNAL
        )
    finally:
        client.close()


def _failed_probe(
    provider: str,
    started: float,
    checked_at: str,
    error_category: str,
    failure_class: FailureClass,
) -> ProviderProbeEvidence:
    return ProviderProbeEvidence(
        provider=provider,
        status=CheckStatus.FAILED,
        semantic_health="unavailable",
        checked_at=checked_at,
        duration_ms=_duration_ms(started),
        error_category=error_category,
        failure_class=failure_class,
        worker_cleanup=True,
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def write_report(report: ReleaseGateReport, path: Path) -> None:
    """写入 JSON 或 Markdown 摘要；两者都只包含脱敏字段。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.as_dict()
    if path.suffix.casefold() == ".md":
        lines = [
            "# BridGes 三条旅程收尾发布门",
            "",
            f"- 状态：`{payload['status']}`",
            f"- 可发布：`{payload['release_ready']}`",
            f"- 代码版本：`{payload['code_version']}`",
            f"- 配置类别：`{payload['config_category']}`",
            f"- 数据模式版本：`{payload['schema_version']}`",
            "",
            "## 确定性检查",
            "",
        ]
        lines.extend(
            f"- `{item['name']}`：`{item['status']}`（{item['duration_ms']} ms）"
            for item in payload["deterministic_checks"]
        )
        lines.extend(["", "## 真实提供方探针", ""])
        lines.extend(
            f"- `{item['provider']}`：`{item['status']}`，语义健康 `{item['semantic_health']}`，"
            f"探针时间 `{item['checked_at'] or 'unknown'}`，{item['duration_ms']} ms，"
            f"版本 `{item['provider_version'] or 'unknown'}`，"
            f"结果数 `{item['result_count'] if item['result_count'] is not None else 'n/a'}`，"
            f"正文获取 `{item.get('body_fetch') or 'n/a'}`，"
            f"错误类别 `{item['error_category'] or 'none'}`"
            for item in payload["real_probes"]
        )
        lines.extend(["", "## 风险", ""])
        lines.extend(f"- `{risk}`" for risk in payload["risks"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _code_version(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _python_executable(repo_root: Path) -> str:
    candidate = repo_root / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return str(candidate)
    candidate = repo_root / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _run_command(
    *,
    name: str,
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: float,
) -> CheckEvidence:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, PermissionError) as error:
        category = _error_category(error)
        return CheckEvidence(
            name=name,
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.ENVIRONMENT,
            error_category=category,
        )
    except subprocess.TimeoutExpired:
        return CheckEvidence(
            name=name,
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.PRODUCT,
            error_category="command_timeout",
        )
    if result.returncode == 0:
        return CheckEvidence(
            name=name,
            status=CheckStatus.PASSED,
            duration_ms=_duration_ms(started),
        )
    category = _diagnostic_category(result.stdout, result.stderr)
    return CheckEvidence(
        name=name,
        status=CheckStatus.FAILED,
        duration_ms=_duration_ms(started),
        failure_class=classify_failure(name, result.returncode, category),
        error_category=category,
    )


def _diagnostic_category(stdout: str, stderr: str) -> str:
    """从工具输出选择稳定类别，不把输出正文写入报告。"""

    text = f"{stdout}\n{stderr}".casefold()
    markers = (
        ("executable doesn't exist", "browser_executable_missing"),
        ("no such file or directory", "executable_missing"),
        ("no module named", "python_module_missing"),
        ("command not found", "command_missing"),
        ("timeout", "command_timeout"),
        ("permission denied", "permission_denied"),
        ("error", "tool_error"),
    )
    return next((category for marker, category in markers if marker in text), "check_failed")


#: 被禁止的备用通用搜索提供方配置（Issue 04：出现即配置漂移）。
#: Issue 01 起 Tavily 是生产主提供方，其 Key/开关不再是漂移。
_FALLBACK_SEARCH_ENV_KEYS = (
    "BRIDGES_BRAVE_SEARCH_API_KEY",
    "BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED",
    "BRIDGES_BRAVE_SEARCH_API_KEY_FILE",
)

#: 发布门确定性 Python 测试清单（相对仓库根）。
#: Issue 07：金标路由（Issue 03）与降级语义（Issue 02）作为门禁组成部分
#: 被执行，而非仅被文档引用。
RELEASE_GATE_PYTHON_TESTS: tuple[str, ...] = (
    "tests/closeout/test_three_journeys.py",
    "tests/closeout/test_release_gate.py",
    "tests/chat/test_issue08_conversational_learning.py",
    "tests/chat/test_issue03_learning_evidence_chat.py",
    "tests/chat/test_arxiv_search_chat.py",
    "tests/chat/test_issue05_deadline_cancellation.py",
    "tests/chat/test_issue06_latency_budget.py",
    "tests/closeout/test_arxiv_worker_reliability.py",
    "tests/profiles/test_profile_v2_safe_replay.py",
    "tests/web_search/test_duckduckgo_service.py",
    "tests/web_search/test_public_search_fallback.py",
    "tests/web_search/test_health_monitor.py",
    "tests/web_search/test_health_probe_mapping.py",
    # Issue 17：真实性发布门确定性检查（清单/静态扫描/组合/spy/live）。
    "tests/closeout/test_capability_manifest.py",
    "tests/closeout/test_authenticity_scans.py",
    "tests/closeout/test_authenticity_gate.py",
    # Issue 07：金标路由（Issue 03）与降级语义（Issue 02）。
    "tests/chat/test_golden_intent_routes.py",
    "tests/learning/test_teaching_gate.py",
    # Issue 07：发布门组成项（清单分类/提供方断言/扫描规则/三态 smoke）。
    "tests/closeout/test_search_release_gate.py",
)


def _provider_drift_check(environment: dict[str, str]) -> CheckEvidence:
    """检测备用提供方 Key/开关：存在即配置漂移，发布门失败关闭。

    当前产品唯一通用联网提供方是 Tavily；Brave/Bing/Serper/SearXNG 等
    任何备用源配置（含 Key）都使发布门以稳定错误码
    ``unexpected_search_provider`` 失败，且不会自动切换提供方。
    """
    started = time.monotonic()
    drifted: list[str] = []
    for key in _FALLBACK_SEARCH_ENV_KEYS:
        if environment.get(key, "").strip():
            drifted.append(key)
    allowed_keys = {
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "SCIENCE_COMPANION_TAVILY_API_KEY",
        "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
        *_FALLBACK_SEARCH_ENV_KEYS,
    }
    for key, value in environment.items():
        if (
            value.strip()
            and key.startswith("BRIDGES_")
            and ("SEARCH" in key or "BRAVE" in key or "TAVILY" in key)
            and key not in allowed_keys
        ):
            drifted.append(key)
    if drifted:
        return CheckEvidence(
            name="search-provider-drift",
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.PRODUCT,
            error_category="unexpected_search_provider",
        )
    return CheckEvidence(
        name="search-provider-drift",
        status=CheckStatus.PASSED,
        duration_ms=_duration_ms(started),
    )


def _production_provider_check() -> CheckEvidence:
    """生产组合断言：通用网页搜索提供方清单恰好只有 ``tavily``（Issue 07）。

    断言组合根（``build_web_search_provider``）构造的是
    ``TavilySearchClient`` 且 provider 标识为 ``tavily``、合同主提供方常量
    为 ``tavily``、默认配置下不启用任何备用提供方（ADR-0029）。任一不满足
    都以稳定错误码 ``unexpected_search_provider`` 失败关闭；本检查不读取
    任何 Key 值。
    """
    from bridges.web_search.contracts import PRIMARY_WEB_SEARCH_PROVIDER
    from bridges.web_search.providers import (
        REGISTERED_FALLBACK_PROVIDERS,
        FallbackProviderConfigurationError,
        build_fallback_provider,
        build_web_search_provider,
    )

    started = time.monotonic()
    problems: list[str] = []
    if PRIMARY_WEB_SEARCH_PROVIDER != "tavily":
        problems.append(
            f"合同主提供方常量为 {PRIMARY_WEB_SEARCH_PROVIDER!r}，必须恰好为 tavily。"
        )
    try:
        provider = build_web_search_provider(None)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        problems.append(f"生产组合根构造异常（{exc.__class__.__name__.lower()}）。")
    else:
        provider_name = getattr(provider, "provider_name", None)
        if provider_name != "tavily":
            problems.append(
                f"生产通用网页搜索提供方为 {provider_name!r}，必须恰好为 tavily。"
            )
        if not isinstance(provider, TavilySearchClient):
            problems.append("生产组合根未构造 TavilySearchClient。")
    try:
        fallback = build_fallback_provider(Settings())
    except FallbackProviderConfigurationError as exc:
        problems.append(f"默认配置下备用提供方构造异常：{exc}")
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        problems.append(f"备用提供方检查异常（{exc.__class__.__name__.lower()}）。")
    else:
        if fallback is not None:
            problems.append("默认配置下启用了备用搜索提供方，生产提供方清单必须恰好只有 tavily。")
    if "duckduckgo" in {name.casefold() for name in REGISTERED_FALLBACK_PROVIDERS}:
        problems.append("DDG 仍登记为备用提供方，生产组合不得再出现 duckduckgo。")
    if problems:
        return CheckEvidence(
            name="production-search-provider",
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.PRODUCT,
            error_category="unexpected_search_provider",
        )
    return CheckEvidence(
        name="production-search-provider",
        status=CheckStatus.PASSED,
        duration_ms=_duration_ms(started),
    )


def _load_artifact_scan() -> Any | None:
    """按路径加载 ``scripts/artifact_secret_scan.py``（不执行其 main）。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "artifact_secret_scan.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("bridges_closeout_artifact_scan", script)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_report_for_secrets(path: Path) -> CheckEvidence:
    """进程内复核发布报告：含 ``tvly-``/Qwen Key 形态即失败关闭（Issue 07）。

    报告由脱敏字段构造，本检查是最后一道防线：一旦报告正文命中
    ``scripts/artifact_secret_scan`` 的任一秘密形态（含新加的 ``tvly-``），
    发布门以 ``secret_leak_in_report`` 失败关闭。只输出命中位置与类型，
    绝不回显命中值。
    """
    started = time.monotonic()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return CheckEvidence(
            name="release-report-secret-scan",
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.ENVIRONMENT,
            error_category=exc.__class__.__name__.lower(),
        )
    scanner = _load_artifact_scan()
    if scanner is None:
        return CheckEvidence(
            name="release-report-secret-scan",
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.ENVIRONMENT,
            error_category="secret_scan_unavailable",
        )
    hits = scanner.scan_text(text)
    if hits:
        return CheckEvidence(
            name="release-report-secret-scan",
            status=CheckStatus.FAILED,
            duration_ms=_duration_ms(started),
            failure_class=FailureClass.PRODUCT,
            error_category="secret_leak_in_report",
        )
    return CheckEvidence(
        name="release-report-secret-scan",
        status=CheckStatus.PASSED,
        duration_ms=_duration_ms(started),
    )


def diagnose_web_health() -> int:
    """运维诊断命令：单次真实 Tavily 探针，输出 JSON 与简洁中文摘要。

    同一次运行记录探针时间、状态、耗时、provider version、结果数量与
    稳定错误码；退出码：0=READY，1=失败，2=无法判定（缺 Key/外部不可达/
    未授权）。
    """
    with httpx.Client(trust_env=False, timeout=10.0) as http_client:
        probe = _probe_web(http_client)
    payload = probe.as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if probe.status == CheckStatus.PASSED:
        print(
            "Tavily 公网搜索可用："
            f"{probe.result_count or 0} 条可解析结果，"
            f"正文获取 {probe.body_fetch or 'n/a'}，"
            f"耗时 {probe.duration_ms} ms，"
            f"提供方版本 {probe.provider_version or 'unknown'}。"
        )
        return 0
    if probe.status == CheckStatus.INCONCLUSIVE:
        print(
            "无法判定：Tavily 暂时不可达、缺搜索凭据或网络未授权"
            f"（{probe.error_category or 'unknown'}），耗时 {probe.duration_ms} ms；"
            "请人工复核网络与凭据配置后重试，不能以 fixture/mock 替代真实判定。"
        )
        return 2
    print(
        "失败：Tavily 探针未通过合同校验"
        f"（{probe.error_category or 'unknown'}），耗时 {probe.duration_ms} ms；"
        "请检查解析契约或提供方配置。"
    )
    return 1


def run_gate(
    repo_root: Path | None = None,
    *,
    real_probes: bool = False,
    skip_browser: bool = False,
    skip_web: bool = False,
    report_path: Path | None = None,
) -> ReleaseGateReport:
    """运行离线门；只有 ``real_probes=True`` 才访问外部网络。"""

    root = (repo_root or _repo_root()).resolve()
    python = _python_executable(root)
    environment = os.environ.copy()
    environment.update(
        {
            "BRIDGES_ENVIRONMENT": "test",
            "BRIDGES_CLOSEOUT_FIXTURES": "true",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    checks: list[CheckEvidence] = []
    checks.append(_provider_drift_check(environment))
    # Issue 07：生产组合断言——通用网页搜索提供方清单恰好只有 tavily。
    checks.append(_production_provider_check())
    python_tests = [str(root / relative) for relative in RELEASE_GATE_PYTHON_TESTS]
    # 每次使用新的临时目录，避免不同权限身份的历史 pytest 目录互相阻塞。
    with tempfile.TemporaryDirectory(prefix=".release-gate-", dir=root) as basetemp:
        checks.append(
            _run_command(
                name="python-tests",
                command=[
                    python,
                    "-m",
                    "pytest",
                    "--basetemp",
                    basetemp,
                    *python_tests,
                    "-q",
                ],
                cwd=root,
                environment=environment,
                timeout_seconds=300,
            )
        )
    checks.append(
        _run_command(
            name="python-static",
            command=[
                python,
                "-m",
                "ruff",
                "check",
                str(root / "src" / "bridges" / "closeout"),
                str(root / "src" / "bridges" / "config.py"),
                str(root / "tests" / "closeout"),
                str(root / "scripts" / "release_gate.py"),
                str(root / "scripts" / "qwen_authenticity_gate.py"),
                str(root / "scripts" / "artifact_secret_scan.py"),
            ],
            cwd=root,
            environment=environment,
            timeout_seconds=120,
        )
    )
    checks.append(
        _run_command(
            name="python-typecheck",
            command=[
                python,
                "-m",
                "mypy",
                str(root / "src" / "bridges" / "closeout"),
                str(root / "scripts" / "release_gate.py"),
                str(root / "scripts" / "qwen_authenticity_gate.py"),
                str(root / "scripts" / "artifact_secret_scan.py"),
            ],
            cwd=root,
            environment=environment,
            timeout_seconds=180,
        )
    )
    # Issue 07：密钥泄漏硬门——扫描日志/运行锁/消息投影/SSE 记录/发布
    # 报告产物（``tvly-`` 与 Qwen Key 形态，scripts/artifact_secret_scan）。
    checks.append(
        _run_command(
            name="artifact-secret-scan",
            command=[python, str(root / "scripts" / "artifact_secret_scan.py")],
            cwd=root,
            environment=environment,
            timeout_seconds=120,
        )
    )
    if not skip_web:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        checks.extend(
            [
                _run_command(
                    name="web-unit",
                    command=[npm, "run", "test:unit"],
                    cwd=root / "apps" / "web",
                    environment=environment,
                    timeout_seconds=180,
                ),
                _run_command(
                    name="web-typecheck",
                    command=[npm, "run", "typecheck"],
                    cwd=root / "apps" / "web",
                    environment=environment,
                    timeout_seconds=180,
                ),
            ]
        )
    else:
        checks.append(CheckEvidence("web-checks", CheckStatus.SKIPPED))
    if not skip_browser:
        npx = "npx.cmd" if os.name == "nt" else "npx"
        checks.append(
            _run_command(
                name="playwright-closeout",
                command=[npx, "playwright", "test", "-c", "playwright.release-gate.config.ts"],
                cwd=root / "apps" / "web",
                environment=environment,
                timeout_seconds=420,
            )
        )
    else:
        checks.append(CheckEvidence("playwright-closeout", CheckStatus.SKIPPED))

    probes = run_real_provider_probes() if real_probes else []
    risks: list[str] = []
    if not real_probes:
        risks.append("real_provider_probes_not_run")
    if skip_web:
        risks.append("web_checks_skipped")
    if skip_browser:
        risks.append("browser_gate_skipped")
    if any(
        probe.status in {CheckStatus.FAILED, CheckStatus.INCONCLUSIVE}
        for probe in probes
    ):
        risks.append("real_provider_probe_failed")
    if any(probe.status == CheckStatus.INCONCLUSIVE for probe in probes):
        risks.append("real_provider_probe_inconclusive")
    deterministic_ok = all(
        check.status in {CheckStatus.PASSED, CheckStatus.SKIPPED} for check in checks
    )
    probes_ok = real_probes and all(probe.status == CheckStatus.PASSED for probe in probes)
    report = ReleaseGateReport(
        code_version=_code_version(root),
        config_category="real-provider-probe" if real_probes else "deterministic-test",
        schema_version=SCHEMA_VERSION,
        deterministic_checks=checks,
        real_probes=probes,
        risks=risks,
        release_ready=deterministic_ok and probes_ok,
    )
    if report_path is not None:
        write_report(report, report_path)
        # Issue 07：报告本身也纳入密钥泄漏硬门（进程内复核）；把检查结果
        # 并入报告后再写一次，保证归档报告包含本次复核证据。
        checks.append(_scan_report_for_secrets(report_path))
        deterministic_ok = all(
            check.status in {CheckStatus.PASSED, CheckStatus.SKIPPED} for check in checks
        )
        report = ReleaseGateReport(
            code_version=_code_version(root),
            config_category="real-provider-probe" if real_probes else "deterministic-test",
            schema_version=SCHEMA_VERSION,
            deterministic_checks=checks,
            real_probes=probes,
            risks=risks,
            release_ready=deterministic_ok and probes_ok,
        )
        write_report(report, report_path)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 BridGes 三条旅程收尾发布门。")
    parser.add_argument(
        "--real-probes",
        action="store_true",
        help="显式访问 Tavily 与 arXiv 真实探针。",
    )
    parser.add_argument(
        "--web-health",
        action="store_true",
        help="运维诊断：只运行一次真实 Tavily 探针，输出 JSON 与中文摘要（不运行完整发布门）。",
    )
    parser.add_argument("--skip-browser", action="store_true", help="跳过真实浏览器收尾门。")
    parser.add_argument(
        "--skip-web", action="store_true", help="跳过 Web 单测与 TypeScript 类型检查。"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".tmp") / "release-gate" / "report.json",
        help="脱敏 JSON/Markdown 证据报告路径。",
    )
    args = parser.parse_args(argv)
    if args.web_health:
        return diagnose_web_health()
    report = run_gate(
        real_probes=args.real_probes,
        skip_browser=args.skip_browser,
        skip_web=args.skip_web,
        report_path=args.report,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.status == "blocked":
        return 1
    if args.real_probes and not report.release_ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
