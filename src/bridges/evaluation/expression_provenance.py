"""工单 39：评测套件身份、代码提交与运行锁（复用既有评测合同）。

运行锁把套件/代码提交/量表/策略臂/模型调用锁写进同一份不可变记录，
真实与确定性运行共用，保证可复现。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.evaluation_suite import SuiteRunLock, now_iso
from bridges.evaluation.expression_policy_arms import ARM_STRATEGY_VERSIONS
from bridges.evaluation.expression_scale import REVIEW_DIMENSIONS, SCALE_VERSION

#: 评测套件身份（进入运行锁）。
SUITE_ID = "human-expression-blind-evaluation"
SUITE_VERSION = "1.1.0"
#: 确定性门禁与检查点版本。
GATE_VERSION = "human-expression-gates-v2"
REPO_ROOT = Path(__file__).resolve().parents[3]


def code_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_run_lock(
    *,
    lock_id: str,
    dataset_versions: dict[str, str],
    prompt_versions: dict[str, str],
    model_run_locks: list[ModelRunLock] | None = None,
    random_seeds: list[int] | None = None,
    execution_count: int = 1,
    suite_digest_value: str,
    network_cache_policy: str,
) -> SuiteRunLock:
    """构造复用合同的不可变运行锁（真实运行附带观察到的模型锁）。"""

    from bridges.storage.database import SCHEMA_VERSION

    return SuiteRunLock(
        lock_id=lock_id,
        suite_id=SUITE_ID,
        suite_version=SUITE_VERSION,
        suite_digest=suite_digest_value,
        code_commit_or_build_digest=code_commit() or "unknown",
        runtime_identifier="conda-agent-windows-eval",
        os_hardware_summary=(
            f"{platform.system()} {platform.release()} {platform.machine()} / "
            f"Python {platform.python_version()}"
        ),
        database_migration_version=str(SCHEMA_VERSION),
        config_digest=sha256_text(
            json.dumps(
                {
                    "arms": {arm.value: version for arm, version in ARM_STRATEGY_VERSIONS.items()},
                    "scales": SCALE_VERSION,
                    "gates": GATE_VERSION,
                    "seeds": random_seeds or [],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
        dataset_versions=dict(dataset_versions),
        model_run_locks=list(model_run_locks or []),
        prompt_versions=dict(prompt_versions),
        schema_versions={"expression-task-contract": "expression-task-v1"},
        tool_adapter_versions={"real_qwen_gateway": "production-composition"},
        judge_versions={"deterministic-gates": GATE_VERSION},
        scoring_scale_versions={
            dimension.dimension_id: SCALE_VERSION for dimension in REVIEW_DIMENSIONS
        },
        random_seeds=list(random_seeds or [39]),
        execution_count=execution_count,
        network_cache_policy=network_cache_policy,
        created_at=now_iso(),
    )


__all__ = [
    "GATE_VERSION",
    "REPO_ROOT",
    "SUITE_ID",
    "SUITE_VERSION",
    "build_run_lock",
    "code_commit",
    "sha256_text",
]
