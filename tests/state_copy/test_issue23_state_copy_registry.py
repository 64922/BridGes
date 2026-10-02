"""Issue 23：固定文案注册表、错误分类与清单覆盖（标准层）。

验证：

- 注册表结构完整（路径唯一、模板/渲染器齐备、类别与状态覆盖）；
- 清单登记版本、路径、真实状态、所属方与策略，可复核；
- 错误码具有真实失败类别与恢复方式，未读取、不支持、未配置、限流/超时、
  不可核实与不可达分开且措辞不承诺虚假恢复；
- 六模块与学习清单的渲染器/生成入口真实存在，机器字段保持确定性渲染。
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping

import pytest

from bridges.state_copy import (
    CHAT_ERROR_TEMPLATES,
    DEFAULT_ERROR_MESSAGE,
    ERROR_TEMPLATES,
    RETRIEVAL_SUFFICIENCY_COPY,
    STATE_COPY_ENTRIES,
    STATE_COPY_REGISTRY,
    STATE_COPY_VERSION,
    CopyCategory,
    CopyStrategy,
    FailureClass,
    RecoveryAction,
    StateCopyNotFoundError,
    StateCopyRenderError,
    error_failure_class,
    error_recovery,
    error_template,
    render_state_copy,
    state_copy_manifest,
    validate_state_copy_registry,
)

_GAP_MARKERS = ("未", "没有", "缺少", "无法", "不足", "不能", "不会")
_CONFIG_MARKERS = (
    "配置",
    "凭据",
    "Key",
    "密钥",
    "权限",
    "DNS",
    "代理",
    "证书",
    "网络环境",
    "设置",
    "管理员",
)


def _resolve(path: str) -> object:
    module_name, _, attribute = path.rpartition(".")
    return getattr(importlib.import_module(module_name), attribute)


def test_registry_validates_and_lists_every_required_category() -> None:
    validate_state_copy_registry()

    covered = {entry.category for entry in STATE_COPY_ENTRIES}
    assert covered == set(CopyCategory)
    assert len(STATE_COPY_REGISTRY) == len(STATE_COPY_ENTRIES)


def test_manifest_registers_version_path_state_owner_and_strategy() -> None:
    manifest = state_copy_manifest()

    assert len(manifest) == len(STATE_COPY_ENTRIES)
    for item in manifest:
        assert item["version"] == STATE_COPY_VERSION
        assert isinstance(item["path"], str) and item["path"]
        assert item["owner"]
        assert item["states"]
        strategy = item["strategy"]
        assert strategy in {candidate.value for candidate in CopyStrategy}
        if strategy == CopyStrategy.FIXED_TEMPLATE.value:
            assert isinstance(item["text"], str) and item["text"].strip()
        else:
            assert isinstance(item["renderer"], str) and item["renderer"]


def test_render_substitutes_values_and_rejects_bad_calls() -> None:
    assert (
        render_state_copy("chat.clarification.multiple_tasks", names="论文、通勤")
        == "这条消息包含多个任务（论文、通勤）；本轮先执行哪一个？"
    )
    assert (
        render_state_copy("error.web_search_timeout")
        == CHAT_ERROR_TEMPLATES["web_search_timeout"]
    )
    with pytest.raises(StateCopyNotFoundError):
        render_state_copy("not.registered")
    with pytest.raises(StateCopyRenderError):
        render_state_copy("chat.clarification.multiple_tasks")
    with pytest.raises(StateCopyRenderError):
        render_state_copy("module.paper.result")


def test_module_and_study_entries_resolve_to_real_renderers() -> None:
    renderable = [
        entry
        for entry in STATE_COPY_ENTRIES
        if entry.strategy is not CopyStrategy.FIXED_TEMPLATE
    ]
    assert renderable
    for entry in renderable:
        symbol = _resolve(entry.renderer or "")
        assert callable(symbol) or isinstance(symbol, Mapping), entry.path

    module_owners = {
        entry.owner
        for entry in STATE_COPY_ENTRIES
        if entry.category is CopyCategory.MODULE
    }
    assert {
        "paper",
        "resources",
        "github",
        "tieba",
        "career_plan",
        "commute",
    } <= module_owners
    study_owners = {
        entry.owner
        for entry in STATE_COPY_ENTRIES
        if entry.category is CopyCategory.STUDY
    }
    assert study_owners == {"study"}


def test_module_outcomes_cover_success_partial_failure_empty_and_stopped() -> None:
    states: set[str] = set()
    for entry in STATE_COPY_ENTRIES:
        if entry.category in {CopyCategory.MODULE, CopyCategory.STUDY}:
            states.update(entry.states)

    assert {"success", "partial", "failure", "empty", "stopped"} <= states


def test_module_machine_fields_stay_with_deterministic_renderers() -> None:
    """路线、引用、价格、时间、岗位、题目状态等字段不交给模型全文重写。"""
    for entry in STATE_COPY_ENTRIES:
        if entry.category is CopyCategory.MODULE:
            assert entry.strategy is CopyStrategy.DETERMINISTIC_RENDERER, entry.path


def test_every_error_template_exposes_class_and_recovery() -> None:
    for template in ERROR_TEMPLATES:
        assert error_failure_class(template.code) is template.failure_class
        assert error_recovery(template.code) is template.recovery
        assert template.text.strip()
        assert f"error.{template.code}" in STATE_COPY_REGISTRY


def test_contextual_codes_are_registered_but_not_table_overrides() -> None:
    """节点错误登记真实类别，但不能覆盖父图拼装的节点位置与恢复方式。"""
    from bridges.chat.turn import user_facing_error

    for template in ERROR_TEMPLATES:
        if template.contextual:
            assert template.code not in CHAT_ERROR_TEMPLATES
            assert error_failure_class(template.code) is template.failure_class

    wrapped = "在「选择模块」步骤失败：该模块尚未开放，请使用普通对话。请调整后重试。"
    assert user_facing_error("module_not_available", wrapped) == wrapped
    assert user_facing_error("route_rejected", "请指明要查的论文主题。") == "请指明要查的论文主题。"


def test_required_failure_classes_are_separately_registered() -> None:
    classes = {template.failure_class for template in ERROR_TEMPLATES}

    assert {
        FailureClass.NOT_READ,
        FailureClass.UNSUPPORTED,
        FailureClass.NOT_CONFIGURED,
        FailureClass.RATE_LIMIT_TIMEOUT,
        FailureClass.UNVERIFIABLE,
        FailureClass.UNAVAILABLE,
    } <= classes


def test_error_copy_states_the_real_gap_or_config() -> None:
    for template in ERROR_TEMPLATES:
        text = template.text
        if template.failure_class is FailureClass.NOT_READ:
            assert any(marker in text for marker in _GAP_MARKERS), template.code
        if template.failure_class is FailureClass.UNSUPPORTED:
            assert "稍后重试" not in text, template.code
            assert "可点击重试" not in text, template.code
        if template.failure_class is FailureClass.NOT_CONFIGURED:
            assert "稍后重试" not in text, template.code
            assert any(marker in text for marker in _CONFIG_MARKERS), template.code
        if template.failure_class is FailureClass.RATE_LIMIT_TIMEOUT:
            assert "重试" in text or "稍后" in text, template.code
        if template.failure_class is FailureClass.UNVERIFIABLE:
            assert any(marker in text for marker in _GAP_MARKERS), template.code


def test_click_retry_copy_only_for_real_retryable_codes() -> None:
    from bridges.chat.turn import error_is_retryable

    for template in ERROR_TEMPLATES:
        if "可点击重试" in template.text:
            assert error_is_retryable(template.code), template.code
        if template.recovery in {
            RecoveryAction.RECONFIGURE,
            RecoveryAction.ADJUST_REQUEST,
            RecoveryAction.REFRESH_STATE,
            RecoveryAction.NONE,
        }:
            assert "可点击重试" not in template.text, template.code


def test_client_error_taxonomy_keeps_rejection_honest() -> None:
    rejected = error_template("client_error_400")
    assert rejected is not None
    assert rejected.failure_class is FailureClass.UNSUPPORTED
    assert rejected.recovery is RecoveryAction.NONE
    assert "重试不会恢复" in rejected.text

    throttled = error_template("client_error_429")
    assert throttled is not None
    assert throttled.failure_class is FailureClass.RATE_LIMIT_TIMEOUT
    assert throttled.recovery is RecoveryAction.RETRY

    upstream = error_template("client_error_503")
    assert upstream is not None
    assert upstream.failure_class is FailureClass.INTERNAL

    assert error_template("unknown_code") is None
    assert error_failure_class(None) is None
    assert error_recovery(None) is None


def test_default_error_message_is_the_internal_error_template() -> None:
    assert CHAT_ERROR_TEMPLATES["internal_error"] == DEFAULT_ERROR_MESSAGE


def test_retrieval_sufficiency_copy_is_registry_sourced() -> None:
    assert RETRIEVAL_SUFFICIENCY_COPY == {
        "sufficient": "已检索到足够的本地材料。",
        "no_hits": "没有找到与问题相关的本地材料。",
        "conflict": "检索到的候选来源存在冲突，结果可能不确定。",
        "insufficient_coverage": "检索到的本地材料覆盖不足。",
        "index_unavailable": "本地索引不可用，暂无法检索本地材料，请稍后重试。",
    }
