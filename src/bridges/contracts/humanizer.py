"""bridges-humanizer SKILL 契约（Issue 28）。

这些模型定义原创净室人味化 SKILL 的公开表面：任务契约、文本级事实锁、
输出合同与结果投影。事实锁在文本层面直接保护数值、单位、对象关系、
限定条件、公式、引用与结论强度；输出合同固定包含最终文本、逐项修改
细节、每项理由、事实核查结果与尚未解决的问题，缺一不标记完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bridges.contracts.expression import Genre
from bridges.contracts.expression_task import ExpressionTaskContract


@dataclass(frozen=True)
class HumanizerProfileSlice:
    """Issue 04：人味化轮次的最小画像切片输入（聊天层编译后传入）。

    只承载服务端实际需要的三件事：是否实际使用（``used``）、条数
    （``item_count``，审计用）与编译好的上下文文本（``context``，按
    「风格与背景偏好」用途注入 prompt）。画像原文绝不进入运行锁、日志、
    SSE 事件或人味化语料产物；披露只含条数与类别。
    """

    used: bool
    item_count: int = 0
    context: str | None = None


class HumanizerPath(StrEnum):
    """人味化任务的两条端到端路径。"""

    REWRITE = "rewrite"
    GENERATE = "generate"


class HumanizerRouteSource(StrEnum):
    """人味化任务的入口来源。"""

    EXPLICIT_SKILL = "explicit_skill"
    NATURAL_LANGUAGE = "natural_language"


class HumanizerRouteDecision(BaseModel):
    """自然语言路由的不可变快照。"""

    source: HumanizerRouteSource = Field(description="任务进入人味化能力的来源。")
    version: str = Field(description="路由规则版本。")
    reason: str = Field(description="命中的中文意图说明。")
    external_evidence_requested: bool = Field(
        default=False,
        description="用户是否明确要求补充或核验外部事实；默认不联网。",
    )


class FactLockKind(StrEnum):
    """文本级事实锁类别（对应 Issue 28 受保护的信息集合）。"""

    NUMBER = "number"
    UNIT = "unit"
    OBJECT_RELATION = "object_relation"
    QUALIFIER = "qualifier"
    FORMULA = "formula"
    CITATION = "citation"
    CONCLUSION_STRENGTH = "conclusion_strength"


class FactLockStatus(StrEnum):
    """一次前后比较中单个事实锁的判定结果。"""

    PRESERVED = "preserved"
    CHANGED = "changed"
    REMOVED = "removed"
    ADDED = "added"


class FactLockSeverity(StrEnum):
    """事实锁冲突严重度：阻断必须停止，需人工则明确标注。"""

    BLOCKING = "blocking"
    NEEDS_HUMAN = "needs_human"
    INFO = "info"


# ---------------------------------------------------------------------------
# 来源账本与保真硬门（Issue 02）
# ---------------------------------------------------------------------------

SOURCE_LEDGER_VERSION = "2"
"""来源账本版本：1 为旧事实锁（无账本），2 为版本化来源账本。"""

FIDELITY_CHECKER_VERSION = "2.0"
"""保真检查器版本：随检查规则变更递增。"""


class SourceType(StrEnum):
    """来源类型：区分用户原文、补充/授权材料、外部来源、常识与显式假设。"""

    USER_ORIGINAL = "user_original"
    USER_SUPPLEMENT = "user_supplement"
    ACCOUNT_SCOPED = "account_scoped"
    EXTERNAL_ALLOWED = "external_allowed"
    COMMON_KNOWLEDGE = "common_knowledge"
    EXPLICIT_ASSUMPTION = "explicit_assumption"


class SourceUsage(StrEnum):
    """来源条目的允许用途；纯改写默认只消费用户原文。"""

    REWRITE = "rewrite"
    FACT = "fact"
    QUOTE = "quote"
    EXPERIENCE = "experience"


class ProtectedSpanKind(StrEnum):
    """保护区域类别：后续表达审稿只检查作者新增正文，不改写这些区域。"""

    QUOTE = "quote"
    CODE = "code"
    FORMULA = "formula"
    URL = "url"
    CITATION = "citation"
    USER_PHRASE = "user_phrase"


class PersonalExperienceMode(StrEnum):
    """第一人称建模：当下判断与亲历分开，亲历必须可绑定来源。"""

    NONE = "none"
    PRESENT_JUDGMENT = "present_judgment"
    PAST_EXPERIENCE = "past_experience"


class SpanLocation(BaseModel):
    """规范化文本中的起止偏移（审计用位置，不含正文）。"""

    start: int = Field(description="起始偏移。")
    end: int = Field(description="结束偏移。")


class ProtectedSpan(BaseModel):
    """一条受保护区域：只记录种类、内容哈希与位置，不记录正文。"""

    span_id: str = Field(description="稳定保护区域标识。")
    kind: ProtectedSpanKind = Field(description="保护区域类别。")
    text_hash: str = Field(description="区域内容哈希（sha256）。")
    location: SpanLocation = Field(description="规范化文本中的位置。")


class SourceEntry(BaseModel):
    """账本中一条来源：内容哈希、类型、允许用途与提取的保护项。"""

    entry_id: str = Field(description="稳定来源条目标识。")
    source_type: SourceType = Field(description="来源类型。")
    content_hash: str = Field(description="条目内容哈希（sha256，不含正文存储）。")
    usage: list[SourceUsage] = Field(description="允许用途；纯改写默认只含 REWRITE。")
    proper_nouns: list[str] = Field(default_factory=list, description="专名规范化键。")
    numbers: list[str] = Field(default_factory=list, description="数字/单位规范化键。")
    dates: list[str] = Field(default_factory=list, description="日期规范化键。")
    formulas: list[str] = Field(default_factory=list, description="公式代码规范化键。")
    urls: list[str] = Field(default_factory=list, description="URL 规范化键。")
    quotes: list[str] = Field(default_factory=list, description="精确引语规范化键。")
    citations: list[str] = Field(default_factory=list, description="引用规范化键。")
    directions: list[str] = Field(
        default_factory=list, description="否定与因果方向描述。"
    )
    qualifiers: list[str] = Field(default_factory=list, description="限定词。")
    conclusion_strength: str | None = Field(
        default=None, description="结论强度分级（weak/medium/strong）。"
    )
    personal_experience: PersonalExperienceMode = Field(
        default=PersonalExperienceMode.NONE, description="第一人称建模。"
    )
    allow_first_person: bool = Field(
        default=False, description="是否允许绑定第一人称亲历（原文已有或用户授权）。"
    )
    protected_spans: list[ProtectedSpan] = Field(
        default_factory=list, description="受保护区域。"
    )
    source_label: str = Field(default="", description="来源显示名（文件名/说明）。")
    codes: list[str] = Field(default_factory=list, description="代码块规范化键。")
    experiences: list[str] = Field(
        default_factory=list, description="第一人称亲历规范化键（原文已有）。"
    )
    text_key: str = Field(
        default="", description="条目全文规范化键（内部比较用，审计不记录）。"
    )


class LedgerCompileSummary(BaseModel):
    """账本编译摘要：脱敏计数（供审计与指标，不含正文）。"""

    entry_count: int = Field(description="来源条目数。")
    protected_spans_by_kind: dict[str, int] = Field(
        description="各保护区域类别的数量。"
    )
    proper_noun_count: int = Field(description="专名数。")
    number_count: int = Field(description="数字/单位数。")
    date_count: int = Field(description="日期数。")
    quote_count: int = Field(description="精确引语数。")
    experience_entries: int = Field(description="含第一人称权限的条目数。")


class SourceLedger(BaseModel):
    """版本化来源账本：调用方只提交来源与权限，不各自维护事实词表。"""

    ledger_version: str = Field(default=SOURCE_LEDGER_VERSION, description="账本版本。")
    ledger_hash: str = Field(description="账本哈希（版本与全部条目规范化摘要）。")
    entries: list[SourceEntry] = Field(default_factory=list, description="来源条目。")
    compiled_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="编译时间。"
    )
    compile_summary: LedgerCompileSummary | None = Field(
        default=None, description="编译摘要（脱敏计数）。"
    )


class FidelityFailureCode(StrEnum):
    """保真失败码（审计日志只记录失败码，不记录正文）。"""

    NUMBER_CHANGED = "number_changed"
    DATE_CHANGED = "date_changed"
    PROPER_NOUN_CHANGED = "proper_noun_changed"
    QUOTE_CHANGED = "quote_changed"
    CODE_CHANGED = "code_changed"
    FORMULA_CHANGED = "formula_changed"
    URL_CHANGED = "url_changed"
    CITATION_CHANGED = "citation_changed"
    DIRECTION_FLIPPED = "direction_flipped"
    STRENGTH_UPGRADED = "strength_upgraded"
    STRENGTH_DOWNGRADED = "strength_downgraded"
    QUALIFIER_REMOVED = "qualifier_removed"
    UNATTRIBUTED_CLAIM = "unattributed_claim"
    FIRST_PERSON_UNBOUND = "first_person_unbound"
    ASSUMPTION_NOT_ALLOWED = "assumption_not_allowed"
    ASSUMPTION_CARRIES_FACT = "assumption_carries_fact"
    UNDETERMINED = "undetermined"


class FidelitySeverity(StrEnum):
    """保真失败严重度：关键失败硬门阻止，无法判定项需用户确认。"""

    BLOCKING = "blocking"
    NEEDS_USER_CONFIRMATION = "needs_user_confirmation"


class FidelityFailure(BaseModel):
    """一条保真失败：只含位置与失败码描述，正文片段仅进结果投影。"""

    failure_id: str = Field(description="稳定失败标识。")
    code: FidelityFailureCode = Field(description="失败码（审计用）。")
    severity: FidelitySeverity = Field(description="严重度。")
    category: str = Field(description="中文类别说明。")
    item_type: str = Field(description="项类型（number/date/proper_noun/…）。")
    location: SpanLocation | None = Field(default=None, description="失败位置。")
    note: str = Field(default="", description="中文说明（用户可见的结果投影）。")


class FidelitySummary(BaseModel):
    """保真检查摘要：脱敏计数（指标与审计用）。"""

    protected_span_count: int = Field(description="账本保护区域总数。")
    preserved_count: int = Field(description="保持检查通过的保护项数。")
    new_claim_count: int = Field(description="候选新增 claim 数。")
    attributed_claim_count: int = Field(description="有来源绑定的新增 claim 数。")
    unattributed_claim_count: int = Field(description="无来源新增 claim 数。")
    first_person_interception_count: int = Field(description="第一人称经历拦截数。")
    assumption_count: int = Field(description="允许的标注假设数。")
    blocking_count: int = Field(description="硬失败数。")
    needs_confirmation_count: int = Field(description="需用户确认项数。")


class FidelityCheckResult(BaseModel):
    """一次保真检查的完整结果：缺任一字段不标记成功（失败关闭）。"""

    check_id: str = Field(description="稳定检查标识。")
    ledger_version: str = Field(description="账本版本（须受支持才可成功）。")
    checker_version: str = Field(description="检查器版本。")
    ledger_hash: str = Field(description="本次检查使用的账本哈希。")
    passed: bool = Field(description="无关键失败即为通过。")
    blocking_failures: list[FidelityFailure] = Field(
        default_factory=list, description="硬失败清单。"
    )
    needs_confirmation: list[FidelityFailure] = Field(
        default_factory=list, description="需用户确认项清单。"
    )
    summary: FidelitySummary = Field(description="脱敏检查摘要。")


class HumanizerProcessState(StrEnum):
    """人味化过程卡的可见状态（中文五态 + 终态 done）。

    done 只出现在结果投影的终态快照上（过程卡已由结果卡接管），
    SSE 过程事件只下发前五种状态。
    """

    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"
    DONE = "done"


class HumanizerResultStatus(StrEnum):
    """人味化任务的终态；needs_human 表示事实锁冲突或待核事项。"""

    DONE = "done"
    NEEDS_HUMAN = "needs_human"
    ERROR = "error"


class HumanizerQualityStatus(StrEnum):
    """软门质量状态：风格类指标（句式/节奏/体裁/重复/口吻）的交付口径。

    OK 表示软门全部通过；WARN 表示存在未完全满足项，正文仍照常交付并
    附具体警告（硬门才有权阻止交付最终稿）。
    """

    OK = "ok"
    WARN = "warn"


class HumanizerTaskContract(BaseModel):
    """一次人味化任务对目标、受众、体裁、渠道与硬约束的共同约定。

    与 CONTEXT.md「表达任务契约」一致：拒绝“写得自然一点”式模糊提示。
    改写路径必带 source_text、知识库引用或历史兼容附件；生成路径必带 topic。
    """

    model_config = ConfigDict(extra="forbid")

    path: HumanizerPath = Field(description="改写或生成路径。")
    genre: Genre | None = Field(
        default=None,
        description=(
            "四体裁之一（科普文案/课程讲稿/科研汇报/论文写作）；"
            "None 表示未识别体裁，使用通用文章 profile，不强制科普必现模板。"
        ),
    )
    topic: str | None = Field(
        default=None, description="生成路径的主题；改写路径可为空。"
    )
    audience: str | None = Field(
        default=None, description="目标受众（如：大一新生、课题组同行）。"
    )
    channel: str | None = Field(
        default=None, description="发布或展示渠道（如：公众号、课堂、组会汇报）。"
    )
    length_target: str | None = Field(
        default=None, description="长度或时长目标（如：800 字、10 分钟）。"
    )
    hard_constraints: list[str] = Field(
        default_factory=list, description="用户声明的硬约束（不得删减/必须保留等）。"
    )
    source_text: str | None = Field(
        default=None, description="改写路径的粘贴原文（可与知识库材料互补）。"
    )
    knowledge_base_object_ids: list[str] = Field(
        default_factory=list,
        description="改写路径引用的当前账户全局知识库材料对象标识。",
    )
    source_label: str | None = Field(
        default=None, description="来源显示名（文件名或用户粘贴说明）。"
    )
    knowledge_base_reference: str | None = Field(
        default=None,
        description="用户明确引用的当前账户知识库文档名；为空时不得泛化检索。",
    )
    allow_assumptions: bool = Field(
        default=False,
        description="契约是否允许显式假设（以「比如/假设/设想」标注且不冒充"
        "亲历、不承载高风险事实）；默认关闭，不自动扩大事实范围。",
    )
    allow_first_person: bool = Field(
        default=False,
        description="用户是否明确授权代写作者口吻（第一人称亲历绑定）；"
        "原文已有的亲历不受此字段限制。",
    )
    explicit_common_knowledge: list[str] = Field(
        default_factory=list,
        description="用户显式列为普通常识的内容（可作来源放行）；"
        "为空时不静默豁免任何新增内容。",
    )


class HumanizerSkillInput(BaseModel):
    """发送消息时携带的 SKILL 载荷（skill_id 须为内置注册标识）。"""

    skill_id: str = Field(description="稳定 SKILL 注册标识，如 bridges-humanizer。")
    contract: HumanizerTaskContract = Field(description="本次人味化任务契约。")
    version: str | None = Field(
        default=None, description="请求声明版本；空则使用注册的默认版本。"
    )
    route: HumanizerRouteDecision | None = Field(
        default=None, description="进入能力前保存的路由决策快照。"
    )
    expression_contract: ExpressionTaskContract | None = Field(
        default=None,
        description=(
            "版本化表达任务契约（人味化改造 Issue 03）；不可变快照，"
            "同一任务重试必须复用，未知版本执行前拒绝。"
        ),
    )


class FactLockEntry(BaseModel):
    """前后文本比较中的一条事实锁记录。"""

    entry_id: str = Field(description="稳定条目标识。")
    kind: FactLockKind = Field(description="事实锁类别。")
    surface_before: str | None = Field(
        default=None, description="原文中的表面形式；新增条目为 None。"
    )
    surface_after: str | None = Field(
        default=None, description="新文中的表面形式；删除条目为 None。"
    )
    canonical: str = Field(description="规范化可比键（全半角/单位统一后）。")
    status: FactLockStatus = Field(description="比较判定。")
    severity: FactLockSeverity = Field(description="冲突严重度。")
    note: str = Field(default="", description="中文说明（为何判定、需人工处理什么）。")


class FactLockCheckResult(BaseModel):
    """一次事实锁前后比较的整体结果。"""

    check_id: str = Field(description="稳定检查标识。")
    source_text: str = Field(description="比较的原文（改写路径为源文，生成路径为约束集合）。")
    entries: list[FactLockEntry] = Field(
        default_factory=list, description="全部事实锁条目。"
    )
    blocking_conflicts: list[str] = Field(
        default_factory=list, description="阻断性冲突的中文描述（必须停止或人工确认）。"
    )
    needs_human: list[str] = Field(
        default_factory=list, description="需人工确认事项的中文描述。"
    )
    passed: bool = Field(
        description="无阻断冲突即为通过；需人工事项不阻断但必须披露。"
    )


class HumanizerEditKind(StrEnum):
    """逐项修改细节的类别。"""

    REWRITE = "rewrite"
    RESTRUCTURE = "restructure"
    WORD_CHOICE = "word_choice"
    AUDIENCE_ADAPT = "audience_adapt"
    NO_CHANGE = "no_change"


class HumanizerEdit(BaseModel):
    """一次修改细节：原文片段、新文片段、类别与理由。"""

    edit_id: str = Field(description="稳定修改条目标识。")
    kind: HumanizerEditKind = Field(description="修改类别。")
    original: str = Field(description="原文片段（生成路径为对应的表达决策基线）。")
    revised: str = Field(description="新文片段。")
    reason: str = Field(description="每项理由：对应哪条体裁规则或表达目标。")
    genre_rule: str | None = Field(
        default=None, description="触发的体裁规则标识（可溯源）。"
    )


class HumanizerFactCheckItem(BaseModel):
    """事实核查结果中的一项。"""

    item: str = Field(description="核查对象（数值/单位/公式/引用/结论强度/来源）。")
    result: str = Field(description="核查结果：已核实 / 需人工确认 / 存在虚构风险。")
    evidence: str = Field(
        description="依据（本地检索/联网来源/原文事实锁/无来源需人工核实）。"
    )


class HumanizerOutputContract(BaseModel):
    """人味化输出合同：缺一不标记完成。

    五项全部齐全（final_text 非空、edits 每项带理由、fact_check 非空、
    open_questions 字段存在且可空列表已说明）才视为完成；软门状态
    （quality_status）与来源标识随输出持久化。历史附件字段保留用于只读兼容，
    新任务使用 source_knowledge_base_object_ids。
    """

    final_text: str = Field(description="最终文本。")
    edits: list[HumanizerEdit] = Field(
        default_factory=list, description="逐项修改细节（每项必带理由）。"
    )
    fact_check: list[HumanizerFactCheckItem] = Field(
        default_factory=list, description="事实核查结果。"
    )
    open_questions: list[str] = Field(
        default_factory=list, description="尚未解决的问题（可空但必须存在）。"
    )
    quality_status: HumanizerQualityStatus = Field(
        default=HumanizerQualityStatus.OK,
        description="软门质量状态：风格指标未完全通过时为 warn（正文照常交付）。",
    )
    source_attachment_ids: list[str] = Field(
        default_factory=list,
        description="历史改写任务实际解析成功的聊天附件（只读兼容）。",
    )
    source_knowledge_base_object_ids: list[str] = Field(
        default_factory=list,
        description="改写路径实际解析成功的全局知识库材料对象标识。",
    )

    def completeness_gaps(self) -> list[str]:
        """返回缺失项的中文描述；空列表表示合同完整。"""
        gaps: list[str] = []
        if not self.final_text.strip():
            gaps.append("缺少最终文本")
        if not self.edits:
            gaps.append("缺少逐项修改细节")
        elif any(not edit.reason.strip() for edit in self.edits):
            gaps.append("存在未附理由的修改项")
        if not self.fact_check:
            gaps.append("缺少事实核查结果")
        if self.open_questions is None:  # 字段恒存在，防御性保留
            gaps.append("缺少尚未解决的问题")
        return gaps


class HumanizerReference(BaseModel):
    """人味化结果保持的引用条目（经本地/联网证据合同呈现）。"""

    reference_id: str = Field(description="稳定引用标识。")
    label: str = Field(description="中文显示名（文件/文章/来源标题）。")
    source_type: str = Field(description="来源类型：attachment/retrieval/web/arxiv/原文。")
    detail: str = Field(default="", description="定位细节（页码/章节/网址/访问时间）。")
    citation_surface: str | None = Field(
        default=None, description="正文中的引用表面形式（如 (Smith, 2020)）。"
    )
    preserved: bool = Field(
        default=True, description="是否在改写前后保持（未被删改）。"
    )


#: 持久化运行检查点键（Issue 05）：助手消息 skill 列中的中间检查点 JSON。
#: 检查点形如 ``{"_humanizer_checkpoint": {"writing_call_count": 1}}``，只含
#: 脱敏写作调用计数，不是完整结果投影；服务重启后重试据此沿用调用额度。
#: （重试新尝试的初始检查点还携带 ``recovered_text`` 用于跳过首稿调用，
#: 运行中的检查点只更新计数——正文已在消息 content 列持久化。）
HUMANIZER_CHECKPOINT_KEY = "_humanizer_checkpoint"


class HumanizerRevisionAudit(BaseModel):
    """一次定向修订的运行审计（Issue 05；脱敏，不含正文）。

    ``triggered`` 为 False 时修订未执行，问题计数仍反映首稿检查摘要；
    ``skipped_reason`` 记录未执行原因（预算不足/用户停止/开关关闭/模型
    失败），此时首稿的真实硬门/软审稿状态照常交付。
    """

    model_config = ConfigDict(extra="forbid")

    revision_version: str = Field(description="修订策略版本（revision-policy-v1）。")
    triggered: bool = Field(description="是否触发修订调用。")
    trigger_code: str | None = Field(
        default=None, description="主要触发 code（保真/契约/审稿 code）。"
    )
    problem_count: int = Field(default=0, description="待修问题数（触发时 >0）。")
    draft_fidelity_blocking: int = Field(
        default=0, description="首稿关键保真失败数。"
    )
    draft_contract_omissions: int = Field(default=0, description="首稿契约遗漏数。")
    draft_warning_count: int = Field(default=0, description="首稿高置信风格发现数。")
    revised_fidelity_blocking: int | None = Field(
        default=None, description="修订后关键保真失败数（未修订为 None）。"
    )
    revised_contract_omissions: int | None = Field(
        default=None, description="修订后契约遗漏数（未修订为 None）。"
    )
    revised_warning_count: int | None = Field(
        default=None, description="修订后高置信风格发现数（未修订为 None）。"
    )
    resolved_problem_count: int | None = Field(
        default=None, description="修订解决的问题数（触发问题数减修订后仍存在数）。"
    )
    extra_tokens: int | None = Field(
        default=None, description="修订调用 token 数（未修订为 None）。"
    )
    extra_latency_ms: int | None = Field(
        default=None, description="修订调用延迟毫秒（未修订为 None）。"
    )
    final_state: str = Field(
        default="pending",
        description=(
            "终态：pending/deliver_revised/deliver_draft/stop_delivery/"
            "excised_delivery（Issue 02：机械违规确定性剔除后交付成品）。"
        ),
    )
    skipped_reason: str | None = Field(
        default=None,
        description="未执行修订的原因（capability_disabled/call_limit_reached/"
        "user_stopped/budget_insufficient/model_error:<code>/recheck_failed）。",
    )
    #: Issue 02：确定性句子级剔除的脱敏审计（不含正文）。
    excision_attempted: bool = Field(
        default=False, description="是否尝试过确定性句子级剔除。"
    )
    excision_removed_count: int | None = Field(
        default=None, description="剔除交付移除的违规条目数（未剔除为 None）。"
    )
    excision_removed_sentences: int | None = Field(
        default=None, description="剔除交付移除的句子数（未剔除为 None）。"
    )


# ---------------------------------------------------------------------------
# 版本化文章结果投影（Issue 08：正文优先交付界面）
# ---------------------------------------------------------------------------

ARTICLE_PROJECTION_VERSION = "1"
"""文章结果投影版本：前端据 ``HumanizerResultProjection.article`` 是否存在
决定渲染正文优先的新界面或 legacy 旧界面。"""

ARTICLE_AUDIT_VERSION = "1"
"""投影内全部稳定 code（失败码/风险码/触发类别）的版本标识；任一 code
语义变更时递增，保证审计计数跨版本可比。"""


class ArticleDeliveryStatus(StrEnum):
    """正文交付状态：成功（含软警告）为 delivered；修订未完成仅交付首稿为
    partial（Issue 06 第七轮：部分交付，附 ``delivery_note``）；硬门失败为
    failed。"""

    DELIVERED = "delivered"
    PARTIAL = "partial"
    FAILED = "failed"


class ArticleMaterialState(StrEnum):
    """材料状态：材料充足为 sufficient；缺原文/缺主题/材料不可读为
    insufficient（界面只展示一个最高价值问题，不堆叠通用建议）。"""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class ArticleFidelityItem(BaseModel):
    """一条保真失败/待确认项（确定性投影自 FidelityFailure，不含正文）。"""

    code: str = Field(description="稳定失败码（审计用）。")
    severity: str = Field(description="严重度：blocking / needs_user_confirmation。")
    category: str = Field(description="中文类别说明。")
    note: str = Field(description="中文说明（用户可见）。")


class ArticleFidelitySummary(BaseModel):
    """保真摘要：确定性计数与失败项；通过时 items 为空，不生成空洞总结。"""

    passed: bool = Field(description="无关键失败即为通过。")
    blocking_count: int = Field(description="硬失败数。")
    needs_confirmation_count: int = Field(description="需用户确认项数。")
    items: list[ArticleFidelityItem] = Field(
        default_factory=list, description="失败/待确认项清单。"
    )


class ArticleStyleReviewItem(BaseModel):
    """一条表达审稿定向项（确定性投影自 ExpressionReviewReport 的发现）。"""

    severity: str = Field(description="warning / suggestion。")
    category: str = Field(description="中文类别说明。")
    evidence: str = Field(description="触发审稿发现的正文证据片段。")
    suggestion: str = Field(description="定向建议。")
    location: SpanLocation = Field(description="发现位置。")


class ArticleStyleReviewSummary(BaseModel):
    """表达审稿摘要：计数 + 定向项；无发现时 items 为空。"""

    finding_count: int = Field(description="全部发现数。")
    warning_count: int = Field(description="warning 级发现数。")
    suggestion_count: int = Field(description="suggestion 级发现数。")
    items: list[ArticleStyleReviewItem] = Field(
        default_factory=list, description="定向审稿项（仅 warning/suggestion）。"
    )


class ArticleRevisionSummary(BaseModel):
    """定向修订摘要（Issue 05）：为何触发、解决哪些问题、仍有哪些风险。

    只投影确定性审计数据，不包含内部提示词、思维链或完整规则清单。
    """

    triggered: bool = Field(description="是否执行了修订调用。")
    trigger_label: str | None = Field(
        default=None, description="触发类别中文说明（保真/契约遗漏/审稿）。"
    )
    problem_count: int = Field(default=0, description="触发时待修问题数。")
    resolved_count: int = Field(default=0, description="修订解决的问题数。")
    remaining_count: int = Field(
        default=0, description="修订后仍存在的风险数（警告+遗漏+保真）。"
    )
    skipped_reason: str | None = Field(
        default=None, description="未执行修订的原因中文说明。"
    )


class ArticleEvidenceItem(BaseModel):
    """一条证据风险/变化项（确定性投影自 EvidenceSafeReport）。

    kind 区分：risk=默认模式风险项（正文保持原结论）；change=证据安全
    修订的实质变化；hold=材料不足/无法判定，保持原文待用户确认。
    """

    code: str = Field(description="稳定风险/变化 code。")
    category: str = Field(description="中文类别说明。")
    kind: str = Field(description="risk / change / hold。")
    original_span: str | None = Field(
        default=None, description="原文片段（change 项为修订前，其余为 None）。"
    )
    revised_span: str | None = Field(
        default=None, description="改后片段（change 项为修订后，其余为 None）。"
    )
    reason: str = Field(description="为何构成风险/为何修订的中文理由。")
    source_label: str | None = Field(
        default=None, description="消费的来源条目显示名；无法判定为 None。"
    )
    needs_user_confirmation: bool = Field(
        default=False, description="是否需用户确认（删除/升级/无法判定时 True）。"
    )


class ArticleConfirmationItem(BaseModel):
    """一条待用户确认项（保真待确认、契约需人工、证据 hold 等汇总）。"""

    code: str = Field(description="稳定来源 code。")
    label: str = Field(description="中文类别标签。")
    detail: str = Field(description="中文说明（用户可见）。")


class ArticleExcisionItem(BaseModel):
    """一条被剔除的违规条目（确定性投影自 FidelityFailure，不含正文）。"""

    code: str = Field(description="稳定失败码（审计用）。")
    category: str = Field(description="中文类别说明。")
    note: str = Field(description="中文说明（用户可见）。")


class ArticleExcisionSummary(BaseModel):
    """确定性句子级剔除摘要（Issue 02）：移除条数与条目类别，如实披露。

    只投影脱敏计数与失败码/类别，不包含被剔除的正文片段。
    """

    removed_count: int = Field(description="移除的违规条目数。")
    removed_sentence_count: int = Field(description="剔除的句子数。")
    sentence_ratio: float = Field(description="剔除句数占原文句数比例（0-1）。")
    items: list[ArticleExcisionItem] = Field(
        default_factory=list, description="被剔除的违规条目清单。"
    )


class HumanizerArticleProjection(BaseModel):
    """版本化文章结果投影（Issue 08）。

    新任务（表达路径或旧路径）的终态都生成该结构挂到
    ``HumanizerResultProjection.article``；前端只消费它渲染正文优先的
    交付界面。旧任务无此字段（None），按 legacy 投影展示，不伪造新
    来源硬门或新审稿通过状态。
    """

    projection_version: str = Field(
        default=ARTICLE_PROJECTION_VERSION, description="投影版本。"
    )
    audit_version: str = Field(
        description="审计版本：本结构内全部稳定 code 的版本标识。"
    )
    delivery_status: ArticleDeliveryStatus = Field(description="正文交付状态。")
    delivery_note: str | None = Field(
        default=None,
        description=(
            "用户可见交付说明：部分交付（``delivery_status == partial``）为"
            "「已交付首稿，未完成修订」；已剔除交付（``excision`` 非空）为"
            "「已移除 N 处无来源/未授权内容」。"
        ),
    )
    material_state: ArticleMaterialState = Field(
        default=ArticleMaterialState.SUFFICIENT, description="材料状态。"
    )
    one_question: str | None = Field(
        default=None,
        description=(
            "材料不足时唯一要问的最高价值问题（表达契约裁决产生）；"
            "界面只展示这一个问题，不堆叠通用建议。"
        ),
    )
    final_text: str | None = Field(
        default=None, description="最终正文；硬门失败或材料不足时为 None。"
    )
    fidelity: ArticleFidelitySummary | None = Field(
        default=None, description="保真摘要（未检查为 None）。"
    )
    style_review: ArticleStyleReviewSummary | None = Field(
        default=None, description="表达审稿摘要（新流程有；旧路径为 None）。"
    )
    revision: ArticleRevisionSummary | None = Field(
        default=None, description="定向修订摘要（未修订为 None）。"
    )
    excision: ArticleExcisionSummary | None = Field(
        default=None, description="确定性剔除摘要（未剔除为 None；Issue 02）。"
    )
    evidence: list[ArticleEvidenceItem] = Field(
        default_factory=list, description="证据风险/变化项。"
    )
    confirmations: list[ArticleConfirmationItem] = Field(
        default_factory=list, description="待用户确认项汇总。"
    )


class HumanizerResultProjection(BaseModel):
    """人味化任务的结果投影（挂载到助手消息）。"""

    task_id: str = Field(description="稳定任务标识（与消息尝试一致）。")
    skill_id: str = Field(description="SKILL 注册标识。")
    skill_version: str = Field(description="使用的 SKILL 固定版本。")
    path: HumanizerPath = Field(description="任务路径。")
    genre: Genre | None = Field(
        default=None, description="使用的体裁合同；None 为通用文章 profile。"
    )
    contract: HumanizerTaskContract = Field(description="任务契约快照。")
    expression_contract: ExpressionTaskContract | None = Field(
        default=None,
        description="版本化表达任务契约快照（人味化改造 Issue 03）；重试复用同一快照。",
    )
    status: HumanizerResultStatus = Field(description="任务终态。")
    output: HumanizerOutputContract | None = Field(
        default=None, description="输出合同；失败时可能为 None。"
    )
    fact_lock_check: FactLockCheckResult | None = Field(
        default=None, description="事实锁前后比较结果。"
    )
    source_ledger: SourceLedger | None = Field(
        default=None,
        description="本次任务使用的来源账本快照（新账本版本任务才有；"
        "旧任务为空，不得伪装成已通过新增来源检查）。",
    )
    fidelity_check: FidelityCheckResult | None = Field(
        default=None,
        description="保真硬门检查结果（新账本版本任务才有；缺失不得标记成功）。",
    )
    expression_review: Any | None = Field(
        default=None,
        description=(
            "表达审稿报告（人味化改造 Issue 04）；只有走表达任务契约的新流程"
            "才有。旧任务为空，风格发现默认为软审稿，不阻止交付。类型为"
            "ExpressionReviewReport（避免与 expression_review 模块循环导入，"
            "运行时不做 pydantic 校验）。"
        ),
    )
    evidence_safe: Any | None = Field(
        default=None,
        description=(
            "证据安全检查报告（人味化改造 Issue 06）：默认模式的风险项与"
            "授权修订的变化记录；普通无风险文章为空列表，不生成空洞的固定"
            "事实核查总结。类型为 EvidenceSafeReport（避免循环导入，运行时"
            "不做 pydantic 校验）。"
        ),
    )
    references: list[HumanizerReference] = Field(
        default_factory=list, description="保持的引用清单。"
    )
    genre_check: list[str] = Field(
        default_factory=list, description="体裁规则复核结果的中文摘要。"
    )
    quality_warnings: list[str] = Field(
        default_factory=list,
        description="软门未完全满足项的中文警告（交付正文时附；硬门冲突不在此列）。",
    )
    repair_attempts: int = Field(
        default=0, description="软门定向修复次数（最多 1 次，受总预算约束）。"
    )
    writing_call_count: int = Field(
        default=0,
        description="累计写作模型调用数（首稿+定向修订，上限 2；持久运行状态，"
        "重试与恢复沿用，服务重启不得重新获得修订额度）。",
    )
    revision: HumanizerRevisionAudit | None = Field(
        default=None, description="定向修订审计（Issue 05，脱敏）。"
    )
    process_state: HumanizerProcessState = Field(
        default=HumanizerProcessState.LOADING, description="过程卡当前状态。"
    )
    process_steps: list[str] = Field(
        default_factory=list, description="已完成的编排步骤中文名（过程卡轨迹）。"
    )
    error_code: str | None = Field(
        default=None, description="失败分类码（recovery 态展示）。"
    )
    error_message: str | None = Field(
        default=None, description="可操作的中文错误说明。"
    )
    article: HumanizerArticleProjection | None = Field(
        default=None,
        description=(
            "版本化文章结果投影（Issue 08）：新任务终态生成，前端据此渲染"
            "正文优先交付界面；旧任务为 None，按 legacy 投影展示，不伪造"
            "新来源硬门或新审稿通过状态。"
        ),
    )
    run_lock_id: str | None = Field(
        default=None,
        description=(
            "本次执行记录的主要模型运行锁（首稿锁）引用（Issue 11）；完整"
            "调用集合由统一锁仓库按 account_id + model_run_id 查询，投影"
            "不复制 prompt、正文或模型完整响应。"
        ),
    )
    model_run_id: str | None = Field(
        default=None,
        description=(
            "本次执行所属业务 run 标识（与模型运行锁 run_id 同一标识空间，"
            "Issue 11）；完整调用集合由统一锁仓库按 account_id + model_run_id "
            "查询，跨账户隔离由仓库保证。"
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="创建时间。"
    )


class HumanizerProcessData(BaseModel):
    """SSE 过程事件载荷：驱动过程卡五态。"""

    message_id: str = Field(description="助手消息标识。")
    state: HumanizerProcessState = Field(description="过程卡状态。")
    step_label: str = Field(description="当前步骤中文说明（loading/recovery 用）。")
    detail: str | None = Field(default=None, description="补充中文说明。")
    retryable: bool = Field(default=False, description="是否可重试。")
    progress_steps: list[str] = Field(
        default_factory=list, description="已完成的步骤中文轨迹。"
    )


class HumanizerSkillManifest(BaseModel):
    """内置 SKILL 的注册清单（供插件页与审计消费）。"""

    skill_id: str = Field(description="稳定注册标识。")
    name: str = Field(description="显示名（中文）。")
    version: str = Field(description="固定版本。")
    description: str = Field(description="能力说明（中文）。")
    read_only: bool = Field(default=True, description="内置只读，不可篡改或卸载。")
    source: str = Field(description="来源说明（原创净室）。")
    license: str = Field(description="许可证声明。")
    capabilities: list[str] = Field(
        default_factory=list, description="能力清单（改写/生成/事实锁/四体裁）。"
    )
    registration_version: str = Field(description="注册表 Schema 版本。")
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="注册时间。"
    )


__all__ = [
    "HumanizerProfileSlice",
    "HumanizerPath",
    "SOURCE_LEDGER_VERSION",
    "FIDELITY_CHECKER_VERSION",
    "SourceType",
    "SourceUsage",
    "ProtectedSpanKind",
    "PersonalExperienceMode",
    "SpanLocation",
    "ProtectedSpan",
    "SourceEntry",
    "LedgerCompileSummary",
    "SourceLedger",
    "FidelityFailureCode",
    "FidelitySeverity",
    "FidelityFailure",
    "FidelitySummary",
    "FidelityCheckResult",
    "FactLockKind",
    "FactLockStatus",
    "FactLockSeverity",
    "HumanizerProcessState",
    "HumanizerResultStatus",
    "HumanizerQualityStatus",
    "HumanizerTaskContract",
    "HumanizerSkillInput",
    "FactLockEntry",
    "FactLockCheckResult",
    "HumanizerEditKind",
    "HumanizerEdit",
    "HumanizerFactCheckItem",
    "HumanizerOutputContract",
    "HumanizerReference",
    "HumanizerResultProjection",
    "HumanizerProcessData",
    "HumanizerSkillManifest",
    "HumanizerRevisionAudit",
    "HUMANIZER_CHECKPOINT_KEY",
    "ARTICLE_PROJECTION_VERSION",
    "ARTICLE_AUDIT_VERSION",
    "ArticleDeliveryStatus",
    "ArticleMaterialState",
    "ArticleFidelityItem",
    "ArticleFidelitySummary",
    "ArticleStyleReviewItem",
    "ArticleStyleReviewSummary",
    "ArticleRevisionSummary",
    "ArticleEvidenceItem",
    "ArticleConfirmationItem",
    "HumanizerArticleProjection",
]
