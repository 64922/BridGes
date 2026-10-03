"""知识范围映射、范围核验与辅助预习的持久节点（改进工单 31）。

配方与 ``docs/workflow/study-workflow.md`` 第 4 节一致：

    study.map → study.verify_scope → study.preview

- ``study.map`` 建立核心概念、关系、应用与易错点，逐项绑定书页片段；
  实质教学片段必须关联知识点或有依据排除理由，不以标题为唯一键。
- ``study.verify_scope`` 分开裁决：确定性结构门（``study.scope_structure``）
  与关键定义/公式/关系的内容核对门（``study.scope_content``）。结构完整
  不能代替内容正确；矛盾时回到受影响映射修复，不靠生成更多知识点凑覆盖。
- ``study.preview`` 在通过核验的范围上按密度生成阅读引导问题；问题引用
  知识点稳定 ID 与范围版本，不要求用户当场作答。

每个节点在共享执行内核里提交类型化产物与完成收据；恢复先读收据/按输入键
回填产物，只有消息与范围/问题在同一终态事务提交后才进入辅导。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bridges.contracts.study import (
    StudyContentCheck,
    StudyCoverageEntry,
    StudyQuestion,
    StudyScope,
    StudyUnit,
)
from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
    RecoveryPolicy,
)
from bridges.kernel.executor import GateHandler, NodeKernel
from bridges.kernel.registry import RecipeRegistry
from bridges.study.kernel import (
    _DEFINITION_PATTERN,
    NodeFailureError,
    StoppedError,
    SupersededError,
    _digest,
    critical_symbol_kinds,
)

#: 知识范围协议版本：映射/核验/预习规则（提示、结构与门）变化时递增，
#: 旧产物按输入键自然不被复用。
SCOPE_PROTOCOL_VERSION = "study-scope-v1"

#: 节点名（进度事件、失败定位、产物身份与质量门）。
NODE_MAP = "study.map"
NODE_VERIFY_SCOPE = "study.verify_scope"
NODE_PREVIEW = "study.preview"

#: 质量门：结构门与内容正确性门分开；预习覆盖门独立。
GATE_SCOPE_STRUCTURE = "study.scope_structure"
GATE_SCOPE_CONTENT = "study.scope_content"
GATE_PREVIEW_COVERAGE = "study.preview_coverage"
SCOPE_GATES: frozenset[str] = frozenset(
    {GATE_SCOPE_STRUCTURE, GATE_SCOPE_CONTENT, GATE_PREVIEW_COVERAGE}
)

#: 门失败码（领域层据此决定一次性修复还是终止）。
GATE_SCOPE_INCOMPLETE = "study_scope_incomplete"
GATE_SCOPE_CONFLICT = "study_scope_content_conflict"
GATE_SCOPE_UNVERIFIED = "study_scope_content_unverified"
GATE_PREVIEW_INCOMPLETE = "study_preview_incomplete"

SCOPE_RECIPE_VERSION = "study-scope-recipe-v1"
MAPPING_RECIPE_ID = "study-scope-mapping"
PREVIEW_RECIPE_ID = "study-scope-preview"

_NODE_RECIPE_IDS: dict[str, str] = {
    NODE_MAP: MAPPING_RECIPE_ID,
    NODE_VERIFY_SCOPE: MAPPING_RECIPE_ID,
    NODE_PREVIEW: PREVIEW_RECIPE_ID,
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
SCOPE_CAPABILITY_VERSIONS: dict[str, str] = {
    "study.map": "study-map-v1",
    "study.verify_scope": "study-verify-scope-v1",
    "study.preview": "study-preview-v1",
}


def assign_unit_id(kind: str, title: str, fragment_ids: Sequence[str]) -> str:
    """由知识点内容与支持片段确定性生成稳定 ID。

    不使用标题作为唯一键：同名概念落在不同片段上得到不同 ID；同一知识点
    在重试/重放中保持同一 ID。页内容变化时其支持片段 ID 变化，生成新 ID。
    """
    material = "\x1f".join(
        (kind, title, ",".join(sorted(fragment_ids)))
    )
    return "ku_" + sha256(material.encode("utf-8")).hexdigest()[:16]


def _coverage_entry_payload(entry: StudyCoverageEntry | Mapping[str, Any]) -> dict[str, Any]:
    return entry.model_dump() if isinstance(entry, StudyCoverageEntry) else dict(entry)


def coverage_digest(
    units: Sequence[StudyUnit],
    coverage: Sequence[StudyCoverageEntry | Mapping[str, Any]],
) -> str:
    return _digest(
        {
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "title": unit.title,
                    "kind": unit.kind,
                    "fragment_ids": list(unit.fragment_ids),
                    "core": unit.core,
                }
                for unit in units
            ],
            "coverage": [_coverage_entry_payload(entry) for entry in coverage],
        }
    )


def scope_version_id(
    material_hash: str,
    units: Sequence[StudyUnit],
    coverage: Sequence[StudyCoverageEntry | Mapping[str, Any]],
) -> str:
    """范围版本 ID 由材料与覆盖内容确定；材料不变时版本稳定。"""
    material = f"{SCOPE_PROTOCOL_VERSION}\x1f{material_hash}\x1f{coverage_digest(units, coverage)}"
    return "scope-" + sha256(material.encode("utf-8")).hexdigest()[:16]


def preview_bounds(units: Sequence[StudyUnit]) -> tuple[int, int]:
    """按知识密度给出预习问题数量下界与上界。

    每个核心知识点都要被覆盖，一题可覆盖多个相关知识点；问题数量随
    核心知识点数量变化，而非固定题数。
    """
    core = sum(1 for unit in units if unit.core)
    total = core or len(units)
    if total <= 0:
        return 0, 0
    minimum = max(1, (total + 2) // 3)
    maximum = max(minimum, min(8, total))
    return minimum, maximum


def needs_content_check(unit: StudyUnit, fragment_texts: Mapping[str, str]) -> bool:
    """关键定义/公式与关系必须与原文核对；普通描述性概念不额外调用。"""
    if unit.kind == "relation":
        return True
    for fragment_id in unit.fragment_ids:
        text = fragment_texts.get(fragment_id, "")
        if critical_symbol_kinds(text) or _DEFINITION_PATTERN.search(text):
            return True
    return False


def check_scope_structure(
    payload: Mapping[str, Any],
) -> list[dict[str, str]]:
    """确定性结构检查：覆盖完整、引用合法、排除有理由、ID 稳定且唯一。"""
    problems: list[dict[str, str]] = []
    fragments = [str(item) for item in payload.get("fragment_ids", [])]
    units_payload = [item for item in payload.get("units", []) if isinstance(item, Mapping)]
    coverage_payload = [
        item for item in payload.get("coverage", []) if isinstance(item, Mapping)
    ]
    unit_ids = [str(item.get("unit_id", "")) for item in units_payload]
    if not units_payload:
        problems.append({"code": "scope_empty", "detail": "知识范围为空，未建立任何知识点。"})
    if any(not unit_id for unit_id in unit_ids):
        problems.append({"code": "unit_id_missing", "detail": "存在未分配稳定 ID 的知识点。"})
    if len(set(unit_ids)) != len(unit_ids):
        problems.append(
            {"code": "unit_id_duplicate", "detail": "知识点稳定 ID 重复，需合并或补充依据。"}
        )
    fragment_set = set(fragments)
    coverage_ids = [str(item.get("fragment_id", "")) for item in coverage_payload]
    if len(set(coverage_ids)) != len(coverage_ids):
        problems.append(
            {"code": "coverage_duplicate", "detail": "覆盖矩阵存在重复片段。"}
        )
    missing = fragment_set - set(coverage_ids)
    foreign = set(coverage_ids) - fragment_set
    if missing:
        problems.append(
            {
                "code": "coverage_missing",
                "detail": "以下实质片段没有知识点关系，也没有排除理由："
                + "、".join(sorted(missing)),
            }
        )
    if foreign:
        problems.append(
            {
                "code": "coverage_foreign",
                "detail": "覆盖矩阵引用了范围之外的片段：" + "、".join(sorted(foreign)),
            }
        )
    mapped: dict[str, set[str]] = {}
    for entry in coverage_payload:
        fragment_id = str(entry.get("fragment_id", ""))
        entry_units = [str(item) for item in entry.get("unit_ids", [])]
        unknown = [unit_id for unit_id in entry_units if unit_id not in set(unit_ids)]
        if unknown:
            problems.append(
                {
                    "code": "coverage_unknown_unit",
                    "detail": f"片段 {fragment_id} 关联了不存在的知识点："
                    + "、".join(unknown),
                }
            )
            continue
        reason = str(entry.get("exclusion_reason", "")).strip()
        if not entry_units and len(reason) < 4:
            problems.append(
                {
                    "code": "exclusion_without_reason",
                    "detail": f"片段 {fragment_id} 既未关联知识点，也没有有依据的排除理由。",
                }
            )
            continue
        mapped.setdefault(fragment_id, set()).update(entry_units)
    for unit in units_payload:
        unit_id = str(unit.get("unit_id", ""))
        unit_fragments = [str(item) for item in unit.get("fragment_ids", [])]
        if not unit_fragments:
            problems.append(
                {"code": "unit_without_evidence", "detail": f"知识点 {unit_id} 没有支持片段。"}
            )
            continue
        for fragment_id in unit_fragments:
            if fragment_id not in fragment_set:
                problems.append(
                    {
                        "code": "unit_foreign_fragment",
                        "detail": f"知识点 {unit_id} 引用了范围之外的片段 {fragment_id}。",
                    }
                )
            elif unit_id not in mapped.get(fragment_id, set()):
                problems.append(
                    {
                        "code": "coverage_mismatch",
                        "detail": f"片段 {fragment_id} 的覆盖矩阵未关联知识点 {unit_id}。",
                    }
                )
    if payload.get("repair_unit_limit") is not None:
        limit = int(payload["repair_unit_limit"])
        if len(units_payload) > limit:
            problems.append(
                {
                    "code": "repair_unit_added",
                    "detail": "修复不得通过新增知识点扩大范围；请修正受影响知识点的依据。",
                }
            )
    return problems


class _MappedUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    kind: Literal["concept", "relation", "application", "misconception"] = "concept"
    fragment_ids: list[str] = Field(min_length=1)
    core: bool = True


class _Exclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fragment_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class _Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[_MappedUnit] = Field(min_length=1)
    exclusions: list[_Exclusion] = Field(default_factory=list)


class _ContentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    unit_id: str = Field(min_length=1)
    status: Literal["consistent", "conflict", "insufficient"]
    detail: str = ""


class _ContentChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[_ContentCheck] = Field(default_factory=list)


class _PreviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)


class _PreviewQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[_PreviewQuestion] = Field(min_length=1)


@dataclass(frozen=True)
class ScopeFragment:
    """映射输入中的一个实质教学片段（已按识别质量与用户补录过滤）。"""

    fragment_id: str
    page_ordinal: int
    kind: str
    position: str
    text: str
    source: str


@dataclass(frozen=True)
class ScopeMaterial:
    """一次范围映射的不可变材料快照（身份 + 片段内容）。"""

    material_hash: str
    page_object_ids: tuple[str, ...]
    fragments: tuple[ScopeFragment, ...]

    @classmethod
    def build(
        cls,
        *,
        pages: Sequence[Any],
        fragments: Sequence[ScopeFragment],
    ) -> ScopeMaterial:
        material = [
            {
                "ordinal": page.ordinal,
                "object_id": page.object_id,
                "content_hash": page.content_hash,
            }
            for page in pages
        ]
        fragment_payload = [
            {
                "fragment_id": item.fragment_id,
                "page": item.page_ordinal,
                "kind": item.kind,
                "position": item.position,
                "text": item.text,
                "source": item.source,
            }
            for item in fragments
        ]
        return cls(
            material_hash=_digest({"pages": material, "fragments": fragment_payload}),
            page_object_ids=tuple(str(page.object_id) for page in pages),
            fragments=tuple(fragments),
        )

    def fragment_texts(self) -> dict[str, str]:
        return {item.fragment_id: item.text for item in self.fragments}


@dataclass
class ScopeMapOutcome:
    """一次范围映射的结果：通过核验的范围，或可修复/终止的失败。"""

    scope: StudyScope | None
    failure_code: str = ""
    failure_message: str = ""
    failure_detail: dict[str, Any] = field(default_factory=dict)


def _map_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "protocol": SCOPE_PROTOCOL_VERSION,
            "material": inputs.prior_digest,
        }
    )


def _verify_key(inputs: RecipeInputs) -> str:
    mapped = inputs.artifacts[NODE_MAP]
    return _digest(
        {
            "protocol": SCOPE_PROTOCOL_VERSION,
            "map": mapped.content_hash,
        }
    )


def _preview_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "protocol": SCOPE_PROTOCOL_VERSION,
            "scope": inputs.prior_digest,
        }
    )


def build_scope_mapping_recipe() -> RecipeDefinition:
    """建立知识点并核验范围：结构门与内容正确性门分开。"""
    return RecipeDefinition(
        recipe_id=MAPPING_RECIPE_ID,
        recipe_version=SCOPE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_MAP,
                capability="study.map",
                artifact_type="study.scope_mapping",
                capability_version=SCOPE_CAPABILITY_VERSIONS[NODE_MAP],
                input_key=_map_key,
                recovery=RecoveryPolicy.RETRY_NODE,
                description="核心概念、关系、应用与易错点；逐项绑定书页片段。",
            ),
            NodeSpec(
                name=NODE_VERIFY_SCOPE,
                capability="study.verify_scope",
                artifact_type="study.scope_verification",
                capability_version=SCOPE_CAPABILITY_VERSIONS[NODE_VERIFY_SCOPE],
                input_key=_verify_key,
                depends_on=(NODE_MAP,),
                required_gates=(GATE_SCOPE_STRUCTURE, GATE_SCOPE_CONTENT),
                recovery=RecoveryPolicy.ASK_INPUT,
                description="结构覆盖完整；关键定义/公式/关系与原文核对一致。",
            ),
        ),
    )


def build_scope_preview_recipe() -> RecipeDefinition:
    """通过范围核验后按密度生成阅读引导问题。"""
    return RecipeDefinition(
        recipe_id=PREVIEW_RECIPE_ID,
        recipe_version=SCOPE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PREVIEW,
                capability="study.preview",
                artifact_type="study.preview_questions",
                capability_version=SCOPE_CAPABILITY_VERSIONS[NODE_PREVIEW],
                input_key=_preview_key,
                required_gates=(GATE_PREVIEW_COVERAGE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按知识密度生成覆盖核心点的阅读引导问题，引用知识点 ID。",
            ),
        ),
    )


def study_scope_recipe_registry() -> RecipeRegistry:
    """登记范围能力、质量门与两个配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=SCOPE_CAPABILITY_VERSIONS.keys(),
        gates=SCOPE_GATES,
    )
    registry.register(build_scope_mapping_recipe())
    registry.register(build_scope_preview_recipe())
    return registry


def _mapping_problems(
    mapping: _Mapping, material: ScopeMaterial
) -> list[dict[str, str]]:
    """模型映射输出到覆盖矩阵的确定性校验（先于内容核对）。"""
    problems: list[dict[str, str]] = []
    valid_fragments = {item.fragment_id for item in material.fragments}
    seen_ids: set[str] = set()
    for unit in mapping.units:
        unknown = set(unit.fragment_ids) - valid_fragments
        if unknown:
            problems.append(
                {
                    "code": "unit_foreign_fragment",
                    "detail": f"知识点「{unit.title}」引用了不存在的片段："
                    + "、".join(sorted(unknown)),
                }
            )
        unit_id = assign_unit_id(unit.kind, unit.title, unit.fragment_ids)
        if unit_id in seen_ids:
            problems.append(
                {
                    "code": "unit_id_duplicate",
                    "detail": f"知识点「{unit.title}」重复出现，需合并为一条。",
                }
            )
        seen_ids.add(unit_id)
    for exclusion in mapping.exclusions:
        if exclusion.fragment_id not in valid_fragments:
            problems.append(
                {
                    "code": "exclusion_foreign",
                    "detail": f"排除理由引用了不存在的片段 {exclusion.fragment_id}。",
                }
            )
    return problems


def _build_coverage(
    mapping: _Mapping, material: ScopeMaterial
) -> tuple[list[StudyUnit], list[dict[str, Any]]]:
    units = [
        StudyUnit(
            unit_id=assign_unit_id(item.kind, item.title, item.fragment_ids),
            title=item.title,
            kind=item.kind,
            fragment_ids=list(dict.fromkeys(item.fragment_ids)),
            core=item.core,
        )
        for item in mapping.units
    ]
    exclusions = {
        item.fragment_id: item.reason
        for item in mapping.exclusions
        if item.reason.strip()
    }
    coverage: list[dict[str, Any]] = []
    for fragment in material.fragments:
        entry_units = [
            unit.unit_id for unit in units if fragment.fragment_id in unit.fragment_ids
        ]
        coverage.append(
            {
                "fragment_id": fragment.fragment_id,
                "unit_ids": entry_units,
                "exclusion_reason": (
                    "" if entry_units else exclusions.get(fragment.fragment_id, "")
                ),
            }
        )
    return units, coverage


def _structure_execution(
    invocation: NodeInvocation,
    *,
    payload: dict[str, Any],
    source_refs: Sequence[str],
    read_scope: str,
    requirement_coverage: Sequence[Mapping[str, Any]] = (),
    unconfirmed: Sequence[str] = (),
) -> NodeExecution:
    return NodeExecution(
        artifact=_artifact(
            invocation,
            trust_state=ArtifactTrust.EVIDENCE_BOUND,
            payload=payload,
            source_refs=source_refs,
            read_scope=read_scope,
            requirement_coverage=requirement_coverage,
            unconfirmed=unconfirmed,
        ),
        verdict=QualityVerdict.PASS,
        status=NodeReceiptStatus.COMPLETED,
        detail={"units": len(payload.get("units", []))},
    )


def _failure_execution(
    invocation: NodeInvocation,
    *,
    code: str,
    message: str,
    retryable: bool,
    payload: dict[str, Any] | None = None,
) -> NodeExecution:
    artifact = _artifact(
        invocation,
        trust_state=ArtifactTrust.INVALIDATED,
        payload=payload or {},
        error={"code": code, "message": message, "retryable": retryable},
    )
    return NodeExecution(
        artifact=artifact,
        verdict=(
            QualityVerdict.REPAIRABLE_FAILURE if retryable else QualityVerdict.BLOCKED
        ),
        status=NodeReceiptStatus.FAILED,
        detail={"code": code, "message": message, "retryable": retryable},
        stop_recipe=True,
        recovery=invocation.spec.recovery,
    )


def _artifact(
    invocation: NodeInvocation,
    *,
    trust_state: ArtifactTrust,
    payload: dict[str, Any],
    source_refs: Sequence[str] = (),
    read_scope: str = "",
    requirement_coverage: Sequence[Mapping[str, Any]] = (),
    unconfirmed: Sequence[str] = (),
    error: dict[str, Any] | None = None,
) -> NodeArtifact:
    inputs = invocation.inputs
    return NodeArtifact.build(
        account_id=inputs.account_id,
        conversation_id=inputs.conversation_id,
        run_id=inputs.run_id,
        task_id=inputs.task_id,
        task_version=inputs.task_version,
        recipe_id=_NODE_RECIPE_IDS[invocation.spec.name],
        recipe_version=SCOPE_RECIPE_VERSION,
        node=invocation.spec.name,
        artifact_type=invocation.spec.artifact_type,
        capability_version=invocation.spec.capability_version,
        trust_state=trust_state,
        input_key=invocation.spec.input_key(inputs),
        input_deps=(),
        source_refs=tuple(source_refs),
        read_scope=read_scope,
        requirement_coverage=tuple(dict(item) for item in requirement_coverage),
        unconfirmed=tuple(unconfirmed),
        error=error,
        payload=payload,
        now=datetime.now(UTC),
    )


class StudyScopeNodeFlow:
    """范围映射、核验与预习节点的确定性编排执行体（模型经 invoke 注入）。"""

    def __init__(
        self,
        *,
        invoke: Callable[[str, dict[str, Any]], dict[str, Any]],
        compile_context: (
            Callable[[str, dict[str, Any]], tuple[list[dict[str, str]] | None, bool]] | None
        ) = None,
    ) -> None:
        self._invoke = invoke
        #: 预习自然语言生成经统一上下文编译（表达策略 + 采纳证据 + 预算门）；
        #: 缺省时退回普通提示，仍由网关的最终载荷门兜底。
        self._compile_context = compile_context
        self._material: ScopeMaterial | None = None
        self._repair: dict[str, Any] | None = None
        self._scope: StudyScope | None = None
        self._policy_block = ""

    # -- 编排接口 ---------------------------------------------------------

    def configure_mapping(
        self,
        material: ScopeMaterial,
        *,
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        self._material = material
        self._repair = dict(repair) if repair else None

    def configure_preview(self, scope: StudyScope, *, policy_block: str = "") -> None:
        self._scope = scope
        self._policy_block = policy_block

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_MAP: self._run_map,
            NODE_VERIFY_SCOPE: self._run_verify_scope,
            NODE_PREVIEW: self._run_preview,
        }[invocation.spec.name]
        return handler(invocation)

    # -- 映射 -------------------------------------------------------------

    def _run_map(self, invocation: NodeInvocation) -> NodeExecution:
        material = self._material
        assert material is not None, "范围映射缺少材料"
        fragments = [
            {
                "fragment_id": item.fragment_id,
                "page": item.page_ordinal,
                "kind": item.kind,
                "position": item.position,
                "text": item.text,
                "source": item.source,
            }
            for item in material.fragments
        ]
        instruction = (
            '只输出 JSON 对象 {"units":[{"title":"知识点","kind":'
            '"concept|relation|application|misconception","fragment_ids":["片段ID"],'
            '"core":true}],"exclusions":[{"fragment_id":"片段ID","reason":"排除理由"}]}。'
            "只依据给定片段提炼知识点：概念、关系、应用与易错点；每条知识点必须"
            "引用真实片段 ID，覆盖实现该知识点所需的全部依据。同名知识点若依据"
            "不同片段，必须分别列出。每个实质教学片段要么被至少一条知识点引用，"
            "要么在 exclusions 中给出有依据的排除理由（如页眉页码、非教学内容）；"
            "不得遗漏，也不得为凑覆盖新增没有直接依据的知识点。"
        )
        repair = self._repair
        if repair:
            instruction += (
                " 上一版范围核验未通过："
                + str(repair.get("message", ""))
                + "。只修正受影响的知识点或排除理由，"
                "不得增加知识点数量，也不得删除有依据的知识点；"
                "若原文确实不支持该结论，改为与原文一致的表述。"
            )
        prompt = instruction + json.dumps(fragments, ensure_ascii=False)
        raw = self._invoke(
            "qwen_structured_output",
            {"task": NODE_MAP, "prompt": prompt, "temperature": 0.01},
        )
        try:
            mapping = _Mapping.model_validate(raw)
        except ValidationError:
            return _failure_execution(
                invocation,
                code="study_map_invalid",
                message="知识范围映射结构不完整，请重试。",
                retryable=True,
            )
        problems = _mapping_problems(mapping, material)
        units, coverage = _build_coverage(mapping, material)
        version_id = scope_version_id(material.material_hash, units, coverage)
        fragment_lookup = material.fragment_texts()
        check_unit_ids = [
            unit.unit_id
            for unit in units
            if needs_content_check(unit, fragment_lookup)
        ]
        payload = {
            "scope_version_id": version_id,
            "protocol_version": SCOPE_PROTOCOL_VERSION,
            "material_hash": material.material_hash,
            "page_object_ids": list(material.page_object_ids),
            "fragment_ids": [item.fragment_id for item in material.fragments],
            "units": [unit.model_dump() for unit in units],
            "coverage": coverage,
            "check_unit_ids": check_unit_ids,
            "map_problems": problems,
            "repair_unit_limit": (
                int(repair["prior_unit_count"]) if repair and "prior_unit_count" in repair else None
            ),
        }
        return _structure_execution(
            invocation,
            payload=payload,
            source_refs=[item.fragment_id for item in material.fragments],
            read_scope="本节通过识别门的实质教学片段（照片原文与用户补录）",
        )

    # -- 核验 -------------------------------------------------------------

    def _run_verify_scope(self, invocation: NodeInvocation) -> NodeExecution:
        material = self._material
        assert material is not None, "范围核验缺少材料"
        mapped = invocation.dependencies[NODE_MAP].payload
        structure_problems = list(mapped.get("map_problems", []))
        structure_problems.extend(check_scope_structure(mapped))
        checks: list[dict[str, Any]] = []
        if not structure_problems:
            check_ids = [str(item) for item in mapped.get("check_unit_ids", [])]
            if check_ids:
                units_by_id = {
                    str(item.get("unit_id")): item for item in mapped.get("units", [])
                }
                fragment_texts = material.fragment_texts()
                check_input = []
                for unit_id in check_ids:
                    unit = units_by_id.get(unit_id)
                    if unit is None:
                        continue
                    check_input.append(
                        {
                            "unit_id": unit_id,
                            "title": unit.get("title", ""),
                            "kind": unit.get("kind", ""),
                            "fragments": [
                                {
                                    "fragment_id": fragment_id,
                                    "text": fragment_texts.get(fragment_id, ""),
                                }
                                for fragment_id in unit.get("fragment_ids", [])
                            ],
                        }
                    )
                prompt = (
                    '只输出 JSON {"checks":[{"unit_id":"知识点ID","status":'
                    '"consistent|conflict|insufficient","detail":"简短说明"}]}。'
                    "逐条核对知识点标题与类别是否被所列书页原文支持：定义、公式、"
                    "关系必须与原文完全一致（含负号、上下标、单位与限定条件）；"
                    "原文不足以支持或你无法确认时填 insufficient；不得引入书页之外"
                    "的知识，也不得改写原文。"
                    + json.dumps(check_input, ensure_ascii=False)
                )
                raw = self._invoke(
                    "qwen_structured_output",
                    {"task": NODE_VERIFY_SCOPE, "prompt": prompt, "temperature": 0.01},
                )
                try:
                    result = _ContentChecks.model_validate(raw)
                except ValidationError:
                    checks = [
                        {
                            "unit_id": unit_id,
                            "status": "insufficient",
                            "detail": "关键内容核对结果结构不完整",
                            "fragment_ids": [],
                        }
                        for unit_id in check_ids
                    ]
                else:
                    returned = {item.unit_id: item for item in result.checks}
                    for item in result.checks:
                        unit = units_by_id.get(item.unit_id)
                        checks.append(
                            {
                                "unit_id": item.unit_id,
                                "status": item.status,
                                "detail": item.detail,
                                "fragment_ids": list(
                                    unit.get("fragment_ids", []) if unit else []
                                ),
                            }
                        )
                    for unit_id in check_ids:
                        if unit_id not in returned:
                            unit = units_by_id.get(unit_id) or {}
                            checks.append(
                                {
                                    "unit_id": unit_id,
                                    "status": "insufficient",
                                    "detail": "核验未覆盖该知识点",
                                    "fragment_ids": list(unit.get("fragment_ids", [])),
                                }
                            )
        payload = {
            **mapped,
            "structure": {
                "ok": not structure_problems,
                "problems": structure_problems,
            },
            "content_checks": checks,
        }
        return _structure_execution(
            invocation,
            payload=payload,
            source_refs=list(mapped.get("fragment_ids", [])),
            read_scope="本节全部实质片段的结构覆盖与关键内容核对",
            unconfirmed=[
                problem["detail"] for problem in structure_problems
            ]
            + [
                f"{item['unit_id']}：{item['detail']}"
                for item in checks
                if item["status"] != "consistent"
            ],
            requirement_coverage=[
                {"requirement": "结构覆盖完整", "covered": not structure_problems},
                {
                    "requirement": "关键定义/公式/关系与原文一致",
                    "covered": bool(checks) and all(
                        item["status"] == "consistent" for item in checks
                    ),
                },
            ],
        )

    # -- 预习 -------------------------------------------------------------

    def _run_preview(self, invocation: NodeInvocation) -> NodeExecution:
        scope = self._scope
        assert scope is not None, "预习缺少有效范围版本"
        minimum, maximum = preview_bounds(scope.units)
        units_payload = [
            {
                "unit_id": unit.unit_id,
                "title": unit.title,
                "kind": unit.kind,
                "core": unit.core,
            }
            for unit in scope.units
        ]
        system_prompt = (
            "你是教材预习助教。只依据已核验的知识范围生成阅读引导问题，"
            "不要求学生现在作答，不泄露答案。问题引用给定知识点 ID；"
            "每个核心知识点至少被一个问题覆盖；一题可覆盖多个相关知识点。"
            f"问题数量必须在 {minimum} 到 {maximum} 之间（随知识密度变化），"
            "不固定题数。用自然中文，不输出知识点 ID 以外的编号或链接。"
        )
        if self._policy_block:
            system_prompt = system_prompt + "\n" + self._policy_block
        data = {
            "scope_version_id": scope.scope_version_id,
            "minimum_questions": minimum,
            "maximum_questions": maximum,
            "units": units_payload,
        }
        if self._compile_context is not None:
            compiled, adopted = self._compile_context(system_prompt, data)
            if not adopted:
                return _failure_execution(
                    invocation,
                    code="study_preview_budget",
                    message="预习问题所需范围超出当前模型上下文预算，请缩小本节范围后重试。",
                    retryable=True,
                )
            raw = self._invoke(
                "qwen_structured_output",
                {
                    "task": NODE_PREVIEW,
                    "messages": compiled,
                    "max_tokens": 1024,
                    "temperature": 0.2,
                },
            )
        else:
            prompt = (
                '只输出 JSON {"questions":[{"question":"阅读引导问题",'
                '"unit_ids":["知识点ID"]}]}。'
                + json.dumps(data, ensure_ascii=False)
            )
            raw = self._invoke(
                "qwen_structured_output",
                {"task": NODE_PREVIEW, "prompt": prompt, "temperature": 0.2},
            )
        try:
            parsed = _PreviewQuestions.model_validate(raw)
        except ValidationError:
            return _failure_execution(
                invocation,
                code=GATE_PREVIEW_INCOMPLETE,
                message="预习问题生成不完整，请重试。",
                retryable=True,
            )
        by_id = {unit.unit_id: unit for unit in scope.units}
        questions: list[StudyQuestion] = []
        texts: set[str] = set()
        for item in parsed.questions:
            unknown = [ref for ref in item.unit_ids if ref not in by_id]
            if unknown:
                return _failure_execution(
                    invocation,
                    code=GATE_PREVIEW_INCOMPLETE,
                    message="预习问题引用了范围之外的知识点，请重试。",
                    retryable=True,
                )
            if item.question in texts:
                return _failure_execution(
                    invocation,
                    code=GATE_PREVIEW_INCOMPLETE,
                    message="预习问题重复，请重试。",
                    retryable=True,
                )
            texts.add(item.question)
            questions.append(
                StudyQuestion(
                    question=item.question,
                    unit_ids=list(dict.fromkeys(item.unit_ids)),
                    unit_titles=[by_id[ref].title for ref in dict.fromkeys(item.unit_ids)],
                    scope_version_id=scope.scope_version_id,
                )
            )
        problems = preview_coverage_problems(questions, scope, minimum, maximum)
        if problems:
            return _failure_execution(
                invocation,
                code=GATE_PREVIEW_INCOMPLETE,
                message=problems[0],
                retryable=True,
            )
        return _structure_execution(
            invocation,
            payload={
                "scope_version_id": scope.scope_version_id,
                "minimum": minimum,
                "maximum": maximum,
                "questions": [item.model_dump() for item in questions],
            },
            source_refs=[unit.unit_id for unit in scope.units],
            read_scope="经核验的有效知识范围版本（不读取书页正文）",
            requirement_coverage=[
                {"requirement": "每个核心知识点被覆盖", "covered": True},
                {"requirement": "问题数量随密度且不要求作答", "covered": True},
            ],
        )


def preview_coverage_problems(
    questions: Sequence[StudyQuestion],
    scope: StudyScope,
    minimum: int,
    maximum: int,
) -> list[str]:
    """预习覆盖门的确定性检查（问题数量、核心点覆盖、引用合法）。"""
    problems: list[str] = []
    if not minimum:
        problems.append("知识范围为空，无法生成预习问题。")
        return problems
    if not (minimum <= len(questions) <= maximum):
        problems.append(
            f"预习问题数量 {len(questions)} 不在密度范围 {minimum}–{maximum} 内。"
        )
    unit_ids = {unit.unit_id for unit in scope.units}
    covered: set[str] = set()
    for question in questions:
        if not question.unit_ids or set(question.unit_ids) - unit_ids:
            problems.append("预习问题引用了范围之外的知识点。")
        if question.scope_version_id != scope.scope_version_id:
            problems.append("预习问题未绑定当前有效范围版本。")
        covered.update(question.unit_ids)
    missing = [
        unit.title for unit in scope.units if unit.core and unit.unit_id not in covered
    ]
    if missing:
        problems.append("预习问题未覆盖核心知识点：" + "、".join(missing))
    return problems


def _scope_structure_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    del invocation
    if execution.verdict is not QualityVerdict.PASS:
        return QualityGateResult(gate=GATE_SCOPE_STRUCTURE, verdict=QualityVerdict.PASS)
    structure = execution.artifact.payload.get("structure", {})
    problems = list(structure.get("problems", [])) if isinstance(structure, Mapping) else []
    if problems:
        detail = "；".join(
            str(item.get("detail", "")) for item in problems[:3] if isinstance(item, Mapping)
        )
        return QualityGateResult(
            gate=GATE_SCOPE_STRUCTURE,
            verdict=QualityVerdict.NEED_INPUT,
            code=GATE_SCOPE_INCOMPLETE,
            message=f"知识范围结构核验未通过：{detail}",
            detail={"problems": problems},
        )
    return QualityGateResult(gate=GATE_SCOPE_STRUCTURE, verdict=QualityVerdict.PASS)


def _scope_content_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    del invocation
    if execution.verdict is not QualityVerdict.PASS:
        return QualityGateResult(gate=GATE_SCOPE_CONTENT, verdict=QualityVerdict.PASS)
    checks = [
        item
        for item in execution.artifact.payload.get("content_checks", [])
        if isinstance(item, Mapping)
    ]
    conflicts = [item for item in checks if item.get("status") == "conflict"]
    unverified = [item for item in checks if item.get("status") == "insufficient"]
    failed = conflicts or unverified
    if not failed:
        return QualityGateResult(gate=GATE_SCOPE_CONTENT, verdict=QualityVerdict.PASS)
    detail = "；".join(
        f"{item.get('unit_id', '')}：{item.get('detail', '关键内容与原文不一致或无法确认')}"
        for item in failed
    )
    return QualityGateResult(
        gate=GATE_SCOPE_CONTENT,
        verdict=QualityVerdict.NEED_INPUT,
        code=GATE_SCOPE_CONFLICT if conflicts else GATE_SCOPE_UNVERIFIED,
        message=f"关键定义/公式/关系与书页原文核对未通过：{detail}",
        detail={"checks": checks},
    )


def _preview_coverage_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    del invocation
    if execution.verdict is not QualityVerdict.PASS:
        return QualityGateResult(gate=GATE_PREVIEW_COVERAGE, verdict=QualityVerdict.PASS)
    payload = execution.artifact.payload
    try:
        questions = [
            StudyQuestion.model_validate(item) for item in payload.get("questions", [])
        ]
    except ValidationError:
        return QualityGateResult(
            gate=GATE_PREVIEW_COVERAGE,
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code=GATE_PREVIEW_INCOMPLETE,
            message="预习问题结构不完整，请重试。",
        )
    minimum = int(payload.get("minimum", 0))
    maximum = int(payload.get("maximum", 0))
    if minimum and not (minimum <= len(questions) <= maximum):
        return QualityGateResult(
            gate=GATE_PREVIEW_COVERAGE,
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code=GATE_PREVIEW_INCOMPLETE,
            message="预习问题数量不符合知识密度，请重试。",
        )
    if not questions:
        return QualityGateResult(
            gate=GATE_PREVIEW_COVERAGE,
            verdict=QualityVerdict.REPAIRABLE_FAILURE,
            code=GATE_PREVIEW_INCOMPLETE,
            message="预习问题为空，请重试。",
        )
    return QualityGateResult(gate=GATE_PREVIEW_COVERAGE, verdict=QualityVerdict.PASS)


SCOPE_GATE_HANDLERS: dict[str, GateHandler] = {
    GATE_SCOPE_STRUCTURE: _scope_structure_gate,
    GATE_SCOPE_CONTENT: _scope_content_gate,
    GATE_PREVIEW_COVERAGE: _preview_coverage_gate,
}


class StudyScopeRecognition:
    """执行范围映射/核验/预习持久节点，产出可合并进 StudyState 的结果。"""

    def __init__(
        self,
        *,
        kernel: NodeKernel,
        flow: StudyScopeNodeFlow,
        account_id: str,
        conversation_id: str,
        run_id: str,
        user_message_id: str,
        user_content: str,
        stop_event: Any = None,
        event_sink: Callable[[str, str, int | None], None] | None = None,
    ) -> None:
        self._kernel = kernel
        self._flow = flow
        self._account_id = account_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._user_message_id = user_message_id
        self._user_content = user_content
        self._stop_event = stop_event
        self._event_sink = event_sink
        self._mapping_recipe = build_scope_mapping_recipe()
        self._preview_recipe = build_scope_preview_recipe()

    def map_scope(
        self,
        material: ScopeMaterial,
        *,
        repair: Mapping[str, Any] | None = None,
        prior_scope: StudyScope | None = None,
    ) -> ScopeMapOutcome:
        """建立并核验知识范围；失败时返回可修复/终止的失败详情。"""
        repair_digest = _digest(dict(repair)) if repair else ""
        self._flow.configure_mapping(material, repair=repair)
        result = self._kernel.execute(
            recipe=self._mapping_recipe,
            inputs=self._inputs(
                prior_digest=f"{material.material_hash}\x1f{repair_digest}"
            ),
            event_sink=self._event_sink,
            stop_event=self._stop_event,
        )
        self._raise_if_terminal(result)
        failure = result.failure
        if failure is not None:
            artifact = result.artifact(NODE_VERIFY_SCOPE) or result.artifact(NODE_MAP)
            detail = dict(artifact.payload) if artifact is not None else {}
            return ScopeMapOutcome(
                scope=None,
                failure_code=failure.code,
                failure_message=failure.message,
                failure_detail=detail,
            )
        verified = result.artifact(NODE_VERIFY_SCOPE)
        if verified is None:
            return ScopeMapOutcome(
                scope=None,
                failure_code="study_scope_missing",
                failure_message="知识范围核验产物缺失，请重试。",
            )
        return ScopeMapOutcome(scope=self._build_scope(verified.payload, prior_scope))

    def preview_scope(
        self, scope: StudyScope, *, policy_block: str = ""
    ) -> list[StudyQuestion]:
        """通过核验的范围上生成阅读引导问题；结构不合法即失败。"""
        self._flow.configure_preview(scope, policy_block=policy_block)
        result = self._kernel.execute(
            recipe=self._preview_recipe,
            inputs=self._inputs(prior_digest=scope.scope_version_id),
            event_sink=self._event_sink,
            stop_event=self._stop_event,
        )
        self._raise_if_terminal(result)
        failure = result.failure
        if failure is not None:
            raise NodeFailureError(
                failure.node, failure.code, failure.message, failure.retryable
            )
        artifact = result.artifact(NODE_PREVIEW)
        if artifact is None:
            raise NodeFailureError(
                NODE_PREVIEW,
                "study_preview_missing",
                "预习问题产物缺失，请重试。",
                True,
            )
        return [
            StudyQuestion.model_validate(item)
            for item in artifact.payload.get("questions", [])
        ]

    # -- 内部 -------------------------------------------------------------

    def _inputs(self, *, prior_digest: str) -> RecipeInputs:
        return RecipeInputs(
            account_id=self._account_id,
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            user_message_id=self._user_message_id,
            user_content=self._user_content,
            task_id=None,
            task_version=None,
            wait_identity=None,
            artifacts={},
            prior_digest=prior_digest,
        )

    def _build_scope(
        self, payload: Mapping[str, Any], prior_scope: StudyScope | None
    ) -> StudyScope:
        version = str(payload["scope_version_id"])
        if prior_scope is not None and prior_scope.scope_version_id == version:
            revision = prior_scope.revision
        else:
            revision = (prior_scope.revision + 1) if prior_scope else 1
        return StudyScope(
            scope_version_id=version,
            revision=revision,
            protocol_version=str(payload.get("protocol_version", SCOPE_PROTOCOL_VERSION)),
            material_hash=str(payload.get("material_hash", "")),
            page_object_ids=[
                str(item) for item in payload.get("page_object_ids", [])
            ],
            fragment_ids=[str(item) for item in payload.get("fragment_ids", [])],
            units=[
                StudyUnit.model_validate(item) for item in payload.get("units", [])
            ],
            coverage=[
                StudyCoverageEntry.model_validate(item)
                for item in payload.get("coverage", [])
            ],
            content_checks=[
                StudyContentCheck.model_validate(item)
                for item in payload.get("content_checks", [])
            ],
            verified=True,
        )

    def _raise_if_terminal(self, result: Any) -> None:
        if result.status is KernelStatus.STOPPED:
            raise StoppedError(result.stopped_at or NODE_MAP)
        if result.status is KernelStatus.REJECTED:
            raise SupersededError(result.rejection_code or "generation_superseded")
