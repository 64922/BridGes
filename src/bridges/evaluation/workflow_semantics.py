"""工单 42：固定来源下实际生产语义角色与评分函数的独立量表。

来源是公开概念的合成夹具，不是外部可得性证据。此评测不经过 API、
持久化与前端，也不替代旧新树配对。模型响应必须真实调用后另行归档。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import RunModelQuota, build_run_model_quota
from bridges.ai.run_model_config import factory_run_model_config
from bridges.chat.budget import RunBudget
from bridges.contracts.study import StudyReview, StudyReviewQuestion, StudyState
from bridges.paper.assessment import PaperEvidenceRole
from bridges.paper.contracts import PaperTermAnalysis
from bridges.paper.sources import PaperCandidate
from bridges.study.review import ReviewGradeError, grade_question
from bridges.study.scope import (
    ScopeFragment,
)
from bridges.study.summary import build_summary

PAPER_REQUEST = "寻找基于自注意力的序列到序列建模研究"
# 仅用于这组固定夹具的人类标注真值；不是以字面规则替代通用语义判断。
# 方法与任务两项必须均由实际引用片段覆盖，需求标签允许同义简写。
REQUIRED_SPANS = {
    "synonym": ("self-attention", "input sequence", "output sequence"),
    "cross_language": ("自注意力", "输入序列", "输出序列"),
}
PAPER_FIXTURES = (
    (
        "synonym",
        "Attention-only sequence transduction",
        "We replace recurrence with self-attention to map an input sequence to an output sequence.",
        True,
    ),
    (
        "cross_language",
        "仅用注意力的序列转换",
        "本研究使用自注意力进行输入序列到输出序列的转换，不采用循环网络。",
        True,
    ),
    (
        "keyword_false_positive",
        "Transformer terminology in electrical engineering",
        "This study concerns voltage transformers, "
        "not neural self-attention or sequence transduction.",
        False,
    ),
    (
        "sparse_source",
        "Sequence models",
        "The authors discuss models. No method or research problem is described.",
        False,
    ),
)
PAGE_TEXT = "设 f(x)=x²，则 f'(x)=2x。导数由差商极限定义。"
POINT = "x² 的导数为 2x"
GRADE_FIXTURES = (
    ("equivalent_derivation", "lim(h→0)((x+h)²-x²)/h = lim(h→0)(2x+h) = 2x", "correct"),
    ("cross_language_answer", "The derivative is twice x, i.e. 2*x.", "correct"),
    ("wrong_sign", "导数是 -2x。", "incorrect"),
)
# 教学范围固定夹具：同名知识点落在不同书页、页眉非教学内容、负号处不清。
# 真值：同名两点必须各自绑定自身片段；不清负号不得被核验为一致。
SCOPE_FRAGMENTS = (
    ScopeFragment("scope-p1-title", 1, "text", "1.1 标题", "1.1 导数定义", "照片原文"),
    ScopeFragment(
        "scope-p1-def",
        1,
        "formula",
        "1.1 公式",
        "设 f(x)=x²，在点 x 处的导数由差商极限定义：f′(x)=lim(h→0)((x+h)²−x²)/h。",
        "照片原文",
    ),
    ScopeFragment(
        "scope-p2-def",
        2,
        "formula",
        "3.2 公式",
        "3.2 导数定义：对 g(x)=x³，其导数为 g′(x)=3x²。",
        "照片原文",
    ),
    ScopeFragment(
        "scope-p2-minus",
        2,
        "formula",
        "3.3 公式",
        "补充公式：若 h(x)=x⁻¹，则 h′(x)=−x⁻²。【照片中负号处不清晰，需补录核对】",
        "照片原文",
    ),
    ScopeFragment("scope-p2-header", 2, "text", "页眉", "42 / 3.2 幂函数导数", "照片原文"),
)


def fingerprint(value: Any) -> str:
    """锁定合成来源、实际提示与 Schema；不包含运行凭据。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def corpus_snapshot() -> dict[str, Any]:
    return {
        "request": PAPER_REQUEST,
        "papers": PAPER_FIXTURES,
        "page": PAGE_TEXT,
        "point": POINT,
        "answers": GRADE_FIXTURES,
        "scope_fragments": [
            {
                "fragment_id": item.fragment_id,
                "page": item.page_ordinal,
                "kind": item.kind,
                "position": item.position,
                "text": item.text,
                "source": item.source,
            }
            for item in SCOPE_FRAGMENTS
        ],
    }


def paper_rubric(matches: dict[str, Any]) -> dict[str, dict[str, bool]]:
    """相关结论必须带可定位逐字证据；假阳性/稀疏来源必须排除。"""
    checks = {}
    for candidate_id, title, abstract, expected in PAPER_FIXTURES:
        result = matches.get(candidate_id, {})
        evidence = result.get("evidence", [])
        sources = {"title": title, "abstract": abstract}
        located = bool(evidence) and all(
            isinstance(item, dict)
            and isinstance(item.get("requirement"), str)
            and bool(item["requirement"].strip())
            and isinstance(item.get("quote"), str)
            and bool(item["quote"].strip())
            and item["quote"] in sources.get(str(item.get("source")), "")
            for item in evidence
        )
        quotes = " ".join(item["quote"] for item in evidence) if located else ""
        supported = located and all(span in quotes for span in REQUIRED_SPANS.get(candidate_id, ()))
        checks[candidate_id] = {
            "semantic_decision": result.get("relevant") is expected,
            "necessary_requirement_supported": supported if expected else True,
            "read_range_honest": all(
                isinstance(item, dict) and item.get("source") in sources for item in evidence
            ),
        }
    return checks


def study_fixture() -> tuple[StudyState, StudyReviewQuestion]:
    """冻结作答前已核验的题目及书页；量表不由受测模型生成。"""
    state = StudyState.model_validate(
        {
            "subsection_id": "derivatives",
            "stage": "review",
            "state_version": 4,
            "pages": [
                {
                    "object_id": "page-1",
                    "ordinal": 1,
                    "content_hash": fingerprint(PAGE_TEXT),
                    "model_id": "fixture",
                    "fragments": [
                        {
                            "fragment_id": "formula-1",
                            "kind": "formula",
                            "position": "公式 1",
                            "text": PAGE_TEXT,
                            "confidence": 1,
                        }
                    ],
                }
            ],
        }
    )
    question = StudyReviewQuestion.model_validate(
        {
            "question_id": "q-1",
            "question": "求 f(x)=x² 的导数并说明。",
            "coverage_units": ["derivative"],
            "fragment_ids": ["formula-1"],
            "scope_version_id": "fixture-scope-v1",
            "core_points": [POINT],
            "canonical_answer": "f'(x)=2x。",
            "equivalents": ["差商极限得到 2x", "twice x", "2*x"],
            "incomplete_basis": "没有给出导数结论",
            "incorrect_basis": "符号或系数错误",
            "verification": {
                "status": "consistent",
                "question_matches_knowledge": True,
                "rubric_supported": True,
                "answer_consistent": True,
            },
        }
    )
    return state, question


class FixtureContext:
    """仅为合成来源提供完整证据输入；不宣称验证了生产上下文编译。"""

    def compile_turn_context(self, run: Any, *, system_prompt: str, evidence: Any) -> Any:
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": "user", "content": item.content} for item in evidence)
        return messages, {
            "budget_floor_exceeded": False,
            "adopted_evidence_ids": [item.evidence_id for item in evidence],
        }


class SemanticGateway:
    """把既有专业角色原始请求传给调用器，不替换生产提示或模型响应。"""

    def __init__(self, invoke: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._invoke = invoke

    def invoke(
        self, capability: str, version: str, context: Any, payload: dict[str, Any], **kwargs: Any
    ) -> Any:
        return SimpleNamespace(
            status=SimpleNamespace(value="success"), output=self._invoke(payload)
        )


def run_semantic_cases(
    invoke: Callable[[dict[str, Any]], dict[str, Any]], *, quota: RunModelQuota | None = None
) -> dict[str, Any]:
    """实际执行生产筛选与评分函数；失败原样列出，不记作通过。"""
    analysis = PaperTermAnalysis(
        original_phrase=PAPER_REQUEST, normalized_term=PAPER_REQUEST, confidence=1
    )
    candidates = [
        PaperCandidate(
            candidate_id,
            title,
            [],
            datetime(2024, 1, 1, tzinfo=UTC),
            "https://example.org/abs",
            "https://example.org/pdf",
            abstract,
        )
        for candidate_id, title, abstract, _ in PAPER_FIXTURES
    ]
    role = PaperEvidenceRole(
        SemanticGateway(invoke),
        run_context=None,
        model_id=None,
        quota=quota or build_run_model_quota(factory_run_model_config()),
        budget=RunBudget("semantic-fixture"),
        manifest_sink=None,
    )
    matches = dict(role.judge_many(analysis, candidates))
    rows: list[dict[str, Any]] = [
        {"case_id": key, "checks": checks, "output": matches.get(key)}
        for key, checks in paper_rubric(matches).items()
    ]
    run = SimpleNamespace(config={}, user_message_id="fixture-answer")
    for case_id, answer, expected in GRADE_FIXTURES:
        state, question = study_fixture()
        frozen = question.model_dump()
        try:
            outcome = grade_question(
                FixtureContext(),
                run,
                state,
                question,
                answer,
                lambda capability, payload: invoke(payload),
            )
            checks = {
                "judgement": outcome.judgement == expected,
                "frozen_rubric": question.model_dump() == frozen,
                "point_coverage": [item.point for item in outcome.record.point_checks] == [POINT],
                "source_identity": all(
                    item.fragment_ids == ["formula-1"] for item in outcome.record.point_checks
                ),
            }
            rows.append(
                {
                    "case_id": case_id,
                    "checks": checks,
                    "output": {
                        "judgement": outcome.judgement,
                        "record": outcome.record.model_dump(),
                        "explanation": outcome.explanation,
                    },
                }
            )
        except ReviewGradeError as exc:
            rows.append(
                {"case_id": case_id, "checks": {"judgement": False}, "error_code": exc.code}
            )
    # 错误书页身份和关键符号不清均必须在模型调用前被生产门拒绝。
    called: list[dict[str, Any]] = []

    def reject_call(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        called.append(payload)
        return {}

    for case_id in ("wrong_page_identity", "unclear_symbol"):
        state, question = study_fixture()
        if case_id == "wrong_page_identity":
            question.fragment_ids = ["missing-fragment"]
        else:
            state.pages[0].fragments[0].confidence = 0.4
        called.clear()
        try:
            grade_question(
                FixtureContext(),
                run,
                state,
                question,
                "2x",
                reject_call,
            )
            blocked = False
        except ReviewGradeError:
            blocked = True
        rows.append(
            {"case_id": case_id, "checks": {"blocked_before_model": blocked and not called}}
        )
    state, question = study_fixture()
    pending = question.model_copy(update={"asked": True, "answer": "2x"}, deep=True)
    unanswered = question.model_copy(
        update={"question_id": "q-2", "asked": True, "answer": None, "unanswered": True}, deep=True
    )
    state.review = StudyReview(questions=[pending, unanswered])
    frozen_state = state.model_dump()
    try:
        summary = build_summary(
            FixtureContext(), run, state, lambda capability, payload: invoke(payload)
        )
        separate = {
            ref
            for point in summary.points
            if point.kind == "unanswered"
            for ref in point.question_ids
        }
        exaggerated = any(point.kind in {"mastered", "gap"} for point in summary.points)
        rows.append(
            {
                "case_id": "L13.pending_and_unanswered_summary",
                "checks": {
                    "pending_separate": separate == {"q-1", "q-2"},
                    "no_mastery_or_wrong_answer": not exaggerated,
                    "state_unchanged": state.model_dump() == frozen_state,
                },
                "output": summary.model_dump(),
            }
        )
    except ValueError:
        rows.append(
            {
                "case_id": "L13.pending_and_unanswered_summary",
                "checks": {"summary_verified": False},
                "error_code": "summary_evidence_invalid",
            }
        )
    return {
        "kind": "production-role-semantics",
        "corpus": corpus_snapshot(),
        "corpus_sha256": fingerprint(corpus_snapshot()),
        "cases": rows,
        "passed": all(all(row["checks"].values()) for row in rows),
        "limitations": [
            "独立新树角色测试；未验证旧新树完整流程配对、识别图片、范围生成或完整总结量表",
            "合成来源固定；不是外部读取上线证据，不经过生产 API/持久化/界面",
        ],
    }
