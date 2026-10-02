"""校园通勤配方的持久节点实现（改进工单 10 的首个纵向试点）。

配方的必经顺序与 ``docs/workflow/daily-workflows.md`` 第 4 节一致：

    route.parse → route.resolve → route.validate → route.request
      → route.buffer → route.verify → route.present

每个节点只接收最小任务/版本/运行/节点引用、依赖产物与剩余预算，输出
类型化证据与结果；通勤计算保持确定性（不调用模型），专业角色不能
自主委派。恢复由完成收据驱动：地点成功而路线失败时，解析与定位产物
按输入键回填，只有路线及其下游重跑；方式变更复用定位并按输入键使
路线/时间产物失效。
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from bridges.commute.buffer import evaluate_break_buffer
from bridges.commute.contracts import (
    MODE_LABELS,
    CommuteClarification,
    CommuteMode,
    CommutePlace,
    CommutePlaceRole,
    CommuteRequestAnalysis,
    CommuteRouteStatus,
)
from bridges.commute.lexicon import CAMPUS_TIMEZONE, PLACE_CITY
from bridges.commute.parsing import parse_commute_request
from bridges.commute.resolving import PlaceResolution, PlaceResolver, resolve_place
from bridges.commute.sources import (
    SNAP_LIMIT_METERS,
    PlaceSearchOutcome,
    coordinates_distance_meters,
)
from bridges.contracts.modules import (
    ModuleQueryRecord,
    ModuleQueryStatus,
)
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecoveryPolicy,
)
from bridges.kernel.registry import RecipeRegistry

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 未实测校内道路与楼门可通行性时的固定局限说明（随每条结果展示）。
FIELD_TEST_LIMITATION = (
    "校内小路与楼门可通行性需按代表性地点实测；本轮结论只依据高德返回的道路结果。"
)

#: 配方节点名（进度事件、失败定位与产物身份）。
NODE_PARSE = "route.parse"
NODE_RESOLVE = "route.resolve"
NODE_VALIDATE = "route.validate"
NODE_REQUEST = "route.request"
NODE_BUFFER = "route.buffer"
NODE_VERIFY = "route.verify"
NODE_PRESENT = "route.present"

COMMUTE_RECIPE_ID = "campus-commute"
COMMUTE_RECIPE_VERSION = "campus-commute-recipe-v1"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
COMMUTE_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解通勤请求",
    NODE_RESOLVE: "定位起终点",
    NODE_VALIDATE: "校验校内范围与方式",
    NODE_REQUEST: "查询高德路线",
    NODE_BUFFER: "计算课间缓冲",
    NODE_VERIFY: "核验路线与文字",
    NODE_PRESENT: "整理路线结果",
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
COMMUTE_CAPABILITY_VERSIONS: dict[str, str] = {
    "commute.parse_request": "commute-parse-v1",
    "commute.resolve_place": "commute-resolve-v1",
    "commute.validate_scope": "commute-validate-v1",
    "commute.request_route": "commute-amap-route-v1",
    "commute.break_buffer": "commute-buffer-v1",
    "commute.verify_route": "commute-verify-v1",
    "commute.present_route": "commute-present-v1",
}

#: 配方的必要门与可选门（登记集合；代码拒绝未登记质量门）。
COMMUTE_GATES: frozenset[str] = frozenset(
    {
        "route.units_and_text",
        "route.snap_disclosure",
        "route.map_text_consistency",
    }
)

#: 单轮外部调用的墙钟上限（秒）：超时如实失败，绝不无限等待上游。
ROUTE_DEADLINE_SECONDS = 20.0

#: 方式变更需要失效的下游节点（定位产物保留，路线/时间/呈现失效）。
ROUTE_DEPENDENT_NODES: tuple[str, ...] = (
    NODE_REQUEST,
    NODE_BUFFER,
    NODE_VERIFY,
    NODE_PRESENT,
)


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _place_key(phrase: str | None, place: CommutePlace | None) -> dict[str, Any]:
    return {
        "phrase": phrase,
        "selected": place.location if place is not None else None,
        "name": place.name if place is not None else None,
    }


# ---------------------------------------------------------------------------
# 预算视图：节点领取而非重建预算（工单 09 账本）
# ---------------------------------------------------------------------------


class CommuteBudget:
    """通勤外部调用的共享运行预算视图（缺失账本时按无预算模式运行）。"""

    def __init__(
        self,
        *,
        ledger: RunBudgetLedgerRepository,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def deadline_seconds(self, now: datetime | None = None) -> float:
        return max(0.5, self.remaining_work_ms(now) / 1000.0)

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return self._ledger.register_external_call(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=datetime.now(UTC),
        )

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )


@dataclass(frozen=True)
class BudgetedPlaceResolver:
    """按调用登记/释放共享预算的地点检索端口包装。"""

    port: PlaceResolver
    budget: CommuteBudget | None
    role: str

    def search_place(
        self,
        account_id: str,
        keywords: str,
        *,
        city: str = PLACE_CITY,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> PlaceSearchOutcome:
        if self.budget is None:
            return self.port.search_place(
                account_id, keywords, city=city, deadline=deadline, stop_event=stop_event
            )
        call_key = f"commute.place:{self.role}:{_digest(keywords)[:16]}"
        if not self.budget.register_external(call_key, purpose="commute.place"):
            return PlaceSearchOutcome(
                query=keywords,
                pois=[],
                record=ModuleQueryRecord(
                    source="amap_place",
                    query=keywords,
                    status=ModuleQueryStatus.ERROR,
                    error_code="run_budget_exhausted",
                    error_message="本轮运行预算已用尽，未发起新的地点检索。",
                    retryable=False,
                ),
            )
        outcome = self.port.search_place(
            account_id, keywords, city=city, deadline=deadline, stop_event=stop_event
        )
        status = outcome.record.status.value if outcome.record is not None else "error"
        with contextlib.suppress(Exception):  # 释放失败不改变真实结果
            self.budget.release_external(call_key, outcome_code=status)
        return outcome


# ---------------------------------------------------------------------------
# 质量门（结构化裁决；模型不能自行宣布通过）
# ---------------------------------------------------------------------------


def _units_and_text_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：距离/时间单位、缓冲时区与建议总时间一致。"""
    payload = execution.artifact.payload
    try:
        distance = int(payload.get("distance_m") or 0)
        duration = int(payload.get("base_duration_seconds") or 0)
        buffer = payload.get("buffer") or {}
        added = int(buffer.get("added_minutes") or 0)
        total = int(payload.get("suggested_total_seconds") or 0)
        timezone = str(buffer.get("timezone") or "")
    except (TypeError, ValueError):
        return QualityGateResult(
            gate="route.units_and_text",
            verdict=QualityVerdict.BLOCKED,
            code="commute_units_invalid",
            message="路线距离或耗时不是有效的整数单位，本轮不交付行程结论。",
        )
    if distance <= 0 or duration <= 0:
        return QualityGateResult(
            gate="route.units_and_text",
            verdict=QualityVerdict.BLOCKED,
            code="commute_units_invalid",
            message="路线距离或耗时为 0，不能作为可核验的行程结论。",
        )
    if timezone != CAMPUS_TIMEZONE:
        return QualityGateResult(
            gate="route.units_and_text",
            verdict=QualityVerdict.BLOCKED,
            code="commute_timezone_mismatch",
            message="课间缓冲时区不是 Asia/Shanghai，时间与地图文字不保证一致。",
        )
    if total != duration + added * 60:
        return QualityGateResult(
            gate="route.units_and_text",
            verdict=QualityVerdict.BLOCKED,
            code="commute_total_mismatch",
            message="建议总时间与基础耗时加缓冲不一致，本轮不呈现确定结论。",
        )
    return QualityGateResult(gate="route.units_and_text", verdict=QualityVerdict.PASS)


def _snap_disclosure_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选门：吸附距离超限时必须有文字披露；未披露记为待确认。"""
    del invocation
    payload = execution.artifact.payload
    disclosed = any("吸附" in str(note) for note in payload.get("limitations", []))
    if payload.get("snap_far") and not disclosed:
        return QualityGateResult(
            gate="route.snap_disclosure",
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code="commute_snap_undisclosed",
            message="高德把地点吸附到较远道路点，但结果未披露该限制。",
        )
    return QualityGateResult(gate="route.snap_disclosure", verdict=QualityVerdict.PASS)


def _map_text_consistency_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选门：有路径点必须同时有文字路段；无路径点必须显式说明不绘制。"""
    del invocation
    payload = execution.artifact.payload
    polyline = list(payload.get("polyline") or [])
    steps = list(payload.get("steps") or [])
    limitations = " ".join(str(note) for note in payload.get("limitations", []))
    if polyline and not steps:
        return QualityGateResult(
            gate="route.map_text_consistency",
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code="commute_steps_missing",
            message="存在路径点但缺少文字路段，地图与文字不一致。",
        )
    if not polyline and "不绘制任何路线线" not in limitations:
        return QualityGateResult(
            gate="route.map_text_consistency",
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code="commute_unverified_undisclosed",
            message="未取得路径点但未说明不绘制路线线。",
        )
    return QualityGateResult(gate="route.map_text_consistency", verdict=QualityVerdict.PASS)


#: 配方质量门处理器（与配方登记的门名一一对应；内核据此执行结构化裁决）。
COMMUTE_GATE_HANDLERS: dict[str, Any] = {
    "route.units_and_text": _units_and_text_gate,
    "route.snap_disclosure": _snap_disclosure_gate,
    "route.map_text_consistency": _map_text_consistency_gate,
}


# ---------------------------------------------------------------------------
# 配方与节点执行体
# ---------------------------------------------------------------------------


def _parse_key(inputs: Any) -> str:
    return _digest(
        {
            "content": inputs.user_content,
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
        }
    )


def _resolve_key(inputs: Any) -> str:
    analysis = inputs.artifacts[NODE_PARSE].payload["analysis"]
    return _digest(
        {
            "origin": _place_key(
                analysis.get("origin_phrase"), _place(analysis.get("origin_place"))
            ),
            "destination": _place_key(
                analysis.get("destination_phrase"), _place(analysis.get("destination_place"))
            ),
            "unlocatable": [
                analysis.get("origin_unlocatable"),
                analysis.get("destination_unlocatable"),
            ],
            "scope": "campus",
            "city": PLACE_CITY,
        }
    )


def _validate_key(inputs: Any) -> str:
    analysis = inputs.artifacts[NODE_PARSE].payload["analysis"]
    if NODE_RESOLVE in inputs.artifacts:
        resolve_payload = inputs.artifacts[NODE_RESOLVE].payload
        origin = resolve_payload.get("origin")
        destination = resolve_payload.get("destination")
    else:
        origin = analysis.get("origin_place")
        destination = analysis.get("destination_place")
    return _digest(
        {
            "origin": (origin or {}).get("location"),
            "destination": (destination or {}).get("location"),
            "mode": analysis.get("mode"),
        }
    )


def _request_key(inputs: Any) -> str:
    validate = inputs.artifacts[NODE_VALIDATE].payload
    return _digest(
        {
            "origin": (validate.get("origin") or {}).get("location"),
            "destination": (validate.get("destination") or {}).get("location"),
            "mode": validate.get("mode"),
            "capability": COMMUTE_CAPABILITY_VERSIONS["commute.request_route"],
        }
    )


def _buffer_key(inputs: Any) -> str:
    return _digest(
        {
            "request": inputs.artifacts[NODE_REQUEST].content_hash,
            "snapshot": inputs.buffer_snapshot,
        }
    )


def _verify_key(inputs: Any) -> str:
    return _digest(
        {
            "request": inputs.artifacts[NODE_REQUEST].content_hash,
            "buffer": inputs.artifacts[NODE_BUFFER].content_hash,
            "mode": inputs.artifacts[NODE_VALIDATE].payload.get("mode"),
        }
    )


def _present_key(inputs: Any) -> str:
    return _digest(
        {
            "verify": inputs.artifacts[NODE_VERIFY].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
        }
    )


def _place(payload: Mapping[str, Any] | None) -> CommutePlace | None:
    if not payload:
        return None
    return CommutePlace.model_validate(payload)


def build_commute_recipe() -> RecipeDefinition:
    """构造并校验通勤配方（必经顺序与依赖只指向前置节点）。"""
    return RecipeDefinition(
        recipe_id=COMMUTE_RECIPE_ID,
        recipe_version=COMMUTE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="commute.parse_request",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.parse_request"],
                artifact_type="commute.request_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="理解起终点、方式与缺失项（纯确定性解析）。",
            ),
            NodeSpec(
                name=NODE_RESOLVE,
                capability="commute.resolve_place",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.resolve_place"],
                artifact_type="commute.place_resolution",
                input_key=_resolve_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.ASK_INPUT,
                description="校内地点别名与真实 POI 候选；外部调用计入运行预算。",
            ),
            NodeSpec(
                name=NODE_VALIDATE,
                capability="commute.validate_scope",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.validate_scope"],
                artifact_type="commute.scope_validation",
                input_key=_validate_key,
                depends_on=(NODE_PARSE, NODE_RESOLVE),
                recovery=RecoveryPolicy.BLOCK,
                description="校验校内范围、可用方式与必要地点；方式变更使路线失效。",
            ),
            NodeSpec(
                name=NODE_REQUEST,
                capability="commute.request_route",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.request_route"],
                artifact_type="commute.route_path",
                input_key=_request_key,
                depends_on=(NODE_VALIDATE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="调用登记且支持该方式的路线适配器；不拿其他方式顶替。",
            ),
            NodeSpec(
                name=NODE_BUFFER,
                capability="commute.break_buffer",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.break_buffer"],
                artifact_type="commute.break_buffer",
                input_key=_buffer_key,
                depends_on=(NODE_REQUEST,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按 Asia/Shanghai 求解时间快照计算约定高峰缓冲。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="commute.verify_route",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.verify_route"],
                artifact_type="commute.route_verification",
                input_key=_verify_key,
                depends_on=(NODE_PARSE, NODE_VALIDATE, NODE_REQUEST, NODE_BUFFER),
                required_gates=("route.units_and_text",),
                optional_gates=("route.snap_disclosure", "route.map_text_consistency"),
                recovery=RecoveryPolicy.BLOCK,
                description="核对方式、单位、路段来源、吸附与地图文字一致性。",
            ),
            NodeSpec(
                name=NODE_PRESENT,
                capability="commute.present_route",
                capability_version=COMMUTE_CAPABILITY_VERSIONS["commute.present_route"],
                artifact_type="commute.route_delivery",
                input_key=_present_key,
                depends_on=(NODE_PARSE, NODE_RESOLVE, NODE_REQUEST, NODE_VERIFY),
                recovery=RecoveryPolicy.BLOCK,
                description="确定性结果投影，交由内核核验后统一提交。",
            ),
        ),
    )


def commute_recipe_registry() -> RecipeRegistry:
    """登记通勤能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=COMMUTE_CAPABILITY_VERSIONS.keys(),
        gates=COMMUTE_GATES,
    )
    registry.register(build_commute_recipe())
    return registry


class CommuteNodeFlow:
    """通勤节点的确定性执行体（不调用模型，不自主任意委派）。"""

    def __init__(
        self,
        *,
        amap: Any,
        clock: Callable[[], datetime],
        prior_context: Sequence[str] = (),
        module_context: ModuleTaskContext | None = None,
        pending_wait: Any = None,
        budget: CommuteBudget | None = None,
        stop_event: Event | None = None,
        deadline_seconds: float = ROUTE_DEADLINE_SECONDS,
    ) -> None:
        self._amap = amap
        self._clock = clock
        self._solve_time = clock()
        self._prior_context = tuple(prior_context)
        self._module_context = module_context
        self._pending_wait = pending_wait
        self._budget = budget
        self._stop_event = stop_event
        self._deadline_seconds = deadline_seconds

    @property
    def prior_digest(self) -> str | None:
        if self._module_context is not None and self._module_context.used_task_scope:
            return _digest([
                (condition.condition_id, condition.kind, condition.text)
                for condition in self._module_context.effective_conditions
            ])
        return _digest(list(self._prior_context)) if self._prior_context else None

    @property
    def buffer_snapshot(self) -> str:
        """求解时间快照（分钟粒度，Asia/Shanghai）：缓冲输入键的一部分。"""
        moment = self._solve_time.astimezone(ZoneInfo(CAMPUS_TIMEZONE))
        return moment.replace(second=0, microsecond=0).isoformat()

    # -- 节点执行体 -------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_RESOLVE: self._run_resolve,
            NODE_VALIDATE: self._run_validate,
            NODE_REQUEST: self._run_request,
            NODE_BUFFER: self._run_buffer,
            NODE_VERIFY: self._run_verify,
            NODE_PRESENT: self._run_present,
        }[invocation.spec.name]
        try:
            return handler(invocation)
        except Exception as exc:  # noqa: BLE001 - 未预期异常按可重试失败收敛
            artifact = self._artifact(
                invocation,
                trust_state=ArtifactTrust.INVALIDATED,
                payload={"error": {"type": exc.__class__.__name__}},
                error={
                    "code": "commute_node_error",
                    "message": f"节点执行出现内部错误（{exc.__class__.__name__}）。",
                },
            )
            return NodeExecution(
                artifact=artifact,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                detail={
                    "code": "commute_node_error",
                    "message": f"节点执行出现内部错误（{exc.__class__.__name__}）。",
                    "retryable": True,
                },
                stop_recipe=True,
                recovery=invocation.spec.recovery,
            )

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = parse_commute_request(
            invocation.inputs.user_content,
            prior_context=self._prior_context,
            pending=self._pending_wait,
            module_context=self._module_context,
        )
        payload: dict[str, Any] = {"analysis": analysis.model_dump(mode="json")}
        if analysis.clarification is not None:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    unconfirmed=[analysis.clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "commute_clarification",
                    "message": analysis.clarification.question,
                    "retryable": False,
                    "node": NODE_PARSE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.DRAFT,
                payload=payload,
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"confidence": analysis.confidence},
        )

    def _run_resolve(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = CommuteRequestAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        deadline = self._external_deadline()
        records: list[ModuleQueryRecord] = []
        origin = PlaceResolution(place=analysis.origin_place)
        if analysis.origin_place is None:
            assert analysis.origin_phrase is not None
            origin = resolve_place(
                CommutePlaceRole.ORIGIN,
                analysis.origin_phrase,
                resolver=BudgetedPlaceResolver(
                    port=self._amap, budget=self._budget, role="origin"
                ),
                account_id=invocation.account_id,
                deadline=deadline,
                stop_event=self._stop_event,
            )
            records.extend(origin.records)
        destination: PlaceResolution | None = None
        if (
            not origin.cancelled
            and origin.clarification is None
            and origin.failure is None
        ):
            if analysis.destination_place is not None:
                destination = PlaceResolution(place=analysis.destination_place)
            else:
                assert analysis.destination_phrase is not None
                destination = resolve_place(
                    CommutePlaceRole.DESTINATION,
                    analysis.destination_phrase,
                    resolver=BudgetedPlaceResolver(
                        port=self._amap, budget=self._budget, role="destination"
                    ),
                    account_id=invocation.account_id,
                    deadline=deadline,
                    stop_event=self._stop_event,
                )
                records.extend(destination.records)

        payload: dict[str, Any] = {
            "origin": origin.place.model_dump(mode="json") if origin.place else None,
            "destination": (
                destination.place.model_dump(mode="json")
                if destination is not None and destination.place is not None
                else None
            ),
            "queries": [record.model_dump(mode="json") for record in records],
            "clarification": None,
            "origin_candidate_query": origin.used_query,
            "destination_candidate_query": (
                destination.used_query if destination is not None else None
            ),
        }
        if origin.cancelled or (destination is not None and destination.cancelled):
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_RESOLVE,
                verdict=QualityVerdict.INVALIDATED,
                status=NodeReceiptStatus.FAILED,
                code="amap_cancelled",
                message="用户停止了本轮地点解析。",
                retryable=True,
                stopped=True,
            )
        failure = origin.failure or (
            destination.failure if destination is not None else None
        )
        if failure is not None:
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_RESOLVE,
                verdict=(
                    QualityVerdict.REPAIRABLE_FAILURE
                    if failure.retryable
                    else QualityVerdict.BLOCKED
                ),
                status=NodeReceiptStatus.FAILED,
                code=failure.error_code or "amap_place_failed",
                message=failure.error_message or "高德地点检索失败，请稍后重试。",
                retryable=failure.retryable,
            )
        clarification = origin.clarification or (
            destination.clarification if destination is not None else None
        )
        if clarification is not None:
            payload["clarification"] = clarification.model_dump(mode="json")
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    source_refs=[record.source for record in records],
                    read_scope="高德校内地点检索",
                    unconfirmed=[clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "commute_clarification",
                    "message": clarification.question,
                    "retryable": False,
                    "node": NODE_RESOLVE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        if origin.place is None or (
            destination is not None and destination.place is None
        ):
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_RESOLVE,
                verdict=QualityVerdict.BLOCKED,
                status=NodeReceiptStatus.BLOCKED,
                code="commute_place_unresolved",
                message="起点或终点尚未定位到真实的校内地点。",
                retryable=False,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[record.source for record in records],
                read_scope="高德校内地点检索",
                requirement_coverage=[
                    {"requirement": "真实校内 POI", "covered": True}
                ],
                unconfirmed=list(origin.place.unverified)
                + (
                    list(destination.place.unverified)
                    if destination is not None and destination.place is not None
                    else []
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_validate(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = CommuteRequestAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        resolve_payload = self._dep(invocation, NODE_RESOLVE)
        origin = _place(resolve_payload.get("origin"))
        destination = _place(resolve_payload.get("destination"))
        mode = analysis.mode
        payload: dict[str, Any] = {
            "origin": origin.model_dump(mode="json") if origin else None,
            "destination": destination.model_dump(mode="json") if destination else None,
            "mode": mode.value if mode else None,
        }
        if origin is None or destination is None or mode is None:
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_VALIDATE,
                verdict=QualityVerdict.BLOCKED,
                status=NodeReceiptStatus.BLOCKED,
                code="commute_request_incomplete",
                message="起点、终点或方式尚未确定，无法规划路线。",
                retryable=False,
            )
        if origin.location and origin.location == destination.location:
            clarification = CommuteClarification(
                question=(
                    f"起点和终点都定位到「{origin.name}」同一个地点，无法规划路线；"
                    "请告诉我另一个校内地点（例如「图书馆」「南门」）？"
                ),
                missing="destination",
                role=CommutePlaceRole.DESTINATION,
            )
            payload["clarification"] = clarification.model_dump(mode="json")
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    unconfirmed=[clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "commute_clarification",
                    "message": clarification.question,
                    "retryable": False,
                    "node": NODE_VALIDATE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        if not origin.campus_verified or not destination.campus_verified:
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_VALIDATE,
                verdict=QualityVerdict.BLOCKED,
                status=NodeReceiptStatus.BLOCKED,
                code="commute_scope_unverified",
                message="起点或终点未核实为华东交通大学校内地点，不规划校外路线。",
                retryable=False,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=["amap_place"],
                read_scope="校内范围与方式校验",
                requirement_coverage=[
                    {"requirement": "校内范围", "covered": True},
                    {"requirement": "步行/自行车/电动车", "covered": mode is not None},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            # 方式或地点确定后，旧的路线/时间/呈现产物按输入键失效；
            # 定位产物不在失效列表，因此方式变更复用定位。
            invalidate_nodes=(NODE_VALIDATE, *ROUTE_DEPENDENT_NODES),
        )

    def _run_request(self, invocation: NodeInvocation) -> NodeExecution:
        validate = self._dep(invocation, NODE_VALIDATE)
        origin = _place(validate.get("origin"))
        destination = _place(validate.get("destination"))
        mode = CommuteMode(str(validate.get("mode")))
        assert origin is not None and destination is not None
        call_key = (
            f"commute.route:{mode.value}:"
            f"{_digest([origin.location, destination.location])[:16]}"
        )
        if self._budget is not None and not self._budget.register_external(
            call_key, purpose="commute.route"
        ):
            return self._failure_execution(
                invocation,
                payload={"mode": mode.value, "path": None, "record": None},
                node=NODE_REQUEST,
                verdict=QualityVerdict.BLOCKED,
                status=NodeReceiptStatus.BLOCKED,
                code="run_budget_exhausted",
                message="本轮运行预算已用尽，未发起新的路线请求。",
                retryable=False,
            )
        outcome_code = "exception"
        try:
            outcome = self._amap.route(
                invocation.account_id,
                mode,
                origin=origin.location,
                destination=destination.location,
                origin_name=origin.name,
                destination_name=destination.name,
                deadline=self._external_deadline(),
                stop_event=self._stop_event,
            )
            outcome_code = (
                outcome.record.status.value if outcome.record is not None else "empty"
            )
        finally:
            if self._budget is not None:
                with contextlib.suppress(Exception):  # 释放失败不改变真实结果
                    self._budget.release_external(call_key, outcome_code=outcome_code)
        record = outcome.record
        payload: dict[str, Any] = {
            "mode": mode.value,
            "query": outcome.query,
            "path": None,
            "record": record.model_dump(mode="json") if record is not None else None,
        }
        if record is not None and record.status is ModuleQueryStatus.CANCELLED:
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_REQUEST,
                verdict=QualityVerdict.INVALIDATED,
                status=NodeReceiptStatus.FAILED,
                code="amap_cancelled",
                message="用户停止了本轮路线查询。",
                retryable=True,
                stopped=True,
            )
        if outcome.path is None:
            return self._failure_execution(
                invocation,
                payload=payload,
                node=NODE_REQUEST,
                verdict=(
                    QualityVerdict.REPAIRABLE_FAILURE
                    if record is None or record.retryable
                    else QualityVerdict.BLOCKED
                ),
                status=NodeReceiptStatus.FAILED,
                code=(record.error_code if record is not None else None)
                or "amap_route_unavailable",
                message=(
                    record.error_message
                    if record is not None and record.error_message
                    else f"高德没有返回{MODE_LABELS[mode]}的可用路线。"
                ),
                retryable=record.retryable if record is not None else True,
            )
        path = outcome.path
        payload["path"] = {
            "distance_m": path.distance_m,
            "duration_seconds": path.duration_seconds,
            "steps": [
                {
                    "instruction": step.instruction,
                    "road_name": step.road_name,
                    "distance_m": step.distance_m,
                }
                for step in path.steps
            ],
            "polyline": list(path.polyline),
            "snapped_origin": path.snapped_origin,
            "snapped_destination": path.snapped_destination,
            "plan_count": path.plan_count,
        }
        path_verified = len(path.polyline) >= 2
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if path_verified
                    else ArtifactTrust.EVIDENCE_BOUND
                ),
                payload=payload,
                source_refs=[record.source] if record is not None else [],
                read_scope=f"高德{MODE_LABELS[mode]}路线规划",
                requirement_coverage=[
                    {
                        "requirement": f"{MODE_LABELS[mode]}真实路线",
                        "covered": True,
                    }
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"path_verified": path_verified},
        )

    def _run_buffer(self, invocation: NodeInvocation) -> NodeExecution:
        buffer = evaluate_break_buffer(now=self._solve_time)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.QUALIFIED,
                payload={
                    "buffer": buffer.model_dump(mode="json"),
                    "snapshot": self.buffer_snapshot,
                },
                read_scope="Asia/Shanghai 求解时间快照",
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = CommuteRequestAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        validate = self._dep(invocation, NODE_VALIDATE)
        request_payload = self._dep(invocation, NODE_REQUEST)
        buffer_payload = self._dep(invocation, NODE_BUFFER)["buffer"]
        origin = _place(validate.get("origin"))
        destination = _place(validate.get("destination"))
        mode = CommuteMode(str(validate.get("mode")))
        path = request_payload.get("path") or {}
        steps = [
            {
                "index": index,
                "instruction": step["instruction"],
                "road_name": step.get("road_name"),
                "distance_m": step.get("distance_m"),
            }
            for index, step in enumerate(path.get("steps", []), start=1)
        ]
        polyline = list(path.get("polyline") or [])
        path_verified = len(polyline) >= 2
        limitations, snap_far = self._limitations(
            analysis=analysis,
            origin=origin,
            destination=destination,
            mode=mode,
            request_payload=request_payload,
        )
        if not path_verified:
            limitations.insert(
                0,
                "高德未返回路径点，本轮不绘制任何路线线，只展示已证实的地点、距离与耗时。",
            )
        distance = int(path.get("distance_m") or 0)
        duration = int(path.get("duration_seconds") or 0)
        added = int(buffer_payload.get("added_minutes") or 0)
        payload: dict[str, Any] = {
            "mode": mode.value,
            "distance_m": distance,
            "base_duration_seconds": duration,
            "suggested_total_seconds": duration + added * 60,
            "steps": steps,
            "polyline": polyline,
            "path_verified": path_verified,
            "snap_far": snap_far,
            "limitations": limitations,
            "buffer": buffer_payload,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if path_verified
                    else ArtifactTrust.EVIDENCE_BOUND
                ),
                payload=payload,
                source_refs=["amap_place", "amap_route"],
                read_scope="路线结果与地图文字一致性核验",
                requirement_coverage=[
                    {"requirement": "距离/时间单位", "covered": distance > 0 and duration > 0},
                    {"requirement": "地图与文字一致", "covered": True},
                ],
                unconfirmed=[] if path_verified else ["未取得路径点，地图不绘制路线线"],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_present(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = CommuteRequestAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )
        resolve_payload = self._dep(invocation, NODE_RESOLVE)
        verify = self._dep(invocation, NODE_VERIFY)
        origin = _place(resolve_payload.get("origin"))
        destination = _place(resolve_payload.get("destination"))
        mode = CommuteMode(str(verify.get("mode")))
        queries = [
            ModuleQueryRecord.model_validate(item)
            for item in resolve_payload.get("queries", [])
        ]
        record = self._dep(invocation, NODE_REQUEST).get("record")
        if record is not None:
            queries.append(ModuleQueryRecord.model_validate(record))
        projection = {
            "status": (
                CommuteRouteStatus.SUCCESS.value
                if verify.get("path_verified")
                else CommuteRouteStatus.UNVERIFIED.value
            ),
            "mode": mode.value,
            "mode_label": MODE_LABELS[mode],
            "mode_phrase": analysis.mode_phrase,
            "origin": origin.model_dump(mode="json") if origin else None,
            "destination": destination.model_dump(mode="json") if destination else None,
            "distance_m": verify.get("distance_m"),
            "base_duration_seconds": verify.get("base_duration_seconds"),
            "suggested_total_seconds": verify.get("suggested_total_seconds"),
            "steps": verify.get("steps", []),
            "polyline": verify.get("polyline", []),
            "path_verified": bool(verify.get("path_verified")),
            "buffer": verify.get("buffer"),
            "queries": [item.model_dump(mode="json") for item in queries],
            "evidence_notes": verify.get("limitations", []),
            "pending": None,
            "resolved_at": self._solve_time.isoformat(),
            "error_code": None,
            "error_message": None,
            "retryable": False,
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=(
                    ArtifactTrust.QUALIFIED
                    if verify.get("path_verified")
                    else ArtifactTrust.EVIDENCE_BOUND
                ),
                payload={"projection": projection},
                source_refs=["amap_place", "amap_route"],
                read_scope="待交付的通勤结果投影",
                requirement_coverage=[
                    {"requirement": "待交付产物", "covered": True}
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    # -- 内部工具 ---------------------------------------------------------

    def _dep(self, invocation: NodeInvocation, node: str) -> dict[str, Any]:
        return dict(invocation.dependencies[node].payload)

    def _artifact(
        self,
        invocation: NodeInvocation,
        *,
        trust_state: ArtifactTrust,
        payload: dict[str, Any],
        source_refs: Sequence[str] = (),
        read_scope: str = "",
        requirement_coverage: Sequence[Mapping[str, Any]] = (),
        unconfirmed: Sequence[str] = (),
        error: dict[str, Any] | None = None,
    ) -> NodeArtifact:
        inputs = invocation.inputs
        dependencies = tuple(
            InputDependency(
                node=name,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for name, artifact in invocation.dependencies.items()
        )
        return NodeArtifact.build(
            account_id=inputs.account_id,
            conversation_id=inputs.conversation_id,
            run_id=inputs.run_id,
            task_id=inputs.task_id,
            task_version=inputs.task_version,
            recipe_id=COMMUTE_RECIPE_ID,
            recipe_version=COMMUTE_RECIPE_VERSION,
            node=invocation.spec.name,
            artifact_type=invocation.spec.artifact_type,
            capability_version=invocation.spec.capability_version,
            trust_state=trust_state,
            input_key=invocation.spec.input_key(inputs),
            input_deps=dependencies,
            source_refs=tuple(source_refs),
            read_scope=read_scope,
            requirement_coverage=tuple(dict(item) for item in requirement_coverage),
            unconfirmed=tuple(unconfirmed),
            error=error,
            payload=payload,
            now=self._clock(),
        )

    def _failure_execution(
        self,
        invocation: NodeInvocation,
        *,
        payload: dict[str, Any],
        node: str,
        verdict: QualityVerdict,
        status: NodeReceiptStatus,
        code: str,
        message: str,
        retryable: bool,
        stopped: bool = False,
    ) -> NodeExecution:
        artifact = self._artifact(
            invocation,
            trust_state=ArtifactTrust.INVALIDATED,
            payload=payload,
            error={"code": code, "message": message, "retryable": retryable},
        )
        return NodeExecution(
            artifact=artifact,
            verdict=verdict,
            status=status,
            detail={
                "code": code,
                "message": message,
                "retryable": retryable,
                "node": node,
                "stopped": stopped,
            },
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )

    def _external_deadline(self) -> float:
        remaining = self._deadline_seconds
        if self._budget is not None:
            remaining = min(remaining, self._budget.deadline_seconds())
        return time.monotonic() + remaining

    def _limitations(
        self,
        *,
        analysis: CommuteRequestAnalysis,
        origin: CommutePlace | None,
        destination: CommutePlace | None,
        mode: CommuteMode,
        request_payload: Mapping[str, Any],
    ) -> tuple[list[str], bool]:
        """证据边界：方式隔离、吸附距离、候选沿用与实测缺口。"""
        path = request_payload.get("path") or {}
        notes = [
            f"距离与耗时来自高德{MODE_LABELS[mode]}路线接口，不与其他方式混用。",
            f"地点坐标来自高德 POI 检索（起点实际检索词："
            f"{origin.query if origin else '未定位'}；终点实际检索词："
            f"{destination.query if destination else '未定位'}）。",
            f"路线方案数 {path.get('plan_count', 0)}，采用高德返回的第 1 条。",
            FIELD_TEST_LIMITATION,
        ]
        snap_far = False
        for place in (origin, destination):
            if place is None:
                continue
            snapped = (
                path.get("snapped_origin")
                if place.role is CommutePlaceRole.ORIGIN
                else path.get("snapped_destination")
            )
            distance = coordinates_distance_meters(place.location, snapped)
            if distance is not None and distance > SNAP_LIMIT_METERS:
                snap_far = True
                label = "起点" if place.role is CommutePlaceRole.ORIGIN else "终点"
                notes.append(
                    f"高德把{label}吸附到约 {round(distance)} 米外的道路点"
                    f"（原 POI 坐标 {place.location}），该楼门可能不在可通行道路旁，"
                    f"路线端点以高德吸附结果为准（{MODE_LABELS[mode]}）。"
                )
        if analysis.origin_place is not None and origin is not None:
            notes.append(
                f"起点「{origin.name}」沿用上一轮候选选择，坐标来自当时的高德 POI 返回。"
            )
        if analysis.destination_place is not None and destination is not None:
            notes.append(
                f"终点「{destination.name}」沿用上一轮候选选择，坐标来自当时的高德 POI 返回。"
            )
        return notes, snap_far


