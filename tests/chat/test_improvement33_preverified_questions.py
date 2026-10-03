"""工单 33：出题前冻结并核验覆盖计划与评分要点的验收测试。

覆盖：有效范围才计划、题干/评分依据/标准答案经内容门、等价表述与评分
要点作答前冻结、登记计算工具复算数值、私有字段与未来题不经读取/SSE 泄露、
呈现事务失败不发坏题、旧评分合同题目保留原判定。确定性模型替身只证明
机制，业务规则全部走正式代码。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.study.review import evaluate_calculation
from tests.chat.test_improvement31_study_scope_preview import ScopeGateway
from tests.chat.test_v2_05_photo_attachments import (
    PNG_BYTES,
    _app,
    _register,
    _upload_draft,
)
from tests.chat.test_v2_17_study_pages import _first
from tests.chat.test_v2_18_study_tutoring import _ask, _retry

_FROZEN_CANONICAL = "标准答案：温度表示物体冷热程度。"
_FROZEN_CORE_POINTS = ["温度的定义", "冷热程度的度量"]


def _default_plan(data: dict[str, Any]) -> list[dict[str, Any]]:
    units = [unit["unit_id"] for unit in data["units"]]
    sources = [source["source_id"] for source in data["sources"]]
    return [
        {
            "question": "请说明本节核心概念及其关系。",
            "coverage_units": units,
            "fragment_ids": sources,
            "core_points": list(_FROZEN_CORE_POINTS),
            "canonical_answer": _FROZEN_CANONICAL,
            "equivalents": ["意思相同的另一种说法"],
            "key_misconceptions": ["把定义说反"],
            "incomplete_basis": "只说出概念，未说明关系",
            "incorrect_basis": "结论与书页冲突",
            "conditions": "",
        }
    ]


class Review33Gateway(ScopeGateway):
    """在范围替身上追加复盘计划/核验/判定三类确定性响应。"""

    def __init__(
        self,
        texts: list[str],
        *,
        map_fn: Any,
        checks_fn: Any = None,
        preview_fn: Any = None,
        plan_fn: Any = None,
        verify_fn: Any = None,
        grade_fn: Any = None,
    ) -> None:
        super().__init__(
            texts, map_fn=map_fn, checks_fn=checks_fn, preview_fn=preview_fn
        )
        self.plan_fn = plan_fn
        self.verify_fn = verify_fn
        self.grade_fn = grade_fn
        self.plan_calls: list[dict[str, Any]] = []
        self.plan_prompts: list[str] = []
        self.question_verify_calls: list[dict[str, Any]] = []
        self.grade_calls: list[dict[str, Any]] = []
        self.grade_judgement = "correct"

    def summarize(self, payload: dict[str, Any]) -> ModelCallResult:
        data = next(
            json.loads(message["content"])
            for message in payload["messages"]
            if message["content"].startswith('{"')
        )
        correct = [
            item["question_id"]
            for item in data["questions"]
            if item["judgement"] == "correct"
        ]
        weak = [
            item["question_id"]
            for item in data["questions"]
            if item["judgement"] != "correct"
        ]
        points: list[dict[str, Any]] = [
            {
                "kind": "learned",
                "text": "本节讲温度的定义。",
                "fragment_ids": [data["sources"][0]["fragment_id"]],
            }
        ]
        if correct:
            points.append(
                {"kind": "mastered", "text": "能说出温度定义。", "question_ids": correct}
            )
        if weak:
            points.append(
                {"kind": "gap", "text": "温度定义还需复习。", "question_ids": weak}
            )
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output={"points": points})

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        task = payload.get("task")
        if task == "study.summarize":
            return self.summarize(payload)
        if task not in {"study.plan_review", "study.verify_questions", "study.grade"}:
            return super().invoke(capability, version, context, payload, **kwargs)
        data = next(
            json.loads(message["content"])
            for message in payload["messages"]
            if message["content"].startswith('{"')
        )
        if task == "study.plan_review":
            self.plan_calls.append(data)
            self.plan_prompts.append(
                "\n".join(message["content"] for message in payload["messages"])
            )
            questions = self.plan_fn(data) if self.plan_fn is not None else _default_plan(data)
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS, output={"questions": questions}
            )
        if task == "study.verify_questions":
            self.question_verify_calls.append(data)
            checks = (
                self.verify_fn(data)
                if self.verify_fn is not None
                else [
                    {
                        "question_id": question["question_id"],
                        "question_matches_knowledge": True,
                        "rubric_supported": True,
                        "answer_consistent": True,
                        "status": "consistent",
                        "detail": "",
                    }
                    for question in data["questions"]
                ]
            )
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS, output={"checks": checks}
            )
        self.grade_calls.append(data)
        output = {
            "question_id": data["question"]["question_id"],
            "judgement": self.grade_judgement,
            "explanation": "按冻结评分要点核对。",
            # 新合同不得采用该值；旧合同用它补齐标准答案。
            "canonical_answer": "旧评分合同生成的标准答案。",
        }
        if self.grade_fn is not None:
            self.grade_fn(data, output)
        return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)


def _single_unit_map(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
    assert repair is False
    return {
        "units": [
            {
                "title": "温度",
                "kind": "concept",
                "fragment_ids": [fragments[0]["id"]],
                "core": True,
            }
        ],
        "exclusions": [],
    }


def _start_review(
    client: TestClient, app: Any, gateway: Review33Gateway, *, drafts: int = 1
) -> tuple[dict[str, Any], str]:
    app.state.chat_service._gateway = gateway
    account = _register(client, "review33")
    object_ids = []
    for index in range(drafts):
        content = PNG_BYTES if index == 0 else PNG_BYTES + f"-extra-{index}".encode()
        draft = _upload_draft(
            client, upload_id=f"review33-page-{index}", content=content
        ).json()
        object_ids.append(draft["object_id"])
    first = _first(client, object_ids, "review33-first")
    assert first.status_code == 201, first.text
    app.state.generation_executor.run_tick()
    endpoint = f"/chat/conversations/{first.json()['conversation']['conversation_id']}"
    return account, endpoint


def _raw_state(app: Any, account_id: str, endpoint: str) -> Any:
    from bridges.study.service import StudyRepository

    conversation_id = endpoint.rsplit("/", 1)[-1]
    state = StudyRepository(app.state.chat_service._repo.database).get(
        account_id, conversation_id
    )
    assert state is not None
    return state


def test_plan_freezes_private_rubric_and_reads_never_leak_it(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        public = result["study"]["review"]
        assert public["scope_version_id"] == result["study"]["scope"]["scope_version_id"]
        assert public["protocol_version"] == "study-review-v2"
        assert len(public["questions"]) == 1
        shown = public["questions"][0]
        assert shown["asked"] is True
        assert shown["canonical_answer"] is None
        assert shown["core_points"] == []
        assert shown["equivalents"] == []
        assert shown["key_misconceptions"] == []
        assert shown["conditions"] == ""
        assert shown["verification"] is None
        assert "标准答案" not in json.dumps(public, ensure_ascii=False)
        assert "冷热程度" not in json.dumps(public, ensure_ascii=False)

        raw = _raw_state(app, account["id"], endpoint)
        stored = raw.review.questions[0]
        assert stored.question_id.startswith("rq_")
        assert stored.legacy is False
        assert stored.scope_version_id == raw.scope.scope_version_id
        assert stored.core_points == list(_FROZEN_CORE_POINTS)
        assert stored.canonical_answer == _FROZEN_CANONICAL
        assert stored.verification is not None
        assert stored.verification.status == "consistent"
        assert stored.verification.answer_consistent is True
        assert raw.state_version == 3
        assert len(gateway.plan_calls) == 1
        assert len(gateway.question_verify_calls) == 1

        assistant_id = result["messages"][-1]["message_id"]
        events = client.get(endpoint + f"/messages/{assistant_id}/events")
        assert events.status_code == 200
        assert "请说明本节核心概念" in events.text
        assert _FROZEN_CANONICAL not in events.text
        assert "冷热程度的度量" not in events.text

        first_read = client.get(endpoint).json()["study"]
        assert client.get(endpoint).json()["study"] == first_read

        graded = _ask(client, app, endpoint, "温度表示冷热程度")
        question = graded["study"]["review"]["questions"][0]
        assert question["judgement"] == "correct"
        # 判定调用拿到的是作答前冻结的依据，而不是临时生成。
        assert gateway.grade_calls[0]["question"]["core_points"] == list(_FROZEN_CORE_POINTS)
        assert gateway.grade_calls[0]["question"]["equivalents"]
        # 判定模型返回的 canonical_answer 被忽略，冻结答案不随答案漂移。
        assert question["canonical_answer"] == _FROZEN_CANONICAL
        assert _FROZEN_CANONICAL in graded["messages"][-1]["content"]


def test_same_title_different_pages_keep_question_evidence_separate(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = [
        "温度是表示冷热程度的物理量。",
        "温度计利用热胀冷缩测量温度。",
    ]

    def map_fn(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        assert repair is False
        return {
            "units": [
                {
                    "title": "温度",
                    "kind": "concept",
                    "fragment_ids": [fragments[0]["id"]],
                    "core": True,
                },
                {
                    "title": "温度",
                    "kind": "concept",
                    "fragment_ids": [fragments[1]["id"]],
                    "core": True,
                },
            ],
            "exclusions": [],
        }

    def plan_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "question": f"第{index + 1}个角度的温度问题。",
                "coverage_units": [unit["unit_id"]],
                "fragment_ids": list(unit["fragment_ids"]),
                "core_points": [f"要点{index + 1}"],
                "canonical_answer": f"答案{index + 1}",
                "equivalents": [f"等价说法{index + 1}"],
                "key_misconceptions": [f"误解{index + 1}"],
                "incomplete_basis": "不完整依据",
                "incorrect_basis": "错误依据",
                "conditions": "",
            }
            for index, unit in enumerate(data["units"])
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(texts, map_fn=map_fn, plan_fn=plan_fn)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway, drafts=2)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(gateway.question_verify_calls) == 1
        verified = gateway.question_verify_calls[0]["questions"]
        assert len({item["question_id"] for item in verified}) == 2
        assert [item["canonical_answer"] for item in verified] == ["答案1", "答案2"]
        assert verified[0]["coverage_units"] != verified[1]["coverage_units"]
        assert verified[0]["fragment_ids"] != verified[1]["fragment_ids"]

        graded = _ask(client, app, endpoint, "第一个答案")
        review = graded["study"]["review"]
        assert review["questions"][0]["canonical_answer"] == "答案1"
        second = next(
            item
            for item in review["questions"]
            if item["question_id"] != review["questions"][0]["question_id"]
        )
        assert second["canonical_answer"] is None
        raw = _raw_state(app, account["id"], endpoint)
        canonical_by_id = {
            item.question_id: item.canonical_answer for item in raw.review.questions
        }
        assert set(canonical_by_id.values()) == {"答案1", "答案2"}
        assert "答案2" not in graded["messages"][-1]["content"]


def test_verification_conflict_repairs_once_then_presents(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def verify_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        status = "conflict" if len(gateway.question_verify_calls) == 1 else "consistent"
        return [
            {
                "question_id": question["question_id"],
                "question_matches_knowledge": True,
                "rubric_supported": True,
                "answer_consistent": True,
                "status": status,
                "detail": "首次核验发现题干未测对应知识",
            }
            for question in data["questions"]
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        verify_fn=verify_fn,
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["stage"] == "review"
        assert len(gateway.plan_calls) == 2
        assert len(gateway.question_verify_calls) == 2
        assert "上一版出题前核验未通过" in gateway.plan_prompts[1]
        assert "首次核验发现题干未测对应知识" in gateway.plan_prompts[1]


def test_persistent_verification_failure_never_presents_question(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def verify_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "question_id": question["question_id"],
                "question_matches_knowledge": False,
                "rubric_supported": True,
                "answer_consistent": False,
                "status": "conflict",
                "detail": "标准答案与书页冲突",
            }
            for question in data["questions"]
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        verify_fn=verify_fn,
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        before = client.get(endpoint).json()["study"]
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "error"
        assert result["messages"][-1]["error_code"] == "study_review_verify_conflict"
        assert result["messages"][-1]["content"] == ""
        assert result["study"] == before
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None
        assert len(gateway.plan_calls) == 2  # 一次有界修复后仍失败
        raw = _raw_state(app, account["id"], endpoint)
        assert raw.review is None
        gateway.verify_fn = None
        resumed = _retry(client, app, endpoint)
        assert resumed["messages"][-1]["status"] == "done", resumed["messages"][-1]
        assert resumed["study"]["stage"] == "review"
        assert len(resumed["study"]["review"]["questions"]) == 1


def test_registered_calculator_rejects_wrong_numeric_rubric(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def verify_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        expected = 5 if len(gateway.question_verify_calls) == 1 else 4
        return [
            {
                "question_id": question["question_id"],
                "question_matches_knowledge": True,
                "rubric_supported": True,
                "answer_consistent": True,
                "status": "consistent",
                "detail": "",
                "calculation": {
                    "expression": "2 + 2",
                    "variables": {},
                    "expected": expected,
                },
            }
            for question in data["questions"]
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        verify_fn=verify_fn,
    )
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(gateway.question_verify_calls) == 2
        assert (
            gateway.question_verify_calls[0]["questions"][0]["canonical_answer"]
            == _FROZEN_CANONICAL
        )
        raw = _raw_state(app, account["id"], endpoint)
        assert raw.review.questions[0].verification.calculation_checked is True

    assert evaluate_calculation("2*a + 1", {"a": 3}) == 7
    assert evaluate_calculation("-b ** 2 + 1", {"b": 2}) == -3
    for bad in ("__import__('os')", "1/0", "c + 1", "2 ** 100"):
        with pytest.raises(ValueError):
            evaluate_calculation(bad, {"a": 1})


def test_persistent_calculation_mismatch_blocks_with_dedicated_code(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def verify_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "question_id": question["question_id"],
                "question_matches_knowledge": True,
                "rubric_supported": True,
                "answer_consistent": True,
                "status": "consistent",
                "detail": "",
                "calculation": {
                    "expression": "2 + 2",
                    "variables": {},
                    "expected": 5,
                },
            }
            for question in data["questions"]
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["温度是表示物体冷热程度的物理量。"],
        map_fn=_single_unit_map,
        verify_fn=verify_fn,
    )
    with TestClient(app) as client:
        _account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "error"
        assert result["messages"][-1]["error_code"] == "study_review_calculation"
        assert result["study"]["stage"] == "tutoring"
        assert result["study"]["review"] is None


def test_self_set_conditions_must_be_labelled(
    tmp_path: Any, monkeypatch: Any
) -> None:
    def plan_fn(data: dict[str, Any]) -> list[dict[str, Any]]:
        unit = data["units"][0]
        conditions = "" if len(gateway.plan_calls) == 1 else "题设：a=2，x=1"
        return [
            {
                "question": "设 a=2，x=1，求 y=ax+b 的值。",
                "coverage_units": [unit["unit_id"]],
                "fragment_ids": list(unit["fragment_ids"]),
                "core_points": ["代入计算"],
                "canonical_answer": "y=a+b",
                "equivalents": [],
                "key_misconceptions": ["漏代入"],
                "incomplete_basis": "未代入",
                "incorrect_basis": "算错",
                "conditions": conditions,
            }
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["线性函数 y=ax+b。"], map_fn=_single_unit_map, plan_fn=plan_fn)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        result = _ask(client, app, endpoint, "开始复盘")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert len(gateway.plan_calls) == 2
        assert "自设条件未明确标注为题设" in gateway.plan_prompts[1]
        raw = _raw_state(app, account["id"], endpoint)
        assert raw.review.questions[0].conditions == "题设：a=2，x=1"


def test_legacy_review_question_keeps_old_judgement_and_path(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(
        ["线性函数 y=ax+b，斜率 a 表示变化率。"], map_fn=_single_unit_map
    )
    with TestClient(app) as client:
        app.state.chat_service._gateway = gateway
        account = _register(client, "review33legacy")
        photo = _upload_draft(client, upload_id="review33-legacy").json()
        first = _first(client, [photo["object_id"]], "review33-legacy-first")
        assert first.status_code == 201, first.text
        app.state.generation_executor.run_tick()
        conversation_id = first.json()["conversation"]["conversation_id"]
        endpoint = f"/chat/conversations/{conversation_id}"
        base = _raw_state(app, account["id"], endpoint)
        fragment_id = base.pages[0].fragments[0].fragment_id
        unit_id = base.units[0].unit_id
        legacy = {
            "subsection_id": conversation_id,
            "stage": "review",
            "state_version": 2,
            "pages": [
                {
                    "object_id": "legacy-page",
                    "ordinal": 1,
                    "content_hash": "legacy-hash",
                    "model_id": "legacy-model",
                    "page_number": 12,
                    "same_section": True,
                    "fragments": [
                        {
                            "fragment_id": fragment_id,
                            "kind": "text",
                            "position": "正文",
                            "text": "线性函数 y=ax+b，斜率 a 表示变化率。",
                            "confidence": 0.9,
                            "source": "photo",
                            "recognition_path": "vision",
                        }
                    ],
                    "unclear": [],
                }
            ],
            "units": [
                {
                    "unit_id": unit_id,
                    "title": "线性函数",
                    "kind": "concept",
                    "fragment_ids": [fragment_id],
                    "core": True,
                }
            ],
            "scope": {
                "scope_version_id": "scope-legacy",
                "revision": 1,
                "protocol_version": "study-scope-v2",
                "material_hash": "legacy",
                "page_object_ids": ["legacy-page"],
                "fragment_ids": [fragment_id],
                "units": [
                    {
                        "unit_id": unit_id,
                        "title": "线性函数",
                        "kind": "concept",
                        "fragment_ids": [fragment_id],
                        "core": True,
                    }
                ],
                "coverage": [{"fragment_id": fragment_id, "unit_ids": [unit_id]}],
                "verified": True,
                "legacy": False,
            },
            "review": {
                "questions": [
                    {
                        "question_id": "old-question-1",
                        "question": "a 是什么？",
                        "coverage_units": [unit_id],
                        "fragment_ids": [fragment_id],
                        "asked": True,
                        "answer": "斜率",
                        "judgement": "correct",
                        "canonical_answer": "a 是斜率。",
                        "explanation": "历史判定保留。",
                    },
                    {
                        "question_id": "old-question-2",
                        "question": "b 是什么？",
                        "coverage_units": [unit_id],
                        "fragment_ids": [fragment_id],
                        "asked": True,
                    },
                ],
                "active_question_id": "old-question-2",
                "complete": False,
            },
        }
        app.state.chat_service._repo.database.scoped(account["id"]).execute(
            "UPDATE study_states SET state_json = ?"
            " WHERE account_id = ? AND conversation_id = ?",
            (json.dumps(legacy, ensure_ascii=False), account["id"], conversation_id),
        )
        projection = client.get(endpoint).json()["study"]
        assert projection["state_version"] == 3
        old = projection["review"]["questions"][0]
        assert old["judgement"] == "correct"
        assert old["canonical_answer"] == "a 是斜率。"
        assert old["legacy"] is True
        assert old["verification"] is None

        result = _ask(client, app, endpoint, "b 是纵截距")
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        graded = result["study"]["review"]["questions"][1]
        assert graded["judgement"] == "correct"
        assert graded["canonical_answer"] == "旧评分合同生成的标准答案。"
        assert result["study"]["review"]["questions"][0]["canonical_answer"] == "a 是斜率。"
        # 旧题不经过新出题前核验，也不重新计划或改写旧判定。
        assert gateway.plan_calls == []
        assert gateway.question_verify_calls == []
        assert gateway.grade_calls[0]["question"]["legacy"] is True


def test_failed_plan_commit_sends_no_question_and_retry_recovers(
    tmp_path: Any, monkeypatch: Any
) -> None:
    app = _app(tmp_path, monkeypatch)
    gateway = Review33Gateway(["温度是表示物体冷热程度的物理量。"], map_fn=_single_unit_map)
    with TestClient(app) as client:
        account, endpoint = _start_review(client, app, gateway)
        from bridges.study.service import StudyRepository

        original = StudyRepository.save_in_transaction

        def fail_commit(*args: Any, **kwargs: Any) -> None:
            original(*args, **kwargs)
            raise RuntimeError("模拟呈现提交中断")

        with monkeypatch.context() as patch:
            patch.setattr(StudyRepository, "save_in_transaction", fail_commit)
            failed = _ask(client, app, endpoint, "开始复盘")
        assert failed["messages"][-1]["status"] == "error"
        assert failed["messages"][-1]["content"] == ""
        assert failed["study"]["stage"] == "tutoring"
        assert failed["study"]["review"] is None
        raw = _raw_state(app, account["id"], endpoint)
        assert raw.review is None
        result = _retry(client, app, endpoint)
        assert result["messages"][-1]["status"] == "done", result["messages"][-1]
        assert result["study"]["stage"] == "review"
        questions = result["study"]["review"]["questions"]
        assert len(questions) == 1 and questions[0]["asked"] is True
