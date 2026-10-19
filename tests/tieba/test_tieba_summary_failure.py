"""模型总结失败时必须保留已读证据并明确未生成总结。"""

from datetime import UTC, datetime
from types import SimpleNamespace

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.contracts import ReadStatus, TiebaPostProjection, TiebaReply
from bridges.tieba.summary import TiebaSummaryGenerator


def _post() -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="123",
        url="https://tieba.baidu.com/p/123",
        title="宿舍条件",
        affiliation_evidence="已读取帖子页面，页面标题为华东交通大学吧。",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=2,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=3,
                reply_id="reply-3",
                posted_at="2026-09-01",
                content="北区宿舍有空调，但热水供应不太稳定。",
            )
        ],
        retrieved_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def test_model_failure_keeps_read_evidence_and_reports_no_summary() -> None:
    class FailingGateway:
        calls = 0

        def invoke(self, *args: object, **kwargs: object) -> object:
            self.calls += 1
            raise RuntimeError("上游模型超时")

    gateway = FailingGateway()
    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [_post()],
        topic="宿舍条件",
        model_id="verified-model",
        model_quota=RunModelQuota(
            model_id="verified-model",
            context_window=16000,
            max_input_tokens=12000,
            verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
        ),
        budget=None,
    )

    assert gateway.calls == 1
    assert outcome.summary is None
    assert outcome.claims == []
    assert outcome.citations == []
    assert "模型没有返回" in (outcome.note or "")
