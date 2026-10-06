"""工单 39：三个表达策略臂的职责封装（评测专用，不改产品路径）。

同一 ChatService 管线只替换表达系统块：

- ``current-v4``：现行 ``GlobalWritingPolicyCompiler``（global-chat-lightweight-v4）；
- ``legacy-v2``：提交 079faab6 的冻结回放（global-chat-lightweight-v2）；
- ``concise-baseline``：仅保留事实/任务合同的简洁基线。

评测臂必须与产品调用方同形：``turn._compile_writing_policy`` 会把真实
任务、续接与工具状态传入。包装器负责把 v4 信号映射到旧编译器，并把三个
臂的输出额度统一为同一确定性口径，保证「同模型、同证据、可比输入/输出
额度」。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from bridges.chat.global_writing_policy import (
    GlobalWritingPolicyCompiler,
    GlobalWritingPolicySnapshot,
)
from bridges.chat.lightweight_policy import (
    GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    ChatLightweightPolicySnapshot,
    ChatResponseForm,
    ToolOutcome,
    output_tokens_for_request,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression_task import ExpressionTaskContract
from bridges.contracts.profile_adoption import AdoptedProfileSlice
from bridges.contracts.profiles import ProfileSliceItem
from bridges.evaluation.legacy_v2_policy import (
    LEGACY_V2_STRATEGY_VERSION,
    LegacyV2LightweightPolicyCompiler,
)

#: 简洁基线版本：只保留事实/任务合同，不携带任何表达形态规则或画像规则。
CONCISE_BASELINE_VERSION = "eval-concise-contract-baseline-v1"

#: 简洁基线的系统块原文（仅事实/任务合同；不含人味规则与画像）。
CONCISE_BASELINE_BLOCK = (
    "【简洁任务合同】\n"
    "按当前请求与任务合同直接回答；遵守用户本轮的明确限制。\n"
    "保持事实、数字、单位、限定条件、代码、公式、引用和真实工具状态不变；"
    "不编造经历、来源或引用。\n"
    "没有被要求时不追加建议、安慰或追问；任务完成后自然结束。"
)


class StrategyArm(StrEnum):
    """三个真实模型配对策略臂。"""

    CURRENT = "current-v4"
    LEGACY = "legacy-v2"
    BASELINE = "concise-baseline"


#: 臂 → 策略版本（进入运行锁与模型调用合同）。
ARM_STRATEGY_VERSIONS: dict[StrategyArm, str] = {
    StrategyArm.CURRENT: GLOBAL_CHAT_LIGHTWEIGHT_VERSION,
    StrategyArm.LEGACY: LEGACY_V2_STRATEGY_VERSION,
    StrategyArm.BASELINE: CONCISE_BASELINE_VERSION,
}

ARM_TITLES: dict[StrategyArm, str] = {
    StrategyArm.CURRENT: "现行策略 v4（工单 21/22 后的新策略）",
    StrategyArm.LEGACY: "历史策略 v2（冻结回放历史对照）",
    StrategyArm.BASELINE: "简洁事实/任务合同基线",
}


class ArmPolicyCompiler:
    """评测臂编译器：与 ``GlobalWritingPolicyCompiler`` 同形的鸭子接口。"""

    def __init__(
        self,
        arm: StrategyArm,
        *,
        delegate: GlobalWritingPolicyCompiler | None = None,
    ) -> None:
        self.arm = arm
        self._delegate = delegate or GlobalWritingPolicyCompiler()
        self._legacy = LegacyV2LightweightPolicyCompiler()

    @property
    def strategy_version(self) -> str:
        return ARM_STRATEGY_VERSIONS[self.arm]

    def compile(
        self,
        mode: ChatMode | str,
        *,
        user_text: str | None = None,
        expression_contract: ExpressionTaskContract | None = None,
        adopted_slice: AdoptedProfileSlice | None = None,
        profile_slice_id: str | None = None,
        profile_items: Sequence[ProfileSliceItem] = (),
        profile_context: str | None = None,
        profile_failed: bool = False,
        tool_error: bool = False,
        tool_result: bool = False,
        refusal: bool = False,
        lesson: bool = False,
        continuation_text: str = "",
        tool_outcome: ToolOutcome = ToolOutcome.NONE,
        existing_snapshot: GlobalWritingPolicySnapshot | dict[str, Any] | None = None,
    ) -> ChatLightweightPolicySnapshot:
        text = user_text or ""
        if self.arm is StrategyArm.CURRENT:
            snapshot = self._delegate.compile(
                mode,
                user_text=text,
                expression_contract=expression_contract,
                adopted_slice=adopted_slice,
                profile_slice_id=profile_slice_id,
                profile_items=list(profile_items),
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
        elif self.arm is StrategyArm.LEGACY:
            # v2 没有续接/采用快照/工具枚举信号：把可见信号按旧布尔接口映射，
            # 原子采用条目按旧路径折算为无类别条目后会被旧白名单丢弃（历史事实）。
            legacy_items: list[ProfileSliceItem] = list(profile_items)
            if adopted_slice is not None:
                legacy_items.extend(adopted_slice.to_profile_slice().included_items)
            snapshot = self._legacy.compile(
                mode,
                user_text=text,
                expression_contract=expression_contract,
                profile_slice_id=profile_slice_id
                or (adopted_slice.slice_id if adopted_slice is not None else None),
                profile_items=legacy_items,
                profile_context=profile_context,
                profile_failed=profile_failed,
                tool_error=tool_error or tool_outcome is ToolOutcome.ERROR,
                tool_result=tool_result
                or tool_outcome in {ToolOutcome.SUCCESS, ToolOutcome.PARTIAL},
                refusal=refusal,
                lesson=lesson,
                existing_snapshot=existing_snapshot,
            )
        else:
            snapshot = self._baseline_snapshot(mode, existing_snapshot=existing_snapshot)
        budget = output_tokens_for_request(text, continuation_text)
        return snapshot.model_copy(update={"output_tokens": budget})

    def seed(self, mode: ChatMode | str) -> ChatLightweightPolicySnapshot:
        if self.arm is StrategyArm.CURRENT:
            return self._delegate.seed(mode)
        if self.arm is StrategyArm.LEGACY:
            return self._legacy.seed(mode)
        return self._baseline_snapshot(mode, snapshot_complete=False)

    def _baseline_snapshot(
        self,
        mode: ChatMode | str,
        *,
        existing_snapshot: GlobalWritingPolicySnapshot | dict[str, Any] | None = None,
        snapshot_complete: bool = True,
    ) -> ChatLightweightPolicySnapshot:
        if existing_snapshot is not None:
            snapshot = (
                existing_snapshot
                if isinstance(existing_snapshot, ChatLightweightPolicySnapshot)
                else ChatLightweightPolicySnapshot.model_validate(existing_snapshot)
            )
            if snapshot.snapshot_complete:
                return snapshot
        mode_value = mode.value if isinstance(mode, ChatMode) else str(mode)
        return ChatLightweightPolicySnapshot(
            version=CONCISE_BASELINE_VERSION,
            mode=mode_value,
            form=ChatResponseForm.COMPACT_DEFAULT,
            rule_ids=("concise-contract",),
            rule_count=1,
            snapshot_complete=snapshot_complete,
            system_block=CONCISE_BASELINE_BLOCK,
        )


__all__ = [
    "ARM_STRATEGY_VERSIONS",
    "ARM_TITLES",
    "CONCISE_BASELINE_BLOCK",
    "CONCISE_BASELINE_VERSION",
    "ArmPolicyCompiler",
    "StrategyArm",
]
