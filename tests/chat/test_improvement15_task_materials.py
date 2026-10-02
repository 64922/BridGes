"""改进工单 15 测试：按解析任务选择检索材料与模块上下文。

覆盖本票验收标准：

1. 「第二个有什么区别」「继续解释」用解析对象/有效条件发起正确的本地
   查询，末尾条件与最新纠正保留；
2. 普通聊天中的任务主题可被模块续接，被取代/撤销的旧条件不污染新任务；
3. 公开查询只含最小公开词，不含任务条件、私人历史或画像正文；
4. 模块自己的模型调用在工具结果加入后重新检查预算，并记录自身采用清单；
5. 材料采用不改变授权：用户禁用来源时不调用；账户/会话作用域保持不变。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bridges.ai.model_quota import RunModelQuota
from bridges.chat.context_compiler import compile_turn_context
from bridges.chat.repository import MessageRecord
from bridges.chat.task_materials import (
    MODULE_DECLARATIONS,
    MaterialDomain,
    build_module_context,
    public_query_from_context,
    select_task_materials,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus, ChatMode
from bridges.contracts.references import (
    AnchorKind,
    ReferenceAnchor,
    ReferenceResolution,
    ReferenceTaskBrief,
    ReferenceTaskContext,
    TaskBriefCondition,
)
from bridges.contracts.retrieval import (
    RetrievalDecisionAction,
    RetrievalDecisionReason,
)
from bridges.contracts.tasks import (
    ConditionOrigin,
    ConditionScope,
    ConditionStatus,
    TaskCondition,
)
from bridges.github.parsing import parse_github_request
from bridges.paper.parsing import parse_paper_request

_BASE = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
_CONV = "conv-15"
_ACC = "acc-15"


# ---------------------------------------------------------------------------
# 测试材料构造
# ---------------------------------------------------------------------------


def _record(
    message_id: str,
    role: ChatMessageRole,
    content: str,
    *,
    seconds: int = 0,
    **extra: Any,
) -> MessageRecord:
    created = _BASE + timedelta(seconds=seconds)
    return MessageRecord(
        message_id=message_id,
        conversation_id=_CONV,
        account_id=_ACC,
        role=role,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content=content,
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=created,
        updated_at=created,
        **extra,
    )


def _user(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.USER, content, **extra)


def _assistant(message_id: str, content: str, **extra: Any) -> MessageRecord:
    return _record(message_id, ChatMessageRole.ASSISTANT, content, **extra)


def _paper_projection(*titles: str) -> dict[str, Any]:
    return {
        "papers": [
            {"order": index, "title": title, "arxiv_id": f"2401.{index:05d}"}
            for index, title in enumerate(titles, start=1)
        ]
    }


def _condition(
    condition_id: str,
    kind: str,
    text: str,
    status: ConditionStatus,
    *,
    source_message_id: str = "m1",
) -> TaskCondition:
    return TaskCondition(
        condition_id=condition_id,
        task_id="task-15",
        version=1,
        kind=kind,
        text=text,
        scope=ConditionScope.TASK,
        origin=ConditionOrigin.USER_STATED,
        status=status,
        source_message_id=source_message_id,
        created_at=_BASE,
        updated_at=_BASE,
    )


def _task(*conditions: TaskCondition, goal: str = "研究注意力机制") -> ReferenceTaskContext:
    return ReferenceTaskContext(
        task_id="task-15",
        goal=goal,
        version=2,
        status="active",
        conditions=list(conditions),
        source_message_ids=["m1"],
    )


def _resolution_with_item(label: str, *, object_id: str = "paper-2") -> ReferenceResolution:
    return ReferenceResolution(
        task=ReferenceTaskBrief(
            task_id="task-15",
            goal="研究注意力机制",
            version=2,
            status="active",
            conditions=[
                TaskBriefCondition(
                    condition_id="c-budget",
                    kind="budget",
                    text="预算不超过 3000 元",
                    status="effective",
                    effective=True,
                    source_message_id="m1",
                ),
                TaskBriefCondition(
                    condition_id="c-old",
                    kind="budget",
                    text="预算不超过 5000 元",
                    status="superseded",
                    effective=False,
                    source_message_id="m0",
                    supersedes_condition_id=None,
                ),
            ],
        ),
        anchors=[
            ReferenceAnchor(
                anchor_id="a-item-2",
                kind=AnchorKind.LIST_ITEM,
                label=label,
                message_ids=["m2"],
                object_id=object_id,
                list_version=1,
            )
        ],
        adopted_message_ids=["m1", "m2"],
        adopted_object_ids=[object_id],
    )


def _quota(window: int, *, max_input: int | None = None) -> RunModelQuota:
    from bridges.ai.model_quota import QuotaVerificationBasis

    return RunModelQuota(
        model_id="qwen-plus",
        context_window=window,
        max_input_tokens=max_input if max_input is not None else window,
        verification_basis=QuotaVerificationBasis.SETTINGS_ACTIVATION,
    )


# ---------------------------------------------------------------------------
# 1. 选择入口：解析对象 + 有效条件 + 各域最小查询
# ---------------------------------------------------------------------------


def test_anaphoric_request_uses_resolved_object_and_effective_conditions() -> None:
    selection = select_task_materials(
        "第二个有什么区别",
        resolution=_resolution_with_item("注意力机制论文"),
        current_message_id="m3",
    )
    kb = selection.query_for(MaterialDomain.KNOWLEDGE_BASE)
    attachment = selection.query_for(MaterialDomain.CONVERSATION_ATTACHMENT)
    assert "注意力机制论文" in kb
    assert "预算不超过 3000 元" in kb
    assert "5000" not in kb  # 已被取代的旧值不进入查询
    assert attachment == kb
    assert selection.request_has_own_topic is False
    assert selection.adopted_object_ids == ("paper-2",)
    assert selection.excluded_condition_ids == ("c-old",)


def test_public_query_excludes_conditions_history_and_profile() -> None:
    selection = select_task_materials(
        "第二个有什么区别",
        resolution=_resolution_with_item("注意力机制论文"),
        current_message_id="m3",
    )
    public = selection.query_for(MaterialDomain.PUBLIC_SEARCH)
    assert "注意力机制论文" in public
    assert "预算" not in public
    assert "3000" not in public
    assert "5000" not in public


def test_continuation_without_task_uses_recent_disambiguation_text() -> None:
    selection = select_task_materials(
        "继续解释",
        recent_user_texts=["我们刚才在讲梯度下降", "更早的无关话题"],
    )
    assert selection.used_continuation_fallback is True
    assert len(selection.topic_terms) == 1
    assert "梯度下降" in selection.topic_terms[0]
    assert "梯度下降" in selection.query_for(MaterialDomain.KNOWLEDGE_BASE)


def test_superseded_revoked_and_draft_conditions_are_excluded() -> None:
    task = _task(
        _condition("c-keep", "topic", "注意力机制", ConditionStatus.EFFECTIVE),
        _condition("c-sup", "budget", "预算 5000 元", ConditionStatus.SUPERSEDED),
        _condition("c-rev", "exclusion", "排除旧模型", ConditionStatus.REVOKED),
        _condition("c-draft", "budget", "助手提议的 4000 元", ConditionStatus.DRAFT),
        _condition("c-clue", "topic", "模型猜测的题目", ConditionStatus.CLUE),
    )
    selection = select_task_materials("继续解释", task=task)
    query = selection.query_for(MaterialDomain.KNOWLEDGE_BASE)
    assert "注意力机制" in query
    assert "5000" not in query
    assert "排除旧模型" not in query
    assert "4000" not in query
    assert "模型猜测的题目" not in query
    assert set(selection.excluded_condition_ids) == {"c-sup", "c-rev", "c-draft", "c-clue"}


def test_selection_audit_record_contains_no_query_or_condition_text() -> None:
    selection = select_task_materials(
        "第二个有什么区别",
        resolution=_resolution_with_item("注意力机制论文"),
        current_message_id="m3",
    )
    record = selection.audit_record()
    serialized = json.dumps(record, ensure_ascii=False)
    assert "注意力机制论文" not in serialized
    assert "预算不超过 3000 元" not in serialized
    assert "第二个" not in serialized
    assert record["query_fingerprint"]
    execution = selection.execution_record()
    assert "注意力机制论文" in execution["queries"]["knowledge_base"]


def test_public_query_from_context_prefers_selection_query() -> None:
    context_budget = {"task_queries": {MaterialDomain.PUBLIC_SEARCH.value: "注意力机制"}}
    assert public_query_from_context(context_budget, "第二个有什么区别") == "注意力机制"
    assert public_query_from_context({}, "原始请求") == "原始请求"


# ---------------------------------------------------------------------------
# 2. 模块上下文：任务来源优先、排除旧条件、无任务回退
# ---------------------------------------------------------------------------


def test_module_context_uses_task_sources_and_skips_unrelated_window() -> None:
    task = _task(
        _condition("c-topic", "topic", "注意力机制", ConditionStatus.EFFECTIVE),
        _condition("c-sup", "topic", "无关的旧话题", ConditionStatus.SUPERSEDED),
    )
    context = build_module_context(
        declaration=MODULE_DECLARATIONS["paper_search"],
        task=task,
        messages=[
            ("m1", "user", "我在研究注意力机制，想找几篇论文"),
            ("m2", "user", "顺便问下杭州周末怎么玩"),
            ("m3", "assistant", "好的。"),
            ("m4", "user", "帮我找几篇论文"),
        ],
        current_user_message_id="m4",
    )
    assert context.topic_hint == "注意力机制"
    assert context.prior_messages == ("我在研究注意力机制，想找几篇论文",)
    assert "顺便问下杭州周末怎么玩" not in context.prior_messages
    assert context.excluded_condition_ids == ("c-sup",)
    assert context.used_task_scope is True
    audit = json.dumps(context.audit_record(), ensure_ascii=False)
    assert "注意力机制" not in audit
    assert "杭州" not in audit


def test_module_context_without_task_falls_back_to_recent_window() -> None:
    context = build_module_context(
        declaration=MODULE_DECLARATIONS["paper_search"],
        task=None,
        messages=[
            ("m1", "user", "第一条"),
            ("m2", "user", "第二条"),
            ("m3", "user", "第三条"),
        ],
        current_user_message_id="m3",
    )
    assert context.prior_messages == ("第一条", "第二条")
    assert context.used_task_scope is False
    assert context.topic_hint == ""


def test_paper_parse_uses_task_topic_hint_when_request_has_no_topic() -> None:
    parsed = parse_paper_request("帮我找几篇论文", task_topic_hint="注意力机制")
    assert parsed.clarification is None
    assert "注意力机制" in parsed.original_phrase or "注意力机制" in parsed.normalized_term


def test_github_parse_takes_task_anchor_phrase_verbatim() -> None:
    from bridges.github.contracts import GithubContextSource

    analysis = parse_github_request(
        "找它的实现项目",
        prior_context=[
            GithubContextSource(
                kind="task",
                label="当前任务主题",
                phrase="注意力机制",
                message_id="m1",
            )
        ],
    )
    assert analysis.context_source is not None
    assert analysis.scenario == "注意力机制"
    assert analysis.features == ["注意力机制"]


# ---------------------------------------------------------------------------
# 3. 编译器接缝：审计只留 ID/指纹，执行记录带各域查询
# ---------------------------------------------------------------------------


def test_compile_turn_context_selects_task_queries() -> None:
    task = _task(
        _condition("c-topic", "topic", "注意力机制", ConditionStatus.EFFECTIVE),
        _condition("c-sup", "topic", "无关旧话题", ConditionStatus.SUPERSEDED),
    )
    records = [
        _user("m1", "我在研究注意力机制"),
        _assistant("m2", "以下是论文", paper_search=_paper_projection("论文甲", "论文乙")),
        _user("m3", "第二个有什么区别"),
    ]
    compiled = compile_turn_context(
        messages=records,
        current_user_message_id="m3",
        model_id="qwen-plus",
        mode=ChatMode.COMPANION,
        context_window=8192,
        task=task,
    )
    audit = compiled.to_record()
    selection = audit["material_selection"]
    assert selection is not None
    selection_json = json.dumps(selection, ensure_ascii=False)
    assert "论文乙" not in selection_json
    assert "注意力机制" not in selection_json
    assert "无关旧话题" not in selection_json
    execution = compiled.execution_record()
    queries = execution["task_queries"]
    local = queries[MaterialDomain.KNOWLEDGE_BASE.value]
    public = queries[MaterialDomain.PUBLIC_SEARCH.value]
    assert "论文乙" in local
    assert "注意力机制" in local
    assert "无关旧话题" not in local
    assert "论文乙" in public
    assert "注意力机制" not in public


# ---------------------------------------------------------------------------
# 4. 检索接缝：知识库/附件各用自己的最小查询；授权与账户作用域不变
# ---------------------------------------------------------------------------


def test_run_round_uses_per_domain_task_queries(tmp_path: Path, monkeypatch: Any) -> None:
    from bridges.retrieval import service as retrieval_service
    from tests.retrieval.conftest import (
        add_material,
        add_user_message,
        make_retrieval_env,
        make_storage,
        seed_conversation,
    )

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    user_message_id = add_user_message(env, account, conversation_id, "第二个有什么区别")
    add_material(
        env,
        account,
        "手写笔记.txt",
        "手写笔记记录：卷积核尺寸对特征图的影响。",
        layer="attachment",
        conversation_id=conversation_id,
        user_message_id=user_message_id,
    )
    kb_object = add_material(
        env,
        account,
        "教材第二章.txt",
        "第二章讲解梯度下降与学习率。",
        layer="knowledge_base",
    )
    captures: list[tuple[tuple[str, ...], str]] = []
    original = retrieval_service.search_keyword

    def _capture(connection: Any, **kwargs: Any) -> Any:
        captures.append((tuple(kwargs["document_ids"]), kwargs["query"]))
        return original(connection, **kwargs)

    monkeypatch.setattr(retrieval_service, "search_keyword", _capture)
    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        f"assistant-{datetime.now(UTC).timestamp()}",
        user_message_id,
        "第二个有什么区别",
        use_knowledge_base=True,
        knowledge_base_query="梯度下降",
        attachment_query="卷积核",
    )
    assert round_ is not None
    by_query = {query for _, query in captures}
    assert "梯度下降" in by_query  # 知识库层用自己的最小查询
    assert "卷积核" in by_query  # 附件层用自己的最小查询
    assert "第二个有什么区别" not in by_query  # 空指代不作为查询词
    assert kb_object is not None


def test_disabled_knowledge_base_depends_on_user_switch_not_materials(
    tmp_path: Path,
) -> None:
    from tests.retrieval.conftest import (
        add_material,
        add_user_message,
        make_retrieval_env,
        make_storage,
        seed_conversation,
    )

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    conversation_id = seed_conversation(env, account)
    user_message_id = add_user_message(env, account, conversation_id, "第二个有什么区别")
    add_material(
        env,
        account,
        "手写笔记.txt",
        "手写笔记记录：卷积核尺寸对特征图的影响。",
        layer="attachment",
        conversation_id=conversation_id,
        user_message_id=user_message_id,
    )
    add_material(
        env,
        account,
        "教材第二章.txt",
        "第二章讲解梯度下降与学习率。",
        layer="knowledge_base",
    )
    assistant_message_id = "assistant-disabled"
    env["retrieval"].ensure_decision(
        account,
        conversation_id,
        assistant_message_id,
        user_message_id,
        "第二个有什么区别",
        mode="companion",
        capability_route="companion",
        use_knowledge_base=False,
    )
    decision = env["retrieval"].decision_projection(account, assistant_message_id)
    assert decision is not None
    assert decision.action is RetrievalDecisionAction.SKIP
    assert decision.reason is RetrievalDecisionReason.USER_DISABLED
    round_ = env["retrieval"].run_round(
        account,
        conversation_id,
        assistant_message_id,
        user_message_id,
        "第二个有什么区别",
        use_knowledge_base=True,
        decision=decision,
        knowledge_base_query="梯度下降",
        attachment_query="卷积核",
    )
    assert round_ is not None
    # 附件仍按当前会话检索；知识库层保持被用户关闭。
    assert {citation.source_layer for citation in round_.citations} == {"attachment"}


def test_task_queries_do_not_cross_account_scope(tmp_path: Path) -> None:
    from tests.retrieval.conftest import (
        add_material,
        make_retrieval_env,
        make_storage,
        seed_conversation,
    )

    env = make_retrieval_env(make_storage(tmp_path))
    account_a = env["account_a"]
    account_b = env["account_b"]
    conversation_id = seed_conversation(env, account_a)
    add_material(
        env,
        account_b,
        "B的知识库.txt",
        "梯度下降在 B 账户的知识库里。",
        layer="knowledge_base",
    )
    round_ = env["retrieval"].run_round(
        account_a,
        conversation_id,
        "assistant-a",
        None,
        "继续解释",
        use_knowledge_base=True,
        knowledge_base_query="梯度下降",
        attachment_query="梯度下降",
    )
    assert round_ is None or not round_.citations


# ---------------------------------------------------------------------------
# 5. 模块模型调用：工具结果加入后重新过门，并记录自身采用清单
# ---------------------------------------------------------------------------


class _CapturingGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(
        self, capability: str, version: str, context: Any, payload: dict[str, Any], **kwargs: Any
    ) -> Any:
        from bridges.contracts.ai import ModelCallResult, ModelCallStatus

        self.calls.append({"payload": payload, **kwargs})
        return ModelCallResult(
            status=ModelCallStatus.SUCCESS,
            output={"summaries": [], "insights": []},
        )


def _paper_recommendation() -> Any:
    from bridges.paper.contracts import PaperRecommendation

    return PaperRecommendation(
        order=1,
        arxiv_id="1706.03762",
        title="Attention Is All You Need",
        source="arxiv",
        abs_url="https://arxiv.org/abs/1706.03762",
        role="foundation",
        reason_zh="主题相关",
        match_basis="标题与摘要命中主题词",
    )


def test_paper_summary_blocks_when_final_payload_exceeds_quota() -> None:
    from bridges.paper.presenting import PaperSummaryGenerator

    gateway = _CapturingGateway()
    generator = PaperSummaryGenerator(gateway)
    outcome = generator.generate(
        {"run_id": "run-15"},
        [_paper_recommendation()],
        abstracts={"1706.03762": "自注意力机制。"},
        model_id="qwen-plus",
        model_quota=_quota(256),  # 远比系统提示 + 候选证据小
    )
    assert gateway.calls == []
    assert outcome.manifest is not None
    assert outcome.manifest.gate.within_budget is False
    assert outcome.manifest.entries
    assert "预算" in (outcome.note or "")


def test_paper_summary_records_manifest_when_within_budget() -> None:
    from bridges.paper.presenting import PaperSummaryGenerator

    gateway = _CapturingGateway()
    generator = PaperSummaryGenerator(gateway)
    outcome = generator.generate(
        {"run_id": "run-15"},
        [_paper_recommendation()],
        abstracts={"1706.03762": "自注意力机制。"},
        model_id="qwen-plus",
        model_quota=_quota(32000),
    )
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["model_quota"] is not None
    assert outcome.manifest is not None
    assert outcome.manifest.gate.within_budget is True
    assert "paper.summary.candidates" in outcome.manifest.adopted_ids


def test_github_insight_records_manifest_after_tool_evidence() -> None:
    from bridges.github.contracts import (
        GithubCoverage,
        GithubEvidenceKind,
        GithubLicenseCheck,
        GithubMaintenanceEvidence,
        GithubReadmeStatus,
        GithubRecommendation,
    )
    from bridges.github.presenting import GithubInsightGenerator

    gateway = _CapturingGateway()
    generator = GithubInsightGenerator(gateway)
    recommendation = GithubRecommendation(
        rank=1,
        full_name="owner/repo",
        html_url="https://github.com/owner/repo",
        coverage=GithubCoverage.WHOLE,
        coverage_note="整体覆盖",
        evidence_kinds=[GithubEvidenceKind.METADATA],
        readme_status=GithubReadmeStatus.NOT_FOUND,
        maintenance=GithubMaintenanceEvidence(note="只取得 API 元数据"),
        license=GithubLicenseCheck(detected=False, note="上游未标注许可"),
        reason_zh="功能匹配",
        borrow_note="可参考目录组织方式",
        retrieved_at=_BASE,
    )
    outcome = generator.generate(
        {"run_id": "run-15"},
        [recommendation],
        model_id="qwen-plus",
        model_quota=_quota(32000),
    )
    assert len(gateway.calls) == 1
    assert outcome.manifest is not None
    assert outcome.manifest.gate.within_budget is True
    assert "github.insight.evidence" in outcome.manifest.adopted_ids
