"""统一聊天入口中的文章人味化自然语言路由。

路由只负责确定能力、来源和任务契约，不生成文章，也不调用模型。它必须
宁可放弃模糊命中，也不能把普通聊天或解释性问题误送进人味化流程。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    HumanizerPath,
    HumanizerRouteDecision,
    HumanizerRouteSource,
    HumanizerSkillInput,
    HumanizerTaskContract,
)

HUMANIZER_ROUTE_VERSION = "humanizer-route-v1"
DEFAULT_AUDIENCE = "普通读者"
DEFAULT_CHANNEL = "聊天回复"
DEFAULT_LENGTH_TARGET = "保持原文长度"

_ACTION_RE = re.compile(
    r"润色|改写|重写|人味化|去(?:掉|除)?\s*(?:模板腔|AI味|ai味)|"
    r"模板腔|AI\s*味|改得更自然|改成更自然|更像人写|口语化|顺一顺|顺一下|"
    r"调整表达|改动措辞",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(
    r"文章|原文|文本|文字|段落|摘要|邮件|报告|演讲稿|讲稿|论文|稿子|这段话|内容"
)
_DIRECT_REQUEST_RE = re.compile(
    r"帮我|给我|请(?:帮我|给我|把|给|直接|润色|改写|重写)|把|让|需要|希望|想要|直接"
)
_REWRITE_COMMAND_RE = re.compile(
    r"润色|改写|重写|去掉|去除|去(?:模板腔|AI\s*味|ai\s*味)|改得更自然|改成更自然|"
    r"更像人写|口语化|顺一顺|顺一下|调整表达|改动措辞|文章人味化"
)
_INFORMATIONAL_RE = re.compile(r"是什么意思|是什么|怎么用|怎么收费|怎么理解|含义|如何写|怎么写")
_NEGATION_RE = re.compile(r"不要|不想|别|不用|无需|不是要|不需要")

_SOURCE_MARKERS = (
    re.compile(r"(?:原文|正文|文本|内容)\s*(?:是|为|如下)?\s*[：:]\s*(.+)$", re.DOTALL),
    re.compile(r"(?:下面(?:这段(?:话|文字)?)?|如下)\s*[：:]\s*(.+)$", re.DOTALL),
)
_AUDIENCE_RE = re.compile(
    r"(?:面向|针对|目标受众(?:是|为)?|受众(?:是|为)?)\s*"
    r"([^，,。；;\n：:]+)"
)
_LENGTH_RE = re.compile(
    r"(?:不超过|不多于|控制在|长度为|长度|字数|约)\s*"
    r"(\d+\s*(?:字|词|分钟|页))"
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
    contract = HumanizerTaskContract(
        path=HumanizerPath.REWRITE,
        genre=_genre(task_text),
        audience=_audience(task_text),
        channel=_channel(task_text),
        length_target=_length_target(task_text),
        hard_constraints=_hard_constraints(task_text),
        source_text=source_text,
        source_label=(
            f"知识库文档：{knowledge_base_reference}"
            if knowledge_base_reference
            else "聊天内原文"
            if source_text
            else None
        ),
        knowledge_base_reference=knowledge_base_reference,
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
        ),
        use_knowledge_base=bool(knowledge_base_reference),
    )


def _is_rewrite_intent(text: str) -> bool:
    match = _ACTION_RE.search(text)
    if match is None:
        return False
    before = text[max(0, match.start() - 10) : match.start()]
    if _NEGATION_RE.search(before):
        return False
    if _INFORMATIONAL_RE.search(text) and not _DIRECT_REQUEST_RE.search(text):
        return False
    if not _REWRITE_COMMAND_RE.search(text):
        return False
    return bool(_ARTICLE_RE.search(text) or _DIRECT_REQUEST_RE.search(text))


def _split_task_and_source(text: str) -> tuple[str, str | None]:
    for marker in _SOURCE_MARKERS:
        match = marker.search(text)
        if match and match.group(1).strip():
            return text[: match.start()].strip(), match.group(1).strip()
    colon = re.search(r"[：:]", text)
    if colon and _ACTION_RE.search(text[: colon.start()]) and text[colon.end() :].strip():
        return text[: colon.start()].strip(), text[colon.end() :].strip()
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if _ACTION_RE.search(first) and rest.strip():
            return first.strip(), rest.strip()
    return text, None


def _genre(task_text: str) -> Genre:
    if re.search(r"演讲稿|课程讲稿|课堂讲稿|讲稿", task_text):
        return Genre.LECTURE_SCRIPT
    if re.search(r"科研汇报|科研报告|研究报告|实验报告", task_text):
        return Genre.RESEARCH_REPORT
    if re.search(r"论文|论文写作|论文摘要", task_text):
        return Genre.PAPER_ASSIST
    return Genre.POPULAR_SCIENCE


def _audience(task_text: str) -> str:
    match = _AUDIENCE_RE.search(task_text)
    return match.group(1).strip() if match else DEFAULT_AUDIENCE


def _channel(task_text: str) -> str:
    for channel in ("公众号", "邮件", "组会", "课堂", "演讲", "报告", "知乎", "小红书"):
        if channel in task_text:
            return channel
    return DEFAULT_CHANNEL


def _length_target(task_text: str) -> str:
    match = _LENGTH_RE.search(task_text)
    if match:
        return re.sub(r"\s+", " ", match.group(0).strip())
    return DEFAULT_LENGTH_TARGET


def _hard_constraints(task_text: str) -> list[str]:
    constraints: list[str] = []
    for clause in re.split(r"[，,；;。\n]", task_text):
        normalized = clause.strip()
        if not normalized:
            continue
        if re.search(r"必须|不得|不能|不要|保留|原样|保持", normalized):
            constraints.append(normalized)
    return list(dict.fromkeys(constraints))


def _knowledge_base_reference(task_text: str) -> str | None:
    match = _KB_REFERENCE_RE.search(task_text)
    if match:
        return match.group(1).strip() or None
    return None


def _requests_external_evidence(task_text: str) -> bool:
    return bool(_EXTERNAL_EVIDENCE_RE.search(task_text))
