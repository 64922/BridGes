"""Issue 10 checkpoint resume tests."""

from __future__ import annotations

from typing import Any

import pytest

from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import search_candidates
from bridges.tieba.node_flow_support import (
    read_outcome_payload,
    search_attempts_payload,
)
from bridges.tieba.parsing import parse_tieba_request
from tests.tieba.stop_resume_support import SECOND_URL, _hit, _read_result
from tests.tieba.support import CONFIRMED_URL, _FakeReader, _FakeSearchPort


def test_read_checkpoint_resumes_without_rereading_confirmed_posts() -> None:
    analysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    port = _FakeSearchPort(hits=[_hit(CONFIRMED_URL), _hit(SECOND_URL)])
    attempts = search_candidates(
        port, "acc-10", analysis, stop_event=None, deadline_seconds=10.0
    )
    reader = _FakeReader({CONFIRMED_URL: _read_result(CONFIRMED_URL)})
    saved: list[dict[str, Any]] = []

    class CrashError(RuntimeError):
        pass

    def checkpoint(outcome: Any) -> None:
        saved.append(read_outcome_payload(outcome))
        if len(outcome.confirmed) == 1:
            raise CrashError

    with pytest.raises(CrashError):
        read_candidates(
            reader,
            attempts,
            analysis=analysis,
            stop_event=None,
            deadline_seconds=10.0,
            checkpoint=checkpoint,
        )

    assert [item["url"] for item in saved[-1]["confirmed"]] == [CONFIRMED_URL]
    resumed_reader = _FakeReader(
        {CONFIRMED_URL: _read_result(CONFIRMED_URL), SECOND_URL: _read_result(SECOND_URL)}
    )
    reads = read_candidates(
        resumed_reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=10.0,
        resume=saved[-1],
    )
    assert resumed_reader.read_urls == [SECOND_URL]
    assert {post.url for post in reads.confirmed} == {CONFIRMED_URL, SECOND_URL}


def test_search_checkpoint_does_not_repeat_completed_query_rounds() -> None:
    analysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    port = _FakeSearchPort(hits=[_hit()])
    saved: list[dict[str, Any]] = []

    class CrashError(RuntimeError):
        pass

    def checkpoint(outcome: Any) -> None:
        saved.append(search_attempts_payload(outcome))
        raise CrashError

    with pytest.raises(CrashError):
        search_candidates(
            port,
            "acc-10",
            analysis,
            stop_event=None,
            deadline_seconds=10.0,
            checkpoint=checkpoint,
        )
    first_queries = list(port.queries)
    assert saved[-1]["records"]

    resumed_port = _FakeSearchPort(hits=[_hit()])
    search_candidates(
        resumed_port,
        "acc-10",
        analysis,
        stop_event=None,
        deadline_seconds=10.0,
        resume=saved[-1],
    )
    assert set(resumed_port.queries).isdisjoint(first_queries)


