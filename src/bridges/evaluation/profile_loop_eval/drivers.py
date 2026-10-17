"""工单 18：T 场景驱动（通用对话场景 + T04–T12 专用驱动）。

驱动只使用真实入口（聊天路由、画像条目 API、控制 API、概览/事项 API）与
真实后台执行器；所有判定关键词来自 ``scenes.py`` 的冻结规格。
"""

from __future__ import annotations

import time
from typing import Any

from bridges.evaluation.profile_loop_eval import metrics
from bridges.evaluation.profile_loop_eval.scenes import SCENARIO_SPECS_BY_ID
from bridges.evaluation.profile_loop_eval.session import AccountSession
from bridges.evaluation.profile_loop_eval.types import (
    KIND_HOLDOUT,
    KIND_MIXED_SUBJECT,
    KIND_NEGATION,
    KIND_SCENARIO,
    KIND_SYNONYM,
    SPLIT_HOLDOUT,
    ExtraSample,
    ScenarioSpec,
    SceneRecord,
    SceneResult,
)


def new_result(
    scene_id: str,
    kind: str,
    title: str,
    *,
    split: str = "dev",
    session: AccountSession | None = None,
    run_mode: str = "real",
) -> SceneResult:
    record = SceneRecord(
        scene_id=scene_id,
        kind=kind,
        title=title,
        split=split,
        account_id=session.account_id if session is not None else "",
        run_mode=run_mode,
    )
    return SceneResult(record=record)


def finalize(result: SceneResult, session: AccountSession, started: float) -> SceneResult:
    record = result.record
    record.items_after = session.active_items()
    record.elapsed_ms = int((time.monotonic() - started) * 1000)
    record.account_id = session.account_id
    seen: set[str] = set()
    for turn in session.turns:
        conversation_id = str(turn.get("conversation_id") or "")
        if conversation_id and conversation_id not in seen:
            seen.add(conversation_id)
            record.conversations.append(conversation_id)
        record.turns.append(turn)
        slices = turn.get("adopted_items") or []
        if slices:
            record.slices.append(
                {
                    "user_content": turn.get("user_content"),
                    "adopted_items": slices,
                }
            )
    return result


def add_standard_checks(
    result: SceneResult,
    *,
    groups: tuple[Any, ...],
    forbidden: tuple[str, ...],
    negation: tuple[str, ...] = (),
    session: AccountSession | None = None,
) -> None:
    """判定前先把当前活动条目快照进记录（判定只读记录）。"""

    if session is not None:
        result.record.items_after = session.active_items()
    if groups:
        metrics.check_fact_groups(result, groups)
    if forbidden:
        metrics.check_forbidden(result, forbidden)
    if negation:
        metrics.check_negation(result, negation)


def run_conversation_scenario(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """通用驱动：首轮建会话、其余轮续聊；追问在新会话进行。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    result.record.extra["expected_groups"] = [
        {"label": group.label, "keywords": list(group.keywords)} for group in spec.expected_groups
    ]
    conversation_id, turn = session.start_chat(spec.dev_turns[0])
    for content in spec.dev_turns[1:]:
        session.chat(conversation_id, content)
    add_standard_checks(
        result,
        groups=spec.expected_groups,
        forbidden=spec.forbidden_item_keywords,
        session=session,
    )
    if spec.follow_up_question:
        _, follow_up = session.chat_in_new_conversation(spec.follow_up_question)
        result.record.extra["follow_up"] = follow_up_evidence(follow_up)
        metrics.check_follow_up(
            result,
            must_include=spec.adoption_must_include,
            must_exclude=spec.adoption_must_exclude,
        )
        result.answer_review.append(
            {
                "sample_id": spec.scenario_id,
                "question": spec.follow_up_question,
                "answer": follow_up.get("assistant_content"),
                "adopted_items": follow_up.get("adopted_items"),
            }
        )
    result.record.extra["unmatched_items"] = metrics.unmatched_items(
        result.record.to_dict(), spec.expected_groups
    )
    return finalize(result, session, started)


def run_extra_sample(
    ctx: Any,
    parent: ScenarioSpec,
    sample: ExtraSample,
    session: AccountSession,
) -> SceneResult:
    started = time.monotonic()
    kind = sample.kind
    result = new_result(
        sample.sample_id,
        kind,
        f"{parent.scenario_id} 变体（{kind}）",
        split=SPLIT_HOLDOUT if kind == KIND_HOLDOUT else "variant",
        session=session,
    )
    setup = _assistant_setup_for(sample.sample_id)
    if setup is not None:
        conversation_id, _ = session.start_chat(setup)
    else:
        conversation_id = ""
    first = True
    for content in sample.turns:
        if first and conversation_id:
            session.chat(conversation_id, content)
            first = False
        elif first:
            conversation_id, _ = session.start_chat(content)
            first = False
        else:
            session.chat(conversation_id, content)
    add_standard_checks(
        result,
        groups=sample.expected_groups,
        forbidden=sample.forbidden_item_keywords,
        negation=sample.negation_keywords,
        session=session,
    )
    if sample.note:
        result.record.extra["note"] = sample.note
    if sample.expected_groups:
        result.record.extra["unmatched_items"] = metrics.unmatched_items(
            result.record.to_dict(), sample.expected_groups
        )
    return finalize(result, session, started)


def _assistant_setup_for(sample_id: str) -> str | None:
    if sample_id == "T04-holdout":
        return "你对什么研究方向感兴趣？"
    if sample_id == "T06-negation":
        return "我建议你制定一个每天刷 20 道题的学习计划。"
    return None


def follow_up_evidence(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": turn.get("user_content"),
        "answer": turn.get("assistant_content"),
        "assistant_status": turn.get("assistant_status"),
        "adopted_items": turn.get("adopted_items") or [],
    }


# ---------------------------------------------------------------------------
# 场景专用驱动
# ---------------------------------------------------------------------------


def run_t04(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """跨轮指代：助手先问专业，再验证长期偏好与追问采用。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat("你现在读什么专业？")
    # 助手提问由真实模型回答；注入用户的真实回答进入下一轮。
    for content in spec.dev_turns:
        session.chat(conversation_id, content)
    add_standard_checks(
        result,
        groups=spec.expected_groups,
        forbidden=spec.forbidden_item_keywords,
        session=session,
    )
    preference = (
        session.find_item("先给结论")
        or session.find_item("先给我结论")
        or session.find_item("先给重点")
    )
    result.add(
        "preference:captured",
        preference is not None,
        f"偏好条目={preference!r}",
    )
    result.record.extra["neighbor_probe"] = _neighbor_probe(session, conversation_id)
    extra = spec.extra_samples[0]
    sample_session = ctx.new_session(f"{spec.scenario_id}-holdout")
    setup = _assistant_setup_for(extra.sample_id)
    assert setup is not None
    holdout_conversation, _ = sample_session.start_chat(setup)
    for content in extra.turns:
        sample_session.chat(holdout_conversation, content)
    holdout_result = new_result(
        extra.sample_id,
        KIND_HOLDOUT,
        f"{spec.scenario_id} 变体（holdout）",
        split=SPLIT_HOLDOUT,
        session=sample_session,
    )
    add_standard_checks(
        holdout_result,
        groups=extra.expected_groups,
        forbidden=(),
        session=sample_session,
    )
    preference_holdout = sample_session.find_item("先讲重点") or sample_session.find_item(
        "先给结论"
    )
    holdout_result.add(
        "preference:captured",
        preference_holdout is not None,
        f"偏好条目={preference_holdout!r}",
    )
    ctx.register_side_result(finalize(holdout_result, sample_session, started))
    _, follow_up = session.chat_in_new_conversation(spec.follow_up_question or "")
    result.record.extra["follow_up"] = follow_up_evidence(follow_up)
    metrics.check_follow_up(
        result,
        must_include=spec.adoption_must_include,
        must_exclude=spec.adoption_must_exclude,
    )
    return finalize(result, session, started)


def _neighbor_probe(session: AccountSession, conversation_id: str) -> list[str] | None:
    from bridges.evaluation.profile_journey_runner import probe_neighbor_reader

    detail = session.collector.conversation_detail(conversation_id)
    users = [m for m in detail["messages"] if m["role"] == "user"]
    if not users:
        return None
    return probe_neighbor_reader(
        session.app, session.account_id, conversation_id, users[-1]["message_id"]
    )


def run_t06(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """孤立同意：助手建议由注入消息准备，用户仅回「同意」。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat("我建议你制定一个每天刷 20 道题的学习计划。")
    session.insert_assistant_message(conversation_id, "我建议你制定一个每天刷 20 道题的学习计划。")
    session.chat(conversation_id, "同意。")
    add_standard_checks(result, groups=(), forbidden=spec.forbidden_item_keywords, session=session)
    extra = spec.extra_samples[0]
    sample_session = ctx.new_session(f"{spec.scenario_id}-negation")
    negation_conversation, _ = sample_session.start_chat(
        "我建议你制定一个每天刷 20 道题的学习计划。"
    )
    sample_session.insert_assistant_message(
        negation_conversation, "我建议你制定一个每天刷 20 道题的学习计划。"
    )
    for content in extra.turns:
        sample_session.chat(negation_conversation, content)
    negation_result = new_result(
        extra.sample_id,
        KIND_NEGATION,
        f"{spec.scenario_id} 变体（negation）",
        split="variant",
        session=sample_session,
    )
    add_standard_checks(
        negation_result,
        groups=(),
        forbidden=extra.forbidden_item_keywords,
        session=sample_session,
    )
    ctx.register_side_result(finalize(negation_result, sample_session, started))
    return finalize(result, session, started)


def run_t08(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """同义去重：跑步只保留合并后的事实；朋友主体不得并入。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    for content in spec.dev_turns[1:]:
        session.chat(conversation_id, content)
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    running = [item for item in session.active_items() if "跑" in str(item.get("text") or "")]
    result.add(
        "dedup:running-items",
        len(running) <= 1,
        f"跑步相关活动条目={[item['text'] for item in running]!r}",
    )
    texts = session.active_texts()
    result.add(
        "dedup:no-duplicate-text",
        len(texts) == len(set(texts)),
        f"活动条目正文={texts!r}",
    )
    result.add(
        "attribution:friend-excluded",
        not any("朋友" in text for text in texts),
        f"活动条目正文={texts!r}",
    )
    extra = spec.extra_samples[0]
    sample_session = ctx.new_session(f"{spec.scenario_id}-holdout")
    holdout_conversation, _ = sample_session.start_chat(extra.turns[0])
    for content in extra.turns[1:]:
        sample_session.chat(holdout_conversation, content)
    holdout_result = new_result(
        extra.sample_id,
        KIND_HOLDOUT,
        f"{spec.scenario_id} 变体（holdout）",
        split=SPLIT_HOLDOUT,
        session=sample_session,
    )
    add_standard_checks(
        holdout_result,
        groups=extra.expected_groups,
        forbidden=(),
        session=sample_session,
    )
    holdout_running = [
        item for item in sample_session.active_items() if "跑" in str(item.get("text") or "")
    ]
    holdout_result.add(
        "dedup:running-items",
        len(holdout_running) <= 1,
        f"跑步相关活动条目={[item['text'] for item in holdout_running]!r}",
    )
    ctx.register_side_result(finalize(holdout_result, sample_session, started))
    return finalize(result, session, started)


def run_t09(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """本次要求、长期偏好与表达投诉的区分。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat("介绍一下贝叶斯定理。")
    session.chat(conversation_id, spec.dev_turns[0])
    session.chat(conversation_id, spec.dev_turns[1])
    session.chat(conversation_id, spec.dev_turns[2])
    add_standard_checks(
        result,
        groups=spec.expected_groups,
        forbidden=spec.forbidden_item_keywords,
        session=session,
    )
    texts = session.active_texts()
    result.add(
        "feedback:one-off-not-persisted",
        not any("简短" in text for text in texts),
        f"活动条目={texts!r}",
    )
    _, follow_up = session.chat_in_new_conversation("解释一下牛顿第二定律。")
    result.record.extra["follow_up"] = follow_up_evidence(follow_up)
    adopted = " ".join(follow_up.get("adopted_items") or [])
    result.add(
        "feedback:preference-adopted",
        "先给结论" in adopted or "先给重点" in adopted,
        f"采用条目={follow_up.get('adopted_items')!r}",
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": "解释一下牛顿第二定律。",
            "answer": follow_up.get("assistant_content"),
            "adopted_items": follow_up.get("adopted_items"),
        }
    )
    extra = spec.extra_samples[0]
    sample_session = ctx.new_session(f"{spec.scenario_id}-negation")
    negation_conversation, _ = sample_session.start_chat("解释一下贝叶斯定理。")
    sample_session.chat(negation_conversation, extra.turns[0])
    negation_result = new_result(
        extra.sample_id,
        KIND_NEGATION,
        f"{spec.scenario_id} 变体（negation）",
        split="variant",
        session=sample_session,
    )
    add_standard_checks(
        negation_result,
        groups=extra.expected_groups,
        forbidden=(),
        session=sample_session,
    )
    ctx.register_side_result(finalize(negation_result, sample_session, started))
    return finalize(result, session, started)


def run_t10(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """用户手改优先：迟到提取不得覆盖；重放幂等。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, first_turn = session.start_chat(spec.dev_turns[0])
    item = session.find_item("北京")
    result.add("setup:item-created", item is not None, f"条目={item!r}")
    if item is None:
        return finalize(result, session, started)
    edited = session.patch_item(item["profile_item_id"], "我住在上海。", item["version"])
    result.record.extra["edited_item"] = edited
    before_count = len(session.items())
    replay = session.reschedule_extraction(
        conversation_id=conversation_id,
        message_id=first_turn["user_message_id"],
        content=spec.dev_turns[0],
    )
    session.drive(conversation_id)
    replay2 = session.reschedule_extraction(
        conversation_id=conversation_id,
        message_id=first_turn["user_message_id"],
        content=spec.dev_turns[0],
    )
    session.drive(conversation_id)
    result.record.extra["replay"] = [replay, replay2]
    texts = session.active_texts()
    result.add(
        "authority:manual-edit-preserved",
        any("上海" in text for text in texts),
        f"活动条目={texts!r}",
    )
    result.add(
        "authority:late-extraction-rejected",
        not any("北京" in text for text in texts),
        f"活动条目={texts!r}",
    )
    result.add(
        "idempotency:no-duplicate-item",
        len(session.items()) == before_count,
        f"重放前={before_count} 重放后={len(session.items())}",
    )
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    return finalize(result, session, started)


def run_t11(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """遗忘后不复活、不间接回流；概览同步收敛。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    seeded = session.find_item("怕狗")
    result.add("setup:fear-item-created", seeded is not None, f"条目={seeded!r}")
    session.chat(conversation_id, spec.dev_turns[1])
    result.add(
        "forget:no-active-fear-item",
        session.find_item("怕狗") is None,
        f"活动条目={session.active_texts()!r}",
    )
    overview = session.overview()
    overview_text = _overview_text(overview)
    result.add(
        "forget:overview-clean",
        "怕狗" not in overview_text,
        f"概览={overview_text[:200]!r}",
    )
    _, follow_up = session.chat_in_new_conversation(spec.dev_turns[2])
    result.record.extra["follow_up"] = follow_up_evidence(follow_up)
    metrics.check_follow_up(
        result,
        must_include=spec.adoption_must_include,
        must_exclude=spec.adoption_must_exclude,
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": spec.dev_turns[2],
            "answer": follow_up.get("assistant_content"),
            "adopted_items": follow_up.get("adopted_items"),
        }
    )
    return finalize(result, session, started)


def _overview_text(overview: dict[str, Any]) -> str:
    parts: list[str] = []
    for statement in overview.get("statements") or []:
        parts.append(str(statement.get("text") or ""))
    for key in ("state", "notice"):
        if overview.get(key):
            parts.append(str(overview[key]))
    return " ".join(parts)


def run_t12(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """跨会话续学：接续进展、暂停为当前状态、恢复与完成。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    session.start_chat(spec.dev_turns[0])
    conversation_b, continue_turn = session.chat_in_new_conversation(spec.dev_turns[1])
    adopted = " ".join(continue_turn.get("adopted_items") or [])
    goal_row = session.active_item_row("考研英语")
    goal_id = str((goal_row or {}).get("profile_item_id") or "")
    continue_excluded = [
        entry
        for entry in (continue_turn.get("generation_run") or {})
        .get("adopted_profile_slice", {})
        .get("excluded_items", [])
        if str(entry.get("profile_item_id") or "") == goal_id
    ]
    result.record.extra["continue_excluded"] = continue_excluded
    result.add(
        "ongoing:continued-across-sessions",
        "考研" in adopted or "英语" in adopted or "阅读" in adopted or bool(continue_excluded),
        f"采用条目={continue_turn.get('adopted_items')!r} 本轮刚整理={continue_excluded!r}",
    )
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    pause_turn = session.chat(conversation_b, spec.dev_turns[2])
    result.record.extra["pause_turn"] = {
        "answer": pause_turn.get("assistant_content"),
        "items": [
            {
                "text": entry.get("text"),
                "status": entry.get("status"),
            }
            for entry in session.items()
            if "考研" in str(entry.get("text") or "")
        ],
    }
    ongoing = session.ongoing()
    result.record.extra["ongoing"] = _ongoing_summary(ongoing)
    paused = _ongoing_state_for(ongoing, "考研")
    result.add(
        "ongoing:paused-after-chat-signal",
        paused == "paused",
        f"事项状态解析={paused!r}",
    )
    _, follow_up = session.chat_in_new_conversation(spec.follow_up_question or "")
    result.record.extra["follow_up"] = follow_up_evidence(follow_up)
    result.add(
        "ongoing:paused-not-current",
        not any("考研" in str(item) for item in follow_up.get("adopted_items") or []),
        f"采用条目={follow_up.get('adopted_items')!r}",
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": spec.follow_up_question,
            "answer": follow_up.get("assistant_content"),
            "adopted_items": follow_up.get("adopted_items"),
        }
    )
    holdout = spec.extra_samples[0]
    sample_session = ctx.new_session(f"{spec.scenario_id}-holdout")
    sample_result = new_result(
        holdout.sample_id,
        KIND_HOLDOUT,
        f"{spec.scenario_id} 变体（holdout）",
        split=SPLIT_HOLDOUT,
        session=sample_session,
    )
    seed_conversation, _ = sample_session.start_chat("我在准备考研英语，进度到阅读部分。")
    sample_session.chat(seed_conversation, holdout.turns[0])
    complete_turn = sample_session.chat(seed_conversation, holdout.turns[1])
    sample_result.record.extra["complete_answer"] = complete_turn.get("assistant_content")
    sample_result.record.extra["note"] = holdout.note
    holdout_row = sample_session.active_item_row("考研英语")
    sample_result.record.extra["holdout_row"] = holdout_row
    completed_state = str((holdout_row or {}).get("goal_state") or "") or None
    sample_result.add(
        "ongoing:completed-after-signal",
        completed_state == "completed",
        f"目标状态={completed_state!r}",
    )
    ctx.register_side_result(finalize(sample_result, sample_session, started))
    return finalize(result, session, started)


def _ongoing_summary(ongoing: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in ongoing.get("items") or []:
        goal = item.get("goal") or {}
        summary.append(
            {
                "goal_text": goal.get("text"),
                "goal_state": goal.get("goal_state"),
                "validity_status": goal.get("validity_status"),
                "version": goal.get("version"),
                "progress": [
                    {
                        "text": entry.get("text"),
                        "validity_status": entry.get("validity_status"),
                    }
                    for entry in item.get("progress") or []
                ],
                "pending_questions": [
                    {
                        "text": entry.get("text"),
                        "validity_status": entry.get("validity_status"),
                    }
                    for entry in item.get("pending_questions") or []
                ],
                "domain_references": [
                    {
                        "domain": reference.get("domain"),
                        "state_code": reference.get("state_code"),
                        "version": reference.get("version"),
                        "resolved": reference.get("resolved"),
                    }
                    for reference in item.get("domain_references") or []
                ],
            }
        )
    return summary


def _ongoing_state_for(ongoing: dict[str, Any], keyword: str) -> str | None:
    for item in ongoing.get("items") or []:
        goal = item.get("goal") or {}
        if keyword in str(goal.get("text") or ""):
            return str(goal.get("goal_state") or goal.get("validity_status") or "")
    return None


def scenario_or_raise(scenario_id: str) -> ScenarioSpec:
    return SCENARIO_SPECS_BY_ID[scenario_id]


def is_simple_scenario(spec: ScenarioSpec) -> bool:
    return spec.driver == "conversation" and spec.scenario_id in {
        "T01",
        "T02",
        "T03",
        "T05",
        "T07",
        "T20",
    }


def run_sample_kind(sample_kind: str) -> str:
    return {
        "holdout": KIND_HOLDOUT,
        "negation": KIND_NEGATION,
        "mixed_subject": KIND_MIXED_SUBJECT,
        "synonym": KIND_SYNONYM,
    }.get(sample_kind, sample_kind)


__all__ = [
    "new_result",
    "finalize",
    "add_standard_checks",
    "run_conversation_scenario",
    "run_extra_sample",
    "follow_up_evidence",
    "run_t04",
    "run_t06",
    "run_t08",
    "run_t09",
    "run_t10",
    "run_t11",
    "run_t12",
    "scenario_or_raise",
    "is_simple_scenario",
]
