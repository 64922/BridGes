"""工单 01：画像闭环基线的语料类型与公共常量。

本模块只定义可重放评测资产的类型与固定常量，不参与生产运行路径。
场景正文见 ``profile_journey_scenarios.py``，可运行旅程与摘要见
``profile_journeys.py``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

#: 评测语料版本：任何场景文本、预期或变体改动都必须提升版本。
#: v2（工单 04）：T01–T03/T02 基线观测从 known_failing 更新为 pass。
PROFILE_JOURNEY_CORPUS_VERSION = "profile-loop-corpus-v2"

#: 合成对话的发生日期（写入证据，提示数据不是真实用户对话）。
SYNTHETIC_CONVERSATION_DATE = "2026-10-15（合成）"

#: 场景实现状态：``baseline_defect`` 为当前已复现缺陷；``planned`` 为尚未
#: 实现的目标功能（不得表述为现有缺陷）。
SCENARIO_STATUS_BASELINE_DEFECT = "baseline_defect"
SCENARIO_STATUS_PLANNED = "planned"

#: 语料划分：开发语料用于实现期自测；留出变体只在 18 的正式评测使用。
SPLIT_DEV = "dev"
SPLIT_HOLDOUT = "holdout"

#: 变体的固定类型标签。
VARIANT_NEGATION = "negation"
VARIANT_MIXED_SUBJECT = "mixed_subject"
VARIANT_SYNONYM = "synonym"
VARIANT_HOLDOUT = "holdout"
VARIANT_KINDS = (
    VARIANT_NEGATION,
    VARIANT_MIXED_SUBJECT,
    VARIANT_SYNONYM,
    VARIANT_HOLDOUT,
)


@dataclass(frozen=True, slots=True)
class JourneyTurn:
    """一条固定发言：说话者、来源时间与正文。"""

    speaker: str
    source_time: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScenarioVariant:
    """同一场景的变体：否定、混合主体、同义表达或独立留出措辞。"""

    kind: str
    content: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    """场景的固定预期结果（目标合同，不是现状描述）。"""

    facts: tuple[str, ...]
    changes: tuple[str, ...]
    answer: str
    baseline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": list(self.facts),
            "changes": list(self.changes),
            "answer": self.answer,
            "baseline": self.baseline,
        }


@dataclass(frozen=True, slots=True)
class ProfileScenario:
    """一条固定、可重放的画像场景。"""

    scenario_id: str
    title: str
    contracts: tuple[str, ...]
    responsible_tickets: tuple[str, ...]
    split: str
    status: str
    turns: tuple[JourneyTurn, ...]
    expectation: ScenarioExpectation
    variants: tuple[ScenarioVariant, ...]
    runnable_journey_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "contracts": list(self.contracts),
            "responsible_tickets": list(self.responsible_tickets),
            "split": self.split,
            "status": self.status,
            "turns": [turn.to_dict() for turn in self.turns],
            "expectation": self.expectation.to_dict(),
            "variants": [variant.to_dict() for variant in self.variants],
            "runnable_journey_id": self.runnable_journey_id,
        }


@dataclass(frozen=True, slots=True)
class BaselineJourney:
    """一条可经实际服务入口重放的六类基线旅程。"""

    journey_id: str
    scenario_id: str
    problem_class: str
    title: str
    user_turns: tuple[str, ...]
    target: str
    baseline_status: str
    baseline_observation: str
    evidence_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def user_turn(time_label: str, content: str) -> JourneyTurn:
    return JourneyTurn(speaker="user", source_time=time_label, content=content)


def assistant_turn(time_label: str, content: str) -> JourneyTurn:
    return JourneyTurn(speaker="assistant", source_time=time_label, content=content)


def make_variant(kind: str, content: str, note: str) -> ScenarioVariant:
    """构造变体并校验类型标签（断言标签在固定集合内）。"""

    assert kind in VARIANT_KINDS, f"未知变体类型：{kind}"
    return ScenarioVariant(kind=kind, content=content, note=note)


__all__ = [
    "PROFILE_JOURNEY_CORPUS_VERSION",
    "SYNTHETIC_CONVERSATION_DATE",
    "SCENARIO_STATUS_BASELINE_DEFECT",
    "SCENARIO_STATUS_PLANNED",
    "SPLIT_DEV",
    "SPLIT_HOLDOUT",
    "VARIANT_NEGATION",
    "VARIANT_MIXED_SUBJECT",
    "VARIANT_SYNONYM",
    "VARIANT_HOLDOUT",
    "VARIANT_KINDS",
    "JourneyTurn",
    "ScenarioVariant",
    "ScenarioExpectation",
    "ProfileScenario",
    "BaselineJourney",
    "user_turn",
    "assistant_turn",
    "make_variant",
]
