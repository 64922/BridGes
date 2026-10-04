"""职业规划配方的持久节点实现（改进工单 28／29）。

配方顺序（与 ``docs/workflow/daily-workflows.md`` 的职业规划段落一致）：

    career.parse → career.plan → career.collect → career.filter
      → career.analyze → career.background → career.gap → career.advise
      → career.verify

每个节点都在自己的局部事务里提交类型化产物、输入依赖/哈希、质量裁决与完成
收据；恢复先读完成收据，未完成或输入变化（例如任务城市条件改成杭州）的节点
及其下游按输入键重算，未变化的中间结果直接回填，旧统计不会被当成当前数据复用。

- ``career.parse``：保留岗位原词/职责意图/阶段/城市/经验条件；当前消息只修订
  条件（如「换成杭州」）时回退任务上下文补齐目标，任务条件版本进入输入键；
  同时确定性区分「只查岗位」与「个人准备」两条目的分支。
- ``career.collect``：逐来源检索、逐页公开读取；每次调用都进统一查询记录。
- ``career.filter``：逐条代码核对岗位/职责、过期、城市与经验条件；未知一律
  不进入相应统计（职责语义命中需两票以上且相邻族不占优）。
- ``career.analyze``：只按主样本归纳，薪资保留币种/计薪单位/发薪月数与缺失
  字段；样本不足停止总体推断。
- ``career.background``（工单 29）：仅个人规划分支读取当前陈述、允许使用的
  19 切片或登记简历片段；只查岗位时明确跳过，公开检索查询从不携带背景正文。
- ``career.gap``（工单 29）：岗位要求 × 用户证据逐项对照；没有依据一律
  待确认，未知不等于不足。
- ``career.advise``（工单 29）：岗位侧行动照旧；个人分支按明确待提升、已有
  依据、待确认排序，并把每天可用时间等约束翻成可执行说明。
- ``career.verify``：必需门核验「样本证据、统计口径、条件核对、个人证据
  两侧支持」后形成待交付投影；可选门如实记录未执行独立复核。
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from hashlib import sha256
from threading import Event
from typing import TYPE_CHECKING, Any

from bridges.career_plan.advising import build_advice
from bridges.career_plan.analyzing import SMALL_SAMPLE_MIN, analyze_samples
from bridges.career_plan.background import (
    CareerBackgroundProvider,
    build_statement_items,
    finalize_snapshot,
    unavailable_snapshot,
)
from bridges.career_plan.collecting import (
    JobPageReadResult,
    ParsedJobPage,
)
from bridges.career_plan.contracts import (
    AdjacentJobSuggestion,
    CareerAdviceItem,
    CareerAnalysis,
    CareerBackgroundSnapshot,
    CareerBranch,
    CareerCandidateLink,
    CareerCombinationRequirement,
    CareerGapItem,
    CareerPlanProjection,
    CareerPlanStatus,
    CareerQueryPlanItem,
    CareerRejectedSample,
    CareerRequestAnalysis,
    JobReadStatus,
    JobSample,
)
from bridges.career_plan.filtering import (
    JobCandidate,
    filter_candidates,
    site_label,
)
from bridges.career_plan.gap import (
    build_gaps,
    build_personal_advice,
    personal_boundary_notes,
)
from bridges.career_plan.lexicon import (
    SOURCE_LABELS,
    experience_matches,
    normalize_for_match,
)
from bridges.career_plan.parsing import parse_career_request
from bridges.career_plan.planning import build_plan
from bridges.career_plan.searching import CareerSearchHit, CareerSearchPort
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import (
    ArtifactTrust,
    InputDependency,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
    RecoveryPolicy,
)
from bridges.kernel.registry import RecipeRegistry

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
    from bridges.chat.task_materials import ModuleTaskContext

#: 配方节点名（进度事件、失败定位与产物身份；沿用历史对外标签）。
NODE_PARSE = "career.parse"
NODE_PLAN = "career.plan"
NODE_COLLECT = "career.collect"
NODE_FILTER = "career.filter"
NODE_ANALYZE = "career.analyze"
NODE_BACKGROUND = "career.background"
NODE_GAP = "career.gap"
NODE_ADVISE = "career.advise"
NODE_VERIFY = "career.verify"

CAREER_RECIPE_ID = "career-job-sample"
CAREER_RECIPE_VERSION = "career-job-sample-recipe-v3"

#: 节点的用户可读中文名（父图失败信息按此标注真实失败位置）。
CAREER_NODE_LABELS: dict[str, str] = {
    NODE_PARSE: "理解求职目标",
    NODE_PLAN: "制定检索计划",
    NODE_COLLECT: "读取公开岗位",
    NODE_FILTER: "筛选匹配岗位",
    NODE_ANALYZE: "归纳技能与薪资",
    NODE_BACKGROUND: "编译个人背景",
    NODE_GAP: "对照个人差距",
    NODE_ADVISE: "编排优先行动",
    NODE_VERIFY: "核验样本证据",
}

#: 已登记的确定性能力与版本（代码拒绝未登记能力）。
CAREER_CAPABILITY_VERSIONS: dict[str, str] = {
    "career.parse_request": "career-parse-v4",
    "career.plan_query": "career-plan-v2",
    "career.collect_jobs": "career-collect-v2",
    "career.filter_jobs": "career-filter-v3",
    "career.analyze_jobs": "career-analyze-v4",
    "career.load_background": "career-background-v2",
    "career.match_gap": "career-gap-v2",
    "career.advise_actions": "career-advise-v3",
    "career.verify_delivery": "career-verify-v3",
}

#: 配方的必要门、可选门（登记集合；代码拒绝未登记质量门）。
CAREER_GATES: frozenset[str] = frozenset(
    {
        "career.sample_evidence",
        "career.stats_caliber",
        "career.conditions_hold",
        "career.personal_evidence",
        "career.independent_review",
        "career.personal_review",
    }
)

#: 各阶段的墙钟预算：检索（三条来源查询共用）、逐页读取。
SEARCH_DEADLINE_SECONDS = 30.0
READ_DEADLINE_SECONDS = 20.0

#: 每条来源查询最多读取的岗位页数（每个页面一次真实公开读取）。
READS_PER_SOURCE = 3

#: 记为失败的查询状态（检索成功的空结果不算失败，它有自己的终态）。
FAILED_QUERY_STATUSES: frozenset[ModuleQueryStatus] = frozenset(
    {
        ModuleQueryStatus.ERROR,
        ModuleQueryStatus.TIMEOUT,
        ModuleQueryStatus.CANCELLED,
        ModuleQueryStatus.RATE_LIMITED,
    }
)

#: 证据边界里固定说明（每条都对应真实实现约束）。
BOUNDARY_NOT_MODEL = "本模块不调用模型生成内容，正文、统计与建议都来自实际读到的页面文本。"


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 预算视图：节点领取而非重建预算（工单 09 账本）
# ---------------------------------------------------------------------------


class CareerBudget:
    """职业规划外部调用的共享运行预算视图（缺失账本时按无预算模式运行）。"""

    def __init__(
        self,
        *,
        ledger: RunBudgetLedgerRepository,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def deadline_seconds(self, now: datetime | None = None) -> float:
        return max(0.5, self.remaining_work_ms(now) / 1000.0)

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return self._ledger.register_external_call(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=datetime.now(UTC),
        )

    def release_external(self, call_key: str, *, outcome_code: str) -> None:
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# 产物序列化（检查点与审计只保存 JSON 可序列化载荷）
# ---------------------------------------------------------------------------


def _read_dict(read: JobPageReadResult) -> dict[str, Any]:
    payload = asdict(read)
    payload["status"] = read.status.value
    payload["retrieved_at"] = read.retrieved_at.isoformat()
    published = read.page.published_date
    payload["page"]["published_date"] = (
        published.isoformat() if published is not None else None
    )
    return payload


def _read_from_dict(payload: Mapping[str, Any]) -> JobPageReadResult:
    page_payload = dict(payload.get("page") or {})
    published_raw = page_payload.pop("published_date", None)
    page = ParsedJobPage(
        **page_payload,
        published_date=(
            date.fromisoformat(str(published_raw)) if published_raw else None
        ),
    )
    return JobPageReadResult(
        url=str(payload.get("url") or ""),
        status=JobReadStatus(str(payload.get("status") or JobReadStatus.ERROR.value)),
        page=page,
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
        retrieved_at=datetime.fromisoformat(str(payload.get("retrieved_at"))),
    )


def _candidate_dict(candidate: JobCandidate) -> dict[str, Any]:
    return {
        "url": candidate.url,
        "source": candidate.source,
        "label": candidate.label,
        "read": _read_dict(candidate.read),
    }


def _candidate_from_dict(payload: Mapping[str, Any]) -> JobCandidate:
    return JobCandidate(
        url=str(payload.get("url") or ""),
        source=str(payload.get("source") or ""),
        label=str(payload.get("label") or ""),
        read=_read_from_dict(payload.get("read") or {}),
    )


def _hit_dict(hit: CareerSearchHit) -> dict[str, Any]:
    return {
        "url": hit.url,
        "title": hit.title,
        "snippet": hit.snippet,
        "source": hit.source,
    }


# ---------------------------------------------------------------------------
# 质量门（结构化裁决；模型不能自行宣布通过）
# ---------------------------------------------------------------------------


def _sample_evidence_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：每个主样本都有真实详情与岗位/职责匹配依据。"""
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    samples = projection.get("samples") or []
    experience = projection.get("experience_hint")
    for sample in samples:
        url = str(sample.get("url") or "")
        if not url or not sample.get("title_evidence") or not sample.get("city_evidence"):
            return QualityGateResult(
                gate="career.sample_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_sample_evidence_missing",
                message="样本缺少岗位匹配或城市核对依据，本轮不交付。",
                detail={"url": url},
            )
        if str(sample.get("read_status") or "") not in {"read", "partial"}:
            return QualityGateResult(
                gate="career.sample_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_sample_unreadable",
                message="样本不是公开可读的岗位页内容，本轮不交付。",
                detail={"url": url, "read_status": sample.get("read_status")},
            )
        if str(sample.get("match_basis") or "title") == "duty" and not sample.get(
            "duty_evidence"
        ):
            return QualityGateResult(
                gate="career.sample_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_duty_evidence_missing",
                message="职责匹配的样本缺少命中的职责锚点，本轮不交付。",
                detail={"url": url},
            )
        if experience and not sample.get("experience_evidence"):
            return QualityGateResult(
                gate="career.sample_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_experience_evidence_missing",
                message="经验条件下的样本缺少逐条核对依据，本轮不交付。",
                detail={"url": url},
            )
    return QualityGateResult(
        gate="career.sample_evidence",
        verdict=QualityVerdict.PASS,
        detail={"samples": len(samples)},
    )


def _stats_caliber_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：统计口径与样本一致，薪资保留币种/单位/缺失字段。"""
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    samples = projection.get("samples") or []
    analysis = projection.get("analysis")
    if analysis is None:
        if samples:
            return QualityGateResult(
                gate="career.stats_caliber",
                verdict=QualityVerdict.BLOCKED,
                code="career_stats_missing",
                message="有样本却没有分析口径，本轮不交付。",
                detail={"samples": len(samples)},
            )
        return QualityGateResult(
            gate="career.stats_caliber",
            verdict=QualityVerdict.PASS,
            detail={"samples": 0},
        )
    sample_count = int(analysis.get("sample_count") or 0)
    if sample_count != len(samples):
        return QualityGateResult(
            gate="career.stats_caliber",
            verdict=QualityVerdict.BLOCKED,
            code="career_stats_sample_mismatch",
            message="分析口径的样本数与主样本不一致，本轮不交付。",
            detail={"analysis": sample_count, "samples": len(samples)},
        )
    expected_missing = sum(
        1 for sample in samples if not str(sample.get("salary_raw") or "").strip()
    )
    if int(analysis.get("missing_salary_count") or 0) != expected_missing:
        return QualityGateResult(
            gate="career.stats_caliber",
            verdict=QualityVerdict.BLOCKED,
            code="career_missing_salary_mismatch",
            message="缺失薪资字段的计数与主样本不一致，本轮不交付。",
            detail={"expected": expected_missing},
        )
    if bool(analysis.get("overall_inference_stopped")) != (
        sample_count < SMALL_SAMPLE_MIN
    ):
        return QualityGateResult(
            gate="career.stats_caliber",
            verdict=QualityVerdict.BLOCKED,
            code="career_inference_flag_mismatch",
            message="小样本停止推断的口径与样本量不一致，本轮不交付。",
            detail={"sample_count": sample_count},
        )
    for interval in analysis.get("salary_intervals") or []:
        if not interval.get("unit") or not interval.get("currency"):
            return QualityGateResult(
                gate="career.stats_caliber",
                verdict=QualityVerdict.BLOCKED,
                code="career_salary_caliber_invalid",
                message="薪资区间缺少币种或计薪单位，本轮不交付。",
                detail={"unit": interval.get("unit")},
            )
    return QualityGateResult(
        gate="career.stats_caliber",
        verdict=QualityVerdict.PASS,
        detail={
            "samples": len(samples),
            "intervals": len(analysis.get("salary_intervals") or []),
        },
    )


def _conditions_hold_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：城市与经验条件在样本上逐条核对成立。"""
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    samples = projection.get("samples") or []
    cities = [str(city) for city in projection.get("cities") or []]
    experience = projection.get("experience_hint")
    city_wanted = cities[0] if cities else None
    for sample in samples:
        sample_city = str(sample.get("city") or "").strip() or None
        if sample_city is None:
            return QualityGateResult(
                gate="career.conditions_hold",
                verdict=QualityVerdict.BLOCKED,
                code="career_city_unverified",
                message="样本实际城市无法核实，本轮不交付该样本的统计。",
                detail={"url": sample.get("url")},
            )
        if city_wanted is not None and (
            normalize_for_match(city_wanted) not in normalize_for_match(sample_city)
        ):
            return QualityGateResult(
                gate="career.conditions_hold",
                verdict=QualityVerdict.BLOCKED,
                code="career_city_condition_violated",
                message="样本城市与你要求的城市不一致，本轮不交付。",
                detail={"url": sample.get("url"), "city": sample_city},
            )
        if experience and not experience_matches(
            str(experience), sample.get("experience")
        ):
            return QualityGateResult(
                gate="career.conditions_hold",
                verdict=QualityVerdict.BLOCKED,
                code="career_experience_condition_violated",
                message="样本经验要求与你给的经验条件不一致，本轮不交付。",
                detail={"url": sample.get("url")},
            )
    return QualityGateResult(
        gate="career.conditions_hold",
        verdict=QualityVerdict.PASS,
        detail={"samples": len(samples), "city": city_wanted, "experience": experience},
    )


def _independent_review_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """可选门：本轮为确定性规则核验，如实记录未执行独立模型复核。"""
    del invocation, execution
    return QualityGateResult(
        gate="career.independent_review",
        verdict=QualityVerdict.PASS,
        detail={
            "executed": False,
            "reason": "本轮为确定性规则与页面原文核验，未执行独立的模型复核。",
        },
    )


def _personal_evidence_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门（个人分支）：差距结论必须有用户与岗位两侧可定位证据。

    只查岗位分支明确跳过；个人分支里非「待确认」的每条差距必须同时具备
    岗位要求原文与用户侧来源引用；只查岗位或没有个人结论时不拦截。
    """
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    if str(projection.get("branch") or CareerBranch.JOB_INTEL.value) != (
        CareerBranch.PERSONAL_PLANNING.value
    ):
        return QualityGateResult(
            gate="career.personal_evidence",
            verdict=QualityVerdict.PASS,
            detail={"skipped": True, "reason": "本轮只查岗位，未进入个人差距流程。"},
        )
    for gap in projection.get("gaps") or []:
        category = str(gap.get("category") or "")
        term = str(gap.get("term") or "")
        if category in {"has_evidence", "to_improve"} and (
            not gap.get("job_evidence")
            or not gap.get("background_evidence")
            or not gap.get("background_refs")
        ):
            return QualityGateResult(
                gate="career.personal_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_personal_evidence_missing",
                message="个人差距缺少用户或岗位两侧证据，本轮不交付该结论。",
                detail={"term": term, "category": category},
            )
        if category == "to_confirm" and "不等于不足" not in str(gap.get("note") or ""):
            return QualityGateResult(
                gate="career.personal_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_unknown_treated_as_weakness",
                message="待确认项缺少「未知不等于不足」的边界说明，本轮不交付。",
                detail={"term": term},
            )
    for advice in projection.get("personal_advices") or []:
        if bool(advice.get("inference")) is False and str(advice.get("kind") or "") in {
            "skill",
            "leverage",
            "pace",
        } and (not advice.get("basis") or not advice.get("background_basis")):
            return QualityGateResult(
                gate="career.personal_evidence",
                verdict=QualityVerdict.BLOCKED,
                code="career_personal_advice_evidence_missing",
                message="个人行动缺少直接依据，本轮不交付。",
                detail={"title": advice.get("title")},
            )
    return QualityGateResult(
        gate="career.personal_evidence",
        verdict=QualityVerdict.PASS,
        detail={"gaps": len(projection.get("gaps") or [])},
    )


def _personal_review_gate(
    invocation: NodeInvocation, execution: NodeExecution
) -> QualityGateResult:
    """必要门：独立重查原始证据；新的综合推断没有复核来源则阻塞。"""
    del invocation
    projection = execution.artifact.payload.get("projection") or {}
    if str(projection.get("branch") or CareerBranch.JOB_INTEL.value) != (
        CareerBranch.PERSONAL_PLANNING.value
    ):
        return QualityGateResult(
            gate="career.personal_review",
            verdict=QualityVerdict.PASS,
            detail={"executed": False, "reason": "本轮不涉及个人综合判断。"},
        )
    from bridges.career_plan.reviewing import review_personal_projection

    reason = review_personal_projection(CareerPlanProjection.model_validate(projection))
    if reason is not None:
        return QualityGateResult(
            gate="career.personal_review",
            verdict=QualityVerdict.BLOCKED,
            code="career_personal_review_blocked",
            message=reason,
            detail={"executed": True, "method": "independent_code_evidence_review"},
        )
    return QualityGateResult(
        gate="career.personal_review",
        verdict=QualityVerdict.PASS,
        detail={
            "executed": True,
            "method": "independent_code_evidence_review",
            "reason": (
                "独立代码核验已从原始样本与背景重查分类和有限行动策略；"
                "这不是模型复核，新的能力综合推断缺少独立来源时保持阻塞。"
            ),
        },
    )


#: 配方质量门处理器（与配方登记的门名一一对应；内核据此执行结构化裁决）。
CAREER_GATE_HANDLERS: dict[str, Any] = {
    "career.sample_evidence": _sample_evidence_gate,
    "career.stats_caliber": _stats_caliber_gate,
    "career.conditions_hold": _conditions_hold_gate,
    "career.personal_evidence": _personal_evidence_gate,
    "career.independent_review": _independent_review_gate,
    "career.personal_review": _personal_review_gate,
}


# ---------------------------------------------------------------------------
# 配方
# ---------------------------------------------------------------------------


def _parse_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "content": inputs.user_content,
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
        }
    )


def _plan_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.plan_query"],
        }
    )


def _collect_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.collect_jobs"],
        }
    )


def _filter_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "collect": inputs.artifacts[NODE_COLLECT].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.filter_jobs"],
        }
    )


def _analyze_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "filter": inputs.artifacts[NODE_FILTER].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.analyze_jobs"],
        }
    )


def _background_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "content": inputs.user_content,
            "wait": inputs.wait_identity,
            "prior": inputs.prior_digest,
            "capability": CAREER_CAPABILITY_VERSIONS["career.load_background"],
        }
    )


def _gap_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "filter": inputs.artifacts[NODE_FILTER].content_hash,
            "analyze": inputs.artifacts[NODE_ANALYZE].content_hash,
            "background": inputs.artifacts[NODE_BACKGROUND].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.match_gap"],
        }
    )


def _advise_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "filter": inputs.artifacts[NODE_FILTER].content_hash,
            "analyze": inputs.artifacts[NODE_ANALYZE].content_hash,
            "background": inputs.artifacts[NODE_BACKGROUND].content_hash,
            "gap": inputs.artifacts[NODE_GAP].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.advise_actions"],
        }
    )


def _verify_key(inputs: RecipeInputs) -> str:
    return _digest(
        {
            "parse": inputs.artifacts[NODE_PARSE].content_hash,
            "plan": inputs.artifacts[NODE_PLAN].content_hash,
            "collect": inputs.artifacts[NODE_COLLECT].content_hash,
            "filter": inputs.artifacts[NODE_FILTER].content_hash,
            "analyze": inputs.artifacts[NODE_ANALYZE].content_hash,
            "background": inputs.artifacts[NODE_BACKGROUND].content_hash,
            "gap": inputs.artifacts[NODE_GAP].content_hash,
            "advise": inputs.artifacts[NODE_ADVISE].content_hash,
            "capability": CAREER_CAPABILITY_VERSIONS["career.verify_delivery"],
        }
    )


def build_career_recipe(
    *, background_input_key: Callable[[RecipeInputs], str] | None = None
) -> RecipeDefinition:
    """构造并校验职业规划配方（必经顺序、依赖只指向前置节点）。"""
    return RecipeDefinition(
        recipe_id=CAREER_RECIPE_ID,
        recipe_version=CAREER_RECIPE_VERSION,
        nodes=(
            NodeSpec(
                name=NODE_PARSE,
                capability="career.parse_request",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.parse_request"],
                artifact_type="career.request_analysis",
                input_key=_parse_key,
                recovery=RecoveryPolicy.ASK_INPUT,
                description="保留岗位原词/职责意图/阶段/城市/经验条件；只在岗位不足时追问。",
            ),
            NodeSpec(
                name=NODE_PLAN,
                capability="career.plan_query",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.plan_query"],
                artifact_type="career.query_plan",
                input_key=_plan_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.BLOCK,
                description="生成三类来源的实际查询词与逐条筛选条件。",
            ),
            NodeSpec(
                name=NODE_COLLECT,
                capability="career.collect_jobs",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.collect_jobs"],
                artifact_type="career.collected_candidates",
                input_key=_collect_key,
                depends_on=(NODE_PLAN,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="逐来源检索并逐页公开读取；每次调用都留统一查询记录。",
            ),
            NodeSpec(
                name=NODE_FILTER,
                capability="career.filter_jobs",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.filter_jobs"],
                artifact_type="career.filter_result",
                input_key=_filter_key,
                depends_on=(NODE_PARSE, NODE_COLLECT),
                recovery=RecoveryPolicy.BLOCK,
                description="逐条核对岗位/职责、过期、城市与经验；未知不进入统计。",
            ),
            NodeSpec(
                name=NODE_ANALYZE,
                capability="career.analyze_jobs",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.analyze_jobs"],
                artifact_type="career.analysis",
                input_key=_analyze_key,
                depends_on=(NODE_PARSE, NODE_FILTER),
                recovery=RecoveryPolicy.BLOCK,
                description="只按主样本归纳技能与薪资；保留币种/月数/缺失字段。",
            ),
            NodeSpec(
                name=NODE_BACKGROUND,
                capability="career.load_background",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.load_background"],
                artifact_type="career.background_snapshot",
                input_key=background_input_key or _background_key,
                depends_on=(NODE_PARSE,),
                recovery=RecoveryPolicy.RETRY_NODE,
                description="仅个人分支读取当前陈述与允许的长期背景；只查岗位明确跳过。",
            ),
            NodeSpec(
                name=NODE_GAP,
                capability="career.match_gap",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.match_gap"],
                artifact_type="career.gap_analysis",
                input_key=_gap_key,
                depends_on=(NODE_PARSE, NODE_FILTER, NODE_ANALYZE, NODE_BACKGROUND),
                recovery=RecoveryPolicy.BLOCK,
                description="岗位要求与用户证据逐项对照；无依据一律待确认。",
            ),
            NodeSpec(
                name=NODE_ADVISE,
                capability="career.advise_actions",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.advise_actions"],
                artifact_type="career.advice_plan",
                input_key=_advise_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_FILTER,
                    NODE_ANALYZE,
                    NODE_BACKGROUND,
                    NODE_GAP,
                ),
                recovery=RecoveryPolicy.BLOCK,
                description="岗位行动照旧；个人分支按差距与约束编排优先行动。",
            ),
            NodeSpec(
                name=NODE_VERIFY,
                capability="career.verify_delivery",
                capability_version=CAREER_CAPABILITY_VERSIONS["career.verify_delivery"],
                artifact_type="career.delivery",
                input_key=_verify_key,
                depends_on=(
                    NODE_PARSE,
                    NODE_PLAN,
                    NODE_COLLECT,
                    NODE_FILTER,
                    NODE_ANALYZE,
                    NODE_BACKGROUND,
                    NODE_GAP,
                    NODE_ADVISE,
                ),
                required_gates=(
                    "career.sample_evidence",
                    "career.stats_caliber",
                    "career.conditions_hold",
                    "career.personal_evidence",
                    "career.personal_review",
                ),
                optional_gates=("career.independent_review",),
                recovery=RecoveryPolicy.BLOCK,
                description="核验样本证据、统计口径、条件核对与个人证据后形成待交付投影。",
            ),
        ),
    )


def career_recipe_registry(
    *, background_input_key: Callable[[RecipeInputs], str] | None = None
) -> RecipeRegistry:
    """登记职业规划能力、质量门与配方；非法定义在装配时即被拒绝。"""
    registry = RecipeRegistry(
        capabilities=CAREER_CAPABILITY_VERSIONS.keys(),
        gates=CAREER_GATES,
    )
    registry.register(build_career_recipe(background_input_key=background_input_key))
    return registry


# ---------------------------------------------------------------------------
# 投影组装（终态只由真实证据决定）
# ---------------------------------------------------------------------------


def _status(
    records: Sequence[ModuleQueryRecord],
    samples: list[JobSample],
    has_candidate_links: bool,
) -> CareerPlanStatus:
    """终态只由真实证据决定：有样本→成功；全是失败→错误；有未核实链接→仅链接。"""
    if samples:
        return CareerPlanStatus.SUCCESS
    if records and all(record.status in FAILED_QUERY_STATUSES for record in records):
        return CareerPlanStatus.ERROR
    if has_candidate_links:
        return CareerPlanStatus.LINKS_ONLY
    return CareerPlanStatus.EMPTY


def _empty_reason(analysis: CareerRequestAnalysis) -> str:
    job = analysis.job_title or (analysis.job_terms[0] if analysis.job_terms else "")
    city_part = f"，城市 {'、'.join(analysis.cities)}" if analysis.cities else ""
    return (
        f"本轮没有取得公开可读且匹配的「{job}」{city_part}岗位样本："
        "上面的检索计划与调用记录是实际发出的查询，未核实的候选链接已逐条列出。"
    )


def _evidence_boundary(
    analysis: CareerRequestAnalysis,
    records: Sequence[ModuleQueryRecord],
    *,
    unconfirmed_count: int,
    unread_count: int,
    samples: list[JobSample],
    rejected: list[CareerRejectedSample],
    reads_per_source: int,
) -> list[str]:
    """证据边界：只写本轮真实的取舍与缺口。"""
    notes = [
        "只把公开可读、岗位名或职责原文命中目标岗位、城市可核对的岗位纳入主样本；"
        "搜索摘要不构成岗位样本。",
    ]
    if unconfirmed_count:
        notes.append(
            f"另有 {unconfirmed_count} 个候选未通过详情与条件核验，只给出链接并标注"
            "缺失字段或读取失败原因；这些候选未纳入任何统计。"
        )
    if unread_count:
        notes.append(
            f"本轮每条来源最多读取 {reads_per_source} 个岗位页，"
            f"另有 {unread_count} 个候选没有读取。"
        )
    if analysis.adjacent_jobs:
        notes.append(
            "相邻岗位（"
            + "、".join(analysis.adjacent_jobs[:3])
            + "）单列建议，不并入技能与薪资统计。"
        )
    if analysis.experience_hint:
        unverified = sum(
            1 for item in rejected if item.kind == "experience_unverified"
        )
        notes.append(
            f"你提到的经验要求「{analysis.experience_hint}」已逐条代码核对："
            f"页面没有给出经验要求的候选不进入样本（{unverified} 条）。"
        )
    if not samples:
        notes.append("本轮没有可用岗位样本，因此没有给出技能、薪资或市场层面的结论。")
    if records and all(record.status in FAILED_QUERY_STATUSES for record in records):
        notes.append("本轮的来源检索全部失败，没有可用的岗位候选。")
    notes.append(BOUNDARY_NOT_MODEL)
    return notes


def _topic_of(analysis: CareerRequestAnalysis) -> str:
    job = analysis.job_title or " ".join(analysis.job_terms) or "未确定岗位"
    parts = [job]
    if analysis.cities:
        parts.append("、".join(analysis.cities))
    if analysis.stage:
        parts.append(analysis.stage)
    return " · ".join(parts)


def _error_record(
    records: Sequence[ModuleQueryRecord],
) -> ModuleQueryRecord | None:
    for record in records:
        if record.status in FAILED_QUERY_STATUSES:
            return record
    return None


def _candidate_links(
    unconfirmed: Sequence[CareerCandidateLink],
    unread_links: Sequence[Mapping[str, Any]],
) -> list[CareerCandidateLink]:
    links = list(unconfirmed)
    for item in unread_links:
        url = str(item.get("url") or "")
        source = str(item.get("source") or "")
        links.append(
            CareerCandidateLink(
                url=url,
                title=str(item.get("title") or "") or site_label(url),
                source=source,
                source_label=SOURCE_LABELS.get(source, source),
                note=str(item.get("note") or "未核实：本轮没有读取该岗位页。"),
            )
        )
    return links


def _build_projection(
    *,
    analysis: CareerRequestAnalysis,
    plan: Sequence[CareerQueryPlanItem],
    records: Sequence[ModuleQueryRecord],
    samples: list[JobSample],
    rejected: list[CareerRejectedSample],
    unconfirmed: Sequence[CareerCandidateLink],
    unread_links: Sequence[Mapping[str, Any]],
    report: CareerAnalysis | None,
    advices: list[CareerAdviceItem],
    adjacent: list[AdjacentJobSuggestion],
    reads_per_source: int,
    background: CareerBackgroundSnapshot | None = None,
    gaps: Sequence[CareerGapItem] = (),
    personal_advices: Sequence[CareerAdviceItem] = (),
    combination_requirements: Sequence[CareerCombinationRequirement] = (),
    follow_up_question: str | None = None,
    personal_boundary: Sequence[str] = (),
) -> CareerPlanProjection:
    links = _candidate_links(unconfirmed, unread_links)
    status = _status(records, samples, bool(links))
    error = _error_record(records)
    is_personal = analysis.branch is CareerBranch.PERSONAL_PLANNING
    return CareerPlanProjection(
        status=status,
        topic=_topic_of(analysis),
        original_request=analysis.original_request,
        job_terms=list(analysis.job_terms),
        family_title=analysis.family_title,
        stage=analysis.stage,
        graduation_year=analysis.graduation_year,
        cities=list(analysis.cities),
        constraints=list(analysis.constraints),
        experience_hint=analysis.experience_hint,
        branch=analysis.branch,
        plan=list(plan),
        queries=list(records),
        samples=samples,
        candidate_links=links,
        rejected=rejected,
        analysis=report if samples else None,
        advices=advices,
        background=background if is_personal else None,
        gaps=list(gaps) if is_personal else [],
        personal_advices=list(personal_advices) if is_personal else [],
        combination_requirements=(
            list(combination_requirements) if is_personal else []
        ),
        follow_up_question=follow_up_question if is_personal else None,
        personal_boundary=list(personal_boundary) if is_personal else [],
        adjacent_suggestions=adjacent,
        evidence_boundary=(
            _evidence_boundary(
                analysis,
                records,
                unconfirmed_count=len(unconfirmed),
                unread_count=len(unread_links),
                samples=samples,
                rejected=rejected,
                reads_per_source=reads_per_source,
            )
            + list(personal_boundary if is_personal else ())
        ),
        empty_reason=(
            _empty_reason(analysis)
            if status in {CareerPlanStatus.LINKS_ONLY, CareerPlanStatus.EMPTY}
            else None
        ),
        retryable=bool(error and error.retryable),
        error_code=error.error_code if error is not None else None,
        error_message=error.error_message if error is not None else None,
    )


# ---------------------------------------------------------------------------
# 节点执行体
# ---------------------------------------------------------------------------


class CareerNodeFlow:
    """职业规划节点的确定性执行体（不调用模型，不自主任意委派）。"""

    def __init__(
        self,
        *,
        search: CareerSearchPort,
        reader: Any,
        clock: Callable[[], datetime] | None = None,
        module_context: ModuleTaskContext | None = None,
        pending_wait: Any = None,
        budget: CareerBudget | None = None,
        stop_event: Event | None = None,
        search_deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
        read_deadline_seconds: float = READ_DEADLINE_SECONDS,
        reads_per_source: int = READS_PER_SOURCE,
        background_provider: CareerBackgroundProvider | None = None,
    ) -> None:
        self._search = search
        self._reader = reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._module_context = module_context
        self._pending_wait = pending_wait
        self._budget = budget
        self._stop_event = stop_event
        self._search_deadline_seconds = search_deadline_seconds
        self._read_deadline_seconds = read_deadline_seconds
        self._reads_per_source = reads_per_source
        self._background_provider = background_provider
        self._prepared_background: CareerBackgroundSnapshot | None = None
        self._prepared_background_key: str | None = None

    @property
    def prior_digest(self) -> str | None:
        if self._module_context is not None and self._module_context.used_task_scope:
            return _digest(
                {
                    "goal": self._module_context.topic_hint or self._module_context.task_goal,
                    "conditions": [
                        (condition.condition_id, condition.kind, condition.text)
                        for condition in self._module_context.effective_conditions
                    ],
                }
            )
        return None

    def _task_texts(self) -> tuple[str, ...]:
        """任务已确认的目标与条件原话（目标在前；不含无关旧消息）。"""
        context = self._module_context
        if context is None or not context.used_task_scope:
            return ()
        texts: list[str] = []
        goal = (context.topic_hint or context.task_goal or "").strip()
        if goal:
            texts.append(goal)
        for condition in context.effective_conditions:
            text = condition.text.strip()
            if text and text not in texts:
                texts.append(text)
        return tuple(texts)

    # -- 节点执行体 -------------------------------------------------------

    def run_node(self, invocation: NodeInvocation) -> NodeExecution:
        handler = {
            NODE_PARSE: self._run_parse,
            NODE_PLAN: self._run_plan,
            NODE_COLLECT: self._run_collect,
            NODE_FILTER: self._run_filter,
            NODE_ANALYZE: self._run_analyze,
            NODE_BACKGROUND: self._run_background,
            NODE_GAP: self._run_gap,
            NODE_ADVISE: self._run_advise,
            NODE_VERIFY: self._run_verify,
        }[invocation.spec.name]
        try:
            return handler(invocation)
        except Exception as exc:  # noqa: BLE001 - 未预期异常按可重试失败收敛
            message = f"节点执行出现内部错误（{exc.__class__.__name__}）。"
            return self._failure_execution(
                invocation,
                payload={"error": {"type": exc.__class__.__name__}},
                node=invocation.spec.name,
                verdict=QualityVerdict.REPAIRABLE_FAILURE,
                status=NodeReceiptStatus.FAILED,
                code="career_node_error",
                message=message,
                retryable=True,
            )

    def _run_parse(self, invocation: NodeInvocation) -> NodeExecution:
        pending = (
            dict(self._pending_wait.context)
            if self._pending_wait is not None
            else None
        )
        analysis = parse_career_request(
            invocation.inputs.user_content,
            pending=pending,
            task_texts=self._task_texts(),
        )
        payload: dict[str, Any] = {"analysis": analysis.model_dump(mode="json")}
        if analysis.clarification is not None:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.DRAFT,
                    payload=payload,
                    unconfirmed=[analysis.clarification.question],
                ),
                verdict=QualityVerdict.NEED_INPUT,
                status=NodeReceiptStatus.NEEDS_INPUT,
                detail={
                    "code": "career_clarification",
                    "message": analysis.clarification.question,
                    "retryable": False,
                    "node": NODE_PARSE,
                },
                stop_recipe=True,
                recovery=RecoveryPolicy.ASK_INPUT,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.DRAFT,
                payload=payload,
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_plan(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = build_plan(analysis)
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"plan": [item.model_dump(mode="json") for item in plan]},
                read_scope="解析结果的条件推导（无外部读取）",
                requirement_coverage=[
                    {"requirement": "三类来源各一条实际查询词", "covered": True},
                    {"requirement": "筛选条件只列实际执行的判定", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
        )

    def _run_collect(self, invocation: NodeInvocation) -> NodeExecution:
        plan = [
            CareerQueryPlanItem.model_validate(item)
            for item in self._dep(invocation, NODE_PLAN).get("plan") or []
        ]
        records: list[ModuleQueryRecord] = []
        candidates: list[JobCandidate] = []
        unread: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        search_deadline = time.monotonic() + self._search_deadline_seconds
        read_deadline = time.monotonic() + self._read_deadline_seconds
        if self._budget is not None:
            budget_seconds = self._budget.deadline_seconds()
            search_deadline = min(search_deadline, time.monotonic() + budget_seconds)
            read_deadline = min(read_deadline, time.monotonic() + budget_seconds)
        budget_exhausted = False
        for item in plan:
            if self._stopped(search_deadline):
                break
            call_key = f"career.search:{item.source}:{_digest(item.query)[:16]}"
            if self._budget is not None and not self._budget.register_external(
                call_key, purpose="career.search"
            ):
                records.append(
                    ModuleQueryRecord(
                        source=item.source,
                        query=item.query,
                        status=ModuleQueryStatus.ERROR,
                        error_code="run_budget_exhausted",
                        error_message="本轮运行预算已用尽，未发起新的岗位检索。",
                        retryable=False,
                    )
                )
                budget_exhausted = True
                break
            outcome_code = "exception"
            try:
                outcome = self._search.search_public(
                    invocation.account_id,
                    query=item.query,
                    reason=f"职业规划：{item.source_label}按最小公开查询词检索",
                    source=item.source,
                    stop_event=self._stop_event,
                    deadline=search_deadline,
                )
                outcome_code = outcome.record.status.value
            finally:
                if self._budget is not None:
                    with contextlib.suppress(Exception):
                        self._budget.release_external(
                            call_key, outcome_code=outcome_code
                        )
            records.append(outcome.record)
            fresh = [hit for hit in outcome.hits if hit.url not in seen_urls]
            for index, hit in enumerate(fresh):
                if self._stopped(read_deadline):
                    for remaining in fresh[index:]:
                        seen_urls.add(remaining.url)
                        unread.append(
                            {
                                **_hit_dict(remaining),
                                "note": "未核实：本轮已停止读取，未读取该岗位页。",
                            }
                        )
                    budget_exhausted = True
                    break
                if index >= self._reads_per_source:
                    seen_urls.add(hit.url)
                    unread.append(
                        {
                            **_hit_dict(hit),
                            "note": "未核实：超过本轮读取上限，没有读取该岗位页。",
                        }
                    )
                    continue
                read_key = f"career.read:{_digest(hit.url)[:16]}"
                if self._budget is not None and not self._budget.register_external(
                    read_key, purpose="career.read"
                ):
                    for remaining in fresh[index:]:
                        seen_urls.add(remaining.url)
                        unread.append(
                            {
                                **_hit_dict(remaining),
                                "note": "未核实：本轮运行预算已用尽，没有读取该岗位页。",
                            }
                        )
                    budget_exhausted = True
                    break
                read_outcome_code = "exception"
                try:
                    read = self._reader.read(
                        hit.url,
                        stop_event=self._stop_event,
                        deadline=read_deadline,
                    )
                    read_outcome_code = read.status.value
                finally:
                    if self._budget is not None:
                        with contextlib.suppress(Exception):
                            self._budget.release_external(
                                read_key, outcome_code=read_outcome_code
                            )
                seen_urls.add(hit.url)
                candidates.append(
                    JobCandidate(
                        url=hit.url,
                        source=item.source,
                        label=hit.title or site_label(hit.url),
                        read=read,
                    )
                )
            if budget_exhausted:
                break
        read_scope = (
            f"本轮实际读取 {len(candidates)} 个岗位页"
            f"（每条来源最多 {self._reads_per_source} 个；另有 {len(unread)} 个候选未读取）"
        )
        if records and all(record.status in FAILED_QUERY_STATUSES for record in records):
            error = _error_record(records)
            assert error is not None
            retryable = any(record.retryable for record in records)
            return self._failure_execution(
                invocation,
                payload={
                    "records": [record.model_dump(mode="json") for record in records],
                    "candidates": [_candidate_dict(candidate) for candidate in candidates],
                    "unread_links": unread,
                },
                node=NODE_COLLECT,
                verdict=(
                    QualityVerdict.REPAIRABLE_FAILURE if retryable else QualityVerdict.BLOCKED
                ),
                status=NodeReceiptStatus.FAILED,
                code=error.error_code or "career_search_failed",
                message=(
                    f"{CAREER_NODE_LABELS[NODE_COLLECT]}："
                    f"{error.error_message or '本轮的来源检索全部失败，请稍后重试。'}"
                ),
                retryable=retryable,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "records": [
                        record.model_dump(mode="json") for record in records
                    ],
                    "candidates": [
                        _candidate_dict(candidate) for candidate in candidates
                    ],
                    "unread_links": unread,
                },
                read_scope=read_scope,
                requirement_coverage=[
                    {"requirement": "每次外部调用都有统一查询记录", "covered": True},
                    {"requirement": "每页读取都有真实结果分类", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"candidates": len(candidates), "unread": len(unread)},
        )

    def _run_filter(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        collect_payload = self._dep(invocation, NODE_COLLECT)
        candidates = [
            _candidate_from_dict(item) for item in collect_payload.get("candidates") or []
        ]
        outcome = filter_candidates(
            candidates, analysis, reference=self._clock()
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "samples": [
                        sample.model_dump(mode="json") for sample in outcome.samples
                    ],
                    "rejected": [
                        item.model_dump(mode="json") for item in outcome.rejected
                    ],
                    "unconfirmed": [
                        item.model_dump(mode="json") for item in outcome.unconfirmed
                    ],
                    "adjacent_counts": dict(outcome.adjacent_counts),
                    "experience_unverified_count": outcome.experience_unverified_count,
                },
                requirement_coverage=[
                    {"requirement": "相邻岗位不混入主样本", "covered": True},
                    {"requirement": "未知城市/经验不进入相应统计", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={
                "samples": len(outcome.samples),
                "rejected": len(outcome.rejected),
            },
        )

    def _run_analyze(self, invocation: NodeInvocation) -> NodeExecution:
        filter_payload = self._dep(invocation, NODE_FILTER)
        samples = [
            JobSample.model_validate(item) for item in filter_payload.get("samples") or []
        ]
        report = analyze_samples(
            samples,
            experience_unverified_count=int(
                filter_payload.get("experience_unverified_count") or 0
            ),
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={"analysis": report.model_dump(mode="json")},
                read_scope="只基于主样本页面原文归纳（无外部读取）",
                requirement_coverage=[
                    {"requirement": "薪资按币种与计薪单位分别归并", "covered": True},
                    {"requirement": "小样本停止总体推断", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"sample_count": report.sample_count},
        )

    def background_input_key(self, inputs: RecipeInputs) -> str:
        """复用收据前核验背景；当前执行只加载一次，时钟不改变内容键。"""
        analysis = CareerRequestAnalysis.model_validate(
            inputs.artifacts[NODE_PARSE].payload["analysis"]
        )
        if analysis.branch is not CareerBranch.PERSONAL_PLANNING:
            self._prepared_background = None
            self._prepared_background_key = _background_key(inputs)
            return self._prepared_background_key
        statement_items = build_statement_items(
            user_content=inputs.user_content,
            user_message_id=inputs.user_message_id,
            task_texts=self._task_texts(),
            task_ref=(
                f"task:{inputs.task_id}#v{inputs.task_version}"
                if inputs.task_id is not None else None
            ),
        )
        profile_snapshot: CareerBackgroundSnapshot | None = None
        if self._background_provider is not None:
            try:
                profile_snapshot = self._background_provider.load(
                    inputs.account_id,
                    run_id=inputs.run_id,
                    query=inputs.user_content or None,
                    current_user_message_id=inputs.user_message_id,
                    now=self._clock(),
                )
            except Exception:  # noqa: BLE001 - 背景来源失败不阻断公开岗位部分
                profile_snapshot = unavailable_snapshot(
                    "长期背景来源本轮不可用；个人部分只使用当前陈述。",
                    checked_at=self._clock(),
                )
        self._prepared_background = finalize_snapshot(
            analysis=analysis,
            statement_items=statement_items,
            profile_snapshot=profile_snapshot,
        )
        self._prepared_background_key = _digest({
            "request": _background_key(inputs),
            "background": self._prepared_background.model_dump(
                mode="json", exclude={"checked_at"}
            ),
        })
        return self._prepared_background_key

    def _run_background(self, invocation: NodeInvocation) -> NodeExecution:
        """仅个人规划分支读取允许背景；只查岗位时明确跳过。"""
        analysis = self._analysis(invocation)
        if analysis.branch is not CareerBranch.PERSONAL_PLANNING:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload={
                        "background": None,
                        "skipped": True,
                        "reason": "本轮只查岗位，未进入个人背景流程。",
                    },
                    requirement_coverage=[
                        {"requirement": "只岗位请求不读取个人背景", "covered": True},
                    ],
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
                detail={"skipped": True},
            )
        snapshot = self._prepared_background
        if snapshot is None:
            self.background_input_key(invocation.inputs)
            snapshot = self._prepared_background
        assert snapshot is not None
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "background": snapshot.model_dump(mode="json"),
                    "skipped": False,
                },
                read_scope=(
                    "当前陈述与任务原话"
                    + ("、允许使用的已记住信息切片" if snapshot.used_profile else "")
                    + "（不含未采用正文）"
                ),
                requirement_coverage=[
                    {"requirement": "每次调用检查切片版本与来源", "covered": True},
                    {"requirement": "背景正文不进入公开检索", "covered": True},
                ],
                source_refs=[item.source_ref for item in snapshot.items],
                unconfirmed=(
                    [snapshot.unavailable_reason]
                    if snapshot.unavailable_reason
                    else []
                ),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={
                "items": len(snapshot.items),
                "used_profile": snapshot.used_profile,
                "time_budget_minutes": snapshot.time_budget_minutes,
            },
        )

    def _run_gap(self, invocation: NodeInvocation) -> NodeExecution:
        """岗位要求 × 用户证据逐项对照；未知保持待确认。"""
        analysis = self._analysis(invocation)
        background = self._background(invocation)
        if analysis.branch is not CareerBranch.PERSONAL_PLANNING or background is None:
            return NodeExecution(
                artifact=self._artifact(
                    invocation,
                    trust_state=ArtifactTrust.EVIDENCE_BOUND,
                    payload={"gaps": [], "skipped": True},
                    requirement_coverage=[
                        {"requirement": "只岗位请求不产生个人差距结论", "covered": True},
                    ],
                ),
                verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
                detail={"skipped": True},
            )
        report = self._report(invocation)
        samples = self._samples(invocation)
        gaps = build_gaps(
            analysis=analysis, report=report, samples=samples, background=background
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "gaps": [gap.model_dump(mode="json") for gap in gaps],
                    "skipped": False,
                    "insufficient": not any(gap.background_evidence for gap in gaps),
                },
                read_scope="只对照主样本要求原文与允许使用的背景正文（无模型调用）",
                requirement_coverage=[
                    {"requirement": "未知能力不判为不足", "covered": True},
                    {"requirement": "差距两侧证据可定位", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={
                "gaps": len(gaps),
                "confirmed": sum(
                    1 for gap in gaps if gap.background_evidence
                ),
            },
        )

    def _run_advise(self, invocation: NodeInvocation) -> NodeExecution:
        """岗位行动照旧；个人分支按差距与约束编排优先行动。"""
        analysis = self._analysis(invocation)
        filter_payload = self._dep(invocation, NODE_FILTER)
        samples = [
            JobSample.model_validate(item) for item in filter_payload.get("samples") or []
        ]
        report = self._report(invocation)
        is_personal = analysis.branch is CareerBranch.PERSONAL_PLANNING
        advices, adjacent = build_advice(
            analysis,
            report,
            samples,
            adjacent_counts=filter_payload.get("adjacent_counts") or {},
            personal=is_personal,
        )
        gaps = self._gaps(invocation)
        background = self._background(invocation)
        personal_advices: list[CareerAdviceItem] = []
        requirements: list[CareerCombinationRequirement] = []
        question: str | None = None
        if is_personal and report is not None and gaps:
            personal_advices, requirements, question = build_personal_advice(
                analysis=analysis,
                report=report,
                gaps=gaps,
                background=background,
            )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.EVIDENCE_BOUND,
                payload={
                    "advices": [item.model_dump(mode="json") for item in advices],
                    "adjacent": [item.model_dump(mode="json") for item in adjacent],
                    "personal_advices": [
                        item.model_dump(mode="json") for item in personal_advices
                    ],
                    "combination_requirements": [
                        item.model_dump(mode="json") for item in requirements
                    ],
                    "follow_up_question": question,
                },
                read_scope="只引用主样本要求原文与已采用背景（无外部调用）",
                requirement_coverage=[
                    {"requirement": "建议区分直接证据与推断", "covered": True},
                    {"requirement": "时间约束影响行动可行性", "covered": True},
                ],
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={
                "advices": len(advices),
                "personal_advices": len(personal_advices),
            },
        )

    def _run_verify(self, invocation: NodeInvocation) -> NodeExecution:
        analysis = self._analysis(invocation)
        plan = [
            CareerQueryPlanItem.model_validate(item)
            for item in self._dep(invocation, NODE_PLAN).get("plan") or []
        ]
        collect_payload = self._dep(invocation, NODE_COLLECT)
        records = [
            ModuleQueryRecord.model_validate(item)
            for item in collect_payload.get("records") or []
        ]
        unread_links = list(collect_payload.get("unread_links") or [])
        filter_payload = self._dep(invocation, NODE_FILTER)
        samples = [
            JobSample.model_validate(item) for item in filter_payload.get("samples") or []
        ]
        rejected = [
            CareerRejectedSample.model_validate(item)
            for item in filter_payload.get("rejected") or []
        ]
        unconfirmed = [
            CareerCandidateLink.model_validate(item)
            for item in filter_payload.get("unconfirmed") or []
        ]
        analyze_payload = self._dep(invocation, NODE_ANALYZE)
        report = (
            CareerAnalysis.model_validate(analyze_payload.get("analysis"))
            if samples and analyze_payload.get("analysis")
            else None
        )
        advise_payload = self._dep(invocation, NODE_ADVISE)
        advices = [
            CareerAdviceItem.model_validate(item)
            for item in advise_payload.get("advices") or []
        ]
        adjacent = [
            AdjacentJobSuggestion.model_validate(item)
            for item in advise_payload.get("adjacent") or []
        ]
        personal_advices = [
            CareerAdviceItem.model_validate(item)
            for item in advise_payload.get("personal_advices") or []
        ]
        requirements = [
            CareerCombinationRequirement.model_validate(item)
            for item in advise_payload.get("combination_requirements") or []
        ]
        background = self._background(invocation)
        gaps = self._gaps(invocation)
        boundary = personal_boundary_notes(
            branch_is_personal=analysis.branch is CareerBranch.PERSONAL_PLANNING,
            background=background,
            gaps=gaps,
        )
        projection = _build_projection(
            analysis=analysis,
            plan=plan,
            records=records,
            samples=samples,
            rejected=rejected,
            unconfirmed=unconfirmed,
            unread_links=unread_links,
            report=report,
            advices=advices,
            adjacent=adjacent,
            reads_per_source=self._reads_per_source,
            background=background,
            gaps=gaps,
            personal_advices=personal_advices,
            combination_requirements=requirements,
            follow_up_question=advise_payload.get("follow_up_question"),
            personal_boundary=boundary,
        )
        return NodeExecution(
            artifact=self._artifact(
                invocation,
                trust_state=ArtifactTrust.QUALIFIED,
                payload={"projection": projection.model_dump(mode="json")},
                source_refs=[sample.url for sample in samples],
                requirement_coverage=[
                    {"requirement": "每一条结论都有样本或查询依据", "covered": True},
                ],
                unconfirmed=list(projection.evidence_boundary),
            ),
            verdict=QualityVerdict.PASS,
            status=NodeReceiptStatus.COMPLETED,
            detail={"status": projection.status.value, "samples": len(samples)},
        )

    # -- 内部工具 ---------------------------------------------------------

    def _dep(self, invocation: NodeInvocation, node: str) -> dict[str, Any]:
        return dict(invocation.dependencies[node].payload)

    def _analysis(self, invocation: NodeInvocation) -> CareerRequestAnalysis:
        return CareerRequestAnalysis.model_validate(
            self._dep(invocation, NODE_PARSE)["analysis"]
        )

    def _samples(self, invocation: NodeInvocation) -> list[JobSample]:
        filter_payload = self._dep(invocation, NODE_FILTER)
        return [
            JobSample.model_validate(item)
            for item in filter_payload.get("samples") or []
        ]

    def _report(self, invocation: NodeInvocation) -> CareerAnalysis | None:
        analyze_payload = self._dep(invocation, NODE_ANALYZE)
        if not analyze_payload.get("analysis"):
            return None
        if not self._samples(invocation):
            return None
        return CareerAnalysis.model_validate(analyze_payload["analysis"])

    def _background(
        self, invocation: NodeInvocation
    ) -> CareerBackgroundSnapshot | None:
        payload = self._dep(invocation, NODE_BACKGROUND).get("background")
        if not isinstance(payload, Mapping):
            return None
        return CareerBackgroundSnapshot.model_validate(dict(payload))

    def _gaps(self, invocation: NodeInvocation) -> list[CareerGapItem]:
        payload = self._dep(invocation, NODE_GAP)
        return [
            CareerGapItem.model_validate(item) for item in payload.get("gaps") or []
        ]

    def _artifact(
        self,
        invocation: NodeInvocation,
        *,
        trust_state: ArtifactTrust,
        payload: dict[str, Any],
        source_refs: Sequence[str] = (),
        read_scope: str = "",
        requirement_coverage: Sequence[Mapping[str, Any]] = (),
        unconfirmed: Sequence[str] = (),
        error: dict[str, Any] | None = None,
    ) -> NodeArtifact:
        inputs = invocation.inputs
        dependencies = tuple(
            InputDependency(
                node=name,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
            for name, artifact in invocation.dependencies.items()
        )
        return NodeArtifact.build(
            account_id=inputs.account_id,
            conversation_id=inputs.conversation_id,
            run_id=inputs.run_id,
            task_id=inputs.task_id,
            task_version=inputs.task_version,
            recipe_id=CAREER_RECIPE_ID,
            recipe_version=CAREER_RECIPE_VERSION,
            node=invocation.spec.name,
            artifact_type=invocation.spec.artifact_type,
            capability_version=invocation.spec.capability_version,
            trust_state=trust_state,
            input_key=(
                self._prepared_background_key
                if invocation.spec.name == NODE_BACKGROUND and self._prepared_background_key
                else invocation.spec.input_key(inputs)
            ),
            input_deps=dependencies,
            source_refs=tuple(source_refs),
            read_scope=read_scope,
            requirement_coverage=tuple(dict(item) for item in requirement_coverage),
            unconfirmed=tuple(unconfirmed),
            error=error,
            payload=payload,
            now=self._clock(),
        )

    def _failure_execution(
        self,
        invocation: NodeInvocation,
        *,
        payload: dict[str, Any],
        node: str,
        verdict: QualityVerdict,
        status: NodeReceiptStatus,
        code: str,
        message: str,
        retryable: bool,
    ) -> NodeExecution:
        artifact = self._artifact(
            invocation,
            trust_state=ArtifactTrust.INVALIDATED,
            payload=payload,
            error={"code": code, "message": message, "retryable": retryable},
        )
        return NodeExecution(
            artifact=artifact,
            verdict=verdict,
            status=status,
            detail={
                "code": code,
                "message": message,
                "retryable": retryable,
                "node": node,
            },
            stop_recipe=True,
            recovery=invocation.spec.recovery,
        )

    def _stopped(self, deadline: float) -> bool:
        return (
            bool(self._stop_event is not None and self._stop_event.is_set())
            or time.monotonic() >= deadline
        )


__all__ = [
    "BOUNDARY_NOT_MODEL",
    "CAREER_CAPABILITY_VERSIONS",
    "CAREER_GATE_HANDLERS",
    "CAREER_GATES",
    "CAREER_NODE_LABELS",
    "CAREER_RECIPE_ID",
    "CAREER_RECIPE_VERSION",
    "FAILED_QUERY_STATUSES",
    "NODE_ADVISE",
    "NODE_ANALYZE",
    "NODE_BACKGROUND",
    "NODE_COLLECT",
    "NODE_FILTER",
    "NODE_GAP",
    "NODE_PARSE",
    "NODE_PLAN",
    "NODE_VERIFY",
    "READS_PER_SOURCE",
    "READ_DEADLINE_SECONDS",
    "SEARCH_DEADLINE_SECONDS",
    "CareerBudget",
    "CareerNodeFlow",
    "build_career_recipe",
    "career_recipe_registry",
]
