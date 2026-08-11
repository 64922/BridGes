"""Issue 06：自然语言论文搜索路由的公开 seam。"""

from bridges.routing import (
    CapabilityDefinition,
    CapabilityRouteRegistry,
    CapabilityRouteRegistryError,
    MainCapability,
    NaturalLanguageRouter,
    RouteStatus,
)


def test_explicit_chinese_paper_request_extracts_constraints() -> None:
    route = NaturalLanguageRouter().classify(
        "请找 2020-2024 年关于量子纠错的论文，作者为 Ada Lovelace，最多 3 篇。"
    )

    assert route.status == RouteStatus.MATCHED
    assert route.main_capability == MainCapability.PAPER_SEARCH
    assert route.paper_search is not None
    assert route.paper_search.constraints.year_from == 2020
    assert route.paper_search.constraints.year_to == 2024
    assert route.paper_search.constraints.author == "Ada Lovelace"
    assert route.paper_search.constraints.max_results == 3
    assert "量子纠错" in route.paper_search.constraints.topic_terms
    assert "author:Ada Lovelace" in route.paper_search.normalized_query
    assert "year:2020-2024" in route.paper_search.normalized_query


def test_chinese_request_keeps_only_high_information_topic() -> None:
    route = NaturalLanguageRouter().classify("给我找几篇Transformer方向相关的论文")

    assert route.status == RouteStatus.MATCHED
    assert route.paper_search is not None
    assert route.paper_search.normalized_query == "Transformer"
    assert route.paper_search.constraints.topic_terms == ["Transformer"]
    assert route.paper_search.removed_categories == ["instruction_scaffold"]

    variant = NaturalLanguageRouter().classify("我想找几篇 Transformer 论文")
    assert variant.paper_search is not None
    assert variant.paper_search.normalized_query == "Transformer"


def test_direction_phrase_keeps_the_actual_chinese_subject() -> None:
    route = NaturalLanguageRouter().classify("搜索量子纠错方向的论文")

    assert route.status == RouteStatus.MATCHED
    assert route.paper_search is not None
    assert "量子纠错方向" in route.paper_search.normalized_query
    assert "论文" not in route.paper_search.normalized_query


def test_personal_privacy_is_preserved_when_it_is_the_public_topic() -> None:
    route = NaturalLanguageRouter().classify("搜索个人隐私保护的论文")

    assert route.paper_search is not None
    assert route.paper_search.normalized_query == "个人隐私保护"


def test_private_material_is_removed_before_paper_constraints_are_extracted() -> None:
    route = NaturalLanguageRouter().classify(
        "给我找几篇 Transformer 论文。私人文档：内部代号蓝鲸；"
        "alice@example.com；token=secret-123；个人网址：https://private.example/me。"
    )

    assert route.paper_search is not None
    query = route.paper_search.normalized_query
    for private_value in (
        "内部代号蓝鲸",
        "alice@example.com",
        "secret-123",
        "https://private.example/me",
        "个人网址",
    ):
        assert private_value not in query
    assert "private_material" in route.paper_search.removed_categories
    assert "credential" in route.paper_search.removed_categories
    assert "email" in route.paper_search.removed_categories
    assert "url" in route.paper_search.removed_categories


def test_empty_chinese_paper_topic_only_clarifies() -> None:
    route = NaturalLanguageRouter().classify("给我找几篇论文")

    assert route.status == RouteStatus.CLARIFY
    assert route.error_code == "paper_empty_query"


def test_english_arxiv_request_keeps_identifier_and_scrubs_identity() -> None:
    route = NaturalLanguageRouter().classify(
        "Find papers about 2401.12345v2. 我的邮箱 alice@example.com，password=secret-123。"
    )

    assert route.status == RouteStatus.MATCHED
    assert route.paper_search is not None
    constraints = route.paper_search.constraints
    assert constraints.arxiv_id == "2401.12345v2"
    assert "alice@example.com" not in route.paper_search.normalized_query
    assert "secret-123" not in route.paper_search.normalized_query

    personal = NaturalLanguageRouter().classify(
        "Find papers about quantum error correction, my email is alice@example.com."
    )
    assert personal.paper_search is not None
    assert "alice@example.com" not in personal.paper_search.normalized_query
    assert "com" not in personal.paper_search.normalized_query


def test_paper_mention_without_search_or_rewrite_is_not_a_search() -> None:
    router = NaturalLanguageRouter()

    assert router.classify("这篇论文的实验方法有什么问题？").main_capability == MainCapability.ORDINARY_CHAT
    assert router.classify("请帮我润色这篇论文").main_capability == MainCapability.ORDINARY_CHAT
    assert router.classify("给我解释知识库中的论文").main_capability == MainCapability.ORDINARY_CHAT
    assert (
        router.classify("Search the knowledge base for what this paper says").main_capability
        == MainCapability.ORDINARY_CHAT
    )


def test_ambiguous_or_multi_task_request_only_asks_one_question() -> None:
    router = NaturalLanguageRouter()

    ambiguous = router.classify("帮我找论文")
    assert ambiguous.status == RouteStatus.CLARIFY
    assert ambiguous.clarification_question

    multi = router.classify("搜索量子纠错论文并生成一张实验室图片")
    assert multi.status == RouteStatus.CLARIFY
    assert multi.error_code == "multiple_capabilities"
    assert multi.paper_search is None


def test_invalid_result_limit_is_rejected_without_empty_result_semantics() -> None:
    route = NaturalLanguageRouter().classify("搜索量子纠错论文，最多 11 篇")

    assert route.status == RouteStatus.REJECTED
    assert route.error_code == "paper_result_limit"
    assert route.clarification_question


def test_english_constraints_and_invalid_bounds_are_normalized() -> None:
    router = NaturalLanguageRouter()
    route = router.classify(
        'Find at most 3 papers by Ada Lovelace from 2020-2024 about quantum error correction.'
    )
    assert route.status == RouteStatus.MATCHED
    assert route.paper_search is not None
    assert route.paper_search.constraints.max_results == 3
    assert route.paper_search.constraints.author == "Ada Lovelace"
    assert route.paper_search.constraints.year_from == 2020
    assert route.paper_search.constraints.year_to == 2024

    zero = router.classify("Find 0 papers about quantum error correction")
    assert zero.status == RouteStatus.REJECTED
    assert zero.error_code == "paper_result_limit"

    invalid_year = router.classify("Find papers about quantum error correction from 1899")
    assert invalid_year.status == RouteStatus.REJECTED
    assert invalid_year.error_code == "paper_year_range"


def test_registry_is_additive_and_does_not_allow_overwrite() -> None:
    registry = CapabilityRouteRegistry.builtin()
    definition = CapabilityDefinition("future_capability", "1.0.0", 10, "测试能力")
    registry.register(definition)
    registry.register(definition)

    try:
        registry.register(CapabilityDefinition("future_capability", "1.0.0", 11, "覆盖"))
    except CapabilityRouteRegistryError:
        pass
    else:
        raise AssertionError("注册表不应允许覆盖既有能力")
