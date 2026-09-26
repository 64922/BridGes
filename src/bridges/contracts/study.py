"""学习小节的页级证据和阶段投影。"""

from typing import Literal

from pydantic import BaseModel, Field


class StudyFragment(BaseModel):
    fragment_id: str
    kind: Literal["text", "formula", "chart"]
    position: str
    text: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["photo", "user"] = "photo"


class StudyUnclear(BaseModel):
    position: str
    reason: str


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

    def public_view(self) -> "StudyState":
        """题库仅留在服务端；客户端只接收已展示题及其实际判定。"""
        result = self.model_copy(deep=True)
        if result.review:
            result.review.questions = [item for item in result.review.questions if item.asked]
        return result
