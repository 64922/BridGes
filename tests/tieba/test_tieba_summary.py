import json
from datetime import UTC, datetime
from types import SimpleNamespace

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPostProjection,
    TiebaReply,
    TiebaResearchProjection,
    TiebaResearchStatus,
    TiebaSummaryCitation,
    TiebaSummaryClaim,
    TiebaTemporalScope,
    TiebaTimeFilter,
)
from bridges.tieba.evidence_verification import _summary_traceable
from bridges.tieba.summary import TiebaSummaryGenerator


class FakeGateway:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = iter(outputs)
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict[str, object]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: object,
        payload: dict[str, object],
        **kwargs: object,
    ) -> object:
        self.calls.append((capability, version))
        self.payloads.append(payload)
        output = next(self.outputs)
        return SimpleNamespace(status=SimpleNamespace(value="success"), output=output)


def _post() -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="123",
        url="https://tieba.baidu.com/p/123",
        title="宿舍讨论",
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=2,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=3,
                reply_id="reply-3",
                posted_at="2026-09-01",
                content="我住过东区宿舍，晚上热水供应不太稳定。",
            )
        ],
        retrieved_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def test_summary_requires_known_read_evidence_and_second_support_check() -> None:
    gateway = FakeGateway(
        [
            {
                "claims": [
                    {
                        "kind": "finding",
                        "text": "一条个人经历提到东区宿舍热水供应不稳定。",
                        "citation_ids": ["E1"],
                    }
                ]
            },
            {
                "summary": "部分学生提到东区宿舍热水供应不稳定。",
                "claims": [
                    {
                        "kind": "finding",
                        "text": "一条个人经历提到东区宿舍热水供应不稳定。",
                        "citation_ids": ["E1"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_post()],
        topic="宿舍条件",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )
    assert outcome.summary == "部分学生提到东区宿舍热水供应不稳定。"
    assert outcome.citations[0].reply_id == "reply-3"
    assert outcome.citations[0].quote == "我住过东区宿舍，晚上热水供应不太稳定。"
    assert gateway.calls == [("qwen_structured_output", "1")] * 3
    assert outcome.claims[0].kind == "finding"


def test_unknown_citation_is_dropped_before_support_verification() -> None:
    gateway = FakeGateway(
        [{"claims": [{"kind": "finding", "text": "没有证据的观点。", "citation_ids": ["E999"]}]}]
    )
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_post()],
        topic="宿舍",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )
    assert outcome.summary is None
    assert not outcome.citations
    assert len(gateway.calls) == 1


def test_multiple_evidence_batches_are_semantically_aggregated_before_verification() -> None:
    replies = [
        TiebaReply(
            floor=index,
            reply_id=f"reply-{index}",
            content=(
                "宿舍热水不稳定"
                if index == 1
                else "宿舍热水供应稳定"
                if index == 9
                else f"第{index}条宿舍经历"
            ),
        )
        for index in range(1, 10)
    ]
    post = _post().model_copy(update={"replies": replies})
    gateway = FakeGateway(
        [
            {
                "claims": [
                    {
                        "kind": "finding",
                        "text": "一条回复认为热水不稳定。",
                        "citation_ids": ["E1"],
                    }
                ]
            },
            {
                "claims": [
                    {
                        "kind": "difference",
                        "text": "另一条回复认为热水供应稳定。",
                        "citation_ids": ["E9"],
                    }
                ]
            },
            {
                "summary": (
                    "讨论中对宿舍热水稳定性存在不同描述，有人认为供应不稳定，也有人认为较稳定。"
                ),
                "claims": [
                    {
                        "kind": "difference",
                        "text": "回复对热水供应稳定性存在不同描述。",
                        "citation_ids": ["E1", "E9"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    assert outcome.summary == (
        "讨论中对宿舍热水稳定性存在不同描述，有人认为供应不稳定，也有人认为较稳定。"
    )
    assert outcome.claims[0].kind == "difference"
    assert len(gateway.calls) == 4
    aggregation = json.loads(gateway.payloads[2]["messages"][1]["content"])
    assert len(aggregation["claims"]) == 2
    assert {"E1", "E9"} <= {
        citation_id for claim in aggregation["claims"] for citation_id in claim["citation_ids"]
    }


def test_explicit_unmappable_time_excludes_unknown_replies_from_model_material() -> None:
    post = _post().model_copy(
        update={
            "replies": [
                TiebaReply(
                    reply_id="unknown-time",
                    content="时间未知的宿舍经历",
                    temporal_scope=TiebaTemporalScope.UNKNOWN,
                )
            ]
        }
    )
    gateway = FakeGateway([])

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍",
        time_requirement="上学期",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    assert outcome.summary is None
    assert "时间条件" in (outcome.note or "")
    assert gateway.calls == []


def test_explicit_time_excludes_historical_and_unknown_replies_from_summary() -> None:
    post = _post().model_copy(
        update={
            "replies": [
                TiebaReply(
                    reply_id="in-range",
                    content="本学期宿舍热水比较稳定。",
                    temporal_scope=TiebaTemporalScope.RECENT,
                ),
                TiebaReply(
                    reply_id="historical",
                    content="上一学年宿舍热水经常中断。",
                    temporal_scope=TiebaTemporalScope.HISTORICAL,
                ),
                TiebaReply(
                    reply_id="unknown",
                    content="时间未知的宿舍体验。",
                    temporal_scope=TiebaTemporalScope.UNKNOWN,
                ),
            ]
        }
    )
    gateway = FakeGateway(
        [
            {
                "claims": [
                    {
                        "kind": "finding",
                        "text": "一条本学期经历认为热水较稳定。",
                        "citation_ids": ["E1"],
                    }
                ]
            },
            {
                "summary": "本学期有回复认为宿舍热水较稳定。",
                "claims": [
                    {
                        "kind": "finding",
                        "text": "一条本学期经历认为热水较稳定。",
                        "citation_ids": ["E1"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍热水",
        time_requirement="本学期",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    payload = json.loads(gateway.payloads[0]["messages"][1]["content"])
    assert [item["reply_id"] for item in payload["evidence"]] == ["in-range"]
    assert outcome.summary == "本学期有回复认为宿舍热水较稳定。"
    assert "历史发言" in (outcome.note or "")
    assert "时间未知" in (outcome.note or "")


def test_explicit_time_includes_historical_parent_only_as_recent_reply_context() -> None:
    post = _post().model_copy(
        update={
            "replies": [
                TiebaReply(
                    reply_id="recent-child",
                    parent_reply_id="old-parent",
                    content="是的，晚上确实会断供。",
                    temporal_scope=TiebaTemporalScope.RECENT,
                ),
                TiebaReply(
                    reply_id="old-parent",
                    content="宿舍晚上热水供应稳定吗？",
                    temporal_scope=TiebaTemporalScope.HISTORICAL,
                ),
                TiebaReply(
                    reply_id="unrelated-old",
                    content="与该楼中楼无关的历史回复。",
                    temporal_scope=TiebaTemporalScope.HISTORICAL,
                ),
            ]
        }
    )
    gateway = FakeGateway(
        [
            {
                "claims": [
                    {
                        "kind": "finding",
                        "text": "近期楼中楼有人称晚间会断供。",
                        "citation_ids": ["E1"],
                    },
                    {
                        "kind": "finding",
                        "text": "旧回复讨论热水是否稳定。",
                        "citation_ids": ["E2"],
                    },
                ]
            },
            {
                "summary": "一条近期楼中楼回复称晚间热水会断供。",
                "claims": [
                    {
                        "kind": "finding",
                        "text": "近期楼中楼有人称晚间会断供。",
                        "citation_ids": ["E1"],
                    },
                    {
                        "kind": "finding",
                        "text": "旧回复讨论热水是否稳定。",
                        "citation_ids": ["E2"],
                    },
                ],
            },
            {"supported": [0, 1]},
        ]
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍热水",
        time_requirement="本学期",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    evidence = json.loads(gateway.payloads[0]["messages"][1]["content"])["evidence"]
    assert [(item["reply_id"], item.get("context_only", False)) for item in evidence] == [
        ("recent-child", False),
        ("old-parent", True),
    ]
    assert "context_only 为 true" in gateway.payloads[0]["messages"][0]["content"]
    aggregation = json.loads(gateway.payloads[1]["messages"][1]["content"])
    assert [claim["citation_ids"] for claim in aggregation["claims"]] == [["E1"]]
    verification = json.loads(gateway.payloads[2]["messages"][1]["content"])
    assert "宿舍晚上热水供应稳定吗？" not in json.dumps(verification, ensure_ascii=False)
    assert outcome.summary == "一条近期楼中楼回复称晚间热水会断供。"
    assert [item.reply_id for item in outcome.citations] == ["recent-child"]
    assert "仅供理解近期楼中楼上下文" in (outcome.note or "")


def test_material_and_claim_caps_are_reported_in_summary_note() -> None:
    post = _post().model_copy(
        update={
            "replies": [
                TiebaReply(
                    floor=index,
                    reply_id=f"reply-{index}",
                    content=f"第{index}条宿舍经历",
                    temporal_scope=TiebaTemporalScope.RECENT,
                )
                for index in range(1, 26)
            ]
        }
    )
    first_claims = [
        {
            "kind": "finding",
            "text": f"第一批观点 {index}",
            "citation_ids": [f"E{index}"],
        }
        for index in range(1, 6)
    ]
    second_claims = [
        {
            "kind": "finding",
            "text": f"其他观点 {index}",
            "citation_ids": [f"E{index}"],
        }
        for index in range(9, 13)
    ]
    third_claims = [
        {
            "kind": "finding",
            "text": f"其他观点 {index}",
            "citation_ids": [f"E{index}"],
        }
        for index in range(17, 21)
    ]
    gateway = FakeGateway(
        [
            {"claims": first_claims},
            {"claims": second_claims},
            {"claims": third_claims},
            {
                "summary": "多条回复分享了宿舍经历。",
                "claims": [
                    {
                        "kind": "finding",
                        "text": "多条回复分享了宿舍经历。",
                        "citation_ids": ["E1", "E9", "E17"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍",
        model_id="verified-model",
        model_quota=_quota(),
        budget=None,
    )

    assert outcome.summary == "多条回复分享了宿舍经历。"
    assert "另有 1 条已读原文未进入总结" in (outcome.note or "")
    assert "观点超过 12 条展示上限" in (outcome.note or "")


def test_kernel_rejects_a_citation_with_unread_quote_or_identity() -> None:
    post = _post()
    projection = TiebaResearchProjection(
        status=TiebaResearchStatus.SUCCESS,
        topic="宿舍",
        original_question="宿舍条件",
        time_filter=TiebaTimeFilter(note="已分类。"),
        summary="可核验观点。",
        summary_claims=[TiebaSummaryClaim(text="可核验观点。", citation_ids=["E1"])],
        summary_citations=[
            TiebaSummaryCitation(
                citation_id="E1",
                url=post.url,
                reply_id="reply-3",
                floor=3,
                posted_at="2026-09-01",
                quote="我住过其他校区。",
            )
        ],
    )
    assert not _summary_traceable(projection, [post])

    valid_post = post.model_copy(
        update={
            "replies": [
                post.replies[0].model_copy(
                    update={"content": "我住过东区宿舍，晚上热水供应不太稳定。"}
                )
            ]
        }
    )
    independent_summary = projection.model_copy(
        update={
            "summary": "有回复提到东区宿舍晚间热水供应不稳定。",
            "summary_citations": [
                projection.summary_citations[0].model_copy(
                    update={"quote": "晚上热水供应不太稳定。"}
                )
            ],
        }
    )
    assert _summary_traceable(independent_summary, [valid_post])
