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
    replaced_object_ids: list[str] = Field(default_factory=list)
    fragments: list[StudyFragment]
    unclear: list[StudyUnclear] = Field(default_factory=list)


class StudyUnit(BaseModel):
    title: str
    fragment_ids: list[str] = Field(min_length=1)
    core: bool = True


class StudyQuestion(BaseModel):
    question: str
    unit_titles: list[str] = Field(min_length=1)


class StudyState(BaseModel):
    subsection_id: str
    stage: Literal["awaiting_pages", "recognizing", "preview", "tutoring"] = "awaiting_pages"
    wait_reason: str | None = None
    pages: list[StudyPage] = Field(default_factory=list)
    units: list[StudyUnit] = Field(default_factory=list)
    questions: list[StudyQuestion] = Field(default_factory=list)
