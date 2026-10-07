"""真实搜索模式：访问验证页不能阻断补搜，已提取正文不能被丢弃。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridges.career_plan.collecting import HttpJobPageReader
from bridges.career_plan.contracts import CareerPlanProjection, CareerPlanStatus
from bridges.career_plan.extracted_pages import read_extracted_hit
from bridges.career_plan.kernel import CareerBudget, build_career_recipe
from bridges.career_plan.searching import (
    CareerSearchHit,
    CareerSearchOutcome,
    WebSearchServiceAdapter,
    query_record,
)
from bridges.chat.run_budget_ledger import (
    RunBudgetClass,
    RunBudgetLedgerRepository,
    derive_run_budget_plan,
)
from bridges.contracts.modules import ModuleQueryStatus
from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
    WebSearchVerification,
)
from tests.career_plan.test_career_plan_kernel_acceptance import (
    ACCOUNT,
    CONVERSATION,
    RUN,
    _inputs,
    _kernel,
    _Ports,
    _seed,
)

NOW = datetime(2026, 9, 26, tzinfo=UTC)
REQUEST = "我想做agent开发相关工作，给我规划一下；算法；大三"
BLOCKED = "https://www.zhipin.com/job_detail/blocked.html"
DETAIL = "https://careers.example.com/campus/job/123"
BODY = (
    "# Agent开发实习生\n\n工作地点：上海\n\n发布日期：2026-09-24\n\n"
    "## 岗位职责\n\n负责 Agent 工具调用与大模型应用开发。\n\n"
    "## 任职要求\n\n熟悉 Python、RAG 与 LangChain。"
)
HTML = (
    "<h1>Agent开发实习生</h1><p>工作地点：上海</p><p>发布日期：2026-09-24</p>"
    "<h2>岗位职责</h2><p>负责 Agent 工具调用与大模型应用开发。</p>"
    "<h2>任职要求</h2><p>熟悉 Python、RAG 与 LangChain。</p>"
)


def test_campaign_faq_with_role_tags_is_not_an_individual_job() -> None:
    """真实返回的校招汇总 FAQ 不能用标签拼出一个不存在的单岗位。"""
    hit = CareerSearchHit(
        url="https://www.wondercv.com/xiaozhao/taotian-2026-daily-intern",
        title="淘天集团日常实习", source="campus", snippet="",
        page_fetched_at=NOW,
        page_content=(
            "Agent开发 算法工程 多模态大模型 视频调色 AI游戏 淘天集团 日常实习\n"
            "杭州 北京\n所有实习岗位均要求线下全职实习。\n"
            "根据岗位要求，Agent开发实习生和算法工程师实习生建议提前规划。\n"
        ),
    )
    assert read_extracted_hit(hit) is None


def test_extracted_daily_salary_and_last_numbered_requirement_are_preserved() -> None:
    requirements = " ".join(
        f"{i}、熟悉大模型与智能体开发，具备良好团队合作意识和项目实践能力。"
        for i in range(1, 8)
    ) + " 8、具备良好的英文读写能力。"
    result = read_extracted_hit(CareerSearchHit(
        url=DETAIL, title="Agent开发实习生", source="campus", snippet="",
        page_fetched_at=NOW,
        page_content=BODY.replace("工作地点：上海", "100-200元/天\n工作地点：上海")
        .replace("熟悉 Python、RAG 与 LangChain。", requirements),
    ))
    assert result is not None
    assert result.page.salary_raw == "100-200元/天"
    assert any("英文读写" in line for line in result.page.requirements)


def _run(
    tmp_path: Path, ports: Any, *, consumed_recovery: bool | None = None,
) -> CareerPlanProjection:
    database = BridgesDatabase(tmp_path / "recovery.db")
    database.initialize()
    _seed(database)
    try:
        kernel, flow = _kernel(database, ports)
        if consumed_recovery is not None:
            ledger = RunBudgetLedgerRepository(database)
            now = datetime.now(UTC)
            plan = derive_run_budget_plan(
                RunBudgetClass.DEEP, deadline_at=now + timedelta(seconds=120),
            )
            ledger.freeze_for_run(
                account_id=ACCOUNT, run_id=RUN, conversation_id=CONVERSATION, plan=plan, now=now,
            )
            if consumed_recovery:
                ledger.begin_adjustment(account_id=ACCOUNT, run_id=RUN, now=now)
            flow._budget = CareerBudget(
                ledger=ledger, account_id=ACCOUNT, run_id=RUN,
                work_deadline=now + timedelta(seconds=90),
            )
        result = kernel.execute(recipe=build_career_recipe(), inputs=_inputs(REQUEST))
        assert result.delivery is not None
        return CareerPlanProjection.model_validate(result.delivery.payload["projection"])
    finally:
        database.close()


@pytest.mark.parametrize("consumed_recovery", [None, False, True])
def test_nonempty_but_unusable_initial_queries_trigger_bounded_recovery(
    tmp_path: Path, consumed_recovery: bool | None,
) -> None:
    """用户这轮虽有搜索结果，实际详情全失败；不能直接交付链接并结束。"""
    class Ports(_Ports):
        def search_public(self, *args: Any, **kwargs: Any) -> CareerSearchOutcome:
            self.queries.append((kwargs["query"], kwargs["source"]))
            hit = CareerSearchHit(
                url=BLOCKED if len(self.queries) <= 3 else DETAIL,
                title="Agent开发实习生", snippet="", source=kwargs["source"],
            )
            return CareerSearchOutcome(
                record=query_record(query=kwargs["query"], status=ModuleQueryStatus.SUCCESS,
                                    evidence_count=1),
                hits=(hit,),
            )

        def read(self, url: str, **kwargs: Any) -> Any:
            self.reads.append(url)
            return reader.read(url, **kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        body = HTML if str(request.url) == DETAIL else "<title>请稍候 - BOSS直聘</title>"
        return httpx.Response(200, text=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        reader = HttpJobPageReader(client=http)
        ports = Ports()
        projection = _run(tmp_path, ports, consumed_recovery=consumed_recovery)
    if consumed_recovery:
        assert projection.status is CareerPlanStatus.LINKS_ONLY
        assert len(ports.queries) == 3, "整轮已使用补证额度时不得额外补搜"
        return
    assert projection.status is CareerPlanStatus.SUCCESS
    assert [sample.url for sample in projection.samples] == [DETAIL]
    assert len(ports.queries) == 4, "补搜取到有效样本后立即结束，不继续无边界尝试"
    assert len(projection.plan) == 4
    assert [p.query for p in projection.plan] == [q.query for q in projection.queries]
    assert "Agent" in projection.plan[-1].query


def test_recovery_never_exceeds_twenty_candidate_verifications(tmp_path: Path) -> None:
    class Ports(_Ports):
        def search_public(self, *args: Any, **kwargs: Any) -> CareerSearchOutcome:
            self.queries.append((kwargs["query"], kwargs["source"]))
            hits = tuple(CareerSearchHit(
                url=f"https://careers.example.com/campus/job/{len(self.queries) * 10 + i}",
                title="Agent开发实习生", snippet="", source="campus",
                page_content=BODY.replace("工作地点：上海", ""), page_fetched_at=NOW,
            ) for i in range(5))
            return CareerSearchOutcome(
                record=query_record(query=kwargs["query"], status=ModuleQueryStatus.SUCCESS,
                                    evidence_count=len(hits)), hits=hits,
            )

    ports = Ports()
    projection = _run(tmp_path, ports)
    assert projection.samples == []
    assert len(projection.rejected) == 20
    assert len(ports.queries) == 4
    assert ports.reads == []


@pytest.mark.parametrize(
    "verification", [WebSearchVerification.VERIFIED, WebSearchVerification.STRUCTURED],
)
def test_only_actual_extracted_page_body_can_be_used(
    tmp_path: Path, verification: WebSearchVerification,
) -> None:
    """搜索摘要不能冒充详情；提供方已抓到的正文应通过同样的字段核验。"""
    class Search:
        def search(self, account_id: str, plan: Any, **kwargs: Any) -> WebSearchProjection:
            return WebSearchProjection(
                status=WebSearchStatus.SUCCESS, trigger_reason="公开岗位", query_summary=plan.query,
                searched_at=NOW,
                results=[WebSearchResult(
                    result_id="job", title="Agent开发实习生", site="zhipin.com", url=BLOCKED,
                    accessed_at=NOW, snippet="摘要宣称薪资100K，不能进入统计",
                    content_summary=BODY,
                    fetched_at=NOW if verification is WebSearchVerification.VERIFIED else None,
                    verification=verification,
                )],
            )

    class Ports(_Ports):
        def search_public(self, *args: Any, **kwargs: Any) -> CareerSearchOutcome:
            return adapter.search_public(*args, **kwargs)

        def read(self, url: str, **kwargs: Any) -> Any:
            self.reads.append(url)
            return reader.read(url, **kwargs)

    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text="<title>请稍候 - BOSS直聘</title>")
    )
    with httpx.Client(transport=transport) as http:
        reader = HttpJobPageReader(client=http)
        adapter = WebSearchServiceAdapter(Search())
        ports = Ports()
        projection = _run(tmp_path, ports)
    if verification is WebSearchVerification.VERIFIED:
        assert projection.status is CareerPlanStatus.SUCCESS
        assert len(projection.samples) == 1
        assert projection.samples[0].salary_raw is None
        assert "RAG" in projection.samples[0].skills
        assert ports.reads == [], "已取得公开正文时不再重复撞同一访问验证页"
    else:
        assert projection.samples == []
        assert ports.reads == [BLOCKED]
