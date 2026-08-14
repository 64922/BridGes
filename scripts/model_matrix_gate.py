"""Issue 09 发布门：生产组合门禁 + 模型字面量扫描 + vision/OCR 真实兼容 smoke。

执行内容（全部脱敏）：

1. 确定性回归：registry/ai/ocr/image/video/speech 测试 + 静态架构测试
   （生产路径模型字面量扫描，report 文件与 capability）；
2. production-like 组合校验：批准矩阵一致性、真实 adapter 绑定、
   Stub/cassette/deterministic 拒绝、缺全局 Key 失败关闭；
3. ``--real-probes`` 显式 opt-in 时才访问真实 Qwen：对 ``qwen_vision`` /
   ``qwen_ocr`` 用最小含已知文字图像验证描述合同与 OCR 文本合同，
   保存脱敏结果与运行锁；不具备真实 Key/网络时结果为 ``inconclusive``，
   不能通过发布门（``vision_ocr_compatibility_unproven``）。

用法：

```powershell
python scripts/model_matrix_gate.py                 # 离线门（缺 Key/未证实 → 失败关闭）
python scripts/model_matrix_gate.py --real-probes    # 真实兼容验收（需全局 Qwen Key）
```
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai.composition import (  # noqa: E402, I001
    CompositionViolation,
    validate_production_composition,
)
from bridges.ai.matrix_probe import (  # noqa: E402
    VisionOcrProbeResult,
    run_vision_ocr_probes,
    write_probe_report,
)
from bridges.ai.production import build_production_composition  # noqa: E402
from bridges.config import get_settings  # noqa: E402

#: 发布门覆盖的确定性测试文件（Issue 09 Test plan）。
GATE_TEST_FILES = [
    "tests/credentials/test_registry.py",
    "tests/ai",
    "tests/ingestion/test_ocr.py",
    "tests/image",
    "tests/video",
    "tests/speech",
]

DEFAULT_REPORT_DIR = REPO_ROOT / ".tmp" / "model-matrix-gate"


@dataclass(frozen=True)
class CheckEvidence:
    """确定性检查证据：只含名称/状态/耗时，不含命令输出正文。"""

    name: str
    status: str
    duration_ms: int = 0
    error_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error_category": self.error_category,
        }


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _code_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _run_pytest(environment: dict[str, str]) -> CheckEvidence:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=".model-matrix-pytest-", dir=REPO_ROOT) as basetemp:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--basetemp",
            basetemp,
            *[str(REPO_ROOT / item) for item in GATE_TEST_FILES],
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CheckEvidence(
                "pytest-regression", "failed", _duration_ms(started), "command_timeout"
            )
    category = None
    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".casefold()
        category = "test_failed" if "failed" in combined else "check_failed"
    return CheckEvidence(
        "pytest-regression",
        "passed" if result.returncode == 0 else "failed",
        _duration_ms(started),
        category,
    )


def _violations_as_dict(violations: Sequence[CompositionViolation]) -> list[dict[str, str]]:
    return [violation.as_dict() for violation in violations]


def run_gate(
    *,
    real_probes: bool = False,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> dict[str, Any]:
    """运行 Issue 09 发布门；只有 ``real_probes=True`` 才访问真实 Qwen。"""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["BRIDGES_ENVIRONMENT"] = "test"  # pytest 确定性（registry 测试自设环境）
    checks = [_run_pytest(environment)]

    # production-like 组合：强制生产语义（禁 cassette 录制、真实适配器）。
    os.environ["BRIDGES_ENVIRONMENT"] = "production"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    get_settings.cache_clear()
    settings = get_settings()
    composition = build_production_composition(settings)

    probes: list[VisionOcrProbeResult] = []
    if real_probes:
        probes = run_vision_ocr_probes(
            composition.registry,
            composition.gateway,
            global_key_configured=composition.global_key_configured,
        )
        write_probe_report(
            probes,
            report_dir,
            code_version=_code_version(),
        )
    vision_ocr_proven = bool(probes) and all(probe.status == "passed" for probe in probes)

    violations = validate_production_composition(
        composition.registry,
        composition.gateway,
        global_key_configured=composition.global_key_configured,
        cassette_enabled=composition.cassette_enabled,
        vision_ocr_compatibility_proven=vision_ocr_proven,
    )

    risks: list[str] = []
    if not real_probes:
        risks.append("real_probes_not_run")
    if violations:
        risks.append("production_composition_violations")
    if real_probes and not vision_ocr_proven:
        risks.append("vision_ocr_compatibility_unproven")
    if any(check.status != "passed" for check in checks):
        risks.append("regression_tests_failed")

    deterministic_ok = all(check.status == "passed" for check in checks)
    probes_ok = real_probes and vision_ocr_proven
    release_ready = deterministic_ok and not violations and probes_ok

    report: dict[str, Any] = {
        "kind": "model_matrix_gate",
        "status": "passed" if release_ready else "blocked",
        "release_ready": release_ready,
        "generated_at": datetime.now(UTC).isoformat(),
        "code_version": _code_version(),
        "environment": "production-like",
        "deterministic_checks": [check.as_dict() for check in checks],
        "composition_violations": _violations_as_dict(violations),
        "vision_ocr_probes": [probe.as_dict() for probe in probes],
        "risks": risks,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 Issue 09 模型矩阵发布门。")
    parser.add_argument(
        "--real-probes",
        action="store_true",
        help="显式访问真实 Qwen 执行 vision/OCR 兼容 smoke（需全局 Key）。",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="脱敏 JSON 报告目录（含 probes.json 与运行锁）。",
    )
    args = parser.parse_args(argv)
    report = run_gate(real_probes=args.real_probes, report_dir=args.report_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "blocked":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
