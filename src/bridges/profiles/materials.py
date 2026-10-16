"""画像工单 10：指定材料更新个人记忆的确定性合同与读取端口模型。

本模块只承载纯规则与端口数据结构（不调用模型、不访问存储）：

- **指定更新指令**：普通上传、阅读与任务分析不触发画像；只有用户明确要求
  「根据材料更新背景/记忆」时才进入受控处理。归属不明或明确属于他人的材料
  不猜测归属（直接不处理，不把材料内容当成该用户的事实）。
- **材料就绪状态**：解析完成前不提取；不支持或不可读如实标注；失败如实
  失败。解析中的材料等待而不是当成空材料。
- **来源定位**：文本材料按页码/章节/片段定位，图片材料按「整图」定位；
  只允许引用实际读取到的片段，绝不伪造页码或引用。
- **多人材料守卫**：材料出现多个「姓名」字段（多人内容）时不猜测归属。

读取端口由组合根按账户与会话注入（``chat_attachments`` 绑定行 + 解析分块
或图片识别）；本模块不读取其他账户或其他会话的材料。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

#: 单次指定处理允许的材料条数上限（提示词与预算都按有界处理）。
MATERIAL_MAX_MATERIALS = 6
#: 进入模型提示词的材料文本总长度上限（超出部分不读取，不宣称已处理）。
MATERIAL_MAX_CHARS = 24000
#: 材料指令消息之前回看多少条消息以定位「随后明确要求」的材料。
MATERIAL_LOOKBACK_MESSAGES = 3
#: 等待材料解析完成的最大重试次数（每次重试都会重新检查解析状态）。
MATERIAL_MAX_WAIT_ATTEMPTS = 12
#: 单次材料提取允许的候选条数上限（合同与提示词共用）。
MATERIAL_MAX_ITEMS = 12

#: 材料相关稳定错误码（审计与新重试状态机共用；改名必须同步测试）。
MATERIAL_PARSING_ERROR = "profile_extraction_material_parsing"
MATERIAL_UNREADABLE_ERROR = "profile_extraction_material_unreadable"
MATERIAL_PARSE_FAILED_ERROR = "profile_extraction_material_parse_failed"
MATERIAL_MISSING_ERROR = "profile_extraction_material_missing"
MATERIAL_NOT_OWNED_ERROR = "profile_extraction_material_not_owned"
MATERIAL_INVALIDATED_ERROR = "profile_extraction_material_invalidated"
MATERIAL_SUBJECT_UNCERTAIN_ERROR = "profile_extraction_material_subject_uncertain"
MATERIAL_MULTIPLE_SUBJECTS_ERROR = "profile_extraction_material_multiple_subjects"
MATERIAL_READ_FAILED_ERROR = "profile_extraction_material_read_failed"
MATERIAL_NO_EXTRACTOR_ERROR = "profile_extraction_material_no_extractor"

#: 材料被删除或权限失效时进入采用/依据的稳定排除原因。
RECALL_MATERIAL_INVALID_REASON = "材料来源已删除或权限失效，暂不用于回答"
RECALL_MATERIAL_UNVERIFIABLE_REASON = "材料来源无法核验，暂不用于回答"


class MaterialKind(StrEnum):
    """指定材料的类别：文件类走解析分块读取，图片类走识别读取。"""

    FILE = "file"
    PHOTO = "photo"


class MaterialReadiness(StrEnum):
    """材料当前是否可用于提取（由解析状态机折算）。"""

    READY = "ready"
    PENDING = "pending"
    UNREADABLE = "unreadable"
    FAILED = "failed"


@dataclass(frozen=True)
class DesignatedMaterial:
    """一条被明确指定、绑定在当前账户/会话消息上的材料。

    只承载定位与状态：对象标识、绑定消息/会话、页序、文件名、媒体类型、
    内容版本哈希与就绪状态；材料正文绝不在此携带。
    """

    object_id: str
    message_id: str
    conversation_id: str
    ordinal: int
    filename: str
    media_type: str
    kind: MaterialKind
    content_hash: str | None
    readiness: MaterialReadiness


@dataclass(frozen=True)
class MaterialSegment:
    """材料中一段可定位的文本（解析分块或整图识别结果）。"""

    text: str
    page_number: int | None = None
    section_title: str | None = None
    part_index: int = 0
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class MaterialRead:
    """一次材料读取的确定结果：内容版本与可定位片段。"""

    content_hash: str | None
    segments: tuple[MaterialSegment, ...]

    @property
    def text(self) -> str:
        """按片段顺序拼接的完整可读文本（只含实际读取到的内容）。"""

        return "\n".join(segment.text for segment in self.segments)


@dataclass(frozen=True)
class MaterialUpdateDirective:
    """一次「据指定材料更新个人记忆」的确定性解析结果。"""

    own: bool
    #: 指令中出现的材料名词（如「简历」）；没有具体名词时为空。
    material_noun: str | None
    #: 指令是否自带材料指称（名词或本人所有格）；仅凭附带附件成立时为假。
    references_material: bool


class DesignatedMaterialReader(Protocol):
    """指定材料的读取端口（组合根按账户/会话注入真实实现）。

    ``list_designated_materials`` 返回本轮指令可用的材料：当前消息绑定的
    附件优先；当前消息没有附件时按指令的前文窗口回看「随后明确要求」的
    材料（有界条数）。草稿、其他会话与其他账户的材料绝不返回。
    ``read_material`` 返回解析完成后的可定位片段；解析中/不可读/失败返回
    ``None``（调用方按材料自身的就绪状态如实报告，不伪造内容）。
    """

    def list_designated_materials(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        lookback: int,
        max_materials: int,
    ) -> list[DesignatedMaterial]: ...

    def read_material(
        self, account_id: str, material: DesignatedMaterial
    ) -> MaterialRead | None: ...


#: 材料名词表（开放维护清单；未收录的新说法按普通消息处理）。
_MATERIAL_NOUNS = (
    "简历",
    "履历",
    "个人经历",
    "材料",
    "文件",
    "文档",
    "资料",
    "图片",
    "照片",
    "证书",
    "奖状",
    "成绩单",
    "推荐信",
    "报告",
    "论文",
)
_MATERIAL_NOUN_PATTERN = "(?:" + "|".join(_MATERIAL_NOUNS) + ")"
_MATERIAL_NOUN_RE = re.compile(_MATERIAL_NOUN_PATTERN)

#: 明确属于他人的材料所有格；命中即拒绝（不猜测归属）。
_THIRD_PARTY_OWNERS = (
    "朋友",
    "室友",
    "同学",
    "同事",
    "男朋友",
    "女朋友",
    "对象",
    "哥哥",
    "姐姐",
    "弟弟",
    "妹妹",
    "爸爸",
    "妈妈",
    "父母",
    "儿子",
    "女儿",
    "孩子",
    "家人",
    "亲戚",
    "别人",
    "他人",
)
_THIRD_PARTY_OWNER_PATTERN = "(?:" + "|".join(_THIRD_PARTY_OWNERS) + ")"
_THIRD_PARTY_MATERIAL_RE = re.compile(
    rf"(?:我(?:的)?)?{_THIRD_PARTY_OWNER_PATTERN}的"
    rf"[^，。；;！!？?\n]{{0,6}}{_MATERIAL_NOUN_PATTERN}"
)

#: 本人材料的明确指称：``我的简历`` / ``这是我的成绩单`` / ``我上传的材料``。
_OWN_MATERIAL_RE = re.compile(
    rf"(?:我的|本人的|自己的|这是我(?:的)?|我上传(?:的)?|我发(?:的)?|"
    rf"我提交(?:的)?|我刚(?:刚)?(?:上传|发|提交)(?:的)?)"
    rf"[^，。；;！!？?\n]{{0,6}}{_MATERIAL_NOUN_PATTERN}"
)

#: 更新动作 + 记忆目标；目标「背景」排除「背景知识」（那是查资料，不是画像）。
_ACTION_PATTERN = (
    r"(?:更新|补充|完善|整理|提取|记录|录入|写入|刷新|重新整理)"
)
_TARGET_PATTERN = (
    r"(?:个人记忆|长期记忆|记忆|画像|个人档案|档案|背景(?!知识)|个人资料|个人信息)"
)
_ACTION_TARGET_RE = re.compile(
    rf"{_ACTION_PATTERN}(?:一下|一次)?(?:我(?:的)?|本人的)?"
    rf"[^，。；;！!？?\n]{{0,4}}{_TARGET_PATTERN}"
)
_TARGET_ACTION_RE = re.compile(
    rf"(?:把|将)?(?:我(?:的)?|本人的)?{_TARGET_PATTERN}"
    rf"[^，。；;！!？?\n]{{0,4}}{_ACTION_PATTERN}(?:一下|一次)?"
)

#: 多人材料守卫：材料中多个不同的「姓名」字段视为多人内容，不猜测归属。
_NAME_FIELD_RE = re.compile(r"(?:姓\s*名|名字)\s*[:：]?\s*([\u4e00-\u9fff]{2,4})")

#: 末尾疑问语气（「我的背景需要更新吗」）：疑问句不是明确的执行指令。
_INTERROGATIVE_TAIL_RE = re.compile(r"(?:吗|呢)[。！!？?]?\s*$")


def parse_material_update_directive(content: str) -> MaterialUpdateDirective | None:
    """解析「根据材料更新个人记忆」的明确指令；不是指令返回 ``None``。

    - 必须出现更新动作与记忆目标（如「更新我的背景」「整理到个人记忆」）；
    - 出现明确属于他人的材料所有格时返回 ``own=False``（调用方立即拒绝，
      不读取、不处理，也不进入普通提取）；
    - 只有材料名词/本人所有格，或调用方确认消息附带材料时，指令才成立；
      仅动作+目标、也没有材料指称的消息由调用方按普通消息处理。
    """

    text = " ".join(str(content or "").split())
    if not text:
        return None
    if _INTERROGATIVE_TAIL_RE.search(text):
        return None
    if not (_ACTION_TARGET_RE.search(text) or _TARGET_ACTION_RE.search(text)):
        return None
    noun_match = _MATERIAL_NOUN_RE.search(text)
    noun = noun_match.group(0) if noun_match is not None else None
    if _THIRD_PARTY_MATERIAL_RE.search(text):
        # 明确他人的材料：这是受控指令，但归属不合法，调用方如实拒绝。
        return MaterialUpdateDirective(
            own=False, material_noun=noun, references_material=True
        )
    own_phrase = _OWN_MATERIAL_RE.search(text)
    return MaterialUpdateDirective(
        own=True,
        material_noun=noun,
        references_material=bool(noun or own_phrase),
    )


def detect_multiple_subjects(text: str) -> bool:
    """材料文本是否出现多个不同的姓名（多人内容守卫，不猜测归属）。"""

    names = {match.group(1) for match in _NAME_FIELD_RE.finditer(str(text or ""))}
    return len(names) >= 2


def material_locator(
    *,
    page_number: int | None = None,
    section_title: str | None = None,
    part_index: int = 0,
) -> str:
    """把解析分块的定位信息折算为人类可读定位（只描述真实位置）。"""

    parts: list[str] = []
    if page_number is not None:
        parts.append(f"第 {page_number} 页")
    if section_title:
        parts.append(str(section_title))
    if not parts:
        parts.append(f"原文片段 {part_index + 1}")
    return " · ".join(parts)


def photo_locator(ordinal: int) -> str:
    """图片材料的定位：当前材料是整图识别，没有更细的区域坐标。"""

    del ordinal
    return "整图"


def material_readiness(kind: MaterialKind, raw_status: str | None) -> MaterialReadiness:
    """把摄取原始状态折算为材料就绪状态。

    照片不走摄取解析（发送后按原图识别），直接视为可读；文件缺少解析
    记录时按「尚未完成」处理，绝不把未知当成可读。``empty``（解析完成但
    没有可用文字）按不可读如实标注，``error`` 按解析失败。
    """

    if kind is MaterialKind.PHOTO:
        return MaterialReadiness.READY
    if raw_status == "ready":
        return MaterialReadiness.READY
    if raw_status == "empty":
        return MaterialReadiness.UNREADABLE
    if raw_status == "error":
        return MaterialReadiness.FAILED
    # queued / parsing / processing / 缺失记录：等待解析完成。
    return MaterialReadiness.PENDING


def segment_for_quote(read: MaterialRead, quote: str) -> MaterialSegment | None:
    """在读取结果中定位唯一出现原话的片段；重复或找不到时返回 ``None``。

    引用只允许落在单个真实片段内；跨片段或重复出现无法消歧时不写入，
    也不伪造定位。
    """

    needle = str(quote or "").strip()
    if not needle:
        return None
    matches = [
        segment
        for segment in read.segments
        if segment.text.count(needle) == 1
    ]
    if len(matches) != 1:
        return None
    if sum(segment.text.count(needle) for segment in read.segments) != 1:
        return None
    return matches[0]


__all__ = [
    "DesignatedMaterial",
    "DesignatedMaterialReader",
    "MATERIAL_INVALIDATED_ERROR",
    "MATERIAL_LOOKBACK_MESSAGES",
    "MATERIAL_MAX_CHARS",
    "MATERIAL_MAX_ITEMS",
    "MATERIAL_MAX_MATERIALS",
    "MATERIAL_MAX_WAIT_ATTEMPTS",
    "MATERIAL_MISSING_ERROR",
    "MATERIAL_MULTIPLE_SUBJECTS_ERROR",
    "MATERIAL_NO_EXTRACTOR_ERROR",
    "MATERIAL_NOT_OWNED_ERROR",
    "MATERIAL_PARSE_FAILED_ERROR",
    "MATERIAL_PARSING_ERROR",
    "MATERIAL_READ_FAILED_ERROR",
    "MATERIAL_SUBJECT_UNCERTAIN_ERROR",
    "MATERIAL_UNREADABLE_ERROR",
    "MaterialKind",
    "MaterialRead",
    "MaterialReadiness",
    "MaterialSegment",
    "MaterialUpdateDirective",
    "RECALL_MATERIAL_INVALID_REASON",
    "RECALL_MATERIAL_UNVERIFIABLE_REASON",
    "detect_multiple_subjects",
    "material_locator",
    "material_readiness",
    "parse_material_update_directive",
    "photo_locator",
    "segment_for_quote",
]
