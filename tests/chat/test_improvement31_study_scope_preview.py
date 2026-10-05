"""工单 31：知识范围覆盖核验与辅助预习的验收测试。

覆盖：同名概念跨页不串 ID/依据、实质片段覆盖或排除有理由、结构门与
内容正确性分开、按密度提问、提交前不推进、重试复用映射不重复预习、
旧状态升级与导出恢复。模型替身只提供确定性响应，业务规则走正式代码。
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.lifecycle.catalog import export_rows
from bridges.study.scope import assign_unit_id
from tests.chat.test_expression_fact_drift import _service_with_answer
from tests.chat.test_v2_05_photo_attachments import (
    PNG_BYTES,
    _app,
    _register,
    _upload_draft,
)
from tests.chat.test_v2_17_study_pages import StudyGateway, _first

_FRAGMENT_PATTERN = re.compile(
    r'"fragment_id": "([^"]+)", "page": (\d+), "kind": "([^"]*)", '
    r'"position": "([^"]*)", "text": "([^"]*)"'
)


def _prompt_fragments(prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "id": match.group(1),
            "page": int(match.group(2)),
            "kind": match.group(3),
            "position": match.group(4),
            "text": match.group(5),
        }
        for match in _FRAGMENT_PATTERN.finditer(prompt)
    ]


class ScopeGateway(StudyGateway):
    """按页给定文本、按任务给定映射/核对/预习响应的范围测试替身。"""

    def __init__(
        self,
        texts: list[str],
        *,
        map_fn: Any,
        checks_fn: Any = None,
        preview_fn: Any = None,
    ) -> None:
        super().__init__()
        self.texts = list(texts)
        self.map_fn = map_fn
        self.checks_fn = checks_fn
        self.preview_fn = preview_fn
        self.ocr_count = 0
        self.map_calls: list[dict[str, Any]] = []
        self.verify_calls: list[dict[str, Any]] = []
        self.preview_calls: list[dict[str, Any]] = []
        self.unit_ids: list[str] = []

    def invoke(
        self,
        capability: str,
        version: str,
        context: Any,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> ModelCallResult:
        if capability == "qwen_ocr":
            text = self.texts[min(self.ocr_count, len(self.texts) - 1)]
            self.ocr_count += 1
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS, output={"content": text}
            )
        if capability == "qwen_vision":
            index = self.vision_count
            self.vision_count += 1
            text = self.texts[min(index, len(self.texts) - 1)]
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "content": json.dumps(
                        {
                            "same_section": True,
                            "page_number": 12 + index,
                            "fragments": [
                                {
                                    "kind": "text",
                                    "position": "正文",
                                    "text": text,
                                    "confidence": 0.95,
                                }
                            ],
                            "unclear": [],
                        },
                        ensure_ascii=False,
                    )
                },
            )
        if capability == "qwen_structured_output":
            task = payload.get("task")
            if task == "study.map":
                fragments = _prompt_fragments(payload["prompt"])
                repair = "上一版范围核验未通过" in payload["prompt"]
                self.map_calls.append(
                    {"fragments": fragments, "repair": repair, "prompt": payload["prompt"]}
                )
                mapping = self.map_fn(fragments, repair)
                self.unit_ids = [
                    assign_unit_id(
                        unit.get("kind", "concept"), unit["title"], unit["fragment_ids"]
                    )
                    for unit in mapping["units"]
                ]
                return ModelCallResult(status=ModelCallStatus.SUCCESS, output=mapping)
            if task == "study.verify_scope":
                unit_ids = list(
                    dict.fromkeys(
                        re.findall(r'"unit_id": "(ku_[^"]+)"', payload["prompt"])
                    )
                )
                self.verify_calls.append(
                    {"unit_ids": unit_ids, "prompt": payload["prompt"]}
                )
                checks = (
                    self.checks_fn(unit_ids)
                    if self.checks_fn is not None
                    else [
                        {"unit_id": unit_id, "status": "consistent", "detail": ""}
                        for unit_id in unit_ids
                    ]
                )
                return ModelCallResult(
                    status=ModelCallStatus.SUCCESS, output={
                        "checks": checks,
                        "exclusions": [
                            {"fragment_id": item["fragment_id"], "status": "consistent"}
                            for item in json.loads(payload["prompt"].split("\n被排除片段：")[1])
                        ],
                    }
                )
            if task == "study.preview":
                self.preview_calls.append(payload)
                questions = (
                    self.preview_fn(self.unit_ids)
                    if self.preview_fn is not None
                    else [
                        {
                            "question": "带着问题阅读本节，暂不需要作答。",
                            "unit_ids": list(self.unit_ids),
                        }
                    ]
                )
                return ModelCallResult(
                    status=ModelCallStatus.SUCCESS, output={"questions": questions}
                )
            raise AssertionError(task)
        raise AssertionError(capability)


def _tick(app: Any) -> None:
    app.state.generation_executor.run_tick()


def _study(client: TestClient, conversation_id: str) -> dict[str, Any]:
    return client.get(f"/chat/conversations/{conversation_id}").json()["study"]


def _retry(client: TestClient, app: Any, conversation_id: str, key: str) -> None:
    endpoint = f"/chat/conversations/{conversation_id}"
    message = client.get(endpoint).json()["messages"][-1]
    response = client.post(
        f"{endpoint}/messages/{message['message_id']}/retry",
        json={"idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    _tick(app)


def test_same_title_on_different_pages_keeps_distinct_ids_and_evidence(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = [
        "温度是表示物体冷热程度的物理量。",
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

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(texts, map_fn=map_fn)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        account = _register(client, "scope31same")
        a = _upload_draft(client, upload_id="scope31-a").json()
        b = _upload_draft(
            client, upload_id="scope31-b", content=PNG_BYTES + b"-same-title"
        ).json()
        first = _first(client, [a["object_id"], b["object_id"]], "scope31-same-first")
        assert first.status_code == 201, first.text
        conversation_id = first.json()["conversation"]["conversation_id"]
        _tick(app)
        study = _study(client, conversation_id)
        assert study["stage"] == "tutoring", study
        assert [unit["title"] for unit in study["units"]] == ["温度", "温度"]
        ids = [unit["unit_id"] for unit in study["units"]]
        assert len(set(ids)) == 2
        fragments = gateway.map_calls[0]["fragments"]
        assert ids == [
            assign_unit_id("concept", "温度", [fragments[0]["id"]]),
            assign_unit_id("concept", "温度", [fragments[1]["id"]]),
        ]
        coverage = {item["fragment_id"]: item for item in study["scope"]["coverage"]}
        assert coverage[fragments[0]["id"]]["unit_ids"] == [ids[0]]
        assert coverage[fragments[1]["id"]]["unit_ids"] == [ids[1]]
        question = study["questions"][0]
        assert sorted(question["unit_ids"]) == sorted(ids)
        assert question["scope_version_id"] == study["scope"]["scope_version_id"]
        content = client.get(
            f"/chat/conversations/{conversation_id}"
        ).json()["messages"][-1]["content"]
        assert "暂不需要作答" in content
        assert len(gateway.map_calls) == 1
        assert len(gateway.verify_calls) == 1
        run = app.state.chat_service._repo.get_run_by_message(
            account["id"], first.json()["assistant_message"]["message_id"]
        )
        assert (
            run.config or {}
        ).get("global_writing_policy", {}).get("snapshot_complete") is True


def test_unmapped_teaching_fragment_needs_reason_and_repair_is_bounded(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = [
        "第 12 页 第三章 温度",
        "本节介绍温度的定义：温度表示冷热程度。",
        "例题：计算平均温度。",
    ]

    def map_fn(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        units = [
            {
                "title": "温度",
                "kind": "concept",
                "fragment_ids": [fragments[1]["id"]],
                "core": True,
            }
        ]
        exclusions = [
            {
                "fragment_id": fragments[0]["id"],
                "reason": "页眉页码，非教学内容",
            }
        ]
        if repair:
            exclusions.append(
                {
                    "fragment_id": fragments[2]["id"],
                    "reason": "例题属于练习，不进入本节知识范围",
                }
            )
        return {"units": units, "exclusions": exclusions}

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(texts, map_fn=map_fn)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "scope31repair")
        attachments = [
            _upload_draft(
                client, upload_id=f"scope31-repair-{index}",
                content=PNG_BYTES + f"-repair-{index}".encode(),
            ).json()["object_id"]
            for index in range(3)
        ]
        first = _first(client, attachments, "scope31-repair-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        _tick(app)
        study = _study(client, conversation_id)
        assert study["stage"] == "tutoring", study
        assert len(study["units"]) == 1
        coverage = {item["fragment_id"]: item for item in study["scope"]["coverage"]}
        fragments = gateway.map_calls[0]["fragments"]
        assert coverage[fragments[0]["id"]]["exclusion_reason"] == "页眉页码，非教学内容"
        assert coverage[fragments[2]["id"]]["exclusion_reason"]
        assert len(gateway.map_calls) == 2
        assert gateway.map_calls[0]["repair"] is False
        assert gateway.map_calls[1]["repair"] is True
        assert "不得增加知识点数量" in gateway.map_calls[1]["prompt"]
        assert len(gateway.verify_calls) == 1


def test_formula_conflict_blocks_until_repaired_content(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = ["质能方程 E=mc² 表明质量与能量等价。"]

    def map_fn(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        return {
            "units": [
                {
                    "title": "质能方程",
                    "kind": "relation",
                    "fragment_ids": [fragments[0]["id"]],
                    "core": True,
                }
            ],
            "exclusions": [],
        }

    def checks_fn(unit_ids: list[str]) -> list[dict[str, Any]]:
        status = "conflict" if len(gateway.verify_calls) == 1 else "consistent"
        return [
            {"unit_id": unit_id, "status": status, "detail": "与原文逐字核对"}
            for unit_id in unit_ids
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(texts, map_fn=map_fn, checks_fn=checks_fn)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "scope31conflict")
        photo = _upload_draft(client, upload_id="scope31-conflict").json()
        first = _first(client, [photo["object_id"]], "scope31-conflict-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        _tick(app)
        study = _study(client, conversation_id)
        assert study["stage"] == "tutoring", study
        assert len(gateway.map_calls) == 2
        assert gateway.map_calls[1]["repair"] is True
        assert len(gateway.verify_calls) == 2
        assert study["scope"]["content_checks"][0]["status"] == "consistent"
        assert study["questions"]


def test_persistent_content_conflict_never_publishes_preview(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = ["质能方程 E=mc² 表明质量与能量等价。"]

    def map_fn(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        return {
            "units": [
                {
                    "title": "质能方程",
                    "kind": "relation",
                    "fragment_ids": [fragments[0]["id"]],
                    "core": True,
                }
            ],
            "exclusions": [],
        }

    def checks_fn(unit_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"unit_id": unit_id, "status": "conflict", "detail": "映射写成 E=mc³"}
            for unit_id in unit_ids
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(texts, map_fn=map_fn, checks_fn=checks_fn)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "scope31blocked")
        photo = _upload_draft(client, upload_id="scope31-blocked").json()
        first = _first(client, [photo["object_id"]], "scope31-blocked-first")
        conversation_id = first.json()["conversation"]["conversation_id"]
        _tick(app)
        projection = client.get(f"/chat/conversations/{conversation_id}").json()
        message = projection["messages"][-1]
        assert message["status"] == "error"
        assert message["error_code"] == "study_scope_content_conflict"
        assert "核对未通过" in message["error_message"]
        study = projection["study"]
        assert study["stage"] == "recognizing"
        assert study["questions"] == [] and study["units"] == []
        assert len(gateway.map_calls) == 2
        assert len(gateway.verify_calls) == 2
        assert gateway.preview_calls == []


def test_preview_density_and_retry_reuses_mapping_without_duplicate_preview(
    tmp_path: Any, monkeypatch: Any
) -> None:
    texts = ["本节包含多个核心概念。", "第二个核心概念在下一页。"]

    def map_fn(fragments: list[dict[str, Any]], repair: bool) -> dict[str, Any]:
        units = [
            {
                "title": f"核心概念{index + 1}",
                "kind": "concept",
                "fragment_ids": [fragments[index % 2]["id"]],
                "core": True,
            }
            for index in range(6)
        ]
        return {"units": units, "exclusions": []}

    def preview_fn(unit_ids: list[str]) -> list[dict[str, Any]]:
        if len(gateway.preview_calls) == 1:
            return [
                {"question": "第一题覆盖全部核心概念。", "unit_ids": list(unit_ids)}
            ]
        return [
            {"question": "第一题覆盖前三个核心概念。", "unit_ids": list(unit_ids[:3])},
            {"question": "第二题覆盖后三个核心概念。", "unit_ids": list(unit_ids[3:])},
        ]

    app = _app(tmp_path, monkeypatch)
    gateway = ScopeGateway(texts, map_fn=map_fn, preview_fn=preview_fn)
    app.state.chat_service._gateway = gateway
    with TestClient(app) as client:
        _register(client, "scope31density")
        a = _upload_draft(client, upload_id="scope31-density-a").json()
        b = _upload_draft(
            client, upload_id="scope31-density-b", content=PNG_BYTES + b"-density-b"
        ).json()
        first = _first(
            client, [a["object_id"], b["object_id"]], "scope31-density-first"
        )
        conversation_id = first.json()["conversation"]["conversation_id"]
        _tick(app)
        failed = client.get(f"/chat/conversations/{conversation_id}").json()
        assert failed["messages"][-1]["error_code"] == "study_preview_incomplete"
        assert failed["study"]["stage"] == "preview"
        assert failed["study"]["questions"] == []
        _retry(client, app, conversation_id, "scope31-density-retry")
        study = _study(client, conversation_id)
        assert study["stage"] == "tutoring", study
        assert len(study["questions"]) == 2
        covered = {unit_id for item in study["questions"] for unit_id in item["unit_ids"]}
        assert covered == {unit["unit_id"] for unit in study["units"]}
        assert len(gateway.map_calls) == 1
        assert len(gateway.verify_calls) == 1
        assert len(gateway.preview_calls) == 2
        content = client.get(
            f"/chat/conversations/{conversation_id}"
        ).json()["messages"][-1]["content"]
        assert "暂不需要作答" in content


def test_legacy_state_upgrades_with_stable_ids_and_exports(tmp_path: Any) -> None:
    from bridges.study.service import StudyRepository

    service = _service_with_answer(tmp_path, "测试回答")
    conversation = service.create_conversation("alice")
    database = service._repo.database
    legacy = {
        "subsection_id": conversation.conversation_id,
        "stage": "tutoring",
        "units": [{"title": "线性函数", "fragment_ids": ["photo:0"], "core": True}],
        "questions": [{"question": "斜率是什么？", "unit_titles": ["线性函数"]}],
        "review": {
            "questions": [
                {
                    "question_id": "q1",
                    "question": "a 是什么？",
                    "coverage_units": ["线性函数"],
                    "fragment_ids": ["photo:0"],
                }
            ]
        },
    }
    database.scoped("alice").execute(
        "INSERT INTO study_states (account_id, conversation_id, state_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (
            "alice",
            conversation.conversation_id,
            json.dumps(legacy, ensure_ascii=False),
            "2026-09-26T00:00:00+00:00",
        ),
    )
    repository = StudyRepository(database)
    state = repository.get("alice", conversation.conversation_id)
    assert state is not None
    assert state.state_version == 4
    unit_id = state.units[0].unit_id
    assert unit_id.startswith("legacy-")
    assert state.questions[0].unit_ids == [unit_id]
    assert state.review is not None
    assert state.review.questions[0].coverage_units == [unit_id]
    assert state.scope is not None and state.scope.legacy
    assert state.scope.scope_version_id == "legacy-scope-v1"
    assert state.scope.units[0].unit_id == unit_id
    again = repository.get("alice", conversation.conversation_id)
    assert again is not None and again.units[0].unit_id == unit_id
    repository.save("alice", conversation.conversation_id, state)
    exported = export_rows(database, "alice", "study_states")
    assert len(exported) == 1
    restored = json.loads(exported[0]["state_json"])
    assert restored["state_version"] == 4
    assert restored["scope"]["scope_version_id"] == "legacy-scope-v1"
    assert restored["units"][0]["unit_id"] == unit_id
