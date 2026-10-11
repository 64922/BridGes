"""论文持久节点配方、预算默认值与质量门。"""

from __future__ import annotations

from typing import Any

from bridges.kernel.contracts import (
    NodeExecution,
    NodeInvocation,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecoveryPolicy,
)
from bridges.kernel.registry import RecipeRegistry
from bridges.paper.artifact_codec import _digest

NODE_PARSE = "paper.parse"


NODE_PLAN = "paper.plan"


NODE_SEARCH = "paper.search"


NODE_SCREEN = "paper.screen"


NODE_READ = "paper.read"


NODE_ENRICH = "paper.enrich"


NODE_EVALUATE = "paper.evaluate"


NODE_VERIFY = "paper.verify"


PAPER_RECIPE_ID = "paper-search"


# 新意图与语义证据合同不能复用旧词表路径的持久化产物。
PAPER_RECIPE_VERSION = "paper-search-recipe-v3"


PAPER_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解论文请求",
    NODE_PLAN: "规划论文检索",
    NODE_SEARCH: "检索登记学术来源",
    NODE_SCREEN: "按证据筛选候选",
    NODE_READ: "读取摘要或正文",
    NODE_ENRICH: "核对论文来源",
    NODE_EVALUATE: "整理选择与顺序",
    NODE_VERIFY: "核验身份与断言证据",
}


PAPER_CAPABILITY_VERSIONS: dict[str, str] = {
    "paper.parse_request": "paper-parse-v2",
    "paper.plan_queries": "paper-plan-v2",
    "paper.search_sources": "paper-search-v2",
    "paper.screen_candidates": "paper-screen-v2",
    "paper.read_evidence": "paper-read-v1",
    "paper.enrich_metadata": "paper-enrich-v1",
    "paper.evaluate_selection": "paper-evaluate-v1",
    "paper.verify_selection": "paper-verify-v1",
}


PAPER_GATES: frozenset[str] = frozenset(
    {
        "paper.identity_present",
        "paper.claims_have_evidence",
        "paper.hard_conditions_hold",
        "paper.independent_review",
    }
)


SEARCH_DEADLINE_SECONDS = 25.0


ENRICH_DEADLINE_SECONDS = 12.0


DEFAULT_SCREEN_MAX = 20


DEFAULT_DEEP_READ_MAX = 3


def _parse_key(inputs: Any) -> str:
    return _digest(
        {
            "content": inputs.user_content,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.parse_request"],
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
        }
    )


def _plan_key(inputs: Any) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.plan_queries"],
        }
    )


def _search_key(inputs: Any) -> str:
    # 这里刻意不含 run_id：同一会话、同一内容与同一冻结日期（见
    # prior_digest）的新运行可以复用真实检索产物；跨日/跨年时解析键变化，
    # 过期来源结果不会复用（恢复失效合同）。
    plans = inputs.artifacts[NODE_PLAN].payload.get("plans") or []
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "queries": [item.get("query") for item in plans],
            "capability": PAPER_CAPABILITY_VERSIONS["paper.search_sources"],
        }
    )


def _screen_key(inputs: Any) -> str:
    return _digest(
        {
            "search": inputs.artifacts[NODE_SEARCH].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.screen_candidates"],
        }
    )


def _read_key(inputs: Any) -> str:
    return _digest(
        {
            "screen": inputs.artifacts[NODE_SCREEN].content_hash,
            "goal": inputs.artifacts[NODE_PARSE].payload.get("reading_goal"),
            "capability": PAPER_CAPABILITY_VERSIONS["paper.read_evidence"],
        }
    )


def _enrich_key(inputs: Any) -> str:
    return _digest(
        {
            "search": inputs.artifacts[NODE_SEARCH].content_hash,
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.enrich_metadata"],
        }
    )


def _evaluate_key(inputs: Any) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "screen": inputs.artifacts[NODE_SCREEN].content_hash,
            "read": inputs.artifacts[NODE_READ].content_hash,
            "enrich": inputs.artifacts[NODE_ENRICH].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.evaluate_selection"],
        }
    )


def _verify_key(inputs: Any) -> str:
    return _digest(
        {
            "evaluate": inputs.artifacts[NODE_EVALUATE].content_hash,
            "capability": PAPER_CAPABILITY_VERSIONS["paper.verify_selection"],
        }
    )


def build_paper_recipe() -> RecipeDefinition:
    """构造并校验论文配方（必经顺序与依赖只指向前置节点）。"""
    capabilities = PAPER_CAPABILITY_VERSIONS
    return RecipeDefinition(
        recipe_id=PAPER_RECIPE_ID,
        recipe_version=PAPER_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="paper.parse_request",
                capability_version=capabilities["paper.parse_request"],
                artifact_type="paper.request_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="解析主题/领域、阅读目标、原话锚点、年份等硬条件与缺口。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="paper.plan_queries",
                capability_version=capabilities["paper.plan_queries"],
                artifact_type="paper.query_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="精确查询与受约束扩展；冻结筛选与深读上限。",
            ),
            NodeSpec(
                name=NODE_SEARCH,
                capability="paper.search_sources",
                capability_version=capabilities["paper.search_sources"],
                artifact_type="paper.candidates",
                input_key=_search_key,
                depends_on=(NODE_PLAN, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="仅调用已登记且可用的来源；学术标识去重并记录真实查询。",
            ),
            NodeSpec(
                name=NODE_SCREEN,
                capability="paper.screen_candidates",
                capability_version=capabilities["paper.screen_candidates"],
                artifact_type="paper.screened_candidates",
                input_key=_screen_key,
                depends_on=(NODE_SEARCH, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="硬条件代码过滤 + 需求到标题/摘要证据的语义匹配。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="paper.read_evidence",
                capability_version=capabilities["paper.read_evidence"],
                artifact_type="paper.readings",
                input_key=_read_key,
                depends_on=(NODE_SCREEN, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按结论需要读取摘要/正文片段；未接入或失败时诚实降级。",
            ),
            NodeSpec(
                name=NODE_ENRICH,
                capability="paper.enrich_metadata",
                capability_version=capabilities["paper.enrich_metadata"],
                artifact_type="paper.enrichment",
                input_key=_enrich_key,
                depends_on=(NODE_SEARCH, NODE_PARSE),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="可选补充发表/版本/引用信息；失败只留缺口不阻断。",
            ),
            NodeSpec(
                name=NODE_EVALUATE,
                capability="paper.evaluate_selection",
                capability_version=capabilities["paper.evaluate_selection"],
                artifact_type="paper.selection",
                input_key=_evaluate_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_SCREEN, NODE_READ, NODE_ENRICH),
                required_gates=("paper.identity_present",),
                recovery=RecoveryPolicy.BLOCK,
                description="组织选择与阅读顺序，保存可核实身份；引用量只作辅助。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="paper.verify_selection",
                capability_version=capabilities["paper.verify_selection"],
                artifact_type="paper.verification",
                input_key=_verify_key,
                depends_on=(NODE_EVALUATE,),
                required_gates=(
                    "paper.claims_have_evidence",
                    "paper.hard_conditions_hold",
                    "paper.independent_review",
                ),
                recovery=RecoveryPolicy.BLOCK,
                description="核对身份、断言证据与未读范围；复杂比较/冲突触发独立复核。",
            ),
        ),
    )


def paper_recipe_registry() -> RecipeRegistry:
    """登记论文能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=PAPER_CAPABILITY_VERSIONS.keys(),
        gates=PAPER_GATES,
    )
    registry.register(build_paper_recipe())
    return registry


def identity_present_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要身份缺失阻塞对应结论：每篇选定论文必须有 arXiv ID 或 DOI。"""
    del invocation
    missing = execution.artifact.payload.get("identity_missing") or []
    if missing:
        return QualityGateResult(
            gate="paper.identity_present",
            verdict=QualityVerdict.BLOCKED,
            code="paper_identity_missing",
            message="选定论文缺少可核实身份（arXiv ID/DOI），已阻塞对应结论。",
            detail={"missing": list(missing)},
        )
    return QualityGateResult(gate="paper.identity_present", verdict=QualityVerdict.PASS)


def claims_have_evidence_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """方法/实验/局限断言必须有正文依据；只有摘要不得冒充精读。"""
    del invocation
    unsupported = execution.artifact.payload.get("unsupported_claims") or []
    if unsupported:
        return QualityGateResult(
            gate="paper.claims_have_evidence",
            verdict=QualityVerdict.BLOCKED,
            code="paper_claim_without_body_evidence",
            message="存在没有正文依据的方法/实验断言，已阻塞交付。",
            detail={"claims": list(unsupported)},
        )
    return QualityGateResult(gate="paper.claims_have_evidence", verdict=QualityVerdict.PASS)


def hard_conditions_hold_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """年份等硬条件不得静默放宽。"""
    del invocation
    violations = execution.artifact.payload.get("hard_condition_violations") or []
    if violations:
        return QualityGateResult(
            gate="paper.hard_conditions_hold",
            verdict=QualityVerdict.BLOCKED,
            code="paper_hard_condition_violated",
            message="推荐结果违反年份等明确条件，已阻塞交付。",
            detail={"violations": list(violations)},
        )
    return QualityGateResult(gate="paper.hard_conditions_hold", verdict=QualityVerdict.PASS)


def independent_review_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选独立复核：记录触发与执行情况，不把它伪装成模型自评。"""
    del invocation
    review = execution.artifact.payload.get("independent_review") or {}
    if review.get("executed") and (review.get("result") or {}).get("passed") is not True:
        return QualityGateResult(
            gate="paper.independent_review",
            verdict=QualityVerdict.BLOCKED,
            code="paper_independent_review_failed",
            message="独立复核未通过或裁决结构无效，已阻塞相关结论。",
        )
    return QualityGateResult(
        gate="paper.independent_review",
        verdict=QualityVerdict.PASS,
        detail={
            "triggered": bool(review.get("triggered")),
            "executed": bool(review.get("executed")),
        },
    )


PAPER_GATE_HANDLERS = {
    "paper.identity_present": identity_present_gate,
    "paper.claims_have_evidence": claims_have_evidence_gate,
    "paper.hard_conditions_hold": hard_conditions_hold_gate,
    "paper.independent_review": independent_review_gate,
}
