"""贴吧终态的恢复说明与认证等待问题。"""

from __future__ import annotations

from bridges.kernel.contracts import KernelResult
from bridges.tieba.contracts import (
    TiebaRecovery,
    TiebaRecoveryAction,
    TiebaResearchProjection,
)
from bridges.tieba.recipe import (
    NODE_READ,
    NODE_SEARCH,
    NODE_SYNTHESIZE,
    NODE_VERIFY_OFFICIAL,
)
from bridges.tieba.service_contract import is_auth_wait_error


def auth_wait_recovery() -> TiebaRecovery:
    return TiebaRecovery(
        action=TiebaRecoveryAction.CONTINUE_AUTH,
        automatic=True,
        reuses_saved_evidence=True,
        scope_note=(
            "完成登录或安全验证后会自动续接；若窗口已关闭，"
            "在聊天中回复“继续”即可。已读页面会复用，不重复读取。"
        ),
    )


def stopped_recovery(*, reuses_saved_evidence: bool) -> TiebaRecovery:
    return TiebaRecovery(
        action=TiebaRecoveryAction.NONE,
        automatic=False,
        reuses_saved_evidence=reuses_saved_evidence,
        scope_note=(
            "已停止，不会自动继续；再次发送会创建新运行。"
            "已保存的检索与页面证据会按当前条件复用。"
        ),
    )


def failure_recovery(result: KernelResult) -> TiebaRecovery:
    saved = any(
        artifact.reusable
        and artifact.node
        in {
            NODE_SEARCH,
            NODE_READ,
            NODE_VERIFY_OFFICIAL,
            NODE_SYNTHESIZE,
        }
        for artifact in result.artifacts
    )
    return TiebaRecovery(
        action=TiebaRecoveryAction.RETRY_RUN,
        automatic=False,
        reuses_saved_evidence=saved,
        failed_stage=(result.failure.node if result.failure else NODE_SEARCH),
        scope_note=(
            "重试会创建新运行，并复用已保存的检索与页面证据；未完成阶段会继续。"
            if saved
            else "重试会创建新运行，并重新执行本轮取证。"
        ),
    )


def auth_wait_question(projection: TiebaResearchProjection) -> str:
    """保留真实等待原因（登录或安全验证），并给出可执行的恢复方式。"""

    messages = [
        record.error_message
        for record in projection.queries
        if is_auth_wait_error(record.error_code) and record.error_message
    ]
    messages.extend(
        post.read_error_message
        for post in projection.confirmed_posts
        if is_auth_wait_error(post.read_error_code) and post.read_error_message
    )
    messages.extend(
        link.unconfirmed_reason
        for link in projection.candidate_links
        if is_auth_wait_error(link.read_error_code) and link.unconfirmed_reason
    )
    question = next((message for message in messages if message), "")
    if not question:
        question = "请在当前账户专用的贴吧会话中完成登录或安全验证。"
    if "继续" not in question:
        question += " 任务已保留；完成后在聊天中说“继续”，我会接着原话题检索。"
    return question
