"""画像工单 15 走查：整体概览、开放主题浏览与失效守卫。

确定性部分（真实 SQLite 存储 + 真实原子画像与概览服务，不调用外部模型、
不读取任何凭据）：

[1] 概览生成与可追溯依据：每条陈述逐段携带条目标识与版本，正文由条目
    原文用「；」原样拼接；全部条目恰好出现一次，不产生新句子。
[2] 开放主题浏览：未知主题不丢条目、多主题不复制事实（按规范顺序归到
    唯一主题）、无主题单独成组且最后展示；近义主题精确归一不改变身份，
    主题调整只推进版本、不改变条目标识与授权。
[3] 编辑 / 删除 / 失效与迟到重建：编辑后旧概览不再作为当前内容；迟到
    重建的世代 CAS 失败，不能把旧内容写回；两个并发重建先提交者生效。
[4] 状态诚实：无记忆（空态）、重组竞争（部分可用）、全部失效（整理中）
    与关闭使用（仍可管理且如实提示）各自如实呈现，不伪造概览。
[5] 生产组合 HTTP 端到端：create_app（内存模式）注册后经真实路由读取
    概览、编辑与删除条目、关闭使用；未登录被拒。
[6] 迁移 87 → 88、重启恢复与派生数据边界：升级建表且幂等；重启后概览
    持久化可读、编辑后仍会失效重建；概览随账户删除清空、参与备份快照、
    不进导出类别与逻辑摘要，且不是回答编译的前置依赖（监听器可选）。

用法：

    python .scratch/profile-memory-redesign/validation/15-overview-and-topic-browsing/tools/
        walk_issue15_overview_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（`.tmp/issue15-demo-*`），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.contracts.atomic_profile import (  # noqa: E402
    AtomicProfileItemModifyRequest,
)
from bridges.contracts.profile_extraction import (  # noqa: E402
    ProfileAccountControlsProjection,
)
from bridges.contracts.profile_overview import (  # noqa: E402
    ProfileOverviewState,
)
from bridges.lifecycle.catalog import (  # noqa: E402
    ACCOUNT_TABLES,
    EXPORT_CATEGORIES,
    delete_account_rows,
    logical_summary,
)
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    AtomicProfileService,
    ProfileSourceMessage,
    SqliteAtomicProfileRepository,
    normalize_topics,
)
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.overview import ProfileOverviewService  # noqa: E402
from bridges.profiles.overview_projection import (  # noqa: E402
    PROFILE_OVERVIEW_PARTIAL_NOTICE,
    PROFILE_OVERVIEW_REBUILDING_NOTICE,
    PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE,
)
from bridges.profiles.overview_repository import (  # noqa: E402
    SqliteProfileOverviewStore,
)
from bridges.profiles.purpose import build_purpose  # noqa: E402
from bridges.storage.database import (  # noqa: E402
    MIGRATIONS,
    SCHEMA_VERSION,
    BridgesDatabase,
)

ALICE = "demo-alice"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

TEXT_NAME = "我叫小林"
TEXT_CAT = "我养了一只猫叫团子"
TEXT_HALF_HOUR = "我每晚只能抽出半小时"
TEXT_YOGA = "我周末练瑜伽"
TEXT_VOLUNTEER = "我周末去社区做志愿者"
TEXT_DOG = "我家有一只狗"
OVERVIEW_TABLE = "profile_overview"


class _Script:
    def __init__(self, out: list[str]) -> None:
        self._out = out
        self.failures = 0
        self.skipped = 0

    def say(self, text: str) -> None:
        print(text)
        self._out.append(text)

    def check(self, ok: bool, label: str) -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        self.say(f"    [{status}] {label}")


class _Controls:
    def __init__(self) -> None:
        self.usage_enabled = True
        self.usage_control_version = 1

    def projection(self) -> ProfileAccountControlsProjection:
        return ProfileAccountControlsProjection(
            recording_enabled=True,
            usage_enabled=self.usage_enabled,
            usage_control_version=self.usage_control_version,
            usage_updated_at=None,
        )


def _never_commit(
    account_id: str, stored: object, snapshot: object, now: datetime | None = None
) -> bool:
    """模拟重建竞争持续失败（CAS 始终丢失）。"""
    return False


def _message(message_id: str, content: str) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id="conversation-demo",
        role="user",
        status="sent",
        content=content,
        created_at=NOW,
    )


def _build(
    tmp: Path, messages: dict[str, ProfileSourceMessage] | None = None
) -> tuple[
    BridgesDatabase, AtomicProfileService, ProfileOverviewService, _Controls
]:
    tmp.mkdir(parents=True, exist_ok=True)
    database = BridgesDatabase(tmp / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(
        four,
        SqliteAtomicProfileRepository(database, initialize=False),
        message_reader=lambda account_id, message_id: (messages or {}).get(
            message_id
        ),
    )
    controls = _Controls()
    overview = ProfileOverviewService(
        atomic=atomic,
        overview_store=SqliteProfileOverviewStore(database),
        controls_reader=lambda account_id: controls.projection(),
        message_reader=lambda account_id, message_id: (messages or {}).get(
            message_id
        ),
    )
    atomic.set_profile_overview_listener(overview)
    return database, atomic, overview, controls


def _seed(atomic: AtomicProfileService) -> dict[str, str]:
    name = atomic.remember(ALICE, TEXT_NAME, topics=["称呼"])
    cat = atomic.remember(ALICE, TEXT_CAT, topics=["兴趣爱好"])
    half_hour = atomic.remember(ALICE, TEXT_HALF_HOUR, topics=["现实约束"])
    assert name is not None and cat is not None and half_hour is not None
    return {
        "name": name.profile_item_id,
        "cat": cat.profile_item_id,
        "half_hour": half_hour.profile_item_id,
    }


def _segment_ids(overview) -> list[str]:
    return [
        segment.profile_item_id
        for statement in overview.statements
        for segment in statement.segments
    ]


def _edit(
    atomic: AtomicProfileService, item_id: str, text: str, **kwargs
) -> object:
    item = atomic.get_item(ALICE, item_id)
    return atomic.modify_item(
        ALICE,
        item_id,
        AtomicProfileItemModifyRequest(text=text, version=item.version, **kwargs),
    )


def section_1_traceable(script: _Script, tmp: Path) -> None:
    script.say("[1] 概览生成与可追溯依据：逐段原文 + 版本，不产生新句子")
    _database, atomic, overview_service, _controls = _build(tmp)
    ids = _seed(atomic)
    items = {item.profile_item_id: item for item in atomic.list_items(ALICE)}

    overview = overview_service.list_overview(ALICE, now=NOW)

    script.check(
        overview.state is ProfileOverviewState.CURRENT
        and overview.usage_enabled is True
        and overview.notice is None,
        "冷启动生成当前概览（current，无提示）",
    )
    script.check(
        overview.generated_at is not None, "概览带最近整理时间"
    )
    verbatim = all(
        statement.text == "；".join(segment.text for segment in statement.segments)
        for statement in overview.statements
    )
    script.check(verbatim, "每条陈述正文 = 各段原文用「；」连接（不改写）")
    traceable = all(
        segment.text == items[segment.profile_item_id].text
        and segment.version == items[segment.profile_item_id].version
        for statement in overview.statements
        for segment in statement.segments
    )
    script.check(traceable, "每段携带条目标识，正文与版本等于当前条目")
    script.check(
        sorted(_segment_ids(overview)) == sorted(ids.values()),
        "全部条目恰好出现一次（无遗漏、无复制）",
    )


def section_2_topics(script: _Script, tmp: Path) -> None:
    script.say("[2] 开放主题浏览：未知主题不丢、多主题不复制、无主题成组")
    _database, atomic, overview_service, _controls = _build(tmp / "s2")

    multi = atomic.remember(ALICE, TEXT_YOGA, topics=["兴趣", "学习背景"])
    custom = atomic.remember(ALICE, TEXT_VOLUNTEER, topics=["志愿活动"])
    plain = atomic.remember(ALICE, TEXT_DOG)
    assert multi is not None and custom is not None and plain is not None

    overview = overview_service.list_overview(ALICE, now=NOW)
    by_segment = {
        segment.profile_item_id: statement
        for statement in overview.statements
        for segment in statement.segments
    }
    script.check(
        by_segment[multi.profile_item_id].topic == "兴趣与喜好"
        and _segment_ids(overview).count(multi.profile_item_id) == 1,
        "多主题条目只出现一次，归属规范顺序更靠前的默认主题",
    )
    script.check(
        by_segment[custom.profile_item_id].topic == "志愿活动",
        "未知主题原样保留成组（不丢条目）",
    )
    script.check(
        by_segment[plain.profile_item_id].topic is None
        and overview.statements[-1].topic is None,
        "无主题条目归入无主题陈述（最后），不被丢弃",
    )

    script.check(
        normalize_topics(["爱好", " 兴趣 ", "宠物"]) == ["兴趣与喜好", "宠物"]
        and normalize_topics(["我的爱好清单"]) == ["我的爱好清单"],
        "近义主题只在折叠空白后完全相同时归一；未知主题不动",
    )
    original = atomic.get_item(ALICE, custom.profile_item_id)
    rebuilt = _edit(
        atomic, custom.profile_item_id, TEXT_VOLUNTEER, topics=["公益", "志愿活动"]
    )
    script.check(
        rebuilt.profile_item_id == original.profile_item_id
        and rebuilt.version == original.version + 1
        and rebuilt.profile_item_id == custom.profile_item_id,
        "调整主题只推进版本：条目标识与事实身份不变",
    )


def section_3_invalidation(script: _Script, tmp: Path) -> None:
    script.say("[3] 编辑 / 删除 / 迟到重建：旧概览不再作为当前事实")
    _database, atomic, overview_service, _controls = _build(tmp / "s3")
    ids = _seed(atomic)
    cold = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        TEXT_CAT in "".join(statement.text for statement in cold.statements),
        "初始概览包含猫的条目",
    )

    _edit(atomic, ids["cat"], "我养了一只猫叫小白")
    edited = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in edited.statements)
    script.check(
        "小白" in joined and TEXT_CAT not in joined,
        "编辑后概览重建：显示新正文，不残留旧正文",
    )

    stored, snapshot = overview_service.begin_overview_rebuild(ALICE)
    assert stored is not None
    _edit(atomic, ids["cat"], "我养了一只猫叫团团")
    late = overview_service.commit_overview_rebuild(
        ALICE, stored, snapshot, now=NOW
    )
    script.check(late is False, "迟到重建的世代 CAS 提交失败")
    refreshed = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in refreshed.statements)
    script.check(
        "团团" in joined and "小白" not in joined,
        "读取路径给出反映新状态的概览（旧内容不写回）",
    )

    ticket_a = overview_service.begin_overview_rebuild(ALICE)
    ticket_b = overview_service.begin_overview_rebuild(ALICE)
    won_a = overview_service.commit_overview_rebuild(ALICE, *ticket_a, now=NOW)
    won_b = overview_service.commit_overview_rebuild(ALICE, *ticket_b, now=NOW)
    script.check(
        won_a is True and won_b is False,
        "两个并发重建：先提交者生效，陈旧世代被拒绝",
    )

    fresh = atomic.get_item(ALICE, ids["cat"])
    atomic.delete_item(ALICE, ids["cat"], fresh.version)
    deleted = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in deleted.statements)
    script.check(
        "团团" not in joined
        and ids["cat"] not in _segment_ids(deleted)
        and TEXT_NAME in joined,
        "删除条目后概览不再包含它，其余条目照常",
    )


def section_4_states(script: _Script, tmp: Path) -> None:
    script.say("[4] 状态诚实：空态 / 部分可用 / 整理中 / 关使用")
    _database, empty_atomic, empty_overview, _controls = _build(
        tmp / "s4-empty"
    )
    empty = empty_overview.list_overview(ALICE, now=NOW)
    script.check(
        empty.state is ProfileOverviewState.EMPTY
        and empty.statements == []
        and empty.generated_at is None,
        "无记忆：空态无陈述、不写伪造概览行",
    )

    _database, atomic, overview_service, controls = _build(tmp / "s4")
    ids = _seed(atomic)
    overview_service.list_overview(ALICE, now=NOW)

    original_commit = overview_service.commit_overview_rebuild
    overview_service.commit_overview_rebuild = _never_commit
    _edit(atomic, ids["cat"], "我养了一只猫叫小白")
    partial = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        partial.state is ProfileOverviewState.PARTIAL
        and partial.notice == PROFILE_OVERVIEW_PARTIAL_NOTICE
        and ids["cat"] not in _segment_ids(partial)
        and ids["name"] in _segment_ids(partial)
        and ids["half_hour"] in _segment_ids(partial),
        "重组竞争：只保留依赖仍一致的旧陈述并如实提示「部分」",
    )
    for key in ("name", "half_hour"):
        entry = atomic.get_item(ALICE, ids[key])
        _edit(atomic, ids[key], entry.text + "（更新）")
    rebuilding = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        rebuilding.state is ProfileOverviewState.REBUILDING
        and rebuilding.notice == PROFILE_OVERVIEW_REBUILDING_NOTICE
        and rebuilding.statements == [],
        "全部失效：整理中不显示任何陈述，不回退旧内容",
    )
    overview_service.commit_overview_rebuild = original_commit
    recovered = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        recovered.state is ProfileOverviewState.CURRENT
        and len(_segment_ids(recovered)) == 3,
        "竞争结束后重建成功，概览恢复当前",
    )

    controls.usage_enabled = False
    controls.usage_control_version += 1
    overview_service.invalidate(ALICE, reason="controls")
    off = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        off.usage_enabled is False
        and off.notice == PROFILE_OVERVIEW_USAGE_DISABLED_NOTICE
        and bool(off.statements),
        "关使用：回答不使用记忆，页面仍可查看与管理并给出说明",
    )


def section_5_production_http(script: _Script) -> None:
    script.say("[5] 生产组合 HTTP 端到端（create_app 内存模式 + 真实路由）")
    from fastapi.testclient import TestClient

    from bridges.api.main import create_app

    client = TestClient(create_app())
    anonymous = client.get("/profiles/overview")
    script.check(anonymous.status_code == 401, "未登录读取概览被拒（401）")

    registered = client.post(
        "/auth/register",
        json={
            "username": "issue15-walk",
            "qq_email": "150999@qq.com",
            "password": "correct-horse-15",
        },
    )
    script.check(registered.status_code == 201, "注册账户")
    account_id = registered.json()["account"]["id"]
    atomic = client.app.state.atomic_profile_service
    cat = atomic.remember(account_id, TEXT_CAT, topics=["兴趣爱好"])
    half_hour = atomic.remember(account_id, TEXT_HALF_HOUR, topics=["现实约束"])

    body = client.get("/profiles/overview").json()
    segments = [
        segment
        for statement in body["statements"]
        for segment in statement["segments"]
    ]
    script.check(
        body["state"] == "current"
        and {segment["profile_item_id"] for segment in segments}
        == {cat.profile_item_id, half_hour.profile_item_id}
        and all(
            statement["text"]
            == "；".join(segment["text"] for segment in statement["segments"])
            for statement in body["statements"]
        ),
        "GET /profiles/overview 返回可追溯陈述（逐段标识 + 原文拼接）",
    )

    current = atomic.get_item(account_id, cat.profile_item_id)
    edited = client.patch(
        f"/profiles/items/{cat.profile_item_id}",
        json={"text": "我养了一只猫叫小白", "version": current.version},
    )
    script.check(edited.status_code == 200, "PATCH 条目：编辑成功")
    joined = "".join(
        statement["text"]
        for statement in client.get("/profiles/overview").json()["statements"]
    )
    script.check(
        "小白" in joined and TEXT_CAT not in joined,
        "编辑后概览不再显示旧概览内容",
    )

    current = atomic.get_item(account_id, cat.profile_item_id)
    deleted = client.request(
        "DELETE",
        f"/profiles/items/{cat.profile_item_id}",
        json={"version": current.version},
    )
    body = client.get("/profiles/overview").json()
    joined = "".join(statement["text"] for statement in body["statements"])
    script.check(
        deleted.status_code == 204
        and "小白" not in joined
        and TEXT_HALF_HOUR in joined,
        "DELETE 条目后概览移除它，其余条目照常",
    )

    off = client.put("/profiles/controls", json={"usage_enabled": False})
    body = client.get("/profiles/overview").json()
    script.check(
        off.status_code == 200
        and body["usage_enabled"] is False
        and body["notice"]
        and bool(body["statements"]),
        "关使用：概览仍列出信息供管理并如实提示",
    )
    client.close()


def section_6_migration_restart_boundary(script: _Script, tmp: Path) -> None:
    script.say("[6] 迁移 87 → 88、重启恢复与派生数据边界")

    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "upgrade-87.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 88):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '87')")
    database = BridgesDatabase(path)
    script.check(
        database.initialize() == SCHEMA_VERSION and SCHEMA_VERSION >= 88,
        f"v87 库升级到 {SCHEMA_VERSION}",
    )
    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    columns = {
        str(row["name"])
        for row in database.connection.execute(
            f"PRAGMA table_info({OVERVIEW_TABLE})"
        )
    }
    script.check(
        OVERVIEW_TABLE in tables
        and {
            "account_id",
            "context_version",
            "epoch",
            "dependency_fingerprint",
            "payload_json",
            "built_at",
            "invalidated_at",
            "invalidated_reason",
        }
        <= columns,
        "升级补上概览派生表及其列",
    )
    script.check(database.initialize() == SCHEMA_VERSION, "重复初始化幂等")
    database.close()

    script.say("[6b] 重启恢复：概览持久化可读，编辑后仍会失效重建")
    restart_dir = tmp / "restart"
    database, atomic, overview_service, _controls = _build(restart_dir)
    ids = _seed(atomic)
    first = overview_service.list_overview(ALICE, now=NOW)
    assert first.state is ProfileOverviewState.CURRENT
    database.close()

    database, atomic, overview_service, _controls = _build(restart_dir)
    persisted = overview_service.list_overview(ALICE, now=NOW)
    script.check(
        persisted.state is ProfileOverviewState.CURRENT
        and sorted(_segment_ids(persisted)) == sorted(ids.values())
        and persisted.generated_at is not None,
        "重启后概览从存储读取，仍为当前且条目齐全",
    )
    _edit(atomic, ids["name"], "我叫林同学")
    rebuilt = overview_service.list_overview(ALICE, now=NOW)
    joined = "".join(statement.text for statement in rebuilt.statements)
    script.check(
        rebuilt.state is ProfileOverviewState.CURRENT
        and "林同学" in joined
        and TEXT_NAME not in joined,
        "重启后编辑条目：概览立即失效并重建出新内容",
    )

    script.say("[6c] 派生数据边界：删除 / 导出 / 备份 / 非编译依赖")
    exported_tables = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    script.check(
        OVERVIEW_TABLE not in exported_tables,
        "进出与导出：概览不进导出类别",
    )
    script.check(
        OVERVIEW_TABLE not in logical_summary(database, ALICE),
        "进出与导出：概览不进逻辑摘要",
    )
    script.check(
        ACCOUNT_TABLES.index(OVERVIEW_TABLE) < ACCOUNT_TABLES.index("profile_items"),
        "账户删除顺序：概览先于事实表",
    )
    backup_path = str(database.path) + ".backup"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    restored.initialize()
    row = restored.connection.execute(
        f"SELECT COUNT(*) AS count FROM {OVERVIEW_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    script.check(int(row["count"]) >= 1, "备份快照包含概览行")
    restored.close()
    delete_account_rows(database, ALICE)
    row = database.connection.execute(
        f"SELECT COUNT(*) AS count FROM {OVERVIEW_TABLE} WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    script.check(int(row["count"]) == 0, "账户删除清空概览行")

    script.say("[6d] 概览不是回答编译的前置依赖（监听器可选）")
    plain_dir = tmp / "no-listener"
    plain_dir.mkdir(parents=True, exist_ok=True)
    plain_db = BridgesDatabase(plain_dir / "bridges.db")
    plain_db.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    plain_atomic = AtomicProfileService(
        four, SqliteAtomicProfileRepository(plain_db, initialize=False)
    )
    plain_atomic.remember(ALICE, TEXT_CAT, topics=["兴趣爱好"])
    adopted = plain_atomic.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="聊聊我的猫"),
        run_id="run-issue15",
    )
    covered = {entry.fact_text for entry in adopted.adopted_items} | {
        entry.fact_text for entry in adopted.excluded_items
    }
    script.check(
        TEXT_CAT in covered,
        "不装概览监听器也能编译回答：概览不是编译依赖，只服务页面展示",
    )
    plain_db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    out: list[str] = []
    script = _Script(out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    script.say(f"# 画像工单 15 走查（{stamp}）")
    script.say(
        f"python={sys.version.split()[0]}  schema={SCHEMA_VERSION}"
        f"  # 临时库：.tmp/issue15-demo-{stamp}"
    )
    script.say("")

    base = REPO_ROOT / ".tmp" / f"issue15-demo-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)

    section_1_traceable(script, base / "s1")
    script.say("")
    section_2_topics(script, base / "s2")
    script.say("")
    section_3_invalidation(script, base / "s3")
    script.say("")
    section_4_states(script, base / "s4")
    script.say("")
    section_5_production_http(script)
    script.say("")
    section_6_migration_restart_boundary(script, base / "s6")
    script.say("")

    script.say(f"合计：FAIL {script.failures} 项，SKIP {script.skipped} 项。")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"证据已写入：{args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
