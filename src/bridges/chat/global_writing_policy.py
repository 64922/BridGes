"""Issue 17/07：一次主生成内使用的全局轻量有人味表达策略（兼容入口）。

Issue 07 起普通聊天策略由 ``bridges.chat.lightweight_policy`` 重建：每轮
按回答形态只编译少量高优先级正向规则，不再注入共享方法规则块、文章体裁
规则或全量禁词表。本模块保留 ``GlobalWritingPolicyCompiler`` 等公开接口
并委托轻量编译器，同时继续提供确定性保护区恢复函数，保证既有调用方与
旧快照重试兼容。保护区恢复自 Issue 05 起委托 ``bridges.chat.fact_protection``
的可绑定片段协议：按保留意图与对象锚点精确逐一绑定，不再盲替换。该模块只
编译自然语言正文的表达约束，不执行第二次模型调用，也不承载文章人味化任务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bridges.chat.fact_protection import (
    FACT_PROTECTION_PROTOCOL_VERSION,
    ProtectionIntent,
    plan_fragment_protection,
)
from bridges.chat.lightweight_policy import (
    GLOBAL_CHAT_LIGHTWEIGHT_SOURCE,
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    SAFE_BASELINE_POLICY_VERSION,
    ChatLightweightPolicyCompiler,
    ChatLightweightPolicySnapshot,
    ChatResponseForm,
    ToolOutcome,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import ExpressionTaskContract
from bridges.contracts.profile_adoption import AdoptedProfileSlice
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
        adopted_slice: AdoptedProfileSlice | None = None,
        profile_slice_id: str | None = None,
        profile_items: list[ProfileSliceItem] | tuple[ProfileSliceItem, ...] = (),
        profile_context: str | None = None,
        profile_failed: bool = False,
        tool_error: bool = False,
        tool_result: bool = False,
        refusal: bool = False,
        lesson: bool = False,
        continuation_text: str = "",
        tool_outcome: ToolOutcome = ToolOutcome.NONE,
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
            adopted_slice=adopted_slice,
            profile_slice_id=profile_slice_id,
            profile_items=profile_items,
            profile_context=profile_context,
            profile_failed=profile_failed,
            tool_error=tool_error,
            tool_result=tool_result,
            refusal=refusal,
            lesson=lesson,
            continuation_text=continuation_text,
            tool_outcome=tool_outcome,
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


# 保护区恢复已迁至 ``bridges.chat.fact_protection`` 的「可绑定片段协议」
# （Issue 05）：按保留意图与对象锚点精确逐一绑定，不再按同类第一个候选盲替换。
# 旧函数名保留为兼容入口，供既有调用方与旧快照重试继续使用。


def restore_protected_regions(
    original: str,
    candidate: str,
    *,
    append_missing: bool = False,
    additional_sources: tuple[str, ...] = (),
) -> str:
    """按保留意图恢复候选文本中的代码/公式/引用等合同片段。

    这是确定性保护而非自然度评分，委托 :func:`plan_fragment_protection`：

    - 仅对确有保留意图的具体对象做精确逐一绑定；已消费的候选片段不再配给
      其他源片段，顺序变化不串对象。
    - 用户明确要求纠正/计算时保留授权结果，不把旧输入当事实锁。
    - ``additional_sources`` 只限定引用资格，不按清单顺序强制换链接。
    - ``append_missing`` 默认 ``False``：非必需遗漏片段不强行补尾，是否必须
      出现由任务合同决定。
    """
    return plan_fragment_protection(
        original,
        candidate,
        append_missing=append_missing,
        additional_sources=additional_sources,
    ).content


__all__ = [
    "FACT_PROTECTION_PROTOCOL_VERSION",
    "GLOBAL_WRITING_POLICY_SOURCE",
    "GLOBAL_WRITING_POLICY_VERSION",
    "GlobalWritingPolicyCompiler",
    "GlobalWritingPolicyResource",
    "GlobalWritingPolicySnapshot",
    "ProtectionIntent",
    "SAFE_BASELINE_POLICY_VERSION",
    "ChatResponseForm",
    "ToolOutcome",
    "restore_protected_regions",
]
