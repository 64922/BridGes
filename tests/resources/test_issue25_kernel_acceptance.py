"""工单 25：真实节点产物与 SQLite 预算账本的停止、记账和补证验收。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import patch

import pytest

from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.resources.contracts import BookReadEvidence
from bridges.resources.kernel import ResourcesBudget, ResourcesNodeFlow, build_resources_recipe
from bridges.resources.sources import (
    BookCandidate,
    BookSearchOutcome,
    VideoDiscoveryOutcome,
    VideoVerifyOutcome,
)
from bridges.storage.database import BridgesDatabase


class _Ports:
    """无外网来源替身；真实预算不使用替身。"""

    source = "openlibrary"

    def __init__(self, stop: Event, stop_at: str | None = None) -> None:
        self.stop = stop
        self.stop_at = stop_at
        self.calls: dict[str, list[float | None]] = {
            kind: [] for kind in ("book", "discover", "verify", "read")
        }

    def _record(self, kind: str, deadline: float | None) -> None:
        self.calls[kind].append(deadline)
        if self.stop_at == kind:
            self.stop.set()

    def search(
        self, account_id: str, query: str, *, limit: int, deadline: float | None = None
    ) -> BookSearchOutcome:
        self._record("book", deadline)
        return BookSearchOutcome(
            query=query,
            candidates=[
                BookCandidate(
                    title=f"深度学习入门资料{i}",
                    creators=[f"作者{i}"],
                    year=2024,
                    publisher=None,
                    isbn=None,
                    source=self.source,
                    url=f"https://openlibrary.org/works/OL{i}W",
                )
                for i in range(min(limit, 7))
            ],
            record=ModuleQueryRecord(
                source=self.source, query=query, status=ModuleQueryStatus.SUCCESS
            ),
        )

    def discover(self, account_id: str, query: str, **kwargs: Any) -> VideoDiscoveryOutcome:
        self._record("discover", kwargs["deadline"])
        return VideoDiscoveryOutcome(
            query=query,
            direct_pages=[f"https://www.bilibili.com/video/BV{i}" for i in range(3)],
            record=ModuleQueryRecord(
                source="tavily", query=query, status=ModuleQueryStatus.SUCCESS
            ),
        )

    def verify(
        self, pages: list[str], *, account_id: str, deadline: float | None = None
    ) -> VideoVerifyOutcome:
        assert len(pages) == 1
        self._record("verify", deadline)
        return VideoVerifyOutcome(candidates=[], records=[], rejected=1)

    def read(
        self, candidate_url: str, source: str, *, account_id: str, deadline: float | None = None
    ) -> BookReadEvidence:
        self._record("read", deadline)
        return BookReadEvidence(url=candidate_url, scope="已读取作品页，但页面没有目录或简介")


def _flow(tmp_path: Path, stop_at: str | None = None) -> tuple[Any, ...]:
    db = BridgesDatabase(tmp_path / "budget.db")
    db.initialize()
    ledger = RunBudgetLedgerRepository(db)
    now = datetime.now(UTC)
    ledger.freeze_for_run(
        account_id="account",
        run_id="run",
        conversation_id="conversation",
        plan=derive_run_budget_plan(RunBudgetClass.NORMAL, deadline_at=now + timedelta(seconds=60)),
        now=now,
    )
    budget = ResourcesBudget(
        ledger=ledger, account_id="account", run_id="run", work_deadline=now + timedelta(seconds=45)
    )
    stop = Event()
    ports = _Ports(stop, stop_at)
    flow = ResourcesNodeFlow(
        books=[ports, ports],
        discoverer=ports,
        verifier=ports,
        reader=ports,
        budget=budget,
        stop_event=stop,
    )
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="我是零基础，想系统学习深度学习，推荐学习资料",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )
    return flow, inputs, ports, ledger, budget


def test_subsecond_deadline_is_not_extended(tmp_path: Path) -> None:
    """不足半秒的预算不能被最小超时值延长。"""
    _, _, _, ledger, _ = _flow(tmp_path)
    now = datetime.now(UTC)
    budget = ResourcesBudget(
        ledger=ledger, account_id="account", run_id="run",
        work_deadline=now + timedelta(milliseconds=200),
    )
    assert budget.deadline_seconds(now) == 0.2
    assert budget.deadline_seconds(now + timedelta(milliseconds=201)) == 0


def _execute(
    flow: ResourcesNodeFlow, inputs: RecipeInputs, *, through: str = "resources.verify"
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for spec in build_resources_recipe().nodes:
        invocation = NodeInvocation(
            spec=spec,
            inputs=replace(inputs, artifacts=dict(artifacts)),
            dependencies={name: artifacts[name] for name in spec.depends_on},
            remaining_budget_ms=45_000,
        )
        result = flow.run_node(invocation)
        assert not result.stop_recipe, result.detail
        artifacts[spec.name] = result.artifact
        if spec.name == through:
            break
    return artifacts


def test_video_pages_each_consume_one_external_call_and_share_deadline(tmp_path: Path) -> None:
    flow, inputs, ports, ledger, _budget = _flow(tmp_path)
    with patch("bridges.resources.kernel.time.monotonic", return_value=100.0):
        _execute(flow, inputs, through="resources.search_videos")
    snapshot = ledger.load("account", "run")
    assert snapshot is not None
    assert snapshot.external_calls_used == 6  # 两次书目、一次发现、三次视频核对。
    assert snapshot.external_calls_active == 0
    assert ports.calls["verify"] == [112.0] * 3
    assert ports.calls["discover"] == [120.0]


@pytest.mark.parametrize(
    ("stage", "through"),
    [
        ("book", "resources.search_books"),
        ("discover", "resources.search_videos"),
        ("verify", "resources.search_videos"),
        ("read", "resources.read"),
    ],
)
def test_stop_during_external_call_prevents_next_request(
    tmp_path: Path, stage: str, through: str
) -> None:
    flow, inputs, ports, _ledger, _budget = _flow(tmp_path, stage)
    _execute(flow, inputs, through=through)
    assert len(ports.calls[stage]) == 1
    if stage == "discover":
        assert ports.calls["verify"] == []
    if stage == "book":
        assert ports.calls["discover"] == []


def test_evidence_supplement_uses_one_shared_adjustment_and_keeps_candidates(
    tmp_path: Path,
) -> None:
    flow, inputs, ports, ledger, budget = _flow(tmp_path)
    artifacts = _execute(flow, inputs)
    snapshot = ledger.load("account", "run")
    assert snapshot is not None
    assert snapshot.adjustment_rounds_used == 1
    assert not budget.begin_adjustment()
    assert len(ports.calls["read"]) == 6  # 初读三本，唯一补证轮再读三本。
    projection = artifacts["resources.verify"].payload["projection"]
    assert projection["items"]
    assert projection["path_verified"] is False
    assert all(item["role"] == "supplement" for item in projection["items"])
