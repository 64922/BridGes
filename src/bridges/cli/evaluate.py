"""BridGes evaluate 子命令（Issue 40）。

可复现 A/B 科学评测的命令行入口：

- ``evaluate run``：执行套件运行矩阵（可双跑对比），持久化运行锁、结果与报告；
- ``evaluate replay``：一键重放失败案例（固定输入 + 运行锁 + 相同种子）；
- ``evaluate blind-review create/submit``：构建盲评集、记录评审提交并给出
  一致性摘要（隐藏系统身份、随机化顺序）；
- ``evaluate report``：输出版本化报告；
- ``evaluate gates``：按发布阈值判定报告（供 Issue 41 发布门消费）。

评测库默认写入配置的数据目录（bridges.db），可用 ``--db`` 显式指定。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from bridges.contracts.evaluation_suite import now_iso

evaluate_app = typer.Typer(
    name="evaluate",
    help="可复现 A/B 科学评测套件",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _resolve_database_path(explicit: str | None) -> Path:
    """解析评测数据库路径：显式 --db 优先，否则取配置的数据目录。"""
    if explicit:
        path = Path(explicit)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    from bridges.config import get_settings
    from bridges.persistence import PersistenceError, resolve_database_path

    settings = get_settings()
    database_url = settings.database_url
    if database_url is None or not database_url.get_secret_value():
        typer.echo(
            "error: 未配置 BRIDGES_DATABASE_URL，请用 --db 指定评测数据库路径。",
            err=True,
        )
        raise typer.Exit(1)
    try:
        return Path(resolve_database_path(database_url))
    except PersistenceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _open_repository(path: Path) -> Any:
    from bridges.evaluation.repository import EvaluationRepository
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(path)
    database.initialize()
    return EvaluationRepository(database)


def _load_suite(repository: Any, suite_id: str, version: str | None) -> Any:
    from bridges.evaluation import suite_data
    from bridges.evaluation.suite_registry import SuiteRegistry

    if suite_id != suite_data.SUITE_ID:
        typer.echo(f"error: 未知评测套件：{suite_id}（当前仅内置 science-baseline）。", err=True)
        raise typer.Exit(1)
    registry = SuiteRegistry()
    registry.register(suite_data.build_science_baseline_suite())
    suite_definition = registry.get(suite_id, version)
    repository.save_suite(suite_definition)
    return suite_definition


def _print_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


@evaluate_app.command("run")
def run_suite(
    suite: Annotated[str, typer.Option("--suite", help="套件标识")] = "science-baseline",
    version: Annotated[str | None, typer.Option("--version", help="套件版本")] = None,
    db: Annotated[str | None, typer.Option("--db", help="评测数据库路径")] = None,
    seeds: Annotated[str, typer.Option("--seeds", help="随机种子（逗号分隔）")] = "42",
    executions: Annotated[int, typer.Option("--executions", help="每个种子执行次数")] = 1,
    double_run: Annotated[bool, typer.Option("--double-run", help="连续两次运行并对比")] = False,
    gates: Annotated[bool, typer.Option("--gates", help="执行发布阈值判定")] = True,
) -> None:
    """执行套件运行矩阵并持久化运行锁、结果与报告。"""
    from bridges.evaluation.runner import EvaluationRunner
    from bridges.evaluation.sut import build_sut_registry

    path = _resolve_database_path(db)
    repository = _open_repository(path)
    suite_definition = _load_suite(repository, suite, version)
    seed_values = [int(seed.strip()) for seed in seeds.split(",") if seed.strip()]

    runner = EvaluationRunner(
        suite=suite_definition,
        sut_registry=build_sut_registry(),
        seeds=seed_values,
        execution_count=executions,
    )
    first = runner.run(db_path=path)
    second = runner.run(db_path=path) if double_run else None

    comparison = None
    if double_run and second is not None:
        from bridges.evaluation.runner import compare_double_run

        metric_ids = {
            metric.metric_id
            for scale in suite_definition.scales
            for metric in scale.items
        }
        comparison = compare_double_run(
            first,
            second,
            tolerances=dict.fromkeys(metric_ids, 1.0),
        )

    verdict = None
    if gates:
        from bridges.evaluation.gates import build_default_threshold, evaluate_release_gate

        threshold = build_default_threshold()
        verdict = evaluate_release_gate(first.report, threshold)
        if verdict.passed:
            typer.echo(f"发布阈值：通过（{len(verdict.checks)} 项检查）")
        else:
            typer.echo(f"发布阈值：未通过（{len(verdict.blockers)} 项阻断）", err=True)
            for blocker in verdict.blockers:
                typer.echo(f"  - {blocker}", err=True)

    # 阈值判定与双跑对比写回报告后再落库（报告携带完整判定）。
    first.report = first.report.model_copy(
        update={
            "threshold_verdict": verdict,
            "cost_latency": {
                **first.report.cost_latency,
                "double_run": comparison,
            },
        }
    )
    report_version = str(len(repository.list_reports(first.lock.lock_id)) + 1)
    first.report = first.report.model_copy(update={"report_version": report_version})
    repository.save_lock(first.lock)
    for result in first.results:
        repository.save_result(result)
    repository.save_report(first.report)

    if second is not None:
        repository.save_lock(second.lock)
        for result in second.results:
            repository.save_result(result)
        second_report_version = str(len(repository.list_reports(second.lock.lock_id)) + 1)
        repository.save_report(
            second.report.model_copy(update={"report_version": second_report_version})
        )

    typer.echo(f"运行锁：{first.lock.lock_id}")
    typer.echo(f"案例结果：{len(first.results)} 条")
    typer.echo(f"报告：{first.report.report_id}@v{first.report.report_version}")
    if comparison is not None:
        typer.echo("双跑对比：")
        typer.echo(f"  锁摘要一致：{comparison['locks_match']}")
        typer.echo(f"  样本集合一致：{comparison['samples_match']}")
        typer.echo(f"  指标差异在容差内：{comparison['within_tolerance']}")
    _print_json(
        {
            "lock_id": first.lock.lock_id,
            "result_count": len(first.results),
            "report_id": first.report.report_id,
            "report_version": first.report.report_version,
            "double_run": comparison,
            "threshold_verdict": (
                verdict.model_dump(mode="json") if verdict is not None else None
            ),
        }
    )


@evaluate_app.command("replay")
def replay_case(
    case: Annotated[str, typer.Option("--case", help="案例标识")],
    suite: Annotated[str, typer.Option("--suite", help="套件标识")] = "science-baseline",
    version: Annotated[str | None, typer.Option("--version", help="套件版本")] = None,
    sut: Annotated[str, typer.Option("--sut", help="被测系统标识")] = "bridges_full",
    seed: Annotated[int, typer.Option("--seed", help="随机种子")] = 42,
    execution: Annotated[int, typer.Option("--execution", help="执行序号")] = 0,
    db: Annotated[str | None, typer.Option("--db", help="评测数据库路径")] = None,
) -> None:
    """一键重放失败案例（固定套件/案例/种子/序号，输出与断言）。"""
    from bridges.evaluation import suite_data
    from bridges.evaluation.case_executors import execute_case
    from bridges.evaluation.executors import EvalEnvironment
    from bridges.evaluation.metrics import compute_dimension_metrics, run_auto_assertions
    from bridges.evaluation.sut import build_sut_registry

    if suite != suite_data.SUITE_ID:
        typer.echo(f"error: 未知评测套件：{suite}", err=True)
        raise typer.Exit(1)
    path = _resolve_database_path(db)
    case_definition = next(
        (c for c in suite_data.CASES if c.case_id == case), None
    )
    if case_definition is None:
        typer.echo(f"error: 案例不存在：{case}", err=True)
        raise typer.Exit(1)
    sut_spec = build_sut_registry().get(sut)
    if sut_spec is None:
        typer.echo(f"error: 被测系统未注册：{sut}", err=True)
        raise typer.Exit(1)

    env = EvalEnvironment(db_path=path)
    try:
        env.cases = {c.case_id: c for c in suite_data.CASES}
        outcome = execute_case(sut_spec, case_definition, seed, execution, env)
        metrics = compute_dimension_metrics(case_definition, outcome.outputs)
        assertions = run_auto_assertions(case_definition, outcome.outputs)
    finally:
        env.close()

    _print_json(
        {
            "suite_id": suite,
            "suite_version": version or suite_data.SUITE_VERSION,
            "case_id": case,
            "sut_id": sut,
            "seed": seed,
            "execution_index": execution,
            "outputs": outcome.outputs,
            "trajectory": outcome.trajectory,
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
            "assertions": [a.model_dump(mode="json") for a in assertions],
            "latency_ms": outcome.latency_ms,
            "reproduced_at": now_iso(),
        }
    )


@evaluate_app.command("blind-review")
def blind_review(
    action: Annotated[str, typer.Argument(help="create 或 submit")],
    lock: Annotated[str | None, typer.Option("--lock", help="运行锁标识（create 用）")] = None,
    review_set: Annotated[str | None, typer.Option("--set", help="盲评集标识（submit 用）")] = None,
    reviewer: Annotated[str | None, typer.Option("--reviewer", help="盲评者标识")] = None,
    answers: Annotated[str | None, typer.Option("--answers", help="评审答案 JSONL 文件")] = None,
    db: Annotated[str | None, typer.Option("--db", help="评测数据库路径")] = None,
    order_seed: Annotated[int, typer.Option("--order-seed", help="匿名化顺序种子")] = 2026,
) -> None:
    """构建盲评集（create）或记录评审提交（submit）。"""
    from bridges.evaluation.blind_review import (
        build_review_set,
        consensus_summary,
        record_submission,
    )

    path = _resolve_database_path(db)
    repository = _open_repository(path)

    if action == "create":
        if lock is None:
            typer.echo("error: create 需要 --lock。", err=True)
            raise typer.Exit(1)
        results = repository.list_results(lock)
        if not results:
            typer.echo(f"error: 运行锁 {lock} 没有结果。", err=True)
            raise typer.Exit(1)
        results_by_sut: dict[str, list[Any]] = {}
        for result in results:
            results_by_sut.setdefault(result.sut_id, []).append(result)
        review_set_id = f"review-{lock}-{order_seed}"
        created_set = build_review_set(
            review_set_id=review_set_id,
            lock_id=lock,
            results_by_sut=results_by_sut,
            order_seed=order_seed,
        )
        repository.save_review_set(created_set)
        typer.echo(f"盲评集：{review_set_id}（{len(created_set.items)} 项）")
        for item in created_set.items:
            _print_json(
                {
                    "item_id": item.item_id,
                    "label_a": item.label_a,
                    "label_b": item.label_b,
                    "output_a": item.output_a,
                    "output_b": item.output_b,
                    "dimension": item.dimension.value,
                }
            )
        return

    if action == "submit":
        if review_set is None or reviewer is None or answers is None:
            typer.echo("error: submit 需要 --set、--reviewer 与 --answers。", err=True)
            raise typer.Exit(1)
        review_set_record = repository.get_review_set(review_set)
        if review_set_record is None:
            typer.echo(f"error: 盲评集不存在：{review_set}", err=True)
            raise typer.Exit(1)
        answers_path = Path(answers)
        if not answers_path.exists():
            typer.echo(f"error: 评审答案文件不存在：{answers_path}", err=True)
            raise typer.Exit(1)
        submitted = 0
        with answers_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                record_submission(
                    review_set_record,
                    reviewer_id=reviewer,
                    item_id=str(payload["item_id"]),
                    chosen=str(payload["chosen"]),
                    rationale=payload.get("rationale"),
                )
                submitted += 1
        repository.save_review_submission(
            review_set, review_set_record.model_dump(mode="json")
        )
        summary = consensus_summary(review_set_record)
        typer.echo(f"已记录 {submitted} 条提交。")
        _print_json(summary)
        if summary["low_consistency_items"]:
            typer.echo(
                "低一致性项目已标记复核（不强行合并）："
                + "、".join(summary["low_consistency_items"]),
                err=True,
            )
        return

    typer.echo(f"error: 未知盲评动作：{action}（应为 create/submit）。", err=True)
    raise typer.Exit(1)


@evaluate_app.command("report")
def report(
    lock: Annotated[str, typer.Option("--lock", help="运行锁标识")],
    version: Annotated[str | None, typer.Option("--version", help="报告版本")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="输出完整 JSON")] = False,
    db: Annotated[str | None, typer.Option("--db", help="评测数据库路径")] = None,
) -> None:
    """输出版本化评测报告摘要或完整 JSON。"""
    path = _resolve_database_path(db)
    repository = _open_repository(path)
    reports = repository.list_reports(lock)
    if not reports:
        typer.echo(f"error: 运行锁 {lock} 没有报告。", err=True)
        raise typer.Exit(1)
    report_record = reports[-1] if version is None else repository.get_report(
        reports[-1].report_id if version is None else f"report-{lock}",
        version,
    )
    if report_record is None:
        typer.echo(f"error: 报告不存在：{lock}@{version}", err=True)
        raise typer.Exit(1)
    if json_output:
        _print_json(report_record.model_dump(mode="json"))
        return
    typer.echo(f"报告 {report_record.report_id}@v{report_record.report_version}（锁 {lock}）")
    typer.echo(f"套件：{report_record.suite_id}@{report_record.suite_version}")
    for estimate in report_record.estimates:
        typer.echo(
            f"  {estimate.dimension.value}/{estimate.sut_id}/{estimate.metric_id}: "
            f"n={estimate.n} mean={estimate.mean:.2f} "
            f"CI=[{estimate.ci_low:.2f}, {estimate.ci_high:.2f}]"
        )
    typer.echo("对比（Welch t 检验，p<0.05 显著）：")
    for comparison in report_record.comparisons:
        marker = "显著" if comparison.significant else "不显著"
        typer.echo(
            f"  {comparison.dimension.value}/{comparison.metric_id}: "
            f"{comparison.sut_a}-{comparison.sut_b} 差={comparison.mean_diff:+.2f} "
            f"p={comparison.p_value:.3f}（{marker}）"
        )
    typer.echo("失败率：")
    for stats in report_record.failure_stats:
        typer.echo(
            f"  {stats.dimension.value}/{stats.sut_id}: "
            f"{stats.failure_rate:.1%}（高风险 {stats.high_risk_count}）"
        )
    if report_record.threshold_verdict is not None:
        verdict = report_record.threshold_verdict
        typer.echo(f"发布阈值：{'通过' if verdict.passed else '未通过'}")
        for blocker in verdict.blockers:
            typer.echo(f"  - {blocker}", err=True)


@evaluate_app.command("gates")
def gates(
    report_id: Annotated[str, typer.Option("--report", help="报告标识")],
    version: Annotated[str | None, typer.Option("--version", help="报告版本")] = None,
    db: Annotated[str | None, typer.Option("--db", help="评测数据库路径")] = None,
) -> None:
    """按发布阈值判定报告（供 Issue 41 发布门接入）。"""
    from bridges.evaluation.gates import build_default_threshold, evaluate_release_gate

    path = _resolve_database_path(db)
    repository = _open_repository(path)
    report_record = repository.get_report(report_id, version)
    if report_record is None:
        typer.echo(f"error: 报告不存在：{report_id}@{version or 'latest'}", err=True)
        raise typer.Exit(1)
    verdict = evaluate_release_gate(report_record, build_default_threshold())
    _print_json(verdict.model_dump(mode="json"))
    if not verdict.passed:
        raise typer.Exit(1)


__all__ = ["evaluate_app"]
