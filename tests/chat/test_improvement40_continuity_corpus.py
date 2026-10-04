"""工单 40 固定原创多轮语料（确定性机制断言）。

对照工单任务第 3 条"固定原创多轮集覆盖以下连续性情形"：

1. 末尾条件跨轮保持（``test_corpus_tail_condition_survives_later_turns``）；
2. 中途纠正覆盖旧值（``test_corpus_correction_overrides_earlier_value``）；
3. 任务往返回到最新有效条件（``test_corpus_task_roundtrip_restores_latest_value``）；
4. 单一列表指代唯一解析（``test_corpus_single_list_reference_resolves``）；
5. 多列表指代只问一个必要澄清（``test_corpus_multi_list_reference_asks_one_clarification``）；
6. 摘要失败不阻塞、不伪造（``test_corpus_summary_failure_falls_back_honestly``）；
7. 旧图细节跨轮重读原图（``test_corpus_old_photo_detail_survives_intervening_turns``）；
8. 跨账户历史互不可见（``test_corpus_cross_account_history_is_isolated``）。

附件末尾条件（工单情形 8）的页码/末尾段选择与已删除来源缺口由
``tests/chat/test_improvement14_material_reads.py``
（``test_select_file_segments_tail_and_section_gap``、
``test_deleted_old_photo_yields_gap_not_description``）以正式读取断言覆盖；
"普通→论文→GitHub"主题续接由工单 15 的任务材料回归与真实配对评测覆盖。
本文件只证明确定性机制；真实模型语义由评测脚本配对给出。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridges.chat.repository import MessageRecord
from bridges.chat.summary import SummaryUnavailableError
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.observability import AuditAction, AuditResult
from tests.chat.test_chat_api import _create_conversation, _register
from tests.chat.test_improvement03_model_quota import _MODEL_A, _activate
from tests.chat.test_improvement40_context_boundaries import (
    _assistant_messages,
    _compiled_records,
    _install,
    _manifest_records,
    _payload_text,
    _ReviewAdapter,
    _seed,
)
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用夹具
    client as client,
)
from tests.chat.test_issue02_durable_generation import (
    sqlite_app as sqlite_app,
)

_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _paper_projection(*titles: str) -> dict[str, Any]:
    return {
        "papers": [
            {"order": index, "title": title, "arxiv_id": f"2401.{index:05d}"}
            for index, title in enumerate(titles, start=1)
        ]
    }


def _github_projection(*names: str) -> dict[str, Any]:
    return {
        "recommendations": [
            {"rank": index, "full_name": name}
            for index, name in enumerate(names, start=1)
        ]
    }


def _seed_records(
    app: Any,
    *,
    account_id: str,
    conversation_id: str,
    records: list[dict[str, Any]],
) -> None:
    """直接落库多轮历史（可带论文/仓库列表投影），并锁定会话模式。"""
    repo = app.state.chat_service._repo  # noqa: SLF001 - 验证正式读路径
    for offset, spec in enumerate(records):
        created = _BASE + timedelta(seconds=offset)
        message_id = spec.get(
            "message_id", f"seed-{conversation_id[-8:]}-{offset:04d}"
        )
        repo.insert_message(
            MessageRecord(
                message_id=message_id,
                conversation_id=conversation_id,
                account_id=account_id,
                role=spec["role"],
                attempt_number=1,
                status=ChatMessageStatus.DONE,
                content=spec["content"],
                thinking=None,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=None,
                run_lock_id=None,
                created_at=created,
                updated_at=created,
            )
        )
        if spec.get("paper_search") is not None:
            repo.update_message_paper_search(
                account_id, message_id, spec["paper_search"], created
            )
        if spec.get("github_projects") is not None:
            repo.update_message_github_projects(
                account_id, message_id, spec["github_projects"], created
            )
    database = app.state.bridges_database
    with database.transaction():
        database.scoped(account_id).execute(
            "UPDATE conversations SET mode_locked = 1"
            " WHERE conversation_id = ? AND account_id = ?",
            (conversation_id, account_id),
        )


# ---------------------------------------------------------------------------
# 情形 1：末尾条件跨轮保持
# ---------------------------------------------------------------------------


def test_corpus_tail_condition_survives_later_turns(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client, tag="4021")

    def answer(payload: dict[str, Any]) -> str:
        if "最终预算不得超过两千五百元" in _payload_text(payload):
            return "你之前定的预算上限是两千五百元。"
        return "我没有找到预算上限。"
    adapter = _ReviewAdapter(answer)
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=3500, max_input=3500)
    conversation_id = _create_conversation(client)
    items: list[tuple[ChatMessageRole, str]] = [
        (
            ChatMessageRole.USER,
            "背景说明。" * 60 + "最终预算不得超过两千五百元。",
        ),
        (ChatMessageRole.ASSISTANT, "好的，已记下预算上限。"),
    ]
    for index in range(6):
        items.append(
            (ChatMessageRole.USER, f"无关话题{index}：" + "闲聊" * 200)
        )
        items.append((ChatMessageRole.ASSISTANT, "收到。"))
    _seed(
        sqlite_app,
        account_id=account["id"],
        conversation_id=conversation_id,
        items=items,
    )
    generation_helpers["send"](
        client, conversation_id, content="之前说好的预算上限是多少？"
    )
    generation_helpers["drive"](sqlite_app)

    assistant = _assistant_messages(client, conversation_id)[-1]
    audit = _compiled_records(sqlite_app)[-1]
    assert audit["unresolved_reference"] is False
    assert "最终预算不得超过两千五百元" in _payload_text(adapter.payloads[-1])
    assert "两千五百元" in assistant["content"]


# ---------------------------------------------------------------------------
# 情形 2：中途纠正覆盖旧值
# ---------------------------------------------------------------------------


def test_corpus_correction_overrides_earlier_value(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client, tag="4022")

    def answer(payload: dict[str, Any]) -> str:
        if "改成三千元" in _payload_text(payload):
            return "最新预算是三千元。"
        return "预算是五千元。"
    adapter = _ReviewAdapter(answer)
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=3500, max_input=3500)
    conversation_id = _create_conversation(client)
    items: list[tuple[ChatMessageRole, str]] = [
        (ChatMessageRole.USER, "帮我选笔记本电脑，预算五千元。"),
        (ChatMessageRole.ASSISTANT, "好的，按五千元筛选。"),
        (ChatMessageRole.USER, "预算改成三千元，最高不超过这个数。"),
        (ChatMessageRole.ASSISTANT, "已更新为三千元。"),
    ]
    for index in range(3):
        items.append((ChatMessageRole.USER, f"补充问题{index}：" + "闲聊" * 100))
        items.append((ChatMessageRole.ASSISTANT, "收到。"))
    _seed(
        sqlite_app,
        account_id=account["id"],
        conversation_id=conversation_id,
        items=items,
    )
    generation_helpers["send"](client, conversation_id, content="现在按预算重新推荐。")
    generation_helpers["drive"](sqlite_app)

    assistant = _assistant_messages(client, conversation_id)[-1]
    assert "改成三千元" in _payload_text(adapter.payloads[-1])
    assert "三千元" in assistant["content"]
    assert "五千元" not in assistant["content"]


# ---------------------------------------------------------------------------
# 情形 3：任务往返回到最新有效条件
# ---------------------------------------------------------------------------


def test_corpus_task_roundtrip_restores_latest_value(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    _register(client, tag="4023")

    def answer(payload: dict[str, Any]) -> str:
        text = _payload_text(payload)
        if "改成三千元" in text:
            return "最新预算是三千元。"
        if "天气" in text:
            return "今天有雨，记得带伞。"
        if "五千元" in text:
            return "已按五千元记录。"
        return "收到。"
    adapter = _ReviewAdapter(answer)
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=4000, max_input=4000)
    conversation_id = _create_conversation(client)
    generation_helpers["send"](
        client, conversation_id, content="帮我选一台笔记本电脑，预算五千元。"
    )
    generation_helpers["drive"](sqlite_app)
    generation_helpers["send"](
        client, conversation_id, content="先不说电脑了，今天天气怎么样？"
    )
    generation_helpers["drive"](sqlite_app)
    generation_helpers["send"](
        client,
        conversation_id,
        content="回到刚才的电脑选购，预算改成三千元，继续推荐。",
    )
    generation_helpers["drive"](sqlite_app)

    messages = _assistant_messages(client, conversation_id)
    assert len(messages) == 3
    assert "带伞" in messages[1]["content"]
    assert "三千元" in messages[2]["content"]
    assert "五千元" not in messages[2]["content"]
    assert "改成三千元" in _payload_text(adapter.payloads[-1])


# ---------------------------------------------------------------------------
# 情形 4：单一列表指代唯一解析
# ---------------------------------------------------------------------------


def test_corpus_single_list_reference_resolves(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client, tag="4024")
    adapter = _ReviewAdapter("第二篇论文讲的是检索增强。")
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=3000, max_input=3000)
    conversation_id = _create_conversation(client)
    _seed_records(
        sqlite_app,
        account_id=account["id"],
        conversation_id=conversation_id,
        records=[
            {"role": ChatMessageRole.USER, "content": "找几篇论文。"},
            {
                "role": ChatMessageRole.ASSISTANT,
                "content": "以下是最新的论文候选。",
                "paper_search": _paper_projection("论文甲", "论文乙"),
            },
        ],
    )
    generation_helpers["send"](client, conversation_id, content="第二篇论文讲了什么？")
    generation_helpers["drive"](sqlite_app)

    audit = _compiled_records(sqlite_app)[-1]
    assert audit["reference_status"] == "resolved"
    assert audit["reference_ambiguous"] is False
    assert audit["reference_adopted_object_ids"]
    assert "论文乙" in _payload_text(adapter.payloads[-1])


# ---------------------------------------------------------------------------
# 情形 5：多列表指代只问一个必要澄清
# ---------------------------------------------------------------------------


def test_corpus_multi_list_reference_asks_one_clarification(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client, tag="4025")
    adapter = _ReviewAdapter()
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=3000, max_input=3000)
    conversation_id = _create_conversation(client)
    _seed_records(
        sqlite_app,
        account_id=account["id"],
        conversation_id=conversation_id,
        records=[
            {"role": ChatMessageRole.USER, "content": "找几篇论文。"},
            {
                "role": ChatMessageRole.ASSISTANT,
                "content": "以下是最新的论文候选。",
                "paper_search": _paper_projection("论文甲", "论文乙"),
            },
            {"role": ChatMessageRole.USER, "content": "再找实现仓库。"},
            {
                "role": ChatMessageRole.ASSISTANT,
                "content": "以下是最新的仓库候选。",
                "github_projects": _github_projection("owner/a", "owner/b"),
            },
        ],
    )
    generation_helpers["send"](client, conversation_id, content="第二个有什么区别？")
    generation_helpers["drive"](sqlite_app)

    audit = _compiled_records(sqlite_app)[-1]
    records = sqlite_app.state.chat_service._repo.list_messages(  # noqa: SLF001
        account["id"], conversation_id
    )
    assert audit["reference_status"] == "ambiguous"
    assert audit["reference_ambiguous"] is True
    assert audit["reference_adopted_object_ids"] == []
    # 多列表歧义只问一个必要澄清，不猜、不调用模型。
    assert records[-1].content == "你指的是第 2 篇论文，还是第 2 个仓库？"
    assert adapter.payloads == []


# ---------------------------------------------------------------------------
# 情形 6：摘要失败不阻塞、不伪造
# ---------------------------------------------------------------------------


class _FailingExtractor:
    version = "issue40-failing-extractor-v1"

    def extract(self, *, run_context: Any, sources: Any, timeout_ms: int) -> Any:
        raise SummaryUnavailableError(
            "summary_call_failed", "测试用摘要不可用。", retryable=False
        )


def test_corpus_summary_failure_falls_back_honestly(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client, tag="4026")
    adapter = _ReviewAdapter("收到。")
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=6000, max_input=6000)
    conversation_id = _create_conversation(client)
    items: list[tuple[ChatMessageRole, str]] = []
    for index in range(40):
        items.append((ChatMessageRole.USER, f"第{index}段历史：" + "问" * 300))
        items.append((ChatMessageRole.ASSISTANT, f"第{index}段回答：" + "答" * 300))
    _seed(
        sqlite_app,
        account_id=account["id"],
        conversation_id=conversation_id,
        items=items,
    )
    sqlite_app.state.chat_summary_service._extractor = (  # noqa: SLF001
        _FailingExtractor()
    )
    generation_helpers["send"](client, conversation_id, content="总结一下。")
    generation_helpers["drive"](sqlite_app)

    assistant = _assistant_messages(client, conversation_id)[-1]
    audit = _compiled_records(sqlite_app)[-1]
    events = sqlite_app.state.observability_service.list_audit_events(
        action=AuditAction.HISTORY_SUMMARY_PREPARED
    )
    assert assistant["status"] == "done", assistant
    assert audit["summary_fallback_entries"] >= 1
    assert any(event.result == AuditResult.RETRYABLE_FAIL for event in events)
    # 摘要失败不把错误正文或伪造摘要塞进载荷。
    assert "summary_call_failed" not in _payload_text(adapter.payloads[-1])


# ---------------------------------------------------------------------------
# 情形 7：旧图细节跨轮重读原图
# ---------------------------------------------------------------------------


def test_corpus_old_photo_detail_survives_intervening_turns(
    tmp_path: Path, monkeypatch: Any, generation_helpers: dict[str, Any]
) -> None:
    from tests.chat.test_v2_05_photo_attachments import (
        _app,
        _CapturingAdapter,
        _gateway_with,
        _start_conversation,
        _upload_draft,
    )
    from tests.chat.test_v2_05_photo_attachments import (
        _register as _register_photo,
    )

    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _register_photo(client, "issue40-corpus-photo")
        adapter = _CapturingAdapter()
        app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
        draft = _upload_draft(client, upload_id="u-issue40-corpus").json()
        conversation_id = _start_conversation(client, app, content="先打个招呼。")
        photo = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "这张照片里是什么？",
                "attachment_ids": [draft["object_id"]],
            },
        )
        assert photo.status_code == 200, photo.text
        generation_helpers["drive"](app)
        with_photo = adapter.payloads[-1]
        assert any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in with_photo["messages"][-1]["content"]
        )

        middle = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "顺便问一下，明天会下雨吗？"},
        )
        assert middle.status_code == 200, middle.text
        generation_helpers["drive"](app)

        detail = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={"content": "刚才那张照片左下角的小字是什么？"},
        )
        assert detail.status_code == 200, detail.text
        generation_helpers["drive"](app)

        payload = adapter.payloads[-1]
        parts = payload["messages"][-1]["content"]
        assert any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in parts
        )
        manifest = _manifest_records(app)[-1]
        photo_entries = [
            entry
            for entry in manifest["entries"]
            if entry["material_id"].startswith("referenced_photo:")
        ]
        assert photo_entries and all(entry["adopted"] for entry in photo_entries)


# ---------------------------------------------------------------------------
# 情形 8：跨账户历史互不可见
# ---------------------------------------------------------------------------


def test_corpus_cross_account_history_is_isolated(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account_a = _register(client, tag="4027")
    adapter = _ReviewAdapter()
    _install(sqlite_app, adapter)
    _activate(sqlite_app, _MODEL_A, window=3500, max_input=3500)
    conversation_a = _create_conversation(client)
    _seed(
        sqlite_app,
        account_id=account_a["id"],
        conversation_id=conversation_a,
        items=[
            (ChatMessageRole.USER, "甲账户的私密标记ALPHA。" + "闲聊" * 100),
            (ChatMessageRole.ASSISTANT, "好的。"),
        ],
    )
    generation_helpers["send"](client, conversation_a, content="继续聊聊。")
    generation_helpers["drive"](sqlite_app)
    payload_a = adapter.payloads[-1]

    account_b = _register(client, tag="4028")
    conversation_b = _create_conversation(client)
    _seed(
        sqlite_app,
        account_id=account_b["id"],
        conversation_id=conversation_b,
        items=[
            (ChatMessageRole.USER, "乙账户的私密标记BETA。" + "闲聊" * 100),
            (ChatMessageRole.ASSISTANT, "好的。"),
        ],
    )
    generation_helpers["send"](client, conversation_b, content="继续聊聊。")
    generation_helpers["drive"](sqlite_app)
    payload_b = adapter.payloads[-1]

    assert "私密标记ALPHA" in _payload_text(payload_a)
    assert "私密标记BETA" not in _payload_text(payload_a)
    assert "私密标记BETA" in _payload_text(payload_b)
    assert "私密标记ALPHA" not in _payload_text(payload_b)
