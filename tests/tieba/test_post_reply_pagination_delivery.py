from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.tieba.browser_pagination import PAGE_LIMIT
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPageEvidence,
    TiebaPostProjection,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.summary import TiebaSummaryGenerator
from tests.tieba.support import (
    _create_conversation,
    _FakeReader,
    _FakeSearchPort,
    _gateway_with,
    _install_tieba_service,
    _read_result,
    _register,
    _run_and_read,
    _send,
    _SilentAdapter,
)

URL = "https://tieba.baidu.com/p/10897336006"


def test_page_evidence_and_page_two_reply_survive_chat_projection(
    sqlite_app: Any, client: Any, generation_helpers: dict[str, Any]
) -> None:
    _register(client)
    reply_page_1 = TiebaReply(
        floor=3, reply_id="r-3", content="北区宿舍晚上热水有时不稳定。"
    )
    reply_page_2 = TiebaReply(
        floor=17, reply_id="r-17", content="第二页提到十一点后热水会停。"
    )
    result = _read_result(
        url=URL,
        status=ReadStatus.PARTIAL,
        replies=(reply_page_1, reply_page_2),
        pages_read=2,
        total_pages=10,
    ).model_copy(
        update={
            "pages_complete": False,
            "page_evidence": [
                TiebaPageEvidence(
                    page=1,
                    total_pages=10,
                    has_more=True,
                    top_level_count=14,
                    reply_count=20,
                ),
                TiebaPageEvidence(
                    page=2,
                    total_pages=10,
                    has_more=True,
                    top_level_count=15,
                    reply_count=18,
                ),
            ],
            "pagination_error_code": PAGE_LIMIT,
            "pagination_error_message": "已读取第 1–2 页，页面声明共 10 页；从第 3 页起未读取。",
        }
    )
    port = _FakeSearchPort(
        hits=[TiebaSearchHit(url=URL, title="宿舍热水和查寝情况", snippet="")]
    )
    reader = _FakeReader({URL: result})
    _install_tieba_service(sqlite_app, port=port, reader=reader)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation = _create_conversation(client)

    _send(client, conversation, "华东交通大学吧 宿舍热水", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation
    )

    post = assistant["tieba_research"]["confirmed_posts"][0]
    assert post["pages_read"] == 2
    assert post["pages_complete"] is False
    assert [item["page"] for item in post["page_evidence"]] == [1, 2]
    assert post["pagination_error_code"] == PAGE_LIMIT
    assert [reply["floor"] for reply in post["replies"]] == [3, 17]
    assert post["replies"][-1]["reply_id"] == "r-17"


class _SummaryGateway:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self._outputs = iter(outputs)
        self.payloads: list[dict[str, object]] = []

    def invoke(
        self,
        capability: str,
        version: str,
        run_context: object,
        payload: dict[str, object],
        **kwargs: object,
    ) -> object:
        del capability, version, run_context, kwargs
        self.payloads.append(payload)
        return SimpleNamespace(
            status=SimpleNamespace(value="success"), output=next(self._outputs)
        )


def test_second_page_reply_can_enter_summary_citation() -> None:
    post = TiebaPostProjection(
        thread_id="10897336006",
        url=URL,
        title="宿舍热水和查寝情况",
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.PARTIAL,
        pages_read=2,
        pages_limit=2,
        total_pages=10,
        total_replies=242,
        replies_obtained=True,
        replies=[
            TiebaReply(
                floor=3,
                reply_id="r-3",
                content="北区宿舍晚上热水有时不稳定。",
            ),
            TiebaReply(
                floor=17,
                reply_id="r-17",
                content="第二页提到十一点后热水会停。",
            ),
        ],
        retrieved_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    gateway = _SummaryGateway(
        [
            {
                "claims": [
                    {
                        "kind": "finding",
                        "text": "第二页回复提到十一点后热水会停。",
                        "citation_ids": ["E2"],
                    }
                ]
            },
            {
                "summary": "有回复提到晚上十一点后宿舍热水会停。",
                "claims": [
                    {
                        "kind": "finding",
                        "text": "第二页回复提到十一点后热水会停。",
                        "citation_ids": ["E2"],
                    }
                ],
            },
            {"supported": [0, 1]},
        ]
    )
    quota = RunModelQuota(
        model_id="verified-model",
        context_window=16000,
        max_input_tokens=12000,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )

    outcome = TiebaSummaryGenerator(gateway).generate(
        SimpleNamespace(),
        [post],
        topic="宿舍热水",
        model_id="verified-model",
        model_quota=quota,
        budget=None,
    )

    assert outcome.summary == "有回复提到晚上十一点后宿舍热水会停。"
    assert outcome.citations[0].reply_id == "r-17"
    assert outcome.citations[0].floor == 17
