"""提醒画像措辞适配测试（Issue 33，确定性纯函数）。

验证：称呼规则（称呼我X/叫我X）、语气披露（本次使用类别）、关闭适配
时正文不含任何个性化措辞、非白名单维度不参与、类别披露顺序与数量。
"""

from __future__ import annotations

from bridges.contracts.profiles import ProfileSliceItem
from bridges.reminder.adaptation import adapt_reminder_body, dimension_label

SUBJECT = "复习 transformer"


def _item(dimension: str, value: str) -> ProfileSliceItem:
    return ProfileSliceItem(
        assertion_id=f"a-{dimension}-{value[:4]}",
        dimension=dimension,
        value_or_rule=value,
        inclusion_reason="r",
        sensitivity_class="preference",
    )


def test_plain_body_without_profile() -> None:
    """关闭画像适配：正文只含主题与固定落款。"""
    result = adapt_reminder_body(SUBJECT, [])
    assert result.categories == []
    assert result.item_count == 0
    assert result.body == f"{SUBJECT}\n\n—— 来自 BridGes 提醒"
    assert "画像" not in result.body


def test_greeting_from_call_me_rule() -> None:
    """「称呼我为小谷」→ 正文首行问候。"""
    items = [
        _item("basic_information", "称呼我为小谷"),
        _item("expression_habit", "说话简洁"),
    ]
    result = adapt_reminder_body(SUBJECT, items)
    assert result.body.startswith("你好，小谷！")
    assert "说话简洁" not in result.body  # 语气值不直接进入正文


def test_greeting_from_other_form() -> None:
    """「叫我阿明」形式。"""
    result = adapt_reminder_body(SUBJECT, [_item("basic_information", "叫我阿明")])
    assert result.body.startswith("你好，阿明！")


def test_categories_disclosure_and_order() -> None:
    """披露类别：按白名单出现顺序去重，包含表达习惯与基本偏好。"""
    items = [
        _item("expression_habit", "说话简洁"),
        _item("basic_information", "称呼我为小谷"),
        _item("basic_information", "喜欢早晨学习"),
        _item("interest_preference", "偏好短句"),
    ]
    result = adapt_reminder_body(SUBJECT, items)
    assert result.categories == ["表达习惯", "基本偏好", "偏好"]
    assert result.item_count == 4
    assert "「表达习惯」「基本偏好」「偏好」" in result.body


def test_non_whitelist_dimension_excluded() -> None:
    """非白名单维度（阶段目标/知识状态）不参与适配。"""
    items = [
        _item("stage_goal", "目标：考取研究生"),
        _item("knowledge_state", "已掌握微积分"),
    ]
    result = adapt_reminder_body(SUBJECT, items)
    assert result.item_count == 0
    assert result.categories == []
    assert result.body == f"{SUBJECT}\n\n—— 来自 BridGes 提醒"


def test_dimension_label_known_and_fallback() -> None:
    assert dimension_label("expression_habit") == "表达习惯"
    assert dimension_label("unknown_dim") == "unknown_dim"
