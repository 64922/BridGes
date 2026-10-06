"""量表拒绝假证据，并确实调用生产评分/总结函数；不冒充真实模型运行。"""

import json
from typing import Any

from bridges.evaluation.workflow_semantics import (
    PAPER_FIXTURES,
    PAPER_REQUEST,
    POINT,
    corpus_snapshot,
    fingerprint,
    paper_rubric,
    run_semantic_cases,
)
from bridges.evaluation.workflow_semantics_scope import run_scope_cases
from bridges.study.scope import assign_unit_id


def _paper_matches() -> dict[str, Any]:
    return {
        key: {
            "arxiv_id": key,
            "relevant": expected,
            "evidence": [{"requirement": PAPER_REQUEST, "source": "abstract", "quote": abstract}]
            if expected
            else [],
        }
        for key, title, abstract, expected in PAPER_FIXTURES
    }


def test_semantic_rubric_requires_actual_quote_and_required_coverage() -> None:
    matches = _paper_matches()
    assert all(all(row.values()) for row in paper_rubric(matches).values())
    matches["synonym"]["evidence"][0]["quote"] = "并不存在的来源"
    assert not paper_rubric(matches)["synonym"]["necessary_requirement_supported"]
    matches["cross_language"]["evidence"][0]["quote"] = "自注意力"
    assert not paper_rubric(matches)["cross_language"]["necessary_requirement_supported"]
    matches["keyword_false_positive"]["relevant"] = True
    assert not paper_rubric(matches)["keyword_false_positive"]["semantic_decision"]


def test_synonymous_requirement_label_still_requires_method_and_task_evidence() -> None:
    matches = _paper_matches()
    matches["synonym"]["evidence"][0]["requirement"] = "基于自注意力的序列到序列建模"
    assert paper_rubric(matches)["synonym"]["necessary_requirement_supported"]
    matches["synonym"]["evidence"][0]["quote"] = "an input sequence to an output sequence"
    assert not paper_rubric(matches)["synonym"]["necessary_requirement_supported"]


def test_production_grading_and_summary_are_exercised() -> None:
    tasks = []

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        task = payload.get("task", "paper.relevance")
        tasks.append(task)
        if task == "paper.relevance":
            return {"matches": list(_paper_matches().values())}
        data = json.loads(payload["messages"][1]["content"])
        if task == "study.summarize":
            return {
                "points": [
                    {"kind": "learned", "text": "本节学过导数。", "fragment_ids": ["formula-1"]},
                    {
                        "kind": "unanswered",
                        "text": "未判定题单独列出。",
                        "question_ids": ["q-1", "q-2"],
                    },
                ]
            }
        wrong = data["answer"] == "导数是 -2x。"
        output = {
            "question_id": "q-1",
            "judgement": "incorrect" if wrong else "correct",
            "explanation": "根据本节公式判定。",
            "dispute": False,
            "point_checks": [
                {
                    "point": POINT,
                    "status": "contradicted" if wrong else "hit",
                    "fragment_ids": ["formula-1"],
                }
            ],
        }
        if task == "study.recheck_grade":
            output.pop("dispute")
            output.update(status="confirmed", detail="维持原判定。")
        return output

    report = run_semantic_cases(invoke)
    assert report["passed"], [row for row in report["cases"] if not all(row["checks"].values())]
    assert tasks.count("study.grade") == 3
    assert tasks.count("study.recheck_grade") == 1
    assert tasks.count("study.summarize") == 1
    assert len(report["cases"]) == 10


def test_source_and_rubric_changes_invalidate_fingerprint() -> None:
    snapshot = corpus_snapshot()
    locked = fingerprint(snapshot)
    snapshot["point"] = "改动评分要点"
    assert fingerprint(snapshot) != locked


def _scope_invoke(
    *, minus_status: str = "insufficient", mixed: bool = False
) -> tuple[Any, list[str]]:
    tasks: list[str] = []
    units = (
        [
            {
                "title": "导数定义",
                "kind": "concept",
                "fragment_ids": ["scope-p1-def", "scope-p2-def"],
                "core": True,
            }
        ]
        if mixed
        else [
            {
                "title": "导数定义",
                "kind": "concept",
                "fragment_ids": ["scope-p1-def"],
                "core": True,
            },
            {
                "title": "幂函数导数定义",
                "kind": "concept",
                "fragment_ids": ["scope-p2-def"],
                "core": True,
            },
        ]
    )
    units.append(
        {
            "title": "负指数幂导数",
            "kind": "concept",
            "fragment_ids": ["scope-p2-minus"],
            "core": True,
        }
    )
    exclusions = [
        {"fragment_id": "scope-p1-title", "reason": "小节标题，非独立知识点"},
        {"fragment_id": "scope-p2-header", "reason": "页眉页码，非教学内容"},
    ]

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        task = payload.get("task", "")
        tasks.append(str(task))
        if task == "study.map":
            return {"units": units, "exclusions": exclusions}
        checks = []
        for unit in units:
            status = (
                minus_status if "scope-p2-minus" in unit["fragment_ids"] else "consistent"
            )
            checks.append(
                {
                    "unit_id": assign_unit_id(
                        unit["kind"], unit["title"], unit["fragment_ids"]
                    ),
                    "status": status,
                    "detail": "与原文核对结论",
                }
            )
        return {
            "checks": checks,
            "exclusions": [
                {"fragment_id": item["fragment_id"], "status": "consistent", "detail": "页眉页码"}
                for item in exclusions
            ],
        }

    return invoke, tasks


def test_scope_scale_passes_when_coverage_isolation_and_unclear_symbol_hold() -> None:
    invoke, tasks = _scope_invoke()
    report = run_scope_cases(invoke)
    assert report["passed"], report["cases"]
    assert [case["case_id"] for case in report["cases"]] == [
        "scope.page_coverage",
        "scope.unclear_symbol",
        "scope.same_name_isolation",
    ]
    assert tasks == ["study.map", "study.verify_scope"]
    assert len(report["units"]) == 3


def test_scope_scale_rejects_certified_unclear_symbol_and_same_name_mixing() -> None:
    certified = run_scope_cases(_scope_invoke(minus_status="consistent")[0])
    unclear = next(case for case in certified["cases"] if case["case_id"] == "scope.unclear_symbol")
    assert not certified["passed"]
    assert unclear["checks"] == {
        "unclear_sign_not_certified": False,
        "unclear_sign_blocked": False,
    }
    mixed = run_scope_cases(_scope_invoke(mixed=True)[0])
    isolation = next(
        case for case in mixed["cases"] if case["case_id"] == "scope.same_name_isolation"
    )
    assert not mixed["passed"]
    assert isolation["checks"] == {"same_name_isolated": False}
