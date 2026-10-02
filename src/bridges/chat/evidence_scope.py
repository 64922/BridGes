"""已保存模块证据的读取范围声明（改进工单 14）。

追问「第二篇论文的结论」或「上次那个仓库的 README」时，模型看到的结果对象
来自消息上的模块投影。本模块从投影**如实**提取每个对象的来源时间、来源消息
锚点与实际读取范围（标题/摘要/已读页数/深查范围），供上下文编译器随对象一起
送入模型与材料清单：

- 仅有摘要/元数据时明确标注「未通读全文」，不得声称已读全文或原图细节；
- 证据的时间、页数、README/文件/深查范围按投影保存值原样呈现；
- 不读取新的外部来源，也不触发模块执行（重新读取已保存材料与刷新外部
  来源是两条路径，外部刷新按用户确认的模块选择合同执行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bridges.chat.repository import MessageRecord

_README_STATUS_LABELS = {
    "fetched": "已读 README 正文",
    "not_fetched": "未取得 README 正文",
    "missing": "仓库无 README",
    "failed": "README 获取失败",
    "truncated": "README 仅取到截断片段",
}
_READ_STATUS_LABELS = {
    "full_text": "已读岗位页面正文",
    "partial": "仅读取页面部分内容",
    "summary": "仅取得页面摘要",
    "links_only": "仅取得链接，未读取正文",
    "unread": "未读取正文",
    "failed": "读取失败",
}


@dataclass(frozen=True)
class EvidenceScope:
    """一条已保存证据对象的读取范围声明（脱敏，不含正文）。"""

    object_id: str
    source: str
    read_range: str
    source_time: str | None = None
    #: 证据来源消息 ID 前缀（消息纠错/删除后据此追溯；非对象自身版本号）。
    source_message_id: str | None = None

    def line(self) -> str:
        parts = [f"对象 {self.object_id}", self.read_range]
        if self.source_time:
            parts.append(f"来源时间 {self.source_time}")
        if self.source_message_id:
            parts.append(f"来源消息 {self.source_message_id}")
        return "；".join(parts)


def _time_of(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:19]
    return None


def _scope_for_paper(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.paper_search
    if not isinstance(payload, dict):
        return []
    source_time = _time_of(payload, "searched_at")
    scopes: list[EvidenceScope] = []
    for paper in payload.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        object_id = str(paper.get("arxiv_id") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        has_summary = bool(str(paper.get("summary_zh") or "").strip())
        read_range = (
            "读取范围：标题、摘要与来源元数据（未通读全文）"
            if has_summary
            else "读取范围：标题与来源元数据（无可用摘要，未通读全文）"
        )
        if paper.get("full_text_available"):
            read_range += "；仅有 PDF 链接可用，链接可得不等于已读全文"
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="paper",
                read_range=read_range,
                source_time=source_time,
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


def _scope_for_github(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.github_projects
    if not isinstance(payload, dict):
        return []
    source_time = _time_of(payload, "completed_at")
    scopes: list[EvidenceScope] = []
    for repo in payload.get("recommendations") or []:
        if not isinstance(repo, dict):
            continue
        object_id = str(repo.get("full_name") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        status = _README_STATUS_LABELS.get(
            str(repo.get("readme_status") or ""), "README 读取范围见来源投影"
        )
        files_read = len(repo.get("files_read") or [])
        checks = len(repo.get("implementation_checks") or [])
        read_range = (
            f"读取范围：{status}；实际读取文件 {files_read} 个；"
            f"实现核对 {checks} 项；元数据以外的内容以实际条数为准"
        )
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="github",
                read_range=read_range,
                source_time=source_time,
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


def _scope_for_tieba(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.tieba_research
    if not isinstance(payload, dict):
        return []
    scopes: list[EvidenceScope] = []
    for post in payload.get("confirmed_posts") or []:
        if not isinstance(post, dict):
            continue
        object_id = str(post.get("thread_id") or post.get("url") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        pages_read = int(post.get("pages_read") or 0)
        pages_limit = int(post.get("pages_limit") or 0)
        floor_min = post.get("floor_min")
        floor_max = post.get("floor_max")
        floors = (
            f"；楼层 {floor_min}-{floor_max}"
            if floor_min is not None and floor_max is not None
            else ""
        )
        read_range = (
            f"读取范围：实际读取 {pages_read}/{pages_limit} 页{floors}"
            "；未读取的页面不得当作已读"
        )
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="tieba",
                read_range=read_range,
                source_time=_time_of(post, "retrieved_at"),
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


def _scope_for_career(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.career_plan
    if not isinstance(payload, dict):
        return []
    scopes: list[EvidenceScope] = []
    for sample in payload.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        object_id = str(sample.get("url") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        status = _READ_STATUS_LABELS.get(
            str(sample.get("read_status") or ""), "读取范围见来源投影"
        )
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="career",
                read_range=f"读取范围：{status}；仅有摘要时不得声称已读正文",
                source_time=_time_of(sample, "retrieved_at"),
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


def _scope_for_resources(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.learning_resources
    if not isinstance(payload, dict):
        return []
    scopes: list[EvidenceScope] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("url") or item.get("title") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        unverified = bool(item.get("unverified"))
        read_range = (
            "读取范围：仅来源元数据（未观看/未通读，不得声称已读内容）"
            if unverified
            else "读取范围：来源元数据与书目信息（未通读全文）"
        )
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="resources",
                read_range=read_range,
                source_time=_time_of(payload, "searched_at"),
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


def _scope_for_arxiv(
    message: MessageRecord, object_ids: set[str] | None
) -> list[EvidenceScope]:
    payload = message.arxiv_search
    if not isinstance(payload, dict):
        return []
    scopes: list[EvidenceScope] = []
    for paper in [*(payload.get("papers") or []), *(payload.get("results") or [])]:
        if not isinstance(paper, dict):
            continue
        object_id = str(paper.get("arxiv_id") or "")
        if not object_id or (object_ids and object_id not in object_ids):
            continue
        has_abstract = bool(str(paper.get("abstract") or "").strip())
        read_range = (
            "读取范围：标题与已保存摘要原文（未通读全文）"
            if has_abstract
            else "读取范围：标题与来源元数据（未通读全文）"
        )
        scopes.append(
            EvidenceScope(
                object_id=object_id,
                source="arxiv",
                read_range=read_range,
                source_time=_time_of(paper, "published_at"),
                source_message_id=message.message_id[:12],
            )
        )
    return scopes


_SCOPE_EXTRACTORS = (
    _scope_for_paper,
    _scope_for_github,
    _scope_for_tieba,
    _scope_for_career,
    _scope_for_resources,
    _scope_for_arxiv,
)


def module_evidence_scopes(
    message: MessageRecord, *, object_ids: set[str] | None = None
) -> list[EvidenceScope]:
    """从一条消息的模块投影提取读取范围（只读；无对象匹配返回空）。"""
    scopes: list[EvidenceScope] = []
    for extractor in _SCOPE_EXTRACTORS:
        scopes.extend(extractor(message, object_ids))
    return scopes


__all__ = [
    "EvidenceScope",
    "module_evidence_scopes",
]
