"""统一聊天入口中的文章人味化自然语言路由。

路由只负责确定能力、来源和任务契约，不生成文章，也不调用模型。它必须
宁可放弃模糊命中，也不能把普通聊天或解释性问题误送进人味化流程。

任务契约（Issue 03 人味化改造）由 ``contract_compiler`` 确定性编译为版本化
``ExpressionTaskContract``：surface/operation/现实承诺/改写强度/说话位置/
第一人称与假设权限/受众渠道长度/体裁（未识别用通用 profile）/材料充分度/
证据修订模式。旧 ``HumanizerTaskContract`` 从新契约映射，保持下游兼容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bridges.contracts.expression_task import ExpressionTaskContract, Operation, Surface
from bridges.contracts.humanizer import (
    HumanizerPath,
    HumanizerRouteDecision,
    HumanizerRouteSource,
    HumanizerSkillInput,
    HumanizerTaskContract,
)
from bridges.skills.humanizer.contract_compiler import (
    REWRITE_ACTION_RE,
    CompileRequest,
    compile_task_contract,
)

HUMANIZER_ROUTE_VERSION = "humanizer-route-v1"

_ARTICLE_RE = re.compile(
    r"文章|原文|文本|文字|段落|摘要|邮件|报告|演讲稿|讲稿|论文|稿子|这段话|内容"
)
_DIRECT_REQUEST_RE = re.compile(
    r"帮我|给我|请(?:帮我|给我|把|给|直接|润色|改写|重写)|把|让|需要|希望|想要|直接"
)
_INFORMATIONAL_RE = re.compile(r"是什么意思|是什么|怎么用|怎么收费|怎么理解|含义|如何写|怎么写")
_NEGATION_RE = re.compile(r"不要|不想|别|不用|无需|不是要|不需要")

_SOURCE_MARKERS = (
    re.compile(r"(?:原文|正文|文本|内容)\s*(?:是|为|如下)?\s*[：:]\s*(.+)$", re.DOTALL),
    re.compile(r"(?:下面(?:这段(?:话|文字)?)?|如下)\s*[：:]\s*(.+)$", re.DOTALL),
)
_KB_REFERENCE_RE = re.compile(
    r"知识库(?:文档|文件)?\s*[「『“\"]([^」』”\"]+)[」』”\"]"
)
_EXTERNAL_EVIDENCE_RE = re.compile(
    r"补充(?:外部|公开|事实|来源)|核验(?:外部)?事实|联网|网上(?:检索|查找)?|"
    r"查证|检索(?:公开|外部|文献)"
)


@dataclass(frozen=True)
class HumanizerRouteResult:
    """自然语言路由结果；可直接落入既有 SKILL 消息快照。"""

    skill_input: HumanizerSkillInput
    use_knowledge_base: bool


def route_humanizer_message(content: str) -> HumanizerRouteResult | None:
    """识别显式文章改写意图并编译稳定任务契约。

    返回 ``None`` 表示本消息不是文章改写任务。函数只做确定性文本处理，
    不访问账户、知识库或模型；知识库引用只保存用户明确给出的文档名，
    实际账户授权与证据读取留在后续既有检索/人味化服务中。
    """

    text = (content or "").strip()
    if not text or not _is_rewrite_intent(text):
        return None

    task_text, source_text = _split_task_and_source(text)
    knowledge_base_reference = _knowledge_base_reference(task_text)

    # Issue 03：由契约编译器收敛全部任务边界，不再零散拼装体裁默认值。
    # 路由已确认文章改写意图，surface 显式钉死为文章，不走聊天轻量策略。
    compiled = compile_task_contract(
        CompileRequest(
            content=task_text,
            source_material=source_text,
            surface_hint=Surface.ARTICLE,
        )
    )
    expression_contract: ExpressionTaskContract = compiled.contract

    contract = HumanizerTaskContract(
        path=_legacy_path(expression_contract),
        genre=expression_contract.genre,
        audience=expression_contract.audience,
        channel=expression_contract.channel,
        length_target=expression_contract.length_target,
        hard_constraints=expression_contract.user_constraints,
        source_text=source_text,
        source_label=(
            f"知识库文档：{knowledge_base_reference}"
            if knowledge_base_reference
            else "聊天内原文"
            if source_text
            else None
        ),
        knowledge_base_reference=knowledge_base_reference,
        # Issue 01：表达契约的权限必须映射进旧契约——prompt 权限行读表达
        # 契约（draft_compiler/revision_prompt），保真硬门读旧契约
        # （service 的 allow_assumptions/allow_first_person），两份真值
        # 不一致时用户授权会被硬门误拦（ASSUMPTION_NOT_ALLOWED）。
        allow_assumptions=expression_contract.hypothetical_permission,
        allow_first_person=expression_contract.first_person_permission,
    )
    route = HumanizerRouteDecision(
        source=HumanizerRouteSource.NATURAL_LANGUAGE,
        version=HUMANIZER_ROUTE_VERSION,
        reason="检测到显式文章改写意图",
        external_evidence_requested=_requests_external_evidence(task_text),
    )
    return HumanizerRouteResult(
        skill_input=HumanizerSkillInput(
            skill_id="bridges-humanizer",
            contract=contract,
            route=route,
            expression_contract=expression_contract,
        ),
        use_knowledge_base=bool(knowledge_base_reference),
    )


def _legacy_path(contract: ExpressionTaskContract) -> HumanizerPath:
    """旧双路径契约与新三操作映射：改写 → REWRITE，扩写/生成 → GENERATE。"""
    return (
        HumanizerPath.REWRITE
        if contract.operation == Operation.REWRITE
        else HumanizerPath.GENERATE
    )


def _is_rewrite_intent(text: str) -> bool:
    match = REWRITE_ACTION_RE.search(text)
    if match is None:
        return False
    before = text[max(0, match.start() - 10) : match.start()]
    if _NEGATION_RE.search(before):
        return False
    if _INFORMATIONAL_RE.search(text) and not _DIRECT_REQUEST_RE.search(text):
        return False
    return bool(_ARTICLE_RE.search(text) or _DIRECT_REQUEST_RE.search(text))


def _split_task_and_source(text: str) -> tuple[str, str | None]:
    for marker in _SOURCE_MARKERS:
        match = marker.search(text)
        if match and match.group(1).strip():
            return text[: match.start()].strip(), match.group(1).strip()
    colon = re.search(r"[：:]", text)
    if (
        colon
        and REWRITE_ACTION_RE.search(text[: colon.start()])
        and text[colon.end() :].strip()
    ):
        return text[: colon.start()].strip(), text[colon.end() :].strip()
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if REWRITE_ACTION_RE.search(first) and rest.strip():
            return first.strip(), rest.strip()
    return text, None


def _knowledge_base_reference(task_text: str) -> str | None:
    match = _KB_REFERENCE_RE.search(task_text)
    if match:
        return match.group(1).strip() or None
    return None


def _requests_external_evidence(task_text: str) -> bool:
    return bool(_EXTERNAL_EVIDENCE_RE.search(task_text))
