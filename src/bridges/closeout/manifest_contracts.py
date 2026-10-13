"""能力清单的稳定合同类型与路由模式构造器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: 清单版本：任何分类、类别或覆盖范围的变更都必须受控递增。
#: v2（Issue 07）：通用网页搜索收口——``tavily_web_search`` 唯一分类为
#: ``external_non_qwen`` 且自带凭据（ADR-0029），发布门断言生产提供方
#: 清单恰好只有 tavily。
#: v3（Issue 21）：旧图片/视频生成、回答朗读与科学文章创作的公开写入口
#: 退役为 410（``legacy_image_writes``/``legacy_video_writes``/
#: ``legacy_read_aloud_writes``/``legacy_science_writes``）；对应供应商
#: 适配器与发布门真实探针保留，历史结果按只读面继续可查可导出。
#: v4（Issue 21）：文章人味化编排与评测框架整体移除——``humanizer``
#: 由 ``qwen_model`` 改判 ``retired``，只保留历史投影读取与 410 守卫。
#: v5：贴吧专用浏览器窗口归入本地会话管理，不引入通用搜索提供方。
#: v7：补齐已注册的凭据设置、地图代理、附件草稿与任务路由分类。
CAPABILITY_MANIFEST_VERSION = 7

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
    "legacy_image",
    "legacy_video",
    "legacy_read_aloud",
    "legacy_science",
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


def exact_route(path: str, *methods: str) -> RoutePattern:
    """构造精确路由模式；不传方法表示不限制。"""
    return RoutePattern(path, frozenset(methods) if methods else None)


def route_prefix(path: str) -> RoutePattern:
    """构造段边界前缀伞形模式（用于整域本地前缀）。"""
    return RoutePattern(path, prefix=True)


def runtime_retired_prefix(path: str) -> RoutePattern:
    """构造运行时恒定 410 的退役模式（装饰器未声明 410，处理器恒定 410）。"""
    return RoutePattern(path, prefix=True, runtime_retired=True)


#: 生产能力清单（Issue 17 分类基线的机器可读版本）。
