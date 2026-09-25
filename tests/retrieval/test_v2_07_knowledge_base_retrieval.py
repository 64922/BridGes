"""V2 Issue 07 验收测试：知识库检索门、代表材料定位与索引隔离。

覆盖验收标准（``.scratch/bridges-v2/issues/07-knowledge-base-retrieval.md``）：

1. 检索触发只看模式、任务与用户请求——没有学科名词白名单，同一专业名词
   在任何学科下结论一致；与请求无关的材料不注入模型上下文；
2. 讲义、笔记、简历与教材章节四类代表材料经真实摄取状态机解析、分块、
   索引后可检索，引用带文档名与页码/章节/片段定位；
3. 向量模型与索引版本独立于主模型激活（ADR-0008/0031）；跨账户材料与
   已删除材料不可召回。

AC1（上传、状态、重试、下载、删除仍可用；聊天附件不入库）由既有套件锁定，
不在本文件重复：``tests/knowledge_base/test_knowledge_base_api.py``
（上传/列表/详情/下载/重试/重建/删除/跨账户 404）与
``tests/chat/test_v2_05_photo_attachments.py`` 的
``test_chat_photo_never_enters_global_knowledge_base``（材料列表为空且无
``document_records`` 行）。

确定性 Embedding/OCR 替身只用于验证接线与合同（引用字段、账户与删除
隔离、版本）；供应商可用性由 ``tests/ingestion/test_ocr_real_smoke.py``
的真实 smoke 证明，本文件不把替身结果当作供应商证据。
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest

from bridges.ai.fixed_models import EMBEDDING_MODEL_ID
from bridges.ai.run_model_config import RunModelConfigProvider, configured_model_id
from bridges.chat.turn import assemble_payload
from bridges.contracts.ai import ModelCapabilities
from bridges.contracts.retrieval import (
    CitationAccessStatus,
    RetrievalDecisionAction,
    RetrievalSufficiency,
)
from bridges.ingestion.embedding import EMBEDDING_DIMENSIONS
from bridges.ingestion.index import CURRENT_CONTRACT, IndexContract, VersionedIndex
from bridges.ingestion.ocr import OcrPageRequest
from bridges.ingestion.service import IngestionService
from bridges.knowledge_base import KnowledgeBaseService
from bridges.retrieval.service import LayeredRetrievalService
from tests.retrieval.conftest import add_material, seed_conversation

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# ---------------------------------------------------------------------------
# 四类代表材料的固定内容（讲义、简历、教材章节、笔记）
# ---------------------------------------------------------------------------

#: 讲义：两页 PDF，检索命中第二页（页码定位）。
_LECTURE_PAGES = [
    "第一章 数据结构与算法\n讲义：二叉树遍历与递归实现",
    "第二章 信号处理\n讲义：傅里叶变换用于信号分解",
]
#: 简历：DOCX 分节（章节定位）。
_RESUME_PARAGRAPHS = [
    ("Heading1", "工作经历"),
    ("Normal", "2023-2025 后端开发工程师，负责订单系统重构。"),
    ("Heading1", "项目经验"),
    ("Normal", "知识库检索优化：召回率提升 12%。"),
]
#: 教材章节：Markdown 标题（章节定位）。
_TEXTBOOK_MARKDOWN = (
    "# 第三章 电磁场\n\n"
    "高斯定理描述电场通量与电荷的关系。\n\n"
    "## 3.1 电通量\n\n"
    "电通量等于场强与面积的乘积。\n"
)
#: 笔记：图片经通用 OCR 得到的识别文本（片段定位）。
_NOTES_OCR_TEXT = "课堂笔记：泰勒展开用于近似函数值，误差由余项控制。"


def _pdf_bytes(pages: list[str]) -> bytes:
    """用 PyMuPDF 生成多页中文 PDF（讲义代表材料）。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF（项目声明依赖）

    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text, fontsize=12, fontname="china-s")
    data = document.tobytes()
    document.close()
    return data


def _docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    """构造 DOCX：段落列表 [(pStyle, 文本)]（简历分节代表材料）。"""
    body: list[str] = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    document_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_DOCX_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _png_bytes(width: int = 900, height: int = 600) -> bytes:
    """最小 PNG 头（解析器只读像素尺寸；文字由通用 OCR 接缝提供）。"""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height)


class _StubOcrPort:
    """确定性 OCR 替身：记录页级请求并返回固定识别文本（不发网络请求）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[OcrPageRequest] = []

    def extract(self, request: OcrPageRequest) -> str:
        self.requests.append(request)
        return self.text


class _MemoryStatePort:
    """运行配置持久化替身（与 SqliteStateStore 同语义：整体替换命名空间）。"""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    def load(self, namespace: str) -> dict[str, object] | None:
        return self.values.get(namespace)

    def save(self, namespace: str, state: dict[str, object]) -> None:
        self.values[namespace] = dict(state)


def _ocr_ingestion(env: dict[str, Any], ocr: _StubOcrPort) -> IngestionService:
    """挂载确定性 OCR 端口的摄取服务（与后台执行器同一状态机）。"""
    return IngestionService(
        database=env["database"],
        object_repository=env["repository"],
        embedding=env["embedding"],
        ocr=ocr,
        index=VersionedIndex(env["database"], env["embedding"]),
    )


def _ingest(
    env: dict[str, Any],
    ingestion: IngestionService,
    account_id: str,
    filename: str,
    content: bytes,
    media_type: str,
) -> str:
    """把材料写入对象库并入队摄取（与真实上传共用同一状态机）。"""
    stored = env["repository"].create_object(
        account_id, filename, content, media_type=media_type
    )
    ingestion.enqueue(account_id, stored.object_id)
    ingestion.process_pending()
    return str(stored.object_id)


def _keyword_only_retrieval(env: dict[str, Any]) -> LayeredRetrievalService:
    """仅关键词的检索服务：隔离「措辞相关性」判定。

    确定性 Embedding 替身的向量是按位构造的近似向量（任意两段文本的余弦
    常常高于 0.89），不能用来判定语义相关性——语义门属于真实
    ``text-embedding-v4``，替身证明不了。涉及「无关材料不得注入」的断言
    因此走关键词路径，使结论不受替身噪声影响。
    """
    return LayeredRetrievalService(
        database=env["database"],
        embedding=None,
        object_repository=env["repository"],
    )


def _round(
    env: dict[str, Any],
    retrieval: LayeredRetrievalService,
    account_id: str,
    conversation_id: str,
    message_id: str,
    query: str,
) -> Any:
    """按检索决策执行一轮检索：真实决策门 → 真实检索轮次。"""
    user_message_id = f"{message_id}-user"
    decision = retrieval.ensure_decision(
        account_id,
        conversation_id,
        message_id,
        user_message_id,
        query,
        mode="companion",
        capability_route="companion",
        use_knowledge_base=True,
    )
    return retrieval.run_round(
        account_id,
        conversation_id,
        message_id,
        user_message_id,
        query,
        use_knowledge_base=True,
        decision=decision,
    )


def _document_row(env: dict[str, Any], account_id: str, object_id: str) -> Any:
    return env["database"].connection.execute(
        "SELECT status, chunk_count, page_count, section_count FROM document_records"
        " WHERE account_id = ? AND object_id = ?",
        (account_id, object_id),
    ).fetchone()


def _context_text(payload: dict[str, Any]) -> str:
    """模型载荷的全部文本（用于断言材料是否真的进入了上下文）。"""
    return "\n".join(str(message.get("content")) for message in payload["messages"])


def _citation_containing(round_: Any, text: str) -> Any:
    """取出片段中含指定文本的引用：即真正回答了本次请求的那条引用。

    同一文档的多个分块都可能进入融合结果（确定性 Embedding 替身的向量
    与文本无对应关系，会额外带入噪声分块），因此断言定位字段时按片段
    选取，而不是按文档名去重。
    """
    return next(citation for citation in round_.citations if text in citation.snippet)


# ---------------------------------------------------------------------------
# AC2：检索门由模式/任务/用户请求决定，无关材料不进上下文
# ---------------------------------------------------------------------------


def test_bare_topic_term_runs_no_retrieval_at_all(env: dict[str, Any]) -> None:
    """AC2：主题名词不触发检索——决策跳过、无轮次、无查询向量化、无材料注入。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env,
        account,
        "我的简历.txt",
        "求职意向：后端开发工程师，期望城市杭州。",
        layer="knowledge_base",
    )
    embed_calls_before = len(env["embedding"].embed_calls)
    query = "热力学第二定律"
    user_message_id = "assistant-1-user"

    decision = env["retrieval"].ensure_decision(
        account,
        conversation_id,
        "assistant-1",
        user_message_id,
        query,
        mode="companion",
        capability_route="companion",
        use_knowledge_base=True,
    )
    assert decision.action == RetrievalDecisionAction.SKIP
    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        "assistant-1",
        user_message_id,
        query,
        use_knowledge_base=True,
        decision=decision,
    )

    assert round_ is None
    assert len(env["embedding"].embed_calls) == embed_calls_before
    payload = assemble_payload(
        [{"role": "user", "content": query}], retrieval_round=round_
    )
    assert "后端开发工程师" not in _context_text(payload)


def test_unrelated_material_is_not_injected_when_retrieval_runs(
    env: dict[str, Any],
) -> None:
    """AC2：请求点名了材料类型但内容无关 → 材料在候选内、无命中、不注入。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env,
        account,
        "我的简历.txt",
        "求职意向：后端开发工程师，期望城市杭州。",
        layer="knowledge_base",
    )
    query = "根据我的简历 量子纠缠的非局域性"

    round_ = _round(
        env, _keyword_only_retrieval(env), account, conversation_id, "assistant-1", query
    )

    assert round_ is not None
    assert round_.citations == []
    assert round_.sufficiency == RetrievalSufficiency.NO_HITS
    kb_layer = next(
        layer for layer in round_.layers if layer.layer.value == "knowledge_base"
    )
    # 材料确实在本轮候选范围内，只是没有被当成相关证据：检索成功不等于注入。
    assert [item.filename for item in kb_layer.candidate_files] == ["我的简历.txt"]
    payload = assemble_payload(
        [{"role": "user", "content": query}], retrieval_round=round_
    )
    assert "后端开发工程师" not in _context_text(payload)


def test_matching_material_is_injected_with_its_location(env: dict[str, Any]) -> None:
    """AC2 正对照：命中材料时引用块带文档名与定位进入上下文。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    add_material(
        env,
        account,
        "数据结构讲义.txt",
        "讲义：傅里叶变换用于信号分解。",
        layer="knowledge_base",
    )
    query = "根据我的讲义 傅里叶变换"

    round_ = _round(env, env["retrieval"], account, conversation_id, "assistant-1", query)

    assert round_ is not None
    citation = _citation_containing(round_, "傅里叶变换")
    assert citation.filename == "数据结构讲义.txt"
    payload = assemble_payload(
        [{"role": "user", "content": query}], retrieval_round=round_
    )
    context = _context_text(payload)
    assert "数据结构讲义.txt" in context
    assert "傅里叶变换" in context


# ---------------------------------------------------------------------------
# AC3：讲义、笔记、简历、教材章节代表材料的解析与定位引用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "filename", "builder", "media_type", "expected"),
    [
        (
            "讲义",
            "数据结构讲义.pdf",
            lambda: _pdf_bytes(_LECTURE_PAGES),
            "application/pdf",
            {"page_number": 2, "section_title": None, "snippet": "傅里叶变换"},
        ),
        (
            "简历",
            "王小明-简历.docx",
            lambda: _docx_bytes(_RESUME_PARAGRAPHS),
            _DOCX_MEDIA_TYPE,
            {"page_number": None, "section_title": "项目经验", "snippet": "召回率提升"},
        ),
        (
            "教材章节",
            "教材第三章-电磁场.md",
            lambda: _TEXTBOOK_MARKDOWN.encode("utf-8"),
            "text/markdown",
            {
                "page_number": None,
                "section_title": "第三章 电磁场",
                "snippet": "高斯定理",
            },
        ),
    ],
)
def test_representative_document_material_is_searchable_with_location(
    env: dict[str, Any],
    slot: str,
    filename: str,
    builder: Callable[[], bytes],
    media_type: str,
    expected: dict[str, Any],
) -> None:
    """AC3：讲义/简历/教材章节经解析、分块、索引后可检索并给出定位。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    object_id = _ingest(
        env, env["ingestion"], account, filename, builder(), media_type
    )

    row = _document_row(env, account, object_id)
    assert row["status"] == "ready", slot
    assert row["chunk_count"] > 0, slot

    query = f"根据我的{slot} {expected['snippet']}"
    round_ = _round(
        env, env["retrieval"], account, conversation_id, "assistant-1", query
    )

    assert round_ is not None
    citation = _citation_containing(round_, expected["snippet"])
    assert citation.filename == filename, slot
    assert citation.page_number == expected["page_number"]
    assert citation.section_title == expected["section_title"]
    assert expected["snippet"] in citation.snippet


def test_lecture_pdf_parses_page_structure(env: dict[str, Any]) -> None:
    """AC3：多页讲义保留页码结构（页码定位来自真实解析跨度）。"""
    account = env["account_a"]
    object_id = _ingest(
        env,
        env["ingestion"],
        account,
        "数据结构讲义.pdf",
        _pdf_bytes(_LECTURE_PAGES),
        "application/pdf",
    )

    row = _document_row(env, account, object_id)
    assert row["status"] == "ready"
    assert row["page_count"] == len(_LECTURE_PAGES)


def test_notes_photo_goes_through_general_ocr_before_indexing(
    env: dict[str, Any],
) -> None:
    """AC3：笔记照片经通用 OCR 接缝识别后才能检索，引用为原文片段定位。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    ocr = _StubOcrPort(_NOTES_OCR_TEXT)
    ingestion = _ocr_ingestion(env, ocr)
    filename = "课堂笔记.png"
    object_id = _ingest(env, ingestion, account, filename, _png_bytes(), "image/png")

    row = _document_row(env, account, object_id)
    assert row["status"] == "ready"
    assert [request.page_ordinal for request in ocr.requests] == [1]
    assert ocr.requests[0].document_id == f"doc-{object_id}"

    query = "根据我的笔记 泰勒展开"
    round_ = _round(
        env, env["retrieval"], account, conversation_id, "assistant-1", query
    )

    assert round_ is not None
    citation = _citation_containing(round_, "泰勒展开")
    assert citation.filename == filename
    assert citation.page_number is None
    assert citation.section_title is None
    assert "泰勒展开" in citation.snippet


# ---------------------------------------------------------------------------
# AC4：向量模型/索引版本独立，跨账户与已删除材料不可召回
# ---------------------------------------------------------------------------


def test_index_contract_is_independent_of_main_model_activation(
    env: dict[str, Any],
) -> None:
    """AC4：激活手填主模型不改向量模型与索引版本（ADR-0008/0031）。"""
    account = env["account_a"]
    add_material(
        env, account, "讲义.txt", "傅里叶变换用于信号分解。", layer="knowledge_base"
    )
    version_row = env["database"].connection.execute(
        "SELECT version_id, contract_json, contract_hash FROM index_versions"
        " WHERE account_id = ?",
        (account,),
    ).fetchone()
    contract = IndexContract.from_json(str(version_row["contract_json"]))
    assert contract.model_id == EMBEDDING_MODEL_ID
    assert contract.dimensions == EMBEDDING_DIMENSIONS
    assert str(version_row["contract_hash"]) == CURRENT_CONTRACT.contract_hash()

    provider = RunModelConfigProvider(_MemoryStatePort())
    provider.activate(
        model_id="qwen-hand-filled-candidate",
        capabilities=ModelCapabilities(
            text=True, image=True, tool_calling=True, structured_output=True
        ),
        context_window=131_072,
        max_input_tokens=130_048,
        validated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert provider.snapshot().model_id == "qwen-hand-filled-candidate"
    # 向量化不在可手填能力内：运行配置到不了它的绑定。
    assert configured_model_id("qwen_embedding", provider.snapshot()) is None
    unchanged = env["database"].connection.execute(
        "SELECT version_id, contract_json, contract_hash FROM index_versions"
        " WHERE account_id = ?",
        (account,),
    ).fetchone()
    active = env["database"].connection.execute(
        "SELECT version_id FROM index_active WHERE account_id = ?", (account,)
    ).fetchone()
    assert str(unchanged["version_id"]) == str(version_row["version_id"])
    assert str(active["version_id"]) == str(version_row["version_id"])
    assert str(unchanged["contract_json"]) == str(version_row["contract_json"])
    assert str(unchanged["contract_hash"]) == str(version_row["contract_hash"])


def test_cross_account_material_is_not_recallable(env: dict[str, Any]) -> None:
    """AC4：账户 A 的材料对账户 B 不可召回，也不泄漏候选文件名。"""
    account_a, account_b = env["account_a"], env["account_b"]
    add_material(
        env,
        account_a,
        "简历A.txt",
        "项目经验：知识库检索优化，召回率提升 12%。",
        layer="knowledge_base",
    )
    conversation_b = seed_conversation(env, account_b)

    round_ = _round(
        env,
        env["retrieval"],
        account_b,
        conversation_b,
        "assistant-1",
        "根据我的简历 知识库检索优化",
    )

    assert round_ is not None
    assert round_.citations == []
    assert round_.sufficiency == RetrievalSufficiency.NO_HITS
    assert all(layer.candidate_files == [] for layer in round_.layers)


def test_deleted_material_is_not_recallable_but_citation_reports_deleted(
    env: dict[str, Any],
) -> None:
    """AC4：删除后新一轮不再召回；历史引用如实报告已删除（不复活）。"""
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    query = "根据我的简历 知识库检索优化"
    object_id = add_material(
        env,
        account,
        "简历.txt",
        "项目经验：知识库检索优化，召回率提升 12%。",
        layer="knowledge_base",
    )
    first = _round(
        env, env["retrieval"], account, conversation_id, "assistant-1", query
    )

    assert first is not None
    citation = _citation_containing(first, "召回率提升")
    assert citation.filename == "简历.txt"
    citation_id = citation.citation_id

    KnowledgeBaseService(
        env["database"], env["repository"], env["ingestion"]
    ).delete(account, object_id)

    second = _round(
        env, env["retrieval"], account, conversation_id, "assistant-2", query
    )
    assert second is not None
    assert second.citations == []
    assert second.sufficiency == RetrievalSufficiency.NO_HITS
    detail = env["retrieval"].citation_detail(
        account, conversation_id, "assistant-1", citation_id
    )
    assert detail.access_status == CitationAccessStatus.DELETED
