"""profile-auto-v2 安全回放命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.config import get_settings  # noqa: E402
from bridges.profiles.replay import (  # noqa: E402
    ProfileReplayCoordinator,
    ProfileReplayError,
    create_verified_backup,
    load_runtime_application_state,
    resolve_authoritative_database,
    upgrade_authoritative_schema,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="profile-auto-v2 安全回放")
    parser.add_argument(
        "--db",
        type=Path,
        help="仅作为运行时配置的显式候选；不会覆盖或绕过权威配置。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inspect", help="检查权威数据库身份、schema 和写入者")
    subparsers.add_parser("schema-upgrade", help="显式执行数据库 schema upgrade")

    backup = subparsers.add_parser("backup", help="创建并验证回放备份")
    backup.add_argument("--target", required=True, type=Path)

    subparsers.add_parser("dry-run", help="只读统计候选和安全跳过原因")

    replay = subparsers.add_parser("replay", help="使用已验证备份入队 v2 回放")
    replay.add_argument("--backup", required=True, type=Path)
    replay.add_argument("--confirm", required=True)
    replay.add_argument("--drain", action="store_true")
    replay.add_argument("--max-steps", type=int, default=100)

    worker = subparsers.add_parser("worker", help="运行 profile-replay-v2 受监督 worker")
    worker.add_argument("--max-steps", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        authority = resolve_authoritative_database(
            settings=get_settings(),
            explicit_path=args.db,
            application_state=load_runtime_application_state(),
        )
        if args.command == "inspect":
            result: object = authority.as_dict()
        elif args.command == "schema-upgrade":
            result = upgrade_authoritative_schema(authority).as_dict()
        elif args.command == "backup":
            result = create_verified_backup(authority, args.target).as_dict()
        elif args.command == "dry-run":
            result = ProfileReplayCoordinator(authority).dry_run().as_dict()
        elif args.command == "replay":
            coordinator = ProfileReplayCoordinator(authority)
            dry_run_report = coordinator.dry_run()
            result = coordinator.replay(
                backup=args.backup,
                confirmation=args.confirm,
                dry_run_report=dry_run_report,
                drain=args.drain,
                max_steps=args.max_steps,
            ).as_dict()
        else:
            result = ProfileReplayCoordinator(authority).worker(
                max_steps=args.max_steps
            ).as_dict()
    except ProfileReplayError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
