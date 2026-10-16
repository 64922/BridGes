# mypy: disable-error-code="misc"

"""聊天服务的画像方法。"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from bridges.contracts.atomic_profile import (
    AtomicProfileMaterialResult,
    AtomicProfileMemoryResult,
)
from bridges.contracts.chat import (
    ChatMode,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profile_extraction import (
    ProfileCorrectionResult,
    ProfileCorrectionStatus,
    ProfilePreprocessResult,
)
from bridges.contracts.profiles import ProfileNotification
from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassifier

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceProfilesMixin:
    def _process_profile_effects(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        content: str,
        mode: ChatMode,
        run_id: str,
    ) -> ProfilePreprocessResult | None:
        """首轮/续轮的画像记忆副作用：处理消息并把通知追加为 profile 事件。

        Issue 26：确定性记忆意图处理（明确记住/不记/仅会话、许可内自动
        写入、敏感候选、单次情绪提示）。画像处理失败绝不阻断聊天主流程：
        记忆能力独立于回答生成，通知留待重试轮次补发。
        Issue 02：画像通知作为 profile 事件随运行持久化（started 之后），
        订阅者从游标回放即可即时展示，重开页面不重复下发。
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
