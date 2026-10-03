"""学习小节的页级证据和阶段投影。"""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    #: 稳定知识点 ID（改进工单 31）。由代码按知识点内容与支持片段确定性生成，
    #: 不以标题为唯一键：同名概念在不同片段上拥有不同 ID，评分依据不互相串用。
    #: 旧数据（v1 状态）读取时为空，由升级函数补上确定性遗留 ID。
    unit_id: str = ""
    title: str = Field(min_length=1)
    #: 知识点类别：概念/关系/应用/易错点。默认 concept 兼容旧数据。
    kind: Literal["concept", "relation", "application", "misconception"] = "concept"
    fragment_ids: list[str] = Field(min_length=1)
    core: bool = True


class StudyQuestion(BaseModel):
    question: str = Field(min_length=1)
    #: 引用的知识点稳定 ID（改进工单 31）：预习问题按 ID 绑定当前范围版本。
    unit_ids: list[str] = Field(default_factory=list)
    #: 知识点标题（展示与旧数据兼容）；不得作为唯一身份使用。
    unit_titles: list[str] = Field(default_factory=list)
    #: 生成该问题时的有效范围版本；旧数据为空。
    scope_version_id: str = ""

    @model_validator(mode="after")
    def _require_unit_reference(self) -> "StudyQuestion":
        if not self.unit_ids and not self.unit_titles:
            raise ValueError("预习问题必须引用至少一个知识点。")
        return self


class StudyCoverageEntry(BaseModel):
    """覆盖矩阵的一行：一个实质教学片段与知识点/排除理由的关系。"""

    fragment_id: str
    #: 引用该片段的知识点稳定 ID；为空时必须给出排除理由。
    unit_ids: list[str] = Field(default_factory=list)
    #: 有依据的排除理由（片段不是实质教学内容或不应进入知识范围）。
    exclusion_reason: str = ""

    @model_validator(mode="after")
    def _require_mapping_or_reason(self) -> "StudyCoverageEntry":
        if not self.unit_ids and not self.exclusion_reason.strip():
            raise ValueError("实质片段必须关联知识点或给出排除理由。")
        return self


class StudyContentCheck(BaseModel):
    """关键定义/公式/关系与书页原文的逐点核对结果（与结构门分开）。"""

    unit_id: str
    status: Literal["consistent", "conflict", "insufficient"]
    detail: str = ""
    fragment_ids: list[str] = Field(default_factory=list)


class StudyExclusionCheck(BaseModel):
    """被排除片段与排除理由的原文核对结果。"""

    fragment_id: str
    status: Literal["consistent", "conflict", "insufficient"]
    detail: str = ""


class StudyScope(BaseModel):
    """一个经核验的有效知识范围版本；旧版本保留供导出与恢复。"""

    scope_version_id: str
    revision: int = Field(default=1, ge=1)
    #: 映射协议版本（提示与规则）：变化时旧产物不被复用。
    protocol_version: str = "study-scope-v2"
    #: 覆盖页身份与内容指纹，用于判断新增页后的适用性（工单 35）。
    material_hash: str = ""
    page_object_ids: list[str] = Field(default_factory=list)
    #: 实质教学片段全集（映射输入），含被排除项。
    fragment_ids: list[str] = Field(default_factory=list)
    units: list[StudyUnit] = Field(default_factory=list)
    coverage: list[StudyCoverageEntry] = Field(default_factory=list)
    content_checks: list[StudyContentCheck] = Field(default_factory=list)
    exclusion_checks: list[StudyExclusionCheck] = Field(default_factory=list)
    verified: bool = False
    #: 旧合同下生成的历史范围（读取时由升级函数补齐 ID 并标记）。
    legacy: bool = False


class StudySource(BaseModel):
    source_id: str
    kind: Literal["page", "knowledge_base", "web"]
    label: str
    snippet: str
    object_id: str | None = None
    fragment_id: str | None = None
    url: str | None = None


class StudyEvidenceGap(BaseModel):
    """问题级证据评估的一条缺口（工单 32）。

    ``supplement`` 是所需补证类型：``knowledge_base`` 查本节之外的知识库、
    ``web`` 需公开来源（时效或明确核验要求也归入此项）、``page`` 关键书页
    不清需补拍补录、``none`` 无可用补证。``status`` 是补给后的最终处置：
    ``resolved`` 已由补充来源覆盖、``needs_page`` 等待书页、``unverified``
    因用户限制、来源失败或预算保持未核实。
    """

    point: str = Field(min_length=1)
    reason: str = ""
    supplement: Literal["knowledge_base", "web", "page", "none"] = "none"
    status: Literal["resolved", "unverified", "needs_page"] = "unverified"


class StudySupplementAttempt(BaseModel):
    """一层补证的真实执行结果（用于审计“为何没查/没查到”）。"""

    layer: Literal["knowledge_base", "web"]
    status: Literal[
        "used",
        "empty",
        "failed",
        "conflict",
        "skipped_disabled",
        "skipped_restricted",
        "not_needed",
        "budget_exhausted",
        "query_insufficient",
    ]
    detail: str = ""
    source_count: int = Field(default=0, ge=0)


class StudyEvidenceAssessment(BaseModel):
    """问题级证据充分性评估与逐层补证记录（工单 32）。

    先取本节相关片段与必要前文判断关键解释点是否已支持；不足时按用户
    设置检索知识库，复查剩余缺口，仍不足且允许联网时用最小公开术语补证。
    评估只读材料，不改写阶段、范围或考查范围。
    """

    protocol_version: str = "study-tutor-evidence-v1"
    #: 全部关键解释点是否都有已采纳来源支持（无缺口）。
    sufficient: bool = False
    key_points: list[str] = Field(default_factory=list)
    supported_points: list[str] = Field(default_factory=list)
    gaps: list[StudyEvidenceGap] = Field(default_factory=list)
    supplements: list[StudySupplementAttempt] = Field(default_factory=list)
    #: 保留的冲突来源说明（不合并为单一结论，也不作为支持证据）。
    conflicts: list[str] = Field(default_factory=list)


class StudyExchange(BaseModel):
    user_message_id: str
    assistant_message_id: str
    question: str
    answer: str
    sources: list[StudySource]
    gap: str = ""
    #: 本轮的证据充分性评估（工单 32）；旧状态（v2 无该字段）读取为 None。
    assessment: StudyEvidenceAssessment | None = None


class StudyPageUpdate(BaseModel):
    """追加页的候选范围，全部核对并合并成功后才替换已确认范围。"""

    pages: list[StudyPage]
    units: list[StudyUnit] = Field(default_factory=list)
    wait_reason: str | None = None
    #: 候选范围对应的知识范围版本与历史（工单 31）：追加页提交成功前，
    #: 已确认范围继续以 ``StudyState.scope`` 为准，旧版本可读。
    scope: StudyScope | None = None
    scope_history: list[StudyScope] = Field(default_factory=list)


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
    #: 当前经核验的有效知识范围版本（工单 31）。旧预习/范围版本保留在
    #: ``scope_history``，不静默宣称旧问题覆盖新增页（适用性由工单 35 更新）。
    scope: StudyScope | None = None
    scope_history: list[StudyScope] = Field(default_factory=list)
    #: 状态 JSON 的合同版本（1 = 标题作为知识点键的旧状态；2 = 稳定 ID）。
    state_version: int = 1

    def public_view(self) -> "StudyState":
        """题库仅留在服务端；客户端只接收已展示题及其实际判定。"""
        result = self.model_copy(deep=True)
        if result.review:
            result.review.questions = [item for item in result.review.questions if item.asked]
        return result


#: 当前学习状态 JSON 合同版本（读取旧版本时由升级函数补齐稳定 ID）。
STUDY_STATE_VERSION = 2


def _legacy_unit_id(index: int, title: str) -> str:
    """旧状态的确定性遗留知识点 ID：同一旧状态每次读取得到同一 ID。"""
    material = f"study-unit-v1\x1f{index}\x1f{title}"
    return "legacy-" + sha256(material.encode("utf-8")).hexdigest()[:16]


def upgrade_legacy_study_state(state: StudyState) -> StudyState:
    """把 v1 状态升级为稳定 ID 合同；旧预习/范围保持可读、可导出。

    只补齐身份与范围版本字段，不改写阶段、题目判定、总结或任何正文；
    升级后的状态在下一次保存时以 v2 落库。
    """
    if state.state_version >= STUDY_STATE_VERSION:
        return state
    units = [unit.model_copy(deep=True) for unit in state.units]
    title_to_units: dict[str, list[StudyUnit]] = {}
    seen_ids: set[str] = set()
    for index, unit in enumerate(units, 1):
        if not unit.unit_id:
            candidate = _legacy_unit_id(index, unit.title)
            while candidate in seen_ids:
                candidate = _legacy_unit_id(index + len(seen_ids), unit.title)
            unit.unit_id = candidate
        seen_ids.add(unit.unit_id)
        title_to_units.setdefault(unit.title, []).append(unit)
    questions = [
        question.model_copy(
            update={
                "unit_ids": (
                    list(question.unit_ids)
                    or [
                        title_to_units[title][0].unit_id
                        for title in question.unit_titles
                        if len(title_to_units.get(title, [])) == 1
                    ]
                ),
                "scope_version_id": question.scope_version_id or "legacy-scope-v1",
            }
        )
        for question in state.questions
    ]
    review = state.review
    if review is not None:
        review = review.model_copy(
            update={
                "questions": [
                    item.model_copy(
                        update={
                            "coverage_units": [
                                unit_id
                                for ref in item.coverage_units
                                for unit_id in (
                                    [unit.unit_id for unit in title_to_units.get(ref, [])
                                     if set(unit.fragment_ids) & set(item.fragment_ids)]
                                    or [ref]
                                )
                            ]
                        }
                    )
                    for item in review.questions
                ]
            }
        )
    scope = state.scope
    if scope is None and units:
        coverage = [
            StudyCoverageEntry(
                fragment_id=fragment_id,
                unit_ids=[
                    unit.unit_id for unit in units if fragment_id in unit.fragment_ids
                ],
            )
            for fragment_id in dict.fromkeys(
                fragment_id
                for unit in units
                for fragment_id in unit.fragment_ids
            )
        ]
        scope = StudyScope(
            scope_version_id="legacy-scope-v1",
            protocol_version="study-scope-v1",
            material_hash="",
            page_object_ids=[page.object_id for page in state.pages],
            fragment_ids=[entry.fragment_id for entry in coverage],
            units=units,
            coverage=coverage,
            verified=True,
            legacy=True,
        )
    return state.model_copy(
        update={
            "units": units,
            "questions": questions,
            "review": review,
            "scope": scope,
            "state_version": STUDY_STATE_VERSION,
        }
    )
