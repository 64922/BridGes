"""画像工单 13 走查：跨会话事项接续、暂停/完成与近期工作上下文守卫。

确定性部分（真实 SQLite 存储 + 真实原子画像服务，不调用外部模型、不读取
任何凭据）：

[1] 两会话接续：会话 A 写入目标/进展/待续问题，会话 B 说「继续上次的考研
    英语准备」→ 整件事项按 ongoing 依据进入编译；无关爱好不注入。
[2] 多个事项独立：暂停/恢复/完成精确到目标，不影响并行事项；歧义查询不
    按主题批量归属。
[3] 待续问题有真实交流依据：投影使用条目原文与真实来源消息标识，不新增
    摘要句子；来源不可读时仍如实呈现依据状态。
[4] 上下文失效与守卫：条目修改立即失效并重建新版本；迟到重建的世代 CAS
    失败、不能覆盖新状态；关使用让上下文不可用（页面仍可管理）。
[5] 生产组合 HTTP 端到端：create_app（内存模式）注册后经真实路由读事项、
    暂停/继续/完成、设置领域引用（未接通如实未解析）、开关使用。
[6] 迁移与派生边界：v86 → v87 升级补列补表且幂等；上下文不进导出类别与
    逻辑摘要、参与备份与账户删除、删除顺序先于 profile_items。

用法：

    python .scratch/profile-memory-redesign/validation/13-ongoing-items-continuity/tools/
        walk_issue13_ongoing_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（`.tmp/issue13-demo-*`），不读取或记录任何凭据与真实数据。
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
    AtomicProfileGoalState,
    AtomicProfileItemModifyRequest,
)
from bridges.contracts.ongoing_profile import (  # noqa: E402
    OngoingContextState,
    OngoingGoalAction,
)
from bridges.contracts.profile_extraction import (  # noqa: E402
    ProfileAccountControlsProjection,
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
)
from bridges.profiles.commit import ProfileFactSubmission  # noqa: E402
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.ongoing import OngoingItemsService  # noqa: E402
from bridges.profiles.ongoing_repository import (  # noqa: E402
    SqliteOngoingContextStore,
)
from bridges.profiles.purpose import build_purpose  # noqa: E402
from bridges.storage.database import (  # noqa: E402
    MIGRATIONS,
    SCHEMA_VERSION,
    BridgesDatabase,
)

ALICE = "demo-alice"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

GOAL = "我在准备考研英语"
PROGRESS = "考研英语的阅读已经刷完一轮"
PENDING = "考研英语的冲刺班还没决定报不报"
HOBBY = "我平时喜欢跑步"
SECOND_GOAL = "我在准备计算机三级"
SECOND_PROGRESS = "计算机三级的教材已经买好了"


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

    def skip(self, label: str) -> None:
        self.skipped += 1
        self.say(f"    [SKIP] {label}")


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


def _message(message_id: str, content: str) -> ProfileSourceMessage:
    return ProfileSourceMessage(
        message_id=message_id,
        conversation_id="conversation-session-a",
        role="user",
        status="sent",
        content=content,
        created_at=NOW,
    )


def _build(tmp: Path, messages: dict[str, ProfileSourceMessage]):
    database = BridgesDatabase(tmp / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(
        four,
        SqliteAtomicProfileRepository(database, initialize=False),
        message_reader=lambda account_id, message_id: messages.get(message_id),
    )
    controls = _Controls()
    ongoing = OngoingItemsService(
        atomic=atomic,
        context_store=SqliteOngoingContextStore(database),
        controls_reader=lambda account_id: controls.projection(),
        message_reader=lambda account_id, message_id: messages.get(message_id),
    )
    atomic.set_ongoing_context_listener(ongoing)
    atomic.set_ongoing_context_provider(ongoing)
    return database, atomic, ongoing, controls


def _seed_session_a(atomic: AtomicProfileService) -> dict[str, str]:
    goal = atomic.remember(ALICE, GOAL, source_message_id="msg-goal")
    progress = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(text=PROGRESS, evidence_message_id="msg-progress"),
    )
    pending = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(text=PENDING, evidence_message_id="msg-pending"),
    )
    hobby = atomic.remember(ALICE, HOBBY, source_message_id="msg-hobby")
    assert progress is not None and pending is not None
    return {
        "goal": goal.profile_item_id,
        "progress": progress.profile_item_id,
        "pending": pending.profile_item_id,
        "hobby": hobby.profile_item_id,
    }


def section_1_continuation(script: _Script, tmp: Path) -> None:
    script.say("[1] 两会话接续：会话 B 说「继续上次的考研英语准备」")
    messages = {
        "msg-goal": _message("msg-goal", GOAL),
        "msg-progress": _message("msg-progress", PROGRESS),
        "msg-pending": _message("msg-pending", PENDING),
        "msg-hobby": _message("msg-hobby", HOBBY),
    }
    _database, atomic, ongoing, _controls = _build(tmp, messages)
    ids = _seed_session_a(atomic)

    group = ongoing.continuation_group(ALICE, query="继续上次的考研英语准备。")
    script.check(group is not None, "唯一目标被提及：接续通道给出整组候选")
    script.check(
        group is not None and group.goal_item_id == ids["goal"],
        f"接续目标正确（goal={ids['goal'][:12]}…）",
    )

    adopted = atomic.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="继续上次的考研英语准备。"),
        run_id="run-session-b",
    )
    by_text = {entry.fact_text: entry for entry in adopted.adopted_items}
    for text in (GOAL, PROGRESS, PENDING):
        basis = (by_text.get(text).recall_basis or "") if text in by_text else None
        script.check(
            basis is not None and "ongoing" in basis.split("+"),
            f"「{text}」按 ongoing 依据采用（basis={basis}）",
        )
    hobby_excluded = {
        entry.fact_text: entry.exclusion_reason for entry in adopted.excluded_items
    }
    script.check(
        HOBBY in hobby_excluded,
        f"无关爱好未注入（排除原因：{hobby_excluded.get(HOBBY)}）",
    )
    script.check(
        "ongoing" not in (hobby_excluded.get(HOBBY) or ""),
        "无关爱好不是靠接续依据排除",
    )


def section_2_parallel_matters(script: _Script, tmp: Path) -> None:
    script.say("[2] 多个事项独立：暂停 / 恢复 / 完成不误覆盖其他目标")
    messages = {
        "msg-goal": _message("msg-goal", GOAL),
        "msg-progress": _message("msg-progress", PROGRESS),
        "msg-pending": _message("msg-pending", PENDING),
    }
    _database, atomic, ongoing, _controls = _build(tmp, messages)
    ids = _seed_session_a(atomic)
    second = atomic.remember(ALICE, SECOND_GOAL, source_message_id="msg-second")
    second_progress = atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text=SECOND_PROGRESS, evidence_message_id="msg-second-progress"
        ),
    )
    assert second_progress is not None

    paused = ongoing.set_state(
        ALICE,
        ids["goal"],
        OngoingGoalAction.PAUSE,
        version=atomic.get_item(ALICE, ids["goal"]).version,
    )
    script.check(
        paused.goal_state is AtomicProfileGoalState.PAUSED, "目标 A 已暂停"
    )
    script.check(
        atomic.get_item(ALICE, second.profile_item_id).goal_state
        is AtomicProfileGoalState.ACTIVE,
        "并行目标 B 保持进行中（未被误覆盖）",
    )
    script.check(
        ongoing.continuation_group(ALICE, query="继续上次的考研英语准备。") is None,
        "暂停后的目标不再作为当前目标接续",
    )

    resumed = ongoing.set_state(
        ALICE,
        ids["goal"],
        OngoingGoalAction.RESUME,
        version=paused.version,
    )
    script.check(
        resumed.goal_state is AtomicProfileGoalState.ACTIVE
        and ongoing.continuation_group(ALICE, query="继续上次的考研英语准备。")
        is not None,
        "恢复后重新成为可接续的当前目标",
    )

    ambiguous = ongoing.continuation_group(
        ALICE, query="考研英语和计算机三级怎么安排？"
    )
    script.check(ambiguous is None, "并行事项同时命中：不猜、不批量归属")

    completed = ongoing.set_state(
        ALICE,
        second.profile_item_id,
        OngoingGoalAction.COMPLETE,
        version=second.version,
    )
    script.check(
        completed.goal_state is AtomicProfileGoalState.COMPLETED,
        "目标 B 完成",
    )
    remaining = {
        item.goal.profile_item_id for item in ongoing.list_items(ALICE).items
    }
    script.check(
        remaining == {ids["goal"]}, "完成的目标从事项区消失"
    )
    kept = {item.profile_item_id for item in atomic.list_items(ALICE)}
    script.check(
        second.profile_item_id in kept and second_progress.profile_item_id in kept,
        "完成只隐藏事项，同组记录仍保留在记忆里",
    )


def section_3_pending_evidence(script: _Script, tmp: Path) -> None:
    script.say("[3] 待续问题有真实交流依据，投影不新增摘要句子")
    messages = {
        "msg-goal": _message("msg-goal", GOAL),
        "msg-progress": _message("msg-progress", PROGRESS),
        "msg-pending": _message("msg-pending", PENDING),
    }
    _database, atomic, ongoing, _controls = _build(tmp, messages)
    _seed_session_a(atomic)

    projection = ongoing.list_items(ALICE)
    item = projection.items[0]
    script.check(item.goal.text == GOAL, "目标展示条目原文")
    script.check(
        [entry.text for entry in item.progress] == [PROGRESS], "进展展示条目原文"
    )
    script.check(
        [entry.text for entry in item.pending_questions] == [PENDING],
        "待续问题展示条目原文（无摘要改写）",
    )
    script.check(
        item.pending_questions[0].source_message_ids == ["msg-pending"],
        "待续问题携带真实来源消息标识",
    )
    script.check(
        item.pending_questions[0].role.value == "pending_question",
        "未决标记（还没…）判定为待续问题",
    )


def section_4_invalidation_guard(script: _Script, tmp: Path) -> None:
    script.say("[4] 上下文失效与守卫：迟到重建不能覆盖新状态")
    messages = {
        "msg-goal": _message("msg-goal", GOAL),
        "msg-progress": _message("msg-progress", PROGRESS),
        "msg-pending": _message("msg-pending", PENDING),
    }
    _database, atomic, ongoing, controls = _build(tmp, messages)
    ids = _seed_session_a(atomic)

    first = ongoing.read_context(ALICE)
    script.check(
        first.state is OngoingContextState.CURRENT and first.version >= 1,
        f"首次读取建立当前上下文（version={first.version}）",
    )

    progress = atomic.get_item(ALICE, ids["progress"])
    atomic.modify_item(
        ALICE,
        ids["progress"],
        AtomicProfileItemModifyRequest(
            text="考研英语的阅读已经刷完两轮", version=progress.version
        ),
    )
    after_edit = ongoing.read_context(ALICE)
    script.check(
        after_edit.state is OngoingContextState.CURRENT
        and after_edit.version > first.version
        and after_edit.dependency_fingerprint != first.dependency_fingerprint,
        "条目修改立即失效并在读取时重建新版本",
    )
    script.check(
        after_edit.items[0].progress[0].text == "考研英语的阅读已经刷完两轮",
        "新上下文包含修改后的正文",
    )

    stored, snapshot = ongoing.begin_context_rebuild(ALICE)
    atomic.remember(ALICE, "我在准备雅思考试", source_message_id="msg-new")
    late = ongoing.commit_context_rebuild(ALICE, stored, snapshot)
    script.check(late is False, "迟到重建的世代 CAS 提交失败")
    current = ongoing.read_context(ALICE)
    goals = {item.goal.text for item in current.items}
    script.check(
        current.state is OngoingContextState.CURRENT
        and "我在准备雅思考试" in goals
        and GOAL in goals,
        "读取路径给出反映新状态的上下文（不覆盖、不丢新目标）",
    )

    ticket_a = ongoing.begin_context_rebuild(ALICE)
    ticket_b = ongoing.begin_context_rebuild(ALICE)
    won_a = ongoing.commit_context_rebuild(ALICE, *ticket_a)
    won_b = ongoing.commit_context_rebuild(ALICE, *ticket_b)
    script.check(
        won_a is True and won_b is False,
        "两个并发重建：先提交者生效，后到的陈旧世代被拒绝",
    )

    controls.usage_enabled = False
    off = ongoing.read_context(ALICE)
    listing = ongoing.list_items(ALICE)
    script.check(
        off.state is OngoingContextState.REBUILDING and off.items == [],
        "关使用后上下文不再作为当前内容",
    )
    script.check(
        ongoing.continuation_group(ALICE, query="继续考研英语") is None,
        "关使用后回答不采用接续依据",
    )
    script.check(
        listing.usage_enabled is False
        and listing.notice is not None
        and len(listing.items) == 2,
        "页面仍列出事项供管理并给出说明",
    )

    controls.usage_enabled = True
    on = ongoing.read_context(ALICE)
    script.check(
        on.state is OngoingContextState.CURRENT
        and on.version > current.version,
        "重新开启后按当时依赖重建（代数继续递增）",
    )


def section_5_production_http(script: _Script) -> None:
    script.say("[5] 生产组合 HTTP 端到端（create_app 内存模式 + 真实路由）")
    from fastapi.testclient import TestClient

    from bridges.api.main import create_app

    client = TestClient(create_app())
    registered = client.post(
        "/auth/register",
        json={
            "username": "issue13-walk",
            "qq_email": "130000@qq.com",
            "password": "correct-horse-13",
        },
    )
    script.check(registered.status_code == 201, "注册账户")
    account_id = registered.json()["account"]["id"]
    atomic = client.app.state.atomic_profile_service
    goal = atomic.remember(account_id, GOAL, source_message_id="http-msg-goal")
    goal_version = int(goal.version)
    progress = atomic.commit_fact(
        account_id,
        ProfileFactSubmission(text=PROGRESS, evidence_message_id="http-msg-progress"),
    )
    assert progress is not None

    listed = client.get("/profiles/ongoing")
    body = listed.json()
    script.check(
        listed.status_code == 200
        and body["items"][0]["goal"]["text"] == GOAL
        and body["items"][0]["progress"][0]["text"] == PROGRESS,
        "GET /profiles/ongoing 返回事项与进展",
    )

    paused = client.post(
        f"/profiles/ongoing/{goal.profile_item_id}/state",
        json={"action": "pause", "version": goal_version},
    )
    script.check(
        paused.status_code == 200
        and paused.json()["items"][0]["goal"]["goal_state"] == "paused",
        "POST state：暂停生效并返回刷新后事项区",
    )
    stale = client.post(
        f"/profiles/ongoing/{goal.profile_item_id}/state",
        json={"action": "complete", "version": goal_version},
    )
    script.check(stale.status_code == 409, "陈旧版本被拒绝（409 版本冲突）")

    resumed = client.post(
        f"/profiles/ongoing/{goal.profile_item_id}/state",
        json={"action": "resume", "version": goal_version + 1},
    )
    script.check(
        resumed.status_code == 200
        and resumed.json()["items"][0]["goal"]["goal_state"] == "active",
        "POST state：继续恢复为进行中",
    )

    reference = client.put(
        f"/profiles/ongoing/{goal.profile_item_id}/domain-reference",
        json={
            "reference": {"domain": "learning", "reference_id": "lp-1"},
            "version": goal_version + 2,
        },
    )
    references = reference.json()["items"][0]["domain_references"]
    script.check(
        reference.status_code == 200
        and references[0]["resolved"] is False
        and references[0]["display"] is None,
        "领域引用只存引用；未接通提供方如实未解析",
    )

    off = client.put("/profiles/controls", json={"usage_enabled": False})
    listing = client.get("/profiles/ongoing").json()
    ongoing = client.app.state.ongoing_items_service
    script.check(
        off.status_code == 200
        and listing["usage_enabled"] is False
        and listing["notice"]
        and len(listing["items"]) == 1,
        "关使用：页面提示且仍可管理事项",
    )
    script.check(
        ongoing.read_context(account_id).state is OngoingContextState.REBUILDING,
        "关使用：上下文不再是当前内容",
    )
    client.put("/profiles/controls", json={"usage_enabled": True})
    script.check(
        ongoing.read_context(account_id).state is OngoingContextState.CURRENT,
        "重新开启：上下文按当时依赖重建",
    )

    completed = client.post(
        f"/profiles/ongoing/{goal.profile_item_id}/state",
        json={"action": "complete", "version": goal_version + 3},
    )
    script.check(
        completed.status_code == 200 and completed.json()["items"] == [],
        "完成：事项区清空该事项",
    )
    items = client.get("/profiles/items").json()
    script.check(
        goal.profile_item_id in {item["profile_item_id"] for item in items},
        "完成：记录仍保留在「你的信息」列表",
    )
    client.close()


def section_6_migration_boundary(script: _Script, tmp: Path) -> None:
    script.say("[6] 迁移 86 → 87 与派生数据边界")

    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "upgrade-86.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 87):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_meta VALUES ('version', '86')")
    database = BridgesDatabase(path)
    script.check(
        database.initialize() == SCHEMA_VERSION and SCHEMA_VERSION >= 87,
        f"v86 库升级到 {SCHEMA_VERSION}",
    )
    columns = {
        str(row["name"])
        for row in database.connection.execute("PRAGMA table_info(profile_items)")
    }
    tables = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    script.check(
        {"related_goal_item_id", "domain_reference_json"} <= columns
        and "profile_ongoing_context" in tables,
        "升级补上归组列与上下文表",
    )
    script.check(
        database.initialize() == SCHEMA_VERSION, "重复初始化幂等"
    )
    database.close()


def section_6_boundary_checks(script: _Script, database: BridgesDatabase) -> None:
    exported_tables = {
        table for category in EXPORT_CATEGORIES for table in category.tables
    }
    script.check(
        "profile_ongoing_context" not in exported_tables,
        "进出与导出：上下文不进导出类别",
    )
    script.check(
        "profile_ongoing_context" not in logical_summary(database, ALICE),
        "进出与导出：上下文不进逻辑摘要",
    )
    script.check(
        ACCOUNT_TABLES.index("profile_ongoing_context")
        < ACCOUNT_TABLES.index("profile_items"),
        "账户删除顺序：上下文先于事实表",
    )
    backup_path = database.path + ".backup"
    with database.snapshot_lock():
        database.snapshot_to(backup_path)
    restored = BridgesDatabase(backup_path)
    restored.initialize()
    row = restored.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_ongoing_context WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    script.check(int(row["count"]) == 1, "备份快照包含上下文行")
    restored.close()
    delete_account_rows(database, ALICE)
    row = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_ongoing_context WHERE account_id = ?",
        (ALICE,),
    ).fetchone()
    script.check(int(row["count"]) == 0, "账户删除清空上下文行")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    out: list[str] = []
    script = _Script(out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    script.say(f"# 画像工单 13 走查（{stamp}）")
    script.say(
        f"python={sys.version.split()[0]}  schema={SCHEMA_VERSION}"
        f"  # 临时库：.tmp/issue13-demo-{stamp}"
    )
    script.say("")

    base = REPO_ROOT / ".tmp" / f"issue13-demo-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)

    section_1_continuation(script, base / "s1")
    script.say("")
    section_2_parallel_matters(script, base / "s2")
    script.say("")
    section_3_pending_evidence(script, base / "s3")
    script.say("")
    section_4_invalidation_guard(script, base / "s4")
    script.say("")
    section_5_production_http(script)
    script.say("")
    section_6_migration_boundary(script, base / "s6")
    script.say("")

    script.say("[6b] 上下文生命周期边界（删除 / 导出 / 备份）")
    messages = {
        "msg-goal": _message("msg-goal", GOAL),
        "msg-progress": _message("msg-progress", PROGRESS),
        "msg-pending": _message("msg-pending", PENDING),
    }
    database, atomic, ongoing, _controls = _build(base / "s6b", messages)
    _seed_session_a(atomic)
    ongoing.read_context(ALICE)
    section_6_boundary_checks(script, database)

    script.say("")
    script.say(
        f"合计：FAIL {script.failures} 项，SKIP {script.skipped} 项。"
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"证据已写入：{args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
