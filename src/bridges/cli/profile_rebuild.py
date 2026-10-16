"""画像工单 17：``BridGes profile-rebuild`` 维护命令。

在 16 的只读预览、来源校验与可恢复备份之上，执行隔离副本内的受控重整：

    profile-rebuild start      --manifest <清单> --work-dir <目录> [--start-at/--end-at]
    profile-rebuild run        --work-dir <目录> [--max-ticks N]
    profile-rebuild status     --work-dir <目录>
    profile-rebuild reconcile  --work-dir <目录> [--drill-dir <目录>] [--db <权威库>]
    profile-rebuild cleanup    --work-dir <目录> [--purge] [--force]

所有命令输出 JSON；硬门失败以 ``{"error": {"code", "message"}}`` 写入
stderr 并以退出码 2 结束。对账未通过同样退出码 2。真实用户库的执行按
现有操作授权与运行手册单独确认（本命令只处理工作副本与自身产物）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bridges.config import get_settings
from bridges.profiles.history_preview import load_preview_manifest
from bridges.profiles.history_rebuild import (
    DEFAULT_REBUILD_MAX_TICKS,
    ExtractorFactory,
    cleanup_rebuild,
    rebuild_status,
    run_rebuild,
    start_rebuild,
)
from bridges.profiles.history_rebuild_contracts import RebuildWindow
from bridges.profiles.history_rebuild_reconcile import reconcile_rebuild
from bridges.profiles.replay import (
    DatabaseIdentity,
    ProfileReplayError,
    load_runtime_application_state,
    resolve_authoritative_database,
)

profile_rebuild_app = typer.Typer(
    name="profile-rebuild",
    help="画像历史重整：隔离副本执行、有界恢复、对账与恢复演练",
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


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


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


def _optional_authority(database_path: Path | None) -> DatabaseIdentity | None:
    """对账的漂移复核：拿不到权威来源时降级为警告，不阻断隔离副本对账。"""

    try:
        return _authority(database_path)
    except typer.Exit:
        return None


def _parse_moment(value: str | None) -> str | None:
    if value is None:
        return None
    from datetime import UTC, datetime

    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        _fail(
            ProfileReplayError("window_invalid", f"时间参数不是有效 ISO 时间：{value}")
        )
        raise typer.Exit(2) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _extractor_factory() -> ExtractorFactory:
    """装配抽取器的模型适配部分：测试环境为确定性规则，其余为生产网关。"""

    from collections.abc import Callable

    from bridges.profiles.automatic import (
        AutomaticProfileExtractor,
        GatewayAutomaticProfileExtractor,
        NeighborWindowReader,
        RuleBasedAutomaticProfileExtractor,
    )
    from bridges.storage.database import BridgesDatabase

    settings = get_settings()
    if settings is None or settings.environment.lower() == "test":

        def rule_based(
            database: BridgesDatabase,
            neighbor: NeighborWindowReader | None,
            memory: Callable[[str, str], list[str]] | None,
        ) -> AutomaticProfileExtractor:
            del database, neighbor, memory
            return RuleBasedAutomaticProfileExtractor()

        return rule_based

    from bridges.ai.production import build_production_composition
    from bridges.ai.run_model_config import RunModelConfigProvider
    from bridges.persistence import build_state_store

    def gateway(
        database: BridgesDatabase,
        neighbor: NeighborWindowReader | None,
        memory: Callable[[str, str], list[str]] | None,
    ) -> AutomaticProfileExtractor:
        assert settings is not None
        state_store = build_state_store(
            settings.database_url, encryption_key=settings.secret_key
        )
        provider = RunModelConfigProvider(state_store)
        composition = build_production_composition(
            settings, model_config_provider=provider
        )
        if not composition.global_key_configured:
            raise ProfileReplayError(
                "model_gateway_unavailable",
                "未配置全局模型 Key，无法执行历史重整的模型抽取。",
            )
        return GatewayAutomaticProfileExtractor(
            composition.gateway,
            model_config_provider=provider,
            neighbor_reader=neighbor,
            memory_reader=memory,
        )

    return gateway


@profile_rebuild_app.command("start")
def profile_rebuild_start(
    manifest: Annotated[Path, typer.Option("--manifest", help="预览清单路径")],
    work_dir: Annotated[Path, typer.Option("--work-dir", help="重整工作目录")],
    start_at: Annotated[
        str | None, typer.Option("--start-at", help="窗口下界（ISO，含）")
    ] = None,
    end_at: Annotated[
        str | None, typer.Option("--end-at", help="窗口上界（ISO，不含）")
    ] = None,
    database_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """校验清单新鲜度、创建已验证备份与隔离工作副本并播种窗口判定。"""

    authority = _authority(database_path)
    try:
        preview = load_preview_manifest(manifest)
        window = RebuildWindow(_parse_moment(start_at), _parse_moment(end_at))
        report = start_rebuild(authority, preview, work_dir=work_dir, window=window)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("rebuild_io_failed", str(exc)))
        raise typer.Exit(2) from exc
    _emit(report)


@profile_rebuild_app.command("run")
def profile_rebuild_run(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="重整工作目录")],
    max_ticks: Annotated[
        int,
        typer.Option(
            "--max-ticks",
            help=f"本次最多执行的模型调用次数（默认 {DEFAULT_REBUILD_MAX_TICKS}）",
        ),
    ] = DEFAULT_REBUILD_MAX_TICKS,
) -> None:
    """有界推进一次重整；中断后重复运行即恢复，不会重复生效。"""

    try:
        report = run_rebuild(
            work_dir, extractor_factory=_extractor_factory(), max_ticks=max_ticks
        )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("rebuild_io_failed", str(exc)))
        raise typer.Exit(2) from exc
    _emit(report)


@profile_rebuild_app.command("status")
def profile_rebuild_status(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="重整工作目录")],
) -> None:
    """查看进度、分母、剩余范围与版本绑定（只读）。"""

    try:
        report = rebuild_status(work_dir)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("rebuild_io_failed", str(exc)))
        raise typer.Exit(2) from exc
    _emit(report)


@profile_rebuild_app.command("reconcile")
def profile_rebuild_reconcile(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="重整工作目录")],
    drill_dir: Annotated[
        Path | None, typer.Option("--drill-dir", help="演练目录（默认工作目录/drill）")
    ] = None,
    database_path: Annotated[
        Path | None, typer.Option("--db", help="权威库路径（漂移复核用，可选）")
    ] = None,
) -> None:
    """完整对账与演练：事实/证据/控制/派生/清理/导出/恢复；未通过退出码 2。"""

    try:
        report = reconcile_rebuild(
            work_dir,
            extractor_factory=_extractor_factory(),
            authority=_optional_authority(database_path),
            drill_dir=drill_dir,
        )
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("rebuild_io_failed", str(exc)))
        raise typer.Exit(2) from exc
    _emit(report)
    if not bool(report.get("passed")):
        raise typer.Exit(2)


@profile_rebuild_app.command("cleanup")
def profile_rebuild_cleanup(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="重整工作目录")],
    purge: Annotated[
        bool, typer.Option("--purge", help="连同恢复前提与记录一并删除")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="放弃未收敛的台账并强制清理")
    ] = False,
) -> None:
    """清理工作副本与台账；默认保留已验证备份与对账记录供审计。"""

    try:
        report = cleanup_rebuild(work_dir, purge=purge, force=force)
    except ProfileReplayError as exc:
        _fail(exc)
        raise typer.Exit(2) from exc
    except OSError as exc:
        _fail(ProfileReplayError("rebuild_io_failed", str(exc)))
        raise typer.Exit(2) from exc
    _emit(report)


__all__ = ["profile_rebuild_app"]
