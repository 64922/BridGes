"""模型及外部服务能力分类数据。"""

from __future__ import annotations

from bridges.closeout.manifest_contracts import (
    CapabilityCategory,
    CapabilityManifestEntry,
    exact_route,
    route_prefix,
)

MODEL_AND_EXTERNAL_CAPABILITIES: tuple[CapabilityManifestEntry, ...] = (
    # ── qwen_model：真实 Qwen/百炼能力 ──────────────────────────────────
    CapabilityManifestEntry(
        id="chat_daily",
        journey="普通聊天",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
        route_patterns=(
            exact_route("/chat/first-turn"),
            exact_route("/chat/conversations/*/messages", "POST"),
            exact_route("/chat/conversations/*/messages/*/retry", "POST"),
            exact_route("/chat/conversations/*/messages/*/stop", "POST"),
        ),
        chat_actions=("chat",),
        truth_contract="真实 qwen_text_chat，固定文本模型，消息与运行锁可追溯。",
    ),
    CapabilityManifestEntry(
        id="qwen_model_configuration",
        journey="Qwen 凭据验证与主模型配置验证",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_text_chat",
        route_patterns=(
            exact_route("/settings/credentials/qwen", "PUT"),
            exact_route("/settings/models", "PUT"),
        ),
        truth_contract="设置流程会访问百炼元数据并执行真实能力探测，不等同于聊天生成。",
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
        journey="文章人味化专用编排（Issue 21 整体退役）",
        category=CapabilityCategory.RETIRED,
        retired_capability_names=("bridges-humanizer",),
        chat_actions=("humanizer",),
        truth_contract=(
            "SKILL 编排与评测框架已移除；带 ``bridges-humanizer`` 载荷的"
            "写入稳定返回 410（``humanizer_capability_retired``），历史"
            "结果与过程投影按只读面继续可查可导出。"
        ),
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
            exact_route("/chat/conversations/*/attachments/*/ingestion"),
            exact_route("/knowledge-base/materials/*/retry"),
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
            route_prefix("/chat/ingestion"),
            exact_route("/knowledge-base/materials/*/rebuild"),
        ),
        chat_actions=("kb_embedding",),
        truth_contract="固定能力、固定模型，批次或页面调用锁与业务对象关联。",
    ),
    CapabilityManifestEntry(
        id="image_generate_edit",
        journey="图片生成/编辑（Issue 21 退役公开入口，供应商能力保留）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_image",
        chat_actions=("image_generate", "image_edit"),
        truth_contract=(
            "公开提交/取消/重试/删除入口已退役为 410（见 legacy_image_writes）；"
            "适配器与真实供应商探针保留在发布门，历史结果只读可导出。"
        ),
    ),
    CapabilityManifestEntry(
        id="image_cancel",
        journey="图片供应商取消（Issue 21 退役公开入口）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_image",
        chat_actions=("image_cancel",),
        truth_contract="公开取消入口已退役为 410；供应商取消探针仅在发布门内驱动。",
    ),
    CapabilityManifestEntry(
        id="image_alt_text",
        journey="图片视觉替代文本（Issue 21 退役公开入口）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_vision",
        chat_actions=("image_alt_text",),
        truth_contract="公开替代文本写入入口已退役为 410；历史替代文本仍随资产只读展示。",
    ),
    CapabilityManifestEntry(
        id="video_generate",
        journey="视频生成（Issue 21 退役公开入口，供应商能力保留）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_wan",
        chat_actions=("video_generate",),
        truth_contract=(
            "公开提交/取消/重试/删除入口已退役为 410（见 legacy_video_writes）；"
            "适配器与真实供应商探针保留在发布门，历史结果只读可导出。"
        ),
    ),
    CapabilityManifestEntry(
        id="video_cancel",
        journey="视频供应商取消（Issue 21 退役公开入口）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_wan",
        chat_actions=("video_cancel",),
        truth_contract="公开取消入口已退役为 410；供应商取消探针仅在发布门内驱动。",
    ),
    CapabilityManifestEntry(
        id="asr_short",
        journey="ASR 短音频转写",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_asr_short",
        route_patterns=(exact_route("/chat/conversations/*/dictation"),),
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
        journey="回答朗读 TTS（Issue 21 退役公开入口，适配器保留）",
        category=CapabilityCategory.QWEN_MODEL,
        model_capability="qwen_tts",
        chat_actions=("tts_narration",),
        truth_contract=(
            "公开生成/停止入口已退役为 410（见 legacy_read_aloud_writes）；"
            "适配器与真实语音探针保留在发布门，历史音频只读可回放与导出。"
        ),
    ),
    # ── external_non_qwen：不消费 Qwen Key 的外部能力 ────────────────────
    CapabilityManifestEntry(
        id="tavily_web_search",
        journey="Tavily 通用网页搜索",
        category=CapabilityCategory.EXTERNAL_NON_QWEN,
        route_patterns=(exact_route("/settings/credentials/tavily", "PUT"),),
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
    CapabilityManifestEntry(
        id="amap_commute_services",
        journey="高德通勤凭据验证与地图数据请求",
        category=CapabilityCategory.EXTERNAL_NON_QWEN,
        route_patterns=(
            exact_route("/settings/credentials/amap/web-service", "PUT"),
            exact_route("/settings/credentials/amap/browser-map", "PUT"),
            exact_route("/commute/amap-proxy/*", "GET"),
        ),
        truth_contract="服务端会调用高德验证凭据或代理受限范围内的地图数据请求。",
    ),

)
