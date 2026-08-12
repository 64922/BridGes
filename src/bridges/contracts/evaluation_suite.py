"""可复现 A/B 科学评测套件契约（Issue 40）。

本文件定义评测套件的公开表面，分六层：

- :class:`SuiteDefinition`：版本化评测包，统一登记数据清单、授权与许可证、
  任务定义、运行矩阵、模型与 SKILL 版本、随机种子、评分量表和预期产物
  Schema；任一内容变化都会改变套件摘要（digest）。
- :class:`EvalCase`：最小可重放评测单元，带明确用户/租户/项目/学习阶段/
  科学领域上下文、初始状态、输入轮次、授权、允许工具、预期 Claim、
  合法状态路径、自动断言、人工量表和预算。
- :class:`SuiteRunLock`：一次评测实际使用的代码与环境、数据集、领域包、
  模型运行锁、提示、Schema、工作流、工具、裁判、评分量表、随机种子和
  执行次数的不可变快照；任一变化都会生成新的锁与报告版本。
- :class:`CaseResult`：单案例执行结果，含结构化产物、工具记录、自动断言、
  指标、裁判分、高风险失败案例与一键重放命令。
- 盲评：匿名化（隐藏系统身份、随机化顺序）、评审提交与一致性统计。
- :class:`EvaluationReport` 与 :class:`ReleaseThreshold`：报告与发布阈值。

设计约束：产物绝不包含完整私人正文、完整提示词或秘密；失败案例只引用
固定输入、运行锁、工具记录与输出的稳定标识，便于一键重放。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from bridges.contracts.ai import ModelRunLock


def now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(UTC).isoformat()


#: 评测套件的六层+安全维度。报告、矩阵与指标按此维度组织。
class EvaluationDimension(StrEnum):
    PROFILE = "profile"            # A 数字分身画像闭环
    HUMANIZATION = "humanization"  # B 原创人味表达
    SCIENCE = "science"            # 科学事实准确与低幻觉
    TEACHING = "teaching"          # 因材施教
    CAREER = "career"              # 生涯规划与陪伴
    MULTIMODAL = "multimodal"      # ASR/TTS/图片/视频/提醒
    SECURITY = "security"          # 风险识别与边界


class SUTKind(StrEnum):
    """被测系统类别：完整 BridGes、基础 Qwen、合法开源参考方法与消融。"""

    BRIDGES_FULL = "bridges_full"
    QWEN_BASELINE = "qwen_baseline"
    OPEN_SOURCE_REFERENCE = "open_source_reference"
    ABLATION = "ablation"


class SuiteStatus(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"


class CaseRunStatus(StrEnum):
    """单案例执行终态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ERROR = "error"


class SuiteInvalidationTrigger(StrEnum):
    """评测套件失效事件触发源（与领域包失效语义对齐）。"""

    DATA_RECALL = "data_recall"
    LICENSE_REVOCATION = "license_revocation"
    MODEL_CONTRACT_CHANGE = "model_contract_change"
    SCORING_LOGIC_CHANGE = "scoring_logic_change"
    MANUAL = "manual"


class LicenseRecord(BaseModel):
    """一条资产的授权与许可证记录（验证外部参考的合法性）。"""

    asset_id: str = Field(description="稳定资产标识。")
    title: str = Field(description="资产标题。")
    source: str = Field(description="来源说明：原创 / 方法启发 / 依法复用。")
    source_url: str | None = Field(default=None, description="外部来源链接。")
    license: str = Field(description="许可证声明（如 MIT、CC-BY-4.0、原创无外部许可需求）。")
    usage_scope: str = Field(description="使用范围（如：仅评测数据、方法借鉴）。")
    checked_at: str = Field(description="许可证检查日期（ISO 8601）。")
    notes: str = Field(default="", description="补充说明。")

    def provenance_digest(self) -> str:
        """规范化来源摘要：资产+来源+许可证+范围。"""
        canonical = json.dumps(
            [self.asset_id, self.title, self.source, self.license, self.usage_scope],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DataManifestEntry(BaseModel):
    """数据清单中的一项：固定数据集及其版本与哈希。"""

    dataset_id: str = Field(description="稳定数据集标识。")
    version: str = Field(description="数据集版本。")
    title: str = Field(description="数据集标题。")
    description: str = Field(default="", description="数据集说明。")
    license_ref: str = Field(description="对应的 LicenseRecord.asset_id。")
    content_hash: str = Field(description="数据内容 SHA-256（用于双跑校验样本集合一致）。")
    record_count: int = Field(default=0, description="记录条数。")
    language: str = Field(default="zh-CN", description="数据语言。")
    created: str = Field(
        default_factory=now_iso, description="创建/固定日期（ISO 8601）。"
    )


class ScoringAnchor(BaseModel):
    """评分量表锚点：某个分数对应的可观察描述。"""

    score: float = Field(description="锚点分数。")
    description: str = Field(description="该分数对应的中文描述。")

    def anchor_key(self) -> str:
        return f"{self.score:g}"


class ScoringScaleItem(BaseModel):
    """评分量表中的一项（一个可评分维度）。"""

    item_id: str = Field(description="量表项标识。")
    name: str = Field(description="量表项名称（中文）。")
    description: str = Field(description="量表项描述。")
    anchors: list[ScoringAnchor] = Field(
        default_factory=list, description="分数锚点（用于人工与自动裁判）。"
    )


class ScoringScale(BaseModel):
    """评分量表：量表项 + 分值范围，版本化。"""

    scale_id: str = Field(description="量表标识。")
    version: str = Field(description="量表版本（变化必须生成新锁）。")
    items: list[ScoringScaleItem] = Field(description="量表项。")
    min: float = Field(description="最低分。")
    max: float = Field(description="最高分。")

    def item_ids(self) -> list[str]:
        return [item.item_id for item in self.items]


class ExpectedArtifactField(BaseModel):
    """预期产物 Schema 中的一个字段。"""

    name: str = Field(description="字段名。")
    kind: str = Field(description="字段类型：string/number/boolean/object/array。")
    required: bool = Field(default=True, description="是否必须。")
    description: str = Field(default="", description="字段说明（中文）。")


class ExpectedArtifactSchema(BaseModel):
    """预期产物 Schema：评测案例结构化产物的契约。"""

    schema_id: str = Field(description="Schema 标识。")
    version: str = Field(description="Schema 版本。")
    fields: list[ExpectedArtifactField] = Field(description="字段清单。")

    def validate_output(self, output: dict[str, Any]) -> list[str]:
        """校验输出是否符合 Schema，返回缺失/类型不符字段的中文描述。"""
        problems: list[str] = []
        for field in self.fields:
            if field.name not in output:
                if field.required:
                    problems.append(f"缺少必需字段：{field.name}")
                continue
            value = output[field.name]
            if field.kind == "string" and not isinstance(value, str):
                problems.append(f"字段 {field.name} 应为字符串")
            elif field.kind == "number" and not isinstance(value, (int, float)):
                problems.append(f"字段 {field.name} 应为数值")
            elif field.kind == "boolean" and not isinstance(value, bool):
                problems.append(f"字段 {field.name} 应为布尔值")
            elif field.kind == "array" and not isinstance(value, list):
                problems.append(f"字段 {field.name} 应为数组")
            elif field.kind == "object" and not isinstance(value, dict):
                problems.append(f"字段 {field.name} 应为对象")
        return problems


class EvalBudget(BaseModel):
    """一次评测案例的预算：模型调用上限、成本上限与时延上限。"""

    max_model_calls: int = Field(default=20, description="允许的最大模型调用次数。")
    max_cost_estimate: float | None = Field(default=None, description="成本估算上限。")
    max_latency_seconds: float | None = Field(default=None, description="时延上限。")

    def exceed_reasons(
        self, model_calls: int, cost: float | None, latency_seconds: float | None
    ) -> list[str]:
        reasons: list[str] = []
        if model_calls > self.max_model_calls:
            reasons.append(f"模型调用超预算：{model_calls} > {self.max_model_calls}")
        if (
            self.max_cost_estimate is not None
            and cost is not None
            and cost > self.max_cost_estimate
        ):
            reasons.append(f"成本超预算：{cost:g} > {self.max_cost_estimate:g}")
        if (
            self.max_latency_seconds is not None
            and latency_seconds is not None
            and latency_seconds > self.max_latency_seconds
        ):
            reasons.append(f"时延超预算：{latency_seconds:g}s > {self.max_latency_seconds:g}s")
        return reasons


class TaskDefinition(BaseModel):
    """评测任务定义：一个维度内的任务（可含多个案例）。"""

    task_id: str = Field(description="任务标识。")
    dimension: EvaluationDimension = Field(description="所属维度。")
    title: str = Field(description="任务标题（中文）。")
    description: str = Field(description="任务描述。")
    dataset_refs: list[str] = Field(default_factory=list, description="引用的数据集。")
    scale_id: str = Field(description="使用的评分量表。")
    expected_artifact_schema_id: str = Field(description="预期产物 Schema。")
    allowed_tools: list[str] = Field(default_factory=list, description="允许的工具能力。")
    budget: EvalBudget = Field(default_factory=EvalBudget, description="案例预算。")
    required_claims: list[str] = Field(default_factory=list, description="预期 Claim 清单。")
    legal_state_paths: list[list[str]] = Field(
        default_factory=list, description="合法状态路径（状态名序列）。"
    )


class DataCard(BaseModel):
    """数据卡：数据集用途、采集方式、敏感度、风险切片与已知偏差。"""

    dataset_id: str = Field(description="对应数据集。")
    version: str = Field(description="数据卡版本。")
    purpose: str = Field(description="用途（中文）。")
    collection_method: str = Field(description="采集/构造方式。")
    sensitivity: str = Field(default="low", description="敏感度：low/medium/high。")
    risk_slice: str = Field(
        default="normal", description="风险切片：normal/high_risk/novice/expert。"
    )
    missing_data: str = Field(default="", description="缺失数据说明。")
    known_biases: str = Field(default="", description="已知偏差说明。")
    license_ref: str = Field(description="对应的 LicenseRecord.asset_id。")


class ModelSkillPin(BaseModel):
    """模型与 SKILL 版本固定记录（评测时实际使用的版本快照）。"""

    capability_name: str = Field(description="逻辑能力名。")
    capability_version: str = Field(description="能力版本。")
    model_id: str | None = Field(default=None, description="实际模型 ID。")
    prompt_version: str = Field(default="", description="提示词版本。")
    skill_id: str | None = Field(default=None, description="SKILL 标识（如 bridges-humanizer）。")
    skill_version: str | None = Field(default=None, description="SKILL 版本。")
    license_ref: str | None = Field(default=None, description="许可证记录引用。")


class RunMatrixEntry(BaseModel):
    """运行矩阵中的一项：某被测系统 × 任务 × 案例 × 种子 × 执行次数。"""

    sut_id: str = Field(description="被测系统标识（SUT 注册表中的键）。")
    task_id: str = Field(description="任务标识。")
    case_ids: list[str] = Field(default_factory=list, description="执行的案例。")
    seeds: list[int] = Field(default_factory=list, description="随机种子。")
    execution_count: int = Field(default=1, ge=1, description="每个种子执行次数。")


class SuiteRunMatrix(BaseModel):
    entries: list[RunMatrixEntry] = Field(default_factory=list)

    def entry_count(self) -> int:
        return len(self.entries)

    def coverage(self) -> list[str]:
        """返回矩阵覆盖的 (sut_id, task_id) 对清单。"""
        return sorted({f"{e.sut_id}@{e.task_id}" for e in self.entries})


class SuiteDefinition(BaseModel):
    """版本化评测包：数据清单、许可证、任务、运行矩阵、版本固定与失效状态。

    套件是不可变资产：任何内容变化都应产生新版本（新 digest），旧版本
    仍可追溯且不被覆盖。
    """

    suite_id: str = Field(description="稳定套件标识。")
    version: str = Field(description="套件语义版本。")
    name: str = Field(description="套件名称。")
    description: str = Field(default="", description="套件说明。")
    manifest: list[DataManifestEntry] = Field(
        default_factory=list, description="固定数据清单。"
    )
    licenses: list[LicenseRecord] = Field(
        default_factory=list, description="授权与许可证记录。"
    )
    tasks: list[TaskDefinition] = Field(default_factory=list, description="任务定义。")
    run_matrix: SuiteRunMatrix = Field(
        default_factory=SuiteRunMatrix, description="运行矩阵。"
    )
    model_skill_pins: list[ModelSkillPin] = Field(
        default_factory=list, description="模型与 SKILL 版本固定。"
    )
    seeds: list[int] = Field(default_factory=list, description="随机种子集合。")
    scales: list[ScoringScale] = Field(default_factory=list, description="评分量表。")
    artifact_schemas: list[ExpectedArtifactSchema] = Field(
        default_factory=list, description="预期产物 Schema。"
    )
    data_cards: list[DataCard] = Field(default_factory=list, description="数据卡。")
    domain_pack_dependencies: dict[str, str] = Field(
        default_factory=dict, description="领域包依赖：包名 -> 版本。"
    )
    status: SuiteStatus = Field(default=SuiteStatus.ACTIVE, description="套件状态。")
    invalidated_at: str | None = Field(default=None, description="失效时间。")
    invalidation_trigger: SuiteInvalidationTrigger | None = Field(
        default=None, description="失效触发源。"
    )
    invalidation_reason: str | None = Field(default=None, description="失效原因（中文）。")
    created_at: str = Field(description="创建时间（ISO 8601）。")

    def digest(self) -> str:
        """规范化套件摘要：任何内容变化都会产生新摘要（元数据不参与）。"""
        canonical = json.dumps(
            self.model_dump(
                exclude={
                    "created_at",
                    "invalidated_at",
                    "invalidation_trigger",
                    "invalidation_reason",
                }
            ),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def scale(self, scale_id: str) -> ScoringScale:
        for scale in self.scales:
            if scale.scale_id == scale_id:
                return scale
        raise KeyError(f"评分量表不存在：{scale_id}")

    def artifact_schema(self, schema_id: str) -> ExpectedArtifactSchema:
        for schema in self.artifact_schemas:
            if schema.schema_id == schema_id:
                return schema
        raise KeyError(f"预期产物 Schema 不存在：{schema_id}")

    def task(self, task_id: str) -> TaskDefinition:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"任务不存在：{task_id}")

    def dataset(self, dataset_id: str) -> DataManifestEntry:
        for entry in self.manifest:
            if entry.dataset_id == dataset_id:
                return entry
        raise KeyError(f"数据集不存在：{dataset_id}")


class EvalCaseContext(BaseModel):
    """评测案例的明确上下文：用户、租户、项目、学习阶段与科学领域。"""

    account_id: str = Field(description="明确用户（合成评测账户）。")
    tenant_id: str | None = Field(default=None, description="机构租户（可空）。")
    project_id: str = Field(description="项目标识。")
    learning_stage: str = Field(
        default="novice", description="学习阶段：novice/intermediate/advanced。"
    )
    science_domain: str = Field(default="physics", description="科学领域。")
    synthetic_profile: dict[str, Any] = Field(
        default_factory=dict, description="合成画像快照（不来自真实用户）。"
    )


class EvalTurn(BaseModel):
    """案例输入轮次：用户消息或反馈修正。"""

    role: str = Field(description="角色：user / feedback。")
    content: str = Field(description="消息内容（中文）。")
    meta: dict[str, Any] = Field(
        default_factory=dict, description="附加元数据（如：反馈类型、附件引用）。"
    )


class AutoAssertion(BaseModel):
    """自动断言：对结构化产物的确定性检查项。"""

    assertion_id: str = Field(description="断言标识。")
    kind: str = Field(description="断言类型（由断言器实现，见 metrics 模块）。")
    expectation: str = Field(description="预期描述（中文）。")


class EvalCase(BaseModel):
    """最小可重放评测单元（CONTEXT.md「评测案例」）。"""

    case_id: str = Field(description="案例标识。")
    suite_id: str = Field(description="所属套件。")
    suite_version: str = Field(description="套件版本。")
    task_id: str = Field(description="所属任务。")
    title: str = Field(description="案例标题（中文）。")
    context: EvalCaseContext = Field(description="明确上下文。")
    initial_state: dict[str, Any] = Field(
        default_factory=dict, description="初始状态（如已有画像断言、知识库材料）。"
    )
    turns: list[EvalTurn] = Field(default_factory=list, description="输入轮次。")
    authorization: dict[str, Any] = Field(
        default_factory=dict, description="授权快照（如画像自动更新许可范围）。"
    )
    allowed_tools: list[str] = Field(default_factory=list, description="允许工具。")
    expected_claims: list[str] = Field(
        default_factory=list, description="预期 Claim（中文描述）。"
    )
    legal_state_paths: list[list[str]] = Field(
        default_factory=list, description="合法状态路径（状态名序列）。"
    )
    auto_assertions: list[AutoAssertion] = Field(
        default_factory=list, description="自动断言清单。"
    )
    human_scale_id: str | None = Field(default=None, description="人工量表标识。")
    budget: EvalBudget = Field(default_factory=EvalBudget, description="预算。")
    expected_artifact_schema_id: str | None = Field(
        default=None, description="预期产物 Schema。"
    )
    risk_tier: str = Field(default="normal", description="风险档：normal/high_risk。")

    def case_key(self) -> str:
        return f"{self.suite_id}@{self.suite_version}@{self.task_id}@{self.case_id}"


class ToolCallRecord(BaseModel):
    """一次工具/模型调用的不可变记录（失败案例回溯的依据）。"""

    call_id: str = Field(description="调用标识。")
    capability_name: str = Field(description="逻辑能力名。")
    capability_version: str = Field(description="能力版本。")
    actual_model_id: str | None = Field(default=None, description="实际模型 ID。")
    prompt_version: str = Field(default="", description="提示词版本。")
    input_output_contract: str = Field(default="", description="输入输出契约。")
    status: str = Field(description="调用状态。")
    error_code: str | None = Field(default=None, description="稳定错误码。")
    latency_ms: int = Field(default=0, description="时延（毫秒）。")
    result_summary: dict[str, Any] = Field(
        default_factory=dict, description="结果摘要（不含秘密与完整正文）。"
    )


class ArtifactRef(BaseModel):
    """产物引用：结构化产物或媒体资产的稳定标识。"""

    artifact_id: str = Field(description="产物标识。")
    kind: str = Field(description="产物类别。")
    object_id: str | None = Field(default=None, description="对象库引用（媒体资产）。")
    content_hash: str | None = Field(default=None, description="内容哈希。")
    note: str = Field(default="", description="中文说明。")


class MetricValue(BaseModel):
    """一条指标值：可带维度、量表与切片标签。"""

    metric_id: str = Field(description="指标标识。")
    dimension: EvaluationDimension = Field(description="所属维度。")
    name: str = Field(description="指标名（中文）。")
    value: float = Field(description="指标值。")
    scale_id: str | None = Field(default=None, description="对应评分量表。")
    slice_tag: str | None = Field(default=None, description="切片标签（如 high_risk）。")
    evidence: list[str] = Field(default_factory=list, description="证据引用（稳定标识）。")


class AutoAssertionResult(BaseModel):
    assertion_id: str = Field(description="断言标识。")
    passed: bool = Field(description="是否通过。")
    detail: str = Field(default="", description="断言结果说明（中文）。")


class JudgeKind(StrEnum):
    DETERMINISTIC = "deterministic"
    FIXED_MODEL = "fixed_model"
    HUMAN = "human"


class JudgeScore(BaseModel):
    """自动或人工裁判对单个量表项的评分。"""

    judge_id: str = Field(description="裁判标识（确定性规则或固定模型裁判）。")
    judge_version: str = Field(description="裁判版本。")
    kind: JudgeKind = Field(description="裁判类别。")
    item_id: str = Field(description="量表项。")
    score: float = Field(description="评分。")
    scale_id: str | None = Field(default=None, description="量表标识。")
    rationale: str | None = Field(default=None, description="评分理由（中文，可空）。")

    def score_key(self) -> str:
        return f"{self.judge_id}@{self.judge_version}:{self.item_id}"


class FailureCaseInfo(BaseModel):
    """高风险失败案例：从报告可追溯到固定输入、锁、工具记录与输出。"""

    failure_id: str = Field(description="失败案例标识。")
    dimension: EvaluationDimension = Field(description="所属维度。")
    task_id: str = Field(description="任务标识。")
    case_id: str = Field(description="案例标识。")
    sut_id: str = Field(description="被测系统标识。")
    severity: str = Field(default="high", description="严重度：high/medium/low。")
    description: str = Field(description="失败描述（中文）。")
    trace_refs: list[str] = Field(
        default_factory=list,
        description="追溯引用：固定输入 / 运行锁 / 工具记录 / 产物标识。",
    )
    reproduction_command: str = Field(description="一键重放该案例的命令。")
    created_at: str = Field(description="记录时间（ISO 8601）。")


class CaseResult(BaseModel):
    """单案例执行结果（append-only，不覆盖历史）。"""

    case_result_id: str = Field(description="结果标识。")
    lock_id: str = Field(description="产生该结果的评测运行锁。")
    sut_id: str = Field(description="被测系统标识。")
    task_id: str = Field(description="任务标识。")
    case_id: str = Field(description="案例标识。")
    seed: int = Field(description="随机种子。")
    execution_index: int = Field(description="执行序号。")
    status: CaseRunStatus = Field(description="执行终态。")
    outputs: dict[str, Any] = Field(
        default_factory=dict, description="结构化产物（按预期产物 Schema）。"
    )
    artifacts: list[ArtifactRef] = Field(default_factory=list, description="产物引用。")
    tool_records: list[ToolCallRecord] = Field(
        default_factory=list, description="工具/模型调用记录。"
    )
    state_trajectory: list[str] = Field(default_factory=list, description="状态轨迹。")
    auto_assertions: list[AutoAssertionResult] = Field(
        default_factory=list, description="自动断言结果。"
    )
    metrics: list[MetricValue] = Field(default_factory=list, description="确定性指标。")
    judge_scores: list[JudgeScore] = Field(default_factory=list, description="裁判评分。")
    failure_case: FailureCaseInfo | None = Field(
        default=None, description="高风险失败案例（命中时挂载）。"
    )
    reproduction_command: str = Field(description="一键重放命令。")
    latency_ms: int = Field(default=0, description="案例总时延（毫秒）。")
    cost_estimate: dict[str, Any] | None = Field(
        default=None, description="成本估算元数据。"
    )
    created_at: str = Field(description="产生时间（ISO 8601）。")

    def metric(self, metric_id: str) -> MetricValue | None:
        for metric in self.metrics:
            if metric.metric_id == metric_id:
                return metric
        return None

    def judge_score(self, judge_id: str, item_id: str) -> JudgeScore | None:
        for score in self.judge_scores:
            if score.judge_id == judge_id and score.item_id == item_id:
                return score
        return None


class SuiteRunLock(BaseModel):
    """一次评测运行实际使用内容的不可变快照（CONTEXT.md「评测运行锁」）。

    任一固定快照变化都会生成新的锁和报告版本；旧结果仍可追溯且不会被
    静默覆盖。锁身份由 :meth:`digest` 规范化哈希确定。
    """

    lock_id: str = Field(description="稳定锁标识。")
    suite_id: str = Field(description="套件标识。")
    suite_version: str = Field(description="套件版本。")
    suite_digest: str = Field(description="套件内容摘要。")
    code_commit_or_build_digest: str = Field(description="代码/构建摘要。")
    runtime_identifier: str = Field(description="运行载体。")
    os_hardware_summary: str = Field(description="操作系统与硬件摘要。")
    database_migration_version: str = Field(description="数据库迁移版本。")
    config_digest: str = Field(description="配置摘要（不含秘密）。")
    dataset_versions: dict[str, str] = Field(
        default_factory=dict, description="数据集名 -> 版本。"
    )
    domain_pack_versions: dict[str, str] = Field(
        default_factory=dict, description="领域包名 -> 版本。"
    )
    model_run_locks: list[ModelRunLock] = Field(
        default_factory=list, description="实际观察到的模型调用锁。"
    )
    prompt_versions: dict[str, str] = Field(
        default_factory=dict, description="能力 -> 提示词版本。"
    )
    schema_versions: dict[str, str] = Field(
        default_factory=dict, description="Schema -> 版本。"
    )
    tool_adapter_versions: dict[str, str] = Field(
        default_factory=dict, description="工具适配器 -> 版本。"
    )
    judge_versions: dict[str, str] = Field(
        default_factory=dict, description="裁判 -> 版本。"
    )
    scoring_scale_versions: dict[str, str] = Field(
        default_factory=dict, description="评分量表 -> 版本。"
    )
    random_seeds: list[int] = Field(default_factory=list, description="随机种子。")
    execution_count: int = Field(default=1, ge=1, description="每个种子的执行次数。")
    network_cache_policy: str = Field(default="frozen", description="网络缓存策略。")
    created_at: str = Field(description="锁创建时间（ISO 8601）。")

    def digest(self) -> str:
        """锁身份：除锁标识、创建时间与随机观察锁外的全部内容参与哈希。

        观察锁（model_run_locks）携带随机锁 ID 与时间戳，不参与身份哈希；
        其能力→模型映射由 :attr:`observed_locks_digest` 冻结，双跑比较
        以身份摘要 + 观察摘要双重校验。
        """
        payload = self.model_dump(exclude={"lock_id", "created_at", "model_run_locks"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def observed_digest(self) -> str:
        """观察锁的能力→模型映射摘要（随机 ID/时间戳不参与）。"""
        pairs = sorted(
            {
                (lock.capability_name, lock.capability_version, lock.actual_model_id)
                for lock in self.model_run_locks
            }
        )
        canonical = json.dumps(pairs, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BlindReviewItem(BaseModel):
    """一个盲评对照项：匿名化、随机化顺序的两段输出。

    只携带匿名标签与输出文本，不携带「标签 → 被测系统」的映射；映射由
    评审组织者按固定种子恢复，评审者只接触匿名化输出（隐藏系统身份）。
    """

    item_id: str = Field(description="盲评项标识。")
    review_set_id: str = Field(description="所属盲评集。")
    dimension: EvaluationDimension = Field(description="所属维度。")
    task_id: str = Field(description="任务标识。")
    case_id: str = Field(description="案例标识。")
    label_a: str = Field(description="随机化后的 A 标签（隐藏系统身份）。")
    label_b: str = Field(description="随机化后的 B 标签。")
    output_a: str = Field(description="A 标签下的输出（已剥离系统身份）。")
    output_b: str = Field(description="B 标签下的输出（已剥离系统身份）。")
    order_seed: int = Field(description="随机化顺序的种子。")


class BlindReviewSubmission(BaseModel):
    """一位盲评者对某个盲评项的评审提交。"""

    submission_id: str = Field(description="提交标识。")
    review_set_id: str = Field(description="盲评集标识。")
    reviewer_id: str = Field(description="盲评者标识（不知道系统身份）。")
    item_id: str = Field(description="盲评项。")
    chosen: str = Field(description="选择：label_a / label_b / tie。")
    rationale: str | None = Field(default=None, description="评审理由（中文，可空）。")
    submitted_at: str = Field(description="提交时间（ISO 8601）。")


class BlindReviewSet(BaseModel):
    """一个盲评集：匿名对照项 + 评审提交 + 一致性统计。

    ``legacy`` 标记旧人工评审集（Issue 10 起）：只读展示，不接受新提交，
    不进入新的隔离多模型自动统计，也不阻塞全自动流程。
    """

    review_set_id: str = Field(description="盲评集标识。")
    lock_id: str = Field(description="关联运行锁。")
    items: list[BlindReviewItem] = Field(default_factory=list, description="对照项。")
    submissions: list[BlindReviewSubmission] = Field(
        default_factory=list, description="评审提交。"
    )
    created_at: str = Field(description="创建时间（ISO 8601）。")
    legacy: bool = Field(
        default=False,
        description="旧人工评审集标记：只读，不进入新的自动统计。",
    )

    def item(self, item_id: str) -> BlindReviewItem:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise KeyError(f"盲评项不存在：{item_id}")

    def submission_count(self) -> int:
        return len(self.submissions)

    def pair_agreement(self) -> float:
        """在共享项目上计算两位评审者的简单一致率（0-1）。"""
        # 计算共享项目的评审一致性
        by_item: dict[str, list[BlindReviewSubmission]] = {}
        for submission in self.submissions:
            by_item.setdefault(submission.item_id, []).append(submission)
        shared = [s for s in by_item.values() if len(s) >= 2]
        if not shared:
            return 0.0
        agreed = sum(
            1 for group in shared if len({sub.chosen for sub in group}) == 1
        )
        return agreed / len(shared)

    def low_consistency_items(self, threshold: float = 0.6) -> list[str]:
        """低一致性项目：多位评审者选择不一致的项目（需复核，不强行合并）。"""
        by_item: dict[str, list[BlindReviewSubmission]] = {}
        for submission in self.submissions:
            by_item.setdefault(submission.item_id, []).append(submission)
        low: list[str] = []
        for item_id, group in by_item.items():
            if len(group) < 2:
                continue
            choices = {sub.chosen for sub in group}
            if len(choices) > 1:
                low.append(item_id)
        return low


class PointEstimate(BaseModel):
    """报告中的一个点估计：样本量、均值与 95% 置信区间。"""

    dimension: EvaluationDimension = Field(description="维度。")
    sut_id: str = Field(description="被测系统。")
    metric_id: str = Field(description="指标。")
    n: int = Field(description="样本量。")
    mean: float = Field(description="点估计。")
    ci_low: float = Field(description="区间下界。")
    ci_high: float = Field(description="区间上界。")
    unit: str | None = Field(default=None, description="单位。")

    def interval_width(self) -> float:
        return self.ci_high - self.ci_low


class Comparison(BaseModel):
    """一次两两对比：均值差、p 值与显著性（方法固定，种子可复现）。"""

    dimension: EvaluationDimension = Field(description="维度。")
    metric_id: str = Field(description="指标。")
    sut_a: str = Field(description="对比方 A。")
    sut_b: str = Field(description="对比方 B。")
    mean_diff: float = Field(description="均值差（A - B）。")
    p_value: float = Field(description="显著性 p 值。")
    significant: bool = Field(description="是否显著。")
    method: str = Field(description="检验方法（固定，如 Welch t 检验）。")
    direction: str = Field(description="方向说明（中文）。")


class FailureStats(BaseModel):
    dimension: EvaluationDimension = Field(description="维度。")
    sut_id: str = Field(description="被测系统。")
    failure_count: int = Field(description="失败案例数。")
    failure_rate: float = Field(description="失败率（0-1）。")
    high_risk_count: int = Field(description="高风险失败数。")


class ReportSlice(BaseModel):
    """逐切片结果：按风险档/学习阶段等切片聚合。"""

    slice_tag: str = Field(description="切片标签。")
    dimension: EvaluationDimension = Field(description="维度。")
    sut_id: str = Field(description="被测系统。")
    metric_id: str = Field(description="指标。")
    n: int = Field(description="样本量。")
    mean: float = Field(description="均值。")


class BlindReviewSummary(BaseModel):
    """盲评摘要：评审数、一致率、分歧与复核状态。"""

    reviewer_count: int = Field(default=0, description="评审者数。")
    item_count: int = Field(default=0, description="对照项数。")
    agreement: float = Field(default=0.0, description="简单一致率。")
    low_consistency_items: list[str] = Field(
        default_factory=list, description="低一致性项目（已标记复核）。"
    )
    auto_judge_primary: bool = Field(
        default=True, description="是否仅依赖自动裁判（False 表示有人工盲评参与）。"
    )


class ReleaseGateCheck(BaseModel):
    """发布门中的一项检查。"""

    check_id: str = Field(description="检查标识。")
    passed: bool = Field(description="是否通过。")
    detail: str = Field(description="检查详情（中文）。")


class ReleaseGateVerdict(BaseModel):
    """发布阈值判定结果：完整系统未达最低事实/安全/稳定性阈值时阻止发行。"""

    passed: bool = Field(description="整体是否通过。")
    checks: list[ReleaseGateCheck] = Field(default_factory=list, description="逐项检查。")
    blockers: list[str] = Field(default_factory=list, description="阻断原因（中文）。")


class EvaluationReport(BaseModel):
    """版本化评测报告（append-only：同一锁产生的新报告版本不覆盖旧版）。"""

    report_id: str = Field(description="报告标识。")
    report_version: str = Field(description="报告版本。")
    lock_id: str = Field(description="关联运行锁。")
    suite_id: str = Field(description="套件标识。")
    suite_version: str = Field(description="套件版本。")
    generated_at: str = Field(description="生成时间（ISO 8601）。")
    estimates: list[PointEstimate] = Field(default_factory=list, description="点估计。")
    comparisons: list[Comparison] = Field(default_factory=list, description="对比。")
    failure_stats: list[FailureStats] = Field(default_factory=list, description="失败率。")
    slices: list[ReportSlice] = Field(default_factory=list, description="逐切片。")
    blind_review_summary: BlindReviewSummary = Field(
        default_factory=BlindReviewSummary, description="盲评摘要。"
    )
    failure_cases: list[FailureCaseInfo] = Field(
        default_factory=list, description="代表性失败案例（可回溯重放）。"
    )
    cost_latency: dict[str, Any] = Field(
        default_factory=dict, description="成本与时延汇总。"
    )
    threshold_verdict: ReleaseGateVerdict | None = Field(
        default=None, description="发布阈值判定（Issue 41 消费）。"
    )
    reproduction_prefix: str = Field(
        default="BridGes evaluate replay", description="一键重放命令前缀。"
    )


class ReleaseThreshold(BaseModel):
    """发布阈值：最低事实、安全与稳定性阈值（供 Issue 41 发布门接入）。"""

    threshold_id: str = Field(description="阈值标识。")
    version: str = Field(description="阈值版本。")
    minimums: list[ThresholdMinimum] = Field(
        default_factory=list, description="各指标最低分。"
    )
    max_failure_rate: float = Field(
        default=0.1, ge=0.0, le=1.0, description="最高允许失败率。"
    )
    max_high_risk_failures: int = Field(
        default=0, ge=0, description="最高允许高风险失败数（默认 0）。"
    )
    stability_tolerance: dict[str, float] = Field(
        default_factory=dict,
        description="双跑指标允许差异容差（指标 -> 绝对差上限）。",
    )


class ThresholdMinimum(BaseModel):
    metric_id: str = Field(description="指标标识。")
    dimension: EvaluationDimension = Field(description="维度。")
    min_value: float = Field(description="最低分。")


__all__ = [
    "EvaluationDimension",
    "SUTKind",
    "SuiteStatus",
    "CaseRunStatus",
    "SuiteInvalidationTrigger",
    "LicenseRecord",
    "DataManifestEntry",
    "ScoringAnchor",
    "ScoringScaleItem",
    "ScoringScale",
    "ExpectedArtifactField",
    "ExpectedArtifactSchema",
    "EvalBudget",
    "TaskDefinition",
    "DataCard",
    "ModelSkillPin",
    "RunMatrixEntry",
    "SuiteRunMatrix",
    "SuiteDefinition",
    "EvalCaseContext",
    "EvalTurn",
    "AutoAssertion",
    "EvalCase",
    "ToolCallRecord",
    "ArtifactRef",
    "MetricValue",
    "AutoAssertionResult",
    "JudgeKind",
    "JudgeScore",
    "FailureCaseInfo",
    "CaseResult",
    "SuiteRunLock",
    "BlindReviewItem",
    "BlindReviewSubmission",
    "BlindReviewSet",
    "PointEstimate",
    "Comparison",
    "FailureStats",
    "ReportSlice",
    "BlindReviewSummary",
    "ReleaseGateCheck",
    "ReleaseGateVerdict",
    "EvaluationReport",
    "ReleaseThreshold",
    "ThresholdMinimum",
    "now_iso",
]
