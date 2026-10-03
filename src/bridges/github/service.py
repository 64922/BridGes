"""GitHub 项目推荐子图编排：八节点配方由持久节点内核执行（改进工单 26）。

模块不再把解析、规划、检索、读取、匹配、排序、核验与交付包在一个黑盒函数
里：配方与节点定义在 :mod:`bridges.github.kernel`，每个节点在自己的事务中
提交类型化产物与完成收据；恢复先读收据（检索失败而解析可用时按输入键回填），
限流只保留已完成检查，未读候选不伪装核实。部分失败的检索/读取产物会失效但
保留已取得结果，重试只补缺口。本模块只负责把内核结果翻译成既有消息投影，
并复用既有终态收敛路径统一提交（显式派发时由父图沿真实产物依赖链核验后提交）。

三条核心约束（``docs/v2/workflows.md`` 第 7 节）：

1. 解析保留完整 idea 的核心场景、必要/可选功能与技术/许可/运行限制；优先找整体
   相似的项目，整体不足时才用要点查询补组件候选，并逐条记下它覆盖了哪一部分；
2. README 只是项目自述——没有读取实现文件就不对内部架构作断言；没有读到许可
   文件就不声称代码可自由复用；静态读取不等于实际运行；
3. 上游限流、文件缺失或匹配不足时展示实际结果与局限，并保留可重试状态。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from bridges.ai.model_quota import RunModelQuota
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.contracts.ai import ModelRunLock
from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus, ModuleWaitState
from bridges.contracts.workflows import RunContextEnvelope
from bridges.github.contracts import (
    GithubContextSource,
    GithubEvidenceKind,
    GithubIdeaAnalysis,
    GithubProjectsProjection,
    GithubProjectStatus,
    GithubRateLimitState,
    GithubRecommendation,
    GithubRejectedRepository,
    GithubRequirementInput,
)
from bridges.github.inspecting import GithubRepositoryReader
from bridges.github.kernel import (
    GITHUB_GATE_HANDLERS,
    GITHUB_NODE_LABELS,
    GITHUB_RECIPE_ID,
    GITHUB_RECIPE_VERSION,
    INSPECT_DEADLINE_SECONDS,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PRESENT,
    NODE_SEARCH,
    NODE_VERIFY,
    SEARCH_DEADLINE_SECONDS,
    GithubNodeFlow,
    build_github_recipe,
    github_recipe_registry,
)
from bridges.github.parsing import pending_payload
from bridges.github.presenting import (
    GithubInsightGenerator,
    InsightOutcome,
    rate_limit_recovery_note,
    render_clarification_content,
    render_empty_content,
    render_result_content,
    render_stopped_content,
)
from bridges.github.ranking import RankOutcome
from bridges.github.searching import FAILED_QUERY_STATUSES, GithubSearchPort
from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelResult,
    KernelStatus,
    NodeArtifact,
    NodeReceiptStatus,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository

if TYPE_CHECKING:
    from bridges.chat.global_writing_policy import GlobalWritingPolicySnapshot
    from bridges.chat.repository import ConversationRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 兼容旧引用：节点常量与中文标签现由内核层提供。
NODE_INSPECT = "github.read"
NODE_RANK = "github.evaluate"

#: 模块标识与等待原因（父图与前端都依赖）。
GITHUB_MODULE_ID = "github"
WAIT_KIND_CLARIFICATION = "clarification"
WAIT_REASON_CLARIFICATION = "github_clarification"

#: 前文回看的用户消息条数上限（够用即止，不把整段历史塞进解析）。
PRIOR_MESSAGES_LOOKBACK = 8


class GithubModuleError(Exception):
    """模块内的稳定失败（定位到具体节点）。"""

    def __init__(
        self, node: str, code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable


class GithubSupersededError(Exception):
    """迟到结果：租约/版本/消息归属已变化，本轮不写任何交付终态。"""


class GithubDelivery(BaseModel):
    """交给父图核验与提交的领域交付，检查点只保存可序列化数据。"""

    projection: GithubProjectsProjection
    content: str
    message_status: ChatMessageStatus
    lock: ModelRunLock | None = None
    verification_artifact_id: str | None = None
    present_artifact_id: str | None = None
    lease_owner: str | None = None
    task_ref: tuple[str | None, int | None] | None = None
    error_node: str | None = None


@dataclass(frozen=True)
class GithubRunOutcome:
    """一轮 GitHub 项目推荐的收敛结果（父图据此写等待原因与判断终态）。"""

    status: GithubProjectStatus
    wait_reason: str | None = None
    queries: list[ModuleQueryRecord] = field(default_factory=list)
    delivery: GithubDelivery | None = None


class GithubProjectsService:
    """GitHub 项目推荐编排服务（由日常父图在显式派发时调用）。"""

    def __init__(
        self,
        *,
        search: GithubSearchPort,
        reader: GithubRepositoryReader,
        insights: GithubInsightGenerator | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        inspect_deadline_seconds: float = INSPECT_DEADLINE_SECONDS,
        task_version_provider: Callable[
            [str, str], tuple[str | None, int | None] | None
        ]
        | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._insights = insights
        self._search_deadline_seconds = search_deadline_seconds
        self._inspect_deadline_seconds = inspect_deadline_seconds
        self._task_version_provider = task_version_provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._registry = github_recipe_registry()
        self._recipe = build_github_recipe()

    # -- 对外入口 --------------------------------------------------------

    def run(
        self,
        *,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope | None = None,
        run_model_id: str | None = None,
        emit_node: Callable[[str, str, int | None], None],
        stop_event: threading.Event | None,
        module_context: ModuleTaskContext | None = None,
        model_quota: RunModelQuota | None = None,
        manifest_sink: Callable[[CallMaterialManifest], None] | None = None,
        requirement: GithubRequirementInput | None = None,
        writing_policy: GlobalWritingPolicySnapshot | None = None,
        defer_finalization: bool = False,
    ) -> GithubRunOutcome:
        """执行一轮 GitHub 项目推荐：持久节点内核执行，结果统一提交回同一消息。

        ``module_context``（工单 15）提供当前任务主题（只含有效条件与有来源的
        前文）；``requirement``（工单 26）是既定的类型化需求产物（选定论文标识
        或岗位需求），跨模块触发由工单 37 校验；``model_quota``/``manifest_sink``
        让借鉴角度调用进入同一最终载荷门并把采用清单交给父图审计。
        """
        user_message = repo.get_message(account_id, user_message_id)
        if user_message is None:
            raise GithubModuleError(
                NODE_PARSE,
                "message_not_found",
                "消息不存在或没有访问权限。",
                retryable=False,
            )
        if run_context is None:
            raise GithubModuleError(
                NODE_PARSE,
                "run_context_missing",
                "本轮运行上下文缺失，未执行 GitHub 项目推荐。",
                retryable=True,
            )
        pending = self._pending_wait(repo, account_id, conversation_id)
        prior = self._prior_context(
            repo,
            account_id,
            conversation_id,
            user_message_id,
            module_context=module_context,
        )
        run_id = run_context.run_id
        task_ref = self._current_task_ref(account_id, conversation_id)
        from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
        from bridges.chat.lightweight_policy import ToolOutcome, continuation_source_text

        run = repo.get_run_by_message(account_id, assistant_message_id)
        existing_policy = (run.config or {}).get("global_writing_policy") if run else None
        conversation = repo.get_conversation(account_id, conversation_id)
        writing_policy = GlobalWritingPolicyCompiler().compile(
            conversation.mode if conversation else "companion",
            user_text=user_message.content,
            continuation_text=continuation_source_text(
                repo.list_messages(account_id, conversation_id), user_message_id
            ),
            tool_outcome=ToolOutcome.NONE,
            existing_snapshot=writing_policy or existing_policy,
        )
        if run is not None:
            config = dict(run.config or {})
            config["global_writing_policy"] = writing_policy.model_dump(mode="json")
            repo.update_generation_config(account_id, run_id, config)
        guard = RunCommitGuard(
            repo,
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            task_ref=task_ref,
            task_version_provider=self._task_version_provider,
            stop_event=stop_event,
            clock=self._clock,
        )
        flow = GithubNodeFlow(
            search=self._search,
            reader=self._reader,
            clock=self._clock,
            prior_context=prior,
            module_context=module_context,
            pending_wait=pending,
            requirement=requirement,
            stop_event=stop_event,
            search_deadline_seconds=self._search_deadline_seconds,
            inspect_deadline_seconds=self._inspect_deadline_seconds,
            insights=self._insights,
            run_context=run_context,
            run_model_id=run_model_id,
            model_quota=model_quota,
            manifest_sink=manifest_sink,
            writing_policy=writing_policy,
            repository=NodeKernelRepository(repo.database),
        )
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(repo.database),
            guard=guard,
            gates=GITHUB_GATE_HANDLERS,
            runner=flow.run_node,
            clock=self._clock,
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
                user_message_id=user_message_id,
                user_content=user_message.content,
                task_id=task_ref[0] if task_ref is not None else None,
                task_version=task_ref[1] if task_ref is not None else None,
                wait_identity=self._wait_identity(pending),
                artifacts={},
                prior_digest=flow.prior_digest,
            ),
            event_sink=emit_node,
            stop_event=stop_event,
        )
        # 最终消息写入与守卫复核共用写事务，拒绝核验后转租约或改版本的结果。
        with NodeKernelRepository(repo.database).transaction():
            decision = guard.verify()
            if not decision.ok:
                if decision.code != "run_stopped":
                    raise GithubSupersededError(decision.code)
                result = replace(result, status=KernelStatus.STOPPED)
            if defer_finalization:
                return self._prepare_delivery(
                    result, flow, assistant_message_id=assistant_message_id,
                    lease_owner=guard.lease_owner, task_ref=task_ref,
                )
            try:
                return self._deliver(
                    repo,
                    account_id=account_id,
                    assistant_message_id=assistant_message_id,
                    result=result,
                    flow=flow,
                    stop_event=stop_event,
                )
            except GithubModuleError as error:
                # 失败投影提交后，再通知父图收敛错误，避免异常回滚投影。
                delivery_error = error
        raise delivery_error

    def _prepare_delivery(
        self,
        result: KernelResult,
        flow: GithubNodeFlow,
        *,
        assistant_message_id: str,
        lease_owner: str | None,
        task_ref: tuple[str | None, int | None] | None,
    ) -> GithubRunOutcome:
        """生产父图路径只构造交付；不在模块内写助手消息终态。"""
        analysis = _analysis_of(result) or _empty_analysis()
        insights = _insights_of(result, flow)
        wait_reason = None
        error_node = None
        if result.status is KernelStatus.COMPLETED:
            projection = self._projection(result=result, insights=insights)
            message_status = (
                ChatMessageStatus.ERROR if projection.status is GithubProjectStatus.ERROR
                else ChatMessageStatus.DONE
            )
            content = (
                render_result_content(projection) if projection.recommendations
                else render_empty_content(projection)
            )
            if message_status is ChatMessageStatus.ERROR:
                error_node = NODE_SEARCH
        elif result.status is KernelStatus.NEEDS_INPUT and analysis.clarification is not None:
            projection = _base_projection(
                analysis,
                status=GithubProjectStatus.CLARIFICATION,
                pending=ModuleWaitState(
                    module_id=GITHUB_MODULE_ID, kind=WAIT_KIND_CLARIFICATION,
                    question=analysis.clarification.question,
                    origin_message_id=assistant_message_id,
                    context=pending_payload(analysis), created_at=self._clock(),
                ), completed_at=self._clock(),
            )
            wait_reason = WAIT_REASON_CLARIFICATION
            message_status = ChatMessageStatus.DONE
            content = render_clarification_content(projection)
        elif result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED}:
            projection = _base_projection(
                analysis, status=GithubProjectStatus.STOPPED,
                queries=_records_of(result),
                evidence_boundary=["你已停止本轮推荐，未继续读取仓库证据。"],
                completed_at=self._clock(),
            )
            message_status = ChatMessageStatus.STOPPED
            content = render_stopped_content(projection)
        elif result.status is KernelStatus.REJECTED:
            raise GithubSupersededError(result.rejection_code or "generation_superseded")
        else:
            failure = result.failure
            error_node = failure.node if failure else NODE_SEARCH
            projection = _base_projection(
                analysis, status=GithubProjectStatus.ERROR, queries=_records_of(result),
                completed_at=self._clock(),
                error_code=(failure.code if failure else "") or "github_failed",
                error_message=(failure.message if failure else "")
                or "GitHub 项目推荐失败，请稍后重试。",
                retryable=failure.retryable if failure else True,
            )
            message_status = ChatMessageStatus.ERROR
            content = render_empty_content(projection)
        verification = result.artifact(NODE_VERIFY)
        present = result.artifact(NODE_PRESENT)
        return GithubRunOutcome(
            status=projection.status, wait_reason=wait_reason, queries=list(projection.queries),
            delivery=GithubDelivery(
                projection=projection, content=content, message_status=message_status,
                lock=insights.lock,
                verification_artifact_id=verification.artifact_id if verification else None,
                present_artifact_id=present.artifact_id if present else None,
                lease_owner=lease_owner, task_ref=task_ref, error_node=error_node,
            ),
        )

    def verify_delivery_scope(
        self, repo: ConversationRepository, delivery: GithubDelivery,
        *, account_id: str, run_id: str, conversation_id: str,
        assistant_message_id: str, stop_event: threading.Event | None,
        expected_lease_owner: str | None,
    ) -> None:
        """父图提交事务内复核原执行租约、当前任务版本及停止状态。"""
        run = repo.get_generation_run(account_id, run_id)
        if run is None or run.lease_owner != expected_lease_owner:
            raise GithubSupersededError("lease_lost")
        if self._current_task_ref(account_id, conversation_id) != delivery.task_ref:
            raise GithubSupersededError("task_version_changed")
        guard = RunCommitGuard(
            repo, account_id=account_id, run_id=run_id, conversation_id=conversation_id,
            assistant_message_id=assistant_message_id, task_ref=delivery.task_ref,
            task_version_provider=self._task_version_provider, stop_event=stop_event,
            clock=self._clock,
        )
        guard.capture()
        decision = guard.verify()
        if not decision.ok:
            raise GithubSupersededError(decision.code)

    def verify_delivery_projection(
        self, repo: ConversationRepository, delivery: GithubDelivery,
        *, account_id: str, run_id: str, conversation_id: str,
    ) -> bool:
        """从真实交付产物的依赖链重建投影，逐字段核对交付内容。

        交付可以复用上一轮已完成的产物（重试只补缺口），因此绑定的是**产出
        交付产物那条依赖链**：链上每个节点必须是配方登记的节点，配方/能力/
        任务引用/会话吻合，依赖集合与配方声明一致，上游产物 ID 与内容哈希
        逐一吻合且通过自校验；交付引用的核验产物必须是链上同一节点。若本轮
        真实执行过节点（存在完成收据），其结果必须与链上同节点内容一致——
        旧核验不能为新任务或新证据背书。
        """
        repository = NodeKernelRepository(repo.database)
        present = repository.get_artifact(account_id, delivery.present_artifact_id or "")
        spec_by_node = {spec.name: spec for spec in self._recipe.nodes}
        chain: dict[str, NodeArtifact] = {}

        def walk(artifact: NodeArtifact) -> bool:
            spec = spec_by_node.get(artifact.node)
            if (
                spec is None
                or artifact.recipe_id != GITHUB_RECIPE_ID
                or artifact.recipe_version != GITHUB_RECIPE_VERSION
                or artifact.artifact_type != spec.artifact_type
                or artifact.capability_version != spec.capability_version
                or artifact.conversation_id != conversation_id
                or not artifact.verify_hash()
            ):
                return False
            if (artifact.task_id, artifact.task_version) != (
                delivery.task_ref or (None, None)
            ):
                return False
            existing = chain.get(artifact.node)
            if existing is not None:
                return existing.artifact_id == artifact.artifact_id
            chain[artifact.node] = artifact
            if {dependency.node for dependency in artifact.input_deps} != set(
                spec.depends_on
            ):
                return False
            for dependency in artifact.input_deps:
                upstream = repository.get_artifact(
                    account_id, dependency.artifact_id or ""
                )
                if (
                    upstream is None
                    or upstream.node != dependency.node
                    or upstream.content_hash != dependency.content_hash
                ):
                    return False
                if not walk(upstream):
                    return False
            return True

        if (
            present is None
            or present.node != NODE_PRESENT
            or present.trust_state is not ArtifactTrust.QUALIFIED
            or not walk(present)
            or set(chain) != set(spec_by_node)
        ):
            return False
        verify = chain[NODE_VERIFY]
        if (
            verify.artifact_id != delivery.verification_artifact_id
            or verify.trust_state is not ArtifactTrust.QUALIFIED
            or verify.node != "github.verify"
        ):
            return False
        if {artifact.run_id for artifact in chain.values()} != {run_id}:
            # 链包含跨运行回填：本轮真实执行出的结果必须与链上同节点一致，
            # 否则视为旧核验为新任务或新证据背书。
            for receipt in repository.list_receipts(account_id, run_id):
                if receipt.status is not NodeReceiptStatus.COMPLETED:
                    return False
                artifact = repository.get_artifact(
                    account_id, receipt.artifact_id or ""
                )
                if artifact is None:
                    return False
                if artifact.trust_state is ArtifactTrust.INVALIDATED:
                    continue
                chained = chain.get(artifact.node)
                if chained is None or chained.content_hash != artifact.content_hash:
                    return False
        result = KernelResult(
            status=KernelStatus.COMPLETED, nodes=(), artifacts=tuple(chain.values()),
            delivery=present,
        )
        payload = present.payload
        expected = self._projection(
            result=result,
            insights=InsightOutcome(
                insights=payload.get("insights") or {}, note=payload.get("note")
            ),
        )
        return expected.model_dump(exclude={"completed_at"}) == delivery.projection.model_dump(
            exclude={"completed_at"}
        )

    # -- 交付（既有终态收敛路径） ----------------------------------------

    def _deliver(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        flow: GithubNodeFlow,
        stop_event: threading.Event | None,
    ) -> GithubRunOutcome:
        if result.status is KernelStatus.COMPLETED:
            return self._deliver_success(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                result=result,
                flow=flow,
            )
        if result.status is KernelStatus.NEEDS_INPUT:
            return self._deliver_clarification(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                result=result,
            )
        if result.status in {KernelStatus.STOPPED, KernelStatus.INVALIDATED} or (
            stop_event is not None and stop_event.is_set()
        ):
            return self._deliver_stopped(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                result=result,
            )
        if result.status is KernelStatus.REJECTED:
            raise GithubSupersededError(
                result.rejection_code or "generation_superseded"
            )
        return self._deliver_failure(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            result=result,
            flow=flow,
        )

    def _deliver_success(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        flow: GithubNodeFlow,
    ) -> GithubRunOutcome:
        projection = self._projection(
            result=result,
            insights=_insights_of(result, flow),
        )
        if projection.status is GithubProjectStatus.ERROR:
            # 失败投影先随同一事务收敛到消息，再在事务提交后通知父图收敛错误
            # （与通勤服务同一收敛路径；异常在提交前抛出会让投影回滚）。
            self._finalize(
                repo,
                account_id=account_id,
                assistant_message_id=assistant_message_id,
                status=ChatMessageStatus.ERROR,
                projection=projection,
                insights=flow.last_insight,
                content=render_empty_content(projection),
            )
            raise GithubModuleError(
                NODE_SEARCH,
                projection.error_code or "github_search_failed",
                projection.error_message or "GitHub 检索失败，请稍后重试。",
                retryable=projection.retryable,
            )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            insights=flow.last_insight,
            content=(
                render_result_content(projection)
                if projection.recommendations
                else render_empty_content(projection)
            ),
        )
        return GithubRunOutcome(
            status=projection.status,
            wait_reason=None,
            queries=list(projection.queries),
        )

    def _deliver_clarification(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> GithubRunOutcome:
        analysis = _analysis_of(result)
        if analysis is None or analysis.clarification is None:
            raise GithubModuleError(
                NODE_PARSE,
                "github_clarification_missing",
                "GitHub 澄清状态缺少恢复载荷，本轮未提交。",
                retryable=True,
            )
        now = self._clock()
        projection = _base_projection(
            analysis,
            status=GithubProjectStatus.CLARIFICATION,
            pending=ModuleWaitState(
                module_id=GITHUB_MODULE_ID,
                kind=WAIT_KIND_CLARIFICATION,
                question=analysis.clarification.question,
                origin_message_id=assistant_message_id,
                context=pending_payload(analysis),
                created_at=now,
            ),
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.DONE,
            projection=projection,
            insights=InsightOutcome(),
            content=render_clarification_content(projection),
        )
        return GithubRunOutcome(
            status=projection.status,
            wait_reason=WAIT_REASON_CLARIFICATION,
            queries=[],
        )

    def _deliver_stopped(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
    ) -> GithubRunOutcome:
        analysis = _analysis_of(result) or _empty_analysis()
        queries = _records_of(result)
        now = self._clock()
        projection = _base_projection(
            analysis,
            status=GithubProjectStatus.STOPPED,
            queries=queries,
            rate_limit=_rate_limit_state(limited=False),
            evidence_boundary=["你已停止本轮推荐，未继续读取仓库证据。"],
            completed_at=now,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.STOPPED,
            projection=projection,
            insights=InsightOutcome(),
            content=render_stopped_content(projection),
        )
        return GithubRunOutcome(
            status=GithubProjectStatus.STOPPED, wait_reason=None, queries=queries
        )

    def _deliver_failure(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        result: KernelResult,
        flow: GithubNodeFlow,
    ) -> GithubRunOutcome:
        analysis = _analysis_of(result) or _empty_analysis()
        queries = _records_of(result)
        failure = result.failure
        node = failure.node if failure is not None else NODE_SEARCH
        code = (failure.code if failure is not None else "") or "github_failed"
        message = (
            failure.message if failure is not None and failure.message else ""
        ) or "GitHub 项目推荐失败，请稍后重试。"
        retryable = failure.retryable if failure is not None else True
        now = self._clock()
        projection = _base_projection(
            analysis,
            status=GithubProjectStatus.ERROR,
            queries=queries,
            rate_limit=_rate_limit_state(limited=False),
            evidence_boundary=_query_notes(queries),
            completed_at=now,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )
        self._finalize(
            repo,
            account_id=account_id,
            assistant_message_id=assistant_message_id,
            status=ChatMessageStatus.ERROR,
            projection=projection,
            insights=_insights_of(result, flow),
            content=render_empty_content(projection),
        )
        raise GithubModuleError(node, code, message, retryable=retryable)

    # -- 内核结果到投影 --------------------------------------------------

    def _projection(
        self,
        *,
        result: KernelResult,
        insights: InsightOutcome,
    ) -> GithubProjectsProjection:
        analysis = _analysis_of(result) or _empty_analysis()
        records = _records_of(result)
        ranked = _ranked_of(result)
        evaluate = result.artifact(NODE_EVALUATE)
        read = result.artifact("github.read")
        verify = result.artifact(NODE_VERIFY)
        rate_limited = bool(
            evaluate is not None and evaluate.payload.get("rate_limited")
        ) or _has_rate_limit(records)
        reset_at = _parse_moment(
            evaluate.payload.get("reset_at") if evaluate is not None else None
        ) or _parse_moment(read.payload.get("reset_at") if read is not None else None)
        errors = _first_error(records)
        recommendations = [
            item.model_copy(update={"insight_zh": insights.insights.get(item.full_name)})
            for item in ranked.recommendations
        ]
        metadata_only = False
        if recommendations:
            metadata_only = all(
                GithubEvidenceKind.README not in item.evidence_kinds
                and GithubEvidenceKind.IMPLEMENTATION not in item.evidence_kinds
                for item in recommendations
            )
            status = (
                GithubProjectStatus.METADATA_ONLY
                if metadata_only
                else GithubProjectStatus.SUCCESS
            )
        elif not records or _only_failures(records):
            status = GithubProjectStatus.ERROR
        else:
            status = GithubProjectStatus.EMPTY
        boundary = _evidence_boundary(
            analysis=analysis,
            recommendations=recommendations,
            rejected=ranked.rejected,
            rate_limited=rate_limited,
            rate_limit_reset_at=reset_at,
            metadata_only=metadata_only,
            insights_note=insights.note,
            has_insights=bool(insights.insights),
            verification=verify.payload if verify is not None else None,
        )
        return GithubProjectsProjection(
            status=status,
            scenario=analysis.scenario,
            original_request=analysis.original_request,
            features=list(analysis.features),
            optional_features=list(analysis.optional_features),
            tech_terms=list(analysis.tech_terms),
            constraints=analysis.constraints.model_copy(deep=True),
            whole_idea=analysis.whole_idea,
            component_terms=list(analysis.component_terms),
            context_source=analysis.context_source,
            requirement_source=analysis.requirement_source,
            identity_note=_identity_note(analysis),
            queries=list(records),
            recommendations=recommendations,
            rejected=list(ranked.rejected),
            rate_limit=_rate_limit_state(limited=rate_limited, reset_at=reset_at),
            evidence_boundary=boundary,
            empty_reason=(
                _failure_reason(errors)
                if status is GithubProjectStatus.ERROR and errors is not None
                else (_empty_reason(ranked) if status is GithubProjectStatus.EMPTY else None)
            ),
            retryable=bool(errors and errors.retryable)
            or rate_limited
            or metadata_only,
            error_code=errors.error_code if errors is not None else None,
            error_message=errors.error_message if errors is not None else None,
            completed_at=self._clock(),
        )

    # -- 恢复与等待 ------------------------------------------------------

    def _pending_wait(
        self, repo: ConversationRepository, account_id: str, conversation_id: str
    ) -> ModuleWaitState | None:
        """读取本会话最近一次尚未被后续结果取代的澄清等待状态。"""
        for message in reversed(repo.list_messages(account_id, conversation_id)):
            stored = message.github_projects
            if not isinstance(stored, dict):
                continue
            try:
                projection = GithubProjectsProjection.model_validate(stored)
            except ValueError:
                continue
            if projection.pending is not None:
                return projection.pending
            if projection.status in {
                GithubProjectStatus.SUCCESS,
                GithubProjectStatus.METADATA_ONLY,
                GithubProjectStatus.EMPTY,
            }:
                return None
        return None

    def _prior_context(
        self,
        repo: ConversationRepository,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        *,
        module_context: ModuleTaskContext | None = None,
    ) -> list[GithubContextSource]:
        """已确认的会话前文里的可追溯原词（供「找实现它的项目」指向）。

        只看**模块投影**里的原词，按时间顺序合并：上一轮论文搜索的原始术语，
        以及上一轮 GitHub 请求的场景。每条都带回承载它的助手消息 ID，用户
        看到的「前文依据」因此能追溯到具体那条会话记录；普通聊天消息不作为
        依据，避免把无关的一句话当成检索主题。

        工单 15：有当前任务主题时，把它作为**最早**一条依据参与定位——
        它只包含有效条件（未被取代/撤销）与有来源的原文，显式的论文/仓库
        锚点仍优先于它；因此普通聊天里先讲过的目标可以续接，而无关旧条件
        不会污染新任务。
        """
        anchors: list[GithubContextSource] = []
        last_user_message_id: str | None = None
        for message in repo.list_messages(account_id, conversation_id):
            if message.message_id == user_message_id:
                break
            if getattr(message, "role", None) == "user":
                last_user_message_id = message.message_id
            if (
                module_context is not None
                and module_context.used_task_scope
                and message.message_id not in module_context.source_message_ids
                and last_user_message_id not in module_context.source_message_ids
            ):
                continue
            anchors.extend(_paper_anchors(message))
            anchors.extend(_github_anchors(message))
        anchors = anchors[-PRIOR_MESSAGES_LOOKBACK:]
        if module_context is not None and module_context.topic_hint.strip():
            anchors.insert(
                0,
                GithubContextSource(
                    kind="task",
                    label="当前任务主题",
                    phrase=module_context.topic_hint.strip(),
                    message_id=(
                        module_context.source_message_ids[-1]
                        if module_context.source_message_ids
                        else None
                    ),
                ),
            )
        return anchors

    # -- 落库 ------------------------------------------------------------

    def _finalize(
        self,
        repo: ConversationRepository,
        *,
        account_id: str,
        assistant_message_id: str,
        status: ChatMessageStatus,
        projection: GithubProjectsProjection,
        insights: InsightOutcome,
        content: str,
    ) -> None:
        now = self._clock()
        projection = projection.model_copy(
            update={"completed_at": projection.completed_at or now}
        )
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
            run_lock_id=insights.lock.lock_id if insights.lock is not None else None,
            lock=insights.lock,
            started=time.monotonic(),
            now=now,
            github_projects=projection.model_dump(mode="json"),
            final_content=content,
        )

    def _current_task_ref(
        self, account_id: str, conversation_id: str
    ) -> tuple[str | None, int | None] | None:
        if self._task_version_provider is None:
            return None
        return self._task_version_provider(account_id, conversation_id)

    def _wait_identity(self, pending: ModuleWaitState | None) -> str | None:
        if pending is None:
            return None
        return (
            f"{pending.module_id}:{pending.kind}:{pending.origin_message_id}:"
            f"{pending.created_at.isoformat()}"
        )


# -- 内核结果读取 ------------------------------------------------------------


def _analysis_of(result: KernelResult) -> GithubIdeaAnalysis | None:
    artifact = result.artifact(NODE_PARSE)
    if artifact is None:
        return None
    analysis = artifact.payload.get("analysis")
    if not isinstance(analysis, dict):
        return None
    return GithubIdeaAnalysis.model_validate(analysis)


def _empty_analysis() -> GithubIdeaAnalysis:
    return GithubIdeaAnalysis(
        original_request="",
        scenario="",
        features=[],
        tech_terms=[],
        whole_idea=True,
        component_terms=[],
    )


def _records_of(result: KernelResult) -> list[ModuleQueryRecord]:
    records: list[ModuleQueryRecord] = []
    for node in (NODE_SEARCH, "github.read"):
        artifact = result.artifact(node)
        if artifact is None:
            continue
        records.extend(
            ModuleQueryRecord.model_validate(item)
            for item in artifact.payload.get("queries", [])
        )
    return records


def _ranked_of(result: KernelResult) -> RankOutcome:
    artifact = result.artifact(NODE_EVALUATE)
    if artifact is None:
        return RankOutcome()
    ranked = artifact.payload.get("ranked") or {}
    return RankOutcome(
        recommendations=[
            GithubRecommendation.model_validate(item)
            for item in ranked.get("recommendations", [])
        ],
        rejected=[
            GithubRejectedRepository.model_validate(item)
            for item in ranked.get("rejected", [])
        ],
    )


def _base_projection(
    analysis: GithubIdeaAnalysis,
    *,
    status: GithubProjectStatus,
    pending: ModuleWaitState | None = None,
    queries: Sequence[ModuleQueryRecord] = (),
    rate_limit: GithubRateLimitState | None = None,
    evidence_boundary: Sequence[str] = (),
    completed_at: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
) -> GithubProjectsProjection:
    return GithubProjectsProjection(
        status=status,
        scenario=analysis.scenario,
        original_request=analysis.original_request,
        features=list(analysis.features),
        optional_features=list(analysis.optional_features),
        tech_terms=list(analysis.tech_terms),
        constraints=analysis.constraints.model_copy(deep=True),
        whole_idea=analysis.whole_idea,
        component_terms=list(analysis.component_terms),
        context_source=analysis.context_source,
        requirement_source=analysis.requirement_source,
        identity_note=_identity_note(analysis),
        queries=list(queries),
        rate_limit=rate_limit or GithubRateLimitState(),
        evidence_boundary=list(evidence_boundary),
        pending=pending,
        completed_at=completed_at,
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
    )


def _insights_of(result: KernelResult, flow: GithubNodeFlow) -> InsightOutcome:
    """交付用借鉴角度：本轮生成优先；产物按收据复用时从 present 产物回填。

    重试复用 ``github.present`` 产物时节点 runner 不会再执行，``flow.last_insight``
    是空值；产物 payload 里已有首次生成的解读与说明，回填后重试不会丢解读。
    """
    current = flow.last_insight
    if current.insights or current.note:
        return current
    present = result.artifact(NODE_PRESENT)
    if present is None:
        return current
    payload = present.payload
    insights = payload.get("insights")
    note = payload.get("note")
    return InsightOutcome(
        insights=(
            {str(key): str(value) for key, value in insights.items()}
            if isinstance(insights, dict)
            else {}
        ),
        note=note if isinstance(note, str) else None,
    )


def _identity_note(analysis: GithubIdeaAnalysis) -> str | None:
    """类型化需求来源的身份说明；未确认时明确「不宣称对应实现」。"""
    source = analysis.requirement_source
    if source is None:
        return None
    if source.identity_confirmed:
        return f"需求来自已确认的{source.label}（{source.identifier or '无标识'}）。"
    return (
        f"需求来自{source.label}，但该来源身份尚未确认：只按原词检索，"
        "不把仓库断言为它的实现。"
    )


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _failure_reason(record: ModuleQueryRecord) -> str:
    """失败时正文要写清「哪一步没成、上游怎么说的、能不能重试」。"""
    message = record.error_message or "上游没有给出原因。"
    code = f"（错误码：{record.error_code}）" if record.error_code else ""
    tail = "稍后可以重试。" if record.retryable else "本轮不重试。"
    return f"本轮检索未完成：{message}{code}{tail}"


def _empty_reason(ranked: RankOutcome) -> str:
    if ranked.rejected:
        return (
            "本轮没有取得能对上你 idea 要点的仓库证据，因此没有推荐；"
            "未纳入的候选与理由逐条列在下面。"
        )
    return (
        "本轮检索没有返回可用的公开仓库，我不会用记忆补造条目。"
        "可以补充功能描述或换个说法后重试。"
    )


def _query_notes(queries: Sequence[ModuleQueryRecord]) -> list[str]:
    notes = [
        f"{record.source} 查询「{record.query}」：{record.status.value}"
        + (f"（{record.error_message}）" if record.error_message else "")
        for record in queries
    ]
    return notes or ["本轮没有发出可记录的外部查询。"]


def _evidence_boundary(
    *,
    analysis: GithubIdeaAnalysis,
    recommendations: Sequence[GithubRecommendation],
    rejected: Sequence[GithubRejectedRepository],
    rate_limited: bool,
    rate_limit_reset_at: datetime | None,
    metadata_only: bool,
    insights_note: str | None,
    has_insights: bool,
    verification: dict[str, object] | None = None,
) -> list[str]:
    notes = [
        "只纳入真实取得证据的公开仓库：API 元数据来自 GitHub 接口，"
        "README 是项目自述，实现文件证据来自实际读取到的路径与内容。",
        "每项需求都按支持层次标注：文档自述／静态实现／未确认／未支持；"
        "必要功能还有未支持或未确认时不会判为整体适配（可选项与 star 不能抵消）。",
        "README 只说明项目自称的能力；没有读取实现文件时不对内部架构作断言，"
        "没有读到许可文件时不声称代码可自由复用；静态读取不等于实际运行。",
    ]
    if analysis.context_source is not None:
        notes.append(
            f"本轮 idea 取自{analysis.context_source.label}的原始词"
            f"「{analysis.context_source.phrase}」，检索词与它逐字一致。"
        )
    if analysis.requirement_source is not None:
        source = analysis.requirement_source
        if source.identity_confirmed:
            notes.append(
                f"需求来自已确认的{source.label}"
                f"（{source.identifier or '无标识'}），原词未被改写。"
            )
        else:
            notes.append(
                f"需求来自{source.label}，但身份尚未确认：只按原词检索，"
                "不把仓库断言为它的实现。"
            )
    if not analysis.whole_idea:
        notes.append("你本轮要的是组件，推荐一律按组件项目呈现，不代表完整产品。")
    if rejected:
        notes.append(f"另有 {len(rejected)} 个候选没有纳入推荐，理由已逐条列出。")
    if rate_limited:
        notes.append(
            "本轮撞上 GitHub 接口额度限制，没有再继续外发请求；缺失的 README／文件"
            "证据与未完成检查的候选已在各仓库下如实标出，"
            f"{rate_limit_recovery_note(rate_limit_reset_at)}，稍后重试可补齐。"
        )
    if metadata_only and not rate_limited:
        notes.append(
            "本轮只取得 API 元数据（没有读到 README 与实现文件），"
            "不对内部实现作断言；稍后可重试补齐证据。"
        )
    if verification is not None:
        unconfirmed = verification.get("unconfirmed")
        if isinstance(unconfirmed, list) and unconfirmed:
            notes.append(
                f"核验产物记录了 {len(unconfirmed)} 条未确认项，逐条列在各仓库下；"
                "未确认不等于不支持。"
            )
    if not recommendations:
        notes.append("本轮没有可展示的推荐，正文只保留实际查询词与真实原因。")
    if insights_note:
        notes.append(insights_note)
    if has_insights:
        notes.append(
            "「借鉴角度」是模型在下列证据范围内的归纳，不是仓库原文；"
            "判定依据以功能矩阵与已读文件为准。"
        )
    notes.append("star 数只作辅助参考，不作为功能匹配或代码质量的依据。")
    return notes


def _rate_limit_state(
    *,
    limited: bool,
    reset_at: datetime | None = None,
) -> GithubRateLimitState:
    """限流投影：只报「是否撞上 + 恢复时间」，重试口吻由呈现侧统一补一次。"""
    if not limited:
        return GithubRateLimitState(note="本轮未撞上 GitHub 接口额度限制。")
    return GithubRateLimitState(
        limited=True,
        note="本轮撞上 GitHub 接口额度限制，本轮不再继续请求。",
        reset_at=reset_at,
    )


def _first_error(records: Sequence[ModuleQueryRecord]) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


def _only_failures(records: Sequence[ModuleQueryRecord]) -> bool:
    return bool(records) and all(
        record.status in FAILED_QUERY_STATUSES for record in records
    )


def _has_rate_limit(records: Sequence[ModuleQueryRecord]) -> bool:
    return any(record.status is ModuleQueryStatus.RATE_LIMITED for record in records)


def _paper_anchors(message: object) -> list[GithubContextSource]:
    """上一轮论文搜索的原始术语（可追溯的前文原词，AC5 指向对象之一）。"""
    stored = getattr(message, "paper_search", None)
    if not isinstance(stored, dict):
        return []
    phrase = str(stored.get("original_phrase") or "").strip()
    if not phrase:
        return []
    return [
        GithubContextSource(
            kind="paper_search",
            label="上一轮论文搜索",
            phrase=phrase,
            message_id=_message_id(message),
        )
    ]


def _github_anchors(message: object) -> list[GithubContextSource]:
    """上一轮 GitHub 请求的场景（同会话连续使用时的指向对象）。"""
    stored = getattr(message, "github_projects", None)
    if not isinstance(stored, dict):
        return []
    phrase = str(stored.get("scenario") or "").strip()
    if not phrase:
        return []
    return [
        GithubContextSource(
            kind="github_projects",
            label="上一轮 GitHub 项目推荐",
            phrase=phrase,
            message_id=_message_id(message),
        )
    ]


def _message_id(message: object) -> str | None:
    value = getattr(message, "message_id", None)
    return str(value) if value else None


__all__ = [
    "FAILED_QUERY_STATUSES",
    "GITHUB_NODE_LABELS",
    "GITHUB_MODULE_ID",
    "NODE_EVALUATE",
    "NODE_INSPECT",
    "NODE_PARSE",
    "NODE_PRESENT",
    "NODE_RANK",
    "NODE_SEARCH",
    "NODE_VERIFY",
    "WAIT_REASON_CLARIFICATION",
    "GithubModuleError",
    "GithubProjectsService",
    "GithubRunOutcome",
    "GithubSupersededError",
]
