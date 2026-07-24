"""一次性 TUI：手动推动成熟项目路线并观察阻塞原因。"""

from __future__ import annotations

import argparse
import os

from roadmap_model import (
    Action,
    DEPLOYMENT_PATHS,
    GateState,
    ProgramState,
    STAGES,
    blocking_reasons,
    current_stage,
    dispatch,
    initial_state,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def render(state: ProgramState) -> str:
    stage = current_stage(state)
    lines = [
        f"{BOLD}成熟科教智能体开发路线逻辑原型{RESET}",
        f"{DIM}一次性原型；不代表生产调度器，也不包含开发工期估算。{RESET}",
        "",
        f"{BOLD}当前阶段{RESET}  {stage.code} · {stage.name}",
        f"{BOLD}正式发布{RESET}  {'允许' if state.released else '禁止'}",
        f"{BOLD}全局安全门{RESET}  {state.global_security_gate.value}",
        f"{BOLD}迁移阶段{RESET}  {state.migration_phase}",
        f"{BOLD}部署证据{RESET}  {len(state.deployment_paths)}/{len(DEPLOYMENT_PATHS)}",
        "",
        f"{BOLD}当前阶段证据{RESET}",
    ]
    for requirement in stage.requirements:
        lines.append(f"  {requirement:<18} {state.evidence[requirement].value}")
    lines.extend(("", f"{BOLD}关键风险{RESET}"))
    if state.risks:
        lines.extend(f"  {risk.severity} · {risk.name} · {risk.state}" for risk in state.risks)
    else:
        lines.append("  无已登记风险")
    reasons = blocking_reasons(state)
    lines.extend(("", f"{BOLD}推进判定{RESET}"))
    lines.append("  可推进" if not reasons else "  不可推进：" + "；".join(reasons))
    lines.extend(
        (
            "",
            f"{BOLD}最近结果{RESET}",
            f"  {state.last_result}",
            "",
            f"{BOLD}[p]{RESET} 登记下一项证据  {BOLD}[a]{RESET} 尝试推进  {BOLD}[c]{RESET} 注入合同变更",
            f"{BOLD}[m]{RESET} 推进迁移        {BOLD}[s]{RESET} 注入安全失败  {BOLD}[r]{RESET} 提交安全修复",
            f"{BOLD}[v]{RESET} 完成安全复验    {BOLD}[d]{RESET} 认证部署路径  {BOLD}[f]{RESET} 容量/故障/恢复演练",
            f"{BOLD}[x]{RESET} 重置             {BOLD}[q]{RESET} 退出",
        )
    )
    return "\n".join(lines)


KEYS = {
    "p": Action("pass_next"),
    "a": Action("advance"),
    "c": Action("inject_contract_change"),
    "m": Action("migration_next"),
    "s": Action("inject_security_failure"),
    "r": Action("remediate_security"),
    "v": Action("close_security_risk"),
    "d": Action("certify_deployment"),
    "f": Action("run_resilience_drills"),
}


def interactive() -> None:
    state = initial_state()
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print(render(state))
        key = input("\n选择动作：").strip().lower()[:1]
        if key == "q":
            return
        if key == "x":
            state = initial_state()
        elif key in KEYS:
            state = dispatch(state, KEYS[key])


def pass_current_stage(state: ProgramState) -> ProgramState:
    while any(state.evidence[item] != GateState.PASS for item in current_stage(state).requirements):
        before = state
        state = dispatch(state, Action("pass_next"))
        if state == before:
            break
    return state


def demo() -> None:
    state = initial_state()
    print("场景一：局部成果不能绕过阶段证据")
    state = dispatch(state, Action("advance"))
    print(state.last_result)
    state = pass_current_stage(state)
    state = dispatch(state, Action("advance"))
    print(state.last_result)

    print("\n场景二：破坏性合同变更使旧证据失效")
    state = dispatch(state, Action("inject_contract_change"))
    print(state.last_result)
    print(dispatch(state, Action("advance")).last_result)
    while state.migration_phase != "已验证":
        state = dispatch(state, Action("migration_next"))
    print(state.last_result)

    print("\n场景三：安全硬失败不能被其他分数抵消")
    state = dispatch(state, Action("inject_security_failure"))
    print(dispatch(state, Action("advance")).last_result)
    state = dispatch(state, Action("remediate_security"))
    print(state.last_result)
    state = dispatch(state, Action("close_security_risk"))
    print(state.last_result)

    print("\n场景四：生产硬化要求四种部署路径等价")
    evidence = dict(state.evidence)
    for requirement in STAGES[6].requirements:
        evidence[requirement] = GateState.PASS
    evidence["四路径等价"] = GateState.PENDING
    state = ProgramState(
        stage_index=6,
        evidence=evidence,
        global_security_gate=state.global_security_gate,
        migration_phase="已验证",
        deployment_paths=frozenset(),
        risks=tuple(risk for risk in state.risks if risk.state == "已验证关闭"),
        released=False,
        last_result="进入生产硬化场景。",
    )
    print(dispatch(state, Action("advance")).last_result)
    for _ in DEPLOYMENT_PATHS:
        state = dispatch(state, Action("certify_deployment"))
    print(dispatch(state, Action("advance")).last_result)


def main() -> None:
    parser = argparse.ArgumentParser(description="成熟项目开发路线逻辑原型")
    parser.add_argument("--demo", action="store_true", help="运行四个预置难例")
    args = parser.parse_args()
    demo() if args.demo else interactive()


if __name__ == "__main__":
    main()
