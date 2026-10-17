"""工单 18：评测编排（场景套件、对照实验、边界与资源采集）。"""

from __future__ import annotations

import time
from typing import Any

from bridges.evaluation.profile_loop_eval import drivers, drivers2, drivers_ext
from bridges.evaluation.profile_loop_eval.drivers import (
    is_simple_scenario,
    run_conversation_scenario,
    run_extra_sample,
)
from bridges.evaluation.profile_loop_eval.environment import (
    LoopAccount,
    LoopEnvironment,
)
from bridges.evaluation.profile_loop_eval.scenes import (
    PAIRED_TASKS,
    SCENARIO_SPECS,
    SCENARIO_SPECS_BY_ID,
)
from bridges.evaluation.profile_loop_eval.session import AccountSession
from bridges.evaluation.profile_loop_eval.types import KIND_SCENARIO, SceneResult

_SPECIAL_DRIVERS = {
    "T04": drivers.run_t04,
    "T06": drivers.run_t06,
    "T08": drivers.run_t08,
    "T09": drivers.run_t09,
    "T10": drivers.run_t10,
    "T11": drivers.run_t11,
    "T12": drivers.run_t12,
    "T13": drivers2.run_t13,
    "T14": drivers2.run_t14,
    "T15": drivers2.run_t15,
    "T16": drivers2.run_t16,
    "T17": drivers2.run_t17,
    "T18": drivers2.run_t18,
    "T19": drivers2.run_t19,
    "T20": drivers2.run_t20,
}


class LoopEvaluationContext:
    """评测上下文：账户/会话工厂、重启与旁路结果登记。"""

    def __init__(self, environment: LoopEnvironment) -> None:
        self.environment = environment
        self.results: list[SceneResult] = []
        self.side_results: list[SceneResult] = []

    # ------------------------------------------------------------------
    # 会话工厂
    # ------------------------------------------------------------------

    def register_account(self, tag: str) -> LoopAccount:
        return self.environment.register_account(tag)

    def session_for(self, account: LoopAccount) -> AccountSession:
        return AccountSession(self.environment, account)

    def new_session(self, tag: str) -> AccountSession:
        return self.session_for(self.register_account(tag))

    def restart(self) -> None:
        self.environment.restart()

    def register_side_result(self, result: SceneResult) -> None:
        self.side_results.append(result)


def run_scenario_suite(
    ctx: LoopEvaluationContext, *, selection: tuple[str, ...] | None = None
) -> list[SceneResult]:
    selected = [
        spec for spec in SCENARIO_SPECS if selection is None or spec.scenario_id in selection
    ]
    results: list[SceneResult] = []
    for spec in selected:
        print(f"[18] 场景 {spec.scenario_id}：{spec.title} ...", flush=True)
        side_before = len(ctx.side_results)
        session = ctx.new_session(f"18-{spec.scenario_id}")
        driver = _SPECIAL_DRIVERS.get(spec.scenario_id)
        try:
            if driver is not None:
                result = driver(ctx, spec, session)
            else:
                result = run_conversation_scenario(ctx, spec, session)
        except Exception as exc:  # 单场景失败不阻塞其余场景，如实记录
            result = drivers.new_result(
                spec.scenario_id, KIND_SCENARIO, spec.title, session=session
            )
            result.record.error = f"{exc.__class__.__name__}: {exc}"
        results.append(result)
        if is_simple_scenario(spec):
            for sample in spec.extra_samples:
                print(f"[18] 变体 {sample.sample_id} ...", flush=True)
                sample_session = ctx.new_session(sample.sample_id)
                try:
                    results.append(run_extra_sample(ctx, spec, sample, sample_session))
                except Exception as exc:
                    failed = drivers.new_result(
                        sample.sample_id,
                        sample.kind,
                        f"{spec.scenario_id} 变体",
                        session=sample_session,
                    )
                    failed.record.error = f"{exc.__class__.__name__}: {exc}"
                    results.append(failed)
        # 专用驱动可能在驱动内登记了变体结果，随后统一并回。
        results.extend(ctx.side_results[side_before:])
        print(f"[18] 场景 {spec.scenario_id} 完成", flush=True)
    return results


def run_paired_suite(ctx: LoopEvaluationContext) -> list[SceneResult]:
    results: list[SceneResult] = []
    for task in PAIRED_TASKS:
        for arm in ("none", "correct", "wrong", "corrected"):
            print(f"[18] 对照实验 {task.task_id}/{arm} ...", flush=True)
            session = ctx.new_session(f"18-{task.task_id}-{arm}")
            try:
                results.append(drivers_ext.run_paired_arm(ctx, task, arm, session))
            except Exception as exc:
                failed = drivers.new_result(
                    f"{task.task_id}-{arm}",
                    "paired",
                    task.title,
                    session=session,
                )
                failed.record.error = f"{exc.__class__.__name__}: {exc}"
                results.append(failed)
    return results


def run_boundary_suite(ctx: LoopEvaluationContext) -> list[SceneResult]:
    results: list[SceneResult] = []
    for name, runner in (
        ("boundary-deletion", drivers_ext.run_boundary_deletion),
        ("boundary-sensitive-unrequested", drivers_ext.run_boundary_sensitive),
    ):
        print(f"[18] 边界场景 {name} ...", flush=True)
        session = ctx.new_session(f"18-{name}")
        try:
            results.append(runner(ctx, session))
        except Exception as exc:
            failed = drivers.new_result(name, "boundary", name, session=session)
            failed.record.error = f"{exc.__class__.__name__}: {exc}"
            results.append(failed)
    return results


def collect_resource_evidence(
    environment: LoopEnvironment,
    results: list[SceneResult],
    *,
    started_monotonic: float,
) -> dict[str, Any]:
    from bridges.evaluation.profile_loop_eval import metrics

    locks = environment.model_run_locks()
    queue_samples: list[int] = []
    for result in results:
        queue_samples.extend(result.record.extra.get("queue_depth_samples") or [])
        for turn in result.record.turns:
            turn_queue = (turn.get("idle") or {}).get("queue_depth")
            if isinstance(turn_queue, int):
                queue_samples.append(turn_queue)
    return {
        "resources": metrics.summarize_resources(locks),
        "latency": metrics.latency_summary(results),
        "tasks_by_status": environment.extraction_tasks_summary(),
        "queue_depth_max_observed": max(queue_samples) if queue_samples else 0,
        "queue_depth_samples": len(queue_samples),
        "wall_clock_ms": int((time.monotonic() - started_monotonic) * 1000),
    }


def scenario_ids_for(selection: str) -> tuple[str, ...]:
    if not selection.strip():
        return tuple(spec.scenario_id for spec in SCENARIO_SPECS)
    ids = [item.strip().upper() for item in selection.split(",") if item.strip()]
    for scenario_id in ids:
        if scenario_id not in SCENARIO_SPECS_BY_ID:
            raise SystemExit(f"未知场景：{scenario_id}")
    return tuple(ids)


__all__ = [
    "LoopEvaluationContext",
    "run_scenario_suite",
    "run_paired_suite",
    "run_boundary_suite",
    "collect_resource_evidence",
    "scenario_ids_for",
]
