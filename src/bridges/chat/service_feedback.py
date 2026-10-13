# mypy: disable-error-code="misc"

"""聊天服务的 feedback 方法。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.chat.repository import (
    GenerationEventRecord,
    GenerationRunRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.contracts.chat import (
    ChatMessageProjection,
    ChatMessageRole,
    ChatRunView,
)
from bridges.contracts.feedback import (
    AnswerFeedback,
    AnswerFeedbackRequest,
    FeedbackKind,
    FeedbackStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.teaching_progress import (
    LearningProgressProjection,
    PlanAdjustment,
)

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceFeedbackMixin:
    def message_projection(
        self: ChatService, account_id: str, message_id: str
    ) -> ChatMessageProjection | None:
        """返回单条消息投影（供流式完成事件与测试读取最新状态）。"""
        message = self._repo.get_message(account_id, message_id)
        if message is None:
            return None
        return self._project_message(message)
    def _model_history(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        until_user_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """组装发送给模型的会话历史（委托给回合编排模块，语义不变）。

        首条为当前对话模式的系统角色合同；每轮用户消息只带最新一条已完成
        的助手回答；失败、停止与进行中的尝试不进上下文。``until_user_message_id``
        把历史截断到指定轮次（重试旧轮次失败消息时，新尝试的上下文不得
        包含其后的后续轮次）。
        """
        return self._turn._model_history(  # noqa: SLF001 - 编排内部契约经服务暴露
            account_id, conversation_id, until_user_message_id
        )
    def submit_feedback(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        message_id: str,
        request: AnswerFeedbackRequest,
    ) -> AnswerFeedback:
        """记录一条回答反馈（幂等，失败重试不重复写入，不丢失反馈）。

        ``answer_inappropriate`` 反馈针对回答本身；``profile_incorrect``
        定位到上下文说明披露中的画像记录。反馈按账户隔离持久化。
        """
        message = self._repo.get_message(account_id, message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.role != ChatMessageRole.ASSISTANT
        ):
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if request.kind == FeedbackKind.PROFILE_INCORRECT:
            if request.assertion_id is None:
                raise ChatDomainError(
                    "assertion_required",
                    "标记画像有误时请先选择对应的画像记录。",
                    422,
                )
            if self._profiles is not None:
                try:
                    self._profiles.get_assertion(account_id, request.assertion_id)
                except Exception as exc:  # noqa: BLE001 - 跨账户/不存在统一安全 404
                    raise ChatDomainError(
                        "assertion_not_found", "画像记录不存在或没有访问权限。", 404
                    ) from exc
        career_item_ref = (
            request.career_item_ref.strip() if request.career_item_ref else None
        )
        if career_item_ref is not None and message.career_planning is None:
            raise ChatDomainError(
                "career_item_not_found",
                "该消息不是生涯规划结果，无法定位到具体条目。",
                422,
            )
        existing = self._repo.find_duplicate_feedback(
            account_id,
            message_id,
            request.kind,
            request.assertion_id,
            career_item_ref,
            request.feedback_text.strip(),
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        feedback = AnswerFeedback(
            feedback_id=secrets.token_urlsafe(16),
            account_id=account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            kind=request.kind,
            feedback_text=request.feedback_text.strip(),
            preference=(
                request.preference.strip() if request.preference is not None else None
            ),
            assertion_id=request.assertion_id,
            career_item_ref=career_item_ref,
            status=FeedbackStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
        )
        saved = self._repo.save_feedback(feedback)
        if message.teaching is not None:
            feedback_signal = " ".join(
                value for value in (saved.feedback_text, saved.preference) if value
            )
            if any(token in feedback_signal for token in ("太快", "太难", "多练习", "多练")):
                self._teaching_progress.apply_feedback(
                    account_id,
                    conversation_id,
                    f"feedback:{saved.feedback_id}",
                    feedback_signal,
                )
        if self._observability is not None:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.ANSWER_FEEDBACK,
                result=AuditResult.SUCCESS,
                object_refs=[message_id, feedback.feedback_id],
                reason="用户提交回答反馈。",
                details={
                    "kind": request.kind.value,
                    "assertion_id": request.assertion_id,
                    "career_item_ref": career_item_ref,
                    "has_preference": request.preference is not None,
                },
            )
        return saved
    def list_feedback(
        self: ChatService, account_id: str, conversation_id: str
    ) -> list[AnswerFeedback]:
        """返回对话内该账户的反馈记录（最新在前，供恢复与闭环查看）。"""
        return self._repo.list_feedback(account_id, conversation_id)
    def learning_progress(
        self: ChatService, account_id: str, conversation_id: str
    ) -> LearningProgressProjection | None:
        """返回当前账户在对话内的轻量学习进度。"""
        conversation = self._repo.get_conversation(account_id, conversation_id)
        if conversation is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        return self._teaching_progress.get_learning_progress(account_id, conversation_id)
    def learning_adjustments(
        self: ChatService, account_id: str, conversation_id: str
    ) -> list[PlanAdjustment]:
        """返回当前账户在对话内的计划调整记录。"""
        conversation = self._repo.get_conversation(account_id, conversation_id)
        if conversation is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        return self._teaching_progress.list_adjustments(account_id, conversation_id)
    def resolve_feedback(
        self: ChatService,
        account_id: str,
        feedback_id: str,
        resolution_note: str,
    ) -> AnswerFeedback:
        """把一条反馈标记为已处理并记录修正说明（幂等）。"""
        feedback = self._repo.get_feedback(account_id, feedback_id)
        if feedback is None:
            raise ChatDomainError("feedback_not_found", "反馈不存在或没有访问权限。", 404)
        if feedback.status == FeedbackStatus.RESOLVED:
            return feedback
        updated = self._repo.mark_feedback_status(
            account_id,
            feedback_id,
            FeedbackStatus.RESOLVED,
            resolution_note.strip(),
            datetime.now(UTC),
        )
        if updated is None:
            raise ChatDomainError("feedback_not_found", "反馈不存在或没有访问权限。", 404)
        if self._observability is not None:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=AuditAction.FEEDBACK_RESOLVED,
                result=AuditResult.SUCCESS,
                object_refs=[feedback_id],
                reason="回答反馈已按修正说明处理。",
                details={"resolution_note": resolution_note.strip()},
            )
        return updated
    def run_view_of(
        self: ChatService, account_id: str, message_id: str
    ) -> ChatRunView | None:
        """返回消息关联运行的外部视图（游标/状态），未运行过返回 None。"""
        run = self._repo.get_run_by_message(account_id, message_id)
        if run is None:
            return None
        return self._run_view(run, account_id)
    def generation_events(
        self: ChatService, account_id: str, run_id: str, after_seq: int
    ) -> list[GenerationEventRecord]:
        """按游标读取运行上的持久化事件（SSE 订阅回放；账户隔离）。"""
        with self._repo.connection_lock():
            return self._repo.list_generation_events(account_id, run_id, after_seq)
    def generation_run(
        self: ChatService, account_id: str, run_id: str
    ) -> GenerationRunRecord | None:
        """按账户读取运行记录（订阅端点判断终态；跨账户返回 None）。"""
        with self._repo.connection_lock():
            return self._repo.get_generation_run(account_id, run_id)
    def performance_summary(
        self: ChatService, account_id: str, since: datetime | None = None
    ) -> dict[str, Any]:
        """本地性能摘要（Issue 06 T6）：p50/p95、超时率、阶段占比、重试。

        只聚合脱敏指标（毫秒/状态/类别），绝不返回用户内容；不建立任何
        遥测外传。用于本地性能观测与防代码回归（本地确定性适配器 p95
        首 token ≤ 2s、终态 ≤ 5s）。
        """
        return self._repo.performance_summary(account_id, since=since)
