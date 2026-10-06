"""工单 39：学习相关正式路径的真实执行收据（真实服务行 + 策略接缝）。

接缝通过真实 `ChatService` 运行行与真实模型调用执行；完整 NodeKernel
节点校验不在评测进程内复现的路径（study.scope）用同一提示合同与覆盖门
直连模型，并在收据中如实标注。
"""

from __future__ import annotations

import json
from typing import Any

from bridges.evaluation.expression_path_receipts import (
    build_receipt,
    serialize_locks,
)
from bridges.evaluation.expression_real_gateway import receipt_run_context
from bridges.evaluation.expression_real_run import build_attachment_service

RECEIPT_ACCOUNT = "eval39-account"

#: 与 `bridges/study/scope.py::StudyScopeNodeFlow._run_preview` 同一合同。
_PREVIEW_SYSTEM_PROMPT = (
    '只输出 JSON {"questions":[{"question":"阅读引导问题",'
    '"unit_ids":["知识点ID"]}]}。'
    "你是教材预习助教。只依据已核验的知识范围生成阅读引导问题，"
    "不要求学生现在作答，不泄露答案。问题引用给定知识点 ID；"
    "每个核心知识点至少被一个问题覆盖；一题可覆盖多个相关知识点。"
    "问题数量必须在给定范围内（随知识密度变化），不固定题数。"
    "用自然中文，不输出知识点 ID 以外的编号或链接。"
)


def _study_state(stage: str, *, with_judged_question: bool = False) -> Any:
    from bridges.contracts.study import (
        StudyContentCheck,
        StudyCoverageEntry,
        StudyFragment,
        StudyPage,
        StudyReview,
        StudyReviewQuestion,
        StudyScope,
        StudyState,
        StudyUnit,
    )

    page = StudyPage(
        object_id="obj-receipt",
        ordinal=1,
        content_hash="hash-receipt",
        model_id="eval39",
        fragments=[
            StudyFragment(
                fragment_id="obj-receipt:1",
                kind="text",
                position="正文",
                text="线性函数 y=ax+b 中，a 是斜率，b 是纵截距。",
                confidence=0.95,
            )
        ],
    )
    unit = StudyUnit(
        unit_id="ku-eval39-linear",
        title="线性函数的斜率与截距",
        kind="concept",
        fragment_ids=["obj-receipt:1"],
        core=True,
    )
    scope = StudyScope(
        scope_version_id="scope-receipt",
        material_hash="material-receipt",
        page_object_ids=["obj-receipt"],
        fragment_ids=["obj-receipt:1"],
        units=[unit],
        coverage=[
            StudyCoverageEntry(
                fragment_id="obj-receipt:1", unit_ids=[unit.unit_id]
            )
        ],
        content_checks=[
            StudyContentCheck(
                unit_id=unit.unit_id,
                status="consistent",
                fragment_ids=["obj-receipt:1"],
            )
        ],
        verified=True,
    )
    questions = []
    if with_judged_question:
        questions = [
            StudyReviewQuestion(
                question_id="q1",
                question="y=ax+b 中 a 表示什么？",
                coverage_units=[unit.unit_id],
                fragment_ids=["obj-receipt:1"],
                asked=True,
                answer="a 是斜率。",
                judgement="correct",
                canonical_answer="a 是斜率。",
                explanation="x 每增加 1，y 增加 a。",
            )
        ]
    return StudyState(
        subsection_id="section-receipt",
        stage=stage,
        pages=[page],
        units=[unit],
        scope=scope,
        review=StudyReview(questions=questions),
    )


def _seed_study_state(service: Any, conversation_id: str) -> None:
    """把会话预置为 tutoring 阶段，使学习模式普通聊天可真实执行。"""

    from bridges.study.service import StudyRepository

    StudyRepository(service._repo.database).save(
        RECEIPT_ACCOUNT, conversation_id, _study_state("tutoring")
    )


def _study_service(gateway: Any) -> tuple[Any, Any]:
    """与真实配对同形的学习会话：真实 ChatService + 真实运行行。

    评测夹具与 `tests/chat/study_state_fixtures.py` 一样，为驱动生产接缝
    使用 `ChatService._repo` / `._gateway`；不改生产接口。
    """

    from bridges.chat.repository import ConversationRepository
    from bridges.chat.service import ChatService
    from bridges.contracts.chat import ChatMode
    from bridges.profiles.adapters import InMemoryProfileRepository
    from bridges.profiles.atomic import (
        AtomicProfileService,
        InMemoryAtomicProfileRepository,
    )
    from bridges.profiles.automatic import (
        AutomaticProfileService,
        InMemoryAutomaticProfileRepository,
    )
    from bridges.profiles.four_dimensions import (
        FourDimensionProfileService,
        InMemoryFourDimensionProfileRepository,
    )
    from bridges.storage.database import BridgesDatabase

    database = BridgesDatabase(":memory:")
    database.initialize()
    conversations = ConversationRepository(database)
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    atomic = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    service = ChatService(
        repository=conversations,
        gateway=gateway,
        four_dimension_profile_service=four,
        atomic_profile_service=atomic,
        automatic_profile_service=automatic,
        attachment_service=build_attachment_service(database, RECEIPT_ACCOUNT),
    )
    conversation = service.create_conversation(RECEIPT_ACCOUNT, mode=ChatMode.STUDY)
    _seed_study_state(service, conversation.conversation_id)
    _, assistant, _ = service.start_generation(
        RECEIPT_ACCOUNT, conversation.conversation_id, "开始本节学习。"
    )
    run = service._repo.get_run_by_message(RECEIPT_ACCOUNT, assistant.message_id)
    if run is None:
        raise RuntimeError("学习收据无法取得运行行。")
    return service, run


def _study_invoke(service: Any, run: Any) -> tuple[Any, Any, list[Any]]:
    from bridges.chat.budget import load_run_budget

    budget = load_run_budget(
        service._repo, run.account_id, run.run_id, mode_hint="study"
    )
    quota = service.run_model_quota(run)
    context = receipt_run_context()
    locks: list[Any] = []

    def invoke(capability: str, payload: dict[str, Any]) -> Any:
        result = service._gateway.invoke(
            capability,
            "1",
            context,
            payload=payload,
            model_override=(run.config or {}).get("run_model_id"),
            budget=budget,
            model_quota=quota,
        )
        if result.lock is not None:
            locks.append(result.lock)
        if not isinstance(result.output, dict):
            # 与生产 `study/service.py` 的运行闭包同口径：失败结果不得作为
            # 载荷继续传入数据模型，否则会把失败误报成结构校验错误。
            raise RuntimeError(
                f"study 模型结果不完整：{(result.error_code or 'unknown')}"
            )
        return result.output

    return invoke, budget, locks


def run_study_tutoring_receipt(gateway: Any) -> dict[str, Any]:
    from bridges.study.tutoring import tutor

    service, run = _study_service(gateway)
    invoke, budget, locks = _study_invoke(service, run)
    result = tutor(
        service,
        run,
        _study_state("tutoring"),
        "y=ax+b 里的 a 是什么？",
        invoke,
        None,
        budget=budget,
    )
    output = json.dumps(
        {"answer": result.answer, "gap": result.gap}, ensure_ascii=False
    )
    return build_receipt(
        "study.tutoring",
        execution_kind="real_study_seam_call",
        output=output,
        model_locks=serialize_locks(locks),
        notes="通过真实 ChatService 运行行调用 tutor 接缝，真实模型回答。",
    )


def run_study_summary_receipt(gateway: Any) -> dict[str, Any]:
    from bridges.study.summary import build_summary, render_summary

    service, run = _study_service(gateway)
    invoke, _, locks = _study_invoke(service, run)
    state = _study_state("review", with_judged_question=True)
    summary = build_summary(service, run, state, invoke)
    return build_receipt(
        "study.summary",
        execution_kind="real_study_seam_call",
        output=render_summary(summary, state),
        model_locks=serialize_locks(locks),
        notes="通过真实 ChatService 运行行调用 build_summary 接缝，真实模型回答。",
    )


def run_study_review_receipt(gateway: Any) -> dict[str, Any]:
    from bridges.study.review import plan_review

    service, run = _study_service(gateway)
    invoke, _, locks = _study_invoke(service, run)
    state = _study_state("review")
    review = plan_review(service, run, state, invoke)
    output = json.dumps(
        [question.question for question in review.questions], ensure_ascii=False
    )
    return build_receipt(
        "study.review",
        execution_kind="real_study_seam_call",
        output=output,
        model_locks=serialize_locks(locks),
        notes="通过真实 ChatService 运行行调用 plan_review 接缝，真实模型出题。",
    )


def run_study_scope_receipt(gateway: Any, quota: Any) -> dict[str, Any]:
    """预习说明接缝：同一提示合同与覆盖门，真实模型调用，收据如实标注。"""

    from bridges.chat.lightweight_policy import ChatLightweightPolicyCompiler
    from bridges.contracts.chat import ChatMode
    from bridges.contracts.study import StudyQuestion
    from bridges.study.scope import preview_bounds, preview_coverage_problems

    policy = ChatLightweightPolicyCompiler(candidate_enabled=True).compile(
        ChatMode.STUDY,
        user_text="生成本节预习问题",
        lesson=True,
    )
    state = _study_state("preview")
    scope = state.scope
    minimum, maximum = preview_bounds(scope.units)
    system_prompt = (
        _PREVIEW_SYSTEM_PROMPT
        + f"问题数量必须在 {minimum} 到 {maximum} 之间。\n"
        + policy.system_block
    )
    data = {
        "scope_version_id": scope.scope_version_id,
        "minimum_questions": minimum,
        "maximum_questions": maximum,
        "units": [
            {
                "unit_id": unit.unit_id,
                "title": unit.title,
                "kind": unit.kind,
                "core": unit.core,
            }
            for unit in scope.units
        ],
    }
    result = gateway.invoke(
        "qwen_structured_output",
        "1",
        receipt_run_context(),
        payload={
            "task": "study.preview",
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(data, ensure_ascii=False),
                },
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
        },
        model_quota=quota,
    )
    raw = result.output or {}
    by_id = {unit.unit_id: unit for unit in scope.units}
    questions: list[Any] = []
    problems: list[str] = []
    try:
        for item in raw.get("questions", []):
            unit_ids = [ref for ref in item.get("unit_ids", []) if ref in by_id]
            if not unit_ids:
                raise ValueError("预习问题没有引用有效知识点。")
            questions.append(
                StudyQuestion(
                    question=str(item.get("question") or "").strip(),
                    unit_ids=list(dict.fromkeys(unit_ids)),
                    unit_titles=[by_id[ref].title for ref in dict.fromkeys(unit_ids)],
                    scope_version_id=scope.scope_version_id,
                )
            )
        problems = preview_coverage_problems(questions, scope, minimum, maximum)
    except (KeyError, ValueError) as exc:
        problems = [f"预习问题结构不合法：{exc}"]
    output = json.dumps(
        {"questions": [item.model_dump(mode="json") for item in questions]},
        ensure_ascii=False,
    )
    lock = result.lock.model_dump(mode="json") if result.lock is not None else None
    receipt = build_receipt(
        "study.scope",
        execution_kind="policy_seam_model_call",
        output=output,
        model_locks=[lock] if lock else [],
        notes=(
            "用同一预习提示合同与覆盖门直连模型；完整 NodeKernel 节点校验"
            "不在评测进程内复现（如实标注）。"
        ),
    )
    receipt["review_problems"] = problems
    if problems or not questions:
        receipt["status"] = "failed"
        receipt["error"] = "；".join(problems or ["未生成预习问题。"])
    return receipt


__all__ = [
    "RECEIPT_ACCOUNT",
    "run_study_review_receipt",
    "run_study_scope_receipt",
    "run_study_summary_receipt",
    "run_study_tutoring_receipt",
]
