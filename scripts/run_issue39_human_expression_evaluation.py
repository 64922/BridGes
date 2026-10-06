"""工单 39 人味表达盲评入口：确定性机制验证 + 有界真实模型配对。

用法（conda `agent` 环境）::

    python scripts/run_issue39_human_expression_evaluation.py            # 仅确定性机制
    python scripts/run_issue39_human_expression_evaluation.py --real-probes
    python scripts/run_issue39_human_expression_evaluation.py --review-report \
        .scratch/2/validation/39-human-expression/real-report.json \
        --submissions submissions.json --output-dir .scratch/2/validation/39-review

``--real-probes`` 显式 opt-in 真实 Qwen 调用；缺少凭据/适配器时记录
``inconclusive`` 并非零退出，不伪装通过。真实调用有界：默认抽取 9 个
原创可运行场景 × 3 个策略臂 × 各自轮次；同一模型、同证据、可比输出额度。
人工盲评提交通过 ``--submissions`` 注入；没有提交时报告明确不放行、
不宣称提升。密钥绝不写入报告或日志。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.evaluation.expression_corpus import (  # noqa: E402
    real_runnable_scenarios,
    scenario_digest,
)
from bridges.evaluation.expression_deterministic import (  # noqa: E402
    run_deterministic_suite,
)
from bridges.evaluation.expression_frozen_review import (  # noqa: E402
    load_frozen_report,
    score_frozen_report,
)
from bridges.evaluation.expression_policy_arms import (  # noqa: E402
    ARM_STRATEGY_VERSIONS,
    StrategyArm,
)
from bridges.evaluation.expression_provenance import (  # noqa: E402
    SUITE_ID,
    SUITE_VERSION,
    build_run_lock,
    code_commit,
)
from bridges.evaluation.expression_real_run import RealArmSender  # noqa: E402
from bridges.evaluation.expression_release import evaluate_release  # noqa: E402
from bridges.evaluation.expression_review import (  # noqa: E402
    CURRENT_ARM_ID,
    DEFAULT_COMPARISONS,
    BlindPairItem,
    aggregate_review,
    build_blind_review,
)
from bridges.evaluation.expression_scale import SCALE_VERSION  # noqa: E402
from bridges.evaluation.expression_submission import (  # noqa: E402
    render_blind_material,
    submission_template,
)
from bridges.evaluation.human_expression import build_real_report  # noqa: E402

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".scratch" / "2" / "validation" / "39-human-expression"
_FORBIDDEN_PATTERN = re.compile(
    r"(?i)(password|api[_-]?key|secret|authorization)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{8,}"
)


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData/Local"


DEFAULT_DATA_DIR = _local_app_data() / "BridGes/data"


def _source_hashes() -> dict[str, str]:
    paths = sorted((REPO_ROOT / "src" / "bridges").rglob("*.py"))
    paths.append(Path(__file__).resolve())
    return {
        path.relative_to(REPO_ROOT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }


def _ensure_redacted(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    match = _FORBIDDEN_PATTERN.search(text)
    if match:
        raise SystemExit("报告疑似包含秘密字段，拒绝写入；请检查输入。")


def _select_real_scenarios(limit: int) -> list[Any]:
    """按类别分散抽取可真实运行的场景（默认每类首个）。"""

    selected: list[Any] = []
    seen_categories: set[str] = set()
    for scenario in real_runnable_scenarios():
        if scenario.category.value in seen_categories:
            continue
        selected.append(scenario)
        seen_categories.add(scenario.category.value)
        if len(selected) >= limit:
            return selected
    for scenario in real_runnable_scenarios():
        if scenario not in selected:
            selected.append(scenario)
            if len(selected) >= limit:
                break
    return selected


def build_real_gateway(data_dir: Path) -> tuple[Any, Any]:
    """从 OS 凭据库读取运行期 Qwen 凭据并装配生产组合；缺凭据报错。"""

    from pydantic import SecretStr

    from bridges.ai.production import build_production_composition
    from bridges.config import get_settings
    from bridges.credentials.runtime_resolver import RuntimeCredentialResolver
    from bridges.credentials.store import build_credential_store

    settings = get_settings()
    store = build_credential_store(settings, data_dir, namespace="runtime")
    resolver = RuntimeCredentialResolver(settings=settings, credential_store=store)
    resolved = resolver.qwen_api_key()
    if not resolved.configured or resolved.value is None:
        raise RuntimeError("missing_global_qwen_key")
    composition = build_production_composition(
        settings.model_copy(
            update={"qwen_api_key": SecretStr(resolved.value.get_secret_value())}
        )
    )
    if not composition.global_key_configured:
        raise RuntimeError("missing_global_qwen_key")
    if composition.gateway.get_adapter("qwen_text_chat", "1") is None:
        raise RuntimeError("missing_text_chat_adapter")
    return composition, resolver


def _environment(model_id: str | None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "code_commit": code_commit(),
        "source_hashes": _source_hashes(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "conda_environment": os.environ.get("CONDA_DEFAULT_ENV"),
        "model_id": model_id,
        "prompt_versions": {
            arm.value: ARM_STRATEGY_VERSIONS[arm] for arm in StrategyArm
        },
        "scale_version": SCALE_VERSION,
        "suite": {"suite_id": SUITE_ID, "suite_version": SUITE_VERSION},
        "corpus_digest": scenario_digest(),
        "dependencies": {
            name: version(name)
            for name in ("pydantic", "httpx", "pytest", "ruff")
        },
    }


def _render_report_markdown(payload: dict[str, Any]) -> str:
    deterministic = payload["deterministic"]
    release = payload["release"]
    cost = payload["cost"]["arms"]
    lines = [
        "# 工单 39 人味表达盲评报告",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 代码提交：{payload['environment'].get('code_commit')}",
        f"- 模型：{payload['environment'].get('model_id')}",
        f"- 语料摘要：{payload['suite']['corpus_digest'][:16]}",
        f"- 确定性机制：{'通过' if deterministic['passed'] else '不通过'}",
        f"- 放行结论：{release['status']}",
        "",
        "## 真实测量（按臂）",
        "",
        "| 策略臂 | 回合 | 调用 | 输入token | 输出token | 首字延迟ms | 总延迟ms | 重试 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm in StrategyArm:
        summary = cost.get(arm.value)
        if summary is None:
            continue
        lines.append(
            f"| {arm.value} | {summary['turns']} | {summary['calls']} | "
            f"{summary['input_tokens']} | {summary['output_tokens']} | "
            f"{summary['first_token_ms_mean']} | {summary['latency_ms_mean']} | "
            f"{summary['retries']} |"
        )
    lines.extend(
        [
            "",
            "- 人味专属新增调用为零："
            f"{'是' if payload['cost']['humanization_specific_calls_zero'] else '否'}",
            "",
            "## 场景分布",
            "",
            "| 类别 | 语料总数 | 可运行数 | 实际配对数 | 多轮语料数 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for entry in payload.get("scenario_distribution", []):
        lines.append(
            f"| {entry['category']} | {entry['total']} | "
            f"{entry['real_runnable']} | {entry.get('executed', 0)} | {entry['multi_turn']} |"
        )
    baseline = (payload.get("deployment_reference") or {}).get("measured")
    if baseline:
        lines.extend(
            [
                "",
                "- 部署参照（简洁基线实测）："
                f"调用 {baseline['calls']}、输入 {baseline['input_tokens']} token、"
                f"输出 {baseline['output_tokens']} token、"
                f"首字延迟均值 {baseline['first_token_ms_mean']}ms；"
                "部署门槛由发布票按本测量与预注册策略设定。",
            ]
        )
    lines.extend(
        [
            "",
            "## 硬门（独立于温暖感得分）",
            "",
        ]
    )
    for gate, totals in sorted(payload["hard_gates"]["totals"].items()):
        lines.append(f"- {gate}: 通过 {totals['passed']} / 失败 {totals['failed']}")
    lines.extend(["", "## 盲评放行结论", ""])
    lines.append(f"- 状态：{release['status']}")
    for reason in release["reasons"]:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 真实配对覆盖可运行的日常聊天场景；学习/模块/固定文案路径"
            "由确定性覆盖矩阵与消费者验收记录核对。",
            "- 无人工盲评提交时状态为 inconclusive：不放行，不宣称自然度提升。",
            "- 确定性模型/工具响应只证明机制，真实模型体验以本报告实测为准。",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工单 39 人味表达盲评")
    parser.add_argument("--real-probes", action="store_true", help="显式开启真实模型配对")
    parser.add_argument("--review-report", type=Path, help="对冻结报告离线评分，不调用模型")
    parser.add_argument("--scenarios", type=int, default=9, help="真实运行场景数上限")
    parser.add_argument(
        "--submissions", type=Path, default=None, help="人工盲评提交 JSON"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=39)
    args = parser.parse_args(argv)
    if args.submissions is not None and args.review_report is None:
        parser.error("--submissions 必须配合 --review-report，不能把旧评分用于新回答。")
    if args.review_report is not None and args.real_probes:
        parser.error("冻结评分不能与 --real-probes 同时使用。")

    started = time.monotonic()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.review_report is not None:
        frozen = load_frozen_report(args.review_report)
        if args.submissions is None:
            items = [BlindPairItem(**item) for item in frozen["blind_review"]["items"]]
            payload = submission_template(items)
            material = render_blind_material(items)
            _ensure_redacted(material)
            (output_dir / "blind-review.md").write_text(material, encoding="utf-8")
            name = "blind-review-submissions.template.json"
        else:
            submission = json.loads(args.submissions.read_text(encoding="utf-8"))
            payload = score_frozen_report(frozen, submission)
            name = "review-result.json"
        _ensure_redacted(payload)
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[39] 冻结材料处理完成：{output_dir / name}")
        return 0 if "release" not in payload or payload["release"]["released"] else 5

    deterministic = run_deterministic_suite()
    (output_dir / "deterministic-report.json").write_text(
        json.dumps(deterministic.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[39] 确定性机制：{'通过' if deterministic.passed else '不通过'} "
        f"({deterministic.counts()[0]}/{deterministic.counts()[1]} 检查点)"
    )
    if not deterministic.passed:
        for checkpoint in deterministic.checkpoints:
            if not checkpoint.passed:
                print(f"  - 失败：{checkpoint.checkpoint_id}：{checkpoint.detail}")
        return 2

    if not args.real_probes:
        print("[39] 未开启 --real-probes：只输出确定性机制报告。")
        return 0

    scenarios = _select_real_scenarios(max(1, min(args.scenarios, 12)))
    planned_calls = sum(len(s.turns) for s in scenarios) * len(StrategyArm)
    print(
        f"[39] 真实配对：{len(scenarios)} 场景 × {len(StrategyArm)} 臂，"
        f"预计最多 {planned_calls} 次聊天调用。"
    )
    results = []
    try:
        composition, _resolver = build_real_gateway(args.data_dir)
    except RuntimeError as exc:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "inconclusive",
            "error_code": str(exc),
            "detail": "真实配对未执行：缺少可用凭据或适配器。",
            "environment": _environment(None),
        }
        _ensure_redacted(payload)
        (output_dir / "real-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 3

    model_id = composition.registry.get("qwen_text_chat", "1").model_id
    sender = RealArmSender(composition.gateway)
    for scenario in scenarios:
        for arm in StrategyArm:
            results.append(sender.run(scenario, arm))

    failed_runs = sum(
        1 for result in results if any(m.status != "done" for m in result.measurements)
    )
    transcripts = {
        (result.scenario.scenario_id, result.arm.value): result.transcript
        for result in results
    }
    review_items, mapping = build_blind_review(
        review_set_id=f"issue39-{int(time.time())}",
        transcripts=transcripts,
        comparisons=DEFAULT_COMPARISONS,
        order_seed=args.seed,
    )
    (output_dir / "blind-review.md").write_text(
        render_blind_material(review_items), encoding="utf-8"
    )
    (output_dir / "blind-review-submissions.template.json").write_text(
        json.dumps(submission_template(review_items), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    aggregate = aggregate_review(review_items, mapping, [])
    hard_failures = tuple(
        f"{result.arm.value}/{result.scenario.scenario_id}#{gate.turn_index}:"
        f"{gate.detail}"
        for result in results
        if result.arm.value == CURRENT_ARM_ID
        for gate in result.gates
        if not gate.passed
    )
    release = evaluate_release(aggregate, hard_gate_failures=hard_failures)

    observed_locks = [lock for result in results for lock in result.locks]
    prompt_versions = {
        lock.capability_name: lock.prompt_version
        for lock in observed_locks
        if lock.prompt_version
    }
    prompt_versions.update(
        {arm.value: ARM_STRATEGY_VERSIONS[arm] for arm in StrategyArm}
    )
    run_lock = build_run_lock(
        lock_id=f"issue39-real-{int(time.time())}",
        dataset_versions={"expression-scenarios-real-subset": scenario_digest()[:16]},
        prompt_versions=prompt_versions,
        model_run_locks=observed_locks,
        random_seeds=[args.seed],
        execution_count=1,
        suite_digest_value=scenario_digest(),
        network_cache_policy="live-real-model",
    )
    environment = _environment(model_id)
    payload = build_real_report(
        deterministic=deterministic,
        results=results,
        environment=environment,
        review_items=review_items,
        review_mapping=mapping,
        aggregate=aggregate,
        release=release,
    )
    payload["run_lock"] = json.loads(run_lock.model_dump_json())
    payload["run_lock_digest"] = run_lock.digest()
    payload["observed_locks_digest"] = run_lock.observed_digest()
    payload["elapsed_seconds"] = round(time.monotonic() - started, 1)
    _ensure_redacted(payload)
    (output_dir / "real-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "run-lock.json").write_text(
        json.dumps(json.loads(run_lock.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        _render_report_markdown(payload), encoding="utf-8"
    )

    print(
        f"[39] 真实配对完成：{len(results)} 个臂运行，失败回合 {failed_runs}；"
        f"放行结论 {release['status']}；耗时 {payload['elapsed_seconds']}s。"
    )
    if hard_failures:
        print(f"  - 候选策略硬门失败 {len(hard_failures)} 项，明确不放行。")
        return 5
    if failed_runs == 0:
        return 0
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
