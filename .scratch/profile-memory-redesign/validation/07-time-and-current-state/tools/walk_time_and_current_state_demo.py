"""画像工单 07 演示走查：时区/周期/生命周期定位/回顾用途/页面证据。

运行（仓库根、conda agent 环境）：

    DIR=.scratch/profile-memory-redesign/validation/07-time-and-current-state
    python $DIR/tools/walk_time_and_current_state_demo.py \
        --out $DIR/evidence/demo-walkthrough-<stamp>.txt \
        [--proxy http://127.0.0.1:7890]

结构：

- [1] 真实模型、development 装配：相对日期按 Asia/Shanghai 解释、周期安排
  结构化落库，并在证据投影中与存储一致。
- [2] 生命周期定位：先确定性写入带窗口的目标（时间锚可控），再由真实聊天
  通道验证暂停只命中唯一目标、恢复不延长旧期限。
- [3] 页面证据（GET /profiles/items/{id}/evidence）与存储一致。
- [4] 回顾用途：独立白纸账户里种一条过期经历，复盘问题经真实聊天链路，
  运行记录切片与模型载荷都携带「既往经历（回顾用途）」，且使用不改条目。
- [5] 歧义守卫在独立账户上复验：目标词同时包含于多条目标时不修改任何一条。

输出是可直接入档的证据文本与 -model-outputs.json（聊天载荷片段，绝不
记录凭据）。真实模型的抽取存在波动：落库缺失时以确定性写入补种子并在
文本中如实标注（观察项），机制正确性仍由自动化测试锁死。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

_SLICE_MARKER = "以下是本轮为你参考的已授权信息"

_CREDENTIAL_ENV_KEYS = (
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_TAVILY_API_KEY_FILE",
)


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"


def _prepare_environment(run_dir: Path, proxy: str) -> None:
    for key in list(os.environ):
        if key.upper() in _CREDENTIAL_ENV_KEYS:
            os.environ.pop(key, None)
    os.environ["BRIDGES_ENVIRONMENT"] = "development"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{run_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue07-demo-{uuid.uuid4().hex}"
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy


def _register(client: Any, tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue07demo_{digest[:10]}",
            "qq_email": f"138{int(digest[:8], 16) % 10**8:08d}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    from bridges.api.auth import SESSION_COOKIE_NAME

    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    return str(
        client.app.state.identity_service.resolve_session(token).subject.account_id
    )


class _RecordingChatAdapter:
    """只录制聊天载荷，不改变真实适配器行为。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def stream_call(self, capability: Any, run_context: Any, payload: Any):
        system_blocks = [
            str(message.get("content") or "")
            for message in (payload.get("messages") or [])
            if str(message.get("role")) == "system"
        ]
        self.calls.append(
            {
                "run_id": run_context.run_id,
                "slice_blocks": [
                    block for block in system_blocks if _SLICE_MARKER in block
                ],
            }
        )
        yield from self._inner.stream_call(capability, run_context, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--app-data", default="")
    parser.add_argument("--proxy", default="")
    args = parser.parse_args()

    lines: list[str] = []

    def emit(message: str) -> None:
        print(message)
        lines.append(message)

    run_dir = REPO_ROOT / ".tmp" / f"issue07-demo-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _prepare_environment(run_dir, args.proxy)

    from fastapi.testclient import TestClient

    from bridges.api.main import create_app
    from bridges.config import get_settings
    from bridges.credentials.store import OsCredentialStore
    from bridges.evaluation.profile_journey_runner import ProfileJourneyCollector
    from bridges.profiles.atomic import PROFILE_TIMEZONE, parse_validity_window

    get_settings.cache_clear()
    app_data = Path(args.app_data) if args.app_data else _local_app_data()
    store = OsCredentialStore(data_dir=app_data, namespace="runtime")
    app = create_app(runtime_credential_store=store)

    gateway = app.state.model_gateway
    chat_recorder = _RecordingChatAdapter(gateway.get_adapter("qwen_text_chat", "1"))
    gateway.register_adapter("qwen_text_chat", "1", chat_recorder)

    client = TestClient(app)
    client.__enter__()
    account_id = _register(client, f"issue07-demo-{uuid.uuid4().hex[:6]}")
    collector = ProfileJourneyCollector(
        app=app,
        client=client,
        database=app.state.bridges_database,
        account_id=account_id,
    )
    atomic = app.state.atomic_profile_service
    local_tz = ZoneInfo(PROFILE_TIMEZONE)

    conversation_id: str | None = None

    def ask(content: str, tag: str) -> dict[str, Any]:
        nonlocal conversation_id
        attempt = 0
        if conversation_id is None:
            conversation_id = collector.start_conversation(
                content, f"issue07-demo-{tag}-0"
            )
            detail = collector.conversation_detail(conversation_id)
            sent = {
                "user_message_id": next(
                    m["message_id"]
                    for m in detail["messages"]
                    if m["role"] == "user"
                ),
                "assistant_message_id": next(
                    m["message_id"]
                    for m in detail["messages"]
                    if m["role"] == "assistant"
                ),
            }
        else:
            while True:
                attempt += 1
                try:
                    sent = collector.send_turn(
                        conversation_id, content, f"issue07-demo-{tag}-{attempt}"
                    )
                    break
                except AssertionError as exc:  # 上一轮仍在生成：等它收尾再发
                    if attempt >= 5:
                        raise
                    emit(f"    （发送被拒：{str(exc)[:120]}；等待后重试）")
                    collector.drive_until_idle(conversation_id, timeout=300)
                    time.sleep(5)
        turn: dict[str, Any] = {}
        for _ in range(4):  # 慢流式：只等待与重捕，不重发，避免产生垃圾轮次
            idle = collector.drive_until_idle(conversation_id, timeout=300)
            turn = collector.capture_turn(
                conversation_id,
                sent,
                user_content=content,
                expectation="",
            )
            turn["idle"] = idle
            turn["conversation_id"] = conversation_id
            if (
                turn["assistant_status"] == "done"
                or turn.get("assistant_content")
            ):
                return turn
            emit(f"    （status={turn['assistant_status']}，等待后重捕）")
            time.sleep(5)
        return turn

    def dump_turn(turn: dict[str, Any]) -> None:
        emit(f"    助手（status={turn['assistant_status']}）：{turn['assistant_content'][:160]!r}")
        for run in turn.get("extraction_runs") or []:
            emit(
                "    提取 run：source={source} status={status} outcome={outcome}"
                " error={error}".format(
                    source=run.get("source"),
                    status=run.get("status"),
                    outcome=run.get("outcome"),
                    error=run.get("last_error"),
                )
            )

    def items(account: str | None = None) -> list[Any]:
        return [
            item
            for item in atomic._repository.list_items(  # noqa: SLF001
                account or account_id, include_withdrawn=True
            )
            if item.text
        ]

    def find_item(text: str, account: str | None = None) -> Any | None:
        target = text.rstrip("。！？!?.")
        return next(
            (
                item
                for item in items(account)
                if item.text.rstrip("。！？!?.") == target
            ),
            None,
        )

    def stored_slice(
        assistant_message_id: str, account: str | None = None
    ) -> dict[str, Any]:
        row = app.state.bridges_database.connection.execute(
            "SELECT config_json FROM generation_runs WHERE account_id = ?"
            " AND assistant_message_id = ? ORDER BY created_at DESC LIMIT 1",
            (account or account_id, assistant_message_id),
        ).fetchone()
        if row is None or not row["config_json"]:
            return {}
        return (json.loads(row["config_json"]).get("adopted_profile_slice")) or {}

    def dump_items(label: str = "", account: str | None = None) -> list[Any]:
        current = items(account)
        if label:
            emit(f"    {label}")
        for item in current:
            extra = [f"goal_state={item.goal_state.value}"]
            if item.recurrence is not None:
                extra.append(f"recurrence={item.recurrence.phrase}")
            if item.validity_phrase:
                extra.append(f"phrase={item.validity_phrase}")
            if item.valid_until is not None:
                extra.append(f"valid_until={item.valid_until.isoformat()}")
            emit(
                "      [{status}] {text!r} v{version} 依据{count}条（{detail}）".format(
                    status=item.status.value,
                    text=item.text,
                    version=item.version,
                    count=len(item.source_message_ids),
                    detail="；".join(extra),
                )
            )
        return current

    def slice_lines(account: str, question: str, *, run_id: str) -> list[str]:
        from bridges.contracts.profile_adoption import (
            ProfileSlicePurpose,
            ProfileTaskKind,
        )
        from bridges.profiles.purpose import build_purpose

        purpose = build_purpose(mode="companion", query=question)
        adopted = atomic.compile_adopted_slice(
            account, run_id=run_id, purpose=purpose
        )
        default = atomic.compile_adopted_slice(
            account,
            run_id=f"{run_id}-default",
            purpose=ProfileSlicePurpose(
                mode="companion",
                task_kind=ProfileTaskKind.PLAN,
                query=question,
            ),
        )
        return [
            "当前用途 task_kind={kind}：{adopted}".format(
                kind=purpose.task_kind.value,
                adopted=[
                    (entry.fact_text, entry.adoption_reason)
                    for entry in adopted.adopted_items
                ],
            ),
            "计划用途排除：{default}".format(
                default=[
                    (entry.fact_text, entry.exclusion_reason)
                    for entry in default.excluded_items
                ]
            ),
        ]

    emit("[0] development 装配 + 真实模型（聊天/抽取）；凭据来自操作系统凭据库")
    emit(f"    产品时区：{PROFILE_TIMEZONE}；运行目录：{run_dir}")

    emit("")
    emit("[1] 相对日期按 Asia/Shanghai 解释、周期安排结构化落库（真实模型）")
    time_turn = ask("我下周要参加英语六级考试。", "time-1")
    emit("  用户：我下周要参加英语六级考试。")
    dump_turn(time_turn)
    dump_items("落库条目：")
    exam = next((item for item in items() if "英语六级" in item.text), None)
    if exam is None or not exam.validity_phrase:
        if exam is None:
            emit(
                "    观察：真实模型本轮未落库考试条目（抽取波动）；"
                "改用同一消息的确定性写入继续验证。"
            )
        else:
            emit(
                f"    观察：真实模型落库条目未保留时间表达（{exam.text!r}，抽取波动）；"
                "同一消息的确定性写入仍带窗口，继续验证存储侧。"
            )
        exam = atomic.remember(account_id, "我下周要参加英语六级考试。")
    if exam.validity_phrase:
        anchor_local = exam.validity_anchor_at.astimezone(local_tz)
        local_from = (
            exam.valid_from.astimezone(local_tz).isoformat()
            if exam.valid_from
            else None
        )
        local_until = (
            exam.valid_until.astimezone(local_tz).isoformat()
            if exam.valid_until
            else None
        )
        emit(
            f"    检查：时间表达={exam.validity_phrase!r} 锚（本地）="
            f"{anchor_local.isoformat()} 窗口（本地）={local_from}→{local_until}"
        )
        reparsed = parse_validity_window(exam.text, exam.validity_anchor_at)
        emit(
            "    检查：窗口与按锚重解析一致={ok}（解析一次、不随使用重解释）".format(
                ok=bool(
                    reparsed
                    and reparsed.valid_from == exam.valid_from
                    and reparsed.valid_until == exam.valid_until
                )
            )
        )
    else:
        emit("    检查：本轮条目没有明示期限（模型原话不含时间表达）——观察项")

    lab_turn = ask("我每周三晚上去实验室做实验。", "time-2")
    emit("  用户：我每周三晚上去实验室做实验。")
    dump_turn(lab_turn)
    dump_items("周期条目：")
    lab = find_item("我每周三晚上去实验室做实验")
    if lab is None:
        emit("    观察：真实模型本轮未落库周期条目（抽取波动）；改用确定性写入继续验证。")
        lab = atomic.remember(account_id, "我每周三晚上去实验室做实验")
    emit(
        f"    检查：周期={lab.recurrence} 无期限={lab.valid_until is None}"
    )
    lab_evidence = atomic.item_evidence(account_id, lab.profile_item_id)
    rec_phrase = (
        lab_evidence.recurrence.phrase if lab_evidence.recurrence else None
    )
    emit(
        f"    证据投影：recurrence_phrase={rec_phrase!r} "
        f"validity_status={lab_evidence.validity_status.value}"
    )

    emit("")
    emit("[2] 生命周期定位：暂停/恢复只命中唯一目标，恢复不续期（真实聊天管道）")
    goal = atomic.remember(account_id, "我计划下周考六级")
    emit(
        f"    种子目标：{goal.text!r} 窗口={goal.valid_from}→{goal.valid_until}"
        "（按下周=本地自然周）"
    )
    pause_turn = ask("暂时不考六级了。", "lifecycle-1")
    emit("  用户：暂时不考六级了。")
    dump_turn(pause_turn)
    paused = find_item("我计划下周考六级")
    emit(
        "    检查：暂停只命中该目标={ok}（goal_state={state}）".format(
            ok=paused is not None and paused.goal_state.value == "paused",
            state=paused.goal_state.value if paused else None,
        )
    )
    for line in slice_lines(account_id, "帮我规划六级备考计划", run_id="slice-paused"):
        emit(f"    切片：{line}")

    resume_turn = ask("我继续考六级。", "lifecycle-2")
    emit("  用户：我继续考六级。")
    dump_turn(resume_turn)
    resumed = find_item("我计划下周考六级")
    emit(
        "    检查：恢复不延长旧期限={ok}（goal_state={state}；窗口保持 {until}）".format(
            ok=(
                resumed is not None
                and resumed.goal_state.value == "active"
                and resumed.valid_until == paused.valid_until
            ),
            state=resumed.goal_state.value if resumed else None,
            until=resumed.valid_until if resumed else None,
        )
    )

    emit("")
    emit("[3] 页面证据与存储一致（GET /profiles/items/{id}/evidence）")
    response = client.get(f"/profiles/items/{lab.profile_item_id}/evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    emit(
        "    周期条目：validity_status={status} phrase={phrase!r} recurrence={recurrence}".format(
            status=body["validity_status"],
            phrase=body["validity_phrase"],
            recurrence=body.get("recurrence"),
        )
    )
    emit("    页面视图（属主账户）：")
    page = client.get("/profiles/items")
    assert page.status_code == 200, page.text
    for entry in page.json():
        emit(f"      {entry['text']!r} v{entry['version']} 来源={entry['write_origin']}")

    emit("")
    emit("[4] 回顾用途：过期经历在复盘任务里进入模型载荷（独立账户，白纸环境）")
    review_account = _register(client, f"issue07-demo-rev-{uuid.uuid4().hex[:6]}")
    expired_at = datetime.now(UTC) - timedelta(days=30)
    expired = atomic.remember(
        review_account, "我下周有英语六级考试", source_at=expired_at
    )
    emit(
        f"    种子过期经历：{expired.text!r}（锚 {expired.validity_anchor_at}，已过期）"
    )
    review_mark = len(chat_recorder.calls)
    conversation_id = None
    review_turn = ask("帮我复盘一下英语六级考试的经历。", "review-1")
    emit("  用户：帮我复盘一下英语六级考试的经历。")
    dump_turn(review_turn)
    stored = stored_slice(
        review_turn["assistant_message_id"], account=review_account
    )
    emit(
        "    运行记录切片：task_kind={kind} adopted={items}".format(
            kind=(stored.get("purpose") or {}).get("task_kind"),
            items=[
                (entry.get("fact_text"), entry.get("adoption_reason"))
                for entry in (stored.get("adopted_items") or [])
            ],
        )
    )
    review_blocks = [
        block
        for call in chat_recorder.calls[review_mark:]
        for block in (call.get("slice_blocks") or [])
    ]
    history_lines = [
        line
        for block in review_blocks
        for line in block.splitlines()
        if "既往经历（回顾用途）" in line
    ]
    emit(
        "    检查：模型载荷注入既往经历={ok}：{line}".format(
            ok=bool(history_lines),
            line=history_lines[0] if history_lines else "（未注入）",
        )
    )
    after_review = atomic.get_item(review_account, expired.profile_item_id)
    emit(
        "    检查：回顾使用不改变条目={ok}（v{before}→v{after}，正文与期限不变）".format(
            ok=(
                after_review.version == expired.version
                and after_review.valid_until == expired.valid_until
                and after_review.confidence == expired.confidence
            ),
            before=expired.version,
            after=after_review.version,
        )
    )
    expired_evidence = client.get(
        f"/profiles/items/{expired.profile_item_id}/evidence"
    )
    assert expired_evidence.status_code == 200, expired_evidence.text
    emit(
        "    页面证据：validity_status={status} phrase={phrase!r}".format(
            status=expired_evidence.json()["validity_status"],
            phrase=expired_evidence.json()["validity_phrase"],
        )
    )

    emit("")
    emit("[5] 歧义守卫：目标词同时包含于多条目标时不批量修改（独立账户）")
    other_account = _register(client, f"issue07-demo-amb-{uuid.uuid4().hex[:6]}")
    atomic.remember(other_account, "我计划考研英语")
    atomic.remember(other_account, "我计划考研数学")
    emit("    种子目标：我计划考研英语 / 我计划考研数学")
    conversation_id = None
    ambiguous_turn = ask("暂时不考研了。", "ambiguous-1")
    emit("  用户：暂时不考研了。")
    dump_turn(ambiguous_turn)
    ambiguous_states = {
        item.text: item.goal_state.value
        for item in items(other_account)
        if "考研" in item.text
    }
    emit(
        "    检查：歧义时两条都不修改={ok}（states={states}）".format(
            ok=bool(ambiguous_states)
            and all(state == "active" for state in ambiguous_states.values()),
            states=ambiguous_states,
        )
    )

    client.__exit__(None, None, None)
    emit("")
    emit(f"运行目录：{run_dir}")

    if args.out:
        target_out = Path(args.out)
        if not target_out.is_absolute():
            target_out = REPO_ROOT / target_out
        target_out.parent.mkdir(parents=True, exist_ok=True)
        target_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload_path = target_out.with_name(target_out.stem + "-model-outputs.json")
        payload_path.write_text(
            json.dumps(chat_recorder.calls, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[done] evidence written to {target_out} and {payload_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
