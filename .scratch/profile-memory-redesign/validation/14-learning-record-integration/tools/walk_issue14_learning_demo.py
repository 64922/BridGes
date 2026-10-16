"""画像工单 14 走查：学习引用、领域状态操作与失效联动（确定性）。

真实接口原则：学习目标读走 L08 的 ``StudyGoalRepository``（含聊天侧续学的
会话关联接口），学情判断由复用工单 06 助手的真实导师流程产生（真实路由、
真实后台执行器、真实 L06 纠正/删除边界），组合根按生产 ``create_app`` 装配。
模型调用使用仓库内既有脚本替身（确定性、不触网），学习领域接口本身全部真实。

[1] 记忆页引用区：用户自述留在个人记忆条目里，不冒充已核验学情；真实核验
    记录以原文透传（verified/依据实际作答）。
[2] 纠正与删除立即反映：纠正后不再核验；删除后不再列出，持久层保持已删除。
[3] 页面暂停/继续/完成经学习领域执行：reference_version 守卫（陈旧 409、
    缺失 422），画像条目不写第二份状态；聊天侧续学承接原目标与原路线。
[4] 上下文与接续联动：学习状态变化让依赖指纹变化；暂停中不接续；关使用
    不绕过学习桥接。
[5] 组合根接线：有库时领域端口接通（resolved 实时状态）；未接通时如实 503。

用法：

    python .scratch/profile-memory-redesign/validation/14-learning-record-integration/tools/
        walk_issue14_learning_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（``.tmp/issue14-demo-*``），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from bridges.api.main import create_app  # noqa: E402
from bridges.config import get_settings  # noqa: E402
from bridges.contracts.atomic_profile import (  # noqa: E402
    AtomicProfileDomainReference,
)
from bridges.contracts.ongoing_profile import (  # noqa: E402
    OngoingContextState,
    OngoingGoalAction,
)
from bridges.profiles.commit import ProfileFactSubmission  # noqa: E402
from bridges.study.goals import StudyGoalRepository  # noqa: E402
from tests.chat.test_issue05_durable_diagnosis import (  # noqa: E402
    _close,
    _detail,
    _record_version,
    _start_study,
)
from tests.chat.test_issue06_learning_record_controls import _seed_record  # noqa: E402
from tests.chat.test_v2_05_photo_attachments import _register  # noqa: E402
from tests.profiles.test_issue14_learning_integration import (  # noqa: E402
    _seed_goal,
    _wire_tutor,
)

SELF_REPORT = "我已经掌握了互斥与独立的判断"
GOAL_ITEM_TEXT = "我在准备考研概率论考试"


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


def _stack(tmp: Path):
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{tmp / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = "issue14-walk-secret"
    os.environ["BRIDGES_ENVIRONMENT"] = "test"
    get_settings.cache_clear()
    return create_app()


def _drive(app) -> None:
    executor = app.state.generation_executor
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        executor.run_tick()
        if "无待处理" in executor._last_summary:  # noqa: SLF001 - 读执行器摘要
            return
        time.sleep(0.02)


def _link_goal_item(app, account_id: str, goal_id: str):
    """写下目标类条目并经画像 13 的接缝关联学习目标；返回刷新后的条目。"""

    atomic = app.state.atomic_profile_service
    ongoing = app.state.ongoing_items_service
    item = atomic.remember(
        account_id, GOAL_ITEM_TEXT, source_message_id="walk-goal-item"
    )
    ongoing.set_domain_reference(
        account_id,
        item.profile_item_id,
        AtomicProfileDomainReference(domain="study", reference_id=goal_id),
        version=item.version,
    )
    return atomic.get_item(account_id, item.profile_item_id)


def _section_1_references(script: _Script, app, client: TestClient) -> None:
    script.say("[1] 记忆页引用区：自述与核验学情分栏（真实 L06 流程）")
    account_id = _register(client, "issue14-walk")["id"]
    atomic = app.state.atomic_profile_service
    submitted = atomic.commit_fact(
        account_id,
        ProfileFactSubmission(
            text=SELF_REPORT, evidence_message_id="walk-self-report"
        ),
    )
    script.check(submitted is not None, "用户自述写入个人记忆条目")

    text, plan, _structured = _wire_tutor(app)
    conversation_id = _seed_record(
        app, client, {"drive": _drive}, text, plan, "issue14-walk-a"
    )
    record_id = _detail(client, conversation_id)["study_learning_record"][0][
        "record_id"
    ]

    response = client.get("/profiles/learning-references")
    body = response.json()
    script.check(response.status_code == 200, "GET /profiles/learning-references 可用")
    script.check(
        body["owner"] == "study"
        and body["contract_version"] == "learning-references-v1",
        "投影标明所有权与合同版本",
    )
    script.check(
        len(body["evidences"]) == 1
        and body["evidences"][0]["record_id"] == record_id
        and body["evidences"][0]["verified"] is True
        and body["evidences"][0]["verdict"] == "independent_completion",
        "真实作答核验的记录进入引用区并标注已核验（依据实际作答）",
    )
    script.check(
        SELF_REPORT not in str(body),
        "自述不进入学习引用区（不冒充核验结论）",
    )
    items = client.get("/profiles/items").json()
    script.check(
        any(item["text"] == SELF_REPORT for item in items),
        "自述仍在个人记忆条目列表里",
    )


def _section_2_correct_and_delete(script: _Script, app, client: TestClient) -> None:
    script.say("[2] 纠正与删除立即反映（真实 L06 路由）")
    database = app.state.bridges_database
    conversation_id = str(
        database.connection.execute(
            "SELECT conversation_id FROM conversations ORDER BY rowid LIMIT 1"
        ).fetchone()["conversation_id"]
    )
    record_id = str(
        database.connection.execute(
            "SELECT record_id FROM study_learning_records ORDER BY rowid LIMIT 1"
        ).fetchone()["record_id"]
    )
    version = _record_version(database)

    corrected = client.post(
        f"/chat/conversations/{conversation_id}/study/records/{record_id}/corrections",
        json={
            "note": "我当时只是看懂了，还没真正做对。",
            "expected_version": version,
        },
    )
    script.check(corrected.status_code == 200, "真实 L06 纠正路由成功")
    evidence = client.get("/profiles/learning-references").json()["evidences"][0]
    script.check(
        evidence["state"] == "corrected"
        and evidence["verified"] is False
        and evidence["user_note"].startswith("我当时只是看懂了"),
        "纠正后立即不再核验，用户说明原文透传",
    )

    removed = client.request(
        "DELETE",
        f"/chat/conversations/{conversation_id}/study/records/{record_id}",
        json={"expected_version": evidence["record_version"]},
    )
    script.check(removed.status_code == 204, "真实 L06 删除路由成功")
    script.check(
        client.get("/profiles/learning-references").json()["evidences"] == [],
        "删除后引用区不再列出（立即失效）",
    )
    row = database.connection.execute(
        "SELECT state FROM study_learning_records WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    script.check(row["state"] == "deleted", "记录在持久层保持已删除")


def _section_3_domain_state(script: _Script, app, client: TestClient) -> None:
    script.say("[3] 页面暂停/继续/完成经学习领域执行（版本守卫、不双写）")
    account_id = _register(client, "issue14-walk-state")["id"]
    database = app.state.bridges_database
    atomic = app.state.atomic_profile_service

    text, plan, _structured = _wire_tutor(app)
    conversation_id = _start_study(client, "互斥是不是独立？", "issue14-walk-s1")
    _drive(app)
    goal_version = _seed_goal(app, account_id, "goal-walk-14")
    item = _link_goal_item(app, account_id, "goal-walk-14")

    listing = client.get("/profiles/ongoing").json()
    reference = listing["items"][0]["domain_references"][0]
    script.check(
        reference["resolved"] is True
        and reference["state_code"] == "active"
        and reference["version"] == goal_version,
        "组合根接通学习领域：引用实时解析（display/state/version）",
    )

    paused = client.post(
        f"/profiles/ongoing/{item.profile_item_id}/state",
        json={
            "action": "pause",
            "version": item.version,
            "reference_version": goal_version,
        },
    )
    repository = StudyGoalRepository(database)
    goal = repository.get_goal(account_id, "goal-walk-14")
    script.check(
        paused.status_code == 200 and goal.status == "paused" and goal.version == 2,
        "页面暂停在学习领域内执行（目标状态 paused，版本 +1）",
    )
    stored = atomic.get_item(account_id, item.profile_item_id)
    script.check(
        stored.version == item.version and stored.goal_state.value == "active",
        "画像条目不写第二份状态（版本与自身状态不变）",
    )

    stale = client.post(
        f"/profiles/ongoing/{item.profile_item_id}/state",
        json={"action": "resume", "version": item.version, "reference_version": 1},
    )
    script.check(
        stale.status_code == 409
        and stale.json()["detail"]["error"] == "ongoing_domain_reference_conflict",
        "陈旧领域版本按冲突拒绝（409，不覆盖学习新状态）",
    )
    missing = client.post(
        f"/profiles/ongoing/{item.profile_item_id}/state",
        json={"action": "resume", "version": item.version},
    )
    script.check(missing.status_code == 422, "缺失领域版本的页面操作如实拒绝（422）")

    now = datetime.now(UTC)
    with database.transaction():
        linked = repository.link_existing_goal_in_transaction(
            account_id=account_id,
            conversation_id=conversation_id,
            goal_id="goal-walk-14",
            expected_version=2,
            now=now,
        )
    resumed_goal = repository.get_linked_goal(account_id, conversation_id)
    script.check(
        linked is True
        and resumed_goal is not None
        and resumed_goal.status == "active"
        and resumed_goal.version == 3
        and resumed_goal.route is not None
        and resumed_goal.route.current_node_id == "node-1",
        "聊天侧续学承接原目标与原路线（L08 会话关联接口）",
    )
    resumed = client.get("/profiles/ongoing").json()
    script.check(
        resumed["items"][0]["goal"]["goal_state"] == "active"
        and resumed["items"][0]["domain_references"][0]["version"] == 3,
        "页面读取实时反映恢复后的领域状态",
    )

    paused_again = client.post(
        f"/profiles/ongoing/{item.profile_item_id}/state",
        json={"action": "pause", "version": item.version, "reference_version": 3},
    )
    script.check(paused_again.status_code == 200, "再次暂停（当前版本）成功")
    completed = client.post(
        f"/profiles/ongoing/{item.profile_item_id}/state",
        json={"action": "complete", "version": item.version, "reference_version": 4},
    )
    completed_goal = repository.get_goal(account_id, "goal-walk-14")
    script.check(
        completed.status_code == 200
        and completed_goal.status == "completed"
        and completed.json()["items"] == [],
        "完成：学习目标 completed，事项区不再列出",
    )
    items = client.get("/profiles/items").json()
    script.check(
        item.profile_item_id in {entry["profile_item_id"] for entry in items},
        "完成只隐藏事项；画像条目仍保留",
    )
    references = app.state.learning_references_service.read(account_id)
    script.check(
        references.goals == [],
        "已完成目标不进入学习引用投影（活动/暂停才列出）",
    )


def _section_4_context(script: _Script, app, client: TestClient) -> None:
    script.say("[4] 上下文与接续联动：领域状态变化使指纹失效，不被桥接绕过")
    account_id = _register(client, "issue14-walk-ctx")["id"]
    ongoing = app.state.ongoing_items_service

    goal_version = _seed_goal(app, account_id, "goal-walk-ctx")
    item = _link_goal_item(app, account_id, "goal-walk-ctx")
    first = ongoing.read_context(account_id)
    script.check(
        first.state is OngoingContextState.CURRENT,
        "首次读取建立当前上下文",
    )

    ongoing.set_state(
        account_id,
        item.profile_item_id,
        OngoingGoalAction.PAUSE,
        version=item.version,
        reference_version=goal_version,
    )
    paused = ongoing.read_context(account_id)
    script.check(
        paused.state is OngoingContextState.CURRENT
        and paused.dependency_fingerprint != first.dependency_fingerprint
        and paused.items[0].domain_references[0].state_code == "paused",
        "学习领域暂停使指纹变化并在读取时重建（引用不复制进画像）",
    )
    script.check(
        ongoing.continuation_group(
            account_id,
            query="继续考研概率论",
            semantic_item_ids=[item.profile_item_id],
        )
        is None,
        "暂停中的关联目标不进入接续候选",
    )

    off = client.put("/profiles/controls", json={"usage_enabled": False})
    script.check(
        off.status_code == 200
        and ongoing.read_context(account_id).state is OngoingContextState.REBUILDING
        and ongoing.continuation_group(
            account_id,
            query="继续考研概率论",
            semantic_item_ids=[item.profile_item_id],
        )
        is None,
        "关使用：上下文不可用且接续通道关闭（不绕过开关）",
    )
    client.put("/profiles/controls", json={"usage_enabled": True})
    ongoing.set_state(
        account_id,
        item.profile_item_id,
        OngoingGoalAction.RESUME,
        version=item.version,
        reference_version=2,
    )
    back = ongoing.read_context(account_id)
    continuation = ongoing.continuation_group(
        account_id,
        query="继续考研概率论",
        semantic_item_ids=[item.profile_item_id],
    )
    script.check(
        back.state is OngoingContextState.CURRENT
        and continuation is not None
        and continuation.goal_item_id == item.profile_item_id,
        "重新开启并恢复目标后：上下文重建、接续候选回到该事项",
    )


def _section_5_wiring(script: _Script) -> None:
    script.say("[5] 组合根接线：接通与未接通如实区分")
    os.environ.pop("BRIDGES_DATABASE_URL", None)
    get_settings.cache_clear()
    in_memory = TestClient(create_app())
    _register(in_memory, "issue14-walk-503")
    response = in_memory.get("/profiles/learning-references")
    script.check(
        response.status_code == 503
        and response.json()["detail"]["error"] == "learning_references_unavailable",
        "无学习领域接线的组合如实返回 503（不伪造空学情）",
    )
    in_memory.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    out: list[str] = []
    script = _Script(out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    script.say(f"# 画像工单 14 走查（{stamp}）")
    script.say(f"python={sys.version.split()[0]}  # 临时库：.tmp/issue14-demo-{stamp}")
    script.say("")

    base = REPO_ROOT / ".tmp" / f"issue14-demo-{uuid.uuid4().hex[:8]}"

    app = _stack(base / "s12")
    try:
        with TestClient(app) as client:
            _section_1_references(script, app, client)
            script.say("")
            _section_2_correct_and_delete(script, app, client)
    finally:
        _close(app)
    script.say("")

    app = _stack(base / "s34")
    try:
        with TestClient(app) as client:
            _section_3_domain_state(script, app, client)
            script.say("")
            _section_4_context(script, app, client)
    finally:
        _close(app)
    script.say("")

    _section_5_wiring(script)

    script.say("")
    script.say(f"合计：FAIL {script.failures} 项，SKIP {script.skipped} 项。")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"证据已写入：{args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
