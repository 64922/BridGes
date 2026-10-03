"""工单 26 独立验收：模型解读事实门、表达快照和最终载荷预算。"""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.github.presenting import GithubInsightGenerator, safe_insight_choices
from bridges.github.ranking import rank_candidates
from tests.chat.test_improvement15_task_materials import _quota
from tests.github.test_github_module_flow import (
    WHOLE_IDEA,
    WHOLE_QUERY,
    _candidate,
    _create_conversation,
    _FakeReader,
    _FakeSearchPort,
    _install_github_service,
    _register,
    _run_and_read,
    _send,
)
from tests.github.test_github_requirement_matrix import _analysis_with, _readme_evidence, _retry


class _Gateway:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def invoke(self, capability, version, context, payload, **kwargs):
        self.calls.append(payload)
        return SimpleNamespace(
            status=SimpleNamespace(value="success"),
            lock=None,
            output={"insights": [{"full_name": "demo/bookswap", "insight_zh": self.text}]},
        )


def _recommendation():
    return rank_candidates(
        _analysis_with(features=["发布书籍"]),
        [_readme_evidence(readme_text="发布书籍：学生发布想卖的书。")],
    ).recommendations[0]


@pytest.mark.parametrize("text", [
    "内部实现采用微服务架构，已经验证能运行。",
    "相比其他项目性能高十倍，更适合生产部署。",
    "可先阅读 README 中的「无证据的新功能」，再核对是否适用于你的需求。",
])
def test_unverified_claims_and_comparisons_are_closed(text):
    gateway = _Gateway(text)
    outcome = GithubInsightGenerator(gateway).generate(
        {}, [_recommendation()], model_id="qwen-plus", model_quota=_quota(32000)
    )
    assert len(gateway.calls) == 1
    assert outcome.insights == {}
    assert outcome.dropped == 1
    assert "无法由本轮原始证据核验" in outcome.note


def test_simple_bound_reading_advice_passes_without_second_model_call():
    recommendation = _recommendation()
    text = safe_insight_choices(recommendation)[0]
    gateway = _Gateway(text)
    outcome = GithubInsightGenerator(gateway).generate(
        {}, [recommendation], model_id="qwen-plus", model_quota=_quota(32000)
    )
    assert outcome.insights == {recommendation.full_name: text}
    assert len(gateway.calls) == 1


def test_expression_policy_is_in_final_payload_and_budget_manifest():
    policy = GlobalWritingPolicyCompiler().compile("companion", user_text="只给答案，不要安慰")
    gateway = _Gateway(safe_insight_choices(_recommendation())[0])
    outcome = GithubInsightGenerator(gateway).generate(
        {}, [_recommendation()], model_id="qwen-plus", model_quota=_quota(32000),
        writing_policy=policy,
    )
    payload = gateway.calls[0]
    assert policy.system_block in payload["messages"][0]["content"]
    assert payload["global_writing_policy"] == policy.metadata()
    system_entry = next(
        e for e in outcome.manifest.entries if e.material_id == "github.insight.system"
    )
    assert system_entry.source_version == (
        "sha256:" + hashlib.sha256(payload["messages"][0]["content"].encode()).hexdigest()
    )
    assert outcome.manifest.gate.within_budget


def test_policy_cannot_be_added_after_the_final_budget_gate():
    policy = GlobalWritingPolicyCompiler().compile("companion").model_copy(
        update={"system_block": "策略正文" * 3000}
    )
    gateway = _Gateway("")
    outcome = GithubInsightGenerator(gateway).generate(
        {}, [_recommendation()], model_id="qwen-plus", model_quota=_quota(1200),
        writing_policy=policy,
    )
    assert gateway.calls == []
    assert not outcome.manifest.gate.within_budget


def _install(app):
    return _install_github_service(
        app,
        port=_FakeSearchPort(per_query={WHOLE_QUERY: [
            _candidate("demo/bookswap", description="校园二手书交换")
        ]}),
        reader=_FakeReader({"demo/bookswap": _readme_evidence(
            readme_text="学生可以发布想卖的书，搜索想要的书，线下交换。"
        )}),
    )


def test_parent_commits_verified_delivery_and_preserves_expression_snapshot(
    sqlite_app, client, generation_helpers, monkeypatch,
):
    """真实 HTTP 路径：子图返回时消息仍在生成，父图最后才统一提交。"""
    from bridges.chat import graph

    _register(client)
    service = _install(sqlite_app)
    original_run = service.run
    observed = []

    def defer_run(**kwargs):
        outcome = original_run(**kwargs)
        assert kwargs["defer_finalization"] is True
        assert outcome.delivery is not None
        message = kwargs["repo"].get_message(kwargs["account_id"], kwargs["assistant_message_id"])
        assert message.status.value == "streaming"
        observed.append(outcome.delivery)
        return outcome

    monkeypatch.setattr(service, "run", defer_run)
    original_verify = graph._verify_github_delivery

    def verify(deps, delivery):
        message = deps.repo.get_message(deps.run.account_id, deps.run.assistant_message_id)
        assert message.status.value == "streaming"
        return original_verify(deps, delivery)

    monkeypatch.setattr(graph, "_verify_github_delivery", verify)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert assistant["status"] == "done"
    assert observed[0].verification_artifact_id
    database = sqlite_app.state.bridges_database
    first = database.connection.execute(
        "SELECT config_json FROM generation_runs WHERE assistant_message_id = ?",
        (assistant["message_id"],),
    ).fetchone()
    policy = json.loads(first["config_json"])["global_writing_policy"]
    assert policy["snapshot_complete"] is True
    _retry(client, conversation_id, assistant["message_id"])
    generation_helpers["drive"](sqlite_app)
    rows = database.connection.execute(
        "SELECT config_json FROM generation_runs WHERE conversation_id = ? ORDER BY created_at",
        (conversation_id,),
    ).fetchall()
    assert json.loads(rows[-1]["config_json"])["global_writing_policy"] == policy


def test_parent_rejects_delivery_without_persisted_verification(
    sqlite_app, client, generation_helpers, monkeypatch,
):
    """即便子图声称成功，无核验产物引用仍不能提交最终结果。"""
    _register(client)
    service = _install(sqlite_app)
    original_run = service.run

    def drop_verification(**kwargs):
        outcome = original_run(**kwargs)
        delivery = outcome.delivery.model_copy(update={"verification_artifact_id": None})
        return replace(outcome, delivery=delivery)

    monkeypatch.setattr(service, "run", drop_verification)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert assistant["status"] == "error"
    assert assistant["error_code"] == "github_delivery_unverified"
    assert assistant["github_projects"] is None


@pytest.mark.parametrize("field", ["coverage", "scenario", "features"])
def test_parent_rejects_projection_tampering(
    sqlite_app, client, generation_helpers, monkeypatch, field,
):
    """合格核验产物不为篡改后的矩阵或来源需求背书。"""
    from bridges.github.contracts import GithubCoverage
    from bridges.github.presenting import render_result_content

    _register(client)
    service = _install_github_service(
        sqlite_app,
        port=_FakeSearchPort(per_query={WHOLE_QUERY: [
            _candidate("demo/bookswap", description="校园二手书交换")
        ]}),
        reader=_FakeReader({"demo/bookswap": _readme_evidence(
            readme_text="学生可以发布想卖的书，搜索想要的书。"
        )}),
    )
    original_run = service.run

    def tamper(**kwargs):
        outcome = original_run(**kwargs)
        projection = outcome.delivery.projection
        if field == "coverage":
            recommendation = projection.recommendations[0]
            assert recommendation.coverage is GithubCoverage.COMPONENT
            projection = projection.model_copy(update={"recommendations": [
                recommendation.model_copy(update={"coverage": GithubCoverage.WHOLE})
            ]})
        else:
            projection = projection.model_copy(update={
                field: "被替换的主题" if field == "scenario" else ["被删除的需求"]
            })
        delivery = outcome.delivery.model_copy(update={
            "projection": projection, "content": render_result_content(projection)
        })
        return replace(outcome, delivery=delivery)

    monkeypatch.setattr(service, "run", tamper)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert assistant["error_code"] == "github_delivery_unverified"
    assert assistant["github_projects"] is None


def test_old_qualified_artifact_in_same_conversation_cannot_verify_new_task(
    sqlite_app, client, generation_helpers, monkeypatch,
):
    """同会话旧核验必须是本轮实际收据引用且依赖匹配，不能借来背书。"""
    _register(client)
    service = _install(sqlite_app)
    original_run = service.run
    deliveries = []

    def capture(**kwargs):
        outcome = original_run(**kwargs)
        if deliveries:
            outcome = replace(outcome, delivery=outcome.delivery.model_copy(update={
                "verification_artifact_id": deliveries[0].verification_artifact_id
            }))
        deliveries.append(outcome.delivery)
        return outcome

    monkeypatch.setattr(service, "run", capture)
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, WHOLE_IDEA, module_id="github")
    first = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert first["status"] == "done"
    _send(client, conversation_id, WHOLE_IDEA + "，必须支持离线处理", module_id="github")
    second = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)
    assert second["error_code"] == "github_delivery_unverified"
    assert second["github_projects"] is None
