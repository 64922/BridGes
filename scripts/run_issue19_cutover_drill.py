"""画像工单 19：四维路径退出、切换、回滚与重升级演练。

用法（仓库根目录、conda ``agent`` 环境）：

    python scripts/run_issue19_cutover_drill.py --strict

演练只使用仓库 ``.tmp`` 下的隔离数据库，不接触正式数据库，也不调用网络模型。
它创建切换前已验证备份，模拟切换后新增事实、人工修订、删除墓碑、控制变更、
旧队列残留与迁移台账，再恢复到新库；随后执行重升级审计与确定性重试探针，
证明旧任务不会复活已删事实，新路径不再写四维记录。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from issue19_cutover_fixtures import (
    CUTOVER_ACCOUNT as ACCOUNT,
)
from issue19_cutover_fixtures import (
    TS,
    run_retry_probe,
    seed_account,
    seed_item,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _check(checks: list[dict[str, Any]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({"check_id": check_id, "passed": bool(ok), "detail": detail})


def _authority(database_path: Path) -> Any:
    from pydantic import SecretStr

    from bridges.config import Settings
    from bridges.profiles.replay import resolve_authoritative_database

    settings = Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{database_path.as_posix()}"),
    )
    return resolve_authoritative_database(settings=settings)


def _seed_extraction_run(
    database: Any,
    account_id: str,
    *,
    extraction_id: str,
    message_id: str,
    version: str,
    status: str,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_runs "
            "(extraction_id, account_id, message_id, extractor_version, source_hash, "
            "source_snapshot, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '来源快照', ?, ?, ?)",
            (
                extraction_id,
                account_id,
                message_id,
                version,
                f"hash-{extraction_id}",
                status,
                TS,
                TS,
            ),
        )


def _seed_extraction_task(
    database: Any,
    account_id: str,
    *,
    task_id: str,
    message_id: str,
    version: str,
    status: str,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tasks "
            "(task_id, account_id, message_id, extractor_version, source_hash, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, account_id, message_id, version, f"hash-{task_id}", status, TS, TS),
        )


def _seed_template(database_path: Path) -> None:
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(database_path)
    database.initialize()
    seed_account(database, ACCOUNT)
    seed_item(database, ACCOUNT, "item-edit", "我住在北京")
    seed_item(database, ACCOUNT, "item-delete", "我下周去上海")
    seed_item(database, ACCOUNT, "item-backup-only", "我养了一只猫")
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_four_dimension_records "
            "(record_id, account_id, dimension, label, content, "
            "first_stable_recorded_at, updated_at, version, status, "
            "source_record_id, source_version, content_hash, write_origin, "
            "migration_version) "
            "VALUES ('legacy-1', ?, 'hobby', '兴趣爱好', '我养了一只猫', ?, ?, 1, "
            "'active', 'record-1', 1, 'legacy-hash', 'migration', 'v1')",
            (ACCOUNT, TS, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_overview "
            "(account_id, context_version, epoch, dependency_fingerprint, payload_json) "
            "VALUES (?, 'overview-v1', 1, '', '[]')",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_ongoing_context "
            "(account_id, context_version, epoch, dependency_fingerprint, payload_json) "
            "VALUES (?, 'ongoing-v1', 1, '', '[]')",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_recall_index_versions "
            "(version_id, account_id, contract_json, contract_hash, status, created_at) "
            "VALUES ('version-1', ?, '{}', 'hash-1', 'ready', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_recall_index_entries "
            "(version_id, account_id, profile_item_id, item_version, text_hash, updated_at) "
            "VALUES ('version-1', ?, 'item-edit', 1, 'text-hash', ?)",
            (ACCOUNT, TS),
        )
    _seed_extraction_run(
        database,
        ACCOUNT,
        extraction_id="run-old-pending",
        message_id="message-old-pending",
        version="old-v1",
        status="pending",
    )
    _seed_extraction_task(
        database,
        ACCOUNT,
        task_id="task-old-pending",
        message_id="message-old-pending",
        version="old-v1",
        status="pending",
    )
    database.close()


def _mutate_after_cutover(current_path: Path) -> None:
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(current_path)
    database.initialize()
    seed_item(database, ACCOUNT, "item-new", "我加入了摄影社")
    with database.transaction():
        database.connection.execute(
            "UPDATE profile_items SET text = ?, user_edited_at = ?, version = 2, "
            "updated_at = ? WHERE account_id = ? AND profile_item_id = 'item-edit'",
            ("我住在杭州", TS, TS, ACCOUNT),
        )
        database.connection.execute(
            "UPDATE profile_items SET text = '', status = 'withdrawn', version = 2, "
            "updated_at = ? WHERE account_id = ? AND profile_item_id = 'item-delete'",
            (TS, ACCOUNT),
        )
        database.connection.execute(
            "DELETE FROM profile_items WHERE account_id = ? "
            "AND profile_item_id = 'item-backup-only'",
            (ACCOUNT,),
        )
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) "
            "VALUES (?, 0, 'profile-controls-v1', 2, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_feedback "
            "(feedback_id, account_id, profile_item_id, kind, note, item_version, created_at) "
            "VALUES ('feedback-1', ?, 'item-edit', 'accurate', NULL, 2, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, 'message-deleted', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES ('block-account', ?, 'account', '', ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_migrations "
            "(run_id, account_id, migration_version, status, migrated, duplicated, "
            "tombstoned, skipped, failed, created_item_ids_json, source_record_ids_json, "
            "reconciliation_digest, retryable, created_at) "
            "VALUES ('run-cutover', ?, 'profile-migration-v1', 'completed', 1, 0, 0, 0, 0, "
            "'[\"item-new\"]', '[]', 'digest-1', 0, ?)",
            (ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_migration_records "
            "(run_id, account_id, source_record_id, outcome, reason_code, "
            "profile_item_id, recorded_at) "
            "VALUES ('run-cutover', ?, 'record-1', 'migrated', 'ok', 'item-new', ?)",
            (ACCOUNT, TS),
        )
    _seed_extraction_run(
        database,
        ACCOUNT,
        extraction_id="run-new-pending",
        message_id="message-new-pending",
        version="new-v1",
        status="pending",
    )
    _seed_extraction_task(
        database,
        ACCOUNT,
        task_id="task-new-pending",
        message_id="message-new-pending",
        version="new-v1",
        status="pending",
    )
    database.close()


def _render(payload: dict[str, Any]) -> str:
    environment = payload["environment"]
    lines = [
        f"# 四维路径退出与切换恢复演练（{payload['generated_at']}）",
        "",
        f"- 代码提交：{payload['git_commit']}",
        f"- 运行命令：`{payload['command']}`",
        (
            f"- 环境：Python {environment['python']} / "
            f"{environment['platform']} / conda `{environment['conda_env']}`"
        ),
        f"- 运行目录：{payload['run_dir']}",
        f"- 备份：{payload['backup']['backup_id']}",
        f"- 恢复核对：{'通过' if payload['recovery']['passed'] else '未通过'}",
        "",
        "## 判定",
        "",
    ]
    for check in payload["checks"]:
        mark = "通过" if check["passed"] else "未通过"
        lines.append(f"- [{mark}] {check['check_id']}：{check['detail']}")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本演练使用合成数据库，不包含真实用户数据。",
            "- 覆盖审计、已验证备份、切换后写入、整库画像域恢复、重升级审计、"
            "旧队列边界、墓碑/控制保留和四维存量只读。",
            "- 不覆盖旧生产队列实例的重试、真实模型调用或整库聊天/文件/学习记录回滚。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / ".scratch"
        / "profile-memory-redesign"
        / "validation"
        / "19-cutover-retirement-and-recovery",
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    output_dir: Path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / ".tmp" / f"issue19-cutover-{stamp}-{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True)
    checks: list[dict[str, Any]] = []

    from bridges.profiles.cutover import audit_profile_retirement
    from bridges.profiles.cutover_recovery import recover_cutover_state
    from bridges.profiles.replay import create_verified_backup
    from bridges.storage.database import BridgesDatabase

    template_path = run_dir / "template.db"
    _seed_template(template_path)
    backup_dir = run_dir / "backups"
    backup_dir.mkdir()
    backup = create_verified_backup(_authority(template_path), backup_dir / "cutover.db")
    current_path = run_dir / "current" / "bridges.db"
    current_path.parent.mkdir()
    shutil.copy2(backup.path, current_path)
    _mutate_after_cutover(current_path)

    target_path = run_dir / "recovered" / "bridges.db"
    target_path.parent.mkdir()
    recovery = recover_cutover_state(
        backup=backup,
        current_path=current_path,
        target_path=target_path,
        account_id=ACCOUNT,
    )
    audit = audit_profile_retirement(target_path, account_id=ACCOUNT)
    retry_probe = run_retry_probe(target_path, checks)
    _check(checks, "recovery_passed", recovery.passed, "恢复库核对全部通过")
    _check(checks, "reupgrade_audit_passed", audit.passed, "恢复库重升级审计通过")

    database = BridgesDatabase(target_path)
    database.initialize()
    active = {
        str(row["profile_item_id"]): str(row["text"])
        for row in database.connection.execute(
            "SELECT profile_item_id, text FROM profile_items "
            "WHERE account_id = ? AND status = 'active'",
            (ACCOUNT,),
        ).fetchall()
    }
    deleted = database.connection.execute(
        "SELECT status, text FROM profile_items "
        "WHERE account_id = ? AND profile_item_id = 'item-delete'",
        (ACCOUNT,),
    ).fetchone()
    controls = database.connection.execute(
        "SELECT profile_usage_enabled FROM profile_account_controls WHERE account_id = ?",
        (ACCOUNT,),
    ).fetchone()
    four_dimension_count = int(
        database.connection.execute(
            "SELECT COUNT(*) FROM profile_four_dimension_records WHERE account_id = ?",
            (ACCOUNT,),
        ).fetchone()[0]
    )
    old_rows = int(
        database.connection.execute(
            "SELECT (SELECT COUNT(*) FROM profile_extraction_runs "
            "WHERE account_id = ? AND extraction_id = 'run-old-pending') "
            "+ (SELECT COUNT(*) FROM profile_extraction_tasks "
            "WHERE account_id = ? AND task_id = 'task-old-pending')",
            (ACCOUNT, ACCOUNT),
        ).fetchone()[0]
    )
    new_rows = int(
        database.connection.execute(
            "SELECT (SELECT COUNT(*) FROM profile_extraction_runs "
            "WHERE account_id = ? AND extraction_id = 'run-new-pending') "
            "+ (SELECT COUNT(*) FROM profile_extraction_tasks "
            "WHERE account_id = ? AND task_id = 'task-new-pending')",
            (ACCOUNT, ACCOUNT),
        ).fetchone()[0]
    )
    database.close()
    _check(
        checks,
        "post_cutover_changes_survive",
        active.get("item-new") == "我加入了摄影社"
        and active.get("item-edit") == "我住在杭州"
        and active.get("item-backup-only") == "我养了一只猫",
        "新增事实、人工修订与备份独有事实都在恢复库中",
    )
    _check(
        checks,
        "deletion_barrier_survives",
        deleted is not None and deleted["status"] == "withdrawn" and deleted["text"] == "",
        "删除条目的墓碑状态和空正文都保留",
    )
    _check(
        checks,
        "controls_survive",
        controls is not None and int(controls["profile_usage_enabled"]) == 0,
        "切换后的画像使用控制保留",
    )
    _check(
        checks,
        "legacy_records_are_not_rewritten",
        four_dimension_count == 1,
        "四维存量记录只读保留，没有新增写入",
    )
    _check(
        checks,
        "old_extraction_queue_not_resumed",
        old_rows == 0 and new_rows == 2,
        "切换前活动运行/任务已移除，切换后新增运行/任务保留",
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "command": "python scripts/run_issue19_cutover_drill.py --strict",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "conda_env": os.environ.get("CONDA_DEFAULT_ENV", ""),
        },
        "run_dir": str(run_dir.relative_to(REPO_ROOT)),
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "backup": backup.as_dict(),
        "recovery": recovery.as_dict(),
        "reupgrade_audit": audit.as_dict(),
        "retry_probe": retry_probe,
    }
    evidence_path = output_dir / f"cutover-drill-{stamp}.json"
    evidence_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    record_path = output_dir / "verification-record.md"
    record_path.write_text(_render(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.strict and not payload["passed"]:
        return 4
    return 0 if payload["passed"] else 1


def _git_commit() -> str:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
