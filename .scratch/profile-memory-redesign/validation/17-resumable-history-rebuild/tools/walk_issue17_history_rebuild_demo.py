"""画像工单 17 走查：可恢复历史重整（确定性）。

覆盖六个维护者旅程（真实 SQLite 实例，不调用外部模型、不读取任何凭据）：

[1] 启动与隔离：过期清单拒绝启动；新鲜清单建立已验证备份 + 工作副本，
    分母/跳过原因与预览清单一致；重复启动幂等、窗口冲突拒绝；源库只读
    （文件摘要与业务行计数不变、源库零抽取运行）。
[2] 有界推进与崩溃恢复：一次 run 至多消耗 max_ticks 次模型调用；进程重入
    （重新装配运行）后继续推进；完成后重复运行返回 ticks=0 且不重复生效
    （每个来源一个成功运行、一条事实）。
[3] 窗口范围：--start-at 只播种窗口内消息，窗口外消息不进台账；窗口绑定
    重建标识（不同窗口 = 不同批次）。
[4] 版本不符拒恢复；终态分类诚实：重试耗尽如实记为失败（不当作成功），
    提交前屏障（来源失效/隐私/版本）记为跳过而非失败。
[5] 完整对账：事实/证据/控制/派生/导出/删除/恢复全部通过并写盘；人工条目
    被篡改时对账失败；权威来源前进只记警告（冻结副本语义）。
[6] 清理语义：未收敛拒绝清理；完成后保留恢复前提；purge 删除全部产物且
    重复清理幂等。

用法：
    在仓根执行本文件（tools/walk_issue17_history_rebuild_demo.py），以 --out
    指定证据输出路径，例如 evidence/demo-walkthrough-<stamp>.txt。

脚本只使用临时库（`.tmp/issue17-demo-*` 与同目录工作区），不读取、不修改
任何真实数据或凭据；运行目录保留供复核，清理命令在证据末尾给出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pydantic import SecretStr  # noqa: E402

from bridges.config import Settings  # noqa: E402
from bridges.profiles.automatic import RuleBasedAutomaticProfileExtractor  # noqa: E402
from bridges.profiles.history_preview import preview_history  # noqa: E402
from bridges.profiles.history_rebuild import (  # noqa: E402
    cleanup_rebuild,
    rebuild_status,
    start_rebuild,
)
from bridges.profiles.history_rebuild_contracts import (  # noqa: E402
    ProfileRebuildError,
    RebuildWindow,
    rebuild_id_for,
)
from bridges.profiles.history_rebuild_drive import run_rebuild  # noqa: E402
from bridges.profiles.history_rebuild_reconcile import reconcile_rebuild  # noqa: E402
from bridges.profiles.replay import resolve_authoritative_database  # noqa: E402
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
TS = "2026-08-12T00:00:00+00:00"
TS2 = "2026-09-01T00:00:00+00:00"
OLDER = "2026-01-01T00:00:00+00:00"
FIXED_NOW = datetime(2026, 10, 16, 4, 0, 0, tzinfo=UTC)


class _Script:
    def __init__(self, out: list[str]) -> None:
        self._out = out
        self.failures = 0

    def say(self, text: str) -> None:
        print(text)
        self._out.append(text)

    def check(self, ok: bool, label: str) -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        self.say(f"    [{status}] {label}")


def _settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr(f"sqlite:///{path.as_posix()}"),
    )


def _authority(database_path: Path):
    return resolve_authoritative_database(settings=_settings(database_path))


def _preview(database_path: Path, account_id: str = ACCOUNT):
    return preview_history(
        _authority(database_path), account_id=account_id, now=FIXED_NOW
    )


def _rule_factory():
    def factory(database: object, neighbor: object, memory: object) -> object:
        del database, neighbor, memory
        return RuleBasedAutomaticProfileExtractor()

    return factory


def _seed_account(database: BridgesDatabase, account_id: str) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO accounts (account_id, email, created_at) VALUES (?, ?, ?)",
            (account_id, f"{account_id}@example.com", TS),
        )
        database.connection.execute(
            "INSERT INTO conversations "
            "(conversation_id, account_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, '', 'companion', ?, ?)",
            (f"conversation-{account_id}", account_id, TS, TS),
        )


def _seed_message(
    database: BridgesDatabase,
    account_id: str,
    message_id: str,
    content: str,
    *,
    role: str = "user",
    status: str = "done",
    created_at: str = TS,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO messages "
            "(message_id, conversation_id, account_id, role, attempt_number, "
            "status, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                message_id,
                f"conversation-{account_id}",
                account_id,
                role,
                status,
                content,
                created_at,
                created_at,
            ),
        )


def _seed_item(
    database: BridgesDatabase,
    account_id: str,
    item_id: str,
    text: str,
    *,
    status: str = "active",
    origin: str = "automatic",
    sources: tuple[str, ...] = (),
    quotes: dict[str, str] | None = None,
    stored_identity_key: str | None = None,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_items "
            "(profile_item_id, account_id, text, identity_key, fact_key, "
            "source_message_ids_json, source_quotes_json, status, write_origin, "
            "confidence, version, created_at, updated_at, user_edited_at, "
            "usage_purpose) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'medium', 1, ?, ?, NULL, NULL)",
            (
                item_id,
                account_id,
                text,
                stored_identity_key or f"key-{item_id}",
                json.dumps(list(sources), ensure_ascii=False),
                json.dumps(quotes or {}, ensure_ascii=False),
                status,
                origin,
                TS,
                TS,
            ),
        )


def _seed_persona(database: BridgesDatabase) -> dict[str, str]:
    """合成旧库：人工、可核验自动、缺证据、撤回与全部门控屏障。"""

    _seed_account(database, ACCOUNT)
    messages = {
        "hobby": "msg-alice-hobby",
        "goal": "msg-alice-goal",
        "cat": "msg-alice-cat",
        "stopped": "msg-alice-stopped",
        "empty": "msg-alice-empty",
        "tombstoned": "msg-alice-tombstoned",
        "blocked": "msg-alice-blocked",
        "withdrawn": "msg-alice-withdrawn",
        "assistant": "msg-alice-assistant",
        "missing": "msg-alice-deleted-source",
        "old": "msg-alice-old",
    }
    _seed_message(database, ACCOUNT, messages["hobby"], "我喜欢跑步，每周跑三次。")
    _seed_message(database, ACCOUNT, messages["goal"], "我在准备考研，目标是北航。", created_at=TS2)
    _seed_message(database, ACCOUNT, messages["cat"], "我养了一只猫叫团子")
    _seed_message(
        database, ACCOUNT, messages["stopped"], "这是一个被停止的回合", status="stopped"
    )
    _seed_message(database, ACCOUNT, messages["empty"], "   ")
    _seed_message(database, ACCOUNT, messages["tombstoned"], "这段历史已经被删除")
    _seed_message(database, ACCOUNT, messages["blocked"], "这段内容被禁止记录")
    _seed_message(database, ACCOUNT, messages["withdrawn"], "我其实搬去了上海")
    _seed_message(
        database, ACCOUNT, messages["assistant"], "好的，我记住了", role="assistant"
    )
    _seed_message(database, ACCOUNT, messages["old"], "我过去住在南京", created_at=OLDER)
    items = {
        "manual": "item-alice-manual",
        "auto_ok": "item-alice-auto-ok",
        "auto_missing": "item-alice-auto-missing",
        "withdrawn": "item-alice-withdrawn",
        "superseded": "item-alice-superseded",
    }
    _seed_item(database, ACCOUNT, items["manual"], "我叫小林", origin="user")
    _seed_item(
        database,
        ACCOUNT,
        items["auto_ok"],
        "我养了一只猫叫团子",
        sources=(messages["cat"],),
        quotes={messages["cat"]: "我养了一只猫叫团子"},
    )
    _seed_item(
        database,
        ACCOUNT,
        items["auto_missing"],
        "我在读大二",
        sources=(messages["missing"],),
    )
    _seed_item(
        database,
        ACCOUNT,
        items["withdrawn"],
        "",
        status="withdrawn",
        stored_identity_key="withdrawn-identity-alice",
        sources=(messages["withdrawn"],),
    )
    _seed_item(database, ACCOUNT, items["superseded"], "我在读大一", status="superseded")
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            (ACCOUNT, messages["tombstoned"], TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'content', ?, ?)",
            ("block-alice", ACCOUNT, "这段内容被禁止记录", TS),
        )
    return messages | items


def _open_database(path: Path) -> BridgesDatabase:
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    return database


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _business_row_counts(path: Path, account_id: str) -> dict[str, int]:
    database = BridgesDatabase(path)
    try:
        tables = (
            "profile_items",
            "profile_extraction_runs",
            "profile_extraction_tasks",
            "profile_extraction_observations",
            "profile_extraction_tombstones",
            "profile_extraction_privacy_blocks",
            "profile_account_controls",
            "profile_item_feedback",
            "profile_rebuild_runs",
            "profile_rebuild_items",
        )
        counts: dict[str, int] = {}
        for table in tables:
            row = database.connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            counts[table] = 0 if row is None else int(row["total"])
        return counts
    finally:
        database.close()


def journey_1_start_isolation(script: _Script, run_dir: Path) -> Path:
    script.say("[1] 启动与隔离：清单新鲜度、备份 + 工作副本、幂等与窗口冲突")
    database_path = run_dir / "runtime" / "bridges.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = _open_database(database_path)
    _seed_persona(database)
    database.close()

    before_digest = _file_digest(database_path)
    before_counts = _business_row_counts(database_path, ACCOUNT)
    manifest = _preview(database_path)
    authority = _authority(database_path)
    work = run_dir / "work"

    database = _open_database(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-late", "我后来搬去了杭州")
    database.close()
    try:
        start_rebuild(authority, manifest, work_dir=work)
        stale_code = None
    except ProfileRebuildError as exc:
        stale_code = exc.code
    script.check(
        stale_code == "preview_stale" and not work.exists(),
        "来源已变化时过期清单拒绝启动（未建立工作目录）",
    )

    database = _open_database(database_path)
    with database.transaction():
        database.connection.execute(
            "DELETE FROM messages WHERE message_id = 'msg-alice-late'"
        )
    database.close()
    before_digest = _file_digest(database_path)
    before_counts = _business_row_counts(database_path, ACCOUNT)
    manifest = _preview(database_path)

    report = start_rebuild(authority, manifest, work_dir=work, now=FIXED_NOW)
    progress = report["progress"]
    chat = manifest.chat_scope
    script.say(
        f"    启动：rebuild_id={report['rebuild_id']}，"
        f"窗口总数 {progress['window_total']}，可用 {progress['eligible']}，"
        f"跳过 {progress['skipped']}（{progress['skipped_by_reason']}）"
    )
    script.check(
        report["reused"] is False
        and progress["window_total"] == chat.user_messages_total == 9
        and progress["eligible"] == chat.eligible == 4
        and progress["skipped"] == 5
        and progress["skipped_by_reason"]
        == {
            "content_blocked": 1,
            "empty": 1,
            "tombstoned": 1,
            "unfinished": 1,
            "withdrawn_suppressed": 1,
        },
        "分母与跳过原因和预览清单逐项一致（旧消息在窗口内、门控屏障计数一致）",
    )
    script.check(
        (work / "rebuild.json").exists()
        and (work / "preview.json").exists()
        and (work / "history.db").exists()
        and (work / "working" / "bridges.db").exists(),
        "工作目录含元数据、清单副本、已验证备份与工作副本",
    )
    again = start_rebuild(authority, manifest, work_dir=work, now=FIXED_NOW)
    script.check(
        again["reused"] is True and again["rebuild_id"] == report["rebuild_id"],
        "重复启动幂等：返回同一批次，不重建副本",
    )
    try:
        start_rebuild(
            authority,
            manifest,
            work_dir=work,
            window=RebuildWindow(TS2, None),
            now=FIXED_NOW,
        )
        conflict_code = None
    except ProfileRebuildError as exc:
        conflict_code = exc.code
    script.check(
        conflict_code == "work_dir_conflict",
        "同一工作目录绑定另一窗口/标识时拒绝启动",
    )
    script.check(
        _file_digest(database_path) == before_digest
        and _business_row_counts(database_path, ACCOUNT) == before_counts
        and _business_row_counts(database_path, ACCOUNT)["profile_extraction_runs"]
        == 0,
        "源库仍为启动前状态：文件摘要与业务行计数不变、零抽取运行（隔离执行）",
    )
    return database_path


def journey_2_bounded_resume(script: _Script, database_path: Path, run_dir: Path) -> Path:
    script.say("[2] 有界推进与崩溃恢复：一次 run 有上限；重入继续；完成即幂等")
    work = run_dir / "work"

    first = run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    script.say(
        f"    第一次 run：ticks={first['ticks']}（上限 {first['max_ticks']}），"
        f"已抽取 {first['progress']['extracted']}，未完成"
    )
    script.check(
        first["ticks"] == 1
        and first["reached_budget"] is True
        and first["completed"] is False
        and first["progress"]["extracted"] == 1,
        "单次 run 的模型调用次数受 max_ticks 硬限制",
    )

    report = first
    runs = 1
    while not report["completed"] and runs < 5:
        report = run_rebuild(
            work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
        )
        runs += 1
    progress = report["progress"]
    script.say(
        f"    重入 {runs} 次后：completed={report['completed']}，"
        f"抽取 {progress['extracted']}，写入事实 {progress['committed']}，"
        f"失败 {progress['failed']}，跳过 {progress['skipped']}"
    )
    script.check(
        report["completed"] is True
        and progress["extracted"] == 4
        and progress["committed"] == 2
        and progress["failed"] == 0
        and progress["pending"] == 0
        and progress["scheduled"] == 0,
        "分进程重入持续推进直至收敛，分母全部落终态",
    )

    finished = run_rebuild(
        work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    script.check(
        finished["completed"] is True and finished["ticks"] == 0,
        "终态后重复 run 不再消耗模型调用（ticks=0）",
    )

    working = _open_database(work / "working" / "bridges.db")
    hobby_runs = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_extraction_runs"
        " WHERE account_id = ? AND message_id = 'msg-alice-hobby' AND status = 'succeeded'",
        (ACCOUNT,),
    ).fetchone()
    hobby_items = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_items"
        " WHERE account_id = ? AND source_message_ids_json LIKE '%msg-alice-hobby%'",
        (ACCOUNT,),
    ).fetchone()
    working.close()
    script.check(
        int(hobby_runs["total"]) == 1 and int(hobby_items["total"]) == 1,
        "幂等键（账户/消息/抽取器版本/来源哈希）保证重启不重复生效",
    )
    return work


def journey_3_window(script: _Script, database_path: Path, run_dir: Path) -> None:
    script.say("[3] 窗口范围：--start-at 只播种窗口内消息；窗口绑定批次标识")
    work = run_dir / "work-window"
    manifest = _preview(database_path)
    report = start_rebuild(
        _authority(database_path),
        manifest,
        work_dir=work,
        window=RebuildWindow(TS, None),
        now=FIXED_NOW,
    )
    progress = report["progress"]
    script.say(
        f"    窗口 [2026-08-12 起)：窗口总数 {progress['window_total']}（全量 9），"
        f"可用 {progress['eligible']}"
    )
    script.check(
        progress["window_total"] == 8
        and progress["eligible"] == 3
        and progress["window_total"] < manifest.chat_scope.user_messages_total,
        "窗口外旧消息不进入本批台账",
    )
    working = _open_database(work / "working" / "bridges.db")
    old_rows = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_rebuild_items WHERE message_id = ?",
        ("msg-alice-old",),
    ).fetchone()
    window_row = working.connection.execute(
        "SELECT window_start_at FROM profile_rebuild_runs WHERE rebuild_id = ?",
        (report["rebuild_id"],),
    ).fetchone()
    working.close()
    script.check(
        int(old_rows["total"]) == 0 and str(window_row["window_start_at"]) == TS,
        "窗口外消息零行；运行台账记录窗口下界",
    )

    unbounded = rebuild_id_for(
        account_id=ACCOUNT,
        state_digest=manifest.state_digest,
        policy_version="p",
        preview_version=manifest.manifest_version,
        window=RebuildWindow(None, None),
    )
    bounded = rebuild_id_for(
        account_id=ACCOUNT,
        state_digest=manifest.state_digest,
        policy_version="p",
        preview_version=manifest.manifest_version,
        window=RebuildWindow(TS, TS2),
    )
    script.check(
        unbounded != bounded and unbounded.startswith("profile-rebuild-"),
        "不同窗口得到不同批次标识（同库可并存多个有界批次）",
    )


def journey_4_versions_and_barriers(script: _Script, run_dir: Path) -> None:
    script.say("[4] 版本不符拒恢复；重试耗尽如实失败；屏障跳过不算失败；中途墓碑挡迟到写入")
    work = run_dir / "work-barriers"
    database_path = run_dir / "runtime" / "bridges.db"
    manifest = _preview(database_path)
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)
    run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)

    working = _open_database(work / "working" / "bridges.db")
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_rebuild_runs SET extractor_version = 'profile-auto-v2+signal-other'"
        )
    working.close()
    try:
        run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
        mismatch_code = None
    except ProfileRebuildError as exc:
        mismatch_code = exc.code
    script.check(
        mismatch_code == "rebuild_version_mismatch",
        "抽取器/策略版本变化后禁止恢复旧台账（要求重新预览新批次）",
    )

    work2 = run_dir / "work-barriers2"
    start_rebuild(_authority(database_path), manifest, work_dir=work2, now=FIXED_NOW)
    run_rebuild(work2, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    working = _open_database(work2 / "working" / "bridges.db")
    scheduled = {
        str(row["message_id"])
        for row in working.connection.execute(
            "SELECT message_id FROM profile_rebuild_items WHERE status = 'scheduled'"
        ).fetchall()
    }
    script.check(
        {"msg-alice-goal", "msg-alice-hobby"} <= scheduled,
        "前置：目标与爱好两条仍在途",
    )
    with working.transaction():
        task = working.connection.execute(
            "SELECT task_id FROM profile_extraction_tasks WHERE message_id = ?",
            ("msg-alice-goal",),
        ).fetchone()
        working.connection.execute(
            "UPDATE task_claims SET status = 'failed', next_retry_at = NULL WHERE task_key = ?",
            (str(task["task_id"]),),
        )
        working.connection.execute(
            "UPDATE profile_extraction_runs SET status = 'exhausted',"
            " last_error = 'profile_extraction_version_unavailable'"
            " WHERE message_id = 'msg-alice-hobby'"
        )
    working.close()

    report = run_rebuild(work2, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW)
    progress = report["progress"]
    script.say(
        f"    终态分类：失败 {progress['failed']}，"
        f"屏障跳过 {progress['skipped_by_reason'].get('version_unavailable', 0)}，"
        f"完成 {report['completed']}"
    )
    script.check(
        report["completed"] is True
        and progress["failed"] == 1
        and progress["skipped_by_reason"].get("version_unavailable") == 1
        and progress["extracted"] == 2
        and progress["committed"] == 0,
        "重试耗尽记为失败（不冒充成功水位）；屏障记为跳过（不算失败）",
    )

    work3 = run_dir / "work-barriers3"
    start_rebuild(_authority(database_path), manifest, work_dir=work3, now=FIXED_NOW)
    run_rebuild(work3, extractor_factory=_rule_factory(), max_ticks=1, now=FIXED_NOW)
    working = _open_database(work3 / "working" / "bridges.db")
    with working.transaction():
        working.connection.execute(
            "INSERT INTO profile_extraction_tombstones"
            " (account_id, message_id, created_at) VALUES (?, ?, ?)",
            (ACCOUNT, "msg-alice-goal", TS),
        )
    working.close()

    report = run_rebuild(
        work3, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW
    )
    progress = report["progress"]
    working = _open_database(work3 / "working" / "bridges.db")
    victim = working.connection.execute(
        "SELECT status, skip_reason FROM profile_rebuild_items WHERE message_id = ?",
        ("msg-alice-goal",),
    ).fetchone()
    leaked = working.connection.execute(
        "SELECT COUNT(*) AS total FROM profile_items"
        " WHERE account_id = ? AND status = 'active' AND source_message_ids_json LIKE ?",
        (ACCOUNT, "%msg-alice-goal%"),
    ).fetchone()
    working.close()
    script.check(
        report["completed"] is True
        and victim is not None
        and str(victim["status"]) == "skipped"
        and str(victim["skip_reason"]) == "source_invalidated"
        and progress["skipped_by_reason"].get("source_invalidated") == 1
        and progress["failed"] == 0,
        "重整期间出现墓碑：提交前复核拒绝迟到写入，如实记为屏障跳过",
    )
    script.check(
        leaked is not None and int(leaked["total"]) == 0,
        "被墓碑阻止的消息没有落地任何有效事实",
    )


def journey_5_reconcile(script: _Script, run_dir: Path, database_path: Path) -> None:
    script.say("[5] 完整对账与演练：全部通过并写盘；篡改检出；来源前进仅警告")
    work = run_dir / "work"
    before = _business_row_counts(database_path, ACCOUNT)
    report = reconcile_rebuild(
        work,
        extractor_factory=_rule_factory(),
        authority=_authority(database_path),
        now=FIXED_NOW,
    )
    facts = report["facts"]
    script.say(
        f"    对账：passed={report['passed']}，"
        f"人工条目 {facts['manual_items_intact']}/{facts['manual_items_expected']}，"
        f"导出 {report['export']['tables']} 表，"
        f"删除演练残留 {len(report['deletion']['residual'])}，"
        f"恢复演练 {report['restore']['restore_verified']}"
    )
    script.check(
        report["passed"] is True
        and report["errors"] == []
        and report["evidence"]["known_unverified_refs"]
        == ["item-alice-auto-missing:msg-alice-deleted-source"]
        and report["deletion"]["residual"] == {}
        and report["deletion"]["account_row_left"] is False
        and report["restore"]["backup_verified"] is True
        and report["restore"]["restore_verified"] is True
        and report["restore"]["logical_summary_mismatches"] == {}
        and (work / "reconciliation.json").exists()
        and rebuild_status(work)["reconciled_at"] is not None,
        "事实/证据/控制/派生/导出/删除/恢复逐项通过；既有缺证据条目按标记对待",
    )
    script.check(
        _business_row_counts(database_path, ACCOUNT) == before,
        "演练只在副本上执行：源库业务行计数不变",
    )

    working = _open_database(work / "working" / "bridges.db")
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_items SET text = '我叫小李' WHERE profile_item_id = 'item-alice-manual'"
        )
    working.close()
    tampered = reconcile_rebuild(
        work, extractor_factory=_rule_factory(), authority=_authority(database_path)
    )
    checks = {str(error["check"]) for error in tampered["errors"]}
    script.check(
        tampered["passed"] is False and "manual_items_preserved" in checks,
        "人工条目被改写后对账失败（人工权威不可被动摇）",
    )
    working = _open_database(work / "working" / "bridges.db")
    with working.transaction():
        working.connection.execute(
            "UPDATE profile_items SET text = '我叫小林' WHERE profile_item_id = 'item-alice-manual'"
        )
    working.close()

    database = _open_database(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-forward", "我又开始练吉他")
    database.close()
    drifted = reconcile_rebuild(
        work, extractor_factory=_rule_factory(), authority=_authority(database_path)
    )
    script.check(
        drifted["passed"] is True
        and drifted["source_drift"]["checked"] is True
        and drifted["source_drift"]["fresh"] is False
        and any("已前进" in warning for warning in drifted["warnings"]),
        "权威来源在冻结期间前进：只记警告（切换由运行手册单独确认）",
    )


def journey_6_cleanup(script: _Script, run_dir: Path) -> None:
    script.say("[6] 清理语义：未收敛拒绝；完成后保留恢复前提；purge 幂等")
    work = run_dir / "work-cleanup"
    database_path = run_dir / "runtime" / "bridges.db"
    manifest = _preview(database_path)
    start_rebuild(_authority(database_path), manifest, work_dir=work, now=FIXED_NOW)
    try:
        cleanup_rebuild(work)
        unmet_code = None
    except ProfileRebuildError as exc:
        unmet_code = exc.code
    script.check(unmet_code == "rebuild_not_settled", "未收敛的批次拒绝清理")

    run_rebuild(work, extractor_factory=_rule_factory(), max_ticks=100, now=FIXED_NOW)
    cleaned = cleanup_rebuild(work)
    script.check(
        cleaned["removed"] == [str(work / "working")]
        and (work / "history.db").exists()
        and (work / "preview.json").exists()
        and (work / "rebuild.json").exists(),
        "完成后清理工作副本与台账，保留已验证备份、清单与记录（可审计/可恢复）",
    )
    script.check(cleanup_rebuild(work)["removed"] == [], "重复清理幂等")

    purged = cleanup_rebuild(work, purge=True)
    script.check(
        purged["purged"] is True
        and not (work / "history.db").exists()
        and not (work / "preview.json").exists()
        and not (work / "rebuild.json").exists(),
        "--purge 删除恢复前提与元数据（维护者显式放弃）",
    )
    try:
        rebuild_status(work)
        missing_code = None
    except ProfileRebuildError as exc:
        missing_code = exc.code
    script.check(
        missing_code == "rebuild_metadata_missing",
        "purge 后状态查询显式失败（不制造幽灵批次）",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 17 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        f"SCHEMA_VERSION={SCHEMA_VERSION}；临时库 .tmp/issue17-demo-{stamp}"
    )

    run_dir = REPO_ROOT / ".tmp" / f"issue17-demo-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    script.say("")
    database_path = journey_1_start_isolation(script, run_dir)
    script.say("")
    journey_2_bounded_resume(script, database_path, run_dir)
    script.say("")
    journey_3_window(script, database_path, run_dir)
    script.say("")
    journey_4_versions_and_barriers(script, run_dir)
    script.say("")
    journey_5_reconcile(script, run_dir, database_path)
    script.say("")
    journey_6_cleanup(script, run_dir)

    script.say("")
    script.say(f"运行目录（保留供复核）：{run_dir}")
    script.say(
        "数据清理：确认无需复核后删除该目录（"
        f"Remove-Item -LiteralPath '{run_dir}' -Recurse -Force）；"
        "其中仅含本脚本生成的合成数据。"
    )
    script.say(f"结果：{'全部通过' if script.failures == 0 else f'{script.failures} 项失败'}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
