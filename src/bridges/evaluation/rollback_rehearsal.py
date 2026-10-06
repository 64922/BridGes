"""工单 43：正式回滚演练（保留数据、停新写、不复活退役能力）。

演练直接对真实 SQLite 数据库执行，不模拟内存替身：

1. 记录账户数据清单（新旧任务/事件/产物/运行/画像/检查点）；
2. 表达策略回滚：策略资源缺失时编译器降级为安全基线，旧快照重试原样
   复用，画像条目与采用快照不因回滚丢失，资源恢复后可重新采用；
3. 确定性保护复核：事实保护协议常量为代码注册，不随提示策略回滚撤销；
4. 停新写复核：库完整、全部登记表可读、清单不缩水（不清库/不丢数据）；
5. 退役能力复核：重跑退役清理保持幂等，历史行保留，不恢复启用态。

本模块只读业务数据（退役清理为幂等 UPDATE，不删除历史），供测试与
``scripts/run_issue43_rollback_rehearsal.py`` 复用。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bridges.chat.fact_protection import FACT_PROTECTION_PROTOCOL_VERSION
from bridges.chat.global_writing_policy import (
    GlobalWritingPolicyCompiler,
    restore_protected_regions,
)
from bridges.credentials.store import InMemoryCredentialStore
from bridges.profiles.atomic import (
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.retirement import retire_user_extensions, run_reminder_retirement
from bridges.storage import BridgesDatabase

REHEARSAL_RUN_ID = "issue43-rollback-rehearsal"

INVENTORY_TABLES: tuple[str, ...] = (
    "conversations",
    "messages",
    "conversation_tasks",
    "task_versions",
    "task_events",
    "node_artifacts",
    "generation_runs",
    "generation_events",
    "graph_checkpoints",
    "workflow_runs",
    "profile_items",
)

_PROTECTED_ORIGINAL = (
    "请保留 `result = 42`，公式 $E=mc^2$，链接 https://example.com/a?q=1，"
    '以及 JSON {"answer": 42}。'
)
_PROTECTED_CANDIDATE = (
    "请保留 `result = 0`，公式 $E=mc^2$，链接 https://example.com/b，"
    '以及 JSON {"answer": 0}。'
)


@dataclass(frozen=True)
class RehearsalCheck:
    """一项回滚演练断言。"""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class RollbackRehearsalReport:
    """回滚演练报告：逐项断言 + 回滚前后数据清单。"""

    checks: tuple[RehearsalCheck, ...]
    inventory_before: Mapping[str, int]
    inventory_after: Mapping[str, int]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def failures(self) -> tuple[RehearsalCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "inventory_before": dict(self.inventory_before),
            "inventory_after": dict(self.inventory_after),
        }


def snapshot_inventory(
    database: BridgesDatabase, account_id: str
) -> dict[str, int]:
    """统计账户在各登记表的数据条数；表名固定，缺失即演练失败。"""

    counts: dict[str, int] = {}
    for table in INVENTORY_TABLES:
        row = database.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        counts[table] = int(row["count"])
    return counts


def rehearsal_expression_rollback(
    database: BridgesDatabase, account_id: str
) -> tuple[RehearsalCheck, ...]:
    """表达策略资源缺失（回滚态）下画像数据保留且可恢复采用。"""

    repository = SqliteAtomicProfileRepository(database, initialize=False)
    items_before = repository.list_items(account_id)
    service = AtomicProfileService(
        FourDimensionProfileService(
            SqliteProfileRepository(database),
            SqliteFourDimensionProfileRepository(database, initialize=False),
        ),
        repository,
    )
    adopted = service.compile_adopted_slice(
        account_id, run_id=REHEARSAL_RUN_ID
    )

    degraded = GlobalWritingPolicyCompiler(resource=None)
    baseline = degraded.compile("companion", user_text="你好")
    reused = degraded.compile("companion", existing_snapshot=baseline)

    recovered = GlobalWritingPolicyCompiler().compile(
        "companion", user_text="你好", adopted_slice=adopted
    )
    items_after = repository.list_items(account_id)

    checks = [
        RehearsalCheck(
            "expression_baseline_complete",
            baseline.snapshot_complete and bool(baseline.system_block),
            f"降级基线快照完整：version={baseline.version}",
        ),
        RehearsalCheck(
            "expression_retry_snapshot_reused",
            reused.system_block == baseline.system_block
            and reused.version == baseline.version,
            "旧任务重试原样复用快照，不因策略回滚漂移。",
        ),
        RehearsalCheck(
            "profile_items_preserved",
            [item.profile_item_id for item in items_before]
            == [item.profile_item_id for item in items_after],
            f"画像条目回滚前后一致：{len(items_before)} 条。",
        ),
        RehearsalCheck(
            "profile_adoption_recovered",
            recovered.profile_slice_id == adopted.slice_id,
            f"资源恢复后重新采用同一快照：{adopted.slice_id}。",
        ),
    ]
    return tuple(checks)


def rehearsal_deterministic_protection() -> tuple[RehearsalCheck, ...]:
    """确定性保护由代码注册，提示策略回滚不撤销。"""

    restored = restore_protected_regions(
        _PROTECTED_ORIGINAL, _PROTECTED_CANDIDATE
    )
    fragments = (
        "`result = 42`",
        "$E=mc^2$",
        "https://example.com/a?q=1",
        '{"answer": 42}',
    )
    missing = [fragment for fragment in fragments if fragment not in restored]
    return (
        RehearsalCheck(
            "fact_protection_protocol_registered",
            FACT_PROTECTION_PROTOCOL_VERSION == "intent-bound-fragment-v1",
            f"协议版本常量固定：{FACT_PROTECTION_PROTOCOL_VERSION}。",
        ),
        RehearsalCheck(
            "fact_protection_still_binds_fragments",
            not missing,
            "保护区恢复不依赖提示策略快照；缺失片段："
            + (", ".join(missing) if missing else "无"),
        ),
    )


def rehearsal_stop_new_writes(
    database: BridgesDatabase,
    account_id: str,
    before: Mapping[str, int],
) -> tuple[RehearsalCheck, ...]:
    """停新写后复核库完整、表可读、清单不缩水。"""

    integrity = database.connection.execute("PRAGMA integrity_check").fetchone()[0]
    after = snapshot_inventory(database, account_id)
    shrunk = {
        table: (before[table], after[table])
        for table in INVENTORY_TABLES
        if after[table] < before[table]
    }
    unreadable: list[str] = []
    for table in INVENTORY_TABLES:
        try:
            database.connection.execute(
                f"SELECT 1 FROM {table} WHERE account_id = ? LIMIT 1",
                (account_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001 - 演练要把不可读表如实登记
            unreadable.append(table)
    return (
        RehearsalCheck(
            "database_integrity_ok",
            str(integrity) == "ok",
            f"PRAGMA integrity_check={integrity}。",
        ),
        RehearsalCheck(
            "registered_tables_readable",
            not unreadable,
            "全部登记表可读；不可读："
            + (", ".join(unreadable) if unreadable else "无"),
        ),
        RehearsalCheck(
            "no_data_loss_after_stop",
            not shrunk,
            "回滚不清库、不丢任务/事件/产物；缩水：" + (str(shrunk) if shrunk else "无"),
        ),
    )


def rehearsal_retired_capabilities(
    database: BridgesDatabase,
) -> tuple[RehearsalCheck, ...]:
    """重跑退役清理保持幂等，不恢复退役媒体/提醒/插件启用态。"""

    first = retire_user_extensions(database)
    reminders = run_reminder_retirement(
        database=database,
        credential_store=InMemoryCredentialStore(namespace="smtp"),
    )
    second = retire_user_extensions(database)
    skills_active = int(
        database.connection.execute(
            "SELECT COUNT(*) AS count FROM skill_packages WHERE status <> 'disabled'"
        ).fetchone()["count"]
    )
    servers_enabled = int(
        database.connection.execute(
            "SELECT COUNT(*) AS count FROM mcp_servers "
            "WHERE status <> 'disabled' OR enabled <> 0"
        ).fetchone()["count"]
    )
    reminders_active = int(
        database.connection.execute(
            "SELECT COUNT(*) AS count FROM reminders "
            "WHERE status IN ('enabled', 'paused')"
        ).fetchone()["count"]
    )
    reminders_history = int(
        database.connection.execute(
            "SELECT COUNT(*) AS count FROM reminders WHERE status = 'retired'"
        ).fetchone()["count"]
    )
    return (
        RehearsalCheck(
            "user_extensions_remain_disabled",
            skills_active == 0 and servers_enabled == 0,
            f"技能/插件未复活：{skills_active}；MCP 未启用：{servers_enabled}。",
        ),
        RehearsalCheck(
            "reminders_remain_retired",
            reminders_active == 0,
            f"提醒未复活：{reminders_active} 条启用；历史保留 {reminders_history} 条。",
        ),
        RehearsalCheck(
            "retirement_idempotent",
            first["status"] == "completed"
            and second["status"] == "completed"
            and reminders["status"] in {"completed", "retryable"},
            f"退役清理可重跑：{first}；{reminders['status']}。",
        ),
    )


def run_rollback_rehearsal(
    database: BridgesDatabase, account_id: str
) -> RollbackRehearsalReport:
    """执行完整回滚演练并返回可复核报告。"""

    before = snapshot_inventory(database, account_id)
    checks: list[RehearsalCheck] = []
    checks.extend(rehearsal_expression_rollback(database, account_id))
    checks.extend(rehearsal_deterministic_protection())
    checks.extend(rehearsal_stop_new_writes(database, account_id, before))
    checks.extend(rehearsal_retired_capabilities(database))
    after = snapshot_inventory(database, account_id)
    return RollbackRehearsalReport(
        checks=tuple(checks),
        inventory_before=before,
        inventory_after=after,
    )
