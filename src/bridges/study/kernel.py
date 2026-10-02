"""学习书页识别的持久节点（改进工单 30）。

配方与 ``docs/workflow/study-workflow.md`` 第 3 节一致：

    study.validate_pages → study.recognize_page → study.verify_recognition
      → study.request_page_fix

每个节点只接收最小任务/版本/运行/节点引用、依赖产物与剩余预算；识别
保持 OCR（文字）与视觉（公式/图表/疑点）双路径，OCR 优化只在代表样本
实测通过后才启用。恢复由完成收据与按页输入键驱动：页内容未变时回填
已识别产物，只有未完成或内容变化的页重新识别；关键符号疑点（负号、
上下标、分子分母、单位、核心定义）不能靠置信阈值放行，保持材料待补充
并阻塞依赖它的出题。

本模块只提供节点执行体、配方与按页编排；提交守卫、产物/收据持久化与
事件投递由共享执行内核（``bridges.kernel``）负责。
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from bridges.contracts.study import StudyFragment, StudyPage, StudyUnclear
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

#: 书页识别协议版本：页内容指纹之外的识别合同（提示词、双路径、关键
#: 符号规则）变化时递增，旧产物按输入键自然不被复用。
STUDY_RECOGNITION_PROTOCOL_VERSION = "study-recognition-v2"

#: 节点名（进度事件、失败定位、产物身份与质量门）。
NODE_VALIDATE_PAGES = "study.validate_pages"
NODE_RECOGNIZE_PAGE = "study.recognize_page"
NODE_VERIFY_RECOGNITION = "study.verify_recognition"
NODE_REQUEST_PAGE_FIX = "study.request_page_fix"

#: 关键疑点质量门失败码（领域层据此把页保留为待补拍而不是普通失败）。
GATE_CRITICAL_EVIDENCE = "study_critical_evidence_unclear"

STUDY_PAGE_RECIPE_VERSION = "study-page-recipe-v1"

#: 三个独立配方（登记要求同一配方 ID 的定义不可变，节点集不同必须分开）。
VALIDATION_RECIPE_ID = "study-page-validation"
RECOGNITION_RECIPE_ID = "study-page-recognition"
FIX_RECIPE_ID = "study-page-fix"

#: 节点到配方 ID 的映射（产物身份与复用兼容都按配方 ID + 版本判定）。
_NODE_RECIPE_IDS: dict[str, str] = {
    NODE_VALIDATE_PAGES: VALIDATION_RECIPE_ID,
    NODE_RECOGNIZE_PAGE: RECOGNITION_RECIPE_ID,
    NODE_VERIFY_RECOGNITION: RECOGNITION_RECIPE_ID,
    NODE_REQUEST_PAGE_FIX: FIX_RECIPE_ID,
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
STUDY_PAGE_CAPABILITY_VERSIONS: dict[str, str] = {
    "study.validate_pages": "study-validate-pages-v1",
    "study.recognize_page": "study-recognize-page-v1",
    "study.verify_recognition": "study-verify-recognition-v1",
    "study.request_page_fix": "study-request-page-fix-v1",
}

#: 已登记质量门：关键符号疑点门（结构化裁决；模型不能自行宣布通过）。
STUDY_PAGE_GATES: frozenset[str] = frozenset({"study.critical_symbols"})

#: 关键符号类别（负号/上下标/分子分母/单位/核心定义）。命中只表示该片段
#: 属于不能仅凭置信阈值放行的范围；是否存在疑点仍由模型信号与双路径
#: 核对决定。正则要求算式/量值上下文，避免把单词连字符（如 well-known）
#: 或孤立字母误判成关键量。
_SIGN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[-−]\s*[0-9A-Za-zα-ωΑ-Ω(]|[-−]\s*[0-9(]"
)
_SCRIPT_PATTERN = re.compile(r"[_^]|[\u00b2\u00b3\u00b9\u2070-\u2079\u2080-\u2089]")
_FRACTION_PATTERN = re.compile(r"[A-Za-z0-9)\]}]\s*/\s*[A-Za-z0-9([{]")
_UNIT_PATTERN = re.compile(
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:m/s|km/s|mol/L|kg|mg|ms|min|km|cm|mm|mL|mol|Pa|Hz|°C|"
    r"g|m|s|h|A|V|W|J|N|K|L|%)(?![A-Za-z0-9])"
)
_DEFINITION_PATTERN = re.compile(r"定义|称为|叫做|记作")
#: 模型疑点描述里点名的关键类别；位置没有对应片段时也要按关键疑点阻塞。
_DOUBT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("负号/正负号", re.compile(r"负号|正负号|正负")),
    ("上下标", re.compile(r"上标|下标|指数")),
    ("分子分母", re.compile(r"分子|分母|分数线")),
    ("单位", re.compile(r"单位|量纲")),
    ("核心定义", re.compile(r"定义|概念|定理|公式")),
)


def critical_symbol_kinds(text: str) -> tuple[str, ...]:
    """返回文本中命中的关键符号类别（确定性、只读；不判定正确性）。"""
    kinds: list[str] = []
    if _SIGN_PATTERN.search(text):
        kinds.append("负号/正负号")
    if _SCRIPT_PATTERN.search(text):
        kinds.append("上下标")
    if _FRACTION_PATTERN.search(text):
        kinds.append("分子分母")
    if _UNIT_PATTERN.search(text):
        kinds.append("单位")
    if _DEFINITION_PATTERN.search(text):
        kinds.append("核心定义")
    return tuple(kinds)


def critical_doubt_kinds(text: str) -> tuple[str, ...]:
    """模型疑点描述里点名的关键类别（位置无片段也不能降级放行）。"""
    return tuple(name for name, pattern in _DOUBT_PATTERNS if pattern.search(text))


def _normalized(text: str) -> str:
    """去空白并统一大小写，用于双路径文本核对（不改变原文语义）。"""
    return re.sub(r"\s+", "", text).casefold()


def _paths_agree(vision_text: str, ocr_text: str) -> bool:
    """视觉片段是否在 OCR 文本中有对应内容（双向包含，去空白核对）。"""
    vision = _normalized(vision_text)
    ocr = _normalized(ocr_text)
    if not vision or not ocr:
        return False
    return vision in ocr or ocr in vision


class _RecognizedFragment(BaseModel):
    kind: Literal["text", "formula", "chart"]
    position: str = Field(min_length=1)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    #: 模型对图表/公式的推断（可选）；不会计入书页原文。
    interpretation: str = ""


class _Recognition(BaseModel):
    same_section: bool
    page_number: int | None = Field(default=None, ge=1)
    fragments: list[_RecognizedFragment] = Field(min_length=1)
    unclear: list[StudyUnclear] = Field(default_factory=list)


def _json_object_text(content: str) -> str:
    """抽取视觉模型正文里的 JSON 对象正文再交给原合同校验。

    视觉能力没有结构化输出开关，实测模型会把 JSON 包在 ```json 代码块里
    （issue 04 用原始教材页复现：整段合法 JSON 被围栏与一句说明包住，
    ``model_validate_json`` 因此以"书页结构识别不完整"失败）。围栏与
    前后说明是可确定的包装，剥掉后仍按原 pydantic 合同校验，识别结论
    本身不做任何猜测或补全。按第一个完整 JSON 对象截取（而不是"首个
    ``{`` 到最后一个 ``}``"），说明文字里再出现花括号也不会改变截取范围。
    """
    text = content.strip()
    if text.startswith("```"):
        _, _, text = text.partition("\n")
    start = text.find("{")
    if start < 0:
        return text
    try:
        _, end = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return text[start:]
    return text[start : start + end]


@dataclass(frozen=True)
class StudyPageCandidate:
    """一个待识别的书页照片候选（账户+会话作用域内已校验的附件引用）。"""

    object_id: str
    content_hash: str
    media_type: str


@dataclass(frozen=True)
class StudyRecognitionOutcome:
    """一次识别阶段的结果（已提交产物 + 未处理页）。"""

    #: 预算/批量限制下尚未识别的候选对象（非空时不宣布整节已读）。
    pending_object_ids: list[str]
    #: 存在疑点时的补拍/补录请求消息（无则为空）。
    fix_message: str = ""


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 配方与输入键（确定性复用边界）
# ---------------------------------------------------------------------------


def _validation_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "protocol": STUDY_RECOGNITION_PROTOCOL_VERSION,
            "prior": inputs.prior_digest,
            "content": inputs.user_content,
        }
    )


def _recognize_key(inputs: RecipeInputs) -> str:
    #: ``prior_digest`` 在按页执行时是页身份令牌（对象 ID + 内容指纹 +
    #: 识别协议版本）：内容未变的页命中历史产物，只有新页/变化页重新识别。
    return _digest(
        {
            "protocol": STUDY_RECOGNITION_PROTOCOL_VERSION,
            "page": inputs.prior_digest,
        }
    )


def _verify_key(inputs: RecipeInputs) -> str:
    recognize = inputs.artifacts[NODE_RECOGNIZE_PAGE]
    return _digest(
        {
            "protocol": STUDY_RECOGNITION_PROTOCOL_VERSION,
            "recognize": recognize.content_hash,
        }
    )


def _fix_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "protocol": STUDY_RECOGNITION_PROTOCOL_VERSION,
            "doubts": inputs.prior_digest,
        }
    )


def build_page_validation_recipe() -> RecipeDefinition:
    """页序/去重/同节归属/账户会话校验（单节点配方）。"""
    return RecipeDefinition(
        recipe_id=VALIDATION_RECIPE_ID,
        recipe_version=STUDY_PAGE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_VALIDATE_PAGES,
                capability="study.validate_pages",
                artifact_type="study.page_validation",
                capability_version=STUDY_PAGE_CAPABILITY_VERSIONS[NODE_VALIDATE_PAGES],
                input_key=_validation_key,
                recovery=RecoveryPolicy.RETRY_NODE,
                description="去重、页序、同节归属、账户/会话和附件处理状态。",
            ),
        ),
    )


def build_page_recognition_recipe() -> RecipeDefinition:
    """单页识别：文字/公式/图表与位置，再核对关键符号与图文对应。"""
    return RecipeDefinition(
        recipe_id=RECOGNITION_RECIPE_ID,
        recipe_version=STUDY_PAGE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_RECOGNIZE_PAGE,
                capability="study.recognize_page",
                artifact_type="study.page_recognition",
                capability_version=STUDY_PAGE_CAPABILITY_VERSIONS[NODE_RECOGNIZE_PAGE],
                input_key=_recognize_key,
                recovery=RecoveryPolicy.RETRY_NODE,
                description="文字、公式、图表及位置；每页单独保存可恢复产物。",
            ),
            NodeSpec(
                name=NODE_VERIFY_RECOGNITION,
                capability="study.verify_recognition",
                artifact_type="study.page_verification",
                capability_version=STUDY_PAGE_CAPABILITY_VERSIONS[NODE_VERIFY_RECOGNITION],
                input_key=_verify_key,
                depends_on=(NODE_RECOGNIZE_PAGE,),
                required_gates=("study.critical_symbols",),
                recovery=RecoveryPolicy.ASK_INPUT,
                description="检查断句、关键符号、单位和图文对应的疑点。",
            ),
        ),
    )


def build_page_fix_recipe() -> RecipeDefinition:
    """关键证据不足时的具体位置补拍/补录请求（确定性节点）。"""
    return RecipeDefinition(
        recipe_id=FIX_RECIPE_ID,
        recipe_version=STUDY_PAGE_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_REQUEST_PAGE_FIX,
                capability="study.request_page_fix",
                artifact_type="study.page_fix_request",
                capability_version=STUDY_PAGE_CAPABILITY_VERSIONS[NODE_REQUEST_PAGE_FIX],
                input_key=_fix_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="仅在关键证据不足时提出具体位置的补拍/补录问题。",
            ),
        ),
    )


def study_recipe_registry() -> RecipeRegistry:
    """登记书页识别能力、质量门与三个配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=STUDY_PAGE_CAPABILITY_VERSIONS.keys(),
        gates=STUDY_PAGE_GATES,
    )
    registry.register(build_page_validation_recipe())
    registry.register(build_page_recognition_recipe())
    registry.register(build_page_fix_recipe())
    return registry


def _critical_symbols_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：关键符号存在疑点时不允许进入范围映射/预习。"""
    del invocation
    payload = execution.artifact.payload
    critical = [
        item
        for item in payload.get("unclear", [])
        if isinstance(item, Mapping) and item.get("critical")
    ]
    if critical:
        positions = "；".join(
            f"{item.get('position', '未知位置')}（{item.get('reason', '关键证据不清')}）"
            for item in critical
        )
        return QualityGateResult(
            gate="study.critical_symbols",
            verdict=QualityVerdict.NEED_INPUT,
            code=GATE_CRITICAL_EVIDENCE,
            message=f"关键符号或图文对应仍不清：{positions}。请补拍或补录后再继续。",
        )
    return QualityGateResult(gate="study.critical_symbols", verdict=QualityVerdict.PASS)


STUDY_PAGE_GATE_HANDLERS: dict[str, GateHandler] = {
    "study.critical_symbols": _critical_symbols_gate,
}


# ---------------------------------------------------------------------------
# 节点执行体
# ---------------------------------------------------------------------------


class StudyPageNodeFlow:
    """书页识别节点的确定性编排执行体（模型调用经注入的 invoke 接缝）。"""

    def __init__(
        self,
        *,
        load_image: Callable[[str], tuple[Any, bytes]],
        invoke: Callable[[str, dict[str, Any]], dict[str, Any]],
        budget_remaining_calls: Callable[[], int | None] | None = None,
    ) -> None:
        self._load_image = load_image
        self._invoke = invoke
        self._budget_remaining_calls = budget_remaining_calls
        self._candidates: tuple[StudyPageCandidate, ...] = ()
        self._known_hashes: frozenset[str] = frozenset()
        self._current: StudyPageCandidate | None = None
        self._fix_requests: tuple[dict[str, Any], ...] = ()
        self._prior_fragments: list[Any] = []

    # -- 编排接口 ---------------------------------------------------------

    def configure(
        self,
        candidates: Sequence[StudyPageCandidate],
        known_hashes: frozenset[str],
    ) -> None:
        """设置本批候选与既有页指纹（去重边界）。"""
        self._candidates = tuple(candidates)
        self._known_hashes = known_hashes

    def set_prior_fragments(self, fragments: Sequence[Any]) -> None:
        """同一小节已有片段提示（识别前注入；不改变权威边界）。"""
        self._prior_fragments = list(fragments)

    def select_page(self, candidate: StudyPageCandidate) -> None:
        """按页执行前设置当前候选（执行体只读该引用）。"""
        self._current = candidate

    def select_fix_requests(self, requests: Sequence[Mapping[str, Any]]) -> None:
        self._fix_requests = tuple(dict(item) for item in requests)

    def can_recognize_more(self) -> bool:
        """预算是否放得下下一页的 OCR + 视觉双路径调用。

        账本缺失时按无预算模式放行；账本存在时至少保留两格调用额度，
        不发起注定被网关拒绝的调用。已登记页的产物复用不消耗调用。
        """
        if self._budget_remaining_calls is None:
            return True
        remaining = self._budget_remaining_calls()
        return remaining is None or remaining >= 2

    # -- 节点分发 ---------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_VALIDATE_PAGES: self._run_validate_pages,
            NODE_RECOGNIZE_PAGE: self._run_recognize_page,
            NODE_VERIFY_RECOGNITION: self._run_verify_recognition,
            NODE_REQUEST_PAGE_FIX: self._run_request_page_fix,
        }[invocation.spec.name]
        return handler(invocation)

    # -- 校验 -------------------------------------------------------------

    def _run_validate_pages(self, invocation: NodeInvocation) -> NodeExecution:
        accepted: list[dict[str, Any]] = []
        duplicates: list[str] = []
        rejected: list[dict[str, Any]] = []
        for candidate in self._candidates:
            record: dict[str, Any] = {
                "object_id": candidate.object_id,
                "content_hash": candidate.content_hash,
                "media_type": candidate.media_type,
            }
            if not candidate.media_type.startswith("image/"):
                rejected.append({**record, "reason": "学习模式只接受本节书页照片"})
                continue
            if candidate.content_hash in self._known_hashes:
                duplicates.append(candidate.object_id)
                continue
            accepted.append(record)
        payload = {
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": rejected,
        }
        stopped = bool(rejected)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                read_scope="当前消息绑定的本节书页附件（账户+会话作用域）",
                unconfirmed=[str(item["reason"]) for item in rejected],
            ),
            verdict=QualityVerdict.NEED_INPUT if stopped else QualityVerdict.PASS,
            status=(
                NodeReceiptStatus.NEEDS_INPUT if stopped else NodeReceiptStatus.COMPLETED
            ),
            detail={
                "accepted": len(accepted),
                "duplicates": len(duplicates),
                "rejected": len(rejected),
                "code": "study_non_photo_attachment" if stopped else "",
                "message": "学习模式只接受本节书页照片。" if stopped else "",
                "retryable": False,
            },
            stop_recipe=stopped,
            recovery=invocation.spec.recovery,
        )

    # -- 识别 -------------------------------------------------------------

    def _run_recognize_page(self, invocation: NodeInvocation) -> NodeExecution:
        candidate = self._current
        assert candidate is not None, "按页识别缺少当前候选"
        record, content = self._load_image(candidate.object_id)
        encoded = base64.b64encode(content).decode("ascii")
        ocr = self._invoke(
            "qwen_ocr",
            {
                "image_base64": encoded,
                "mime_type": record.media_type,
                "prompt": (
                    "按阅读顺序逐字识别这页教材的文字、公式与图表标签；"
                    "看不清的符号写[不清]，不要猜测。"
                ),
                "task": None,
                "temperature": 0.01,
            },
        )
        ocr_text = ocr.get("content")
        if not isinstance(ocr_text, str) or not ocr_text.strip():
            return self._failure_execution(
                invocation,
                code="study_ocr_empty",
                message="书页文字识别失败，请重试或补拍。",
                retryable=True,
            )
        vision = self._invoke(
            "qwen_vision",
            {
                "image_base64": encoded,
                "mime_type": record.media_type,
                "temperature": 0.01,
                "prompt": (
                    "只输出 JSON 对象，字段 same_section(boolean),"
                    " page_number(书上印刷页码，正整数；看不到或不确定时为 null)，"
                    " fragments(数组：kind 为 text/formula/chart、"
                    "position 为页面位置、text 为所见原文、confidence 为 0-1、"
                    "interpretation 为对图表/公式的推断或解释——没有则为空字符串，"
                    "不得把推断写进 text),"
                    " unclear(数组：position、reason)。"
                    "逐段保留公式和图表；任何看不清或低置信内容必须列入 unclear，不得猜测。"
                    "后续页是否同一小节参考既有片段："
                    + json.dumps(
                        [
                            {
                                "position": item.position,
                                "text": item.text,
                                "kind": item.kind,
                            }
                            for item in self._prior_fragments
                        ],
                        ensure_ascii=False,
                    )
                    + "；独立 OCR 结果仅供核对："
                    + ocr_text[:9000]
                ),
            },
        )
        try:
            parsed = _Recognition.model_validate_json(
                _json_object_text(str(vision.get("content", "")))
            )
        except ValidationError:
            return self._failure_execution(
                invocation,
                code="study_recognition_invalid",
                message="书页结构识别不完整，请重试。",
                retryable=True,
            )
        payload = {
            "object_id": candidate.object_id,
            "content_hash": candidate.content_hash,
            "media_type": record.media_type,
            "page_number": parsed.page_number,
            "same_section": parsed.same_section,
            "model_id": "",
            "ocr_text": ocr_text[:20000],
            "recognition_paths": ["ocr", "vision"],
            "fragments": [
                {
                    "kind": item.kind,
                    "position": item.position,
                    "text": item.text,
                    "confidence": item.confidence,
                    "recognition_path": "vision",
                    "interpretation": item.interpretation,
                }
                for item in parsed.fragments
            ],
            "model_unclear": [item.model_dump() for item in parsed.unclear],
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[candidate.object_id],
                read_scope=f"原书页照片（{record.media_type}）",
                requirement_coverage=[
                    {"requirement": "文字/公式/图表与位置", "covered": True},
                    {"requirement": "OCR + 视觉双路径核对（优化未验证前保持）", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"fragments": len(parsed.fragments)},
        )

    # -- 核验 -------------------------------------------------------------

    def _run_verify_recognition(self, invocation: NodeInvocation) -> NodeExecution:
        recognized = invocation.dependencies[NODE_RECOGNIZE_PAGE].payload
        fragments = list(recognized.get("fragments", []))
        ocr_text = str(recognized.get("ocr_text", ""))
        unclear: list[StudyUnclear] = []
        critical_positions: list[str] = []
        mismatch_positions: list[str] = []
        low_confidence_positions: list[str] = []

        # 1) 模型声明的疑点：位置命中关键符号、或疑点描述点名关键类别时
        #    按关键疑点记，不能因该位置没有可比对片段而降级。
        for item in recognized.get("model_unclear", []):
            position = str(item.get("position", ""))
            reason = str(item.get("reason", "模型标记为看不清"))
            kinds = critical_symbol_kinds(
                " ".join(
                    str(fragment.get("text", ""))
                    for fragment in fragments
                    if str(fragment.get("position", "")) == position
                )
            )
            if not kinds:
                kinds = critical_doubt_kinds(reason)
            is_critical = bool(kinds)
            if is_critical:
                critical_positions.append(position)
                reason = f"{reason}（{('、'.join(kinds))}）"
            unclear.append(
                StudyUnclear(
                    position=position or "未知位置",
                    reason=reason,
                    kind="critical_symbol" if is_critical else "unclear",
                    critical=is_critical,
                )
            )

        # 2) 低置信片段：保留原有阈值提示；命中关键符号时标为关键疑点。
        for fragment in fragments:
            position = str(fragment.get("position", ""))
            if float(fragment.get("confidence", 1.0)) >= 0.7:
                continue
            kinds = critical_symbol_kinds(str(fragment.get("text", "")))
            low_confidence_positions.append(position)
            unclear.append(
                StudyUnclear(
                    position=position or "未知位置",
                    reason=(
                        "关键内容识别置信度低"
                        + (f"（{('、'.join(kinds))}）" if kinds else "")
                    ),
                    kind="critical_symbol" if kinds else "unclear",
                    critical=bool(kinds),
                )
            )

        # 3) 双路径不一致：关键符号片段的视觉原文在 OCR 文本中找不到对应
        #    内容时，即使模型自报高置信也保持待补充（模型一致/自报置信
        #    不是正确保证；只有两路文本一致才不额外标疑点）。
        for fragment in fragments:
            text = str(fragment.get("text", ""))
            kinds = critical_symbol_kinds(text)
            if not kinds:
                continue
            if _paths_agree(text, ocr_text):
                continue
            position = str(fragment.get("position", "")) or "未知位置"
            mismatch_positions.append(position)
            unclear.append(
                StudyUnclear(
                    position=position,
                    reason=(
                        f"视觉与文字识别不一致（{('、'.join(kinds))}），"
                        "请补拍或补录该位置"
                    ),
                    kind="dual_path_mismatch",
                    critical=True,
                )
            )

        page_unclear = self._dedupe_unclear(unclear)
        payload = {
            "object_id": recognized.get("object_id"),
            "unclear": [item.model_dump() for item in page_unclear],
            "checks": {
                "critical_positions": critical_positions,
                "dual_path_mismatch": mismatch_positions,
                "low_confidence": low_confidence_positions,
                "recognition_paths": recognized.get("recognition_paths", []),
            },
        }
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload=payload,
                source_refs=[str(recognized.get("object_id", ""))],
                read_scope="书页断句、关键符号、单位与图文对应核验",
                requirement_coverage=[
                    {"requirement": "关键符号与图文对应", "covered": not page_unclear},
                ],
                unconfirmed=[item.reason for item in page_unclear],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"unclear": len(page_unclear)},
        )

    @staticmethod
    def _dedupe_unclear(items: Sequence[StudyUnclear]) -> list[StudyUnclear]:
        seen: set[tuple[str, str, bool]] = set()
        result: list[StudyUnclear] = []
        for item in items:
            key = (item.position, item.reason, item.critical)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    # -- 补拍/补录请求 -----------------------------------------------------

    def _run_request_page_fix(self, invocation: NodeInvocation) -> NodeExecution:
        requests = tuple(self._fix_requests)
        if not requests:
            return self._failure_execution(
                invocation,
                code="study_fix_not_required",
                message="没有需要补拍或补录的关键疑点。",
                retryable=False,
            )
        details = "；".join(
            f"第{item.get('page_ordinal')}页{item.get('position')}：{item.get('reason')}"
            for item in requests
        )
        message = (
            f"这些位置还看不清：{details}。"
            "请补拍对应位置，或按“第N页+位置：具体内容”补录文字。"
            "若确认是同节照片，请回复“确认第N页属于本节”；"
            "不同小节的书页请在新对话上传。"
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.DRAFT,
                payload={"requests": list(requests), "message": message},
                source_refs=sorted(
                    {str(item.get("object_id", "")) for item in requests}
                ),
                read_scope="按页疑点定位（页号 + 位置）",
                unconfirmed=[str(item.get("reason", "")) for item in requests],
            ),
            verdict=QualityVerdict.NEED_INPUT,
            status=NodeReceiptStatus.NEEDS_INPUT,
            detail={"requests": len(requests)},
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )

    # -- 内部工具 ---------------------------------------------------------

    def _artifact(
        self,
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
            recipe_version=STUDY_PAGE_RECIPE_VERSION,
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

    def _failure_execution(
        self,
        invocation: NodeInvocation,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> NodeExecution:
        artifact = self._artifact(
            invocation,
            trust_state=ArtifactTrust.INVALIDATED,
            payload={},
            error={"code": code, "message": message, "retryable": retryable},
        )
        return NodeExecution(
            artifact=artifact,
            verdict=(
                QualityVerdict.REPAIRABLE_FAILURE
                if retryable
                else QualityVerdict.BLOCKED
            ),
            status=NodeReceiptStatus.FAILED,
            detail={"code": code, "message": message, "retryable": retryable},
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )


# ---------------------------------------------------------------------------
# 按页编排（内核执行 + 结果合并）
# ---------------------------------------------------------------------------

EventSink = Callable[[str, str, int | None], None]


class StudyPageRecognition:
    """按页调用持久节点内核，产出可合并进 StudyState 的页级结果。

    编排语义：

    - 先执行 ``study.validate_pages`` 决定实际要识别的页（去重后）；
    - 每页单独执行 ``study.recognize_page`` + ``study.verify_recognition``；
      输入键只含页身份与识别协议版本，恢复时未变页直接回填产物，只有
      未完成或内容变化的页重新识别；
    - 识别页提交后由 ``on_page`` 回调立即持久化（每页独立可恢复）；
    - 预算放不下下一页双路径调用时停止批处理，剩余页保持待处理；
    - 任一关键疑点存在时执行 ``study.request_page_fix``，由领域层按
      具体页号/位置向用户请求补拍或补录。
    """

    def __init__(
        self,
        *,
        kernel: NodeKernel,
        flow: StudyPageNodeFlow,
        account_id: str,
        conversation_id: str,
        run_id: str,
        user_message_id: str,
        user_content: str,
        stop_event: Any = None,
        event_sink: EventSink | None = None,
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
        self._validation_recipe = build_page_validation_recipe()
        self._recognition_recipe = build_page_recognition_recipe()
        self._fix_recipe = build_page_fix_recipe()

    # -- 对外入口 ---------------------------------------------------------

    def recognize(
        self,
        candidates: Sequence[StudyPageCandidate],
        known_hashes: frozenset[str],
        *,
        on_page: Callable[[StudyPage], StudyPage],
        prior_fragments: Sequence[Any] = (),
    ) -> StudyRecognitionOutcome:
        flow = self._flow
        flow.configure(candidates, known_hashes)
        flow.set_prior_fragments(prior_fragments)

        validation = self._kernel.execute(
            recipe=self._validation_recipe,
            inputs=self._inputs(
                prior_digest=_digest(
                    [
                        (item.object_id, item.content_hash, item.media_type)
                        for item in candidates
                    ]
                    + sorted(known_hashes)
                )
            ),
            event_sink=self._event_sink,
            stop_event=self._stop_event,
        )
        self._raise_if_terminal(validation)
        validation_artifact = validation.artifact(NODE_VALIDATE_PAGES)
        accepted_payload = (
            list(validation_artifact.payload.get("accepted", []))
            if validation_artifact is not None
            else []
        )
        by_object = {item.object_id: item for item in candidates}
        pages: list[StudyPage] = []
        pending: list[str] = []
        for index, accepted in enumerate(accepted_payload):
            object_id = str(accepted.get("object_id", ""))
            candidate = by_object.get(object_id)
            if candidate is None:
                continue
            if not flow.can_recognize_more():
                pending.extend(
                    str(item.get("object_id", ""))
                    for item in accepted_payload[index:]
                )
                break
            flow.select_page(candidate)
            result = self._kernel.execute(
                recipe=self._recognition_recipe,
                inputs=self._inputs(
                    prior_digest=(
                        f"{candidate.object_id}:{candidate.content_hash}"
                        f":{STUDY_RECOGNITION_PROTOCOL_VERSION}"
                    )
                ),
                event_sink=self._event_sink,
                stop_event=self._stop_event,
            )
            self._raise_if_terminal(result)
            failure = result.failure
            if failure is not None and failure.code != GATE_CRITICAL_EVIDENCE:
                raise NodeFailureError(
                    failure.node, failure.code, failure.message, failure.retryable
                )
            page = self._build_page(
                result.artifact(NODE_RECOGNIZE_PAGE),
                result.artifact(NODE_VERIFY_RECOGNITION),
            )
            if page is None:
                raise NodeFailureError(
                    NODE_RECOGNIZE_PAGE,
                    "study_recognition_missing",
                    "书页识别产物缺失，请重试。",
                    True,
                )
            # 回调返回已持久化的页（含领域层分配的页序），后续补拍请求与
            # 关键页记录都按最终页序定位，不能使用回调前的临时对象。
            page = on_page(page)
            pages.append(page)

        fix_message = ""
        if any(page.unclear for page in pages):
            flow.select_fix_requests(self._fix_requests(pages))
            fix_result = self._kernel.execute(
                recipe=self._fix_recipe,
                inputs=self._inputs(
                    prior_digest=_digest(
                        [
                            (page.object_id, issue.model_dump())
                            for page in pages
                            for issue in page.unclear
                        ]
                    )
                ),
                event_sink=self._event_sink,
                stop_event=self._stop_event,
            )
            self._raise_if_terminal(fix_result)
            fix_artifact = fix_result.artifact(NODE_REQUEST_PAGE_FIX)
            if fix_artifact is not None:
                fix_message = str(fix_artifact.payload.get("message", ""))
        return StudyRecognitionOutcome(
            pending_object_ids=pending,
            fix_message=fix_message,
        )

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

    def _build_page(
        self, recognized: NodeArtifact | None, verify: NodeArtifact | None
    ) -> StudyPage | None:
        if recognized is None or not recognized.payload.get("object_id"):
            return None
        payload = recognized.payload
        unclear = (
            [StudyUnclear.model_validate(item) for item in verify.payload.get("unclear", [])]
            if verify is not None
            else []
        )
        fragments = [
            StudyFragment(
                fragment_id=f"{payload.get('object_id')}:{index}",
                kind=item["kind"],
                position=item["position"],
                text=item["text"],
                confidence=item["confidence"],
                source="photo",
                recognition_path=item.get("recognition_path", "vision"),
                interpretation=item.get("interpretation", ""),
            )
            for index, item in enumerate(payload.get("fragments", []), 1)
        ]
        return StudyPage(
            object_id=str(payload.get("object_id", "")),
            ordinal=0,
            content_hash=str(payload.get("content_hash", "")),
            model_id=str(payload.get("model_id", "")),
            page_number=payload.get("page_number"),
            same_section=bool(payload.get("same_section", True)),
            fragments=fragments,
            unclear=unclear,
            recognition_paths=[
                str(item) for item in payload.get("recognition_paths", ["ocr", "vision"])
            ],
        )

    def _fix_requests(self, pages: Sequence[StudyPage]) -> list[dict[str, Any]]:
        return [
            {
                "page_ordinal": page.ordinal,
                "object_id": page.object_id,
                "position": issue.position,
                "reason": issue.reason,
                "kind": issue.kind,
                "critical": issue.critical,
            }
            for page in pages
            for issue in page.unclear
        ]

    def _raise_if_terminal(self, result: Any) -> None:
        if result.status is KernelStatus.STOPPED:
            raise StoppedError(result.stopped_at or NODE_RECOGNIZE_PAGE)
        if result.status is KernelStatus.REJECTED:
            raise SupersededError(result.rejection_code or "generation_superseded")


class StoppedError(Exception):
    """用户停止或内核拒绝提交时抛出的领域信号。"""

    def __init__(self, node: str) -> None:
        self.node = node
        super().__init__("学习处理已停止。")


class SupersededError(Exception):
    """运行已被新尝试取代（租约/版本/终态冲突），结果不得交付。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NodeFailureError(Exception):
    """节点真实失败（模型/结构/OCR），由领域层映射为用户可见错误。"""

    def __init__(self, node: str, code: str, message: str, retryable: bool) -> None:
        self.node = node
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)
