"""Evaluation domain service for T012.

The service creates immutable ``EvaluationRunLock`` instances from completed
project task runs, replays them through the production workflow seam to produce
``EvaluationResultBundle`` objects, and compares bundles in the evaluation
center without exposing private body to logs.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bridges import __version__ as package_version
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.evaluation import (
    EvaluationCreateRequest,
    EvaluationDiffEntry,
    EvaluationDiffProjection,
    EvaluationFailureCategory,
    EvaluationMetric,
    EvaluationResultBundle,
    EvaluationRunLock,
    EvaluationRunProjection,
    EvaluationRunStatus,
    EvaluationSuiteRef,
)
from bridges.contracts.workflows import (
    RunProjection,
    WorkflowRunStatus,
    WorkOrder,
)
from bridges.observability.scrubber import scrub_payload
from bridges.workflows import WorkflowError, WorkflowService


class EvaluationError(Exception):
    """Domain exception for evaluation failures.

    The message is safe to expose to callers; it never leaks whether a run
    exists or belongs to another account.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _build_digest() -> str:
    """Return a deterministic build digest for the current code.

    In a real CI pipeline this would be the git commit hash or container digest.
    For local development the package version is combined with an optional
    ``BRIDGES_BUILD_DIGEST`` override so tests can freeze it.
    """
    override = os.environ.get("BRIDGES_BUILD_DIGEST")
    if override:
        return override
    return f"bridges@{package_version}"


def _runtime_summary() -> str:
    """Return a short OS/hardware summary string."""
    return f"{os.name}/python-{package_version}"


def _config_digest() -> str:
    """Return a deterministic digest of relevant runtime configuration.

    Only non-secret configuration keys are included; secrets are never read.
    """
    return "default-config-v1"


def _extract_prompt_versions(locks: list[ModelRunLock]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for lock in locks:
        key = f"{lock.capability_name}@{lock.capability_version}"
        versions[key] = lock.prompt_version
    return versions


def _extract_schema_versions(locks: list[ModelRunLock]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for lock in locks:
        key = f"{lock.capability_name}@{lock.capability_version}"
        versions[key] = lock.input_output_contract
    return versions


def _model_locks_match(a: list[ModelRunLock], b: list[ModelRunLock]) -> bool:
    if len(a) != len(b):
        return False
    for lock_a, lock_b in zip(a, b, strict=True):
        if lock_a.capability_name != lock_b.capability_name:
            return False
        if lock_a.capability_version != lock_b.capability_version:
            return False
        if lock_a.actual_model_id != lock_b.actual_model_id:
            return False
        if lock_a.prompt_version != lock_b.prompt_version:
            return False
        if lock_a.input_output_contract != lock_b.input_output_contract:
            return False
    return True


@dataclass
class _EvaluationRunRecord:
    evaluation_run_id: str
    account_id: str
    project_id: str
    lock: EvaluationRunLock
    status: EvaluationRunStatus
    bundle_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)


class EvaluationService:
    """In-memory evaluation service for T012.

    The public interface mirrors the eventual persistent evaluation-center adapter
    so later tickets can swap storage without changing callers.
    """

    def __init__(self, workflow_service: WorkflowService) -> None:
        self._workflow_service = workflow_service
        self._runs: dict[str, _EvaluationRunRecord] = {}
        self._bundles: dict[str, EvaluationResultBundle] = {}

    def _require_record(
        self, account_id: str, evaluation_run_id: str
    ) -> _EvaluationRunRecord:
        record = self._runs.get(evaluation_run_id)
        if record is None or record.account_id != account_id:
            raise EvaluationError("评测运行不存在或没有访问权限。")
        return record

    def _require_bundle(self, account_id: str, bundle_id: str) -> EvaluationResultBundle:
        bundle = self._bundles.get(bundle_id)
        if bundle is None or bundle.account_id != account_id:
            raise EvaluationError("结果包不存在或没有访问权限。")
        return bundle

    def _build_lock(
        self,
        source_run: RunProjection,
        request: EvaluationCreateRequest,
    ) -> EvaluationRunLock:
        suite = EvaluationSuiteRef(
            suite_id=request.suite_id,
            suite_version=request.suite_version,
            case_id=request.case_id,
        )
        return EvaluationRunLock(
            lock_id=secrets.token_urlsafe(16),
            source_run_id=source_run.run_id,
            account_id=source_run.context_envelope.account_id,
            tenant_id=source_run.context_envelope.tenant_id,
            project_id=source_run.context_envelope.project_id,
            suite=suite,
            work_order=WorkOrder(
                workflow_name=source_run.context_envelope.workflow_name,
                workflow_version=source_run.context_envelope.workflow_version,
                project_id=source_run.context_envelope.project_id,
                objective=source_run.objective,
                success_criteria=source_run.success_criteria,
                risk_statement=source_run.risk_statement,
                object_refs=list(source_run.context_envelope.object_refs),
                memory_slice_refs=list(source_run.context_envelope.memory_slice_refs),
                domain_pack_refs=list(source_run.context_envelope.domain_pack_refs),
            ),
            workflow_name=source_run.context_envelope.workflow_name,
            workflow_version=source_run.context_envelope.workflow_version,
            code_commit_or_build_digest=_build_digest(),
            runtime_identifier=request.runtime_identifier,
            os_hardware_summary=_runtime_summary(),
            database_migration_version="migration-v1",
            config_digest=_config_digest(),
            random_seed=request.random_seed,
            dataset_versions={},
            domain_pack_versions=dict.fromkeys(
                source_run.context_envelope.domain_pack_refs,
                "1",
            ),
            model_run_locks=list(source_run.model_run_locks),
            prompt_versions=_extract_prompt_versions(source_run.model_run_locks),
            schema_versions=_extract_schema_versions(source_run.model_run_locks),
            tool_adapter_versions={},
            judge_versions={},
            execution_count=request.execution_count,
            network_cache_policy="frozen",
            time_baseline=_now(),
            created_at=_now(),
        )

    def _replay_workflow(
        self, account_id: str, lock: EvaluationRunLock
    ) -> tuple[
        RunProjection | None,
        list[ModelRunLock],
        list[str],
        EvaluationFailureCategory,
        str | None,
    ]:
        """Replay the frozen WorkOrder through the production workflow seam.

        Returns the final projection, observed model locks, state trajectory,
        failure category and failure reason. Failures are captured rather than
        raised so the result bundle can record them deterministically.
        """
        trajectory: list[str] = []
        observed_locks: list[ModelRunLock] = []

        try:
            draft = self._workflow_service.submit_work_order(
                account_id=account_id, order=lock.work_order
            )
        except WorkflowError as exc:
            return (
                None,
                observed_locks,
                trajectory,
                EvaluationFailureCategory.ORCHESTRATION,
                str(exc),
            )

        trajectory.append(draft.run_status.value)

        try:
            confirmed = self._workflow_service.confirm_work_order(
                account_id=account_id,
                run_id=draft.run_id,
                confirmed=True,
            )
        except WorkflowError as exc:
            return (
                draft,
                observed_locks,
                trajectory,
                EvaluationFailureCategory.ORCHESTRATION,
                str(exc),
            )

        trajectory.append(confirmed.run_status.value)
        projection = confirmed

        while projection.run_status in {
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.RETRYING,
        }:
            try:
                projection = self._workflow_service.advance_run(
                    account_id=account_id, run_id=projection.run_id
                )
            except WorkflowError as exc:
                return (
                    projection,
                    observed_locks,
                    trajectory,
                    EvaluationFailureCategory.ORCHESTRATION,
                    str(exc),
                )
            trajectory.append(projection.run_status.value)
            observed_locks.extend(projection.model_run_locks)

        if projection.run_status == WorkflowRunStatus.SUCCEEDED:
            return projection, observed_locks, trajectory, EvaluationFailureCategory.NONE, None
        if projection.run_status == WorkflowRunStatus.BLOCKED:
            failure_reason = "工作流进入阻塞终态。"
            for node in projection.nodes:
                if node.failure_reason:
                    failure_reason = node.failure_reason
                    break
            return (
                projection,
                observed_locks,
                trajectory,
                EvaluationFailureCategory.ORCHESTRATION,
                failure_reason,
            )
        if projection.run_status == WorkflowRunStatus.CANCELLED:
            return (
                projection,
                observed_locks,
                trajectory,
                EvaluationFailureCategory.ORCHESTRATION,
                f"运行被取消：{projection.cancel_reason}",
            )

        return (
            projection,
            observed_locks,
            trajectory,
            EvaluationFailureCategory.ORCHESTRATION,
            f"未预期的运行终态：{projection.run_status.value}",
        )

    def _execute_once(
        self,
        account_id: str,
        lock: EvaluationRunLock,
    ) -> tuple[
        RunProjection | None,
        list[ModelRunLock],
        list[str],
        EvaluationFailureCategory,
        str | None,
    ]:
        """Execute the frozen WorkOrder once and return replay results."""
        return self._replay_workflow(account_id, lock)

    def _build_bundle(
        self,
        record: _EvaluationRunRecord,
        request: EvaluationCreateRequest,
    ) -> EvaluationResultBundle:
        lock = record.lock
        last_bundle: EvaluationResultBundle | None = None

        for exec_index in range(request.execution_count):
            projection, observed_locks, trajectory, failure_category, failure_reason = (
                self._execute_once(record.account_id, lock)
            )

            status = (
                EvaluationRunStatus.SUCCEEDED
                if failure_category == EvaluationFailureCategory.NONE
                else EvaluationRunStatus.FAILED
            )

            run_projection = projection
            # Strip the full model run locks from the projection before storing it in
            # the bundle summary; the bundle stores them separately and scrubbed.
            if run_projection is not None:
                run_projection = run_projection.model_copy(
                    update={"model_run_locks": []}
                )

            outputs: dict[str, Any] = {}
            if projection is not None:
                outputs["run_status"] = projection.run_status.value
                outputs["artifact_trust_status"] = projection.artifact_trust_status.value
                outputs["publish_eligible"] = projection.publish_eligible

            metrics: list[EvaluationMetric] = []
            observed_latency: float | None = None
            if projection is not None:
                metrics.append(
                    EvaluationMetric(
                        name="node_count",
                        value=float(len(projection.nodes)),
                        unit="nodes",
                    )
                )
                if projection.run_started_at and projection.run_ended_at:
                    observed_latency = (
                        projection.run_ended_at - projection.run_started_at
                    ).total_seconds()

            logs, _ = scrub_payload(
                {
                    "objective": lock.work_order.objective,
                    "success_criteria": lock.work_order.success_criteria,
                    "risk_statement": lock.work_order.risk_statement,
                }
            )

            bundle = EvaluationResultBundle(
                bundle_id=secrets.token_urlsafe(16),
                lock_id=lock.lock_id,
                source_run_id=lock.source_run_id,
                account_id=record.account_id,
                project_id=record.project_id,
                suite=lock.suite,
                status=status,
                frozen_inputs=lock.work_order,
                run_projection=run_projection,
                outputs=outputs,
                state_trajectory=trajectory,
                typed_artifact_refs=[
                    node.output_ref for node in (projection.nodes if projection else [])
                    if node.output_ref
                ],
                model_tool_calls=observed_locks,
                failure_category=failure_category,
                failure_reason=failure_reason,
                derived_metrics=metrics,
                cost_latency={
                    "execution_count": request.execution_count,
                    "execution_index": exec_index,
                    # Runtime latency is an observation, not a deterministic
                    # replay result, so it must not participate in result diffs.
                    "observed_run_latency_seconds": observed_latency,
                },
                logs_and_traces=logs,
                reproduction_command=self._build_reproduction_command(lock),
                environment_summary={
                    "runtime": lock.runtime_identifier,
                    "build_digest": lock.code_commit_or_build_digest,
                    "config_digest": lock.config_digest,
                },
                created_at=_now(),
            )
            self._bundles[bundle.bundle_id] = bundle
            record.bundle_ids.append(bundle.bundle_id)
            last_bundle = bundle

        assert last_bundle is not None
        return last_bundle

    def _build_reproduction_command(self, lock: EvaluationRunLock) -> str:
        return (
            f"BridGes evaluation replay "
            f"--project-id {lock.project_id} "
            f"--evaluation-run-id {lock.lock_id} "
            f"--runtime {lock.runtime_identifier}"
        )

    def _is_terminal(self, run_status: WorkflowRunStatus) -> bool:
        """Return True when the run status is a terminal state."""
        return run_status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.CANCELLED,
        }

    def create_evaluation_run(
        self,
        account_id: str,
        source_run: RunProjection,
        request: EvaluationCreateRequest,
    ) -> EvaluationRunProjection:
        """Create an evaluation run from a completed project task run."""
        if source_run.context_envelope.account_id != account_id:
            raise EvaluationError("没有权限为此运行创建评测。")

        if not self._is_terminal(source_run.run_status):
            raise EvaluationError(
                "只能从已完成的任务运行创建评测（当前状态："
                f"{source_run.run_status.value}）。"
            )

        lock = self._build_lock(source_run, request)
        record = _EvaluationRunRecord(
            evaluation_run_id=lock.lock_id,
            account_id=account_id,
            project_id=source_run.context_envelope.project_id,
            lock=lock,
            status=EvaluationRunStatus.PENDING,
        )
        self._runs[lock.lock_id] = record
        return self._build_projection(record)

    def replay_evaluation_run(
        self,
        account_id: str,
        evaluation_run_id: str,
    ) -> EvaluationResultBundle:
        """Replay an evaluation run lock and produce a new result bundle."""
        record = self._require_record(account_id, evaluation_run_id)
        record.status = EvaluationRunStatus.RUNNING
        request = EvaluationCreateRequest(
            suite_id=record.lock.suite.suite_id,
            suite_version=record.lock.suite.suite_version,
            case_id=record.lock.suite.case_id,
            runtime_identifier=record.lock.runtime_identifier,
            random_seed=record.lock.random_seed,
            execution_count=record.lock.execution_count,
        )
        bundle = self._build_bundle(record, request)
        record.status = bundle.status
        return bundle

    def get_evaluation_run(
        self,
        account_id: str,
        evaluation_run_id: str,
    ) -> EvaluationRunProjection:
        """Return the evaluation-center projection for an evaluation run."""
        record = self._require_record(account_id, evaluation_run_id)
        return self._build_projection(record)

    def get_result_bundle(
        self,
        account_id: str,
        bundle_id: str,
    ) -> EvaluationResultBundle:
        """Return a single result bundle."""
        return self._require_bundle(account_id, bundle_id)

    def compare_bundles(
        self,
        account_id: str,
        bundle_id_a: str,
        bundle_id_b: str,
    ) -> EvaluationDiffProjection:
        """Compare two result bundles in the evaluation center.

        The diff focuses on inputs, locks, results and failures; it never
        includes private body, full prompts or secrets.
        """
        bundle_a = self._require_bundle(account_id, bundle_id_a)
        bundle_b = self._require_bundle(account_id, bundle_id_b)

        if bundle_a.lock_id != bundle_b.lock_id:
            raise EvaluationError("只能比较同一评测运行锁产生的结果包。")

        return EvaluationDiffProjection(
            evaluation_run_id_a=bundle_a.lock_id,
            evaluation_run_id_b=bundle_b.lock_id,
            bundle_id_a=bundle_a.bundle_id,
            bundle_id_b=bundle_b.bundle_id,
            lock_match=bundle_a.lock_id == bundle_b.lock_id,
            input_diffs=self._diff_inputs(bundle_a.frozen_inputs, bundle_b.frozen_inputs),
            lock_diffs=self._diff_locks(
                bundle_a.model_tool_calls, bundle_b.model_tool_calls
            ),
            result_diffs=self._diff_results(bundle_a, bundle_b),
            failure_diffs=self._diff_failures(bundle_a, bundle_b),
        )

    def _build_projection(
        self, record: _EvaluationRunRecord
    ) -> EvaluationRunProjection:
        return EvaluationRunProjection(
            evaluation_run_id=record.evaluation_run_id,
            source_run_id=record.lock.source_run_id,
            account_id=record.account_id,
            project_id=record.project_id,
            suite=record.lock.suite,
            status=record.status,
            lock=record.lock,
            bundle_ids=list(record.bundle_ids),
            latest_bundle_id=record.bundle_ids[-1] if record.bundle_ids else None,
            created_at=record.created_at,
        )

    def _diff_inputs(
        self, a: WorkOrder, b: WorkOrder
    ) -> list[EvaluationDiffEntry]:
        diffs: list[EvaluationDiffEntry] = []
        for field_name in ("objective", "success_criteria", "risk_statement"):
            va = getattr(a, field_name)
            vb = getattr(b, field_name)
            if va != vb:
                diffs.append(
                    EvaluationDiffEntry(
                        field=f"work_order.{field}",
                        value_a=va,
                        value_b=vb,
                        kind="changed",
                    )
                )
        if a.object_refs != b.object_refs:
            diffs.append(
                EvaluationDiffEntry(
                    field="work_order.object_refs",
                    value_a=a.object_refs,
                    value_b=b.object_refs,
                    kind="changed",
                )
            )
        if a.memory_slice_refs != b.memory_slice_refs:
            diffs.append(
                EvaluationDiffEntry(
                    field="work_order.memory_slice_refs",
                    value_a=a.memory_slice_refs,
                    value_b=b.memory_slice_refs,
                    kind="changed",
                )
            )
        return diffs

    def _diff_locks(
        self, a: list[ModelRunLock], b: list[ModelRunLock]
    ) -> list[EvaluationDiffEntry]:
        diffs: list[EvaluationDiffEntry] = []
        if not _model_locks_match(a, b):
            diffs.append(
                EvaluationDiffEntry(
                    field="model_run_locks",
                    value_a=[lock.capability_name for lock in a],
                    value_b=[lock.capability_name for lock in b],
                    kind="changed",
                )
            )
        return diffs

    def _diff_results(
        self, a: EvaluationResultBundle, b: EvaluationResultBundle
    ) -> list[EvaluationDiffEntry]:
        diffs: list[EvaluationDiffEntry] = []
        if a.status != b.status:
            diffs.append(
                EvaluationDiffEntry(
                    field="status",
                    value_a=a.status.value,
                    value_b=b.status.value,
                    kind="changed",
                )
            )
        if a.outputs != b.outputs:
            diffs.append(
                EvaluationDiffEntry(
                    field="outputs",
                    value_a=a.outputs,
                    value_b=b.outputs,
                    kind="changed",
                )
            )
        if a.state_trajectory != b.state_trajectory:
            diffs.append(
                EvaluationDiffEntry(
                    field="state_trajectory",
                    value_a=a.state_trajectory,
                    value_b=b.state_trajectory,
                    kind="changed",
                )
            )
        metrics_a = {m.name: m.value for m in a.derived_metrics}
        metrics_b = {m.name: m.value for m in b.derived_metrics}
        if metrics_a != metrics_b:
            diffs.append(
                EvaluationDiffEntry(
                    field="derived_metrics",
                    value_a=metrics_a,
                    value_b=metrics_b,
                    kind="changed",
                )
            )
        return diffs

    def _diff_failures(
        self, a: EvaluationResultBundle, b: EvaluationResultBundle
    ) -> list[EvaluationDiffEntry]:
        diffs: list[EvaluationDiffEntry] = []
        if a.failure_category != b.failure_category:
            diffs.append(
                EvaluationDiffEntry(
                    field="failure_category",
                    value_a=a.failure_category.value,
                    value_b=b.failure_category.value,
                    kind="changed",
                )
            )
        if a.failure_reason != b.failure_reason:
            diffs.append(
                EvaluationDiffEntry(
                    field="failure_reason",
                    value_a=a.failure_reason,
                    value_b=b.failure_reason,
                    kind="changed",
                )
            )
        return diffs
