"""工单 32：保存前证据、公式与条件门及有效部分降级。"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.study import StudyEvidenceAssessment, StudySource, StudyState
from bridges.study.evidence import EvidenceBundle
from tests.chat.test_improvement32_study_tutoring_evidence import (
    EvidenceGateway,
    _ask,
    _start,
)
from tests.chat.test_v2_05_photo_attachments import _app


def _run(monkeypatch: Any, checks: Any, *, adopted: bool = True) -> Any:
    from bridges.study.tutoring import tutor

    source = StudySource(source_id="p", kind="page", label="教材",
                         snippet="若 a>0，y=ax+b 单调递增。")
    assessment = StudyEvidenceAssessment(
        protocol_version="study-tutor-evidence-v1", sufficient=True,
        key_points=["单调性"], supported_points=["单调性"],
    )
    monkeypatch.setattr("bridges.study.tutoring.page_sources", lambda *a: [source])
    monkeypatch.setattr("bridges.study.tutoring.gather_evidence", lambda *a, **k:
                        EvidenceBundle(assessment, [], []))
    monkeypatch.setattr("bridges.study.tutoring._tutoring_policy", lambda *a:
                        (SimpleNamespace(system_block=""), None))
    calls: list[str] = []

    def invoke(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload["task"])
        if payload["task"] == "study.tutor":
            return {"parts": [
                {"kind": "page", "text": "若 a>0，y=ax+b 单调递增。",
                 "source_ids": ["p"]},
                {"kind": "model", "text": "F=mv 在所有条件下成立。",
                 "source_ids": []},
            ], "gap": ""}
        assert payload["task"] == "study.verify_tutoring"
        assert "F=mv" in payload["messages"][-1]["content"]
        if isinstance(checks, Exception):
            raise checks
        return {"checks": checks}

    def compile_context(*args: Any, **kwargs: Any) -> Any:
        ids = [item.evidence_id for item in kwargs["evidence"]] if adopted or not calls else []
        return ([{"role": "system", "content": kwargs["system_prompt"]},
                 {"role": "user", "content": "\n".join(
                     item.content for item in kwargs["evidence"])}],
                {"adopted_evidence_ids": ids, "budget_floor_exceeded": False})

    service = SimpleNamespace(compile_turn_context=compile_context)
    run = SimpleNamespace(config={}, user_message_id="u", assistant_message_id="a")
    return tutor(service, run, StudyState(subsection_id="s"), "解释单调性", invoke, None)


def _check(index: int, **changes: Any) -> dict[str, Any]:
    return {"index": index, "supported": True, "formulas_valid": True,
            "conditions_preserved": True, "gaps_respected": True, **changes}


@pytest.mark.parametrize("field", [
    "supported", "formulas_valid", "conditions_preserved", "gaps_respected",
])
def test_rejected_segment_not_saved_but_supported_part_delivered(
    monkeypatch: Any, field: str,
) -> None:
    """任一门失败移除该段，有效书页部分仍可交付。"""
    result = _run(monkeypatch, [_check(0), _check(1, **{field: False}, detail="条件未核实")])
    assert "若 a>0" in result.answer
    assert "F=mv" not in result.answer
    assert "条件未核实" in result.gap
    assert [source.source_id for source in result.sources] == ["p"]


@pytest.mark.parametrize("checks", [
    [], [_check(0), _check(0)], [_check(0)],
    [_check(0, supported=False), _check(1, formulas_valid=False)],
    ValueError("核验服务不可用"),
])
def test_incomplete_failed_or_all_rejected_checks_block_save(
    monkeypatch: Any, checks: Any,
) -> None:
    """漏段、重复段、核验失败或全部拒绝时不形成可保存产物。"""
    with pytest.raises(ValueError):
        _run(monkeypatch, checks)


def test_verification_cannot_accept_citation_omitted_by_budget(monkeypatch: Any) -> None:
    """核验上下文必须实际采用正文引用，不以首轮采用集合放行。"""
    with pytest.raises(ValueError, match="缺少实际引用依据"):
        _run(monkeypatch, [_check(0), _check(1)], adopted=False)


def test_rejected_formula_does_not_commit_tutoring_exchange(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    """真实会话路径中核验拒绝后，无正文或辅导产物提交。"""
    class RejectingGateway(EvidenceGateway):
        def invoke(self, capability: str, version: str, context: Any,
                   payload: dict[str, Any], **kwargs: Any) -> ModelCallResult:
            if payload.get("task") == "study.verify_tutoring":
                return ModelCallResult(status=ModelCallStatus.SUCCESS, output={
                    "checks": [_check(index, conditions_preserved=False)
                               for index, _ in enumerate(payload["parts"])],
                })
            return super().invoke(capability, version, context, payload, **kwargs)

    app = _app(tmp_path, monkeypatch)
    app.state.chat_service._gateway = RejectingGateway()
    with TestClient(app) as client:
        endpoint = _start(client, app, "tutoringreject")
        result = _ask(client, app, endpoint, "解释斜率")
        assert result["messages"][-1]["status"] == "error"
        assert result["study"]["tutoring"] == []
