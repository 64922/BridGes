"""固定文案注册表：构建、渲染、清单与完整性校验（Issue 23）。

注册表是「公共状态文案」的唯一查询入口：调用方按登记路径渲染中文模板，
清单（:func:`state_copy_manifest`）给出每条来源的版本、路径、真实状态、
所属方与生成策略，供后续模块票据复核。校验函数只做结构性检查（重复
路径、缺渲染器、类别与状态覆盖缺口），不代替各领域的真实状态验收。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from bridges.state_copy.catalog import (
    RETRIEVAL_SUFFICIENCY_PATHS,
    STATE_COPY_ENTRIES,
)
from bridges.state_copy.types import (
    STATE_COPY_VERSION,
    CopyCategory,
    CopyEntry,
    CopyStrategy,
    FailureClass,
    StateCopyNotFoundError,
    StateCopyRegistryError,
    StateCopyRenderError,
)

#: 清单必须覆盖的路径类别（工单 23 的正式路径清单）。
REQUIRED_CATEGORIES = frozenset(CopyCategory)
#: 六模块/学习清单必须覆盖的合理结果状态（不适用范围由条目策略说明）。
REQUIRED_MODULE_STATES = frozenset(
    {"success", "partial", "failure", "empty", "stopped"}
)
#: 错误清单必须分开登记的失败类别（停止不属于错误，单独登记）。
REQUIRED_FAILURE_CLASSES = frozenset(
    failure_class
    for failure_class in FailureClass
    if failure_class is not FailureClass.STOPPED
)

_STATE_TOKEN_SEPARATORS = (" ",)


def _build_registry() -> Mapping[str, CopyEntry]:
    registry: dict[str, CopyEntry] = {}
    duplicates: list[str] = []
    for entry in STATE_COPY_ENTRIES:
        if entry.path in registry:
            duplicates.append(entry.path)
        registry[entry.path] = entry
    if duplicates:
        raise StateCopyRegistryError(
            "固定文案注册表存在重复路径：" + "、".join(sorted(set(duplicates)))
        )
    return MappingProxyType(registry)


#: 路径 → 登记项（只读）。领域票据按路径复核自己负责的文案。
STATE_COPY_REGISTRY: Mapping[str, CopyEntry] = _build_registry()


def state_copy_entry(path: str) -> CopyEntry:
    """按路径取登记项；未登记时抛出 :class:`StateCopyNotFoundError`。"""
    try:
        return STATE_COPY_REGISTRY[path]
    except KeyError as exc:
        raise StateCopyNotFoundError(path) from exc


def render_state_copy(path: str, /, **values: object) -> str:
    """按路径渲染固定中文模板；只接受本注册表登记的固定模板路径。"""
    entry = state_copy_entry(path)
    if entry.strategy is not CopyStrategy.FIXED_TEMPLATE or not entry.text:
        raise StateCopyRenderError(
            f"路径 {path} 不是固定模板（策略 {entry.strategy.value}），不能直接渲染"
        )
    try:
        return entry.text.format(**values)
    except (KeyError, IndexError) as exc:
        raise StateCopyRenderError(f"路径 {path} 缺少占位值：{exc}") from exc


def state_copy_manifest() -> tuple[dict[str, object], ...]:
    """全部登记项的审计清单（版本、路径、真实状态、策略、所属方）。"""
    return tuple(
        {
            "version": STATE_COPY_VERSION,
            "path": entry.path,
            "category": entry.category.value,
            "owner": entry.owner,
            "strategy": entry.strategy.value,
            "states": list(entry.states),
            "text": entry.text,
            "renderer": entry.renderer,
            "note": entry.note,
        }
        for entry in STATE_COPY_ENTRIES
    )


def retrieval_sufficiency_copy() -> dict[str, str]:
    """检索充足性枚举值 → 已登记的确定中文说明。"""
    return {
        value: state_copy_entry(path).text or ""
        for value, path in RETRIEVAL_SUFFICIENCY_PATHS.items()
    }


#: 检索充足性文案（由注册表派生，检索模块按状态直接选择）。
RETRIEVAL_SUFFICIENCY_COPY = retrieval_sufficiency_copy()


def validate_state_copy_registry() -> None:
    """结构性校验：重复路径、缺渲染器、类别与状态覆盖缺口一次报全。

    只检查清单自身的完整性，不检查领域渲染器的真实业务语义；渲染器可
    解析性由接线测试验证。校验失败抛 :class:`StateCopyRegistryError`。
    """
    problems: list[str] = []
    seen_paths: set[str] = set()
    category_states: dict[CopyCategory, set[str]] = {}
    module_states: dict[str, set[str]] = {}
    failure_classes: set[str] = set()

    for entry in STATE_COPY_ENTRIES:
        if entry.path in seen_paths:
            problems.append(f"重复路径：{entry.path}")
        seen_paths.add(entry.path)
        if not entry.path or any(part for part in entry.path.split(".") if not part):
            problems.append(f"路径不合法：{entry.path!r}")
        if not entry.owner.strip():
            problems.append(f"缺少所属方：{entry.path}")
        if not entry.states:
            problems.append(f"缺少真实状态：{entry.path}")
        if len(set(entry.states)) != len(entry.states):
            problems.append(f"真实状态重复：{entry.path}")
        for state in entry.states:
            if state != state.strip().lower() or any(
                separator in state for separator in _STATE_TOKEN_SEPARATORS
            ):
                problems.append(f"状态标识不规范：{entry.path} → {state!r}")
        if entry.strategy is CopyStrategy.FIXED_TEMPLATE:
            if not entry.text or not entry.text.strip():
                problems.append(f"固定模板缺少文案：{entry.path}")
            if entry.renderer:
                problems.append(f"固定模板不应登记渲染器：{entry.path}")
        elif not entry.renderer:
            problems.append(f"{entry.strategy.value} 缺少渲染器：{entry.path}")
        category_states.setdefault(entry.category, set()).update(entry.states)
        if entry.category in {CopyCategory.ERROR, CopyCategory.STOP}:
            failure_classes.update(
                failure_class.value
                for failure_class in FailureClass
                if failure_class.value in entry.states
            )
        if entry.category in {CopyCategory.MODULE, CopyCategory.STUDY}:
            module_states.setdefault(entry.owner, set()).update(entry.states)

    missing_categories = REQUIRED_CATEGORIES - set(category_states)
    if missing_categories:
        problems.append(
            "缺少路径类别：" + "、".join(sorted(c.value for c in missing_categories))
        )
    missing_classes = REQUIRED_FAILURE_CLASSES - failure_classes
    if missing_classes:
        problems.append(
            "缺少失败类别：" + "、".join(sorted(c.value for c in missing_classes))
        )
    covered_module_states = set().union(*module_states.values()) if module_states else set()
    missing_states = REQUIRED_MODULE_STATES - covered_module_states
    if missing_states:
        problems.append("模块/学习清单缺少结果状态：" + "、".join(sorted(missing_states)))
    for owner in sorted(module_states):
        if "success" not in module_states[owner]:
            problems.append(f"模块 {owner} 缺少成功状态登记")
        if "stopped" not in module_states[owner]:
            problems.append(f"模块 {owner} 缺少停止状态登记")

    if problems:
        raise StateCopyRegistryError("；".join(problems))


__all__ = [
    "REQUIRED_CATEGORIES",
    "REQUIRED_FAILURE_CLASSES",
    "REQUIRED_MODULE_STATES",
    "RETRIEVAL_SUFFICIENCY_COPY",
    "STATE_COPY_REGISTRY",
    "render_state_copy",
    "retrieval_sufficiency_copy",
    "state_copy_entry",
    "state_copy_manifest",
    "validate_state_copy_registry",
]
