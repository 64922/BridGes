"""画像工单 08 走查：忘掉/关闭个性化后的间接回流阻断（确定性，本机规则）。

覆盖四个用户可见旅程（全部真实存储与真实聊天管线，不调用外部模型）：

[1] 忘掉后同义新提及不恢复：记住 → 忘掉 → 同义说法提交 → 列表为空。
[2] 恢复只解除指定范围：两条墓碑 → 明确重新记住一条 → 另一条来源重放仍被拦截。
[3] 本轮不用：真实聊天回合不注入切片、披露如实、信息保留、下一轮恢复注入；
    编译后发送前关闭使用（竞态注入）时旧切片失效。
[4] 账号删除后迟到 worker：握着删除前任务对象的 worker 不重建任何携带来源
    快照的行（运行/任务记账 0 行）。

用法：
    python .scratch/profile-memory-redesign/validation/08-semantic-forgetting-and-controls/tools/walk_issue08_forgetting_demo.py \
        --out .scratch/profile-memory-redesign/validation/08-semantic-forgetting-and-controls/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（`.tmp/issue08-demo-*`），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai import ModelGateway  # noqa: E402
from bridges.ai.adapters import StreamChunk  # noqa: E402
from bridges.ai.capability_registry import CapabilityRegistry  # noqa: E402
from bridges.chat.repository import ConversationRepository  # noqa: E402
from bridges.chat.service import ChatService  # noqa: E402
from bridges.contracts.ai import CapabilityKind, CapabilityRecord  # noqa: E402
from bridges.contracts.chat import ChatMessageStatus, ChatMode  # noqa: E402
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence  # noqa: E402
from bridges.contracts.projects import ObjectDomain  # noqa: E402
from bridges.contracts.workflows import RunContextEnvelope  # noqa: E402
from bridges.lifecycle.catalog import delete_account_rows  # noqa: E402
from bridges.observability.service import ObservabilityService  # noqa: E402
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.automatic import (  # noqa: E402
    AutomaticProfileService,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission  # noqa: E402
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.service import ProfileService  # noqa: E402
from bridges.storage.database import BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
JOG_TEXT = "我喜欢跑步"
SLICE_MARKER = "以下是本轮为你参考的已授权信息"
DIRECTIVE_TURN = "这轮先不用我的画像，帮我安排复习计划"
STUDY_TEXT = "我在准备雅思考试"


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


class _CapturingStreamAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self, capability: CapabilityRecord, run_context: RunContextEnvelope, payload: dict[str, Any]
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="好的。")
        yield StreamChunk(kind="done")


class _HookedRepository(ConversationRepository):
    """在「本次上下文说明」首次落库时注入一次回调（模拟发送前竞态）。"""

    def __init__(self, database: BridgesDatabase, hook: Callable[[], None] | None) -> None:
        super().__init__(database)
        self._hook = hook

    def update_message_context_note(self, account_id, message_id, context_note, updated_at):
        updated = super().update_message_context_note(
            account_id, message_id, context_note, updated_at
        )
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()
        return updated


class _ChatDemo:
    """真实聊天管线：SQLite 会话库 + 原子/自动画像服务 + 捕获载荷。"""

    def __init__(self, tmp: Path, hook: Callable[[], None] | None = None) -> None:
        self.database = BridgesDatabase(tmp / "chat.db")
        self.database.initialize()
        four = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=SqliteFourDimensionProfileRepository(self.database),
        )
        self.atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(self.database))
        self.automatic = AutomaticProfileService(
            four_dimension_service=four,
            repository=SqliteAutomaticProfileRepository(self.database),
            atomic_profile_service=self.atomic,
        )
        registry = CapabilityRegistry()
        registry.register(
            CapabilityRecord(
                name="qwen_text_chat",
                version="1",
                kind=CapabilityKind.MODEL,
                vendor="qwen",
                region="cn-beijing",
                model_id="qwen3.7-plus-2026-05-26",
                input_schema_version="chat-messages-v1",
                output_schema_version="chat-completion-v1",
            )
        )
        gateway = ModelGateway(registry)
        self.adapter = _CapturingStreamAdapter()
        gateway.register_adapter("qwen_text_chat", "1", self.adapter)
        self.chat = ChatService(
            repository=_HookedRepository(self.database, hook),
            gateway=gateway,
            profile_service=ProfileService(repository=InMemoryProfileRepository()),
            automatic_profile_service=self.automatic,
            four_dimension_profile_service=four,
            atomic_profile_service=self.atomic,
            observability_service=ObservabilityService(),
        )
        self.conversation_id = self.chat.create_conversation(ACCOUNT).conversation_id
        self.turn = 0

    def turn_now(self, content: str) -> Any:
        self.turn += 1
        _, assistant, _ = self.chat.start_generation(ACCOUNT, self.conversation_id, content)
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                RunContextEnvelope(
                    run_id=f"run-{self.turn}",
                    account_id=ACCOUNT,
                    project_id=self.conversation_id,
                    workflow_name="chat",
                    workflow_version="1",
                    object_domain=ObjectDomain.PERSONAL_VAULT,
                    submitted_at=datetime.now(UTC),
                ),
            )
        )
        self.automatic.run_retry_tick()
        final = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def payload_text(self) -> str:
        payload = self.adapter.payloads[-1]
        return "\n".join(str(m.get("content", "")) for m in payload["messages"])

    def items(self) -> list[str]:
        return [item.text for item in self.atomic.projections(ACCOUNT)]


def _fact(text: str) -> ProfileFactSubmission:
    return ProfileFactSubmission(
        text=text,
        dimension=FourDimension.KNOWLEDGE_INTEREST,
        confidence=FourDimensionConfidence.MEDIUM,
        evidence_message_id="demo-replay",
    )


def journey_1_forget_then_synonym(script: _Script, tmp: Path) -> None:
    script.say("[1] 忘掉后同义新提及不恢复")
    database = BridgesDatabase(tmp / "forget.db")
    database.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))

    item = atomic.remember(ACCOUNT, JOG_TEXT, source_message_id="m-remember")
    script.say(f"    记住：{JOG_TEXT}（条目 {item.profile_item_id[:8]}…）")
    result = atomic.forget(ACCOUNT, "跑步")
    script.say(f"    忘掉「跑步」：{result.status.value}，命中 {result.matched_count} 条")
    script.check(result.matched_count == 1, "忘掉命中 1 条")

    deferred = atomic.commit_fact(ACCOUNT, _fact("我喜欢慢跑"))
    script.check(deferred is None, "同义新提及（我喜欢慢跑）暂缓写入，不恢复")
    script.check(atomic.list_items(ACCOUNT) == [], "活动条目为 0")
    database.close()


def journey_2_restore_scope(script: _Script, tmp: Path) -> None:
    script.say("[2] 恢复只解除指定范围")
    database = BridgesDatabase(tmp / "restore.db")
    database.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))

    run_item = atomic.remember(ACCOUNT, JOG_TEXT)
    home_item = atomic.remember(ACCOUNT, "我住在南昌")
    atomic.delete_item(ACCOUNT, run_item.profile_item_id, run_item.version)
    atomic.delete_item(ACCOUNT, home_item.profile_item_id, home_item.version)
    script.say("    删除两条：我喜欢跑步 / 我住在南昌")

    atomic.remember(ACCOUNT, JOG_TEXT, source_message_id="m-restore")
    script.check(atomic.list_items(ACCOUNT) and [i.text for i in atomic.list_items(ACCOUNT)] == [JOG_TEXT],
                 "明确重新记住只恢复跑步")
    replayed = atomic.commit_fact(ACCOUNT, _fact("我住在南昌"))
    script.check(replayed is None, "未指定的墓地方向：来源重放仍被拦截")
    database.close()


def journey_3_turn_controls(script: _Script, tmp: Path) -> None:
    script.say("[3] 本轮不用与发送前使用控制（真实聊天回合）")
    demo = _ChatDemo(tmp)
    demo.atomic.remember(ACCOUNT, STUDY_TEXT, source_message_id="m-study")

    final = demo.turn_now("我在准备雅思考试，帮我安排复习")
    script.check(SLICE_MARKER in demo.payload_text(), "前置：相关提问正常注入画像切片")

    final = demo.turn_now(DIRECTIVE_TURN)
    note = final.context_note
    script.check(SLICE_MARKER not in demo.payload_text(), "本轮不用：切片未注入")
    script.check(note is not None and note.state.value == "off", "披露为 OFF")
    script.check(demo.items() == [STUDY_TEXT], "只影响本轮使用，不删除信息")
    script.check("【本轮长期信息使用限制】" in demo.payload_text(), "控制语义进入模型合同")

    demo.turn_now("我在准备雅思考试，帮我安排复习")
    script.check(SLICE_MARKER in demo.payload_text(), "下一轮恢复正常使用")

    race_holder: dict[str, _ChatDemo] = {}

    def disable_usage() -> None:
        race_holder["demo"].automatic.set_account_controls(ACCOUNT, usage_enabled=False)

    race = _ChatDemo(tmp / "race", hook=disable_usage)
    race_holder["demo"] = race
    race.atomic.remember(ACCOUNT, STUDY_TEXT, source_message_id="m-study")
    final = race.turn_now("我在准备雅思考试，帮我安排复习")
    script.check(SLICE_MARKER not in race.payload_text(), "编译后关闭使用：旧切片未进入发送载荷")
    script.check(
        final.context_note is not None and final.context_note.profile_item_count == 0,
        "披露如实降级（条目数 0）",
    )
    race.database.close()
    demo.database.close()


def journey_4_account_deletion(script: _Script, tmp: Path) -> None:
    script.say("[4] 账号删除后迟到 worker 不重建内容")

    class _FailExtractor:
        version = "demo-fail-v1"

        def extract(self, *, message_id: str, **_ignored: object):
            raise RuntimeError("网关暂时不可用")

    database = BridgesDatabase(tmp / "delete.db")
    database.initialize()
    four = FourDimensionProfileService(
        source_repository=InMemoryProfileRepository(),
        repository=SqliteFourDimensionProfileRepository(database),
    )
    atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(database))
    state = SqliteAutomaticProfileRepository(database)
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=state,
        extractor=_FailExtractor(),
        atomic_profile_service=atomic,
    )
    automatic.preprocess_message(
        ACCOUNT,
        conversation_id="c1",
        message_id="m-late",
        content="我喜欢Python",
        run_id="r-late",
        mode="companion",
    )
    task = state.list_tasks(ACCOUNT)[0]
    script.say(f"    删除前：任务 {task.task_id[:8]}…（PENDING，含来源快照记账）")

    with database.transaction():
        database.connection.execute(
            "INSERT INTO account_deletions(deletion_id, account_id, status,"
            " retry_count, last_error, pending_hashes, started_at)"
            " VALUES (?, ?, 'deleting', 0, NULL, '[]', ?)",
            ("demo-deletion", ACCOUNT, datetime.now(UTC).isoformat()),
        )
    delete_account_rows(database, ACCOUNT)
    script.check(not state.account_exists(ACCOUNT), "删除记录落地：账户判定为已删除")

    status = automatic._run_retry_task(task, task.attempts + 1)  # noqa: SLF001 - 模拟迟到 worker
    script.say(f"    迟到 worker 结论：{status.value}")
    runs = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_extraction_runs WHERE account_id = ?",
        (ACCOUNT,),
    ).fetchone()
    tasks = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_extraction_tasks WHERE account_id = ?",
        (ACCOUNT,),
    ).fetchone()
    script.check(int(runs["count"]) == 0, "运行记账 0 行（来源快照未重建）")
    script.check(int(tasks["count"]) == 0, "任务记账 0 行")
    script.check(atomic.list_items(ACCOUNT) == [], "画像条目 0 条")
    database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 08 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(f"环境：conda agent / Python {sys.version.split()[0]}；临时库 .tmp/issue08-demo-*")

    tmp_root = REPO_ROOT / ".tmp" / f"issue08-demo-{stamp}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    script.say("")
    journey_1_forget_then_synonym(script, tmp_root)
    script.say("")
    journey_2_restore_scope(script, tmp_root)
    script.say("")
    journey_3_turn_controls(script, tmp_root)
    script.say("")
    journey_4_account_deletion(script, tmp_root)

    script.say("")
    script.say(f"结果：{'全部通过' if script.failures == 0 else f'{script.failures} 项失败'}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
