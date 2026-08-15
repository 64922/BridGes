"""Issue 17：版本化生产能力清单（capability manifest）。

清单把每一项用户可达的公开能力唯一归类为 ``qwen_model``、
``external_non_qwen``、``local_deterministic`` 或 ``retired``，是真实性
发布门（``bridges.closeout.authenticity_gate``）的入口事实源：

- ``PRODUCTION_CAPABILITY_MANIFEST`` 覆盖公开 API 路由、聊天动作与 UI
  对应的生产服务；新增公开能力必须先加入清单再决定类别，不能静默忽略；
- 每项能力恰好属于一个类别；``qwen_model`` 项必须绑定批准矩阵中的
  ``model_capability``；``retired`` 项必须对应真实 410 路由或退役能力名；
- 路由完整性核对以 FastAPI 应用实际注册的路由为准（含惰性
  ``_IncludedRouter`` 展开）：声明 410 的路由必须被 ``retired`` 项覆盖，
  非 410 路由不得被 ``retired`` 项覆盖，任何路由都必须被至少一个清单项
  分类。``/chat`` 家族（模型关键路由）逐条精确分类；纯本地前缀
  （``/auth``、``/knowledge-base`` 等）按领域伞形分类。

本模块不读取、打印或持久化任何 Key、请求/响应正文。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bridges.ai.fixed_models import MODEL_BY_CAPABILITY

#: 清单版本：任何分类、类别或覆盖范围的变更都必须受控递增。
#: v2（Issue 07）：通用网页搜索收口——``tavily_web_search`` 唯一分类为
#: ``external_non_qwen`` 且自带凭据（ADR-0029），发布门断言生产提供方
#: 清单恰好只有 tavily。
CAPABILITY_MANIFEST_VERSION = 2

#: 稳定门禁错误码（Issue 17 Observability 合同）。
UNCLASSIFIED_CAPABILITY = "unclassified_capability"
PRODUCTION_STUB = "production_stub"
MISSING_ADAPTER = "missing_adapter"
MODEL_MATRIX_DRIFT = "model_matrix_drift"
DIRECT_CLIENT_BYPASS = "direct_client_bypass"
MISSING_RUN_LOCK = "missing_run_lock"
RETIRED_ROUTE_ACTIVE = "retired_route_active"


class CapabilityCategory(StrEnum):
    """公开能力的唯一真实性类别（Issue 17 分类基线）。"""

    QWEN_MODEL = "qwen_model"
    EXTERNAL_NON_QWEN = "external_non_qwen"
    LOCAL_DETERMINISTIC = "local_deterministic"
    RETIRED = "retired"


#: 聊天动作稳定标识：完整性核对要求每个动作被至少一个清单项认领。
#: 新增聊天动作（如新的自然语言能力路由）必须先加入本清单再决定类别。
CHAT_ACTIONS: tuple[str, ...] = (
    # 普通对话与学习模式
    "chat",
    "learning_mode",
    "paper_synthesis",
    # 外部非 Qwen 检索
    "web_search",
    "arxiv_search",
    "paper_card",
    # 结构化生成
    "humanizer",
    "career",
    "profile_extraction",
    # 现代媒体
    "image_generate",
    "image_edit",
    "image_cancel",
    "image_alt_text",
    "video_generate",
    "video_cancel",
    # 语音
    "asr_transcribe",
    "tts_narration",
    # 知识库
    "kb_ocr",
    "kb_embedding",
    # 本地操作
    "conversation_list",
    "profile_edit",
    "profile_retract",
    "material_crud",
    "keyword_search",
    "login",
    "account_manage",
    # 退役入口
    "legacy_expression",
    "legacy_media",
    "legacy_reminders",
    "legacy_extensions",
)


@dataclass(frozen=True)
class RoutePattern:
    """一条路由模式：路径（``{param}`` 段归一化为 ``*``）+ 可选约束。

    - ``methods``：非空时只匹配这些 HTTP 方法；
    - ``prefix``：True 时按段边界前缀匹配（``P`` 匹配 ``P`` 与 ``P/...``）；
    - ``runtime_retired``：路由装饰器未声明 410、但处理器恒定返回 410 的
      退役入口（如 ``/plugins``、``/mcp``）。静态核对跳过其"非 410 冲突"
      检查，410 行为由门禁契约探针证明。
    """

    path: str
    methods: frozenset[str] | None = None
    prefix: bool = False
    runtime_retired: bool = False

    def matches(self, path: str, method: str) -> bool:
        if self.methods is not None and method not in self.methods:
            return False
        if self.prefix:
            return path == self.path or path.startswith(self.path + "/")
        return path == self.path


@dataclass(frozen=True)
class CapabilityManifestEntry:
    """一项公开能力的唯一分类与真实性合同。"""

    #: 稳定标识（门禁报告与错误码使用）。
    id: str
    #: 用户旅程/公开能力名称（中文，Issue 17 分类基线对应项）。
    journey: str
    #: 唯一类别。
    category: CapabilityCategory
    #: qwen_model 时对应的注册表能力名（必须在批准矩阵中）。
    model_capability: str | None = None
    #: 该能力对应的公开 API 路由模式（用于完整性核对）。
    route_patterns: tuple[RoutePattern, ...] = ()
    #: 认领的聊天动作（CHAT_ACTIONS 子集）。
    chat_actions: tuple[str, ...] = ()
    #: 退役能力名（retired 且不依赖路由时，注册表不得出现这些能力）。
    retired_capability_names: tuple[str, ...] = ()
    #: 真实性合同说明（人类可读）。
    truth_contract: str = ""


#: 旧 Expression/Media 退役能力名：注册表再次出现即 ``retired_route_active``。
RETIRED_EXPRESSION_CAPABILITY = "expression_draft_generation"
RETIRED_MEDIA_CAPABILITIES = (
    "media_asset_generation",
    "media_deterministic_generation",
    "media_narration_synthesis",
    "media_storyboard_generation",
)


def _p(path: str, *methods: str) -> RoutePattern:
    """构造精确路由模式；不传方法表示不限制。"""
    return RoutePattern(path, frozenset(methods) if methods else None)


def _px(path: str) -> RoutePattern:
    """构造段边界前缀伞形模式（用于整域本地前缀）。"""
    return RoutePattern(path, prefix=True)


def _pr(path: str) -> RoutePattern:
    """构造运行时恒定 410 的退役模式（装饰器未声明 410，处理器恒定 410）。"""
    return RoutePattern(path, prefix=True, runtime_retired=True)


#: 生产能力清单（Issue 17 分类基线的机器可读版本）。
PRODUCTION_CAPABILITY_MANIFEST: tuple[CapabilityManifestEntry, ...] = (
    # ── qwen_model：真实 Qwen/百炼能力 ──────────────────────────────────
    CapabilityManifestEntry(
        id="chat_daily",
        journey="普通聊天",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
        route_patterns=(
            _p("/chat/first-turn"),
            _p("/chat/conversations/*/messages", "POST"),
            _p("/chat/conversations/*/messages/*/retry", "POST"),
            _p("/chat/conversations/*/messages/*/stop", "POST"),
        ),
        chat_actions=("chat",),
        truth_contract="真实 qwen_text_chat，固定文本模型，消息与运行锁可追溯。",
    ),
    CapabilityManifestEntry(
        id="learning_final_answer",
        journey="学习模式最终正文",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
        chat_actions=("learning_mode",),
        truth_contract=(
            "学习模式最终正文经真实文本模型探针；联网失败时 Tavily 失败投影"
            "与后续 Qwen 降级分别记录。"
        ),
    ),
    CapabilityManifestEntry(
        id="paper_synthesis",
        journey="论文搜索成功后的引用综合",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
        chat_actions=("paper_synthesis",),
        truth_contract="引用综合属于文本模型调用，与 arXiv 检索阶段严格区分。",
    ),
    CapabilityManifestEntry(
        id="humanizer",
        journey="Humanizer 首稿与条件修订",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_structured_output",
        route_patterns=(_px("/chat/humanizer"),),
        chat_actions=("humanizer",),
        truth_contract="每次结构化调用分别落锁，模型输出确实决定结果。",
    ),
    CapabilityManifestEntry(
        id="career",
        journey="Career 生成与条件修复",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_structured_output",
        chat_actions=("career",),
        truth_contract="每次结构化调用分别落锁，不得以解析失败形成完成态。",
    ),
    CapabilityManifestEntry(
        id="profile_ambiguous",
        journey="Profile 歧义信号抽取",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_profile_extraction",
        chat_actions=("profile_extraction",),
        truth_contract="真实画像 adapter 与运行锁；本地分支零调用零锁。",
    ),
    CapabilityManifestEntry(
        id="kb_ocr",
        journey="知识库 OCR",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_ocr",
        route_patterns=(
            _p("/chat/conversations/*/attachments/*/ingestion"),
            _p("/knowledge-base/materials/*/retry"),
        ),
        chat_actions=("kb_ocr",),
        truth_contract="固定能力、固定模型，页级调用锁与业务对象关联。",
    ),
    CapabilityManifestEntry(
        id="kb_embedding",
        journey="知识库入库/重建/查询 Embedding",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_embedding",
        route_patterns=(
            _px("/chat/ingestion"),
            _p("/knowledge-base/materials/*/rebuild"),
        ),
        chat_actions=("kb_embedding",),
        truth_contract="固定能力、固定模型，批次或页面调用锁与业务对象关联。",
    ),
    CapabilityManifestEntry(
        id="image_generate_edit",
        journey="现代图片生成/编辑",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_image",
        route_patterns=(
            _p("/chat/conversations/*/image-tasks"),
            _p("/chat/conversations/*/image-tasks/*"),
            _p("/chat/conversations/*/image-tasks/*/retry"),
            _p("/chat/conversations/*/image-assets/*"),
            _p("/chat/conversations/*/image-assets/*/versions/*/image"),
        ),
        chat_actions=("image_generate", "image_edit"),
        truth_contract="实际供应商动作逐次落锁；纯本地读取不造锁。",
    ),
    CapabilityManifestEntry(
        id="image_cancel",
        journey="图片供应商取消",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_image",
        route_patterns=(_p("/chat/conversations/*/image-tasks/*/cancel"),),
        chat_actions=("image_cancel",),
        truth_contract="真实取消动作逐次落锁；无云任务不伪造取消成功。",
    ),
    CapabilityManifestEntry(
        id="image_alt_text",
        journey="图片视觉替代文本",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_vision",
        route_patterns=(_p("/chat/conversations/*/image-assets/*/alt-text"),),
        chat_actions=("image_alt_text",),
        truth_contract="真实视觉调用落锁；失败/空输出回退明确 fallback 且不冒充模型来源。",
    ),
    CapabilityManifestEntry(
        id="video_generate",
        journey="现代视频生成",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_wan",
        route_patterns=(
            _px("/chat/conversations/*/video-tasks"),
            _px("/chat/conversations/*/video-assets"),
        ),
        chat_actions=("video_generate",),
        truth_contract="Wan 调用逐次落锁；本地状态投影不造锁。",
    ),
    CapabilityManifestEntry(
        id="video_cancel",
        journey="视频供应商取消",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_wan",
        route_patterns=(_p("/chat/conversations/*/video-tasks/*/cancel"),),
        chat_actions=("video_cancel",),
        truth_contract="真实取消动作逐次落锁；无可安全取消任务时失败关闭，不伪造成功。",
    ),
    CapabilityManifestEntry(
        id="asr_short",
        journey="ASR 短音频转写",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_asr_short",
        route_patterns=(_p("/chat/conversations/*/dictation"),),
        chat_actions=("asr_transcribe",),
        truth_contract="真实语音 adapter、固定模型及运行锁。",
    ),
    CapabilityManifestEntry(
        id="asr_long",
        journey="ASR 长音频/文件转写",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_asr_long",
        truth_contract="真实语音 adapter、固定模型及运行锁（文件转写变体）。",
    ),
    CapabilityManifestEntry(
        id="tts",
        journey="回答朗读 TTS",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_tts",
        route_patterns=(_px("/chat/conversations/*/messages/*/read-aloud"),),
        chat_actions=("tts_narration",),
        truth_contract="真实语音 adapter、固定模型及运行锁。",
    ),
    # ── external_non_qwen：不消费 Qwen Key 的外部能力 ────────────────────
    CapabilityManifestEntry(
        id="tavily_web_search",
        journey="Tavily 通用网页搜索",
        category=CapabilityCategory.EXTERNAL_NON_QWEN,
        chat_actions=("web_search",),
        truth_contract=(
            "只访问 Tavily（ADR-0029）；自带凭据（tvly-，安装期收集），"
            "不读取/不继承/不发送 Qwen Key；失败后才允许另起真实聊天模型"
            "降级调用；缺 Key 时入口如实标注不可用，不静默回退其他提供方。"
        ),
    ),
    CapabilityManifestEntry(
        id="arxiv_search",
        journey="arXiv 检索与论文卡片字段整理",
        category=CapabilityCategory.EXTERNAL_NON_QWEN,
        chat_actions=("arxiv_search", "paper_card"),
        truth_contract="检索 worker 不继承 Qwen Key；最终自然语言综合另计聊天模型调用。",
    ),
    # ── local_deterministic：本地确定性能力，不调用模型、不造锁 ─────────
    CapabilityManifestEntry(
        id="profile_local_rules",
        journey="Profile 明确信号、更正和撤回",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        chat_actions=("profile_edit", "profile_retract"),
        truth_contract="明确标记本地规则，不调用模型、不伪造锁。",
    ),
    CapabilityManifestEntry(
        id="auth_login_account",
        journey="登录、账户与设备会话",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(_px("/auth"), _p("/me")),
        chat_actions=("login", "account_manage"),
        truth_contract="不应产生 Qwen 调用或运行锁。",
    ),
    CapabilityManifestEntry(
        id="conversation_management",
        journey="会话列表与消息管理",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(
            _p("/chat/conversations"),
            _p("/chat/conversations/*"),
            _p("/chat/conversations/*/messages", "GET"),
            _p("/chat/conversations/*/messages/*", "GET"),
            _p("/chat/conversations/*/messages/*/events", "GET"),
            _p("/chat/conversations/*/messages/*/feedback"),
            _p("/chat/conversations/*/messages/*/feedback/*/resolve"),
            _p("/chat/conversations/*/messages/*/citations/*"),
            _px("/chat/conversations/*/messages/*/mcp/confirmations"),
            _p("/chat/conversations/*/learning-plan-adjustments"),
            _p("/chat/conversations/*/learning-progress"),
            _px("/chat/conversations/*/feedback"),
        ),
        chat_actions=("conversation_list",),
        truth_contract="本地会话/消息 CRUD 不应产生 Qwen 调用或运行锁。",
    ),
    CapabilityManifestEntry(
        id="material_crud",
        journey="材料与知识库 CRUD",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(
            _p("/chat/conversations/*/attachments/*/download"),
            _px("/knowledge-base"),
            _px("/data"),
        ),
        chat_actions=("material_crud",),
        truth_contract="本地材料管理不应产生 Qwen 调用或运行锁。",
    ),
    CapabilityManifestEntry(
        id="keyword_search",
        journey="关键词检索",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(_px("/search"),),
        chat_actions=("keyword_search",),
        truth_contract="本地 FTS/关键词检索不应产生 Qwen 调用或运行锁。",
    ),
    CapabilityManifestEntry(
        id="local_data_management",
        journey="本地数据管理（画像、项目、保险库、同步、领域包等）",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(
            _px("/profiles"),
            _px("/vault"),
            _px("/projects"),
            _px("/sharing"),
            _px("/sync"),
            _px("/institutions"),
            _px("/domain-packs"),
            _px("/workflows"),
            _px("/scope"),
            _px("/evaluation"),
            _px("/science"),
            _px("/learning"),
            _px("/learning-projects"),
            _px("/compatibility"),
            _px("/skills"),
            _px("/health"),
            _px("/docs"),
            _px("/redoc"),
            _p("/openapi.json"),
            _px("/_test"),
        ),
        truth_contract="本地管理操作不应产生 Qwen 调用或运行锁。",
    ),
    CapabilityManifestEntry(
        id="media_legacy_reads",
        journey="旧 Media 只读访问（迁移审计）",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(
            _px("/media/objects"),
            _px("/media/assets"),
            _px("/media/storyboards"),
            _px("/media/sandbox-runs"),
            _px("/media/accessibility/bundles"),
            _px("/media/publish"),
            _px("/media/figures"),
            _px("/media/charts"),
        ),
        truth_contract="只读历史访问，不调用模型、不造锁。",
    ),
    CapabilityManifestEntry(
        id="expression_legacy_reads",
        journey="旧 Expression 只读访问",
        category=CapabilityCategory.LOCAL_DETERMINISTIC,
        route_patterns=(_px("/expression/drafts"),),
        truth_contract="只读历史访问，不调用模型、不造锁。",
    ),
    # ── retired：稳定返回 410 的退役写入口 ───────────────────────────────
    CapabilityManifestEntry(
        id="legacy_expression_writes",
        journey="旧 Expression 写入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(
            _p("/expression/drafts", "POST"),
            _p("/expression/drafts/compare", "POST"),
            _p("/expression/drafts/*/approve", "POST"),
            _p("/expression/drafts/*/feedback", "POST"),
            _p("/expression/drafts/*/patches/*/apply", "POST"),
            _p("/expression/drafts/*/publish", "POST"),
            _p("/expression/drafts/*/style-diagnostic", "POST"),
        ),
        retired_capability_names=(RETIRED_EXPRESSION_CAPABILITY,),
        chat_actions=("legacy_expression",),
        truth_contract="稳定返回 410，不注册伪模型能力，不执行确定性假成功。",
    ),
    CapabilityManifestEntry(
        id="legacy_media_writes",
        journey="旧 Media 写入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(
            _p("/media/accessibility/bundles", "POST"),
            _p("/media/accessibility/bundles/*/playback", "POST"),
            _p("/media/assets", "POST"),
            _p("/media/assets/*/claim-graph", "POST"),
            _p("/media/assets/*/derived", "POST"),
            _p("/media/assets/*/revoke", "POST"),
            _p("/media/charts", "POST"),
            _p("/media/cross-media/consistency", "POST"),
            _p("/media/figures", "POST"),
            _p("/media/objects/*/spec", "PUT"),
            _p("/media/projects/*/assets", "POST"),
            _p("/media/publish", "POST"),
            _p("/media/publish/check", "POST"),
            _p("/media/sandbox-runs/*/repair", "POST"),
            _p("/media/storyboards", "POST"),
            _p("/media/storyboards/*", "PUT"),
            _p("/media/storyboards/*", "DELETE"),
            _p("/media/storyboards/*/code", "POST"),
            _p("/media/storyboards/*/sandbox", "POST"),
            _p("/media/storyboards/*/validate"),
            _p("/media/validate-spec", "POST"),
        ),
        retired_capability_names=RETIRED_MEDIA_CAPABILITIES,
        chat_actions=("legacy_media",),
        truth_contract="稳定返回 410，不注册伪模型能力，不执行确定性假成功。",
    ),
    CapabilityManifestEntry(
        id="legacy_reminders",
        journey="旧提醒入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(_px("/reminders"),),
        chat_actions=("legacy_reminders",),
        truth_contract="全部提醒路由稳定返回 410。",
    ),
    CapabilityManifestEntry(
        id="legacy_extensions",
        journey="旧插件/MCP/复习/项目文件/附件入口",
        category=CapabilityCategory.RETIRED,
        route_patterns=(
            _pr("/plugins"),
            _pr("/mcp"),
            _px("/learning/review-tasks"),
            _p("/learning/missions/*/review-schedule"),
            _p("/learning/missions/*/review-tasks"),
            _p("/learning-projects", "POST"),
            _p("/learning-projects/*", "PUT"),
            _p("/learning-projects/*", "DELETE"),
            _p("/learning-projects/*", "PATCH"),
            _p("/learning-projects/*/files", "POST"),
            _p("/learning-projects/*/files/*", "DELETE"),
            _p("/chat/conversations/*/mode"),
            _p("/chat/conversations/*/attachments"),
            _p("/chat/conversations/*/attachments/by-upload/*"),
            _p("/chat/conversations/*/attachments/*", "DELETE"),
            _p("/chat/conversations/*/attachments/*/ingestion/retry", "POST"),
            _p("/chat/conversations/*/messages/*/attachments/*"),
        ),
        chat_actions=("legacy_extensions",),
        truth_contract="稳定返回 410，不注册伪模型能力。",
    ),
)

_MANIFEST_BY_ID = {entry.id: entry for entry in PRODUCTION_CAPABILITY_MANIFEST}


def normalize_route_path(path: str) -> str:
    """把 FastAPI 路由路径的 ``{param}``/``:param`` 段归一化为 ``*``。"""
    segments: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}") or segment.startswith(":"):
            segments.append("*")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def validate_manifest(
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[str]:
    """校验清单自身的一致性；返回违规说明列表（空列表表示通过）。

    规则：
    - 清单版本为正整数；
    - 每项能力 ID 唯一（重复分类失败）；
    - ``qwen_model`` 项必须绑定批准矩阵中的 model capability；
    - 非 ``qwen_model`` 项不得绑定 model capability；
    - ``retired`` 项必须声明退役能力名或至少一条路由模式（缺失分类失败）；
    - ``local_deterministic``/``external_non_qwen`` 项不得声明退役能力名；
    - 认领的聊天动作必须存在于 ``CHAT_ACTIONS``，且每个动作被恰当地认领。
    """
    violations: list[str] = []
    if CAPABILITY_MANIFEST_VERSION < 1:
        violations.append("清单版本必须为正整数。")
    seen_ids: set[str] = set()
    claimed_actions: set[str] = set()
    for entry in manifest:
        if entry.id in seen_ids:
            violations.append(f"重复能力分类：{entry.id}。")
        seen_ids.add(entry.id)
        if entry.category == CapabilityCategory.QWEN_MODEL:
            if entry.model_capability is None:
                violations.append(f"qwen_model 能力 {entry.id} 缺少 model_capability。")
            elif entry.model_capability not in MODEL_BY_CAPABILITY:
                violations.append(
                    f"qwen_model 能力 {entry.id} 绑定未批准能力 {entry.model_capability}。"
                )
        elif entry.model_capability is not None:
            violations.append(
                f"非 qwen_model 能力 {entry.id} 不得绑定 model_capability。"
            )
        if entry.category == CapabilityCategory.RETIRED:
            if not entry.route_patterns and not entry.retired_capability_names:
                violations.append(
                    f"retired 能力 {entry.id} 必须声明 410 路由模式或退役能力名。"
                )
        elif entry.retired_capability_names:
            violations.append(f"非 retired 能力 {entry.id} 不得声明退役能力名。")
        for action in entry.chat_actions:
            if action not in CHAT_ACTIONS:
                violations.append(f"能力 {entry.id} 认领未知聊天动作 {action}。")
            claimed_actions.add(action)
    for action in CHAT_ACTIONS:
        if action not in claimed_actions:
            violations.append(f"聊天动作 {action} 未被任何清单项认领。")
    return violations


@dataclass(frozen=True)
class DiscoveredRoute:
    """应用实际注册的一条公开路由（脱敏：只含路径/方法/状态码）。"""

    path: str
    method: str
    status_code: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "method": self.method,
            "status_code": self.status_code,
        }


def discover_api_routes(app: Any) -> list[DiscoveredRoute]:
    """枚举 FastAPI 应用注册的公开路由（展开惰性 ``_IncludedRouter``）。"""
    discovered: list[DiscoveredRoute] = []

    def flatten(routes: Iterable[Any]) -> Iterable[Any]:
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from flatten(route.original_router.routes)
            else:
                yield route

    for route in flatten(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = getattr(route, "methods", None) or []
        status_code = getattr(route, "status_code", None) or 200
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            discovered.append(
                DiscoveredRoute(
                    path=normalize_route_path(str(path)),
                    method=method,
                    status_code=int(status_code),
                )
            )
    return discovered


@dataclass(frozen=True)
class ManifestViolation:
    """一条清单完整性违规：稳定错误码 + 对象 + 脱敏说明。"""

    code: str
    target: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "target": self.target, "detail": self.detail}


def check_route_coverage(
    routes: Iterable[DiscoveredRoute],
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对公开路由覆盖：未分类、retired 误覆盖与非 410 退役均失败。

    - 声明 410 的路由必须被 retired 清单项覆盖（``unclassified_capability``）；
    - 非 410 路由不得被 retired 清单项覆盖（``retired_route_active``）；
    - 任何路由都必须被至少一个清单项分类（``unclassified_capability``）。
    """
    violations: list[ManifestViolation] = []
    for route in routes:
        retired_declared: list[CapabilityManifestEntry] = []
        retired_runtime: list[CapabilityManifestEntry] = []
        active_matches: list[CapabilityManifestEntry] = []
        for entry in manifest:
            for pattern in entry.route_patterns:
                if not pattern.matches(route.path, route.method):
                    continue
                if entry.category == CapabilityCategory.RETIRED:
                    if pattern.runtime_retired:
                        retired_runtime.append(entry)
                    else:
                        retired_declared.append(entry)
                else:
                    active_matches.append(entry)
                break
        if route.status_code == 410:
            if not retired_declared:
                violations.append(
                    ManifestViolation(
                        UNCLASSIFIED_CAPABILITY,
                        f"{route.method} {route.path}",
                        "410 退役路由未被任何 retired 清单项覆盖。",
                    )
                )
        else:
            if retired_declared:
                violations.append(
                    ManifestViolation(
                        RETIRED_ROUTE_ACTIVE,
                        f"{route.method} {route.path}",
                        "retired 清单项覆盖了非 410 的活跃路由（类别误分）。",
                    )
                )
            if not active_matches and not retired_declared and not retired_runtime:
                violations.append(
                    ManifestViolation(
                        UNCLASSIFIED_CAPABILITY,
                        f"{route.method} {route.path}",
                        "公开路由未被任何清单项分类。",
                    )
                )
    return violations


def check_chat_action_coverage(
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对聊天动作覆盖：清单未覆盖的动作必须失败并输出稳定标识。"""
    claimed = {action for entry in manifest for action in entry.chat_actions}
    return [
        ManifestViolation(
            UNCLASSIFIED_CAPABILITY,
            action,
            "聊天动作未被任何清单项分类。",
        )
        for action in CHAT_ACTIONS
        if action not in claimed
    ]


def check_retired_registry(
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """核对退役能力名：注册表再次出现即 ``retired_route_active``。

    按注册表活跃清单逐名核对（不依赖固定 version），任何版本复活都失败。
    """
    retired_names = {
        name
        for entry in manifest
        if entry.category == CapabilityCategory.RETIRED
        for name in entry.retired_capability_names
    }
    if not retired_names:
        return []
    registered = {
        capability.name for capability in registry.list_active()
    }
    return [
        ManifestViolation(
            RETIRED_ROUTE_ACTIVE,
            name,
            "退役能力名被重新注册到生产能力注册表。",
        )
        for name in sorted(retired_names)
        if name in registered
    ]


def check_registry_coverage(
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """反向核对：注册表中每个活跃 MODEL 能力必须被清单 qwen_model 项认领。

    清单声明了能力分类，注册表必须与之吻合；注册表里出现清单未认领的
    活跃 MODEL 能力（如新能力未分类）即 ``unclassified_capability`` 并
    点名 capability，禁止静默忽略。
    """
    from bridges.contracts.ai import CapabilityKind

    claimed = {
        entry.model_capability
        for entry in manifest
        if entry.category == CapabilityCategory.QWEN_MODEL
        and entry.model_capability is not None
    }
    violations: list[ManifestViolation] = []
    for capability in registry.list_active():
        if capability.kind != CapabilityKind.MODEL or capability.model_id is None:
            continue
        if capability.name not in claimed:
            violations.append(
                ManifestViolation(
                    UNCLASSIFIED_CAPABILITY,
                    capability.name,
                    "注册表活跃 MODEL 能力未被能力清单认领，必须先加入清单再决定类别。",
                )
            )
    return violations


def check_manifest_completeness(
    routes: Iterable[DiscoveredRoute],
    registry: Any,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[ManifestViolation]:
    """完整性与类别一致性总检查：清单自检 + 路由覆盖 + 聊天动作 + 退役注册。"""
    violations: list[ManifestViolation] = [
        ManifestViolation(UNCLASSIFIED_CAPABILITY, "manifest", message)
        for message in validate_manifest(manifest)
    ]
    violations.extend(check_route_coverage(routes, manifest=manifest))
    violations.extend(check_chat_action_coverage(manifest=manifest))
    violations.extend(check_retired_registry(registry, manifest=manifest))
    violations.extend(check_registry_coverage(registry, manifest=manifest))
    return violations


__all__ = [
    "CAPABILITY_MANIFEST_VERSION",
    "CHAT_ACTIONS",
    "CapabilityCategory",
    "CapabilityManifestEntry",
    "DIRECT_CLIENT_BYPASS",
    "DiscoveredRoute",
    "ManifestViolation",
    "MISSING_ADAPTER",
    "MISSING_RUN_LOCK",
    "MODEL_MATRIX_DRIFT",
    "PRODUCTION_CAPABILITY_MANIFEST",
    "PRODUCTION_STUB",
    "RETIRED_EXPRESSION_CAPABILITY",
    "RETIRED_MEDIA_CAPABILITIES",
    "RETIRED_ROUTE_ACTIVE",
    "RoutePattern",
    "UNCLASSIFIED_CAPABILITY",
    "check_chat_action_coverage",
    "check_manifest_completeness",
    "check_registry_coverage",
    "check_retired_registry",
    "check_route_coverage",
    "discover_api_routes",
    "normalize_route_path",
    "validate_manifest",
]
