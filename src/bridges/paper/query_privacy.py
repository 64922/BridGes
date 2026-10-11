"""公开论文查询的本地隐私门；不截断研究概念或限制学术词汇。"""

from bridges.web_search.service import LocalQueryPlanner


def is_public_search_term(value: str) -> bool:
    """复用已有身份/凭据模式，拒绝将敏感片段作为公开检索概念。"""
    return not any(
        pattern.search(value)
        for pattern in (
            LocalQueryPlanner._SECRET_ASSIGNMENT,
            LocalQueryPlanner._BEARER,
            LocalQueryPlanner._EMAIL,
            LocalQueryPlanner._PHONE,
            LocalQueryPlanner._ID_CARD,
            LocalQueryPlanner._NAME_MARKER,
            LocalQueryPlanner._PRECISE_LOCATION,
            LocalQueryPlanner._CODE,
        )
    )
