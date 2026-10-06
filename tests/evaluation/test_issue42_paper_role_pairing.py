"""角色配对必须锁真实输入与收据，并按同一人工真值比较。"""

import json

import pytest

from bridges.evaluation.paper_role_pairing import (
    build_role_pairing,
    paired_input,
    real_relevance_calls,
)
from bridges.evaluation.workflow_semantics import PAPER_FIXTURES, PAPER_REQUEST, fingerprint


def _artifact():
    request = {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": {"original_phrase": PAPER_REQUEST},
                        "candidates": [
                            {"arxiv_id": key, "title": title, "abstract": abstract}
                            for key, title, abstract, _ in PAPER_FIXTURES
                        ],
                    }
                ),
            }
        ]
    }
    return {
        "model_config": {"model_id": "model"},
        "repeats": 1,
        "calls": [
            {
                "request": request,
                "request_sha256": fingerprint(request),
                "actual_model": "model",
                "finish_reason": "stop",
                "usage": {"total_tokens": 30},
                "elapsed_ms": 10,
                "output": {
                    "matches": [
                        {
                            "arxiv_id": key,
                            "relevant": truth,
                            "evidence": [
                                {
                                    "source": "abstract",
                                    "quote": abstract,
                                    "requirement": "固定必要需求",
                                }
                            ]
                            if truth
                            else [],
                        }
                        for key, title, abstract, truth in PAPER_FIXTURES
                    ]
                },
            }
        ],
    }


def test_pairing_scores_same_truth_with_actual_cost() -> None:
    old = {
        "paired_input_sha256": fingerprint(paired_input()),
        "runs": [{"selected": [], "elapsed_ms": 1}],
    }
    report = build_role_pairing(old, _artifact())
    assert report["observable_gain"]
    assert report["new_passed"]
    assert report["pairs"][0]["quality_gain"] == 2
    assert report["pairs"][0]["new"]["model_calls"] == 1
    assert report["pairs"][0]["old"]["model_calls"] == 0


def test_incomplete_or_changed_real_receipt_is_rejected() -> None:
    artifact = _artifact()
    artifact["calls"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError, match="响应不完整"):
        real_relevance_calls(artifact)
    artifact = _artifact()
    artifact["calls"][0]["request_sha256"] = "wrong"
    with pytest.raises(ValueError, match="指纹不匹配"):
        real_relevance_calls(artifact)


def test_mismatched_candidate_pool_or_old_input_is_rejected() -> None:
    artifact = _artifact()
    message = artifact["calls"][0]["request"]["messages"][0]
    payload = json.loads(message["content"])
    payload["candidates"][0]["abstract"] = "不同来源"
    message["content"] = json.dumps(payload)
    with pytest.raises(ValueError, match="配对任务/来源不同"):
        real_relevance_calls(artifact)
    with pytest.raises(ValueError, match="旧侧任务数据"):
        build_role_pairing({"paired_input_sha256": "wrong"}, _artifact())
