"""学习消息终态事务内冻结公开结果，避免可变领域状态改写历史。"""

from collections.abc import Callable
from typing import Any

from bridges.chat.repository import ConversationRepository, GenerationRunRecord
from bridges.chat.turn_result import derive_turn_result, trust_label
from bridges.contracts.chat import ChatMessageStatus, ResultTrust, TurnResultBlock
from bridges.contracts.study import StudyState
from bridges.state_copy import render_state_copy


def finalize_study_message(
    repo: ConversationRepository,
    run: GenerationRunRecord,
    *,
    state_provider: Callable[[], StudyState | None],
    finalizer: Callable[..., int],
    **kwargs: Any,
) -> int:
    """先提交领域回调，再按本条消息已提交事实冻结；失败整笔回滚。

    借用既有 finalize_message 的 persist_learning 事务接缝，不增加读取
    副作用或第二套终态协议。状态提供者按账户读取，只有 public_view
    进入派生，未来题、私有评分依据及未提交的内存状态均不作为输入。
    """
    persist_learning = kwargs.pop("persist_learning", None)

    def persist_result() -> None:
        if persist_learning is not None:
            persist_learning()
        message = repo.get_message(run.account_id, run.assistant_message_id)
        current_run = repo.get_generation_run(run.account_id, run.run_id)
        if message is None:
            raise RuntimeError("学习终态事务中的消息已不存在。")
        state = state_provider()
        result = derive_turn_result(
            status=message.status,
            error_code=message.error_code,
            route=message.route,
            wait_reason=current_run.wait_reason if current_run is not None else None,
            study=state.public_view() if state is not None else None,
            study_node=current_run.current_node if current_run is not None else None,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
        )
        # 只有本次总结节点成功提交了真实总结才列为已交付；不根据
        # 当前阶段或历史字段猜测，独立块 ID 避免与反馈块在 UI 中重名。
        if (message.status is ChatMessageStatus.DONE and current_run is not None
                and current_run.current_node == "study.summarize"
                and state is not None and state.summary is not None):
            result = result.model_copy(update={"trust": ResultTrust.QUALIFIED,
                "trust_label": trust_label(ResultTrust.QUALIFIED), "delivered": [
                *result.delivered,
                TurnResultBlock(module_id="study_summary",
                    label=render_state_copy("chat.result.study.summary"),
                    state="success", trust=ResultTrust.QUALIFIED),
            ]})
        repo.database.scoped(run.account_id).execute(
            "UPDATE messages SET turn_result = ? WHERE message_id = ? AND account_id = ?",
            (result.model_dump_json(), run.assistant_message_id, run.account_id),
        )

    return finalizer(
        repo, run.account_id, run.assistant_message_id,
        persist_learning=persist_result, **kwargs,
    )
