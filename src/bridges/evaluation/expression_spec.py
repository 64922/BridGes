"""工单 39：场景类型、正式路径清单与场景构造助手（无模型调用）。

本模块是语料层叶子模块：只含类型与静态清单，不依赖其他评测模块，
由 `expression_scenarios_*` 与 `expression_corpus` 共享。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExpressionCategory(StrEnum):
    """场景类别（覆盖矩阵维度）。"""

    VENTING = "venting"
    MIXED_TROUBLESHOOTING = "mixed_troubleshooting"
    QUOTED_EMOTION = "quoted_emotion"
    THANKS_CLOSING = "thanks_closing"
    CORRECTION = "correction"
    CONTINUATION = "continuation"
    LONG_TASK = "long_task"
    TOOL_FAILURE = "tool_failure"
    BOUNDARY = "boundary"
    PREFERENCE = "preference"
    FORMAL_PATH = "formal_path"


#: 场景矩阵必须覆盖的类别（工单 39 任务 5）。
REQUIRED_CATEGORIES: tuple[ExpressionCategory, ...] = tuple(ExpressionCategory)


class ToolSignal(StrEnum):
    """场景的确定性工具状态信号（用于硬门与策略编译）。"""

    NONE = "none"
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class FormalPath:
    """一条正式用户可见路径及其接线证据接缝。"""

    path_id: str
    title: str
    render_kind: str  # model_with_policy / deterministic_renderer / fixed_template / composite
    seam: str
    evidence: str


#: 正式用户可见路径清单（工单 39 要求“全部正式路径覆盖可查”）。
FORMAL_PATHS: tuple[FormalPath, ...] = (
    FormalPath(
        "chat.companion",
        "日常陪伴普通聊天",
        "model_with_policy",
        "chat/turn.py::_compile_writing_policy → assemble_payload",
        "tests/chat/test_improvement21_contextual_expression.py",
    ),
    FormalPath(
        "chat.study",
        "学习模式普通聊天（非课时任务）",
        "model_with_policy",
        "chat/turn.py::_compile_writing_policy（mode=study）",
        "tests/chat/test_v2_18_study_tutoring.py",
    ),
    FormalPath(
        "study.tutoring",
        "学习辅导生成",
        "model_with_policy",
        "study/tutoring.py::_tutoring_policy",
        "tests/chat/test_improvement22_unified_profile_expression.py",
    ),
    FormalPath(
        "study.review",
        "复盘出题与判定反馈",
        "model_with_policy",
        "study/review.py::_call（读取冻结策略快照）",
        "tests/chat/test_v2_19_study_review.py",
    ),
    FormalPath(
        "study.summary",
        "学习总结生成",
        "model_with_policy",
        "study/summary.py::_tutoring_policy",
        "tests/chat/test_improvement36_policy_acceptance.py",
    ),
    FormalPath(
        "study.scope",
        "学习范围/预习说明",
        "model_with_policy",
        "study/service.py::preview_policy_block",
        "tests/chat/test_improvement31_study_scope_preview.py",
    ),
    FormalPath(
        "paper.summary",
        "论文概述生成",
        "model_with_policy",
        "paper/presenting.py::SUMMARY_SYSTEM_PROMPT + expression.system_block",
        "tests/paper/test_paper_issue24.py::test_summary_generator_uses_policy_system_block_and_output_budget",
    ),
    FormalPath(
        "github.insights",
        "GitHub 借鉴角度生成",
        "model_with_policy",
        "github/presenting.py（追加 system_block 与 global_writing_policy）",
        "tests/github/test_github_acceptance_insights.py",
    ),
    FormalPath(
        "commute.result",
        "通勤路线结果",
        "deterministic_renderer",
        "commute/presenting.py::render_result_content",
        "src/bridges/commute/presenting.py",
    ),
    FormalPath(
        "resources.result",
        "学习资料路径结果",
        "deterministic_renderer",
        "resources/presenting.py",
        "src/bridges/resources/presenting.py",
    ),
    FormalPath(
        "tieba.research",
        "贴吧取证与研究说明",
        "deterministic_renderer",
        "tieba/presenting.py",
        "src/bridges/tieba/presenting.py",
    ),
    FormalPath(
        "career_plan.result",
        "职业规划结果",
        "deterministic_renderer",
        "career_plan/presenting.py",
        "src/bridges/career_plan/presenting.py",
    ),
    FormalPath(
        "composite",
        "复合计划统一综合结果",
        "model_with_policy",
        "chat/graph.py::_invoke_composite_plan",
        "tests/chat/test_issue37_composite_dispatch.py",
    ),
    FormalPath(
        "fixed_copy",
        "澄清/错误/空结果/进度/部分成功固定文案",
        "fixed_template",
        "state_copy/registry.py::render_state_copy",
        "tests/state_copy/test_issue23_state_copy_registry.py",
    ),
)

#: 路径 ID 集合（覆盖校验用）。
FORMAL_PATH_IDS = frozenset(path.path_id for path in FORMAL_PATHS)


@dataclass(frozen=True)
class ExpressionScenario:
    """一个原创多轮盲评场景及其确定性硬门期望。"""

    scenario_id: str
    title: str
    category: ExpressionCategory
    formal_path: str
    turns: tuple[str, ...]
    mode: str = "companion"
    profile_facts: tuple[str, ...] = ()
    tool_outcome: ToolSignal = ToolSignal.NONE
    protected_facts: tuple[str, ...] = ()
    boundary: str | None = None
    required_any: tuple[str, ...] = ()
    forbidden_any: tuple[str, ...] = ()
    detail_required: bool = False
    expects_continuation: bool = False
    real_runnable: bool = True
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def multi_turn(self) -> bool:
        return len(self.turns) > 1



def scenario(**kwargs: object) -> ExpressionScenario:
    """场景构造助手：数据模块以此声明场景（键值与字段一一对应）。"""

    return ExpressionScenario(**kwargs)  # type: ignore[arg-type]
