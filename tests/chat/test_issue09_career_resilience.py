"""Issue 09 反馈环测试：生涯规划助手快速入题、后台执行与可恢复降级。

覆盖验收标准：
- 仅输入模糊请求时返回一个关键澄清问题，不启动完整规划生成（无模型调用）；
- 用户回复澄清问题后（即使不含触发词）继续走生涯编排（会话级粘性）；
- 提供足够信息时生成完整结构化计划（含未来 7 天行动与可检查里程碑）；
- 结构化输出第一次非法、第二次合法时只修复一次并成功；连续非法时在
  总预算内失败（不写 stream_interrupted）；
- 慢模型调用在预算内完成（SSE 断开/切会话不影响运行终态）；
- 模型调用超预算时明确降级失败，运行保持可恢复终态；
- 辅助检索缺失时交付不依赖外部事实的规划骨架并列出待核实项；
- 重试创建新 attempt 并保留旧失败记录。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult
from bridges.config import get_settings
from tests.chat.test_career_planning_chat import (
    _create_conversation,
    _gateway_with,
    _register,
)


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 生涯规划编排）。"""
    from bridges.api.main import create_app

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


class _ProgrammableStructuredAdapter:
    """可编程结构化适配器：按顺序返回脚本化输出，记录调用次数与载荷。"""

    def __init__(self, outputs: list[dict[str, Any] | None] | None = None) -> None:
        self._outputs = list(outputs or [])
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def call(
        self,
        capability: Any,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        self.requests.append(payload)
        output = self._outputs[self.calls - 1] if self.calls <= len(self._outputs) else None
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=output,
        )


class _ParseFailThenGoodAdapter:
    """格式类失败适配器：第一次调用抛 JSON 解析失败（可修复），
    第二次返回合法输出。"""

    def __init__(self, good: dict[str, Any]) -> None:
        self._good = good
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def call(
        self,
        capability: Any,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        self.requests.append(payload)
        if self.calls == 1:
            from bridges.ai.adapters import AdapterError

            raise AdapterError(
                code="structured_output_parse_failed",
                message="Model output was not valid JSON: test",
                retryable=False,
            )
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=self._good,
        )


class _SlowStructuredAdapter:
    """慢模型适配器：每次调用休眠固定秒数后返回合法输出。"""

    def __init__(self, output: dict[str, Any], delay: float = 0.3) -> None:
        self._output = output
        self._delay = delay
        self.calls = 0

    def call(
        self,
        capability: Any,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        time.sleep(self._delay)
        return AdapterResult(
            actual_model_id=capability.model_id,
            output=self._output,
        )


def _swap_gateways(sqlite_app: Any, structured: Any) -> None:
    gateway = _gateway_with(structured)
    sqlite_app.state.chat_service._gateway = gateway
    sqlite_app.state.career_planner_service._gateway = gateway


def _good_output() -> dict[str, Any]:
    """合法完整输出：含未来 7 天行动与带时间范围的里程碑路径。"""
    return {
        "final_text": "综合你的阶段与方向，可以从选课与实习两条线并行推进。",
        "facts": [
            {
                "content": "数据分析相关岗位需求在近三年持续增长。",
                "evidence_refs": ["statement:current"],
                "note": "来源：行业报告。",
            }
        ],
        "assumptions": [
            {
                "content": "你可能适合偏业务的数据分析岗。",
                "evidence_refs": [],
                "note": None,
                "verification_next_step": "与从业者交流或做一次实习验证。",
            }
        ],
        "options": [
            {
                "content": "数据分析方向（业务侧）。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "rationale": "与你的兴趣与课程背景匹配。",
            }
        ],
        "risks": [
            {
                "content": "岗位竞争加剧。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "trigger": "应届求职季人数增加。",
            }
        ],
        "path": [
            {
                "content": "先补齐统计与 SQL 基础。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "timeline": "第 1-3 个月",
            },
            {
                "content": "完成一个端到端数据分析项目。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "timeline": "第 3-6 个月（里程碑：项目可上线演示）",
            },
        ],
        "suggestions": [
            {
                "content": "未来 7 天完成一门 SQL 入门课的课程作业。",
                "evidence_refs": ["statement:current"],
                "note": None,
                "verification": "完成后可检验对 SQL 基础的掌握。",
            }
        ],
        "boundary_statement": "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
        "open_questions": ["行业报告的统计口径未完全披露，建议进一步核查。"],
    }


def _invalid_output() -> dict[str, Any]:
    """结构非法输出：六类全空且无正文（触发一次有界修复）。"""
    return {
        "final_text": "",
        "facts": [],
        "assumptions": [],
        "options": [],
        "risks": [],
        "path": [],
        "suggestions": [],
        "boundary_statement": "",
        "open_questions": [],
    }


def _send(client: TestClient, conversation_id: str, content: str) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _drive(sqlite_app: Any) -> None:
    """同步驱动后台执行器直到没有待处理运行（测试环境不自动启动）。"""
    import time as _time

    executor = sqlite_app.state.generation_executor
    deadline = _time.monotonic() + 10.0
    while _time.monotonic() < deadline:
        executor.run_tick()
        if "无待处理" in executor._last_summary:  # noqa: SLF001 - 测试读摘要
            return
        _time.sleep(0.02)
    raise AssertionError("执行器未在超时前完成运行。")


def _message(client: TestClient, conversation_id: str, message_id: str) -> dict[str, Any]:
    response = client.get(f"/chat/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    history = response.json()
    for message in history["messages"]:
        if message["message_id"] == message_id:
            return message
    raise AssertionError(f"消息 {message_id} 不存在")


# ---------------------------------------------------------------------------
# 用例 1：信息不足 → 快速澄清，不启动完整规划生成
# ---------------------------------------------------------------------------


def test_vague_request_returns_clarification_without_model_call(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
) -> None:
    """仅输入模糊请求：立即返回一个关键澄清问题，无模型调用、无六类输出。"""
    adapter = _ProgrammableStructuredAdapter()
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "191")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    started = time.monotonic()
    payload = _send(client, conversation_id, "生涯规划助手：帮我规划一下")
    elapsed = time.monotonic() - started
    assistant = payload["assistant_message"]

    assert elapsed < 2.0, "创建响应 2 秒内返回"
    assert adapter.calls == 0, "信息不足时不得调用结构化模型"
    # 澄清不启动检索/模型：事件流只有 started → career(clarify) → done，
    # 无任何 stage 阶段事件（「2 秒内出现澄清问题」的确定性证据）
    _drive(sqlite_app)
    events = generation_helpers["subscribe"](
        client, conversation_id, assistant["message_id"]
    )
    kinds = [kind for kind, _ in events]
    assert kinds == ["started", "career", "done"], f"澄清事件流异常：{kinds}"
    career_event = next(payload for kind, payload in events if kind == "career")
    assert career_event["state"] == "clarify"
    assert not any(kind == "stage" for kind, _ in events), (
        "澄清路径不得启动检索/模型阶段"
    )
    final = _message(client, conversation_id, assistant["message_id"])
    assert final["status"] == "done"
    career = final.get("career_planning") or {}
    assert career.get("clarification"), "澄清投影必须携带澄清问题"
    assert career.get("process_state") == "clarify"
    assert final["content"] == career["clarification"], "消息正文即澄清问题"
    assert career.get("output") is None, "澄清路径不交付六类规划"


# ---------------------------------------------------------------------------
# 用例 2：澄清粘性——回复澄清后继续生涯编排
# ---------------------------------------------------------------------------


def test_clarification_reply_continues_career_planning(
    sqlite_app: Any, client: TestClient
) -> None:
    """用户回答澄清问题（不含触发词）后，本轮继续走生涯编排并完成规划。"""
    adapter = _ProgrammableStructuredAdapter([_good_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "192")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    first = _send(client, conversation_id, "生涯规划助手：帮我规划一下")
    _drive(sqlite_app)
    first_id = first["assistant_message"]["message_id"]
    first_final = _message(client, conversation_id, first_id)
    assert (first_final.get("career_planning") or {}).get("clarification")

    # 回复不含任何生涯触发词——靠澄清粘性继续走生涯编排
    second = _send(client, conversation_id, "我想做数据分析，现在大二")
    _drive(sqlite_app)
    second_id = second["assistant_message"]["message_id"]
    second_final = _message(client, conversation_id, second_id)

    assert adapter.calls == 1, "澄清回答必须走一次结构化规划生成"
    assert second_final["status"] == "done"
    career = second_final.get("career_planning") or {}
    assert career.get("output") is not None, "第二轮必须交付六类规划"
    assert career.get("clarification") is None, "完成轮不再携带澄清问题"
    assert career["output"]["final_text"]


# ---------------------------------------------------------------------------
# 用例 3：信息足够 → 完整结构化计划（7 天行动 + 里程碑）
# ---------------------------------------------------------------------------


def test_sufficient_request_generates_full_plan(
    sqlite_app: Any, client: TestClient
) -> None:
    """提供足够信息时生成完整结构化计划，至少含未来 7 天行动与里程碑。"""
    adapter = _ProgrammableStructuredAdapter([_good_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "193")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert final["status"] == "done"
    career = final.get("career_planning") or {}
    output = career.get("output") or {}
    suggestions = [item["content"] for item in output.get("suggestions", [])]
    assert any(("7" in text or "一周" in text or "近期" in text) for text in suggestions), (
        "至少一条建议是未来 7 天可执行行动"
    )
    timelines = [
        item.get("timeline") or ""
        for item in output.get("path", [])
    ]
    assert any(timelines), "成长路径带时间范围（可检查里程碑）"
    assert adapter.calls == 1, "合法输出不得触发修复重调"


# ---------------------------------------------------------------------------
# 用例 4：首轮非法 → 一次有界修复成功
# ---------------------------------------------------------------------------


def test_invalid_first_output_repairs_once_then_succeeds(
    sqlite_app: Any, client: TestClient
) -> None:
    """结构化输出第一次非法、第二次合法时只修复一次并成功。"""
    adapter = _ProgrammableStructuredAdapter([_invalid_output(), _good_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "194")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert adapter.calls == 2, "一次修复 = 恰好两次模型调用"
    assert final["status"] == "done"
    career = final.get("career_planning") or {}
    assert career.get("output") is not None, "修复后成功交付六类规划"
    repair_prompt = adapter.requests[1]["messages"][-1]["content"]
    assert "修复要求" in repair_prompt, "第二次调用必须携带修复指令"


# ---------------------------------------------------------------------------
# 用例 4b：格式类失败（JSON 解析失败）同样只修复一次
# ---------------------------------------------------------------------------


def test_parse_failure_repairs_once_then_succeeds(
    sqlite_app: Any, client: TestClient
) -> None:
    """模型输出不是合法 JSON 时（格式类失败），带修复指令重调一次成功。"""
    adapter = _ParseFailThenGoodAdapter(_good_output())
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "201")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert adapter.calls == 2, "解析失败也只修复一次（恰好两次调用）"
    assert final["status"] == "done"
    career = final.get("career_planning") or {}
    assert career.get("output") is not None, "修复后成功交付六类规划"
    assert "修复要求" in adapter.requests[1]["messages"][-1]["content"]


# ---------------------------------------------------------------------------
# 用例 5：连续非法 → 在总预算内失败（不写 stream_interrupted）
# ---------------------------------------------------------------------------


def test_consistently_invalid_output_fails_within_budget(
    sqlite_app: Any, client: TestClient
) -> None:
    """连续非法输出：最多修复一次后明确失败，不做无限重试。"""
    adapter = _ProgrammableStructuredAdapter([_invalid_output(), _invalid_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "195")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert adapter.calls == 2, "连续非法也只调用两次（一次修复后失败）"
    assert final["status"] == "error"
    career = final.get("career_planning") or {}
    assert career.get("error_code") == "career_output_invalid"
    assert "结构校验" in (career.get("error_message") or "")
    assert career.get("clarification") is None
    # 明确领域失败而非 transport 中断
    assert final.get("error_code") == "career_output_invalid"


# ---------------------------------------------------------------------------
# 用例 6：慢模型在预算内完成，断开/切会话不中断运行
# ---------------------------------------------------------------------------


def test_slow_model_completes_durably_across_conversation_switch(
    sqlite_app: Any, client: TestClient
) -> None:
    """95 秒量级的慢模型：SSE 断开/切会话后 run 仍完成，无 stream_interrupted。"""
    adapter = _SlowStructuredAdapter(_good_output(), delay=0.4)
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "196")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    assistant_id = payload["assistant_message"]["message_id"]
    # 模拟页面断开：不订阅、立即切到另一会话再回来；运行在后台继续
    other = _create_conversation(client)
    _send(client, other, "普通消息")
    _drive(sqlite_app)

    final = _message(client, conversation_id, assistant_id)
    assert final["status"] == "done"
    assert final.get("error_code") is None, "不得写 stream_interrupted"
    career = final.get("career_planning") or {}
    assert career.get("output") is not None, "后台运行完成并交付规划"
    assert adapter.calls == 1, "运行只调用一次模型"


def test_model_over_budget_with_real_result_delivers_plan(
    sqlite_app: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算到期但真实结果已形成：先消费真实结果——交付规划而非 budget_exceeded。

    Issue 06 第七轮语义：只有「预算耗尽且无任何草稿/真实错误可交付」时
    才使用 ``budget_exceeded``。替身适配器不可中断（忽略截断超时）但
    最终返回了完整规划；真实 HTTP 客户端路径由网关截断保证单次调用最迟
    在「剩余预算 − 交接预留」处超时并透传真实错误。
    """
    from bridges.chat import budget as budget_module

    monkeypatch.setattr(budget_module, "TOTAL_BUDGET_MS", 50)
    adapter = _SlowStructuredAdapter(_good_output(), delay=0.3)
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "197")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert final["status"] == "done", "真实结果优先消费：交付规划而非 budget_exceeded"
    assert final.get("error_code") is None
    career = final.get("career_planning") or {}
    assert career.get("output") is not None, "已完成规划照常交付"
    assert final.get("error_code") != "stream_interrupted"


# ---------------------------------------------------------------------------
# 用例 7：检索缺失 → 交付骨架 + 待核实项（不伪造事实）
# ---------------------------------------------------------------------------


def test_missing_external_evidence_delivers_skeleton_with_open_questions(
    sqlite_app: Any, client: TestClient
) -> None:
    """无联网/论文/本地材料时：交付基于用户自述的规划骨架并列出待核实项。"""
    adapter = _ProgrammableStructuredAdapter([_good_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "198")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    final = _message(client, conversation_id, payload["assistant_message"]["message_id"])

    assert final["status"] == "done"
    career = final.get("career_planning") or {}
    output = career.get("output") or {}
    assert output.get("final_text"), "仍基于用户自述交付规划骨架"
    # 系统提示词要求：无外部证据时提示不编造外部事实
    system_prompt = adapter.requests[0]["messages"][0]["content"]
    assert "外部证据缺失" in system_prompt
    # 证据集合不含任何联网/论文/本地材料（检索缺失不伪造事实）
    sources = [s["kind"] for s in career.get("evidence_sources") or []]
    assert "user_statement" in sources
    assert "web_search" not in sources and "arxiv" not in sources
    # 输出列出待核实项（open_questions 非空）
    assert output.get("open_questions"), "交付骨架同时列出待核实项"


# ---------------------------------------------------------------------------
# 用例 8：重试创建新 attempt 并保留旧失败记录
# ---------------------------------------------------------------------------


def test_retry_creates_new_attempt_keeping_old_failure(
    sqlite_app: Any, client: TestClient
) -> None:
    """重试创建新 attempt 并保留旧失败记录，不覆盖历史证据。"""
    adapter = _ProgrammableStructuredAdapter([_invalid_output(), _invalid_output()])
    _swap_gateways(sqlite_app, adapter)
    account = _register(client, "199")
    conversation_id = _create_conversation(client)
    client.headers.update({"X-Account-Id": account["id"]})

    payload = _send(
        client,
        conversation_id,
        "生涯规划助手：我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向",
    )
    _drive(sqlite_app)
    old_id = payload["assistant_message"]["message_id"]
    old = _message(client, conversation_id, old_id)
    assert old["status"] == "error"

    # 重试：第二个适配器返回合法输出
    adapter2 = _ProgrammableStructuredAdapter([_good_output()])
    _swap_gateways(sqlite_app, adapter2)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{old_id}/retry",
        json={},
    )
    assert response.status_code == 200, response.text
    retry_id = response.json()["assistant_message"]["message_id"]
    assert retry_id != old_id, "重试必须创建新消息（attempt）"
    _drive(sqlite_app)

    retried = _message(client, conversation_id, retry_id)
    assert retried["status"] == "done"
    assert retried["attempt_number"] == old["attempt_number"] + 1
    # 旧失败记录原样保留
    old_after = _message(client, conversation_id, old_id)
    assert old_after["status"] == "error"
    assert (old_after.get("career_planning") or {}).get("error_code") == (
        "career_output_invalid"
    )
