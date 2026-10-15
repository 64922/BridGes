"""画像工单 06 演示走查：重复/纠错/状态变化/并存/冲突 + 权威与版本守卫。

运行（仓库根、conda agent 环境）：

    python .scratch/profile-memory-redesign/validation/06-revision-conflict-and-deduplication/tools/walk_revision_conflict_demo.py \
        --out .scratch/profile-memory-redesign/validation/06-revision-conflict-and-deduplication/evidence/demo-walkthrough-<stamp>.txt

结构：

- [1]–[5] 真实模型、development 装配：聊天重复换说法归并、纠错与状态变化
  的历史语义、并行目标并存、冲突澄清（相关注入/无关不打扰）与页面视图。
- [6] worker 提交前插入用户修改：确定性替身抽取器（只替换语义抽取，
  worker/提交/守卫为真实路径）；重放不覆盖用户编辑，过期版本写回 409。
- [7] 页面状态与存储一致：`GET /profiles/items` 全量视图按条目输出。

输出是可直接入档的证据文本与 -model-outputs.json（聊天载荷片段，绝不
记录凭据）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

_SLICE_MARKER = "以下是本轮为你参考的已授权信息"
_CLARIFY_MARKER = "以下信息在你此前的说法里互相矛盾"

_CREDENTIAL_ENV_KEYS = (
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_TAVILY_API_KEY_FILE",
)


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"


def _prepare_environment(run_dir: Path) -> None:
    for key in list(os.environ):
        if key.upper() in _CREDENTIAL_ENV_KEYS:
            os.environ.pop(key, None)
    os.environ["BRIDGES_ENVIRONMENT"] = "development"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{run_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue06-demo-{uuid.uuid4().hex}"


def _register(client: Any, tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue06demo_{digest[:10]}",
            "qq_email": f"137{int(digest[:8], 16) % 10**8:08d}@qq.com",
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
                "clarify_blocks": [
                    block for block in system_blocks if _CLARIFY_MARKER in block
                ],
            }
        )
        yield from self._inner.stream_call(capability, run_context, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--app-data", default="")
    args = parser.parse_args()

    lines: list[str] = []

    def emit(message: str) -> None:
        print(message)
        lines.append(message)

    run_dir = REPO_ROOT / ".tmp" / f"issue06-demo-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _prepare_environment(run_dir)

    from fastapi.testclient import TestClient

    from bridges.api.main import create_app
    from bridges.config import get_settings
    from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
    from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
    from bridges.credentials.store import OsCredentialStore
    from bridges.evaluation.profile_journey_runner import (
        ProfileJourneyCollector,
        install_deterministic_profile_extractor,
    )
    from bridges.profiles.commit import ProfileFactSubmission

    get_settings.cache_clear()
    app_data = Path(args.app_data) if args.app_data else _local_app_data()
    store = OsCredentialStore(data_dir=app_data, namespace="runtime")
    app = create_app(runtime_credential_store=store)

    gateway = app.state.model_gateway
    chat_recorder = _RecordingChatAdapter(gateway.get_adapter("qwen_text_chat", "1"))
    gateway.register_adapter("qwen_text_chat", "1", chat_recorder)

    client = TestClient(app)
    client.__enter__()
    account_id = _register(client, f"issue06-demo-{uuid.uuid4().hex[:6]}")
    collector = ProfileJourneyCollector(
        app=app,
        client=client,
        database=app.state.bridges_database,
        account_id=account_id,
    )
    atomic = app.state.atomic_profile_service

    def ask(content: str, tag: str) -> dict[str, Any]:
        conversation_id = getattr(collector, "_issue06_conversation", None)
        attempt = 0
        while True:
            attempt += 1
            if conversation_id is None:
                conversation_id = collector.start_conversation(
                    content, f"issue06-demo-{tag}-0"
                )
                collector._issue06_conversation = conversation_id  # type: ignore[attr-defined]
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
                sent = collector.send_turn(
                    conversation_id, content, f"issue06-demo-{tag}-{attempt}"
                )
            idle = collector.drive_until_idle(conversation_id)
            turn = collector.capture_turn(
                conversation_id,
                sent,
                user_content=content,
                expectation="",
            )
            turn["idle"] = idle
            turn["conversation_id"] = conversation_id
            if turn["assistant_status"] == "done" or attempt >= 2:
                return turn
            emit(
                "    （本轮生成异常 status={status}，重发一次）".format(
                    status=turn["assistant_status"]
                )
            )

    def dump_turn(turn: dict[str, Any]) -> None:
        emit(f"    助手（status={turn['assistant_status']}）：{turn['assistant_content'][:200]!r}")
        calls = chat_recorder.calls[-1] if chat_recorder.calls else {}
        emit(
            "    注入检查：画像切片={slice} 冲突澄清={clarify}".format(
                slice=bool(calls.get("slice_blocks")),
                clarify=bool(calls.get("clarify_blocks")),
            )
        )
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

    def dump_items(label: str = "") -> list[Any]:
        # 服务层只暴露 active 列表（页面视图口径）；演示需要看历史，直接读仓库。
        items = [
            item
            for item in atomic._repository.list_items(  # noqa: SLF001
                account_id, include_withdrawn=True
            )
            if item.text
        ]
        if label:
            emit(f"    {label}")
        for item in items:
            extra = []
            if item.retired_reason:
                extra.append(f"retired_reason={item.retired_reason}")
            if item.conflict_state.value != "none":
                extra.append(f"conflict_state={item.conflict_state.value}")
            if item.valid_until is not None:
                extra.append(f"valid_until={item.valid_until.isoformat()}")
            detail = f"（{'；'.join(extra)}）" if extra else ""
            emit(
                "      [{status}] {text!r} 依据 {count} 条 v{version} {detail}".format(
                    status=item.status.value,
                    text=item.text,
                    count=len(item.source_message_ids),
                    version=item.version,
                    detail=detail,
                )
            )
        return items

    emit("[0] development 装配 + 真实模型（聊天/抽取）；凭据来自操作系统凭据库")

    emit("")
    emit("[1] 重复换说法只增加依据；不同爱好并存（T08/T07 语义）")
    first = ask("我喜欢跑步。", "dup-1")
    emit("  用户：我喜欢跑步。")
    dump_turn(first)
    dump_items()
    second = ask("我真的很喜欢跑步。", "dup-2")
    emit("  用户：我真的很喜欢跑步。")
    dump_turn(second)
    items = dump_items("换说法后：")
    running = [item for item in items if "跑" in item.text]
    emit(
        "    检查：换说法未新增条目={ok}（跑步相关条目 {count} 条，依据 {evidence} 条）".format(
            ok=len(running) == 1,
            count=len(running),
            evidence=(
                len(running[0].source_message_ids) if running else 0
            ),
        )
    )
    if running:
        evidence = atomic.item_evidence(account_id, running[0].profile_item_id)
        emit(
            "    依据投影：原话={quote!r} 逐来源={sources}".format(
                quote=evidence.evidence_quote,
                sources=[
                    f"{source.status.value}/{source.quote_status.value}"
                    for source in evidence.sources
                ],
            )
        )
    swim_turn = ask("我也喜欢游泳。", "dup-3")
    emit("  用户：我也喜欢游泳。")
    dump_turn(swim_turn)
    items = dump_items("不同爱好：")
    emit(
        "    观察：本轮真实模型的抽取结果见上方提取 run（未新增候选时，"
        "爱好并存的确定性证据见 [6b]）"
    )

    emit("")
    emit("[2] 纠错：说错年级不保留成真实经历（T07）")
    atomic.commit_fact(
        account_id,
        ProfileFactSubmission(
            text="我现在读大四。",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我现在读大四。",
        ),
    )
    emit("  （先前的抽取结果：年级=大四已落库，模拟真实抽取成功的那一轮）")
    dump_items("纠错前：")
    corrected = ask("之前说错了，我不是大四，是大三。", "grade-1")
    emit("  用户：之前说错了，我不是大四，是大三。")
    dump_turn(corrected)
    corrected_items = dump_items("纠错后：")
    emit(
        "    检查：错误年级未被保留={ok}（含「大四」条目 {count} 条）".format(
            ok=not any("大四" in item.text for item in corrected_items),
            count=sum(1 for item in corrected_items if "大四" in item.text),
        )
    )

    emit("")
    emit("[3] 状态变化：保留真实历史（T07）")
    changed = ask("我现在升大四了。", "grade-3")
    emit("  用户：我现在升大四了。")
    dump_turn(changed)
    changed_items = dump_items("状态变化后：")
    retired = [
        item for item in changed_items if item.retired_reason is not None
    ]
    emit(
        "    检查：旧年级保留为历史={ok}（retired={reasons}）".format(
            ok=any(item.retired_reason and "state_change" in item.retired_reason
                   for item in retired),
            reasons=[item.retired_reason for item in retired],
        )
    )

    emit("")
    emit("[4] 并行目标并存（T07）")
    goals = ask("我准备考研，最近也在找工作。", "goals-1")
    emit("  用户：我准备考研，最近也在找工作。")
    dump_turn(goals)
    goal_items = dump_items("并行目标：")
    emit(
        "    观察：真实模型本轮落库目标/事实={texts}".format(
            texts=[item.text for item in goal_items if "考研" in item.text or "找" in item.text]
        )
    )

    emit("")
    emit("[5] 冲突未解决：不采用、依赖时澄清（T07/D05）")
    atomic.commit_fact(
        account_id,
        ProfileFactSubmission(
            text="我的专业是软件工程",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我的专业是软件工程",
        ),
    )
    atomic.commit_fact(
        account_id,
        ProfileFactSubmission(
            text="我的专业是计算机科学与技术",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_quote="我的专业是计算机科学与技术",
        ),
    )
    emit("  （两条互相矛盾的专业说法直接落库，模拟两次不同轮次的抽取结果）")
    relevant = ask("帮我推荐几门适合我专业方向的选修课。", "conflict-relevant")
    emit("  用户：帮我推荐几门适合我专业方向的选修课。")
    dump_turn(relevant)
    relevant_call = chat_recorder.calls[-1] if chat_recorder.calls else {}
    clarify_blocks = relevant_call.get("clarify_blocks") or []
    if clarify_blocks:
        question_line = next(
            (
                line
                for line in clarify_blocks[0].splitlines()
                if "以哪个为准" in line
            ),
            clarify_blocks[0].splitlines()[-1],
        )
        emit(f"    澄清问句（注入模型）：{question_line!r}")
    numbered = [
        line
        for block in relevant_call.get("slice_blocks") or []
        for line in block.splitlines()
        if re.match(r"^\[\d+\]", line)
    ]
    emit(f"    画像切片编号事实：{numbered or '（无）'}")
    emit(
        "    检查：争议条目不作为编号事实={ok}".format(
            ok=all(
                "软件工程" not in line and "计算机科学与技术" not in line
                for line in numbered
            )
        )
    )
    unrelated = ask("今晚吃什么好？", "conflict-unrelated")
    emit("  用户：今晚吃什么好？")
    dump_turn(unrelated)
    emit(
        "    检查：无关问题未注入澄清={ok}".format(
            ok=not (chat_recorder.calls[-1].get("clarify_blocks") or [])
        )
    )
    page = client.get("/profiles/items")
    assert page.status_code == 200, page.text
    for entry in page.json():
        if "专业" in entry["text"]:
            emit(
                "    页面视图：{text!r} conflict_state={state} v{version}"
                " 来源={origin}".format(
                    text=entry["text"],
                    state=entry["conflict_state"],
                    version=entry["version"],
                    origin=entry["write_origin"],
                )
            )

    emit("")
    emit("[6] worker 提交前插入用户修改：权威与版本守卫")
    from bridges.evaluation.profile_journey_runner import echo_profile_proposal

    extraction_stub = install_deterministic_profile_extractor(app)

    def forced_restatement(payload: dict[str, Any]) -> dict[str, Any]:
        proposal = echo_profile_proposal(payload)
        for item in proposal.get("items") or []:
            item["normalized_value"] = "我每天学习两小时"
            item["fact_text"] = "我每天学习两小时，固定在晚上七点"
        return proposal

    extraction_stub._responder = forced_restatement  # noqa: SLF001
    emit("  （自此替换为确定性替身抽取器；worker、提交与守卫均为真实路径）")
    thread_alive = getattr(app.state, "generation_executor_thread", None) is not None
    emit(f"  后台执行线程存在={thread_alive}（否：drive_until_idle 手动推进 worker）")
    race = ask("我每天学习两小时，固定在晚上七点，请按这个安排作息。", "race-1")
    emit("  用户：我每天学习两小时，固定在晚上七点，请按这个安排作息。")
    dump_turn(race)
    race_items = dump_items("worker 完成后：")
    target = next(item for item in race_items if "两小时" in item.text)
    conversation_id = race["conversation_id"]
    emit("  用户在重放提交前于页面改正文（插入到 worker 提交之前）：")
    edited = client.patch(
        f"/profiles/items/{target.profile_item_id}",
        json={"text": "我改成每天早上学习两小时。", "version": target.version},
    )
    assert edited.status_code == 200, edited.text
    emit(
        "    PATCH → 200，正文={text!r} v{version}（用户编辑后版本）".format(
            text=edited.json()["text"], version=edited.json()["version"]
        )
    )
    replay_sent = collector.send_turn(
        conversation_id,
        "再说一遍：我每天学习两小时，固定在晚上七点，别记错。",
        f"issue06-demo-{uuid.uuid4().hex[:8]}",
    )
    idle = collector.drive_until_idle(conversation_id)
    emit(f"  重放并推进 worker（自动抽取试图归并同一条）：{idle}")
    stub_hits = [
        call
        for call in extraction_stub.calls
        if "别记错" in call.get("current_message_content", "")
    ]
    emit(
        "    替身抽取器收到重放消息={ok}（机制路径确实被走到）".format(
            ok=len(stub_hits) > 0
        )
    )
    for run in collector.extraction_runs((replay_sent["user_message_id"],)):
        emit(
            "    重放提取 run：source={source} status={status} outcome={outcome}"
            " error={error}".format(
                source=run.get("source"),
                status=run.get("status"),
                outcome=run.get("outcome"),
                error=run.get("last_error"),
            )
        )
    final_items = dump_items("重放提交后：")
    kept = next(item for item in final_items if item.profile_item_id == target.profile_item_id)
    emit(
        "    检查：用户编辑保留={ok}（正文={text!r}）".format(
            ok=kept.text == "我改成每天早上学习两小时。", text=kept.text
        )
    )
    emit(
        "    检查：worker 自动说法未改写用户版本={ok}".format(
            ok=kept.text != "我每天学习两小时，固定在晚上七点"
        )
    )
    stale = client.patch(
        f"/profiles/items/{target.profile_item_id}",
        json={"text": "过期页面的写入。", "version": target.version},
    )
    assert stale.status_code == 409, stale.text
    emit(
        "    检查：过期版本写回被拒={status} {detail}".format(
            status=stale.status_code,
            detail=json.dumps(stale.json().get("detail"), ensure_ascii=False),
        )
    )

    emit("")
    emit("[6b] 确定性复现：重复归并进同一事实 + 不同爱好并存（替身抽取器）")
    running_before = [
        item for item in atomic.list_items(account_id) if "跑步" in item.text
    ]
    before_evidence = (
        len(running_before[0].source_message_ids) if running_before else 0
    )

    def forced_duplicate(payload: dict[str, Any]) -> dict[str, Any]:
        proposal = echo_profile_proposal(payload)
        items = proposal.get("items") or []
        if items:
            items[0]["normalized_value"] = "我一直喜欢跑步"
            items[0]["fact_text"] = "我真的很喜欢跑步"
            swimming = dict(items[0])
            swimming["normalized_value"] = "也游泳"
            swimming["fact_text"] = "我也喜欢游泳"
            items.append(swimming)
        return proposal

    extraction_stub._responder = forced_duplicate
    duplicate = ask("我一直喜欢跑步，也游泳。", "dup-dup")
    emit("  用户：我一直喜欢跑步，也游泳。")
    dump_turn(duplicate)
    running_after = [
        item for item in atomic.list_items(account_id) if "跑步" in item.text
    ]
    emit(
        "    检查：归并进同一条={ok}（跑步条目 {count} 条，依据 {before}→{after} 条）".format(
            ok=len(running_after) == 1
            and running_after[0].evidence_quote is not None
            and len(running_after[0].source_message_ids) == before_evidence + 1,
            count=len(running_after),
            before=before_evidence,
            after=(
                len(running_after[0].source_message_ids) if running_after else 0
            ),
        )
    )
    swim_after = [
        item for item in atomic.list_items(account_id) if "游泳" in item.text
    ]
    emit(
        "    检查：游泳独立保留={ok}（游泳条目 {count} 条）".format(
            ok=len(swim_after) == 1, count=len(swim_after)
        )
    )

    emit("")
    emit("[6c] 确定性复现：并行目标并存不互相替代（替身抽取器）")

    def forced_parallel_goals(payload: dict[str, Any]) -> dict[str, Any]:
        proposal = echo_profile_proposal(payload)
        items = proposal.get("items") or []
        if items:
            items[0]["normalized_value"] = "我准备考研"
            items[0]["fact_text"] = "我准备考研"
            second_goal = dict(items[0])
            second_goal["normalized_value"] = "还打算找工作"
            second_goal["fact_text"] = "我还打算找工作"
            items.append(second_goal)
        return proposal

    extraction_stub._responder = forced_parallel_goals
    parallel = ask("再说一次：我准备考研，还打算找工作。", "goals-stub")
    emit("  用户：再说一次：我准备考研，还打算找工作。")
    dump_turn(parallel)
    parallel_items = dump_items("并行目标（确定性）：")
    emit(
        "    检查：考研与求职并存={ok}".format(
            ok=any("考研" in item.text for item in parallel_items)
            and any("找工作" in item.text for item in parallel_items)
        )
    )

    emit("")
    emit("[7] 页面状态与存储一致（GET /profiles/items 全量视图）")
    page = client.get("/profiles/items")
    assert page.status_code == 200, page.text
    for entry in page.json():
        emit(
            "    {text!r} conflict_state={state} v{version} 来源={origin}".format(
                text=entry["text"],
                state=entry["conflict_state"],
                version=entry["version"],
                origin=entry["write_origin"],
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
