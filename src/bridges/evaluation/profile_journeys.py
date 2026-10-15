"""工单 01：画像闭环合成基线与可复现验收旅程（公共入口）。

- 类型与常量见 ``profile_journey_types.py``；
- T01–T20 场景正文见 ``profile_journey_scenarios.py``；
- 本模块提供六类已知问题的可运行旅程、语料摘要与查询辅助。

全部语料均为合成数据，不含真实私人原文、凭据或生产数据。「预期事实」
是目标合同，不是当前已实现能力；未支持的新功能标记为 ``planned``。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bridges.evaluation.profile_journey_scenarios_baseline import (
    SCENARIOS_BASELINE,
)
from bridges.evaluation.profile_journey_scenarios_planned import (
    SCENARIOS_PLANNED,
)
from bridges.evaluation.profile_journey_types import (
    PROFILE_JOURNEY_CORPUS_VERSION,
    SCENARIO_STATUS_BASELINE_DEFECT,
    SCENARIO_STATUS_PLANNED,
    SPLIT_DEV,
    SPLIT_HOLDOUT,
    SYNTHETIC_CONVERSATION_DATE,
    VARIANT_KINDS,
    BaselineJourney,
    JourneyTurn,
    ProfileScenario,
    ScenarioExpectation,
    ScenarioVariant,
    assistant_turn,
    make_variant,
    user_turn,
)

#: T01–T20 固定语料（顺序即验收编号顺序；先基线缺陷后计划场景）。
SCENARIOS: tuple[ProfileScenario, ...] = SCENARIOS_BASELINE + SCENARIOS_PLANNED

SCENARIOS_BY_ID: dict[str, ProfileScenario] = {
    scenario.scenario_id: scenario for scenario in SCENARIOS
}

#: 六类已知问题旅程 + 写入正向对照 + 召回对照（当前失败）；经实际服务入口可重放。
BASELINE_JOURNEYS: tuple[BaselineJourney, ...] = (
    BaselineJourney(
        journey_id="t01-name",
        scenario_id="T01",
        problem_class="name",
        title="明确姓名自述不应漏提取",
        user_turns=("我叫小林。",),
        target="提取「称呼为小林」并在后续回答中使用。",
        baseline_status="known_failing",
        baseline_observation=(
            "无提取运行、无记录、无条目；称呼只出现在当前轮回复（模型直接"
            "复述输入），不进入画像。"
        ),
        evidence_note="对照 PRD 3.1 与 T01；责任票 03/04。",
    ),
    BaselineJourney(
        journey_id="t01-pet",
        scenario_id="T01",
        problem_class="pet",
        title="宠物自述不应漏提取",
        user_turns=("我养了一只猫叫团子。",),
        target="提取「养了一只叫团子的猫」并在后续回答中使用。",
        baseline_status="known_failing",
        baseline_observation="无提取运行、无记录、无条目。",
        evidence_note="对照 PRD 3.1 与 T01；责任票 03/04。",
    ),
    BaselineJourney(
        journey_id="t01-habit",
        scenario_id="T01",
        problem_class="habit",
        title="习惯自述不应漏提取",
        user_turns=("周末一般会去爬山。",),
        target="提取「周末一般去爬山」并在周末相关回答中使用。",
        baseline_status="known_failing",
        baseline_observation="无提取运行、无记录、无条目。",
        evidence_note="对照 PRD 3.1 与 T01；责任票 03/04。",
    ),
    BaselineJourney(
        journey_id="t02-psychology",
        scenario_id="T02",
        problem_class="sensitive_keyword",
        title="学科兴趣不应被敏感词误拦截",
        user_turns=("我喜欢心理学。",),
        target="提取学科兴趣「心理学」，不产生心理推断。",
        baseline_status="known_failing",
        baseline_observation="分类为 forbidden/sensitive_content，不产生提取运行。",
        evidence_note="对照 PRD 3.1 与 T02；责任票 04/09。",
    ),
    BaselineJourney(
        journey_id="t03-time-limit",
        scenario_id="T03",
        problem_class="time_constraint",
        title="学习内容与时间限制应同时保留",
        user_turns=("我在学线性代数，每晚只能抽出半小时。",),
        target="提取「正在学线性代数」与「每晚约半小时」。",
        baseline_status="known_failing",
        baseline_observation="仅提取 knowledge_interest=线性代数，时间约束丢失。",
        evidence_note="对照 PRD 3.1 与 T03；责任票 04/11。",
    ),
    BaselineJourney(
        journey_id="t04-cross-turn",
        scenario_id="T04",
        problem_class="neighbor_messages",
        title="跨轮抽取应拿到正确历史范围",
        user_turns=(
            "我在读计算机专业，大二。",
            "以后先给我结论，再展开解释。",
        ),
        target="网关抽取收到真实会话的邻近用户消息（非空），并据此解析指代。",
        baseline_status="known_failing",
        baseline_observation=(
            "网关抽取调用 project_id=retry，邻近消息为空；真实会话的邻居读取"
            "也因逆序遇当前消息即退出而返回空。"
        ),
        evidence_note="对照 PRD 3.1 与 T04；责任票 05。",
    ),
    BaselineJourney(
        journey_id="positive-academic-control",
        scenario_id="T03",
        problem_class="positive_control",
        title="正向对照：明确学业自述走通提取与成对写入",
        user_turns=("我是计算机专业的大二学生。",),
        target="高置信学业自述被规则抽取成功，四维记录与原子条目成对写入。",
        baseline_status="pass",
        baseline_observation=(
            "提取运行 succeeded_written，四维记录与原子条目成对出现，条目保持"
            " active。采用/召回不在本旅程检查范围（见 recall-academic-control）。"
        ),
        evidence_note="当前可用写入路径的正向对照；后续票不得破坏。",
    ),
    BaselineJourney(
        journey_id="recall-academic-control",
        scenario_id="T04",
        problem_class="recall_control",
        title="召回对照：下一轮问题应直接采用已存学业事实",
        user_turns=(
            "我是计算机专业的大二学生。",
            "你记得我在读什么专业吗？",
        ),
        target="下一轮采用切片（adopted_profile_slice）包含该学业事实并被回答直接使用。",
        baseline_status="known_failing",
        baseline_observation=(
            "切片编译把该事实排除为「与当前问题无关」，下一轮 adopted_items 为"
            "空；回答中的事实来自会话历史而非画像采用（D11 召回缺口）。"
        ),
        evidence_note="责任票 11/15；采用因果待 18 复核。",
    ),
)

BASELINE_JOURNEYS_BY_ID: dict[str, BaselineJourney] = {
    journey.journey_id: journey for journey in BASELINE_JOURNEYS
}


def corpus_payload() -> dict[str, Any]:
    """规范化语料载荷（摘要与证据文件共用同一序列化）。"""

    return {
        "corpus_version": PROFILE_JOURNEY_CORPUS_VERSION,
        "synthetic_date": SYNTHETIC_CONVERSATION_DATE,
        "scenarios": [scenario.to_dict() for scenario in SCENARIOS],
        "baseline_journeys": [journey.to_dict() for journey in BASELINE_JOURNEYS],
    }


def corpus_digest() -> str:
    """语料摘要：任何文本或预期变化都会改变该值。"""

    canonical = json.dumps(
        corpus_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def variant_kinds_by_scenario() -> dict[str, tuple[str, ...]]:
    """场景 → 变体类型集合（结构校验与报告共用）。"""

    return {
        scenario.scenario_id: tuple(
            dict.fromkeys(variant.kind for variant in scenario.variants)
        )
        for scenario in SCENARIOS
    }


__all__ = [
    "PROFILE_JOURNEY_CORPUS_VERSION",
    "SYNTHETIC_CONVERSATION_DATE",
    "SCENARIO_STATUS_BASELINE_DEFECT",
    "SCENARIO_STATUS_PLANNED",
    "SPLIT_DEV",
    "SPLIT_HOLDOUT",
    "VARIANT_KINDS",
    "JourneyTurn",
    "ScenarioVariant",
    "ScenarioExpectation",
    "ProfileScenario",
    "BaselineJourney",
    "SCENARIOS",
    "SCENARIOS_BY_ID",
    "BASELINE_JOURNEYS",
    "BASELINE_JOURNEYS_BY_ID",
    "corpus_payload",
    "corpus_digest",
    "variant_kinds_by_scenario",
    "assistant_turn",
    "user_turn",
    "make_variant",
]
