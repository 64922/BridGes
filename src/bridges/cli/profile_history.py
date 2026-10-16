"""画像工单 16：``BridGes profile-history`` 维护命令。

命令族只做只读预览、来源版本校验、可恢复备份与隔离恢复演练；不执行历史
重整（工单 17），不停止用户运行实例，也不把预览解释为恢复被遗忘内容的
许可。所有命令输出 JSON；硬门失败以 ``{"error": {"code", "message"}}``
写入 stderr 并以退出码 2 结束。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bridges.config import get_settings
from bridges.profiles.history_preview import (
    create_history_backup,
    load_preview_manifest,
    preview_history,
    save_preview_manifest,
    verify_history_restore,
    verify_preview_manifest,
)
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    load_runtime_application_state,
    resolve_authoritative_database,
)

profile_history_app = typer.Typer(
    name="profile-history",
    help="画像历史重整：只读预览、来源校验、可恢复备份与恢复演练",
    no_args_is_help=True,
)


def _fail(exc: ProfileReplayError) -> None:
    typer.echo(
        json.dumps(
            {"error": {"code": exc.code, "message": exc.message}},
            ensure_ascii=False,
        ),
        err=True,
    )


def _authority(database_path: Path | None) -> DatabaseIdentity:
    try:
        return resolve_authoritative_database(
            settings=get_settings(),
            explicit_path=database_path,
            application_state=load_runtime_application_state(),
        )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@profile_history_app.command("preview")
def profile_history_preview(
    account: Annotated[str, typer.Option("--account", help="要预览的账户标识")],
    out: Annotated[
        Path | None, typer.Option("--out", help="预览清单保存路径（可选）")
    ] = None,
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """只读预览账户的可重整条目与聊天范围；不写库、不升级 schema、不调用模型。"""
    authority = _authority(database_path)
    try:
        manifest = preview_history(authority, account_id=account)
        if out is not None:
            save_preview_manifest(manifest, out)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    _emit(manifest.as_dict())


@profile_history_app.command("verify")
def profile_history_verify(
    manifest: Annotated[Path, typer.Option("--manifest", help="预览清单路径")],
    require_fresh: Annotated[
        bool, typer.Option("--require-fresh", help="清单过期时以退出码 2 失败")
    ] = False,
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """对照当前数据源校验预览清单；漂移时要求重新预览。"""
    authority = _authority(database_path)
    try:
        preview = load_preview_manifest(manifest)
        report = verify_preview_manifest(authority, preview)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    _emit(report.as_dict())
    if require_fresh and not report.fresh:
        raise typer.Exit(2)


@profile_history_app.command("backup")
def profile_history_backup(
    manifest: Annotated[Path, typer.Option("--manifest", help="预览清单路径")],
    target: Annotated[Path, typer.Option("--target", help="备份文件目标路径")],
    work_dir: Annotated[
        Path | None, typer.Option("--work-dir", help="隔离恢复演练工作目录（可选）")
    ] = None,
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """按预览清单创建可恢复备份并执行隔离恢复核对。"""
    authority = _authority(database_path)
    try:
        preview = load_preview_manifest(manifest)
        record = create_history_backup(
            authority, target, preview=preview, work_dir=work_dir
        )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    _emit(record.as_dict())


@profile_history_app.command("restore-verify")
def profile_history_restore_verify(
    manifest: Annotated[Path, typer.Option("--manifest", help="预览清单路径")],
    backup: Annotated[Path, typer.Option("--backup", help="备份文件路径")],
    work_dir: Annotated[
        Path | None, typer.Option("--work-dir", help="隔离恢复演练工作目录（可选）")
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="恢复演练记录保存路径（可选）")
    ] = None,
) -> None:
    """在隔离副本中恢复并核对一个已有备份；只验证文件存在不算通过。"""
    try:
        preview = load_preview_manifest(manifest)
        record = verify_history_restore(backup, preview=preview, work_dir=work_dir)
        if out is not None:
            out.write_text(
                json.dumps(
                    record.as_dict(), ensure_ascii=False, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("record_write_failed", f"无法写入恢复记录：{out}"))
        raise typer.Exit(2) from exc
    _emit(record.as_dict())


__all__ = ["profile_history_app"]
