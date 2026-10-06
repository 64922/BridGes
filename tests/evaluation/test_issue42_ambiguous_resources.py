"""A03：资料的孤立领域歧义在实际服务入口澄清，不检索猜测的领域。"""

from pathlib import Path

from tests.chat.test_improvement12_hybrid_entry import (
    _chat_service,
    _finish_turn,
    _understand,
    _user_message,
)


def test_a03_isolated_resources_domain_waits_before_any_model_or_tool(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找一下 transformer 相关的资料。"
    )
    assert assistant.route.status == "clarify"
    assert assistant.route.module_id is None
    assert assistant.route.clarification_question.count("？") == 1
    run = service._repo.get_run_by_message("alice", assistant.message_id)
    emitted = []
    service.run_graph_turn(run, on_event=emitted.append)
    final = service._repo.get_message("alice", assistant.message_id)
    assert final.status.value == "done"
    assert final.content == assistant.route.clarification_question
    assert final.run_lock_id is None
    assert final.model_id is None
    projection = tasks.list_projections("alice", conversation.conversation_id)[0]
    assert len(projection.open_waits) == 1
    assert projection.open_waits[0].missing_fields == ["topic"]


def test_a03_prior_user_domain_avoids_unnecessary_clarification() -> None:
    current = "帮我找一下 transformer 相关的资料。"
    result = _understand(
        current,
        messages=[
            _user_message("prior", "我在学机器学习与注意力机制。"),
            _user_message("user-now", current),
        ],
    )
    assert result.actual_module_id == "resources"
    assert result.clarification_question is None


def test_a03_current_explicit_domain_and_ordinary_chat_are_preserved() -> None:
    explicit = _understand("找一下机器学习 Transformer 的资料")
    assert explicit.actual_module_id == "resources"
    assert explicit.clarification_question is None
    ordinary = _understand("今天看到 transformer 这个词挺有趣")
    assert ordinary.clarification_question is None
    assert ordinary.actual_module_id is None


def test_a03_resource_domain_answer_does_not_switch_to_paper(tmp_path: Path) -> None:
    service, tasks = _chat_service(tmp_path)
    conversation = service.create_conversation("alice")
    _, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找一下 transformer 相关的资料。"
    )
    _finish_turn(service, assistant)
    _, answered, _ = service.start_generation(
        "alice", conversation.conversation_id, "机器学习中的模型"
    )
    assert answered.route.module_id == "resources"
    assert answered.route.status != "clarify"
