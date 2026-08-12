"""版本化人味评测案例与分层语料注册表（Issue 01 + Issue 09）。

每个案例是 Immutable 定义：包含用户请求、原文或上下文、表面类型、操作、
对话模式、体裁/profile、风险、长度、允许材料、禁止新增 claim、保护项、
do-no-harm 标志、切片标签、许可证/来源说明、development/holdout 分区
与内容哈希。案例定义以 ``case_id`` 结尾版本号（``-v1``）标识，任何内容
变化都必须升版本，不得原地修改。

Issue 09 在此建立分层语料：``chat-naturalness`` 与 ``article-humanization``
两个语料面分别建集、分别报告；holdout 分区在调优前冻结哈希，解封事件有
审计（见 ``holdout.py``）。全部案例文本均为 BridGes 原创（净室），不复制
任何外部参考项目的文字、结构或示例（docs/adr/0011 与 humanizer/skill/
CLEAN_ROOM.md）。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class HumanizeCaseKind(StrEnum):
    """案例表面类型：文章改写/生成 与 普通聊天。"""

    ARTICLE = "article"
    CHAT = "chat"


class CaseOperation(StrEnum):
    """案例操作：改写 / 按主题生成 / 直接回答（聊天）。"""

    REWRITE = "rewrite"
    GENERATE = "generate"
    DIRECT_ANSWER = "direct_answer"


class ConversationMode(StrEnum):
    """普通聊天的模式合同（与 expression_task.ConversationMode 对齐）。"""

    CASUAL = "casual"
    LEARNING = "learning"


class ArticleGenre(StrEnum):
    """文章体裁/profile（Issue 09 覆盖清单：邮件/报告/教程/观点/演讲/科普/科研）。"""

    EMAIL = "email"
    REPORT = "report"
    TUTORIAL = "tutorial"
    OPINION = "opinion"
    SPEECH = "speech"
    POPULAR_SCIENCE = "popular_science"
    RESEARCH_TECHNICAL = "research_technical"
    INSTRUCTION = "instruction"
    GENERAL = "general"


class RewriteIntensity(StrEnum):
    """三级改写强度（与 expression_task.RewriteIntensity 对齐）。"""

    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"


class RiskLevel(StrEnum):
    """风险级别：高风险内容（健康/财务/安全）必须守住边界。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CasePartition(StrEnum):
    """语料分区：development 可看；holdout 冻结，发布 Issue 12 才可解封。"""

    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class HumanizeCase(BaseModel):
    """一个版本化的人味评测案例（不可变，升版本不改原样）。"""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="版本化案例标识，如 article-time-management-v1。")
    kind: HumanizeCaseKind = Field(description="表面类型：文章或聊天。")
    title: str = Field(description="案例短标题（中文）。")
    user_request: str = Field(description="用户请求原文。")
    source_text: str | None = Field(
        default=None, description="文章案例的原文；聊天案例可缺省。"
    )
    context: str | None = Field(
        default=None, description="聊天案例的上下文；无历史时为空。"
    )
    surface_type: str = Field(description="表面类型描述：改写 / 生成 / 直接回答。")
    operation: CaseOperation = Field(description="操作：改写/生成/直接回答。")
    mode: str = Field(description="模式描述：文章改写档位或聊天模式。")
    conversation_mode: ConversationMode | None = Field(
        default=None, description="聊天案例的对话模式（日常陪伴/学习模式）。"
    )
    genre_profile: ArticleGenre | None = Field(
        default=None, description="文章体裁/profile（聊天案例为空）。"
    )
    rewrite_intensity: RewriteIntensity | None = Field(
        default=None, description="文章改写强度（生成案例可为空）。"
    )
    risk: RiskLevel = Field(
        default=RiskLevel.LOW, description="风险级别（健康/财务/安全等）。"
    )
    audience: str = Field(description="目标受众。")
    channel: str = Field(description="发布渠道。")
    target_length: str = Field(description="目标篇幅。")
    allowed_materials: list[str] = Field(
        default_factory=list, description="允许使用的外部材料（无来源不可新增 claim）。"
    )
    forbidden_claims: list[str] = Field(
        default_factory=list, description="禁止新增的 claim 类别。"
    )
    protected_items: list[str] = Field(
        default_factory=list, description="必须原样保留的保护项。"
    )
    do_no_harm: bool = Field(
        default=False, description="do-no-harm 标志：高风险内容的边界必须守住。"
    )
    slice_tags: list[str] = Field(
        default_factory=list, description="切片标签（路径/模式/密度/场景，覆盖统计用）。"
    )
    adversarial_type: str | None = Field(
        default=None, description="对抗类型标签（非对抗案例为空）。"
    )
    partition: CasePartition = Field(
        default=CasePartition.DEVELOPMENT, description="语料分区。"
    )
    task_contract_version: str = Field(
        default="3",
        description="任务契约版本（对应 expression_task 契约世代，进入运行锁）。",
    )
    license_source_note: str = Field(description="许可证/来源说明。")
    content_sha256: str = Field(
        default="", description="规范化内容哈希（除本字段外的全部字段参与计算）。"
    )

    def compute_hash(self) -> str:
        """规范化内容哈希：排序键 JSON，任何内容变化都会改变哈希。"""
        payload = self.model_dump(exclude={"content_sha256"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def ensure_hash(self) -> str:
        """返回并校验 content_sha256 与当前内容一致；不一致视为内容被篡改。"""
        computed = self.compute_hash()
        if self.content_sha256 and self.content_sha256 != computed:
            raise HumanizeCaseIntegrityError(
                f"案例 {self.case_id} 内容哈希不一致：声明 "
                f"{self.content_sha256[:12]}…，实际 {computed[:12]}…，请升版本而不是改原文。"
            )
        return computed


class HumanizeCaseIntegrityError(Exception):
    """案例内容哈希校验失败。"""


# ---------------------------------------------------------------------------
# 语料注册表（来自 corpus 包；Issue 09 起按 chat/article 两个语料面聚合）
# ---------------------------------------------------------------------------

def _finalize_case(case: HumanizeCase) -> HumanizeCase:
    """填充内容哈希并冻结：注册表中的案例必须携带与内容一致的哈希。"""
    return case.model_copy(update={"content_sha256": case.compute_hash()})


def _load_corpus() -> tuple[HumanizeCase, ...]:
    """加载全部案例并填充哈希（数据文件在 corpus/ 包中按面组织）。"""
    from bridges.humanize_eval.corpus.article_cases import ARTICLE_CASE_DEFS
    from bridges.humanize_eval.corpus.chat_cases import CHAT_CASE_DEFS

    return tuple(
        _finalize_case(case)
        for case in (*CHAT_CASE_DEFS, *ARTICLE_CASE_DEFS)
    )


#: 当前语料注册表（版本化，只追加；哈希已填充）。
HUMANIZE_CASES: tuple[HumanizeCase, ...] = _load_corpus()

#: 语料版本：schema/内容集合每次变更递增（进入运行锁）。
CORPUS_VERSION = "9.1"

#: 各语料面的最低有效案例数（低于此数该面结论必须为 inconclusive）。
MIN_CASES_PER_SURFACE = 40


def case_hashes() -> dict[str, str]:
    """案例 id -> 内容哈希（运行锁的记录来源）。"""
    return {case.case_id: case.ensure_hash() for case in HUMANIZE_CASES}


def _surface_aggregate_hash(payload_for: list[object]) -> str:
    """按面聚合任意 case 载荷的规范化哈希（corpus/ledger 共用）。"""
    canonical = json.dumps(payload_for, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def corpus_hashes() -> dict[str, str]:
    """语料面 -> 注册表聚合哈希（id+hash 规范化摘要；运行锁记录）。"""
    result: dict[str, str] = {}
    for surface in SURFACE_NAMES_BY_KIND:
        result[surface] = _surface_aggregate_hash(
            [
                f"{case.case_id}:{case.ensure_hash()}"
                for case in HUMANIZE_CASES
                if case.kind.value == surface
            ]
        )
    return result


def surface_cases(kind: HumanizeCaseKind) -> tuple[HumanizeCase, ...]:
    """按语料面返回案例（分别建集、分别报告的切片入口）。"""
    return tuple(case for case in HUMANIZE_CASES if case.kind is kind)


def ledger_hashes() -> dict[str, str]:
    """来源账本聚合哈希：每个 case 的来源/许可/保护字段的规范化摘要。

    面级（chat/article）聚合，进入运行锁；任何许可字段或保护项变化
    都会改变哈希，比较前完整性检查的依据。
    """
    result: dict[str, str] = {}
    for surface in SURFACE_NAMES_BY_KIND:
        payloads = []
        for case in HUMANIZE_CASES:
            if case.kind.value != surface:
                continue
            payloads.append(
                {
                    "id": case.case_id,
                    "allowed": sorted(case.allowed_materials),
                    "forbidden": sorted(case.forbidden_claims),
                    "protected": sorted(case.protected_items),
                    "license": case.license_source_note,
                }
            )
        result[surface] = _surface_aggregate_hash(payloads)
    return result


#: 语料面名 -> 案例类型（单一事实源：cases/runner/cli 共用）。
SURFACE_NAMES_BY_KIND: dict[str, HumanizeCaseKind] = {
    "chat": HumanizeCaseKind.CHAT,
    "article": HumanizeCaseKind.ARTICLE,
}


def validate_cases() -> list[str]:
    """校验全部注册案例：哈希一致、必填字段非空、分区与切片约束；返回问题清单。"""
    problems: list[str] = []
    seen: set[str] = set()
    for case in HUMANIZE_CASES:
        try:
            case.ensure_hash()
        except HumanizeCaseIntegrityError as exc:
            problems.append(str(exc))
        if case.case_id in seen:
            problems.append(f"案例 id 重复：{case.case_id}")
        seen.add(case.case_id)
        if not case.user_request.strip():
            problems.append(f"{case.case_id}：user_request 为空")
        if case.kind == HumanizeCaseKind.ARTICLE and not case.source_text:
            if case.operation is not CaseOperation.GENERATE:
                problems.append(
                    f"{case.case_id}：文章非生成案例缺少原文"
                )
        if not case.forbidden_claims:
            problems.append(f"{case.case_id}：缺少禁止新增 claim 清单")
        if not case.protected_items:
            problems.append(f"{case.case_id}：缺少保护项清单")
        if not case.license_source_note.strip():
            problems.append(f"{case.case_id}：缺少许可证/来源说明")
        if not case.task_contract_version.strip():
            problems.append(f"{case.case_id}：缺少任务契约版本")
        if case.kind == HumanizeCaseKind.CHAT:
            if case.conversation_mode is None:
                problems.append(f"{case.case_id}：聊天案例缺少对话模式")
            if case.operation is not CaseOperation.DIRECT_ANSWER:
                problems.append(f"{case.case_id}：聊天案例操作必须是 direct_answer")
        if case.kind == HumanizeCaseKind.ARTICLE:
            if case.operation is CaseOperation.REWRITE and not case.source_text:
                problems.append(f"{case.case_id}：改写案例缺少原文")
            if case.rewrite_intensity is None:
                problems.append(f"{case.case_id}：文章案例缺少改写强度")
            if case.genre_profile is None:
                problems.append(f"{case.case_id}：文章案例缺少体裁 profile")
            if case.source_text and case.do_no_harm and case.risk is RiskLevel.LOW:
                problems.append(f"{case.case_id}：do_no_harm 案例风险级别不得为 low")
    return problems
