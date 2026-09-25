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
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from bridges.ai.ports import EmbeddingContext, EmbeddingOperation
from bridges.chat.attachments_repository import AttachmentRepository
from bridges.chat.repository import ConversationRepository
from bridges.contracts.retrieval import (
    CitationAccessStatus,
    CitationDetailProjection,
    CitationProjection,
    RetrievalCandidateFile,
    RetrievalDecisionAction,
    RetrievalDecisionProjection,
    RetrievalDecisionReason,
    RetrievalLayerResult,
    RetrievalLayerStatus,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.ingestion.embedding import EmbeddingError, EmbeddingPort
from bridges.retrieval.decision import (
    RULES_VERSION,
    decide_retrieval,
    query_fingerprint,
)
from bridges.retrieval.repository import RetrievalRepository
from bridges.retrieval.search import (
    LAYER_QUOTAS,
    MAX_CITATIONS,
    VECTOR_MIN_SIMILARITY,
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
#: 第一阶段最多允许进入片段检索的全局知识库文件数。
KNOWLEDGE_BASE_CANDIDATE_LIMIT = 8
#: 附件层一次纳入检索的最大附件数（V2 Issue 06：按绑定时间取最近若干份，
#: 与一条消息的附件数上限同量级，避免长会话无限扩张检索作用域）。
ATTACHMENT_SCOPE_LIMIT = 10


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

    def ensure_decision(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message_id: str | None,
        query: str,
        *,
        mode: str,
        capability_route: str,
        use_knowledge_base: bool,
    ) -> RetrievalDecisionProjection:
        """在任何索引副作用前形成并持久化一份检索决策。"""
        existing = self._repository.decision_row(
            account_id,
            user_message_id=user_message_id,
        )
        if existing is None:
            existing = self._repository.decision_row(
                account_id,
                assistant_message_id=assistant_message_id,
            )
        if existing is not None:
            return self._project_decision(existing)

        decision = decide_retrieval(
            query,
            mode=mode,
            capability_route=capability_route,
            use_knowledge_base=use_knowledge_base,
        )
        now = _now()
        try:
            with self._database.transaction():
                self._repository.insert_decision(
                    account_id=account_id,
                    decision_id=f"dec-{secrets.token_urlsafe(12)}",
                    assistant_message_id=assistant_message_id,
                    user_message_id=user_message_id,
                    conversation_id=conversation_id,
                    action=decision.action.value,
                    reason=decision.reason.value,
                    rules_version=RULES_VERSION,
                    capability_route=capability_route,
                    mode=mode,
                    query_fingerprint=query_fingerprint(query),
                    created_at=now,
                )
        except sqlite3.IntegrityError:
            # 并发重试可能同时形成同一用户回合的决策；唯一约束选出先提交的
            # 快照，调用方永远不会看到第二份规则结果。
            existing = self._repository.decision_row(
                account_id,
                user_message_id=user_message_id,
            )
            if existing is None:
                raise
        else:
            existing = self._repository.decision_row(
                account_id,
                assistant_message_id=assistant_message_id,
            )
        if existing is None:
            raise sqlite3.IntegrityError("检索决策写入后无法读取。")
        return self._project_decision(existing)

    def decision_projection(
        self, account_id: str, assistant_message_id: str
    ) -> RetrievalDecisionProjection | None:
        """返回助手尝试对应的决策；重试沿用户消息复用原快照。"""
        row = self._repository.decision_row_for_message(account_id, assistant_message_id)
        return self._project_decision(row) if row is not None else None

    def run_round(
        self,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message_id: str | None,
        query: str,
        *,
        use_knowledge_base: bool,
        decision: RetrievalDecisionProjection | None = None,
    ) -> RetrievalRoundProjection | None:
        """为一条助手消息执行一轮分层检索并固化结果。

        返回 None 表示本轮没有可检索作用域（无附件、未归属项目且知识库
        无材料/被关闭），前端不渲染检索卡；其余情况总是返回结构化轮次
        投影，绝不把无命中伪装成成功。
        """
        if decision is not None:
            # 新聊天回合的决策控制整个检索副作用；历史直接调用仍走下方兼容路径。
            # 重试不得用新的请求开关改写已持久化的决策。
            if user_message_id is not None:
                existing = self._repository.round_row_for_user_message(
                    account_id, user_message_id
                )
                if existing is not None:
                    return self._project_round(
                        existing,
                        self._repository.citation_rows(
                            account_id, str(existing["round_id"])
                        ),
                        message_id=assistant_message_id,
                    )
        else:
            # 直接调用检索服务是 Issue 20 的历史公开接口，保留其显式
            # ``use_knowledge_base`` 语义；聊天编排必须先传入 Issue 12
            # 决策快照，避免绕过意图门。
            use_knowledge_base = bool(use_knowledge_base)
        # conversations 属 chat 域：经属主 ConversationRepository 读取
        # 归属项目，对话不存在时本轮无作用域可检索。
        conversation = self._conversations.get_conversation(
            account_id, conversation_id
        )
        if conversation is None:
            return None
        project_id = conversation.project_id
        # V2 Issue 06：会话附件是本轮显式交给助手的材料，作用域独立于全局
        # 知识库决策——用户关掉知识库或本轮请求形态不触发知识库检索时，
        # 附件内容仍须可被引用（决定只控制知识库层）。
        attachment_ids = self._attachment_ids(account_id, conversation_id)
        if decision is not None:
            if (
                decision.action != RetrievalDecisionAction.RETRIEVE
                and decision.reason != RetrievalDecisionReason.USER_DISABLED
                and not attachment_ids
            ):
                return None
            use_knowledge_base = decision.action == RetrievalDecisionAction.RETRIEVE
            # 附件存在时本轮一定形成检索轮次：无候选也要落库，供前端如实
            # 呈现「附件仍在处理中/无匹配片段」，而不是静默跳过。
        layers = self._resolve_layers(
            account_id,
            attachment_ids=attachment_ids,
            project_id=project_id,
            use_knowledge_base=use_knowledge_base,
            query=query,
        )
        # 没有任何层有已就绪材料时跳过本轮（无检索作用域，不假装成功）。
        if not any(layer["ready_document_ids"] for layer in layers.values()):
            if decision is not None and decision.action == RetrievalDecisionAction.RETRIEVE:
                unavailable = any(
                    layer["status"]
                    in {
                        RetrievalLayerStatus.INDEX_PROCESSING,
                        RetrievalLayerStatus.INDEX_CORRUPT,
                        RetrievalLayerStatus.TIMEOUT,
                    }
                    for layer in layers.values()
                )
                sufficiency = (
                    RetrievalSufficiency.INDEX_UNAVAILABLE
                    if unavailable
                    else RetrievalSufficiency.NO_HITS
                )
                return self._persist_round(
                    account_id,
                    conversation_id,
                    assistant_message_id,
                    user_message_id,
                    use_knowledge_base=use_knowledge_base,
                    index_version_id=None,
                    sufficiency=sufficiency,
                    layers=self._layer_results(layers, index_unavailable=False, notes={}),
                    candidates=[],
                    note=_sufficiency_note(sufficiency),
                )
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
        # Issue 15：查询向量锁关联到检索 round（round_id 在向量化前生成，
        # 锁与轮次记录共享同一对象标识；无向量化的路径不建锁）。
        round_id = f"rnd-{secrets.token_urlsafe(12)}"
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
            except TimeoutError:
                layer_search_failed = True
                layers[layer]["status"] = RetrievalLayerStatus.TIMEOUT
                layers[layer]["note"] = "该层检索超时，请稍后重试。"
            vector_rows[layer] = self._vector_rows(account_id, version_id, document_ids)

        search_results: dict[RetrievalSourceLayer, LayerSearchResult] = {}
        vector_note: str | None = None
        query_vector: list[float] | None = None
        # Issue 15：清理后为空的查询不向量化（无内容可检索，不发远端请求、
        # 不建伪锁），本轮按纯关键词路径处理。
        if self._embedding is not None and cleaned_query:
            try:
                embedded = self._embedding.embed(
                    account_id,
                    [cleaned_query],
                    context=EmbeddingContext(
                        operation=EmbeddingOperation.RETRIEVAL_QUERY,
                        run_id=round_id,
                        object_type="retrieval_round",
                        object_id=round_id,
                        project_id=project_id or "default",
                    ),
                )
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
                search_vectors(
                    vector_rows[layer],
                    query_vector,
                    min_similarity=VECTOR_MIN_SIMILARITY,
                )
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
            round_id=round_id,
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
            user_message_id = self._repository.generation_user_message_id(
                account_id, message_id
            )
            if user_message_id is not None:
                row = self._repository.round_row_for_user_message(
                    account_id, user_message_id
                )
        if row is None:
            return None
        citations = self._repository.citation_rows(
            account_id, str(row["round_id"])
        )
        return self._project_round(row, citations, message_id=message_id)

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
        same_message = str(row["message_id"]) == message_id
        request_user_message_id = self._repository.generation_user_message_id(
            account_id, message_id
        )
        round_user_message_id = self._repository.round_user_message_id(
            account_id, str(row["round_id"])
        )
        same_retry_turn = (
            request_user_message_id is not None
            and request_user_message_id == round_user_message_id
        )
        if (not same_message and not same_retry_turn) or str(
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
        self, account_id: str, conversation_id: str
    ) -> list[str]:
        """本会话已绑定附件（V2 Issue 06：随会话可引用，不跨会话/账户）。

        按绑定时间从新到旧取有界窗口：刚发送的附件必然在内，同一会话里
        稍后追问同一份文件也仍在范围内。未绑定（已上传未发送）的附件不
        属于任何消息，不进入检索。
        """
        return self._attachments.bound_object_ids_for_conversation(
            account_id, conversation_id, limit=ATTACHMENT_SCOPE_LIMIT
        )

    def _resolve_layers(
        self,
        account_id: str,
        *,
        attachment_ids: list[str],
        project_id: str | None,
        use_knowledge_base: bool,
        query: str = "",
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
        # 聊天附件（V2 Issue 06）：解析成功的附件进入本轮检索；仍在解析、
        # 解析失败或无可读正文的附件没有可检索材料，如实呈现为该层注记。
        # 学习项目文件只保留历史读模型，不能进入新检索轮次（Issue 12）。
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
            layers[RetrievalSourceLayer.ATTACHMENT][
                "stale"
            ] = self._has_stale_documents(
                account_id,
                source="chat_attachment",
                object_ids=attachment_ids,
            )
        layers[RetrievalSourceLayer.PROJECT]["note"] = "学习项目文件来源已退役。"
        if use_knowledge_base:
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE].update(
                status=RetrievalLayerStatus.NO_MATERIAL,
                note="知识库暂无已就绪材料。",
            )
            ready_document_ids = self._ready_documents(account_id, source="knowledge_base")
            if not ready_document_ids:
                status, note = self._unready_status(
                    account_id,
                    source="knowledge_base",
                    fallback="知识库暂无已就绪材料。",
                )
                layers[RetrievalSourceLayer.KNOWLEDGE_BASE].update(
                    status=status, note=note
                )
            candidate_ids, candidate_files = self._knowledge_base_candidates(
                account_id, ready_document_ids, query
            )
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE][
                "ready_document_ids"
            ] = candidate_ids
            layers[RetrievalSourceLayer.KNOWLEDGE_BASE][
                "candidate_files"
            ] = candidate_files
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

    def _knowledge_base_candidates(
        self, account_id: str, document_ids: list[str], query: str
    ) -> tuple[list[str], list[dict[str, str]]]:
        """按文件元数据选出有界候选，再交给片段检索。"""
        metadata = self._repository.document_metadata(
            account_id, document_ids, source="knowledge_base"
        )
        if len(metadata) <= KNOWLEDGE_BASE_CANDIDATE_LIMIT:
            return [item["document_id"] for item in metadata], metadata

        normalized = query.casefold()
        terms = _filename_terms(query)
        scored: list[tuple[int, int, dict[str, str]]] = []
        for position, item in enumerate(metadata):
            filename = item["filename"].casefold()
            score = int(normalized and normalized in filename)
            score += sum(1 for term in terms if term in filename)
            scored.append((score, -position, item))
        scored.sort(reverse=True)
        matched = [item for item in scored if item[0] > 0]
        selected_pool = matched or scored
        selected = [
            item for _, _, item in scored[:KNOWLEDGE_BASE_CANDIDATE_LIMIT]
        ]
        if matched:
            selected = [
                item for _, _, item in selected_pool[:KNOWLEDGE_BASE_CANDIDATE_LIMIT]
            ]
        return [item["document_id"] for item in selected], selected

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
        round_id: str | None = None,
    ) -> RetrievalRoundProjection | None:
        # 主路径的 round_id 在向量化前生成（查询锁与轮次共享同一对象标识）；
        # 无向量化的提前返回路径在此自行生成。
        if round_id is None:
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
                    candidate_files=[
                        RetrievalCandidateFile(**item)
                        for item in entry.get("candidate_files", [])
                    ],
                    note=note,
                )
            )
        return results

    @staticmethod
    def _project_decision(row: sqlite3.Row) -> RetrievalDecisionProjection:
        """把数据库快照投影为稳定合同，不重新计算规则。"""
        return RetrievalDecisionProjection(
            decision_id=str(row["decision_id"]),
            assistant_message_id=str(row["assistant_message_id"]),
            user_message_id=(
                str(row["user_message_id"])
                if row["user_message_id"] is not None
                else None
            ),
            conversation_id=str(row["conversation_id"]),
            action=RetrievalDecisionAction(str(row["action"])),
            reason=RetrievalDecisionReason(str(row["reason"])),
            rules_version=str(row["rules_version"]),
            capability_route=str(row["capability_route"]),
            mode=str(row["mode"]),
            query_fingerprint=str(row["query_fingerprint"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def _unready_status(
        self,
        account_id: str,
        *,
        source: str,
        object_ids: list[str] | None = None,
        project_id: str | None = None,
        fallback: str,
    ) -> tuple[RetrievalLayerStatus, str]:
        statuses = self._repository.document_statuses(
            account_id,
            source=source,
            object_ids=object_ids,
            project_id=project_id,
        )
        if statuses & {"queued", "parsing", "processing"}:
            return RetrievalLayerStatus.INDEX_PROCESSING, "材料正在处理或建立索引。"
        if "error" in statuses:
            return RetrievalLayerStatus.INDEX_CORRUPT, "材料索引损坏或处理失败，请重试。"
        return RetrievalLayerStatus.NO_MATERIAL, fallback

    def _project_round(
        self,
        row: sqlite3.Row,
        citations: list[sqlite3.Row],
        *,
        message_id: str | None = None,
    ) -> RetrievalRoundProjection:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        return RetrievalRoundProjection(
            round_id=str(row["round_id"]),
            message_id=message_id or str(row["message_id"]),
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
            # V2 Issue 06：附件随会话可引用（不限于引用所在的那一轮），
            # 授权范围以「仍绑定在本会话某条消息上」为准；解绑/换会话即
            # 视为授权已变，绝不跨会话打开原文。
            if not self._attachments.bound_in_conversation(
                account_id, conversation_id, object_id
            ):
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


def _filename_terms(query: str) -> list[str]:
    """提取文件名筛选用的确定性词项，不保存或记录原始请求。"""
    terms = [token.casefold() for token in re.findall(r"[A-Za-z0-9_]+", query)]
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        normalized = run.casefold()
        terms.append(normalized)
        terms.extend(
            normalized[index : index + size]
            for size in (2, 3, 4)
            for index in range(max(0, len(normalized) - size + 1))
        )
    return list(dict.fromkeys(term for term in terms if term))


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
