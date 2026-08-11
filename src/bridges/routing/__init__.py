"""聊天自然语言能力路由。"""

from bridges.routing.contracts import (
    PAPER_QUERY_VERSION,
    CapabilityRoute,
    MainCapability,
    PaperSearchConstraints,
    PaperSearchPlan,
    RemovedQueryCategory,
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
    "PAPER_QUERY_VERSION",
    "PaperSearchConstraints",
    "PaperSearchPlan",
    "RemovedQueryCategory",
    "RouteStatus",
    "VideoGenerationPlan",
]
