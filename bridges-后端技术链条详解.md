# BridGes 后端技术链条详解(`src/bridges`)

> 本文是 `src/bridges` 目录的逐文件夹讲解文档,目标是把每个代码文件的作用与设计思想讲明白,让读者能读懂整个后端的"技术链条"。
> 讲解以通俗为主,保留必要的技术名词(中英对照)。

---

## 目录

1. [这个后端是什么(全景)](#一这个后端是什么全景)
2. [技术链条怎么串起来的](#二技术链条怎么串起来的)
3. [第一批:骨架层](#三第一批骨架层)
   - [根目录 `src/bridges/`](#31-根目录-srcbridges)
   - [`storage/` 存储层](#32-storage-存储层)
   - [`runtime/` 运行层](#33-runtime-运行层)
   - [`ai/` 模型能力层](#34-ai-模型能力层)
   - [`api/main.py` 组装工厂](#35-apimainpy-组装工厂)
4. [第二批:契约与入口](#四第二批契约与入口)
   - [`contracts/` 契约层](#41-contracts-契约层)
   - [`api/` HTTP 入口层](#42-api-http-入口层)
   - [`evaluation/` 评测子系统](#43-evaluation-评测子系统)
   - [`domain/` 领域包](#44-domain-领域包)
5. [第三批:核心业务域](#五第三批核心业务域)(整理中)
6. [第四批:其余业务域](#六第四批其余业务域)(整理中)
7. [附录](#七附录)

---

## 一、这个后端是什么(全景)

`src/bridges` 是 **BridGes**(一个"长期科学学习与表达伙伴")的**后端单体**。可以把它想象成一家 **科学自习室的管理系统**:

| 类比 | 对应模块 | 干什么 |
|---|---|---|
| 前台(HTTP 大门) | `api/` | 收请求、发响应,所有路由入口 |
| 大脑 | `ai/` | 调通义千问(Qwen)各种模型:对话、写摘要、识图、语音、生图、生视频 |
| 规章制度 | `contracts/` | 定义所有数据结构的"合同"——请求长什么样、能力有哪些 |
| 记账本 | `storage/` + `persistence.py` | SQLite 数据库 + 加密对象库,所有持久化 |
| 后勤(后台员工) | `runtime/` | 定时/后台跑活:摄取文档、生成图片、清理垃圾 |
| 老板办公室 | `config.py` | 全项目唯一配置源 |

**技术栈一句话**:FastAPI(API) + SQLite(存储) + 通义千问/DashScope(模型) + 线程后台执行器(异步任务),单机部署、进程内多服务。

**进程形态**(由 `cli/` 管理):
- **API 进程**:提供 HTTP 服务,只负责"下单",不跑耗时活。
- **worker 进程**(`runtime/executor.py`):后台执行器,周期领活干活(摄取、生成、清理)。
- **scheduler 进程**(`runtime/scheduler.py`):提醒调度器(现为退役清理员)。

---

## 二、技术链条怎么串起来的

最核心的一条数据流(聊天场景):

```
浏览器
  → api/chat 路由(收请求、认证)
  → ChatService(业务编排)
      → ModelGateway(模型网关:解析能力、限流重试、写运行锁)
          → QwenApiClient(真实 HTTP 调阿里云 DashScope)
              → 流式回传(SSE 事件)
  → 结果落 SQLite(messages 表) + 写一条"运行锁"审计记录
  → 返回给前端
```

后台任务链(如"用户上传 PDF"):

```
api/ingestion 入队 → task_claims 表(统一领取队列)
  → worker 进程按租约领取
      → 解析 → 分块 → 向量化 → 版本化索引写入
  → 完成后更新 document_records 状态
```

**贯穿全系统的三个设计原则**:
1. **账户隔离是强制的** —— 数据库层 `ScopedConnection` 在 SQL 层面强制 `account_id` 过滤,跨账户访问直接报错("不可能"而不是"约定")。
2. **一切固定快照、可审计** —— 每次模型调用留不可变"运行锁";评测、领域包、检索决策都固化成可复现快照,绝不被静默覆盖。
3. **没有假成功** —— 缺配置就报错/待机/明确失败,绝不静默降级到内存或 Stub。

---

## 三、第一批:骨架层

> 这批是整个系统运行的地基:配置、存储、运行机制、模型能力、应用组装。

### 3.1 根目录 `src/bridges/`

包里的 4 个"地基"文件。

| 文件 | 作用 | 主要内容/设计思想 |
|---|---|---|
| `__init__.py` | 包门面 | 只有一行描述「BridGes —— 长期科学学习与表达伙伴」和版本号 `0.1.0`,无逻辑 |
| `config.py` | **全项目唯一配置中心** | pydantic-settings 从环境变量读配置,统一 `BRIDGES_` 前缀(兼容旧前缀)。秘密支持直接给值或 `_FILE` 文件引用。**不读任何 `.env`**。`get_settings()` 带缓存,全进程一份 |
| `persistence.py` | **状态存储**(小状态持久化) | `StateStore` 接口(按命名空间存取 JSON)+ `SqliteStateStore` 实现 + `derive_fernet`(从主密钥派生 Fernet 加密密钥)。`build_state_store()` 工厂:配了数据库必须配密钥,否则报错 |
| `retirement.py` | **退役兼容层** | 旧能力下线后:统一返回 410 GONE + 结构化指引(`raise_retired_capability`),记录兼容指标;`run_reminder_retirement` 退役提醒功能;`retire_user_extensions` 退役用户插件(SKILL/MCP) |

**关键思想**:
- `config.py`:全进程只有一个配置源,所有运行载体(源码/CLI/Docker/Podman)读同一份,避免配置发散。
- `persistence.py`:给"服务自己的小状态"用的轻量持久化,业务数据走 `storage/`。
- `retirement.py`:功能下线不是"删代码走人",要有兼容边界(旧请求收到明确的 410 和指引)并保留审计。

### 3.2 `storage/` 存储层

数据地基:权威数据库 + 加密对象库。

| 文件 | 作用 | 主要内容/设计思想 |
|---|---|---|
| `errors.py` | 统一错误类型 | 唯一的 `StorageError` 异常,中文消息面向运维,不含敏感路径 |
| `database.py` | **版本化事务 SQLite(核心)** | `MIGRATIONS` 字典(版本号→SQL 脚本)管理全部表结构,`SCHEMA_VERSION`=39。`BridgesDatabase`:单连接+显式事务(`transaction()`),`initialize()` 幂等迁移。**`scoped(account_id)` → `ScopedConnection`**:SQL 层强制账户隔离(INSERT 必须带 account_id、其他语句 WHERE 必须带 account_id 过滤)。另有快照/备份/重开连接能力 |
| `object_store.py` | 加密文件对象库 | 文件按内容 SHA-256 哈希寻址存储(`objects/<hash前2位>/<hash>`),落盘前 Fernet 加密,写走临时文件+原子替换,读时校验哈希。路径不含账户/文件名,无法猜他人文件 |
| `repository.py` | 对象元数据仓库 | 桥接数据库元数据与文件内容。`create_object` 先落盘再写元数据;删除先标记 `pending_cleanup` 再物理清理(可观察、可重试);清理前检查内容哈希是否被共享引用(防跨账户误删);`find_orphans`/`cleanup_orphans` 清孤儿 |

> ⚠️ **注意**:`database.py` 目前有**未解决的 git 合并冲突**(`SCHEMA_VERSION` 39 vs 35,迁移 35 有两份不同定义),对应 `git status` 里的 `UU` 状态。**现在是合并中间态,直接运行会崩**,需要先解决冲突。

**关键思想**:版本化迁移保证数据库可升级;`ScopedConnection` 把"账户隔离"从约定变成不可能绕过;删除讲究"先标记、后清理、可重试、共享安全"。

### 3.3 `runtime/` 运行层

后台运行机制:活怎么派、怎么跑、怎么保证不冲突。

| 文件 | 作用 | 主要内容/设计思想 |
|---|---|---|
| `loop.py` | 受监督循环 | `supervised_loop()`:执行一轮 `tick()` → `stop.wait(interval)` 等待。用 `threading.Event.wait` 而非 `sleep`,停止信号立刻生效 |
| `queue.py` | **统一"领取型任务"契约(深模块)** | `TaskQueue`:任务状态机 `queued → claimed → completed/failed`,原子领取(`claim_next`,租约过期可重领=崩溃恢复唯一规则)、退避调度(固定/线性/指数)、续租(`touch`)。`TaskWorker`:claim→handler→complete/requeue 薄封装。`task_claims` 表**只做调度,不存业务状态** |
| `executor.py` | 后台执行器(worker) | 周期(默认 60s)跑:文档摄取、项目迁移、图片/视频任务轮询、删除重试、对象清理、孤儿清理。各服务惰性创建,缺配置给出中文待机原因,待机不崩溃 |
| `scheduler.py` | 提醒调度器 | 提醒功能已退役,此进程降级为"清理员":周期幂等清理遗留提醒、结束验证状态、清除 SMTP 授权码,绝不发邮件 |
| `lock.py` | 数据目录单实例锁 | `DataDirectoryLock` 上下文管理器:同一数据目录同时只允许一个实例(POSIX flock / Windows msvcrt),进程退出(含崩溃)锁自动释放,遗留锁文件无害 |

**关键思想**:后台任务的机械规则(领取/重试/退避/恢复)收拢成唯一深模块,子系统只管"干活";API 进程下单、worker 进程干活,职责分离;单实例约束在数据目录上。

### 3.4 `ai/` 模型能力层

全项目最体现架构功力的模块。核心概念:**逻辑能力(capability)与具体模型解耦**。

| 文件 | 作用 | 主要内容/设计思想 |
|---|---|---|
| `__init__.py` | 汇总导出 | 把下面各文件统一暴露为 `bridges.ai` 公开 API,无逻辑 |
| `fixed_models.py` | **固定模型矩阵(ADR-0009)** | 全项目唯一模型 ID 常量表:对话 `qwen3.7-plus-2026-05-26`、结构化 `qwen3.6-flash`、向量 `text-embedding-v4`、ASR `qwen3-asr-flash`、TTS `qwen3-tts-flash`、图片 `qwen-image-2.0-pro`、视频 `wan2.7-t2v`(唯一非 Qwen 例外)。**换模型要走受控变更,禁止暗中切换** |
| `capability_registry.py` | 能力注册表 | 内存字典:键 `(能力名, 版本)`,值 `CapabilityRecord`。**只登记,不知道如何调用** |
| `model_gateway.py` | **模型网关(核心)** | 唯一的"逻辑能力→具体模型"映射点。`invoke()`/`stream()`:按能力名找记录→找适配器→调用,全程产生不可变 `ModelRunLock`(审计基石)。容错:限流/瞬时→退避重试;鉴权/区域→立即失败关闭;主能力耗尽→只允许已验证同区域 fallback。流式路径不做网关级自动重试(以"新建助手尝试"为用户级重试) |
| `qwen_client.py` | 底层 HTTP 客户端 | 最低层 Qwen/DashScope 调用。**不知道能力和网关**,只把 HTTP 状态码翻译成稳定错误分类(`RateLimitError/TransientError/AuthError/RegionError`)供网关决策。含 cassette 录音带机制(测试可回放,**生产禁录**防对话正文落盘) |
| `adapters.py` | 适配器协议 | 定义 `CapabilityAdapter` 接口(`call() → AdapterResult`)和错误类型家族;流式协议(`StreamChunk/StreamEvent`);`StubQwenAdapter`(**只允许 test 环境**的确定性替身,生产绝不注册到真实能力上) |
| `qwen_adapters.py` | 对话/结构化适配器 | 文本对话、结构化输出(带 JSON Schema) |
| `qwen_asr_adapter.py` | 语音转写适配器 | ASR 能力 |
| `qwen_tts_adapter.py` | 语音合成适配器 | 文本转语音 |
| `qwen_image_adapter.py` | 图片生成/编辑适配器 | 异步任务:submit/poll/fetch/cancel |
| `qwen_vision_adapters.py` | OCR/视觉适配器 | 图像理解与文字识别 |
| `qwen_wan_adapter.py` | 文生视频适配器 | Wan 模型异步任务 |

**关键思想**:能力注册表是目录、适配器是工人、网关是调度员;每次调用留一张不可变"发票"(运行锁);供应商的杂乱被 qwen_client 挡在门外。

### 3.5 `api/main.py` 组装工厂

**作用**:`create_app()` 创建并配置整个 FastAPI 应用,全项目几十个服务在此实例化、接线、挂到 `app.state`。1700 行的"上帝文件",读它 = 读系统拓扑图。

流程:
1. 读配置 → 建状态存储 → 建 `BridgesDatabase` + `EncryptedFileObjectStore`。持久化不可用时,中间件让除 `/health` 外全部请求返回 503(绝不静默降级内存)。
2. 健康检查 `/health/ready` 把"持久化配置了没有""全局 Qwen 密钥配了没有"纳入就绪门。
3. `_register_builtin_capabilities()` 登记全部模型能力;有全局密钥绑真实适配器,test 环境才绑 Stub。
4. 实例化所有业务服务并挂 `app.state`(身份/保险库/分享/机构/画像/聊天/摄取/知识库/检索/图片/视频/插件/MCP/科学/表达/媒体/工作流/评测/领域包...),每个服务注入依赖。
5. 挂载全部路由(30 次 `include_router`)。
6. startup 启动生成执行器后台线程(测试环境默认不启)。

---

## 四、第二批:契约与入口

### 4.1 `contracts/` 契约层

**这个文件夹是干嘛的**:整个后端(乃至前端)**所有数据结构长什么样**的唯一真相源。每个文件是一个业务域的"票据格式"——请求/响应/投影/状态机全部用 pydantic 模型 + 枚举写死。

**为什么要有它**:
1. 前后端共用,不会"你说你的、我传我的";
2. 投影即脱敏——"Projection"类型刻意只暴露该暴露的字段,内部信息(对象库路径、向量原文、凭据)不进响应;
3. 服务间接口稳定。

**看代码建议**:读任何 `service.py`,先看它 import 了哪些 `bridges.contracts.X`——那个 X 就是它的输入输出形状。

按业务域分组(42 个文件):

**A. 跨域基础(4)**
| 文件 | 内容 |
|---|---|
| `__init__.py` | 总入口,re-export 跨领域共享的 90+ 符号 |
| `health.py` | 健康检查契约(`HealthProjection` 等),`/health` 的返回格式 |
| `retirement.py` | 退役能力契约(单个 `RetiredCapabilityError`) |
| `scope.py` | 作用域隔离契约(`ScopeEnvelope`/`RLSContext`/`ScopeAction`) |

**B. AI 与模型(1)**
| 文件 | 内容 |
|---|---|
| `ai.py` | 能力注册表/网关/运行锁契约:`CapabilityRecord`、`ModelRunLock`、`RetryPolicy`/`FallbackPolicy` |

**C. 聊天与生成(7)**
| 文件 | 内容 |
|---|---|
| `chat.py` | 对话/消息/SSE 事件契约——聊天域地基(`ChatMessageProjection`、`ChatStreamEvent` 及多种载荷) |
| `speech.py` | 听写与朗读契约 |
| `image.py` | 图片生成/编辑契约 |
| `video.py` | 文生视频契约 |
| `routing.py` | 聊天自然语言路由契约(用户意图→图片/视频能力) |
| `humanizer.py` | 人味化改写契约(事实锁、过程卡) |
| `career.py` | 生涯规划契约(六类输出、过程事件) |

**D. 内容摄取与检索(7)**
| 文件 | 内容 |
|---|---|
| `ingestion.py` | 文档摄取与版本化索引契约 |
| `retrieval.py` | 分层检索/融合/引用契约 |
| `knowledge_base.py` | 全局知识库材料投影 |
| `search.py` | 跨内容统一搜索契约 |
| `science.py` | 科学来源/论断/证据/引用**大契约**(几十个枚举 + `Source`/`Claim`/`ClaimGraph`/`FactLock`) |
| `teaching.py` | 教学轮次与证据门契约 |
| `reminder.py` | QQ 提醒契约(功能已退役,契约保留) |

**E. 画像与学习(5)**
| 文件 | 内容 |
|---|---|
| `profiles.py` | 画像观察/候选/断言/切片/许可**大契约** |
| `profile_extraction.py` | 自动画像抽取内部合同 |
| `learning.py` | 学习使命/诊断/知识状态/教学计划/复习契约 |
| `learning_projects.py` | 文件夹式学习项目契约 |
| `learning_project_migration.py` | 学习项目迁移契约 |

**F. 身份、协作与资产(7)**
| 文件 | 内容 |
|---|---|
| `identity.py` | 账户/会话/设备切换/恢复契约 |
| `institution.py` | 机构管理契约 |
| `sharing.py` | 显式共享契约 |
| `sync.py` | 设备同步契约 |
| `vault.py` | 个人保险库契约 |
| `projects.py` | 科学项目空间契约 |
| `media.py` | 多模态媒体资产**大契约** |

**G. 治理与观测(9)**
| 文件 | 内容 |
|---|---|
| `workflows.py` | 工作流/工作单契约 |
| `domain.py` | 领域包治理契约(三签/灰度/失效/回滚) |
| `evaluation.py` | 评测运行锁与结果包契约 |
| `evaluation_suite.py` | 评测套件契约(案例/量表/盲评/发布门) |
| `expression.py` | 科学表达任务契约 |
| `invalidation.py` | 失效/墓碑/影响集契约 |
| `observability.py` | 审计/SLO/告警契约 |
| `feedback.py` | 回答反馈与画像修正契约 |
| `lifecycle.py` | 数据生命周期契约(导出/删除/备份/恢复) |
| `plugins.py` / `mcp.py` | 插件中心契约(已退役) |

**总结**:全是数据形状,没有业务逻辑;是全系统互相引用、前后端共用的"共同地基"。

### 4.2 `api/` HTTP 入口层

**这个文件夹是干嘛的**:系统的 HTTP 大门。每个文件是一个业务域的"门面"(Blueprint)。**路由本身不含业务逻辑**——标准动作:解析认证主体 → 从 `app.state` 取服务 → 调用服务 → 投影成响应。错误统一映射,跨账户访问一律返回统一 404。

| 文件 | 作用 |
|---|---|
| `main.py` | 应用工厂 + 总装配点(见 3.5),30 次 `include_router` 挂全部蓝图 |
| `__init__.py` | 只 re-export `create_app` |

**认证与安全(3)**
| 文件 | 作用 |
|---|---|
| `auth.py` | 账户认证蓝图(`/auth/*`):注册/登录/登出/再认证/恢复/头像/会话/设备账户切换。核心依赖 `require_subject`(解析 Cookie)与 `RecentAuthRequired`(敏感操作近期密码门) |
| `csrf.py` | 全局中间件 `CsrfOriginMiddleware`:对改状态请求校验来源,防 CSRF |
| `scope.py` | 作用域隔离端点:让前端读当前请求的权限上下文 |

**聊天与生成(4)**
| 文件 | 作用 |
|---|---|
| `chat.py` | 聊天蓝图(`/chat/*`):会话 CRUD、原子首轮+幂等键、发消息、SSE 流式订阅、停止/重试/反馈 |
| `speech.py` | 听写与朗读(`/chat/conversations/{cid}/dictation` 等) |
| `image.py` | 图片任务操作面(查询/取消/重试/资产),提交走聊天 SSE |
| `video.py` | 视频任务操作面(同理) |

**内容域(5)**
| 文件 | 作用 |
|---|---|
| `ingestion.py` | 文档摄取详情与索引状态 |
| `knowledge_base.py` | 全局知识库材料(上传/列表/下载/重建/删除) |
| `learning_projects.py` | 学习项目列表/详情/文件下载;**写操作全部 410**(已退役,改用全局知识库) |
| `learning_project_migration.py` | 学习项目迁移 |
| `search.py` | 跨内容统一搜索(`GET /search`) |

**科学域(7)**
| 文件 | 作用 |
|---|---|
| `science.py` | 科学来源/检索/Claim 图/事实锁/质量门 |
| `expression.py` | 表达任务(文稿/体裁转换/修订/发布门) |
| `media.py` | 多模态媒体(摄取/图表/分镜沙箱/无障碍/发布)——端点最多的蓝图 |
| `domain_packs.py` | 领域包专家工作台(三签/语义 diff/灰度/失效/回滚) |
| `evaluation.py` | 评测中心 |
| `workflows.py` | 工作单(work-orders/运行确认/取消/待办) |
| `projects.py` | 科学项目空间 CRUD |

**身份协作(4)**
| 文件 | 作用 |
|---|---|
| `institution.py` | 机构管理 |
| `sharing.py` | 显式共享 |
| `vault.py` | 个人保险库 |
| `sync.py` | 设备同步 |

**生命周期(1)**
| 文件 | 作用 |
|---|---|
| `data.py` | 数据生命周期(`/data/*`):导出/删除/备份/恢复,写操作挂敏感门 |

**退役 410 组(3)** —— 旧功能下线后的"墓碑"路由,任何请求统一 410 + 替代指引:
`reminder.py`(QQ 提醒)、`plugins.py`(旧插件)、`mcp.py`(旧 MCP)。

**其他(1)**
| 文件 | 作用 |
|---|---|
| `skills.py` | 内置 SKILL 清单 + 兼容性观测 |

**关键思想**:路由是"薄层",业务全在 `*/service.py`。想找"某个功能在哪处理",顺着路由找 service。

### 4.3 `evaluation/` 评测子系统

**这个文件夹是干嘛的**:BridGes 的**"可复现 A/B 科学评测"实验室**——用产品真实能力(画像、人味表达、科学事实、教学、生涯规划、多模态、安全)跑标准考试,判断"改版变好还是变坏"。**核心卖点:一切固定快照,任何变化产生新版本,旧结果永不被覆盖,所以两次跑能精确复现**。

**完整评测流程(7 步)**:
1. **定义套件** `suite_data.py`:内置套件 `science-baseline@1.0.0`(原创数据、量表、7 任务、24 案例、模型固定、运行矩阵)。
2. **登记套件** `suite_registry.py`:校验摘要一致性与引用完整性;失效只标记不删历史。
3. **建运行锁** `runner.py`:`EvaluationRunner.run()` 先生成不可变 `SuiteRunLock`,冻结套件摘要/代码摘要/模型固定/裁判版本/种子/次数——"可复现"的来源。
4. **执行矩阵** `executors.py` + `case_executors.py`:按 `sut × case × seed × 次数` 跑;每个案例在临时数据库装配生产服务 + 可编程网关替身(`ScriptedAdapter`)。`sut.py` 登记 6 种被测系统(完整/基础 Qwen/参考方法/三个消融);`reference_method.py` 是不调模型的确定性参考基线。
5. **打分断言** `metrics.py` + `judges.py`:7 维 0-5 分规则指标 + 自动断言 + 裁判量表。
6. **聚合报告** `report.py`:点估计、95% 置信区间、Welch 检验、失败率、代表性失败案例。
7. **发布门 + 盲评** `gates.py`:低于阈值阻止发行;`blind_review.py`:隐藏身份的 A/B 盲评。`injection.py`:对故意缺陷验证指标能检出(指标自身的回归测试)。

**持久化** `repository.py`:套件/锁/结果/报告/盲评集 **append-only**(只增不改)。**领域入口** `service.py`:从已完成工作流运行创建评测、对比结果包。

**总结**:把"产品改版"变成一场能复现的考试,考不过(低于阈值)就不让上线。

### 4.4 `domain/` 领域包

**这个文件夹是干嘛的**:把不同学科(天文、地球气候、计算机、生命科学、医学高风险、数学证明、物理化学、标准与数据集)的"判断科学结论是否成立"的知识,封装成**可注册、可验证、可发行、可失效的领域包**。通俗讲:每个包是一套"科学质检手册",让 AI 回答科学问题时按手册检查"这句话站不站得住脚"。

**核心文件关系(一条链)**:
```
8 个学科包按 protocol 接口实现
  → loader.py 预检把关(Manifest 校验、版本、安全)
  → registry.py 登记版本(保留受信历史)
  → runtime.py 夹具重放验证(包必须通过自带测试)
  → workbench.py 专家工作台(三签/语义 diff/灰度发行,三签齐了才激活)
  → pack_lifecycle.py 失效/撤销/回滚(失效后阻断新运行,受信回滚)
```

**控制面(7)**
| 文件 | 作用 |
|---|---|
| `protocol.py` | **核心接口**:`DomainPack` 协议(11 个方法:问题分类/来源规划/元数据归一化/结构解析/Claim 抽取/证据评估/冲突检测/措辞约束/Claim 校验/夹具评估)+ 不可变快照 `LoadedDomainPack` |
| `loader.py` | 第一道闸门:`validate/load` 做 Manifest 预检(schema/版本/签名/平台下限/依赖/循环检测),通过才放行 |
| `registry.py` | 版本目录:`register` 预检 + `validate_upgrade` 升级兼容检查,保留受信历史 |
| `runtime.py` | 验证运行时:对加载的包**重放夹具**(自带测试题),不合格不许发布 |
| `workbench.py` | 专家工作台:三签、语义 Diff、灰度发行、H3 安全联合门 |
| `pack_lifecycle.py` | 治理层:失效状态机、影响集解析、紧急撤销、运行闭锁、重验证、受信回滚 |
| `__init__.py` | re-export 公开符号 |

**8 个学科包(8)** —— 模式完全一致,只是领域规则不同:
| 文件 | 领域 | 检查重点 |
|---|---|---|
| `math_formal_proof.py` | 数学与形式证明 | prompt 注入、证明步骤、符号歧义、公式等价、循环 |
| `physics_chemistry.py` | 物理化学 | 单位/量纲、质量 vs 重量、摩尔 vs 分子、方程式配平、有效数字、误差 vs 不确定度 |
| `life_science.py` | 生命科学 | 独立重复声明、关联性标记、生物安全标记 |
| `medical_high_risk.py` | 医学高风险 | 8 类问题(PICO 维度、禁止性措辞) |
| `earth_climate.py` | 地球气候 | 天气≠气候、相关≠因果、预测、面积断言 |
| `astronomy.py` | 天文学 | 7 类问题(天体识别、星历、坐标变换、岁差修正) |
| `computer_science.py` | 计算机与数据集 | 版本字段、历史生命周期、版本绑定 |
| `standards_datasets.py` | 标准与数据集 | 通用稳定性/检查助手 |

**总结**:8 本学科"质检手册" + 一套"手册治理流水线",让科学判断知识成为受控的、可治理的资产。

---

## 五、第三批:核心业务域

### chat/ —— 持久化流式聊天(主链路)

**作用**:聊天的完整纵向切片——会话/消息/生成运行的账户隔离落库、后台领取执行的运行生命周期、一轮对话的回合编排、面向 API 的发送/重试/停止/投影控制面。

**核心关系(service 与 turn 谁干什么)**:
- **`service.py` 是"下单员"**:`start_generation` / `start_first_turn` / `retry_generation` 做载荷校验、自然语言路由,并在**同一事务内**落库「用户消息 + 助手占位 + queued 运行 + started 事件 + 登记领取队列」,返回投影。它只创建"待执行任务",不阻塞等待模型。
- **`turn.py` 是"执行者"**:`TurnOrchestrator.stream_turn` 是唯一接口——组装历史 → 按载荷分派(SKILL/生涯/图片/视频/MCP/普通)→ 分层检索 → 联网/arXiv 并行搜索 → 画像切片编译与披露 → `assemble_payload` 组装提示词 → `gateway.stream()` 流式调用(固定能力 `qwen_text_chat@1`)→ 边收边增量落库 → `finalize_message` 收敛终态。
- 二者之间由后台执行器 `run_executor.py` 连接:领取 queued 运行 → 调 `stream_generation` → 委托给 `turn.py`。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `attachments.py` | 聊天附件安全校验/绑定/授权访问(魔数嗅探 MIME、10MB 限制、白名单+保留名校验) |
| `attachments_repository.py` | `chat_attachments` 表账户作用域仓库 |
| `budget.py` | 统一阶段时钟与预算控制器(总预算 120s,分阶段脱敏指标)——Issue 06 时延预算 |
| `lifecycle.py` | 进行中生成的停止信号(`threading.Event`)+ 活跃度跟踪 |
| `repository.py` | 对话/消息/生成运行/模型运行锁仓库(全部账户隔离) |
| `routing.py` | 聊天内自然语言**图片**路由(`CapabilityRouteRegistry` / `NaturalLanguageImageRouter.route`) |
| `run_executor.py` | 持久化生成运行后台执行器(收尸→领取→执行→终态状态机,心跳续租) |
| `selections.py` | 对话级插件选择(校验/失效清洗/工具上下文编译)——Issue 36 |
| `service.py` | 持久化聊天服务:发送/重试/停止/投影/反馈控制面 |
| `turn.py` | **回合编排深模块**:整条生成管线唯一执行点 |

### routing/ —— 聊天自然语言路由

**作用**:把用户消息**确定性地(不调模型)**裁决为「主能力 + 可执行合同」,让聊天服务决定走哪条生成路径(普通对话/论文搜索/人味化/图片/视频/生涯/澄清)。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `contracts.py` | 版本化路由合同:`MainCapability` 枚举、`RouteStatus`、论文搜索约束与计划、文生视频合同 |
| `registry.py` | 可追加的能力注册表(内置普通聊天/论文/视频/生涯,只许追加不许覆盖) |
| `service.py` | 确定性判定器:`NaturalLanguageRouter.classify` 把文本编译成 `CapabilityRoute`(大量正则规则) |

> 使用链:`chat/service.py` 发送时调用 `classify`,结果存消息 `route` 列,`chat/turn.py` 的 `capability_route_from` 读取并分派执行。

### career/ —— 生涯规划助手

**作用**:在聊天/学习模式中按明确意图触发,产出可保存可恢复可追溯的结构化规划。**确定性控制面**(意图判定/输入检查/复核)包围着**模型生成**(结构化输出)。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包说明 |
| `intent.py` | 确定性意图检测:`is_career_intent`(关键词 + 否定防护,避免模型猜测进入编排) |
| `intake.py` | 最小必需输入判断:「目标方向 + 当前阶段」,缺哪个只问一个澄清问题 |
| `review.py` | **确定性复核门**(不依赖模型自评):承诺词边界/完整性门/事实证据门/引用核验/过时/冲突 |
| `service.py` | 编排服务:证据 → Qwen 结构化生成 → 宽容解析 → 复核 → 投影与审计 |
### retrieval/ —— 分层本地检索

**作用**:聊天生成前的"证据管线"——**先决策、再分层检索、后融合成引用**。用户回合的意图固化进 `retrieval_decisions` 快照(重试复用),解析作用域(现仅全局知识库层活跃),对每层独立跑关键词+向量两路检索,用固定 RRF 合同层内融合、作用域权重跨层去重合并,算出结构化"充足性"信号,把轮次与引用持久化,点击引用时按对象当前状态**实时校验授权**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面,暴露 `LayeredRetrievalService` |
| `decision.py` | **确定性决策层(无 LLM)**:按禁用/显式知识库词/上传材料词/专用能力路由/学习意图/科学主题词/寒暄,决定 `RETRIEVE` 或 `SKIP`;`query_fingerprint` 生成不可逆请求指纹 |
| `search.py` | 关键词(FTS5 trigram BM25)+ 向量(余弦)融合搜索内核:RRF(k=60)层内融合、层配额、跨层权重合并、`compute_sufficiency` 充足性计算 |
| `repository.py` | 检索轮次/引用/决策的账户作用域持久化(`retrieval_rounds`/`message_citations`/`retrieval_decisions` 表) |
| `service.py` | 编排主服务:`ensure_decision`(先落决策快照)→ `run_round`(每轮检索)→ `citation_detail`(引用授权实时校验) |

### ingestion/ —— 文档摄取与版本化索引

**作用**:把账户内的文档对象转成可追溯、可恢复的本地检索材料。**状态机闭环:入队 → 解析 → 分块 → 向量化 → 版本化索引**。失败按阶段落 `error` + 中文原因;永久失败顶满自动重试等用户手动重试;租约过期呈现 recovery。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包入口 |
| `parsers.py` | PDF/DOCX/TXT/Markdown/图片 → 归一文本+结构跨度;图片只保留元数据不做 OCR;解析结果可 JSON 缓存 |
| `chunker.py` | 结构锚点的哈希分块器(目标 ~900 字符/块,段落优先合并、超长硬切,SHA-256 确定性哈希) |
| `embedding.py` | 向量化端口:`QwenEmbeddingPort`(真实调 `text-embedding-v4`)/ `DeterministicEmbeddingPort`(测试替身) |
| `index.py` | 不可混写的版本化全文/向量索引:`IndexContract`(合同哈希)、`VersionedIndex`(构建→校验→原子切换活跃版本→回滚) |
| `service.py` | 摄取状态机服务:`enqueue` 入队(同一事务写记录+投任务)、`process_pending` 后台执行(`_process_document` 完整流水线)、重建/删除/孤儿清理 |

### knowledge_base/ —— 全局本地知识库

**作用**:知识库材料的**写模型与授权面**——材料是账户级全局检索材料(`source=knowledge_base`,不绑定对话)。上传校验后创建对象并入队摄取,解析/分块/索引委托给 `IngestionService`。重复上传按"文件名+内容哈希"幂等去重;跨账户/未知材料统一返回不泄漏存在性的 404。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面,暴露 `KnowledgeBaseService` |
| `service.py` | 上传/列表/详情/下载/重试/重建/删除;`download_for_capability` 要求索引就绪才可被聊天使用;删除级联触发迁移墓碑 |

### search/ —— 跨内容统一桌面搜索

**作用**:用户**主动发起**的只读桌面搜索——一次输入同时检索聊天(标题+正文)、文档(名称+分块)、图片(文件名+元数据)、学习项目(名称+描述)四类内容。用参数化 `LIKE` 子串匹配(刻意不用 FTS5:支持任意长度子串、摆脱对索引版本的依赖),改删即时反映、无缓存。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面,暴露 `SearchService` |
| `repository.py` | 只读 SQL 查询集合(会话/消息/文档/图片/项目五类命中,全部参数化) |
| `service.py` | 编排:分类计数、命中高亮片段(`highlight`)、按 `updated_at` 倒序截断 |

> **search 与 retrieval 的区别**:`search` 偏"查得全、看得见"(主动界面);`retrieval` 偏"按需取、可引用"(生成管线内的检索增强,依赖版本化索引,产出带授权校验的引用)。
### profiles/ —— 画像中心(数字分身)

**作用**:画像闭环:**观察 → 候选 → 人工决策 → 断言 → 切片 → 许可**。分层清晰:`ports.py` 定义领域依赖的稳定持久化端口(ABC),`adapters.py`(内存)与 `sqlite_repository.py`(SQLite)是同一端口的两类实现;`service.py` 承载全部域规则;`api.py` 只做 HTTP 适配。

**关键约束**:未确认候选绝不进切片;瞬时/第三方/敏感观察进 discard 阻断后续写入;许可默认关闭、只能用户开启。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `adapters.py` | 内存实现 + 域异常 |
| `api.py` | FastAPI 路由(`/profiles`) |
| `automatic.py` | 自动画像预处理:抽取→写四维→最小切片,有界重试(Issue 15) |
| `extraction.py` | 确定性记忆意图提取:`REMEMBER/FORGET/SESSION_ONLY/AUTO_WRITE/TRANSIENT` |
| `four_dimensions.py` | 四维画像目标仓库与迁移(旧断言分类为 target/teaching/legacy/skip) |
| `ports.py` | 稳定持久化端口(ABC) |
| `service.py` | 画像闭环核心:候选晋升门、断言版本快照、切片过滤、许可门、阻断去重、审计 |
| `sqlite_repository.py` | SQLite 实现(全部经 `scoped(account_id)` 强制账户隔离) |

### learning/ —— 学习系统

**作用**:**使命 → 诊断 → 教学 → 复习**四子系统。核心原则:只把"可观察答案"算成掌握——浏览/完成/模型猜测不算;被拒的提议需新证据才能重提。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `adapters.py` | 内存实现(知识状态带版本链) |
| `api.py` | 路由(`/learning`);复习相关端点已 410 退役 |
| `pathway.py` | 学习路径:记录→提议→人工确认→路径重编译 |
| `ports.py` | 持久化端口(ABC) |
| `review_scheduler.py` | 间隔复习调度(SM-2 式翻倍,对外 API 已退役、内部仍被调用) |
| `service.py` | 使命与诊断:逐概念判 UNKNOWN/EMERGING/SUPPORTED/ROBUST,按最近发展区(ZPD)编译教学计划 |
| `teaching.py` | 短课与检索练习(质量门/证据绑定/事实锁/确定性判分,强制主动回忆) |
| `teaching_gate.py` | 教学证据门 + 对话式教学状态机(五阶段,Issue 08) |

### learning_projects/ —— 学习项目(只读兼容 + 迁移收口)

**作用**:历史"文件夹式学习项目"的只读兼容与迁移收口——**新文件来源统一由全局知识库管理**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `migration.py` | 迁移到全局知识库(幂等键/同内容哈希复用/墓碑防复活/全局收缩闸门) |
| `service.py` | 旧项目写模型(新建/改名/删除/对话归属;文件上传删除已 410,迁移窗口冻结写) |

### science/ —— 科学证据系统

**作用**:按"**来源 → 论断 → 证据 → 引用 → 事实锁**"组织的证据链地基。下游 expression/media 只消费事实锁产物,**禁止越过其约束**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `claims.py` | Claim–Evidence–Citation 领域服务(生成 ClaimGraph、引用重验、不可变版本化、发布门、失效重验证) |
| `fact_lock.py` | 事实锁编译与诚实降级(六类锁:数值/术语公式/关系/条件/引用/强度),**纯规则不调模型** |
| `parser.py` | 文本/PDF 解析器(炸弹防护、注入检测、按段落分块) |
| `qwen_parser.py` | OCR PDF 解析器(文本层为空时经 `qwen_ocr` 逐页识别,模型背书) |
| `search.py` | 科学来源作用域混合检索(词法 BM25 + 向量 + RRF 融合) |
| `service.py` | 来源摄入与版本化(质量门,失败进入 QUARANTINED/BLOCKED;校正生成新版本) |

### expression/ —— 科学表达管线

**作用**:把事实锁约束下的 ClaimGraph 转成面向受众的可发布成稿(科普/讲稿/科研汇报/论文辅助四体裁)。**任何模型或修订都不许越过事实锁给出的措辞强度上限**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 表达任务域服务:体裁契约→论证计划→生成(每个片段绑定 claim/citation/fact_lock)→表达质量门→风格诊断→修订(须过事实锁不变性校验)→审批→发布资格门 |
| `style.py` | 中文"人味"诊断引擎(10 类规则扫描模板腔/翻译腔/节奏/分寸/科学越界,越界一律 BLOCKING) |

### media/ —— 多模态媒体创作

**作用**:**摄取 → 图表 → 分镜 → 无障碍 → 发布**的多模态科学创作流水线。全链路由事实锁统一约束,保证同一 Claim 在文字/图表/音视频中的数值、术语与措辞强度一致。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `accessibility_service.py` | 无障碍替代包:朗读/字幕/文字稿/键盘路径/减少动画/顺序阅读;真实 TTS 合成转存受控存储 |
| `extraction.py` | 确定性媒体提取(图片/公式/表格/音视频) |
| `generation.py` | 可编辑静态科学图与数据图表(确定性 spec + SVG 渲染,绑定 Claim/事实锁) |
| `publish_service.py` | 跨媒体一致性与发布门(7 门,任一必需门失败阻止发布) |
| `qwen_extraction.py` | Qwen 提取(OCR/视觉/ASR) |
| `service.py` | 媒体摄入与校正(质量门、派生资产、人工校正生成新版本) |
| `storyboard_service.py` | 分镜生成与沙箱执行(AST 静态检查/导入白名单/有限修复,预算耗尽则 QUARANTINED) |

---

## 六、第四批:其余业务域

### arxiv_mcp/ —— 聊天内 arXiv 论文证据来源

**作用**:聊天内的 arXiv 论文搜索。核心是"**隔离 + 固化**":网络请求不在宿主进程执行,而是由 `worker.py` 独立子进程承担,父进程用白名单环境启动、经 UTF-8 JSONL 协议通信(安全、可取消、可重启);搜索结果**固化到 assistant 消息的 `arxiv_search` JSON 列**,刷新/重放时从同一份真实来源恢复。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `client.py` | 受限 arXiv 同步 HTTP 客户端(固定 `export.arxiv.org`) |
| `contracts.py` | 内部记录与聊天公开投影(七态状态机) |
| `manifest.py` | 固定版本与最小权限清单(只允许登记 URL) |
| `process.py` | 受限 worker 进程客户端(环境白名单、JSONL 协议、崩溃最多重启一次) |
| `service.py` | 触发、查询脱敏、中文结果编排与隐私审计 |
| `worker.py` | 子进程主循环(ready 握手 + 逐行处理) |

### web_search/ —— 聊天内公网搜索

**作用**:聊天内的公网搜索证据来源(固定 DuckDuckGo,无需 Key/.env)。**与 arXiv 互斥**——检测到"论文/文献"意图直接让给 arXiv。只依据当前用户消息做本地触发判断(显式"联网/搜索"词、时效性、事实核查),由 `LocalQueryPlanner` 删净私密内容后产出最小查询。状态固化到 `web_search` 列,方式同 arXiv。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包说明 |
| `client.py` | DuckDuckGo Instant Answer 客户端(只收有真实 URL 的结果,最多 5 条) |
| `contracts.py` | 状态与结果投影(七态) |
| `service.py` | 编排(触发判断 + 私密脱敏 + 审计) |

### mcp/ —— 已退役的用户扩展 MCP 管理

**作用**:Issue 35 实现的通用 MCP 插件管理。**现状:所有公开方法先被 `410 user_extensions_retired` 退役门拦截**(返回"请回到聊天或知识库"),不再对外可用,但实现保留完整(供回滚/审计参考)。它当时的链路:解析 MCP.yaml → 安全闭锁 → 按账户持久化 → 受限进程惰性启动 → JSONL 双向通信,一切 I/O 都经宿主强制校验 + 敏感操作逐次确认。

| 文件 | 作用 |
|---|---|
| `checker.py` | MCP.yaml 安装描述确定性检查器 |
| `manifest.py` | MCP.yaml 子集解析与权限清单校验 |
| `process.py` | 受限 worker 进程客户端 |
| `runtime.py` | 每账户进程注册表(pid 文件回收孤儿进程) |
| `service.py` | 插件治理(安装/启用/调用/授权/审批,宿主强制工具层) |
| `servers/base.py` | JSONL 协议服务器共享框架 |
| `servers/echo.py` | 受控回显演示服务器(无任何 I/O) |
| `servers/note.py` | 受控笔记演示(敏感写入路径) |

### plugins/ —— 已退役的 SKILL 插件中心

**作用**:Issue 34 实现的 SKILL 插件中心。现状同 mcp:**410 退役门拦截**。它当时负责"内置插件随应用发布默认安装 + 用户上传声明式 zip 包经安全闭锁后按账户安装"。

| 文件 | 作用 |
|---|---|
| `checker.py` | SKILL 包 zip 确定性安全检查器(尺寸/条目数/符号链接/外部引用) |
| `registry.py` | 内置只读插件清单(humanizer 条目与 SkillRegistry 同源) |
| `service.py` | 插件中心账户作用域服务(安装/启停/卸载/demo,含退役门) |

### skills/ —— 内置只读 SKILL 宿主

**作用**:内置 bridges-humanizer SKILL 的运行宿主。四模块分工:**识别(intent)→ 锁(factlock)→ 体(genre_rules)→ 编排(service)**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `registry.py` | 内置只读 SKILL 注册表(启动注册固定版本,插件中心展示与运行审计同源) |
| `humanizer/intent.py` | 把聊天消息判定为改写意图并编译任务契约(宁可不命中也不误送) |
| `humanizer/factlock.py` | 文本级事实锁引擎(七类锁:数值/单位/关系/条件/公式/引用/强度),改写前后比较 |
| `humanizer/genre_rules.py` | 四体裁表达规则(科普/讲稿/科研汇报/论文辅助) |
| `humanizer/service.py` | 编排:组装指令→生成→确定性复核(硬门冲突停交付、软门至多一次定向修复),不虚构事实/论文/引用 |

### credentials/ —— 全局凭据与凭据存储

**作用**:统一管理全部运行凭据。**铁律:Key 正文绝不进入异常消息、日志、repr、SQLite 明文或 `.env`**;存储不可用明确抛中文错误而非静默降级明文。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包说明 |
| `global_credential.py` | 全局百炼运行凭据(GQ-01:正式运行唯一认证来源,CLI 启动硬门;只校验"可读",不发探测) |
| `retire.py` | 历史账户 Qwen 秘密清退(GQ-07:一次性幂等,完成标记落 StateStore;绝不碰 smtp 命名空间) |
| `store.py` | 按命名空间隔离的账户级凭据存储(OS keyring / Windows DPAPI / 容器加密卷),GQ-07 后唯一服务 SMTP 授权码 |

### identity/ —— 身份与会话

**作用**:身份与认证域。稳定内部 `account_id` 是所有用户数据的归属键,用户名与 QQ 邮箱仅是可变登录标识。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 注册/认证/会话/设备切换/恢复/删除(Argon2 密码哈希、会话令牌哈希、近期认证门槛、QQ 邮箱脱敏、30 分钟恢复令牌;备份刻意不包含会话/令牌/设备绑定) |

### vault/ —— 个人保险库

**作用**:个人保险库边界。按 `content_authority` 决策加密路径:**设备本地加密**(密文 + wrapping key 包裹的数据键落库,云只收哈希投影)或**服务端副本**;设备不可达返回 `DeviceUnavailableState`,**绝不从云端偷偷重建明文**。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `adapters.py` | 内存实现 + 设备配对(RSA 密钥对、wrapping key、证书、KeyEpoch) |
| `ports.py` | 稳定抽象端口(全 ABC) |
| `service.py` | 协调层:对象创建/临时任务胶囊/云控制投影(只存元数据)/最小化共享副本 |

### sync/ —— 设备同步

**作用**:设备副本的因果同步。先拉控制面(授权版本、密钥时期、撤销设备、墓碑),再校验并应用带 RSA 签名的设备操作;并发修改落冲突分支等人工裁决(**不采用"最后写入者获胜"**);删除生成墓碑,防离线修改复活对象。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 拉控制快照→校验签名→应用操作→冲突分支→墓碑;违规一律隔离(quarantine) |

### sharing/ —— 显式共享

**作用**:显式共享与最小化项目副本。共享前预览(声明哪些字段进副本、哪些被排除),经 vault 创建最小化副本、发放对象级授权与一次性邀请令牌,按角色基线(OWNER/EDITOR/REVIEWER/COMMENTER/VIEWER)约束权限上限。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 预览/执行/邀请/授权/成员/机构项目(成员判定绑定到 scope enforcer 的 `is_member`) |

### institution/ —— 机构管理

**作用**:机构管理域。受控内容访问(安全事件)要求**双人批准**(申请人不能批自己、不能重复批准)、限时、逐次审计日志;默认保持个人保险库边界不被机构触碰。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 机构/成员/角色(ADMIN/SECURITY_ADMIN/BILLING_ADMIN/MEMBER)/席位/邀请/机构所有项目/受控访问 |

### projects/ —— 科学项目空间

**作用**:科学项目空间。每个项目作为 `PERSONAL_VAULT` 域对象,每次读取/更新/删除先经 `ScopeEnforcer.authorize`(模拟 RLS),跨账户项目被跳过、永不被枚举。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 项目 CRUD + 归档(版本号随每次变更递增) |

### scope/ —— 作用域强制器

**作用**:作用域强制器——**所有 API 路由、域服务、缓存构建器、后台任务校验器共享的单一解释器**。只依赖 contracts,按 subject/tenant/project/object_domain/authorization_version 判定访问,未授权一律抛 `ScopeIsolationError` 失败关闭;还负责生成 RLS 上下文、作用域感知缓存键、后台任务信封完整性校验。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | `ScopeEnforcer`(authorize/compile_scope/set_rls_context/build_cache_key/validate_background_task/report_violation)+ 测试夹具 |
### lifecycle/ —— 数据生命周期

**作用**:数据生命周期四服务(导出/删除/备份/恢复),共享 `catalog.py` 作为**唯一表清单真相源**。导出=可审阅的 JSON 快照;删除=「状态记录→数据库事务删除→文件系统清理」三段编排,失败入队可重试、绝不假成功;备份=跨载体同格式的加密容器;恢复=预检全过才原子替换、失败回滚。四者都绕审计、零秘密泄漏。

| 文件 | 作用 |
|---|---|
| `backup.py` | 加密备份与恢复(`BRIDGESBACKUP1` 容器,口令派生 Fernet;原子替换 + 失败回滚) |
| `catalog.py` | 账户数据目录单一事实源(38 张账户表清单 = 删除顺序 + 导出类别聚合) |
| `deletion.py` | 账户删除编排(状态行+pending_hashes → 事务删除 → 文件/凭据/身份清理;统一领取型队列重试) |
| `exports.py` | 账户数据导出(可读 JSON 文档,不含对象字节与秘密) |

### reminder/ —— QQ 邮件提醒(已退役)

**作用**:QQ 邮件提醒全链路(Issue 33 实现,功能现已退役,契约与实现保留)。`smtp.py` 是最底层传输网关,错误分类决定上游「授权失效立即暂停 vs 临时失败退避重试」;`parser.py` 把自然语言**确定性地**解析成带时区的结构化日程(所见即所存)。

| 文件 | 作用 |
|---|---|
| `adaptation.py` | 提醒措辞的画像适配(最小切片白名单织入称呼,披露使用类别) |
| `parser.py` | 中文自然语言提醒解析(确定性,不调模型) |
| `rules.py` | 重复规则计算(账户时区本地推进、DST 安全、月末收敛) |
| `service.py` | 编排:SMTP 验证 attempt 状态机/创建复核/调度/投递/24h 补发/退避/授权失效处理 |
| `smtp.py` | QQ 邮箱 SMTP 发送与自发自收验证(IMAP 会话缓存 + NOOP 保活) |

### image/ —— 图片生成

**作用**:图片生成异步任务服务(固定 `qwen-image-2.0-pro` 快照)。API 进程只负责提交/查询/取消/重试/资产管理(任务与消息投影**同事务落库**,无孤儿);后台执行器经统一领取队列按租约处理「提交→单次轮询→终态收敛」,成功转存账户对象库建**版本化资产**(编辑生成新版本,不覆盖原图);取消用条件更新(`WHERE status != 'cancelled'`)隔离迟到结果。

| 文件 | 作用 |
|---|---|
| `service.py` | 图片任务编排(提交/轮询/终态/资产管理/替代文本,模型失败确定性降级) |

### video/ —— 文生视频

**作用**:与 image 同构的异步任务服务(**Wan 是唯一非 Qwen 例外**,仍用全局百炼凭据)。API 侧提交/查询/取消(`cancelling` 中间态)/重试;执行器侧提交→轮询→终态后转存对象库建账户隔离资产,用条件发布隔离取消竞态。

| 文件 | 作用 |
|---|---|
| `constants.py` | 固定模型/尺寸/时长合同(供路由、适配器、任务服务共用) |
| `service.py` | 视频任务编排(提交/轮询/取消收敛/转存/资产) |

### speech/ —— 听写与朗读

**作用**:语音双向能力。与 image/video 不同,speech 是**同步调用**(非任务队列):听写把音频直传 ASR **不落盘**;朗读把 Markdown 剥成纯文本按上限截断到句子边界,经 TTS 合成后**转存账户对象库**并把快照写回消息 `read_aloud` 列。两者模型标识都经网关进入不可变运行锁。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | 听写(transcribe)+ 朗读(read_aloud 生成/查询/删除);`markdown_to_plain_text` 剥 Markdown |

### health/ —— 健康探针

**作用**:对外健康检查投影(`/health/live|ready|degraded`)。探针刻意**不做有副作用的检查**(防止冒烟伪装成健康检查污染 SLI);当前必备依赖仅 configuration,可选 observability。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `probe.py` | `build_health_projection`(组装 live/ready/degraded 与依赖列表) |

### observability/ —— 观测基建

**作用**:观测基建,三条链路:**审计**(append-only 脱敏存储)、**SLO**(声明注册表)、**告警**(生命周期管理)。审计事件永不携带正文/完整提示/密钥。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包说明 |
| `alert_manager.py` | 告警管理器(fire 去重/acknowledge/resolve 必须带关闭证据/suppress 需理由) |
| `audit_event.py` | 追加式审计事件日志 |
| `run_summary.py` | 隐私保护的运行摘要构建(只留引用与元数据 + 隐私清单) |
| `scrubber.py` | 隐私脱敏器(`sk-`/`LTAI` 等敏感模式) |
| `sli_registry.py` | SLI/SLO 注册表 |
| `telemetry_context.py` | 遥测关联上下文(账户/项目/租户伪匿名化,contextvars 传播) |
| `service.py` | 观测门面(业务服务唯一入口) |

### workflows/ —— 工作流与工作单

**作用**:双状态机(运行状态 + 制品信任状态)的任务编排核心。把 WorkOrder 编译为带 `RunContextEnvelope` 的版本化运行,节点执行前过**失效门**与**领域包门**;支持人工门待办;运行状态落 SQLite(`workflow_runs`)支持崩溃恢复。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | register/submit/confirm/advance/cancel/resolve_todo/get_run/find_runs_using_pack |

### invalidation/ —— 失效与墓碑

**作用**:对象失效的唯一权威。**墓碑优先于普通失效事件**、历史追加不可覆盖、未知状态一律 fail-closed、outbox 传播与重验证幂等且受作用域约束。域模块可扩展影响解析与重验证处理器,但不能改核心规则。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 门面 |
| `service.py` | record_invalidation/record_tombstone/check_state/require_active/register_impact_resolver/plan_invalidation/process_outbox/schedule_revalidation |

### cli/ —— 统一命令行

**作用**:统一生产运行契约(`BridGes` 命令)。**start 命令同步监督 Web/API/后台执行器/提醒调度器四子进程**,带:全局 Key 硬门 → 账户密钥清退 → 依赖/构建产物校验 → 数据目录单实例锁 → 迁移 → 拉起四进程 → 健康检查 → 监督运行(任一退出整体失败)→ 优雅停止。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 惰性导入(避免 `python -m` 重复导入告警) |
| `evaluate.py` | `BridGes evaluate` 子命令:run_suite/replay_case/blind_review/report/gates(可复现 A/B 评测) |
| `main.py` | 统一入口:`start`/`api`/`web`/`worker`/`scheduler`/`migrate`/`doctor` |

---

## 七、附录

### 7.1 常见"想找某个功能"的路径

| 想找什么 | 去哪个目录 |
|---|---|
| 聊天怎么发一条消息、怎么流式返回 | `api/chat.py` → `chat/service.py` → `ai/model_gateway.py` |
| 数据表结构、迁移 | `storage/database.py`(MIGRATIONS) |
| 有哪些模型、用什么模型 | `ai/fixed_models.py` |
| 后台定时任务有哪些 | `runtime/executor.py`(run_tick) |
| 某个接口返回什么形状 | `contracts/` 对应业务域文件 |
| 账户怎么隔离 | `storage/database.py` 的 `ScopedConnection` |
| 文件存哪、怎么加密 | `storage/object_store.py` |
| 后台任务的领取/重试规则 | `runtime/queue.py` |

### 7.2 已知问题

- **`storage/database.py` 有未解决的 git 合并冲突**(`SCHEMA_VERSION` 39 vs 35),当前为 `UU` 状态,需先解决才能正常运行。

---

*本文档由对 `src/bridges` 源码的逐文件阅读整理而成,覆盖全部约 270 个 Python 文件。*
