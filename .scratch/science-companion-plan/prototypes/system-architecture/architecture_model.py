"""一次性原型：验证系统架构的作用域、撤权、删除、故障与部署不变量。"""

from __future__ import annotations

from copy import deepcopy


MODES = ("dev-conda", "manual", "cli", "docker", "podman")
FAILURES = ("healthy", "redis", "qwen", "sandbox", "postgres")


def initial_state() -> dict:
    return {
        "active_user": "用户A",
        "mode": "dev-conda",
        "dependency": "healthy",
        "private_object": None,
        "project_object": None,
        "project_access": {"用户A": True, "用户B": True},
        "run": {"state": "未创建", "actor": None, "can_commit": False},
        "key_epoch": 1,
        "tombstone": False,
        "quarantine": [],
        "last_decision": "就绪：Conda agent 仅为本地开发模式。",
    }


def _can_read_private(state: dict) -> bool:
    obj = state["private_object"]
    return bool(obj and obj["owner"] == state["active_user"] and not obj["deleted"])


def reduce(state: dict, action: str) -> dict:
    next_state = deepcopy(state)
    user = next_state["active_user"]

    if action == "create_private":
        if user != "用户A":
            next_state["last_decision"] = "拒绝：用户B不能在用户A的保险库内创建对象。"
        else:
            next_state["private_object"] = {
                "id": "vault:A:001",
                "owner": "用户A",
                "domain": "个人保险库",
                "deleted": False,
            }
            next_state["last_decision"] = "通过：私人对象只属于用户A。"

    elif action == "share_copy":
        if not _can_read_private(next_state):
            next_state["last_decision"] = "拒绝：只有原件所有者可执行最小化复制。"
        else:
            next_state["project_object"] = {
                "id": "project:P1:001",
                "derived_from": next_state["private_object"]["id"],
                "domain": "显式共享项目",
                "deleted": False,
                "version": 1,
            }
            next_state["tombstone"] = False
            next_state["last_decision"] = "通过：已创建独立项目副本，未共享私人原件。"

    elif action == "switch_user":
        next_state["active_user"] = "用户B" if user == "用户A" else "用户A"
        next_state["last_decision"] = f"已切换为{next_state['active_user']}，缓存作用域随会话重建。"

    elif action == "read_private":
        if _can_read_private(next_state):
            next_state["last_decision"] = "通过：所有者读取自己的私人对象。"
        else:
            next_state["last_decision"] = "拒绝：RLS与对象授权阻止跨账户读取。"

    elif action == "start_job":
        obj = next_state["project_object"]
        allowed = (
            obj
            and not obj["deleted"]
            and next_state["project_access"].get(user, False)
            and next_state["dependency"] != "postgres"
        )
        if not allowed:
            next_state["run"] = {"state": "BLOCKED", "actor": user, "can_commit": False}
            next_state["last_decision"] = "闭锁：对象、授权或PostgreSQL权威状态不可用。"
        else:
            next_state["run"] = {"state": "RUNNING", "actor": user, "can_commit": True}
            next_state["last_decision"] = "通过：任务携带当前授权快照与密钥时期启动。"

    elif action == "revoke_b":
        next_state["project_access"]["用户B"] = False
        next_state["key_epoch"] += 1
        if next_state["run"]["actor"] == "用户B":
            next_state["run"]["state"] = "CANCELLED"
            next_state["run"]["can_commit"] = False
        next_state["last_decision"] = "撤权完成：阻止新访问、轮换密钥、取消用户B运行。"

    elif action == "delete_project":
        if next_state["project_object"]:
            next_state["project_object"]["deleted"] = True
            next_state["tombstone"] = True
            next_state["run"]["can_commit"] = False
            if next_state["run"]["state"] == "RUNNING":
                next_state["run"]["state"] = "CANCELLED"
            next_state["last_decision"] = "删除墓碑已优先传播，运行不能继续提交。"
        else:
            next_state["last_decision"] = "无项目对象可删除。"

    elif action == "offline_edit":
        if next_state["tombstone"]:
            next_state["quarantine"].append("设备D旧操作")
            next_state["last_decision"] = "隔离：旧离线编辑不能越过删除墓碑复活对象。"
        elif next_state["project_object"]:
            next_state["project_object"]["version"] += 1
            next_state["last_decision"] = "通过：离线操作基于当前授权产生新版本。"
        else:
            next_state["last_decision"] = "拒绝：没有可编辑的项目对象。"

    elif action == "cycle_failure":
        current = FAILURES.index(next_state["dependency"])
        next_state["dependency"] = FAILURES[(current + 1) % len(FAILURES)]
        failure = next_state["dependency"]
        decisions = {
            "healthy": "依赖恢复：重新通过就绪检查后开放能力。",
            "redis": "降级：绕过缓存并收紧并发，权威状态不变。",
            "qwen": "降级：保留浏览编辑，模型任务明确离线。",
            "sandbox": "降级：只保留不可运行草稿，禁止发布媒体成品。",
            "postgres": "闭锁：私人读写和新任务全部停止，禁止缓存兜底。",
        }
        if failure == "postgres":
            next_state["run"]["can_commit"] = False
        next_state["last_decision"] = decisions[failure]

    elif action == "cycle_mode":
        current = MODES.index(next_state["mode"])
        next_state["mode"] = MODES[(current + 1) % len(MODES)]
        if next_state["mode"] == "dev-conda":
            next_state["last_decision"] = "本地开发：使用 Conda agent，业务合同不变。"
        else:
            next_state["last_decision"] = (
                f"生产模式 {next_state['mode']}：不依赖 Conda，复用同一配置、迁移和健康语义。"
            )

    return next_state
