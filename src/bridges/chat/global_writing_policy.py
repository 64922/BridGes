"""Issue 17/07：一次主生成内使用的全局轻量有人味表达策略（兼容入口）。

Issue 07 起普通聊天策略由 ``bridges.chat.lightweight_policy`` 重建：每轮
按回答形态只编译少量高优先级正向规则，不再注入共享方法规则块、文章体裁
规则或全量禁词表。本模块保留 ``GlobalWritingPolicyCompiler`` 等公开接口
并委托轻量编译器，同时继续提供确定性保护区恢复函数，保证既有调用方与
旧快照重试兼容。该模块只编译自然语言正文的表达约束，不执行第二次模型
调用，也不承载文章人味化任务。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bridges.chat.lightweight_policy import (
    GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    SAFE_BASELINE_POLICY_VERSION,
    ChatLightweightPolicyCompiler,
    ChatLightweightPolicySnapshot,
    ChatResponseForm,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import ExpressionTaskContract
from bridges.contracts.profiles import ProfileSliceItem

#: 旧策略版本常量（兼容快照解码；技能注册表 manifest 已改用新版本）。
GLOBAL_WRITING_POLICY_VERSION = "global-humanized-writing-v2"
GLOBAL_WRITING_POLICY_SOURCE = GLOBAL_CHAT_LIGHTWEIGHT_SOURCE
_DEFAULT_RESOURCE = object()


@dataclass(frozen=True)
class GlobalWritingPolicyResource:
    """编译器使用的只读策略资源（兼容旧接口；自定义 instruction 保留）。"""

    version: str = GLOBAL_CHAT_LIGHTWEIGHT_VERSION
    instruction: str | None = field(default=None)


#: 轻量策略快照类型别名：旧公开类型名保持可导入，字段向后兼容。
GlobalWritingPolicySnapshot = ChatLightweightPolicySnapshot


class GlobalWritingPolicyCompiler:
    """把模式与最小画像切片编译为一次性策略快照（Issue 07 轻量策略）。

    内部委托 :class:`ChatLightweightPolicyCompiler`：每轮按当前意图与回答
    形态只编译 6—10 条正向规则；``user_text`` 参与形态路由；重试回传
    完整快照时原样复用，不因热更新漂移。
    """

    def __init__(
        self,
        resource: GlobalWritingPolicyResource | None | object = _DEFAULT_RESOURCE,
    ) -> None:
        # ``None`` 是启动/测试环境模拟策略资源缺失的显式方式；
        # omitted resource 仍使用内置原创资源。
        if resource is _DEFAULT_RESOURCE:
            self._delegate = ChatLightweightPolicyCompiler()
            self._custom_instruction = None
        elif resource is None:
            self._delegate = ChatLightweightPolicyCompiler(resource=None)
            self._custom_instruction = None
        elif isinstance(resource, GlobalWritingPolicyResource):
            self._delegate = ChatLightweightPolicyCompiler(
                instruction=resource.instruction,
                version=resource.version,
            )
            self._custom_instruction = resource.instruction
        else:
            # 非预期对象资源：与旧行为一致，按资源缺失降级。
            self._delegate = ChatLightweightPolicyCompiler(resource=None)
            self._custom_instruction = None

    def compile(
        self,
        mode: ChatMode | str,
        *,
        user_text: str | None = None,
        expression_contract: ExpressionTaskContract | None = None,
        profile_slice_id: str | None = None,
        profile_items: list[ProfileSliceItem] | tuple[ProfileSliceItem, ...] = (),
        profile_context: str | None = None,
        profile_failed: bool = False,
        tool_error: bool = False,
        tool_result: bool = False,
        refusal: bool = False,
        lesson: bool = False,
        existing_snapshot: GlobalWritingPolicySnapshot | dict[str, Any] | None = None,
    ) -> GlobalWritingPolicySnapshot:
        """编译策略；完整已有快照优先，保证重试不受热更新影响。"""
        # 复用路径：完整快照原样返回（含旧版快照），不追加资源指令，
        # 保证“旧任务重试继续复用原策略快照”语义不被自定义指令污染。
        if existing_snapshot is not None:
            snapshot = (
                existing_snapshot
                if isinstance(existing_snapshot, GlobalWritingPolicySnapshot)
                else GlobalWritingPolicySnapshot.model_validate(existing_snapshot)
            )
            if snapshot.snapshot_complete:
                return snapshot
        snapshot = self._delegate.compile(
            mode,
            user_text=user_text or "",
            expression_contract=expression_contract,
            profile_slice_id=profile_slice_id,
            profile_items=profile_items,
            profile_context=profile_context,
            profile_failed=profile_failed,
            tool_error=tool_error,
            tool_result=tool_result,
            refusal=refusal,
            lesson=lesson,
            existing_snapshot=existing_snapshot,
        )
        return self._append_custom_instruction(snapshot)

    def seed(self, mode: ChatMode | str) -> GlobalWritingPolicySnapshot:
        """为 queued 运行创建尚未绑定画像切片的稳定策略种子。"""
        return self._append_custom_instruction(self._delegate.seed(mode))

    def _append_custom_instruction(
        self, snapshot: GlobalWritingPolicySnapshot
    ) -> GlobalWritingPolicySnapshot:
        if self._custom_instruction is None:
            return snapshot
        return snapshot.model_copy(
            update={
                "system_block": (
                    snapshot.system_block + "\n" + self._custom_instruction
                )
            }
        )


# 这些正则只负责保护已有合同片段，不是全局硬词表或风格评分器。
_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("code_fence", re.compile(r"```[\s\S]*?```")),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("formula", re.compile(r"\$[^$\n]+\$")),
    ("url", re.compile(r"https?://[^\s<>\]})]+")),
    ("json", re.compile(r"\{\s*\"[^{}\n]+\"\s*:\s*[^{}\n]+\}")),
    ("citation", re.compile(r"(?<!\w)\[[0-9]+(?:[-,][0-9]+)*\]")),
    ("number_with_unit", re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*[A-Za-zμΩ%°][^\s，。；;)]*")),
)


def _protected_regions(text: str) -> list[tuple[str, str]]:
    regions: list[tuple[str, str]] = []
    for kind, pattern in _PROTECTED_PATTERNS:
        regions.extend((kind, match.group(0)) for match in pattern.finditer(text))
    return regions


def restore_protected_regions(
    original: str,
    candidate: str,
    *,
    append_missing: bool = True,
    additional_sources: tuple[str, ...] = (),
) -> str:
    """按原始输入恢复候选文本中的代码/公式/引用等合同片段。

    这是确定性保护而非自然度评分：候选已有相同片段时不重复插入；候选
    修改了同类片段时按出现顺序替换；候选完全删除片段时追加原片段，宁可
    保留合同内容，也不让表达策略覆盖事实或机器协议。
    """
    restored = candidate
    for source_index, source in enumerate((original, *additional_sources)):
        source_append_missing = append_missing and source_index == 0
        for kind, original_region in _protected_regions(source):
            if original_region in restored:
                continue
            pattern = dict(_PROTECTED_PATTERNS)[kind]
            match = pattern.search(restored)
            if match is not None:
                restored = (
                    restored[: match.start()]
                    + original_region
                    + restored[match.end() :]
                )
            elif source_append_missing:
                separator = "" if not restored or restored.endswith("\n") else "\n"
                restored += f"{separator}{original_region}"
    return restored


__all__ = [
    "GLOBAL_WRITING_POLICY_SOURCE",
    "GLOBAL_WRITING_POLICY_VERSION",
    "GlobalWritingPolicyCompiler",
    "GlobalWritingPolicyResource",
    "GlobalWritingPolicySnapshot",
    "SAFE_BASELINE_POLICY_VERSION",
    "ChatResponseForm",
    "restore_protected_regions",
]
