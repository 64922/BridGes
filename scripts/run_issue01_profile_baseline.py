"""工单 01：画像闭环基线与可复现验收旅程证据运行器。

用法（在仓库根目录、``conda agent`` 环境）：

    python scripts/run_issue01_profile_baseline.py \
        --mode both --proxy http://127.0.0.1:7890 \
        --output .scratch/profile-memory-redesign/validation/01-baseline

- ``deterministic``：test 环境 + 确定性替身；抽取器替换为生产接线的
  网关抽取器 + 录制替身，用于复现六类问题机制；响应标记为替身。
- ``real``：development 环境 + 操作系统凭据库中的真实主模型；不注册任何
  替身。凭据不可用时写 ``inconclusive`` 并以退出码 2 结束，绝不伪造结果。
- 每次运行使用仓库 ``.tmp`` 下的隔离数据目录与独立数据库；不接触正式
  数据库，不记录任何凭据正文。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_CREDENTIAL_ENV_KEYS = (
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_TAVILY_API_KEY_FILE",
)


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"


def _register(client: Any, tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    response = client.post(
        "/auth/register",
        json={
            "username": f"issue01_{digest[:12]}",
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


def _prepare_environment(*, mode: str, run_dir: Path, proxy: str) -> None:
    for key in list(os.environ):
        if key.upper() in _CREDENTIAL_ENV_KEYS:
            os.environ.pop(key, None)
    os.environ["BRIDGES_ENVIRONMENT"] = "test" if mode == "deterministic" else "development"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{run_dir / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue01-{uuid.uuid4().hex}"
    if mode == "real" and proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy


def _build_app(mode: str, local_app_data: Path) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    get_settings.cache_clear()
    if mode == "deterministic":
        # test 环境的默认凭据存储是进程内实现：确定性运行绝不读取开发机
        # 的真实操作系统凭据库（否则会注册真实适配器）。
        return create_app()
    from bridges.credentials.store import OsCredentialStore

    store = OsCredentialStore(data_dir=local_app_data, namespace="runtime")
    return create_app(runtime_credential_store=store)


def _run_journeys(
    app: Any,
    client: Any,
    *,
    tag: str,
    journey_ids: list[str],
    deterministic: bool,
) -> tuple[list[dict[str, Any]], Any]:
    from bridges.evaluation.profile_journey_runner import (
        ProfileJourneyCollector,
        install_deterministic_profile_extractor,
        run_baseline_journey,
    )
    from bridges.evaluation.profile_journeys import BASELINE_JOURNEYS_BY_ID

    adapter = install_deterministic_profile_extractor(app) if deterministic else None
    journeys: list[dict[str, Any]] = []
    for journey_id in journey_ids:
        # 每条旅程用独立账户：快照与修订链不与其他旅程互相污染。
        account_id = _register(client, f"{tag}-{journey_id}")
        collector = ProfileJourneyCollector(
            app=app,
            client=client,
            database=app.state.bridges_database,
            account_id=account_id,
        )
        journey = BASELINE_JOURNEYS_BY_ID[journey_id]
        print(f"[journey] {journey_id} ...", flush=True)
        journeys.append(run_baseline_journey(collector, journey, tag=tag))
        print(f"[journey] {journey_id} done", flush=True)
    return journeys, adapter


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lock = payload["run_lock"]
    lines.append(f"# 画像闭环基线证据（{lock['response_mode']}）")
    lines.append("")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 代码提交：{lock['git_commit']}（dirty={lock['git_dirty']}）")
    lines.append(f"- 语料：{lock['corpus_version']}（{lock['corpus_sha256']}）")
    lines.append(f"- 替身模型覆盖：{lock['injected_models_overridden']}")
    model_config = lock.get("model_config") or {}
    lines.append(f"- 主模型：{model_config.get('model_id')}")
    lines.append(f"- 凭据：{lock['credentials']}")
    lines.append("")
    for journey in payload["journeys"]:
        evaluation = journey.get("evaluation") or {}
        lines.append(f"## {journey['journey_id']}（{journey['problem_class']}）")
        lines.append("")
        lines.append(f"- 目标：{journey['target']}")
        lines.append(
            f"- 预期基线：{journey['expected_baseline_status']}——"
            f"{journey['expected_baseline_observation']}"
        )
        lines.append(
            f"- 实测结论：{evaluation.get('observed_status')}"
            f"（与预期一致={evaluation.get('baseline_match')}）"
        )
        for check in evaluation.get("checks") or []:
            mark = "通过" if check["passed"] else "未通过"
            lines.append(f"  - [{mark}] {check['check_id']}：{check['detail']}")
        lines.append(f"- 邻居读取探测：{journey.get('neighbor_reader_probe')}")
        for turn in journey.get("turns", []):
            lines.append("")
            lines.append(f"### 用户：{turn['user_content']}")
            lines.append("")
            lines.append(f"助手（status={turn.get('assistant_status')}）：")
            lines.append("")
            lines.append(turn.get("assistant_content") or "（空回复）")
            for run in turn.get("extraction_runs") or []:
                lines.append(
                    f"> 提取 run：source={run.get('source')} status={run.get('status')}"
                    f" outcome={run.get('outcome')} error={run.get('last_error')}"
                )
        snapshot = journey.get("profile_snapshot") or {}
        lines.append("")
        lines.append(
            f"- 四维记录 {len(snapshot.get('four_dimensions') or [])} 条，"
            f"原子条目 {len(snapshot.get('items') or [])} 条，"
            f"提取运行 {len(snapshot.get('extraction_runs') or [])} 个。"
        )
        lines.append("")
    totals = payload["totals"]
    lines.append("## 汇总（仅实测）")
    lines.append("")
    lines.append(
        f"- 模型调用 {totals['model_calls']} 次；输入 {totals['input_tokens']} / "
        f"输出 {totals['output_tokens']} token。"
    )
    lines.append(f"- 回答耗时样本（ms）：{totals['generation_duration_ms_samples']}")
    lines.append(f"- 提取耗时样本（ms）：{totals['extraction_duration_ms_samples']}")
    lines.append(f"- 备注：{totals['note']}")
    lines.append("")
    return "\n".join(lines)


def _run_mode(mode: str, args: argparse.Namespace, output_dir: Path, stamp: str) -> int:
    from bridges.evaluation.learning_journey_runner import resolve_credential_presence
    from bridges.evaluation.profile_journey_evidence import (
        attach_recording_calls,
        build_run_lock,
        evaluate_journey,
        journey_report_totals,
        write_journey_evidence,
        write_run_lock,
    )
    from bridges.evaluation.profile_journey_runner import (
        RESPONSE_MODE_REAL,
        RESPONSE_MODE_STUB,
    )
    from bridges.evaluation.profile_journeys import BASELINE_JOURNEYS, BASELINE_JOURNEYS_BY_ID

    selected_ids = (
        [item.strip() for item in args.journeys.split(",") if item.strip()]
        if args.journeys
        else [journey.journey_id for journey in BASELINE_JOURNEYS]
    )
    for journey_id in selected_ids:
        if journey_id not in BASELINE_JOURNEYS_BY_ID:
            raise SystemExit(f"未知旅程：{journey_id}")

    response_mode = RESPONSE_MODE_STUB if mode == "deterministic" else RESPONSE_MODE_REAL
    run_dir = REPO_ROOT / ".tmp" / f"issue01-{mode}-{stamp}-{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _prepare_environment(mode=mode, run_dir=run_dir, proxy=args.proxy)
    app = _build_app(mode, args.local_app_data or _local_app_data())
    credentials = resolve_credential_presence(
        app.state.runtime_credential_store, app.state.settings
    )
    payload: dict[str, Any] = {
        "kind": "profile-baseline-evidence",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "journeys": [],
        "totals": journey_report_totals([]),
        "inconclusive": False,
        "reason": None,
    }

    if mode == "real":
        text_adapter = app.state.model_gateway.get_adapter("qwen_text_chat", "1")
        extraction_adapter = app.state.model_gateway.get_adapter(
            "qwen_profile_extraction", "1"
        )
        if text_adapter is None or extraction_adapter is None:
            payload["inconclusive"] = True
            payload["reason"] = "主模型适配器缺失（凭据不可用或未配置）"
            payload["run_lock"] = build_run_lock(
                REPO_ROOT,
                app=app,
                journey_ids=selected_ids,
                response_mode=response_mode,
                credentials=credentials,
                stub_profile_extraction=False,
                stub_chat=False,
            )
            write_run_lock(output_dir, stamp=stamp, payload=payload)
            write_journey_evidence(
                output_dir,
                stamp=stamp,
                payload=payload,
                markdown=_render_markdown(payload),
            )
            print(f"[inconclusive] {payload['reason']}")
            return 2

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        tag = f"{stamp[-6:]}{uuid.uuid4().hex[:4]}"
        journeys, adapter = _run_journeys(
            app,
            client,
            tag=tag,
            journey_ids=selected_ids,
            deterministic=mode == "deterministic",
        )

    attach_recording_calls(journeys, adapter)
    for journey in journeys:
        journey["evaluation"] = evaluate_journey(
            BASELINE_JOURNEYS_BY_ID[journey["journey_id"]], journey
        )
    payload["journeys"] = journeys
    payload["totals"] = journey_report_totals(journeys)
    payload["run_lock"] = build_run_lock(
        REPO_ROOT,
        app=app,
        journey_ids=selected_ids,
        response_mode=response_mode,
        credentials=credentials,
        stub_profile_extraction=adapter is not None,
        stub_chat=mode == "deterministic",
    )
    write_run_lock(output_dir, stamp=stamp, payload=payload)
    json_path, md_path = write_journey_evidence(
        output_dir, stamp=stamp, payload=payload, markdown=_render_markdown(payload)
    )
    app.state.bridges_database.close()
    app.state.state_store.close()
    print(f"[done] evidence written to {json_path} and {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("deterministic", "real", "both"),
        default="both",
        help="运行模式；real 缺凭据时如实 inconclusive",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".scratch" / "profile-memory-redesign" / "validation" / "01-baseline",
    )
    parser.add_argument("--proxy", default="", help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
    parser.add_argument("--journeys", default="", help="逗号分隔的旅程 ID 子集；默认全部")
    parser.add_argument(
        "--local-app-data",
        type=Path,
        default=None,
        help="凭据库数据目录（默认 %%LOCALAPPDATA%%/BridGes/data）",
    )
    args = parser.parse_args(argv)

    output_dir: Path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    modes = ("deterministic", "real") if args.mode == "both" else (args.mode,)
    exit_code = 0
    for mode in modes:
        code = _run_mode(mode, args, output_dir, stamp)
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
