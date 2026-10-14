"""Issue 11：按需公开查证与来源失败降级。

学习模式默认直接教学基础概念、常规方法与推导；只有当本轮要给出的具体
结论确实依赖公开证据时才进入查证分支：

- 具体论文、时效事实、精确外部数据、争议结论与用户明确核实/来源要求
  分别识别（多信号、按学科无关的确定性规则），不靠单一触发词粗判；
- 数学计算等自主推导即使出现数值也不触发公开搜索；
- 查证只经既有登记来源（Tavily）与共享运行预算执行，外发查询只含本地
  脱敏后的必要公开概念；
- 查证产物冻结目的、来源、实际读取范围、结果状态、采用关系与版本；
  搜索成功不等于结论已核实，摘要不冒充全文；
- 用户来源限制、关闭知识库与禁止联网是硬边界：必要缺口保持未核实，
  不用其他工具绕过；
- 失败按证据能力降级交付：已支持的照常讲解，缺口如实说明；停止/失败
  轮次不提交查证记录，重试复用消息上已冻结的有效搜索投影。

正式诊断与长期掌握由后续切片拥有；本模块只交付本轮查证证据与其影响。
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from bridges.chat.repository import (
    ConversationRepository,
    GenerationRunRecord,
)
from bridges.contracts.study_source_verification import (
    PURPOSE_LABELS,
    STUDY_SOURCE_VERIFICATION_VERSION,
    StudySourceAdoption,
    StudySourceReadScope,
    StudySourceVerificationProjection,
    StudySourceVerificationStatus,
    StudyVerificationPurpose,
    StudyVerificationSource,
    adoption_for,
    read_scope_for,
)
from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service_plan import SearchPlan

_QUERY_LIMIT = 200
_SOURCE_LIMIT = 8
_GAP_LIMIT = 400
_STATUS_NOTE_LIMIT = 240

# ---------------------------------------------------------------------------
# 确定性查证需求判断
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StudyVerificationDemand:
    """本轮是否需要公开查证，以及为什么。"""

    needed: bool
    purposes: tuple[StudyVerificationPurpose, ...] = ()
    reasons: tuple[str, ...] = ()
    blocked_reason: str = ""
    computed_locally: bool = False


_EXPLICIT_REQUEST = re.compile(
    r"核实|查证|考证|事实核查|属实|真假|确凿|可靠吗|真的吗|是否真的|"
    r"出处|来源是什么|引用来源|给(?:我)?(?:个|些)?来源|给出来源|"
    r"(?:给出|附上|提供)(?:参考)?文献|参考文献|官方(?:数据|口径|规定|文件|通报)"
)
_PAPER = re.compile(
    r"(?:这篇|那篇|某篇|该篇|此篇|一篇)论文|"
    r"论文(?:的)?(?:结论|作者|发表|期刊|数据|结果|观点)|"
    r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b|"
    r"arxiv(?:\s*[:：]\s*|\s+)\d{4}\.\d{4,5}|"
    r"\bdoi\s*[:：]",
    re.IGNORECASE,
)
_FRESHNESS = re.compile(
    r"最新(?:的)?(?:进展|消息|数据|动态|结果|研究|政策|规定|标准|版本|情况|排名|榜单)|"
    r"(?:当前|目前|如今|现在|截至)\s*(?:的)?(?:数据|价格|汇率|股价|市值|人数|人口|指标|"
    r"税率|政策|规定|行情|进展|版本)|"
    r"今天的(?:新闻|消息|天气|股价|价格|汇率|数据)|"
    r"截至\s*20\d{2}(?:年)?|"
    r"20\d{2}\s*年(?:以来|至今|的最新)"
)
_DATA_ENTITY = (
    r"(?:GDP|CPI|PPI|人口|汇率|股价|市值|税率|失业率|就业率|录取率|分数线|"
    r"销量|产量|发电量|统计数据|财报数据)"
)
_DATA_SEEK = r"(?:是多少|多少|具体|精确|准确|最新|目前|现在|当前|截至|达到|超过|20\d{2})"
_PRECISE_DATA = re.compile(
    rf"{_DATA_ENTITY}[^。！？；;\n]{{0,16}}?{_DATA_SEEK}|"
    rf"(?:具体|精确|准确)[^。！？；;\n]{{0,8}}?{_DATA_ENTITY}|"
    rf"{_DATA_ENTITY}[^。！？；;\n]{{0,4}}?(?:是多少|多少)"
)
_CONTROVERSY = re.compile(
    r"争议|有争议|众说纷纭|各执一词|说法不一|观点不一|尚无定论|没有定论|"
    r"学界(?:观点|看法)?(?:不一致|存在分歧|仍有分歧)|(?:是否|是不是)(?:仍|还)"
)
_COMPUTATION = re.compile(
    r"(?:计算|求(?:解|导|积分|值)?|解(?:方程|不等式)|化简|验算|推导|证明|"
    r"算一下|算算|解一下|做一下这道题)"
)
_NO_NETWORK = re.compile(
    r"不要联网|不用联网|不能联网|禁止联网|别联网|不联网|不要上网|不用上网|"
    r"别上网|不许联网|不要搜索|不用搜索|不要搜|别搜|离线"
)


def plan_verification_demand(
    content: str, *, network_allowed: bool
) -> StudyVerificationDemand:
    """确定性判断本轮是否需要公开查证；数学计算不因出现数值而触发。"""

    text = " ".join(content.split())
    if not text:
        return StudyVerificationDemand(needed=False)
    purposes: list[StudyVerificationPurpose] = []
    reasons: list[str] = []
    if _EXPLICIT_REQUEST.search(text):
        purposes.append(StudyVerificationPurpose.EXPLICIT_REQUEST)
        reasons.append("你明确要求核实或给出可引用来源")
    if _PAPER.search(text):
        purposes.append(StudyVerificationPurpose.PAPER)
        reasons.append("涉及具体论文或文献结论")
    if _FRESHNESS.search(text):
        purposes.append(StudyVerificationPurpose.FRESHNESS)
        reasons.append("结论依赖最新或当前时效信息")
    if _PRECISE_DATA.search(text):
        purposes.append(StudyVerificationPurpose.PRECISE_DATA)
        reasons.append("需要精确的外部数据")
    if _CONTROVERSY.search(text):
        purposes.append(StudyVerificationPurpose.CONTROVERSY)
        reasons.append("该结论存在争议，需要分别呈现")
    needed = bool(purposes)
    computed_locally = False
    if not needed and _COMPUTATION.search(text):
        # 自主数学计算可使用已有计算能力；出现数值本身不触发公开搜索。
        computed_locally = True
    blocked_reason = ""
    if needed:
        if not network_allowed:
            blocked_reason = "用户已限制联网或只用本地材料"
        elif _NO_NETWORK.search(text):
            blocked_reason = "用户在本轮明确要求不联网"
    return StudyVerificationDemand(
        needed=needed,
        purposes=tuple(purposes),
        reasons=tuple(reasons),
        blocked_reason=blocked_reason,
        computed_locally=computed_locally,
    )


# ---------------------------------------------------------------------------
# 搜索计划与产物快照
# ---------------------------------------------------------------------------


def plan_study_verification_search(
    service: Any,
    demand: StudyVerificationDemand,
    original: str,
    public_query: str,
    mode: Any,
) -> SearchPlan:
    """原文只在本地裁决触发与审计；实际外发只用本地脱敏后的公开查询词。"""

    trigger = service.plan(original, mode)
    minimal = service.plan(public_query, mode, force=True)
    reason = "；".join(demand.reasons) or trigger.reason
    return SearchPlan(
        should_search=bool(minimal.query),
        query=minimal.query,
        reason=reason,
        queries=minimal.queries,
        provider=minimal.provider,
        provider_version=minimal.provider_version,
        rules_version=minimal.rules_version,
        query_hash=minimal.query_hash,
        original_query_hash=trigger.original_query_hash,
        freshness_window_seconds=trigger.freshness_window_seconds,
        deleted_categories=tuple(
            sorted(set(trigger.deleted_categories + minimal.deleted_categories))
        ),
    )


_STATUS_FROM_PROJECTION: dict[WebSearchStatus, StudySourceVerificationStatus] = {
    WebSearchStatus.SUCCESS: StudySourceVerificationStatus.SUCCESS,
    WebSearchStatus.PARTIAL: StudySourceVerificationStatus.PARTIAL,
    WebSearchStatus.EMPTY: StudySourceVerificationStatus.EMPTY,
    WebSearchStatus.EVIDENCE_INSUFFICIENT: StudySourceVerificationStatus.INSUFFICIENT,
    WebSearchStatus.FETCH_ERROR: StudySourceVerificationStatus.FETCH_ERROR,
    WebSearchStatus.SOURCE_CONFLICT: StudySourceVerificationStatus.CONFLICT,
    WebSearchStatus.PERMISSION: StudySourceVerificationStatus.UNCONFIGURED,
    WebSearchStatus.CANCELLED: StudySourceVerificationStatus.CANCELLED,
}

_STATUS_NOTES: dict[StudySourceVerificationStatus, str] = {
    StudySourceVerificationStatus.SUCCESS: "已取得可核实的公开来源；未覆盖的结论仍保持未核实。",
    StudySourceVerificationStatus.PARTIAL: (
        "部分来源抓取失败；已支持的来源照常交付，其余保持未核实。"
    ),
    StudySourceVerificationStatus.EMPTY: (
        "没有找到可核实的公开网页结果；可换一种问法或稍后显式重试。"
    ),
    StudySourceVerificationStatus.INSUFFICIENT: (
        "只取得搜索摘要或证据不足，未完成来源核验；可打开来源页或稍后重试。"
    ),
    StudySourceVerificationStatus.FETCH_ERROR: (
        "来源页面暂时无法抓取，本轮未完成联网核验；可稍后重试。"
    ),
    StudySourceVerificationStatus.CONFLICT: (
        "多个公开来源给出冲突信息，未形成单一结论；结论保持未核实。"
    ),
    StudySourceVerificationStatus.ERROR: (
        "公开查证暂时不可达，本轮未完成联网核实；可稍后显式重试。"
    ),
    StudySourceVerificationStatus.UNCONFIGURED: (
        "搜索凭据未配置或无效，本轮未联网核实；配置后可在设置中重试。"
    ),
    StudySourceVerificationStatus.BLOCKED: (
        "你限制了联网或要求只用本地材料，本轮未执行公开查证；解除限制后可再核实。"
    ),
    StudySourceVerificationStatus.CANCELLED: "本轮查证已取消，未完成联网核实；可在本轮重试。",
}

_GAP_NOTES: dict[StudySourceVerificationStatus, str] = {
    StudySourceVerificationStatus.EMPTY: "没有取得可核实的公开来源；相关结论保持未核实。",
    StudySourceVerificationStatus.INSUFFICIENT: (
        "只取得摘要级别内容，未完成来源核验；相关结论只能按摘要级别说明。"
    ),
    StudySourceVerificationStatus.FETCH_ERROR: "来源页面无法抓取，本轮未完成联网核验。",
    StudySourceVerificationStatus.CONFLICT: "多个公开来源冲突，未形成单一结论。",
    StudySourceVerificationStatus.ERROR: "公开查证暂时不可达，本轮未完成联网核实。",
    StudySourceVerificationStatus.UNCONFIGURED: "搜索凭据未配置或无效，本轮未联网核实。",
    StudySourceVerificationStatus.BLOCKED: "用户限制联网，本轮未执行公开查证。",
    StudySourceVerificationStatus.CANCELLED: "本轮查证已取消，未完成联网核实。",
    StudySourceVerificationStatus.PARTIAL: "部分来源抓取失败，其余来源照常交付。",
}


def status_note_for(status: StudySourceVerificationStatus) -> str:
    return _STATUS_NOTES.get(status, "公开查证未完成；相关结论保持未核实。")[
        :_STATUS_NOTE_LIMIT
    ]


def snapshot_status(
    projection: WebSearchProjection | None,
) -> StudySourceVerificationStatus:
    if projection is None:
        return StudySourceVerificationStatus.ERROR
    return _STATUS_FROM_PROJECTION.get(
        projection.status, StudySourceVerificationStatus.ERROR
    )


def _sources_from_projection(
    projection: WebSearchProjection | None,
) -> list[StudyVerificationSource]:
    if projection is None:
        return []
    sources: list[StudyVerificationSource] = []
    for result in projection.results[:_SOURCE_LIMIT]:
        sources.append(_source_from_result(result))
    return sources


def _source_from_result(result: WebSearchResult) -> StudyVerificationSource:
    verification = getattr(result.verification, "value", str(result.verification))
    scope = read_scope_for(verification)
    if (
        verification == "conflicting"
        and result.fetched_at is not None
        and result.content_summary
    ):
        # 冲突来源仍可能已抓取到页面内容，读取范围按实际抓取收紧，
        # 但采用关系保持排除（冲突不得作为确定结论依据）。
        scope = StudySourceReadScope.FULL_TEXT
    return StudyVerificationSource(
        source_id=result.result_id,
        title=result.title,
        site=result.site,
        url=result.url,
        read_scope=scope,
        adoption=adoption_for(verification),
        provider=result.provider,
        provider_version=result.provider_version,
        published_at=result.published_at,
        accessed_at=result.accessed_at,
        fetched_at=result.fetched_at,
    )


def adopted_source_ids(sources: list[StudyVerificationSource]) -> list[str]:
    """允许作为结论依据采用的来源编号（全文抓取或结构化结果）。"""

    return [
        source.source_id
        for source in sources
        if source.adoption is StudySourceAdoption.ADOPTED
    ]


def verification_incomplete(
    status: StudySourceVerificationStatus,
    sources: list[StudyVerificationSource],
) -> bool:
    """证据是否不足以支撑确定结论（含冲突、空命中、仅摘要与用户限制）。"""

    if status not in {
        StudySourceVerificationStatus.SUCCESS,
        StudySourceVerificationStatus.PARTIAL,
    }:
        return True
    return not adopted_source_ids(sources)


def build_verification_snapshot(
    *,
    demand: StudyVerificationDemand,
    plan: SearchPlan | None,
    projection: WebSearchProjection | None,
    assistant_message_id: str,
    status_override: StudySourceVerificationStatus | None = None,
    note_override: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """把本轮查证按证据能力冻结为可持久化快照（写入运行配置）。"""

    status = status_override or snapshot_status(projection)
    sources = [] if status_override is not None else _sources_from_projection(projection)
    adopted = adopted_source_ids(sources)
    conflict = status is StudySourceVerificationStatus.CONFLICT
    gap_note = note_override or _GAP_NOTES.get(status, "")
    query_summary = (
        plan.query
        if plan is not None
        else (
            projection.query_summary if projection is not None else ""
        )
    )
    if status in {
        StudySourceVerificationStatus.SUCCESS,
        StudySourceVerificationStatus.PARTIAL,
    } and not adopted:
        gap_note = _GAP_NOTES[StudySourceVerificationStatus.INSUFFICIENT]
    return {
        "contract_version": STUDY_SOURCE_VERIFICATION_VERSION,
        "purpose": (
            demand.purposes[0].value if demand.purposes else ""
        ),
        "reasons": list(demand.reasons),
        "status": status.value,
        "query_summary": query_summary[:_QUERY_LIMIT],
        "sources": [source.model_dump(mode="json") for source in sources],
        "adopted_source_ids": adopted,
        "has_conflicts": conflict,
        "gap_note": gap_note[:_GAP_LIMIT],
        "can_retry": status
        not in {
            StudySourceVerificationStatus.BLOCKED,
            StudySourceVerificationStatus.CANCELLED,
        },
        "assistant_message_id": assistant_message_id,
        "created_at": (now or datetime.now(UTC)).isoformat(),
    }


def snapshot_source_refs(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """从快照取冻结合同使用的来源版本引用（仅允许采用的来源）。"""

    if not isinstance(snapshot, Mapping):
        return []
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        return []
    adopted = {str(item) for item in snapshot.get("adopted_source_ids") or []}
    refs: list[dict[str, Any]] = []
    for item in sources:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in adopted:
            continue
        refs.append(
            {
                "source_id": source_id,
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "read_scope": str(item.get("read_scope") or "none"),
                "provider": str(item.get("provider") or ""),
                "provider_version": str(item.get("provider_version") or ""),
                "accessed_at": item.get("accessed_at"),
                "fetched_at": item.get("fetched_at"),
            }
        )
    return refs


def snapshot_incomplete(snapshot: Mapping[str, Any] | None) -> bool:
    """本轮查证是否没有形成可支撑确定结论的证据。"""

    if not isinstance(snapshot, Mapping) or not snapshot.get("purpose"):
        return False
    try:
        status = StudySourceVerificationStatus(str(snapshot.get("status") or ""))
    except ValueError:
        return True
    sources: list[StudyVerificationSource] = []
    for item in snapshot.get("sources") or []:
        try:
            sources.append(StudyVerificationSource.model_validate(item))
        except ValidationError:
            continue
    return verification_incomplete(status, sources)


def verification_directive(snapshot: Mapping[str, Any] | None) -> str:
    """把本轮查证结果作为导师生成的硬约束（不暴露私有查询上下文）。"""

    if not isinstance(snapshot, Mapping) or not snapshot.get("purpose"):
        return ""
    try:
        purpose = StudyVerificationPurpose(str(snapshot.get("purpose")))
    except ValueError:
        return ""
    try:
        status = StudySourceVerificationStatus(str(snapshot.get("status") or ""))
    except ValueError:
        status = StudySourceVerificationStatus.ERROR
    label = PURPOSE_LABELS.get(purpose, "需要公开核实")
    parts = [f"【本轮公开查证合同】触发需要：{label}；结果状态：{_STATUS_NOTES[status]}"]
    sources = snapshot.get("sources")
    if isinstance(sources, list) and sources:
        lines: list[str] = []
        adopted = {str(item) for item in snapshot.get("adopted_source_ids") or []}
        for item in sources[:6]:
            if not isinstance(item, Mapping):
                continue
            scope = str(item.get("read_scope") or "none")
            scope_label = {
                "full_text": "已抓取全文核验",
                "structured": "结构化结果",
                "abstract": "仅搜索摘要（未核验全文）",
                "none": "未能读取内容",
            }.get(scope, scope)
            mark = "可引用" if str(item.get("source_id")) in adopted else "不采用"
            lines.append(
                f"- [{mark}] {item.get('title') or ''}（{scope_label}）"
                f" {item.get('url') or ''}"
            )
        if lines:
            parts.append("实际取得的来源（只能引用这些来源）：\n" + "\n".join(lines))
    if snapshot.get("has_conflicts"):
        parts.append("来源存在冲突：分别说明各方说法，不合并为单一结论。")
    parts.append(
        "搜索成功不等于结论已核实：只能按来源的实际读取范围引用——"
        "仅摘要的来源只支持摘要级判断，不得声称读过全文；"
        "未取得支持的结论保持未核实，不得伪造引用或把模型记忆说成已取得来源。"
    )
    if snapshot_incomplete(snapshot):
        parts.append(
            "本轮证据不足以支持确定结论：依赖外部事实的内容只作未核实说明；"
            "不要提出依赖外部证据的正式练习，也不要给出这类题目的标准答案。"
        )
    gap = str(snapshot.get("gap_note") or "")
    if gap:
        parts.append(f"须如实保留的缺口：{gap}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 仓库：终态事务内提交、按消息投影、账户作用域生命周期
# ---------------------------------------------------------------------------


class StudySourceVerificationRepository:
    """按账户读取与提交学习查证记录；记录只追加，不重写历史含义。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def get_for_message(
        self, account_id: str, assistant_message_id: str
    ) -> StudySourceVerificationProjection | None:
        row = self._database.scoped(account_id).execute(
            "SELECT * FROM study_source_verifications"
            " WHERE account_id = ? AND assistant_message_id = ?",
            (account_id, assistant_message_id),
        ).fetchone()
        return _projection_from_row(row) if row is not None else None

    def list_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> list[StudySourceVerificationProjection]:
        rows = self._database.scoped(account_id).execute(
            "SELECT * FROM study_source_verifications"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at ASC, rowid ASC",
            (account_id, conversation_id),
        ).fetchall()
        return [
            projection
            for row in rows
            if (projection := _projection_from_row(row)) is not None
        ]

    def commit_turn_in_transaction(
        self,
        repository: ConversationRepository,
        run: GenerationRunRecord,
        assistant_message_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """成功终态内提交本轮查证记录；缺失/损坏快照不做任何写入。

        停止、失败与被取代的轮次不会进入本方法；同一助手消息重复提交
        由 (account_id, assistant_message_id) 唯一约束幂等兜底，绝不覆盖。
        """

        latest = repository.get_generation_run(run.account_id, run.run_id)
        config = (latest.config if latest is not None else run.config) or {}
        snapshot = config.get("study_source_verification")
        if not isinstance(snapshot, dict) or not snapshot.get("purpose"):
            return
        try:
            purpose = StudyVerificationPurpose(str(snapshot.get("purpose")))
        except ValueError:
            return
        try:
            status = StudySourceVerificationStatus(str(snapshot.get("status") or ""))
        except ValueError:
            status = StudySourceVerificationStatus.ERROR
        sources: list[StudyVerificationSource] = []
        for item in snapshot.get("sources") or []:
            try:
                sources.append(StudyVerificationSource.model_validate(item))
            except ValidationError:
                continue
        timestamp = now or _parse_time(str(snapshot.get("created_at") or ""))
        if timestamp is None:
            timestamp = datetime.now(UTC)
        self._database.scoped(run.account_id).execute(
            "INSERT INTO study_source_verifications"
            " (verification_id, account_id, conversation_id, run_id,"
            " assistant_message_id, purpose, status, query_summary, sources_json,"
            " adopted_source_ids_json, has_conflicts, gap_note, can_retry,"
            " contract_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_id, assistant_message_id) DO NOTHING",
            (
                secrets.token_urlsafe(16),
                run.account_id,
                run.conversation_id,
                run.run_id,
                assistant_message_id,
                purpose.value,
                status.value,
                str(snapshot.get("query_summary") or "")[:_QUERY_LIMIT],
                json.dumps(
                    [source.model_dump(mode="json") for source in sources],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [str(item) for item in snapshot.get("adopted_source_ids") or []],
                    ensure_ascii=False,
                ),
                1 if snapshot.get("has_conflicts") else 0,
                str(snapshot.get("gap_note") or "")[:_GAP_LIMIT],
                1 if snapshot.get("can_retry") else 0,
                str(
                    snapshot.get("contract_version")
                    or STUDY_SOURCE_VERIFICATION_VERSION
                ),
                timestamp.isoformat(),
            ),
        )


def persist_verification_snapshot(
    repository: ConversationRepository,
    account_id: str,
    run_id: str,
    snapshot: dict[str, Any],
) -> None:
    """把查证快照写入运行配置；同一运行已有快照时不覆盖有效状态。"""

    current = repository.get_generation_run(account_id, run_id)
    if current is None:
        return
    try:
        config = dict(current.config or {})
        if isinstance(config.get("study_source_verification"), dict):
            return
        config["study_source_verification"] = snapshot
        repository.update_generation_config(account_id, run_id, config)
    except Exception:  # noqa: BLE001 - 快照落库失败不阻断回答
        return


def _projection_from_row(row: Any) -> StudySourceVerificationProjection | None:
    try:
        purpose = StudyVerificationPurpose(row["purpose"])
        status = StudySourceVerificationStatus(row["status"])
        created_at = _parse_time(row["created_at"])
        if created_at is None:
            return None
        sources = [
            StudyVerificationSource.model_validate(item)
            for item in json.loads(row["sources_json"])
        ]
        return StudySourceVerificationProjection(
            verification_id=row["verification_id"],
            purpose=purpose,
            purpose_label=PURPOSE_LABELS.get(purpose, "需要公开核实"),
            status=status,
            status_note=status_note_for(status),
            query_summary=row["query_summary"],
            sources=sources,
            adopted_source_ids=json.loads(row["adopted_source_ids_json"]),
            has_conflicts=bool(row["has_conflicts"]),
            gap_note=row["gap_note"],
            can_retry=bool(row["can_retry"]),
            contract_version=row["contract_version"],
            created_at=created_at,
        )
    except (TypeError, ValueError, KeyError, ValidationError):
        return None


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
