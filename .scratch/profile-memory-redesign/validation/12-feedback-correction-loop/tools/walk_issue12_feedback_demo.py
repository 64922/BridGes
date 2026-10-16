"""画像工单 12 走查：反馈修正闭环（确定性）。

覆盖七个用户可见旅程（真实 SQLite 聊天仓库 + 真实画像服务，不调用外部
模型、不读取任何凭据；生成用确定性替身回答）：

[1] 身份纠错：定位旧回答采用的条目并定点改写；修正回合直接重写上一轮
    回答（提示块可见）；同会话后续与全新会话复问都不再沿用旧结论。
[2] 本轮要求与表达投诉：只调整本轮回答，零长期写入。
[3] 长期偏好：「以后先给结论」写入长期记忆并当轮可见。
[4] 指向不明：只请用户澄清，零写入。
[5] 保存失败注入：回答照常修正并如实说明未写入（不声称已更新），旧条目
    保留。
[6] 生成失败注入不回收已写入的纠正；重复反馈与生成重试不重复写记忆；
    重试仍渲染同一反馈修正关系。
[7] 删除竞态与过期版本守卫：目标被删除如实报告未写入（NO_ACTIVE_RECORD），
    版本过期如实报告失败（FAILED），都不误改其他条目。

用法：
    python .scratch/profile-memory-redesign/validation/12-feedback-correction-loop/tools/walk_issue12_feedback_demo.py \
        --out .scratch/profile-memory-redesign/validation/12-feedback-correction-loop/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（`.tmp/issue12-demo-*`），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai import ModelGateway  # noqa: E402
from bridges.ai.adapters import StreamChunk  # noqa: E402
from bridges.ai.capability_registry import CapabilityRegistry  # noqa: E402
from bridges.chat.repository import ConversationRepository  # noqa: E402
from bridges.chat.service import ChatService  # noqa: E402
from bridges.contracts.ai import CapabilityKind, CapabilityRecord  # noqa: E402
from bridges.contracts.chat import ChatMessageStatus  # noqa: E402
from bridges.contracts.feedback_correction import (  # noqa: E402
    FeedbackCorrectionKind,
    FeedbackCorrectionRoute,
    FeedbackMemoryStatus,
)
from bridges.contracts.projects import ObjectDomain  # noqa: E402
from bridges.contracts.workflows import RunContextEnvelope  # noqa: E402
from bridges.observability.service import ObservabilityService  # noqa: E402
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    AtomicProfileError,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
)
from bridges.profiles.automatic import (  # noqa: E402
    AutomaticProfileService,
    InMemoryAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.service import ProfileService  # noqa: E402
from bridges.storage.database import BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
FEEDBACK_MARKER = "【本轮是对上一轮回答的反馈修正】"
WRITTEN_COPY = "记忆结果：本次修改已写入长期记忆"
HONEST_COPY = "不得声称已经记住或已经更新画像"
CORRECTION_TEXT = "你记错了，我不是研究生，我是本科生。"


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


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
    )


def _context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=ACCOUNT,
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


class _CapturingStreamAdapter:
    """确定性替身回答；录制最终载荷供提示块断言。"""

    def __init__(self, chunks: list[StreamChunk] | None = None) -> None:
        self.payloads: list[dict[str, object]] = []
        self._chunks = chunks

    def stream_call(self, capability, run_context, payload):  # noqa: ANN001
        self.payloads.append(payload)
        chunks = self._chunks or [
            StreamChunk(kind="delta", delta="好的。"),
            StreamChunk(kind="done"),
        ]
        yield from chunks


class _Env:
    """真实聊天仓库 + 内存画像服务 + 确定性生成替身。"""

    def __init__(self, tmp: Path) -> None:
        self.database_path = tmp / "bridges-feedback.db"
        self.database = BridgesDatabase(self.database_path)
        self.database.initialize()
        self.four_dimensions = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=InMemoryFourDimensionProfileRepository(),
        )
        self.atomic = AtomicProfileService(
            self.four_dimensions, InMemoryAtomicProfileRepository()
        )
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four_dimensions,
            repository=InMemoryAutomaticProfileRepository(),
            atomic_profile_service=self.atomic,
        )
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        self.gateway = ModelGateway(registry)
        self.adapter = _CapturingStreamAdapter()
        self.gateway.register_adapter("qwen_text_chat", "1", self.adapter)
        self.chat = ChatService(
            repository=ConversationRepository(self.database),
            gateway=self.gateway,
            profile_service=ProfileService(repository=InMemoryProfileRepository()),
            automatic_profile_service=self.automatic,
            four_dimension_profile_service=self.four_dimensions,
            atomic_profile_service=self.atomic,
            observability_service=ObservabilityService(),
        )
        self.conversation_id = self.chat.create_conversation(ACCOUNT).conversation_id
        self.turn = 0

    def ask(self, content: str):
        _, assistant, _ = self.chat.start_generation(
            ACCOUNT, self.conversation_id, content
        )
        self.turn += 1
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                _context(f"run-{self.turn}"),
            )
        )
        final = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def ask_in_new_conversation(self, content: str):
        other = self.chat.create_conversation(ACCOUNT).conversation_id
        _, assistant, _ = self.chat.start_generation(ACCOUNT, other, content)
        self.turn += 1
        list(
            self.chat.stream_generation(
                ACCOUNT, other, assistant.message_id, _context(f"run-{self.turn}")
            )
        )
        final = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert final is not None and final.status == ChatMessageStatus.DONE
        return final

    def retry(self, message_id: str):
        _, assistant, _ = self.chat.retry_generation(
            ACCOUNT, self.conversation_id, message_id
        )
        list(
            self.chat.stream_generation(
                ACCOUNT,
                self.conversation_id,
                assistant.message_id,
                _context("retry-run"),
            )
        )
        projection = self.chat.message_projection(ACCOUNT, assistant.message_id)
        assert projection is not None
        return projection

    def config(self, message_id: str) -> dict[str, object]:
        run = self.chat._repo.get_run_by_message(  # noqa: SLF001
            ACCOUNT, message_id
        )
        assert run is not None
        return dict(run.config or {})

    def payload_text(self) -> str:
        return "\n".join(
            str(message["content"]) for message in self.adapter.payloads[-1]["messages"]
        )

    def use_failing_gateway(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_chat_capability())
        gateway = ModelGateway(registry)
        gateway.register_adapter(
            "qwen_text_chat",
            "1",
            _CapturingStreamAdapter(
                chunks=[
                    StreamChunk(kind="delta", delta="部分"),
                    StreamChunk(
                        kind="error",
                        error_code="rate_limit",
                        error_message="vendor msg",
                    ),
                ]
            ),
        )
        self.chat._gateway = gateway  # noqa: SLF001

    def texts(self) -> set[str]:
        return {item.text for item in self.atomic.list_items(ACCOUNT)}

    def close(self) -> None:
        self.database.close()


def journey_1_identity_correction(script: _Script, tmp: Path) -> None:
    script.say("[1] 身份纠错：条目定点改写、修正回答、同会话与全新会话复问")
    env = _Env(tmp)
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    first = env.ask("我研究生读得怎么样？")
    script.check("我是研究生" in env.payload_text(), "前置：第一轮采用了旧结论")

    corrected = env.ask(CORRECTION_TEXT)

    payload = env.payload_text()
    metadata = env.config(corrected.message_id)["feedback_correction"]
    script.check(FEEDBACK_MARKER in payload, "修正回合渲染反馈修正提示块")
    script.check(WRITTEN_COPY in payload, "本轮如实说明记忆已写入")
    script.check(env.texts() == {"我是本科生"}, "条目被定点改写（旧值不再活动）")
    script.check(
        metadata["kind"] == FeedbackCorrectionKind.FACT_CORRECTION.value
        and metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value,
        "运行元数据记录纠正类别与写入结果",
    )
    script.check(
        metadata["original_message_id"] == first.message_id
        and bool(metadata["original_run_id"])
        and bool(metadata["original_slice_id"]),
        "反馈与原回答/原运行/原采用切片的关系已持久化",
    )
    projection = corrected.feedback_correction
    script.check(
        projection is not None
        and projection.original_message_id == first.message_id,
        "消息投影可读反馈关系",
    )

    env.ask("我本科生读得怎么样？")
    same = env.payload_text()
    script.check(
        "我是本科生" in same and "我是研究生" not in same,
        "同会话后续回答不再沿用旧结论",
    )
    env.ask_in_new_conversation("我本科生读得怎么样？")
    fresh = env.payload_text()
    script.check(
        "我是本科生" in fresh and "我是研究生" not in fresh,
        "全新会话复问不再复发旧结论（错误复发样本：纠正后不再出现）",
    )
    env.close()


def journey_2_turn_scoped_requests(script: _Script, tmp: Path) -> None:
    script.say("[2] 本轮要求与表达投诉：只调整本轮回答，零长期写入")
    env = _Env(tmp)
    env.ask("解释一下贝叶斯定理。")

    session = env.ask("今天赶时间，简短一点。")
    session_meta = env.config(session.message_id)["feedback_correction"]
    script.check(
        session_meta["kind"] == FeedbackCorrectionKind.TURN_REQUEST.value
        and session_meta["memory_status"] == FeedbackMemoryStatus.NOT_APPLICABLE.value,
        "本轮要求只作用于当前修正回答（不适用长期记忆）",
    )

    expression = env.ask("身份没错，但解释太难了。")
    expression_meta = env.config(expression.message_id)["feedback_correction"]
    script.check(
        expression_meta["kind"] == FeedbackCorrectionKind.EXPRESSION_QUALITY.value
        and "用更简单、更通俗的表达" in env.payload_text(),
        "表达投诉调整当轮表达",
    )
    script.check(env.texts() == set(), "两类反馈均零长期写入")
    env.close()


def journey_3_long_term_preference(script: _Script, tmp: Path) -> None:
    script.say("[3] 长期偏好：写入长期记忆并当轮可见")
    env = _Env(tmp)
    env.ask("解释一下贝叶斯定理。")

    preference = env.ask("以后都先给结论。")

    metadata = env.config(preference.message_id)["feedback_correction"]
    payload = env.payload_text()
    script.check(
        metadata["kind"] == FeedbackCorrectionKind.LONG_TERM_PREFERENCE.value
        and metadata["memory_status"] == FeedbackMemoryStatus.WRITTEN.value,
        "偏好反馈已写入长期记忆",
    )
    script.check("以后都先给结论。" in env.texts(), "条目正文保留用户原话")
    script.check(
        FEEDBACK_MARKER in payload and "以后都先给结论。" in payload,
        "当轮修正回答采用该偏好",
    )
    env.close()


def journey_4_unclear(script: _Script, tmp: Path) -> None:
    script.say("[4] 指向不明：只请用户澄清，零写入")
    env = _Env(tmp)
    env.ask("解释一下贝叶斯定理。")

    unclear = env.ask("不是这个意思。")

    metadata = env.config(unclear.message_id)["feedback_correction"]
    script.check(
        FEEDBACK_MARKER in env.payload_text()
        and "请用一两句话请用户明确指出" in env.payload_text(),
        "修正回答只请求澄清",
    )
    script.check(
        metadata["clarification_required"] is True
        and metadata["memory_status"] == FeedbackMemoryStatus.NOT_APPLICABLE.value
        and env.texts() == set(),
        "不猜测批量修改、零写入",
    )
    env.close()


def journey_5_memory_failure(script: _Script, tmp: Path) -> None:
    script.say("[5] 保存失败注入：回答照常修正且诚实，不回收旧条目")
    env = _Env(tmp)
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")

    original = env.atomic.correct_item

    def _fail(*args: object, **kwargs: object):
        raise AtomicProfileError("storage unavailable")

    env.atomic.correct_item = _fail  # type: ignore[method-assign]
    try:
        corrected = env.ask(CORRECTION_TEXT)
    finally:
        env.atomic.correct_item = original  # type: ignore[method-assign]

    payload = env.payload_text()
    metadata = env.config(corrected.message_id)["feedback_correction"]
    script.check(FEEDBACK_MARKER in payload, "保存失败仍给出修正回答")
    script.check(HONEST_COPY in payload, "如实说明未写入（不声称已更新）")
    script.check(
        metadata["memory_status"] == FeedbackMemoryStatus.FAILED.value,
        "运行元数据如实记录失败",
    )
    script.check("我是研究生" in env.texts(), "旧条目保留（未被静默删除）")
    env.close()


def journey_6_generation_failure_and_idempotency(
    script: _Script, tmp: Path
) -> None:
    script.say("[6] 生成失败不回滚；重复反馈与重试不重复写记忆")
    env = _Env(tmp)
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    env.ask("我研究生读得怎么样？")
    env.ask(CORRECTION_TEXT)
    item = next(iter(env.atomic.list_items(ACCOUNT)))
    version = item.version

    script.check(env.texts() == {"我是本科生"}, "前置：纠正已写入")

    env.ask(CORRECTION_TEXT)
    remaining = env.atomic.list_items(ACCOUNT)
    script.check(
        len(remaining) == 1 and remaining[0].version == version,
        "重复同一反馈幂等（版本不递增、条目不重复）",
    )

    env.use_failing_gateway()
    _, assistant, _ = env.chat.start_generation(
        ACCOUNT, env.conversation_id, CORRECTION_TEXT
    )
    list(
        env.chat.stream_generation(
            ACCOUNT,
            env.conversation_id,
            assistant.message_id,
            _context("fail-run"),
        )
    )
    failed = env.chat.message_projection(ACCOUNT, assistant.message_id)
    script.check(
        failed is not None
        and failed.status == ChatMessageStatus.ERROR
        and "我是本科生" in env.texts(),
        "改答失败不回滚已成功纠正、不假称完成",
    )
    script.check(
        failed is not None
        and failed.feedback_correction is not None
        and failed.feedback_correction.memory_status
        is FeedbackMemoryStatus.WRITTEN,
        "失败回答的投影仍如实保留记忆结果",
    )

    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    env.gateway = ModelGateway(registry)
    env.adapter = _CapturingStreamAdapter()
    env.gateway.register_adapter("qwen_text_chat", "1", env.adapter)
    env.chat._gateway = env.gateway  # noqa: SLF001
    retried = env.retry(failed.message_id if failed is not None else "")
    script.check(
        retried.status == ChatMessageStatus.DONE
        and FEEDBACK_MARKER in env.payload_text()
        and WRITTEN_COPY in env.payload_text(),
        "重试渲染同一反馈修正关系与记忆结果",
    )
    versions = [entry.version for entry in env.atomic.list_items(ACCOUNT)]
    script.check(
        versions == [version],
        "重试不重复写记忆（版本保持不变）",
    )
    env.close()


def journey_7_delete_and_stale_guards(script: _Script, tmp: Path) -> None:
    script.say("[7] 删除竞态与过期版本：如实报告，不误改其他条目")
    env = _Env(tmp)

    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed")
    target = next(iter(env.atomic.list_items(ACCOUNT)))
    route = FeedbackCorrectionRoute(
        kind=FeedbackCorrectionKind.FACT_CORRECTION,
        target_item_id=target.profile_item_id,
        target_item_version=target.version,
        target_item_text=target.text,
        new_fact_text="我是本科生",
        has_replacement=True,
    )
    env.atomic.delete_item(ACCOUNT, target.profile_item_id, target.version)
    deleted = env.automatic.process_synchronous_controls(
        ACCOUNT,
        conversation_id="c-race",
        message_id="m-race-deleted",
        content=CORRECTION_TEXT,
        run_id="r-race",
        mode="companion",
        feedback_route=route,
    )
    assert deleted is not None and deleted.feedback is not None
    script.say(f"    目标已删除 → {deleted.feedback.memory_status.value}")
    script.check(
        deleted.feedback.memory_status is FeedbackMemoryStatus.NO_ACTIVE_RECORD
        and deleted.feedback.applied_item_id is None
        and env.texts() == set(),
        "目标被删除：报告未写入、不猜改其他条目",
    )

    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-seed-2")
    stale_target = next(iter(env.atomic.list_items(ACCOUNT)))
    stale_route = FeedbackCorrectionRoute(
        kind=FeedbackCorrectionKind.FACT_CORRECTION,
        target_item_id=stale_target.profile_item_id,
        target_item_version=stale_target.version,
        target_item_text=stale_target.text,
        new_fact_text="我是本科生",
        has_replacement=True,
    )
    env.atomic.remember(ACCOUNT, "我是研究生", source_message_id="m-newer")
    stale = env.automatic.process_synchronous_controls(
        ACCOUNT,
        conversation_id="c-race",
        message_id="m-race-stale",
        content=CORRECTION_TEXT,
        run_id="r-race-2",
        mode="companion",
        feedback_route=stale_route,
    )
    assert stale is not None and stale.feedback is not None
    script.say(f"    版本过期 → {stale.feedback.memory_status.value}")
    script.check(
        stale.feedback.memory_status is FeedbackMemoryStatus.FAILED
        and env.texts() == {"我是研究生"},
        "版本过期：如实报告失败、原条目不变",
    )
    env.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 12 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        "临时库 .tmp/issue12-demo-*"
    )

    tmp_root = REPO_ROOT / ".tmp" / f"issue12-demo-{stamp}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    for index, journey in enumerate(
        (
            journey_1_identity_correction,
            journey_2_turn_scoped_requests,
            journey_3_long_term_preference,
            journey_4_unclear,
            journey_5_memory_failure,
            journey_6_generation_failure_and_idempotency,
            journey_7_delete_and_stale_guards,
        )
    ):
        script.say("")
        journey(script, tmp_root / f"case-{index}")

    script.say("")
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
