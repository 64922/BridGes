"""工单 13：真实主模型学习旅程与发布验收证据运行器。

用法（在仓库根目录、``conda agent`` 环境）：

    python scripts/run_issue13_learning_journeys.py \
        --proxy http://127.0.0.1:7890 \
        --output .scratch/learning-mode-redesign/validation/13-release-acceptance

- 凭据经操作系统凭据库（``runtime`` 命名空间）解析；不存在时写
  ``inconclusive`` 并以退出码 2 结束，绝不伪造结果。
- 每次运行使用仓库 ``.tmp`` 下的隔离数据目录与独立数据库；不接触桌面
  正式数据库，不记录任何凭据正文。
- 固定情境与量表见 ``bridges.evaluation.learning_journeys``；运行锁与
  证据采集见 ``bridges.evaluation.learning_journey_runner``。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

if TYPE_CHECKING:
    from bridges.evaluation.learning_journey_runner import JourneyEvidenceCollector


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"


def _register(client: Any, tag: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue13_{tag}",
            "qq_email": f"136{sum(bytearray(tag.encode('utf-8'))) % 10 ** 8:08d}@qq.com",
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


def _run_concept_scenario(
    collector: JourneyEvidenceCollector, scenario_id: str, tag: str
) -> dict[str, Any]:
    from bridges.evaluation.learning_journeys import SCENARIOS_BY_ID

    scenario = SCENARIOS_BY_ID[scenario_id]
    started = time.monotonic()
    turns: list[dict[str, Any]] = []
    conversation_id = ""
    for index, learner_turn in enumerate(scenario.learner_turns):
        key = f"issue13-{tag}-{scenario_id}-{learner_turn.turn_id}"
        if index == 0:
            conversation_id = collector.start_study(learner_turn.content, key)
            runs = collector.latest_run(conversation_id)
            sent = {
                "run_id": runs.get("run_id") or "",
                "assistant_message_id": runs.get("assistant_message_id") or "",
            }
        else:
            sent = collector.send_turn(conversation_id, learner_turn.content, key)
        idle = collector.drive_until_idle(conversation_id)
        if not sent.get("assistant_message_id"):
            detail = collector.conversation_detail(conversation_id)
            assistant = next(
                message
                for message in detail["messages"]
                if message["role"] == "assistant"
            )
            sent["assistant_message_id"] = assistant["message_id"]
        turn = collector.capture_turn(
            conversation_id,
            sent,
            user_content=learner_turn.content,
            expectation=learner_turn.expectation,
        )
        turn["turn_id"] = learner_turn.turn_id
        turn["idle"] = idle
        turns.append(turn)
    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "domain": scenario.domain,
        "conversation_id": conversation_id,
        "turns": turns,
        "snapshot": collector.conversation_snapshot(conversation_id),
        "diagnosis_tasks": collector.diagnosis_tasks(conversation_id),
        "records": collector.records(),
        "adoptions": collector.adoptions(conversation_id),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    if scenario_id == "probability-mutually-exclusive":
        result["record_controls"] = collector.exercise_record_controls(conversation_id)
    return result


def _run_goal_scenario(collector: JourneyEvidenceCollector, tag: str) -> dict[str, Any]:
    from bridges.evaluation.learning_journeys import SCENARIOS_BY_ID

    scenario = SCENARIOS_BY_ID["goal-resume-linear-algebra"]
    started = time.monotonic()
    conversation_a = collector.start_study(
        scenario.learner_turns[0].content, f"issue13-{tag}-goal-a1"
    )
    collector.drive_until_idle(conversation_a)
    detail_a = collector.conversation_detail(conversation_a)
    assistant_a1 = detail_a["messages"][-1]
    turns = [
        collector.capture_turn(
            conversation_a,
            {"assistant_message_id": assistant_a1["message_id"]},
            user_content=scenario.learner_turns[0].content,
            expectation=scenario.learner_turns[0].expectation,
        )
        | {"turn_id": scenario.learner_turns[0].turn_id}
    ]
    sent = collector.send_turn(
        conversation_a, scenario.learner_turns[1].content, f"issue13-{tag}-goal-a2"
    )
    collector.drive_until_idle(conversation_a)
    turns.append(
        collector.capture_turn(
            conversation_a,
            sent,
            user_content=scenario.learner_turns[1].content,
            expectation=scenario.learner_turns[1].expectation,
        )
        | {"turn_id": scenario.learner_turns[1].turn_id}
    )
    snapshot_a = collector.conversation_snapshot(conversation_a)

    conversation_b = collector.start_study(
        "继续学线性代数，接着上次的进度。", f"issue13-{tag}-goal-b1"
    )
    collector.drive_until_idle(conversation_b)
    detail_b = collector.conversation_detail(conversation_b)
    assistant_b1 = detail_b["messages"][-1]
    turns_b = [
        collector.capture_turn(
            conversation_b,
            {"assistant_message_id": assistant_b1["message_id"]},
            user_content="继续学线性代数，接着上次的进度。",
            expectation="恢复上次目标并承接进展；不重新建立无关目标。",
        )
        | {"turn_id": "g3-resume"}
    ]
    return {
        "scenario_id": "goal-resume-linear-algebra",
        "domain": "goal",
        "conversation_id": conversation_a,
        "resume_conversation_id": conversation_b,
        "turns": turns + turns_b,
        "snapshot": snapshot_a,
        "resume_snapshot": collector.conversation_snapshot(conversation_b),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _run_verification_scenario(
    collector: JourneyEvidenceCollector, tag: str
) -> dict[str, Any]:
    from bridges.evaluation.learning_journeys import SCENARIOS_BY_ID

    scenario = SCENARIOS_BY_ID["public-verification-fresh-fact"]
    started = time.monotonic()
    turn = scenario.learner_turns[0]
    conversation_id = collector.start_study(turn.content, f"issue13-{tag}-verify")
    runs = collector.latest_run(conversation_id)
    collector.drive_until_idle(conversation_id)
    captured = collector.capture_turn(
        conversation_id,
        {
            "run_id": runs.get("run_id") or "",
            "assistant_message_id": runs.get("assistant_message_id") or "",
        },
        user_content=turn.content,
        expectation=turn.expectation,
    )
    captured["turn_id"] = turn.turn_id
    return {
        "scenario_id": scenario.scenario_id,
        "domain": scenario.domain,
        "conversation_id": conversation_id,
        "turns": [captured],
        "snapshot": collector.conversation_snapshot(conversation_id),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _totals(journeys: list[dict[str, Any]]) -> dict[str, Any]:
    calls = 0
    input_tokens = 0
    output_tokens = 0
    external_calls = 0
    front_ms: list[int] = []
    diagnosis_ms: list[int] = []
    for journey in journeys:
        for turn in journey.get("turns", []):
            calls += len(turn.get("model_calls") or [])
            budget = turn.get("budget") or {}
            input_tokens += int(budget.get("input_tokens_used") or 0)
            output_tokens += int(budget.get("output_tokens_used") or 0)
            external_calls += int(budget.get("external_calls_used") or 0)
            front = (turn.get("timing") or {}).get("front_stage_completion_ms")
            if isinstance(front, int):
                front_ms.append(front)
        for task in journey.get("diagnosis_tasks", []):
            if isinstance(task.get("latency_ms"), int):
                diagnosis_ms.append(task["latency_ms"])
    return {
        "model_calls": calls,
        "external_calls": external_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "front_stage_completion_ms": front_ms,
        "diagnosis_latency_ms": diagnosis_ms,
        "note": "只报告本次实测；诊断耗时不计入前台阻塞。",
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    from bridges.evaluation.learning_journeys import SCENARIOS_BY_ID

    lines: list[str] = []
    lock = payload["run_lock"]
    lines.append("# 真实主模型学习旅程证据（工单 13）")
    lines.append("")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 代码提交：{lock['git_commit']}（dirty={lock['git_dirty']}）")
    lines.append(f"- 主模型：{lock['model_config']['model_id']}")
    lines.append(f"- 语料摘要：{lock['corpus_sha256']}")
    lines.append(f"- 凭据：{lock['credentials']}")
    lines.append("")
    for journey in payload["journeys"]:
        scenario = SCENARIOS_BY_ID.get(journey["scenario_id"])
        lines.append(f"## {journey['scenario_id']}")
        if scenario is not None:
            lines.append(f"- 目标误解：{scenario.misconception}")
        lines.append(f"- 会话：{journey['conversation_id']}（{journey['elapsed_ms']} ms）")
        lines.append("")
        for turn in journey.get("turns", []):
            lines.append(f"### 学员（{turn.get('turn_id')}）")
            lines.append("")
            lines.append(turn["user_content"])
            lines.append("")
            timing = turn.get("timing") or {}
            lines.append(
                f"**导师**（status={turn.get('assistant_status')}，"
                f"首段 {timing.get('first_delta_ms')} ms，"
                f"完成 {timing.get('front_stage_completion_ms')} ms，"
                f"调用 {(turn.get('budget') or {}).get('model_calls_used')}，"
                f"输入/输出 token "
                f"{(turn.get('budget') or {}).get('input_tokens_used')}/"
                f"{(turn.get('budget') or {}).get('output_tokens_used')}）"
            )
            lines.append("")
            lines.append(turn.get("assistant_content") or "（空回复）")
            lines.append("")
            for verification in turn.get("source_verifications") or []:
                lines.append(
                    f"> 公开查证：purpose={verification.get('purpose')} "
                    f"status={verification.get('status')} "
                    f"sources={verification.get('source_count')}"
                )
                lines.append("")
        for task in journey.get("diagnosis_tasks", []):
            lines.append(
                f"- 诊断任务 {task['task_id'][:8]}：status={task['status']} "
                f"attempt={task['attempt']} latency={task.get('latency_ms')} ms "
                f"question={task.get('question', '')[:60]}"
            )
        for adoption in journey.get("adoptions", []):
            entries = adoption.get("entries") or []
            summary = "；".join(
                f"{entry.get('title', '')}:{entry.get('direction', '')}"
                for entry in entries
            )
            lines.append(
                f"- 采用 {adoption['adoption_id'][:8]}：status={adoption['status']} "
                f"message={str(adoption['assistant_message_id'])[:8]} {summary}"
            )
        lines.append("")
    totals = payload["totals"]
    lines.append("## 汇总（仅实测）")
    lines.append("")
    lines.append(
        f"- 主模型调用 {totals['model_calls']} 次，外部调用 {totals['external_calls']} 次；"
        f"输入 {totals['input_tokens']} / 输出 {totals['output_tokens']} token。"
    )
    lines.append(
        f"- 前台完成耗时样本（ms）：{totals['front_stage_completion_ms']}"
    )
    lines.append(
        f"- 后台诊断耗时样本（ms）：{totals['diagnosis_latency_ms']}"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / ".scratch"
        / "learning-mode-redesign"
        / "validation"
        / "13-release-acceptance",
    )
    parser.add_argument("--proxy", default="", help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
    parser.add_argument(
        "--scenarios",
        default="",
        help="逗号分隔的情境 ID 子集；默认全部",
    )
    parser.add_argument(
        "--local-app-data",
        type=Path,
        default=None,
        help="凭据库数据目录（默认 %%LOCALAPPDATA%%/BridGes/data）",
    )
    args = parser.parse_args(argv)

    from bridges.evaluation.learning_journey_runner import (
        JourneyEvidenceCollector,
        build_run_lock,
        resolve_credential_presence,
        write_json,
    )
    from bridges.evaluation.learning_journeys import (
        RUBRIC_DIMENSIONS,
        SCENARIOS,
        SCENARIOS_BY_ID,
        independent_ground_truth,
    )

    selected_ids = (
        [item.strip() for item in args.scenarios.split(",") if item.strip()]
        if args.scenarios
        else [scenario.scenario_id for scenario in SCENARIOS]
    )
    for scenario_id in selected_ids:
        if scenario_id not in SCENARIOS_BY_ID:
            parser.error(f"未知情境：{scenario_id}")

    output_dir: Path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPO_ROOT / ".tmp" / f"issue13-journeys-{stamp}-{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)

    os.environ["BRIDGES_ENVIRONMENT"] = "test"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{run_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue13-{uuid.uuid4().hex}"
    for key in list(os.environ):
        if key.startswith("BRIDGES_QWEN_API_KEY"):
            os.environ.pop(key, None)
    if args.proxy:
        os.environ["HTTP_PROXY"] = args.proxy
        os.environ["HTTPS_PROXY"] = args.proxy
        os.environ["http_proxy"] = args.proxy
        os.environ["https_proxy"] = args.proxy

    from bridges.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from bridges.api.main import create_app
    from bridges.credentials.store import OsCredentialStore

    store = OsCredentialStore(
        data_dir=args.local_app_data or _local_app_data(), namespace="runtime"
    )
    app = create_app(runtime_credential_store=store)
    settings = app.state.settings
    credentials = resolve_credential_presence(store, settings)

    payload: dict[str, Any] = {
        "kind": "study-journey-evidence",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "journeys": [],
        "totals": {},
        "inconclusive": False,
        "reason": None,
    }

    text_adapter = app.state.model_gateway.get_adapter("qwen_text_chat", "1")
    structured_adapter = app.state.model_gateway.get_adapter(
        "qwen_structured_output", "1"
    )
    if text_adapter is None or structured_adapter is None:
        payload["inconclusive"] = True
        payload["reason"] = "主模型适配器缺失（凭据不可用或未配置）"
        payload["run_lock"] = build_run_lock(
            REPO_ROOT,
            app=app,
            scenario_ids=selected_ids,
            credentials=credentials,
            injected_models=True,
        )
        write_json(output_dir / f"journeys-{stamp}.json", payload)
        write_json(output_dir / f"run-lock-{stamp}.json", payload["run_lock"])
        print(f"[inconclusive] {payload['reason']}")
        return 2

    with TestClient(app) as client:
        tag = f"{stamp[-6:]}{uuid.uuid4().hex[:4]}"
        account_id = _register(client, tag)
        collector = JourneyEvidenceCollector(
            app=app,
            client=client,
            database=app.state.bridges_database,
            account_id=account_id,
        )
        journeys: list[dict[str, Any]] = []
        for scenario_id in selected_ids:
            scenario = SCENARIOS_BY_ID[scenario_id]
            print(f"[journey] {scenario_id} ...", flush=True)
            if scenario.domain in {"probability", "limit", "programming"}:
                journeys.append(_run_concept_scenario(collector, scenario_id, tag))
            elif scenario.domain == "goal":
                journeys.append(_run_goal_scenario(collector, tag))
            elif scenario.domain == "verification":
                journeys.append(_run_verification_scenario(collector, tag))
            print(f"[journey] {scenario_id} done", flush=True)

    payload["journeys"] = journeys
    payload["totals"] = _totals(journeys)
    payload["run_lock"] = build_run_lock(
        REPO_ROOT,
        app=app,
        scenario_ids=selected_ids,
        credentials=credentials,
        injected_models=False,
    )
    write_json(output_dir / f"journeys-{stamp}.json", payload)
    write_json(output_dir / f"run-lock-{stamp}.json", payload["run_lock"])
    write_json(output_dir / f"independent-checks-{stamp}.json", independent_ground_truth())
    (output_dir / f"journeys-{stamp}.md").write_text(
        _render_markdown(payload), encoding="utf-8"
    )
    rubric_template = {
        "corpus_version": payload["run_lock"]["corpus_version"],
        "generated_at": payload["generated_at"],
        "dimensions": [
            {
                "dimension_id": dimension.dimension_id,
                "name": dimension.name,
                "scale": {
                    "0": dimension.zero,
                    "1": dimension.one,
                    "2": dimension.two,
                },
                "ratings": [],
            }
            for dimension in RUBRIC_DIMENSIONS
        ],
        "usage": "评分须引用 journeys-*.json 与 transcripts 中的实际消息，不得凭印象。",
    }
    write_json(output_dir / f"rubric-template-{stamp}.json", rubric_template)

    app.state.bridges_database.close()
    app.state.state_store.close()
    print(f"[done] evidence written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
