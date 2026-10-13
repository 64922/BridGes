"""Issue 17：版本化生产能力清单（capability manifest）。

清单把每一项用户可达的公开能力唯一归类为 ``qwen_model``、
``external_non_qwen``、``local_deterministic`` 或 ``retired``，是真实性
发布门（``bridges.closeout.authenticity_gate``）的入口事实源：

- ``PRODUCTION_CAPABILITY_MANIFEST`` 覆盖公开 API 路由、聊天动作与 UI
  对应的生产服务；新增公开能力必须先加入清单再决定类别，不能静默忽略；
- 每项能力恰好属于一个类别；``qwen_model`` 项必须绑定批准矩阵中的
  ``model_capability``；``retired`` 项必须对应真实 410 路由或退役能力名；
- 路由完整性核对以 FastAPI 应用实际注册的路由为准（含惰性
  ``_IncludedRouter`` 展开）：声明 410 的路由必须被 ``retired`` 项覆盖，
  非 410 路由不得被 ``retired`` 项覆盖，任何路由都必须被至少一个清单项
  分类。``/chat`` 家族（模型关键路由）逐条精确分类；纯本地前缀
  （``/auth``、``/knowledge-base`` 等）按领域伞形分类。

本模块不读取、打印或持久化任何 Key、请求/响应正文。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.closeout.manifest_contracts import (
    CAPABILITY_MANIFEST_VERSION,
    CHAT_ACTIONS,
    DIRECT_CLIENT_BYPASS,
    MISSING_ADAPTER,
    MISSING_RUN_LOCK,
    MODEL_MATRIX_DRIFT,
    PRODUCTION_STUB,
    RETIRED_EXPRESSION_CAPABILITY,
    RETIRED_MEDIA_CAPABILITIES,
    RETIRED_ROUTE_ACTIVE,
    UNCLASSIFIED_CAPABILITY,
    CapabilityCategory,
    CapabilityManifestEntry,
    RoutePattern,
)
from bridges.closeout.manifest_data import PRODUCTION_CAPABILITY_MANIFEST

_MANIFEST_BY_ID = {entry.id: entry for entry in PRODUCTION_CAPABILITY_MANIFEST}


def normalize_route_path(path: str) -> str:
    """把 FastAPI 路由路径的 ``{param}``/``:param`` 段归一化为 ``*``。"""
    segments: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}") or segment.startswith(":"):
            segments.append("*")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def validate_manifest(
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[str]:
    """校验清单自身的一致性；返回违规说明列表（空列表表示通过）。

    规则：
    - 清单版本为正整数；
    - 每项能力 ID 唯一（重复分类失败）；
    - ``qwen_model`` 项必须绑定批准矩阵中的 model capability；
    - 非 ``qwen_model`` 项不得绑定 model capability；
    - ``retired`` 项必须声明退役能力名或至少一条路由模式（缺失分类失败）；
    - ``local_deterministic``/``external_non_qwen`` 项不得声明退役能力名；
    - 认领的聊天动作必须存在于 ``CHAT_ACTIONS``，且每个动作被恰当地认领。
    """
    violations: list[str] = []
    if CAPABILITY_MANIFEST_VERSION < 1:
        violations.append("清单版本必须为正整数。")
    seen_ids: set[str] = set()
    claimed_actions: set[str] = set()
    for entry in manifest:
        if entry.id in seen_ids:
            violations.append(f"重复能力分类：{entry.id}。")
        seen_ids.add(entry.id)
        if entry.category == CapabilityCategory.QWEN_MODEL:
            if entry.model_capability is None:
                violations.append(f"qwen_model 能力 {entry.id} 缺少 model_capability。")
            elif entry.model_capability not in MODEL_BY_CAPABILITY:
                violations.append(
                    f"qwen_model 能力 {entry.id} 绑定未批准能力 {entry.model_capability}。"
                )
        elif entry.model_capability is not None:
            violations.append(
                f"非 qwen_model 能力 {entry.id} 不得绑定 model_capability。"
            )
        if entry.category == CapabilityCategory.RETIRED:
            if not entry.route_patterns and not entry.retired_capability_names:
                violations.append(
                    f"retired 能力 {entry.id} 必须声明 410 路由模式或退役能力名。"
                )
        elif entry.retired_capability_names:
            violations.append(f"非 retired 能力 {entry.id} 不得声明退役能力名。")
        for action in entry.chat_actions:
            if action not in CHAT_ACTIONS:
                violations.append(f"能力 {entry.id} 认领未知聊天动作 {action}。")
            claimed_actions.add(action)
    for action in CHAT_ACTIONS:
        if action not in claimed_actions:
            violations.append(f"聊天动作 {action} 未被任何清单项认领。")
    return violations


@dataclass(frozen=True)
class DiscoveredRoute:
    """应用实际注册的一条公开路由（脱敏：只含路径/方法/状态码）。"""

    path: str
    method: str
    status_code: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "method": self.method,
            "status_code": self.status_code,
        }


def discover_api_routes(app: Any) -> list[DiscoveredRoute]:
    """枚举 FastAPI 应用注册的公开路由（展开惰性 ``_IncludedRouter``）。"""
    discovered: list[DiscoveredRoute] = []

    def flatten(routes: Iterable[Any]) -> Iterable[Any]:
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from flatten(route.original_router.routes)
            else:
                yield route

    for route in flatten(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = getattr(route, "methods", None) or []
        status_code = getattr(route, "status_code", None) or 200
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            discovered.append(
                DiscoveredRoute(
                    path=normalize_route_path(str(path)),
                    method=method,
                    status_code=int(status_code),
                )
            )
    return discovered


@dataclass(frozen=True)
class ManifestViolation:
    """一条清单完整性违规：稳定错误码 + 对象 + 脱敏说明。"""

    code: str
    target: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "target": self.target, "detail": self.detail}


def check_route_coverage(
    routes: Iterable[DiscoveredRoute],
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对公开路由覆盖：未分类、retired 误覆盖与非 410 退役均失败。

    - 声明 410 的路由必须被 retired 清单项覆盖（``unclassified_capability``）；
    - 非 410 路由不得被 retired 清单项覆盖（``retired_route_active``）；
    - 任何路由都必须被至少一个清单项分类（``unclassified_capability``）。
    """
    violations: list[ManifestViolation] = []
    for route in routes:
        retired_declared: list[CapabilityManifestEntry] = []
        retired_runtime: list[CapabilityManifestEntry] = []
        active_matches: list[CapabilityManifestEntry] = []
        for entry in manifest:
            for pattern in entry.route_patterns:
                if not pattern.matches(route.path, route.method):
                    continue
                if entry.category == CapabilityCategory.RETIRED:
                    if pattern.runtime_retired:
                        retired_runtime.append(entry)
                    else:
                        retired_declared.append(entry)
                else:
                    active_matches.append(entry)
                break
        if route.status_code == 410:
            if not retired_declared:
                violations.append(
                    ManifestViolation(
                        UNCLASSIFIED_CAPABILITY,
                        f"{route.method} {route.path}",
                        "410 退役路由未被任何 retired 清单项覆盖。",
                    )
                )
        else:
            if retired_declared:
                violations.append(
                    ManifestViolation(
                        RETIRED_ROUTE_ACTIVE,
                        f"{route.method} {route.path}",
                        "retired 清单项覆盖了非 410 的活跃路由（类别误分）。",
                    )
                )
            if not active_matches and not retired_declared and not retired_runtime:
                violations.append(
                    ManifestViolation(
                        UNCLASSIFIED_CAPABILITY,
                        f"{route.method} {route.path}",
                        "公开路由未被任何清单项分类。",
                    )
                )
    return violations


def check_chat_action_coverage(
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对聊天动作覆盖：清单未覆盖的动作必须失败并输出稳定标识。"""
    claimed = {action for entry in manifest for action in entry.chat_actions}
    return [
        ManifestViolation(
            UNCLASSIFIED_CAPABILITY,
            action,
            "聊天动作未被任何清单项分类。",
        )
        for action in CHAT_ACTIONS
        if action not in claimed
    ]


def check_retired_registry(
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对退役能力名：注册表再次出现即 ``retired_route_active``。

    按注册表活跃清单逐名核对（不依赖固定 version），任何版本复活都失败。
    """
    retired_names = {
        name
        for entry in manifest
        if entry.category == CapabilityCategory.RETIRED
        for name in entry.retired_capability_names
    }
    if not retired_names:
        return []
    registered = {
        capability.name for capability in registry.list_active()
    }
    return [
        ManifestViolation(
            RETIRED_ROUTE_ACTIVE,
            name,
            "退役能力名被重新注册到生产能力注册表。",
        )
        for name in sorted(retired_names)
        if name in registered
    ]


def check_registry_coverage(
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """反向核对：注册表中每个活跃 MODEL 能力必须被清单 qwen_model 项认领。

    清单声明了能力分类，注册表必须与之吻合；注册表里出现清单未认领的
    活跃 MODEL 能力（如新能力未分类）即 ``unclassified_capability`` 并
    点名 capability，禁止静默忽略。
    """
    from bridges.contracts.ai import CapabilityKind

    claimed = {
        entry.model_capability
        for entry in manifest
        if entry.category == CapabilityCategory.QWEN_MODEL
        and entry.model_capability is not None
    }
    violations: list[ManifestViolation] = []
    for capability in registry.list_active():
        if capability.kind != CapabilityKind.MODEL or capability.model_id is None:
            continue
        if capability.name not in claimed:
            violations.append(
                ManifestViolation(
                    UNCLASSIFIED_CAPABILITY,
                    capability.name,
                    "注册表活跃 MODEL 能力未被能力清单认领，必须先加入清单再决定类别。",
                )
            )
    return violations


def check_manifest_completeness(
    routes: Iterable[DiscoveredRoute],
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """完整性与类别一致性总检查：清单自检 + 路由覆盖 + 聊天动作 + 退役注册。"""
    violations: list[ManifestViolation] = [
        ManifestViolation(UNCLASSIFIED_CAPABILITY, "manifest", message)
        for message in validate_manifest(manifest)
    ]
    violations.extend(check_route_coverage(routes, manifest=manifest))
    violations.extend(check_chat_action_coverage(manifest=manifest))
    violations.extend(check_retired_registry(registry, manifest=manifest))
    violations.extend(check_registry_coverage(registry, manifest=manifest))
    return violations


__all__ = [
    "CAPABILITY_MANIFEST_VERSION",
    "CHAT_ACTIONS",
    "CapabilityCategory",
    "CapabilityManifestEntry",
    "DIRECT_CLIENT_BYPASS",
    "DiscoveredRoute",
    "ManifestViolation",
    "MISSING_ADAPTER",
    "MISSING_RUN_LOCK",
    "MODEL_MATRIX_DRIFT",
    "PRODUCTION_CAPABILITY_MANIFEST",
    "PRODUCTION_STUB",
    "RETIRED_EXPRESSION_CAPABILITY",
    "RETIRED_MEDIA_CAPABILITIES",
    "RETIRED_ROUTE_ACTIVE",
    "RoutePattern",
    "UNCLASSIFIED_CAPABILITY",
    "check_chat_action_coverage",
    "check_manifest_completeness",
    "check_registry_coverage",
    "check_retired_registry",
    "check_route_coverage",
    "discover_api_routes",
    "normalize_route_path",
    "validate_manifest",
]
