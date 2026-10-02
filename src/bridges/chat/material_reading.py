"""统一对象读取计划与来源清单（改进工单 14）。

长会话中添加照片不能绕过上下文编译；追问旧图片、文件或模块细节时，系统
按本轮问题**选择性读取已保存的原始材料**，并把实际读取范围（原图、第几页、
第几段、摘要还是全文）如实带进最终载荷与脱敏材料清单：

- **当前照片**：以真实图片部件与数量进入同一最终预算；图状态只保存附件
  引用（对象 ID、消息 ID、内容哈希），绝不重复储存原图。
- **旧图追问**：只有本轮问题需要视觉细节且原图仍在本会话/本账户可读时，
  才重新读取原图；旧助手对图片的文字描述不构成本轮看图依据，也不能被
  记成「已读原图」。原图已删除或不可读时给出具体缺口，不用旧描述代替。
- **附件原文**：文件细节按检索命中的页码/章节取得**已解析原文整段**，
  补回被截断的末尾限定条件；未解析/不可读时如实说明。
- **模块证据**：读取范围用 :mod:`bridges.chat.evidence_scope` 从已保存投影
  如实标注（标题/摘要/已读页数/深查范围），仅有摘要不声称已读全文。

本模块是跨票接缝：15、学习与日常节点复用同一读取计划/成本/来源清单结构，
不各自另造状态权威。读取计划只读，不产生新用户条件，也不触发未被请求的
外部刷新或模块执行。
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from bridges.chat.attachments import PHOTO_MEDIA_TYPES, ChatAttachmentError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bridges.chat.attachments import ChatAttachmentService
    from bridges.chat.repository import MessageRecord

#: 读取计划合同版本（字段或选择语义变化时递增；编译记录与清单携带）。
MATERIAL_READ_VERSION = "material-read-v1"

#: 单轮最多重新读取的历史原图数量（当前轮照片不受此上限约束，仍受附件上限）。
MAX_REFERENCED_PHOTOS = 1
#: 历史照片扫描的消息窗口（从当前轮往回，避免长会话无界查询）。
PHOTO_SCAN_MESSAGE_LIMIT = 40
#: 单轮最多补回的已解析附件整段数量。
MAX_FILE_SEGMENTS = 3


class MaterialReadKind(StrEnum):
    """一次原始材料读取的来源类别。"""

    #: 当前轮随消息绑定的照片（必须进入本轮多模态载荷与预算）。
    CURRENT_PHOTO = "current_photo"
    #: 按本轮问题重新读取的同会话历史原图。
    REFERENCED_PHOTO = "referenced_photo"
    #: 按页码/章节补回的附件已解析原文整段。
    PARSED_SEGMENT = "parsed_segment"


@dataclass(frozen=True)
class PhotoRef:
    """会话内一张照片附件的可追溯引用（只含引用，不含原图字节）。"""

    object_id: str
    message_id: str
    media_type: str
    content_hash: str
    ordinal: int | None = None
    filename: str | None = None


@dataclass(frozen=True)
class MaterialRead:
    """一条计划/实际读取记录（脱敏；可进编译记录与材料清单）。"""

    kind: MaterialReadKind
    object_id: str
    message_id: str
    media_type: str
    content_hash: str
    read_range: str
    reason: str
    ordinal: int | None = None
    filename: str | None = None

    @property
    def material_id(self) -> str:
        return f"{self.kind.value}:{self.object_id}"

    @property
    def source_version(self) -> str:
        """来源版本：内容哈希（对象内容未变则版本不变）。"""
        return self.content_hash

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "object_id": self.object_id,
            "message_id": self.message_id,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "read_range": self.read_range,
            "reason": self.reason,
            "ordinal": self.ordinal,
            "filename": self.filename,
        }


@dataclass(frozen=True)
class FileSegment:
    """一条按页码/章节读取的附件原文整段。"""

    citation_id: str
    object_id: str
    filename: str
    content: str
    content_hash: str
    page_number: int | None = None
    section_title: str | None = None

    @property
    def read_range(self) -> str:
        if self.page_number is not None and self.section_title:
            return f"第 {self.page_number} 页 · {self.section_title}"
        if self.page_number is not None:
            return f"第 {self.page_number} 页整段"
        if self.section_title:
            return f"章节：{self.section_title}"
        return "原文整段"


@dataclass(frozen=True)
class MaterialReadPlan:
    """一轮读取计划：要读的对象、如实缺口与合同版本。"""

    reads: list[MaterialRead] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    contract_version: str = MATERIAL_READ_VERSION

    def image_reads(self) -> list[MaterialRead]:
        return [
            read
            for read in self.reads
            if read.kind
            in {MaterialReadKind.CURRENT_PHOTO, MaterialReadKind.REFERENCED_PHOTO}
        ]

    def image_count(self) -> int:
        return len(self.image_reads())

    def to_record(self) -> dict[str, Any]:
        return {
            "material_read_version": self.contract_version,
            "reads": [read.to_record() for read in self.reads],
            "gaps": list(self.gaps),
        }


#: 视觉细节追问的候选词（问题明确指向图片内容/局部）。
_IMAGE_TERMS = re.compile(r"图|照片|图片|截图|画面|拍的")
#: 视觉细节定位词（缺一则不认为在追问旧图细节）。
_VISUAL_ANCHORS = re.compile(
    r"这(?:张|幅|个)?图|那(?:张|幅|个)?图|上一?张|前一张|刚才那|之前那|最后一张"
    r"|左下|右下|左上|右上|角|小字|细节|局部|放大|里面|中间|画面"
    r"|第[一二三四五六七八九十两\d]+张"
)
#: 中文数字（一到九十九的常用写法）。
_CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_ORDINAL_RE = re.compile(r"第([一二三四五六七八九十两\d]+)张")
_PREVIOUS_PHOTO_RE = re.compile(
    r"上一?张|前一张|刚才那|之前那|最后一张|那张图|那张照片|那张图片"
)
_PARSED_PAGE_RE = re.compile(r"第\s*(\d+)\s*页")
_PARSED_SECTION_RE = re.compile(r"(?:章节|第[一二三四五六七八九十]+节)[:：]?\s*([^\s，。；、]+)")
#: 附件末尾限定条件追问词（按页码序取最后的已解析整段）。
_PARSED_TAIL_RE = re.compile(r"末尾|最后|结尾|末页|文末|落款")
#: 附件原文细节追问词（命中才补回整段，避免无谓放大读取范围）。
_FILE_DETAIL_RE = re.compile(
    r"第\s*\d+\s*页|第[一二三四五六七八九十]+页|末尾|最后|结尾|限定条件|原文"
    r"|细节|章节|段落|具体内容|写了什么"
)


def is_visual_detail_request(request: str) -> bool:
    """本轮问题是否要求图片/照片的视觉细节（需要实际原图依据）。"""
    text = request.strip()
    if not text or not _IMAGE_TERMS.search(text):
        return False
    return bool(_VISUAL_ANCHORS.search(text))


def is_file_detail_request(request: str) -> bool:
    """本轮问题是否指向附件原文细节（需要按页码/章节读取整段原文）。"""
    return bool(request.strip()) and bool(_FILE_DETAIL_RE.search(request))


def _parse_chinese_number(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CN_DIGITS.get(left, 1 if left == "" else None)
        ones = _CN_DIGITS.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CN_DIGITS.get(raw)


def _parse_ordinal(request: str) -> int | None:
    match = _ORDINAL_RE.search(request)
    if match is None:
        return None
    return _parse_chinese_number(match.group(1))


def plan_photo_reads(
    *,
    request: str,
    current_message_id: str,
    photo_refs: Sequence[PhotoRef],
) -> MaterialReadPlan:
    """规划本轮照片读取：当前照片 + 按问题选择性读取的同会话历史原图。

    选择规则（确定性、只读；不产生新用户条件）：

    - 当前轮绑定的照片全部进入本轮（附件数量上限由绑定层保证）；
    - 问题明确指向视觉细节时，按「第 N 张」序数或「上一张/那张」定位一张
      同会话历史原图；无法定位或原图已不在可读范围时给出具体缺口；
    - 问题不需要视觉细节时不额外读取历史原图，避免偷偷扩大读取范围。
    """
    ordered = list(photo_refs)
    current = [ref for ref in ordered if ref.message_id == current_message_id]
    historical = [ref for ref in ordered if ref.message_id != current_message_id]
    reads: list[MaterialRead] = [
        MaterialRead(
            kind=MaterialReadKind.CURRENT_PHOTO,
            object_id=ref.object_id,
            message_id=ref.message_id,
            media_type=ref.media_type,
            content_hash=ref.content_hash,
            read_range=(
                f"本轮原图（第 {ref.ordinal} 张）"
                if ref.ordinal is not None
                else "本轮原图"
            ),
            reason="当前轮随消息绑定的照片，以真实图片部件进入本轮预算。",
            ordinal=ref.ordinal,
            filename=ref.filename,
        )
        for ref in current
    ]
    gaps: list[str] = []
    if not is_visual_detail_request(request):
        return MaterialReadPlan(reads=reads, gaps=gaps)
    if not historical:
        if not current or _PREVIOUS_PHOTO_RE.search(request) or _ORDINAL_RE.search(request):
            gaps.append(
                "本会话没有可读取的更早原图（更早照片可能已删除、不可读或"
                "不属于本会话）；不得用旧助手描述代替看图。"
            )
        return MaterialReadPlan(reads=reads, gaps=gaps)

    targets: list[PhotoRef] = []
    ordinal = _parse_ordinal(request)
    if ordinal is not None:
        if 1 <= ordinal <= len(ordered):
            candidate = ordered[ordinal - 1]
            if candidate.message_id != current_message_id:
                targets = [candidate]
        else:
            gaps.append(
                f"本会话没有第 {ordinal} 张照片的原图（序号对不上或更早照片"
                "已删除）；不得用旧助手描述代替看图。"
            )
            return MaterialReadPlan(reads=reads, gaps=gaps)
    elif _PREVIOUS_PHOTO_RE.search(request) or not current:
        targets = historical[-MAX_REFERENCED_PHOTOS:]

    for target in targets:
        reads.append(
            MaterialRead(
                kind=MaterialReadKind.REFERENCED_PHOTO,
                object_id=target.object_id,
                message_id=target.message_id,
                media_type=target.media_type,
                content_hash=target.content_hash,
                read_range=(
                    f"历史原图（消息 {target.message_id}"
                    + (
                        f"，第 {target.ordinal} 张"
                        if target.ordinal is not None
                        else ""
                    )
                    + "）"
                ),
                reason="本轮问题指向历史原图的视觉细节，重新读取同会话原图。",
                ordinal=target.ordinal,
                filename=target.filename,
            )
        )
    return MaterialReadPlan(reads=reads, gaps=gaps)


def collect_photo_refs(
    messages: Sequence[MessageRecord],
    *,
    attachments: ChatAttachmentService | None,
    account_id: str,
    conversation_id: str,
    current_message_id: str,
    request: str,
) -> list[PhotoRef]:
    """从会话消息收集照片附件引用（当前轮总是收集；历史按问题需要）。

    只读、有界：历史扫描最多 :data:`PHOTO_SCAN_MESSAGE_LIMIT` 条用户消息，
    每条经账户+会话作用域的附件列表读取；跨账户/跨会话对象不会被收集。
    """
    if attachments is None:
        return []
    refs: list[PhotoRef] = []

    def collect(message_id: str) -> None:
        for record in attachments.list_for_message(
            account_id, conversation_id, message_id
        ):
            if record.media_type not in PHOTO_MEDIA_TYPES:
                continue
            refs.append(
                PhotoRef(
                    object_id=record.object_id,
                    message_id=message_id,
                    media_type=record.media_type,
                    content_hash=record.content_hash,
                    ordinal=record.ordinal,
                    filename=record.original_filename,
                )
            )

    collect(current_message_id)
    if not is_visual_detail_request(request):
        return refs
    seen = 0
    historical: list[PhotoRef] = []
    for message in reversed(messages):
        if seen >= PHOTO_SCAN_MESSAGE_LIMIT:
            break
        if message.message_id == current_message_id:
            continue
        if message.role.value != "user":
            continue
        seen += 1
        before = len(refs)
        collect(message.message_id)
        historical.extend(refs[before:])
    # 历史按会话时间序（reversed 之后反转回来），当前轮固定在最后。
    historical.reverse()
    current = [ref for ref in refs if ref.message_id == current_message_id]
    return [*historical, *current]


def read_photo_payloads(
    attachments: ChatAttachmentService,
    *,
    account_id: str,
    conversation_id: str,
    reads: Sequence[MaterialRead],
) -> tuple[list[dict[str, Any]], list[MaterialRead], list[str]]:
    """按读取计划重新读取原图；返回 (图片部件, 实际读到, 缺口)。

    每次读取都经附件服务的账户+会话作用域校验；跨账户、跨会话或已删除的
    对象无法读取，转为如实缺口，绝不用历史描述或视觉摘要顶替原图。
    """
    parts: list[dict[str, Any]] = []
    delivered: list[MaterialRead] = []
    gaps: list[str] = []
    for read in reads:
        try:
            record, content = attachments.download(
                account_id, conversation_id, read.object_id
            )
        except ChatAttachmentError as error:
            gaps.append(f"{read.read_range or '原图'}当前无法读取（{error.message}）")
            continue
        if record.message_id != read.message_id:
            gaps.append(
                f"{read.read_range or '原图'}与来源消息不匹配，已拒绝读取。"
            )
            continue
        encoded = base64.b64encode(content).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{record.media_type};base64,{encoded}"
                },
            }
        )
        delivered.append(read)
    return parts, delivered, gaps


def select_file_segments(
    rows: Iterable[Mapping[str, Any]],
    *,
    request: str,
    had_attachment_citations: bool,
    limit: int = MAX_FILE_SEGMENTS,
) -> tuple[list[FileSegment], list[str]]:
    """按本轮问题从已解析附件整段行中选择读取范围。

    ``rows`` 由检索域按账户作用域与对象存活过滤后给出（含整段原文）。
    问题点名页码时优先该页；否则按检索位次取前 ``limit`` 段。命中引用但
    整段已不可读时给具体缺口。
    """
    segments = [
        FileSegment(
            citation_id=str(row["citation_id"]),
            object_id=str(row["object_id"]),
            filename=str(row["filename"]),
            content=str(row["content"]),
            content_hash=str(row["content_hash"]),
            page_number=(
                int(row["page_number"]) if row["page_number"] is not None else None
            ),
            section_title=(
                str(row["section_title"])
                if row["section_title"] is not None
                else None
            ),
        )
        for row in rows
    ]
    if not segments:
        if had_attachment_citations:
            return [], [
                "本轮引用的附件原文当前不可读取（来源可能已删除或权限已变化）。"
            ]
        return [], []
    page_match = _PARSED_PAGE_RE.search(request)
    if page_match is not None:
        page = int(page_match.group(1))
        matched = [item for item in segments if item.page_number == page]
        if matched:
            return matched[:limit], []
        return [], [
            f"未能取到第 {page} 页的已解析原文（该页可能未解析、内容为空或"
            "来源已删除）；不要用其他页或摘要代答。"
        ]
    section_match = _PARSED_SECTION_RE.search(request)
    if section_match is not None:
        needle = section_match.group(1)
        matched = [
            item
            for item in segments
            if item.section_title is not None and needle in item.section_title
        ]
        if matched:
            return matched[:limit], []
        return segments[:limit], [
            f"未能定位章节「{needle}」的已解析原文（可能未解析或标题不一致）；"
            "以下为检索命中的其他原文片段，页码/章节按实际标注。"
        ]
    if _PARSED_TAIL_RE.search(request):
        ordered = sorted(
            segments,
            key=lambda item: (item.page_number is None, item.page_number or 0),
        )
        return ordered[-limit:], []
    return segments[:limit], []


def read_evidence_block(
    *,
    photo_reads: Sequence[MaterialRead] = (),
    photo_gaps: Sequence[str] = (),
    file_segments: Sequence[FileSegment] = (),
    file_gaps: Sequence[str] = (),
) -> str | None:
    """实际读取回执与边界说明（系统材料块，计入最终预算与清单）。"""
    lines: list[str] = []
    if photo_reads:
        lines.append(
            "本轮按用户问题实际读取的同会话原图（是原图本身，不是历史文字"
            "描述，也不是任何视觉摘要；只有这里列出的原图可作为看图依据）："
        )
        for read in photo_reads:
            label = f"「{read.filename}」" if read.filename else ""
            lines.append(
                f"- {label}{read.read_range}；附件对象 {read.object_id}；"
                f"来源消息 {read.message_id}；内容版本 "
                f"{read.content_hash[:12]}；读取原因：{read.reason}"
            )
    if file_segments:
        lines.append(
            "以下是按本轮问题从已解析附件原文中读取的完整片段（读取范围"
            "如实标注；只有这里列出的页码/章节可作为原文依据）："
        )
        for segment in file_segments:
            lines.append(
                f"- 「{segment.filename}」{segment.read_range}；内容版本 "
                f"{segment.content_hash[:12]}：{segment.content}"
            )
    gaps = [*photo_gaps, *file_gaps]
    if gaps:
        lines.append("本轮未能读取到的材料（如实说明，不得用旧描述代替）：")
        lines.extend(f"- {gap}" for gap in gaps)
    if not lines:
        return None
    lines.append(
        "历史助手的文字（包括对图片、附件的转述）只是当时的描述，不构成本轮"
        "已读原图或原文的依据；涉及图片局部或附件细节时，只能依据上面实际"
        "列出的原图/原文回答；未列出时明确说明无法判断或本轮无法读取。"
    )
    return "\n".join(lines)


def material_reads_from_record(
    context_budget: Mapping[str, Any] | None,
) -> list[MaterialRead]:
    """从编译记录（``context_budget``）还原本轮读取计划。"""
    if not context_budget:
        return []
    plan = context_budget.get("material_read_plan")
    if not isinstance(plan, Mapping):
        return []
    raw_reads = plan.get("reads")
    if not isinstance(raw_reads, list):
        return []
    reads: list[MaterialRead] = []
    for item in raw_reads:
        if not isinstance(item, Mapping):
            continue
        kind_raw = item.get("kind")
        try:
            kind = MaterialReadKind(str(kind_raw))
        except ValueError:
            continue
        reads.append(
            MaterialRead(
                kind=kind,
                object_id=str(item.get("object_id") or ""),
                message_id=str(item.get("message_id") or ""),
                media_type=str(item.get("media_type") or ""),
                content_hash=str(item.get("content_hash") or ""),
                read_range=str(item.get("read_range") or ""),
                reason=str(item.get("reason") or ""),
                ordinal=(
                    int(item["ordinal"])
                    if isinstance(item.get("ordinal"), int)
                    else None
                ),
                filename=(
                    str(item["filename"])
                    if item.get("filename") is not None
                    else None
                ),
            )
        )
    return reads


def material_read_gaps_from_record(
    context_budget: Mapping[str, Any] | None,
) -> list[str]:
    """从编译记录取编译期已经确定的读取缺口（如历史原图已删除）。"""
    if not context_budget:
        return []
    plan = context_budget.get("material_read_plan")
    if not isinstance(plan, Mapping):
        return []
    raw_gaps = plan.get("gaps")
    if not isinstance(raw_gaps, list):
        return []
    return [str(gap) for gap in raw_gaps if isinstance(gap, str) and gap]


def image_attachment_ids_from_plan(
    plan: Mapping[str, Any] | None,
) -> list[str]:
    """从读取计划记录取计划进入载荷的原图附件 ID（照片类别，保序去重）。"""
    if not isinstance(plan, Mapping):
        return []
    raw_reads = plan.get("reads")
    if not isinstance(raw_reads, list):
        return []
    attachment_ids: list[str] = []
    for item in raw_reads:
        if not isinstance(item, Mapping):
            continue
        kind_raw = item.get("kind")
        try:
            kind = MaterialReadKind(str(kind_raw))
        except ValueError:
            continue
        if kind not in {
            MaterialReadKind.CURRENT_PHOTO,
            MaterialReadKind.REFERENCED_PHOTO,
        }:
            continue
        object_id = str(item.get("object_id") or "")
        if object_id and object_id not in attachment_ids:
            attachment_ids.append(object_id)
    return attachment_ids


__all__ = [
    "MATERIAL_READ_VERSION",
    "MAX_FILE_SEGMENTS",
    "MAX_REFERENCED_PHOTOS",
    "PHOTO_SCAN_MESSAGE_LIMIT",
    "FileSegment",
    "MaterialRead",
    "MaterialReadKind",
    "MaterialReadPlan",
    "PhotoRef",
    "collect_photo_refs",
    "image_attachment_ids_from_plan",
    "is_file_detail_request",
    "is_visual_detail_request",
    "material_read_gaps_from_record",
    "material_reads_from_record",
    "plan_photo_reads",
    "read_evidence_block",
    "read_photo_payloads",
    "select_file_segments",
]
