# mypy: disable-error-code="misc"

"""聊天服务的画像方法。"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.chat.repository import MessageRecord
from bridges.contracts.atomic_profile import (
    AtomicProfileMaterialResult,
    AtomicProfileMemoryResult,
)
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
)
from bridges.contracts.feedback_correction import (
    FeedbackCorrectionKind,
    FeedbackCorrectionResult,
    FeedbackCorrectionRoute,
    FeedbackMemoryKind,
    FeedbackMemoryStatus,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profile_adoption import AdoptedProfileSlice
from bridges.contracts.profile_extraction import (
    ProfileCorrectionResult,
    ProfileCorrectionStatus,
    ProfilePreprocessResult,
)
from bridges.contracts.profiles import ProfileNotification
from bridges.profiles.feedback import AdoptedFeedbackTarget, route_feedback
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


def _memory_kind_for_feedback(kind: FeedbackCorrectionKind) -> FeedbackMemoryKind:
    """反馈类别对应的长期记忆操作种类（指向不明与本轮要求不写记忆）。"""

    if kind is FeedbackCorrectionKind.FACT_CORRECTION:
        return FeedbackMemoryKind.CORRECTION
    if kind is FeedbackCorrectionKind.STATE_CHANGE:
        return FeedbackMemoryKind.STATE_CHANGE
    if kind is FeedbackCorrectionKind.LONG_TERM_PREFERENCE:
        return FeedbackMemoryKind.PREFERENCE
    return FeedbackMemoryKind.NONE


class _ServiceProfilesMixin:
    def _feedback_route_for_turn(
        self: ChatService,
        *,
        account_id: str,
        messages: list[MessageRecord],
        content: str,
    ) -> tuple[FeedbackCorrectionRoute | None, dict[str, Any] | None]:
        """准入时确定性路由「对上一轮回答的反馈」（画像工单 12）。

        只对存在前置完成回答、且消息形态明确属于反馈（事实纠正、状态变化、
        长期偏好、本轮要求、表达投诉、指向不明）的消息路由；返回运行时路由
        与可随运行持久化的脱敏快照（原回答/原运行/原采用切片）。
        """

        if self._automatic_profiles is None or self._atomic_profiles is None:
            return None, None
        previous = next(
            (
                message
                for message in reversed(messages)
                if message.role == ChatMessageRole.ASSISTANT
                and message.status == ChatMessageStatus.DONE
            ),
            None,
        )
        if previous is None:
            return None, None
        run = self._repo.get_run_by_message(account_id, previous.message_id)
        config = dict(run.config or {}) if run is not None else {}
        adopted = self._stored_feedback_targets(config)
        session_message_ids = {
            message.message_id
            for message in messages
            if message.role == ChatMessageRole.USER
        }
        if session_message_ids:
            known_item_ids = {item.profile_item_id for item in adopted}
            adopted.extend(
                AdoptedFeedbackTarget(
                    profile_item_id=item.profile_item_id,
                    version=item.version,
                    text=item.text,
                    relation=item.fact_relation.value,
                )
                for item in self._atomic_profiles.list_items(account_id)
                if item.profile_item_id not in known_item_ids
                and session_message_ids.intersection(item.source_message_ids)
            )
        route = route_feedback(
            content,
            previous_answer=previous.content,
            adopted_items=adopted,
        )
        if route is None:
            return None, None
        snapshot: dict[str, Any] = {
            "kind": route.kind.value,
            "adjustment": (
                route.adjustment.value if route.adjustment is not None else None
            ),
            "clarification_required": route.requires_clarification,
            "memory_kind": FeedbackMemoryKind.NONE.value,
            "memory_status": FeedbackMemoryStatus.NOT_APPLICABLE.value,
            "original_message_id": previous.message_id,
            "original_run_id": run.run_id if run is not None else None,
            "original_slice_id": self._stored_feedback_slice_id(config),
            "created_at": datetime.now(UTC).isoformat(),
        }
        return route, snapshot

    @staticmethod
    def _stored_feedback_targets(
        config: dict[str, Any],
    ) -> list[AdoptedFeedbackTarget]:
        """从原回答运行配置读取实际采用条目快照（发送记录优先，编译快照兜底）。"""

        stored = config.get("adopted_profile_slice")
        if not isinstance(stored, dict) or not stored.get("adopted_items"):
            stored = config.get("adopted_profile_slice_compiled")
        if not isinstance(stored, dict):
            return []
        try:
            adopted = AdoptedProfileSlice.model_validate(stored)
        except Exception:  # noqa: BLE001 - 旧运行残留的非法快照不阻断路由
            return []
        return [
            AdoptedFeedbackTarget(
                profile_item_id=item.profile_item_id,
                version=item.version,
                text=item.fact_text,
                relation=item.relation.value,
            )
            for item in adopted.adopted_items
        ]

    @staticmethod
    def _stored_feedback_slice_id(config: dict[str, Any]) -> str | None:
        for key in ("adopted_profile_slice", "adopted_profile_slice_compiled"):
            stored = config.get(key)
            if not isinstance(stored, dict):
                continue
            slice_id = stored.get("slice_id")
            if isinstance(slice_id, str):
                return slice_id
        return None

    def _process_profile_effects(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        content: str,
        mode: ChatMode,
        run_id: str,
        feedback_route: FeedbackCorrectionRoute | None = None,
    ) -> ProfilePreprocessResult | None:
        """首轮/续轮的画像记忆副作用：处理消息并把通知追加为 profile 事件。

        Issue 26：确定性记忆意图处理（明确记住/不记/仅会话、许可内自动
        写入、敏感候选、单次情绪提示）。画像处理失败绝不阻断聊天主流程：
        记忆能力独立于回答生成，通知留待重试轮次补发。
        Issue 02：画像通知作为 profile 事件随运行持久化（started 之后），
        订阅者从游标回放即可即时展示，重开页面不重复下发。
        画像工单 12：``feedback_route`` 是本轮明确反馈的确定性路由；处理
        结果（写入/未写入/只调整）随运行配置持久化，与回答生成分别报告。
        """
        if self._automatic_profiles is not None:
            try:
                # 改进工单 17：回答前只同步处理显式控制（停止记录、记住/
                # 忘掉、明确纠正）；普通自然表达的提取由回答正常完成后的
                # 可恢复队列异步执行，不在提交前调用模型。
                result = self._automatic_profiles.process_synchronous_controls(
                    account_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    content=content,
                    run_id=run_id,
                    mode=mode.value,
                    feedback_route=feedback_route,
                )
                if result is not None and result.correction is not None:
                    self._persist_profile_correction_result(
                        account_id, run_id, result.correction
                    )
                if result is not None and result.memory is not None:
                    self._persist_profile_memory_result(
                        account_id, run_id, result.memory
                    )
                if result is not None and result.material is not None:
                    self._persist_profile_material_result(
                        account_id, run_id, result.material
                    )
                if result is not None and result.feedback is not None:
                    self._persist_profile_feedback_result(
                        account_id, run_id, result.feedback
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - 聊天主流程对画像提取保持 fail-open
                correction: ProfileCorrectionResult | None = None
                classification = ProfileSignalClassifier().classify(content)
                if classification.category == ProfileSignalCategory.CORRECTION:
                    intent = classification.correction_intent
                    correction = ProfileCorrectionResult(
                        status=ProfileCorrectionStatus.FAILED,
                        dimension=intent.dimension if intent is not None else None,
                    )
                    with contextlib.suppress(Exception):
                        self._persist_profile_correction_result(
                            account_id, run_id, correction
                        )
                if feedback_route is not None:
                    # 反馈处理异常：如实记为失败，回答照常给出，不声称已更新。
                    with contextlib.suppress(Exception):
                        self._persist_profile_feedback_result(
                            account_id,
                            run_id,
                            FeedbackCorrectionResult(
                                kind=feedback_route.kind,
                                adjustment=feedback_route.adjustment,
                                memory_kind=_memory_kind_for_feedback(
                                    feedback_route.kind
                                ),
                                memory_status=FeedbackMemoryStatus.FAILED,
                                target_item_id=feedback_route.target_item_id,
                                clarification_required=(
                                    feedback_route.requires_clarification
                                ),
                            ),
                        )
                if self._observability is not None:
                    error_code = str(
                        getattr(exc, "code", "profile_extraction_unexpected")
                    )
                    audit_result = (
                        AuditResult.RETRYABLE_FAIL
                        if bool(getattr(exc, "retryable", True))
                        else AuditResult.BLOCKED
                    )
                    with contextlib.suppress(Exception):
                        self._observability.log_audit(
                            actor_account_id=account_id,
                            action=AuditAction.PROFILE_AUTO_WRITE,
                            result=audit_result,
                            reason=error_code,
                            details={"stage": "preprocess", "outcome": "unhandled"},
                        )
                return None
        elif self._four_dimension_profiles is None and self._profiles is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - 辅助路径静默降级
                self._profiles.process_conversation_message(
                    account_id,
                    message_id=user_message_id,
                    conversation_id=conversation_id,
                    content=content,
                    mode=mode.value,
                )
        return None
    def _persist_profile_correction_result(
        self: ChatService,
        account_id: str,
        run_id: str,
        correction: ProfileCorrectionResult,
    ) -> None:
        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            return
        config = dict(run.config or {})
        config["profile_correction"] = correction.context_metadata()
        self._repo.update_generation_config(account_id, run_id, config)
    def _persist_profile_memory_result(
        self: ChatService,
        account_id: str,
        run_id: str,
        memory: AtomicProfileMemoryResult,
    ) -> None:
        """记录本轮记忆指令结果：重试轮次沿用同一结果，不重复声明成功。"""

        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            return
        config = dict(run.config or {})
        config["profile_memory"] = memory.context_metadata()
        self._repo.update_generation_config(account_id, run_id, config)
    def _persist_profile_material_result(
        self: ChatService,
        account_id: str,
        run_id: str,
        material: AtomicProfileMaterialResult,
    ) -> None:
        """记录本轮材料更新结果（工单 10）。

        与记忆指令结果共用 ``profile_memory`` 元数据键：两者互斥（同一轮
        至多一种指令），元数据里的 ``memory_kind`` 区分种类，模型上下文
        按同一读取路径如实渲染。
        """

        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            return
        config = dict(run.config or {})
        config["profile_memory"] = material.context_metadata()
        self._repo.update_generation_config(account_id, run_id, config)
    def _persist_profile_feedback_result(
        self: ChatService,
        account_id: str,
        run_id: str,
        feedback: FeedbackCorrectionResult,
    ) -> None:
        """记录本轮反馈修正的处理结果（画像工单 12）。

        与准入时写入的关系快照（原回答/原运行/原采用切片）合并：读取与
        重试都按同一份配置还原「改答 + 记忆是否真的写入」，记忆操作结果
        与回答生成分别报告。
        """

        run = self._repo.get_generation_run(account_id, run_id)
        if run is None:
            return
        config = dict(run.config or {})
        metadata = config.get("feedback_correction")
        merged = dict(metadata) if isinstance(metadata, dict) else {}
        merged.update(feedback.context_metadata())
        config["feedback_correction"] = merged
        self._repo.update_generation_config(account_id, run_id, config)
    def profile_notifications_for_message(
        self: ChatService, account_id: str, conversation_id: str, message_id: str
    ) -> list[ProfileNotification]:
        """返回内部画像通知记录；不作为聊天 SSE 或公共 API 投影。"""
        if self._profiles is None:
            return []
        source_ref = f"{conversation_id}:{message_id}"
        return [
            notification
            for notification in self._profiles.list_notifications(account_id)
            if notification.source_ref == source_ref
        ]
