"""收尾 smoke 单命令入口（issue 01：Windows 可执行反馈环）。

在干净 PowerShell 中运行：

    .\\.venv\\Scripts\\python.exe scripts\\closeout_smoke.py

一次执行收尾 pytest 套件（tests/closeout：真实 API 子进程、真实假邮件
服务子进程、真实 arXiv worker 子进程）与浏览器纵向切片（真实前端 + 真实
HTTP 链路，无 route mock）。退出码聚合反映全部子命令的通过/失败：

- 0：全部通过；
- 1：任一子命令失败（pytest 或 Playwright 的失败计数）；
- 2：运行环境错误（Python/浏览器/产物目录不可用）。

CI 或不想启动浏览器时加 ``--skip-browser`` 只跑 pytest 套件。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "apps" / "web"


def _venv_python() -> str:
    if os.name == "nt":
        candidate = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / ".venv" / "bin" / "python"
    if not candidate.exists():
        print(
            f"error: 仓库虚拟环境 Python 不存在：{candidate}。"
            "请先创建虚拟环境并安装依赖（见 README）。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return str(candidate)


def _run_pytest(python: str) -> int:
    """运行收尾 pytest 套件（三个必红 smoke + 基础设施守卫）。

    退出码语义：0=通过；1=用例失败（含两个必红 smoke）；4/5=收集或
    用法错误（运行环境问题），聚合为 2。
    """
    print("=== 1/2 收尾 pytest 套件（tests/closeout） ===", flush=True)
    result = subprocess.run(
        [python, "-m", "pytest", str(REPO_ROOT / "tests" / "closeout"), "-v"],
        cwd=REPO_ROOT,
    )
    if result.returncode == 0:
        print("pytest 套件：通过。", flush=True)
    elif result.returncode in {4, 5}:
        print(
            "pytest 套件：收集/用法错误（环境问题，非用例失败）。",
            flush=True,
        )
        return 2
    else:
        print(
            "pytest 套件：存在失败。注意 smoke 2（arXiv UTF-8）与 smoke 3"
            "（10 秒投递）是 issue 05 / issue 10 的必红回归探针，失败 trace"
            "保留在 test-results/closeout/ 下。",
            flush=True,
        )
    return result.returncode


def _free_port() -> int:
    """返回当前空闲端口（与 playwright 配置共享同一 Python 来源）。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_playwright(python: str) -> int:
    """运行浏览器纵向切片（收尾专用配置：唯一数据目录 + 串行 + 强制新进程）。"""
    print("=== 2/2 浏览器纵向切片（playwright.closeout.config.ts） ===", flush=True)
    if (
        os.name == "nt"
        and os.environ.get("CI")
        and os.environ.get("PLAYWRIGHT_BROWSERS_PATH") is None
    ):
        print("notice: CI 需要先执行 playwright install chromium（见 README）。", flush=True)
    # 端口由驱动一次性选定并经环境变量下发：playwright 主进程与 worker 进程
    # 读取同一组值（配置模块在各进程求值，不能依赖模块级随机）。
    ports_env = {
        **os.environ,
        "BRIDGES_CLOSEOUT_PORTS": ",".join(
            str(_free_port()) for _ in range(5)
        ),
    }
    npx = os.environ.get("NPX_CMD") or ("npx" if os.name != "nt" else "npx.cmd")
    result = subprocess.run(
        [npx, "playwright", "test", "-c", "playwright.closeout.config.ts"],
        cwd=WEB_DIR,
        env=ports_env,
    )
    # 实施步骤 2：结束后清理本次运行生成的浏览器数据目录（run-*），
    # 只清理本驱动创建、且不再被使用的旧运行；直接运行 playwright
    # （不经驱动）时由配置生成的 run-* 保留（下次运行时互不干扰）。
    closeout_runs = REPO_ROOT / ".tmp" / "e2e-closeout"
    if closeout_runs.is_dir():
        try:
            for entry in closeout_runs.iterdir():
                if entry.is_dir() and entry.name.startswith("run-"):
                    shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass
    if result.returncode == 0:
        print("浏览器纵向切片：通过。", flush=True)
    else:
        print(
            "浏览器纵向切片：存在失败。trace 与截图保留在 apps/web/test-results/。",
            flush=True,
        )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="BridGes 收尾 smoke 单命令入口")
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="跳过浏览器纵向切片（CI 无浏览器或只需 pytest 套件时使用）。",
    )
    args = parser.parse_args()

    python = _venv_python()
    results: list[tuple[str, int]] = []

    pytest_code = _run_pytest(python)
    results.append(("pytest", pytest_code))

    if args.skip_browser:
        print("（--skip-browser：已跳过浏览器纵向切片）", flush=True)
    else:
        if not (WEB_DIR / "node_modules" / "@playwright").exists():
            print(
                "error: apps/web 缺少 Playwright 依赖，请先执行"
                " cd apps/web && npm install。",
                file=sys.stderr,
            )
            return 2
        playwright_code = _run_playwright(python)
        results.append(("playwright", playwright_code))

    print("=" * 48, flush=True)
    for name, code in results:
        print(f"{name}: {'通过' if code == 0 else f'失败（退出码 {code}）'}", flush=True)
    overall = 1 if any(code != 0 for _, code in results) else 0
    print(f"收尾 smoke 总结果：{'通过' if overall == 0 else '失败'}", flush=True)
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
