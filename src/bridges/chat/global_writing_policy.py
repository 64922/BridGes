"""Issue 17：一次主生成内使用的全局轻量有人味表达策略。

该模块只编译自然语言正文的表达约束，不执行第二次模型调用，也不承载
文章人味化任务。编译结果是不可变快照，调用方可以把它放进生成运行配置
和模型运行锁的脱敏元数据中，保证重试与租约恢复不随热更新漂移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.chat import ChatMode
from bridges.contracts.profiles import ProfileSliceItem


GLOBAL_WRITING_POLICY_VERSION = "global-humanized-writing-v1"
SAFE_BASELINE_POLICY_VERSION = "global-humanized-writing-safe-baseline-v1"
GLOBAL_WRITING_POLICY_SOURCE = (
    "BridGes 原创净室规则（见 src/bridges/skills/humanizer/skill/CLEAN_ROOM.md）"
)
_DEFAULT_RESOURCE = object()


@dataclass(frozen=True)
class GlobalWritingPolicyResource:
    """编译器使用的只读策略资源。"""

    version: str = GLOBAL_WRITING_POLICY_VERSION
    instruction: str = (
        "只调整面向用户的自然语言正文：先准确完成当前任务，再用清楚、具体、"
        "少空话的中文表达；对依据、证据强度和不确定性保持诚实。不要为了流畅"
        "删除限定条件、升级因果、编造经历、引用或来源，也不要规避 AI 检测、"
        "冒充真人、名人或特定作者。"
    )


class GlobalWritingPolicySnapshot(BaseModel):
    """绑定一次生成尝试的轻量表达策略快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(description="全局表达策略版本。")
    mode: str = Field(description="本轮固定对话模式。")
    profile_slice_id: str | None = Field(
        default=None, description="当前账户最小画像切片标识，不含画像全文。"
    )
    profile_items: tuple[str, ...] = Field(
        default_factory=tuple, description="允许影响表达的画像切片值的最小快照。"
    )
    profile_context: str | None = Field(
        default=None, description="本轮最小画像提示片段，用于同一策略快照重试。"
    )
    snapshot_complete: bool = Field(
        default=True, description="是否已经绑定本轮画像切片结果。"
    )
    fallback_reason: str | None = Field(
        default=None, description="降级到安全基线的确定性原因。"
    )
    source_record: str = Field(
        default=GLOBAL_WRITING_POLICY_SOURCE,
        description="策略来源清洁记录标识，不包含第三方正文。",
    )
    system_block: str = Field(description="注入主生成的中文表达合同。")

    def metadata(self) -> dict[str, Any]:
        """返回可写入运行配置/模型运行锁的非秘密元数据。"""
        return {
            "version": self.version,
            "mode": self.mode,
            "profile_slice_id": self.profile_slice_id,
            "profile_item_count": len(self.profile_items),
            "snapshot_complete": self.snapshot_complete,
            "fallback_reason": self.fallback_reason,
            "source_record": self.source_record,
        }


class GlobalWritingPolicyCompiler:
    """把模式与最小画像切片编译为一次性策略快照。"""

    def __init__(
        self,
        resource: GlobalWritingPolicyResource | None | object = _DEFAULT_RESOURCE,
    ) -> None:
        # ``None`` is an explicit way for startup/测试环境模拟策略资源缺失；
        # omitted resource still使用内置原创资源。
        self._resource = (
            GlobalWritingPolicyResource()
            if resource is _DEFAULT_RESOURCE
            else resource
            if isinstance(resource, GlobalWritingPolicyResource)
            else None
        )

    def compile(
        self,
        mode: ChatMode | str,
        *,
        profile_slice_id: str | None = None,
        profile_items: list[ProfileSliceItem] | tuple[ProfileSliceItem, ...] = (),
        profile_context: str | None = None,
        profile_failed: bool = False,
        existing_snapshot: GlobalWritingPolicySnapshot | dict[str, Any] | None = None,
    ) -> GlobalWritingPolicySnapshot:
        """编译策略；完整已有快照优先，保证重试不受热更新影响。"""
        if existing_snapshot is not None:
            snapshot = (
                existing_snapshot
                if isinstance(existing_snapshot, GlobalWritingPolicySnapshot)
                else GlobalWritingPolicySnapshot.model_validate(existing_snapshot)
            )
            if snapshot.snapshot_complete:
                return snapshot

        mode_value = mode.value if isinstance(mode, ChatMode) else str(mode)
        if self._resource is None or profile_failed:
            reason = (
                "profile_slice_unavailable" if profile_failed else "policy_resource_unavailable"
            )
            return self._fallback_snapshot(mode_value, reason)

        values = tuple(
            item.value_or_rule.strip()
            for item in profile_items[:6]
            if item.value_or_rule.strip()
        )
        return GlobalWritingPolicySnapshot(
            version=self._resource.version,
            mode=mode_value,
            profile_slice_id=profile_slice_id,
            profile_items=values,
            profile_context=profile_context,
            snapshot_complete=True,
            fallback_reason=None,
            source_record=GLOBAL_WRITING_POLICY_SOURCE,
            system_block=self._render(
                mode_value,
                values,
                self._resource.instruction,
                version=self._resource.version,
            ),
        )

    def seed(self, mode: ChatMode | str) -> GlobalWritingPolicySnapshot:
        """为 queued 运行创建尚未绑定画像切片的稳定策略种子。"""
        mode_value = mode.value if isinstance(mode, ChatMode) else str(mode)
        if self._resource is None:
            return self._fallback_snapshot(mode_value, "policy_resource_unavailable")
        return GlobalWritingPolicySnapshot(
            version=self._resource.version,
            mode=mode_value,
            snapshot_complete=False,
            system_block=self._render(
                mode_value,
                (),
                self._resource.instruction,
                version=self._resource.version,
            ),
        )

    def _fallback_snapshot(
        self, mode: str, reason: str
    ) -> GlobalWritingPolicySnapshot:
        return GlobalWritingPolicySnapshot(
            version=SAFE_BASELINE_POLICY_VERSION,
            mode=mode,
            profile_slice_id=None,
            profile_items=(),
            profile_context=None,
            snapshot_complete=True,
            fallback_reason=reason,
            source_record=GLOBAL_WRITING_POLICY_SOURCE,
            system_block=self._render(
                mode,
                (),
                "只完成任务本身，使用清楚、诚实、简洁的中文。保持原始事实、"
                "数字、限定条件、代码、公式、JSON、引用、链接、错误码、工具"
                "结果和协议字段不变；不规避 AI 检测、不冒充真人或名人、不伪造"
                "经历、来源或引用。",
                version=SAFE_BASELINE_POLICY_VERSION,
            ),
        )

    @staticmethod
    def _render(
        mode: str,
        profile_items: tuple[str, ...],
        instruction: str,
        *,
        version: str = GLOBAL_WRITING_POLICY_VERSION,
    ) -> str:
        role = (
            "日常陪伴像可靠且有分寸的朋友，接住当前语境但不替用户编造经历。"
            if mode == ChatMode.COMPANION.value
            else "学习模式像因材施教的老师，从当前水平循序解释，必要时用例子和"
            "短检查帮助理解，但不凭一次回答宣布掌握。"
        )
        profile = (
            "\n允许使用的当前账户画像信息（只影响例子、解释深度和称呼分寸）：\n"
            + "\n".join(f"- {value}" for value in profile_items)
            if profile_items
            else "\n本轮没有可用画像信息，不得自行推断用户经历、身份、人格或偏好。"
        )
        return (
            "【全局轻量有人味表达策略】\n"
            f"策略版本：{version}\n"
            f"{role}\n{instruction}\n"
            "只作用于模型生成的自然语言正文；代码、公式、JSON、引用、链接、"
            "结构化工具结果、确定性错误提示、加载/停止状态和协议字段属于受保护区，"
            "必须原样保留。工具结果外可以加简短说明，但不得改写证据。文章人味化"
            "任务的最终文章不经过本策略二次改写。"
            f"{profile}"
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
    "restore_protected_regions",
]
