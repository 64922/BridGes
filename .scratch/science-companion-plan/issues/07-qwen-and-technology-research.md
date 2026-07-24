# 核验 Qwen 能力并形成候选技术栈

Type: research
Status: resolved
Blocked by: 01

## Question

基于当前阿里百炼官方能力与成熟开源生态，应如何选择 Qwen 文本、视觉、OCR、ASR、TTS、Embedding、Rerank 和评测模型，以及前后端、工作流、RAG、数据库、向量库、对象存储、沙箱、可观测性与部署技术？如何以 Conda 环境 `agent` 作为开发基准，并兼容 Docker、Podman、手动安装和统一 CLI 启动？哪些版本和接口限制会影响设计，候选方案的取舍依据是什么？

## Answer

完整核验与候选栈见 [Qwen 能力核验与候选技术栈](../research/07-qwen-and-technology-stack.md)。

结论是以版本化能力注册表和模型网关隔离 Qwen 的时效性变化，主选 `qwen3.7-plus`/`qwen3.6-flash`、`qwen-vl-ocr`、Qwen3 ASR/TTS、`text-embedding-v4` 与 `qwen3-rerank`，并通过运行探测冻结实际可用组合；原灵感材料中的 `qwen3.5-ocr` 已按当前官方型号纠正为 `qwen-vl-ocr`。工程候选采用 Next.js/TypeScript 前端、Python/FastAPI 模块化单体、PostgreSQL + pgvector、S3 兼容对象存储、自有证据与类型化工作流协议、可替换 LlamaIndex/Temporal/LangGraph 适配器、强隔离沙箱和 OpenTelemetry。Conda `agent` 只承载本地开发工具链；生产的手动分进程、统一 CLI、Docker 与 Podman 必须共享配置、迁移、健康检查、存储和命令语义；最终组件与版本须在票据 11 中依据十二类验证 spike 决定。
