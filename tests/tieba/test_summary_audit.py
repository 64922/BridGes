"""总结各阶段实际模型结果交给运行级审计记录器。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.contracts import ReadStatus, TiebaPostProjection, TiebaReply
from bridges.tieba.summary import TiebaSummaryAuditError, TiebaSummaryGenerator


def test_every_summary_stage_records_its_model_result() -> None:
    records: list[Any] = []
    returned: list[Any] = []

    class Gateway:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            result = SimpleNamespace(lock=object())
            returned.append(result)
            return result

    quota = RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )
    generator = TiebaSummaryGenerator(Gateway(), on_result=records.append)
    for stage in ("claims", "aggregate", "support-check"):
        result = generator._invoke(
            "仅总结已读证据",
            {},
            SimpleNamespace(),
            "verified-model",
            quota,
            None,
            stage,
            800,
        )
        assert records[-1] is result
    assert records == returned
    assert len(records) == 3


def test_audit_failure_is_not_swallowed_as_model_failure() -> None:
    class Gateway:
        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                status=SimpleNamespace(value="success"),
                output={"claims": []},
                lock=object(),
            )

    def fail_audit(_result: Any) -> None:
        raise OSError("运行锁写入失败")

    post = TiebaPostProjection(
        thread_id="123",
        url="https://tieba.baidu.com/p/123",
        title="宿舍讨论",
        affiliation_evidence="页面声明所属贴吧为华东交通大学吧",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=1,
        replies_obtained=True,
        replies=[TiebaReply(reply_id="reply-1", content="宿舍有空调。")],
        retrieved_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    quota = RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )

    with pytest.raises(TiebaSummaryAuditError, match="运行锁审计失败"):
        TiebaSummaryGenerator(Gateway(), on_result=fail_audit).generate(
            SimpleNamespace(),
            [post],
            topic="宿舍条件",
            model_id="verified-model",
            model_quota=quota,
        )
