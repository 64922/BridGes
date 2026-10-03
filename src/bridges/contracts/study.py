"""学习小节的页级证据和阶段投影。"""

from typing import Literal

from pydantic import BaseModel, Field


class StudyFragment(BaseModel):
    fragment_id: str
    kind: Literal["text", "formula", "chart"]
    position: str
    #: 书页原文（照片识别所见内容或用户补录文字）。模型推断不写在这里。
    text: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["photo", "user"] = "photo"
    #: 产生该片段的识别路径：``vision``（视觉结构化，当前双路径的片段来源）、
    #: ``ocr``（文字识别；OCR 优化经代表样本实测后才启用）、``user``（补录）。
    recognition_path: Literal["ocr", "vision", "user"] = "vision"
    #: 模型对图表/公式的推断或解释（如趋势、含义）。与 ``text`` 分字段，
    #: 图表推断不得当作图表原文，也不作为书页依据引用。
    interpretation: str = ""


class StudyUnclear(BaseModel):
    position: str
    reason: str
    #: 疑点类别：``unclear`` 一般不清；``critical_symbol`` 负号/上下标/
    #: 分子分母/单位/核心定义等关键符号疑点；``dual_path_mismatch`` OCR 与
    #: 视觉两路结果不一致。
    kind: Literal["unclear", "critical_symbol", "dual_path_mismatch"] = "unclear"
    #: 是否为不能靠置信阈值放行的关键疑点：为真时保持材料待补充，
    #: 依赖它的出题被阻塞。
    critical: bool = False


class StudyPage(BaseModel):
    object_id: str
    ordinal: int
    content_hash: str
    model_id: str
    page_number: int | None = Field(default=None, ge=1)
    same_section: bool = True
    replaced_object_ids: list[str] = Field(default_factory=list)
    fragments: list[StudyFragment]
    unclear: list[StudyUnclear] = Field(default_factory=list)
    #: 本页实际启用的识别路径（当前未验证 OCR 优化时双路径全开，保留安全
    #: 双路径；代表性样本实测后才允许按风险收窄）。
    recognition_paths: list[str] = Field(default_factory=lambda: ["ocr", "vision"])


class StudyUnit(BaseModel):
    title: str = Field(min_length=1)
    fragment_ids: list[str] = Field(min_length=1)
    core: bool = True


class StudyQuestion(BaseModel):
    question: str = Field(min_length=1)
    unit_titles: list[str] = Field(min_length=1)


class StudySource(BaseModel):
    source_id: str
    kind: Literal["page", "knowledge_base", "web"]
    label: str
    snippet: str
    object_id: str | None = None
    fragment_id: str | None = None
    url: str | None = None


class StudyExchange(BaseModel):
    user_message_id: str
    assistant_message_id: str
    question: str
    answer: str
    sources: list[StudySource]
    gap: str = ""


class StudyPageUpdate(BaseModel):
    """追加页的候选范围，全部核对并合并成功后才替换已确认范围。"""

    pages: list[StudyPage]
    units: list[StudyUnit] = Field(default_factory=list)
    wait_reason: str | None = None


class StudyReviewQuestion(BaseModel):
    question_id: str
    question: str
    coverage_units: list[str]
    fragment_ids: list[str]
    asked: bool = False
    answer: str | None = None
    judgement: Literal["correct", "incomplete", "incorrect"] | None = None
    canonical_answer: str | None = None
    explanation: str | None = None
    user_message_id: str | None = None


class StudyReview(BaseModel):
    questions: list[StudyReviewQuestion] = Field(default_factory=list)
    active_question_id: str | None = None
    needs_replan: bool = False
    complete: bool = False


class StudySummaryPoint(BaseModel):
    """总结的一条结论：掌握与漏洞都指向实际题目或本节书页片段。"""

    kind: Literal["learned", "mastered", "gap"]
    text: str = Field(min_length=1)
    question_ids: list[str] = Field(default_factory=list)
    fragment_ids: list[str] = Field(default_factory=list)


class StudySummary(BaseModel):
    points: list[StudySummaryPoint] = Field(min_length=1)


class StudyState(BaseModel):
    subsection_id: str
    stage: Literal[
        "awaiting_pages", "recognizing", "preview", "tutoring", "review", "summary"
    ] = "awaiting_pages"
    wait_reason: str | None = None
    pages: list[StudyPage] = Field(default_factory=list)
    units: list[StudyUnit] = Field(default_factory=list)
    questions: list[StudyQuestion] = Field(default_factory=list)
    tutoring: list[StudyExchange] = Field(default_factory=list)
    page_update: StudyPageUpdate | None = None
    review: StudyReview | None = None
    summary: StudySummary | None = None
    #: 因运行预算/批量限制尚未识别的书页附件（按上传顺序）。非空时保持
    #: 识别阶段，不宣布整节已读；恢复只处理这些页，已识别页按内容哈希复用。
    pending_object_ids: list[str] = Field(default_factory=list)

    def public_view(self) -> "StudyState":
        """题库仅留在服务端；客户端只接收已展示题及其实际判定。"""
        result = self.model_copy(deep=True)
        if result.review:
            result.review.questions = [item for item in result.review.questions if item.asked]
        return result
