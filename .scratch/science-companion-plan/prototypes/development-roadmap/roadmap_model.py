"""一次性原型：验证成熟项目路线的阶段门是否会被局部完成绕过。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class GateState(str, Enum):
    PENDING = "待证据"
    PASS = "通过"
    WAIT_HUMAN = "等待人工"
    RETRYABLE_FAIL = "可重试失败"
    FAIL_CLOSED = "闭锁失败"
    INVALIDATED = "已失效"


@dataclass(frozen=True)
class Stage:
    code: str
    name: str
    requirements: tuple[str, ...]


STAGES = (
    Stage("R0", "规划合同与可追踪基线", ("需求追踪", "领域边界", "风险台账", "评测章程")),
    Stage("R1", "安全平台底座", ("仓库与锁文件", "身份与RLS", "类型化合同", "运行骨架", "四路径冒烟")),
    Stage("R2", "可信数据与控制内核", ("证据内核", "画像学习内核", "工作流控制", "评测底座", "迁移底座")),
    Stage("R3", "完整专业能力并行构建", ("学习实验室", "科学表达", "多模态实验室", "项目空间", "领域包工作台")),
    Stage("R4", "跨模块科学成长循环", ("端到端成长循环", "双状态界面", "跨模块合同", "出处完整", "全流程无障碍")),
    Stage("R5", "协作、本地优先与治理闭环", ("三域协作", "保险库与同步", "撤权删除", "领域包失效传播", "机构边界")),
    Stage("R6", "生产硬化与部署等价", ("容量证据", "故障演练", "恢复演练", "安全硬门", "四路径等价", "运维文档")),
    Stage("R7", "成熟发行基线", ("G0构建完整", "G1确定性正确", "G2安全科学硬门", "G3质量非劣", "G4人工有效", "G5运营就绪")),
)

MIGRATION_PHASES = ("无", "扩展", "回填", "双读双写", "切换", "收缩", "已验证")
DEPLOYMENT_PATHS = ("手动分进程", "统一CLI", "Docker", "Podman")


@dataclass(frozen=True)
class Risk:
    name: str
    severity: str
    state: str


@dataclass(frozen=True)
class ProgramState:
    stage_index: int
    evidence: dict[str, GateState]
    global_security_gate: GateState
    migration_phase: str
    deployment_paths: frozenset[str]
    risks: tuple[Risk, ...]
    released: bool
    last_result: str


@dataclass(frozen=True)
class Action:
    kind: str
    value: str = ""


def initial_state() -> ProgramState:
    return ProgramState(
        stage_index=0,
        evidence={requirement: GateState.PENDING for stage in STAGES for requirement in stage.requirements},
        global_security_gate=GateState.PASS,
        migration_phase="无",
        deployment_paths=frozenset(),
        risks=(),
        released=False,
        last_result="路线已初始化；尚无阶段可以被跳过。",
    )


def current_stage(state: ProgramState) -> Stage:
    return STAGES[state.stage_index]


def blocking_reasons(state: ProgramState) -> tuple[str, ...]:
    stage = current_stage(state)
    reasons = [
        f"{name}={state.evidence[name].value}"
        for name in stage.requirements
        if state.evidence[name] != GateState.PASS
    ]
    reasons.extend(
        f"关键风险未关闭：{risk.name}"
        for risk in state.risks
        if risk.severity == "关键" and risk.state != "已验证关闭"
    )
    if state.global_security_gate != GateState.PASS:
        reasons.append(f"全局安全硬门={state.global_security_gate.value}")
    if state.migration_phase not in ("无", "已验证"):
        reasons.append(f"迁移仍处于{state.migration_phase}")
    if stage.code == "R6" and state.deployment_paths != frozenset(DEPLOYMENT_PATHS):
        missing = "、".join(path for path in DEPLOYMENT_PATHS if path not in state.deployment_paths)
        reasons.append(f"部署路径未等价：{missing}")
    return tuple(reasons)


def dispatch(state: ProgramState, action: Action) -> ProgramState:
    if action.kind == "pass_next":
        stage = current_stage(state)
        for requirement in stage.requirements:
            if state.evidence[requirement] in (
                GateState.PENDING,
                GateState.WAIT_HUMAN,
                GateState.RETRYABLE_FAIL,
                GateState.INVALIDATED,
            ):
                evidence = dict(state.evidence)
                evidence[requirement] = GateState.PASS
                return replace(state, evidence=evidence, last_result=f"已登记证据：{requirement}=通过。")
        return replace(state, last_result="当前阶段没有可直接登记的待办证据。")

    if action.kind == "advance":
        reasons = blocking_reasons(state)
        if reasons:
            return replace(state, last_result="禁止推进：" + "；".join(reasons))
        if state.stage_index == len(STAGES) - 1:
            return replace(state, released=True, last_result="成熟发行基线已满足；允许受控发布。")
        next_index = state.stage_index + 1
        return replace(
            state,
            stage_index=next_index,
            last_result=f"阶段门通过，进入 {STAGES[next_index].code} {STAGES[next_index].name}。",
        )

    if action.kind == "inject_contract_change":
        evidence = dict(state.evidence)
        for requirement in ("类型化合同", "跨模块合同", "迁移底座", "四路径等价", "G0构建完整", "G1确定性正确"):
            if evidence.get(requirement) == GateState.PASS:
                evidence[requirement] = GateState.INVALIDATED
        risk = Risk("破坏性合同变更", "关键", "迁移中")
        return replace(
            state,
            evidence=evidence,
            migration_phase="扩展",
            risks=tuple(r for r in state.risks if r.name != risk.name) + (risk,),
            last_result="合同变更已登记：相关证据失效，必须完成扩展/回填/双运行/切换/收缩验证。",
        )

    if action.kind == "migration_next":
        if state.migration_phase in ("无", "已验证"):
            return replace(state, last_result="当前没有待推进的破坏性迁移。")
        index = MIGRATION_PHASES.index(state.migration_phase)
        next_phase = MIGRATION_PHASES[index + 1]
        risks = state.risks
        if next_phase == "已验证":
            risks = tuple(
                replace(risk, state="已验证关闭") if risk.name == "破坏性合同变更" else risk
                for risk in risks
            )
        return replace(
            state,
            migration_phase=next_phase,
            risks=risks,
            last_result=f"迁移推进到：{next_phase}。",
        )

    if action.kind == "inject_security_failure":
        risk = Risk("跨账户隔离失败", "关键", "闭锁")
        return replace(
            state,
            global_security_gate=GateState.FAIL_CLOSED,
            risks=tuple(r for r in state.risks if r.name != risk.name) + (risk,),
            last_result="发现安全硬失败：全局安全硬门=闭锁失败，其他质量分数不能抵消。",
        )

    if action.kind == "remediate_security":
        risks = tuple(
            replace(risk, state="待复验证") if risk.name == "跨账户隔离失败" else risk
            for risk in state.risks
        )
        return replace(
            state,
            global_security_gate=GateState.PENDING,
            risks=risks,
            last_result="修复已提交，但硬门被重置为待证据；必须完整复验证才能关闭风险。",
        )

    if action.kind == "close_security_risk":
        risks = tuple(
            replace(risk, state="已验证关闭") if risk.name == "跨账户隔离失败" else risk
            for risk in state.risks
        )
        return replace(
            state,
            global_security_gate=GateState.PASS,
            risks=risks,
            last_result="安全复验证完成，全局硬门重新通过，关键风险已关闭。",
        )

    if action.kind == "certify_deployment":
        remaining = [path for path in DEPLOYMENT_PATHS if path not in state.deployment_paths]
        if not remaining:
            return replace(state, last_result="四种生产部署路径均已有等价证据。")
        paths = state.deployment_paths | {remaining[0]}
        evidence = dict(state.evidence)
        if paths == frozenset(DEPLOYMENT_PATHS):
            evidence["四路径等价"] = GateState.PASS
        return replace(
            state,
            evidence=evidence,
            deployment_paths=paths,
            last_result=f"已认证部署路径：{remaining[0]}。",
        )

    if action.kind == "run_resilience_drills":
        evidence = dict(state.evidence)
        changed = []
        for requirement in ("容量证据", "故障演练", "恢复演练"):
            if requirement in current_stage(state).requirements:
                evidence[requirement] = GateState.PASS
                changed.append(requirement)
        return replace(
            state,
            evidence=evidence,
            last_result="已登记演练证据：" + ("、".join(changed) if changed else "当前阶段不接收生产演练"),
        )

    return replace(state, last_result=f"未知动作：{action.kind}")
