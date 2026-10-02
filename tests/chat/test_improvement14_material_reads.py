"""改进工单 14：统一照片预算与旧附件、原图、证据读取。

验收对照：

- 照片轮不再绕过编译：当前照片与历史原图以真实图片部件进入统一预算，
  编译记录与材料清单携带实际读取范围与版本；
- 旧图追问只在原图仍可读时重新读取，旧助手描述不作看图依据；删除后给
  具体缺口而不是复用旧描述；
- 附件原文细节按页码/章节补回已解析整段；模块证据读取范围如实标注；
- 跨账户/跨会话读取被拒绝；读取计划确定、有界，不偷偷扩大范围。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.payload_budget import IMAGE_PART_COST_TOKENS
from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.evidence_scope import module_evidence_scopes
from bridges.chat.material_reading import (
    MATERIAL_READ_VERSION,
    MAX_REFERENCED_PHOTOS,
    MaterialRead,
    MaterialReadKind,
    PhotoRef,
    is_file_detail_request,
    is_visual_detail_request,
    material_reads_from_record,
    plan_photo_reads,
    read_evidence_block,
    read_photo_payloads,
    select_file_segments,
)
from bridges.chat.repository import MessageRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.observability import AuditAction
from tests.chat.test_v2_05_photo_attachments import (
    PNG_BYTES,
    _app,
    _CapturingAdapter,
    _gateway_with,
    _register,
    _start_conversation,
    _upload_draft,
)

_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 测试材料构造
# ---------------------------------------------------------------------------


def _record(
    message_id: str,
    role: ChatMessageRole,
    content: str,
    **extra: Any,
) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        conversation_id="conv-14",
        account_id="acc-14",
        role=role,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=_BASE,
        updated_at=_BASE,
        **extra,
    )


def _user(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.USER, content, **extra)


def _assistant(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.ASSISTANT, content, **extra)


def _photo_ref(object_id: str, message_id: str, ordinal: int) -> PhotoRef:
    return PhotoRef(
        object_id=object_id,
        message_id=message_id,
        media_type="image/png",
        content_hash=f"hash-{object_id}",
        ordinal=ordinal,
        filename=f"{object_id}.png",
    )


def _compiled_records(app: Any) -> list[dict[str, Any]]:
    events = app.state.observability_service.list_audit_events(
        action=AuditAction.CONTEXT_COMPILED
    )
    return [event.details for event in events]


def _manifest_records(app: Any) -> list[dict[str, Any]]:
    events = app.state.observability_service.list_audit_events(
        action=AuditAction.PAYLOAD_BUDGET_EVALUATED
    )
    return [event.details for event in events]


def _system_text(payload: dict[str, Any]) -> str:
    return "\n".join(
        message["content"]
        for message in payload["messages"]
        if message["role"] == "system"
    )


# ---------------------------------------------------------------------------
# 单元：视觉/原文细节识别与读取计划（确定、有界）
# ---------------------------------------------------------------------------


def test_visual_detail_detection_requires_anchor() -> None:
    assert is_visual_detail_request("这张照片左下角的小字是什么？")
    assert is_visual_detail_request("上一张图里写了什么")
    assert is_visual_detail_request("第二张照片里是什么")
    assert not is_visual_detail_request("帮我推荐几篇论文")
    assert not is_visual_detail_request("照片收到了吗")


def test_file_detail_detection_targets_page_and_tail() -> None:
    assert is_file_detail_request("文件第2页末尾的限定条件是什么？")
    assert is_file_detail_request("附件结尾写了什么")
    assert not is_file_detail_request("帮我总结一下这轮对话")


def test_plan_current_and_referenced_reads_in_order() -> None:
    refs = [
        _photo_ref("p1", "m1", 1),
        _photo_ref("p2", "m1", 2),
        _photo_ref("p3", "m2", 1),
    ]
    plan = plan_photo_reads(
        request="第二张照片左下角的小字是什么？",
        current_message_id="m3",
        photo_refs=refs,
    )
    kinds = [(read.kind, read.object_id) for read in plan.reads]
    assert kinds == [(MaterialReadKind.REFERENCED_PHOTO, "p2")]
    assert plan.gaps == []


def test_plan_latest_historical_with_current_photo() -> None:
    refs = [
        _photo_ref("p1", "m1", 1),
        _photo_ref("p2", "m2", 1),
        _photo_ref("p3", "m2", 2),
    ]
    plan = plan_photo_reads(
        request="上一张图里的字是什么",
        current_message_id="m2",
        photo_refs=refs,
    )
    kinds = [(read.kind, read.object_id) for read in plan.reads]
    # 本轮照片固定在计划里；历史按问题只回读最近一张。
    assert kinds == [
        (MaterialReadKind.CURRENT_PHOTO, "p2"),
        (MaterialReadKind.CURRENT_PHOTO, "p3"),
        (MaterialReadKind.REFERENCED_PHOTO, "p1"),
    ]
    assert plan.gaps == []


def test_plan_current_reference_does_not_gap() -> None:
    plan = plan_photo_reads(
        request="这张图里是什么",
        current_message_id="m2",
        photo_refs=[_photo_ref("p2", "m2", 1)],
    )
    assert [read.kind for read in plan.reads] == [MaterialReadKind.CURRENT_PHOTO]
    assert plan.gaps == []


def test_plan_gaps_when_referenced_photo_missing() -> None:
    plan = plan_photo_reads(
        request="刚才那张照片左上角写了什么",
        current_message_id="m3",
        photo_refs=[_photo_ref("p3", "m3", 1)],
    )
    assert [read.kind for read in plan.reads] == [MaterialReadKind.CURRENT_PHOTO]
    assert plan.gaps and "不得用旧助手描述代替看图" in plan.gaps[0]


def test_plan_ordinal_out_of_range_gives_specific_gap() -> None:
    plan = plan_photo_reads(
        request="第五张照片里写了什么",
        current_message_id="m3",
        photo_refs=[_photo_ref("p1", "m1", 1)],
    )
    assert plan.reads == []
    assert plan.gaps and "第 5 张" in plan.gaps[0]


def test_plan_is_deterministic_and_bounded() -> None:
    refs = [_photo_ref(f"p{i}", f"m{i}", 1) for i in range(1, 6)]
    first = plan_photo_reads(
        request="刚才那张照片的细节", current_message_id="m9", photo_refs=refs
    )
    second = plan_photo_reads(
        request="刚才那张照片的细节", current_message_id="m9", photo_refs=refs
    )
    assert first.to_record() == second.to_record()
    referenced = [
        read
        for read in first.reads
        if read.kind == MaterialReadKind.REFERENCED_PHOTO
    ]
    assert len(referenced) <= MAX_REFERENCED_PHOTOS
    assert first.contract_version == MATERIAL_READ_VERSION


def test_select_file_segments_page_anchor_and_gap() -> None:
    rows = [
        {
            "citation_id": "c1",
            "object_id": "o1",
            "filename": "手册.pdf",
            "content": "第一页正文……末尾有说明。",
            "content_hash": "h1",
            "page_number": 1,
            "section_title": "绪论",
        },
        {
            "citation_id": "c2",
            "object_id": "o1",
            "filename": "手册.pdf",
            "content": "第二页完整正文……限定条件：仅限校内。",
            "content_hash": "h2",
            "page_number": 2,
            "section_title": "报名",
        },
    ]
    segments, gaps = select_file_segments(
        rows, request="第2页末尾的限定条件是什么？", had_attachment_citations=True
    )
    assert [segment.page_number for segment in segments] == [2]
    assert "限定条件：仅限校内" in segments[0].content
    assert segments[0].read_range == "第 2 页 · 报名"
    assert gaps == []

    missing, missing_gaps = select_file_segments(
        rows, request="第9页写了什么？", had_attachment_citations=True
    )
    assert missing == []
    assert missing_gaps and "第 9 页" in missing_gaps[0]


def test_select_file_segments_reports_deleted_source() -> None:
    segments, gaps = select_file_segments(
        [], request="附件末尾的限定条件", had_attachment_citations=True
    )
    assert segments == []
    assert gaps and "不可读取" in gaps[0]


def test_select_file_segments_tail_and_section_gap() -> None:
    rows = [
        {
            "citation_id": "c1",
            "object_id": "o1",
            "filename": "手册.pdf",
            "content": "第一页正文。",
            "content_hash": "h1",
            "page_number": 1,
            "section_title": "绪论",
        },
        {
            "citation_id": "c2",
            "object_id": "o1",
            "filename": "手册.pdf",
            "content": "最后一页正文……限定条件：仅限校内。",
            "content_hash": "h2",
            "page_number": 3,
            "section_title": "附则",
        },
    ]
    tail, tail_gaps = select_file_segments(
        rows, request="附件末尾的限定条件是什么？", had_attachment_citations=True
    )
    assert [segment.page_number for segment in tail] == [1, 3]
    assert "仅限校内" in tail[-1].content
    assert tail_gaps == []

    fallback, section_gaps = select_file_segments(
        rows, request="章节：报名材料的限定条件", had_attachment_citations=True
    )
    assert fallback
    assert section_gaps and "未能定位章节" in section_gaps[0]


def test_read_evidence_block_carries_range_and_boundary() -> None:
    read = MaterialRead(
        kind=MaterialReadKind.REFERENCED_PHOTO,
        object_id="p1",
        message_id="m1",
        media_type="image/png",
        content_hash="hash-p1",
        read_range="历史原图（消息 m1，第 1 张）",
        reason="本轮问题指向历史原图的视觉细节，重新读取同会话原图。",
    )
    block = read_evidence_block(photo_reads=[read], photo_gaps=["缺口甲"])
    assert block is not None
    assert "实际读取的同会话原图" in block
    assert "对象 p1" in block
    assert "缺口甲" in block
    assert "不构成本轮" in block


def test_material_reads_from_record_roundtrip() -> None:
    plan = plan_photo_reads(
        request="上一张图",
        current_message_id="m2",
        photo_refs=[_photo_ref("p1", "m1", 1), _photo_ref("p2", "m2", 1)],
    )
    restored = material_reads_from_record({"material_read_plan": plan.to_record()})
    assert [read.to_record() for read in restored] == [
        read.to_record() for read in plan.reads
    ]


# ---------------------------------------------------------------------------
# 单元：模块证据读取范围如实标注（仅有摘要不声称已读全文）
# ---------------------------------------------------------------------------


def test_evidence_scope_marks_summary_only_as_not_full_text() -> None:
    message = _assistant(
        "a1",
        "结果列表",
        paper_search={
            "searched_at": "2026-10-01T10:00:00+00:00",
            "papers": [
                {
                    "arxiv_id": "2401.00001",
                    "title": "论文A",
                    "summary_zh": "摘要正文",
                    "full_text_available": True,
                }
            ],
        },
    )
    scopes = module_evidence_scopes(message, object_ids={"2401.00001"})
    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.object_id == "2401.00001"
    assert "未通读全文" in scope.read_range
    assert "链接可得不等于已读全文" in scope.read_range
    assert scope.source_time == "2026-10-01T10:00:00"
    assert scope.source_message_id == "a1"[:12]


def test_evidence_scope_github_reports_read_counts() -> None:
    message = _assistant(
        "a2",
        "仓库列表",
        github_projects={
            "completed_at": "2026-10-01T11:00:00+00:00",
            "recommendations": [
                {
                    "full_name": "org/repo",
                    "readme_status": "not_fetched",
                    "files_read": ["README.md"],
                    "implementation_checks": [],
                }
            ],
        },
    )
    scopes = module_evidence_scopes(message, object_ids={"org/repo"})
    assert len(scopes) == 1
    assert "未取得 README 正文" in scopes[0].read_range
    assert "实际读取文件 1 个" in scopes[0].read_range


def test_compiler_injects_evidence_scope_for_resolved_object() -> None:
    records = [
        _user("m0", "找几篇论文"),
        _assistant(
            "m1",
            "结果列表",
            paper_search={
                "searched_at": "2026-10-01T09:10:00+00:00",
                "papers": [
                    {"arxiv_id": "2401.00001", "title": "论文A", "summary_zh": "摘要A"},
                    {"arxiv_id": "2401.00002", "title": "论文B", "summary_zh": "摘要B"},
                ],
            },
        ),
        _user("m2", "第二篇论文讲了什么"),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m2",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=32768,
    )
    system = "\n".join(
        message["content"]
        for message in compiled.messages
        if message["role"] == "system"
    )
    assert "已定位的结果对象" in system
    assert "2401.00002" in system
    assert "未通读全文" in system
    assert compiled.reference_adopted_object_ids == ["2401.00002"]


def test_compiler_counts_planned_images_in_budget() -> None:
    records = [_user("m0", "这张图里是什么"), _user("m1", "继续")]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m1",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=32768,
        image_count=2,
    )
    record = compiled.to_record()
    assert record["reserves"]["image_tokens"] == 2 * IMAGE_PART_COST_TOKENS


def test_compiler_photo_floor_exceeded_keeps_request_visible() -> None:
    records = [_user("m0", "这张图里是什么")]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m0",
        model_id="probe-model",
        mode=ChatMode.COMPANION,
        context_window=64,
        image_count=1,
    )
    record = compiled.to_record()
    assert record["reserves"]["image_tokens"] == IMAGE_PART_COST_TOKENS
    assert record["budget_floor_exceeded"] is True
    assert "m0" in record["adopted_message_ids"]


# ---------------------------------------------------------------------------
# 集成：照片轮统一预算与清单
# ---------------------------------------------------------------------------


def _send_photo_turn(
    client: TestClient,
    app: Any,
    generation_helpers: dict[str, Any],
    conversation_id: str,
    *,
    object_id: str,
    content: str,
) -> dict[str, Any]:
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "attachment_ids": [object_id]},
    )
    assert response.status_code == 200, response.text
    generation_helpers["drive"](app)
    return response.json()


def test_photo_turn_compiles_budget_and_records_read_plan(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """照片轮编译产物与预算不再为 null；当前照片进入清单与读取计划。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "i14-budget")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-i14-budget").json()
        conversation_id = _start_conversation(client, app)
        _send_photo_turn(
            client,
            app,
            generation_helpers,
            conversation_id,
            object_id=draft["object_id"],
            content="这张照片里是什么？",
        )

        records = _compiled_records(app)
        assert records, "照片轮未产生编译记录"
        record = records[-1]
        assert record["reserves"]["image_tokens"] == IMAGE_PART_COST_TOKENS
        assert record["image_attachment_ids"] == [draft["object_id"]]
        plan = record["material_read_plan"]
        assert plan["material_read_version"] == MATERIAL_READ_VERSION
        assert [read["kind"] for read in plan["reads"]] == ["current_photo"]
        assert plan["gaps"] == []

        payload = adapter.payloads[-1]
        assert "实际读取的同会话原图" in _system_text(payload)
        manifest = _manifest_records(app)[-1]
        assert manifest["gate"]["within_budget"] is True
        receipt = next(
            entry
            for entry in manifest["entries"]
            if entry["material_id"] == f"current_photo:{draft['object_id']}"
        )
        assert receipt["adopted"] is True
        assert receipt["source_version"] == draft["content_hash"]


def test_old_photo_question_rereads_original_and_manifest_scope(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """旧图追问重新读取原图；旧助手描述仍是纯文本，不构成看图依据。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "i14-reread")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-i14-reread").json()
        conversation_id = _start_conversation(client, app)
        _send_photo_turn(
            client,
            app,
            generation_helpers,
            conversation_id,
            object_id=draft["object_id"],
            content="这张图里是什么？",
        )

        adapter.payloads.clear()
        final = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "刚才那张照片左下角的小字是什么？"},
        )
        assert final.status_code == 200, final.text
        generation_helpers["drive"](app)

        assert adapter.payloads, "模型未被调用"
        payload = adapter.payloads[-1]
        messages = payload["messages"]
        current = messages[-1]
        parts = current["content"]
        assert isinstance(parts, list)
        image_parts = [part for part in parts if part["type"] == "image_url"]
        assert [part["image_url"]["url"] for part in image_parts] == [
            f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
        ]
        system = _system_text(payload)
        assert "实际读取的同会话原图" in system
        assert draft["object_id"] in system
        assert "不构成本轮" in system
        assert all(
            isinstance(message["content"], str) for message in messages[:-1]
        )

        record = _compiled_records(app)[-1]
        plan = record["material_read_plan"]
        assert [read["kind"] for read in plan["reads"]] == ["referenced_photo"]
        assert record["image_attachment_ids"] == [draft["object_id"]]

        manifest = _manifest_records(app)[-1]
        receipt = next(
            entry
            for entry in manifest["entries"]
            if entry["material_id"] == f"referenced_photo:{draft['object_id']}"
        )
        assert receipt["adopted"] is True
        assert receipt["read_range"].startswith("历史原图")
        assert receipt["source_version"] == draft["content_hash"]


def test_deleted_old_photo_yields_gap_not_description(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """原图删除后如实给出缺口，不用旧描述顶替，也不再注入历史图片。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        account = _register(client, "i14-deleted")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-i14-deleted").json()
        conversation_id = _start_conversation(client, app)
        sent = _send_photo_turn(
            client,
            app,
            generation_helpers,
            conversation_id,
            object_id=draft["object_id"],
            content="这张图里是什么？",
        )
        app.state.chat_attachment_service.delete(
            account["id"],
            conversation_id,
            draft["object_id"],
            message_id=sent["user_message"]["message_id"],
        )

        adapter.payloads.clear()
        final = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "刚才那张照片左下角的小字是什么？"},
        )
        assert final.status_code == 200, final.text
        generation_helpers["drive"](app)

        payload = adapter.payloads[-1]
        current = payload["messages"][-1]
        assert isinstance(current["content"], str)
        system = _system_text(payload)
        assert "没有可读取的更早原图" in system
        assert "不得用旧助手描述代替看图" in system

        record = _compiled_records(app)[-1]
        assert record["material_read_plan"]["reads"] == []
        assert record["material_read_plan"]["gaps"]
        manifest = _manifest_records(app)[-1]
        assert not [
            entry
            for entry in manifest["entries"]
            if entry["material_id"].startswith("referenced_photo:")
        ]
        gap_entries = [
            entry
            for entry in manifest["entries"]
            if entry["material_id"].startswith("material_read_gap:")
        ]
        assert gap_entries
        assert all(entry["adopted"] is False for entry in gap_entries)
        assert all(entry["reason"] for entry in gap_entries)


def test_cross_account_and_unknown_conversation_reads_are_rejected(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """跨账户或未知会话读取原图被拒绝，转为缺口而不是返回字节。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register(client, "i14-own")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-i14-scope").json()
        conversation_id = _start_conversation(client, app)
        sent = _send_photo_turn(
            client,
            app,
            generation_helpers,
            conversation_id,
            object_id=draft["object_id"],
            content="看这张照片",
        )
        # 注册第二个账户会切换测试客户端会话；对象归属仍是第一个账户。
        other = _register(client, "i14-other")
        read = MaterialRead(
            kind=MaterialReadKind.CURRENT_PHOTO,
            object_id=draft["object_id"],
            message_id=sent["user_message"]["message_id"],
            media_type="image/png",
            content_hash=draft["content_hash"],
            read_range="本轮原图",
            reason="测试读取。",
        )
        service = app.state.chat_attachment_service

        parts, delivered, gaps = read_photo_payloads(
            service,
            account_id=other["id"],
            conversation_id=conversation_id,
            reads=[read],
        )
        assert (parts, delivered) == ([], [])
        assert gaps

        parts, delivered, gaps = read_photo_payloads(
            service,
            account_id=other["id"],
            conversation_id="unknown-conversation",
            reads=[read],
        )
        assert (parts, delivered) == ([], [])
        assert gaps
