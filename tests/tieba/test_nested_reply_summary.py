"""工单 07：楼中楼纠正进入总结时保留父楼层上下文。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaNestedProgress,
    TiebaNestedState,
    TiebaPostProjection,
    TiebaReply,
)
from bridges.tieba.summary import TiebaSummaryGenerator
from bridges.tieba.summary_contracts import SUMMARY_PROMPT_VERSION
from bridges.tieba.summary_material import sources

URL = "https://tieba.baidu.com/p/10897336006"
PARENT_ID = "153765210147"


class _Gateway:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self._outputs = iter(outputs)
        self.payloads: list[dict[str, Any]] = []
        self.contracts: list[Any] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: object,
        payload: dict[str, object],
        **kwargs: object,
    ) -> object:
        del capability, version, run_context
        self.payloads.append(payload)
        self.contracts.append(kwargs["call_contract"])
        return SimpleNamespace(
            status=SimpleNamespace(value="success"), output=next(self._outputs)
        )


def _post() -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="10897336006",
        url=URL,
        title="北区宿舍热水几点停",
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」。",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=2,
        pages_complete=False,
        total_pages=10,
        total_replies=18,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=4,
                reply_id=PARENT_ID,
                author="父楼层作者",
                posted_at="2026-08-01 10:00",
                content="北区热水十一点就停了。",
                nested_progress=TiebaNestedProgress(
                    state=TiebaNestedState.COMPLETE,
                    total_replies=17,
                    preview_count=4,
                    read_count=17,
                    pages_read=2,
                    pages_limit=2,
                    total_pages=2,
                    complete=True,
                ),
            ),
            TiebaReply(
                floor=4,
                reply_id="n5",
                parent_reply_id=PARENT_ID,
                author="纠正者",
                posted_at="2026-08-05 00:17",
                content="更正：北区是十一点半停水，不是十一点。",
                nested=True,
            ),
        ],
        retrieved_at=datetime(2026, 10, 19, tzinfo=UTC),
    )


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def test_nested_correction_payload_includes_parent_context() -> None:
    materials = sources([_post()])

    parent = materials[0]
    nested = materials[1]
    assert parent["reply_id"] == PARENT_ID
    assert parent["nested"] is False
    assert nested["reply_id"] == "n5"
    assert nested["nested"] is True
    assert nested["parent_reply_id"] == PARENT_ID
    assert nested["parent_content"] == "北区热水十一点就停了。"
    assert nested["parent_author"] == "父楼层作者"
    assert nested["parent_context_state"] == "read"


def test_correction_can_be_cited_without_attributing_parent_stance() -> None:
    gateway = _Gateway(
        [
            {
                "claims": [
                    {
                        "kind": "difference",
                        "text": "有楼中楼更正停水时间为十一点半，与父楼层说法不同。",
                        "citation_ids": ["E2"],
                    }
                ]
            },
            {
                "summary": "父楼层说十一点停水，但楼中楼更正为十一点半。",
                "claims": [
                    {
                        "kind": "difference",
                        "text": "有楼中楼更正停水时间为十一点半，与父楼层说法不同。",
                        "citation_ids": ["E2"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_post()],
        topic="北区宿舍热水",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    assert outcome.summary is not None
    assert len(outcome.citations) == 1
    citation = outcome.citations[0]
    assert citation.reply_id == "n5"
    assert citation.parent_reply_id == PARENT_ID
    assert citation.nested is True
    model_payload = gateway.payloads[0]
    evidence = json.loads(model_payload["messages"][1]["content"])["evidence"][1]
    assert evidence["parent_content"] == "北区热水十一点就停了。"
    assert evidence["parent_context_state"] == "read"
    instruction = model_payload["messages"][0]["content"]
    assert "parent_content" in instruction
    assert "不能把父楼层作者" in instruction
    assert gateway.contracts[0].prompt_version == SUMMARY_PROMPT_VERSION
    assert gateway.contracts[0].input_schema_version == "tieba-read-evidence-v4"


def test_orphan_nested_source_marks_parent_context_missing() -> None:
    post = _post().model_copy(
        update={
            "replies": [
                TiebaReply(
                    floor=4,
                    reply_id="n-orphan",
                    parent_reply_id=PARENT_ID,
                    content="找不到父楼层的楼中楼",
                    nested=True,
                )
            ]
        }
    )

    material = sources([post])[0]

    assert material["nested"] is True
    assert material["parent_context_state"] == "missing"
    assert "parent_content" not in material
