"""运行新树生产语义角色真实模型评测；固定合成来源，不冒充配对/外部门。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai.adapters import AdapterError  # noqa: E402
from bridges.ai.capability_registry import CapabilityRegistry  # noqa: E402
from bridges.ai.model_quota import build_run_model_quota  # noqa: E402
from bridges.ai.production import register_builtin_capabilities  # noqa: E402
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter  # noqa: E402
from bridges.ai.qwen_client import QwenApiClient, first_choice  # noqa: E402
from bridges.config import get_settings  # noqa: E402
from bridges.evaluation.workflow_semantics import (  # noqa: E402
    fingerprint,
    run_semantic_cases,
)
from bridges.evaluation.workflow_semantics_scope import run_scope_cases  # noqa: E402
from scripts.run_issue42_external_probes import (  # noqa: E402
    DEFAULT_DATA_DIR,
    _load_model_config,
    _resolve_credentials,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".scratch/2/validation/42-workflow-evaluation",
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("重复次数必须大于零。")
    snapshot = _load_model_config(args.data_dir)
    key, _, _ = _resolve_credentials(args.data_dir)
    if not key:
        print("未配置真实模型凭据，未执行；不能判为通过。")
        return 2
    settings = get_settings()
    calls: list[dict[str, Any]] = []
    reports = []
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    capability = registry.get("qwen_structured_output", "1").model_copy(
        update={"model_id": snapshot.model_id}
    )

    class RecordingClient(QwenApiClient):
        """记录实际适配器载荷；不改变生产请求和响应。"""

        def chat_completions(self, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            started = time.monotonic()
            row = {
                "request": body,
                "request_sha256": fingerprint(body),
                "started_at": datetime.now(UTC).isoformat(),
            }
            calls.append(row)
            try:
                response = super().chat_completions(body, **kwargs)
                row.update(
                    usage=response.get("usage"),
                    actual_model=response.get("model"),
                    finish_reason=first_choice(response).get("finish_reason"),
                )
                if first_choice(response).get("finish_reason") == "length":
                    raise AdapterError(
                        code="output_budget_exceeded",
                        message="结构化输出超出额度。",
                        retryable=False,
                    )
                return response
            finally:
                row["elapsed_ms"] = round((time.monotonic() - started) * 1000)

    with httpx.Client(proxy=args.proxy, timeout=45) as http:
        client = RecordingClient(
            api_key=SecretStr(key),
            workspace_id=settings.qwen_workspace_id,
            region=settings.qwen_region,
            http_client=http,
        )
        adapter = QwenStructuredOutputAdapter(client)

        def invoke(payload: dict[str, Any]) -> dict[str, Any]:
            # 与生产共用结构化适配器的格式、思考和输出额度策略，无隐式重试。
            result = adapter.call(capability, None, payload)
            calls[-1]["output"] = result.output
            calls[-1]["schema_sha256"] = fingerprint(payload.get("json_schema"))
            return result.output

        for repeat in range(args.repeats):
            try:
                report = run_semantic_cases(invoke, quota=build_run_model_quota(snapshot))
                scope = run_scope_cases(invoke)
                reports.append(
                    {
                        "repeat": repeat,
                        **report,
                        "scope": scope,
                        "passed": report["passed"] and scope["passed"],
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 真实执行失败也保留收据
                reports.append(
                    {
                        "repeat": repeat,
                        "passed": False,
                        "error": type(exc).__name__,
                        "error_code": getattr(exc, "code", None),
                    }
                )
    artifact = {
        "kind": "real-model-production-role-semantics",
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip(),
        "model_config": snapshot.model_dump(mode="json"),
        "source_sha256": {
            name: fingerprint((REPO_ROOT / name).read_text(encoding="utf-8"))
            for name in (
                "src/bridges/evaluation/workflow_semantics.py",
                "src/bridges/evaluation/workflow_semantics_scope.py",
                "src/bridges/paper/assessment.py",
                "src/bridges/study/review.py",
                "src/bridges/study/summary.py",
                "scripts/run_issue42_workflow_semantics.py",
            )
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "repeats": args.repeats,
        "reports": reports,
        "calls": calls,
        "passed": all(report["passed"] for report in reports),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "workflow-semantics.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"真实模型语义通过：{artifact['passed']}，报告：{path}")
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
