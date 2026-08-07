"""分层本地检索服务：作用域解析、融合检索、持久化与引用详情（Issue 20）。

每轮按「当前附件 → 当前项目文件 → 已授权全局知识库」确定候选作用域，
只查询当前账户资源。检索（FTS5 BM25 + 向量余弦按固定合同融合）发生在
生成开始前，结果固化为检索轮次与引用（展示数据不随索引重建漂移）；
点击引用时按当前对象状态实时校验授权，对象已删除或权限变化时返回
安全中文状态。用户发送前关闭全局知识库时，本轮不查询、不记录、不
引用该层任何候选。查询向量凭据来源是唯一的全局百炼运行凭据（GQ-05）：
可用性由运行时是否成功构造全局 Embedding 端口决定，不再读取账户
凭据或探测快照；向量调用失败时本轮诚实回退到关键词检索并注明原因。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from bridges.chat.attachments_repository import AttachmentRepository
from bridges.chat.repository import ConversationRepository
from bridges.contracts.retrieval import (
    CitationAccessStatus,
    CitationDetailProjection,
    CitationProjection,
    RetrievalLayerResult,
    RetrievalLayerStatus,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.ingestion.embedding import EmbeddingError, EmbeddingPort
from bridges.retrieval.repository import RetrievalRepository
from bridges.retrieval.search import (
    LAYER_QUOTAS,
    MAX_CITATIONS,
    FusedCandidate,
    LayerSearchResult,
    clean_query,
    compute_sufficiency,
    fuse_layer,
    layer_has_conflict,
    merge_layers,
    search_keyword,
    search_vectors,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.repository import BridgesObjectRepository

#: 引用片段的最大长度（展示数据生成时固化，不漂移）。
SNIPPET_MAX_LEN = 240
#: 作用域顺序（检索/展示/去重优先级共用）。
_LAYER_ORDER = (
    RetrievalSourceLayer.ATTACHMENT,
    RetrievalSourceLayer.PROJECT,
    RetrievalSourceLayer.KNOWLEDGE_BASE,
)

#: 索引不可用时的统一中文说明。
_INDEX_UNAVAILABLE_NOTE = "本地索引不可用，暂无法检索本地材料，请稍后重试。"
#: 向量检索不可用时的回退说明。
_VECTOR_UNAVAILABLE_NOTE = "向量检索暂不可用，本轮仅使用关键词检索。"


class RetrievalError(Exception):
    """检索领域的可预期失败（由 API 层映射为 HTTP 状态与错误体）。"""

    def __init__(self, code: str, message: str, status_code: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


class LayeredRetrievalService:
    """三层本地检索的编排与投影面（API 进程只读检索 + 写入检索记录）。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        embedding: EmbeddingPort | None = None,
        repository: RetrievalRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        attachment_repository: AttachmentRepository | None = None,
        object_repository: BridgesObjectRepository | None = None,
    ) -> None:
        self._database = database
        self._embedding = embedding
        self._repository = repository or RetrievalRepository(database)
        # Issue 45：跨域读取经属主仓库注入——conversations（chat 域）、
        # chat_attachments（chat 域）可缺省构造；objects 属主仓库需要
        # 对象库，必须由调用方注入。
        self._conversations = conversation_repository or ConversationRepository(database)
        self._attachments = attachment_repository or AttachmentRepository(database)
        if object_repository is None:
            raise TypeError("object_repository 必须注入（objects 属主仓库，需对象库）。")
        self._objects = object_repository

    # ------------------------------------------------------------------
    # 每轮检索编排
    # ------------------------------------------------------------------

    def run_round(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message_id: str | None,
        query: str,
        *,
        use_knowledge_base: bool,
    ) -> RetrievalRoundProjection | None:
        """为一条助手消息执行一轮分层检索并固化结果。

        返回 None 表示本轮没有可检索作用域（无附件、未归属项目且知识库
        无材料/被关闭），前端不渲染检索卡；其余情况总是返回结构化轮次
        投影，绝不把无命中伪装成成功。
        """
        # conversations 属 chat 域：经属主 ConversationRepository 读取
        # 归属项目，对话不存在时本轮无作用域可检索。
        conversation = self._conversations.get_conversation(
            account_id, conversation_id
        )
        if conversation is None:
            return None
        project_id = conversation.project_id
        attachment_ids = self._attachment_ids(
            account_id, conversation_id, user_message_id
        )
        layers = self._resolve_layers(
            account_id,
            attachment_ids=attachment_ids,
            project_id=project_id,
            use_knowledge_base=use_knowledge_base,
        )
        # 没有任何层有已就绪材料时跳过本轮（无检索作用域，不假装成功）。
        if not any(layer["ready_document_ids"] for layer in layers.values()):
            return None

        active = self._active_version(account_id)
        if active is None:
            return self._persist_round(
                account_id,
                conversation_id,
                assistant_message_id,
                user_message_id,
                use_knowledge_base=use_knowledge_base,
                index_version_id=None,
                sufficiency=RetrievalSufficiency.INDEX_UNAVAILABLE,
                layers=self._layer_results(
                    layers, index_unavailable=True, notes={}
                ),
                candidates=[],
                note=_INDEX_UNAVAILABLE_NOTE,
            )

        version_id = str(active["version_id"])
        cleaned_query = clean_query(query)
        keyword_lists: dict[RetrievalSourceLayer, list[Any]] = {}
        vector_rows: dict[RetrievalSourceLayer, list[dict[str, Any]]] = {}
        layer_search_failed = False
        for layer in _LAYER_ORDER:
            document_ids = layers[layer]["ready_document_ids"]
            if not document_ids:
                continue
            try:
                keyword_lists[layer] = search_keyword(
                    self._database.connection,
                    account_id=account_id,
                    version_id=version_id,
                    document_ids=document_ids,
                    query=cleaned_query,
                )
            except sqlite3.Error:
                # 单层关键词检索失败不阻塞整轮：该层按索引不可用呈现，
                # 且只要任一启用层检索失败就传导为整体索引不可用信号
                # （AC-07：索引不可用输出结构化充足性，不以无命中代替）。
                layer_search_failed = True
                layers[layer]["status"] = RetrievalLayerStatus.INDEX_UNAVAILABLE
                layers[layer]["note"] = "该层关键词检索失败，请稍后重试。"
            vector_rows[layer] = self._vector_rows(account_id, version_id, document_ids)

        search_results: dict[RetrievalSourceLayer, LayerSearchResult] = {}
        vector_note: str | None = None
        query_vector: list[float] | None = None
        if self._embedding is not None:
            try:
                embedded = self._embedding.embed(account_id, [cleaned_query])
                query_vector = list(embedded[0]) if embedded else None
            except EmbeddingError as exc:
                # GQ-05：向量化调用失败 → 本轮诚实回退关键词检索，并把
                # 端口给出的可操作原因（全局配置/权限等）呈现给用户。
                vector_note = f"{_VECTOR_UNAVAILABLE_NOTE}；{exc.message}"
        if query_vector is None:
            vector_note = vector_note or _VECTOR_UNAVAILABLE_NOTE
        for layer in _LAYER_ORDER:
            if layers[layer]["status"] != RetrievalLayerStatus.OK:
                continue
            vector_hits = (
                search_vectors(vector_rows[layer], query_vector)
                if query_vector is not None
                else []
            )
            search_results[layer] = LayerSearchResult(
                layer=layer,
                keyword_hits=keyword_lists.get(layer, []),
                vector_hits=vector_hits,
                vector_unavailable_reason=(
                    vector_note if query_vector is None else None
                ),
            )

        fused_by_layer: list[tuple[RetrievalSourceLayer, list[FusedCandidate]]] = []
        conflicts = False
        notes: dict[RetrievalSourceLayer, str] = {}
        for layer in _LAYER_ORDER:
            if layer not in search_results:
                continue
            result = search_results[layer]
            conflicts = conflicts or layer_has_conflict(
                result.keyword_hits, result.vector_hits
            )
            fused = fuse_layer(
                result.keyword_hits,
                result.vector_hits,
                quota=LAYER_QUOTAS[layer],
            )
            fused_by_layer.append((layer, fused))
            layers[layer]["candidates"] = len(fused)
            if result.vector_unavailable_reason is not None:
                notes[layer] = result.vector_unavailable_reason

        merged = merge_layers(fused_by_layer, max_citations=MAX_CITATIONS)
        candidates = [candidate for _, candidate in merged]
        # 任一启用层检索失败且整体无候选 → 索引不可用（不以无命中代替）；
        # 其余层仍有候选时保留其充足性，失败层状态在层行如实呈现。
        sufficiency = compute_sufficiency(
            index_unavailable=layer_search_failed and not candidates,
            total_candidates=len(candidates),
            conflicts=conflicts,
        )
        note = _sufficiency_note(sufficiency)
        if vector_note is not None:
            note = f"{note}；{vector_note}"

        return self._persist_round(
            account_id,
            conversation_id,
            assistant_message_id,
            user_message_id,
            use_knowledge_base=use_knowledge_base,
            index_version_id=version_id,
            sufficiency=sufficiency,
            layers=self._layer_results(layers, index_unavailable=False, notes=notes),
            candidates=[(layer, candidate) for layer, candidate in merged],
            note=note,
        )

    # ------------------------------------------------------------------
    # 投影
    # ------------------------------------------------------------------

    def round_projection(
        self, account_id: str, message_id: str
    ) -> RetrievalRoundProjection | None:
        """返回消息绑定的检索轮次投影；无轮次返回 None（不渲染卡片）。"""
        row = self._repository.round_row(account_id, message_id)
        if row is None:
            return None
        citations = self._repository.citation_rows(
            account_id, str(row["round_id"])
        )
        return self._project_round(row, citations)

    def citation_detail(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        citation_id: str,
    ) -> CitationDetailProjection:
        """返回单条引用的证据详情（实时校验授权后给出打开入口）。

        引用不存在或不属于当前对话/消息时抛 ``citation_not_found`` 404，
        不泄漏存在性。
        """
        row = self._repository.citation_row(account_id, citation_id)
        if row is None:
            raise RetrievalError(
                "citation_not_found", "引用不存在或没有访问权限。"
            )
        if str(row["message_id"]) != message_id or str(
            row["conversation_id"]
        ) != conversation_id:
            raise RetrievalError(
                "citation_not_found", "引用不存在或没有访问权限。"
            )
        citation = self._citation_from_row(row)
        access_status, access_message, download_url = self._access_state(
            account_id, conversation_id, row
        )
        return CitationDetailProjection(
            citation=citation,
            access_status=access_status,
            access_message=access_message,
            download_url=download_url,
        )

    # ------------------------------------------------------------------
    # 作用域解析
    # ------------------------------------------------------------------

    def _attachment_ids(
        self, account_id: str, conversation_id: str, user_message_id: str | None
    ) -> list[str]:
        """本轮明确附加的文件（绑定到所属用户消息，本轮授权）。"""
        if user_message_id is None:
            return []
        return self._attachments.object_ids_for_message(
            account_id, conversation_id, user_message_id
        )

    def _resolve_layers(
        self,
        account_id: str,
        *,
        attachment_ids: list[str],
        project_id: str | None,
        use_knowledge_base: bool,
    ) -> dict[RetrievalSourceLayer, dict[str, Any]]:
        """解析三层作用域：每层启用状态、已就绪文档与中文说明。"""
        layers: dict[RetrievalSourceLayer, dict[str, Any]] = {
            RetrievalSourceLayer.ATTACHMENT: {
                "status": RetrievalLayerStatus.DISABLED,
                "note": "本轮未附加文件。",
                "ready_document_ids": [],
                "candidates": 0,
                "stale": False,
            },
            RetrievalSourceLayer.PROJECT: {
                "status": RetrievalLayerStatus.DISABLED,
                "note": "该对话未归属学习项目。",
                "ready_document_ids": [],
                "candidates": 0,
                "stale": False,
            },
            RetrievalSourceLayer.KNOWLEDGE_BASE: {
                "status": RetrievalLayerStatus.DISABLED,
                "note": "全局知识库本轮已关闭。",
                "ready_document_ids": [],
                "candidates": 0,
                "stale": False,
            },
        }
        if attachment_ids:
            layers[RetrievalSourceLayer.ATTACHMENT].update(
                status=RetrievalLayerStatus.NO_MATERIAL,
                note="附件仍在处理中或暂无可检索内容。",
            )
            layers[RetrievalSourceLayer.ATTACHMENT][
                "ready_document_ids"
            ] = self._ready_documents(
                account_id,
                source="chat_attachment",
                object_ids=attachment_ids,
            )
            layers[RetrievalSourceLayer.ATTACHMENT]["stale"] = self._has_stale_documents(
                account_id,
                source="chat_attachment",
                object_ids=attachment_ids,
            )
        if project_id is not None:
            layers[RetrievalSourceLayer.PROJECT].update(
                status=RetrievalLayerStatus.NO_MATERIAL,
                note="项目暂无已就绪文件。",
            )
            # Issue 36「新附件归属」：会话归属项目后，新上传的聊天附件
            # 携带 project_id 入队，纳入项目层检索范围（与项目上传文件
            # 同层）；清除项目归属后的新附件不再进入任何项目层。
            layers[RetrievalSourceLayer.PROJECT][
                "ready_document_ids"
            ] = _merge_document_ids(
                self._ready_documents(
                    account_id, source="project_file", project_id=project_id
                ),
                self._ready_documents(
                    account_id, source="chat_attachment", project_id=project_id
                ),
            )
            layers[RetrievalSourceLayer.PROJECT]["stale"] = (
                self._has_stale_documents(
                    account_id, source="project_file", project_id=project_id
                )
                or self._has_stale_documents(
                    account_id, source="chat_attachment", project_id=project_id
                )
            )
        if use_knowledge_base:
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE].update(
                status=RetrievalLayerStatus.NO_MATERIAL,
                note="知识库暂无已就绪材料。",
            )
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE][
                "ready_document_ids"
            ] = self._ready_documents(account_id, source="knowledge_base")
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE]["stale"] = self._has_stale_documents(
                account_id, source="knowledge_base"
            )
        for layer in _LAYER_ORDER:
            if layers[layer]["stale"]:
                layers[layer]["note"] = "本地材料已更新，但索引尚未重建，当前引用可能过时。"
            if layers[layer]["ready_document_ids"]:
                layers[layer]["status"] = RetrievalLayerStatus.OK
                if not layers[layer]["stale"]:
                    layers[layer]["note"] = None
        return layers

    def _ready_documents(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        """返回某层已就绪且对象仍活跃的文档标识（只查当前账户资源）。"""
        return self._repository.ready_document_ids(
            account_id,
            source=source,
            object_ids=object_ids,
            project_id=project_id,
        )

    def _has_stale_documents(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> bool:
        """返回已就绪但等待索引重建的文档状态，供教学证据门闭锁。"""
        return self._repository.has_stale_documents(
            account_id,
            source=source,
            object_ids=object_ids,
            project_id=project_id,
        )

    def _active_version(self, account_id: str) -> sqlite3.Row | None:
        return self._repository.active_version(account_id)

    def _vector_rows(
        self,
        account_id: str,
        version_id: str,
        document_ids: list[str],
    ) -> list[dict[str, Any]]:
        return self._repository.vector_rows(account_id, version_id, document_ids)

    # ------------------------------------------------------------------
    # 持久化与投影
    # ------------------------------------------------------------------

    def _persist_round(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message_id: str | None,
        *,
        use_knowledge_base: bool,
        index_version_id: str | None,
        sufficiency: RetrievalSufficiency,
        layers: list[RetrievalLayerResult],
        candidates: list[tuple[RetrievalSourceLayer, FusedCandidate]],
        note: str | None,
    ) -> RetrievalRoundProjection | None:
        round_id = f"rnd-{secrets.token_urlsafe(12)}"
        now = _now()
        try:
            with self._database.transaction():
                self._repository.insert_round(
                    account_id=account_id,
                    round_id=round_id,
                    message_id=assistant_message_id,
                    user_message_id=user_message_id,
                    conversation_id=conversation_id,
                    use_knowledge_base=use_knowledge_base,
                    sufficiency=sufficiency.value,
                    index_version_id=index_version_id,
                    layers=[layer.model_dump() for layer in layers],
                    note=note,
                    created_at=now,
                )
                object_meta = self._object_meta_map(
                    account_id, [candidate.hit.object_id for _, candidate in candidates]
                )
                for rank, (layer, candidate) in enumerate(candidates, start=1):
                    hit = candidate.hit
                    filename, media_type = object_meta.get(
                        hit.object_id, (hit.object_id, "application/octet-stream")
                    )
                    self._repository.insert_citation(
                        account_id=account_id,
                        citation_id=f"cit-{secrets.token_urlsafe(12)}",
                        round_id=round_id,
                        message_id=assistant_message_id,
                        conversation_id=conversation_id,
                        source_layer=layer.value,
                        object_id=hit.object_id,
                        filename=filename,
                        media_type=media_type,
                        page_number=hit.page_number,
                        section_title=hit.section_title,
                        snippet=hit.content[:SNIPPET_MAX_LEN],
                        chunk_id=hit.chunk_id,
                        rank=rank,
                        created_at=now,
                    )
        except sqlite3.Error:
            # 检索记录写入失败不阻断生成：本轮无引用可展示，返回 None。
            return None
        row = self._repository.round_row(account_id, assistant_message_id)
        if row is None:
            return None
        citations = self._repository.citation_rows(account_id, round_id)
        return self._project_round(row, citations)

    def _object_meta_map(
        self, account_id: str, object_ids: list[str]
    ) -> dict[str, tuple[str, str]]:
        """一次查询取回多个对象的 (文件名, 媒体类型)（引用写入预取）。"""
        return self._objects.object_metas(account_id, object_ids)

    def _layer_results(
        self,
        layers: dict[RetrievalSourceLayer, dict[str, Any]],
        *,
        index_unavailable: bool,
        notes: dict[RetrievalSourceLayer, str],
    ) -> list[RetrievalLayerResult]:
        results: list[RetrievalLayerResult] = []
        for layer in _LAYER_ORDER:
            entry = layers[layer]
            status = entry["status"]
            note = entry["note"]
            if status == RetrievalLayerStatus.OK and index_unavailable:
                status = RetrievalLayerStatus.INDEX_UNAVAILABLE
                note = _INDEX_UNAVAILABLE_NOTE
            if note is None:
                note = notes.get(layer)
            results.append(
                RetrievalLayerResult(
                    layer=layer,
                    status=status,
                    candidates=int(entry["candidates"]),
                    note=note,
                )
            )
        return results

    def _project_round(
        self, row: sqlite3.Row, citations: list[sqlite3.Row]
    ) -> RetrievalRoundProjection:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        return RetrievalRoundProjection(
            round_id=str(row["round_id"]),
            message_id=str(row["message_id"]),
            conversation_id=str(row["conversation_id"]),
            use_knowledge_base=bool(row["use_knowledge_base"]),
            index_version_id=(
                str(row["index_version_id"])
                if row["index_version_id"] is not None
                else None
            ),
            sufficiency=RetrievalSufficiency(str(row["sufficiency"])),
            layers=[
                RetrievalLayerResult(**layer)
                for layer in _loads_layers(str(row["layers_json"]))
            ],
            citations=[self._citation_from_row(citation) for citation in citations],
            note=str(row["note"]) if row["note"] is not None else None,
            created_at=created_at,
        )

    @staticmethod
    def _citation_from_row(row: sqlite3.Row) -> CitationProjection:
        return CitationProjection(
            citation_id=str(row["citation_id"]),
            source_layer=RetrievalSourceLayer(str(row["source_layer"])),
            object_id=str(row["object_id"]),
            filename=str(row["filename"]),
            media_type=str(row["media_type"]),
            page_number=(
                int(row["page_number"]) if row["page_number"] is not None else None
            ),
            section_title=(
                str(row["section_title"]) if row["section_title"] is not None else None
            ),
            snippet=str(row["snippet"]),
            rank=int(row["rank"]),
        )

    # ------------------------------------------------------------------
    # 引用打开时的授权校验
    # ------------------------------------------------------------------

    def _access_state(
        self,
        account_id: str,
        conversation_id: str,
        row: sqlite3.Row,
    ) -> tuple[CitationAccessStatus, str, str | None]:
        """按当前对象状态实时校验引用可访问性，返回安全中文状态。

        对象已删除或进入清理 → ``deleted``；对象仍在但授权范围已变化
        （附件解绑、项目删除/文件移除、材料删除）→ ``permission_changed``。
        """
        object_id = str(row["object_id"])
        # objects 属 storage 域：经属主仓库读取状态，跨账户/不存在返回 None。
        if self._objects.object_status(account_id, object_id) != "active":
            return (
                CitationAccessStatus.DELETED,
                "原文已删除，无法打开。",
                None,
            )
        layer = RetrievalSourceLayer(str(row["source_layer"]))
        if layer == RetrievalSourceLayer.ATTACHMENT:
            # 附件绑定的是所属用户消息（引用本身是助手消息）：经轮次记录
            # 取回本轮用户消息再校验绑定，杜绝"消息 ID 错位"导致的误判。
            user_message_id = self._repository.round_user_message_id(
                account_id, str(row["round_id"])
            )
            bound = (
                self._attachments.bound_exists(
                    account_id, object_id, conversation_id, user_message_id
                )
                if user_message_id is not None
                else False
            )
            if not bound:
                return (
                    CitationAccessStatus.PERMISSION_CHANGED,
                    "附件已从消息中移除或授权已变化，无法打开原文。",
                    None,
                )
            return (
                CitationAccessStatus.ACCESSIBLE,
                "原文可访问。",
                f"/chat/conversations/{conversation_id}/attachments/{object_id}/download",
            )
        if layer == RetrievalSourceLayer.PROJECT:
            conversation = self._conversations.get_conversation(
                account_id, conversation_id
            )
            project_id = (
                conversation.project_id
                if conversation is not None and conversation.project_id is not None
                else None
            )
            in_project = (
                self._repository.document_record_exists(
                    account_id,
                    object_id,
                    source="project_file",
                    project_id=project_id,
                )
                if project_id is not None
                else None
            )
            if project_id is None or in_project is None:
                return (
                    CitationAccessStatus.PERMISSION_CHANGED,
                    "学习项目已删除或文件已移除，无法打开原文。",
                    None,
                )
            return (
                CitationAccessStatus.ACCESSIBLE,
                "原文可访问。",
                f"/learning-projects/{project_id}/files/{object_id}/download",
            )
        if not self._repository.document_record_exists(
            account_id, object_id, source="knowledge_base"
        ):
            return (
                CitationAccessStatus.PERMISSION_CHANGED,
                "知识库材料已删除或授权已变化，无法打开原文。",
                None,
            )
        return (
            CitationAccessStatus.ACCESSIBLE,
            "原文可访问。",
            f"/knowledge-base/materials/{object_id}/download",
        )


def _merge_document_ids(*groups: list[str]) -> list[str]:
    """按出现顺序合并文档标识并去重（项目层多来源候选）。"""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for document_id in group:
            if document_id not in seen:
                seen.add(document_id)
                merged.append(document_id)
    return merged


def _loads_layers(value: str) -> list[dict[str, Any]]:
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _sufficiency_note(sufficiency: RetrievalSufficiency) -> str:
    """充足性信号对应的面向用户中文说明。"""
    return {
        RetrievalSufficiency.SUFFICIENT: "已检索到足够的本地材料。",
        RetrievalSufficiency.NO_HITS: "没有找到与问题相关的本地材料。",
        RetrievalSufficiency.CONFLICT: "检索到的候选来源存在冲突，结果可能不确定。",
        RetrievalSufficiency.INSUFFICIENT_COVERAGE: "检索到的本地材料覆盖不足。",
        RetrievalSufficiency.INDEX_UNAVAILABLE: _INDEX_UNAVAILABLE_NOTE,
    }[sufficiency]
