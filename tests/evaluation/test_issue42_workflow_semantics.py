"""量表拒绝假证据，并确实调用生产论文筛选角色；不冒充真实模型运行。

工单 12：旧五阶段学习复盘/总结/范围语义随执行退役，量表只保留论文
相关性筛选的真实执行收据。
"""

from typing import Any

from bridges.evaluation.workflow_semantics import (
    PAPER_FIXTURES,
    PAPER_REQUEST,
    corpus_snapshot,
    fingerprint,
    paper_rubric,
    run_semantic_cases,
)


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


def test_production_paper_relevance_role_is_exercised() -> None:
    tasks: list[str] = []

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        tasks.append(str(payload.get("task", "paper.relevance")))
        return {"matches": list(_paper_matches().values())}

    report = run_semantic_cases(invoke)
    assert report["passed"], [
        row for row in report["cases"] if not all(row["checks"].values())
    ]
    assert set(tasks) == {"paper.relevance"}
    assert len(report["cases"]) == len(PAPER_FIXTURES)


def test_source_and_rubric_changes_invalidate_fingerprint() -> None:
    snapshot = corpus_snapshot()
    locked = fingerprint(snapshot)
    snapshot["request"] = "改动后的固定请求"
    assert fingerprint(snapshot) != locked
