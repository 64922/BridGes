"""改进工单 30：书页识别质量门、逐页恢复与预算分批。

覆盖验收标准的确定性部分（真实模型识别质量由评测票 42 实测）：

- L01：关键负号/上标或双路径不一致不清时，按页号+位置请求补拍/补录，
  依赖它的映射与预习保持为空（出题被阻塞）；
- 用户文字补录标为用户来源，命中疑点后解除等待；
- R02：已完成页的产品提交后重试从完成收据恢复，不重复识别；
- R03：停止或租约转移后的迟到提交被拒绝，不写入有效材料；
- 预算分批：超预算时已完成页保留、未处理页仍待处理，重试只识别未完成页；
- 原文与模型解释分字段，识别路径随页记录。
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.payload_budget import estimate_payload_tokens
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.lifecycle.catalog import delete_account_rows, export_rows
from bridges.study.kernel import (
    _paths_agree,
    critical_doubt_kinds,
    critical_symbol_kinds,
)
from tests.chat.test_v2_05_photo_attachments import (
    PNG_BYTES,
    _app,
    _register,
    _upload_draft,
)
from tests.chat.test_v2_17_study_pages import StudyGateway, _first


def test_critical_symbol_kinds_cover_required_categories() -> None:
    """负号、上下标、分子分母、单位与核心定义都属于不能靠阈值放行的范围。"""
    assert "负号/正负号" in critical_symbol_kinds("y = -x + 1")
    assert "上下标" in critical_symbol_kinds("x_1 与 x^2")
    assert "上下标" in critical_symbol_kinds("面积 3 m²")
    assert "分子分母" in critical_symbol_kinds("a/b 表示比值")
    assert "单位" in critical_symbol_kinds("速度 9.8 m/s")
    assert "核心定义" in critical_symbol_kinds("该函数定义为奇函数")
    assert critical_symbol_kinds("普通的一句话。") == ()
    # 单词连字符与孤立字母不是关键量，不能把普通文字误判为必须补证。
    assert critical_symbol_kinds("well-known 结论") == ()
    assert critical_symbol_kinds("选项 A 与 B 之一") == ()
    assert "负号/正负号" in critical_doubt_kinds("左下图表：负号看不清")


def test_direct_photo_payload_is_charged_to_payload_budget() -> None:
    """AC6：照片直连载荷按图片成本计入最终载荷预算，不因无 messages 绕过。"""
    prompt_only = estimate_payload_tokens({"prompt": "识别这页"})
    with_image = estimate_payload_tokens(
        {"prompt": "识别这页", "image_base64": base64.b64encode(PNG_BYTES).decode()}
    )
    assert with_image > prompt_only


def test_dual_path_agreement_requires_literal_correspondence() -> None:
    """双路径核对只看两路文字是否互相包含，不引入相似度猜测。"""
    assert _paths_agree("x^2", "计算 x^2 的值")
    assert not _paths_agree("x^2", "计算 x3 的值")
    assert not _paths_agree("x^2", "")


class _CriticalPageGateway(StudyGateway):
    """视觉路径给出含关键符号、但 OCR 文本中找不到的片段。"""

    def __init__(
        self,
        *,
        text: str = "x^2",
        declared_unclear: bool = False,
        unclear_position: str = "中部公式",
    ) -> None:
        super().__init__()
        self._text = text
        self._declared_unclear = declared_unclear
        self._unclear_position = unclear_position

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        if capability == "qwen_vision":
            self.vision_count += 1
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "content": json.dumps(
                        {
                            "same_section": True,
                            "page_number": 1,
                            "fragments": [
                                {
                                    "kind": "formula",
                                    "position": "中部公式",
                                    "text": self._text,
                                    "confidence": 0.95,
                                    "interpretation": "该式表示二次项",
                                }
                            ],
                            "unclear": (
                                [
                                    {
                                        "position": self._unclear_position,
                                        "reason": "负号模糊",
                                    }
                                ]
                                if self._declared_unclear
                                else []
                            ),
                        },
                        ensure_ascii=False,
                    )
                },
            )
        return super().invoke(capability, version, context, payload, **kwargs)


def _conversation_id(first: Any) -> str:
    assert first.status_code == 201, first.text
    return str(first.json()["conversation"]["conversation_id"])


def test_critical_doubt_requests_exact_position_and_blocks_questions(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """L01：负号疑似模糊 → 第N页+位置补拍请求，范围映射与预习不发布。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _CriticalPageGateway(
        text="y = -x + 1", declared_unclear=True
    )
    with TestClient(app) as client:
        _register(client, "study30critical")
        draft = _upload_draft(client, upload_id="study30-critical").json()
        first = _first(client, [draft["object_id"]], "study30-critical-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        study = projection["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["wait_reason"] == "unclear_page"
        assert study["units"] == [] and study["questions"] == []
        issues = study["pages"][0]["unclear"]
        assert any(
            issue["critical"] and issue["position"] == "中部公式"
            for issue in issues
        )
        assert any("负号" in issue["reason"] for issue in issues)

        content = projection["messages"][-1]["content"]
        assert "第1页" in content and "中部公式" in content
        assert "补拍" in content and "补录" in content
        assert "已识别本节范围" not in content


def test_position_level_critical_doubt_is_not_downgraded(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """疑点描述点名负号、但该位置没有可比对片段时，仍按关键疑点阻塞出题。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _CriticalPageGateway(
        text="x^2", declared_unclear=True, unclear_position="左下图表"
    )
    with TestClient(app) as client:
        _register(client, "study30chartdoubt")
        draft = _upload_draft(client, upload_id="study30-chartdoubt").json()
        first = _first(client, [draft["object_id"]], "study30-chartdoubt-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["questions"] == []
        issues = study["pages"][0]["unclear"]
        assert any(
            issue["critical"]
            and issue["position"] == "左下图表"
            and "负号" in issue["reason"]
            for issue in issues
        )


def test_dual_path_mismatch_is_critical_even_with_high_confidence(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """自报高置信不是正确保证：视觉原文与 OCR 对不上仍保持待补充。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _CriticalPageGateway(text="x^2")
    with TestClient(app) as client:
        _register(client, "study30mismatch")
        draft = _upload_draft(client, upload_id="study30-mismatch").json()
        first = _first(client, [draft["object_id"]], "study30-mismatch-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "awaiting_pages"
        assert study["questions"] == []
        mismatch = [
            issue
            for issue in study["pages"][0]["unclear"]
            if issue["kind"] == "dual_path_mismatch"
        ]
        assert mismatch and mismatch[0]["critical"] is True
        assert "上下标" in mismatch[0]["reason"]


def test_user_supplement_is_labeled_and_resolves_critical_doubt(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """用户文字补录标为用户来源，命中疑点后进入辅导并保留原文与解释分字段。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _CriticalPageGateway(text="x^2")
    with TestClient(app) as client:
        _register(client, "study30supplement")
        draft = _upload_draft(client, upload_id="study30-supplement").json()
        first = _first(client, [draft["object_id"]], "study30-supplement-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()
        assert (
            client.get(f"/chat/conversations/{conversation_id}").json()["study"]["stage"]
            == "awaiting_pages"
        )

        supplement = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "第1页中部公式：x^2 表示平方",
                "idempotency_key": "study30-supplement",
            },
        )
        assert supplement.status_code == 200, supplement.text
        app.state.generation_executor.run_tick()

        study = client.get(f"/chat/conversations/{conversation_id}").json()["study"]
        assert study["stage"] == "tutoring"
        fragments = study["pages"][0]["fragments"]
        user_fragments = [item for item in fragments if item["source"] == "user"]
        assert user_fragments
        assert user_fragments[-1]["recognition_path"] == "user"


def test_interpretation_is_separate_from_page_source_text(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """图表/公式推断写 interpretation，不并入书页原文。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = _CriticalPageGateway(text="y=ax+b")
    with TestClient(app) as client:
        _register(client, "study30interpret")
        draft = _upload_draft(client, upload_id="study30-interpret").json()
        first = _first(client, [draft["object_id"]], "study30-interpret-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        page = client.get(f"/chat/conversations/{conversation_id}").json()["study"][
            "pages"
        ][0]
        assert page["fragments"][0]["text"] == "y=ax+b"
        assert page["fragments"][0]["interpretation"] == "该式表示二次项"
        assert page["fragments"][0]["recognition_path"] == "vision"
        assert page["recognition_paths"] == ["ocr", "vision"]


class _RecordingGateway(StudyGateway):
    """按图片字节记录 OCR/视觉调用，并可在第 N 次视觉调用注入一次失败。"""

    def __init__(self, *, fail_vision_at: int | None = None) -> None:
        super().__init__()
        self.seen: list[tuple[str, bytes]] = []
        self._fail_vision_at = fail_vision_at
        self._vision_calls = 0

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        budget = kwargs.get("budget")
        if budget is not None and not budget.register_model_call(
            f"fake:{capability}", purpose=capability
        ):
            return ModelCallResult(
                status=ModelCallStatus.BLOCKED,
                error_code="run_budget_call_limit",
                error_message="本次运行模型调用已达上限。",
            )
        if capability in {"qwen_ocr", "qwen_vision"}:
            self.seen.append(
                (capability, base64.b64decode(payload["image_base64"]))
            )
        if capability == "qwen_vision":
            self._vision_calls += 1
            if self._vision_calls == self._fail_vision_at:
                return ModelCallResult(
                    status=ModelCallStatus.RETRYABLE_FAIL,
                    error_code="transient",
                    error_message="Qwen request timeout.",
                )
        return super().invoke(capability, version, context, payload, **kwargs)

    def ocr_calls_for(self, tag: bytes) -> int:
        return sum(
            1 for capability, data in self.seen
            if capability == "qwen_ocr" and data == PNG_BYTES + tag
        )


def _upload_pages(client: TestClient, tags: list[bytes], prefix: str) -> list[str]:
    return [
        _upload_draft(
            client,
            upload_id=f"{prefix}-{index}",
            content=PNG_BYTES + tag,
        ).json()["object_id"]
        for index, tag in enumerate(tags, 1)
    ]


def test_retry_recovers_committed_pages_from_receipts(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """R02：第 2 页失败重试时，已提交的第 1 页不重复走 OCR/视觉。"""
    app = _app(tmp_path, monkeypatch)
    gateway = _RecordingGateway(fail_vision_at=2)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "study30receipt")
        pages = _upload_pages(client, [b"-p1", b"-p2", b"-p3"], "study30-receipt")
        first = _first(client, pages, "study30-receipt-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["messages"][-1]["status"] == "error"
        assert [page["ordinal"] for page in failed["study"]["pages"]] == [1]

        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{failed['messages'][-1]['message_id']}/retry",
            json={"idempotency_key": "study30-receipt-retry"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
        assert [page["object_id"] for page in reloaded["study"]["pages"]] == pages
        # 完成收据恢复：第 1 页只调用过一次 OCR，重试只补失败页与其后新页。
        assert gateway.ocr_calls_for(b"-p1") == 1
        assert gateway.ocr_calls_for(b"-p2") == 2
        assert gateway.ocr_calls_for(b"-p3") == 1
        assert "检测到1张重复书页，已跳过。" in reloaded["messages"][-1]["content"]


def test_budget_batching_keeps_unfinished_pages_pending(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """超预算时按页保存已完成结果，剩余页保持待处理，重试只识别未完成页。"""
    app = _app(tmp_path, monkeypatch)
    gateway = _RecordingGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "study30budget")
        tags = [f"-b{index}".encode() for index in range(1, 6)]
        pages = _upload_pages(client, tags, "study30-budget")
        first = _first(client, pages, "study30-budget-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        message = failed["messages"][-1]
        assert message["status"] == "error"
        assert message["error_code"] == "run_budget_exhausted"
        assert "未处理页仍待识别" in message["content"] or "未处理页仍待识别" in (
            message["error_message"] or ""
        )
        study = failed["study"]
        assert study["questions"] == [] and study["units"] == []
        assert study["pending_object_ids"] == [pages[4]]
        assert [page["object_id"] for page in study["pages"]] == pages[:4]
        assert "已识别本节范围" not in message["content"]

        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{message['message_id']}/retry",
            json={"idempotency_key": "study30-budget-retry"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["stage"] == "tutoring"
        assert reloaded["study"]["pending_object_ids"] == []
        assert [page["object_id"] for page in reloaded["study"]["pages"]] == pages
        assert all(gateway.ocr_calls_for(tag) == 1 for tag in tags)


def test_new_upload_does_not_drop_previous_pending_pages(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """新上传不会静默丢弃上一轮未完成页：旧页仍待处理，重试原消息只补它。"""
    app = _app(tmp_path, monkeypatch)
    gateway = _RecordingGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "study30pending")
        tags = [f"-k{index}".encode() for index in range(1, 6)]
        pages = _upload_pages(client, tags, "study30-pending")
        first = _first(client, pages, "study30-pending-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()
        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["study"]["pending_object_ids"] == [pages[4]]

        extra = _upload_draft(
            client, upload_id="study30-pending-extra", content=PNG_BYTES + b"-k6"
        ).json()
        follow = client.post(
            f"/chat/conversations/{conversation_id}/messages",
            json={
                "content": "",
                "attachment_ids": [extra["object_id"]],
                "idempotency_key": "study30-pending-extra",
            },
        )
        assert follow.status_code == 200, follow.text
        app.state.generation_executor.run_tick()

        blocked = client.get(f"/chat/conversations/{conversation_id}").json()
        assert blocked["study"]["pending_object_ids"] == [pages[4]]
        assert blocked["study"]["questions"] == []
        assert extra["object_id"] in [
            page["object_id"] for page in blocked["study"]["pages"]
        ]
        assert "重试原消息" in blocked["messages"][-1]["content"]

        retry = client.post(
            f"/chat/conversations/{conversation_id}/messages/"
            f"{failed['messages'][-1]['message_id']}/retry",
            json={"idempotency_key": "study30-pending-retry"},
        )
        assert retry.status_code == 200, retry.text
        app.state.generation_executor.run_tick()

        reloaded = client.get(f"/chat/conversations/{conversation_id}").json()
        assert reloaded["study"]["pending_object_ids"] == []
        assert reloaded["study"]["stage"] == "tutoring"
        assert {page["object_id"] for page in reloaded["study"]["pages"]} == {
            *pages,
            extra["object_id"],
        }


def test_study_page_artifacts_enter_export_and_delete(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """AC6：书页识别产物/收据进入账户导出与删除清单（v65 生命周期）。"""
    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = StudyGateway()
    with TestClient(app) as client:
        account = _register(client, "study30lifecycle")
        draft = _upload_draft(client, upload_id="study30-lifecycle").json()
        first = _first(client, [draft["object_id"]], "study30-lifecycle-first")
        conversation_id = _conversation_id(first)
        app.state.generation_executor.run_tick()

        database = app.state.bridges_database
        exported = export_rows(database, account["id"], "node_artifacts")
        assert any(
            row["node"] == "study.recognize_page"
            and row["conversation_id"] == conversation_id
            for row in exported
        )
        delete_account_rows(database, account["id"])
        assert export_rows(database, account["id"], "node_artifacts") == []


def test_stop_during_recognition_does_not_commit_material(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """R03：识别中用户停止 → 终态 stopped，本页材料不提交、不发布预习。"""
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "study30stop")
        draft = _upload_draft(client, upload_id="study30-stop").json()
        first = _first(client, [draft["object_id"]], "study30-stop-first")
        conversation_id = _conversation_id(first)
        assistant_id = first.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping_invoke(
            capability: str, *args: Any, **kwargs: Any
        ) -> ModelCallResult:
            result = original(capability, *args, **kwargs)
            if capability == "qwen_vision":
                stopped = client.post(
                    f"/chat/conversations/{conversation_id}/messages/{assistant_id}/stop"
                )
                assert stopped.status_code == 200
            return result

        monkeypatch.setattr(gateway, "invoke", stopping_invoke)
        app.state.generation_executor.run_tick()

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["messages"][-1]["status"] == "stopped"
        assert projection["study"]["stage"] == "recognizing"
        assert projection["study"]["pages"] == []
        assert projection["study"]["questions"] == []


def test_lease_transfer_rejects_stale_commit(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """R03：租约转移后的迟到识别结果被守卫拒绝，不写入书页材料。"""
    app = _app(tmp_path, monkeypatch)
    gateway = StudyGateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        account = _register(client, "study30lease")
        draft = _upload_draft(client, upload_id="study30-lease").json()
        first = _first(client, [draft["object_id"]], "study30-lease-first")
        conversation_id = _conversation_id(first)
        assistant_id = first.json()["assistant_message"]["message_id"]
        run = app.state.chat_service._repo.get_run_by_message(account["id"], assistant_id)
        assert run is not None
        original = gateway.invoke
        transferred = False

        def lease_losing_invoke(
            capability: str, *args: Any, **kwargs: Any
        ) -> ModelCallResult:
            nonlocal transferred
            result = original(capability, *args, **kwargs)
            if capability == "qwen_vision" and not transferred:
                transferred = True
                database = app.state.bridges_database
                with database.transaction():
                    database.connection.execute(
                        "UPDATE generation_runs SET lease_owner = 'other-worker'"
                        " WHERE run_id = ?",
                        (run.run_id,),
                    )
            return result

        monkeypatch.setattr(gateway, "invoke", lease_losing_invoke)
        app.state.generation_executor.run_tick()

        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        assert projection["messages"][-1]["status"] == "error"
        assert projection["messages"][-1]["error_code"] == "lease_lost"
        assert projection["study"]["pages"] == []
        assert projection["study"]["questions"] == []
        completed = app.state.bridges_database.connection.execute(
            "SELECT COUNT(*) FROM node_receipts"
            " WHERE node = 'study.recognize_page' AND status = 'completed'"
        ).fetchone()[0]
        assert completed == 0
