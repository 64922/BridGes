"""工单42：同一固定候选池的生产论文筛选角色配对。

旧侧实际确定性解析/计划/排序；新侧实际模型证据筛选收据。这里不执行检索、
全文读取、最终概述、教学或桌面，所以不能代替完整工作流成本收益。
"""

from __future__ import annotations

import json
from typing import Any

from bridges.evaluation.workflow_semantics import (
    PAPER_FIXTURES,
    PAPER_REQUEST,
    fingerprint,
    paper_rubric,
)


def paired_input() -> dict[str, Any]:
    return {
        "request": PAPER_REQUEST,
        "candidates": [
            {
                "arxiv_id": key,
                "title": title,
                "abstract": abstract,
                "authors": [],
                "published_at": "2024-01-01T00:00:00+00:00",
                "abs_url": "https://example.org/abs",
                "pdf_url": "https://example.org/pdf",
            }
            for key, title, abstract, _ in PAPER_FIXTURES
        ],
    }


def decision_quality(selected: set[str]) -> dict[str, Any]:
    """相同人类真值：支持需求的两项必须入选，假阳性及稀疏项必须排除。"""
    expected = {key for key, _, _, truth in PAPER_FIXTURES if truth}
    allowed = {key for key, *_ in PAPER_FIXTURES}
    if selected - allowed:
        raise ValueError("筛选结果包含未登记候选。")
    return {
        "checks": {key: (key in selected) is truth for key, _, _, truth in PAPER_FIXTURES},
        "true_positive": len(selected & expected),
        "false_positive": len(selected - expected),
        "false_negative": len(expected - selected),
        "necessary_coverage": len(selected & expected) / len(expected),
    }


def real_relevance_calls(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """只采用能与原任务/候选逐项锁定的完整真实筛选收据，不能混入评分调用。"""
    expected = paired_input()
    rows = []
    for call in artifact.get("calls", []):
        output = call.get("output", {})
        if not isinstance(output, dict) or "matches" not in output:
            continue
        request = call.get("request", {})
        messages = request.get("messages", [])
        user = next((item["content"] for item in messages if item.get("role") == "user"), "")
        data = json.loads(user)
        actual = {
            "request": data.get("request", {}).get("original_phrase"),
            "candidates": data.get("candidates"),
        }
        locked = {
            "request": expected["request"],
            "candidates": [
                {name: item[name] for name in ("arxiv_id", "title", "abstract")}
                for item in expected["candidates"]
            ],
        }
        if actual != locked:
            raise ValueError("真实筛选收据与配对任务/来源不同。")
        if call.get("finish_reason") != "stop" or not call.get("usage"):
            raise ValueError("真实筛选响应不完整或未记录实际用量。")
        if call.get("actual_model") != artifact["model_config"]["model_id"]:
            raise ValueError("真实筛选实际模型与配置锁不同。")
        if call.get("request_sha256") != fingerprint(request):
            raise ValueError("真实筛选请求指纹不匹配。")
        rows.append(call)
    if len(rows) != artifact.get("repeats") or not rows:
        raise ValueError("真实筛选收据数量与重复次数不符。")
    return rows


def build_role_pairing(old: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if old.get("paired_input_sha256") != fingerprint(paired_input()):
        raise ValueError("旧侧任务数据与当前配对输入不一致。")
    real_calls = real_relevance_calls(artifact)
    if len(old.get("runs", [])) != len(real_calls):
        raise ValueError("旧新重复次数不一致。")
    pairs = []
    for prior, call in zip(old["runs"], real_calls, strict=True):
        matches = {item["arxiv_id"]: item for item in call["output"]["matches"]}
        selected = {key for key, item in matches.items() if item.get("relevant") is True}
        new_quality = decision_quality(selected)
        checks = paper_rubric(matches)
        new_quality["evidence_checks"] = checks
        old_quality = decision_quality(set(prior["selected"]))
        pairs.append(
            {
                "old": {
                    "quality": old_quality,
                    "model_calls": 0,
                    "tokens": 0,
                    "elapsed_ms": prior["elapsed_ms"],
                },
                "new": {
                    "quality": new_quality,
                    "model_calls": 1,
                    "usage": call["usage"],
                    "elapsed_ms": call["elapsed_ms"],
                },
                "quality_gain": sum(new_quality["checks"].values())
                - sum(old_quality["checks"].values()),
            }
        )
    return {
        "kind": "paper-production-role-pairing",
        "input": paired_input(),
        "input_sha256": fingerprint(paired_input()),
        "old_execution": old,
        "pairs": pairs,
        "observable_gain": all(row["quality_gain"] > 0 for row in pairs),
        "new_passed": all(
            all(all(row.values()) for row in pair["new"]["quality"]["evidence_checks"].values())
            for pair in pairs
        ),
        "limitations": [
            "角色级固定来源配对，不包含网络搜索、全文、最终推荐或教学",
            "旧侧用生产解析/计划/排序，新侧用真实专业筛选；各自边界不同，不能比较全流程耗时",
        ],
    }
