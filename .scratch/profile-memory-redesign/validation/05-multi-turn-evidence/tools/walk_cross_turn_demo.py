"""工单 05 演示走查：真实模型下的跨轮省略、多片段依据与来源竞态。

运行（仓库根、conda agent 环境、PYTHONPATH 指向仓库 src）：

    $env:PYTHONPATH="<repo>/src"
    python .scratch/profile-memory-redesign/validation/05-multi-turn-evidence/tools/walk_cross_turn_demo.py \
        --out .scratch/profile-memory-redesign/validation/05-multi-turn-evidence/evidence/demo-walkthrough-<stamp>.txt

场景（该语料与测试替身均留出措辞，只用真实模型验证泛化）：

1. 「我把『软件工程』选成了第一志愿的专业。」——无主语/自述邻句；
2. 「这个方向我打算读到硕士。」——跨轮省略回指第 1 条；条目写入后
   详情展示当前消息原话 + 邻句片段，两段都能核对；
3. 来源竞态：改掉第 1 条正文后再读依据，片段不再展示（来源不可核对）；
4. 孤立「同意」：缺少明确对象时不保存计划/目标。

输出是可直接入档的证据文本：每轮助手回复、提取运行结果、原子条目、
依据投影的逐来源 quote/quote_status。绝不记录凭据。
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

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

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
    os.environ["BRIDGES_SECRET_KEY"] = f"issue05-demo-{uuid.uuid4().hex}"


def _register(client, tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue05demo_{digest[:10]}",
            "qq_email": f"136{int(digest[:8], 16) % 10**8:08d}@qq.com",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--app-data", default="")
    args = parser.parse_args()

    lines: list[str] = []

    def emit(message: str) -> None:
        print(message)
        lines.append(message)

    run_dir = REPO_ROOT / ".tmp" / f"issue05-demo-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _prepare_environment(run_dir)

    from fastapi.testclient import TestClient

    from bridges.api.main import create_app
    from bridges.config import get_settings
    from bridges.credentials.store import OsCredentialStore
    from bridges.evaluation.profile_journey_runner import ProfileJourneyCollector

    get_settings.cache_clear()
    app_data = Path(args.app_data) if args.app_data else _local_app_data()
    store = OsCredentialStore(data_dir=app_data, namespace="runtime")
    app = create_app(runtime_credential_store=store)

    from bridges.evaluation.profile_journey_runner import _NEIGHBOR_HINT_MARKER

    _ref_pattern = re.compile(r'evidence_ref 固定填写 "([^"]+)"')

    class _RecordingAdapter:
        """只录制，不改变真实适配器行为。"""

        def __init__(self, inner) -> None:
            self._inner = inner
            self.calls: list[dict] = []

        def call(self, capability, run_context, payload):
            result = self._inner.call(capability, run_context, payload)
            messages = payload.get("messages") or []
            ref = ""
            for message in messages:
                match = _ref_pattern.search(str(message.get("content") or ""))
                if match is not None:
                    ref = match.group(1)
                    break
            hints = [
                str(m.get("content") or "")
                for m in messages
                if _NEIGHBOR_HINT_MARKER in str(m.get("content") or "")
            ]
            self.calls.append(
                {
                    "ref": ref,
                    "neighbor_hint_present": bool(hints),
                    "neighbor_hint_lines": hints[0].splitlines() if hints else [],
                    "output": result.output,
                    "usage": getattr(result, "usage", None),
                }
            )
            return result

    gateway = app.state.model_gateway
    recorder = _RecordingAdapter(gateway.get_adapter("qwen_profile_extraction", "1"))
    gateway.register_adapter("qwen_profile_extraction", "1", recorder)

    client = TestClient(app)
    client.__enter__()
    account_id = _register(client, f"issue05-demo-{uuid.uuid4().hex[:6]}")
    collector = ProfileJourneyCollector(
        app=app,
        client=client,
        database=app.state.bridges_database,
        account_id=account_id,
    )
    atomic = app.state.atomic_profile_service

    def dump_runs(message_ids: tuple[str, ...]) -> None:
        for run in collector.extraction_runs(message_ids):
            emit(
                "    提取 run：source={source} status={status} outcome={outcome}"
                " error={error}".format(
                    source=run["source"],
                    status=run["status"],
                    outcome=run["outcome"],
                    error=run["last_error"],
                )
            )

    def dump_model_call(ref: str) -> None:
        calls = [call for call in recorder.calls if call["ref"] == ref]
        if not calls:
            emit("    模型调用：未录制到（未进入语义队列）")
            return
        call = calls[-1]
        emit(
            "    模型调用：neighbor_hint_present={hint} output={output}".format(
                hint=call["neighbor_hint_present"],
                output=json.dumps(call["output"], ensure_ascii=False),
            )
        )

    def dump_items() -> list[dict]:
        items = collector.profile_snapshot()["items"]
        for item in items:
            emit(f"    原子条目：{item['text']!r} status={item['status']}")
        return items

    def dump_evidence(item_id: str) -> None:
        projection = atomic.item_evidence(account_id, item_id)
        quote = projection.evidence_quote
        emit(
            "    依据投影：原话={quote!r} quote_status={status}".format(
                quote=quote, status=projection.evidence_quote_status.value
            )
        )
        for source in projection.sources:
            emit(
                "      来源 message_id={mid} status={status} quote={quote!r}"
                " quote_status={qstatus}".format(
                    mid=source.message_id,
                    status=source.status.value,
                    quote=source.quote,
                    qstatus=source.quote_status.value,
                )
            )

    emit("[0] 真实模型（development 装配，不注册任何替身；凭据来自操作系统凭据库）")

    emit("")
    emit("[1] 用户：我把软件工程填成了第一志愿的专业。")
    conversation_id = collector.start_conversation(
        "我把软件工程填成了第一志愿的专业。",
        f"issue05-demo-{uuid.uuid4().hex[:8]}-0",
    )
    idle = collector.drive_until_idle(conversation_id)
    emit(f"    执行器空闲：{idle}")
    detail = collector.conversation_detail(conversation_id)
    first_user = next(m for m in detail["messages"] if m["role"] == "user")
    first_assistant = next(m for m in detail["messages"] if m["role"] == "assistant")
    emit(f"    助手（status={first_assistant['status']}）：{first_assistant['content'][:120]!r}")
    dump_model_call(first_user["message_id"])
    dump_runs((first_user["message_id"],))
    items_after_first = dump_items()
    for item in items_after_first:
        dump_evidence(item["profile_item_id"])

    emit("")
    emit("[2] 用户：这个专业我打算一直读到硕士。（跨轮省略，回指第 1 条）")
    sent = collector.send_turn(
        conversation_id,
        "这个专业我打算一直读到硕士。",
        f"issue05-demo-{uuid.uuid4().hex[:8]}-1",
    )
    idle = collector.drive_until_idle(conversation_id)
    emit(f"    执行器空闲：{idle}")
    second_turn = collector.capture_turn(
        conversation_id,
        sent,
        user_content="这个专业我打算一直读到硕士。",
        expectation="省略回指：计划写入且多片段依据可核对",
    )
    emit(f"    助手：{second_turn['assistant_content'][:120]!r}")
    dump_model_call(sent["user_message_id"])
    dump_runs((sent["user_message_id"],))
    items_after_second = dump_items()
    for item in items_after_second:
        dump_evidence(item["profile_item_id"])

    emit("")
    emit("[3] 来源竞态：直接改掉第 1 条用户消息正文后再读依据")
    database = app.state.bridges_database
    database.connection.execute(
        "UPDATE messages SET content = ? WHERE message_id = ?",
        ("（来源内容已被改写。）", first_user["message_id"]),
    )
    database.connection.commit()
    for item in items_after_second:
        dump_evidence(item["profile_item_id"])

    emit("")
    emit("[4] 孤立「同意」：助手建议后用户只回「同意」")
    sent = collector.send_turn(
        conversation_id,
        "同意",
        f"issue05-demo-{uuid.uuid4().hex[:8]}-2",
    )
    idle = collector.drive_until_idle(conversation_id)
    emit(f"    执行器空闲：{idle}")
    third_turn = collector.capture_turn(
        conversation_id,
        sent,
        user_content="同意",
        expectation="缺少明确对象：不保存计划/目标",
    )
    emit(f"    助手：{third_turn['assistant_content'][:160]!r}")
    dump_model_call(sent["user_message_id"])
    dump_runs((sent["user_message_id"],))
    final_items = dump_items()
    before_ids = {item["profile_item_id"] for item in items_after_second}
    new_items = [item for item in final_items if item["profile_item_id"] not in before_ids]
    emit(f"    新增条目数：{len(new_items)}（期望：0 或仅有当前消息可直接核验的自述）")

    client.__exit__(None, None, None)
    emit("")
    emit(f"运行目录：{run_dir}")

    if args.out:
        target = Path(args.out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload_path = target.with_name(target.stem + "-model-outputs.json")
        payload_path.write_text(
            json.dumps(recorder.calls, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[done] evidence written to {target} and {payload_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
