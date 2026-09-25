# 固定 BridGes 模型与供应商矩阵

> 适用边界：本矩阵仍是批准模型与工厂默认值的单一事实源，非可配置能力（Embedding、语音转写与朗读、图像生成与编辑、文生视频、公开搜索）继续只走本矩阵。原先「用户不能更换模型」对主对话、视觉识别与 OCR 的适用性已由 [ADR-0031](0031-runtime-qwen-credential-and-main-model-activation.md) 取代：这三个可交互能力允许用户在设置页手填模型 ID，经元数据核验与真实探测后激活；Embedding 仍受 [ADR-0008](0008-contract-locked-embedding-alias.md) 约束。

BridGes 将核心对话、推理、视觉理解与工具调用固定为 `qwen3.7-plus-2026-05-26`，知识库向量化固定为 `text-embedding-v4`（1024 维），语音转写固定为 `qwen3-asr-flash`，单条回答朗读固定为 `qwen3-tts-flash-2025-11-27`，图片生成与编辑固定为 `qwen-image-2.0-pro-2026-06-22`，文生视频固定为 `wan2.7-t2v-2026-06-12`，联网搜索固定使用无需密钥的 DuckDuckGo。首版不另设 OCR 或重排模型：文档优先直接提取文本，图片与图表交由核心多模态模型理解，检索采用关键词与向量结果融合。除 DuckDuckGo 外，全部云端智能能力共用全局百炼运行凭据（ADR-0024，取代账户级百炼密钥）；账户级能力探测已随 GQ-06/GQ-07 整体移除，固定模型常量由 ai/fixed_models.py 单一事实源提供。用户不能更换模型，失败只重试同一绑定，模型不可用时明确停用对应能力，不得静默降级。

## Issue 02：公开搜索提供方顺序与注册表

DuckDuckGo 仍是默认主用提供方。只有部署显式设置 `BRIDGES_PUBLIC_SEARCH_FALLBACK_ENABLED=true` 时，才启用注册表中的 `brave_search` 结构化备用适配器；其版本固定为 `brave-search-api-v1`，请求端点固定为 Brave Search API，不能由用户或请求参数提供任意 URL。备用凭据只从全局 `BRIDGES_BRAVE_SEARCH_API_KEY` 或对应 `_FILE` 引用注入，缺少凭据、未登记标识或配置任意端点时启动失败，不静默换源。

每个结果和搜索投影都保存实际提供方及版本；聊天搜索卡、教学引用、模型上下文和审计只使用这些脱敏字段披露主用/备用选择，不复制凭据、账户信息、会话原文或搜索正文。停用开关后恢复为 DuckDuckGo 主用加显式可重试失败，不引入未登记提供方。
