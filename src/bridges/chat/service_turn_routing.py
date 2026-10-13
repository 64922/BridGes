# mypy: disable-error-code="misc"

"""聊天服务的回合路由方法。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bridges.chat.repository import (
    MessageRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.contracts.chat import (
    CHAT_MODULE_VALUES,
    ChatMode,
    VideoRequestPayload,
)
from bridges.contracts.tasks import TaskRecord, TaskWait
from bridges.contracts.understanding import (
    UNDERSTANDING_CONTRACT_VERSION,
    HardCondition,
    HardConditionKind,
    MainUnderstanding,
    RouteSource,
)
from bridges.paper.suggestion import PAPER_REQUEST_HINTS
from bridges.routing import (
    MODULE_CAPABILITIES,
    CapabilityRoute,
    MainCapability,
    RouteStatus,
)

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceTurnRoutingMixin:
    def _understand_turn(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        content: str,
        mode: ChatMode,
        module_id: str | None,
        messages: list[MessageRecord] | None = None,
    ) -> MainUnderstanding:
        """一次确定性主理解：轻量/关系/硬条件/缺项/实际路由来源。

        只读当前任务快照与会话原文；不调用模型、不执行检索、不写状态。
        未装配任务领域时按无任务处理（普通聊天仍保持轻量）。
        """

        if module_id is not None and module_id not in CHAT_MODULE_VALUES:
            raise ChatDomainError("module_not_allowed", "该模块已取消，无法启动。", 400)
        task_context = None
        tasks: list[TaskRecord] = []
        open_waits: list[TaskWait] = []
        if self._tasks is not None:
            task_context = self._tasks.current_reference_context(
                account_id, conversation_id
            )
            projections = self._tasks.list_projections(account_id, conversation_id)
            tasks = [item.task for item in projections]
            open_waits = [wait for item in projections for wait in item.open_waits]
        if messages is None:
            messages = self._repo.list_messages(account_id, conversation_id)
        understanding = self._understanding.understand(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            content=content,
            mode=mode,
            requested_module_id=module_id,
            task_context=task_context,
            tasks=tasks,
            open_waits=open_waits,
            messages=messages,
        )
        # 本轮条件保留自己的来源；续接时继承有效条件仅供执行门禁使用。
        projection = next(
            (item for item in projections
             if item.task.task_id == understanding.target_task_id),
            None,
        ) if self._tasks is not None else None
        if projection is not None:
            new_kinds = {item.kind.value for item in understanding.hard_conditions}
            understanding.effective_hard_conditions = [
                HardCondition(kind=HardConditionKind(item.kind), text=item.text,
                              source_span=item.source_span)
                for item in projection.effective_conditions
                if item.kind in HardConditionKind._value2member_map_
                and item.kind not in new_kinds
            ]
        return understanding
    @staticmethod
    def _route_metadata(
        understanding: MainUnderstanding | None,
        requested_module_id: str | None,
    ) -> dict[str, Any]:
        """路由快照的请求提示/实际来源/能力列表元数据。"""

        if understanding is None:
            return {"requested_module_id": requested_module_id}
        return {
            "requested_module_id": (
                understanding.requested_module_id or requested_module_id
            ),
            "route_source": understanding.route_source.value,
            "capability_list": list(understanding.capability_list),
            "understanding_version": UNDERSTANDING_CONTRACT_VERSION,
        }
    @staticmethod
    def _route_flags(understanding: MainUnderstanding | None) -> tuple[bool, bool]:
        """联网/知识库开关（硬条件单一事实源在理解合同）。"""

        if understanding is None:
            return True, True
        return not understanding.blocks_network, not understanding.blocks_knowledge_base
    def _route_for_turn(
        self: ChatService,
        *,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        mcp_call_payload: dict[str, Any] | None,
        content: str = "",
        requested_module_id: str | None = None,
        understanding: MainUnderstanding | None = None,
    ) -> CapabilityRoute:
        """显式载荷优先；否则按正文理解的实际路由构建快照。

        V2 issue 04：SKILL 载荷在扩展载荷门一律拒绝，不再有人味化路由。
        改进工单 12：正文明确的单模块意图直达（论文/生涯经统一路由器
        编译可执行计划，其余模块走登记能力）；硬条件收紧联网/本地材料
        开关；歧义只产出一次澄清路由，不先检索猜测领域。
        """

        metadata = self._route_metadata(understanding, requested_module_id)
        web_allowed, kb_allowed = self._route_flags(understanding)
        if image_payload is not None:
            return CapabilityRoute(
                status=RouteStatus.MATCHED,
                main_capability=MainCapability.IMAGE,
                confidence=1.0,
                reason="已提交图片能力载荷",
                knowledge_base_allowed=False,
                web_search_allowed=False,
                **metadata,
            )
        if video_payload is not None:
            payload = VideoRequestPayload.model_validate(video_payload)
            return self._router.route_explicit_video(
                payload.prompt,
                aspect_ratio=payload.aspect_ratio,
                duration_seconds=payload.duration_seconds,
            ).model_copy(update=metadata)
        if mcp_call_payload is not None:
            return CapabilityRoute(
                status=RouteStatus.ORDINARY,
                main_capability=MainCapability.ORDINARY_CHAT,
                confidence=1.0,
                reason="已提交显式 MCP 调用载荷",
                knowledge_base_allowed=False,
                web_search_allowed=False,
                **metadata,
            )
        if understanding is not None and understanding.clarification_question:
            return CapabilityRoute(
                status=RouteStatus.CLARIFY,
                main_capability=MainCapability.CLARIFICATION,
                confidence=1.0,
                reason=understanding.reason,
                clarification_question=understanding.clarification_question,
                knowledge_base_allowed=False,
                web_search_allowed=False,
                **metadata,
            )
        if understanding is not None and len(understanding.capability_list) > 1:
            # 工单 37：已登记跨模块组合由复合计划统一调度与核验；路由只
            # 记录能力列表，实际计划与参数来源由编排器按登记表验证。
            return CapabilityRoute(
                status=RouteStatus.MATCHED,
                main_capability=MainCapability.ORDINARY_CHAT,
                confidence=1.0,
                reason="已登记跨模块组合，按依赖计划统一调度与核验。",
                knowledge_base_allowed=kb_allowed,
                web_search_allowed=web_allowed,
                **metadata,
            )
        module = understanding.actual_module_id if understanding is not None else None
        if module == "paper":
            paper_request = (
                f"找关于{understanding.goal}的论文"
                if understanding is not None and "topic" in understanding.answer_fields
                else content
            )
            classified = self._router.classify(paper_request)
            if (
                not classified.is_paper_search
                and understanding is not None
                and understanding.route_source == RouteSource.MODULE_HINT
            ):
                # 用户显式选择了论文模块（菜单/建议点击/逐消息提示）：正文即使
                # 只是纯主题词（如「Transformer 的注意力机制入门」），也用同一
                # 路由器把主题包装成可执行论文计划后派发；主题逐字保留。
                wrapped = self._router.classify(f"找关于{content}的论文")
                if wrapped.is_paper_search:
                    classified = wrapped
            if classified.is_paper_search:
                return classified.model_copy(update={
                    **metadata, "module_id": "paper",
                    "web_search_allowed": classified.web_search_allowed and web_allowed,
                    "knowledge_base_allowed": classified.knowledge_base_allowed and kb_allowed,
                })
            if classified.status in {
                RouteStatus.CLARIFY,
                RouteStatus.REJECTED,
            }:
                return classified.model_copy(update={
                    **metadata, "module_id": None,
                    "web_search_allowed": classified.web_search_allowed and web_allowed,
                    "knowledge_base_allowed": classified.knowledge_base_allowed and kb_allowed,
                })
        if module is not None and module != "paper" and module in MODULE_CAPABILITIES:
            return CapabilityRoute(
                status=RouteStatus.MATCHED,
                main_capability=MODULE_CAPABILITIES[module],
                confidence=1.0,
                reason=(
                    understanding.reason
                    if understanding is not None
                    else f"按显式模块 {module} 启动。"
                ),
                knowledge_base_allowed=kb_allowed,
                web_search_allowed=web_allowed,
                module_id=module,
                **metadata,
            )
        learning_turn = (
            understanding is not None
            and understanding.route_source == RouteSource.LEARNING_STRATEGY
        )
        if (
            module is None
            and content
            and not learning_turn
            and any(hint.lower() in content.lower() for hint in PAPER_REQUEST_HINTS)
        ):
            # 明确论文请求但主题含糊（如「给我找几篇论文」）：只问必要问题，
            # 不派发模块、不先检索猜测领域。
            classified = self._router.classify(content)
            if classified.status in {
                RouteStatus.CLARIFY,
                RouteStatus.REJECTED,
            } and classified.main_capability in {
                MainCapability.PAPER_SEARCH,
                MainCapability.CLARIFICATION,
            }:
                return classified.model_copy(update=metadata)
        return CapabilityRoute(
            status=RouteStatus.ORDINARY,
            main_capability=MainCapability.ORDINARY_CHAT,
            confidence=1.0,
            reason=(
                understanding.reason
                if understanding is not None
                else "未识别到明确能力意图，按日常对话处理。"
            ),
            knowledge_base_allowed=kb_allowed,
            web_search_allowed=web_allowed and not (
                understanding is not None and any(
                    item.kind == HardConditionKind.SOURCE_RESTRICTION
                    for item in [*understanding.hard_conditions,
                                 *understanding.effective_hard_conditions]
                )
            ),
            **metadata,
        )
