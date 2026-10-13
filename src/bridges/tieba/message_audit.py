"""贴吧已提交消息与其核验产物的一致性复查。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bridges.contracts.chat import ChatMessageStatus
from bridges.kernel.contracts import ArtifactTrust, KernelResult, KernelStatus
from bridges.kernel.repository import NodeKernelRepository
from bridges.tieba.contracts import TiebaResearchProjection, TiebaResearchStatus
from bridges.tieba.presenting import (
    render_empty_content,
    render_result_content,
    render_waiting_auth_content,
)
from bridges.tieba.quality_gates import verified_projection
from bridges.tieba.recipe import NODE_VERIFY, TIEBA_RECIPE_ID
from bridges.tieba.service_contract import WAIT_KIND_AUTH, is_auth_wait_error

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository


def verify_tieba_message(
    repo: ConversationRepository,
    *,
    account_id: str,
    run_id: str,
    conversation_id: str,
    assistant_message_id: str,
) -> bool:
    """父图根据本轮完成收据复验可信产物与最终正文。"""
    repository = NodeKernelRepository(repo.database)
    receipts = repository.list_receipts(account_id, run_id)
    artifacts = tuple(
        artifact
        for receipt in receipts
        if (artifact := repository.get_artifact(account_id, receipt.artifact_id or "")) is not None
        and artifact.recipe_id == TIEBA_RECIPE_ID
    )
    result = KernelResult(
        status=KernelStatus.COMPLETED, nodes=(), artifacts=artifacts, delivery=None
    )
    projection = verified_projection(result)
    message = repo.get_message(account_id, assistant_message_id)
    verify = result.artifact(NODE_VERIFY)
    if (
        projection is None
        or message is None
        or verify is None
        or verify.conversation_id != conversation_id
        or verify.trust_state is not ArtifactTrust.QUALIFIED
        or message.status is not ChatMessageStatus.DONE
    ):
        return False
    expected = (
        render_result_content(projection)
        if projection.confirmed_posts
        else render_empty_content(projection)
    )
    try:
        actual = TiebaResearchProjection.model_validate(message.tieba_research)
    except ValueError:
        return False
    if actual.status is TiebaResearchStatus.WAITING_AUTH:
        pending = actual.pending
        return bool(
            pending is not None
            and pending.kind == WAIT_KIND_AUTH
            and pending.context.get("original_question") == actual.original_question
            and (
                any(is_auth_wait_error(record.error_code) for record in actual.queries)
                or any(is_auth_wait_error(post.read_error_code) for post in actual.confirmed_posts)
                or any(is_auth_wait_error(link.read_error_code) for link in actual.candidate_links)
            )
            and actual.confirmed_posts == projection.confirmed_posts
            and message.content == render_waiting_auth_content(actual)
        )
    # 完成时间由终态事务填写，证据字段与正文仍逐项相等。
    return (
        actual.completed_at is not None
        and actual.model_copy(update={"completed_at": projection.completed_at}) == projection
        and message.content == expected
    )
