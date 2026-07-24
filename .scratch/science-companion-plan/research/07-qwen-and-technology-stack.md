# Qwen 能力核验与候选技术栈

核验日期：2026-07-24  
结论性质：票据 07 的技术候选与验证约束；票据 11 才能依据验证结果确认最终系统架构和版本基线。

## 1. 结论

本项目应以一个可拆分的模块化单体起步，但目标不是 MVP：完整实现身份、科学项目空间、证据链、画像与记忆、学习、多模态、评测和运维等成熟能力，只是不在边界尚未稳定时把它们过早拆成网络微服务。

本票推荐的主技术栈候选是：

| 层 | 本票主选 | 边界 |
| --- | --- | --- |
| Web 前端 | Next.js + React + TypeScript，pnpm 管理 | 只经后端 BFF/API 访问业务数据，不直接持有百炼密钥 |
| 后端 | Python + FastAPI + Pydantic + SQLAlchemy + Alembic | 领域模型、权限、质量门和工作流状态均属于本地权威系统 |
| 权威数据 | PostgreSQL | 账户、项目、claim、证据、画像、授权、工作流、评测与审计的唯一事务真相 |
| 检索 | PostgreSQL 全文检索 + pgvector + 关系表/递归查询 | 先满足词法、向量和关系联合检索；不因“图谱”名义提前引入图数据库 |
| 文件 | S3 兼容对象存储；开发期可用本地适配器 | 数据库只保存对象键、内容哈希、归属、版本和策略 |
| 临时设施 | Redis | 限流、短缓存、事件通知；不得保存权威画像、工作流状态或用户原文 |
| RAG | 自有证据协议为核心，LlamaIndex 作为摄入/检索适配器 | LlamaIndex Node、Index 或 Retriever 不能成为稳定领域对象 |
| 智能体编排 | 自有类型化工作流合同；LangGraph 与 Temporal 均进入 spike | 票据 08 决定编排合同，票据 11 决定是否引入持久工作流运行时 |
| 可观测 | OpenTelemetry 协议与 SDK | trace、metric、log 使用同一 run/tenant/user/project 关联语义，并做敏感字段裁剪 |
| 测试 | pytest + Playwright + 前端单元/组件测试 | 规则、权限和迁移先测；模型测试固定快照、数据集和统计阈值 |
| 本地工具链 | Conda 环境 `agent` + `pyproject.toml` + Python 锁文件；Node + pnpm lock | Conda 管运行时/native 依赖，Python 项目文件管包，pnpm lock 管前端可复现 |
| 部署 | 手动、统一 CLI、Docker Compose、Podman Compose 兼容清单 | 四种路径共享配置、迁移、健康检查、存储布局和启动合同 |

这里的“主选”表示后续架构设计默认从它出发，不等于现在就冻结依赖。票据 11 只有在本文列出的验证 spike 全部形成证据后，才可锁定版本、组件与部署拓扑。

核心架构边界是：

> Qwen 提供推理、理解、语音和向量能力，但不持有用户权威状态；LlamaIndex/LangGraph/Temporal 提供可替换的技术能力，但不定义账户、证据、画像、科学媒体对象或发布门。

## 2. Qwen 能力注册表

模型注册表必须是数据库或版本化配置中的运行资产，而不是散落在提示词和业务代码里的字符串。每条注册记录至少包含：

- 逻辑能力名、供应商、区域、端点协议、稳定别名与已验证快照；
- 支持的输入模态、最大输入约束、思考模式、工具调用和结构化返回实测结果；
- 单价快照来源与日期、RPM/TPM/QPS 快照、项目侧并发预算；
- 数据分类许可、超时、重试、降级、熔断与失败闭锁策略；
- 能力探测时间、探测用例版本、返回 Schema 哈希和当前启用状态。

### 2.1 任务路由

| 任务 | 主模型 | 备选 | 接口与模式 | 限制与失败策略 |
| --- | --- | --- | --- | --- |
| 普通对话、教学、规划、科学解释 | `qwen3.7-plus` | `qwen3.6-flash` | OpenAI 兼容 Chat Completions；按任务启用思考 | 稳定别名承载交互流量；备选必须同区域并通过同一回归集；展示实际路由 |
| 分类、查询改写、候选画像提取、格式转换 | `qwen3.6-flash` | 同模型延迟重试 | 非思考；工具参数或 JSON 结果经 Pydantic 二次验证 | Schema 不合格不得去掉约束后继续，也不得直接写画像 |
| 高难度科学推理/冲突候选分析 | `qwen3.7-plus` | 人工复核或延迟处理 | 思考模式只产生候选判断 | 不能绕过 claim—evidence 验证；高风险结论无证据则 `BLOCKED` |
| 图片、公式、图表和视频理解 | `qwen3.7-plus` | `qwen3.6-flash`（仅在能力探测通过后） | 多模态 Chat Completions | 保留原文件；对页码/坐标/帧建立绑定；模型解释不是原始证据 |
| 文档、表格、试卷、手写内容 OCR | `qwen-vl-ocr` | 视觉主模型做受限提取 | OpenAI 兼容或 DashScope；结构化提取 | OCR 结果是可纠正派生资产，低置信字段禁止进入事实锁 |
| 按键录音转写 | `qwen3-asr-flash` | 保留音频、允许重试或人工编辑 | 非实时 HTTP/OpenAI 兼容 | 官方表中单次上限为 5 分钟/10 MB；发送前让用户校对 |
| 长录音转写 | `qwen3-asr-flash-filetrans` | 排队等待，不换模型污染同一转写 | 异步 HTTP | 官方表中上限 12 小时/2 GB；对象必须有稳定可访问策略和过期处理 |
| 实时字幕候选 | `qwen3-asr-flash-realtime` | 回退到录音后转写 | WebSocket | 不作为首条必经链；断线要保留本地音频和已确认片段 |
| 回答朗读 | `qwen3-tts-flash` | `qwen3-tts-instruct-flash`（需指令控制时） | HTTP；实时版另走 WebSocket | 输出 URL 有时效，立即转存到受控对象存储；公式、数字和缩写必须回归 |
| 文本向量 | `text-embedding-v4`，默认候选 1024 维 | `text-embedding-v3` 仅用于兼容旧索引 | Embeddings API | 模型、维度、规范化或分块变化即新建索引版本，禁止混写 |
| 多模态向量 | `qwen3-vl-embedding`（按需） | 不启用该检索能力 | Embedding API | 仅在文本搜图/跨模态检索验证胜过元数据检索后启用；独立索引类型 |
| 文本重排 | `qwen3-rerank` | 原始融合排序 | Rerank API | 降级时明确 `rerank_status=skipped`；不得假装结果等价 |
| 多模态重排 | `qwen3-vl-rerank`（按需） | 分模态检索后规则融合 | Rerank API | 官方上限依文档类型不同；上线前以实际混合负载探测 |
| 离线 LLM 裁判 | `qwen3.7-max-2026-06-08` 固定快照 | 固定快照 `qwen3.7-plus` | 非思考、温度固定、强制评分工具参数 | 规则指标优先；生成模型不自证正确；模型下线前完成双跑迁移 |

官方视觉文档显示 `qwen3.7-plus` 可接收文本、图像和视频；专用 OCR 文档确认 `qwen-vl-ocr` 支持文本、结构化数据和关键信息抽取。ASR 官方模型表区分非实时、文件转写和实时 WebSocket；TTS 官方模型表区分系统音色、指令控制、声音复刻和实时接口。项目只启用完成产品任务所需的子集，不因为供应商提供了某能力就默认采集或生成更多媒体。

### 2.2 Function Calling、结构化输出与工具边界

官方 OpenAI 兼容 Chat API 接受 `tools`，函数参数使用 JSON Schema。但“接口接受 Schema”不等于“任意模型、任意思考模式和多模态输入都稳定满足 Schema”。因此：

1. 工具调用结果总是候选数据，必须经过 Pydantic 严格模式、枚举、范围、跨字段不变量和权限检查；
2. 在上线前分别探测主模型/备选、思考/非思考、纯文本/视觉、流式/非流式组合；
3. `Schema 400` 或参数校验失败属于兼容性故障，有限修复后失败，不得移除工具约束让模型自由文本继续写状态；
4. 供应商内置联网、文件问答、代码解释器、会话记忆和智能体工具全部禁用。检索、引文、工具结果、记忆切片、权限和沙箱必须经过本地协议；
5. 不保存或展示思维链。系统只保存输入版本、结构化输出、工具调用、验证结论和必要的可解释摘要。

### 2.3 区域与端点

默认候选区域为华北 2（北京）。百炼官方 Base URL 总览显示，业务空间专属 OpenAI 兼容端点为：

`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`

新加坡业务空间端点为：

`https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`

区域不是可随意切换的负载均衡标签。API Key、Workspace、模型可用范围、数据处理地域和 Base URL 必须绑定为一个部署配置；禁止遇到错误后自动跨区。启动探测只发送固定合成文本，不发送用户材料。Coding Plan/Token Plan 官方说明限定为交互式 AI 工具使用，不能作为产品后端端点。

## 3. 价格、限流与更新机制

### 3.1 2026-07-24 快照

价格和限流页面持续变化，以下只用于容量模型，不是账单真值：

| 项目 | 官方页面当日可见事实 | 使用方式 |
| --- | --- | --- |
| `qwen3.7-plus` 北京区、输入不超过 256K | 原价输入 ¥2/百万 Token；非思考与思考输出均 ¥8/百万 Token；页面另有时效促销 | 成本估算使用原价字段，促销独立记录有效期 |
| `qwen3.7-max-2026-06-08` 北京区 | 输入 ¥12/百万 Token，输出 ¥36/百万 Token | 只作低并发固定版评测候选 |
| `qwen3.7-plus` 稳定别名北京区 | 页面显示 30,000 RPM / 5,000,000 TPM | 项目并发预算必须明显低于供应商上限 |
| `qwen3.6-flash` 稳定别名北京区 | 页面显示 30,000 RPM / 10,000,000 TPM | 仅为快照，不作为常量 |
| 固定日期版常见限制 | 页面中多项固定版为 600 RPM / 1,000,000 TPM | 固定评测队列单独限速 |

百炼按主账号聚合 RAM 子账号、业务空间与 API Key 的调用量；拆 Key 不能获得独立配额。官方也提示可能按 RPS/TPS 约束突发流量，故不能只按分钟平均值设计。Embedding、Rerank、OCR、ASR、TTS 的单价和限流若未能由自动快照稳定提取，不在此猜测，列入部署前运行探测。

### 3.2 更新机制

- CI 每周抓取官方模型、价格、限流和上下架页面，保存原始响应哈希、抓取时间和解析结果，变化只生成待审批差异；
- 生产启动执行无用户数据的模型可用性、Schema、区域和权限探测；价格不从 API 响应推断；
- 控制台维护“估算价格快照”和“账单实耗”两条数据，前者服务路由，后者服务财务对账；
- 路由器使用项目侧 token bucket、并发信号量、平滑升速、有限队列、`Retry-After`、指数退避和抖动；
- 401/403、区域错误、余额或权限错误不重试；429 在同模型有限重试后才走已经验证的同区备选；安全拒绝不换模型规避；
- 每个调用记录模型实际 ID、区域、输入/输出 usage、缓存命中、重试、时延、估算成本和结果状态。

## 4. 技术栈取舍矩阵

| 能力 | 主选候选 | 备选/验证项 | 暂不采用及理由 |
| --- | --- | --- | --- |
| 前端 | Next.js、React、TypeScript、pnpm | Vite SPA：若 SSR/路由服务能力无收益 | 不用 Python UI 框架承载成熟工作台；复杂状态、无障碍和组件生态不足 |
| 后端 API | FastAPI、Pydantic | Django：若后台管理和内建认证收益显著 | 不把模型 SDK 直接暴露给浏览器 |
| ORM/迁移 | SQLAlchemy + Alembic | SQLModel 只可在局部验证 | 不用 ORM 自动建表代替版本化迁移 |
| 认证 | 后端账户域、Argon2id 密码、验证邮件、短期 HttpOnly 安全会话/刷新轮换；OIDC 适配 | Keycloak/托管 OIDC 在机构租户票据中验证 | 不把 localStorage bearer token 作为默认；不依赖社交登录才能使用 |
| 多用户隔离 | 应用层 tenant/user 条件 + PostgreSQL RLS 双层 | 独立数据库仅用于高隔离机构部署 | 不以“开发者记得加 WHERE”作为隔离保证 |
| 数据库 | PostgreSQL | SQLite 只允许单机测试和临时开发，不是正式多人后端 | 不拆多个专用数据库作为第一版权威状态 |
| 向量检索 | pgvector | Qdrant：规模/过滤/性能 spike 证明需要后再引入 | 不让云知识库持有私人权威索引 |
| 词法检索 | PostgreSQL FTS，中文分词插件/外部分析器需验证 | OpenSearch：大语料和检索运维成熟后 | 不用纯向量替代术语、编号、公式和精确引用检索 |
| 关系检索 | PostgreSQL 关系表、递归 CTE、物化视图 | Neo4j：复杂图遍历有实测收益时 | “claim 图谱”不自动等于需要图数据库 |
| 对象存储 | S3 兼容 API；生产对象存储，开发本地适配 | MinIO 自托管 | 不在数据库大字段中长期保存所有原件 |
| 缓存/信号 | Redis | PostgreSQL advisory lock/NOTIFY 可覆盖低负载 | Redis 不作权威队列状态和长期记忆 |
| 持久工作流 | 自有工作流合同；Temporal 作为主 spike | PostgreSQL 状态机；LangGraph checkpointer | 不允许 Celery 任务结果冒充完整工作流语义；不以自由 Agent loop 取代状态机 |
| Agent 局部图 | LangGraph 作为可替换适配器 | 自研小内核 | LangGraph checkpoint 不能成为画像、证据或项目权威表 |
| RAG | LlamaIndex 适配摄入、节点、检索与后处理 | 自研最小 pipeline | 不采用供应商托管知识库绕过 claim 级协议 |
| 沙箱 | 正式 Linux 隔离执行服务：无网、只读根、临时卷、非 root、cap drop、seccomp/AppArmor、资源/时间限制；并验证 gVisor/Kata/微虚机 | rootless Podman 作外围容器运行时 | 普通 Docker 容器不是对敌意代码的完整安全边界 |
| 多模态 | Qwen 适配器 + 本地解析器 + 版本化科学媒体对象 | 专用开源模型按领域包评测引入 | 不把模型输出二进制成品视为可追溯源 |
| 可观测 | OpenTelemetry + 可替换采集/存储后端 | Prometheus/Grafana/Loki/Tempo 组合 | 不把供应商专有 tracing ID 作为唯一 run 身份 |
| 评测 | pytest 数据集/规则 + 固定 Qwen 裁判 + 专家与用户盲测 | 专用评测框架经票据 10 选择 | 不用生成模型自评作为发布唯一门 |
| 浏览器测试 | Playwright | 无 | 不用人工点击替代认证、隔离、恢复和无障碍回归 |
| Python 测试 | pytest + property/contract/integration 测试 | Hypothesis 在不变量处使用 | 不把真实云调用放进每次单元测试 |
| CLI | Python Typer/Click 风格入口，具体库由票据 11 锁定 | 跨平台 Node supervisor | 不用仅适用于 Unix 的 shell 脚本作为唯一入口 |
| 包管理 | Conda + `pyproject.toml` + Python 锁文件；pnpm | 纯 uv 仅作 CI/容器优化 | 不让 `environment.yml` 同时重复维护全部 Python 精确版本 |

## 5. 稳定领域模型不得绑定框架

数据库与领域包必须保存项目自己的稳定对象：`Document`、`Chunk`、`Claim`、`EvidenceItem`、`Citation`、`ProfileAssertion`、`MemorySlice`、`ScientificMediaObject`、`WorkflowRun`、`QualityGateResult`。框架对象只能出现在适配层。

- LlamaIndex 的 Node、Retriever、Postprocessor 和索引缓存可帮助摄入与检索，但其序列化格式不得成为数据迁移合同；
- LangGraph 的 State、thread/checkpoint 可帮助局部 Agent 图恢复，但项目 run ID、步骤状态和人工确认仍由自己的工作流合同定义；
- Temporal 若通过 spike，负责可靠执行、重试、定时器和信号，不拥有领域记录；活动必须使用幂等键；
- pgvector 是向量索引实现，embedding 版本、维度、chunk 版本和重建状态属于项目索引清单；
- 替换任何框架时，claim—evidence—wording、授权、租户隔离和发布门语义不得变化。

这样可以避免技术升级迫使用户数据迁移，也允许票据 11 根据 PoC 结果替换候选。

## 6. 本地 Conda、包管理与跨平台开发合同

本地开发基准环境名称固定为 `agent`。Conda 不进入生产运行时；下列文件各负其责：

| 文件 | 职责 | 禁止承担 |
| --- | --- | --- |
| `environment.yml` | Python/Node 等运行时大版本、系统级/原生库和 Conda channel；声明环境名 `agent` | 不重复锁定所有 PyPI 间接依赖 |
| `pyproject.toml` | Python 包元数据、直接依赖、可选依赖组、CLI entry point、lint/test 配置 | 不保存密钥、区域或部署环境配置 |
| Python 锁文件 | 解析后的完整 Python 依赖图与哈希 | 不手工编辑 |
| `package.json` / `pnpm-lock.yaml` | 前端脚本、直接依赖和完整 Node 依赖锁 | 不启动数据库迁移或复制后端配置 |

Windows 开发要求：

- 路径、换行、进程终止、文件锁、长路径和 UTF-8 中文建立跨平台测试；
- CLI 用 Python 进程管理 API 启动前后端，不依赖 `bash`、`make`、`fork` 或 Unix signal 的唯一语义；
- 不在 Windows 本机运行未受信生成代码。开发时要么使用固定可信示例，要么发送到受控 Linux 沙箱；
- 正式生产与沙箱基线为 Linux。Windows 只保证开发、测试和普通应用进程，不宣称与 Linux 内核隔离等价。

## 7. 本地开发与四种生产部署共享一个应用合同

Conda `agent` 只用于本地开发和测试。生产的四种部署方式不是四套产品：

1. **手动分进程部署**：使用受支持的系统 Python/独立虚拟环境和 Node 运行时安装锁定产物，准备 PostgreSQL/对象存储等外部设施，分别执行统一迁移、API、worker 和 Web 启动命令；生产不要求安装 Conda；
2. **统一 CLI 部署**：一个项目 CLI 子命令同时监管后端和前端，转发日志，在任一关键进程失败时返回非零状态，并能优雅终止子进程；
3. **Docker**：使用 Compose Specification 清单启动 web、api、worker、database、redis、object-store 等被确认的服务；
4. **Podman**：复用同一 Compose 兼容清单或由同一配置生成，不维护语义不同的 Podman 专版。

共同合同必须包括：

- 同一环境变量名、配置 Schema、默认值和优先级；
- 同一 `migrate`、`check`、`serve`、`worker` 和 `doctor` 语义；
- `/health/live` 只判断进程存活，`/health/ready` 验证数据库迁移、对象存储和必要依赖；Qwen 不可用时按功能降级，不一定使本地浏览不可用；
- 迁移在受控一次性步骤执行，不由多个 API worker 竞态启动；
- 配置与密钥分离，容器 secret/file reference 和本机凭据适配到同一设置对象；
- 数据卷、对象键、备份、恢复和删除语义一致；
- Compose 使用健康检查与依赖就绪，不依赖固定 sleep；
- 镜像固定 digest、非 root、只读文件系统（必要目录显式可写），生成 SBOM 并做漏洞扫描。

一个 CLI “同时启动前后端”不代表把它们塞进一个生产进程。开发命令是 supervisor；生产 Compose 仍将 web、api 和 worker 分开扩缩。

## 8. 沙箱安全边界

生成 HTML/JS、数据处理代码或动画代码属于潜在敌意工作负载。普通容器共享宿主内核，配置错误、内核漏洞、挂载或容器套接字都可能突破边界，因此“在 Docker 里跑了”不能作为安全验收。

正式验证门：

1. 沙箱节点与主数据库、密钥服务、内部网络隔离，默认无出网；
2. 输入通过只读对象副本，输出只允许写入独立临时卷并经内容扫描后导入；
3. 非 root、user namespace、drop all capabilities、no-new-privileges、只读根、seccomp/AppArmor/SELinux；
4. CPU、内存、进程数、文件数、磁盘、输出大小和 wall time 均有硬上限；
5. 不挂载 Docker/Podman socket，不传 Qwen 密钥、数据库 DSN 或用户长期凭据；
6. 验证 gVisor、Kata Containers 或微虚机类额外隔离，并做逃逸、网络、资源耗尽和残留数据测试；
7. 高风险或无法隔离的代码保持“不可运行草稿”，等待人工或专用执行环境。

Windows 开发机只做静态检查和可信测试；真正的对抗执行必须使用 Linux 隔离节点。Podman rootless 能降低宿主风险，但仍不能自动满足全部隔离门。

## 9. 本地优先与云 Qwen 数据流

数据流固定为：

`本地/项目原件 → 本地解析和敏感性分类 → 权限及租户检查 → 证据/画像最小切片编译 → 云 Qwen → 结构化验证 → 本地权威写入`

约束如下：

- API Key 只在后端模型网关读取，按部署使用 secret file、操作系统凭据或密钥管理服务；永不下发浏览器、写日志或放入导出包；
- 每次上云形成 `ContextManifest`：任务、用户/租户、所含对象 ID、选择原因、授权、区域、保留策略、hash 和 token 预算；
- 默认不上云完整画像、完整聊天历史、全库索引、未选中的文档页和其他用户材料；
- OCR/ASR 可在用户确认或敏感策略允许后调用；拒绝上云时保留本地编辑/浏览能力并标记能力不可用；
- 供应商返回的临时媒体 URL 必须在有效期内转存，转存前验证 MIME、大小与归属；
- 跨区域调用必须是部署级显式决策，不能由路由器在运行时静默发生。

## 10. 版本、迁移与回滚

- 依赖按 lock file 和容器 digest 固定；模型稳定别名只用于在线流量，但每次响应记录实际模型标识；
- 固定模型快照用于可复现评测；稳定别名升级通过旧/新双跑、回归集、成本/延迟比较和人工批准；
- 每项模型都有启动 smoke probe 与定期 capability probe，覆盖 Schema、工具、多模态、最大输入边界和错误码；
- embedding 模型、维度、归一化、分块器或元数据策略变化时创建新索引版本，后台重建、双读评测、原子切换；不能原位混写；
- 数据库使用 Alembic 前向迁移、备份恢复演练和 expand/contract 兼容窗口；破坏性迁移必须有导出与回滚计划；
- 工作流、提示词、Schema、领域包、规则和评测数据集各自版本化，运行记录引用精确版本；
- 回滚包括应用镜像、模型路由、提示词/Schema、领域包和索引指针，不承诺不可逆数据库迁移的“一键回滚”；
- 模型下架公告触发迁移任务，不在下架当天临时换别名。

## 11. 性能、容量、成本与降级

容量模型按任务而不是只按 DAU：

`总成本 = 文本输入/输出 Token + 视觉/音频调用 + embedding + rerank + 重试 + 离线评测 + 存储/出网 + 沙箱计算`

每类任务测量 P50/P95/P99 延迟、排队时间、调用次数、输入输出量、缓存命中、重试率、降级率和单位成功任务成本。容量测试至少覆盖普通教学回合、长 PDF 摄入、混合检索、科学表达长文、多模态创作、并行项目和夜间评测。

错误分级：

| 故障 | 降级 |
| --- | --- |
| 云 Qwen 离线 | 开放本地浏览、编辑、历史、证据查看；模型功能显示离线，不伪造完成 |
| 文本主模型 429/5xx | 有界重试后使用已验证同区备选，并展示实际模型 |
| 结构化输出失败 | 有限局部修复；仍失败则该步骤失败，不写权威状态 |
| Embedding 失败 | 暂停该索引版本、可恢复；禁止换模型续写同一空间 |
| Rerank 失败 | 返回融合初排并标记未重排；高风险发布仍须证据门 |
| OCR/ASR 失败 | 保存原件和草稿，允许人工纠正或重试 |
| TTS 失败 | 文本回答仍可交付，媒体对象标记合成失败 |
| PostgreSQL/权限/RLS 异常 | 全部状态写入和私人读取失败闭锁 |
| Redis 异常 | 关闭缓存并降低并发；权威状态不丢失 |
| 沙箱不可用 | 只交付不可运行设计/代码草稿，不标记媒体完成 |

缓存键必须含 tenant/user、授权版本、内容 hash、模型和 Schema 版本；私人结果不得跨用户复用。

## 12. 架构确认前的验证 spikes

这些 spike 不是 MVP，而是成熟系统的技术证据门：

1. **Qwen Schema/工具**：主备文本模型在思考/非思考、流式/非流式下运行 200+ 结构化样例，统计合法率、语义约束率和修复率；
2. **Vision/OCR**：公式、表格、图表、手写试卷、复杂 PDF 页面，验证坐标/页码绑定和人工纠错；
3. **ASR**：普通话、方言、中英混合、术语、噪声、5 分钟边界和长文件转写；
4. **TTS**：数字、公式、单位、英文缩写、长文分段、临时 URL 转存和无障碍播放器；
5. **Hybrid retrieval**：PostgreSQL FTS + pgvector + Rerank 对比向量-only、词法-only；以 claim 支持率而非相似度定胜负；
6. **RLS**：应用层遗漏过滤、后台 worker、缓存、向量查询、导出、删除和管理员路径的跨用户对抗测试；
7. **持久工作流**：PostgreSQL 状态机、Temporal、LangGraph checkpoint 对长任务、人工暂停、重放、幂等副作用和版本升级比较；
8. **沙箱**：Windows 开发禁用策略，Linux rootless 容器与 gVisor/Kata/微虚机候选的逃逸面、资源限制和残留数据测试；
9. **环境与部署矩阵**：本地 Conda `agent` 单独验证开发可复现性；全新生产主机分别完成手动分进程、统一 CLI、Docker Compose、Podman Compose，核对迁移、健康、日志和恢复；
10. **跨平台**：Windows 与 Linux 路径、编码、进程终止、长文件名、Node/Python 版本和 Playwright；
11. **索引迁移**：embedding 维度或分块版本变化时双索引重建、双读、切换、回滚；
12. **故障演练**：429、区域错误、数据库中断、Redis 丢失、对象 URL 过期、worker 崩溃和模型下架。

每个 spike 必须输出可复现环境、数据集版本、通过阈值、测量结果、失败样例和“采纳/调整/拒绝”结论，不能只展示 happy path。

## 13. 对本地灵感的裁决

| 灵感 | 裁决 | 本项目落点 |
| --- | --- | --- |
| Qwen routing 的能力/风险路由、区域锁、显式降级、带日期注册表 | 直接采用 | 模型网关与能力注册表 |
| 其中 `qwen3.5-ocr` 型号 | 调整 | 2026-07-24 官方文档明确采用 `qwen-vl-ocr`；旧笔记型号不得沿用 |
| 稳定别名承载在线、固定快照承担评测 | 调整采用 | 在线响应记录实际 ID；固定版仍需下架迁移 |
| 价格/限流硬编码 | 舍弃 | 官方快照 + 运行探测 + 配置审批 |
| LlamaIndex 的分层摄入、节点关系、融合检索、重排 | 直接采用其机制 | 以适配器实现，不让框架对象进入稳定领域模型 |
| AutoAgent 的 Schema 中间件、注册表、有限修复 | 直接采用 | 工具/工作流合同与验证门 |
| AutoAgent 动态生成并热加载未知代码 | 舍弃 | 只允许版本化、审查和沙箱验证后的组件 |
| Claude Code 的 Hook、工具前后拦截、审查/验证分离 | 直接采用机制 | 模型网关、权限、质量门和审计事件 |
| 反复调用直到模型自报完成 | 舍弃 | 有限重试 + 结构化完成条件 |
| nanobot 的小内核、状态机、上下文组合 | 调整采用 | 自有工作流合同和最小记忆切片 |
| JSONL/Markdown 作为多人权威存储 | 舍弃 | PostgreSQL + 对象存储；文本仅作导出 |

## 14. 对后续票据的硬约束

### 票据 08：智能体编排

- 必须基于类型化输入输出、幂等键、有限重试、人工中断、质量门和可回放事件；
- 比较 Temporal、LangGraph 和 PostgreSQL 状态机，不得把框架 checkpoint 当领域真相；
- 所有工具经本地注册表，供应商内置工具不能绕过 ToolResult、权限和证据协议。

### 票据 09：前端信息架构

- 所有功能页认证后访问；必须显示模型实际路由、证据/画像使用、工作流状态、降级和人工确认；
- 长任务用可恢复 run，而不是靠页面保持连接；
- OCR/ASR 可编辑中间件、TTS/媒体无障碍和失败草稿必须可见。

### 票据 10：持续评测

- 固定模型快照、提示词、Schema、领域包和数据集版本；
- 规则、专家、用户盲测优先于模型自评；记录成本和方差；
- 加入模型升级双跑、RAG 消融、跨用户隔离、沙箱和四部署路径回归。

### 票据 11：系统架构与治理

- 只有 spike 通过后才能锁定 Temporal/LangGraph、对象存储实现、认证组件和版本；
- 保持模块化单体与独立 worker 边界，按测量结果而非团队人数拆微服务；
- 四种部署共享配置、迁移、健康和 CLI 合同。

### 票据 12：首批领域包

- 领域包只能通过公共证据、工具、模型与评测接口扩展；
- 可声明某领域允许的 Qwen 路由和工具，但不能直接访问密钥、数据库或绕过发布门；
- 模型变化不得改变领域证据强度语义。

### 票据 13：协作与机构租户

- tenant/user/project/share scope 必须贯穿数据库、RLS、对象键、索引、缓存、工作流和观测；
- OIDC/SSO、组织角色和共享空间不能破坏个人画像与私人材料边界；
- 跨区、跨租户或跨用户访问必须是显式授权事件。

## 15. 一手来源、置信度与时效

### 15.1 阿里云百炼官方

- [模型大全](https://help.aliyun.com/zh/model-studio/models)
- [视觉理解与模型选择](https://help.aliyun.com/zh/model-studio/vision-model/)
- [Qwen-OCR](https://help.aliyun.com/zh/model-studio/qwen-vl-ocr)
- [Qwen-OCR API](https://help.aliyun.com/en/model-studio/qwen-vl-ocr-api-reference)
- [语音识别模型](https://help.aliyun.com/zh/model-studio/asr-model/)
- [Qwen-ASR API](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)
- [语音合成模型](https://help.aliyun.com/zh/model-studio/tts-model)
- [Qwen-TTS API](https://help.aliyun.com/en/model-studio/qwen-tts-api)
- [向量与重排序](https://help.aliyun.com/zh/model-studio/embedding-rerank-model)
- [Rerank API](https://help.aliyun.com/zh/model-studio/rerank)
- [OpenAI 兼容 Chat API](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)
- [Base URL 总览](https://help.aliyun.com/zh/model-studio/base-url)
- [模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- [限流](https://help.aliyun.com/zh/model-studio/rate-limit)
- [模型上下架与更新](https://help.aliyun.com/zh/model-studio/newly-released-models)

### 15.2 技术官方文档与源码

- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [Pydantic 官方文档](https://docs.pydantic.dev/)
- [SQLAlchemy 官方文档](https://docs.sqlalchemy.org/)
- [Alembic 官方文档](https://alembic.sqlalchemy.org/)
- [Next.js 官方文档](https://nextjs.org/docs)
- [React 官方文档](https://react.dev/)
- [TypeScript 官方文档](https://www.typescriptlang.org/docs/)
- [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [pgvector 官方源码与文档](https://github.com/pgvector/pgvector)
- [Redis 官方文档](https://redis.io/docs/latest/)
- [LlamaIndex 官方文档](https://docs.llamaindex.ai/)
- [Temporal 官方文档](https://docs.temporal.io/)
- [LangGraph 官方文档](https://langchain-ai.github.io/langgraph/)
- [OpenTelemetry 官方文档](https://opentelemetry.io/docs/)
- [Compose Specification](https://docs.docker.com/reference/compose-file/)
- [Podman 官方文档](https://docs.podman.io/)
- [Conda 环境管理](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html)
- [Playwright 官方文档](https://playwright.dev/docs/intro)
- [pytest 官方文档](https://docs.pytest.org/)

### 15.3 置信度

- **高置信度**：百炼当前公开的模型类别、ASR/TTS/OCR/Embedding/Rerank 型号与接口形态、区域 Base URL、账号级限流语义；PostgreSQL RLS、Compose、框架官方定位。
- **中置信度**：`qwen3.7-plus`/`qwen3.6-flash` 在本项目复杂 Schema、思考模式和视觉输入组合下的实际稳定性；需要 spike。
- **待运行探测**：所有模型的项目账户实际权限、可用区域、精确上下文边界、结构化返回组合、OCR/ASR/TTS 质量、媒体 URL 时效，以及未稳定抽取的价格/限流值。
- **高度时效敏感**：稳定别名所指快照、促销价、免费额度、RPM/TPM/QPS、模型上下架和接口兼容细节。任何实施不得只依赖本文快照。

## 16. 最终判定

票据 07 可以确认“候选栈与验证门”，不能越权确认“最终架构”。当前最稳健的方向是：

> 使用 Python 模块化单体和 PostgreSQL 建立可信领域内核，以 Next.js 提供成熟工作台，以本地模型网关最小化调用 Qwen，以 pgvector/全文检索和 LlamaIndex 适配器形成 RAG，以可替换的持久工作流和强隔离沙箱支撑复杂任务；Conda `agent` 只锁定本地开发工具链，手动分进程、统一 CLI、Docker 与 Podman 共享生产运行合同。

技术深度应来自证据协议、租户隔离、可靠工作流、多模态验证、可观测和持续评测，而不是来自微服务数量或框架堆叠。
