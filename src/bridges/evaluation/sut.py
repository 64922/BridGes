"""被测系统（SUT）注册与规格（Issue 40）。

登记四种类别：完整 BridGes、基础 Qwen、合法开源参考方法与可解释消融
（移除画像切片 / 移除 bridges-humanizer / 移除证据检索）。消融以完整
BridGes 为基座，只关闭单一特性，保证对比可解释。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bridges.contracts.evaluation_suite import SUTKind


class SUTFeatureSet(BaseModel):
    """一次运行启用的系统特性组合。"""

    profile_slices: bool = Field(default=False, description="是否注入画像切片。")
    evidence_retrieval: bool = Field(default=False, description="是否使用本地证据检索。")
    humanizer_skill: bool = Field(default=False, description="是否启用 humanizer SKILL。")
    teaching_gate: bool = Field(default=False, description="是否启用教学证据门与强制联网。")
    plain_chat: bool = Field(default=False, description="纯聊天（无任何系统增强）。")
    deterministic_reference: bool = Field(
        default=False, description="确定性参考方法（不调用模型）。"
    )


class SUTSpec(BaseModel):
    sut_id: str = Field(description="被测系统标识。")
    kind: SUTKind = Field(description="被测系统类别。")
    description: str = Field(description="中文说明。")
    features: SUTFeatureSet = Field(description="启用的特性组合。")
    ablation_of: str | None = Field(
        default=None, description="消融的基座系统（消融类必填）。"
    )
    removes: list[str] = Field(
        default_factory=list, description="消融移除的特性（可解释性描述）。"
    )


def _full() -> SUTSpec:
    return SUTSpec(
        sut_id="bridges_full",
        kind=SUTKind.BRIDGES_FULL,
        description="完整 BridGes 纵向链路（画像切片 + 证据检索 + humanizer + 教学门）。",
        features=SUTFeatureSet(
            profile_slices=True,
            evidence_retrieval=True,
            humanizer_skill=True,
            teaching_gate=True,
        ),
    )


def _baseline() -> SUTSpec:
    return SUTSpec(
        sut_id="qwen_baseline",
        kind=SUTKind.QWEN_BASELINE,
        description="基础 Qwen：纯模型聊天，无画像、无证据、无 SKILL、无教学门。",
        features=SUTFeatureSet(plain_chat=True),
    )


def _reference() -> SUTSpec:
    return SUTSpec(
        sut_id="open_source_reference",
        kind=SUTKind.OPEN_SOURCE_REFERENCE,
        description="合法开源参考方法：本仓库原创的规则式基线（不调用模型，"
        "许可证见套件 license-reference-method）。",
        features=SUTFeatureSet(deterministic_reference=True),
    )


def _ablation(sut_id: str, *, removes: list[str], **features: bool) -> SUTSpec:
    base = _full()
    merged = base.features.model_copy(update=dict(features))
    return SUTSpec(
        sut_id=sut_id,
        kind=SUTKind.ABLATION,
        description=f"完整 BridGes 移除 {('、'.join(removes))} 的消融。",
        features=merged,
        ablation_of="bridges_full",
        removes=removes,
    )


def build_sut_registry() -> dict[str, SUTSpec]:
    """构建内置 SUT 注册表：完整、基线、参考与三个可解释消融。"""
    registry = {
        "bridges_full": _full(),
        "qwen_baseline": _baseline(),
        "open_source_reference": _reference(),
    }
    registry.update(
        {
            "ablation_no_profile": _ablation(
                "ablation_no_profile",
                removes=["画像切片"],
                profile_slices=False,
            ),
            "ablation_no_humanizer": _ablation(
                "ablation_no_humanizer",
                removes=["bridges-humanizer SKILL"],
                humanizer_skill=False,
            ),
            "ablation_no_evidence": _ablation(
                "ablation_no_evidence",
                removes=["证据检索"],
                evidence_retrieval=False,
                teaching_gate=False,
            ),
        }
    )
    return registry


__all__ = ["SUTSpec", "SUTFeatureSet", "build_sut_registry"]
