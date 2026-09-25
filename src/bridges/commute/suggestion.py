"""普通聊天里的校园通勤一键建议（V2 Issue 12）。

合同（``docs/v2/product-contract.md`` 第 2 节）：普通聊天可以建议用户一键启动
模块，但**不得后台执行**——建议只写入助手消息，点击后由服务端以原文显式派发。

建议条件刻意保守：既要有出行意图（怎么走／多久到／骑车去…），也要有校内线索
（校门、图书馆、南区…），且原话里要能看出一次出行的起终点线索。指代含糊
（``那边``／``随便``）时不给建议，避免把普通聊天误报成可以立即执行的任务。
"""

from __future__ import annotations

from bridges.commute.contracts import MODE_LABELS, CommuteMode
from bridges.commute.lexicon import (
    CAMPUS_HINTS,
    COMMUTE_INTENT_HINTS,
    MODE_ALIAS_ORDER,
    VAGUE_SIGNALS,
)
from bridges.commute.parsing import extract_places

#: 建议按钮文案（与论文模块同一形式：以原文启动，不重复输入）。
COMMUTE_SUGGESTION_LABEL = "使用校园通勤"

#: 建议理由（中文一句话，说明为什么建议以及只支持范围）。
COMMUTE_SUGGESTION_REASON = (
    "这看起来是一次校内出行，校园通勤可以按高德真实路线给出距离、耗时与课间缓冲。"
)

#: 方式词表直接复用解析用词表（长别名优先），避免建议与解析两处各写一份。
_MODE_HINTS: tuple[tuple[str, CommuteMode], ...] = MODE_ALIAS_ORDER


def detect_commute_suggestion(content: str) -> dict[str, object] | None:
    """普通聊天里的明显通勤请求：返回建议载荷；不确定时返回 None。"""
    text = content.strip()
    if not text:
        return None
    if any(signal in text for signal in VAGUE_SIGNALS):
        return None
    if not any(hint in text for hint in COMMUTE_INTENT_HINTS):
        return None
    if not any(hint in text for hint in CAMPUS_HINTS):
        return None
    origin, destination = extract_places(text)
    if origin is None and destination is None:
        return None
    mode = _mode_hint(text)
    reason = COMMUTE_SUGGESTION_REASON
    if mode is not None:
        reason = f"{reason}（按你说的{MODE_LABELS[mode]}规划。）"
    return {
        "module_id": "commute",
        "label": COMMUTE_SUGGESTION_LABEL,
        "reason": reason,
        "text": content,
        "needs_disambiguation": False,
    }


def _mode_hint(text: str) -> CommuteMode | None:
    for hint, mode in _MODE_HINTS:
        if hint in text:
            return mode
    return None
