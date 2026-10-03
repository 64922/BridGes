"""Issue 23：公共状态文案接入正式路径（接线层）。

用真实入口验证成功/部分/失败/空/停止逐项来自注册表，错误文案与真实状态
一致，且没有为覆盖固定文案新增模型调用（注册表是纯数据与确定性渲染）。
"""

from __future__ import annotations

from types import SimpleNamespace

from bridges.ai.errors import (
    MODEL_CALL_ERROR_MESSAGES_ZH,
    user_facing_model_error,
)
from bridges.chat import terminal, turn
from bridges.chat.graph import node_failure_message, node_label
from bridges.chat.understanding import MainAgentUnderstanding
from bridges.contracts.chat import ChatMode
from bridges.contracts.references import ReferenceResolution, ReferenceStatus
from bridges.contracts.retrieval import RetrievalSufficiency
from bridges.retrieval.service import _sufficiency_note
from bridges.routing import NaturalLanguageRouter
from bridges.state_copy import (
    CHAT_ERROR_TEMPLATES,
    CLARIFICATION_TASK_AMBIGUITY_TEXT,
    CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT,
    MODEL_CALL_ERROR_TEMPLATES,
    RETRIEVAL_ATTACHMENTS_PENDING_TEXT,
    RETRIEVAL_KNOWLEDGE_BASE_NOT_READY_TEXT,
    RETRIEVAL_SUFFICIENCY_COPY,
    ROUTE_CLARIFY_MULTIPLE_PAPER_OR_OTHER_TEXT,
    ROUTE_CLARIFY_PAPER_TOPIC_TEXT,
    ROUTE_REJECTED_FALLBACK_TEXT,
    STREAM_INTERRUPTED_TEXT,
    THINKING_BUDGET_WARNING_TEXT,
    THINKING_DONE_QUALITY_TEXT,
    THINKING_FAILED_FALLBACK_TEXT,
    THINKING_STOPPED_QUALITY_TEXT,
    USER_STOPPED_TEXT,
    WEB_SEARCH_CANCELLED_TEXT,
    WEB_SEARCH_INTERNAL_TEXT,
    WEB_SEARCH_PROVIDER_UNREADY_TEXT,
    WEB_SEARCH_STAGE_TIMEOUT_TEXT,
    render_state_copy,
)
from bridges.web_search.contracts import WebSearchStatus
from bridges.web_search.service import SearchPlan, WebSearchService


def test_chat_and_model_error_tables_are_registry_single_source() -> None:
    assert turn._ERROR_MESSAGES is CHAT_ERROR_TEMPLATES
    assert MODEL_CALL_ERROR_MESSAGES_ZH is MODEL_CALL_ERROR_TEMPLATES
    for code, text in CHAT_ERROR_TEMPLATES.items():
        assert turn.user_facing_error(code) == text
    for code, text in MODEL_CALL_ERROR_TEMPLATES.items():
        assert user_facing_model_error(code, "internal") == text


def test_terminal_and_thinking_copy_come_from_registry() -> None:
    assert terminal.STOPPED_MESSAGE == USER_STOPPED_TEXT
    assert turn.STREAM_INTERRUPTED_MESSAGE == STREAM_INTERRUPTED_TEXT

    thinking = turn.initial_thinking(ChatMode.COMPANION)
    assert turn.done_thinking(thinking).quality == [THINKING_DONE_QUALITY_TEXT]
    assert turn.stopped_thinking(thinking).quality == [THINKING_STOPPED_QUALITY_TEXT]
    assert turn.budget_warning_thinking(thinking).quality == [
        THINKING_BUDGET_WARNING_TEXT
    ]
    assert turn.failed_thinking(thinking, None).quality == [
        THINKING_FAILED_FALLBACK_TEXT
    ]


def test_graph_failure_copy_uses_registered_templates() -> None:
    assert node_label("compile_context") == "编译上下文"
    assert node_label("unknown_node") == "unknown_node"

    assert node_failure_message(
        node="compile_context", message="运行额度无法验证。", retryable=True
    ) == "在「编译上下文」步骤失败：运行额度无法验证。可点击重试。"
    assert node_failure_message(
        node="select_explicit_module",
        message="学习模式不能启动日常模块。",
        retryable=False,
        code="module_mode_conflict",
    ) == "在「选择模块」步骤失败：学习模式不能启动日常模块。"

    assert node_failure_message(
        node="select_explicit_module",
        message="该模块尚未开放，请使用普通对话。",
        retryable=False,
        code="module_not_available",
    ) == "在「选择模块」步骤失败：该模块尚未开放，请使用普通对话。"
    assert node_failure_message(
        node="invoke_subgraph_or_chat",
        message="任务状态或版本已变化，请基于最新任务重试。",
        retryable=False,
        code="task_state_conflict",
    ) == "在「生成回答」步骤失败：任务状态或版本已变化，请基于最新任务重试。"
    assert node_failure_message(
        node="invoke_subgraph_or_chat",
        message="学习模式不能启动日常模块。",
        retryable=False,
        code="module_mode_conflict",
    ) == "在「生成回答」步骤失败：学习模式不能启动日常模块。"
    assert node_failure_message(
        node="select_explicit_module",
        message="该回答已不在生成中。",
        retryable=False,
        code="generation_not_active",
    ) == "在「选择模块」步骤失败：该回答已不在生成中。"


def test_chat_web_search_projection_copy_comes_from_registry() -> None:
    plan = SimpleNamespace(reason="用户请求联网", query="公开主题")

    cancelled = turn._web_search_projection_from_result(  # noqa: SLF001
        plan, turn._SEARCH_CANCELLED
    )
    assert cancelled.error_message == WEB_SEARCH_CANCELLED_TEXT

    stage_timeout = turn._web_search_projection_from_result(  # noqa: SLF001
        plan, turn._SEARCH_TIMEOUT
    )
    assert stage_timeout.error_message == WEB_SEARCH_STAGE_TIMEOUT_TEXT

    internal = turn._web_search_projection_from_result(  # noqa: SLF001
        plan, object()
    )
    assert internal.error_message == WEB_SEARCH_INTERNAL_TEXT


def test_understanding_clarifications_use_registry() -> None:
    service = MainAgentUnderstanding()
    unresolved = ReferenceResolution(status=ReferenceStatus.UNRESOLVED)

    clarification, missing = service._clarification(
        ambiguity=None,
        detected=["paper", "commute"],
        requested_module_id=None,
        resolution=unresolved,
    )
    assert clarification == render_state_copy(
        "chat.clarification.multiple_tasks", names="paper、commute"
    )
    assert missing == ["capability"]

    assert (
        service._clarification(
            ambiguity=CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT,
            detected=[],
            requested_module_id=None,
            resolution=unresolved,
        )[0]
        == CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT
    )
    assert CLARIFICATION_TASK_AMBIGUITY_TEXT == "找到多个同样合理的任务，需要确认是哪一个。"


def test_routing_clarifications_use_registry() -> None:
    router = NaturalLanguageRouter()

    empty = router.classify("")
    assert empty.clarification_question == ROUTE_CLARIFY_PAPER_TOPIC_TEXT

    mixed = router.classify("帮我找论文，还要看网页新闻")
    assert mixed.clarification_question == ROUTE_CLARIFY_MULTIPLE_PAPER_OR_OTHER_TEXT


def test_retrieval_notes_are_registry_sourced() -> None:
    for value, text in RETRIEVAL_SUFFICIENCY_COPY.items():
        assert _sufficiency_note(RetrievalSufficiency(value)) == text
    assert RETRIEVAL_ATTACHMENTS_PENDING_TEXT == "附件仍在处理中或暂无可检索内容。"
    assert RETRIEVAL_KNOWLEDGE_BASE_NOT_READY_TEXT == "知识库暂无已就绪材料。"
    assert ROUTE_REJECTED_FALLBACK_TEXT == turn.ROUTE_REJECTED_FALLBACK_TEXT


class _DegradedMonitor:
    """健康快照未就绪：初始投影必须如实显示降级说明。"""

    def peek(self) -> SimpleNamespace:
        return SimpleNamespace(pending=False, status=WebSearchStatus.ERROR)


class _FakeClient:
    provider_name = "fake"
    provider_version = "fake-v1"
    request_profile_version = "fake-v1"


def test_web_search_degraded_note_is_registry_sourced() -> None:
    service = WebSearchService(client=_FakeClient())
    service._health_monitor = _DegradedMonitor()  # noqa: SLF001 - 模拟未就绪快照

    projection = service.initial_projection(
        SearchPlan(True, "公开主题", "需要事实核查")
    )

    assert projection is not None
    assert projection.error_message == WEB_SEARCH_PROVIDER_UNREADY_TEXT
