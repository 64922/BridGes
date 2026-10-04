"""工单 41 画像质量评测入口：确定性治理 + 真实抽取/配对报告。

用法（conda agent 环境）::

    python scripts/run_issue41_profile_evaluation.py            # 仅确定性治理
    python scripts/run_issue41_profile_evaluation.py --real-probes

``--real-probes`` 显式 opt-in 真实 Qwen 调用；缺少凭据或适配器时整体非零
退出、报告如实记录 ``inconclusive``，不伪装通过。真实调用有界：抽取 6 条
探针 + 2 任务 × 4 条件配对；密钥绝不写入报告或日志。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.config import get_settings  # noqa: E402
from bridges.credentials.runtime_resolver import RuntimeCredentialResolver  # noqa: E402
from bridges.credentials.store import build_credential_store  # noqa: E402
from bridges.evaluation.profile_pairing import (  # noqa: E402
    make_chat_sender,
    run_pairing,
    write_reports,
)
from bridges.evaluation.profile_quality import (  # noqa: E402
    run_profile_quality_evaluation,
)


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData/Local"


DEFAULT_DATA_DIR = _local_app_data() / "BridGes/data"


def _code_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


# ---------------------------------------------------------------------------
# 真实抽取探针：固定源消息 + 本地判定
# ---------------------------------------------------------------------------


def _item_text(item: Any) -> str:
    return f"{item.fact_text or ''} {item.normalized_value}"


def _check_negated(content: str, items: list[Any]) -> tuple[bool, str]:
    texts = [_item_text(item) for item in items]
    inverted = [text for text in texts if "喜欢" in text and "不" not in text]
    retained = bool(items)
    return retained and not inverted, f"retained={retained} extracted={texts}"


def _check_multi_fact(content: str, items: list[Any]) -> tuple[bool, str]:
    texts = [_item_text(item) for item in items]
    return len(items) >= 2, f"count={len(items)} extracted={texts}"


def _check_third_party(content: str, items: list[Any]) -> tuple[bool, str]:
    texts = [_item_text(item) for item in items]
    violations = [
        text for text in texts if "摄影" in text and "朋友" not in text
    ]
    return not violations, f"extracted={texts}"


def _check_quoted(content: str, items: list[Any]) -> tuple[bool, str]:
    texts = [_item_text(item) for item in items]
    violations = [text for text in texts if "跑步" in text]
    return not violations, f"extracted={texts}"


def _check_self_report(content: str, items: list[Any]) -> tuple[bool, str]:
    texts = [_item_text(item) for item in items]
    return any("概率" in text for text in texts), f"extracted={texts}"


def _check_ambiguous(content: str, items: list[Any]) -> tuple[bool, str]:
    detail = [
        f"{_item_text(item)}|action={item.action.value}|reliability={item.reliability}"
        for item in items
    ]
    violations = [entry for entry in detail if "|action=create|" in entry]
    return not violations, f"extracted={detail}"


@dataclass(frozen=True)
class ExtractionProbe:
    probe_id: str
    content: str
    check: Callable[[str, list[Any]], tuple[bool, str]]


EXTRACTION_PROBES: tuple[ExtractionProbe, ...] = (
    ExtractionProbe("negated_preference", "我不喜欢长篇回答", _check_negated),
    ExtractionProbe("multi_fact", "我喜欢跑步，也喜欢游泳", _check_multi_fact),
    ExtractionProbe("third_party", "我朋友很喜欢摄影", _check_third_party),
    ExtractionProbe("quoted", '最近有人跟我说"你应该每天跑步"', _check_quoted),
    ExtractionProbe("self_report", "我正在学习概率统计", _check_self_report),
    ExtractionProbe("ambiguous_low_confidence", "我可能喜欢摄影", _check_ambiguous),
)


def run_real_extraction_probe(gateway: Any) -> list[dict[str, Any]]:
    """对固定源消息执行真实抽取，按局部判定逐条给出结果。"""

    from bridges.profiles.automatic import GatewayAutomaticProfileExtractor
    from bridges.profiles.signals import ProfileSignalClassifier

    extractor = GatewayAutomaticProfileExtractor(gateway=gateway)
    classifier = ProfileSignalClassifier()
    results: list[dict[str, Any]] = []
    for probe in EXTRACTION_PROBES:
        classification = classifier.extraction_classification(probe.content)
        locks: list[Any] = []
        started = time.monotonic()
        try:
            output = extractor.extract(
                account_id="eval41-extraction",
                conversation_id="eval41-extraction",
                message_id=f"msg-{probe.probe_id}",
                content=probe.content,
                run_id=f"eval41-extract-{probe.probe_id}",
                signal_classification=classification,
                lock_sink=locks.append,
            )
            passed, detail = probe.check(probe.content, list(output.items))
            error_code = None
        except Exception as exc:  # noqa: BLE001 - 探针失败按不通过记录
            passed, detail = False, "探针执行异常"
            error_code = getattr(exc, "code", None) or exc.__class__.__name__.lower()
        usage = (locks[0].usage or {}) if locks else {}
        results.append(
            {
                "probe_id": probe.probe_id,
                "content": probe.content,
                "classification": classification.category.value,
                "passed": passed,
                "detail": detail,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model_calls": len(locks),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "error_code": error_code,
            }
        )
    return results


# ---------------------------------------------------------------------------
# 真实网关装配
# ---------------------------------------------------------------------------


def build_real_gateway(data_dir: Path) -> tuple[Any, Any]:
    """从 OS 凭据库读取运行期 Qwen 凭据并装配生产组合；缺凭据报错。"""

    from pydantic import SecretStr

    from bridges.ai.production import build_production_composition

    settings = get_settings()
    store = build_credential_store(settings, data_dir, namespace="runtime")
    resolver = RuntimeCredentialResolver(settings=settings, credential_store=store)
    resolved = resolver.qwen_api_key()
    if not resolved.configured or resolved.value is None:
        raise RuntimeError("missing_global_qwen_key")
    composition = build_production_composition(
        settings.model_copy(
            update={
                "qwen_api_key": SecretStr(resolved.value.get_secret_value())
            }
        )
    )
    if not composition.global_key_configured:
        raise RuntimeError("missing_global_qwen_key")
    if composition.gateway.get_adapter("qwen_text_chat", "1") is None:
        raise RuntimeError("missing_text_chat_adapter")
    if composition.gateway.get_adapter("qwen_profile_extraction", "1") is None:
        raise RuntimeError("missing_profile_extraction_adapter")
    return composition, resolver


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _render_report(
    *,
    deterministic: dict[str, Any],
    pairing: dict[str, Any] | None,
    extraction: list[dict[str, Any]] | None,
    environment: dict[str, Any],
) -> str:
    lines = [
        "# 工单 41 画像质量评测报告",
        "",
        f"- 生成时间：{environment['generated_at']}",
        f"- 代码提交：{environment['code_commit']}",
        f"- 抽取提示词版本：{environment['prompt_version']}",
        f"- 模型：{environment['model_id']}",
        f"- 账户模型：{environment['account_model']}",
        (
            f"- 真实探针：{'已执行' if extraction is not None else '未执行'}"
            "（--real-probes 显式开启；缺凭据时记录 inconclusive）"
        ),
        "",
        "## 样本与不确定性",
        "",
        (
            f"- 确定性纵向场景：{deterministic['scenario_count']} 个"
            f"（检查点 {deterministic['checkpoint_count']}，通过率 "
            f"{deterministic['pass_rate']:.0%}）"
        ),
    ]
    if pairing is not None:
        lines.append(
            f"- 真实配对：{pairing['run_count']} 次调用"
            f"（{pairing['task_count']} 任务 × 4 条件）"
        )
    if extraction is not None:
        lines.append(f"- 真实抽取探针：{len(extraction)} 条源消息")
    lines.extend(
        [
            "- 样本量不足以宣称准确率；本报告只给方向性判断与阈值建议。",
            "",
            "## 硬门与结果",
            "",
            f"- 确定性治理总体：{'通过' if deterministic['passed'] else '不通过'}",
        ]
    )
    if pairing is not None:
        lines.append(
            f"- 真实配对总体：{'通过' if pairing['passed'] else '不通过'}；"
            f"失败：{pairing['failed_runs']}"
        )
    if extraction is not None:
        failed = [probe["probe_id"] for probe in extraction if not probe["passed"]]
        lines.append(
            f"- 真实抽取探针总体：{'通过' if not failed else '不通过'}；"
            f"失败：{failed}"
        )
    lines.extend(["", "## 真实抽取探针明细", ""])
    if extraction is None:
        lines.append("未执行。")
    else:
        lines.append("| 探针 | 分类 | 结果 | 延迟(ms) | 模型调用 | 说明 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for probe in extraction:
            lines.append(
                f"| {probe['probe_id']} | {probe['classification']} | "
                f"{'通过' if probe['passed'] else '不通过'} | "
                f"{probe['latency_ms']} | {probe['model_calls']} | "
                f"{probe['detail']} |"
            )
    lines.extend(["", "## 真实配对明细", ""])
    if pairing is None:
        lines.append("未执行。")
    else:
        lines.append("| 条件 | 次数 | 通过 | 平均延迟(ms) | 输入 token | 输出 token |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for condition, summary in pairing["conditions"].items():
            lines.append(
                f"| {condition} | {summary['runs']} | {summary['passed']} | "
                f"{summary['latency_ms_avg']} | {summary['input_tokens']} | "
                f"{summary['output_tokens']} |"
            )
    lines.extend(
        [
            "",
            "## 成本与事务边界",
            "",
        ]
    )
    if pairing is not None and extraction is not None:
        total_in = sum(
            summary["input_tokens"] for summary in pairing["conditions"].values()
        ) + sum(probe.get("input_tokens") or 0 for probe in extraction)
        total_out = sum(
            summary["output_tokens"] for summary in pairing["conditions"].values()
        ) + sum(probe.get("output_tokens") or 0 for probe in extraction)
        lines.append(f"- 真实调用总输入 token：{total_in}；总输出 token：{total_out}")
    lines.extend(
        [
            "- 治理动作（记住/修改/删除/忘掉）各以单仓库事务提交，读取与采用切片为只读快照；",
            "  本轮未单独插桩事务占用时长，作为已知局限。",
            "",
            "## 阈值依据与索引/模型选择建议",
            "",
            "- 套话控制与同维度完整事实阈值沿用既有自动断言（注入后可检出），本轮不放宽。",
            "- 抽取路由保持确定性预检 + 固定模型 qwen3.7-plus-2026-05-26；",
            "  探针通过时不建议引入语义索引或更换模型；若后续规模扩大、召回下降，",
            "  再以本报告同一量表复测后决策。",
            "",
            "## 限制",
            "",
            "- 真实配对每条件样本少（2 任务），只证明机制可用与方向性改善，不宣称准确率。",
            "- 配对回答检查为确定性内容规则，不替代人工盲评；盲评材料见 blind-review.md。",
            "- 环境代理不可达 GitHub，仓库同步受限；DashScope 直连可用。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工单 41 画像质量评测")
    parser.add_argument(
        "--real-probes",
        action="store_true",
        help="显式允许真实 Qwen 调用（抽取探针 + 四条件配对）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="运行期凭据数据目录（runtime 命名空间）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".scratch/2/validation/41-profile-quality",
        help="报告输出目录",
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    from bridges.profiles.automatic import PROFILE_EXTRACTION_PROMPT_VERSION

    environment = {
        "generated_at": datetime.now(UTC).isoformat(),
        "code_commit": _code_commit(),
        "prompt_version": PROFILE_EXTRACTION_PROMPT_VERSION,
        "model_id": "qwen3.7-plus-2026-05-26",
        "account_model": "synthetic eval account per condition",
    }
    deterministic = run_profile_quality_evaluation().to_dict()
    deterministic["environment"] = {
        **deterministic.get("environment", {}),
        "code_commit": environment["code_commit"],
        "prompt_version": environment["prompt_version"],
    }
    (args.output_dir / "deterministic-report.json").write_text(
        json.dumps(deterministic, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pairing_payload: dict[str, Any] | None = None
    extraction: list[dict[str, Any]] | None = None
    inconclusive = False
    if args.real_probes:
        try:
            composition, _ = build_real_gateway(args.data_dir)
        except Exception as exc:  # noqa: BLE001 - 缺凭据按 inconclusive 收敛
            print(
                json.dumps(
                    {
                        "status": "inconclusive",
                        "error_code": getattr(exc, "code", None)
                        or str(exc)
                        or exc.__class__.__name__.lower(),
                        "detail": "真实探针未执行：缺少可用凭据或适配器。",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            inconclusive = True
        else:
            extraction = run_real_extraction_probe(composition.gateway)
            report = run_pairing(
                make_chat_sender(composition.gateway), environment=environment
            )
            write_reports(report, args.output_dir)
            pairing_payload = report.to_dict()

    report_markdown = _render_report(
        deterministic=deterministic,
        pairing=pairing_payload,
        extraction=extraction,
        environment=environment,
    )
    (args.output_dir / "report.md").write_text(report_markdown, encoding="utf-8")
    summary = {
        "deterministic_passed": deterministic["passed"],
        "pairing_passed": pairing_payload["passed"] if pairing_payload else None,
        "extraction_passed": (
            all(probe["passed"] for probe in extraction)
            if extraction is not None
            else None
        ),
        "real_probes_requested": args.real_probes,
        "inconclusive": inconclusive,
        "output_dir": str(args.output_dir),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not deterministic["passed"]:
        return 2
    if inconclusive:
        return 3
    if pairing_payload is not None and not pairing_payload["passed"]:
        return 4
    if extraction is not None and not all(probe["passed"] for probe in extraction):
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
