"""工单 42：固定来源下实际生产语义角色与评分函数的独立量表。

来源是公开概念的合成夹具，不是外部可得性证据。此评测不经过 API、
持久化与前端，也不替代旧新树配对。模型响应必须真实调用后另行归档。

工单 12：旧五阶段学习复盘/总结/范围语义随执行删除一并退役，本量表
只保留论文相关性筛选与证据定位的真实执行收据。
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
from bridges.paper.assessment import PaperEvidenceRole
from bridges.paper.contracts import PaperTermAnalysis
from bridges.paper.sources import PaperCandidate

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


def fingerprint(value: Any) -> str:
    """锁定合成来源、实际提示与 Schema；不包含运行凭据。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def corpus_snapshot() -> dict[str, Any]:
    return {
        "request": PAPER_REQUEST,
        "papers": PAPER_FIXTURES,
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
    """实际执行生产论文筛选函数；失败原样列出，不记作通过。"""
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
    return {
        "kind": "production-role-semantics",
        "corpus": corpus_snapshot(),
        "corpus_sha256": fingerprint(corpus_snapshot()),
        "cases": rows,
        "passed": all(all(row["checks"].values()) for row in rows),
        "limitations": [
            "独立新树角色测试；未验证旧新树完整流程配对、识别图片或范围生成",
            "合成来源固定；不是外部读取上线证据，不经过生产 API/持久化/界面",
            "工单 12：旧五阶段学习复盘/总结语义随执行退役，不在本量表内",
        ],
    }
