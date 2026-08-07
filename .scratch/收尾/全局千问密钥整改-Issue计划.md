# 全局百炼（Qwen）API Key 整改 Issue 计划

Status: ready-for-agent  
Created: 2026-08-07  
Scope: 将 BridGes 从“全局密钥负责模型调用、账户密钥负责探测与 Embedding”的双钥模式，收敛为唯一的全局百炼运行凭据；删除账户级百炼密钥设置、探测门禁和全部用户入口。

## 目标结果

维护者在启动正式服务前，通过 `BRIDGES_QWEN_API_KEY` 或 `BRIDGES_QWEN_API_KEY_FILE` 配置一把全局百炼 API Key。`BridGes start` 校验配置后启动 API、Web、后台执行器和提醒调度器；普通用户只需注册或登录，不再录入、替换、删除或探测个人百炼密钥，即可使用核心对话、结构化生成、OCR/视觉、语音转写、回答朗读、图片、视频、知识库向量化与检索等 Qwen/Wan 能力。

本计划所称“能够使用全部功能”的前提是：全局 Key 真实有效，百炼账户已开通固定模型矩阵所需权限且额度、区域和网络正常。仅检查环境变量非空不能证明供应商权限和额度永久可用；运行期供应商错误仍须明确呈现，不得伪装成功。

## 已确认产品决策

- 全局百炼运行凭据是正式运行的必需配置，不再是可选外部能力。
- `development` 和 `production` 的规范运行入口在缺少、为空或无法读取全局 Key 时必须失败关闭；不再启动一个只能登录、不能使用核心能力的降级实例。
- `test` 环境继续使用确定性适配器，自动测试不得依赖真实 Key 或真实网络。
- 正式运行仍不读取 `.env`；支持直接环境变量和 `*_FILE` 文件引用，文件引用优先用于容器或长期部署。
- 全局 Key 只能存在于服务进程配置中，不进入浏览器、普通 API 响应、URL、日志、审计正文、数据导出或备份。
- 普通用户不再拥有百炼密钥的查看、保存、替换、删除、探测或重试权限。
- 账户菜单最终严格保留“切换账号、个人资料、退出登录”三项；移除“密钥设置”。
- `/account/settings/keys` 页面直接删除，访问旧地址返回 404，不保留兼容页面或跳转。
- `/auth/key-settings` 及其探测、重试接口直接退出公开合同，访问旧接口返回 404。
- QQ SMTP 授权码仍是账户级凭据，继续保留原有设置、验证、再认证和隔离语义；本整改不得误删或改为全局凭据。
- 固定模型绑定、索引版本、最小云端披露、账户数据隔离和生产环境禁止 Stub 的合同保持不变。
- 全局 Key 轮换后必须重启相关服务；首轮整改不引入运行期热更新。

## 与现有架构决策的关系

本整改明确推翻“账户级百炼密钥”这一既有决策，不能只改实现而保留冲突文档：

- ADR-0005《使用账户级百炼密钥与真实能力探测》应标记为已被新 ADR 取代。
- ADR-0009 中“全部云端智能能力共用当前账户的百炼密钥”应改为共用全局百炼运行凭据。
- ADR-0018 中账户切换不得共享“百炼密钥”的条款应删除；SMTP 凭据和账户数据隔离继续保留。
- `CONTEXT.md` 中“账户级百炼密钥”“账户能力状态”和“BridGes 纵向替换切片”的相关定义应同步重写，避免后续代理重新实现旧模式。
- ADR-0008 的 Embedding 模型、维度和索引版本合同不变，只改变调用凭据来源。

## 非目标

- 不增加模型选择器、供应商切换或备用模型。
- 不把 DeepSeek、OpenAI 或其他供应商 Key 接入本次整改。
- 不把全局 Key 保存进 `bridges.db`、账户表、浏览器存储或普通配置文件。
- 不删除或弱化 QQ SMTP 账户凭据、敏感操作再认证、数据导出/删除能力。
- 不承诺通过一次启动检查永久保证供应商额度、限流、模型权限或网络状态。
- 不修改科学证据、画像授权、知识库账户隔离、消息持久化或模型运行锁的业务语义。

## 用户故事

- **US-01 维护者配置启动：** 作为本地部署维护者，我在启动终端设置全局 Key 后可以一次启动完整 BridGes；遗漏配置时在任何子服务启动前得到不泄密的中文错误。
- **US-02 新用户直接使用：** 作为普通用户，我注册或登录后无需配置个人百炼 Key，也无需等待账户能力探测，即可发起所有已登记 Qwen/Wan 功能。
- **US-03 无密钥用户界面：** 作为普通用户，我在账户菜单、设置中心、URL 和 API 中都看不到个人百炼密钥管理入口。
- **US-04 安全升级：** 作为已有安装的维护者，升级后历史账户百炼密钥和探测状态被幂等、安全清退，SMTP 授权码不受影响。
- **US-05 统一运行合同：** 作为源码、Docker 或 Podman 用户，我获得一致的全局 Key 注入、缺失失败、健康检查和错误语义。

## 依赖图与执行策略

这是一次宽范围合同替换，采用 expand–migrate–contract，禁止先删除旧能力再逐个修复调用方。

```text
GQ-01 建立全局运行凭据与启动硬门
  ├─ GQ-02 主对话与结构化生成迁移 ─┐
  ├─ GQ-03 听写与回答朗读迁移 ────┤
  ├─ GQ-04 图片与视频任务迁移 ────┤
  └─ GQ-05 知识库向量化与检索迁移 ┤
                                      └─ GQ-06 删除账户密钥用户面与公开合同
                                           └─ GQ-07 清退历史秘密和旧实现
                                                └─ GQ-08 全载体黄金路径发布验收
```

GQ-02～GQ-05 可在 GQ-01 合并后并行实施；每个切片必须独立保持测试绿色。GQ-06 只有在所有调用方均不再读取账户密钥或探测状态后才能开始。

---

## GQ-01 — 建立唯一的全局百炼运行凭据与启动硬门

Status: completed（实施 + 双轴审查 + 全量回归后提交，755136c）  
User stories: US-01、US-05

### What to build

建立唯一的全局百炼运行凭据合同，让能力注册表、模型网关、Embedding 客户端工厂和后台执行器都能从同一份运行配置构造供应商客户端；保留旧账户凭据路径作为暂时兼容层，供后续迁移切片逐步移除。正式运行缺少全局 Key 时在启动边界失败，而不是注册空网关后继续提供“就绪”状态。

同时新增一份“全局百炼运行凭据”ADR，明确取代 ADR-0005，并同步修正固定模型矩阵、统一运行合同与领域词汇。架构文档必须说明：全局凭据改变的是供应商认证来源，不改变账户数据所有权、运行上下文信封、云端最小披露或审计归属。

### Acceptance criteria

- [ ] `BRIDGES_QWEN_API_KEY` 与 `BRIDGES_QWEN_API_KEY_FILE` 都能加载同一 Secret 配置；Key 不出现在对象 `repr`、异常、日志或健康响应中。
- [ ] `development`/`production` 下，`BridGes start` 在获取数据目录锁、迁移和拉起任何子进程之前检查全局 Key；缺失、空值或文件不可读时非零退出并给出中文配置指引。
- [ ] 正式 API 独立入口缺少 Key 时同样失败关闭，避免绕过 `start` 得到“ready=pass、模型不可用”的实例。
- [ ] 后台执行器使用同一全局配置；规范的 `BridGes start` 不可能出现 API 有 Key、worker 无 Key 的分裂状态。
- [ ] `BridGes doctor` 把全局 Key 视为必需项：配置存在时只报告“已配置”，缺失时报告失败且不回显尾号或正文。
- [ ] `test` 环境继续注册确定性 Stub；`development`/`production` 仍禁止 Stub 和固定样例假成功。
- [ ] 能力注册表的固定模型、版本、区域、重试和运行锁合同不因凭据来源改变。
- [ ] 新 ADR 标记 ADR-0005 为 superseded，并同步修正 ADR-0009、ADR-0012、ADR-0018 和 `CONTEXT.md` 中冲突术语。
- [ ] 配置检查只验证必需值可读取；不会在每次启动时偷偷提交图片、视频等可能计费的全量真实探测。

### Verification

```powershell
python -m pytest tests/integration/test_config_contract.py tests/integration/test_runtime_smoke.py tests/credentials/test_registry.py -q
python -m pytest tests/security/test_disclosure_scrubbing.py tests/security/test_mcp_hardening.py -q
python -m bridges.cli.main doctor
```

另需增加子进程级测试：缺少 Key 时断言 `BridGes start` 非零退出且没有启动 API/Web/worker/scheduler；注入非秘密测试占位值时断言全部固定适配器完成注册，但不发起真实网络请求。

### Blocked by

None - can start immediately.

---

## GQ-02 — 让 BridGes 主对话与结构化生成只使用全局运行凭据

Status: completed（实施 + 双轴审查 + 全量回归后提交）  
User stories: US-02

### What to build

把主对话发送链路及其结构化生成消费者迁移到全局模型网关。新账户不再因为“未配置个人 Key”“账户能力未探测”或“账户能力不可用”而被 API 预检拦截；请求直接进入已由启动硬门保证完成注册的模型适配器。人味化、职业规划、表达和其他经主对话触发的结构化生成遵循同一凭据来源。

供应商认证失败、区域错误、限流和瞬态错误继续由网关分类，但面向用户的修复提示必须从“前往账户设置更新密钥”改为“检查启动服务的全局百炼配置/权限”，不得再链接已计划删除的页面。

### Acceptance criteria

- [ ] 全新注册账户在不存在任何账户百炼密钥、密钥元数据或探测记录时，可以创建对话并进入真实/确定性流式回答链路。
- [ ] 主对话 API 不再依赖账户凭据服务，也不检查 `chat` 探测快照。
- [ ] 正常发送、停止、重试、断流恢复、消息持久化和模型运行锁行为保持不变。
- [ ] 人味化、职业规划及其他结构化模型调用沿用同一全局网关，不新增第二套 Key 解析逻辑。
- [ ] 无效全局 Key 的错误提示指向终端运行配置，不再出现“前往设置中的密钥页”。
- [ ] 多账户仍分别记录对话、消息、运行锁和云端披露记录；共享供应商凭据不得造成跨账户上下文或结果召回。
- [ ] 测试夹具不再通过给每个账户写入假 Key/`chat AVAILABLE` 来放行普通聊天；测试环境以确定性适配器作为唯一放行机制。

### Verification

```powershell
python -m pytest tests/chat tests/career tests/humanizer -q
python -m pytest tests/security/test_disclosure_scrubbing.py tests/security/test_background_account_binding.py -q
```

新增纵向回归：设置全局测试适配器 → 注册新账户 → 不创建账户 Key → 新建对话 → 发送消息 → 收到完成事件 → 重启后恢复同一回答。

### Blocked by

- GQ-01

---

## GQ-03 — 让听写与回答朗读只使用全局运行凭据

Status: completed（实施 + 双轴审查 + 全量回归后提交）  
User stories: US-02

### What to build

移除 ASR 听写和 TTS 回答朗读的账户密钥与账户探测门禁，使语音端点直接调用全局网关中已经注册的固定适配器。保留文件大小、媒体类型、文本长度、发音说明、账户归属和对象访问控制等现有合同。

### Acceptance criteria

- [ ] 新账户无需账户 Key 或 `asr`/`tts` 探测记录即可提交听写和朗读请求。
- [ ] 语音 API 不再依赖账户凭据服务，不再返回 `no_api_key`、`capability_probing` 或引导用户前往密钥设置的错误。
- [ ] ASR/TTS 仍固定到 ADR-0009 指定模型，不切换备用模型。
- [ ] 无效、无权限、限流和网络错误继续被分类为稳定中文错误，并指向服务运行配置或稍后重试。
- [ ] 音频对象、转写正文、朗读音频与任务记录继续严格按账户隔离。
- [ ] 回答朗读的暂停、继续、停止及超长文本处理不回归。
- [ ] 语音测试移除逐账户保存假 Key/探测状态的前置步骤，以全局确定性适配器驱动。

### Verification

```powershell
python -m pytest tests/speech tests/media -q
Set-Location apps/web
npm run test:e2e -- issue30-dictation-reading.spec.ts
```

### Blocked by

- GQ-01

---

## GQ-04 — 让图片与视频异步任务只使用全局运行凭据

Status: completed（实施 + 双轴审查 + 全量回归后提交）  
User stories: US-02、US-05

### What to build

移除图片生成/编辑和 Wan 视频任务的账户密钥、账户探测门禁。API 提交端与后台执行器统一使用全局供应商配置；任务仍绑定提交账户、对话、对象库、租约和运行锁。全局凭据失效时任务必须进入可解释、可重试的失败/阻塞状态，不能永远排队，也不能由 Stub 冒充完成。

### Acceptance criteria

- [ ] 新账户无需账户 Key 或 `image`/`video` 探测记录即可提交图片和视频任务。
- [ ] 图片/视频 API 不再依赖账户凭据服务，不再提示前往密钥设置。
- [ ] API 与 worker 使用同一全局 Key、区域、workspace 和录制策略；不得各自维护含义不同的客户端构造逻辑。
- [ ] 图片、视觉替代文本和视频继续使用固定模型绑定及既有运行锁。
- [ ] 全局认证失败或权限不足时，任务形成稳定错误投影并支持既有重试规则；不会静默降级到其他模型。
- [ ] 任务领取、轮询、取消、资产转存、消息投影和重启恢复保持账户隔离及幂等。
- [ ] 生产环境 cassette 录制禁令继续覆盖 API 和 worker，避免私人提示或响应正文落盘。
- [ ] 测试不再为每个账户写入 `image`/`video AVAILABLE`，而由全局确定性任务适配器驱动。

### Verification

```powershell
python -m pytest tests/image tests/video -q
Set-Location apps/web
npm run test:e2e -- issue31-image-generation.spec.ts issue32-video-generation.spec.ts
```

### Blocked by

- GQ-01

---

## GQ-05 — 让知识库向量化、摄取与检索只使用全局运行凭据

Status: completed（实施 + 双轴审查 + 全量回归后提交，c724553）  
User stories: US-02、US-05

### What to build

把 Embedding 从账户凭据库迁移到全局百炼客户端，让 API 查询向量、后台摄取和索引重建共享相同配置。删除以账户探测快照判定 Embedding 是否可用的逻辑；可用性由运行时是否成功构造全局 Embedding 能力以及实际调用结果决定。

账户仍是材料、分块、索引版本、向量行、检索记录和引用的所有权边界。全局凭据共享不得演变为共享知识库或跨账户向量检索。

### Acceptance criteria

- [ ] 真实 Embedding 端口从全局 Secret 构造客户端，不再接受账户凭据存储作为依赖。
- [ ] API 查询向量与 worker 摄取/重建使用同一模型 ID、1024 维、规范化、区域、workspace 和 cassette 策略。
- [ ] 新账户无需账户 Key 或 `embedding` 探测记录即可摄取材料、构建向量索引并执行混合检索。
- [ ] Embedding 调用失败时仍保留全文索引/关键词检索的诚实降级和可操作原因，不写空向量、不伪装向量就绪。
- [ ] 索引版本合同、维度校验、重建和原子切换语义保持不变。
- [ ] 两个账户使用同一全局 Key 时，材料、向量行、检索候选和引用仍严格隔离。
- [ ] 搜索、学习项目、知识库和聊天检索夹具不再以账户探测记录作为放行条件。
- [ ] 全局 Key 不进入索引元数据、对象元数据、检索记录或导出。

### Verification

```powershell
python -m pytest tests/ingestion tests/retrieval tests/knowledge_base tests/search tests/learning_projects -q
python -m pytest tests/security/test_disclosure_scrubbing.py -q
```

新增多账户回归：同一全局确定性 Embedding 端口为两个账户建索引，分别查询时只能得到各自材料。

### Blocked by

- GQ-01

---

## GQ-06 — 删除账户级百炼密钥用户面与公开 API 合同

Status: completed（实施 + 双轴审查 + 全量回归后提交，adaa6d1）  
User stories: US-03

### What to build

在全部 AI 调用方完成迁移后，删除账户级百炼密钥的浏览器页面、账户菜单项、设置中心卡片、前端 API 客户端、公开后端路由和 OpenAPI 类型。旧页面与旧 API 不做跳转或兼容代理，直接成为不存在的资源。

这是一条完整退出切片：不仅删除可见文案，还要用负向测试锁定“入口不存在”，并同步调整所有错误横幅、空态和再认证流程，避免残留链接把用户带到 404。

### Acceptance criteria

- [ ] 账户左下角上拉菜单按顺序且仅包含“切换账号、个人资料、退出登录”。
- [ ] 设置中心不再出现“密钥设置”“模型连接与能力状态”或账户百炼 Key 文案；个人资料和数据与隐私入口保持可用。
- [ ] 密钥设置页面及其组件被删除，直接访问 `/account/settings/keys` 返回 Next.js 404。
- [ ] 前端不再请求 `/api/auth/key-settings`，也不保留保存、替换、删除、全量探测或单项重试的客户端方法和类型。
- [ ] 后端不再挂载 `/auth/key-settings`、`/auth/key-settings/probes` 和重试路由；旧 URL 返回 404。
- [ ] OpenAPI 和生成的 TypeScript 合同中不再包含账户 Qwen Key 请求、投影、探测状态或相关路径。
- [ ] Qwen Key 专属再认证场景被删除；SMTP、数据导出、备份恢复和账户删除的再认证测试继续通过。
- [ ] 聊天、语音、图片、视频等错误文案不再链接密钥设置页，改为全局运行配置提示。
- [ ] 原有“密钥设置可操作性”E2E 不得只删除；应替换为菜单三项、设置中心无入口、旧页面 404、旧 API 404 的负向回归。
- [ ] 不手工编辑 `.next` 构建产物；通过重新构建自然消除旧页面产物。

### Verification

```powershell
python scripts/regenerate_openapi.py
Set-Location apps/web
npx openapi-typescript ../../openapi.json --output ../../packages/contracts/src/generated.ts
npm run typecheck
npm run test:e2e -- issue08-account-settings.spec.ts issue12-shell-sidebar.spec.ts
Set-Location ../..
python -m pytest tests/integration/test_auth_api.py tests/contracts/test_openapi_sync.py -q
```

### Blocked by

- GQ-02
- GQ-03
- GQ-04
- GQ-05

---

## GQ-07 — 安全清退历史账户百炼秘密、探测状态与旧实现

Status: completed（实施 + 双轴审查 + 全量回归后提交）  
User stories: US-04

### What to build

删除已无调用方的账户 Qwen 凭据服务、探测调度、账户探测状态合同和生命周期接线，并为已有安装提供一次性、幂等的秘密清退。模型 ID 与固定能力矩阵仍是能力注册表的架构资产，应迁移到合适的 AI 领域所有者，不能随探测模块误删。

升级清理必须覆盖源码环境的操作系统凭据库和容器加密凭据卷；使用现有账户 ID 找到并删除 Qwen 命名空间中的秘密，清理非秘密的 Key 元数据和能力探测状态。QQ SMTP 命名空间必须保留。历史审计记录不含秘密，可作为旧版本行为证据保留，但新版本不再产生账户 Qwen Key 保存/删除/探测事件。

### Acceptance criteria

- [ ] 应用组合根、聊天/媒体/Embedding、账户删除、备份恢复和后台执行器不再持有账户 Qwen 凭据服务或探测服务依赖。
- [ ] 账户 Qwen Key 的请求/响应合同、服务、探测执行器和专属存储接线被删除；通用凭据存储若仍服务 SMTP，不得误删。
- [ ] 固定模型常量、能力注册和索引合同迁移到明确所有者后仍只有一个事实源。
- [ ] 升级清理能按所有现有账户删除操作系统凭据库或加密卷中的 Qwen 密钥，并删除 Key 元数据与能力探测快照。
- [ ] 清理具有持久化完成标记并可安全重复执行；中途失败时下次启动继续，不会把“未清理”误记为完成。
- [ ] 无法访问旧秘密存储时正式启动失败并给出不含秘密的处置提示，避免留下用户无法再管理的孤儿凭据。
- [ ] 两账户夹具证明只清理 Qwen 命名空间，SMTP 授权码、验证状态和提醒发送不受影响。
- [ ] 账户删除和备份恢复不再处理账户 Qwen Key，但仍正确处理 SMTP 凭据及其他账户数据。
- [ ] 历史审计记录不被伪造或改写；文档注明其为旧合同遗留事件。
- [ ] 仓库生产源码中不存在活跃的 `KeyCredentialService`、账户 `CapabilityProbeService` 或账户 Qwen 凭据读写路径。

### Verification

```powershell
python -m pytest tests/lifecycle tests/reminder tests/integration/test_auth_api.py -q
python -m pytest tests/security -q
rg -n "KeyCredentialService|CapabilityProbeService|/auth/key-settings|账户级百炼密钥" src apps/web/src packages/contracts README.md CONTEXT.md docs/adr
```

最后一条扫描只允许命中：已明确标记为 superseded 的历史 ADR、一次性迁移说明或历史审计枚举；任何活跃运行路径命中都视为未完成。

### Blocked by

- GQ-06

---

## GQ-08 — 完成全载体文档、黄金路径与发布门验收

Status: completed（实施 + 双轴审查 + 全量验证后提交，2511f92；详见 .scratch/收尾/GQ-08-发布验收报告.md）  
User stories: US-01、US-02、US-03、US-04、US-05

### What to build

收口源码环境、Docker 和 Podman 的统一运行合同，更新维护者文档和最终用户文案，并以“全局 Key 启动一次、普通账户零 Key 配置”的黄金路径验证整个产品。自动化使用确定性适配器覆盖全功能编排；真实百炼冒烟测试必须显式启用、不得保存 Key，并应提示图片/视频等调用可能计费。

### Acceptance criteria

- [ ] README、手动部署、Compose 注释和 CLI 帮助均说明：在 `BridGes start` 前配置 `BRIDGES_QWEN_API_KEY` 或文件引用，不创建 `.env`，轮换后重启。
- [ ] Compose/Podman 明确把全局 Key 或挂载的 Key 文件注入 API 和 worker；缺失时在容器启动阶段失败，不出现 Web 正常但 AI 不可用的半启动状态。
- [ ] 文档不再指导普通用户登录后配置百炼 Key，不再描述账户能力探测；QQ SMTP 的账户设置说明保持准确。
- [ ] 全新数据目录黄金路径通过：配置全局 Key → `BridGes start` → 注册 → 登录 → 菜单仅三项 → 新建对话并收到回答 → 知识库向量化/检索 → 听写/朗读 → 图片 → 视频。
- [ ] 已有数据目录升级路径通过：历史账户 Qwen Key 被清理，SMTP 与账户数据保留，登录后无需重新配置 Qwen。
- [ ] 缺少 Key、空 Key、不可读 Key 文件三种启动失败都有稳定中文错误和非零退出码，且不会泄露 Secret 或启动孤儿进程。
- [ ] 无效/无权限的全局 Key 在真实调用时呈现服务配置错误，不引导用户访问已删除页面。
- [ ] OpenAPI、TypeScript 合同、前后端类型、构建产物和测试基线同步。
- [ ] 全量后端测试、静态检查、类型检查、前端单元/E2E 和生产构建全部通过。
- [ ] 发布报告记录共享全局 Key 的运营风险：所有账户共享供应商配额、限流与费用；应用审计仍按发起账户记录，但不代表供应商侧独立计费。

### Verification

```powershell
python scripts/regenerate_openapi.py
Set-Location apps/web
npx openapi-typescript ../../openapi.json --output ../../packages/contracts/src/generated.ts
npm run lint
npm run typecheck
npm run test:unit
npm run test:e2e
npm run build
Set-Location ../..
python -m ruff check .
python -m mypy src
python -m pytest
```

真实冒烟测试单独执行并显式授权费用，不纳入默认 CI。验收记录只保存模型、能力、状态和追踪 ID，不保存全局 Key、完整私人提示或供应商原始敏感响应。

### Blocked by

- GQ-07

## 最终 Definition of Done

- [ ] 正式服务只有一种 Qwen 认证来源：全局百炼运行凭据。
- [ ] 普通账户不存在 Qwen Key 数据模型、API、页面、菜单、探测状态或调用门禁。
- [ ] `BridGes start` 缺少全局 Key 必然失败；配置有效 Key 后完整启动。
- [ ] 登录用户无需任何个人 Qwen 配置即可走通全部已登记 Qwen/Wan 能力。
- [ ] `/account/settings/keys` 和 `/auth/key-settings*` 均为 404。
- [ ] 账户菜单严格为“切换账号、个人资料、退出登录”。
- [ ] 历史账户 Qwen Secret 已安全、幂等清退；SMTP Secret 和账户数据未受损。
- [ ] 全局 Key 不出现在页面、API、URL、日志、审计正文、导出、备份或测试快照。
- [ ] ADR、领域词汇、README、部署文档、OpenAPI 和生成合同与实现一致。
- [ ] 全量验证命令通过，真实冒烟结果另有不泄密记录。

## 风险与实施注意事项

- **共享费用与限流：** 所有本地账户共用同一供应商配额；一个账户的高负载可能影响其他账户。此次按已确认产品方案接受该风险，但必须在发布说明中披露。
- **启动硬门影响：** 没有 Qwen Key 时连本地非 AI 页面也不再通过规范入口启动，这是为了保证“打开即完整可用”的明确选择，不应被后续实现静默改回降级启动。
- **Key 有效性边界：** 非空检查不能证明权限和额度。若未来需要启动前远程探测，应另开 Issue 设计无副作用、低成本、可缓存的服务级探测，不应恢复账户级密钥页。
- **不可逆清退：** 账户 Qwen Secret 清理完成后无法从 BridGes 备份恢复；回滚旧版本时需要维护者重新配置账户 Key。实施前应在升级说明中明确告知，但不得为了回滚继续复制或导出秘密。
- **测试替身边界：** Stub 只属于 `test` 环境；开发和生产环境不得因为全局 Key 缺失而自动切换 Stub。
- **并行开发冲突：** GQ-02～GQ-05 必须以已经合并的 GQ-01 为基线；不要各自创建不同的全局客户端工厂。
