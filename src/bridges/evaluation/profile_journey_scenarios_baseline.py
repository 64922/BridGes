"""工单 01：T01–T04 基线缺陷场景（开发语料 + 留出变体）。

本模块只承载场景正文，不参与生产运行路径。所有语料均为合成数据；「预期
事实」是目标合同，不是当前已实现能力。T05–T20 见
``profile_journey_scenarios_planned.py``。
"""

from __future__ import annotations

from bridges.evaluation.profile_journey_types import (
    SCENARIO_STATUS_BASELINE_DEFECT,
    SPLIT_DEV,
    ProfileScenario,
    ScenarioExpectation,
    assistant_turn,
    make_variant,
    user_turn,
)

_T01 = ProfileScenario(
    scenario_id="T01",
    title="姓名、宠物、习惯等四类外个人事实",
    contracts=("D02", "D11"),
    responsible_tickets=("03", "04"),
    split=SPLIT_DEV,
    status=SCENARIO_STATUS_BASELINE_DEFECT,
    turns=(
        user_turn("第1轮 09:00", "我叫小林。"),
        user_turn("第2轮 09:02", "我养了一只猫叫团子。"),
        user_turn("第3轮 09:04", "周末一般会去爬山。"),
    ),
    expectation=ScenarioExpectation(
        facts=("称呼为小林", "养了一只叫团子的猫", "周末一般去爬山"),
        changes=(),
        answer="后续回答能自然使用称呼、宠物与习惯；周五推荐活动时考虑到爬山。",
        baseline=(
            "2026-10-15 确定性基线：三条消息均未产生提取运行、四维记录或原子"
            "条目，属漏提取缺陷（03/04）。"
        ),
    ),
    variants=(
        make_variant("synonym", "大家都叫我小林。", "称呼的同义表达。"),
        make_variant("mixed_subject", "我朋友养了一只猫，我自己养了只狗。", "只记录用户自己的狗。"),
        make_variant("holdout", "我叫阿泽，家里有只橘猫叫团子，闲时喜欢去徒步。", "独立留出措辞。"),
    ),
    runnable_journey_id="t01-personal-facts",
)

_T02 = ProfileScenario(
    scenario_id="T02",
    title="学科兴趣与敏感心理推断的区分",
    contracts=("D02", "D04", "D11"),
    responsible_tickets=("04", "09"),
    split=SPLIT_DEV,
    status=SCENARIO_STATUS_BASELINE_DEFECT,
    turns=(user_turn("第1轮 10:00", "我喜欢心理学。"),),
    expectation=ScenarioExpectation(
        facts=("对心理学有学科兴趣",),
        changes=(),
        answer="把心理学当作可讨论的学科兴趣，不触发心理健康或人格推断。",
        baseline=(
            "2026-10-15 确定性基线：「心理」关键词命中敏感规则被判 forbidden，"
            "未产生提取运行，属误拦截缺陷（04/09）。"
        ),
    ),
    variants=(
        make_variant("synonym", "我最近在读心理学的书。", "学科兴趣的另一种表达。"),
        make_variant("negation", "我对心理学不感兴趣。", "否定表达不得写入兴趣。"),
        make_variant("holdout", "我最近迷上了认知科学，常看相关公开课。", "独立留出措辞。"),
    ),
    runnable_journey_id="t02-psychology-interest",
)

_T03 = ProfileScenario(
    scenario_id="T03",
    title="学习内容与时间约束并存",
    contracts=("D02", "D03", "D11"),
    responsible_tickets=("04", "11"),
    split=SPLIT_DEV,
    status=SCENARIO_STATUS_BASELINE_DEFECT,
    turns=(user_turn("第1轮 11:00", "我在学线性代数，每晚只能抽出半小时。"),),
    expectation=ScenarioExpectation(
        facts=("正在学线性代数", "每晚可用于学习的时间约半小时"),
        changes=(),
        answer="安排学习任务时同时遵守学习内容与半小时时间限制。",
        baseline=(
            "2026-10-15 确定性基线：仅提取 knowledge_interest=线性代数，"
            "「每晚只能抽出半小时」丢失，属漏提取缺陷（04/11）。"
        ),
    ),
    variants=(
        make_variant("synonym", "我每天只能花半小时学线代。", "内容与约束换说法。"),
        make_variant("holdout", "我打算学概率论，每天最多挤出一小时。", "独立留出措辞。"),
    ),
    runnable_journey_id="t03-study-time-limit",
)

_T04 = ProfileScenario(
    scenario_id="T04",
    title="跨轮指代与正确的历史范围",
    contracts=("D02",),
    responsible_tickets=("05",),
    split=SPLIT_DEV,
    status=SCENARIO_STATUS_BASELINE_DEFECT,
    turns=(
        assistant_turn("第1轮 13:00", "你现在读什么专业？"),
        user_turn("第2轮 13:01", "我在读计算机专业，大二。"),
        assistant_turn("第3轮 13:02", "明白。"),
        user_turn("第4轮 13:05", "这个专业以后先给我结论，再展开解释。"),
    ),
    expectation=ScenarioExpectation(
        facts=("跨轮指代「这个专业」解析为计算机专业", "长期偏好：以后先给结论"),
        changes=(),
        answer="理解指代并记住长期偏好；只读取截至当前消息的必要前文，不读取未来内容。",
        baseline=(
            "2026-10-15 确定性基线：网关提取收到 conversation_id=retry 且邻近"
            "消息为空；指代无法解析，属跨轮依据缺陷（05）。"
        ),
    ),
    variants=(
        make_variant("synonym", "我学的就是这个方向，以后先给结论。", "指代的另一种说法。"),
        make_variant(
            "holdout",
            "助手先问过研究兴趣，用户答完后说「它的进展以后都先讲重点」。",
            "独立留出措辞。",
        ),
    ),
    runnable_journey_id="t04-cross-turn-reference",
)

#: T01–T04 基线缺陷场景（顺序即验收编号顺序）。
SCENARIOS_BASELINE: tuple[ProfileScenario, ...] = (_T01, _T02, _T03, _T04)

__all__ = ["SCENARIOS_BASELINE"]
