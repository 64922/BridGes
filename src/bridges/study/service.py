"""书页识别、知识范围与预习的独立持久化阶段图。"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypedDict, TypeVar

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from bridges.ai.adapters import REQUEST_TIMEOUT_SECONDS_KEY, StreamEvent
from bridges.chat.budget import load_run_budget
from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.chat.context_compiler import ContextEvidence
from bridges.chat.lightweight_policy import ChatLightweightPolicyCompiler
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.chat.run_executor import chat_run_context
from bridges.chat.turn import (
    error_is_retryable,
    failed_thinking,
    finalize_message,
    initial_thinking,
    user_facing_error,
)
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.chat import (
    ChatMessageStatus,
    ChatMode,
    ChatStreamDeltaData,
    ChatStreamNodeData,
)
from bridges.contracts.study import (
    STUDY_STATE_VERSION,
    StudyExchange,
    StudyFragment,
    StudyPage,
    StudyPageUpdate,
    StudyReview,
    StudyState,
    StudySummaryRecord,
    StudyUnclear,
    upgrade_legacy_study_state,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.repository import NodeKernelRepository
from bridges.state_copy import STUDY_STOPPED_TEXT, error_template
from bridges.storage.database import BridgesDatabase
from bridges.study.grade_kernel import ReviewGradeKernel
from bridges.study.kernel import (
    NODE_RECOGNIZE_PAGE,
    NODE_VALIDATE_PAGES,
    NODE_VERIFY_RECOGNITION,
    STUDY_PAGE_GATE_HANDLERS,
    NodeFailureError,
    StoppedError,
    StudyPageCandidate,
    StudyPageNodeFlow,
    StudyPageRecognition,
    SupersededError,
    _json_object_text,  # noqa: F401 - 再导出，兼容既有测试导入路径
    study_recipe_registry,
)
from bridges.study.review import (
    ReviewGradeError,
    ReviewPlanError,
    active_question,
    advance_question,
    judged_by_message,
    next_question,
    render_feedback,
    render_question_prompt,
    review_intent,
)
from bridges.study.review_kernel import ReviewPlanKernel
from bridges.study.scope import (
    GATE_SCOPE_CONFLICT,
    GATE_SCOPE_INCOMPLETE,
    GATE_SCOPE_UNVERIFIED,
    SCOPE_GATE_HANDLERS,
    ScopeFragment,
    ScopeMaterial,
    StudyScopeNodeFlow,
    StudyScopeRecognition,
    study_scope_recipe_registry,
)
from bridges.study.summary import build_summary, render_summary
from bridges.study.turn_result import finalize_study_message
from bridges.study.tutoring import tutor

STUDY_GRAPH_VERSION = "study-tutoring-review-v6"

#: 书页图片调用（OCR／视觉）的单次超时（秒）。原始教材整页的实测耗时：
#: OCR 30—44 秒、视觉 45—59 秒（issue 04 三张原图实测），而默认模型调用
#: 超时是 60 秒——视觉调用贴着上限，整页识别会以 ``transient`` 超时失败，
#: 书页无法进入预习。这里按实测值给出余量，只作用于本流程的图片调用，
#: 不改全局默认超时，也不影响其他调用方。
STUDY_IMAGE_CALL_TIMEOUT_SECONDS = 180.0

#: 书页识别阶段的节点集合：追加页识别失败时按「保留已确认范围」收敛
#: （page_update 语义），不让半完成的追加覆盖已确认的书页与题目。
_RECOGNITION_NODES = frozenset(
    {
        "study.recognize",
        NODE_VALIDATE_PAGES,
        NODE_RECOGNIZE_PAGE,
        NODE_VERIFY_RECOGNITION,
    }
)


def _is_recognition_node(node: str) -> bool:
    return node in _RECOGNITION_NODES


def _mark_active_unanswered(review: StudyReview | None) -> None:
    """暂停/追加/转辅导使当前激活题作废时，单独记录已呈现未答（工单 35）。

    保留题号、来源与已判定题；未作答不计为答对，继续复盘默认从未问题开始。
    """
    if review is None:
        return
    current = active_question(review)
    if current is not None and current.judgement is None and current.answer is None:
        current.unanswered = True
    review.active_question_id = None


class StudyWorkflowError(Exception):
    def __init__(self, node: str, code: str, message: str) -> None:
        self.node = node
        self.code = code
        self.message = message
        super().__init__(message)


#: 学习模型调用失败的兜底文案。可重试故障（限流、超时、连接中断）保留
#: "稍后重试"指引；非重试错误明说重试不会恢复并给出下一步——旧实现把二者
#: 都写成"学习处理模型暂时不可用，请稍后重试。"，让用户等待不可能发生的
#: 恢复（issue 04 第 5 条）。
_STUDY_MODEL_RETRYABLE_FALLBACK = "学习处理模型暂时不可用，请稍后重试。"
_STUDY_MODEL_FINAL_FALLBACK = (
    "学习处理未完成：该错误重试不会恢复；请检查主模型配置，或把这次失败反馈给我们。"
)


def model_failure_message(error_code: str | None) -> str:
    """模型调用失败的用户可见中文原因（共享映射优先，按可重试性兜底）。

    稳定错误码（参数、鉴权、限流、区域、响应解析）统一取
    ``bridges.chat.turn`` 与 ``bridges.ai.errors`` 的同一映射源，不再由
    学习流程自造文案。
    """
    fallback = (
        _STUDY_MODEL_RETRYABLE_FALLBACK
        if error_is_retryable(error_code)
        else _STUDY_MODEL_FINAL_FALLBACK
    )
    return user_facing_error(error_code, fallback)


def _public_failure_message(code: str) -> str:
    """控制节点与外部异常原文留在内部，公共错误只用登记的自然文案。"""
    if code == "stopped":
        return STUDY_STOPPED_TEXT
    template = error_template(code) or error_template("study_internal_error")
    assert template is not None, "学习内部错误必须登记固定文案"
    return template.text


class StudyRepository:
    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    def get(self, account_id: str, conversation_id: str) -> StudyState | None:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT state_json FROM study_states WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        # 旧状态（v1）读取时升级为稳定知识点 ID 合同：旧范围/预习保持可读，
        # 不因格式变化丢失历史，也不改写阶段、判定或总结。
        return upgrade_legacy_study_state(
            StudyState.model_validate_json(row["state_json"])
        )

    def save(self, account_id: str, conversation_id: str, state: StudyState) -> None:
        with self._db.transaction():
            self.save_in_transaction(account_id, conversation_id, state)

    def save_in_transaction(
        self, account_id: str, conversation_id: str, state: StudyState,
    ) -> None:
        """由消息终态事务调用，问答与消息要么一起成功、要么一起回滚。"""
        payload = state.model_copy(update={"state_version": STUDY_STATE_VERSION})
        self._db.scoped(account_id).execute(
            "INSERT INTO study_states (account_id, conversation_id, state_json, updated_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(account_id, conversation_id)"
            " DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
            (
                account_id,
                conversation_id,
                payload.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )


class _GraphState(TypedDict, total=False):
    wait: bool
    answer: str
    tutoring: dict[str, Any]
    updated_pages: dict[str, Any]
    reviewed: dict[str, Any]
    #: 判定/反馈/呈现/总结已按各自提交边界落库（工单 34）；
    #: 终态只写助手消息正文，不重复保存领域状态。
    reviewed_committed: bool
    scope: dict[str, Any]
    questions: list[dict[str, Any]]


#: 节点返回值：图节点返回增量状态，图内子步骤（如总结）返回自己的结果。
_NodeResult = TypeVar("_NodeResult")


class StudyWorkflow:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._repo = service._repo
        self._attachments = service._require_attachment_service()
        self._states = StudyRepository(self._repo.database)

    def state(self, account_id: str, conversation_id: str) -> StudyState | None:
        return self._states.get(account_id, conversation_id)

    def _finalize_message(self, run: Any, **kwargs: Any) -> int:
        return finalize_study_message(
            self._repo, run, finalizer=finalize_message,
            state_provider=lambda: self._states.get(run.account_id, run.conversation_id),
            **kwargs,
        )

    def run(
        self,
        run: Any,
        *,
        on_event: Callable[[StreamEvent], None],
        stop_event: threading.Event | None,
    ) -> str | None:
        started = time.monotonic()
        if run.graph_version != STUDY_GRAPH_VERSION:
            # 旧检查点可能已经完成未原子提交的预习节点，不能套用新图恢复。
            self._finalize_message(
                run,
                status=ChatMessageStatus.ERROR,
                error_code="study_graph_version_changed",
                error_message=_public_failure_message("study_graph_version_changed"),
                duration_ms=None, model_id=None, started=started, now=datetime.now(UTC),
            )
            on_event(StreamEvent(
                kind="error", error_code="study_graph_version_changed",
                error_message=_public_failure_message("study_graph_version_changed"),
            ))
            return "error"
        last_lock: ModelRunLock | None = None
        #: 失败尝试自己的运行锁（与最后一次成功调用的 ``last_lock`` 分开，
        #: 避免把成功的锁当失败证据）。
        failure_lock: ModelRunLock | None = None
        current_node = "study.recognize"
        state = self._states.get(run.account_id, run.conversation_id) or StudyState(
            subsection_id=run.conversation_id
        )
        user = self._repo.get_message(run.account_id, run.user_message_id)
        if user is None:
            raise StudyWorkflowError(current_node, "message_not_found", "学习消息不存在。")
        attachments = self._attachments.list_for_message(
            run.account_id, run.conversation_id, run.user_message_id
        )
        duplicate_count = 0
        updating = state.stage in {"tutoring", "review", "summary"} and bool(
            state.page_update
            or any(
                item.content_hash not in {page.content_hash for page in state.pages}
                for item in attachments
            )
        )
        committed = state.model_copy(deep=True)
        if state.page_update is not None:
            state.pages = state.page_update.pages
            state.units = state.page_update.units
            state.wait_reason = state.page_update.wait_reason
            if state.page_update.scope is not None:
                state.scope = state.page_update.scope
                state.scope_history = state.page_update.scope_history

        material_guard = RunCommitGuard(
            self._repo,
            account_id=run.account_id,
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            assistant_message_id=run.assistant_message_id,
            stop_event=stop_event,
        )
        material_guard.capture()

        def save_state_in_transaction() -> None:
            if updating:
                # 候选写也会更新整份状态；先核对有效版本，避免抹掉并发修改。
                _verify_update_commit()
                pending = committed.model_copy(deep=True)
                pending.pending_object_ids = list(state.pending_object_ids)
                pending.page_update = StudyPageUpdate(
                    pages=state.pages,
                    units=state.units,
                    wait_reason=state.wait_reason,
                    scope=state.scope,
                    scope_history=state.scope_history,
                )
                self._states.save_in_transaction(run.account_id, run.conversation_id, pending)
            else:
                self._states.save_in_transaction(run.account_id, run.conversation_id, state)

        def save_state() -> None:
            with self._repo.database.transaction():
                save_state_in_transaction()

        def run_node(name: str, body: Callable[[], _NodeResult]) -> _NodeResult:
            """报告真实开始的节点、检查停止信号，并记录节点耗时。"""
            nonlocal current_node
            current_node = name
            if stop_event is not None and stop_event.is_set():
                raise StudyWorkflowError(name, "stopped", STUDY_STOPPED_TEXT)
            self._repo.update_generation_progress(run.account_id, run.run_id, current_node=name)
            on_event(
                StreamEvent(
                    kind="node",
                    node=ChatStreamNodeData(
                        message_id=run.assistant_message_id, node=name, status="started"
                    ),
                )
            )
            began = time.monotonic()
            result = body()
            on_event(
                StreamEvent(
                    kind="node",
                    node=ChatStreamNodeData(
                        message_id=run.assistant_message_id,
                        node=name,
                        status="completed",
                        duration_ms=max(1, int((time.monotonic() - began) * 1000)),
                    ),
                )
            )
            return result

        def node(name: str, body: Callable[[], _GraphState]) -> Any:
            def execute(_: _GraphState, config: RunnableConfig) -> _GraphState:
                del config
                return run_node(name, body)

            return execute

        #: 工单 09：本次运行的持久预算（模型调用登记/上限/重试门由网关
        #: 统一执行）与工单 03 的额度快照（最终载荷预算门）；两者与聊天
        #: 父图同源，学习子流程不复制预算器。
        budget = load_run_budget(
            self._repo, run.account_id, run.run_id, mode_hint="study"
        )
        model_quota = self._service.run_model_quota(run)

        def invoke(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal failure_lock, last_lock
            if stop_event is not None and stop_event.is_set():
                raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
            if capability == "qwen_structured_output" and "messages" not in payload:
                payload = {
                    **payload,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是跨学科教材助教，只依据给定书页证据，输出有效 JSON。",
                        },
                        {"role": "user", "content": payload["prompt"]},
                    ],
                }
            if capability in {"qwen_ocr", "qwen_vision"}:
                # 图片能力声明单次上限；网关仍按运行剩余预算截断。
                payload = {
                    **payload,
                    REQUEST_TIMEOUT_SECONDS_KEY: STUDY_IMAGE_CALL_TIMEOUT_SECONDS,
                }
            result = self._service._gateway.invoke(
                capability,
                "1",
                chat_run_context(run.account_id, run.conversation_id, run.run_id),
                payload=payload,
                model_override=(run.config or {}).get("run_model_id"),
                budget=budget,
                model_quota=model_quota,
            )
            if stop_event is not None and stop_event.is_set():
                raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
            if result.status not in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
                # 只保留失败尝试自己的锁：网关没给锁就如实留空，不能把上一次
                # 成功调用的锁当成本次失败的证据（issue 04 第 5 条：内部记录要
                # 能按关联标识复核失败的能力、错误码与实际模型）。
                failure_lock = result.lock
                raise StudyWorkflowError(
                    current_node,
                    result.error_code or "study_model_failed",
                    model_failure_message(result.error_code),
                )
            last_lock = result.lock
            if not isinstance(result.output, dict):
                raise StudyWorkflowError(
                    current_node, "study_model_invalid", "模型结果不完整，请重试。"
                )
            return result.output

        #: 工单 30：书页识别经持久节点内核执行（逐页产物/收据/质量门）。
        ledger = RunBudgetLedgerRepository(self._repo.database)

        def remaining_model_calls() -> int | None:
            snapshot = ledger.load(run.account_id, run.run_id)
            if snapshot is None:
                return None
            if not snapshot.active:
                return 0
            return max(0, snapshot.plan.model_call_limit - snapshot.model_calls_used)

        flow = StudyPageNodeFlow(
            load_image=lambda object_id: self._attachments.download(
                run.account_id, run.conversation_id, object_id
            ),
            invoke=invoke,
            budget_remaining_calls=remaining_model_calls,
        )

        def kernel_event(node: str, status: Any, duration_ms: int | None) -> None:
            nonlocal current_node
            current_node = node
            self._repo.update_generation_progress(
                run.account_id, run.run_id, current_node=node
            )
            on_event(
                StreamEvent(
                    kind="node",
                    node=ChatStreamNodeData(
                        message_id=run.assistant_message_id,
                        node=node,
                        status=status,
                        duration_ms=duration_ms,
                    ),
                )
            )

        recognition = StudyPageRecognition(
            kernel=NodeKernel(
                registry=study_recipe_registry(),
                repository=NodeKernelRepository(self._repo.database),
                guard=RunCommitGuard(
                    self._repo,
                    account_id=run.account_id,
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    assistant_message_id=run.assistant_message_id,
                    stop_event=stop_event,
                ),
                gates=STUDY_PAGE_GATE_HANDLERS,
                runner=flow.run_node,
            ),
            flow=flow,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            user_message_id=run.user_message_id,
            user_content=user.content,
            stop_event=stop_event,
            event_sink=kernel_event,
        )

        #: 工单 31：范围映射、范围核验与预习经持久节点内核执行；预习的
        #: 自然语言生成走统一上下文编译（表达策略 + 采纳证据 + 预算门），
        #: 策略只作用于提问措辞，不改变片段、范围或游标。
        def preview_policy_block() -> str:
            compiler = ChatLightweightPolicyCompiler()
            existing = (run.config or {}).get("global_writing_policy")
            snapshot = compiler.compile(
                ChatMode.STUDY,
                user_text=user.content or "生成本节预习问题",
                lesson=True,
                existing_snapshot=existing,
            )
            config = dict(run.config or {})
            config["global_writing_policy"] = snapshot.model_dump(mode="json")
            self._repo.update_generation_config(run.account_id, run.run_id, config)
            run.config = config
            return snapshot.system_block

        def compile_preview_context(
            system_prompt: str, data: dict[str, Any]
        ) -> tuple[list[dict[str, str]] | None, bool]:
            messages, context_budget = self._service.compile_turn_context(
                run,
                system_prompt=system_prompt,
                evidence=[
                    ContextEvidence("study-preview", json.dumps(data, ensure_ascii=False))
                ],
            )
            adopted = bool(
                messages is not None
                and context_budget is not None
                and not context_budget["budget_floor_exceeded"]
                and "study-preview" in context_budget["adopted_evidence_ids"]
            )
            return messages, adopted

        scope_flow = StudyScopeNodeFlow(
            invoke=invoke,
            compile_context=compile_preview_context,
        )
        scope_recognition = StudyScopeRecognition(
            kernel=NodeKernel(
                registry=study_scope_recipe_registry(),
                repository=NodeKernelRepository(self._repo.database),
                guard=RunCommitGuard(
                    self._repo,
                    account_id=run.account_id,
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    assistant_message_id=run.assistant_message_id,
                    stop_event=stop_event,
                ),
                gates=SCOPE_GATE_HANDLERS,
                runner=scope_flow.run_node,
            ),
            flow=scope_flow,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            user_message_id=run.user_message_id,
            user_content=user.content,
            stop_event=stop_event,
            event_sink=kernel_event,
        )

        def recognize() -> _GraphState:
            nonlocal duplicate_count
            if not attachments and state.wait_reason == "recognition_failed":
                return {
                    "wait": True, "answer": "追加书页识别尚未完成，请重试原失败消息或重新补拍。",
                }
            if not attachments and state.pending_object_ids:
                # 未处理页不被说成已读：先继续完成识别，才进入范围映射。
                return {
                    "wait": True,
                    "answer": "上一轮还有书页未识别完成；请重试原消息继续识别剩余页。",
                }
            previous_stage = state.stage
            state.stage = "recognizing"
            if any(item.content_hash not in {page.content_hash for page in state.pages}
                   for item in attachments):
                state.units = []
                state.questions = []
            save_state()
            known = {page.content_hash for page in state.pages}
            duplicates = 0
            added = 0
            supplemented = False
            if not attachments and state.wait_reason == "page_order":
                order = re.fullmatch(
                    r"页序\s*[:：]\s*(\d+(?:\s*[,，]\s*\d+)*)", user.content.strip()
                )
                if order:
                    ordinals = [int(value) for value in re.split(r"[,，]", order.group(1))]
                    if sorted(ordinals) == list(range(1, len(state.pages) + 1)):
                        state.pages = [state.pages[index - 1] for index in ordinals]
                        for index, page in enumerate(state.pages, 1):
                            page.ordinal = index
                        state.wait_reason = None
                        save_state()
            if not attachments:
                confirmation = re.fullmatch(
                    r"确认第\s*(\d+)\s*页属于本节[。！!]?", user.content.strip(),
                )
                if confirmation:
                    for page in state.pages:
                        if page.ordinal == int(confirmation.group(1)) and not page.same_section:
                            page.same_section = True
                            page.unclear = [issue for issue in page.unclear
                                            if issue.reason != "疑似不同小节，请确认或在新对话上传"]
                            save_state()
            known_before = frozenset(page.content_hash for page in state.pages)
            seen_hashes = set(known_before)
            candidates: list[StudyPageCandidate] = []
            plans: dict[str, tuple[int, list[str]]] = {}
            next_ordinal = len(state.pages) + 1
            unresolved_open = [page for page in state.pages if page.unclear]
            for attachment in attachments:
                if stop_event is not None and stop_event.is_set():
                    raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
                if attachment.content_hash in seen_hashes:
                    duplicates += 1
                    duplicate_count += 1
                    continue
                seen_hashes.add(attachment.content_hash)
                target_match = re.search(r"补拍第\s*(\d+)\s*页", user.content)
                replacement = (
                    next(
                        (
                            page
                            for page in unresolved_open
                            if page.ordinal == int(target_match.group(1))
                        ),
                        None,
                    )
                    if target_match
                    else (
                        unresolved_open[0]
                        if len(unresolved_open) == 1
                        # 隐式补齐只在流程确实在等补拍时生效：一次上传多页的
                        # 批次里，未标"补拍第N页"的后一页是**新的一页**，不是
                        # 前一页的重拍（issue 04 用原始三张教材页实测）。
                        and state.wait_reason == "unclear_page"
                        and (not user.content.strip() or "补拍" in user.content)
                        else None
                    )
                )
                if replacement is not None:
                    plans[attachment.object_id] = (
                        replacement.ordinal,
                        [*replacement.replaced_object_ids, replacement.object_id],
                    )
                    unresolved_open = [
                        page
                        for page in unresolved_open
                        if page.ordinal != replacement.ordinal
                    ]
                else:
                    plans[attachment.object_id] = (next_ordinal, [])
                    next_ordinal += 1
                candidates.append(
                    StudyPageCandidate(
                        object_id=attachment.object_id,
                        content_hash=attachment.content_hash,
                        media_type=attachment.media_type,
                    )
                )

            fix_message = ""
            if candidates:
                prior_fragments = [
                    fragment for page in state.pages for fragment in page.fragments
                ]

                def on_page(page: StudyPage) -> StudyPage:
                    nonlocal added
                    ordinal, replaced = plans.get(page.object_id, (next_ordinal, []))
                    page = page.model_copy(
                        update={
                            "ordinal": ordinal,
                            "replaced_object_ids": replaced,
                            "model_id": (
                                (last_lock.actual_model_id or "") if last_lock else ""
                            ),
                            "same_section": page.same_section if state.pages else True,
                        }
                    )
                    if state.pages and not page.same_section and not any(
                        issue.position == "整页" for issue in page.unclear
                    ):
                        page.unclear.append(
                            StudyUnclear(
                                position="整页",
                                reason="疑似不同小节，请确认或在新对话上传",
                            )
                        )
                    if replaced:
                        # 补拍替换：旧页原识别片段移入历史（superseded_fragments），
                        # 新页片段与旧原文双方来源都保留；旧片段不再进入范围映射
                        # 与当前书页依据。新页未通过关键疑点门时不切有效版本。
                        previous = state.pages[ordinal - 1]
                        page = page.model_copy(
                            update={
                                "superseded_fragments": [
                                    *previous.fragments,
                                    *previous.superseded_fragments,
                                ],
                            }
                        )
                        state.pages[ordinal - 1] = page
                    else:
                        state.pages.append(page)
                    known.add(page.content_hash)
                    added += 1
                    # 节点收据完成后到领域材料提交之间仍可能停止/转移租约。
                    # 复用本轮固定快照，在领域写事务内再次守卫。
                    with self._repo.database.transaction():
                        decision = material_guard.verify()
                        if not decision.ok:
                            raise StudyWorkflowError(
                                NODE_RECOGNIZE_PAGE,
                                "stopped" if decision.code == "run_stopped" else decision.code,
                                decision.message,
                            )
                        save_state_in_transaction()
                    return page

                try:
                    outcome = recognition.recognize(
                        candidates,
                        known_before,
                        on_page=on_page,
                        prior_fragments=prior_fragments,
                    )
                except NodeFailureError as exc:
                    raise StudyWorkflowError(exc.node, exc.code, exc.message) from exc
                except StoppedError as exc:
                    raise StudyWorkflowError(exc.node, "stopped", STUDY_STOPPED_TEXT) from exc
                except SupersededError as exc:
                    raise StudyWorkflowError(
                        NODE_RECOGNIZE_PAGE,
                        exc.code,
                        "本轮生成已被其他尝试取代，结果未提交。",
                    ) from exc
                fix_message = outcome.fix_message
                if outcome.pending_object_ids:
                    # 预算/批量限制：已完成页保留，未处理页仍是待处理，
                    # 不进入映射/预习，也不宣布整节已读。
                    recognized_ids = {page.object_id for page in state.pages}
                    state.pending_object_ids = list(dict.fromkeys(
                        object_id
                        for object_id in [*state.pending_object_ids, *outcome.pending_object_ids]
                        if object_id not in recognized_ids
                    ))
                    save_state()
                    raise StudyWorkflowError(
                        NODE_RECOGNIZE_PAGE,
                        "run_budget_exhausted",
                        "本轮运行预算不足以识别全部书页；已完成的书页已保存，"
                        "未处理页仍待识别。请重试本条消息继续。",
                    )
            recognized_ids = {page.object_id for page in state.pages}
            state.pending_object_ids = [
                object_id
                for object_id in state.pending_object_ids
                if object_id not in recognized_ids
            ]
            if state.pending_object_ids:
                # 未完成页仍然待处理：本轮即使上传了新页也不进入映射/
                # 预习，更不宣布整节已读；旧页只能靠重试原消息继续。
                save_state()
                return {
                    "wait": True,
                    "answer": "本轮新书页已保存；上一轮还有书页未识别完成，"
                    "请重试原消息继续识别剩余页。",
                }
            if not attachments and user.content.strip() and state.wait_reason == "unclear_page":
                match = re.search(r"第\s*(\d+)\s*页", user.content)
                if match:
                    ordinal = int(match.group(1))
                    supplement_page = next(
                        (item for item in state.pages if item.ordinal == ordinal), None
                    )
                    if (supplement_page is not None and supplement_page.unclear
                            and supplement_page.same_section):
                        matches = [
                            issue
                            for issue in supplement_page.unclear
                            if issue.position in user.content
                        ]
                        positions = {issue.position for issue in matches}
                        supplement = (
                            user.content.split(matches[0].position, 1)[1].strip()
                            if len(positions) == 1 else ""
                        )
                        if re.fullmatch(
                            r"(?:[:：]|[^？?。\n]*[是为])\s*\S.*", supplement, flags=re.DOTALL,
                        ) and not re.search(
                            r"[？?]|是不是|是啥|是什么|不知道|看不清|不清楚", supplement,
                        ):
                            issue = matches[0]
                            supplement_page.fragments.append(
                                StudyFragment(
                                    fragment_id=f"user:{user.message_id}",
                                    kind="text",
                                    position=issue.position,
                                    text=user.content,
                                    confidence=1,
                                    source="user",
                                    # 文字补录标用户补充，不冒充照片识别。
                                    recognition_path="user",
                                )
                            )
                            supplement_page.unclear = [
                                doubt for doubt in supplement_page.unclear
                                if doubt.position != issue.position
                            ]
                            supplemented = True
                            save_state()
            unresolved = [page for page in state.pages if page.unclear]
            if unresolved:
                state.stage = "awaiting_pages"
                state.wait_reason = "unclear_page"
                state.pending_object_ids = []
                save_state()
                self._repo.update_generation_progress(
                    run.account_id, run.run_id, wait_reason=state.wait_reason
                )
                details = "；".join(
                    f"第{page.ordinal}页{issue.position}：{issue.reason}"
                    for page in unresolved
                    for issue in page.unclear
                )
                fix_text = fix_message or (
                    f"这些位置还看不清：{details}。"
                    "请补拍对应位置，或按“第N页+位置：具体内容”补录文字。"
                    "若确认是同节照片，请回复“确认第N页属于本节”；"
                    "不同小节的书页请在新对话上传。"
                )
                return {
                    "wait": True,
                    "answer": (
                        (f"检测到{duplicates}张重复书页，已跳过。" if duplicates else "")
                        + fix_text
                    ),
                }
            numbered = [page for page in state.pages if page.page_number is not None]
            if any(
                left.page_number is not None and right.page_number is not None
                and left.page_number > right.page_number
                for left, right in zip(numbered, numbered[1:], strict=False)
            ):
                state.stage = "awaiting_pages"
                state.wait_reason = "page_order"
                save_state()
                self._repo.update_generation_progress(
                    run.account_id, run.run_id, wait_reason=state.wait_reason,
                )
                return {
                    "wait": True,
                    "answer": "书上页码与上传顺序不一致，请查看页级证据，"
                    "按当前上传页号发送完整顺序，例如“页序：2,1”。",
                }
            if duplicates and not added and not supplemented and run.attempt_number == 1:
                state.stage = previous_stage
                save_state()
                return {"wait": True, "answer": "书页重复，请检查页序后重新发送。"}
            if not state.pages:
                return {"wait": True, "answer": "请上传本节书页照片后开始预习。"}
            return {"wait": False}

        def map_units() -> _GraphState:
            fragments = [
                ScopeFragment(
                    fragment_id=fragment.fragment_id,
                    page_ordinal=page.ordinal,
                    kind=fragment.kind,
                    position=fragment.position,
                    text=fragment.text,
                    source=fragment.source,
                )
                for page in state.pages
                for fragment in page.fragments
                if fragment.confidence >= 0.7
                and not (
                    fragment.source == "photo"
                    and any(
                        correction.source == "user"
                        and correction.position == fragment.position
                        for correction in page.fragments
                    )
                )
            ]
            material = ScopeMaterial.build(pages=state.pages, fragments=fragments)
            try:
                outcome = scope_recognition.map_scope(material, prior_scope=state.scope)
                if outcome.scope is None and outcome.failure_code in {
                    "study_map_invalid",
                    GATE_SCOPE_INCOMPLETE,
                    GATE_SCOPE_CONFLICT,
                    GATE_SCOPE_UNVERIFIED,
                } and budget.begin_adjustment(reason_code=outcome.failure_code):
                    # 一次有界修复：带着失败反馈回到受影响映射；禁止新增知识点
                    # 凑覆盖，也不放宽材料/覆盖规则。再失败即如实报错。
                    prior_count = len(outcome.failure_detail.get("units", [])) or len(
                        state.units
                    )
                    repair: dict[str, Any] = {
                        "code": outcome.failure_code,
                        "message": outcome.failure_message,
                    }
                    if prior_count:
                        repair["prior_unit_count"] = prior_count
                    try:
                        outcome = scope_recognition.map_scope(
                            material, repair=repair, prior_scope=state.scope
                        )
                    finally:
                        budget.end_adjustment(
                            outcome_code=outcome.failure_code or "study_scope_repaired"
                        )
            except NodeFailureError as exc:
                raise StudyWorkflowError(exc.node, exc.code, exc.message) from exc
            except StoppedError as exc:
                raise StudyWorkflowError(exc.node, "stopped", STUDY_STOPPED_TEXT) from exc
            except SupersededError as exc:
                raise StudyWorkflowError(
                    current_node,
                    exc.code,
                    "本轮生成已被其他尝试取代，结果未提交。",
                ) from exc
            if outcome.scope is None:
                raise StudyWorkflowError(
                    current_node,
                    outcome.failure_code or "study_scope_failed",
                    outcome.failure_message or "知识范围核验未通过，请重试。",
                )
            scope = outcome.scope
            if (
                state.scope is not None
                and state.scope.scope_version_id != scope.scope_version_id
            ):
                history = [
                    item
                    for item in state.scope_history
                    if item.scope_version_id != state.scope.scope_version_id
                ]
                state.scope_history = [state.scope, *history]
            state.scope = scope
            state.units = scope.units
            state.stage = "preview"
            state.wait_reason = None
            # 范围核验通过即持久为 preview 阶段；预习问题与 tutoring 阶段
            # 仍只在消息终态事务提交，停止/失败不提前进入辅导。
            with self._repo.database.transaction():
                decision = material_guard.verify()
                if not decision.ok:
                    raise StudyWorkflowError(
                        current_node,
                        "stopped" if decision.code == "run_stopped" else decision.code,
                        decision.message,
                    )
                save_state_in_transaction()
            return {}

        def preview() -> _GraphState:
            scope = state.scope
            if scope is None:
                raise StudyWorkflowError(
                    current_node, "study_scope_missing", "有效知识范围缺失，请重试。"
                )
            try:
                questions = scope_recognition.preview_scope(
                    scope, policy_block=preview_policy_block()
                )
            except NodeFailureError as exc:
                raise StudyWorkflowError(exc.node, exc.code, exc.message) from exc
            except StoppedError as exc:
                raise StudyWorkflowError(exc.node, "stopped", STUDY_STOPPED_TEXT) from exc
            except SupersededError as exc:
                raise StudyWorkflowError(
                    current_node,
                    exc.code,
                    "本轮生成已被其他尝试取代，结果未提交。",
                ) from exc
            state.questions = questions
            state.stage = "tutoring"
            state.wait_reason = None
            scope_text = "、".join(
                f"{unit.title}（"
                + "、".join(
                    f"第{page.ordinal}页{fragment.position}"
                    for page in state.pages
                    for fragment in page.fragments
                    if fragment.fragment_id in unit.fragment_ids
                )
                + "）"
                for unit in state.units
            )
            questions_text = "\n".join(
                f"{index}. {question.question}" for index, question in enumerate(questions, 1)
            )
            return {
                "answer": (
                    (f"检测到{duplicate_count}张重复书页，已跳过。\n\n" if duplicate_count else "")
                    + f"已识别本节范围：{scope_text}\n\n"
                    f"预习时可以带着这些问题阅读，暂不需要作答：\n{questions_text}"
                ),
                # 阶段推进与问题一起在消息终态事务内提交：生成完不等于已提交，
                # 停止/提交失败时数据库只停在 preview 阶段，重试不会重复预习。
                "scope": state.model_dump(),
            }

        def finish_pages() -> _GraphState:
            state.stage = "tutoring"
            state.wait_reason = None
            state.page_update = None
            state.questions = committed.questions
            if state.review:
                _mark_active_unanswered(state.review)
                state.review.needs_replan = True
                state.review.complete = False
            # 书页范围变了：旧总结不再对应当前知识范围与题目，移入历史并
            # 标注原范围版本；当前总结置空，待重排后按新范围重新生成，
            # 后续总结不冒用旧版本结论。优先采用总结自身绑定的生成版本
            # （工单 36），旧状态缺失时回退到运行启动时的有效范围。
            if state.summary is not None:
                state.summary_history = [
                    *state.summary_history,
                    StudySummaryRecord(
                        summary=state.summary,
                        scope_version_id=(
                            state.summary.scope_version_id
                            or (committed.scope.scope_version_id if committed.scope else "")
                        ),
                        superseded_reason="追加或补拍书页使本节知识范围更新",
                    ),
                ]
                state.summary = None
            return {"updated_pages": state.model_dump(), "answer": (
                (f"检测到{duplicate_count}张重复书页，已跳过。\n\n" if duplicate_count else "")
                + f"已更新本节书页，共{len(state.pages)}页。"
                "知识范围：" + "、".join(unit.title for unit in state.units)
                + "。原有辅导问答、来源与已判定题已保留；原总结已作为历史保留，"
                "待按新范围重新复盘后生成新总结，可以继续提问。"
            )}

        def tutoring() -> _GraphState:
            if state.stage == "review" and state.review:
                # 提问/要求再讲等同暂停当前题：记录已呈现未答，不把讲解当答案。
                _mark_active_unanswered(state.review)
            state.stage = "tutoring"
            existing = next((item for item in state.tutoring
                             if item.user_message_id == run.user_message_id), None)
            if existing is not None:
                return {"answer": existing.answer, "tutoring": existing.model_dump()}
            try:
                exchange = tutor(
                    self._service,
                    run,
                    state,
                    user.content,
                    invoke,
                    stop_event,
                    budget=budget,
                )
            except ValueError as exc:
                raise StudyWorkflowError(
                    current_node, "study_tutor_invalid", "辅导依据或结果未通过核验，请重试。"
                ) from exc
            return {"answer": exchange.answer, "tutoring": exchange.model_dump()}

        intent = review_intent(user.content)

        expected_review_state = committed.model_dump()

        def _verify_material_commit(
            expected: dict[str, Any],
            code: str,
            message: str,
            *,
            exclude: set[str] | None = None,
            allow_message_terminal: bool = False,
        ) -> None:
            """写事务内核对停止、执行权与预期有效状态，拒绝迟到覆盖。

            停止以显式信号为准；租约转移/运行终态由共享守卫在消息检查前
            拒绝。``finalize_message`` 已在同一事务内把消息收敛到目标终态，
            因此仅追加页提交豁免 ``message_terminal``。
            """
            if stop_event is not None and stop_event.is_set():
                raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
            decision = material_guard.verify()
            if allow_message_terminal and decision.code == "message_terminal":
                # 终态回调中消息已收敛，但跨进程持久停止仍必须生效。
                current_run = self._repo.get_generation_run(run.account_id, run.run_id)
                if current_run is not None and current_run.stop_requested:
                    raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
            if (
                not decision.ok
                and not (allow_message_terminal and decision.code == "message_terminal")
            ):
                raise StudyWorkflowError(
                    current_node,
                    "stopped" if decision.code == "run_stopped" else decision.code,
                    decision.message,
                )
            latest = self._states.get(run.account_id, run.conversation_id)
            if latest is None or latest.model_dump(exclude=exclude) != expected:
                raise StudyWorkflowError(current_node, code, message)

        def _verify_update_commit(*, allow_message_terminal: bool = False) -> None:
            """追加页原子切换：有效状态（不含待提交候选）未变且执行权在握。"""
            _verify_material_commit(
                committed.model_dump(exclude={"page_update", "pending_object_ids"}),
                "study_page_update_changed",
                "小节状态已变化，本次追加结果未提交，请重试。",
                exclude={"page_update", "pending_object_ids"},
                allow_message_terminal=allow_message_terminal,
            )

        def _verify_review_commit() -> None:
            """写事务内核对执行权及预期领域版本，拒绝迟到覆盖。"""
            _verify_material_commit(
                expected_review_state,
                "study_review_scope_changed",
                "小节或题目状态已变化，迟到结果未提交，请重试。",
            )

        def _save_review_content(content: str) -> None:
            """事务内只追加已核验正文，保存游标事件供错误/停止后重放。"""
            message = self._repo.get_message(run.account_id, run.assistant_message_id)
            assert message is not None, "提交守卫已验证助手消息存在"
            if not content.startswith(message.content):
                raise ReviewGradeError(
                    "study_grade_invalid", "已提交反馈与恢复正文不一致，已保留原记录。"
                )
            delta = content[len(message.content):]
            if not delta:
                return
            self._repo.update_message_content_in_transaction(
                run.account_id, run.assistant_message_id, content, datetime.now(UTC)
            )
            self._repo.append_generation_event_in_transaction(
                run.account_id, run.run_id, "delta",
                ChatStreamDeltaData(
                    message_id=run.assistant_message_id, delta=delta
                ).model_dump(mode="json"), datetime.now(UTC),
            )

        def _save_committed(content: str | None = None) -> None:
            """在自己的提交边界内保存领域状态；租约/停止/版本失效即拒绝。"""
            nonlocal expected_review_state
            with NodeKernelRepository(self._repo.database).transaction():
                _verify_review_commit()
                save_state_in_transaction()
                if content is not None:
                    _save_review_content(content)
            expected_review_state = state.model_dump()

        def _current_question() -> Any:
            if state.review is None:
                return None
            return active_question(state.review)

        def _commit_judgement(outcome: Any) -> None:
            """判定提交边界：答案、判定、反馈产物与来源消息同事务落库。"""
            question = _current_question()
            if question is None:
                raise StudyWorkflowError(
                    current_node, "study_review_invalid", "当前复盘题不存在，已保留原阶段。"
                )
            nonlocal expected_review_state
            with NodeKernelRepository(self._repo.database).transaction():
                _verify_review_commit()
                if (
                    outcome.question_id != question.question_id
                    or outcome.scope_version_id != (
                        question.scope_version_id or (
                            state.review.scope_version_id if state.review else ""
                        )
                    )
                    or outcome.user_message_id != run.user_message_id
                ):
                    raise ReviewGradeError(
                        "study_grade_invalid", "判定与当前题或来源不一致，已保留当前题。"
                    )
                question.answer = user.content
                question.judgement = outcome.judgement
                question.canonical_answer = outcome.canonical_answer
                question.explanation = outcome.explanation
                question.feedback = outcome.feedback
                question.grade_record = outcome.record
                question.user_message_id = run.user_message_id
                save_state_in_transaction()
                _save_review_content(outcome.feedback)
            expected_review_state = state.model_dump()

        def _commit_presentation(feedback: str) -> str | None:
            """下一题呈现提交边界：选中下一题不算呈现，落库才算。"""
            if state.review is None:
                return None
            text = advance_question(state.review)
            _save_committed(f"{feedback}\n\n{text}" if text else feedback)
            return text

        def _grade_new_answer() -> str:
            """判定新作答：登记节点提交判定产物，领域层按产物幂等应用。"""
            question = _current_question()
            if question is None or question.judgement is not None:
                raise StudyWorkflowError(
                    current_node, "study_review_invalid", "当前没有待判定的复盘题，已保留原阶段。"
                )
            grading = ReviewGradeKernel(
                self._service, run, invoke,
                stop_event=stop_event, event_sink=kernel_event,
                commit_outcome=_commit_judgement,
            )
            question = grading.question(state)
            # 收到合法当前题答案是独立事实，判定失败也保留来源关联。
            question.answer = user.content
            question.user_message_id = run.user_message_id
            _save_committed()
            outcome = grading.grade(state, user.content)
            if question.judgement is None:
                # 完成产物复用时只幂等回填，不重新判定。
                _commit_judgement(outcome)
            presentation = _commit_presentation(outcome.feedback)
            parts = [outcome.feedback]
            if presentation:
                parts.append(presentation)
            return "\n\n".join(parts)

        def _replay_judged(question: Any) -> str:
            """同一来源消息已提交判定：重放反馈，不重新调用判定。"""
            feedback = render_feedback(question)
            review = state.review
            if review is None or review.complete:
                return feedback
            if review.active_question_id:
                current = _current_question()
                if current is not None and current is not question:
                    return f"{feedback}\n\n{render_question_prompt(review, current)}"
                # 激活题仍是已判定题：呈现提交曾中断，补提交下一题。
            presentation = _commit_presentation(feedback)
            return f"{feedback}\n\n{presentation}" if presentation else feedback

        def finish_review(answer: str) -> str:
            """全部判定后单独执行总结；总结失败不回滚已提交的判定与反馈。"""
            if state.summary is None:
                try:
                    summary = run_node(
                        "study.summarize",
                        lambda: build_summary(self._service, run, state, invoke),
                    )
                except ValueError as exc:
                    raise StudyWorkflowError(
                        "study.summarize", "study_summary_invalid",
                        "总结结果未通过核验，复盘判定与题目反馈已保留，请重试。",
                    ) from exc
                state.summary = summary
                state.stage = "summary"
                _save_committed()
            elif state.stage != "summary":
                state.stage = "summary"
                _save_committed()
            text = render_summary(state.summary, state)
            return f"{answer}\n\n{text}" if answer else text

        def plan_with_repair() -> Any:
            """出题前核验失败时按公共预算做一次有界修复；再失败不上报坏题。"""
            planner = ReviewPlanKernel(
                self._service, run, invoke,
                stop_event=stop_event, event_sink=kernel_event,
            )
            try:
                return planner.plan(state)
            except ReviewPlanError as exc:
                if not budget.begin_adjustment(reason_code=exc.code):
                    raise
                outcome_code = exc.code
                try:
                    repaired = planner.plan(
                        state,
                        repair={"code": exc.code, "message": exc.repair_detail},
                    )
                    outcome_code = "study_review_repaired"
                    return repaired
                finally:
                    budget.end_adjustment(outcome_code=outcome_code)

        def review() -> _GraphState:
            committed = False
            try:
                if intent == "pause":
                    state.stage = "tutoring"
                    _mark_active_unanswered(state.review)
                    answer = (
                        "已暂停复盘，可以继续提问或追加本节照片。已问题与判定保留；"
                        "继续复盘时从未问题开始，未作答题不计为已掌握。"
                    )
                elif intent == "start":
                    if state.review is None or state.review.needs_replan:
                        state.review = plan_with_repair()
                    state.stage = "review"
                    if state.review.active_question_id:
                        current = next(item for item in state.review.questions
                                       if item.question_id == state.review.active_question_id)
                        if current.judgement is not None:
                            # 提交后停止的明确继续：保留反馈，只补呈现，不要求重答。
                            answer = _replay_judged(current)
                            committed = True
                        else:
                            answer = f"请回答当前复盘题：{current.question}"
                            if current.conditions:
                                answer += f"\n\n{current.conditions}"
                    elif state.review.complete:
                        # 复盘计划已完成：复述总结（缺失时在此补齐），不再出题。
                        answer = ""
                    else:
                        answer = next_question(state.review)
                else:
                    replay_question = (
                        judged_by_message(state.review, run.user_message_id)
                        if state.review is not None
                        else None
                    )
                    if replay_question is not None:
                        # 已提交合法判定不因重试重新生成：按来源消息重放反馈。
                        answer = _replay_judged(replay_question)
                    else:
                        answer = _grade_new_answer()
                    committed = True
                if intent != "pause" and state.review is not None and state.review.complete:
                    answer = finish_review(answer)
            except ReviewPlanError as exc:
                raise StudyWorkflowError(
                    current_node, exc.code, exc.message
                ) from exc
            except ReviewGradeError as exc:
                raise StudyWorkflowError(
                    current_node, exc.code, exc.message
                ) from exc
            except StoppedError as exc:
                raise StudyWorkflowError(exc.node, "stopped", STUDY_STOPPED_TEXT) from exc
            except SupersededError as exc:
                raise StudyWorkflowError(
                    current_node, exc.code, "学习运行已失效，原书页与历史已保留。"
                ) from exc
            except ValueError as exc:
                raise StudyWorkflowError(
                    current_node, "study_review_invalid",
                    "复盘结果未通过核验，原题已保留，请重试。",
                ) from exc
            if committed:
                return {"answer": answer, "reviewed_committed": True}
            return {"answer": answer, "reviewed": state.model_dump()}

        graph = StateGraph(_GraphState)
        graph.add_node("study.recognize", node("study.recognize", recognize))
        graph.add_node("study.map", node("study.map", map_units))
        graph.add_node("study.preview", node("study.preview", preview))
        graph.add_node("study.tutor", node("study.tutor", tutoring))
        graph.add_node("study.finish_pages", node("study.finish_pages", finish_pages))
        graph.add_node("study.plan_review", node("study.plan_review", review))
        graph.add_node("study.grade", node("study.grade", review))
        graph.add_node("study.pause_review", node("study.pause_review", review))
        replay_judged = bool(
            state.review is not None
            and judged_by_message(state.review, run.user_message_id) is not None
        )
        entry = "study.recognize"
        if state.stage in {"tutoring", "review", "summary"} and not updating and not attachments:
            if intent == "pause":
                entry = "study.pause_review"
            elif intent == "start":
                entry = "study.plan_review"
            elif intent == "tutor":
                entry = "study.tutor"
            elif replay_judged and state.stage in {"review", "summary"}:
                # 判定/反馈已提交但终态未达：只重放与补齐呈现/总结。
                entry = "study.grade"
            elif state.stage == "review" and state.review and not state.review.complete:
                entry = "study.grade"
            else:
                entry = "study.tutor"
        graph.add_edge(START, entry)
        graph.add_conditional_edges(
            "study.recognize",
            lambda result: END if result.get("wait") else "study.map",
            {END: END, "study.map": "study.map"},
        )
        graph.add_edge("study.map", "study.finish_pages" if updating else "study.preview")
        graph.add_edge("study.preview", END)
        graph.add_edge("study.finish_pages", END)
        graph.add_edge("study.tutor", END)
        for name in ("study.plan_review", "study.grade", "study.pause_review"):
            graph.add_edge(name, END)
        saver = RepositoryCheckpointSaver(
            self._repo.database,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
        )
        compiled = graph.compile(checkpointer=saver)
        config: RunnableConfig = {
            "configurable": {
                "thread_id": run.conversation_id,
                "checkpoint_ns": run.run_id,
            }
        }
        try:
            output = compiled.invoke(
                None if saver.get_tuple(saver.run_config()) is not None else {}, config
            )
            answer = output.get("answer") or "已保存书页，请继续补拍。"
            if stop_event is not None and stop_event.is_set():
                raise StudyWorkflowError(current_node, "stopped", STUDY_STOPPED_TEXT)
            def persist_tutoring() -> None:
                if output.get("reviewed"):
                    self._states.save_in_transaction(
                        run.account_id, run.conversation_id,
                        StudyState.model_validate(output["reviewed"]),
                    )
                if output.get("scope"):
                    # 范围版本与预习问题只随消息终态事务提交：提交失败即整体
                    # 回滚，重放/重试复用持久节点产物，不重复预习也不错误推进。
                    self._states.save_in_transaction(
                        run.account_id, run.conversation_id,
                        StudyState.model_validate(output["scope"]),
                    )
                if output.get("updated_pages"):
                    # 追加页的原子版本切换：执行权与有效版本守卫通过才替换
                    # 有效书页/范围，失败整体回滚并保留原有效版本。
                    _verify_update_commit(allow_message_terminal=True)
                    self._states.save_in_transaction(
                        run.account_id, run.conversation_id,
                        StudyState.model_validate(output["updated_pages"]),
                    )
                if output.get("tutoring"):
                    exchange = StudyExchange.model_validate(output["tutoring"])
                    if not any(item.user_message_id == exchange.user_message_id
                               for item in state.tutoring):
                        state.tutoring.append(exchange)
                    self._states.save_in_transaction(run.account_id, run.conversation_id, state)

            review_committed = bool(output.get("reviewed_committed"))
            if not output.get("reviewed") and not output.get("scope") and not review_committed:
                self._repo.update_message_content(
                    run.account_id, run.assistant_message_id, answer, datetime.now(UTC)
                )
            self._finalize_message(
                run,
                status=ChatMessageStatus.DONE,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=last_lock.actual_model_id if last_lock else None,
                lock=last_lock,
                started=started,
                now=datetime.now(UTC),
                persist_learning=persist_tutoring,
                final_content=(
                    answer
                    if output.get("reviewed")
                    or output.get("scope")
                    or review_committed
                    else None
                ),
            )
            return None
        except StudyWorkflowError as exc:
            if updating and _is_recognition_node(current_node) and exc.code not in {
                "stopped", "lease_lost", "lease_expired", "run_terminal", "run_missing",
                "message_terminal", "message_missing", "task_version_changed",
                "study_page_update_changed",
            }:
                state.wait_reason = "recognition_failed"
                save_state()
            message_status = (
                ChatMessageStatus.STOPPED if exc.code == "stopped" else ChatMessageStatus.ERROR
            )
            self._finalize_message(
                run,
                status=message_status,
                error_code=exc.code,
                error_message=_public_failure_message(exc.code),
                duration_ms=None,
                model_id=failure_lock.actual_model_id if failure_lock else None,
                lock=failure_lock,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(initial_thinking(ChatMode.STUDY), exc.code),
            )
            on_event(StreamEvent(kind="error", error_code=exc.code,
                                 error_message=_public_failure_message(exc.code)))
            return "error"
        except Exception:
            if updating and _is_recognition_node(current_node):
                state.wait_reason = "recognition_failed"
                save_state()
            self._finalize_message(
                run,
                status=ChatMessageStatus.ERROR,
                error_code="study_internal_error",
                error_message=_public_failure_message("study_internal_error"),
                duration_ms=None,
                model_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(initial_thinking(ChatMode.STUDY), "study_internal_error"),
            )
            on_event(
                StreamEvent(
                    kind="error",
                    error_code="study_internal_error",
                    error_message=_public_failure_message("study_internal_error"),
                )
            )
            return "error"
