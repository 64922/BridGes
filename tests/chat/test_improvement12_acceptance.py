"""验收入口、任务绑定与真实生成调用，不以路由名字代替执行证据。"""

from unittest.mock import MagicMock

import pytest

from bridges.chat.run_executor import chat_run_context
from bridges.chat.service import ChatDomainError
from tests.chat.test_improvement12_hybrid_entry import _chat_service, _finish_turn


def test_conflicting_hint_preserved_and_actual_route_used(tmp_path):
    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id,
        "帮我找几篇关于量子纠错的论文", module_id="github",
    )
    assert user.module_id == "github"
    assert assistant.route.requested_module_id == "github"
    assert assistant.route.module_id == "paper"


def test_suggestion_click_uses_independent_source_and_original_task(tmp_path):
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找几篇论文，再找几本入门教材",
    )
    _finish_turn(service, first)
    task = tasks.list_projections("alice", conversation.conversation_id)[0]
    _, retry, _ = service.retry_generation(
        "alice", conversation.conversation_id, first.message_id, module_id="paper",
    )
    updated = tasks.projection("alice", task.task.task_id)
    assert updated.open_waits == []
    run = service._repo.get_run_by_message("alice", retry.message_id)
    assert run.config["task_binding"]["task_id"] == task.task.task_id
    original = service._repo.get_message("alice", first.message_id)
    assert original.route["route_source"] != "suggestion_click"
    _finish_turn(service, retry)
    _, again, _ = service.retry_generation(
        "alice", conversation.conversation_id, retry.message_id,
    )
    assert again.route == retry.route


def test_stale_suggestion_cannot_resume_another_task(tmp_path):
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找几篇论文，再找几本入门教材",
    )
    _finish_turn(service, first)
    _, other, _ = service.start_generation(
        "alice", conversation.conversation_id, "换个话题，推荐几本 Python 的入门教材",
    )
    _finish_turn(service, other)
    context = tasks.current_reference_context("alice", conversation.conversation_id)
    with pytest.raises(ChatDomainError, match="任务已变化"):
        service.retry_generation(
            "alice", conversation.conversation_id, first.message_id, module_id="paper",
        )
    assert tasks.current_reference_context("alice", conversation.conversation_id) == context


def test_continuation_inherits_network_condition_without_claiming_new_source(tmp_path):
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "不要联网，找关于量子纠错的论文",
    )
    _finish_turn(service, first)
    before = tasks.list_projections("alice", conversation.conversation_id)[0]
    _, continued, _ = service.start_generation(
        "alice", conversation.conversation_id, "继续找关于量子纠错的论文",
    )
    run = service._repo.get_run_by_message("alice", continued.message_id)
    assert run.config["understanding"]["hard_conditions"] == []
    assert run.config["understanding"]["effective_hard_conditions"][0]["kind"] == "no_network"
    after = tasks.projection("alice", before.task.task_id)
    assert after.task.current_version == before.task.current_version


def test_topic_answer_resolves_wait_and_compiles_paper_query(tmp_path):
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "给我找几篇论文",
    )
    _finish_turn(service, first)
    before = tasks.list_projections("alice", conversation.conversation_id)[0]
    assert before.open_waits[0].missing_fields == ["topic"]
    _, answer, _ = service.start_generation(
        "alice", conversation.conversation_id, "量子纠错",
    )
    assert answer.route.is_paper_search
    assert "量子纠错" in answer.route.paper_search.normalized_query
    after = tasks.projection("alice", before.task.task_id)
    assert after.open_waits == []


def test_direct_paper_stream_does_not_call_network_when_forbidden(tmp_path):
    service, _ = _chat_service(tmp_path)
    service._turn._arxiv_search = MagicMock()
    service._turn._arxiv_search.plan_from_route.return_value.should_search = True
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "不要联网，找关于量子纠错的论文",
    )
    list(service.stream_generation(
        "alice", conversation.conversation_id, assistant.message_id,
        chat_run_context("alice", conversation.conversation_id,
                         service._repo.get_run_by_message("alice", assistant.message_id).run_id),
        until_user_message_id=user.message_id,
    ))
    service._turn._arxiv_search.plan_from_route.assert_not_called()
    service._turn._arxiv_search.search.assert_not_called()


def test_source_restriction_blocks_generic_web_and_local_retrieval(tmp_path):
    service, _ = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "只查论文，解释一下这个概念",
    )
    assert assistant.route.knowledge_base_allowed is False
    assert assistant.route.web_search_allowed is False
