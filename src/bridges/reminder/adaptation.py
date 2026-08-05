"""提醒措辞的画像适配（Issue 33，确定性纯函数）。

提醒正文只调用用户授权的最小画像切片（reminder 模式白名单：基本偏好、
表达习惯等），用户可在确认前关闭适配（use_profile=false）并查看本次
使用的类别。适配是确定性规则，不调用模型：

- 称呼规则：切片值中出现「称呼我X / 叫我X / 我的称呼是X」→ 正文首行
  「你好，X！」；
- 语气披露：启用适配时正文附加一行说明，列出本次使用的画像类别
  （用户可在邮件与页面两处查看本次使用类别）；
- 关闭适配：正文只含主题与固定落款，不包含任何个性化措辞。

本模块不读取画像记录正文之外的任何信息；输入切片条目由调用方按
最小切片编译结果传入。
"""

from __future__ import annotations

import re

from bridges.contracts.profiles import ProfileSliceItem

#: 称呼规则：从任意切片值提取称呼。
_GREETING_RE = re.compile(r"(?:称呼|叫|我的称呼是)[我]*(?:为)?([一-龥A-Za-z0-9]{1,16})")

#: 维度 → 中文标签（披露用；与 profiles 服务的标签保持一致口径）。
_DIMENSION_LABELS = {
    "basic_information": "基本偏好",
    "interest_preference": "偏好",
    "expression_habit": "表达习惯",
    "stage_goal": "阶段目标",
    "knowledge_state": "知识状态",
}

#: 允许参与措辞适配的维度白名单（reminder 模式最小切片口径）。
_ALLOWED_DIMENSIONS = frozenset(
    {"basic_information", "interest_preference", "expression_habit"}
)


def dimension_label(dimension: str) -> str:
    """返回维度中文标签；未知维度回退为英文标识（不猜测）。"""
    return _DIMENSION_LABELS.get(dimension, dimension)


class ReminderAdaptationResult:
    """一次适配的结果：正文 + 本次使用类别披露。"""

    def __init__(
        self, body: str, *, categories: list[str], item_count: int
    ) -> None:
        self.body = body
        self.categories = categories
        self.item_count = item_count


def adapt_reminder_body(
    subject: str,
    items: list[ProfileSliceItem],
) -> ReminderAdaptationResult:
    """按授权切片条目适配提醒正文；返回正文与使用类别披露。

    ``items`` 必须是调用方已按最小切片编译并校验授权范围的条目；本
    函数只读取条目中的维度与值摘要，不访问画像仓库。
    """
    allowed = [item for item in items if item.dimension in _ALLOWED_DIMENSIONS]
    if not allowed:
        return ReminderAdaptationResult(
            body=_plain_body(subject),
            categories=[],
            item_count=0,
        )

    greeting = ""
    for item in allowed:
        match = _GREETING_RE.search(item.value_or_rule)
        if match:
            greeting = f"你好，{match.group(1)}！\n\n"
            break
    categories = []
    for item in allowed:
        label = dimension_label(item.dimension)
        if label not in categories:
            categories.append(label)
    category_note = "（本条提醒已按你的画像类别「" + "」「".join(categories) + "」适配措辞）"
    body = f"{greeting}{subject}\n\n{category_note}\n\n—— 来自 BridGes 提醒"
    return ReminderAdaptationResult(
        body=body,
        categories=categories,
        item_count=len(allowed),
    )


def _plain_body(subject: str) -> str:
    """关闭画像适配时的固定正文：不含任何个性化措辞。"""
    return f"{subject}\n\n—— 来自 BridGes 提醒"


__all__ = [
    "ReminderAdaptationResult",
    "adapt_reminder_body",
    "dimension_label",
]
