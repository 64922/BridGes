"""Issue 09 的离线回归门、真实提供方探针和脱敏证据报告。"""

from __future__ import annotations

import argparse
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
from bridges.storage.database import SCHEMA_VERSION
from bridges.web_search.client import DuckDuckGoClient, WebSearchError
from bridges.web_search.providers import BraveSearchClient


class FailureClass(StrEnum):
    """收尾失败的责任边界。"""

    PRODUCT = "product_failure"
    EXTERNAL = "external_unavailable"
    ENVIRONMENT = "environment_misconfigured"


class CheckStatus(StrEnum):
    """发布门单项状态。"""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


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
    """真实提供方探针的脱敏结果。"""

    provider: str
    status: str
    semantic_health: str
    duration_ms: int = 0
    error_category: str | None = None
    failure_class: FailureClass | None = None
    worker_cleanup: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "semantic_health": self.semantic_health,
            "duration_ms": self.duration_ms,
            "error_category": self.error_category,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "worker_cleanup": self.worker_cleanup,
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
        if any(probe.status == CheckStatus.FAILED for probe in self.real_probes):
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
    """显式网络探针：调用者必须主动传入 ``--real-probes`` 才会执行。"""

    probes: list[ProviderProbeEvidence] = []
    with httpx.Client(trust_env=False, timeout=10.0) as http_client:
        probes.append(_probe_web(http_client))
        probes.append(_probe_brave(http_client))
        probes.append(_probe_arxiv(http_client))
    return probes


def _probe_web(http_client: httpx.Client) -> ProviderProbeEvidence:
    started = time.monotonic()
    client = DuckDuckGoClient(
        http_client=http_client,
        timeout=8.0,
        fetch_sources=False,
    )
    try:
        results = client.search("Transformer architecture", timeout=8.0)
        semantic = _semantic_web_health(results)
        status = CheckStatus.PASSED if semantic == "ready" else CheckStatus.FAILED
        return ProviderProbeEvidence(
            provider="duckduckgo",
            status=status,
            semantic_health=semantic,
            duration_ms=_duration_ms(started),
            error_category=None if status == CheckStatus.PASSED else "web_search_semantic_health",
            failure_class=None if status == CheckStatus.PASSED else FailureClass.EXTERNAL,
        )
    except WebSearchError as error:
        return _failed_probe("duckduckgo", started, _error_category(error), FailureClass.EXTERNAL)
    except Exception as error:  # noqa: BLE001 - 探针只输出稳定分类
        return _failed_probe("duckduckgo", started, _error_category(error), FailureClass.EXTERNAL)


def _probe_brave(http_client: httpx.Client) -> ProviderProbeEvidence:
    started = time.monotonic()
    key = os.environ.get("BRIDGES_BRAVE_SEARCH_API_KEY", "").strip()
    enabled = os.environ.get("BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED", "").strip().lower()
    if not key or enabled not in {"1", "true", "yes"}:
        return _failed_probe(
            "brave_search",
            started,
            "fallback_provider_not_configured",
            FailureClass.ENVIRONMENT,
        )
    client = BraveSearchClient(
        api_key=SecretStr(key),
        http_client=http_client,
        timeout=8.0,
    )
    try:
        results = client.search("Transformer architecture", timeout=8.0)
        semantic = _semantic_web_health(results)
        status = CheckStatus.PASSED if semantic == "ready" else CheckStatus.FAILED
        return ProviderProbeEvidence(
            provider="brave_search",
            status=status,
            semantic_health=semantic,
            duration_ms=_duration_ms(started),
            error_category=None if status == CheckStatus.PASSED else "web_search_semantic_health",
            failure_class=None if status == CheckStatus.PASSED else FailureClass.EXTERNAL,
        )
    except WebSearchError as error:
        return _failed_probe("brave_search", started, _error_category(error), FailureClass.EXTERNAL)
    except Exception as error:  # noqa: BLE001 - 探针只输出稳定分类
        return _failed_probe("brave_search", started, _error_category(error), FailureClass.EXTERNAL)


def _probe_arxiv(http_client: httpx.Client) -> ProviderProbeEvidence:
    started = time.monotonic()
    client = ArxivMcpClient(http_client=http_client, timeout=10.0)
    try:
        papers = client.search("Transformer architecture", max_results=1)
        semantic = _semantic_arxiv_health(papers)
        status = CheckStatus.PASSED if semantic == "ready" else CheckStatus.FAILED
        return ProviderProbeEvidence(
            provider="arxiv",
            status=status,
            semantic_health=semantic,
            duration_ms=_duration_ms(started),
            error_category=None if status == CheckStatus.PASSED else "arxiv_semantic_health",
            failure_class=None if status == CheckStatus.PASSED else FailureClass.EXTERNAL,
            worker_cleanup=True,
        )
    except ArxivMcpError as error:
        return _failed_probe("arxiv", started, _error_category(error), FailureClass.EXTERNAL)
    except Exception as error:  # noqa: BLE001 - 探针只输出稳定分类
        return _failed_probe("arxiv", started, _error_category(error), FailureClass.EXTERNAL)
    finally:
        client.close()


def _failed_probe(
    provider: str,
    started: float,
    error_category: str,
    failure_class: FailureClass,
) -> ProviderProbeEvidence:
    return ProviderProbeEvidence(
        provider=provider,
        status=CheckStatus.FAILED,
        semantic_health="unavailable",
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
            f"{item['duration_ms']} ms，错误类别 `{item['error_category'] or 'none'}`"
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
    python_tests = [
        str(root / "tests" / "closeout" / "test_three_journeys.py"),
        str(root / "tests" / "closeout" / "test_release_gate.py"),
        str(root / "tests" / "chat" / "test_issue08_conversational_learning.py"),
        str(root / "tests" / "chat" / "test_issue03_learning_evidence_chat.py"),
        str(root / "tests" / "chat" / "test_arxiv_search_chat.py"),
        str(root / "tests" / "chat" / "test_issue05_deadline_cancellation.py"),
        str(root / "tests" / "chat" / "test_issue06_latency_budget.py"),
        str(root / "tests" / "closeout" / "test_arxiv_worker_reliability.py"),
        str(root / "tests" / "profiles" / "test_profile_v2_safe_replay.py"),
        str(root / "tests" / "web_search" / "test_duckduckgo_service.py"),
        str(root / "tests" / "web_search" / "test_public_search_fallback.py"),
    ]
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
            ],
            cwd=root,
            environment=environment,
            timeout_seconds=180,
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
    if any(probe.status == CheckStatus.FAILED for probe in probes):
        risks.append("real_provider_probe_failed")
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
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 BridGes 三条旅程收尾发布门。")
    parser.add_argument("--real-probes", action="store_true", help="显式访问 DDG、Brave 与 arXiv。")
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
