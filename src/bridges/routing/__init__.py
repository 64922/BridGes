"""聊天自然语言能力路由。"""

from bridges.routing.contracts import (
    CapabilityRoute,
    MainCapability,
    PaperSearchConstraints,
    PaperSearchPlan,
    RouteStatus,
    VideoGenerationPlan,
)
from bridges.routing.registry import (
    CapabilityDefinition,
    CapabilityRouteRegistry,
    CapabilityRouteRegistryError,
)
from bridges.routing.service import NaturalLanguageRouter

__all__ = [
    "CapabilityDefinition",
    "CapabilityRoute",
    "CapabilityRouteRegistry",
    "CapabilityRouteRegistryError",
    "MainCapability",
    "NaturalLanguageRouter",
    "PaperSearchConstraints",
    "PaperSearchPlan",
    "RouteStatus",
    "VideoGenerationPlan",
]
