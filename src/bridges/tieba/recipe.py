"""贴吧节点配方、能力版本与稳定输入键。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from bridges.kernel.contracts import (
    NodeSpec,
    RecipeDefinition,
    RecoveryPolicy,
)
from bridges.kernel.registry import RecipeRegistry

NODE_PARSE = "tieba.parse"

NODE_PLAN = "tieba.plan"

NODE_SEARCH = "tieba.search"

NODE_READ = "tieba.read"

NODE_VERIFY_OFFICIAL = "tieba.verify_official"

NODE_SYNTHESIZE = "tieba.synthesize"

NODE_VERIFY = "tieba.verify"

TIEBA_RECIPE_ID = "tieba-research"

TIEBA_RECIPE_VERSION = "tieba-recipe-v9"

#: 各节点能力版本（产物兼容性与收据键的一部分）。
TIEBA_CAPABILITY_VERSIONS: dict[str, str] = {
    "tieba.parse_request": "tieba-parse-v6",
    "tieba.plan_evidence": "tieba-plan-v6",
    "tieba.verify_official_pages": "tieba-official-v5",
    "tieba.search_threads": "tieba-search-v10",
    "tieba.read_threads": "tieba-read-v10",
    "tieba.synthesize_evidence": "tieba-synthesize-v9",
    "tieba.verify_evidence": "tieba-verify-v5",
}

TIEBA_GATES: tuple[str, ...] = (
    "tieba.attribution_evidence",
    "tieba.read_scope_fidelity",
    "tieba.official_scope",
    "tieba.conflict_disclosure",
)

#: 只查贴吧时官方路径的阻断原因（来源限制生效）。
TIEBA_ONLY_OFFICIAL_BLOCKED = (
    "你明确只查贴吧：本轮遵守来源限制，未访问学校官方页面，规定相关内容保持未核实。"
)

#: 官方证据缺失或不适用的固定说明（规定类必须出现）。
OFFICIAL_UNVERIFIED_NOTE = "本轮没有取得可确认适用的官方页面，规定相关内容保持未核实。"

#: 自己生成文本里禁止出现的共识式表述（引文保真，不替吧友下结论）。
CONSENSUS_PHRASES: tuple[str, ...] = (
    "普遍认为",
    "普遍反映",
    "大家都说",
    "一致认为",
    "共识是",
)


def content_digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _dependency_identity(inputs: Any, *nodes: str) -> dict[str, tuple[str, str]]:
    return {
        node: (inputs.artifacts[node].artifact_id, inputs.artifacts[node].content_hash)
        for node in nodes
    }


def _parse_key(inputs: Any) -> str:
    return content_digest(
        {
            "content": inputs.user_content,
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
        }
    )


def _plan_key(inputs: Any) -> str:
    return content_digest(_dependency_identity(inputs, NODE_PARSE))


def _official_key(inputs: Any) -> str:
    return content_digest(_dependency_identity(inputs, NODE_PARSE, NODE_PLAN))


def _search_key(inputs: Any) -> str:
    return content_digest(_dependency_identity(inputs, NODE_PARSE, NODE_PLAN))


def _read_key(inputs: Any) -> str:
    return content_digest(_dependency_identity(inputs, NODE_PARSE, NODE_PLAN, NODE_SEARCH))


def _synthesize_key(inputs: Any) -> str:
    return content_digest(
        _dependency_identity(
            inputs,
            NODE_PARSE,
            NODE_PLAN,
            NODE_VERIFY_OFFICIAL,
            NODE_SEARCH,
            NODE_READ,
        )
    )


def _verify_key(inputs: Any) -> str:
    return content_digest(
        _dependency_identity(
            inputs,
            NODE_PARSE,
            NODE_PLAN,
            NODE_VERIFY_OFFICIAL,
            NODE_READ,
            NODE_SYNTHESIZE,
        )
    )


def build_tieba_recipe() -> RecipeDefinition:
    """构造并校验贴吧取证配方（先官方后贴吧，读取后综合与核验）。"""
    return RecipeDefinition(
        recipe_id=TIEBA_RECIPE_ID,
        recipe_version=TIEBA_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="tieba.parse_request",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.parse_request"],
                artifact_type="tieba.question_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="识别对象、日期/时效与规定／体验／混合类型。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="tieba.plan_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.plan_evidence"],
                artifact_type="tieba.evidence_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="登记来源顺序与查询范围（规定类官方优先，体验类只查贴吧）。",
            ),
            NodeSpec(
                name=NODE_VERIFY_OFFICIAL,
                capability="tieba.verify_official_pages",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.verify_official_pages"],
                artifact_type="tieba.official_checks",
                input_key=_official_key,
                depends_on=(NODE_PARSE, NODE_PLAN),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="核对学校官方主体/校区/用途/日期；只查贴吧时跳过。",
            ),
            NodeSpec(
                name=NODE_SEARCH,
                capability="tieba.search_threads",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.search_threads"],
                artifact_type="tieba.search_results",
                input_key=_search_key,
                depends_on=(NODE_PARSE, NODE_PLAN),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="按计划发出有界检索，记录真实查询词与命中构成。",
            ),
            NodeSpec(
                name=NODE_READ,
                capability="tieba.read_threads",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.read_threads"],
                artifact_type="tieba.read_results",
                input_key=_read_key,
                depends_on=(NODE_PARSE, NODE_PLAN, NODE_SEARCH),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="公开读取候选帖，按页面自身吧名确认归属并记录缺失范围。",
            ),
            NodeSpec(
                name=NODE_SYNTHESIZE,
                capability="tieba.synthesize_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.synthesize_evidence"],
                artifact_type="tieba.synthesis",
                input_key=_synthesize_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_PLAN,
                    NODE_VERIFY_OFFICIAL,
                    NODE_SEARCH,
                    NODE_READ,
                ),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="只用已读楼层归纳经历，核对官方适用性并检测冲突。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="tieba.verify_evidence",
                capability_version=TIEBA_CAPABILITY_VERSIONS["tieba.verify_evidence"],
                artifact_type="tieba.verification",
                input_key=_verify_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_PLAN,
                    NODE_VERIFY_OFFICIAL,
                    NODE_READ,
                    NODE_SYNTHESIZE,
                ),
                required_gates=TIEBA_GATES,
                recovery=RecoveryPolicy.BLOCK,
                description="结构化核对归属、读取范围、官方范围与冲突披露。",
            ),
        ),
    )


def tieba_recipe_registry() -> RecipeRegistry:
    """登记贴吧能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=TIEBA_CAPABILITY_VERSIONS.keys(),
        gates=TIEBA_GATES,
    )
    registry.register(build_tieba_recipe())
    return registry
