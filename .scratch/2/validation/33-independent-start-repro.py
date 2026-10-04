"""复现 runtime_smoke 的 start 缺失 KEY_FILE 场景，带超时与整树清理。

用法：PYTHONPATH=<目标工作树>/src python 33-independent-start-repro.py <目标工作树>
预期：start 在读配置阶段以 rc=1 退出并给出 KEY_FILE 配置指引。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

repo = sys.argv[1]
src = os.path.join(repo, "src")
env = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith(("BRIDGES_", "SCIENCE_COMPANION_"))
    and key not in {"CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL"}
}
env.update(
    {
        "BRIDGES_ENVIRONMENT": "development",
        "BRIDGES_QWEN_API_KEY": "",
        "BRIDGES_QWEN_API_KEY_FILE": os.path.join(
            tempfile.gettempdir(), "bridges-definitely-missing-qwen.key"
        ),
        "BRIDGES_QWEN_RECORD_CASSETTES": "false",
        "BRIDGES_DATABASE_URL": "",
        "BRIDGES_SECRET_KEY": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": src,
    }
)
out_path = os.path.join(tempfile.gettempdir(), "bridges-start-repro.out")
with open(out_path, "w+", encoding="utf-8", errors="replace") as out:
    proc = subprocess.Popen(
        [sys.executable, "-m", "bridges.cli.main", "start"],
        cwd=repo,
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        code = proc.wait(timeout=25)
        print(f"{repo}: EXITED rc={code}")
    except subprocess.TimeoutExpired:
        print(f"{repo}: STILL RUNNING after 25s -> 校验未失败")
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    out.seek(0)
    text = out.read()
print("---- output tail ----")
print(text[-1200:])
