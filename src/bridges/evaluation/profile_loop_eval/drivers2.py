"""工单 18：T13–T20 场景专用驱动。"""

from __future__ import annotations

import time
from typing import Any

from bridges.evaluation.profile_loop_eval import metrics
from bridges.evaluation.profile_loop_eval.drivers import (
    add_standard_checks,
    finalize,
    new_result,
)
from bridges.evaluation.profile_loop_eval.session import AccountSession
from bridges.evaluation.profile_loop_eval.types import (
    KIND_HOLDOUT,
    KIND_MIXED_SUBJECT,
    KIND_SCENARIO,
    SPLIT_HOLDOUT,
    ScenarioSpec,
    SceneResult,
)

RESUME_TEXT = (
    "姓名：林一舟\n"
    "教育经历：本科就读于示例大学 计算机科学与技术专业，2023 年入学\n"
    "技能：Python、数据结构、基础算法\n"
)


def run_t13(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """明确授权的敏感自述：按用途保存；情绪不长期化；凭据不入库。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
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
        "sensitive:emotion-not-persisted",
        not any("焦虑" in text for text in texts),
        f"活动条目={texts!r}",
    )
    peanut = session.find_item("花生", active_only=False)
    result.add("sensitive:authorized-item-created", peanut is not None, f"条目={peanut!r}")
    peanut_row = session.active_item_row("花生")
    if peanut_row is not None:
        result.record.extra["peanut_row"] = {
            "text": peanut_row.get("text"),
            "usage_purpose": peanut_row.get("usage_purpose"),
            "validity_phrase": peanut_row.get("validity_phrase"),
        }
        result.add(
            "sensitive:usage-purpose-recorded",
            bool(peanut_row.get("usage_purpose")),
            f"用途={peanut_row.get('usage_purpose')!r}",
        )
    follow_up_question = "我打算点餐，有什么要注意的吗？"
    _, follow_up = session.chat_in_new_conversation(follow_up_question)
    result.record.extra["follow_up"] = {
        "question": follow_up_question,
        "answer": follow_up.get("assistant_content"),
        "adopted_items": follow_up.get("adopted_items") or [],
    }
    adopted = " ".join(follow_up.get("adopted_items") or [])
    result.add(
        "sensitive:used-within-purpose",
        "花生" in adopted,
        f"采用条目={follow_up.get('adopted_items')!r}",
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": follow_up_question,
            "answer": follow_up.get("assistant_content"),
            "adopted_items": follow_up.get("adopted_items"),
        }
    )
    extra = spec.extra_samples[0]
    holdout_session = ctx.new_session(f"{spec.scenario_id}-holdout")
    holdout_conversation, _ = holdout_session.start_chat(extra.turns[0])
    holdout_result = new_result(
        extra.sample_id,
        KIND_HOLDOUT,
        f"{spec.scenario_id} 变体（holdout）",
        split=SPLIT_HOLDOUT,
        session=holdout_session,
    )
    add_standard_checks(
        holdout_result,
        groups=extra.expected_groups,
        forbidden=(),
        session=holdout_session,
    )
    ctx.register_side_result(finalize(holdout_result, holdout_session, started))
    mixed = spec.extra_samples[1]
    mixed_session = ctx.new_session(f"{spec.scenario_id}-mixed")
    mixed_conversation, _ = mixed_session.start_chat(mixed.turns[0])
    mixed_result = new_result(
        mixed.sample_id,
        KIND_MIXED_SUBJECT,
        f"{spec.scenario_id} 变体（mixed_subject）",
        split="variant",
        session=mixed_session,
    )
    add_standard_checks(
        mixed_result,
        groups=(),
        forbidden=mixed.forbidden_item_keywords,
        session=mixed_session,
    )
    ctx.register_side_result(finalize(mixed_result, mixed_session, started))
    _ = holdout_conversation, mixed_conversation
    return finalize(result, session, started)


def run_t14(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """指定材料更新画像：普通上传不触发；明确指定后真实解析并带来源。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    draft = session.upload_attachment("合成简历.txt", RESUME_TEXT.encode("utf-8"))
    conversation_id = session.start_with_attachment(spec.dev_turns[0], [draft["object_id"]])
    baseline_items = session.items()
    baseline_runs = session.material_runs()
    result.add(
        "material:ordinary-upload-no-extraction",
        not baseline_runs,
        f"材料运行={baseline_runs!r}",
    )
    session.chat(conversation_id, spec.dev_turns[1], ingestion=True, timeout=900.0)
    runs = session.material_runs()
    result.record.extra["material_runs"] = runs
    succeeded = [
        run
        for run in runs
        if str(run.get("status")) == "succeeded" and str(run.get("outcome")) == "succeeded_written"
    ]
    result.add(
        "material:extraction-succeeded",
        bool(succeeded),
        f"材料运行={runs!r}",
    )
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    items = [
        item
        for item in session.items()
        if "示例大学" in str(item.get("text") or "") or "计算机" in str(item.get("text") or "")
    ]
    result.record.extra["material_items"] = items
    for item in items:
        evidence = session.item_evidence(item["profile_item_id"])
        sources = evidence.get("material_sources") or []
        result.add(
            f"material:evidence-for:{str(item.get('text'))[:18]}",
            bool(sources)
            and all(
                str(source.get("status")) == "available"
                and (str(source.get("quote_status")) == "recorded" or bool(source.get("quote")))
                for source in sources
            ),
            f"材料来源={sources!r}",
        )
    result.add(
        "material:no-stray-items-before-directive",
        len(baseline_items) == 0,
        f"指令前条目={baseline_items!r}",
    )
    return finalize(result, session, started)


def run_t15(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """真实恢复：消息入队后在提取前重启，恢复后最终提交且不重复。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    response = session.environment.client.post(
        "/chat/first-turn",
        json={
            "content": spec.dev_turns[0],
            "idempotency_key": f"issue18-t15-{session.account.tag}",
        },
    )
    assert response.status_code in (200, 201), response.text
    conversation_id = str(response.json()["conversation"]["conversation_id"])
    tasks_before = session.environment.extraction_tasks_summary()
    result.record.extra["tasks_before_restart"] = tasks_before
    ctx.restart()
    resumed = ctx.session_for(session.account)
    resumed.drive(conversation_id)
    texts = resumed.active_texts()
    result.record.turns = []
    result.record.conversations = []
    result.record.account_id = resumed.account_id
    result.add(
        "recovery:fact-committed-after-restart",
        any("吉他" in text for text in texts),
        f"活动条目={texts!r}",
    )
    guitar_items = [text for text in texts if "吉他" in text]
    result.add(
        "recovery:no-duplicate",
        len(guitar_items) <= 1,
        f"吉他条目={guitar_items!r}",
    )
    runs = resumed.collector.extraction_runs()
    result.record.extra["extraction_runs_after"] = runs
    result.record.extra["tasks_after_restart"] = resumed.environment.extraction_tasks_summary()
    result.record.extra["queue_depth_samples"] = resumed.queue_depth_samples
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=resumed)
    result.record.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def run_t16(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """反馈纠正：条目定点改写、修正回答、新会话不复发。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    seeded = session.find_item("研究生", active_only=False)
    result.add("setup:wrong-item-created", seeded is not None, f"条目={seeded!r}")
    correction_turn = session.chat(conversation_id, spec.dev_turns[1])
    result.record.extra["correction_answer"] = correction_turn.get("assistant_content")
    texts = session.active_texts()
    result.add(
        "revision:corrected-item-created",
        any("本科" in text for text in texts),
        f"活动条目={texts!r}",
    )
    result.add(
        "revision:old-error-not-active",
        not any("研究生" in text and "本科" not in text for text in texts),
        f"活动条目={texts!r}",
    )
    question = "我正在读什么学历？"
    _, follow_up = session.chat_in_new_conversation(question)
    result.record.extra["follow_up"] = {
        "question": question,
        "answer": follow_up.get("assistant_content"),
        "adopted_items": follow_up.get("adopted_items") or [],
    }
    adopted_items = [str(item) for item in follow_up.get("adopted_items") or []]
    result.add(
        "recurrence:correct-fact-adopted",
        any("本科" in item for item in adopted_items),
        f"采用条目={adopted_items!r}",
    )
    result.add(
        "recurrence:old-error-not-recurring",
        not any("研究生" in item and "本科" not in item for item in adopted_items),
        f"采用条目={adopted_items!r}",
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": question,
            "answer": follow_up.get("assistant_content"),
            "adopted_items": adopted_items,
        }
    )
    return finalize(result, session, started)


def run_t17(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """记录/使用控制与账户隔离；重新启用不回溯禁止记录期间内容。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    controls_before = session.controls()
    result.record.extra["controls_initial"] = controls_before
    session.controls(recording_enabled=False)
    conversation_id, _ = session.start_chat("我开始学油画。")
    result.add(
        "controls:recording-off-blocks-new",
        session.find_item("油画", active_only=False) is None,
        f"全部条目={session.all_item_texts()!r}",
    )
    session.controls(recording_enabled=True)
    session.chat(conversation_id, "我叫小林。")
    result.add(
        "controls:recording-on-resumes",
        session.find_item("小林") is not None,
        f"活动条目={session.active_texts()!r}",
    )
    session.controls(usage_enabled=False)
    _, usage_off_turn = session.chat_in_new_conversation("我叫什么名字？")
    result.record.extra["usage_off_follow_up"] = {
        "question": "我叫什么名字？",
        "answer": usage_off_turn.get("assistant_content"),
        "adopted_items": usage_off_turn.get("adopted_items") or [],
    }
    result.add(
        "controls:usage-off-blocks-injection",
        not any("小林" in str(item) for item in usage_off_turn.get("adopted_items") or []),
        f"采用条目={usage_off_turn.get('adopted_items')!r}",
    )
    result.add(
        "controls:usage-off-keeps-records",
        session.find_item("小林") is not None,
        f"活动条目={session.active_texts()!r}",
    )
    session.controls(usage_enabled=True)
    other_account = ctx.register_account(f"{spec.scenario_id}-isolation")
    other = ctx.session_for(other_account)
    _, other_turn = other.chat_in_new_conversation("我叫什么名字？")
    result.record.extra["isolation_follow_up"] = {
        "answer": other_turn.get("assistant_content"),
        "adopted_items": other_turn.get("adopted_items") or [],
    }
    result.add(
        "isolation:other-account-empty",
        not other.items()
        and not any("小林" in str(item) for item in other_turn.get("adopted_items") or []),
        f"另一账户条目={other.all_item_texts()!r} 采用={other_turn.get('adopted_items')!r}",
    )
    lin = session.find_item("小林")
    if lin is not None:
        response = other.environment.client.get(
            f"/profiles/items/{lin['profile_item_id']}/evidence"
        )
        result.add(
            "isolation:cross-account-evidence-404",
            response.status_code == 404,
            f"HTTP {response.status_code}",
        )
    result.add(
        "retroactive:painting-still-not-recorded",
        session.find_item("油画", active_only=False) is None,
        f"全部条目={session.all_item_texts()!r}",
    )
    return finalize(result, session, started)


def run_t18(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """条目编辑后概览立即失效，重建后收敛；删除同样失效。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    item = session.find_item("大二") or session.find_item("杭州")
    result.add("setup:item-created", item is not None, f"条目={item!r}")
    if item is None:
        return finalize(result, session, started)
    overview_before = session.overview()
    result.record.extra["overview_before"] = overview_before
    before_text = _overview_text(overview_before)
    result.add(
        "overview:reflects-item",
        "大二" in before_text or "杭州" in before_text,
        f"概览正文={before_text[:200]!r}",
    )
    session.patch_item(item["profile_item_id"], "我住在杭州，在读大三。", item["version"])
    overview_after_edit = session.overview()
    result.record.extra["overview_after_edit"] = overview_after_edit
    after_edit_text = _overview_text(overview_after_edit)
    result.add(
        "overview:stale-not-presented",
        "大二" not in after_edit_text,
        f"编辑后概览正文={after_edit_text[:200]!r}",
    )
    rebuilt = _wait_overview(session, "大三")
    result.record.extra["overview_rebuilt"] = rebuilt
    rebuilt_text = _overview_text(rebuilt)
    result.add(
        "overview:rebuilt-with-edit",
        "大三" in rebuilt_text and "大二" not in rebuilt_text,
        f"重建后概览正文={rebuilt_text[:200]!r}",
    )
    current = session.find_item("大三", active_only=False)
    if current is not None:
        session.delete_item(current["profile_item_id"], current["version"])
    overview_after_delete = session.overview()
    result.record.extra["overview_after_delete"] = overview_after_delete
    after_delete_text = _overview_text(overview_after_delete)
    result.add(
        "overview:delete-invalidates",
        "大三" not in after_delete_text and "大二" not in after_delete_text,
        f"删除后概览正文={after_delete_text[:200]!r}",
    )
    _ = conversation_id
    return finalize(result, session, started)


def _overview_text(overview: dict[str, Any]) -> str:
    parts: list[str] = []
    for statement in overview.get("statements") or []:
        parts.append(str(statement.get("text") or ""))
    for key in ("state", "notice"):
        if overview.get(key):
            parts.append(str(overview[key]))
    return " ".join(parts)


def _wait_overview(session: AccountSession, marker: str) -> dict[str, Any]:
    overview = session.overview()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        text = _overview_text(overview)
        if marker in text:
            return overview
        session.drive(session.turns[-1]["conversation_id"]) if session.turns else None
        time.sleep(0.5)
        overview = session.overview()
    return overview


def run_t19(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """来源时间锚定：相对时间按原消息时间解析；来源删除与重整另行演练。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    item = session.find_item("数学建模")
    row: dict[str, Any] | None = None
    if item is not None:
        row = dict(
            session.database.connection.execute(
                "SELECT text, valid_from, valid_until, validity_anchor_at,"
                " validity_phrase, created_at FROM profile_items"
                " WHERE profile_item_id = ?",
                (item["profile_item_id"],),
            ).fetchone()
        )
    result.record.extra["item_validity"] = row
    result.add(
        "time:relative-anchored-to-source",
        row is not None and bool(row.get("valid_from") or row.get("validity_phrase")),
        f"时效字段={row!r}",
    )
    result.answer_review.append(
        {
            "sample_id": spec.scenario_id,
            "question": spec.dev_turns[0],
            "answer": (session.turns[-1].get("assistant_content") if session.turns else None),
            "adopted_items": (session.turns[-1].get("adopted_items") if session.turns else None),
        }
    )
    _ = conversation_id
    return finalize(result, session, started)


def run_t20(ctx: Any, spec: ScenarioSpec, session: AccountSession) -> SceneResult:
    """主题外新事实与多主题事实：不丢事实、不复制、追问采用约束。"""

    started = time.monotonic()
    result = new_result(spec.scenario_id, KIND_SCENARIO, spec.title, session=session)
    conversation_id, _ = session.start_chat(spec.dev_turns[0])
    session.chat(conversation_id, spec.dev_turns[1])
    add_standard_checks(result, groups=spec.expected_groups, forbidden=(), session=session)
    weekday_items = [
        str(item.get("text") or "")
        for item in session.active_items()
        if "周三" in str(item.get("text") or "")
    ]
    result.record.extra["weekday_items"] = weekday_items
    result.add(
        "dedup:multi-topic-single-item",
        0 < len(weekday_items) <= 2,
        f"周三相关条目={weekday_items!r}",
    )
    texts = session.active_texts()
    result.add(
        "dedup:no-duplicate-text",
        len(texts) == len(set(texts)),
        f"活动条目={texts!r}",
    )
    _, follow_up = session.chat_in_new_conversation(spec.follow_up_question or "")
    result.record.extra["follow_up"] = {
        "question": spec.follow_up_question,
        "answer": follow_up.get("assistant_content"),
        "adopted_items": follow_up.get("adopted_items") or [],
    }
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
    return finalize(result, session, started)


__all__ = [
    "RESUME_TEXT",
    "run_t13",
    "run_t14",
    "run_t15",
    "run_t16",
    "run_t17",
    "run_t18",
    "run_t19",
    "run_t20",
]
