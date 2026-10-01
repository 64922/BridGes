"""V2 Issue 06：统一流式正文、终态存储与断线重放（append-only delta）。

覆盖验收标准：

1. ``delta`` 恒为「追加新内容」：把一次生成期间下发的全部增量依次拼接，逐字
   等于终态正文（``assembler.finish().content``）、落库正文与重放正文；不存在
   「整段正文当作伪增量重发」的通道。
2. 跨块（逐字符流式）与变更前缀：受保护片段在闭合/确认前暂缓，已下发前缀不再
   被改写；含多个受保护片段、空块、未闭合起始符（含 `` `` ``/``http:/``/``12 ``）
   的边界都被覆盖。
3. 断线游标重放与终态重载：从任意已保存游标重放，拼接增量等于落库正文；终态
   重载（重新读取消息）与流式期间所见一致。
4. 停止与旧租约：终态之后不再追加任何内容/状态事件（迟到 delta/stage 被
   丢弃；``node`` 进度属于图内遥测，可在实时 done 之后继续补记），停止不
   触发后台润色、不重新获得额度。
5. 历史事件与既有 SSE 保持可读：不新增事件类型、不改变载荷结构，旧记录原样
   可解析。

协议级用例直接驱动 :mod:`bridges.chat.stream_protection`；链路级用例走正式
持久化生成运行（真实执行器 + SSE 游标回放），验证落库正文与事件重放。
"""

from __future__ import annotations

import random
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import StreamChunk
from bridges.chat.fact_protection import ProtectionIntent
from bridges.chat.stream_protection import (
    STREAM_CONSISTENCY_PROTOCOL_VERSION,
    StreamProtectionAssembler,
    safe_append_boundary,
)
from bridges.config import get_settings
from bridges.contracts.chat import ChatMessageStatus
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)

# ---------------------------------------------------------------------------
# 协议级：追加式装配器
# ---------------------------------------------------------------------------


def _drive(
    assembler: StreamProtectionAssembler,
    candidate: str,
    *,
    step: int = 1,
) -> tuple[str, str]:
    """把候选正文按固定块长逐块推入，返回（拼接增量, 终态正文）。"""
    deltas: list[str] = []
    for end in range(step, len(candidate) + step, step):
        delta = assembler.update(candidate[:end])
        if delta:
            deltas.append(delta)
    flush, result = assembler.finish()
    if flush:
        deltas.append(flush)
    return "".join(deltas), result.content


def test_protocol_version_is_append_only() -> None:
    assert STREAM_CONSISTENCY_PROTOCOL_VERSION == "append-only-delta-v1"


def test_stitched_deltas_equal_terminal_body() -> None:
    """逐字符流式：拼接增量逐字等于终态正文。"""
    original = "请原样保留：甲 10 ms；乙 20 ms，示例见 `x = 1`。"
    candidate = "好的：甲 11 ms；乙 21 ms，示例见 `x = 2`。"
    assembler = StreamProtectionAssembler(original)

    stitched, content = _drive(assembler, candidate)

    assert stitched == content
    # 受保护片段按源回填，模型改写的前缀不进入正文。
    assert content == "好的：甲 10 ms；乙 20 ms，示例见 `x = 1`。"


def test_changed_prefix_never_emitted_as_pseudo_delta() -> None:
    """变更前缀：首片段被改写，未确认前不得下发任何被改写内容。"""
    original = "甲 10 ms；乙 20 ms"
    candidate = "甲 11 ms；乙 21 ms"
    assembler = StreamProtectionAssembler(original)

    deltas: list[str] = []
    for end in range(1, len(candidate) + 1):
        delta = assembler.update(candidate[:end])
        if delta:
            deltas.append(delta)
        # 流式期间下发的任何内容都不得包含被改写的数值。
        assert "11 ms" not in "".join(deltas)
        assert "21 ms" not in "".join(deltas)
    flush, result = assembler.finish()
    deltas.append(flush)

    assert "".join(deltas) == result.content == original


def test_multiple_protected_fragments_and_empty_chunks() -> None:
    """多个受保护片段 + 空块：空块与重复候选不产生增量。"""
    original = "链接 https://example.com/a 与 `code`，数值 3.5 ms。"
    candidate = "链接 https://example.com/b 与 `code`，数值 3.5 ms。"
    assembler = StreamProtectionAssembler(original)

    assert assembler.update("") == ""
    first = assembler.update(candidate[:4])
    assert assembler.update(candidate[:4]) == ""  # 重复候选：无新增
    stitched, content = _drive(assembler, candidate, step=3)

    assert first + stitched == content == original


def test_unclosed_double_backtick_is_held() -> None:
    """回归：`` ``a`b```code` `` 的首个反引号不构成空行内代码，必须暂缓。

    旧实现把两个反引号配成空行内代码，漏判未闭合起始符，导致随后被改写的
    前缀提前下发。
    """
    original = "如下：``a`b```x = 1`\n5 kg解释"
    candidate = "如下：``a`b```code`\n5 kg解释"
    assembler = StreamProtectionAssembler(original)

    assert safe_append_boundary(candidate, assembler._sources) == 3  # noqa: SLF001

    stitched, content = _drive(assembler, candidate)
    assert stitched == content == original


def test_transient_fragment_ownership_change_is_held() -> None:
    """回归：跨块时片段归属会变（先围栏、后行内代码），起点必须暂缓。"""
    original = "解释如下：`x = 1````\nraw\n````x = 1`"
    candidate = "解释如下：`a$b````\nraw\n````x = 1`解释如下："
    assembler = StreamProtectionAssembler(
        original, intent=ProtectionIntent.VERBATIM
    )

    stitched, content = _drive(assembler, candidate)
    assert stitched == content
    assert "`a$b" not in stitched


@pytest.mark.parametrize(
    ("original", "candidate"),
    [
        ("https://a", "http://a.com/x"),
        ("见 https://example.com/a", "见 http://a.com/x"),
        ("（注）55 kg（注）（注）样本 ", "（注）12 %（注）（注）样本 "),
        ("总价 100 °C。", "总价 12 m/s。"),
        ("12 m", "55 kg（注）m"),
    ],
)
def test_partial_fragments_are_held_until_closed(
    original: str, candidate: str
) -> None:
    """部分片段（单斜杠 URL、尾随数字、单位前缀）在闭合前暂缓。"""
    stitched, content = _drive(StreamProtectionAssembler(original), candidate)
    assert stitched == content == original


def test_delta_is_always_append_only_under_fuzz() -> None:
    """属性化回归：任意分块下增量恒为追加，拼接等于终态正文。"""
    atoms = [
        "如下：", "甲 乙 丙 ", "，", "\n", "值 ", "解释如下：",
        "`code`", "``a`b```c`", "```\nraw\n```", "$e = m c^2$",
        "https://example.com/a", "http:/", "htt", "h",
        '{"a": 1}', "[1]", "55 kg", "12 ", "12 m/s", "3.5 ms",
    ]
    for seed in range(3000):
        rng = random.Random(seed)
        original = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 6)))
        candidate = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 6)))
        step = rng.randint(1, 4)
        stitched, content = _drive(
            StreamProtectionAssembler(original), candidate, step=step
        )
        assert stitched == content, (seed, original, candidate, stitched, content)


def test_reference_intent_appends_verbatim() -> None:
    """普通问答（REFERENCE）不设保护区：无源片段时边界即全文长度。"""
    assembler = StreamProtectionAssembler(
        "解释一下 55 kg", intent=ProtectionIntent.REFERENCE
    )
    assert assembler.update("55 kg 是质量。") == "55 kg 是质量。"


def _removable_reference_strip(raw: str) -> str:
    """模拟降级学习路径 ``ensure_unverified_teaching_prefix`` 的确定性剥离。

    完整 URL、``[web-N]``/``[arxiv-N]`` 与越界 ``[reference:N]`` 会在候选
    文本构造时被移除；部分完成中的记号仍留在候选里——这正是已发前缀可能
    被后续前处理“改短”的来源。
    """
    stripped = re.sub(r"\[(?:web|arxiv)-[A-Za-z0-9_-]+\]", "", raw)
    stripped = re.sub(r"\[reference:\d+\]", "", stripped)
    return re.sub(r"https?://[^\s)\]>，。]+", "", stripped)


def test_partial_url_tail_is_held_before_degraded_strip() -> None:
    """回归：完整 URL 被降级前处理剥离时，已发出的部分 URL 不得先行下发。

    无原样保留源时旧实现边界即全文长度，``http://`` 会先被追加；随后前处理
    把补全的 URL 整段剥离，终态收敛只能整段重发（伪增量），前端累加后与
    落库正文分叉。
    """
    assembler = StreamProtectionAssembler("")
    deltas: list[str] = []
    raw = ""
    for chunk in ("结果见 http://", "example.com/a，以上。"):
        raw += chunk
        delta = assembler.update(_removable_reference_strip(raw))
        if delta:
            deltas.append(delta)
    flush, result = assembler.finish()
    if flush:
        deltas.append(flush)

    assert "".join(deltas) == result.content
    assert "http" not in result.content


def test_unclosed_web_citation_tail_is_held() -> None:
    """回归：``[web-N]`` 补全后被降级前处理剥离，未闭合 ``[`` 之前不得下发。"""
    assembler = StreamProtectionAssembler("")
    deltas: list[str] = []
    raw = ""
    for chunk in ("参考 [web-", "1] 即可。"):
        raw += chunk
        delta = assembler.update(_removable_reference_strip(raw))
        if delta:
            deltas.append(delta)
    flush, result = assembler.finish()
    if flush:
        deltas.append(flush)

    assert "".join(deltas) == result.content
    assert "web-" not in result.content


def test_complete_kept_citation_is_not_held() -> None:
    """保留范围内的完整 ``[reference:N]`` 不因尾部暂缓规则被无故扣留。"""
    assembler = StreamProtectionAssembler("")
    assert assembler.update("见 [reference:1]") == "见 [reference:1]"


def test_source_less_preprocessing_shrink_fuzz() -> None:
    """属性化回归：无源时任意分块 + 降级前处理剥离下，增量拼接恒等于终态。"""
    atoms = [
        "见 ",
        "http://",
        "https://a.com/x",
        "http:/",
        "htt",
        "h",
        "[web-",
        "1]",
        "[arxiv-9]",
        "[reference:2]",
        "[",
        "]",
        "样本 30 个。",
        "结果见 ",
        "。",
    ]
    for seed in range(600):
        rng = random.Random(seed)
        raw = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 6)))
        step = rng.randint(1, 4)
        assembler = StreamProtectionAssembler("")
        stitched = ""
        for end in range(step, len(raw) + step, step):
            stitched += assembler.update(_removable_reference_strip(raw[:end]))
        flush, result = assembler.finish()
        stitched += flush
        assert stitched == result.content, (seed, raw, stitched, result.content)


def test_late_update_after_finish_is_rejected() -> None:
    """终态收敛后，迟到数据绝不推进正文。"""
    assembler = StreamProtectionAssembler("甲 10 ms")
    assembler.update("甲 11 ms")
    _, result = assembler.finish()

    assert assembler.finalized is True
    assert assembler.update("甲 12 ms 追加") == ""
    assert assembler.emitted == result.content


def test_non_append_regression_is_discarded() -> None:
    """已下发区域出现非追加变化：保守丢弃，不产生回退增量。"""
    assembler = StreamProtectionAssembler("")
    assembler.update("甲乙丙")
    assert assembler.emitted == "甲乙丙"
    # 候选把已下发前缀改写：不能用追加语义回退。
    assert assembler.update("甲X丙丁") == ""
    assert assembler.emitted == "甲乙丙"


# ---------------------------------------------------------------------------
# 链路级：持久化运行 / 落库正文 / 游标重放
# ---------------------------------------------------------------------------

USER_QUERY = "帮我自然地讲解这段实验记录，不要改动事实：\n甲 10 ms；乙 20 ms。"
DRIFTED_ANSWER = "好的，帮你顺一下：甲 11 ms；乙 21 ms，见 `x = 1`。"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


class _ChunkedAdapter:
    """确定性分块流式适配器：按固定块长产出候选正文，统计调用次数。"""

    def __init__(self, answer: str, chunk: int = 3) -> None:
        self._answer = answer
        self._chunk = chunk
        self.stream_calls = 0

    def call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": self._answer}
        )

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        self.stream_calls += 1
        for index in range(0, len(self._answer), self._chunk):
            yield StreamChunk(kind="delta", delta=self._answer[index : index + self._chunk])


class _Chain:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    @property
    def deltas(self) -> list[str]:
        return [
            event.payload["delta"]
            for event in self.events
            if event.kind == "delta"
            and event.payload.get("message_id") == self.message_id
        ]


def _run_chain(
    sqlite_app: Any,
    client: TestClient,
    helpers: dict[str, Any],
    *,
    answer: str = DRIFTED_ANSWER,
    chunk: int = 3,
    query: str = USER_QUERY,
    use_scripted: bool = False,
) -> _Chain:
    account = _register(client)
    if use_scripted:
        adapter = sqlite_app.state.test_chat_stream_script
    else:
        adapter = _ChunkedAdapter(answer, chunk)
        sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = helpers["send"](client, conversation_id, content=query)
    run_id = created["run_id"]
    message_id = created["assistant_message"]["message_id"]
    helpers["drive"](sqlite_app)
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001
    events = repo.list_generation_events(account["id"], run_id, 0)
    message = repo.get_message(account["id"], message_id)
    return _Chain(
        account=account,
        adapter=adapter,
        conversation_id=conversation_id,
        run_id=run_id,
        message_id=message_id,
        message=message,
        events=events,
        repo=repo,
    )


def test_stream_events_stitch_to_persisted_body(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """落库正文 == 拼接的持久化 delta 事件（逐字一致），且只有一次模型调用。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)

    assert chain.deltas, "应至少下发一个增量"
    assert "".join(chain.deltas) == chain.message.content
    assert chain.adapter.stream_calls == 1
    assert "11 ms" not in "".join(chain.deltas)
    assert chain.message.content == "好的，帮你顺一下：甲 10 ms；乙 20 ms，见 `x = 1`。"


def test_test_scripted_stream_fixture_drives_real_executor(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """E2E 脚本适配器经正式执行器分块流式：漂移跨 chunk 后事件拼接 == 落库正文。"""
    assert getattr(sqlite_app.state, "test_chat_stream_script", None) is not None
    configured = client.post(
        "/_test/chat-stream-script",
        json={
            "match": "甲 10 ms",
            "chunks": ["好的，帮你顺一下：甲 11", " ms；乙 21 ms，见 `x = 1`。"],
            "delay_ms": 0,
        },
    )
    assert configured.status_code == 200, configured.text

    chain = _run_chain(sqlite_app, client, generation_helpers, use_scripted=True)

    assert "".join(chain.deltas) == chain.message.content
    assert "11 ms" not in "".join(chain.deltas)
    assert chain.message.content == "好的，帮你顺一下：甲 10 ms；乙 20 ms，见 `x = 1`。"


def test_disconnect_cursor_replay_reconstructs_body(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """断线重放：从游标 0 重放能重建完整正文，从中间游标重放得到其后缀。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)
    seqs = [event.seq for event in chain.events]

    from_cursor_zero = generation_helpers["subscribe"](
        client, chain.conversation_id, chain.message_id, 0
    )
    body = "".join(
        payload["delta"] for name, payload in from_cursor_zero if name == "delta"
    )
    assert body == chain.message.content
    non_node = [name for name, _payload in from_cursor_zero if name != "node"]
    assert non_node[-1] == "done"

    mid = seqs[len(seqs) // 2]
    from_mid = generation_helpers["subscribe"](
        client, chain.conversation_id, chain.message_id, mid
    )
    tail = "".join(
        payload["delta"] for name, payload in from_mid if name == "delta"
    )
    assert chain.message.content.endswith(tail)


def test_terminal_reload_matches_streamed_body(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """终态重载：重新读取消息，正文与流式期间所见一致，且终态事件唯一。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)

    reloaded = chain.repo.get_message(chain.account["id"], chain.message_id)
    assert reloaded is not None
    assert reloaded.status == ChatMessageStatus.DONE
    assert reloaded.content == chain.message.content
    assert reloaded.content == "".join(chain.deltas)

    terminal = [event for event in chain.events if event.kind in {"done", "error"}]
    assert len(terminal) == 1
    assert terminal[0].kind == "done"
    # 内容/状态事件以 done 收尾；node 进度（persist_result 等图内遥测）
    # 允许继续补记，见下方 post-terminal 用例。
    non_node_kinds = [event.kind for event in chain.events if event.kind != "node"]
    assert non_node_kinds[-1] == "done"


def test_post_terminal_content_events_are_dropped_but_node_progress_allowed(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """旧租约迟到：终态之后 delta/stage 一律丢弃；node 进度允许补记。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)

    late_stage = chain.repo.append_generation_event(
        chain.account["id"],
        chain.run_id,
        "stage",
        {"message_id": chain.message_id, "stage": "model_generation", "status": "active"},
        datetime.now(UTC),
    )
    late_delta = chain.repo.append_generation_event(
        chain.account["id"],
        chain.run_id,
        "delta",
        {"message_id": "late", "delta": "迟到内容"},
        datetime.now(UTC),
    )
    late_node = chain.repo.append_generation_event(
        chain.account["id"],
        chain.run_id,
        "node",
        {
            "message_id": chain.message_id,
            "node": "persist_result",
            "status": "completed",
        },
        datetime.now(UTC),
    )
    after = chain.repo.list_generation_events(chain.account["id"], chain.run_id, 0)

    assert late_stage == 0
    assert late_delta == 0
    assert late_node > 0
    assert "迟到内容" not in "".join(chain.deltas)
    terminal = [event for event in after if event.kind in {"done", "error"}]
    assert [event.kind for event in terminal] == ["done"]


def test_late_delta_after_terminal_event_is_dropped(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """旧租约迟到：终态事件之后不再追加内容事件（delta 被丢弃）。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)

    seq = chain.repo.append_generation_event(
        chain.account["id"],
        chain.run_id,
        "delta",
        {"message_id": "late", "delta": "迟到内容"},
        datetime.now(UTC),
    )
    after = chain.repo.list_generation_events(chain.account["id"], chain.run_id, 0)

    assert seq == 0
    assert [event.seq for event in after] == [event.seq for event in chain.events]
    assert "迟到内容" not in "".join(chain.deltas)


def test_historical_events_remain_readable(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """历史兼容：既有 delta 事件载荷结构不变，仍可按原样回放。"""
    chain = _run_chain(sqlite_app, client, generation_helpers)

    for event in chain.events:
        if event.kind == "delta":
            # 既有读取端依赖的字段仍在（可新增字段，不可缺失或改名）。
            assert {"message_id", "delta"} <= set(event.payload)
            assert event.payload["message_id"] == chain.message_id
            assert isinstance(event.payload["delta"], str)


class _GatedFragmentAdapter:
    """闸门适配器：逐块放行固定文本，用于流式停止与迟到数据验证。"""

    def __init__(self, chunks: list[str], gates: list[threading.Event]) -> None:
        self._chunks = chunks
        self._gates = gates
        self.stream_calls = 0

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ):
        self.stream_calls += 1
        for chunk, gate in zip(self._chunks, self._gates, strict=True):
            if not gate.wait(timeout=15):
                raise AssertionError("测试闸门未在超时前放行。")
            yield StreamChunk(kind="delta", delta=chunk)


def test_streaming_stop_discards_unclosed_tail_without_new_calls(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """流式停止：未闭合片段不落库，停止后迟到数据不推进正文、不新增模型调用。"""
    account = _register(client)
    gates = [threading.Event(), threading.Event()]
    # 首块以未闭合行内代码收尾：装配器必须暂缓到闭合或终态。
    adapter = _GatedFragmentAdapter(["答案如下：`未闭合", "`继续"], gates)
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    conversation_id = _create_conversation(client)
    created = generation_helpers["send"](
        client, conversation_id, content="请保留 `x = 1` 的写法并解释。"
    )
    message_id = created["assistant_message"]["message_id"]
    repo = sqlite_app.state.chat_service._repo  # noqa: SLF001

    stop_exec, exec_thread = generation_helpers["executor_thread"](sqlite_app)
    try:
        gates[0].set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            probe = repo.get_message(account["id"], message_id)
            if probe is not None and probe.content:
                break
            time.sleep(0.05)
        stopped = client.post(
            f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["message"]["status"] == "stopped"
    finally:
        stop_exec.set()
        exec_thread.join(timeout=5)

    gates[1].set()  # 迟到数据放行
    message = repo.get_message(account["id"], message_id)
    assert message is not None
    assert message.status == ChatMessageStatus.STOPPED
    # 未闭合片段被暂缓，且停止后不被迟到数据推进。
    assert "未闭合" not in message.content
    assert message.content == "答案如下："
    assert adapter.stream_calls == 1
    # 停止终态之后连节点遥测也不得追加：done 收尾的图内进度是唯一例外。
    late_node = repo.append_generation_event(
        account["id"],
        created["run_id"],
        "node",
        {"message_id": message_id, "node": "persist_result", "status": "completed"},
        datetime.now(UTC),
    )
    assert late_node == 0
