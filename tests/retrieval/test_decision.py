"""Issue 12：全局知识库检索决策的确定性与持久化幂等性。"""

from __future__ import annotations

from bridges.contracts.retrieval import (
    RetrievalDecisionAction,
    RetrievalDecisionReason,
    RetrievalLayerStatus,
    RetrievalSufficiency,
)
from bridges.retrieval.decision import decide_retrieval, infer_capability_route
from tests.retrieval.conftest import add_material, seed_conversation


def test_companion_greeting_skips_global_knowledge_base(env):
    retrieval = env["retrieval"]
    conversation_id = seed_conversation(env, env["account_a"])

    decision = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-1",
        "user-1",
        "你好，今天过得怎么样？",
        mode="companion",
        capability_route="companion",
        use_knowledge_base=True,
    )

    assert decision.action == RetrievalDecisionAction.SKIP
    assert decision.reason == RetrievalDecisionReason.COMPANION_DEFAULT
    assert decision.rules_version == "retrieval-intent/v1"


def test_study_explanation_retrieves_without_model_classifier(env):
    retrieval = env["retrieval"]
    conversation_id = seed_conversation(env, env["account_a"])

    decision = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-1",
        "user-1",
        "请解释热力学第二定律为什么成立",
        mode="study",
        capability_route="study",
        use_knowledge_base=True,
    )

    assert decision.action == RetrievalDecisionAction.RETRIEVE
    assert decision.reason == RetrievalDecisionReason.STUDY_EXPLANATION


def test_specialized_capability_skips_unless_user_names_knowledge_base(env):
    retrieval = env["retrieval"]
    conversation_id = seed_conversation(env, env["account_a"])

    default = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-1",
        "user-1",
        "请把这段话改得更自然",
        mode="companion",
        capability_route="humanizer",
        use_knowledge_base=True,
    )
    explicit = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-2",
        "user-2",
        "请根据我的知识库材料改写这段话",
        mode="companion",
        capability_route="humanizer",
        use_knowledge_base=True,
    )

    assert default.action == RetrievalDecisionAction.SKIP
    assert default.reason == RetrievalDecisionReason.SPECIALIZED_CAPABILITY
    assert explicit.action == RetrievalDecisionAction.RETRIEVE
    assert explicit.reason == RetrievalDecisionReason.EXPLICIT_KNOWLEDGE_BASE


def test_same_user_turn_reuses_one_persisted_decision(env):
    retrieval = env["retrieval"]
    conversation_id = seed_conversation(env, env["account_a"])

    first = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-1",
        "user-1",
        "查询我的知识库",
        mode="companion",
        capability_route="companion",
        use_knowledge_base=True,
    )
    replay = retrieval.ensure_decision(
        env["account_a"],
        conversation_id,
        "assistant-2",
        "user-1",
        "你好",
        mode="companion",
        capability_route="companion",
        use_knowledge_base=False,
    )

    assert replay.decision_id == first.decision_id
    assert replay.action == RetrievalDecisionAction.RETRIEVE
    assert retrieval.decision_projection(env["account_a"], "assistant-1") == first


def test_global_knowledge_base_uses_bounded_filename_candidates(env):
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    for index in range(10):
        add_material(
            env,
            account,
            f"无关材料-{index}.txt",
            "完全不同的内容。",
            layer="knowledge_base",
        )
    add_material(
        env,
        account,
        "热力学讲义.txt",
        "热力学第二定律：熵在孤立系统中永不减少。",
        layer="knowledge_base",
    )

    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        "assistant-1",
        None,
        "热力学讲义",
        use_knowledge_base=True,
    )

    assert round_ is not None
    kb_layer = next(layer for layer in round_.layers if layer.layer.value == "knowledge_base")
    assert len(kb_layer.candidate_files) <= 8
    assert [item.filename for item in kb_layer.candidate_files] == ["热力学讲义.txt"]


def test_study_mode_does_not_force_retrieval_for_unrelated_requests(env):
    decision = decide_retrieval(
        "请写一首关于春天的小诗",
        mode="study",
        capability_route="study",
        use_knowledge_base=True,
    )

    assert decision.action == RetrievalDecisionAction.SKIP
    assert decision.reason == RetrievalDecisionReason.COMPANION_DEFAULT


def test_image_edit_is_a_specialized_route_without_semantic_retrieval():
    assert infer_capability_route("编辑这张图", has_image=True, image_edit=True) == "image_edit"
    decision = decide_retrieval(
        "请根据我的知识库图片编辑背景",
        mode="companion",
        capability_route="image_edit",
        use_knowledge_base=True,
    )

    assert decision.action == RetrievalDecisionAction.SKIP
    assert decision.reason == RetrievalDecisionReason.SPECIALIZED_CAPABILITY


def test_study_retrieval_without_material_persists_a_real_no_hit_round(env):
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    decision = env["retrieval"].ensure_decision(
        account,
        conversation_id,
        "assistant-1",
        "user-1",
        "请解释热力学第二定律",
        mode="study",
        capability_route="study",
        use_knowledge_base=True,
    )

    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        "assistant-1",
        "user-1",
        "请解释热力学第二定律",
        use_knowledge_base=True,
        decision=decision,
    )

    assert round_ is not None
    assert round_.sufficiency == RetrievalSufficiency.NO_HITS
    assert round_.citations == []


def test_processing_material_has_a_distinct_retrieval_state(env):
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env,
        account,
        "处理中.txt",
        "热力学第二定律材料",
        layer="knowledge_base",
        process=False,
    )
    decision = env["retrieval"].ensure_decision(
        account,
        conversation_id,
        "assistant-1",
        "user-1",
        "请解释热力学第二定律",
        mode="study",
        capability_route="study",
        use_knowledge_base=True,
    )

    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        "assistant-1",
        "user-1",
        "请解释热力学第二定律",
        use_knowledge_base=True,
        decision=decision,
    )

    assert round_ is not None
    layer = next(item for item in round_.layers if item.layer.value == "knowledge_base")
    assert layer.status == RetrievalLayerStatus.INDEX_PROCESSING
    assert round_.sufficiency == RetrievalSufficiency.INDEX_UNAVAILABLE
