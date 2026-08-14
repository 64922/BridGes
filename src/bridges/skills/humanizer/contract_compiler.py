"""表达任务契约编译器（人味化改造 Issue 03）。

编译器只做确定性解析和最小澄清决策，不生成正文。它把零散的自然语言意图、
体裁默认值、模式提示和文章参数收敛为版本化 ``ExpressionTaskContract``；
普通聊天和文章共用任务边界词汇，但分别编译自己的轻量策略和完整文章流程，
不能重新合并成同一块总提示词。

规则优先级固定为：用户显式要求与安全边界 → 来源/现实承诺 → 表面与模式合同
→ 场景表达规则 → 软风格检查；冲突时返回可解释的稳定裁决。

快照与重试：同一任务重试必须回传原契约，编译器只做版本与哈希校验后复用，
不因规则热更新改变强度、来源或第一人称权限；未知版本在执行前稳定拒绝。
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from bridges.contracts.expression import Genre
from bridges.contracts.expression_task import (
    ContractAdjudication,
    ContractCompileRecord,
    ConversationMode,
    EvidenceRevisionMode,
    ExpressionTaskContract,
    MaterialSufficiency,
    Operation,
    RealityMode,
    RewriteIntensity,
    RulePriorityLayer,
    SourceScope,
    Surface,
)

#: 当前契约 Schema 版本。旧快照携带的版本不在注册表内时稳定拒绝。
SCHEMA_VERSION = "expression-task-v1"
KNOWN_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

#: 材料充分度判定阈值：低于此长度的原文视为不足以支撑扩写。
_MIN_EXPAND_SOURCE_LENGTH = 120
#: 按主题生成时，主题自带细节足以直接生成的最小长度。
_MIN_GENERATE_TOPIC_LENGTH = 100

# ---------------------------------------------------------------------------
# 确定性解析词表
# ---------------------------------------------------------------------------

_ARTICLE_MARKERS = re.compile(
    r"文章|原文|文本|段落|摘要|邮件|报告|演讲稿|论文|稿子|这段话|内容|"
    r"润色|改写|重写|人味化|去(?:掉|除)?\s*(?:模板腔|AI味|ai味)|模板腔|"
    r"AI\s*味|改得更自然|改成更自然|更像人写|口语化|顺一顺|顺一下|调整表达|"
    r"改动措辞|扩写|写长|展开|生成|写一篇|写一份|帮我写|写个|写一个|创作|"
    r"编一个|改一下|改改",
    re.IGNORECASE,
)
_DIRECT_REQUEST_RE = re.compile(
    r"帮我|给我|请(?:帮我|给我|把|给|直接)|把|让|需要|希望|想要|直接|"
    r"请(?:帮我|给我|把|给|直接|润色|改写|重写)"
)

#: 改写动作词表（公开常量）：路由入口与编译器共用，避免跨文件重复维护漂移。
#: 只含动作命令词；单独的“模板腔”“AI 味”是特征描述不是命令，不触发路由。
#: Issue 03（feature 7）扩展：覆盖「有人味/自然一点/不像 AI 写的」等近似
#: 说法；「有人味」必须带程度修饰（一点/一些/多点/更），纯陈述
#: （「这篇文章写得有人味」）不触发；「像人写的/不是 AI 写的」等陈述形
#: 说法不作为动作词（「这篇稿子不太像人写的」不触发）。
REWRITE_ACTION_RE = re.compile(
    r"润色|改写|重写|人味化|"
    r"去(?:掉|除)?\s*(?:模板腔|AI\s*味)|去掉|去除|"
    r"改得更自然|改成更自然|改得自然|自然一点|自然一些|自然些|自然点|"
    r"更像人写|不像(?:是)?\s*(?:AI|机器|机翻)\s*(?:写|生成|创作)的|"
    r"有人味(?:一点|一些|些|点)|多点人味|更有人味|人味更足|改得有人味|"
    r"口语化|顺一顺|顺一下|"
    r"调整表达|改动措辞|改一下|改改|修一下",
    re.IGNORECASE,
)
_EXPAND_WORDS = re.compile(r"扩写|写长|展开|详细一点|写得长一点|丰富内容|补充细节|拉长")
_GENERATE_WORDS = re.compile(r"生成|写一篇|写一份|写个|写一个|帮我写|起草|草拟|创作|编一个")

_REALITY_FICTIONAL = re.compile(r"虚构|小说|童话|剧本|故事(?:里|中)?|架空|幻想|想象一个|假如世界")
_REALITY_MIXED = re.compile(
    r"真实事件?改编|半虚构|先(?:介绍|给|讲)事实.{0,30}再(?:虚构|编|写)|"
    r"基于(?:真实)?(?:事实|事件).{0,20}(?:创作|虚构|编一个)|"
    r"结合事实.{0,20}(?:虚构|创作)"
)

_INTENSITY_LIGHT = re.compile(r"轻度|轻微|轻润|小改|微调|轻改|只改(?:一下|一点点)?|不要大改")
# 深度强度必须是明确的改写强度表达，避免误命中主题词（如“深度学习”）。
_INTENSITY_DEEP = re.compile(
    r"深度改写|深度润色|深度重写|深度修改|彻底(?:改写|重写|修改)|"
    r"大幅(?:改写|修改)|大改|重写一遍|完全重写|改到认不出|大刀阔斧"
)

_FIRST_PERSON_ALLOW = re.compile(r"第一人称|用(?:我|我的)口吻|以我的名义|可以写.{0,4}我|允许用.{0,4}我")
_FIRST_PERSON_BLOCK = re.compile(
    r"不要(?:用|写|加).{0,4}第一人称|不要(?:用|写|加).{0,4}我|"
    r"第三人称|别用.{0,4}我|不能(?:用|写|加).{0,4}(?:第一人称|我)"
)

_HYPOTHETICAL_ALLOW = re.compile(r"允许(?:假设|设想|举例)|可以假设|可以设想|假设一下|可以加假设|可以举.{0,4}例子")
_HYPOTHETICAL_BLOCK = re.compile(r"不要假设|不允许假设|不要用假设|别假设|不可以假设|不能假设|禁止假设")

#: 否定前缀检查：允许词（如“可以假设”）前面出现否定时视为拒绝，
#: 防止“不可以假设”“不允许调整结论”被误判为允许。
_NEGATION_BEFORE_RE = re.compile(r"(?:不|没|别|无|禁止|不允许|不能)[^，,。；;\n]{0,6}$")

_SPEAKER_RE = re.compile(r"(?:以|用)([^，,。；;]{1,20}?)(?:的)?(?:身份|视角|口吻)")
_SPEAKER_SELF_RE = re.compile(r"(?:我是|我作为|代表|以作者(?:的)?身份|以我(?:的)?(?:名义|视角|口吻))")

_AUDIENCE_RE = re.compile(r"(?:面向|针对|目标受众(?:是|为)?|受众(?:是|为)?)\s*([^，,。；;\n：:]+)")
_LENGTH_RE = re.compile(r"(?:不超过|不多于|控制在|长度为|长度|字数|约)\s*(\d+\s*(?:字|词|分钟|页))")
_CHANNEL_WORDS = ("公众号", "邮件", "组会", "课堂", "演讲", "报告", "知乎", "小红书", "博客", "通知")

_GENRE_WORDS: list[tuple[re.Pattern[str], Genre]] = [
    (re.compile(r"科普|科学常识|科学解释|科学小知识"), Genre.POPULAR_SCIENCE),
    # 演讲不落讲稿：讲稿必现元素（学习目标/理解检查/练习停顿）只适用于课堂讲稿。
    (re.compile(r"课堂讲稿|课程讲稿|课程稿"), Genre.LECTURE_SCRIPT),
    (re.compile(r"科研汇报|科研报告|实验报告|科研工作汇报"), Genre.RESEARCH_REPORT),
    (re.compile(r"论文(?:写作|摘要)?|投稿论文"), Genre.PAPER_ASSIST),
]

_CONSTRAINT_CLAUSE_RE = re.compile(r"必须|不得|不能|不要|保留|原样|保持")
_EVIDENCE_SAFE_WORDS = re.compile(
    r"可以(?:调整|降低|弱化|放宽)(?:结论|强度|措辞)|证据安全修订|"
    r"允许调整结论|允许降低结论|允许弱化结论"
)
#: 证据修订的显式拒绝词；优先于允许词，防止“不允许调整结论”被反转。
_EVIDENCE_SAFE_BLOCK = re.compile(
    r"不(?:允许|要|能)?(?:调整|降低|弱化|放宽)(?:结论|强度|措辞)|"
    r"禁止(?:调整|降低|弱化)(?:结论|强度|措辞)|保持原结论"
)
_SOURCE_SCOPE_WEB = re.compile(r"联网|网上|搜索|查证|查一下|检索(?:公开|外部)")
_SOURCE_SCOPE_KB = re.compile(r"知识库(?:文档|文件)?\s*[「『“\"]([^」』”\"]+)[」』”\"]")

#: 信息性提问：解释、含义、用法类问题不属于文章任务，兜底 surface 判定时排除。
_INFORMATIONAL_QUERY_RE = re.compile(
    r"是什么意思|是什么|怎么理解|含义|怎么用|怎么收费|如何写|怎么写|解释一下|讲讲"
)

#: 材料不足时按操作返回的最高价值问题（只问一个）。
_ONE_QUESTION_BY_OPERATION: dict[Operation, str] = {
    Operation.REWRITE: "请提供需要改写的原文。",
    Operation.EXPAND: "你最希望展开的核心论点或需要补充的一项事实是什么？",
    Operation.GENERATE_BY_TOPIC: "这个主题最需要补充的一项事实或来源是什么？",
}


class ContractVersionError(Exception):
    """契约 Schema 版本未知或快照哈希不匹配。

    消息可安全暴露给调用方；重试必须使用原契约快照，未知版本在执行前
    稳定拒绝，不允许静默重新编译改变任务边界。
    """


@dataclass(frozen=True)
class CompileRequest:
    """契约编译输入：只做确定性解析，不访问账户、知识库或模型。

    ``content`` 是用户请求（任务部分，不含原文）；``source_material`` 是
    用户提供的原文/材料。``explicit_*`` 由调用方从更早的交互中确认的用户
    显式选择传入，优先级高于自动推荐。
    """

    content: str
    source_material: str | None = None
    surface_hint: Surface | None = None
    conversation_mode: ConversationMode | None = None
    profile_slice_id: str | None = None
    profile_item_count: int = 0
    explicit_intensity: RewriteIntensity | None = None
    explicit_evidence_safe: bool = False
    #: 重试路径：回传上一次编译的不可变契约快照，编译器只校验后复用。
    previous_contract: ExpressionTaskContract | None = None
    #: 追问后用户表示不补材料时置 True，裁决降级为 shorten 或 use_placeholders。
    downgrade_material: bool = False


@dataclass(frozen=True)
class CompileResult:
    """契约编译结果：不可变契约 + 可审计编译记录。"""

    contract: ExpressionTaskContract
    record: ContractCompileRecord
    #: 本次结果是否复用旧快照（重试路径）。
    reused: bool = False


def _token(prefix: str = "comp") -> str:
    return f"{prefix}-{secrets.token_urlsafe(8)}"


def _adjudicate(
    adjudications: list[ContractAdjudication],
    layer: RulePriorityLayer,
    rule_id: str,
    decision: str,
    reason: str,
) -> None:
    adjudications.append(
        ContractAdjudication(
            rule_id=rule_id,
            priority_layer=layer,
            decision=decision,
            reason=reason,
        )
    )


def _detect_operation(text: str, has_source: bool) -> Operation:
    """操作判定：扩写命令优先（含改写词时以扩写为准），改写命令次之，
    生成命令兜底。改写命令即使缺原文也保持 REWRITE——改写的边界就是
    原文，缺原文由材料充分度裁决追问，而不是改判生成扩大事实范围。"""
    if _EXPAND_WORDS.search(text):
        return Operation.EXPAND if has_source else Operation.GENERATE_BY_TOPIC
    if REWRITE_ACTION_RE.search(text):
        return Operation.REWRITE
    if _GENERATE_WORDS.search(text):
        return Operation.GENERATE_BY_TOPIC
    if has_source:
        return Operation.REWRITE
    return Operation.GENERATE_BY_TOPIC


def _detect_reality(text: str) -> RealityMode:
    if _REALITY_MIXED.search(text):
        return RealityMode.MIXED
    if _REALITY_FICTIONAL.search(text):
        return RealityMode.FICTIONAL
    return RealityMode.REAL


def _detect_intensity(
    text: str,
    explicit: RewriteIntensity | None,
    adjudications: list[ContractAdjudication],
) -> RewriteIntensity:
    if explicit is not None:
        _adjudicate(
            adjudications,
            RulePriorityLayer.USER_AND_SAFETY,
            "intensity-explicit",
            f"改写强度按用户显式选择：{explicit.value}",
            "用户显式选择优先于自动推荐。",
        )
        return explicit
    if _INTENSITY_LIGHT.search(text):
        return RewriteIntensity.LIGHT
    if _INTENSITY_DEEP.search(text):
        return RewriteIntensity.DEEP
    # 自动推荐：无显式强度词时保持默认标准，不因原文长短自动升降级。
    return RewriteIntensity.STANDARD


def _detect_speaker(text: str, surface: Surface) -> str:
    match = _SPEAKER_RE.search(text)
    if match:
        return f"用户指定的：{match.group(1).strip()}"
    if _SPEAKER_SELF_RE.search(text):
        return "用户本人（以作者身份）"
    return "用户本人（以作者身份）" if surface == Surface.ARTICLE else "助手（以对话者身份）"


def _detect_first_person(
    text: str,
    source_material: str | None,
    reality_mode: RealityMode,
    adjudications: list[ContractAdjudication],
) -> bool:
    if _FIRST_PERSON_BLOCK.search(text):
        return False
    explicit_allow = bool(_FIRST_PERSON_ALLOW.search(text))
    # 默认：原文已含第一人称则保留，否则不主动启用。
    allowed = explicit_allow or bool(source_material and "我" in source_material)
    if reality_mode == RealityMode.REAL and allowed:
        _adjudicate(
            adjudications,
            RulePriorityLayer.USER_AND_SAFETY,
            "first-person-real",
            "现实模式下第一人称仅限已有事实的当下判断，不补亲历",
            "安全边界优先：不能通过第一人称把虚构经历投影为用户事实。",
        )
    return allowed


def _is_negated_before(text: str, match: re.Match[str]) -> bool:
    """检查允许词匹配位置前是否被否定前缀否定（如“不可以假设”）。"""
    before = text[max(0, match.start() - 8) : match.start()]
    return _NEGATION_BEFORE_RE.search(before) is not None


def _detect_hypothetical(
    text: str,
    reality_mode: RealityMode,
    adjudications: list[ContractAdjudication],
) -> bool:
    if _HYPOTHETICAL_BLOCK.search(text):
        return False
    allow_match = _HYPOTHETICAL_ALLOW.search(text)
    if allow_match is not None and not _is_negated_before(text, allow_match):
        return True
    if reality_mode in {RealityMode.FICTIONAL, RealityMode.MIXED}:
        _adjudicate(
            adjudications,
            RulePriorityLayer.SOURCE_REALITY,
            "hypothetical-fictional",
            "虚构或混合模式下允许假设，但必须标明哪些内容允许创作",
            "虚构部分不得被投影为用户事实。",
        )
        return True
    if reality_mode == RealityMode.REAL:
        _adjudicate(
            adjudications,
            RulePriorityLayer.SOURCE_REALITY,
            "hypothetical-real",
            "现实模式下默认不启用假设",
            "假设仅在用户显式允许且明确标注时使用。",
        )
    return False


def _detect_audience(text: str) -> str | None:
    match = _AUDIENCE_RE.search(text)
    if not match:
        return None
    audience = match.group(1).strip()
    return audience or None


def _detect_channel(text: str) -> str | None:
    for channel in _CHANNEL_WORDS:
        if channel in text:
            return channel
    return None


def _detect_length(text: str) -> str | None:
    match = _LENGTH_RE.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(0).strip())
    return None


def _detect_genre(text: str) -> tuple[Genre | None, list[ContractAdjudication]]:
    """识别科学体裁；未命中返回 None（通用文章 profile）。

    邮件、报告（非科研）、教程、观点文、演讲等一律不落入科普默认值，
    避免被强迫使用科普定义与类比。
    """
    adjudications: list[ContractAdjudication] = []
    for pattern, genre in _GENRE_WORDS:
        if pattern.search(text):
            return genre, adjudications
    _adjudicate(
        adjudications,
        RulePriorityLayer.SCENE_EXPRESSION,
        "genre-generic-profile",
        "未识别体裁，使用通用文章 profile",
        "不默认强制科普必现模板；体裁检查只判断任务是否完成，不搜索指定套话。",
    )
    return None, adjudications


def _detect_material(
    operation: Operation,
    source_material: str | None,
    downgrade: bool,
    adjudications: list[ContractAdjudication],
) -> tuple[MaterialSufficiency, str | None]:
    """材料充分度裁决：sufficient / ask_one_question / shorten / use_placeholders。"""
    source = (source_material or "").strip()

    if operation == Operation.REWRITE:
        if not source:
            if downgrade:
                _adjudicate(
                    adjudications,
                    RulePriorityLayer.SOURCE_REALITY,
                    "material-rewrite-empty",
                    "用户不补材料：使用占位",
                    "纯改写不因材料不足扩大事实范围，空缺以明确占位呈现。",
                )
                return MaterialSufficiency.USE_PLACEHOLDERS, "改写原文为空，采用占位。"
            return MaterialSufficiency.ASK_ONE_QUESTION, None
        if downgrade:
            _adjudicate(
                adjudications,
                RulePriorityLayer.SOURCE_REALITY,
                "material-rewrite-downgrade",
                "改写路径材料不足：缩短范围",
                "纯改写不得因材料不足扩大事实范围，只处理已有内容。",
            )
            return MaterialSufficiency.SHORTEN, "材料不足，改写范围缩短至已有内容。"
        return MaterialSufficiency.SUFFICIENT, None

    if operation == Operation.EXPAND:
        if len(source) >= _MIN_EXPAND_SOURCE_LENGTH:
            return MaterialSufficiency.SUFFICIENT, None
        if downgrade:
            if source:
                _adjudicate(
                    adjudications,
                    RulePriorityLayer.SOURCE_REALITY,
                    "material-expand-downgrade",
                    "用户不补材料：缩短交付",
                    "扩写材料不足且用户不补充，交付边界明确的短稿。",
                )
                return MaterialSufficiency.SHORTEN, "扩写材料不足，改为交付短稿。"
            _adjudicate(
                adjudications,
                RulePriorityLayer.SOURCE_REALITY,
                "material-expand-empty",
                "用户不补材料：使用占位",
                "扩写材料几乎为空，以明确占位呈现待补内容。",
            )
            return MaterialSufficiency.USE_PLACEHOLDERS, "扩写材料为空，采用占位。"
        return MaterialSufficiency.ASK_ONE_QUESTION, None

    # generate_by_topic
    if len(source) >= _MIN_GENERATE_TOPIC_LENGTH:
        return MaterialSufficiency.SUFFICIENT, None
    if downgrade:
        if source:
            _adjudicate(
                adjudications,
                RulePriorityLayer.SOURCE_REALITY,
                "material-generate-downgrade",
                "用户不补材料：缩短交付",
                "按主题生成材料不足且用户不补充，交付边界明确的短稿。",
            )
            return MaterialSufficiency.SHORTEN, "生成材料不足，改为交付短稿。"
        _adjudicate(
            adjudications,
            RulePriorityLayer.SOURCE_REALITY,
            "material-generate-empty",
            "用户不补材料：使用占位",
            "生成材料为空，以明确占位呈现待补内容。",
        )
        return MaterialSufficiency.USE_PLACEHOLDERS, "生成材料为空，采用占位。"
    return MaterialSufficiency.ASK_ONE_QUESTION, None


def _detect_evidence_mode(
    text: str,
    explicit: bool,
    adjudications: list[ContractAdjudication],
) -> EvidenceRevisionMode:
    if explicit or (
        _EVIDENCE_SAFE_WORDS.search(text)
        and _EVIDENCE_SAFE_BLOCK.search(text) is None
    ):
        _adjudicate(
            adjudications,
            RulePriorityLayer.USER_AND_SAFETY,
            "evidence-safe-explicit",
            "证据安全修订模式已进入不可变任务快照",
            "只有用户明确选择时才调整结论强度，且该选择固化进快照。",
        )
        return EvidenceRevisionMode.EVIDENCE_SAFE
    return EvidenceRevisionMode.PRESERVE


def _detect_source_scope(text: str) -> SourceScope:
    if _SOURCE_SCOPE_WEB.search(text):
        return SourceScope.WEB
    if _SOURCE_SCOPE_KB.search(text):
        return SourceScope.KNOWLEDGE_BASE
    return SourceScope.ORIGINAL_ONLY


def _detect_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    for clause in re.split(r"[，,；;。\n]", text):
        normalized = clause.strip()
        if not normalized:
            continue
        if _CONSTRAINT_CLAUSE_RE.search(normalized):
            constraints.append(normalized)
    return list(dict.fromkeys(constraints))


def _validate_snapshot(previous: ExpressionTaskContract) -> None:
    """校验重试快照：Schema 版本必须已知且哈希自洽，不满足即抛错。"""
    if previous.schema_version not in KNOWN_SCHEMA_VERSIONS:
        raise ContractVersionError(
            f"契约版本 {previous.schema_version} 未知或已停用，执行前拒绝。"
        )
    if previous.compute_version_hash() != previous.version_hash:
        raise ContractVersionError("契约快照哈希不一致，拒绝重试。")


def compile_task_contract(request: CompileRequest) -> CompileResult:
    """编译版本化表达任务契约。

    快照路径：``request.previous_contract`` 非空时只做版本与哈希校验后
    原样复用，保证同一任务重试不因规则热更新漂移。
    """
    if request.previous_contract is not None:
        _validate_snapshot(request.previous_contract)
        record = ContractCompileRecord(
            compile_id=_token("comp"),
            schema_version=request.previous_contract.schema_version,
            version_hash=request.previous_contract.version_hash,
            surface=request.previous_contract.surface,
            operation=request.previous_contract.operation,
            rewrite_intensity=request.previous_contract.rewrite_intensity,
            material_sufficiency=request.previous_contract.material_sufficiency,
            asked_question=request.previous_contract.one_question is not None,
            profile_slice_id=request.previous_contract.profile_slice_id,
            profile_item_count=request.previous_contract.profile_item_count,
            degradation_reason=None,
            adjudications=[],
        )
        return CompileResult(
            contract=request.previous_contract, record=record, reused=True
        )

    text = (request.content or "").strip()
    adjudications: list[ContractAdjudication] = []

    surface = request.surface_hint
    if surface is None:
        # 信息性提问（解释/含义/用法）永远不是文章任务：即使命中“帮我”
        # 等直接请求词也不得送进文章流程。
        informational = bool(_INFORMATIONAL_QUERY_RE.search(text))
        surface = (
            Surface.ARTICLE
            if not informational
            and (
                REWRITE_ACTION_RE.search(text)
                or _EXPAND_WORDS.search(text)
                or _GENERATE_WORDS.search(text)
                or _ARTICLE_MARKERS.search(text)
                or _DIRECT_REQUEST_RE.search(text)
            )
            else Surface.CHAT
        )
        if surface == Surface.CHAT:
            _adjudicate(
                adjudications,
                RulePriorityLayer.SURFACE_MODE,
                "surface-chat",
                "未命中文章任务边界词，按普通聊天编译",
                "聊天与文章共用任务边界词汇但分别编译，不合并总提示词。",
            )

    has_source = bool((request.source_material or "").strip())
    operation = _detect_operation(text, has_source)
    reality_mode = _detect_reality(text)
    intensity = _detect_intensity(text, request.explicit_intensity, adjudications)
    speaker = _detect_speaker(text, surface)
    first_person = _detect_first_person(text, request.source_material, reality_mode, adjudications)
    hypothetical = _detect_hypothetical(text, reality_mode, adjudications)
    audience = _detect_audience(text)
    channel = _detect_channel(text)
    length_target = _detect_length(text)
    genre, genre_adj = _detect_genre(text)
    adjudications.extend(genre_adj)
    material, degradation = _detect_material(operation, request.source_material, request.downgrade_material, adjudications)
    evidence_mode = _detect_evidence_mode(text, request.explicit_evidence_safe, adjudications)
    source_scope = _detect_source_scope(text)
    constraints = _detect_constraints(text)

    # 三个强度档位都携带不伤害规则：原文已经自然时不得强行加场景、故事、
    # 比喻、第一人称或金句；轻度档额外锁定不自动升级。
    _adjudicate(
        adjudications,
        RulePriorityLayer.SOFT_STYLE,
        "intensity-no-harm",
        f"{intensity.value} 档携带不伤害规则",
        "原文已经自然时三个档位都不得强行加场景、故事、比喻、第一人称或金句。",
    )
    if intensity == RewriteIntensity.LIGHT:
        _adjudicate(
            adjudications,
            RulePriorityLayer.SOFT_STYLE,
            "intensity-light-no-upgrade",
            "轻度档不自动升级",
            "轻度改写保持轻度，不因上下文自动提高改写强度。",
        )

    one_question: str | None = None
    if material == MaterialSufficiency.ASK_ONE_QUESTION:
        one_question = _ONE_QUESTION_BY_OPERATION[operation]
        _adjudicate(
            adjudications,
            RulePriorityLayer.SOURCE_REALITY,
            "material-ask-one-question",
            f"材料不足，只追问一个最高价值问题：{one_question}",
            "扩写或生成不足时只返回一个最高价值问题，不一次追问多个材料问题。",
        )
        # 纯改写不得因材料不足扩大事实范围。
        if operation == Operation.REWRITE:
            _adjudicate(
                adjudications,
                RulePriorityLayer.SOURCE_REALITY,
                "material-rewrite-no-expand",
                "纯改写不因材料不足扩大事实范围",
                "改写的边界是用户提供的原文本身。",
            )

    contract = ExpressionTaskContract(
        schema_version=SCHEMA_VERSION,
        version_hash="",  # 占位，下方计算
        surface=surface,
        operation=operation,
        conversation_mode=request.conversation_mode if surface == Surface.CHAT else None,
        reality_mode=reality_mode,
        rewrite_intensity=intensity,
        speaker_position=speaker,
        first_person_permission=first_person,
        hypothetical_permission=hypothetical,
        audience=audience,
        channel=channel,
        length_target=length_target,
        genre=genre,
        material_sufficiency=material,
        source_scope=source_scope,
        evidence_revision_mode=evidence_mode,
        user_constraints=constraints,
        one_question=one_question,
        profile_slice_id=request.profile_slice_id,
        profile_item_count=request.profile_item_count,
        source_text_present=has_source,
    )
    contract.version_hash = contract.compute_version_hash()

    record = ContractCompileRecord(
        compile_id=_token("comp"),
        schema_version=SCHEMA_VERSION,
        version_hash=contract.version_hash,
        surface=contract.surface,
        operation=contract.operation,
        rewrite_intensity=contract.rewrite_intensity,
        material_sufficiency=contract.material_sufficiency,
        asked_question=one_question is not None,
        profile_slice_id=contract.profile_slice_id,
        profile_item_count=contract.profile_item_count,
        degradation_reason=degradation,
        adjudications=adjudications,
    )
    return CompileResult(contract=contract, record=record, reused=False)


__all__ = [
    "SCHEMA_VERSION",
    "KNOWN_SCHEMA_VERSIONS",
    "REWRITE_ACTION_RE",
    "ContractVersionError",
    "CompileRequest",
    "CompileResult",
    "compile_task_contract",
]
