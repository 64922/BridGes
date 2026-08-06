"""评测运行器（Issue 40）。

执行流程：构建不可变运行锁（冻结套件摘要、代码/环境、数据集、模型与
SKILL 版本、裁判版本、评分量表版本、随机种子与执行次数）→ 按运行矩阵
逐案例逐种子逐次执行 → 计算确定性指标与自动断言 → 聚合版本化报告。

任何固定快照变化都会改变锁的摘要；同一锁的重放产生新的结果与报告
版本，旧结果 append-only 保留（可追溯、不被静默覆盖）。
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges import __version__ as package_version
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.evaluation_suite import (
    BlindReviewSummary,
    CaseResult,
    CaseRunStatus,
    EvaluationReport,
    FailureCaseInfo,
    SuiteDefinition,
    SuiteRunLock,
    now_iso,
)
from bridges.evaluation import suite_data
from bridges.evaluation.blind_review import BlindReviewSet
from bridges.evaluation.case_executors import execute_case
from bridges.evaluation.executors import (
    MODEL_BY_CAPABILITY,
    CaseOutcome,
    EvalEnvironment,
)
from bridges.evaluation.judges import (
    DETERMINISTIC_JUDGE_ID,
    DETERMINISTIC_JUDGE_VERSION,
    DeterministicMetricsJudge,
)
from bridges.evaluation.metrics import compute_dimension_metrics, run_auto_assertions
from bridges.evaluation.reference_method import REFERENCE_METHOD_VERSION
from bridges.evaluation.report import build_report
from bridges.evaluation.sut import SUTSpec
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase


class EvaluationRunnerError(Exception):
    """评测运行器领域错误。"""


@dataclass
class RunOutcome:
    """一次套件运行的完整产物。"""

    lock: SuiteRunLock
    results: list[CaseResult]
    report: EvaluationReport
    review_sets: list[BlindReviewSet] = field(default_factory=list)


def build_digest() -> str:
    """确定性构建摘要：优先 BRIDGES_BUILD_DIGEST，否则包版本。"""
    override = os.environ.get("BRIDGES_BUILD_DIGEST")
    if override:
        return override
    return f"bridges@{package_version}"


def config_digest() -> str:
    """配置摘要（不含秘密；与 T012 评测服务同语义）。"""
    return "default-config-v1"


class EvaluationRunner:
    """评测运行器：构建运行锁、执行矩阵、聚合报告。"""

    def __init__(
        self,
        suite: SuiteDefinition,
        sut_registry: dict[str, SUTSpec],
        *,
        seeds: list[int] | None = None,
        execution_count: int = 1,
        runtime_identifier: str = "eval-harness",
    ) -> None:
        self._suite = suite
        self._suts = sut_registry
        self._seeds = seeds or suite.seeds or [42]
        self._execution_count = execution_count or 1
        self._runtime_identifier = runtime_identifier
        self._judge = DeterministicMetricsJudge()

    # ------------------------------------------------------------------
    # 运行锁
    # ------------------------------------------------------------------

    def _build_plan_lock(
        self, seeds: list[int], execution_count: int
    ) -> SuiteRunLock:
        suite = self._suite
        return SuiteRunLock(
            lock_id=secrets.token_urlsafe(12),
            suite_id=suite.suite_id,
            suite_version=suite.version,
            suite_digest=suite.digest(),
            code_commit_or_build_digest=build_digest(),
            runtime_identifier=self._runtime_identifier,
            os_hardware_summary=f"{os.name}/python-{sys.version_info.major}.{sys.version_info.minor}",
            database_migration_version=str(SCHEMA_VERSION),
            config_digest=config_digest(),
            dataset_versions={
                entry.dataset_id: entry.version for entry in suite.manifest
            },
            domain_pack_versions=dict(suite.domain_pack_dependencies),
            model_run_locks=[],
            prompt_versions={
                pin.capability_name: pin.prompt_version
                for pin in suite.model_skill_pins
            },
            schema_versions={
                schema.schema_id: schema.version for schema in suite.artifact_schemas
            },
            tool_adapter_versions={
                "reference_method": REFERENCE_METHOD_VERSION,
                "scripted_gateway": "scripted-v1",
            },
            judge_versions={
                DETERMINISTIC_JUDGE_ID: DETERMINISTIC_JUDGE_VERSION,
            },
            scoring_scale_versions={
                scale.scale_id: scale.version for scale in suite.scales
            },
            random_seeds=list(seeds),
            execution_count=execution_count,
            network_cache_policy="frozen",
            created_at=now_iso(),
        )

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        db_path: str | os.PathLike[str] | None = None,
    ) -> RunOutcome:
        """执行套件运行矩阵，返回运行锁、结果与报告。"""
        seeds = self._seeds
        execution_count = self._execution_count
        lock = self._build_plan_lock(seeds, execution_count)
        # 每个案例执行使用独立环境（模板库拷贝），保证跨案例/跨种子不
        # 累积知识库与画像状态（双跑可复现的前提）。
        import tempfile

        cases = {case.case_id: case for case in suite_data.CASES}
        results: list[CaseResult] = []
        observed_locks: list[ModelRunLock] = []
        with tempfile.TemporaryDirectory(prefix="bridges-eval-") as tmp_dir:
            template_path = Path(tmp_dir) / "template.db"
            template = BridgesDatabase(template_path)
            template.initialize()
            template.close()

            execution_index_total = 0
            for entry in self._suite.run_matrix.entries:
                sut = self._suts.get(entry.sut_id)
                if sut is None:
                    raise EvaluationRunnerError(
                        f"运行矩阵引用的被测系统未注册：{entry.sut_id}"
                    )
                for case_id in entry.case_ids:
                    case = cases.get(case_id)
                    if case is None:
                        raise EvaluationRunnerError(f"案例不存在：{case_id}")
                    for seed in entry.seeds or seeds:
                        for execution_index in range(
                            entry.execution_count or execution_count
                        ):
                            env_path = (
                                Path(tmp_dir) / f"env-{execution_index_total}.db"
                            )
                            env = EvalEnvironment(
                                db_path=env_path, template_path=template_path
                            )
                            try:
                                env.cases = cases
                                outcome = execute_case(
                                    sut, case, seed, execution_index, env
                                )
                                observed_locks.extend(
                                    self._observed_locks(env)
                                )
                                results.append(
                                    self._to_case_result(
                                        lock,
                                        sut,
                                        case,
                                        seed,
                                        execution_index,
                                        outcome,
                                    )
                                )
                            finally:
                                env.close()
                            execution_index_total += 1

        # 用实际观察到的模型调用锁冻结运行锁（不可变快照）。
        seen_capabilities: set[str] = set()
        deduped_locks: list[ModelRunLock] = []
        for observed in observed_locks:
            if observed.capability_name in seen_capabilities:
                continue
            seen_capabilities.add(observed.capability_name)
            deduped_locks.append(observed)
        lock = lock.model_copy(update={"model_run_locks": deduped_locks})

        report = build_report(
            lock=lock,
            results=results,
            blind_summary=BlindReviewSummary(
                auto_judge_primary=len(results) > 0,
            ),
        )
        return RunOutcome(lock=lock, results=results, report=report)

    def _observed_locks(self, env: EvalEnvironment) -> list[ModelRunLock]:
        """从脚本化适配器捕获的调用构造观察锁（固定模型绑定可审计）。"""
        locks: list[ModelRunLock] = []
        seen: set[str] = set()
        for captured in env.scripted.captured_payloads:
            capability = str(captured.get("capability", ""))
            if capability in seen or not capability:
                continue
            seen.add(capability)
            locks.append(
                ModelRunLock(
                    lock_id=secrets.token_urlsafe(12),
                    run_id=str(captured.get("run_id", "eval")),
                    account_id=suite_data.EVAL_ACCOUNT,
                    project_id=suite_data.EVAL_PROJECT,
                    capability_name=capability,
                    capability_version="1",
                    actual_model_id=MODEL_BY_CAPABILITY.get(capability),
                    region="cn-beijing",
                    parameters={},
                    prompt_version="1",
                    input_output_contract="scripted-v1",
                    fallback_path=[capability],
                    status=ModelCallStatus.SUCCESS,
                    retry_count=0,
                    created_at=datetime.now(UTC),
                )
            )
        return locks

    def _to_case_result(
        self,
        lock: SuiteRunLock,
        sut: SUTSpec,
        case: Any,
        seed: int,
        execution_index: int,
        outcome: CaseOutcome,
    ) -> CaseResult:
        metrics = compute_dimension_metrics(case, outcome.outputs)
        if case.risk_tier != "normal":
            metrics = [
                metric.model_copy(update={"slice_tag": case.risk_tier})
                for metric in metrics
            ]
        assertions = run_auto_assertions(case, outcome.outputs)
        judge_scores = self._judge.score(metrics)

        if outcome.trajectory == ["error"] or bool(outcome.outputs.get("error_code")):
            status = CaseRunStatus.ERROR
        elif any(not assertion.passed for assertion in assertions):
            status = CaseRunStatus.FAILED
        else:
            status = CaseRunStatus.SUCCEEDED

        failure_case: FailureCaseInfo | None = None
        if status in {CaseRunStatus.FAILED, CaseRunStatus.ERROR}:
            failed_assertions = [
                assertion.assertion_id
                for assertion in assertions
                if not assertion.passed
            ]
            severity = "high" if case.risk_tier == "high_risk" else "medium"
            failure_case = FailureCaseInfo(
                failure_id=f"fail-{lock.lock_id}-{case.case_id}-{seed}-{execution_index}",
                dimension=_dimension_for_task(case.task_id),
                task_id=case.task_id,
                case_id=case.case_id,
                sut_id=sut.sut_id,
                severity=severity,
                description=_failure_description(case, failed_assertions, outcome),
                trace_refs=[
                    f"input:{case.case_key()}",
                    f"lock:{lock.lock_id}",
                    "tools:case-result.tool_records",
                    "output:case-result.outputs",
                ],
                reproduction_command=self.reproduction_command(lock, case, sut, seed,
                    execution_index),
                created_at=now_iso(),
            )

        return CaseResult(
            case_result_id=secrets.token_urlsafe(12),
            lock_id=lock.lock_id,
            sut_id=sut.sut_id,
            task_id=case.task_id,
            case_id=case.case_id,
            seed=seed,
            execution_index=execution_index,
            status=status,
            outputs=outcome.outputs,
            artifacts=[],
            tool_records=outcome.tool_records,
            state_trajectory=outcome.trajectory,
            auto_assertions=assertions,
            metrics=metrics,
            judge_scores=judge_scores,
            failure_case=failure_case,
            reproduction_command=self.reproduction_command(lock, case, sut, seed, execution_index),
            latency_ms=outcome.latency_ms,
            cost_estimate={"model_calls": len(outcome.model_locks)},
            created_at=now_iso(),
        )

    @staticmethod
    def reproduction_command(
        lock: SuiteRunLock, case: Any, sut: SUTSpec, seed: int, execution_index: int
    ) -> str:
        """一键重放该案例的命令（确定性：套件+案例+种子+序号）。"""
        return (
            f"BridGes evaluate replay --suite {lock.suite_id} "
            f"--version {lock.suite_version} --case {case.case_id} "
            f"--sut {sut.sut_id} --seed {seed} --execution {execution_index}"
        )


def _dimension_for_task(task_id: str) -> Any:
    from bridges.contracts.evaluation_suite import EvaluationDimension

    mapping = {
        "task-profile-loop": EvaluationDimension.PROFILE,
        "task-humanization": EvaluationDimension.HUMANIZATION,
        "task-science": EvaluationDimension.SCIENCE,
        "task-teaching": EvaluationDimension.TEACHING,
        "task-career": EvaluationDimension.CAREER,
        "task-multimodal": EvaluationDimension.MULTIMODAL,
        "task-security": EvaluationDimension.SECURITY,
    }
    return mapping.get(task_id, EvaluationDimension.SCIENCE)


def _failure_description(
    case: Any, failed_assertions: list[str], outcome: CaseOutcome
) -> str:
    error_message = outcome.outputs.get("error_message", "")
    if error_message:
        return f"执行错误：{error_message}"
    if failed_assertions:
        return f"未通过的自动断言：{'、'.join(failed_assertions[:3])}"
    return "案例未达标。"


def compare_double_run(
    run_a: RunOutcome,
    run_b: RunOutcome,
    *,
    tolerances: dict[str, float] | None = None,
) -> dict[str, Any]:
    """双跑对比：比较运行锁、样本集合与允许随机范围内的指标差异。

    用于 Verification-1（干净环境中连续两次固定评测）。
    """
    tolerances = tolerances or {}
    locks_match = (
        run_a.lock.digest() == run_b.lock.digest()
        and run_a.lock.observed_digest() == run_b.lock.observed_digest()
    )
    sample_a = {_sample_key(r) for r in run_a.results}
    sample_b = {_sample_key(r) for r in run_b.results}
    samples_match = sample_a == sample_b

    metric_diffs: dict[str, float] = {}
    metric_diffs_max: dict[str, float] = {}
    results_b = {_sample_key(r): r for r in run_b.results}
    for result in run_a.results:
        peer = results_b.get(_sample_key(result))
        if peer is None:
            continue
        for metric in result.metrics:
            peer_value = next(
                (m.value for m in peer.metrics if m.metric_id == metric.metric_id),
                None,
            )
            if peer_value is None:
                continue
            diff = abs(metric.value - peer_value)
            metric_diffs[metric.metric_id] = metric_diffs.get(metric.metric_id, 0.0) + diff
            metric_diffs_max[metric.metric_id] = max(
                metric_diffs_max.get(metric.metric_id, 0.0), diff
            )

    within_tolerance = True
    for metric_id, tolerance in tolerances.items():
        if metric_diffs.get(metric_id, 0.0) > tolerance:
            within_tolerance = False

    return {
        "locks_match": locks_match,
        "lock_a_digest": run_a.lock.digest(),
        "lock_b_digest": run_b.lock.digest(),
        "samples_match": samples_match,
        "sample_count_a": len(sample_a),
        "sample_count_b": len(sample_b),
        "metric_diff_sum": metric_diffs,
        "metric_diff_max": metric_diffs_max,
        "within_tolerance": within_tolerance,
        "tolerances": tolerances,
    }


def _sample_key(result: CaseResult) -> str:
    return (
        f"{result.sut_id}@{result.case_id}@{result.seed}@{result.execution_index}"
    )


__all__ = [
    "EvaluationRunner",
    "EvaluationRunnerError",
    "RunOutcome",
    "build_digest",
    "config_digest",
    "compare_double_run",
]
