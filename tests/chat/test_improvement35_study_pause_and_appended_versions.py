"""工单 35：恢复暂停复盘与追加书页的原子版本切换。

覆盖 L08、L09、L13 与跨追加版本/暂停/恢复/晚返回故障：

- 暂停或转辅导不批改，已呈现未答单独记录，继续复盘默认从未问题开始；
- 未答内容不当作掌握或错答，总结拒绝把未答题计入 mastered；
- 追加页只在识别/范围核验全部通过后原子切换有效版本，失败保留原范围；
- 未变化页复用识别产物，新增关键冲突未解决不切有效版本，补拍保留旧原文；
- 旧总结移入带范围版本的历史，预习问题保留原范围关系，后续总结不冒用旧版本；
- 停止/重启/并发写竞争下追加结果被版本守卫拒绝，恢复不改超范围状态。

模型替身只提供确定性响应；业务规则走正式代码，真实模型质量由评测票验证。
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.study import StudyExchange
from bridges.lifecycle.catalog import export_rows
from tests.chat.test_v2_05_photo_attachments import (
    PNG_BYTES,
    _app,
    _register,
    _upload_draft,
)
from tests.chat.test_v2_18_study_tutoring import _ask, _retry, _start
from tests.chat.test_v2_20_study_summary import SummaryGateway, _finish_review, _section


class Ticket35Gateway(SummaryGateway):
    """工单 35 替身：支持范围核验冲突、关键疑点注入与未答超报总结。"""

    def __init__(self) -> None:
        super().__init__()
        self.scope_conflict = False
        self.overclaim_unanswered = False
        #: 视觉识别序列（从 0 计数）中注入关键疑点的位置，模拟关键符号看不清。
        self.vision_unclear_at: set[int] = set()

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        if capability == "qwen_vision" and self.vision_count in self.vision_unclear_at:
            index = self.vision_count
            self.vision_count += 1
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "content": json.dumps(
                        {
                            "same_section": True,
                            "page_number": (
                                self.page_numbers[index] if self.page_numbers else None
                            ),
                            "fragments": [
                                {
                                    "kind": "formula",
                                    "position": "中部公式",
                                    "text": "x=-1",
                                    "confidence": 0.9,
                                }
                            ],
                            "unclear": [
                                {"position": "中部公式", "reason": "负号看不清"}
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )
        if (
            capability == "qwen_structured_output"
            and payload.get("task") == "study.verify_scope"
            and self.scope_conflict
        ):
            refs = list(
                dict.fromkeys(
                    re.findall(r'"unit_id":\s*"(ku_[^"]+)"', payload["prompt"])
                )
            )
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "checks": [
                        {
                            "unit_id": ref,
                            "status": "conflict",
                            "detail": "新页与原文冲突，未解决前不得切换范围",
                        }
                        for ref in refs
                    ],
                    "exclusions": [],
                },
            )
        return super().invoke(capability, version, context, payload, **kwargs)

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        if self.overclaim_unanswered:
            data = next(
                json.loads(message["content"])
                for message in payload["messages"]
                if message["content"].startswith('{"')
            )
            unanswered = [
                item["question_id"]
                for item in data["questions"]
                if item["unanswered"]
            ]
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "points": [
                        {
                            "kind": "learned",
                            "text": "本节讲线性函数 y=ax+b。",
                            "fragment_ids": [data["sources"][0]["fragment_id"]],
                        },
                        {
                            "kind": "mastered",
                            "text": "未作答也算掌握。",
                            "question_ids": unanswered,
                        },
                    ]
                },
            )
        return super().summarize(payload)


def _study(client: TestClient, endpoint: str) -> dict[str, Any]:
    return client.get(endpoint).json()["study"]


def test_pause_is_not_graded_and_resume_defaults_to_next_unasked(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        # 进入复盘后第一题已呈现。
        _ask(client, app, endpoint, "学完了")
        # 第一题判定通过后第二题已呈现等待作答。
        graded = _ask(client, app, endpoint, "a 是斜率")["study"]["review"]
        assert [item["judgement"] for item in graded["questions"]] == [
            "correct",
            None,
        ]
        pause = _ask(client, app, endpoint, "暂停复盘")["study"]
        assert pause["stage"] == "tutoring"
        paused = pause["review"]["questions"]
        assert [item["question_id"] for item in paused] == [
            item["question_id"] for item in graded["questions"]
        ]
        assert paused[0]["judgement"] == "correct"
        # 已呈现未答单独记录，来源消息与题干保留，不当作答对。
        assert paused[1]["unanswered"] is True
        assert paused[1]["answer"] is None and paused[1]["judgement"] is None
        assert paused[1]["user_message_id"] is None
        grade_calls = [task for task, _ in gateway.review_calls if task == "study.grade"]
        assert len(grade_calls) == 1
        # 暂停后的辅导消息进入辅导，不批改。
        tutor = _ask(client, app, endpoint, "再解释一下斜率")
        assert tutor["study"]["stage"] == "tutoring"
        assert [task for task, _ in gateway.review_calls if task == "study.grade"] == grade_calls
        assert len(gateway.tutor_payloads) == 1
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert resumed["messages"][-1]["status"] == "done"
        review = resumed["study"]["review"]
        assert review["questions"][:2] == paused
        assert review["questions"][1]["unanswered"] is True
        assert review["questions"][1]["answer"] is None
        assert "复盘第3题" in resumed["messages"][-1]["content"]
        # 已判定题不重判：plan 只调用一次，Q1 没有重新判定。
        assert [task for task, _ in gateway.review_calls].count("study.plan_review") == 1


def test_unanswered_question_never_counts_as_mastered(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    gateway.question_count = 3
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        _ask(client, app, endpoint, "学完了")
        _ask(client, app, endpoint, "a 是斜率")
        paused = _ask(client, app, endpoint, "暂停复盘")["study"]
        assert paused["review"]["questions"][1]["unanswered"] is True
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert "复盘第3题" in resumed["messages"][-1]["content"]
        # 第三题作答不完整后完成复盘；未答的第二题不得被总结当作掌握。
        gateway.judgements = ["incorrect"]
        gateway.overclaim_unanswered = True
        failed = _ask(client, app, endpoint, "不知道")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["error_code"] == "study_summary_invalid"
        assert failed["study"]["review"]["questions"][2]["judgement"] == "incorrect"
        gateway.overclaim_unanswered = False
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["summary"] is not None
        content = result["messages"][-1]["content"]
        mastered = _section(content, "复盘已掌握")
        gaps = _section(content, "还需补的点")
        pending = _section(content, "未作答或尚未判定")
        assert "第2题" not in mastered
        # 工单 36：未作答与判定不完整分开单列，未答不写成错答。
        assert "第2题" not in gaps
        assert "第2题" in pending
        assert "未作答（已呈现，未计入掌握）" in pending
        assert "第3题" in gaps and "判定为错误" in gaps
        material = gateway.summary_calls[-1]
        second = material["questions"][1]
        assert second["unanswered"] is True and second["judgement"] is None
        assert second["basis_current"] is True


@pytest.mark.parametrize("failure", ["ocr", "map"])
def test_append_failure_keeps_effective_scope_and_reuses_recognized_pages(
    tmp_path: Any, monkeypatch: Any, failure: str
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _study(client, endpoint)
        gateway.page_numbers = [12, 13]
        gateway.fail_ocr = failure == "ocr"
        gateway.fail_map = failure == "map"
        photo = _upload_draft(
            client, upload_id=f"append-fail-{failure}", content=PNG_BYTES + b"-2"
        ).json()
        failed = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert failed["messages"][-1]["status"] == "error"
        # 失败不得覆盖原有效书页、范围与总结；候选更新保留可重试。
        assert failed["study"]["pages"] == before["pages"]
        assert failed["study"]["units"] == before["units"]
        assert failed["study"]["scope"] == before["scope"]
        assert failed["study"]["summary"] == before["summary"]
        assert failed["study"]["page_update"] is not None
        gateway.fail_ocr = gateway.fail_map = False
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        study = result["study"]
        assert len(study["pages"]) == 2
        assert study["page_update"] is None
        assert study["scope"]["scope_version_id"] != before["scope"]["scope_version_id"]
        assert [
            item["scope_version_id"] for item in study["scope_history"]
        ] == [before["scope"]["scope_version_id"]]
        # 未变化页复用识别产物：每页视觉识别恰好一次，没有重复识别第一页。
        assert gateway.vision_count == 2
        assert "已更新本节书页" in result["messages"][-1]["content"]


def test_scope_conflict_blocks_switch_until_resolved(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _study(client, endpoint)
        gateway.page_numbers = [12, 13]
        gateway.scope_conflict = True
        photo = _upload_draft(
            client, upload_id="append-conflict", content=PNG_BYTES + b"-2"
        ).json()
        failed = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["error_code"] == "study_scope_content_conflict"
        assert failed["study"]["scope"] == before["scope"]
        assert failed["study"]["pages"] == before["pages"]
        # 双方来源都保留在待提交候选页中，未解决前不切有效版本。
        candidate = failed["study"]["page_update"]
        assert candidate["pages"][0] == before["pages"][0]
        assert candidate["pages"][1]["object_id"] == photo["object_id"]
        assert candidate["pages"][1]["fragments"][0]["text"] == "y=ax+b"
        gateway.scope_conflict = False
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["page_update"] is None
        assert result["study"]["scope"]["scope_version_id"] != before["scope"]["scope_version_id"]


def test_new_page_critical_unclear_blocks_switch_and_supplement_resolves(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _study(client, endpoint)
        gateway.page_numbers = [12, 13]
        gateway.vision_unclear_at = {1}
        photo = _upload_draft(
            client, upload_id="append-critical", content=PNG_BYTES + b"-2"
        ).json()
        waiting = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert waiting["messages"][-1]["status"] == "done"
        assert waiting["study"]["stage"] == "tutoring"
        assert waiting["study"]["scope"] == before["scope"]
        assert waiting["study"]["pages"] == before["pages"]
        candidate = waiting["study"]["page_update"]
        unclear = candidate["pages"][1]["unclear"]
        assert unclear and unclear[0]["critical"] is True
        # 用户补录后解除关键冲突，全部必要步骤通过才切换有效版本。
        resolved = _ask(client, app, endpoint, "第2页中部公式：x=-1")
        assert resolved["messages"][-1]["status"] == "done", resolved["messages"][-1]
        study = resolved["study"]
        assert study["page_update"] is None
        assert study["pages"][1]["unclear"] == []
        assert any(
            fragment["source"] == "user" and fragment["recognition_path"] == "user"
            for fragment in study["pages"][1]["fragments"]
        )
        assert study["scope"]["scope_version_id"] != before["scope"]["scope_version_id"]


def test_replacement_keeps_superseded_source_fragments(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        gateway.page_numbers = [12, 13, 13]
        gateway.vision_unclear_at = {1}
        first = _upload_draft(
            client, upload_id="replace-first", content=PNG_BYTES + b"-2"
        ).json()
        waiting = _ask(client, app, endpoint, "追加一页", attachment_ids=[first["object_id"]])
        old_fragment = waiting["study"]["page_update"]["pages"][1]["fragments"][0]
        # 补拍同一页：新识别片段生效，旧原识别片段作为历史来源保留。
        replacement = _upload_draft(
            client, upload_id="replace-second", content=PNG_BYTES + b"-3"
        ).json()
        result = _ask(
            client,
            app,
            endpoint,
            "补拍第2页",
            attachment_ids=[replacement["object_id"]],
        )
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        page = result["study"]["pages"][1]
        assert page["object_id"] == replacement["object_id"]
        assert first["object_id"] in page["replaced_object_ids"]
        assert page["fragments"][0]["text"] == "y=ax+b"
        assert old_fragment["fragment_id"].startswith(first["object_id"])
        superseded = page["superseded_fragments"]
        assert [item["fragment_id"] for item in superseded] == [
            old_fragment["fragment_id"]
        ]
        assert superseded[0]["text"] == "x=-1"


def test_append_after_summary_versions_history_and_preview_questions(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    gateway.question_count = 1
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _finish_review(client, app, endpoint)["study"]
        assert before["stage"] == "summary"
        assert before["summary"] is not None
        assert before["summary_history"] == []
        old_scope = before["scope"]["scope_version_id"]
        old_questions = before["questions"]
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(
            client, upload_id="summary-append", content=PNG_BYTES + b"-2"
        ).json()
        result = _ask(client, app, endpoint, "漏拍了一页", attachment_ids=[photo["object_id"]])
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        study = result["study"]
        # 原总结移入历史并标注原范围版本；当前总结置空，不复用旧结论。
        assert study["stage"] == "tutoring"
        assert study["summary"] is None
        history = study["summary_history"]
        assert len(history) == 1
        assert history[0]["summary"] == before["summary"]
        assert history[0]["scope_version_id"] == old_scope
        assert "追加" in history[0]["superseded_reason"]
        assert "原总结已作为历史保留" in result["messages"][-1]["content"]
        new_scope = study["scope"]["scope_version_id"]
        assert new_scope != old_scope
        assert [
            item["scope_version_id"] for item in study["scope_history"]
        ] == [old_scope]
        # 预习问题保留原范围关系，不宣称已覆盖新增页。
        assert study["questions"] == old_questions
        assert all(
            question["scope_version_id"] == old_scope for question in study["questions"]
        )
        # 继续复盘按新范围重排未问题；后续总结不冒用旧版本。
        resumed = _ask(client, app, endpoint, "继续复盘")
        assert resumed["messages"][-1]["status"] == "done", resumed["messages"][-1]
        plans = [data for task, data in gateway.review_calls if task == "study.plan_review"]
        assert plans[-1]["scope_version_id"] == new_scope
        renewed = resumed["study"]["review"]["questions"]
        assert renewed[:1] == before["review"]["questions"]
        assert any(
            fragment_id.startswith(photo["object_id"])
            for item in renewed[1:]
            for fragment_id in item["fragment_ids"]
        )
        finished = _finish_review(client, app, endpoint)
        assert finished["study"]["stage"] == "summary"
        assert finished["study"]["summary"] is not None
        assert len(finished["study"]["summary_history"]) == 1
        assert gateway.summary_calls[-1]["questions"][0]["scope_version_id"] == old_scope
        assert finished["study"]["review"]["questions"][0] == before["review"]["questions"][0]
        # 版本化总结历史随账户导出可读，新增持久状态在既有生命周期内。
        account = client.get("/auth/session").json()["account"]
        rows = export_rows(app.state.bridges_database, account["id"], "study_states")
        exported = json.loads(rows[0]["state_json"])
        assert exported["summary_history"][0]["scope_version_id"] == old_scope
        assert exported["summary_history"][0]["summary"] == before["summary"]


def test_stop_during_append_keeps_effective_state_and_recovers(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _study(client, endpoint)
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(
            client, upload_id="append-stop", content=PNG_BYTES + b"-2"
        ).json()
        response = client.post(
            endpoint + "/messages",
            json={"content": "追加一页", "attachment_ids": [photo["object_id"]]},
        )
        assistant_id = response.json()["assistant_message"]["message_id"]
        original = gateway.invoke

        def stopping(*args: Any, **kwargs: Any) -> ModelCallResult:
            result = original(*args, **kwargs)
            if kwargs.get("payload", {}).get("task") == "study.map":
                assert (
                    client.post(endpoint + f"/messages/{assistant_id}/stop").status_code
                    == 200
                )
            return result

        monkeypatch.setattr(gateway, "invoke", stopping)
        app.state.generation_executor.run_tick()
        stopped = client.get(endpoint).json()
        assert stopped["messages"][-1]["status"] == "stopped"
        assert stopped["study"]["pages"] == before["pages"]
        assert stopped["study"]["units"] == before["units"]
        assert stopped["study"]["scope"] == before["scope"]
        assert stopped["study"]["summary"] == before["summary"]
        assert stopped["study"]["page_update"] is not None
        monkeypatch.setattr(gateway, "invoke", original)
        # 恢复复用已完成识别产物，只重跑失败/未提交步骤并原子切换。
        result = _ask(client, app, endpoint, "继续")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["page_update"] is None
        assert len(result["study"]["pages"]) == 2
        assert gateway.vision_count == 2
        assert result["study"]["scope"]["scope_version_id"] != before["scope"]["scope_version_id"]


def test_restart_after_append_failure_recovers_without_duplicates(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        before = _study(client, endpoint)
        gateway.page_numbers = [12, 13]
        gateway.fail_ocr = True
        photo = _upload_draft(
            client, upload_id="append-restart", content=PNG_BYTES + b"-2"
        ).json()
        failed = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert failed["messages"][-1]["status"] == "error"
        assert failed["study"]["page_update"] is not None
        cookies = dict(client.cookies)
    restarted = _app(tmp_path, monkeypatch)
    restarted.state.chat_service._gateway = gateway
    with TestClient(restarted) as client:
        client.cookies.update(cookies)
        assert _study(client, endpoint)["pages"] == before["pages"]
        gateway.fail_ocr = False
        result = _retry(client, restarted, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(result["study"]["pages"]) == 2
        assert result["study"]["page_update"] is None
        assert gateway.vision_count == 2
        _register(client, "appendforeign")
        assert client.get(endpoint).status_code == 404
        assert client.post(endpoint + "/messages", json={"content": "追加一页"}).status_code == 404


def test_append_switch_rejected_when_effective_state_changed_mid_run(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from bridges.study import service as study_service
    from bridges.study.service import StudyRepository

    app = _app(tmp_path, monkeypatch)
    gateway = Ticket35Gateway()
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        endpoint = _start(client, app)
        account = client.get("/auth/session").json()["account"]
        conversation_id = endpoint.rsplit("/", 1)[-1]
        before = _study(client, endpoint)
        # 在追加结果提交前模拟另一个前台操作写入有效小节状态。
        original_finalize = study_service.finalize_message
        calls = {"tampered": False}

        def finalize_with_concurrent_change(*args: Any, **kwargs: Any) -> Any:
            if not calls["tampered"]:
                calls["tampered"] = True
                repository = StudyRepository(app.state.bridges_database)
                current = repository.get(account["id"], conversation_id)
                assert current is not None
                concurrent = current.model_copy(deep=True)
                concurrent.tutoring.append(
                    StudyExchange(
                        user_message_id="concurrent-user",
                        assistant_message_id="concurrent-assistant",
                        question="并发的问题",
                        answer="并发写入了新的有效状态",
                        sources=[],
                    )
                )
                repository.save(account["id"], conversation_id, concurrent)
            return original_finalize(*args, **kwargs)

        monkeypatch.setattr(
            study_service, "finalize_message", finalize_with_concurrent_change
        )
        gateway.page_numbers = [12, 13]
        photo = _upload_draft(
            client, upload_id="append-race", content=PNG_BYTES + b"-2"
        ).json()
        result = _ask(client, app, endpoint, "追加一页", attachment_ids=[photo["object_id"]])
        assert result["messages"][-1]["status"] == "error"
        assert result["messages"][-1]["error_code"] == "study_page_update_changed"
        # 迟到追加被守卫拒绝：并发写入的有效状态保留，旧范围不被覆盖。
        assert result["study"]["pages"] == before["pages"]
        assert result["study"]["scope"] == before["scope"]
        assert result["study"]["tutoring"][-1]["question"] == "并发的问题"
        rows = export_rows(app.state.bridges_database, account["id"], "study_states")
        assert len(rows) == 1


def test_summary_basis_changed_history_is_marked_not_mastered() -> None:
    from bridges.contracts.study import (
        StudyFragment,
        StudyPage,
        StudyReview,
        StudyReviewQuestion,
        StudyState,
    )
    from bridges.study.summary import build_summary, render_summary

    class _StubCompiler:
        def compile_turn_context(self, run: Any, *, system_prompt: str, evidence: Any) -> Any:
            adopted = [item.evidence_id for item in evidence]
            return (
                [{"role": "system", "content": system_prompt}],
                {"budget_floor_exceeded": False, "adopted_evidence_ids": adopted},
            )

    state = StudyState(
        subsection_id="subsection-1",
        stage="review",
        pages=[
            StudyPage(
                object_id="obj-2",
                ordinal=1,
                content_hash="hash-2",
                model_id="qwen-vl",
                page_number=12,
                fragments=[
                    StudyFragment(
                        fragment_id="obj-2:1",
                        kind="text",
                        position="正文",
                        text="y=ax+b",
                        confidence=0.9,
                    )
                ],
            )
        ],
        review=StudyReview(
            questions=[
                StudyReviewQuestion(
                    question_id="q1",
                    question="第一题？",
                    coverage_units=["u1"],
                    fragment_ids=["obj-1:1"],
                    asked=True,
                    answer="旧回答",
                    judgement="correct",
                    canonical_answer="旧答案",
                    explanation="旧解释",
                ),
                StudyReviewQuestion(
                    question_id="q2",
                    question="第二题？",
                    coverage_units=["u1"],
                    fragment_ids=["obj-2:1"],
                    asked=True,
                    unanswered=True,
                ),
            ],
        ),
    )
    invoke_overclaim = lambda task, payload: {  # noqa: E731
        "points": [
            {"kind": "learned", "text": "本节讲线性函数。", "fragment_ids": ["obj-2:1"]},
            {"kind": "mastered", "text": "旧表现也算掌握。", "question_ids": ["q1"]},
        ]
    }
    with pytest.raises(ValueError, match="依据已变化"):
        build_summary(_StubCompiler(), object(), state, invoke=invoke_overclaim)

    summary = build_summary(
        _StubCompiler(),
        object(),
        state,
        invoke=lambda task, payload: {
            "points": [
                {"kind": "learned", "text": "本节讲线性函数。", "fragment_ids": ["obj-2:1"]},
                {"kind": "gap", "text": "依据已更新，需重新确认。", "question_ids": ["q1"]},
                {"kind": "unanswered", "text": "未作答，需补。", "question_ids": ["q2"]},
            ]
        },
    )
    rendered = render_summary(summary, state)
    assert "判定为正确（依据已更新，需重新确认）" in rendered
    assert "未作答（已呈现，未计入掌握）" in rendered
    assert rendered.index("未作答或尚未判定") > rendered.index("还需补的点")
