"""画像工单 19：``BridGes profile-cutover`` 维护命令。

只提供切换前只读审计和隔离恢复库生成；不停止运行实例、不覆盖正式数据库，
也不把命令成功解释为生产部署完成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bridges.config import get_settings
from bridges.profiles.cutover import audit_profile_retirement
from bridges.profiles.cutover_recovery import (
    recover_all_cutover_state,
    recover_cutover_state,
)
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    load_runtime_application_state,
    resolve_authoritative_database,
)

profile_cutover_app = typer.Typer(
    name="profile-cutover",
    help="画像四维路径退出审计与切换恢复",
    no_args_is_help=True,
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


def _fail(exc: ProfileReplayError) -> None:
    typer.echo(
        json.dumps(
            {"error": {"code": exc.code, "message": exc.message}},
            ensure_ascii=False,
        ),
        err=True,
    )


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@profile_cutover_app.command("audit")
def profile_cutover_audit(
    account: Annotated[str, typer.Option("--account", help="要审计的账户标识")],
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """只读检查一个账户的切换前状态；不通过时退出码 4。"""
    authority = _authority(database_path)
    try:
        report = audit_profile_retirement(authority.path, account_id=account)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    _emit(report.as_dict())
    if not report.passed:
        raise typer.Exit(4)


@profile_cutover_app.command("recover")
def profile_cutover_recover(
    backup: Annotated[Path, typer.Option("--backup", help="已验证的备份文件")],
    target: Annotated[Path, typer.Option("--target", help="新恢复库路径，必须不存在")],
    account: Annotated[
        str | None,
        typer.Option("--account", help="只输出指定账户的报告；省略时输出全部账户"),
    ] = None,
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """把当前库的切换后状态合并到备份副本；实际合并覆盖全部账户。"""
    authority = _authority(database_path)
    try:
        if account is not None:
            report = recover_cutover_state(
                backup=backup,
                current_path=authority.path,
                target_path=target,
                account_id=account,
            )
            _emit(report.as_dict())
            return
        recovered = recover_all_cutover_state(
            backup=backup,
            current_path=authority.path,
            target_path=target,
        )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    _emit(recovered.as_dict())


__all__ = ["profile_cutover_app"]
