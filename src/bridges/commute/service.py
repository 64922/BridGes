"""通勤子图编排：``route.parse → route.resolve → route.request → route.buffer → route.present``。

本切片复用论文模块（Issue 11）建立的同一套合同，不另起一套语义：

1. **证据合同**：每次高德调用产出 ``ModuleQueryRecord``（查询/证据/时间/错误），
   外部只发送最小检索词与坐标，用户私有材料不进请求；
2. **等待合同**：缺失或含糊只问一项，提问随助手消息落库（``ModuleWaitState``），
   下一轮从该处恢复并读取权威记录与实际回复，不靠内存协程跨请求存活；
3. **失败与停止合同**：查询有超时、有限重试与取消；失败保留实际检索词与真实
   分类、可重试；停止在节点边界生效并把状态写回同一条消息。

与论文模块的差异只有一个：通勤**不调用模型**，正文完全由高德返回的证据渲染，
因此不存在用模型记忆补路线的路径（``run_model_id`` 仅保持调用签名一致）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

from bridges.commute.buffer import evaluate_break_buffer
from bridges.commute.contracts import (
    MISSING_DESTINATION_CHOICE,
    MISSING_ORIGIN_CHOICE,
    MODE_LABELS,
    CommuteClarification,
    CommuteMode,
    CommutePlace,
    CommutePlaceRole,
    CommuteRequestAnalysis,
    CommuteRouteProjection,
    CommuteRouteStatus,
    CommuteRouteStep,
)
from bridges.commute.parsing import parse_commute_request, pending_payload
from bridges.commute.presenting import status_content
from bridges.commute.resolving import PlaceResolution, resolve_place, search_queries
from bridges.commute.sources import (
    SNAP_LIMIT_METERS,
    CommuteAmapPort,
    RouteOutcome,
    RoutePath,
    coordinates_distance_meters,
)
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

#: 本轮子图节点名（进度事件与失败定位使用；父图节点仍是 invoke_subgraph_or_chat）。
NODE_PARSE = "route.parse"
NODE_RESOLVE = "route.resolve"
NODE_REQUEST = "route.request"
NODE_BUFFER = "route.buffer"
NODE_PRESENT = "route.present"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
COMMUTE_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解通勤请求",
    NODE_RESOLVE: "定位起终点",
    NODE_REQUEST: "查询高德路线",
    NODE_BUFFER: "计算课间缓冲",
    NODE_PRESENT: "整理路线结果",
}

#: 单轮外部调用的墙钟预算（秒）：超时如实失败，绝不无限等待上游。
ROUTE_DEADLINE_SECONDS = 20.0

#: 澄清等待状态的类型标识（等待合同的一部分）。
WAIT_KIND_CLARIFICATION = "clarification"

COMMUTE_MODULE_ID = "commute"

#: 父图运行表的等待原因（可观测的持久化等待状态）。
WAIT_REASON_CLARIFICATION = "commute_clarification"

#: 未实测校内道路与楼门可通行性时的固定局限说明（随每条结果展示）。
FIELD_TEST_LIMITATION = (
    "校内小路与楼门可通行性需按代表性地点实测；本轮结论只依据高德返回的道路结果。"
)


class CommuteModuleError(Exception):
    """通勤子图失败：节点位置、稳定错误码、可操作中文说明与是否可重试。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class CommuteRunOutcome:
    """一轮通勤模块的收敛结果（父图据此写等待原因与判断终态）。"""

    status: CommuteRouteStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)


class CommuteService:
    """校园通勤模块编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        amap: CommuteAmapPort,
        clock: Callable[[], datetime] | None = None,
        deadline_seconds: float = ROUTE_DEADLINE_SECONDS,
    ) -> None:
        self._amap = amap
        self._clock = clock or (lambda: datetime.now(UTC))
        self._deadline_seconds = deadline_seconds

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        run_model_id: str | None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
    ) -> CommuteRunOutcome:
        """执行一轮通勤模块；终态（完成/澄清/失败/停止）全部写回同一消息。"""
        # 通勤不调用模型：本轮模型锁与上下文编译与本模块无关（签名保持一致，
        # 父图因此可以用同一段派发代码调用六个模块）。
        del run_context, run_model_id
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise CommuteModuleError(
                NODE_PARSE, "message_not_found", "消息不存在或没有访问权限。", retryable=False
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_user_messages(repo, account_id, conversation_id, user_message_id)
        progress = _Run(emit_node)
        analysis = progress.node(
            NODE_PARSE,
            lambda: parse_commute_request(
                user_message.content, prior_context=prior, pending=pending
            ),
        )
        if analysis.clarification is not None:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=analysis,
                clarification=analysis.clarification,
            )
        deadline = time.monotonic() + self._deadline_seconds
        origin_resolution, destination_resolution = progress.node(
            NODE_RESOLVE,
            lambda: self._locate(
                account_id=account_id,
                analysis=analysis,
                deadline=deadline,
                stop_event=stop_event,
            ),
        )
        records = list(origin_resolution.records)
        if origin_resolution.cancelled or (
            destination_resolution is not None and destination_resolution.cancelled
        ):
            return self._persist_stopped(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=analysis,
                origin=origin_resolution.place,
                destination=(
                    destination_resolution.place
                    if destination_resolution is not None
                    else analysis.destination_place
                ),
                queries=records,
            )
        failure = origin_resolution.failure or (
            destination_resolution.failure if destination_resolution is not None else None
        )
        if failure is not None:
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_RESOLVE,
                analysis=analysis,
                queries=records,
                origin=origin_resolution.place,
                code=failure.error_code or "amap_place_failed",
                message=failure.error_message or "高德地点检索失败，请稍后重试。",
                retryable=failure.retryable,
            )
        if origin_resolution.clarification is not None:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=analysis,
                clarification=origin_resolution.clarification,
                queries=records,
            )
        origin = origin_resolution.place
        destination = analysis.destination_place
        if destination_resolution is not None:
            records.extend(destination_resolution.records)
            if destination_resolution.clarification is not None:
                return self._persist_clarification(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    analysis=analysis,
                    clarification=destination_resolution.clarification,
                    origin=origin,
                    queries=records,
                )
            destination = destination_resolution.place
        mode = analysis.mode
        if origin is None or destination is None or mode is None:
            # 结构上不可达：缺项在解析阶段就已澄清。保守收敛为失败而不是猜。
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_RESOLVE,
                analysis=analysis,
                queries=records,
                code="commute_request_incomplete",
                message="起点、终点或方式尚未确定，无法规划路线。",
                retryable=False,
            )
        assert origin is not None and destination is not None and mode is not None
        if origin.location and origin.location == destination.location:
            return self._persist_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                analysis=analysis,
                clarification=CommuteClarification(
                    question=(
                        f"起点和终点都定位到「{origin.name}」同一个地点，无法规划路线；"
                        "请告诉我另一个校内地点（例如「图书馆」「南门」）？"
                    ),
                    missing="destination",
                    role=CommutePlaceRole.DESTINATION,
                ),
                origin=origin,
                destination=destination,
                queries=records,
            )

        def request() -> RouteOutcome:
            return self._amap.route(
                account_id,
                mode,
                origin=origin.location,
                destination=destination.location,
                origin_name=origin.name,
                destination_name=destination.name,
                deadline=deadline,
                stop_event=stop_event,
            )

        outcome = progress.node(NODE_REQUEST, request)
        record = outcome.record
        if record is not None:
            records.append(record)
            if record.status is ModuleQueryStatus.CANCELLED:
                return self._persist_stopped(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    analysis=analysis,
                    origin=origin,
                    destination=destination,
                    queries=records,
                )
        if outcome.path is None:
            self._fail(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                node=NODE_REQUEST,
                analysis=analysis,
                origin=origin,
                destination=destination,
                queries=records,
                code=(record.error_code if record is not None else None)
                or "amap_route_unavailable",
                message=(
                    record.error_message
                    if record is not None and record.error_message
                    else f"高德没有返回{MODE_LABELS[mode]}的可用路线。"
                ),
                retryable=record.retryable if record is not None else True,
            )
        assert outcome.path is not None
        path = outcome.path
        buffer = progress.node(NODE_BUFFER, lambda: evaluate_break_buffer(now=self._clock()))
        notes = self._limitations(
            analysis=analysis, origin=origin, destination=destination, mode=mode, path=path
        )
        suggested_total = path.duration_seconds + buffer.added_minutes * 60
        path_verified = len(path.polyline) >= 2
        if not path_verified:
            notes.insert(
                0,
                "高德未返回路径点，本轮不绘制任何路线线，只展示已证实的地点、距离与耗时。",
            )
        projection = CommuteRouteProjection(
            status=(
                CommuteRouteStatus.SUCCESS
                if path_verified
                else CommuteRouteStatus.UNVERIFIED
            ),
            mode=mode,
            mode_label=MODE_LABELS[mode],
            mode_phrase=analysis.mode_phrase,
            origin=origin,
            destination=destination,
            distance_m=path.distance_m,
            base_duration_seconds=path.duration_seconds,
            suggested_total_seconds=suggested_total,
            steps=[
                CommuteRouteStep(
                    index=index,
                    instruction=step.instruction,
                    road_name=step.road_name,
                    distance_m=step.distance_m,
                )
                for index, step in enumerate(path.steps, start=1)
            ],
            polyline=list(path.polyline),
            path_verified=path_verified,
            buffer=buffer,
            queries=records,
            evidence_notes=notes,
            resolved_at=datetime.now(UTC),
        )
        progress.node(
            NODE_PRESENT,
            lambda: self._finalize(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                status=ChatMessageStatus.DONE,
                projection=projection,
                content=status_content(projection),
                now=datetime.now(UTC),
            ),
        )
        return CommuteRunOutcome(status=projection.status, queries=records)

    # -- 节点实现 --------------------------------------------------------

    def _locate(
        self,
        *,
        account_id: str,
        analysis: CommuteRequestAnalysis,
        deadline: float,
        stop_event: threading.Event | None,
    ) -> tuple[PlaceResolution, PlaceResolution | None]:
        """解析起终点：已选定的一侧直接沿用，未解析的一侧才查高德 POI。"""
        origin = PlaceResolution(place=analysis.origin_place)
        if analysis.origin_place is None:
            assert analysis.origin_phrase is not None
            origin = resolve_place(
                CommutePlaceRole.ORIGIN,
                analysis.origin_phrase,
                resolver=self._amap,
                account_id=account_id,
                deadline=deadline,
                stop_event=stop_event,
            )
        if origin.cancelled or origin.clarification is not None:
            return origin, None
        if analysis.destination_place is not None:
            return origin, PlaceResolution(place=analysis.destination_place)
        assert analysis.destination_phrase is not None
        destination = resolve_place(
            CommutePlaceRole.DESTINATION,
            analysis.destination_phrase,
            resolver=self._amap,
            account_id=account_id,
            deadline=deadline,
            stop_event=stop_event,
        )
        return origin, destination

    def _limitations(
        self,
        *,
        analysis: CommuteRequestAnalysis,
        origin: CommutePlace,
        destination: CommutePlace,
        mode: CommuteMode,
        path: RoutePath,
    ) -> list[str]:
        """证据边界：方式隔离、吸附距离、候选沿用的来源与实测缺口。"""
        notes = [
            f"距离与耗时来自高德{MODE_LABELS[mode]}路线接口，不与其他方式混用。",
            f"地点坐标来自高德 POI 检索（起点检索词：{'、'.join(search_queries(origin.query))}"
            f"；终点检索词：{'、'.join(search_queries(destination.query))}）。",
            f"路线方案数 {path.plan_count}，采用高德返回的第 1 条。",
            FIELD_TEST_LIMITATION,
        ]
        for place in (origin, destination):
            snapped = (
                path.snapped_origin
                if place.role is CommutePlaceRole.ORIGIN
                else path.snapped_destination
            )
            distance = coordinates_distance_meters(place.location, snapped)
            if distance is not None and distance > SNAP_LIMIT_METERS:
                label = "起点" if place.role is CommutePlaceRole.ORIGIN else "终点"
                notes.append(
                    f"高德把{label}吸附到约 {round(distance)} 米外的道路点"
                    f"（原 POI 坐标 {place.location}），该楼门可能不在可通行道路旁，"
                    f"路线端点以高德吸附结果为准（{MODE_LABELS[mode]}）。"
                )
        if analysis.origin_place is not None:
            notes.append(
                f"起点「{origin.name}」沿用上一轮候选选择，坐标来自当时的高德 POI 返回。"
            )
        if analysis.destination_place is not None:
            notes.append(
                f"终点「{destination.name}」沿用上一轮候选选择，坐标来自当时的高德 POI 返回。"
            )
        return notes

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            projection = message.commute_route
            if not isinstance(projection, dict):
                continue
            try:
                stored = CommuteRouteProjection.model_validate(projection)
            except ValueError:
                continue
            if stored.pending is not None:
                return stored.pending
            if stored.status in {
                CommuteRouteStatus.SUCCESS,
                CommuteRouteStatus.UNVERIFIED,
            }:
                # 更晚的完成结果已经取代等待状态：不再恢复。
                return None
        return None

    def _prior_user_messages(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
    ) -> list[str]:
        """已确认的会话前文（最近若干条用户消息），只用于补齐缺失的地点。"""
        prior: list[str] = []
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if message.role.value == "user" and message.content.strip():
                prior.append(message.content)
        return prior[-6:]

    # -- 落库 ------------------------------------------------------------

    def _persist_clarification(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: CommuteRequestAnalysis,
        clarification: CommuteClarification,
        origin: CommutePlace | None = None,
        destination: CommutePlace | None = None,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> CommuteRunOutcome:
        now = datetime.now(UTC)
        origin_candidates = (
            clarification.candidates
            if clarification.missing == MISSING_ORIGIN_CHOICE
            else []
        )
        destination_candidates = (
            clarification.candidates
            if clarification.missing == MISSING_DESTINATION_CHOICE
            else []
        )
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.CLARIFICATION,
            mode=analysis.mode,
            mode_label=MODE_LABELS[analysis.mode] if analysis.mode is not None else None,
            mode_phrase=analysis.mode_phrase,
            origin=origin,
            destination=destination,
            origin_candidates=list(origin_candidates),
            destination_candidates=list(destination_candidates),
            queries=list(queries),
            pending=ModuleWaitState(
                module_id=COMMUTE_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(
                    analysis,
                    awaiting=clarification.missing,
                    origin_candidates=origin_candidates,
                    destination_candidates=destination_candidates,
                ),
                created_at=now,
            ),
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            content=clarification.question,
            now=now,
        )
        return CommuteRunOutcome(
            status=CommuteRouteStatus.CLARIFICATION,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=list(queries),
        )

    def _persist_stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        analysis: CommuteRequestAnalysis,
        origin: CommutePlace | None = None,
        destination: CommutePlace | None = None,
        queries: Sequence[ModuleQueryRecord] = (),
    ) -> CommuteRunOutcome:
        now = datetime.now(UTC)
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.STOPPED,
            mode=analysis.mode,
            mode_label=MODE_LABELS[analysis.mode] if analysis.mode is not None else None,
            mode_phrase=analysis.mode_phrase,
            origin=origin,
            destination=destination,
            queries=list(queries),
            resolved_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            content=status_content(projection),
            now=now,
        )
        return CommuteRunOutcome(
            status=CommuteRouteStatus.STOPPED, queries=list(queries)
        )

    def _fail(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        node: str,
        analysis: CommuteRequestAnalysis,
        queries: Sequence[ModuleQueryRecord] = (),
        origin: CommutePlace | None = None,
        destination: CommutePlace | None = None,
        code: str | None = None,
        message: str | None = None,
        retryable: bool = True,
    ) -> None:
        """写回真实失败状态（实际检索词与分类），再抛出以便父图标注节点位置。"""
        error_code = code or "commute_failed"
        error_message = message or "校园通勤失败，请稍后重试。"
        now = datetime.now(UTC)
        projection = CommuteRouteProjection(
            status=CommuteRouteStatus.ERROR,
            mode=analysis.mode,
            mode_label=MODE_LABELS[analysis.mode] if analysis.mode is not None else None,
            mode_phrase=analysis.mode_phrase,
            origin=origin,
            destination=destination,
            queries=list(queries),
            evidence_notes=self._query_notes(queries),
            resolved_at=now,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )
        repo.update_message_commute_route(
            account_id, assistant_message_id, projection.model_dump(mode="json"), now
        )
        raise CommuteModuleError(node, error_code, error_message, retryable=retryable)

    def _query_notes(self, queries: Sequence[ModuleQueryRecord]) -> list[str]:
        notes = [
            f"{record.source} 查询「{record.query}」：{record.status.value}"
            + (f"（{record.error_message}）" if record.error_message else "")
            for record in queries
        ]
        return notes or ["本轮没有发出可记录的外部查询。"]

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: CommuteRouteProjection,
        content: str,
        now: datetime,
    ) -> None:
        # 正文只能在 streaming 期间写入（流式增量接口的守卫），因此先写正文
        # 再收敛终态；终态与通勤投影在同一事务内提交（finalize_message）。
        if content:
            repo.update_message_content(account_id, assistant_message_id, content, now)
        # 局部导入：模块子图与 chat 服务互相引用（父图调用子图、子图复用消息
        # 终态收敛），模块级导入会形成包级循环。
        from bridges.chat.turn import finalize_message

        finalize_message(
            repo,
            account_id,
            assistant_message_id,
            status=status,
            error_code=projection.error_code,
            error_message=projection.error_message,
            duration_ms=None,
            model_id=None,
            started=time.monotonic(),
            now=now,
            commute_route=projection.model_dump(mode="json"),
        )


T = TypeVar("T")


class _Run:
    """节点进度发射器：只对真实开始/完成的节点发 started/completed 与耗时。"""

    def __init__(self, emit_node: Callable[[str, str, int | None], None]) -> None:
        self._emit = emit_node

    def node(self, name: str, body: Callable[[], T]) -> T:
        self._emit(name, "started", None)
        started = time.monotonic()
        result = body()
        self._emit(name, "completed", max(1, int((time.monotonic() - started) * 1000)))
        return result
