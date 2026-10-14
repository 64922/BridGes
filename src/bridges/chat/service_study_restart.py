# mypy: disable-error-code="misc"

"""聊天服务的旧学习会话主动续学方法（工单 12）。

旧五阶段会话不可再执行；用户在旧会话主动选择本人目标与仍可访问的
材料，服务端把材料采用为新账户级草稿、原子创建新模式会话，并在新
附件行标注来源会话与来源对象。重复请求按幂等键复用同一新会话；
失败不改变旧历史，不留下孤立引用。
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from bridges.chat.attachments import ChatAttachmentError
from bridges.chat.repository import ConversationRecord
from bridges.chat.service_contract import ChatDomainError
from bridges.contracts.chat import ChatFirstTurnResponse, ChatMode
from bridges.contracts.study_restart import (
    StudyRestartOptionsProjection,
    StudyRestartRequest,
)
from bridges.study.goals import StudyGoalRepository
from bridges.study.goal_turn import STUDY_RESUME_GOAL_CONFIG_KEY
from bridges.study.history import legacy_study_conversation

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceStudyRestartMixin:
    def study_restart_options(
        self: ChatService, account_id: str, conversation_id: str
    ) -> StudyRestartOptionsProjection:
        """旧会话的续学选项；非旧流程学习会话如实拒绝，不伪造选项。

        材料只列出仍可访问的已绑定附件；目标为本人活动/暂停目标；
        两者都为空时 ``can_restart=False``，界面不提供续学动作。
        """
        record = self._require_legacy_conversation(account_id, conversation_id)
        goal_repository = StudyGoalRepository(self._repo.database)
        goals = self.list_study_goals(account_id).goals
        linked = goal_repository.get_linked_goal(account_id, conversation_id)
        materials = (
            self._attachments.list_bound_materials(account_id, conversation_id)
            if self._attachments is not None
            else []
        )
        return StudyRestartOptionsProjection(
            conversation_id=record.conversation_id,
            can_restart=bool(goals or materials),
            linked_goal_id=linked.goal_id if linked is not None else None,
            goals=goals,
            materials=[material.projection() for material in materials],
        )

    def restart_study_conversation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        request: StudyRestartRequest,
    ) -> ChatFirstTurnResponse:
        """由用户主动发起的续学：创建新会话并复用所选目标与材料。

        校验通过后才采用材料（克隆对象 + 账户级草稿）；创建失败或幂等
        重放时回收本次采用的草稿，不产生重复会话或孤立引用。原会话的
        消息、判定与材料身份不改写。
        """
        self._require_legacy_conversation(account_id, conversation_id)
        goal_id = (request.goal_id or "").strip() or None
        if goal_id is not None:
            goal = StudyGoalRepository(self._repo.database).get_goal(
                account_id, goal_id
            )
            if goal is None or goal.status == "completed":
                raise ChatDomainError(
                    "study_restart_goal_unavailable",
                    "所选学习目标当前不可用，请刷新选项后重试。",
                    422,
                )
        object_ids = list(dict.fromkeys(request.object_ids))
        if len(object_ids) != len(request.object_ids):
            raise ChatDomainError(
                "study_restart_duplicate_material", "同一份材料不能重复选择。", 422
            )
        if not goal_id and not object_ids:
            raise ChatDomainError(
                "study_restart_empty_selection",
                "请至少选择一个学习目标或一份材料。",
                422,
            )
        content = request.message.strip()
        if not content and not object_ids:
            raise ChatDomainError(
                "study_restart_message_required",
                "请补充一句想继续学习的内容。",
                422,
            )
        if object_ids and self._attachments is None:
            raise ChatDomainError(
                "attachment_unavailable", "附件服务当前不可用，请稍后重试。", 503
            )
        adopted: list[str] = []
        provenance: dict[str, tuple[str, str]] = {}
        try:
            for source_object_id in object_ids:
                try:
                    draft = self._attachments.adopt_bound_attachment(
                        account_id,
                        source_conversation_id=conversation_id,
                        source_object_id=source_object_id,
                    )
                except ChatAttachmentError as exc:
                    raise ChatDomainError(
                        exc.code, exc.message, exc.status_code
                    ) from exc
                adopted.append(draft.object_id)
                provenance[draft.object_id] = (conversation_id, source_object_id)
            response = self.start_first_turn(
                account_id,
                content=content,
                idempotency_key=request.idempotency_key,
                mode=ChatMode.STUDY,
                attachment_ids=adopted or None,
                extra_run_config=(
                    {STUDY_RESUME_GOAL_CONFIG_KEY: goal_id} if goal_id else None
                ),
                adoption_sources=provenance or None,
            )
        except BaseException:
            self._discard_adopted_drafts(account_id, adopted)
            raise
        if response.idempotent_replay:
            # 同键重放：新会话已由首次请求创建，本次采用的草稿不再使用；
            # 不重复绑定、不产生第二个会话。
            self._discard_adopted_drafts(account_id, adopted)
        return response

    def _require_legacy_conversation(
        self: ChatService, account_id: str, conversation_id: str
    ) -> ConversationRecord:
        """会话必须属于当前账户且为旧五阶段学习会话，否则如实拒绝。"""
        with self._repo.connection_lock():
            record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        if record.mode != ChatMode.STUDY.value or not legacy_study_conversation(
            self._repo.database, account_id, conversation_id
        ):
            raise ChatDomainError(
                "study_restart_not_legacy",
                "该会话不是旧版学习会话，无需续学。",
                409,
            )
        return record

    def _discard_adopted_drafts(
        self: ChatService, account_id: str, object_ids: list[str]
    ) -> None:
        """回收本次采用但未绑定的草稿（尽力而为；过期草稿有兜底清理）。"""
        if self._attachments is None or not object_ids:
            return
        for object_id in object_ids:
            with suppress(ChatAttachmentError):
                self._attachments.delete_draft(account_id, object_id)
