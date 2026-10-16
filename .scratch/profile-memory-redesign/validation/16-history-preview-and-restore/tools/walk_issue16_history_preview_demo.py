"""画像工单 16 走查：历史只读预览、可恢复备份与来源漂移（确定性）。

覆盖六个维护者旅程（真实 SQLite 实例，不调用外部模型、不读取任何凭据）：

[1] 权威来源确认与只读预览：合成旧库（全部条目类别 + 隔离账户），预览两次
    结果一致，库文件摘要与业务行计数不变、无抽取运行；清单不含私人原文。
[2] 错误数据源/版本/账户显式拒绝：路径冲突、旧 schema（拒绝且不偷偷升级）、
    未来 schema、未知账户全部硬门失败。
[3] 来源漂移检测：新增消息、修改来源、控制变化都会让清单过期；重新预览后
    重新新鲜。
[4] 可恢复备份与隔离开演：创建备份（不覆盖唯一副本）、写入恢复记录、隔离
    副本实际打开并对账；对同一备份可重复演练。
[5] 失败恢复：过期清单拒绝备份；内容漂移的备份在演练中显式失败；缺少
    manifest 的孤儿备份文件拒绝恢复；原始备份始终不受影响。
[6] 记录开关与来源范围：账号级暂停记录后 eligible 归零；非聊天来源（材料
    类）条目落入「来源无法核验」，不默认可提取。

用法：
    在仓根执行本文件（tools/walk_issue16_history_preview_demo.py），以 --out
    指定证据输出路径，例如 evidence/demo-walkthrough-<stamp>.txt。

脚本只使用临时库（`.tmp/issue16-demo-*`），不读取、不修改任何真实数据或凭据；
运行目录保留供复核，清理命令在证据末尾给出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pydantic import SecretStr  # noqa: E402

from bridges.config import Settings  # noqa: E402
from bridges.profiles.atomic import identity_key  # noqa: E402
from bridges.profiles.history_preview import (  # noqa: E402
    ProfileHistoryError,
    create_history_backup,
    preview_history,
    verify_history_restore,
    verify_preview_manifest,
)
from bridges.profiles.replay import (  # noqa: E402
    ProfileReplayError,
    create_verified_backup,
    resolve_authoritative_database,
)
from bridges.storage.database import SCHEMA_VERSION, BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
OTHER_ACCOUNT = "demo-bob"
TS = "2026-08-12T00:00:00+00:00"
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
                TS,
                TS,
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
    usage_purpose: str | None = None,
    stored_identity_key: str | None = None,
) -> None:
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_items "
            "(profile_item_id, account_id, text, identity_key, fact_key, "
            "source_message_ids_json, source_quotes_json, status, write_origin, "
            "confidence, version, created_at, updated_at, user_edited_at, "
            "usage_purpose) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'medium', 1, ?, ?, NULL, ?)",
            (
                item_id,
                account_id,
                text,
                stored_identity_key or identity_key(account_id, text),
                json.dumps(list(sources), ensure_ascii=False),
                json.dumps(quotes or {}, ensure_ascii=False),
                status,
                origin,
                TS,
                TS,
                usage_purpose,
            ),
        )


def _seed_persona(database: BridgesDatabase, account_id: str) -> dict[str, str]:
    """合成旧库：人工、可核验自动、缺证据、材料来源、敏感、凭据与屏障。"""

    _seed_account(database, account_id)
    prefix = account_id.rsplit("-", 1)[-1]
    messages = {
        "cat": f"msg-{prefix}-cat",
        "tombstoned": f"msg-{prefix}-tombstoned",
        "stopped": f"msg-{prefix}-stopped",
        "blocked": f"msg-{prefix}-blocked",
        "withdrawn": f"msg-{prefix}-withdrawn",
        "eligible": f"msg-{prefix}-eligible",
        "assistant": f"msg-{prefix}-assistant",
        "missing": f"msg-{prefix}-deleted-source",
    }
    _seed_message(database, account_id, messages["cat"], "我养了一只猫叫团子")
    _seed_message(database, account_id, messages["tombstoned"], "这段历史已经被删除")
    _seed_message(
        database, account_id, messages["stopped"], "这是一个被停止的回合", status="stopped"
    )
    _seed_message(database, account_id, messages["blocked"], "这段内容被禁止记录")
    _seed_message(database, account_id, messages["withdrawn"], "我其实搬去了上海")
    _seed_message(database, account_id, messages["eligible"], "我平时喜欢跑步")
    _seed_message(
        database, account_id, messages["assistant"], "好的，我记住了", role="assistant"
    )
    items = {
        "manual": f"item-{prefix}-manual",
        "auto_ok": f"item-{prefix}-auto-ok",
        "auto_unverified": f"item-{prefix}-auto-unverified",
        "auto_partial": f"item-{prefix}-auto-partial",
        "auto_deleted": f"item-{prefix}-auto-deleted",
        "auto_no_source": f"item-{prefix}-auto-no-source",
        "sensitive_authorized": f"item-{prefix}-sensitive-ok",
        "sensitive_unauthorized": f"item-{prefix}-sensitive-no",
        "credential": f"item-{prefix}-credential",
        "withdrawn": f"item-{prefix}-withdrawn",
        "superseded": f"item-{prefix}-superseded",
    }
    _seed_item(database, account_id, items["manual"], "我叫小林", origin="user")
    _seed_item(
        database,
        account_id,
        items["auto_ok"],
        "我养了一只猫叫团子",
        sources=(messages["cat"],),
        quotes={messages["cat"]: "我养了一只猫叫团子"},
    )
    _seed_item(
        database,
        account_id,
        items["auto_unverified"],
        "我在读大二",
        sources=(f"material:resume:{prefix}~v1",),
    )
    _seed_item(
        database,
        account_id,
        items["auto_partial"],
        "我周末去爬山",
        sources=(messages["cat"], messages["missing"]),
    )
    _seed_item(
        database,
        account_id,
        items["auto_deleted"],
        "这段历史已经被删除",
        sources=(messages["tombstoned"],),
    )
    _seed_item(database, account_id, items["auto_no_source"], "这是没有来源的旧条目")
    _seed_item(
        database,
        account_id,
        items["sensitive_authorized"],
        "我有花粉过敏，户外活动要注意",
        origin="user",
        usage_purpose="户外建议",
    )
    _seed_item(
        database,
        account_id,
        items["sensitive_unauthorized"],
        "我最近有点焦虑",
        sources=(messages["eligible"],),
    )
    _seed_item(
        database, account_id, items["credential"], "我的密码是 abc12345", origin="user"
    )
    _seed_item(
        database,
        account_id,
        items["withdrawn"],
        "",
        status="withdrawn",
        stored_identity_key=identity_key(account_id, "我其实搬去了上海"),
        sources=(messages["withdrawn"],),
    )
    _seed_item(database, account_id, items["superseded"], "我在读大一", status="superseded")
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_tombstones "
            "(account_id, message_id, created_at) VALUES (?, ?, ?)",
            (account_id, messages["tombstoned"], TS),
        )
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'content', ?, ?)",
            (f"block-{prefix}", account_id, "这段内容被禁止记录", TS),
        )
        database.connection.execute(
            "INSERT INTO profile_item_feedback "
            "(feedback_id, account_id, profile_item_id, kind, note, item_version, "
            "created_at) VALUES (?, ?, ?, 'helpful', NULL, 1, ?)",
            (f"fb-{prefix}", account_id, items["auto_ok"], TS),
        )
    return messages | items


def _open_database(path: Path) -> BridgesDatabase:
    database = BridgesDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    return database


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_schema_version(path: Path) -> str | None:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        return None if row is None else str(row[0])
    finally:
        connection.close()


def _business_row_counts(path: Path, account_id: str) -> dict[str, int]:
    database = BridgesDatabase(path)
    try:
        tables = (
            "profile_items",
            "profile_extraction_runs",
            "profile_extraction_tombstones",
            "profile_extraction_privacy_blocks",
            "profile_item_feedback",
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


def journey_1_readonly_preview(script: _Script, run_dir: Path) -> Path:
    script.say("[1] 权威来源确认与只读预览（Alice 主账户 + Bob 隔离）")
    database_path = run_dir / "runtime" / "bridges.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = _open_database(database_path)
    keys = _seed_persona(database, ACCOUNT)
    bob_keys = _seed_persona(database, OTHER_ACCOUNT)
    database.close()

    before_digest = _file_digest(database_path)
    before_counts = _business_row_counts(database_path, ACCOUNT)

    manifest = _preview(database_path)
    second = _preview(database_path)
    script.say(
        f"    来源：{manifest.source_path}（schema {manifest.schema_version}，"
        f"writer_state={manifest.writer_state}）"
    )
    script.check(
        manifest.as_dict() == second.as_dict(),
        "重复预览在同一快照上结果一致",
    )
    script.check(
        _file_digest(database_path) == before_digest
        and _business_row_counts(database_path, ACCOUNT) == before_counts
        and _business_row_counts(database_path, ACCOUNT)[
            "profile_extraction_runs"
        ]
        == 0,
        "预览前后库文件摘要与业务行计数不变、无抽取运行（只读）",
    )

    items = manifest.items
    script.say(
        "    条目分类："
        + "，".join(
            f"{key}={value}" for key, value in items.count_dict().items() if value
        )
    )
    script.check(
        items.active_total == 9
        and items.manual_preserved == 3
        and items.automatic_verifiable == 2
        and items.automatic_partial_evidence == 1
        and items.automatic_deleted_evidence == 1
        and items.automatic_unverified_source == 1
        and items.automatic_missing_evidence == 1
        and items.sensitive_authorized == 1
        and items.sensitive_unauthorized == 1
        and items.credentials_flagged == 1
        and items.withdrawn == 1
        and items.superseded == 1,
        "人工/自动/部分/删除/来源无法核验/无证据/敏感/凭据/撤回/被替代分类符合合成库",
    )
    script.check(
        items.locators["withdrawn"] == [keys["withdrawn"]]
        and items.locators["automatic_deleted_evidence"] == [keys["auto_deleted"]]
        and items.locators["automatic_unverified_source"] == [keys["auto_unverified"]]
        and items.locators["sensitive_authorized"] == [keys["sensitive_authorized"]]
        and keys["credential"] in items.locators["credentials_flagged"],
        "定位标识指回对应条目（人工保留/删除证据/材料来源/敏感/凭据）",
    )

    chat = manifest.chat_scope
    script.say(
        f"    聊天范围：可用 {chat.eligible}/{chat.user_messages_total}"
        f"（墓碑 {chat.skipped_tombstoned}、未完成 {chat.skipped_unfinished}、"
        f"禁止记录 {chat.skipped_content_blocked}、撤回 {chat.skipped_withdrawn_suppressed}）"
    )
    script.check(
        chat.eligible == 2
        and chat.skipped_tombstoned == 1
        and chat.skipped_unfinished == 1
        and chat.skipped_content_blocked == 1
        and chat.skipped_withdrawn_suppressed == 1
        and chat.eligible_conversation_ids == (f"conversation-{ACCOUNT}",),
        "聊天范围与跳过原因符合合成库，且按账户限定会话",
    )
    controls = manifest.controls
    script.check(
        controls.recording_enabled is True
        and controls.usage_enabled is True
        and controls.content_block_count == 1
        and controls.tombstone_count == 1
        and manifest.domain_refs.feedback_count == 1,
        "控制开关、内容禁止与删除屏障计数、领域引用（反馈）可核对",
    )

    rendered = str(manifest.as_dict())
    private_texts = (
        "我养了一只猫叫团子",
        "我叫小林",
        "我有花粉过敏",
        "我最近有点焦虑",
        "abc12345",
        "我其实搬去了上海",
        "这段内容被禁止记录",
        "我在读大二",
    )
    script.check(
        all(text not in rendered for text in private_texts),
        "清单不含条目正文与消息正文（只输出计数与定位标识）",
    )

    bob = _preview(database_path, OTHER_ACCOUNT)
    alice_ids = {
        item_id for locators in items.locators.values() for item_id in locators
    }
    bob_ids = {
        item_id for locators in bob.items.locators.values() for item_id in locators
    }
    script.check(
        bob.state_digest != manifest.state_digest
        and bob_keys["manual"] not in alice_ids
        and keys["manual"] not in bob_ids
        and f"conversation-{OTHER_ACCOUNT}"
        not in manifest.chat_scope.eligible_conversation_ids,
        "预览按账户绑定：条目与会话不跨账户引用",
    )
    return database_path


def journey_2_rejections(script: _Script, database_path: Path, run_dir: Path) -> None:
    script.say("[2] 错误数据源/版本/账户显式拒绝（不偷偷升级或换库）")

    other = run_dir / "runtime" / "other" / "bridges.db"
    other.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(database_path, other)
    try:
        resolve_authoritative_database(
            settings=_settings(database_path), explicit_path=other
        )
        code = None
    except ProfileReplayError as exc:
        code = exc.code
    script.check(code == "database_path_conflict", "配置与显式路径冲突被拒")

    legacy = run_dir / "runtime" / "legacy-83.db"
    shutil.copyfile(database_path, legacy)
    connection = sqlite3.connect(str(legacy))
    try:
        with connection:
            connection.execute(
                "UPDATE schema_meta SET value = '83' WHERE key = 'version'"
            )
    finally:
        connection.close()
    try:
        _preview(legacy)
        legacy_code = None
    except ProfileHistoryError as exc:
        legacy_code = exc.code
    script.check(
        legacy_code == "schema_upgrade_required"
        and _read_schema_version(legacy) == "83",
        "旧 schema 被拒且文件仍为 83（预览不会隐式迁移）",
    )

    future = run_dir / "runtime" / "future-999.db"
    shutil.copyfile(database_path, future)
    connection = sqlite3.connect(str(future))
    try:
        with connection:
            connection.execute(
                "UPDATE schema_meta SET value = '999' WHERE key = 'version'"
            )
    finally:
        connection.close()
    try:
        _preview(future)
        future_code = None
    except ProfileHistoryError as exc:
        future_code = exc.code
    script.check(future_code == "schema_version_unsupported", "未来 schema 被拒")

    try:
        _preview(database_path, "account-nobody")
        account_code = None
    except ProfileHistoryError as exc:
        account_code = exc.code
    script.check(account_code == "account_missing", "未知账户被拒")


def journey_3_drift(script: _Script, database_path: Path, run_dir: Path):
    script.say("[3] 来源漂移检测：新增消息/修改来源/控制变化要求重算")
    authority = _authority(database_path)
    manifest = _preview(database_path)

    fresh = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    script.check(fresh.fresh is True, "未变化时校验为新鲜")

    database = _open_database(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-new", "我最近开始学吉他")
    database.close()
    drifted = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    script.say(
        "    新增消息后："
        f"user_messages={drifted.deltas['user_messages']}，"
        f"eligible={drifted.deltas['eligible_messages']}"
    )
    script.check(
        drifted.fresh is False
        and drifted.requires_recompute is True
        and drifted.deltas["user_messages"] == {"preview": 6, "current": 7}
        and drifted.deltas["eligible_messages"] == {"preview": 2, "current": 3},
        "新增消息被识别并要求重算受影响范围",
    )

    database = _open_database(database_path)
    with database.transaction():
        database.connection.execute(
            "UPDATE messages SET content = ? WHERE message_id = ?",
            ("我养了一只狗叫团子", "msg-alice-cat"),
        )
    database.close()
    edited = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    script.check(edited.fresh is False, "修改来源内容被识别为漂移")

    database = _open_database(database_path)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) VALUES (?, 0, ?, 1, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET profile_usage_enabled = 0",
            (ACCOUNT, "profile-controls-v1", TS),
        )
    database.close()
    controlled = verify_preview_manifest(authority, manifest, now=FIXED_NOW)
    script.check(
        controlled.deltas["controls_changed"] is True,
        "控制开关变化被识别为漂移",
    )

    database = _open_database(database_path)
    with database.transaction():
        database.connection.execute(
            "DELETE FROM profile_account_controls WHERE account_id = ?",
            (ACCOUNT,),
        )
    database.close()
    refreshed = _preview(database_path)
    again = verify_preview_manifest(authority, refreshed, now=FIXED_NOW)
    script.check(again.fresh is True, "重新预览后新清单重新新鲜")
    return refreshed


def journey_4_backup_and_drill(
    script: _Script, database_path: Path, run_dir: Path, manifest
):
    script.say("[4] 可恢复备份与隔离开演（实际打开并逐项对账）")
    backup_path = run_dir / "backup" / "history.db"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "drill-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    record = create_history_backup(
        _authority(database_path),
        backup_path,
        preview=manifest,
        work_dir=work_dir,
        now=FIXED_NOW,
    )
    record_path = Path(f"{backup_path}.history-record.json")
    script.say(
        f"    备份：{backup_path}（{record.backup['size_bytes']} 字节，"
        f"schema {record.backup['schema_version']}）"
    )
    script.check(
        record.verified is True
        and backup_path.exists()
        and Path(f"{backup_path}.manifest.json").exists()
        and record_path.exists(),
        "备份、manifest 与恢复记录均已写出，隔离开演判定通过",
    )
    restore = record.restore
    script.say(
        "    演练："
        f"opened={restore['opened']}，integrity={restore['integrity_check']}，"
        f"state_digest_matches={restore['state_digest_matches_preview']}，"
        f"对账字段 {len(restore['checked_keys'])} 项"
    )
    script.check(
        restore["opened"] is True
        and restore["integrity_check"] == "ok"
        and restore["state_digest_matches_preview"] is True
        and restore["reconciliation_passed"] is True
        and len(restore["checked_keys"]) > 20,
        "恢复副本实际打开：事实/证据/控制/屏障/领域引用全部对账",
    )
    script.check(
        record.consistency["method"] == "quiesced_sqlite_online_backup"
        and record.consistency["restore_requirements"]
        == {"program_schema_version": SCHEMA_VERSION, "backup_schema_version": SCHEMA_VERSION}
        and list(work_dir.iterdir()) == [],
        "一致性取得方式与恢复所需版本已记录；演练临时副本已清理",
    )

    second = verify_history_restore(
        backup_path, preview=manifest, work_dir=work_dir, now=FIXED_NOW
    )
    script.check(second.verified is True, "对同一备份可重复执行恢复演练")
    return backup_path


def journey_5_failure_recovery(
    script: _Script, database_path: Path, run_dir: Path, backup_path: Path, manifest
) -> None:
    script.say("[5] 失败恢复：过期清单拒绝备份；坏副本显式失败；原备份不受影响")
    script.check(manifest.chat_scope.eligible == 3, "前置：清单来自漂移后的新鲜快照")

    database = _open_database(database_path)
    _seed_message(database, ACCOUNT, "msg-alice-later", "我上周去了杭州")
    database.close()
    stale_target = run_dir / "backup" / "stale.db"
    try:
        create_history_backup(
            _authority(database_path), stale_target, preview=manifest
        )
        stale_code = None
    except ProfileHistoryError as exc:
        stale_code = exc.code
    script.check(
        stale_code == "preview_stale"
        and not stale_target.exists()
        and not Path(f"{stale_target}.history-record.json").exists(),
        "过期清单拒绝创建备份（未写出任何文件）",
    )

    degraded = run_dir / "backup" / "degraded.db"
    create_verified_backup(_authority(database_path), degraded)
    try:
        verify_history_restore(degraded, preview=manifest)
        degraded_code = None
    except ProfileHistoryError as exc:
        degraded_code = exc.code
    script.check(
        degraded_code == "backup_drill_failed",
        "内容已漂移的备份在演练中显式失败（backup_drill_failed）",
    )

    orphan = run_dir / "backup" / "orphan.db"
    shutil.copyfile(backup_path, orphan)
    try:
        verify_history_restore(orphan, preview=manifest)
        orphan_code = None
    except ProfileHistoryError as exc:
        orphan_code = exc.code
    script.check(
        orphan_code == "backup_unverified",
        "缺少 manifest 的孤儿备份文件拒绝恢复（backup_unverified）",
    )

    before = _file_digest(backup_path)
    still = verify_history_restore(backup_path, preview=manifest)
    script.check(
        still.verified is True and _file_digest(backup_path) == before,
        "原始备份未被覆盖或修改，仍可再次通过演练",
    )


def journey_6_controls_and_scope(
    script: _Script, database_path: Path, run_dir: Path
) -> None:
    script.say("[6] 记录开关与来源范围：暂停记录归零；材料类来源不默认可提取")
    database = _open_database(database_path)
    with database.transaction():
        database.connection.execute(
            "INSERT INTO profile_extraction_privacy_blocks "
            "(block_id, account_id, scope, normalized_value, created_at) "
            "VALUES (?, ?, 'account', '', ?)",
            ("block-off", ACCOUNT, TS),
        )
        database.connection.execute(
            "INSERT INTO profile_account_controls "
            "(account_id, profile_usage_enabled, controls_version, "
            "usage_control_version, updated_at) VALUES (?, 0, ?, 3, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET profile_usage_enabled = 0, "
            "usage_control_version = 3",
            (ACCOUNT, "profile-controls-v1", TS),
        )
    database.close()

    manifest = _preview(database_path)
    script.say(
        f"    recording_enabled={manifest.controls.recording_enabled}，"
        f"usage_enabled={manifest.controls.usage_enabled}"
        f"（版本 {manifest.controls.usage_control_version}），"
        f"eligible={manifest.chat_scope.eligible}，"
        f"skipped_recording_off={manifest.chat_scope.skipped_recording_off}"
    )
    script.check(
        manifest.controls.recording_enabled is False
        and manifest.controls.usage_enabled is False
        and manifest.controls.usage_control_version == 3
        and manifest.chat_scope.eligible == 0
        and manifest.chat_scope.skipped_recording_off == 4,
        "暂停记录后全部可读消息（4 条）落入 recording_off，eligible 归零",
    )
    script.check(
        manifest.items.locators["automatic_unverified_source"]
        and "非聊天来源" in manifest.scope_note,
        "材料类（非聊天）来源条目列为「来源无法核验」，范围说明拒绝默认提取",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 16 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        f"SCHEMA_VERSION={SCHEMA_VERSION}；临时库 .tmp/issue16-demo-{stamp}"
    )

    run_dir = REPO_ROOT / ".tmp" / f"issue16-demo-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    script.say("")
    database_path = journey_1_readonly_preview(script, run_dir)
    script.say("")
    journey_2_rejections(script, database_path, run_dir)
    script.say("")
    manifest = journey_3_drift(script, database_path, run_dir)
    script.say("")
    backup_path = journey_4_backup_and_drill(script, database_path, run_dir, manifest)
    script.say("")
    journey_5_failure_recovery(script, database_path, run_dir, backup_path, manifest)
    script.say("")
    journey_6_controls_and_scope(script, database_path, run_dir)

    script.say("")
    script.say(f"运行目录（保留供复核）：{run_dir}")
    script.say(
        "数据清理：确认无需复核后删除该目录（"
        f"Remove-Item -LiteralPath '{run_dir}' -Recurse -Force）；"
        "其中仅含本脚本生成的合成数据。"
    )
    script.say(
        f"结果：{'全部通过' if script.failures == 0 else f'{script.failures} 项失败'}"
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
