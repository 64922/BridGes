"""Issue 15：自动画像抽取、重试与隐私说明的内部合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictStr,
    field_validator,
)

from bridges.contracts.atomic_profile import AtomicProfileMemoryResult
from bridges.contracts.profiles import FourDimension


class ProfileExtractionSource(StrEnum):
    """一次自动画像处理的稳定来源枚举（Issue 13）。

    每个自动画像 run 都必须且只能标记其中一种来源；本地规则分支绝不
    调用模型，模型分支的每次真实调用都产生独立运行锁。
    """

    LOCAL_RULE = "local_rule"
    QWEN_MODEL = "qwen_model"


PROFILE_SOURCE_LABELS: dict[ProfileExtractionSource, str] = {
    ProfileExtractionSource.LOCAL_RULE: "本地规则识别，未调用模型",
    ProfileExtractionSource.QWEN_MODEL: "Qwen 辅助识别",
}

#: 输出载荷合同版本：工单 04 起 dimension 可选、允许开放事实与相关记忆；
#: 工单 05 起允许 ``evidence_fragments``（跨轮多片段证据）。
PROFILE_EXTRACTION_OUTPUT_SCHEMA_VERSION = "profile-extraction-v4"
#: 网关适配器识别为画像专属合同错误的输出合同版本（历史版本继续识别，
#: 新增版本必须同步加入，否则合同错误码会漂移为通用结构化输出错误）。
PROFILE_EXTRACTION_CONTRACT_VERSIONS = frozenset(
    {
        "profile-extraction-v1",
        "profile-extraction-v2",
        "profile-extraction-v3",
        PROFILE_EXTRACTION_OUTPUT_SCHEMA_VERSION,
    }
)

#: 画像页面/首次说明使用的诚实混合策略说明；禁止夸大为"全部由 Qwen
#: 生成"或"完全不使用模型"。改进工单 04：自然自述与含义明确的表达统一由
#: 全局配置的 Qwen 语义识别提出候选，本机规则只做证据与边界复核（第三方、
#: 引用、假设、敏感信息与一次性情绪不写入）；更正与本轮控制指令仍由本机
#: 确定性规则即时处理，不调用模型。普通提取只在回答正常完成后异步入队，
#: 模型调用在写事务外进行；每次真实调用保留运行锁与调用版本合同，成本与
#: 调用计数按有界重试记录在既有审计里。
PROFILE_HYBRID_EXPLANATION = (
    "自动画像采用语义整理：你关于自己的稳定信息由全局配置的 Qwen 语义"
    "识别提出候选，本机规则复核证据与边界（第三方、引用、假设、敏感自述"
    "和一次性情绪不会自动长期保存）；明确的更正与本轮控制指令由本机规则"
    "即时处理。普通整理只在回答正常完成后异步进行，调用在写事务外完成，"
    "每次真实调用保留可审计的运行锁与调用版本。你可以随时查看、修改、"
    "撤回或关闭自动记录。"
)


class ProfileExtractionStatus(StrEnum):
    """一条用户消息的画像预处理状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    EXHAUSTED = "exhausted"


class ProfileExtractionOutcome(StrEnum):
    """独立于队列生命周期状态的稳定内部结果。

    工单 04 的四个用户可见档位与这些取值的对应关系：
    「无可记事实」= NO_SIGNAL / SUCCEEDED_EMPTY；「已提交」=
    SUCCEEDED_WRITTEN 及各成功变更档；「暂缓」= SUCCEEDED_OBSERVED
    （候选已记为观察、在晋升窗口内等待更多证据，尚未长期化）；「失败」=
    CORRECTION_FAILED / PENDING_RETRY / PERMANENT_FAILURE。
    """

    NO_SIGNAL = "no_signal"
    SUCCEEDED_EMPTY = "succeeded_empty"
    SUCCEEDED_OBSERVED = "succeeded_observed"
    SUCCEEDED_WRITTEN = "succeeded_written"
    SUCCEEDED_CORRECTION_WRITTEN = "succeeded_correction_written"
    SUCCEEDED_CORRECTION_PROTECTED = "succeeded_correction_protected"
    SUCCEEDED_CORRECTION_UNRESOLVED = "succeeded_correction_unresolved"
    SUCCEEDED_CORRECTION_NO_ACTIVE = "succeeded_correction_no_active"
    SUCCEEDED_MEMORY_DIRECTIVE = "succeeded_memory_directive"
    SUCCEEDED_LIFECYCLE_SIGNAL = "succeeded_lifecycle_signal"
    CORRECTION_FAILED = "correction_failed"
    PENDING_RETRY = "pending_retry"
    PERMANENT_FAILURE = "permanent_failure"


class ProfilePageStatus(StrEnum):
    """投影到当前账户画像页面的状态。"""

    READY = "ready"
    EMPTY = "empty"
    PENDING = "pending"
    FAILED = "failed"


class ProfileExtractionAction(StrEnum):
    """模型允许返回的最小动作集合。"""

    CREATE = "create"
    UPDATE = "update"
    OBSERVE = "observe"
    IGNORE = "ignore"


class ProfileCorrectionStatus(StrEnum):
    """一次聊天画像纠正的脱敏结果状态。"""

    WRITTEN = "written"
    PROTECTED = "protected"
    UNRESOLVED = "unresolved"
    NO_ACTIVE_RECORD = "no_active_record"
    FAILED = "failed"


class ProfileCorrectionResult(BaseModel):
    """聊天纠正结果；不保存或注入用户的新值正文。"""

    model_config = ConfigDict(extra="forbid")

    status: ProfileCorrectionStatus
    dimension: FourDimension | None = None
    record_id: StrictStr | None = None

    def context_metadata(self) -> dict[str, str | None]:
        """返回允许进入聊天提示词的最小脱敏元数据。"""

        return {
            "status": self.status.value,
            "dimension": self.dimension.label if self.dimension is not None else None,
        }


class ProfileExtractionFragment(BaseModel):
    """跨轮多片段证据的一条附属引用（工单 05）。

    模型引用窗口内某条消息的原话片段来补足当前消息无法单独支撑的结论
    （如「这个专业」的指代对象在上一轮出现）。片段只是候选证据：服务端
    仍按来源消息定位原话、核验主体/否定/时间/条件，定位失败、重复无法
    消歧、引用助手消息或第三人称片段都不写入。
    """

    model_config = ConfigDict(extra="forbid")

    evidence_ref: StrictStr = Field(
        min_length=1, max_length=200, description="窗口内消息标识。"
    )
    evidence_quote: StrictStr = Field(
        min_length=1,
        max_length=300,
        description="该消息中支持结论的原话片段（精确子串）。",
    )

    @field_validator("evidence_ref", "evidence_quote", mode="before")
    @classmethod
    def _require_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("profile extraction text fields must be strings")
        return value


class ProfileExtractionItem(BaseModel):
    """单条抽取结果；额外字段直接拒绝，防止合同漂移。

    改进工单 04：``dimension`` 是可选解析提示（``None`` 表示开放事实，
    如姓名、宠物、习惯），不再是写入资格或能力上限；``evidence_quote``
    是服务端定位证据的首选字段，事实正文按规范化表述写入但仍须通过证据
    核验（主体、否定、时间、条件与语义锚点）。工单 05：
    ``evidence_fragments`` 是跨轮多片段证据（最多 3 条），主引用仍是当前
    消息，片段只补足指代对象等无法在单条消息内闭合的含义。
    """

    model_config = ConfigDict(extra="forbid")

    dimension: FourDimension | None = Field(
        default=None,
        description=(
            "四维解析提示（可选）；帮助裸值解析事实身份（如「考研」）。"
            "开放事实（姓名、宠物、习惯等）为空，不要求伪造类别。"
        ),
    )
    normalized_value: StrictStr = Field(
        min_length=1,
        max_length=200,
        description=(
            "规范值锚点，必须是证据原话中真实出现的连续片段；事实正文可"
            "规范化改写，但该锚点供服务端确定性核验。"
        ),
    )
    evidence_ref: StrictStr = Field(
        min_length=1, max_length=200, description="内部消息证据引用。"
    )
    reliability: StrictFloat = Field(ge=0, le=1, description="内部可靠度。")
    action: ProfileExtractionAction = Field(
        description="create/update/observe/ignore。"
    )
    fact_text: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "完整事实正文（可选）。允许规范化改写（不要求逐字原文子串），"
            "但不得引入原话没有的主体、时间、条件或对象；缺失时沿用以来源"
            "记录正文为事实正文的旧行为，不伪造原话。"
        ),
    )
    evidence_quote: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "改进工单 04：支持该候选的当前消息原话片段（精确子串）。服务端"
            "按原文定位并核验主体、否定、时间与条件；与 evidence_start/end "
            "同时提供且不一致时以定位结果为准。"
        ),
    )
    evidence_start: int | None = Field(
        default=None,
        ge=0,
        description=(
            "支持原话在用户消息中的 Unicode 码点半开区间起点"
            "[start, end)。有区间时按精确证据验证主体、关系、否定与时间；"
            "缺失时退回整消息证据检查（旧合同行为）。"
        ),
    )
    evidence_end: int | None = Field(
        default=None,
        ge=0,
        description="精确原话区间终点（半开区间，与 evidence_start 成对出现）。",
    )
    evidence_fragments: list[ProfileExtractionFragment] | None = Field(
        default=None,
        max_length=3,
        description=(
            "工单 05：补充当前消息结论的窗口内多片段证据（最多 3 条）。"
            "每条给出消息标识与原话片段；服务端按来源定位并核验，"
            "不可定位、重复无法消歧或引用助手消息的候选零写入。"
        ),
    )

    @field_validator("normalized_value", "evidence_ref", mode="before")
    @classmethod
    def _require_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("profile extraction text fields must be strings")
        return value

    @field_validator("reliability", mode="before")
    @classmethod
    def _require_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("profile extraction reliability must be a number")
        return value


class ProfileExtractionOutput(BaseModel):
    """抽取器唯一允许返回的结构化载荷。

    改进工单 04：一条消息允许多项事实（最多 6 条）；超出按合同错误处理，
    由提示词约束模型自行控制在界内。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ProfileExtractionItem] = Field(max_length=6)


class ProfileExtractionRun(BaseModel):
    """消息级幂等预处理记录。"""

    extraction_id: str
    account_id: str
    message_id: str
    extractor_version: str
    source_hash: str
    source_snapshot: str = Field(max_length=4000)
    status: ProfileExtractionStatus
    outcome: ProfileExtractionOutcome = ProfileExtractionOutcome.SUCCEEDED_EMPTY
    attempts: int = Field(ge=0)
    committed_record_ids: list[str] = Field(default_factory=list)
    observed_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    source: ProfileExtractionSource | None = Field(
        default=None,
        description=(
            "抽取来源（local_rule/qwen_model）。新 run 必须显式标记；"
            "NULL 仅用于迁移前的历史行，投影不得将其伪装成任一来源。"
        ),
    )
    created_at: datetime
    updated_at: datetime


class ProfileExtractionRetryTask(BaseModel):
    """抽取失败后的持久重试任务投影。"""

    task_id: str
    account_id: str
    message_id: str
    extractor_version: str
    source_hash: str
    status: ProfileExtractionStatus
    attempts: int = Field(ge=0)
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AutomaticProfileObservation(BaseModel):
    """不进入模型上下文的内部观察；同一消息只保留一次。"""

    observation_id: str
    account_id: str
    message_id: str
    extractor_version: str
    dimension: FourDimension
    normalized_value: str = Field(min_length=1, max_length=200)
    evidence_ref: str
    reliability: float = Field(ge=0, le=1)
    source: ProfileExtractionSource | None = Field(
        default=None,
        description="产生该观察的抽取来源；新观察必须显式标记。",
    )
    created_at: datetime


class ProfilePrivacyNotice(BaseModel):
    """首次启用自动画像时的一次性非交互说明。"""

    version: str
    text: str
    shown_at: datetime


class ProfilePreprocessResult(BaseModel):
    """消息预处理返回值；主聊天失败时仍可继续生成。"""

    run: ProfileExtractionRun
    privacy_notice: ProfilePrivacyNotice | None = None
    committed_record_ids: list[str] = Field(default_factory=list)
    observed_count: int = Field(default=0, ge=0)
    correction: ProfileCorrectionResult | None = None
    memory: AtomicProfileMemoryResult | None = Field(
        default=None,
        description="本轮同步处理的「记住／忘掉」结果；没有指令时为空。",
    )


class ProfileStatusProjection(BaseModel):
    """画像页面使用的当前账户最小聚合状态。"""

    status: ProfilePageStatus
    has_records: bool
    can_retry: bool = False
    extraction_sources: dict[str, int] = Field(
        default_factory=dict,
        description="按来源（local_rule/qwen_model）统计的抽取 run 计数；历史 NULL 来源不计数。",
    )
    source_explanation: str | None = Field(
        default=None,
        description="对用户可见的混合策略诚实说明（中文）；不夸大为全模型或全本地。",
    )


#: 账户级画像控制合同的版本；控制语义变化时递增，客户端据此识别合同。
PROFILE_CONTROLS_VERSION = "profile-controls-v1"


class ProfileAccountControlsProjection(BaseModel):
    """账户级画像记录/使用控制状态。

    改进工单 07：自动记录与长期画像使用分开控制——关闭记录只阻止自动
    新增/更新（主动记住、修改、忘掉、删除始终可用）；关闭使用不删除信息，
    只停止回答读取长期画像正文。停止记录的持久状态是
    ``profile_extraction_privacy_blocks`` 的账户级行，这里按「是否开启」
    组合投影。
    """

    controls_version: str = Field(
        default=PROFILE_CONTROLS_VERSION,
        description="控制合同版本；客户端据此识别字段语义。",
    )
    recording_enabled: bool = Field(
        description="自动记录开关：False 表示已停止自动新增/更新。",
    )
    usage_enabled: bool = Field(
        description="长期画像使用开关：False 表示回答不再读取长期画像正文。",
    )
    usage_control_version: int = Field(
        ge=0,
        description="使用开关变更序号；0 表示从未变更，同值重复写入不递增。",
    )
    usage_updated_at: datetime | None = Field(
        default=None,
        description="使用开关最近一次真实变化时间；从未变更为空。",
    )


class ProfileAccountControlsUpdateRequest(BaseModel):
    """更新账户级画像控制的请求；两个字段都可选，但至少指定一项。"""

    model_config = ConfigDict(extra="forbid")

    recording_enabled: bool | None = Field(
        default=None,
        description="目标自动记录状态；不传表示不修改。",
    )
    usage_enabled: bool | None = Field(
        default=None,
        description="目标长期画像使用状态；不传表示不修改。",
    )
