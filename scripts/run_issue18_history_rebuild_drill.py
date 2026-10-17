"""工单 18：真实模型历史重整演练（来源删除 × 重整 × 对账）。

用法（在仓库根目录、``conda agent`` 环境）：

    python scripts/run_issue18_history_rebuild_drill.py \
        --proxy http://127.0.0.1:7890 \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

- 真实模型在线写入若干事实后删除一条来源消息，随后用**真实网关抽取器**
  启动历史重整、驱动到收敛并对账；验证来源已删除的事实不会借重整复活、
  重整使用真实模型来源、对账通过、其他账户隔离。
- 只使用仓库 ``.tmp``/输出目录下的隔离数据库；不接触正式数据库。
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


def _gateway_factory(app: Any) -> Any:
    from bridges.profiles.automatic import GatewayAutomaticProfileExtractor

    def factory(database: Any, neighbor: Any, memory: Any) -> Any:
        del database
        return GatewayAutomaticProfileExtractor(
            app.state.model_gateway,
            model_config_provider=getattr(app.state, "run_model_config_provider", None),
            neighbor_reader=neighbor,
            memory_reader=memory,
        )

    return factory


def _authority(database_path: Path) -> Any:
    from pydantic import SecretStr

    from bridges.config import Settings
    from bridges.profiles.replay import resolve_authoritative_database

    settings = Settings(
        environment="test", database_url=SecretStr(f"sqlite:///{database_path.as_posix()}")
    )
    return resolve_authoritative_database(settings=settings)


def _items_citing(database: Any, account_id: str, message_id: str) -> set[str]:
    """返回来源引用包含指定消息的条目标识集合。"""

    return {
        str(row["profile_item_id"])
        for row in database.connection.execute(
            "SELECT profile_item_id, source_message_ids_json FROM profile_items"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        if message_id in str(row["source_message_ids_json"] or "")
    }


def _check(checks: list[dict[str, Any]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({"check_id": check_id, "passed": bool(ok), "detail": detail})


def _render(payload: dict[str, Any]) -> str:
    lines = [f"# 真实模型历史重整演练（{payload['generated_at']}）", ""]
    lines.append(f"- 代码提交：{payload['git_commit']}")
    lines.append(f"- 模型：{payload['model_id']}")
    lines.append(f"- 运行目录：{payload['run_dir']}")
    lines.append(f"- 重整工作目录：{payload['work_dir']}")
    for check in payload["checks"]:
        mark = "通过" if check["passed"] else "未通过"
        lines.append(f"  - [{mark}] {check['check_id']}：{check['detail']}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", default="", help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
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

    run_dir = REPO_ROOT / ".tmp" / f"issue18-rebuild-{stamp}-{uuid.uuid4().hex[:6]}"
    env = LoopEnvironment(repo_root=REPO_ROOT, run_dir=run_dir, mode="real", proxy=args.proxy)
    env.start()
    gateway = env.app.state.model_gateway
    if gateway.get_adapter("qwen_profile_extraction", "1") is None:
        print("[inconclusive] 提取适配器缺失（凭据不可用），重整演练未运行。")
        env.close()
        return 2

    checks: list[dict[str, Any]] = []
    session = AccountSession(env, env.register_account("18-rebuild"))
    deleted_fact = "游泳"
    seed_turns = (
        "我叫路遥，是一名桥梁工程研究生。",
        "我每周三晚上去游泳。",
        "我有一只猫叫小满。",
    )
    conversation_id, _ = session.start_chat(seed_turns[0])
    for content in seed_turns[1:]:
        session.chat(conversation_id, content)
    online_texts = session.active_texts()
    online_items = session.items()

    # 来源删除：按产品删除语义写入墓碑（预览/重整共用同一屏障），其已写
    # 事实不得借重整复活；既有条目保留并由对账按已知墓碑证据核对。
    deleted_message_id = next(
        (
            str(turn["user_message_id"])
            for turn in session.turns
            if deleted_fact in str(turn.get("user_content") or "")
        ),
        "",
    )
    database = env.app.state.bridges_database
    service = env.app.state.automatic_profile_service
    tombstone_error: str | None = None
    if deleted_message_id:
        try:
            service.mark_message_tombstone(session.account_id, deleted_message_id)
        except Exception as exc:  # noqa: BLE001 - 墓碑写入在真实库上的兼容性如实记录
            tombstone_error = f"{exc.__class__.__name__}: {exc}"
    tombstoned = bool(deleted_message_id) and (
        database.connection.execute(
            "SELECT 1 FROM profile_extraction_tombstones WHERE account_id = ? AND message_id = ?",
            (session.account_id, deleted_message_id),
        ).fetchone()
        is not None
    )
    _check(
        checks,
        "drill:source-tombstoned",
        bool(deleted_message_id) and tombstone_error is None and tombstoned,
        f"删除消息={deleted_message_id} 墓碑错误={tombstone_error!r}",
    )
    baseline_citing = (
        _items_citing(database, session.account_id, deleted_message_id)
        if deleted_message_id
        else set()
    )
    _check(
        checks,
        "drill:online-write-before-rebuild",
        any(deleted_fact in text for text in online_texts) and bool(deleted_message_id),
        f"在线事实={online_texts!r} 删除消息={deleted_message_id} "
        f"引用条目={sorted(baseline_citing)!r}",
    )

    # 重整（真实网关抽取器）。
    from bridges.profiles.history_preview import preview_history
    from bridges.profiles.history_rebuild import rebuild_status, start_rebuild
    from bridges.profiles.history_rebuild_drive import run_rebuild
    from bridges.profiles.history_rebuild_reconcile import reconcile_rebuild

    authority = _authority(env.database_path)
    manifest = preview_history(authority, account_id=session.account_id)
    scope = manifest.chat_scope
    _check(
        checks,
        "rebuild:window-excludes-deleted-source",
        scope.eligible == 2 and scope.skipped_tombstoned == 1,
        f"chat_scope={json.dumps(manifest.as_dict()['chat_scope'], ensure_ascii=False)}",
    )
    work = output_dir / "rebuild-drill" / stamp / "work"
    work.mkdir(parents=True, exist_ok=True)
    report = start_rebuild(authority, manifest, work_dir=work)
    print(f"[18] 重整启动：{report['rebuild_id']} ...", flush=True)
    factory = _gateway_factory(env.app)
    progress = report["progress"]
    ticks = 0
    final = {"completed": False, "progress": progress}
    while ticks < 200:
        final = run_rebuild(work, extractor_factory=factory, max_ticks=10)
        progress = final["progress"]
        ticks += 10
        if final["completed"] or progress.get("remaining", 1) == 0:
            break
    status = rebuild_status(work)
    _check(
        checks,
        "rebuild:completed-with-real-extractor",
        bool(final["completed"]) and progress.get("failed", 1) == 0,
        f"progress={json.dumps(progress, ensure_ascii=False, default=str)} "
        f"status={status['progress']}",
    )

    from bridges.storage.database import BridgesDatabase

    working = BridgesDatabase(work / "working" / "bridges.db")
    working_runs = [
        dict(row)
        for row in working.connection.execute(
            "SELECT source, status, COUNT(*) AS total FROM profile_extraction_runs"
            " WHERE account_id = ? GROUP BY source, status",
            (session.account_id,),
        ).fetchall()
    ]
    _check(
        checks,
        "rebuild:real-extractor-used",
        any(str(run.get("source")) == "qwen_model" for run in working_runs),
        f"重整提取运行={working_runs!r}",
    )

    reconcile = reconcile_rebuild(work, extractor_factory=factory, authority=authority)
    _check(
        checks,
        "rebuild:reconcile-passed",
        reconcile.get("passed") is True and reconcile.get("errors") == [],
        f"passed={reconcile.get('passed')!r} errors={reconcile.get('errors')!r}",
    )

    # 注册另一账户会吊销当前会话（注册入口的安全行为），因此放在主账户
    # 全部 HTTP 操作之后，只用于数据库层面的隔离核对。
    other_account = env.register_account("18-rebuild-other")

    refreshed = BridgesDatabase(env.database_path)
    refreshed_items = [
        str(row["text"])
        for row in refreshed.connection.execute(
            "SELECT text FROM profile_items WHERE account_id = ? AND status = 'active'",
            (session.account_id,),
        ).fetchall()
    ]
    other_items = [
        str(row["text"])
        for row in refreshed.connection.execute(
            "SELECT text FROM profile_items WHERE account_id = ? AND status = 'active'",
            (other_account.account_id,),
        ).fetchall()
    ]
    final_citing = (
        _items_citing(refreshed, session.account_id, deleted_message_id)
        if deleted_message_id
        else set()
    )
    facts_section = reconcile.get("facts")
    evidence_section = reconcile.get("evidence")
    facts_section = facts_section if isinstance(facts_section, dict) else {}
    evidence_section = evidence_section if isinstance(evidence_section, dict) else {}
    _check(
        checks,
        "rebuild:deleted-source-not-resurrected",
        final_citing == baseline_citing and bool(baseline_citing),
        f"重整前引用={sorted(baseline_citing)!r} 重整后引用={sorted(final_citing)!r}"
        "（既有条目保留、不得新增引用；新增即复活）",
    )
    _check(
        checks,
        "rebuild:tombstone-barrier-clean",
        facts_section.get("tombstoned_late_success") == 0
        and evidence_section.get("tombstone_refs") == [],
        f"tombstoned_late_success={facts_section.get('tombstoned_late_success')!r} "
        f"known_tombstone_refs={evidence_section.get('known_tombstone_refs')!r} "
        f"tombstone_refs={evidence_section.get('tombstone_refs')!r} "
        f"重整后活动条目={refreshed_items!r}（已知墓碑证据条目按基线保留，"
        f"在线条目数={len(online_items)}）",
    )
    _check(
        checks,
        "rebuild:other-account-isolated",
        not other_items,
        f"另一账户活动条目={other_items!r}",
    )

    provider = getattr(env.app.state, "run_model_config_provider", None)
    model_id = None
    if provider is not None:
        model_id = (provider.snapshot().model_dump(mode="json") or {}).get("model_id")
    payload = {
        "kind": "profile-loop-real-rebuild-drill",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "model_id": model_id,
        "run_dir": str(run_dir),
        "work_dir": str(work),
        "rebuild_id": report["rebuild_id"],
        "progress": progress,
        "working_extraction_runs": working_runs,
        "checks": checks,
    }
    json_path = output_dir / f"rebuild-drill-{stamp}.json"
    md_path = output_dir / f"rebuild-drill-{stamp}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render(payload), encoding="utf-8")
    env.app.state.bridges_database.close()
    env.app.state.state_store.close()
    env.close()

    failed = [check["check_id"] for check in checks if not check["passed"]]
    print(f"[done] 重整演练未通过判定 {len(failed)} 条：{failed}")
    print(f"[done] evidence written to {json_path} and {md_path}")
    if args.strict and failed:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
