"""工单 43 正式报告：覆盖对账、放行裁决与回滚演练（任务 1/6/7）。

在真实文件数据库上执行回滚演练（复用生命周期测试夹具的数据建造器），
同时落库覆盖对账与放行裁决 JSON，供独立验收复核。确定性机制通过不
代表真实模型体验或外部门可得性通过。

用法（仓库根目录，conda ``agent``）：
    python -m scripts.run_issue43_acceptance_reports
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bridges.contracts.atomic_profile import (
    AtomicProfileItem,
    AtomicProfileItemStatus,
    AtomicProfileWriteOrigin,
)
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.evaluation.integration_coverage import build_coverage_report
from bridges.evaluation.release_adjudication import build_release_adjudication
from bridges.evaluation.rollback_rehearsal import run_rollback_rehearsal
from bridges.profiles.atomic import SqliteAtomicProfileRepository
from tests.lifecycle.harness import Harness

OUTPUT_DIR = Path(".scratch/2/validation/43-integrated-migration-and-release-regression")
WORK_DIR = Path(".tmp/issue43-rehearsal")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _seed_tasks_and_artifacts(database, account_id: str, conversation_id: str) -> None:
    """与 ``tests/evaluation/test_issue43_rollback_rehearsal.py`` 同构的演练数据。"""

    now = _now()
    with database.transaction():
        scoped = database.scoped(account_id)
        for task_id, goal, status, version in (
            (f"task-old-{account_id}", "整理旧任务", "completed", 2),
            (f"task-new-{account_id}", "进行中的新任务", "active", 1),
        ):
            scoped.execute(
                "INSERT INTO conversation_tasks(task_id, account_id, conversation_id,"
                " goal, status, current_version, contract_version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'task-v1', ?, ?)",
                (task_id, account_id, conversation_id, goal, status, version, now, now),
            )
            for item in range(1, version + 1):
                scoped.execute(
                    "INSERT INTO task_versions(version_id, task_id, account_id, version,"
                    " goal, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"ver-{task_id}-{item}", task_id, account_id, item, goal, now),
                )
        scoped.execute(
            "INSERT INTO task_events(event_id, account_id, conversation_id, task_id, kind,"
            " payload_json, created_at) VALUES (?, ?, ?, ?, 'version_created', '{}', ?)",
            (f"event-{account_id}", account_id, conversation_id, f"task-new-{account_id}", now),
        )
        scoped.execute(
            "INSERT INTO node_artifacts(artifact_id, account_id, conversation_id, run_id,"
            " task_id, task_version, recipe_id, recipe_version, node, artifact_type,"
            " schema_version, capability_version, trust_state, input_key, payload_json,"
            " content_hash, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1, 'recipe-x', '1.0', 'synthesis', 'answer',"
            " '1', 'cap-v1', 'qualified', 'key-1', '{}', 'hash-1', ?, ?)",
            (
                f"artifact-{account_id}",
                account_id,
                conversation_id,
                f"run-{account_id}",
                f"task-new-{account_id}",
                now,
                now,
            ),
        )


def _seed_profile_item(database, account_id: str) -> str:
    item_id = f"profile-{account_id}"
    now = datetime.now(UTC)
    SqliteAtomicProfileRepository(database, initialize=False).save_item(
        AtomicProfileItem(
            profile_item_id=item_id,
            owner_account_id=account_id,
            text="用户偏好先给结论再看推导",
            identity_key=f"{account_id}:偏好先给结论",
            status=AtomicProfileItemStatus.ACTIVE,
            write_origin=AtomicProfileWriteOrigin.AUTOMATIC,
            confidence=FourDimensionConfidence.MEDIUM,
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    return item_id


def run_rollback_evidence() -> dict[str, object]:
    run_dir = WORK_DIR / f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    run_dir.mkdir(parents=True, exist_ok=True)
    harness = Harness(run_dir)
    account_id = harness.acc1
    harness.seed_everything()
    harness.seed_retired_and_v2_state(account_id)
    conversation_id = harness.database.scoped(account_id).execute(
        "SELECT conversation_id FROM conversations WHERE account_id = ?"
        " ORDER BY created_at LIMIT 1",
        (account_id,),
    ).fetchone()["conversation_id"]
    _seed_tasks_and_artifacts(harness.database, account_id, conversation_id)
    _seed_profile_item(harness.database, account_id)
    report = run_rollback_rehearsal(harness.database, account_id)
    return report.to_dict()


def main() -> int:
    root = Path.cwd()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    coverage = build_coverage_report(root)
    adjudication = build_release_adjudication(root)
    rehearsal = run_rollback_evidence()

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    payloads = {
        "coverage-reconciliation.json": {
            "generated_at": generated_at,
            **coverage.to_dict(),
        },
        "release-adjudication.json": {
            "generated_at": generated_at,
            **adjudication.to_dict(),
        },
        "rollback-rehearsal.json": {
            "generated_at": generated_at,
            **rehearsal,
        },
    }
    for name, payload in payloads.items():
        (OUTPUT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(
        f"覆盖对账：total={coverage.total} problems={len(coverage.problems)}"
    )
    print(
        "放行裁决："
        + "；".join(
            f"{item.ticket}={item.decision}" for item in adjudication.decisions
        )
    )
    print(
        "回滚演练："
        + f"passed={rehearsal['passed']} checks={len(rehearsal['checks'])}"
    )
    ok = coverage.passed and adjudication.passed and bool(rehearsal["passed"])
    print("报告目录：" + str(OUTPUT_DIR))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
