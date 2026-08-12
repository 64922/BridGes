"""版本化人味评测案例（Issue 01 tracer bullet）。

每个案例是 Immutable 定义：包含用户请求、原文或上下文、表面类型、模式、
受众、渠道、长度、允许材料、禁止新增 claim、保护项、许可证/来源说明与
内容哈希。案例定义以 ``case_id`` 结尾版本号（``-v1``）标识，任何内容
变化都必须升版本，不得原地修改（与 ``natural_language_eval.md`` 的
冻结语义一致）。

本文件中的全部案例文本均为 BridGes 原创（净室），不复制任何外部参考
项目的文字、结构或示例（见 docs/adr/0011 与 humanizer/skill/CLEAN_ROOM.md）。
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
    mode: str = Field(description="模式：文章改写档位或聊天模式。")
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
# 文章案例：抽象时间管理文章（原文无个人亲历）
# ---------------------------------------------------------------------------

_ARTICLE_TIME_MANAGEMENT_V1 = HumanizeCase(
    case_id="article-time-management-v1",
    kind=HumanizeCaseKind.ARTICLE,
    title="时间块管理法改写（抽象时间管理文章）",
    user_request=(
        "请把下面这篇时间管理说明改写得更像一个真实作者写的：去掉 PPT 腔和抽象包装，"
        "保留所有事实、数字、引用和否定边界，不新增任何内容。"
    ),
    source_text=(
        "时间块管理法（Time Blocking）的核心价值在于通过日程结构化实现注意力配置的最优化。"
        "该方法最早见于 1992 年出版的《Getting Things Done》相关讨论（简称 GTD），"
        "随后在 2024 年的多篇效率研究综述中被重新检视"
        "（参见 https://research.example.org/time-blocking-2024 的综述全文）。"
        "具体操作分为三个层次：其一，以 90 分钟为一个深度工作块，配合 25 分钟番茄钟单元；"
        "其二，在每个工作块之间保留 5 分钟过渡缓冲；其三，每周日晚规划下一周的块状日程。\n"
        "需要强调的是，时间块方法不鼓励把任务切得过碎，也不鼓励跨块切换。"
        "正如管理学者所言：“计划赶不上变化”，因此每个工作块需要预留 10% 的弹性余量。"
        "上述方法适用于每周工作时间超过 40 小时的办公室人员，对自由职业者与远程工作者同样成立，"
        "但并非适合所有人：研究表明大约 30% 的受试者更依赖任务清单而非时间块。\n"
        "从根本上讲，时间块管理是目标导向的时间资源配置哲学，其底层逻辑在于把“注意力带宽”"
        "视为稀缺资源。通过元认知层面的自我监控，用户可以持续优化工作节奏，"
        "最终实现从局部迈向全局的效率跃迁。"
    ),
    surface_type="改写（原文无个人亲历）",
    mode="标准改写",
    audience="普通办公室读者",
    channel="职场公众号",
    target_length="中等（约原文长度）",
    allowed_materials=[],
    forbidden_claims=[
        "作者个人亲历（我过去/我试过/我之前等）",
        "朋友对话（我一个朋友/上周和朋友聊等）",
        "具体时间地点（上周三晚/半小时前/楼下咖啡店等）",
        "未经来源支持的数据或功能（如 5000 例用户调研）",
        "新增产品功能或工具名",
    ],
    protected_items=[
        "90 分钟",
        "25 分钟番茄钟单元",
        "5 分钟过渡缓冲",
        "每周日晚规划",
        "10%",
        "40 小时",
        "30%",
        "1992 年出版",
        "2024 年",
        "GTD",
        "《Getting Things Done》",
        "Time Blocking",
        "https://research.example.org/time-blocking-2024",
        "计划赶不上变化",
        "不鼓励把任务切得过碎",
        "不鼓励跨块切换",
        "并非适合所有人",
    ],
    license_source_note=(
        "案例原文为 BridGes 原创净室素材（docs/adr/0011），"
        "无第三方文本复用；文中研究链接为占位示例，不指向真实资源。"
    ),
)

# ---------------------------------------------------------------------------
# 聊天案例：简单问题不应扩成教学长文
# ---------------------------------------------------------------------------

_CHAT_TOMATO_METHOD_V1 = HumanizeCase(
    case_id="chat-tomato-method-v1",
    kind=HumanizeCaseKind.CHAT,
    title="番茄工作法是什么（简单问题直接回答）",
    user_request="番茄工作法是什么？",
    context="普通日常聊天，用户没有要求详解，也没有要求学习模式。",
    surface_type="直接回答",
    mode="日常对话",
    audience="普通用户",
    channel="聊天界面",
    target_length="短答（一两句话）",
    allowed_materials=[],
    forbidden_claims=[
        "教学长文结构（编号步骤/分节标题/完整课程）",
        "客服式尾句（希望有帮助/如有问题随时问我）",
        "无来源数据（如 95% 效率提升）",
        "虚构个人经验",
    ],
    protected_items=[
        "25 分钟专注工作",
        "5 分钟休息",
        "一短一长两个时间单元",
    ],
    license_source_note="BridGes 原创净室素材；番茄工作法为通用公共知识描述。",
)


def _finalize_case(case: HumanizeCase) -> HumanizeCase:
    """填充内容哈希并冻结：注册表中的案例必须携带与内容一致的哈希。"""
    return case.model_copy(update={"content_sha256": case.compute_hash()})


#: 当前 Issue 的案例注册表（版本化，只追加；哈希已填充）。
HUMANIZE_CASES: tuple[HumanizeCase, ...] = (
    _finalize_case(_ARTICLE_TIME_MANAGEMENT_V1),
    _finalize_case(_CHAT_TOMATO_METHOD_V1),
)


def case_hashes() -> dict[str, str]:
    """案例 id -> 内容哈希（运行锁的记录来源）。"""
    return {case.case_id: case.ensure_hash() for case in HUMANIZE_CASES}


def validate_cases() -> list[str]:
    """校验全部注册案例：哈希一致、必填字段非空；返回问题清单（空=通过）。"""
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
            problems.append(f"{case.case_id}：文章案例缺少原文")
        if not case.forbidden_claims:
            problems.append(f"{case.case_id}：缺少禁止新增 claim 清单")
        if not case.protected_items:
            problems.append(f"{case.case_id}：缺少保护项清单")
        if not case.license_source_note.strip():
            problems.append(f"{case.case_id}：缺少许可证/来源说明")
    return problems
