"""Issue 17：能力清单完整性测试（假 registry、假路由与真实应用对照）。"""

from __future__ import annotations

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.production import register_builtin_capabilities
from bridges.closeout.manifest import (
    CAPABILITY_MANIFEST_VERSION,
    PRODUCTION_CAPABILITY_MANIFEST,
    RETIRED_ROUTE_ACTIVE,
    UNCLASSIFIED_CAPABILITY,
    CapabilityCategory,
    CapabilityManifestEntry,
    DiscoveredRoute,
    RoutePattern,
    check_chat_action_coverage,
    check_manifest_completeness,
    check_retired_registry,
    check_route_coverage,
    discover_api_routes,
    normalize_route_path,
    validate_manifest,
)


def _p(path: str, *methods: str) -> RoutePattern:
    return RoutePattern(path, frozenset(methods) if methods else None)


def test_manifest_version_is_positive() -> None:
    assert CAPABILITY_MANIFEST_VERSION >= 1


def test_manifest_self_validation_passes() -> None:
    assert validate_manifest() == []


def test_every_chat_action_is_claimed() -> None:
    assert check_chat_action_coverage() == []


def test_qwen_model_entries_bind_approved_capabilities() -> None:
    for entry in PRODUCTION_CAPABILITY_MANIFEST:
        if entry.category != CapabilityCategory.QWEN_MODEL:
            continue
        assert entry.model_capability in MODEL_BY_CAPABILITY, entry.id


def test_manifest_entries_have_unique_ids() -> None:
    ids = [entry.id for entry in PRODUCTION_CAPABILITY_MANIFEST]
    assert len(ids) == len(set(ids))


def test_duplicate_classification_fails() -> None:
    duplicate = CapabilityManifestEntry(
        id="chat_daily",
        journey="重复分类",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
    )
    manifest = (PRODUCTION_CAPABILITY_MANIFEST[0], duplicate)
    assert any(
        "重复能力分类" in message for message in validate_manifest(manifest)
    )


def test_qwen_model_without_capability_fails() -> None:
    bad = CapabilityManifestEntry(
        id="bad-qwen",
        journey="缺能力",
        category=CapabilityCategory.QWEN_MODEL,
    )
    assert any(
        "缺少 model_capability" in message for message in validate_manifest((bad,))
    )


def test_qwen_model_with_unapproved_capability_fails() -> None:
    bad = CapabilityManifestEntry(
        id="bad-qwen",
        journey="未批准能力",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="not_in_matrix",
    )
    assert any(
        "绑定未批准能力" in message for message in validate_manifest((bad,))
    )


def test_non_qwen_with_model_capability_fails() -> None:
    bad = CapabilityManifestEntry(
        id="bad-local",
        journey="本地能力绑模型",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        model_capability="qwen_text_chat",
    )
    assert any(
        "不得绑定 model_capability" in message for message in validate_manifest((bad,))
    )


def test_retired_without_routes_or_names_fails() -> None:
    bad = CapabilityManifestEntry(
        id="bad-retired",
        journey="空退役",
        category=CapabilityCategory.RETIRED,
    )
    assert any(
        "必须声明 410 路由模式或退役能力名" in message
        for message in validate_manifest((bad,))
    )


def test_unknown_chat_action_fails() -> None:
    bad = CapabilityManifestEntry(
        id="bad-action",
        journey="未知动作",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        chat_actions=("not-a-real-action",),
    )
    assert any(
        "认领未知聊天动作" in message for message in validate_manifest((bad,))
    )


def test_normalize_route_path() -> None:
    assert normalize_route_path("/chat/conversations/{conversation_id}/messages") == (
        "/chat/conversations/*/messages"
    )
    assert normalize_route_path("/mcp/{legacy_path:path}") == "/mcp/*"
    assert normalize_route_path("/plain") == "/plain"


def _fake_route(
    path: str, method: str = "GET", status_code: int = 200
) -> DiscoveredRoute:
    return DiscoveredRoute(
        path=normalize_route_path(path), method=method, status_code=status_code
    )


def test_unclassified_route_fails() -> None:
    violations = check_route_coverage(
        [_fake_route("/brand-new/public/route", "POST")],
        manifest=(PRODUCTION_CAPABILITY_MANIFEST[0],),
    )
    assert any(v.code == UNCLASSIFIED_CAPABILITY for v in violations)


def test_retired_route_without_retired_entry_fails() -> None:
    violations = check_route_coverage(
        [_fake_route("/expression/drafts", "POST", 410)],
        manifest=(PRODUCTION_CAPABILITY_MANIFEST[0],),
    )
    assert any(v.code == UNCLASSIFIED_CAPABILITY for v in violations)


def test_retired_entry_matching_live_route_fails() -> None:
    retired = CapabilityManifestEntry(
        id="retired-x",
        journey="退役入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(_p("/legacy/*"),),
    )
    violations = check_route_coverage(
        [_fake_route("/legacy/{item}", "GET", 200)],
        manifest=(retired,),
    )
    assert any(v.code == RETIRED_ROUTE_ACTIVE for v in violations)


def test_retired_entry_matching_410_route_passes() -> None:
    retired = CapabilityManifestEntry(
        id="retired-x",
        journey="退役入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(_p("/legacy/*"),),
    )
    violations = check_route_coverage(
        [_fake_route("/legacy/{item}", "GET", 410)],
        manifest=(retired,),
    )
    assert violations == []


def test_retired_registry_revival_fails() -> None:
    from bridges.closeout.manifest import RETIRED_EXPRESSION_CAPABILITY
    from bridges.contracts.ai import CapabilityKind, CapabilityRecord, CapabilityStatus

    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    assert check_retired_registry(registry) == []

    registry.register(
        CapabilityRecord(
            name=RETIRED_EXPRESSION_CAPABILITY,
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="deterministic",
            input_schema_version="v1",
            output_schema_version="v1",
            status=CapabilityStatus.VERIFIED,
        )
    )
    violations = check_retired_registry(registry)
    assert any(v.code == RETIRED_ROUTE_ACTIVE for v in violations)


def test_real_app_routes_are_fully_classified() -> None:
    """真实应用注册的全部公开路由必须被清单覆盖（Issue 17 AC1）。"""
    from bridges.api.main import create_app

    app = create_app(None)
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    routes = discover_api_routes(app)
    assert len(routes) > 300
    violations = check_manifest_completeness(routes, registry)
    assert violations == []
