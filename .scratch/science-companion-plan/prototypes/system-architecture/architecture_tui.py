"""一次性 TUI 外壳；可移植状态逻辑位于 architecture_model.py。"""

from __future__ import annotations

import os

from architecture_model import initial_state, reduce


BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ACTIONS = {
    "p": "create_private",
    "s": "share_copy",
    "u": "switch_user",
    "r": "read_private",
    "j": "start_job",
    "x": "revoke_b",
    "d": "delete_project",
    "o": "offline_edit",
    "f": "cycle_failure",
    "m": "cycle_mode",
}


def describe(value: object) -> str:
    if value is None:
        return "—"
    return str(value)


def render(state: dict) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{BOLD}系统架构逻辑原型（一次性）{RESET}")
    print(f"{DIM}验证账户隔离、复制分享、撤权、墓碑、故障降级和部署等价{RESET}\n")
    print(f"{BOLD}当前用户{RESET}: {state['active_user']}")
    print(f"{BOLD}运行方式{RESET}: {state['mode']}")
    print(f"{BOLD}依赖状态{RESET}: {state['dependency']}")
    print(f"{BOLD}密钥时期{RESET}: {state['key_epoch']}")
    print(f"{BOLD}私人对象{RESET}: {describe(state['private_object'])}")
    print(f"{BOLD}项目副本{RESET}: {describe(state['project_object'])}")
    print(f"{BOLD}项目权限{RESET}: {state['project_access']}")
    print(f"{BOLD}工作流{RESET}: {state['run']}")
    print(f"{BOLD}删除墓碑{RESET}: {state['tombstone']}")
    print(f"{BOLD}隔离操作{RESET}: {state['quarantine'] or '—'}")
    print(f"\n{BOLD}最后裁决{RESET}: {state['last_decision']}\n")
    print(
        f"{BOLD}[p]{RESET} 私人对象  {BOLD}[s]{RESET} 复制分享  "
        f"{BOLD}[u]{RESET} 切换用户  {BOLD}[r]{RESET} 读私人对象"
    )
    print(
        f"{BOLD}[j]{RESET} 启动任务  {BOLD}[x]{RESET} 撤销用户B  "
        f"{BOLD}[d]{RESET} 删除项目  {BOLD}[o]{RESET} 离线编辑"
    )
    print(
        f"{BOLD}[f]{RESET} 切换故障  {BOLD}[m]{RESET} 切换运行方式  "
        f"{BOLD}[q]{RESET} 退出"
    )


def main() -> None:
    state = initial_state()
    while True:
        render(state)
        key = input("\n操作> ").strip().lower()[:1]
        if key == "q":
            break
        if key in ACTIONS:
            state = reduce(state, ACTIONS[key])
        else:
            state["last_decision"] = "未知操作；请选择底部快捷键。"


if __name__ == "__main__":
    main()
