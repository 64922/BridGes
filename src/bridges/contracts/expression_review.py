"""版本化表达审稿报告（人味化改造 Issue 04）。

首稿生成后，独立表达审稿器对候选正文定位模板动作、PPT 式抽象包装、假
具体、假口语、强行场景、节奏单一和无必要升华，输出带稳定 code、场景
profile、严重度、文本起止位置、命中证据、解释和定向修改建议的
``ExpressionReviewReport``。审稿只检查作者新增正文，跳过精确引语、代码、
公式、URL、合法术语、用户指定措辞与原文已有表达；风格发现默认是软审稿，
只有来源保真问题（Issue 02 硬门）才阻止交付。

报告一旦生成即不可变：``review_version`` 与 ``contract_hash`` 共同构成
审计基线，Issue 05 的定向修订以报告发现为输入，不重复扫描全文。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.humanizer import SpanLocation

#: 表达审稿器 Schema 版本：发现规则或解释调整时递增。
EXPRESSION_REVIEW_VERSION = "expression-review-v1"


class ReviewSeverity(StrEnum):
    """审稿发现的严重度（全部属于软审稿，不阻止交付）。

    严重度只影响定向修订的优先级：warning 高优先、suggestion 中优先、
    info 低优先。来源保真失败由 Issue 02 硬门独立裁决，不进入本枚举。
    """

    WARNING = "warning"
    SUGGESTION = "suggestion"
    INFO = "info"


class ReviewCode(StrEnum):
    """稳定发现 code：审计与定向修订都以此定位，不改写文案避免漂移。

    覆盖 Issue 04 验收要求：助手身份残留、机械承接/收尾、体裁脚手架、
    抽象名词链、商业/PPT 隐喻、用术语解释术语、同义循环、空段、重复
    开场、句长/段长过齐、密集排比或设问、假具体、无权限第一人称、
    强行生活场景、每段金句与通用乐观升华。
    """

    ASSISTANT_IDENTITY_RESIDUE = "assistant_identity_residue"
    MECHANICAL_TRANSITION_CLOSING = "mechanical_transition_closing"
    GENRE_SCAFFOLDING = "genre_scaffolding"
    ABSTRACT_NOUN_CHAIN = "abstract_noun_chain"
    BUSINESS_PPT_METAPHOR = "business_ppt_metaphor"
    TERM_EXPLAINS_TERM = "term_explains_term"
    SYNONYM_LOOP = "synonym_loop"
    EMPTY_PARAGRAPH = "empty_paragraph"
    REPEATED_OPENING = "repeated_opening"
    UNIFORM_LENGTH = "uniform_length"
    DENSE_RHETORIC = "dense_rhetoric"
    FAKE_CONCRETENESS = "fake_concreteness"
    UNAUTHORIZED_FIRST_PERSON = "unauthorized_first_person"
    FORCED_LIFE_SCENE = "forced_life_scene"
    PER_PARAGRAPH_MOTIVATION = "per_paragraph_motivation"
    GENERIC_OPTIMISTIC_ENDING = "generic_optimistic_ending"


class ExpressionReviewFinding(BaseModel):
    """一条审稿发现：只记录位置、证据与建议，不记录整篇正文。"""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(description="稳定发现标识。")
    code: ReviewCode = Field(description="稳定发现 code。")
    severity: ReviewSeverity = Field(description="严重度（软审稿内分级）。")
    category: str = Field(description="发现类别的中文说明。")
    location: SpanLocation = Field(description="候选正文中的起止位置。")
    evidence: str = Field(description="命中的证据文本（限单条发现长度）。")
    explanation: str = Field(description="为什么认为这是模板动作或包装的中文解释。")
    suggestion: str = Field(description="定向修改建议（Issue 05 的输入）。")
    scene_profile: str = Field(description="发现所在场景 profile（体裁:强度）。")


class ExpressionReviewSummary(BaseModel):
    """审稿摘要：按 code 计数与总量（审计与指标用，不含正文）。"""

    finding_count: int = Field(description="发现总数。")
    by_code: dict[str, int] = Field(description="各 code 的发现数量。")
    warning_count: int = Field(description="warning 级发现数。")
    suggestion_count: int = Field(description="suggestion 级发现数。")
    info_count: int = Field(description="info 级发现数。")
    no_change_recommended: bool = Field(
        description="无高价值修改建议时置 True，系统不为满足规则强行改写。"
    )


class ExpressionReviewReport(BaseModel):
    """一次表达审稿的完整报告：软审稿输出，不阻止交付。

    ``contract_hash`` 关联本次任务不可变契约；``scene_profile`` 记录
    （体裁:强度）场景；``no_change_recommended`` 为 True 时正文已自然，
    系统不得为满足规则强行改写。
    """

    model_config = ConfigDict(extra="forbid")

    review_version: str = Field(default=EXPRESSION_REVIEW_VERSION, description="审稿器版本。")
    contract_hash: str = Field(description="本次任务契约的不可变哈希。")
    scene_profile: str = Field(description="场景 profile（体裁:强度）。")
    findings: list[ExpressionReviewFinding] = Field(
        default_factory=list, description="审稿发现清单。"
    )
    no_change_recommended: bool = Field(
        default=True, description="无高价值修改建议。"
    )
    summary: ExpressionReviewSummary = Field(description="脱敏审稿摘要。")


__all__ = [
    "EXPRESSION_REVIEW_VERSION",
    "ReviewSeverity",
    "ReviewCode",
    "ExpressionReviewFinding",
    "ExpressionReviewSummary",
    "ExpressionReviewReport",
]
