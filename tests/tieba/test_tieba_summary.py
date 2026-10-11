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
    TiebaTimeFilter,
)
from bridges.tieba.kernel import _summary_traceable
from bridges.tieba.summary import TiebaSummaryGenerator


class FakeGateway:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = iter(outputs)
        self.calls: list[tuple[str, str]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: object,
        payload: dict[str, object],
        **kwargs: object,
    ) -> object:
        self.calls.append((capability, version))
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
            {"supported": [0]},
        ]
    )
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(), [_post()], topic="宿舍条件", model_id="verified-model",
        model_quota=_quota(), budget=None,
    )
    assert outcome.summary == "一条个人经历提到东区宿舍热水供应不稳定。"
    assert outcome.citations[0].reply_id == "reply-3"
    assert outcome.citations[0].quote == "我住过东区宿舍，晚上热水供应不太稳定。"
    assert gateway.calls == [("qwen_structured_output", "1")] * 2


def test_unknown_citation_is_dropped_before_support_verification() -> None:
    gateway = FakeGateway(
        [{"claims": [{"kind": "finding", "text": "没有证据的观点。", "citation_ids": ["E999"]}]}]
    )
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(), [_post()], topic="宿舍", model_id="verified-model",
        model_quota=_quota(), budget=None,
    )
    assert outcome.summary is None
    assert not outcome.citations
    assert len(gateway.calls) == 1


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
