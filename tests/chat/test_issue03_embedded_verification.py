"""Issue 03：嵌入式理解验证与即时纠错。

覆盖验收项：
1. 关键位置只呈现一个需作答问题，不每轮机械出题；
2. 正式练习评价依据展示前冻结，作答后版本与含义不变；
3. 提问、要求再讲、要提示和岔题、跳过均不被当成错误作答；
4. 独立完成、提示后完成、未验证、无法判断的证据语义有区分；
5. 即时反馈不晋升长期掌握；私有评价依据不进入用户投影；
6. 重试、停止、刷新与迟到结果不串题、不重复记账。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterResult, RateLimitError
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.study.verification import StudyVerificationRepository
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _chat_capability,
    _register,
)

#: 只应存在于服务端评价依据中的哨兵串，用于证明私有依据不泄漏。
_BASIS_SENTINEL = "PRIVATE-BASIS-42"

_Q1 = "如果 A、B 互斥，P(A∩B) 是多少？为什么？"
_Q2 = "如果 A、B 独立，P(A∩B) 与 P(A)、P(B) 的关系是什么？"
_ANSWER = "因为互斥表示不能同时发生，所以 P(A∩B)=0。"


def _plan(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "none",
        "intent": "other",
        "outcome": "",
        "equivalent_accepted": False,
        "reasoning_note": "",
        "supporting": [],
        "missing": [],
        "feedback_direction": "",
        "check_kind": "",
        "check_question": "",
        "check_knowledge_context": "",
        "evaluation_points": [],
        "accepted_equivalents": [],
        "key_misconceptions": [],
        "assumptions": [],
        "expected_answer": "",
        "teaching_direction": "",
    }
    base.update(overrides)
    return base


def _plan_pose(
    question: str,
    *,
    kind: str = "formal_check",
    context: str = "互斥与独立",
) -> dict[str, Any]:
    return _plan(
        action="pose_check",
        intent="other",
        check_kind=kind,
        check_question=question,
        check_knowledge_context=context,
        evaluation_points=[_BASIS_SENTINEL, "指出互斥事件不能同时发生"],
        accepted_equivalents=["互斥事件交集为空，因此 P(A∩B)=0"],
        key_misconceptions=["把互斥理解为互不影响"],
        assumptions=["A、B 为同一概率空间中的事件"],
        expected_answer="0",
    )


def _plan_evaluate(
    outcome: str = "correct",
    *,
    note: str = "学生指出互斥事件不能同时发生",
    equivalent: bool = False,
) -> dict[str, Any]:
    return _plan(
        action="evaluate",
        intent="answer",
        outcome=outcome,
        equivalent_accepted=equivalent,
        reasoning_note=note,
        supporting=["互斥 → 交集为空"],
        missing=[] if outcome == "correct" else ["未说明概率为 0 的依据"],
        feedback_direction="围绕交集为空给出反馈",
    )


class _PlanAdapter:
    """确定性验证规划替身：按脚本出队，脚本耗尽后使用默认值。"""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        default: dict[str, Any] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.default = default or _plan()
        self.payloads: list[dict[str, Any]] = []

    def call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        self.payloads.append(payload)
        output = self.responses.pop(0) if self.responses else self.default
        return AdapterResult(actual_model_id=capability.model_id, output=output)

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        raise AssertionError("验证规划器不应走流式调用")


def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-json-v1",
    )


def _gateway_with_verification(
    text_adapter: _TextTutorAdapter, plan_adapter: _PlanAdapter
) -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    registry.register(_structured_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", text_adapter)
    gateway.register_adapter("qwen_structured_output", "1", plan_adapter)
    return gateway


def _start_study(client: TestClient, content: str, key: str) -> str:
    response = client.post(
        "/chat/first-turn",
        json={"mode": "study", "content": content, "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _send(
    client: TestClient, conversation_id: str, content: str, key: str
) -> str:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["assistant_message"]["message_id"]


def _close(app: Any) -> None:
    app.state.bridges_database.close()
    app.state.state_store.close()


# ---------------------------------------------------------------------------
# 评价依据冻结、独立完成证据与私有依据隔离
# ---------------------------------------------------------------------------


def test_formal_check_freezes_basis_and_records_independent_completion(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"我们先检查一个关键点。{_Q1}")
    plan = _PlanAdapter([_plan_pose(_Q1)])
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    conversation_id: str | None = None

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-independent")
            conversation_id = _start_study(client, "互斥是不是独立？", "issue03-ind-1")
            generation_helpers["drive"](app)

            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            pending = detail["study_pending_check"]
            assert pending is not None
            assert pending["question"] == _Q1
            assert pending["kind"] == "formal_check"
            assert pending["assistance"] == "none"
            assert pending["version"] == 1
            assert pending["source_message_id"] == detail["messages"][-1]["message_id"]
            assert detail["messages"][-1]["content"] == text.answer
            # 私有评价依据不进入用户投影（完整响应正文检查）。
            detail_text = json.dumps(detail, ensure_ascii=False)
            assert _BASIS_SENTINEL not in detail_text
            assert "评价要点" not in detail_text

            database = app.state.bridges_database
            stored = database.connection.execute(
                "SELECT basis_json, version FROM study_pending_checks"
            ).fetchone()
            assert _BASIS_SENTINEL in stored["basis_json"]
            assert "把互斥理解为互不影响" in stored["basis_json"]

            # 刷新后待答问题与依据仍一致（投影与库内版本不变）。
            refreshed = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert refreshed == pending
            assert database.connection.execute(
                "SELECT version FROM study_pending_checks"
            ).fetchone()["version"] == 1

            # 作答：评价载荷必须仍带展示前冻结的依据。
            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, conversation_id, _ANSWER, "issue03-ind-2")
            generation_helpers["drive"](app)

            evaluation_payload = json.dumps(plan.payloads[-1], ensure_ascii=False)
            assert _BASIS_SENTINEL in evaluation_payload
            assert "把互斥理解为互不影响" in evaluation_payload

            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            assert detail["study_pending_check"] is None
            evidence = StudyVerificationRepository(database).list_evidence(
                account["id"], conversation_id
            )
            assert len(evidence) == 1
            item = evidence[0]
            assert item.semantics.value == "independent_completion"
            assert item.outcome.value == "correct"
            assert item.answered is True
            assert item.check_kind.value == "formal_check"
            assert item.question == _Q1
            assert item.basis_protocol_version == "study-verification-v1"
            assert item.evidence_contract_version == "study-verification-v1"
            assert item.assistance.value == "independent"
            assert item.answer_message_id
            assert item.feedback_message_id
            assert item.supporting == ["互斥 → 交集为空"]
            assert item.hint_message_ids == []
            # 即时反馈没有晋升长期掌握：不写学习状态/长期档案。
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0] == 0
            assert "PRIVATE-BASIS-42" not in json.dumps(detail, ensure_ascii=False)

            # 跨账户：其他账户读不到问题与证据。
            with TestClient(app) as other_client:
                other = _register(other_client, "issue03-independent-other")
                assert other["id"] != account["id"]
                blocked = other_client.get(
                    f"/chat/conversations/{conversation_id}"
                )
                assert blocked.status_code == 404
                repository = StudyVerificationRepository(database)
                assert repository.get_pending(other["id"], conversation_id) is None
                assert repository.list_evidence(other["id"], conversation_id) == []
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 提示、非作答意图与同一回答的语义区分
# ---------------------------------------------------------------------------


def test_same_answer_differs_with_and_without_hint(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-hint")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)

            # 对话 A：无提示直接作答 → 独立完成。
            plain = _start_study(client, "互斥是什么？", "issue03-hint-a")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate()
            _send(client, plain, _ANSWER, "issue03-hint-a-answer")
            generation_helpers["drive"](app)

            # 对话 B：先要提示，再给同一段回答 → 提示后完成，提示不被判错。
            plan.default = _plan_pose(_Q1)
            text.answer = f"先检查一下。{_Q1}"
            hinted = _start_study(client, "互斥是什么？", "issue03-hint-b")
            generation_helpers["drive"](app)
            plan.default = _plan(action="help", intent="hint_request")
            text.answer = "提示：先想想互斥对交集意味着什么。"
            _send(client, hinted, "给我一点提示。", "issue03-hint-b-hint")
            generation_helpers["drive"](app)

            pending = client.get(
                f"/chat/conversations/{hinted}"
            ).json()["study_pending_check"]
            assert pending is not None
            assert pending["assistance"] == "hinted"
            assert len(
                repository.get_pending(account["id"], hinted).hint_history
            ) == 1
            # 要提示不产生作答证据，也不被判错。
            assert repository.list_evidence(account["id"], hinted) == []

            plan.default = _plan_evaluate()
            text.answer = "互斥时交集为空，所以 P(A∩B)=0。"
            _send(client, hinted, _ANSWER, "issue03-hint-b-answer")
            generation_helpers["drive"](app)

            plain_evidence = repository.list_evidence(account["id"], plain)
            hinted_evidence = repository.list_evidence(account["id"], hinted)
            assert len(plain_evidence) == 1 and len(hinted_evidence) == 1
            assert plain_evidence[0].semantics.value == "independent_completion"
            assert hinted_evidence[0].semantics.value == "hinted_completion"
            assert hinted_evidence[0].assistance.value == "after_hint"
            assert hinted_evidence[0].hint_message_ids
            assert hinted_evidence[0].question == _Q1
            # 两个对话的回答正文相同，证据语义不混为一谈。
            assert plain_evidence[0].answer_message_id != (
                hinted_evidence[0].answer_message_id
            )
    finally:
        _close(app)


@pytest.mark.parametrize(
    ("intent", "message"),
    [
        ("question", "我还是不太明白，互斥能举个例子吗？"),
        ("explain_request", "能换个说法再讲一遍吗？"),
        ("digression", "对了，Python 的集合运算怎么做？"),
    ],
)
def test_non_answer_intents_are_not_judged(
    tmp_path: Any,
    monkeypatch: Any,
    generation_helpers: Any,
    intent: str,
    message: str,
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, f"i03-{intent}")
            database = app.state.bridges_database
            conversation_id = _start_study(
                client, "互斥是什么？", f"issue03-intent-{intent}-1"
            )
            generation_helpers["drive"](app)

            plan.default = _plan(action="help", intent=intent)
            text.answer = "好的，我先回应这一点，再回到刚才的问题。"
            _send(client, conversation_id, message, f"issue03-intent-{intent}-2")
            generation_helpers["drive"](app)

            repository = StudyVerificationRepository(database)
            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None and pending.question == _Q1
            assert repository.list_evidence(account["id"], conversation_id) == []
            projection = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert projection["question"] == _Q1
            assert projection["assistance"] == "none"
    finally:
        _close(app)


def test_equivalent_expression_accepted_and_undetermined_kept_unconfirmed(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-equivalent")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)

            # 等价表述：按含义接受，不因措辞不同扣分。
            equivalent = _start_study(client, "互斥是什么？", "issue03-equiv-1")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate(equivalent=True)
            text.answer = "换个说法：两者不会一起发生，所以交集概率是零。"
            _send(client, equivalent, "两者不会同时发生，交集概率为零。", "issue03-equiv-1-a")
            generation_helpers["drive"](app)
            items = repository.list_evidence(account["id"], equivalent)
            assert len(items) == 1
            assert items[0].outcome.value == "correct"
            assert items[0].semantics.value == "independent_completion"

            # 无法判断：保留待确认，不因格式或措辞判错。
            plan.default = _plan_pose(_Q1)
            text.answer = f"先检查一下。{_Q1}"
            unclear = _start_study(client, "互斥是什么？", "issue03-equiv-2")
            generation_helpers["drive"](app)
            plan.default = _plan_evaluate(outcome="undetermined")
            text.answer = "你说得还不太清楚，我们再看一步。"
            _send(client, unclear, "差不多就是那个意思吧。", "issue03-equiv-2-a")
            generation_helpers["drive"](app)
            items = repository.list_evidence(account["id"], unclear)
            assert len(items) == 1
            assert items[0].outcome.value == "undetermined"
            assert items[0].semantics.value == "undetermined"
            assert items[0].answered is True
    finally:
        _close(app)


def test_skip_records_unverified_and_allows_next_check(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-skip")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue03-skip-1")
            generation_helpers["drive"](app)

            first = StudyVerificationRepository(database).get_pending(
                account["id"], conversation_id
            )
            assert first is not None

            plan.default = _plan_pose(_Q2)
            plan.default["intent"] = "skip"
            text.answer = f"好，那我们先放一放。{_Q2}"
            _send(client, conversation_id, "这个先跳过吧，问下一个。", "issue03-skip-2")
            generation_helpers["drive"](app)

            repository = StudyVerificationRepository(database)
            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None
            assert pending.question == _Q2
            assert pending.version == first.version + 1
            evidence = repository.list_evidence(account["id"], conversation_id)
            assert len(evidence) == 1
            assert evidence[0].check_id == first.check_id
            assert evidence[0].semantics.value == "unverified"
            assert evidence[0].outcome.value == "no_answer"
            assert evidence[0].answered is False
            assert evidence[0].check_version == first.version
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 单检查约束与连续卡住的粒度调整
# ---------------------------------------------------------------------------


def test_one_pending_check_at_a_time_and_no_mechanical_question(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    # 每轮都规划新题：存在待答问题且不是作答/跳过时，新题必须被抑制。
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-one")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue03-one-1")
            generation_helpers["drive"](app)
            repository = StudyVerificationRepository(database)
            first = repository.get_pending(account["id"], conversation_id)
            assert first is not None

            text.answer = f"我继续讲一点。{_Q2}"
            _send(client, conversation_id, "继续说。", "issue03-one-2")
            generation_helpers["drive"](app)

            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None
            assert pending.check_id == first.check_id
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_pending_checks"
            ).fetchone()[0] == 1

            # 不做验证的轮次（none）也不出题。
            plan.default = _plan()
            text.answer = "这是一段不带检查的讲解。"
            _send(client, conversation_id, "明白了。", "issue03-one-3")
            generation_helpers["drive"](app)
            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            assert detail["study_pending_check"]["check_id"] == first.check_id
            assert detail["messages"][-1]["content"] == text.answer
    finally:
        _close(app)


def test_stuck_streak_blocks_new_formal_check_and_allows_smaller_step(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-stuck")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)
            conversation_id = _start_study(client, "互斥是什么？", "issue03-stuck-1")
            generation_helpers["drive"](app)

            # 第一题部分完成，同时提出第二题（评价 + 出题同轮）。
            follow_up = _plan_pose(_Q2)
            follow_up.update(
                action="evaluate", intent="answer", outcome="partial",
                reasoning_note="只说了不能同时发生",
            )
            plan.default = follow_up
            text.answer = f"有一半对了，我们再看一步。{_Q2}"
            _send(client, conversation_id, "不能同时发生。", "issue03-stuck-2")
            generation_helpers["drive"](app)
            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None and pending.question == _Q2

            # 第二题出现误解：连续未完成，进入卡住状态（本轮不出新题）。
            plan.default = _plan_evaluate(outcome="misconception")
            text.answer = "这里容易混淆，我们从例子看。"
            _send(client, conversation_id, "互斥就是互不影响。", "issue03-stuck-3")
            generation_helpers["drive"](app)
            evidence = repository.list_evidence(account["id"], conversation_id)
            assert [item.outcome.value for item in evidence] == [
                "partial",
                "misconception",
            ]
            assert repository.get_pending(account["id"], conversation_id) is None

            # 卡住状态下：新的正式练习被抑制，改为开放探查。
            plan.default = _plan_pose(_Q1)
            plan.default["intent"] = "skip"
            plan.default["check_kind"] = "formal_check"
            text.answer = f"换个小问题。{_Q1}"
            _send(client, conversation_id, "再试一次。", "issue03-stuck-4")
            generation_helpers["drive"](app)
            assert repository.get_pending(account["id"], conversation_id) is None

            plan.default = _plan_pose(
                "你能用自己的话打一个互斥的例子吗？", kind="open_probe"
            )
            text.answer = "你能用自己的话打一个互斥的例子吗？"
            _send(client, conversation_id, "好。", "issue03-stuck-5")
            generation_helpers["drive"](app)
            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None
            assert pending.kind.value == "open_probe"
    finally:
        _close(app)


# ---------------------------------------------------------------------------
# 失败、停止、重试与跨题绑定
# ---------------------------------------------------------------------------


def test_failed_turn_does_not_activate_check_and_retry_commits_once(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-retry")
            database = app.state.bridges_database
            text.failure = RateLimitError("temporary network interruption")
            conversation_id = _start_study(client, "互斥是什么？", "issue03-retry-1")
            generation_helpers["drive"](app)

            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            assert detail["messages"][-1]["status"] == "error"
            failed_id = detail["messages"][-1]["message_id"]
            assert detail["study_pending_check"] is None
            repository = StudyVerificationRepository(database)
            assert repository.get_pending(account["id"], conversation_id) is None
            assert repository.list_evidence(account["id"], conversation_id) == []
            assert detail["study_local_progress"] is None
            assert len(plan.payloads) == 1

            # 重试重放同一冻结快照：不重新规划、问题只激活一次。
            text.failure = None
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)

            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            pending = detail["study_pending_check"]
            assert pending is not None and pending["question"] == _Q1
            assert detail["study_local_progress"]["version"] == 1
            assert len(plan.payloads) == 1
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_pending_checks"
            ).fetchone()[0] == 1
            assert repository.list_evidence(account["id"], conversation_id) == []
    finally:
        _close(app)


def test_stopped_turn_does_not_activate_or_release_pending(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-stop")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue03-stop-1")
            generation_helpers["drive"](app)
            repository = StudyVerificationRepository(database)
            first = repository.get_pending(account["id"], conversation_id)
            assert first is not None

            # 第二轮规划「跳过旧题 + 提出新题」，但流式中途停止：
            # 旧题保持、新题不激活、不写未验证证据。
            plan.default = _plan_pose(_Q2)
            plan.default["intent"] = "skip"
            text.answer = f"正在逐字生成的回答。{_Q2}" * 20
            text.slow = True

            stop_executor, executor_thread = generation_helpers["executor_thread"](app)
            control_client = TestClient(app)
            control_client.cookies.set(
                "bridges_session", client.cookies.get("bridges_session")
            )
            try:
                sent = client.post(
                    f"/chat/conversations/{conversation_id}/messages",
                    json={"content": "跳过这题吧。", "idempotency_key": "issue03-stop-2"},
                )
                assert sent.status_code == 200, sent.text
                message_id = sent.json()["assistant_message"]["message_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    message = control_client.get(
                        f"/chat/conversations/{conversation_id}"
                    ).json()["messages"][-1]
                    if message["content"]:
                        break
                    time.sleep(0.02)
                stopped = control_client.post(
                    f"/chat/conversations/{conversation_id}/messages/{message_id}/stop"
                )
                assert stopped.status_code == 200, stopped.text
                assert stopped.json()["message"]["status"] == "stopped"
                detail = control_client.get(
                    f"/chat/conversations/{conversation_id}"
                ).json()
                pending = detail["study_pending_check"]
                assert pending is not None
                assert pending["check_id"] == first.check_id
                assert pending["question"] == _Q1
                assert repository.list_evidence(account["id"], conversation_id) == []
                assert detail["study_local_progress"]["version"] == 1
            finally:
                stop_executor.set()
                executor_thread.join(timeout=5)
                control_client.close()
    finally:
        _close(app)


def test_old_answer_retry_stays_bound_to_old_check(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter(f"先检查一下。{_Q1}")
    plan = _PlanAdapter(default=_plan_pose(_Q1))
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-old-retry")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)
            conversation_id = _start_study(client, "互斥是什么？", "issue03-old-1")
            generation_helpers["drive"](app)
            first = repository.get_pending(account["id"], conversation_id)
            assert first is not None

            # 学生对第一题作答，但本轮失败（评价快照已绑定第一题）。
            plan.default = _plan_evaluate()
            text.failure = RateLimitError("temporary network interruption")
            failed_id = _send(client, conversation_id, _ANSWER, "issue03-old-2")
            generation_helpers["drive"](app)
            assert client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["messages"][-1]["status"] == "error"

            # 学生跳过第一题并要求新题：第一题记为未验证，第二题激活。
            plan.default = _plan_pose(_Q2)
            plan.default["intent"] = "skip"
            text.failure = None
            text.answer = f"好，那看下一个。{_Q2}"
            _send(client, conversation_id, "跳过吧，我换一个。", "issue03-old-3")
            generation_helpers["drive"](app)
            pending = repository.get_pending(account["id"], conversation_id)
            assert pending is not None and pending.question == _Q2

            # 重试旧作答：重放旧快照，证据仍绑定第一题，判不到新题上。
            plan_calls_before = len(plan.payloads)
            retried = client.post(
                f"/chat/conversations/{conversation_id}/messages/{failed_id}/retry"
            )
            assert retried.status_code == 200, retried.text
            generation_helpers["drive"](app)
            assert len(plan.payloads) == plan_calls_before

            items = repository.list_evidence(account["id"], conversation_id)
            assert len(items) == 2
            answered = [item for item in items if item.answered]
            unverified = [item for item in items if not item.answered]
            assert len(answered) == 1 and len(unverified) == 1
            assert answered[0].check_id == first.check_id
            assert answered[0].question == _Q1
            assert answered[0].semantics.value == "independent_completion"
            assert unverified[0].check_id == first.check_id
            assert unverified[0].semantics.value == "unverified"
            # 同一作答消息只记账一次；新问题保持待答。
            assert database.connection.execute(
                "SELECT COUNT(*) FROM study_evidence WHERE answer_message_id IS NOT NULL"
            ).fetchone()[0] == 1
            current = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert current is not None and current["question"] == _Q2
    finally:
        _close(app)


def test_planner_failure_degrades_without_writing(
    tmp_path: Any, monkeypatch: Any, generation_helpers: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    # 只注册主对话能力：结构化规划能力不可用时验证静默降级。
    text = _TextTutorAdapter("这是直接讲解，不包含任何理解检查。")
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", text)
    app.state.chat_service._gateway = gateway

    try:
        with TestClient(app) as client:
            account = _register(client, "issue03-degrade")
            database = app.state.bridges_database
            conversation_id = _start_study(client, "互斥是什么？", "issue03-degrade-1")
            generation_helpers["drive"](app)

            detail = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()
            assert detail["messages"][-1]["status"] == "done"
            assert detail["messages"][-1]["content"] == text.answer
            assert detail["study_pending_check"] is None
            assert detail["study_local_progress"]["version"] == 1
            repository = StudyVerificationRepository(database)
            assert repository.get_pending(account["id"], conversation_id) is None
            assert repository.list_evidence(account["id"], conversation_id) == []
    finally:
        _close(app)
