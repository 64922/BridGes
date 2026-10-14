"""工单 13：学习模式真实教学旅程的固定情境、预先量表与独立核对基准。

本模块只承载可重放评测资产，不参与生产运行路径：

- 固定情境：概率概念、函数/极限概念、编程理解三类概念误解旅程，外加
  长期目标跨会话续学与公开查证两个补充旅程。每个情境的学员发言是固定
  文本，包含明确误解、正确等价回答、提示依赖、前置知识不足与不愿作答；
- 预先量表：七个维度的 0/1/2 评分标准在运行时不可更改，只能按本次实际
  记录评分；
- 独立核对基准：数学与代码事实用标准库工具复算（分数精确运算、数值
  极限、真实执行代码片段），不依赖导师或诊断模型自称正确。

真实模型输出不假设确定；本模块提供的是输入语料与评价依据，结论证据由
``scripts/run_issue13_learning_journeys.py`` 与人工核对记录共同给出。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

#: 评测语料版本：任何情境文本或量表改动都必须提升版本并在发布报告中说明。
LEARNING_JOURNEY_CORPUS_VERSION = "study-journey-corpus-v1"


@dataclass(frozen=True, slots=True)
class RubricDimension:
    """一个预先定义的量表维度（0/1/2 分）。"""

    dimension_id: str
    name: str
    description: str
    zero: str
    one: str
    two: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: 七维量表：误解定位、解释正确性、提示适宜、教法随表现变化、尊重控制、
#: 不夸大掌握、诊断反馈教学。评分标准先于运行冻结在本常量中。
RUBRIC_DIMENSIONS: tuple[RubricDimension, ...] = (
    RubricDimension(
        dimension_id="misconception_localization",
        name="误解定位具体性",
        description="是否指出学员推理中的具体错误位置，并给出可检验的澄清。",
        zero="未定位误解，或把正确表述误判为错误。",
        one="指出存在错误但停留在笼统表述，未对应到具体推理步骤。",
        two="明确指出具体错误命题（可引用学员原话）并给出可检验的澄清。",
    ),
    RubricDimension(
        dimension_id="explanation_correctness",
        name="解释正确性",
        description="数学/代码等可核验内容与独立核对基准一致，条件与前提保留。",
        zero="出现被独立核对否定的错误结论或伪造的推导。",
        one="主要结论正确但遗漏关键条件、定义域或边界说明。",
        two="结论与独立核对一致，且保留了必要条件与限制。",
    ),
    RubricDimension(
        dimension_id="hint_appropriateness",
        name="提示适宜性",
        description="提示粒度是否匹配当前表现：先探查后提示、不直接泄露答案、不重复同一问法。",
        zero="直接给出最终答案冒充提示，或机械重复同一问题。",
        one="给出提示但粒度不随表现变化，或提示夹带答案要点。",
        two="提示逐步细化，与学员当前困难匹配，未直接替代其完成推理。",
    ),
    RubricDimension(
        dimension_id="adaptivity_to_performance",
        name="教法随实际表现变化",
        description="是否依据实际回答调整例子、粒度或补讲前置知识，而非只换措辞。",
        zero="无论实际表现如何都采用同一种教法。",
        one="有调整迹象但未针对具体困难（如仅换例子未补前置）。",
        two="可观察到针对具体困难的调整（补前置、降低粒度、换应用情境）。",
    ),
    RubricDimension(
        dimension_id="respect_for_control",
        name="尊重用户控制",
        description="直接讲解、跳过验证、暂停与改方向是否被尊重且不判错、不施压。",
        zero="跳过/直讲被拒绝或被记录为知识缺口、答错。",
        one="基本尊重但夹带强制作答、反复追问或额外考试要求。",
        two="直讲/跳过被完整满足，未验证如实保留，未施加额外门禁。",
    ),
    RubricDimension(
        dimension_id="mastery_language_accuracy",
        name="不夸大掌握",
        description="是否区分未验证、提示后完成与独立完成，不宣称长期掌握或能力标签。",
        zero="把提问、看懂、提示后完成或单次完成宣称为已掌握。",
        one="措辞基本克制但出现模糊的掌握暗示。",
        two="如实使用未验证/提示后完成/本次独立完成的区分，无长期掌握宣称。",
    ),
    RubricDimension(
        dimension_id="diagnosis_feedback_to_teaching",
        name="诊断反馈教学",
        description=(
            "正式诊断是否真正改变后续教学（采用记录 + 可观察行为变化），"
            "且不采用待核验/已撤回判断。"
        ),
        zero="后续教学未使用已核验诊断，或使用了待核验/已撤回判断。",
        one="有采用记录但后续教学行为差异不明显。",
        two="存在 applied 采用记录，后续教学在例子/提示/前置安排上有可观察变化。",
    ),
)

_RUBRIC_BY_ID = {item.dimension_id: item for item in RUBRIC_DIMENSIONS}


@dataclass(frozen=True, slots=True)
class LearnerTurn:
    """一次固定学员发言及其对导师行为的预先期望。"""

    turn_id: str
    content: str
    expectation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LearningScenario:
    """一条固定、可重放的学习情境。"""

    scenario_id: str
    domain: str
    title: str
    misconception: str
    learner_turns: tuple[LearnerTurn, ...]
    rubric_focus: tuple[str, ...]
    independent_checks: tuple[str, ...]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "domain": self.domain,
            "title": self.title,
            "misconception": self.misconception,
            "learner_turns": [turn.to_dict() for turn in self.learner_turns],
            "rubric_focus": list(self.rubric_focus),
            "independent_checks": list(self.independent_checks),
            "notes": self.notes,
        }


_BY_ID = {item.dimension_id: item for item in RUBRIC_DIMENSIONS}


def _focus(*dimension_ids: str) -> tuple[str, ...]:
    for dimension_id in dimension_ids:
        if dimension_id not in _BY_ID:
            raise ValueError(f"未知量表维度：{dimension_id}")
    return dimension_ids


PROBABILITY_SCENARIO = LearningScenario(
    scenario_id="probability-mutually-exclusive",
    domain="probability",
    title="概率概念：互斥与独立的混淆",
    misconception="把「互斥」当成「独立」：认为两个事件不能同时发生就等于互不影响。",
    learner_turns=(
        LearnerTurn(
            turn_id="p1-start",
            content="互斥是不是独立？",
            expectation="纯文字启动局部辅导；先探查理解或给出最小必要引导，不要求资料。",
        ),
        LearnerTurn(
            turn_id="p2-hint",
            content="这题我有点拿不准，先给我一个小提示，我再自己想想。",
            expectation="给出恰当提示，不直接公布答案；待答问题保持未验证。",
        ),
        LearnerTurn(
            turn_id="p3-misconception",
            content=(
                "互斥就是独立吧？事件不能同时发生，说明它们互不影响，"
                "所以应该是独立的。"
            ),
            expectation=(
                "定位「不能同时发生⇒互不影响⇒独立」这一具体误解并澄清；"
                "作答发生在此前提示之后，证据须按提示后作答如实记录。"
            ),
        ),
        LearnerTurn(
            turn_id="p4-independent",
            content=(
                "换个例子我试试：掷一枚硬币 A=正面，B=反面。A、B 互斥，"
                "P(A∩B)=0；但 P(A)P(B)=1/4，所以它们不独立，"
                "而且反面出现的概率会因为正面出现而变成 1。"
            ),
            expectation="在无提示情况下独立给出正确应用；口误部分由导师纠正，不宣称已掌握。",
        ),
    ),
    rubric_focus=_focus(
        "misconception_localization",
        "explanation_correctness",
        "hint_appropriateness",
        "mastery_language_accuracy",
        "diagnosis_feedback_to_teaching",
    ),
    independent_checks=("probability_facts",),
    notes=(
        "误解（p3）出现在提示请求（p2）之后：该证据按 after_hint 记录，"
        "提示后作答不晋升长期掌握。p4 含一处概率口误，导师应纠正。"
    ),
)

LIMIT_SCENARIO = LearningScenario(
    scenario_id="limit-removable-discontinuity",
    domain="limit",
    title="函数/极限概念：0/0 与可去间断点",
    misconception="认为代入得到 0/0 就等于极限不存在，混淆「函数值无定义」与「极限不存在」。",
    learner_turns=(
        LearnerTurn(
            turn_id="l1-start",
            content=(
                "求 lim(x→1) (x²−1)/(x−1)，并说明 x=1 处函数的情况。"
                "我是普通学高数的学生。"
            ),
            expectation="开始局部辅导；探查学生自己的思路，不直接给完整答案。",
        ),
        LearnerTurn(
            turn_id="l2-misconception",
            content="把 x=1 代进去得到 0/0，除数为零没有意义，所以这个极限不存在吧。",
            expectation="定位「0/0⇒极限不存在」的具体误解并给出可检验的澄清。",
        ),
        LearnerTurn(
            turn_id="l3-stuck",
            content="我还是卡住。说实话我连 x²−1 怎么因式分解都忘了，你直接讲一下这个前置吧。",
            expectation="识别前置知识不足（因式分解），补充前置而不是重复追问原题。",
        ),
        LearnerTurn(
            turn_id="l4-skip",
            content="先别出题了，把这道题完整讲一遍吧，我想先看完整过程，不想答题。",
            expectation="尊重跳过验证：完整讲解；当前内容保持尚未验证，不判错、不施压。",
        ),
        LearnerTurn(
            turn_id="l5-independent",
            content=(
                "那我自己复述一遍：x²−1=(x−1)(x+1)，x≠1 时约掉 x−1，"
                "得到 x+1，所以极限是 2。函数在 x=1 处本身没有定义，"
                "但极限存在，是可去间断点。"
            ),
            expectation="接受正确复述并如实记录为本次独立完成；不把跳过轮记为缺口。",
        ),
    ),
    rubric_focus=_focus(
        "misconception_localization",
        "explanation_correctness",
        "hint_appropriateness",
        "adaptivity_to_performance",
        "respect_for_control",
        "mastery_language_accuracy",
    ),
    independent_checks=("limit_facts",),
    notes="连续卡住（l3）与明确跳过（l4）是必须演示的非顺利路径。",
)

PROGRAMMING_SCENARIO = LearningScenario(
    scenario_id="programming-mutable-default",
    domain="programming",
    title="编程理解：Python 可变默认参数",
    misconception="认为默认参数每次调用都会重新求值，两次调用各自得到新列表。",
    learner_turns=(
        LearnerTurn(
            turn_id="c1-start",
            content=(
                "这段 Python 代码两次 print 分别输出什么？为什么？\n"
                "def add(item, bucket=[]):\n"
                "    bucket.append(item)\n"
                "    return bucket\n"
                "print(add(1))\n"
                "print(add(2))"
            ),
            expectation="先探查预测或引导观察，不直接给结论。",
        ),
        LearnerTurn(
            turn_id="c2-misconception",
            content=(
                "第一次输出 [1]，第二次输出 [2]。每次调用都会重新创建默认的"
                "空列表，所以两次互不影响。"
            ),
            expectation="定位「默认参数每次调用重新求值」的具体误解；可提示用运行结果检验。",
        ),
        LearnerTurn(
            turn_id="c3-correct",
            content=(
                "跑了一下确实是 [1] 然后 [1, 2]。默认参数在函数定义时只求值"
                "一次，两次调用操作的是同一个列表对象。"
            ),
            expectation="确认正确理解并区分「定义时求值一次」与「每次调用重新求值」。",
        ),
        LearnerTurn(
            turn_id="c4-independent",
            content=(
                "所以修复方式是用 None 做哨兵：def add(item, bucket=None):"
                " 里面 if bucket is None: bucket = []。这样每次调用才会拿到"
                "新的列表。这个用法和直接写 bucket=[] 的区别就在这里。"
            ),
            expectation="独立迁移到修复方式；记录为独立完成，不夸大长期掌握。",
        ),
    ),
    rubric_focus=_focus(
        "misconception_localization",
        "explanation_correctness",
        "hint_appropriateness",
        "adaptivity_to_performance",
        "mastery_language_accuracy",
    ),
    independent_checks=("programming_facts",),
    notes="正确性由真实执行代码片段核对，不依赖模型自述。",
)

GOAL_RESUME_SCENARIO = LearningScenario(
    scenario_id="goal-resume-linear-algebra",
    domain="goal",
    title="系统性目标与跨会话续学",
    misconception="无（本旅程验证目标与进展的承接，不针对具体误解）。",
    learner_turns=(
        LearnerTurn(
            turn_id="g1-start",
            content="带我系统学线性代数，我想先把矩阵乘法彻底弄明白。",
            expectation="形成简短可调整路线并保存目标；单个问题之外建立长期目标。",
        ),
        LearnerTurn(
            turn_id="g2-pause",
            content="今天先到这里吧，帮我把进度存下来。",
            expectation="自然暂停并保存进展，不要求额外考试。",
        ),
    ),
    rubric_focus=_focus("respect_for_control"),
    independent_checks=(),
    notes=(
        "本旅程由运行器在第二个会话发送「继续学线性代数」并核对目标关联与"
        "进展恢复；g1/g2 是本会话固定输入。"
    ),
)

PUBLIC_VERIFICATION_SCENARIO = LearningScenario(
    scenario_id="public-verification-fresh-fact",
    domain="verification",
    title="按需公开查证与来源失败降级",
    misconception="无（本旅程验证查证触发与真实来源可得性）。",
    learner_turns=(
        LearnerTurn(
            turn_id="v1-start",
            content=(
                "帮我核实一下：2025 年诺贝尔物理学奖的获奖者是谁、"
                "获奖理由是什么？请给出来源。"
            ),
            expectation="识别时效事实与明确核实请求，走真实公开查证；引用绑定实际来源。",
        ),
    ),
    rubric_focus=_focus("explanation_correctness"),
    independent_checks=(),
    notes=(
        "真实外部可得性由本次实际 Tavily 调用与返回状态记录；失败时按合同"
        "如实降级并保留缺口，不伪造引用。"
    ),
)

SCENARIOS: tuple[LearningScenario, ...] = (
    PROBABILITY_SCENARIO,
    LIMIT_SCENARIO,
    PROGRAMMING_SCENARIO,
    GOAL_RESUME_SCENARIO,
    PUBLIC_VERIFICATION_SCENARIO,
)

SCENARIOS_BY_ID = {scenario.scenario_id: scenario for scenario in SCENARIOS}


def corpus_digest(scenarios: tuple[LearningScenario, ...] | None = None) -> str:
    """固定情境 + 量表全量序列化的 sha256，作为运行锁的一部分。"""

    selected = scenarios if scenarios is not None else SCENARIOS
    payload = {
        "corpus_version": LEARNING_JOURNEY_CORPUS_VERSION,
        "scenarios": [scenario.to_dict() for scenario in selected],
        "rubric": [dimension.to_dict() for dimension in RUBRIC_DIMENSIONS],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 独立核对基准：标准库可直接复算的事实，不依赖任何模型输出。
# ---------------------------------------------------------------------------

#: 编程情境中固定出现的代码片段（与学员输入一致）。
PROGRAMMING_SNIPPET = (
    "def add(item, bucket=[]):\n"
    "    bucket.append(item)\n"
    "    return bucket\n"
    "print(add(1))\n"
    "print(add(2))\n"
)

#: 修复版片段：验证哨兵写法的输出与原始写法不同。
PROGRAMMING_FIXED_SNIPPET = (
    "def add(item, bucket=None):\n"
    "    if bucket is None:\n"
    "        bucket = []\n"
    "    bucket.append(item)\n"
    "    return bucket\n"
    "print(add(1))\n"
    "print(add(2))\n"
)


def _run_python_snippet(source: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def probability_facts() -> dict[str, Any]:
    """概率事实：分数精确运算 + 互斥推出不独立的最小数值反例。"""

    p_a = Fraction(1, 2)
    p_b = Fraction(1, 3)
    independent_intersection = p_a * p_b
    mutually_exclusive_intersection = Fraction(0)
    return {
        "check": "probability_facts",
        "mutually_exclusive_intersection": str(mutually_exclusive_intersection),
        "independent_example": {
            "p_a": str(p_a),
            "p_b": str(p_b),
            "p_intersection": str(independent_intersection),
        },
        "mutually_exclusive_implies_dependent": (
            mutually_exclusive_intersection != independent_intersection
        ),
        "statements": [
            "互斥 ⇒ P(A∩B)=0；当 P(A)>0 且 P(B)>0 时 0 ≠ P(A)P(B)，故不独立。",
            "独立 ⇔ P(A∩B)=P(A)P(B)（P(B)>0 时等价于 P(A|B)=P(A)）。",
        ],
    }


def limit_facts() -> dict[str, Any]:
    """极限事实：数值逼近 + 因式分解恒等式抽样核对。"""

    def f(x: float) -> float:
        return (x * x - 1) / (x - 1)

    probes = [0.9999, 0.999999, 1.000001, 1.0001]
    values = [f(x) for x in probes]
    # 恒等式抽样用精确分数运算，避免浮点相等比较产生假阴性。
    algebraic = all(
        (x - 1) * (x + 1) == x * x - 1
        for x in (
            Fraction(1, 2),
            Fraction(17, 10),
            Fraction(-23, 10),
            Fraction(10),
        )
    )
    undefined_at_one = False
    try:
        f(1.0)
    except ZeroDivisionError:
        undefined_at_one = True
    return {
        "check": "limit_facts",
        "numeric_values": {
            str(x): value for x, value in zip(probes, values, strict=True)
        },
        "limit_approaches": 2.0,
        "closest_value": min(values, key=lambda value: abs(value - 2.0)),
        "factorization_identity_holds": algebraic,
        "function_undefined_at_x1": undefined_at_one,
        "divisible_after_cancelling": True,
        "statements": [
            "x≠1 时 (x²−1)/(x−1)=x+1，数值逼近极限为 2。",
            "x=1 处原式分母为 0，函数值无定义；极限存在且等于 2，是可去间断点。",
        ],
    }


def programming_facts() -> dict[str, Any]:
    """编程事实：真实执行原始与修复片段，核对输出。"""

    original = _run_python_snippet(PROGRAMMING_SNIPPET)
    fixed = _run_python_snippet(PROGRAMMING_FIXED_SNIPPET)
    return {
        "check": "programming_facts",
        "original": original,
        "fixed": fixed,
        "expected_original_output": True,
        "statements": [
            "原始片段输出 [1] 与 [1, 2]：默认参数在函数定义时求值一次，两次调用共享同一列表。",
            "哨兵写法（bucket=None 内建新列表）输出 [1] 与 [2]，每次调用得到新列表。",
            "默认参数求值发生在 def 执行时，不是每次调用时。",
        ],
    }


def independent_ground_truth() -> dict[str, Any]:
    """全部独立核对基准的汇总（写入证据文件，供人工核对引用）。"""

    return {
        "corpus_version": LEARNING_JOURNEY_CORPUS_VERSION,
        "corpus_sha256": corpus_digest(),
        "checks": {
            "probability_facts": probability_facts(),
            "limit_facts": limit_facts(),
            "programming_facts": programming_facts(),
        },
    }
