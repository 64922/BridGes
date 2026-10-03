"""工单 29 验收：个人规划分支的背景纪律、差距分类与公开边界。

覆盖五条验收里可确定性验证的部分：

- 没有背景依据的能力一律「待确认」（不等于不足），已确认差距必须两侧可定位；
- 只查岗位的请求不进入个人背景流程，背景来源失败也不阻断公开岗位部分；
- 每天可用时间约束进入行动可行性，当前陈述覆盖长期默认；
- 画像删除／失效不产生旧个人结论，公开节点不依赖背景产物；
- 公开检索查询不含背景正文，岗位结果不写回画像。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import CareerBackgroundLoader
from bridges.career_plan.analyzing import analyze_samples
from bridges.career_plan.background import (
    build_statement_items,
    finalize_snapshot,
    unavailable_snapshot,
)
from bridges.career_plan.contracts import (
    CareerBackgroundItem,
    CareerBackgroundSnapshot,
    CareerBackgroundSource,
    CareerBranch,
    CareerGapCategory,
    JobReadStatus,
    JobSample,
)
from bridges.career_plan.gap import (
    build_follow_up_question,
    build_gaps,
    build_personal_advice,
    personal_boundary_notes,
)
from bridges.career_plan.kernel import (
    NODE_ADVISE,
    NODE_ANALYZE,
    NODE_BACKGROUND,
    NODE_COLLECT,
    NODE_FILTER,
    NODE_GAP,
    NODE_VERIFY,
    _personal_evidence_gate,
    build_career_recipe,
)
from bridges.career_plan.parsing import (
    detect_personal_planning,
    detect_time_budget,
    parse_career_request,
)
from bridges.career_plan.service import CareerPlanService
from bridges.contracts.atomic_profile import (
    AtomicProfileFactRelation,
    AtomicProfileFactScope,
)
from bridges.contracts.profile_adoption import (
    AdoptedProfileItem,
    AdoptedProfileSlice,
    ProfileSlicePurpose,
    ProfileTaskKind,
)
from bridges.kernel.contracts import QualityVerdict
from tests.career_plan.test_career_plan_module_flow import (
    _FakeReader,
    _FakeSearchPort,
    _hit,
    _read_result,
    _run_and_read,
    _send,
    _SilentAdapter,
)
from tests.chat.test_chat_api import _create_conversation, _gateway_with, _register

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
ACCOUNT = "acc-29"
BOSS_URL = "https://www.zhipin.com/job_detail/a.html"

PERSONAL_REQUEST = (
    "我熟悉 Java，想确认一下我还需要补什么，目标岗位是 Java 后端实习，"
    "城市南昌，每天可以安排 30 分钟"
)
JOB_ONLY_REQUEST = (
    "帮我规划一下学习提升，但只看招聘信息：Java 后端开发，城市南昌，每天可以安排 30 分钟"
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _sample(*, skills: list[str], requirements: list[str]) -> JobSample:
    return JobSample(
        url=BOSS_URL,
        source="boss",
        source_label="BOSS直聘",
        title="Java后端开发工程师",
        company="某某科技",
        city="南昌",
        salary_raw="15-25K",
        requirements=list(requirements),
        skills=list(skills),
        title_evidence="岗位名命中目标岗位。",
        city_evidence="页面城市与要求一致。",
        retrieved_at=NOW,
        read_status=JobReadStatus.READ,
    )


def _profile_item(
    text: str,
    *,
    ref: str = "profile-1",
    relation: str = "learning",
    applicable_to: list[str] | None = None,
) -> CareerBackgroundItem:
    return CareerBackgroundItem(
        source=CareerBackgroundSource.PROFILE,
        text=text,
        source_ref=ref,
        relation=relation,
        version=1,
        applicable_to=list(applicable_to or []),
    )


def _background(
    *items: CareerBackgroundItem,
    budget: int | None = None,
    budget_source: str | None = None,
) -> CareerBackgroundSnapshot:
    return CareerBackgroundSnapshot(
        items=list(items),
        used_profile=bool(items),
        time_budget_minutes=budget,
        time_budget_source=budget_source,
        checked_at=NOW,
    )


def _adopted_item(
    text: str,
    *,
    item_id: str = "p1",
    relation: AtomicProfileFactRelation = AtomicProfileFactRelation.LEARNING,
    applicable_to: tuple[str, ...] = (),
) -> AdoptedProfileItem:
    return AdoptedProfileItem(
        profile_item_id=item_id,
        version=1,
        fact_text=text,
        relation=relation,
        scope=AtomicProfileFactScope.LONG_TERM,
        source_authority="user",
        applicable_to=applicable_to,
        adoption_reason="测试采用",
    )


def _adopted_slice(
    *items: AdoptedProfileItem, owner_account_id: str = ACCOUNT
) -> AdoptedProfileSlice:
    return AdoptedProfileSlice(
        slice_id="slice-29",
        owner_account_id=owner_account_id,
        run_id="run-29",
        purpose=ProfileSlicePurpose(
            mode="companion", task_kind=ProfileTaskKind.GENERAL, module_id="career"
        ),
        adopted_items=tuple(items),
        snapshot_versions=tuple(
            (item.profile_item_id, item.version, "active") for item in items
        ),
        compiled_at=NOW,
    )


class _FakeAtomicProfile:
    """按脚本返回采用切片；记录用途参数，供断言按用途编译。"""

    def __init__(
        self,
        adopted: AdoptedProfileSlice | None = None,
        *,
        current: bool = True,
        fail: bool = False,
    ) -> None:
        self.adopted = adopted
        self.current = current
        self.fail = fail
        self.compile_calls: list[dict[str, Any]] = []

    def compile_adopted_slice(
        self,
        account_id: str,
        *,
        run_id: str,
        purpose: Any = None,
        current_user_message_id: str | None = None,
        now: datetime | None = None,
    ) -> AdoptedProfileSlice:
        del current_user_message_id, now
        self.compile_calls.append(
            {"account_id": account_id, "run_id": run_id, "purpose": purpose}
        )
        if self.fail:
            raise RuntimeError("画像仓库本轮不可用")
        assert self.adopted is not None
        return self.adopted

    def is_adopted_slice_current(
        self, account_id: str, adopted: AdoptedProfileSlice, *, now: datetime | None = None
    ) -> bool:
        del account_id, adopted, now
        return self.current


class _RecordingProvider:
    """记录背景加载调用的包装（真实 Loader 只在个人分支被调用）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def load(
        self,
        account_id: str,
        *,
        run_id: str,
        query: str | None,
        current_user_message_id: str | None = None,
        now: datetime | None = None,
    ) -> CareerBackgroundSnapshot:
        self.calls.append(
            {
                "account_id": account_id,
                "run_id": run_id,
                "query": query,
                "current_user_message_id": current_user_message_id,
            }
        )
        return self._inner.load(
            account_id,
            run_id=run_id,
            query=query,
            current_user_message_id=current_user_message_id,
            now=now,
        )


def _install_career(
    app: Any,
    *,
    port: _FakeSearchPort,
    reader: _FakeReader,
    provider: Any = None,
) -> CareerPlanService:
    service = CareerPlanService(
        search=port, reader=reader, background_provider=provider
    )
    app.state.career_plan_service = service
    app.state.chat_service._career_plan = service  # noqa: SLF001
    return service


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


# ---------------------------------------------------------------------------
# 分支判定与时间预算
# ---------------------------------------------------------------------------


def test_job_only_override_and_conservative_personal_detection() -> None:
    """显式「只看招聘信息」覆盖个人规划意图；普通求职请求仍是岗位分支。"""

    assert detect_personal_planning("帮我规划一下学习提升")
    assert not detect_personal_planning("帮我规划一下学习提升，但只看招聘信息")
    assert (
        parse_career_request(JOB_ONLY_REQUEST).branch is CareerBranch.JOB_INTEL
    )
    assert parse_career_request("我想找 Java 后端实习").branch is CareerBranch.JOB_INTEL
    assert parse_career_request(PERSONAL_REQUEST).branch is CareerBranch.PERSONAL_PLANNING


def test_daily_time_budget_is_parsed_and_not_converted_from_weeks() -> None:
    assert detect_time_budget("每天可以安排 30 分钟") == 30
    assert detect_time_budget("每天 1 小时") == 60
    assert detect_time_budget("每天两小时") == 120
    assert detect_time_budget("每天最多 45 分钟") == 45
    assert detect_time_budget("每周 5 小时") is None
    assert detect_time_budget("每天半时") is None


def test_statement_time_budget_overrides_profile_default() -> None:
    """当前陈述优先：本轮说了 30 分钟，画像的 45 分钟默认本轮不生效。"""

    analysis = parse_career_request(PERSONAL_REQUEST)
    assert analysis.time_budget_minutes == 30
    statement_items = build_statement_items(
        analysis, user_content=PERSONAL_REQUEST, user_message_id="m-1"
    )
    profile = _background(
        _profile_item(
            "我每天最多学习 45 分钟",
            ref="time-1",
            applicable_to=["plan_time_budget"],
        )
    )
    merged = finalize_snapshot(
        analysis=analysis, statement_items=statement_items, profile_snapshot=profile
    )
    assert merged.time_budget_minutes == 30
    assert merged.time_budget_source == "statement"

    resumed = parse_career_request("帮我规划一下学习提升，我想找 Java 后端实习")
    merged_default = finalize_snapshot(
        analysis=resumed,
        statement_items=build_statement_items(
            resumed,
            user_content="帮我规划一下学习提升，我想找 Java 后端实习",
            user_message_id="m-2",
        ),
        profile_snapshot=profile,
    )
    assert merged_default.time_budget_minutes == 45
    assert merged_default.time_budget_source == "profile"


# ---------------------------------------------------------------------------
# 差距分类与个人行动
# ---------------------------------------------------------------------------


def _gap_fixture() -> tuple[Any, Any, list[JobSample], list[Any]]:
    analysis = parse_career_request(
        "帮我规划一下学习提升，我想找 Java 后端实习，每天可以安排 30 分钟"
    )
    sample = _sample(
        skills=["Java", "Docker", "Redis"],
        requirements=[
            "熟悉 Java 与 Spring Boot",
            "熟悉 Docker 与容器化部署",
            "了解 Redis 与缓存",
        ],
    )
    report = analyze_samples([sample])
    background = _background(
        _profile_item("我熟悉 Java，也写过 Spring Boot 小项目", ref="p-java"),
        _profile_item("我不会 Docker，还没系统学过", ref="p-docker"),
        budget=30,
        budget_source="statement",
    )
    gaps = build_gaps(
        analysis=analysis, report=report, samples=[sample], background=background
    )
    return analysis, report, [sample], gaps


def test_unknown_is_to_confirm_and_confirmed_gaps_have_both_sides() -> None:
    _analysis, _report, _samples, gaps = _gap_fixture()
    by_term = {gap.term: gap for gap in gaps}

    java = by_term["Java"]
    assert java.category is CareerGapCategory.HAS_EVIDENCE
    assert java.job_evidence and java.background_evidence and java.background_refs
    assert java.inference is False

    docker = by_term["Docker"]
    assert docker.category is CareerGapCategory.TO_IMPROVE
    assert docker.job_evidence and docker.background_evidence and docker.background_refs
    assert "明确待提升" in docker.note

    redis = by_term["Redis"]
    assert redis.category is CareerGapCategory.TO_CONFIRM
    assert redis.background_evidence == []
    assert "不等于不足" in redis.note


def test_without_background_all_gaps_stay_to_confirm_with_boundary() -> None:
    statement = "每天可以安排 30 分钟，目标岗位是 Java 后端实习"
    analysis = parse_career_request(statement)
    sample = _sample(
        skills=["Java", "Docker"], requirements=["熟悉 Java", "了解 Docker"]
    )
    report = analyze_samples([sample])
    statement_items = build_statement_items(
        analysis, user_content=statement, user_message_id="m-1"
    )
    background = finalize_snapshot(
        analysis=analysis,
        statement_items=statement_items,
        profile_snapshot=unavailable_snapshot("本轮没有可用的长期背景来源。"),
    )
    gaps = build_gaps(
        analysis=analysis, report=report, samples=[sample], background=background
    )
    assert gaps and all(
        gap.category is CareerGapCategory.TO_CONFIRM for gap in gaps
    )
    assert all("不等于不足" in gap.note for gap in gaps)
    question = build_follow_up_question(gaps, background)
    assert question is not None and "待确认" in question and "不当作不足" in question
    notes = personal_boundary_notes(
        branch_is_personal=True, background=background, gaps=gaps
    )
    assert any("没有取得可定位的个人背景证据" in note for note in notes)
    assert any("没有把岗位结果写回个人画像" in note for note in notes)


def test_personal_advice_order_feasibility_and_combination_requirements() -> None:
    analysis, report, _samples, gaps = _gap_fixture()
    background = _background(
        CareerBackgroundItem(
            source=CareerBackgroundSource.USER_STATEMENT,
            text=analysis.original_request,
            source_ref="m-1",
        ),
        _profile_item("我熟悉 Java，也写过 Spring Boot 小项目", ref="p-java"),
        _profile_item("我不会 Docker，还没系统学过", ref="p-docker"),
        budget=30,
        budget_source="statement",
    )
    advices, requirements, question = build_personal_advice(
        analysis=analysis, report=report, gaps=gaps, background=background
    )
    assert [advice.kind for advice in advices] == [
        "skill",
        "leverage",
        "confirm",
        "pace",
    ]
    assert "Docker" in advices[0].title
    assert "30 分钟" in (advices[0].feasibility or "")
    pace = advices[-1]
    assert pace.kind == "pace" and "每天 30 分钟" in pace.title
    assert pace.background_basis, "时间约束也必须能回显来源原话"
    assert question is not None and "Redis" in question
    assert [item.kind for item in requirements] == ["resources"]
    assert "Docker" in requirements[0].skills


def test_personal_evidence_gate_blocks_unbacked_conclusions() -> None:
    """门只放行有两侧证据的结论；待确认必须有「不等于不足」边界。"""

    def execution(projection: dict[str, Any]) -> Any:
        return SimpleNamespace(
            artifact=SimpleNamespace(payload={"projection": projection})
        )

    job_only = _personal_evidence_gate(
        None,
        execution(
            {
                "branch": "job_intel",
                "gaps": [{"term": "Java", "category": "to_improve", "note": ""}],
            }
        ),
    )
    assert job_only.verdict is QualityVerdict.PASS
    assert job_only.detail["skipped"] is True

    missing_background = _personal_evidence_gate(
        None,
        execution(
            {
                "branch": "personal_planning",
                "gaps": [
                    {
                        "term": "Docker",
                        "category": "to_improve",
                        "job_evidence": ["熟悉 Docker"],
                        "background_evidence": [],
                        "background_refs": [],
                        "note": "明确待提升。",
                    }
                ],
            }
        ),
    )
    assert missing_background.verdict is QualityVerdict.BLOCKED
    assert missing_background.code == "career_personal_evidence_missing"

    weak_unknown = _personal_evidence_gate(
        None,
        execution(
            {
                "branch": "personal_planning",
                "gaps": [
                    {
                        "term": "Redis",
                        "category": "to_confirm",
                        "note": "没有背景。",
                    }
                ],
            }
        ),
    )
    assert weak_unknown.verdict is QualityVerdict.BLOCKED
    assert weak_unknown.code == "career_unknown_treated_as_weakness"

    unbacked_advice = _personal_evidence_gate(
        None,
        execution(
            {
                "branch": "personal_planning",
                "gaps": [],
                "personal_advices": [
                    {
                        "kind": "pace",
                        "title": "按每天 30 分钟推进",
                        "inference": False,
                        "basis": ["时间约束来自你明确给出的原话。"],
                        "background_basis": [],
                    }
                ],
            }
        ),
    )
    assert unbacked_advice.verdict is QualityVerdict.BLOCKED
    assert unbacked_advice.code == "career_personal_advice_evidence_missing"


def test_background_only_feeds_personal_nodes() -> None:
    """公开样本节点不依赖背景产物；只有差距与建议依赖。"""

    recipe = build_career_recipe()
    deps = {node.name: set(node.depends_on) for node in recipe.nodes}
    for node in (NODE_COLLECT, NODE_FILTER, NODE_ANALYZE):
        assert NODE_BACKGROUND not in deps[node]
    assert NODE_BACKGROUND in deps[NODE_GAP]
    assert NODE_BACKGROUND in deps[NODE_ADVISE]
    assert NODE_GAP in deps[NODE_VERIFY] and NODE_ADVISE in deps[NODE_VERIFY]


# ---------------------------------------------------------------------------
# 背景提供者：开关、版本与降级
# ---------------------------------------------------------------------------


def _loader_app(atomic: Any, *, usage_enabled: bool = True) -> Any:
    automatic = SimpleNamespace(
        is_profile_usage_enabled=lambda account_id: usage_enabled
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            atomic_profile_service=atomic, automatic_profile_service=automatic
        )
    )


def test_loader_reads_current_slice_by_purpose_query() -> None:
    adopted = _adopted_slice(_adopted_item("我熟悉 Java"))
    atomic = _FakeAtomicProfile(adopted)
    loader = CareerBackgroundLoader(_loader_app(atomic))

    snapshot = loader.load(
        ACCOUNT, run_id="run-1", query=PERSONAL_REQUEST, current_user_message_id="m-1"
    )
    assert snapshot.used_profile is True
    assert [item.text for item in snapshot.items] == ["我熟悉 Java"]
    purpose = atomic.compile_calls[0]["purpose"]
    assert purpose.mode == "companion" and purpose.module_id == "career"


def test_loader_respects_usage_switch_and_slice_revocation() -> None:
    adopted = _adopted_slice(_adopted_item("我熟悉 Java"))
    disabled = _FakeAtomicProfile(adopted)
    snapshot = CareerBackgroundLoader(
        _loader_app(disabled, usage_enabled=False)
    ).load(ACCOUNT, run_id="run-1", query=PERSONAL_REQUEST)
    assert snapshot.used_profile is False
    assert "使用已关闭" in (snapshot.unavailable_reason or "")
    assert disabled.compile_calls == [], "使用关闭时不得再编译或读取画像切片"

    revoked = _FakeAtomicProfile(adopted, current=False)
    snapshot = CareerBackgroundLoader(_loader_app(revoked)).load(
        ACCOUNT, run_id="run-1", query=PERSONAL_REQUEST
    )
    assert snapshot.used_profile is False
    assert "已变更" in (snapshot.unavailable_reason or "")
    assert snapshot.items == []


def test_loader_degrades_when_profile_source_fails() -> None:
    failing = _FakeAtomicProfile(fail=True)
    snapshot = CareerBackgroundLoader(_loader_app(failing)).load(
        ACCOUNT, run_id="run-1", query=PERSONAL_REQUEST
    )
    assert snapshot.used_profile is False
    assert snapshot.items == []
    assert "不可用" in (snapshot.unavailable_reason or "")


# ---------------------------------------------------------------------------
# 端到端：个人分支交付、公开边界与不写回
# ---------------------------------------------------------------------------


def test_personal_request_without_profile_delivers_job_part_and_boundary(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """背景不足仍交付真实岗位部分；未知全部待确认并给出一个关键问题。"""

    _register(client)
    atomic = _FakeAtomicProfile(_adopted_slice())
    sqlite_app.state.atomic_profile_service = atomic
    sqlite_app.state.automatic_profile_service.is_profile_usage_enabled = (  # type: ignore[method-assign]
        lambda account_id: True
    )
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    provider = _RecordingProvider(CareerBackgroundLoader(sqlite_app))
    _install_career(sqlite_app, port=port, reader=reader, provider=provider)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, PERSONAL_REQUEST, module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    career = assistant["career_plan"]
    assert career["branch"] == "personal_planning"
    assert len(career["samples"]) == 1
    assert career["background"]["used_profile"] is False
    # 个人分支只读取一次采用切片，没有任何画像写入口被调用
    assert len(provider.calls) == 1
    assert provider.calls[0]["query"] == PERSONAL_REQUEST
    gaps = career["gaps"]
    assert gaps
    for gap in gaps:
        if gap["category"] == "to_confirm":
            assert "不等于不足" in gap["note"]
            assert gap["background_evidence"] == []
        else:
            assert gap["job_evidence"] and gap["background_evidence"]
            assert gap["background_refs"]
    assert any(gap["category"] == "to_confirm" for gap in gaps)
    assert career["follow_up_question"]
    assert "待确认" in career["follow_up_question"]
    assert any("没有把岗位结果写回个人画像" in note for note in career["personal_boundary"])
    content = assistant["content"]
    assert "个人准备" in content and "不等于不足" in content
    assert "关键问题" in content


def test_job_only_request_never_touches_profile_source(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """显式只查岗位：不进入个人背景流程，背景服务一次都不被调用。"""

    _register(client)
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    provider = _RecordingProvider(CareerBackgroundLoader(sqlite_app))
    _install_career(sqlite_app, port=port, reader=reader, provider=provider)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, JOB_ONLY_REQUEST, module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert career["branch"] == "job_intel"
    assert len(career["samples"]) == 1
    assert career["background"] is None
    assert career["gaps"] == []
    assert provider.calls == [], "只查岗位的请求不得触碰个人背景来源"
    assert "个人准备" not in assistant["content"]


def test_current_profile_slice_feeds_gaps_without_leaking_to_public_queries(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """采用切片进入差距与行动；私人正文绝不进入公开检索查询与链接。"""

    _register(client)
    adopted = _adopted_slice(
        _adopted_item("我熟悉 Java，也写过 Spring Boot 小项目", item_id="p-java"),
        _adopted_item("我不会 Docker，还没系统学过", item_id="p-docker"),
        _adopted_item(
            "我每天最多学习 45 分钟",
            item_id="p-time",
            applicable_to=("plan_time_budget",),
        ),
    )
    atomic = _FakeAtomicProfile(adopted)
    sqlite_app.state.atomic_profile_service = atomic
    sqlite_app.state.automatic_profile_service.is_profile_usage_enabled = (  # type: ignore[method-assign]
        lambda account_id: True
    )
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    provider = _RecordingProvider(CareerBackgroundLoader(sqlite_app))
    _install_career(sqlite_app, port=port, reader=reader, provider=provider)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, PERSONAL_REQUEST, module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert career["background"]["used_profile"] is True
    assert len(provider.calls) == 1
    assert provider.calls[0]["query"] == PERSONAL_REQUEST
    gaps = {gap["term"]: gap for gap in career["gaps"]}
    assert gaps["Java"]["category"] == "has_evidence"
    assert gaps["Docker"]["category"] == "to_improve"
    for term in ("Java", "Docker"):
        assert gaps[term]["job_evidence"]
        assert gaps[term]["background_evidence"]
        assert gaps[term]["background_refs"]
    assert gaps["Redis"]["category"] == "to_confirm"
    # 当前陈述 30 分钟覆盖画像默认 45 分钟
    pace = [item for item in career["personal_advices"] if item["kind"] == "pace"]
    assert pace and "30 分钟" in pace[0]["title"]
    assert "45" not in pace[0]["title"]
    # 公开查询与链接不含私人正文
    private_texts = (
        "我熟悉 Java",
        "我不会 Docker",
        "45 分钟",
    )
    for query in port.query_texts:
        assert all(text not in query for text in private_texts)
    for link in career["candidate_links"]:
        assert all(text not in link["title"] for text in private_texts)
    assert "已记住信息" in assistant["content"]


def test_revoked_or_failed_profile_does_not_resurrect_old_conclusions(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """切片失效或来源失败：个人结论全部回到待确认，公开岗位部分照常交付。"""

    _register(client)
    adopted = _adopted_slice(
        _adopted_item("我不会 Docker，还没系统学过", item_id="p-docker"),
        _adopted_item("我熟悉 Spring Boot", item_id="p-spring"),
    )
    sqlite_app.state.atomic_profile_service = _FakeAtomicProfile(adopted, current=False)
    sqlite_app.state.automatic_profile_service.is_profile_usage_enabled = (  # type: ignore[method-assign]
        lambda account_id: True
    )
    port = _FakeSearchPort(
        per_source={"boss": [_hit(BOSS_URL, "boss", "Java后端开发工程师")]}
    )
    reader = _FakeReader({BOSS_URL: _read_result(url=BOSS_URL, status=JobReadStatus.READ)})
    provider = _RecordingProvider(CareerBackgroundLoader(sqlite_app))
    _install_career(sqlite_app, port=port, reader=reader, provider=provider)
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, PERSONAL_REQUEST, module_id="career")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    career = assistant["career_plan"]
    assert len(career["samples"]) == 1
    assert career["background"]["used_profile"] is False
    assert "已变更" in (career["background"]["unavailable_reason"] or "")
    assert len(provider.calls) == 1
    gaps = {gap["term"]: gap for gap in career["gaps"]}
    # 旧切片里的「不会 Docker」与「熟悉 Spring Boot」不得当成本轮依据。
    assert gaps["Docker"]["category"] == "to_confirm"
    assert gaps["Spring Boot"]["category"] == "to_confirm"
    for gap in gaps.values():
        assert "已记住信息" not in " ".join(gap["background_evidence"])
        assert all(not ref.startswith("p-") for ref in gap["background_refs"])
    assert "个人准备" in assistant["content"]

    # 来源直接失败：同样降级，绝不把旧切片当成本轮依据。
    _register(client, tag="2")
    sqlite_app.state.atomic_profile_service = _FakeAtomicProfile(fail=True)
    failing_conversation = _create_conversation(client)
    _send(client, failing_conversation, PERSONAL_REQUEST, module_id="career")
    failed_assistant = _run_and_read(
        sqlite_app,
        client,
        generation_helpers["drive"],
        failing_conversation,
    )
    failed_career = failed_assistant["career_plan"]
    assert failed_career["samples"]
    assert "不可用" in (failed_career["background"]["unavailable_reason"] or "")
    failed_gaps = {gap["term"]: gap for gap in failed_career["gaps"]}
    assert failed_gaps["Docker"]["category"] == "to_confirm"
    assert all(
        "已记住信息" not in " ".join(gap["background_evidence"])
        for gap in failed_gaps.values()
    )
