"""收尾 smoke 1（issue 01 反馈环）：API 从唯一绝对 SQLite 路径启动并通过健康检查。

现状基线：Playwright 启动 API 时曾发生 ``sqlite3.OperationalError: unable to
open database file``；本测试要求 API 进程自身完成迁移并对外提供
``/health/ready = pass``，且 SQLite 与对象目录落在本次运行的唯一绝对目录内。
"""

from __future__ import annotations

import httpx
from conftest import SpawnedApi


def test_api_starts_from_unique_absolute_sqlite_path_and_health_passes(
    api_server,
    unique_data_dir,
) -> None:
    api = api_server(unique_data_dir)

    # 唯一绝对数据目录内已生成版本化 SQLite 与加密对象库根目录
    assert (unique_data_dir / "bridges.db").is_file()
    assert (unique_data_dir / "objects").is_dir()

    # 健康检查：/health 200，/health/ready 为 pass
    # （trust_env=False：不走 Windows 系统代理，仅访问回环服务）
    assert (
        httpx.get(f"{api.base_url}/health", timeout=5.0, trust_env=False).status_code
        == 200
    )
    ready = httpx.get(f"{api.base_url}/health/ready", timeout=5.0, trust_env=False)
    assert ready.status_code == 200
    assert ready.json().get("ready") == "pass"


def test_two_api_runs_do_not_collide_on_ports_or_data_dirs(
    api_server, tmp_path
) -> None:
    """连续两次启动：唯一数据目录 + 空闲端口，互不干扰（AC2 的进程内代理）。

    两个 API 实例同时存活：端口不同、数据目录不同、健康检查各自通过；
    任一实例残留的 WAL/句柄不影响另一个。
    """
    first: SpawnedApi = api_server(tmp_path / "run-a")
    second: SpawnedApi = api_server(tmp_path / "run-b")

    assert first.base_url != second.base_url
    assert first.data_dir != second.data_dir
    assert (first.data_dir / "bridges.db").is_file()
    assert (second.data_dir / "bridges.db").is_file()

    for api in (first, second):
        assert (
            httpx.get(f"{api.base_url}/health", timeout=5.0, trust_env=False).status_code
            == 200
        )
        ready = httpx.get(f"{api.base_url}/health/ready", timeout=5.0, trust_env=False)
        assert ready.status_code == 200
        assert ready.json().get("ready") == "pass"
