"""工单 18：模拟故障注入（保存/生成/提取失败与恢复），与真实模型结果分开报告。

用法（在仓库根目录、``conda agent`` 环境）：

    python scripts/run_issue18_fault_injection.py \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

- 只运行 ``test`` 环境 + 确定性替身；通过生产接缝注入故障（聊天适配器、
  提取适配器、原子提交入口），验证失败如实可见、重试后可恢复、不产生
  重复写入。故障注入结果绝不冒充真实模型结论。
- 每次运行使用仓库 ``.tmp`` 下的隔离数据目录与独立数据库。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _flaky_chat_adapter(env: Any, failures: int = 1) -> Any:
    from bridges.ai.adapters import StreamChunk

    class _FlakyChatAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def stream_call(self, capability: Any, run_context: Any, payload: Any) -> Any:
            self.calls += 1
            if self.calls <= failures:
                raise RuntimeError("注入故障：聊天生成失败")
            yield StreamChunk(kind="delta", delta="故障恢复后的脚本化回答。")
            yield StreamChunk(kind="done", usage={"prompt_tokens": 0, "completion_tokens": 0})

    return _FlakyChatAdapter()


def _flaky_extraction_adapter(failures: int = 1) -> Any:
    from bridges.ai.adapters import AdapterResult
    from bridges.evaluation.profile_journey_runner import echo_profile_proposal

    class _FlakyExtractionAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, capability: Any, run_context: Any, payload: dict[str, Any]) -> Any:
            self.calls += 1
            if self.calls <= failures:
                raise RuntimeError("注入故障：提取模型失败")
            return AdapterResult(
                actual_model_id=capability.model_id,
                output=echo_profile_proposal(payload),
                usage={"prompt_tokens": 0, "completion_tokens": 0},
            )

    return _FlakyExtractionAdapter()


def _fault_generation(env: Any, session: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    conversation_id, first = session.start_chat("我最近在学吉他。")
    failed_runs = [
        dict(row)
        for row in session.database.connection.execute(
            "SELECT status, error_code FROM generation_runs"
            " WHERE account_id = ? AND conversation_id = ? AND status = 'failed'",
            (session.account_id, conversation_id),
        ).fetchall()
    ]
    checks.append(
        {
            "check_id": "fault:generation-failure-visible",
            "passed": bool(failed_runs) and int(first.get("retry_attempts") or 0) >= 1,
            "detail": f"失败运行={failed_runs!r} "
            f"自动重试={first.get('retry_attempts')!r} "
            f"最终状态={first.get('assistant_status')!r}",
        }
    )
    recovery = session.chat(conversation_id, "刚才的回答失败了，重试一下。")
    checks.append(
        {
            "check_id": "fault:generation-retry-succeeds",
            "passed": recovery.get("assistant_status") == "done"
            and bool(recovery.get("assistant_content")),
            "detail": f"status={recovery.get('assistant_status')!r} "
            f"content={str(recovery.get('assistant_content'))[:60]!r}",
        }
    )
    return checks


def _fault_extraction(env: Any, session: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    session.start_chat("我每周三去实验室。")
    runs = session.collector.extraction_runs()
    checks.append(
        {
            "check_id": "fault:extraction-failure-retried",
            "passed": any(int(run.get("attempts") or 0) >= 2 for run in runs),
            "detail": f"attempts={[run.get('attempts') for run in runs]!r} "
            f"status={[run.get('status') for run in runs]!r}",
        }
    )
    texts = session.active_texts()
    checks.append(
        {
            "check_id": "fault:extraction-recovered-once",
            "passed": len([t for t in texts if "实验室" in t]) == 1,
            "detail": f"活动条目={texts!r}",
        }
    )
    return checks


def _fault_save(env: Any, session: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    session.start_chat("我喜欢下围棋。")
    texts = session.active_texts()
    checks.append(
        {
            "check_id": "fault:save-failure-recovered-once",
            "passed": len([t for t in texts if "围棋" in t]) == 1,
            "detail": f"活动条目={texts!r}",
        }
    )
    runs = session.collector.extraction_runs()
    checks.append(
        {
            "check_id": "fault:save-failure-recorded",
            "passed": any(int(run.get("attempts") or 0) >= 2 for run in runs),
            "detail": f"attempts={[run.get('attempts') for run in runs]!r} "
            f"last_error={[run.get('last_error') for run in runs]!r}",
        }
    )
    return checks


def _render(payload: dict[str, Any]) -> str:
    lines = [f"# 画像闭环模拟故障报告（{payload['generated_at']}）", ""]
    lines.append("- 环境：test + 确定性替身；故障经生产接缝注入，结果不冒充真实模型。")
    lines.append(f"- 代码提交：{payload['git_commit']}")
    for scenario in payload["scenarios"]:
        lines.append("")
        lines.append(f"## {scenario['scenario_id']}")
        lines.append("")
        lines.append(f"- 注入：{scenario['injection']}")
        for check in scenario["checks"]:
            mark = "通过" if check["passed"] else "未通过"
            lines.append(f"  - [{mark}] {check['check_id']}：{check['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / ".scratch"
        / "profile-memory-redesign"
        / "validation"
        / "18-real-model-loop-evaluation"
        / "evidence",
    )
    parser.add_argument("--strict", action="store_true", help="有未通过判定时以退出码 4 结束")
    args = parser.parse_args(argv)
    output_dir: Path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    from bridges.evaluation.profile_loop_eval.environment import LoopEnvironment
    from bridges.evaluation.profile_loop_eval.session import AccountSession
    from bridges.evaluation.profile_loop_eval.types import MODE_SCRIPTED

    run_dir = REPO_ROOT / ".tmp" / f"issue18-fault-{stamp}-{uuid.uuid4().hex[:6]}"
    env = LoopEnvironment(repo_root=REPO_ROOT, run_dir=run_dir, mode=MODE_SCRIPTED, proxy="")
    env.start()

    scenarios: list[dict[str, Any]] = []

    # 1. 生成失败与用户级重试。
    env.app.state.model_gateway.register_adapter("qwen_text_chat", "1", _flaky_chat_adapter(env))
    session = AccountSession(env, env.register_account("18-fault-chat"))
    scenarios.append(
        {
            "scenario_id": "fault-chat-generation",
            "injection": "聊天流式适配器首次调用失败，重试的新回合成功。",
            "checks": _fault_generation(env, session),
        }
    )

    # 2. 提取失败与后台重试。
    env.app.state.model_gateway.register_adapter(
        "qwen_profile_extraction", "1", _flaky_extraction_adapter()
    )
    session = AccountSession(env, env.register_account("18-fault-extract"))
    scenarios.append(
        {
            "scenario_id": "fault-extraction-retry",
            "injection": "提取适配器首次调用失败，重试尝试成功且只写入一次。",
            "checks": _fault_extraction(env, session),
        }
    )

    # 3. 保存失败与重试恢复。
    atomic = env.app.state.atomic_profile_service
    original_commit = atomic.commit_fact
    state = {"raised": False}

    def flaky_commit(*call_args: Any, **call_kwargs: Any) -> Any:
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("注入故障：保存失败")
        return original_commit(*call_args, **call_kwargs)

    atomic.commit_fact = flaky_commit  # type: ignore[method-assign]
    session = AccountSession(env, env.register_account("18-fault-save"))
    scenarios.append(
        {
            "scenario_id": "fault-save-retry",
            "injection": "原子提交入口首次调用失败，重试后恢复且只写入一次。",
            "checks": _fault_save(env, session),
        }
    )
    atomic.commit_fact = original_commit  # type: ignore[method-assign]

    payload = {
        "kind": "profile-loop-fault-injection",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "response_mode": "stub",
        "run_dir": str(run_dir),
        "scenarios": scenarios,
    }
    json_path = output_dir / f"fault-injection-{stamp}.json"
    md_path = output_dir / f"fault-injection-{stamp}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render(payload), encoding="utf-8")
    env.close()

    failed = [
        check["check_id"]
        for scenario in scenarios
        for check in scenario["checks"]
        if not check["passed"]
    ]
    print(f"[done] 故障场景 {len(scenarios)} 个；未通过判定 {len(failed)} 条：{failed}")
    print(f"[done] evidence written to {json_path} and {md_path}")
    if args.strict and failed:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
