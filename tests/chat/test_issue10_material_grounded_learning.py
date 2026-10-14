"""Issue 10：照片和文件资料的按需教学。

覆盖验收项：
1. 照片可启动学习（纯资料进入导师流程）；
2. 提问依据冻结进待答问题与学习证据，引用带实际读取范围与可访问标记；
3. 补拍/更新资料使原题按旧版本作废，不按过期依据评价；
4. 资料删除后受影响题目失效并如实记录未验证，不改写历史判定；
5. 文件与照片来源在来源引用中区分（读取范围、版本、消息）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from bridges.chat.material_reading import (
    FileSegment,
    MaterialRead,
    MaterialReadKind,
    material_source_refs,
)
from bridges.study.verification import StudyVerificationRepository
from tests.chat.test_issue02_text_tutor import _TextTutorAdapter
from tests.chat.test_issue03_embedded_verification import (
    _close,
    _gateway_with_verification,
    _plan,
    _plan_evaluate,
    _plan_pose,
    _PlanAdapter,
)
from tests.chat.test_v2_05_photo_attachments import (
    _app,
    _register,
    _upload_draft,
)

_Q_PHOTO = "照片里把 y=ax+b 的 a 判成斜率的依据是什么？为什么不是截距？"
_Q_RETAKE = "补拍后能看清定义域标注了吗？它写在哪个位置？"
_ANSWER = "图上写着 a 是斜率、b 是截距，所以 a 对应斜率。"


def _start_photo_study(
    client: TestClient,
    app: Any,
    generation_helpers: dict[str, Any],
    text: _TextTutorAdapter,
    plan: _PlanAdapter,
    *,
    key: str,
) -> dict[str, Any]:
    """上传照片并启动学习首轮，驱动到待答问题生效。"""

    draft = _upload_draft(client, upload_id=f"{key}-draft").json()
    text.answer = f"我先看这张照片。{_Q_PHOTO}"
    plan.default = _plan_pose(_Q_PHOTO, context="线性函数 y=ax+b")
    response = client.post(
        "/chat/first-turn",
        json={
            "mode": "study",
            "content": "这张照片里的推导请帮我理解。",
            "attachment_ids": [draft["object_id"]],
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201, response.text
    conversation_id = response.json()["conversation"]["conversation_id"]
    generation_helpers["drive"](app)
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    pending = detail["study_pending_check"]
    assert pending is not None, "照片学习未产生待答问题"
    assert pending["question"] == _Q_PHOTO
    user_message = next(
        message for message in detail["messages"] if message["role"] == "user"
    )
    return {
        "draft": draft,
        "conversation_id": conversation_id,
        "user_message_id": user_message["message_id"],
    }


def test_photo_grounded_check_freezes_refs_and_records_availability(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """提问依据冻结进待答问题；作答证据保留真实引用与可访问标记。"""

    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("")
    plan = _PlanAdapter()
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    try:
        with TestClient(app) as client:
            account = _register(client, "issue10-grounding")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)
            grounded = _start_photo_study(
                client, app, generation_helpers, text, plan,
                key="issue10-ground-1",
            )
            conversation_id = grounded["conversation_id"]
            draft = grounded["draft"]

            pending = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert pending["source_status"] == "available"

            stored = database.connection.execute(
                "SELECT basis_json FROM study_pending_checks"
            ).fetchone()
            basis = json.loads(stored["basis_json"])
            assert basis["source_refs"], "提问依据未冻结资料引用"
            ref = basis["source_refs"][0]
            assert ref["kind"] == "photo"
            assert ref["object_id"] == draft["object_id"]
            assert ref["message_id"] == grounded["user_message_id"]
            assert ref["source_version"] == draft["content_hash"]
            assert ref["read_range"].startswith("本轮原图")
            # 私有读取细节只存在于服务端，不进入用户投影。
            assert "source_version" not in json.dumps(pending, ensure_ascii=False)

            plan.default = _plan_evaluate()
            text.answer = "对，图上标注 a 是斜率、b 是截距。"
            response = client.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={"content": _ANSWER, "idempotency_key": "issue10-ground-2"},
            )
            assert response.status_code == 200, response.text
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            evidence = repository.list_evidence(account["id"], conversation_id)
            assert len(evidence) == 1
            item = evidence[0]
            assert item.answered is True
            assert item.outcome.value == "correct"
            assert item.source_refs, "作答证据未保留资料引用"
            resolved = item.source_refs[0]
            assert resolved["object_id"] == draft["object_id"]
            assert resolved["available"] is True
    finally:
        _close(app)


def test_material_update_voids_old_check_without_judging(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """补拍更新资料：原题按旧版本作废，记录未验证，不按旧依据判学情。"""

    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("")
    plan = _PlanAdapter()
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    try:
        with TestClient(app) as client:
            account = _register(client, "issue10-retake")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)
            grounded = _start_photo_study(
                client, app, generation_helpers, text, plan,
                key="issue10-retake-1",
            )
            conversation_id = grounded["conversation_id"]

            retake = _upload_draft(
                client, upload_id="issue10-retake-draft-2",
            ).json()
            text.answer = f"收到补拍，我重新读取。{_Q_RETAKE}"
            plan.default = _plan(
                action="pose_check",
                intent="material_update",
                check_kind="open_probe",
                check_question=_Q_RETAKE,
                check_knowledge_context="补拍后的定义域标注",
            )
            response = client.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={
                    "content": "刚才那张不清楚，我重新拍了一张。",
                    "attachment_ids": [retake["object_id"]],
                    "idempotency_key": "issue10-retake-2",
                },
            )
            assert response.status_code == 200, response.text
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            pending = detail["study_pending_check"]
            assert pending is not None
            assert pending["question"] == _Q_RETAKE

            evidence = repository.list_evidence(account["id"], conversation_id)
            assert len(evidence) == 1
            item = evidence[0]
            assert item.question == _Q_PHOTO
            assert item.answered is False
            assert item.semantics.value == "unverified"
            assert "补拍" in item.reasoning_note
            assert item.outcome.value == "no_answer"
            assert item.answer_message_id is None

            # 新题依据指向本轮补拍的照片，而不是旧图。
            stored = database.connection.execute(
                "SELECT basis_json FROM study_pending_checks"
            ).fetchone()
            basis = json.loads(stored["basis_json"])
            assert [ref["object_id"] for ref in basis["source_refs"]] == [
                retake["object_id"]
            ]
    finally:
        _close(app)


def test_deleted_material_releases_check_without_stale_judgement(
    tmp_path: Any, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    """资料删除：待答问题投影为不可用；作答不按过期依据评价。"""

    app = _app(tmp_path, monkeypatch)
    text = _TextTutorAdapter("")
    plan = _PlanAdapter()
    app.state.chat_service._gateway = _gateway_with_verification(text, plan)
    try:
        with TestClient(app) as client:
            account = _register(client, "issue10-deleted")
            database = app.state.bridges_database
            repository = StudyVerificationRepository(database)
            grounded = _start_photo_study(
                client, app, generation_helpers, text, plan,
                key="issue10-deleted-1",
            )
            conversation_id = grounded["conversation_id"]
            draft = grounded["draft"]

            app.state.chat_attachment_service.delete(
                account["id"],
                conversation_id,
                draft["object_id"],
                message_id=grounded["user_message_id"],
            )
            pending = client.get(
                f"/chat/conversations/{conversation_id}"
            ).json()["study_pending_check"]
            assert pending is not None
            assert pending["source_status"] == "unavailable"

            text.answer = "a 是斜率、b 是截距。"
            plan.default = _plan_evaluate()
            response = client.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={"content": _ANSWER, "idempotency_key": "issue10-deleted-2"},
            )
            assert response.status_code == 200, response.text
            generation_helpers["drive"](app)

            detail = client.get(f"/chat/conversations/{conversation_id}").json()
            assert detail["study_pending_check"] is None
            evidence = repository.list_evidence(account["id"], conversation_id)
            assert len(evidence) == 1
            item = evidence[0]
            assert item.question == _Q_PHOTO
            assert item.answered is False
            assert item.semantics.value == "unverified"
            assert item.outcome.value == "no_answer"
            assert "资料已不可用" in item.reasoning_note
            assert [ref["object_id"] for ref in item.source_refs] == [
                draft["object_id"]
            ]
            assert item.source_refs[0]["available"] is False
    finally:
        _close(app)


def test_material_source_refs_keep_photo_and_file_read_ranges() -> None:
    """照片与文件来源引用区分类型，并保留读取范围与来源版本。"""

    photo = MaterialRead(
        kind=MaterialReadKind.CURRENT_PHOTO,
        object_id="obj-photo",
        message_id="msg-photo",
        media_type="image/png",
        content_hash="hash-photo",
        read_range="本轮原图（第 1 张）",
        reason="当前照片",
        ordinal=1,
        filename="photo.png",
    )
    segment = FileSegment(
        citation_id="cit-1",
        object_id="obj-file",
        filename="notes.pdf",
        content="定义域是 x 的取值集合。",
        content_hash="hash-file",
        page_number=3,
        section_title="线性函数",
    )
    refs = material_source_refs([photo], [segment])
    assert [ref["kind"] for ref in refs] == ["photo", "file_segment"]
    assert refs[0]["message_id"] == "msg-photo"
    assert refs[0]["source_version"] == "hash-photo"
    assert refs[0]["read_range"] == "本轮原图（第 1 张）"
    assert refs[1]["page_number"] == 3
    assert refs[1]["read_range"] == "第 3 页 · 线性函数"
    assert refs[1]["source_version"] == "hash-file"
