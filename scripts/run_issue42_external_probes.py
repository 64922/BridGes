"""工单 42：运行真实外部能力最小探针并生成独立报告。

与真实模型配对报告分开：本脚本只发最小必要外部请求（arXiv 全文、
高德校内路线、Tavily、贴吧回复、公开岗位页、视频元数据、GitHub 文件、
生效模型四项能力），并把「实测层次 vs 产品宣称层」写进报告。

用法：

    python scripts/run_issue42_external_probes.py --proxy http://127.0.0.1:7890

凭据只从系统凭据库读取，不写入报告；缺少凭据的门记为未验证。
退出码：0 全部可用或按合同降级；1 存在无降级合同的宣称缺口；2 存在未验证门。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai.run_model_config import (  # noqa: E402
    RunModelConfigSnapshot,
    factory_run_model_config,
)
from bridges.config import get_settings  # noqa: E402
from bridges.credentials.runtime_resolver import RuntimeCredentialResolver  # noqa: E402
from bridges.credentials.store import build_credential_store  # noqa: E402
from bridges.evaluation.external_probe_contracts import (  # noqa: E402
    PRODUCT_CLAIMS,
    PRODUCT_DEGRADATION_BASIS,
    PRODUCT_DEGRADATION_CONTRACT,
    ProbeContext,
    ProbeStatus,
)
from bridges.evaluation.external_probes import (  # noqa: E402
    claim_consistency_problems,
    run_all_probes,
)

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "42-workflow-evaluation"
DEFAULT_DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行工单 42 外部能力最小探针。")
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--proxy", default=None, help="显式代理（如 http://127.0.0.1:7890）"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="单请求超时（秒）"
    )
    return parser.parse_args(argv)


def _secret_value(resolved: object) -> str | None:
    value = getattr(resolved, "value", None)
    configured = bool(getattr(resolved, "configured", False))
    if not configured or value is None:
        return None
    return value.get_secret_value()


def _resolve_credentials(data_dir: Path) -> tuple[str | None, str | None, str | None]:
    settings = get_settings()
    store = build_credential_store(settings, data_dir, namespace="runtime")
    resolver = RuntimeCredentialResolver(settings=settings, credential_store=store)
    return (
        _secret_value(resolver.qwen_api_key()),
        _secret_value(resolver.tavily_api_key()),
        _secret_value(resolver.amap_web_service_key()),
    )


def _load_model_config(data_dir: Path) -> RunModelConfigSnapshot:
    database = data_dir / "bridges.db"
    if database.exists():
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                row = connection.execute(
                    "SELECT payload FROM application_state"
                    " WHERE namespace = 'run_model_config'"
                ).fetchone()
            finally:
                connection.close()
            if row is not None:
                return RunModelConfigSnapshot.model_validate_json(row[0])
        except (sqlite3.Error, ValueError):
            pass
    return factory_run_model_config()


def _capability_names(snapshot: RunModelConfigSnapshot) -> tuple[str, ...]:
    return tuple(
        name
        for name in ("text", "image", "tool_calling", "structured_output")
        if getattr(snapshot.capabilities, name)
    )


def _render_markdown(report: dict[str, object]) -> str:
    probes = report["probes"]
    lines = [
        "# 工单 42 外部能力探针报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 生效模型：{report['model_config']['model_id']}"
        f"（{report['model_config']['source']}）",
        "- 说明：真实模型能力与外部服务可得性分开报告；本报告只含外部探针。",
        "",
        "| 门 | 状态 | 实测层 | 宣称层 | 降级合同 | 结论 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for probe in probes:  # type: ignore[union-attr]
        lines.append(
            f"| {probe['gate']} | {probe['status']} | {probe['level']} | "
            f"{probe['declared_level']} | "
            f"{'有' if probe['degradation_contract'] else '无'} | "
            f"{probe['summary']} |"
        )
    lines.extend(["", "## 逐门降级说明", ""])
    for probe in probes:  # type: ignore[union-attr]
        lines.append(f"- **{probe['gate']}**：{probe['degradation']}")
    if report["consistency_problems"]:
        lines.extend(["", "## 无降级合同的宣称缺口", ""])
        lines.extend(f"- {item}" for item in report["consistency_problems"])  # type: ignore[union-attr]
    if report["inconclusive_gates"]:
        lines.extend(["", "## 未验证门（缺凭据/不可达）", ""])
        lines.extend(f"- {item}" for item in report["inconclusive_gates"])  # type: ignore[union-attr]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = (
        args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = args.data_dir if args.data_dir.is_absolute() else REPO_ROOT / args.data_dir

    qwen_key, tavily_key, amap_key = _resolve_credentials(data_dir)
    snapshot = _load_model_config(data_dir)
    client = httpx.Client(
        timeout=httpx.Timeout(args.timeout, connect=min(10.0, args.timeout)),
        follow_redirects=True,
        proxy=args.proxy,
    )
    try:
        context = ProbeContext(
            http=client,
            qwen_key=qwen_key,
            tavily_key=tavily_key,
            amap_key=amap_key,
            effective_model_id=snapshot.model_id,
            model_capabilities=_capability_names(snapshot),
            model_context_window=snapshot.context_window,
            model_config_source=snapshot.source.value,
        )
        results = run_all_probes(context)
    finally:
        client.close()

    problems = claim_consistency_problems(results)
    inconclusive = [
        result.gate.value
        for result in results
        if result.status is ProbeStatus.INCONCLUSIVE
    ]
    report = {
        "kind": "external-probes",
        "generated_at": datetime.now(UTC).isoformat(),
        "credential_presence": {
            "qwen": bool(qwen_key),
            "tavily": bool(tavily_key),
            "amap": bool(amap_key),
        },
        "model_config": {
            "model_id": snapshot.model_id,
            "source": snapshot.source.value,
            "context_window": snapshot.context_window,
            "capabilities": list(_capability_names(snapshot)),
        },
        "claims": {
            gate.value: {
                "declared_level": PRODUCT_CLAIMS[gate].value,
                "degradation_contract": PRODUCT_DEGRADATION_CONTRACT[gate],
                "degradation_basis": PRODUCT_DEGRADATION_BASIS[gate],
            }
            for gate in PRODUCT_CLAIMS
        },
        "probes": [result.to_dict() for result in results],
        "consistency_problems": problems,
        "inconclusive_gates": inconclusive,
    }
    json_path = output_dir / "external-probes.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path = output_dir / "external-probes.md"
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")

    for result in results:
        print(
            f"{result.gate.value}: {result.status.value}/{result.level.value} - "
            f"{result.summary}"
        )
    print(f"报告：{json_path}")
    for problem in problems:
        print(f"  [缺口] {problem}")
    if inconclusive:
        print(f"  [未验证] {', '.join(inconclusive)}")
    if problems:
        return 1
    if inconclusive:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
