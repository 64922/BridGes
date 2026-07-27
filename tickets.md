# Tickets: 科教智能体成熟产品

这些票据把[科教智能体成熟产品实现规格](.scratch/science-companion-plan/PRD.md)落实为可独立领取的纵向切片，并共同交付认证用户的科学项目任务生命周期。

按**任务前沿**实施：任何票据的全部阻塞项完成后即可领取。每张票据使用一个新的上下文运行 `/implement`。票据 ID 一经分配永久稳定；标题可以澄清，但不得复用或重编号 ID。

### 统一实施协议

每次实施必须遵循以下顺序：

1. 用户调用 `/implement` 时必须给出本文件中的稳定票据 ID 与准确标题，例如 `/implement T001 — 建立可运行产品骨架与统一契约链`；不要使用没有目标票据的裸调用。
2. 开始编码前完整读取根目录 `AGENTS.md`、`CONTEXT.md`、源规格 PRD、本票据、全部阻塞票据，以及本票据“必读资料”指向的详细决策或研究。
3. 只有当全部阻塞票据的验收项均已勾选、测试通过、代码审查完成并已提交时，当前票据才属于可领取前沿。
4. 先检查当前代码、测试、迁移和已完成票据产生的公开合同；不得仅依据规划文档猜测现有实现。
5. 以本票据“建议测试接缝”为最高层测试入口，把验收项先转成失败测试，再按 `/tdd` 完成最小纵向实现。
6. 测试只断言可观察行为、权限、状态、产物、错误和恢复结果，不依赖模型私有推理、框架私有对象或数据库内部排列。
7. 实施期间定期运行单测和类型检查；结束前运行受影响集成测试、主接缝测试和完整测试套件。
8. 不得以 MVP、临时演示、关闭权限或跳过质量门的方式缩减票据。若票据、PRD、规范性章节、当前代码或已完成合同发生冲突，停止实施并明确报告，不得静默选择。
9. 需要改变稳定合同、数据语义、安全/科学边界或阻塞图时，先按规划书中的变更与重取证流程处理；不要在普通实现票据中顺手改写上游规范。

全局必读资料：[正式规格](.scratch/science-companion-plan/PRD.md)、[完整项目规划书](.scratch/science-companion-plan/科教智能体项目规划书.md)。各票据下面的资料指针是在此基础上的增量阅读。

### 阶段边界

- **基础设施阶段（T001—T011）：** 建立产品主接缝、前端与无障碍基线、身份项目边界、个人保险库端口、任务生命周期、多用户作用域、四部署骨架、模型锁、观测基础和通用失效传播。
- **产品能力与治理阶段（T012—T050）：** 沿生产主接缝交付评测、科学证据、画像学习、表达、多模态、协作同步和领域包能力；任何已清空阻塞项的分支均可按任务前沿并行。
- **全产品验证与发行阶段（T051—T058）：** 在能力齐备后执行全产品红队、隔离与 SLO 验证、响应式与无障碍实测、容量长稳、故障恢复、供应链、四部署等价和 G0—G5 成熟发行裁决。

## T001 — 建立可运行产品骨架与统一契约链

**What to build:** 建立能够同时启动 Web、API 和基础依赖的首条可运行产品接缝；用户可查看系统健康状态，前后端使用同一版本化合同。

**Blocked by:** None — can start immediately.

**关联需求：** REQ-DEV-01、REQ-OPS-01、REQ-SCOPE-01

**规划书章节：** P4-C16、P4-C19、P5-C21

**关键合同：** CONTRACT-RUN-01、健康状态合同、共享前后端类型合同

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 从一个受支持启动入口启动 Web 与 API，用户读取同一健康投影，并验证开发与生产合同没有 Conda 依赖泄漏。

- [x] Web、API 和基础依赖能够在本地开发环境稳定启动和优雅停止。
- [x] 健康状态通过用户界面和 API 表达相同的存活、就绪与降级语义。
- [x] 前后端合同由单一权威定义产生，并有外部行为测试证明兼容。
- [x] Conda `agent` 只用于本地开发，生产运行合同不依赖 Conda。

## T002 — 建立项目主壳、设计系统、响应式与无障碍基线

**What to build:** 建立认证前后共用的项目主壳、设计令牌、基础组件和响应式布局，让后续功能从第一张页面开始就具备键盘、屏幕阅读器、文本缩放、减少动画与移动端合同。

**Blocked by:** T001 — 建立可运行产品骨架与统一契约链

**关联需求：** REQ-SCI-02、REQ-MM-01、REQ-SCOPE-01

**规划书章节：** P2-C10、P4-C16、P5-C21

**关键合同：** 项目主壳、设计令牌、焦点与路由公告合同、响应式断点、AccessibilityAlternative

**必读资料：** [前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 用户从公共入口进入项目主壳，在桌面和移动视口下仅用键盘与屏幕阅读器完成导航，并在文本缩放和减少动画模式下保持相同行为。

- [x] 公共入口、认证主壳、导航、任务状态和上下文检查器使用同一设计令牌与可访问组件合同。
- [x] 键盘焦点、路由公告、错误摘要、状态文本和跳转路径具有可重复的自动化测试。
- [x] 关键布局在移动端、桌面端和 200% 文本缩放下不丢失操作、状态或内容关系。
- [x] 动画、颜色、图标和实时更新均提供减少动画、冗余编码和可感知替代。

## T003 — 完成账户注册、登录、退出与会话恢复

**What to build:** 让用户完成账户注册、验证、登录、退出、凭据恢复和会话轮换，并阻止未认证用户进入功能页面。

**Blocked by:** T001 — 建立可运行产品骨架与统一契约链；T002 — 建立项目主壳、设计系统、响应式与无障碍基线

**关联需求：** REQ-ID-01

**规划书章节：** P2-C04、P2-C10、P4-C18

**关键合同：** CONTRACT-ID-01、Session、SubjectContext、认证守卫

**必读资料：** [产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 未登录用户注册并登录后进入认证首页，退出或恢复凭据后原会话立即失效。

- [x] 用户可以注册、验证并登录，随后进入认证后的安全首页。
- [x] 无效登录、账户枚举、重复恢复令牌和已撤销会话得到安全且不泄露信息的响应。
- [x] 所有功能路由由服务端与 API 共同执行认证守卫。
- [x] 退出或凭据恢复后，受影响会话不可继续访问。

## T004 — 创建带对象归属的科学项目空间

**What to build:** 让认证用户创建、选择和查看科学项目空间，并始终看到当前账户、项目、对象域、所有者和角色。

**Blocked by:** T003 — 完成账户注册、登录、退出与会话恢复

**关联需求：** REQ-ID-01、REQ-PRO-02

**规划书章节：** P2-C04、P2-C06、P2-C10

**关键合同：** CONTRACT-OBJ-01、Project、ObjectRef、对象域与当前项目投影

**必读资料：** [产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 认证用户创建项目、刷新深链并重新进入，始终得到同一合法项目、对象域与角色投影。

- [x] 用户可以创建、重命名、选择和归档自己的科学项目空间。
- [x] 每个项目及其对象都有明确账户、对象域、所有者和版本。
- [x] 深链、刷新和浏览器前进后退会重新鉴权并恢复合法项目状态。
- [x] 未选择项目时不能创建无归属产物。

## T005 — 建立个人保险库边界与持久化端口

**What to build:** 在任何画像或长期记忆落库前，建立设备侧个人保险库、云端控制数据和单次任务胶囊之间的稳定边界，并让开发环境通过可替换持久化端口验证同一对象语义。

**Blocked by:** T003 — 完成账户注册、登录、退出与会话恢复；T004 — 创建带对象归属的科学项目空间

**关联需求：** REQ-ID-01、REQ-ID-02、REQ-PRO-01、REQ-OPS-01

**规划书章节：** P3-C12、P3-C15、P4-C16、P4-C17

**关键合同：** VaultRepository、VaultObjectRef、CloudControlProjection、TemporaryTaskCapsule、设备不可用状态

**必读资料：** [画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)、[协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户创建一个私人对象并授权给单次项目任务，验证正文权威留在保险库端口，云端只得到最小投影和到期胶囊，设备不可用时系统进入合法等待而不复制全文。

- [x] 私人正文、私人索引、云端控制投影和项目副本具有明确且互不混淆的权威边界。
- [x] 领域模块只依赖稳定保险库端口，不直接依赖设备框架、文件系统或云端供应商对象。
- [x] 单次任务胶囊绑定主体、用途、对象、运行、授权版本、密钥时期和 TTL。
- [x] 测试适配器、设备适配器和云端控制投影通过同一合同套件，设备不可用不会触发静默全文上传。

## T006 — 打通 WorkOrder 与任务舞台生命周期

**What to build:** 让用户从全局科学伙伴提交 `WorkOrder`，并在任务舞台查看节点进度、运行状态、产物可信状态、人工待办以及取消和恢复结果。

**Blocked by:** T004 — 创建带对象归属的科学项目空间

**关联需求：** REQ-PRO-02、REQ-AI-01

**规划书章节：** P2-C05、P2-C10、P3-C13

**关键合同：** CONTRACT-WF-01、WorkOrder、RunContextEnvelope、RunProjection、双状态机

**必读资料：** [智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 认证用户提交 WorkOrder，在任务舞台观察从草拟到合法终态的运行与产物双状态，并可取消或处理人工待办。

- [x] 用户能确认任务目标、成功标准和风险后启动任务。
- [x] 任务舞台分别显示运行状态、产物可信状态和发布资格。
- [x] 长任务支持刷新恢复、取消和具名人工待办，不依赖聊天历史作为真相。
- [x] 非法状态转换和无终止条件的任务被确定性拒绝。

## T007 — 建立多用户基础作用域隔离

**What to build:** 让两个账户能够同时使用基础项目任务生命周期，并建立数据库、API、后台任务、缓存、对象引用与保险库端口共同遵守的作用域隔离合同，供后续数据通道持续扩展回归。

**Blocked by:** T006 — 打通 WorkOrder 与任务舞台生命周期；T005 — 建立个人保险库边界与持久化端口

**关联需求：** REQ-ID-01、REQ-ID-02、REQ-SAFE-01

**规划书章节：** P2-C04、P3-C15、P4-C17、P4-C18

**关键合同：** CONTRACT-AUTH-01、ScopeEnvelope、RLS、作用域缓存键、后台任务信封、保险库端口作用域

**必读资料：** [协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户 A 与用户 B 同时执行基础项目任务，主动尝试通过 API、深链、缓存、任务引用和保险库对象引用越权，所有路径均被拒绝；后续数据型票据必须把自身通道加入同一隔离回归集。

- [x] 两用户、两项目和两机构的合成场景可重复运行。
- [x] 用户无法通过直接请求、深链、缓存键、任务引用或保险库对象引用读取另一用户内容。
- [x] 后台任务缺少主体、对象域、授权版本或密钥时期时失败闭锁。
- [x] 账户切换后不残留前一用户的页面、建议、通知或任务状态，并提供供后续存储与索引通道复用的隔离夹具。

## T008 — 建立四种生产运行合同骨架

**What to build:** 让手动分进程、统一 CLI、Docker 和 Podman 能以同一配置、迁移和健康语义启动基础产品。

**Blocked by:** T001 — 建立可运行产品骨架与统一契约链

**关联需求：** REQ-DEV-01、REQ-OPS-01

**规划书章节：** P4-C19、P5-C21

**关键合同：** CONTRACT-RUN-01、配置 Schema、迁移合同、live/ready/degraded 健康合同

**必读资料：** [Qwen 与技术栈研究](.scratch/science-companion-plan/research/07-qwen-and-technology-stack.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用四种运行载体分别执行 doctor、迁移、启动、健康和停止，比较同一外部行为与配置解释。

- [x] 四种运行方式读取同一配置 Schema 和密钥引用规则。
- [x] 统一 CLI 能同时监管并启动 Web 与 API，同时保持独立进程边界。
- [x] 四种方式执行相同 doctor、迁移、健康和优雅停止冒烟。
- [x] 生产产物不检测或要求 Conda 环境。

## T009 — 接入 Qwen 能力注册表与模型运行锁

**What to build:** 让项目任务通过模型网关调用已注册的 Qwen 逻辑能力，并保存可复现的模型运行锁和诚实降级信息。

**Blocked by:** T006 — 打通 WorkOrder 与任务舞台生命周期

**关联需求：** REQ-AI-01、REQ-EVAL-01、REQ-SAFE-01

**规划书章节：** P3-C13、P4-C19、P5-C20

**关键合同：** 能力注册表、模型运行锁、模型网关、工具运行锁

**必读资料：** [Qwen 与技术栈研究](.scratch/science-companion-plan/research/07-qwen-and-technology-stack.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户启动一个需要 Qwen 的任务，随后从运行检查器看到实际能力与模型锁；未注册、限流和区域错误进入规定状态。

- [x] 任务只使用能力注册表中已验证的逻辑能力和输入输出合同。
- [x] 每次调用记录实际模型、区域、参数、提示版本、限制和替代路径。
- [x] 未注册能力、区域错误或禁止降级条件会阻止调用并给出可解释状态。
- [x] 限流和瞬时失败只在预算内重试，不能跨区或换成未验证模型。

## T010 — 建立运行观测与 SLO 基础设施

**What to build:** 为基础项目任务建立统一追踪、指标、日志、审计关联和 SLO 注册机制，让后续摄入、教学、表达、多模态、评测与治理票据能够按同一合同增加观测而不复制私人正文。

**Blocked by:** T006 — 打通 WorkOrder 与任务舞台生命周期；T008 — 建立四种生产运行合同骨架

**关联需求：** REQ-EVAL-01、REQ-OPS-01、REQ-SAFE-01

**规划书章节：** P4-C16、P4-C19、P5-C21

**关键合同：** RunSummary、审计事件、OpenTelemetry 关联合同、SLI/SLO 注册表、AlertOwnership

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 用户执行一次成功、一次重试和一次阻塞的基础任务，运维人员能从统一追踪重建过程且观测数据不包含私人正文，新增工作负载可通过注册合同加入自身 SLI。

- [x] trace、metric、log 和审计事件使用统一运行、主体、项目与对象标识关联。
- [x] 观测系统不复制私人正文、完整提示、密钥或不必要模型输出。
- [x] SLI/SLO 注册表允许后续工作负载声明延迟、可用性、正确性和降级指标及责任人。
- [x] 基础告警具有所有者、运行手册、去重和关闭证据，并提供后续票据复用的观测合同测试。

## T011 — 建立通用失效、墓碑与影响传播基础

**What to build:** 建立跨来源、画像、授权、索引、领域包和同步对象复用的不可变失效事件、删除墓碑、影响集与重验证调度基础，使任何领域都不能通过静默覆盖处理撤回和删除。

**Blocked by:** T007 — 建立多用户基础作用域隔离；T006 — 打通 WorkOrder 与任务舞台生命周期

**关联需求：** REQ-SAFE-01、REQ-ID-01、REQ-PRO-01、REQ-EVAL-01

**规划书章节：** P3-C11、P3-C12、P3-C15、P4-C17、P5-C22

**关键合同：** InvalidationEvent、Tombstone、ImpactSet、InvalidationPlan、Outbox 传播与重验证调度合同

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 将一个已被任务、缓存和索引投影使用的通用测试对象撤权或删除，验证先写事件或墓碑、立即阻止新使用、产生作用域正确的影响集并调度幂等重验证。

- [x] 失效事件和墓碑不可变、版本化并绑定主体、对象域、原因、授权版本和密钥时期。
- [x] 新读取和新运行先检查当前失效状态，缓存、索引和后台任务不能旁路。
- [x] 影响集通过 Outbox 幂等传播到已登记下游，失败可重放且不会扩大作用域。
- [x] 领域模块只能扩展影响解析和重验证处理器，不能改变墓碑优先、历史保留和失败闭锁下限。

## T012 — 从项目任务生成首个可重放评测运行

**What to build:** 让用户从一次项目任务创建评测运行，并在评测与运行中心重放、比较输入、运行锁、结果与失败。

**Blocked by:** T006 — 打通 WorkOrder 与任务舞台生命周期；T009 — 接入 Qwen 能力注册表与模型运行锁

**关联需求：** REQ-EVAL-01、REQ-PRO-02

**规划书章节：** P2-C10、P5-C20

**关键合同：** CONTRACT-EVAL-01、EvaluationRunLock、EvaluationResultBundle

**必读资料：** [持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户从一个已完成项目任务创建两次同锁评测运行，在评测中心比较相同输入、版本、结果与失败。

- [x] 评测运行冻结任务输入、代码/环境标识、模型锁、Schema 和工作流版本。
- [x] 同一运行锁可重复执行并产生可比较的结果包。
- [x] 运行中心显示结果差异、资源使用和失败原因，不暴露私人正文到日志。
- [x] 评测逻辑通过生产主接缝运行，不维护旁路业务实现。

## T013 — 导入并版本化文本与 PDF 科学来源

**What to build:** 让用户向自己的项目导入文本和 PDF，查看结构化解析、来源版本、许可、状态和错误，并阻止未通过输入门的内容进入可信证据。

**Blocked by:** T007 — 建立多用户基础作用域隔离；T011 — 建立通用失效、墓碑与影响传播基础

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-ID-01

**规划书章节：** P3-C11、P4-C17、P4-C18

**关键合同：** CONTRACT-SCI-01、Source、DocumentVersion、ChunkVersion、输入质量门

**必读资料：** [科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户向自己的项目上传文本或 PDF，纠正解析并创建新版本；另一账户和未通过输入门的材料均无法进入检索。

- [x] 用户能上传、解析、纠正和重新版本化文本或 PDF。
- [x] 来源、文档版本、结构块、页码和内容哈希可追溯。
- [x] 恶意内容、文件炸弹、解析失败和许可缺失进入隔离或等待状态。
- [x] 私人来源不会出现在其他账户的缓存、索引或去重提示中。

## T014 — 完成作用域约束的混合检索

**What to build:** 让用户在当前项目和授权范围内使用全文、向量融合与重排检索科学材料，并看见检索覆盖与缺口。

**Blocked by:** T013 — 导入并版本化文本与 PDF 科学来源

**关联需求：** REQ-SCI-01、REQ-ID-01

**规划书章节：** P3-C11、P4-C17

**关键合同：** CONTRACT-SCI-01、ScopeEnvelope、EvidenceSet、索引版本合同

**必读资料：** [科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户在项目中检索一个科学问题，结果同时包含可见的词法与语义候选，并验证撤权、删除与跨账户材料零召回。

- [x] 检索前编译用户、项目、对象域、授权版本和密钥时期。
- [x] 词法与向量候选经过作用域预过滤、融合、重排和输出后二次鉴权。
- [x] 已撤权、删除、失效和其他账户内容不可被召回。
- [x] 相关性分数只用于排序，不作为证据强度展示。

## T015 — 生成可定位的 Claim—Evidence—Citation 关系

**What to build:** 让用户从检索材料得到带支持、反驳、限制和精确引用位置的科学 Claim，并能从产物回到证据。

**Blocked by:** T014 — 完成作用域约束的混合检索；T009 — 接入 Qwen 能力注册表与模型运行锁

**关联需求：** REQ-SCI-01、REQ-SAFE-01

**规划书章节：** P2-C10、P3-C11

**关键合同：** CONTRACT-SCI-01、ClaimGraph、Evidence、Citation、出处图投影

**必读资料：** [科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 用户从任务产物打开引用检查器，逐个 Claim 回到正确来源版本和位置，并观察反驳与限制证据。

- [x] 每个重要 Claim 关联来源版本、精确位置和 Evidence 关系。
- [x] 引用定位在来源版本变化后仍可验证或明确失效。
- [x] Claim、Evidence 和 Citation 修改产生新版本，不覆盖历史。
- [x] 伪造关键引用和无法定位的高置信引用被发布门拒绝。

## T016 — 完成证据冲突、事实锁与诚实降级

**What to build:** 让用户在证据冲突、来源未知或证据不足时获得可解释的限制，并让后续教学、表达和媒体受到事实锁约束。

**Blocked by:** T015 — 生成可定位的 Claim—Evidence—Citation 关系

**关联需求：** REQ-SCI-01、REQ-EXP-01、REQ-SAFE-01

**规划书章节：** P2-C08、P3-C11、P3-C13

**关键合同：** FactLockSet、ClaimGraph、ValidationReport、科学质量门

**必读资料：** [科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)、[有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)

**建议测试接缝：** 对同一问题输入支持、冲突、未知和不足四种证据状态，验证任务舞台、措辞和发布资格进入相应合法状态。

- [x] 支持、反驳与限制证据并列保留，不通过模型投票或最后写入消除。
- [x] 事实锁固定数字、单位、对象、关系、限定、术语、公式和引用。
- [x] 措辞强度根据证据状态被确定性限制。
- [x] 冲突、未知和不足状态具有合法的人工门、补证据或阻塞路径。

## T017 — 传播来源失效并定位下游影响

**What to build:** 让来源撤回、状态未知或版本失效能够阻止新发布，定位关联 Claim、事实锁、运行和产物，并支持重验证。

**Blocked by:** T016 — 完成证据冲突、事实锁与诚实降级；T011 — 建立通用失效、墓碑与影响传播基础

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P2-C05、P3-C11、P5-C22

**关键合同：** InvalidationEvent、ImpactSet、领域影响解析器、重新验证合同

**必读资料：** [科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 将一个已用于发布的来源置为撤回，验证新发布被阻断、既有产物显示失效影响，并能产生新版本重验证。

- [x] 来源状态变化通过通用失效基础生成不可变事件，并由科学领域解析器扩展影响集。
- [x] 新任务和发布不会继续使用失效来源。
- [x] 既有产物保留原运行快照，同时显示当前失效状态。
- [x] 重验证生成新版本和新证据，不静默改写历史。

## T018 — 形成画像观察与候选画像闭环

**What to build:** 让系统从合法用户信号提出带来源、范围、置信度和有效期的候选画像，并让用户决定是否保留。

**Blocked by:** T007 — 建立多用户基础作用域隔离；T006 — 打通 WorkOrder 与任务舞台生命周期；T005 — 建立个人保险库边界与持久化端口

**关联需求：** REQ-PRO-01、REQ-ID-02

**规划书章节：** P3-C12

**关键合同：** CONTRACT-PROFILE-01、ProfileObservation、ProfileCandidate、HumanDecision

**必读资料：** [画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)

**建议测试接缝：** 用户完成一段对话后查看候选画像，接受、修改或拒绝；后续任务不得把未确认候选作为稳定事实。

- [x] 画像观察保留原始来源、场景、授权和内容哈希。
- [x] 候选画像不能作为稳定事实进入后续任务。
- [x] 单次情绪、示例人物和敏感身份推断不会形成稳定画像。
- [x] 用户能接受、拒绝或修改候选，并看到决定依据。

## T019 — 编译并解释最小记忆切片

**What to build:** 让用户在一次任务中只使用相关、已授权且可解释的画像与记忆条目，并在上下文检查器查看调用原因。

**Blocked by:** T018 — 形成画像观察与候选画像闭环

**关联需求：** REQ-PRO-01、REQ-PRO-02、REQ-ID-02

**规划书章节：** P2-C10、P3-C12、P3-C13

**关键合同：** CONTRACT-PROFILE-01、MemorySlice、ContextSlice、RunContextEnvelope

**必读资料：** [画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户提交一个需要个性化的任务，从画像切片检查器查看实际使用、拒绝和未使用条目，并验证模型只能访问切片。

- [x] 记忆切片按任务目的、对象域、授权、有效期和敏感级别编译。
- [x] 模型和工作节点只能读取切片，不能浏览完整个人保险库。
- [x] 用户能查看实际使用、未使用和被拒绝的条目及原因。
- [x] 切片与运行绑定，到期、撤权、取消或删除后失效。

## T020 — 完成画像确认、冻结、删除、导出与回滚

**What to build:** 让用户管理证据化画像的完整生命周期，并验证治理操作传播到记忆切片、任务、缓存、索引和派生产物。

**Blocked by:** T019 — 编译并解释最小记忆切片；T011 — 建立通用失效、墓碑与影响传播基础

**关联需求：** REQ-PRO-01、REQ-PRO-02、REQ-ID-02、REQ-SAFE-01

**规划书章节：** P3-C12、P3-C15、P4-C17

**关键合同：** CONTRACT-PROFILE-01、ProfileAssertionVersion、Tombstone、InvalidationPlan

**必读资料：** [画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)、[协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)

**建议测试接缝：** 用户对同一画像执行确认、冻结、删除和回滚，再配对运行相同任务，验证切片、回答、索引与审计按预期变化。

- [x] 用户能逐条确认、修改、冻结、删除、导出和回滚画像版本。
- [x] 删除先写墓碑并阻止新召回，再处理索引、缓存和派生影响。
- [x] 冻结、删除或回滚前后的配对任务显示预期差异。
- [x] 审计只保留必要事件与哈希，不保留被删除正文。

## T021 — 建立学习使命与先备知识诊断

**What to build:** 让用户定义真实学习使命和成功标准，并获得基于证据而非标签的先备知识与最近发展区判断。

**Blocked by:** T019 — 编译并解释最小记忆切片；T015 — 生成可定位的 Claim—Evidence—Citation 关系

**关联需求：** REQ-SCI-02、REQ-PRO-01、REQ-ID-02

**规划书章节：** P2-C07、P3-C12

**关键合同：** LearningMission、KnowledgeState、TeachingPlan、CONTRACT-PROFILE-01

**必读资料：** [产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)、[画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)

**建议测试接缝：** 两个不同基础的用户建立相同主题使命并完成诊断，得到有证据解释的不同最近发展区。

- [x] 学习使命记录目标、范围、约束和可观察成功标准。
- [x] 诊断问题和结果绑定科学证据、用户回答和版本。
- [x] 没有学习证据时不会把浏览、完成率或模型猜测当成掌握。
- [x] 用户能纠正诊断并看到下一验证任务。

## T022 — 生成可信短课与检索练习

**What to build:** 让用户获得围绕单一学习胜利的短课、示例、检索练习和即时反馈，并能查看其科学依据。

**Blocked by:** T021 — 建立学习使命与先备知识诊断；T016 — 完成证据冲突、事实锁与诚实降级

**关联需求：** REQ-SCI-01、REQ-SCI-02、REQ-PRO-02

**规划书章节：** P2-C07、P3-C11、P3-C13

**关键合同：** CONTRACT-CREATE-01、TeachingPlan、EvidenceSet、教学质量门

**必读资料：** [产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户从学习使命开始完成一节短课和检索练习，随后从任务舞台查看证据、反馈与教学门结果。

- [x] 短课只引入完成当前学习胜利所需的概念。
- [x] 讲解、示例、答案与反馈绑定 EvidenceSet 和事实锁。
- [x] 练习要求用户回忆、解释、计算、比较或应用，而非只浏览。
- [x] 证据不足或高风险主题进入降级或人工门。

## T023 — 由学习证据更新知识状态和学习路径

**What to build:** 让用户完成练习后产生可审查的学习记录候选，并确认或拒绝知识状态和路径的重要变化。

**Blocked by:** T022 — 生成可信短课与检索练习；T020 — 完成画像确认、冻结、删除、导出与回滚

**关联需求：** REQ-PRO-01、REQ-PRO-02、REQ-ID-02

**规划书章节：** P2-C07、P3-C12

**关键合同：** LearningRecord、KnowledgeStateProposal、LearningPath、HumanDecision

**必读资料：** [画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)、[持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)

**建议测试接缝：** 用户完成、纠错或仅浏览三种学习行为，验证只有前两类合格证据能提出知识状态和路径更新。

- [x] 学习记录引用具体作答、误区纠正或先备能力证据。
- [x] 知识状态保留不确定性、反证、适用范围和下一验证任务。
- [x] 用户确认重要路径变化，拒绝不会被模型绕过。
- [x] 不同用户在相同主题上能形成可解释的不同路径。

## T024 — 完成间隔复习与交错练习调度

**What to build:** 让用户依据自己的知识状态获得可解释、可调整的间隔复习和跨主题交错练习。

**Blocked by:** T023 — 由学习证据更新知识状态和学习路径；T006 — 打通 WorkOrder 与任务舞台生命周期

**关联需求：** REQ-SCI-02、REQ-PRO-01、REQ-ID-02

**规划书章节：** P2-C07、P3-C13

**关键合同：** LearningPath、ReviewSchedule、定时工作流、取消与重编合同

**必读资料：** [产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户获得一项有理由的间隔复习，延后或完成后更新记录；删除或路径变化会取消或重编未来任务。

- [x] 调度引用学习记录、知识状态、遗忘证据和学习使命。
- [x] 用户能查看原因、延后、调整或取消复习任务。
- [x] 复习结果创建新学习证据，不直接覆盖旧状态。
- [x] 定时任务在撤权、删除或路径变化后正确取消或重编。

## T025 — 从表达任务契约生成事实锁草稿

**What to build:** 让用户指定目标、受众、体裁、渠道、长度和风险，并得到绑定 Claim、Citation 与事实锁的首个表达草稿。

**Blocked by:** T016 — 完成证据冲突、事实锁与诚实降级；T019 — 编译并解释最小记忆切片；T009 — 接入 Qwen 能力注册表与模型运行锁

**关联需求：** REQ-SCI-01、REQ-EXP-01、REQ-PRO-01

**规划书章节：** P2-C08、P3-C11、P3-C12

**关键合同：** CONTRACT-CREATE-01、ExpressionBrief、AudienceModel、FactLockSet、ExpressionDraft

**必读资料：** [有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)、[科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)

**建议测试接缝：** 用户创建表达任务契约并生成初稿，随后从引用与画像检查器验证每个重要判断和个性化影响。

- [x] 表达任务契约包含目标、受众、体裁、风险、必需判断和成功标准。
- [x] 草稿每个重要判断能回到 Claim、Evidence、Citation 和事实锁。
- [x] 个性化只使用本次记忆切片，且不能改变事实强度。
- [x] 缺少关键证据、授权或体裁责任时任务进入可解释阻塞。

## T026 — 完成科普文案与课程讲稿体裁合同

**What to build:** 让用户为大众科普和教学场景生成、审阅与修订适配受众的内容，同时保留类比边界和教学检查点。

**Blocked by:** T025 — 从表达任务契约生成事实锁草稿

**关联需求：** REQ-SCI-02、REQ-EXP-01

**规划书章节：** P2-C07、P2-C08

**关键合同：** GenreContract、ArgumentPlan、ExpressionDraft、ReviewReport

**必读资料：** [有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)、[产品能力决策](.scratch/science-companion-plan/decisions/02-product-capability-system.md)

**建议测试接缝：** 用户把同一事实锁分别生成科普文案和课程讲稿，验证体裁差异、类比边界与教学检查点，同时事实保持不变。

- [x] 科普文案区分核心概念、类比、类比失效边界和行动相关性。
- [x] 课程讲稿包含学习目标、先备要求、理解检查和练习停顿。
- [x] 体裁转换不改变事实锁、引用和结论强度。
- [x] 用户可以查看体裁规则为何要求或禁止某项内容。

## T027 — 完成科研汇报与论文辅助体裁合同

**What to build:** 让用户生成科研汇报或论文辅助内容，明确区分数据、推断、限制、引用与作者责任。

**Blocked by:** T025 — 从表达任务契约生成事实锁草稿

**关联需求：** REQ-SCI-01、REQ-SCI-02、REQ-EXP-01

**规划书章节：** P2-C08

**关键合同：** GenreContract、ArgumentPlan、ExpressionDraft、作者责任声明

**必读资料：** [有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)

**建议测试接缝：** 用户把同一证据集分别生成科研汇报和论文辅助版本，验证数据、推断、限制、引用与责任边界。

- [x] 科研汇报分离观测、分析、解释、限制和下一步。
- [x] 论文辅助只支持结构、语言、引用核验和论证建议，不虚构数据或实验。
- [x] 高风险或强度升级请求进入人工确认。
- [x] 输出保留完整版本和责任说明。

## T028 — 完成人味诊断与逐条修订闭环

**What to build:** 让用户查看中文模板腔、翻译腔、节奏与分寸问题，并逐条接受、拒绝或改写不改变事实锁的补丁。

**Blocked by:** T026 — 完成科普文案与课程讲稿体裁合同；T027 — 完成科研汇报与论文辅助体裁合同

**关联需求：** REQ-EXP-01、REQ-PRO-02、REQ-EVAL-01

**规划书章节：** P2-C08、P5-C20

**关键合同：** StylePolicy、ReviewReport、RevisionPatch、事实锁差异合同

**必读资料：** [有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)、[持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)

**建议测试接缝：** 用户收到逐条中文表达诊断，选择接受、拒绝和改写不同补丁，验证事实锁不变且反馈进入正确对象。

- [ ] 诊断报告定位具体文本、问题类型、理由和建议补丁。
- [ ] 补丁应用前后自动比较事实锁、引用与措辞强度。
- [ ] 用户反馈分别路由到当前版本、候选偏好、学习记录或事实复核。
- [ ] AI 检测器分数不作为质量目标或通过门。

## T029 — 完成表达版本比较与发布门

**What to build:** 让用户比较表达版本中的事实、证据、结构、措辞、模型和人工修改，并只发布满足全部资格的版本。

**Blocked by:** T028 — 完成人味诊断与逐条修订闭环；T017 — 传播来源失效并定位下游影响

**关联需求：** REQ-SCI-01、REQ-EXP-01、REQ-SAFE-01

**规划书章节：** P2-C08、P2-C10、P3-C13

**关键合同：** ArtifactVersion、ValidationReport、HumanDecision、发布资格合同

**必读资料：** [有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 用户比较两个表达版本并尝试发布，验证事实、证据、措辞、人工修改和失效状态共同决定发布资格。

- [ ] 版本比较突出事实锁、Claim、引用、结论强度和人工决定变化。
- [ ] 工作流成功但产物未批准时发布入口保持锁定。
- [ ] 来源、授权或上游事实锁失效会撤销当前发布资格。
- [ ] 发布事件与实际版本、责任人和质量门结果绑定。

## T030 — 摄入并校正图片、扫描件、公式和表格

**What to build:** 让用户导入科学图片、扫描件、公式与表格，查看和纠正 OCR、区域、符号、单位和数据结构。

**Blocked by:** T013 — 导入并版本化文本与 PDF 科学来源；T016 — 完成证据冲突、事实锁与诚实降级

**关联需求：** REQ-MM-01、REQ-SCI-01、REQ-SAFE-01

**规划书章节：** P2-C09、P3-C11

**关键合同：** SourceAsset、DerivedAsset、MediaManifest、结构提取修订合同

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)

**建议测试接缝：** 用户上传含图片、公式和表格的科学材料，纠正一个识别错误并验证派生版本、Claim 和原始资产关系。

- [ ] 图片区域、OCR、图例、尺度和置信度可查看与修正。
- [ ] 公式保留符号表、结构、变量定义和可访问表示。
- [ ] 表格保留 Schema、单位、缺失值、来源和原始数据对应。
- [ ] 人工纠正创建派生版本并保留原始资产。

## T031 — 摄入并校正音频与视频材料

**What to build:** 让用户导入音频和视频，查看并纠正 ASR、时间戳、说话段、字幕、关键帧和科学术语。

**Blocked by:** T013 — 导入并版本化文本与 PDF 科学来源；T009 — 接入 Qwen 能力注册表与模型运行锁

**关联需求：** REQ-MM-01、REQ-AI-01、REQ-ID-01

**规划书章节：** P2-C09、P4-C19

**关键合同：** SourceAsset、时间轴转写合同、模型运行锁、MediaManifest

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[Qwen 与技术栈研究](.scratch/science-companion-plan/research/07-qwen-and-technology-stack.md)

**建议测试接缝：** 用户上传音频或视频，纠正一个低置信科学术语，验证时间轴、字幕、版本与账户作用域。

- [ ] 音视频解析结果与原始时间轴和资产版本绑定。
- [ ] 用户能修正术语、说话段、字幕与关键帧解释。
- [ ] 多语言、低置信片段和缺失音轨被明确标记。
- [ ] 私人媒体的中间结果遵守账户与项目作用域。

## T032 — 生成可编辑静态科学图与数据图表

**What to build:** 让用户从科学证据和数据生成带可编辑源、Claim 绑定、单位、图例、替代文本与数据表的静态作品。

**Blocked by:** T030 — 摄入并校正图片、扫描件、公式和表格；T016 — 完成证据冲突、事实锁与诚实降级

**关联需求：** REQ-MM-01、REQ-SCI-01、REQ-SCI-02

**规划书章节：** P2-C09

**关键合同：** ScientificMediaObject、EditableSource、FactLockSet、AccessibilityAlternative

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)

**建议测试接缝：** 用户从带单位的数据和 Claim 生成一张可编辑图表，修改源后重新验证数值、事实绑定与等价数据表。

- [ ] 图表数值、轴、单位、聚合和误差表达与源数据一致。
- [ ] 科学图中的标签和关系绑定 Claim 与事实锁。
- [ ] 用户能修改可编辑源并重新验证。
- [ ] 每个视觉作品具备替代文本或等价数据表。

## T033 — 生成结构化分镜并在沙箱运行交互或动画

**What to build:** 让用户先审查结构化分镜和可编辑源，再在强隔离沙箱中运行交互或动画并查看验证结果。

**Blocked by:** T030 — 摄入并校正图片、扫描件、公式和表格；T008 — 建立四种生产运行合同骨架；T009 — 接入 Qwen 能力注册表与模型运行锁

**关联需求：** REQ-MM-01、REQ-AI-01、REQ-SAFE-01

**规划书章节：** P2-C09、P4-C18、P4-C19

**关键合同：** MediaStoryboard、EditableSource、SandboxRun、ValidationReport

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户确认分镜后运行交互或动画，分别验证成功、有限修复和沙箱失败时的可见产物与发布状态。

- [ ] 分镜明确教学目标、对象、布局、状态、时间、旁白和 Claim 绑定。
- [ ] 生成代码在无生产网络、无密钥和有限资源的隔离环境运行。
- [ ] 失败只能在事实锁不变时有限修复，预算耗尽后成品隔离。
- [ ] 沙箱记录依赖、资源、输出、错误和内容哈希。

## T034 — 生成朗读、字幕和完整无障碍替代

**What to build:** 让科学媒体对象拥有朗读、文字稿、字幕、替代文本、键盘路径、减少动画和顺序阅读版本。

**Blocked by:** T031 — 摄入并校正音频与视频材料；T032 — 生成可编辑静态科学图与数据图表；T033 — 生成结构化分镜并在沙箱运行交互或动画

**关联需求：** REQ-MM-01、REQ-SCI-02

**规划书章节：** P2-C09、P2-C10

**关键合同：** AccessibilityAlternative、CaptionTrack、Transcript、减少动画与顺序视图合同

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 键盘和屏幕阅读器用户完成同一媒体任务，并在减少动画模式下获得与视觉/听觉成品一致的科学内容。

- [ ] 音频、字幕和文字稿共享同一科学 Claim 与版本。
- [ ] 交互和媒体核心任务可由键盘与屏幕阅读器完成。
- [ ] 用户可以暂停、控制时间内容并启用减少动画。
- [ ] 无障碍替代经过相同科学校验，不形成另一个未经验证的答案。

## T035 — 完成跨媒体一致性与多模态发布门

**What to build:** 让用户在发布前看到文本、图表、公式、音频、视频和交互的 Claim 一致性、许可、安全与无障碍结果。

**Blocked by:** T029 — 完成表达版本比较与发布门；T034 — 生成朗读、字幕和完整无障碍替代

**关联需求：** REQ-MM-01、REQ-SCI-01、REQ-SAFE-01

**规划书章节：** P2-C09、P3-C13、P5-C20

**关键合同：** ScientificMediaObject、跨媒体 Claim 映射、多模态质量门、发布资格合同

**必读资料：** [多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 用户从同一项目发布包含文本、图表、音频和交互的媒体对象，故意引入跨媒体事实差异并验证发布闭锁。

- [ ] 跨媒体核心事实、数值、术语、限定条件和引用一致。
- [ ] 许可、真实性、沙箱和无障碍任一必需门失败会阻止发布。
- [ ] 发布版本保留原始资产、分镜、可编辑源、渲染物和验证记录。
- [ ] 来源或事实锁失效会传播到所有媒体派生版本。

## T036 — 完成显式共享项目与最小项目副本

**What to build:** 让用户在分享前选择对象、接收者、用途、期限和权限，并生成与个人原件独立的最小项目副本。

**Blocked by:** T007 — 建立多用户基础作用域隔离；T020 — 完成画像确认、冻结、删除、导出与回滚

**关联需求：** REQ-ID-01、REQ-PRO-01、REQ-SAFE-01

**规划书章节：** P3-C15、P2-C10

**关键合同：** CONTRACT-OBJ-01、SharePreview、ProjectObjectRef、ObjectGrant

**必读资料：** [协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)

**建议测试接缝：** 用户从个人保险库选择一个对象并完成分享预览，协作者只能看到生成的最小项目副本，原件后续修改不自动同步。

- [ ] 分享预览明确显示包含与排除字段、所有者和后续独立性。
- [ ] 共享项目只引用项目对象，不挂载个人保险库正文。
- [ ] 角色和对象授权共同决定查看、编辑、审阅与发布。
- [ ] 邀请令牌短时、单用、绑定项目与预期身份。

## T037 — 完成机构管理域与管理员边界

**What to build:** 让机构管理员管理成员、席位、策略和机构自有项目，同时证明其不能读取成员个人保险库。

**Blocked by:** T036 — 完成显式共享项目与最小项目副本

**关联需求：** REQ-ID-01、REQ-SAFE-01

**规划书章节：** P2-C04、P3-C15、P4-C18

**关键合同：** Tenant、Membership、InstitutionOwnedProject、受控正文访问合同

**必读资料：** [协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 同一用户加入两个机构，管理员分别管理成员但无法通过界面、API 或审计旁路读取个人保险库。

- [ ] 账户可加入多个互相不可见的机构。
- [ ] 管理成员与读取内容是分开的权限和界面操作。
- [ ] 机构自有项目创建前披露所有权、保留和恢复边界。
- [ ] 安全事件正文访问需要最小范围、双人批准、时限和完整审计。

## T038 — 配对个人保险库并保存加密本地对象

**What to build:** 让用户将设备与账户配对，在设备侧加密保存私人正文、索引和离线操作，并为任务授权最小切片。

**Blocked by:** T020 — 完成画像确认、冻结、删除、导出与回滚；T008 — 建立四种生产运行合同骨架

**关联需求：** REQ-PRO-01、REQ-ID-01、REQ-OPS-01

**规划书章节：** P3-C15、P4-C16、P4-C17

**关键合同：** VaultRuntime、DeviceCertificate、KeyEpoch、TemporaryTaskCapsule

**必读资料：** [协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 用户配对设备、保存私人对象并授权一次任务切片，验证设备明文权威、云端最小胶囊和过期失效。

- [ ] 设备证书、密钥时期和配对状态与用户账户绑定。
- [ ] 设备私钥进入系统密钥库，不写入普通配置或日志。
- [ ] 个人正文和私人索引默认在设备侧保持明文权威。
- [ ] 云端只能获得与单次运行、用途、对象和 TTL 绑定的任务胶囊。

## T039 — 完成因果同步、冲突分支、撤权和删除墓碑

**What to build:** 让在线与离线设备同步版本化操作，优先应用授权、密钥时期和墓碑，并为科学语义冲突保留人工可裁决分支。

**Blocked by:** T036 — 完成显式共享项目与最小项目副本；T038 — 配对个人保险库并保存加密本地对象；T017 — 传播来源失效并定位下游影响

**关联需求：** REQ-ID-01、REQ-PRO-01、REQ-SAFE-01

**规划书章节：** P3-C15、P4-C17、P4-C18

**关键合同：** SyncOperation、ConflictBranch、DeviceAck、Tombstone、KeyEpoch

**必读资料：** [协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 离线设备编辑一个随后被删除的对象，上线时先接收墓碑和新密钥时期，旧修改被隔离而不能复活对象。

- [ ] 同步先拉取策略、撤权、密钥时期和墓碑，再提交本地操作。
- [ ] 旧密钥时期或已撤权设备的提交被拒绝并隔离。
- [ ] 删除墓碑优先于离线编辑，恢复后对象不会复活。
- [ ] 科学判断、事实锁、归属和授权冲突不使用最后写入者获胜。

## T040 — 建立领域包协议与验证运行时

**What to build:** 建立领域包稳定协议、加载器和候选验证运行时，让任意学科包的来源、规则、校验器、夹具、依赖、版本、迁移和平台下限能够独立登记并通过统一主接缝运行。

**Blocked by:** T016 — 完成证据冲突、事实锁与诚实降级；T009 — 接入 Qwen 能力注册表与模型运行锁；T011 — 建立通用失效、墓碑与影响传播基础

**关联需求：** REQ-SCI-01、REQ-AI-01、REQ-EVAL-01

**规划书章节：** P3-C14

**关键合同：** DomainPackManifest、DomainRule、ValidatorRequirement、FixtureCase、PackDependencyLock

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)、[科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)

**建议测试接缝：** 一个通用领域测试任务装载签名前的最小包候选，运行规则、校验器与夹具并显示为何通过、阻塞或需要人工判断，同时拒绝放宽平台下限的包。

- [ ] Manifest 声明范围、排除项、来源、规则、能力、夹具、依赖和风险。
- [ ] 加载器验证 Schema、摘要、依赖锁、能力合同和兼容范围，并通过生产工作流运行候选夹具。
- [ ] 领域规则不能放宽平台权限、安全、审计和发布下限。
- [ ] 包升级创建新版本并提供兼容、迁移、失效和受信回滚信息。

## T041 — 交付数学与形式证明领域包

**What to build:** 基于稳定领域包协议交付数学与形式证明包，让用户任务能够核验定义、符号、前提、推理步骤和证明状态，并对不完整或错误证明给出可追踪结论。

**Blocked by:** T040 — 建立领域包协议与验证运行时

**关联需求：** REQ-SCI-01、REQ-AI-01、REQ-EVAL-01

**规划书章节：** P3-C14

**关键合同：** DomainPackManifest、数学定义与符号规则、证明步骤校验器、FixtureCase

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)、[科学证据研究](.scratch/science-companion-plan/research/03-scientific-trust-system.md)

**建议测试接缝：** 用户提交正确证明、缺失前提、符号歧义和无效推理四类任务，验证数学包通过统一运行时给出通过、阻塞或人工判断，并保留逐步依据。

- [ ] 数学包声明适用范围、排除项、权威来源、术语和版本兼容范围。
- [ ] 定义、符号、前提、推理步骤和证明状态具有确定性规则或明确人工门。
- [ ] 夹具覆盖正确证明、缺失前提、循环论证、符号冲突和无法判定状态。
- [ ] 包输出保持 Claim、Evidence、事实锁和验证报告可追溯，不能把模型生成步骤直接视为已证明。

## T042 — 交付物理与化学实验测量领域包

**What to build:** 让物理和化学任务正确处理实验条件、测量不确定性、单位、有效数字、物质关系与安全边界。

**Blocked by:** T040 — 建立领域包协议与验证运行时

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P3-C14

**关键合同：** DomainPackManifest、测量/单位规则、实验安全门、FixtureCase

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)

**建议测试接缝：** 用户执行包含测量不确定性、单位与危险实验建议的任务，验证领域规则、措辞限制和安全门。

- [ ] 包定义学科来源层级、术语、单位和证据映射。
- [ ] 测量、实验条件、误差和化学规则有确定性夹具。
- [ ] 危险实验建议进入安全拒绝或人工门。
- [ ] 包与公共平台规则组合后保持 Claim 和措辞可追溯。

## T043 — 交付生命科学与医学高风险领域包

**What to build:** 让生命科学和医学教育任务遵守来源层级、适用人群、证据限制、高风险人工门和禁止越界规则。

**Blocked by:** T040 — 建立领域包协议与验证运行时

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P3-C14、P5-C22

**关键合同：** 高风险 DomainPack、H3 联合门、医学禁止场景、FixtureCase

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)

**建议测试接缝：** 用户提交生命科学教育与医学高风险请求，验证适用范围、证据等级、人工门和诊断/处方禁止规则。

- [ ] 生命科学与医学包分别声明适用和排除范围。
- [ ] 医学高风险内容不能形成诊断、处方或替代专业决策。
- [ ] 关键来源状态未知或证据冲突时高置信发布闭锁。
- [ ] H3 变更要求合资格领域专家和安全治理联合确认。

## T044 — 交付地球、气候与天文学领域包

**What to build:** 让地球、气候与天文学任务正确表达观测、时间空间尺度、模型依赖、不确定性和数据版本。

**Blocked by:** T040 — 建立领域包协议与验证运行时

**关联需求：** REQ-SCI-01、REQ-EVAL-01

**规划书章节：** P3-C14

**关键合同：** 观测数据规则、尺度与模型限定合同、DomainPackManifest

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)

**建议测试接缝：** 用户比较观测与模型结论，验证时间空间尺度、数据版本、不确定性和相关因果边界进入 Claim 与措辞。

- [ ] 包能区分观测事实、模型输出、情景和推断。
- [ ] 时间、空间、仪器和数据版本进入 Claim 限定条件。
- [ ] 数据修订和来源状态变化能触发影响分析。
- [ ] 夹具覆盖尺度误用、相关因果混淆和模型外推。

## T045 — 交付计算机科学、标准与数据集领域包

**What to build:** 让系统处理软件版本、规范状态、安全公告、数据集版本、许可和跨学科标准证据。

**Blocked by:** T040 — 建立领域包协议与验证运行时

**关联需求：** REQ-SCI-01、REQ-EVAL-01

**规划书章节：** P3-C14、P4-C19

**关键合同：** 软件/标准版本规则、数据集许可与状态合同、DomainPackManifest

**必读资料：** [领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)、[Qwen 与技术栈研究](.scratch/science-companion-plan/research/07-qwen-and-technology-stack.md)

**建议测试接缝：** 用户查询一个版本相关的软件或标准问题，验证答案绑定有效一手版本、许可和状态，过时资料不能覆盖。

- [ ] 软件与规范结论绑定明确版本、发布日期和状态。
- [ ] 数据集来源、许可、切片、更新和撤回信息可追溯。
- [ ] 过时文档、废弃 API 和非权威博客不会覆盖有效一手规范。
- [ ] 跨学科规则冲突按明确优先级进入人工门。

## T046 — 完成专家工作台、三签与灰度发行

**What to build:** 让内容维护者、独立复核者和平台发行者通过阶段控制台、语义 Diff、夹具、资质与职责分离完成领域包灰度发行。

**Blocked by:** T040 — 建立领域包协议与验证运行时；T007 — 建立多用户基础作用域隔离

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P3-C14、P2-C10

**关键合同：** 规范化包摘要、ReviewAttestation、QualificationRecord、SemanticDiff、PackRelease

**必读资料：** [专家工作台决策](.scratch/science-companion-plan/decisions/15-domain-pack-expert-workbench.md)、[领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)

**建议测试接缝：** 维护者、独立复核者和发行者分别完成同一包版本的编辑、语义 Diff、夹具、签名和灰度，职责冲突被拒绝。

- [ ] 内容签名、独立验证签名和平台发行签名绑定同一规范化摘要。
- [ ] 同一自然人不能对同一版本同时充当维护者和独立复核者。
- [ ] 语义 Diff 显示规则、措辞、人工门、来源和夹具的判定变化。
- [ ] 灰度只生成可发行候选，不能自动激活。

## T047 — 完成领域包失效、撤销、重验证与受信回滚

**What to build:** 让领域包或依赖失效后建立影响集、阻断新运行、重验证下游，并只回滚到仍受信且兼容的版本。

**Blocked by:** T017 — 传播来源失效并定位下游影响；T041 — 交付数学与形式证明领域包；T042 — 交付物理与化学实验测量领域包；T043 — 交付生命科学与医学高风险领域包；T044 — 交付地球、气候与天文学领域包；T045 — 交付计算机科学、标准与数据集领域包；T046 — 完成专家工作台、三签与灰度发行

**关联需求：** REQ-SCI-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P3-C14、P5-C22

**关键合同：** PackInvalidationEvent、ImpactSet、RevocationEvent、RevalidationReport、受信回滚合同

**必读资料：** [专家工作台决策](.scratch/science-companion-plan/decisions/15-domain-pack-expert-workbench.md)、[领域包治理研究](.scratch/science-companion-plan/research/12-initial-domain-pack-governance.md)

**建议测试接缝：** 撤销一个已被多项目使用的包版本，验证新运行闭锁、影响带完整、重验证推进且回滚不能复活不受信版本。

- [ ] 失效事件按检测、分诊、控制、定位影响、修复、重验证和关闭推进。
- [ ] 安全管理员可紧急撤销，但不能编辑规则或直接发布替代版本。
- [ ] 影响集覆盖包、运行、Claim、Evidence、Wording、产物、项目和用户动作。
- [ ] 回滚不能复活已撤销、签名无效或不兼容版本。

## T048 — 建立版本化评测套件、案例、运行锁和结果包

**What to build:** 让评测负责人登记数据卡、案例、切片、裁判、种子、运行锁和结果包，并可通过生产主接缝执行。

**Blocked by:** T012 — 从项目任务生成首个可重放评测运行

**关联需求：** REQ-EVAL-01、REQ-SCI-01、REQ-SAFE-01

**规划书章节：** P5-C20

**关键合同：** CONTRACT-EVAL-01、EvaluationSuiteVersion、EvaluationCaseVersion、EvaluationRunLock、EvaluationResultBundle

**必读资料：** [持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)

**建议测试接缝：** 评测负责人登记一套带数据卡和隐藏切片的套件，通过生产主接缝执行并复现签名结果包。

- [ ] 套件和案例记录用户、项目、领域、授权、预期 Claim、合法状态和预算。
- [ ] 运行锁冻结代码/环境标识、数据、模型、提示、Schema、工作流、工具和裁判。
- [ ] 结果包保存逐案例结果、失败分类、统计、资源和签名。
- [ ] 开发、固定回归、隐藏、红队、纵向和事故数据相互隔离并检查污染。

## T049 — 完成科学、教学、画像与表达基线和消融

**What to build:** 让团队用相同运行锁比较裸 Qwen、普通 RAG、固定课程、无画像、无事实锁和完整系统，并报告关键用户切片。

**Blocked by:** T024 — 完成间隔复习与交错练习调度；T029 — 完成表达版本比较与发布门；T048 — 建立版本化评测套件、案例、运行锁和结果包

**关联需求：** REQ-EVAL-01、REQ-SCI-01、REQ-SCI-02、REQ-PRO-01、REQ-EXP-01

**规划书章节：** P5-C20

**关键合同：** BaselineRunPair、AblationDefinition、EvaluationResultBundle、统计分析计划

**必读资料：** [持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)、[画像与记忆研究](.scratch/science-companion-plan/research/04-profile-memory-governance.md)、[有人味表达研究](.scratch/science-companion-plan/research/05-human-scientific-expression.md)

**建议测试接缝：** 对同一隐藏案例集运行完整系统、裸 Qwen、普通 RAG、固定课程、无画像和无事实锁配置，比较配对结果与受损切片。

- [ ] 科学准确、引用、校准、学习增益、画像质量和中文表达都有主要指标。
- [ ] 基线与候选采用配对输入和相同非目标变量。
- [ ] 消融分别移除画像切片、证据验证、事实锁和人味诊断。
- [ ] 报告效应量、置信区间、失败分布和受损切片，不只报告平均分。

## T050 — 完成多模态、编排与领域包基线和消融

**What to build:** 让团队量化结构化分镜、沙箱、质量门、人工门、类型化编排和领域包相对简化方案的贡献。

**Blocked by:** T035 — 完成跨媒体一致性与多模态发布门；T047 — 完成领域包失效、撤销、重验证与受信回滚；T048 — 建立版本化评测套件、案例、运行锁和结果包

**关联需求：** REQ-EVAL-01、REQ-MM-01、REQ-SAFE-01

**规划书章节：** P3-C13、P3-C14、P5-C20

**关键合同：** AblationDefinition、WorkflowRunLock、DomainPackLock、MediaValidationResult

**必读资料：** [持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)、[多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[智能体编排决策](.scratch/science-companion-plan/decisions/08-agent-orchestration-contracts.md)

**建议测试接缝：** 对同一多模态与工作流案例分别移除分镜、沙箱、质量门、人工门和领域包，验证质量差异与硬失败。

- [ ] 多模态评测覆盖科学一致性、可编辑性、安全、许可和无障碍。
- [ ] 编排评测覆盖非法状态、重试越界、重复副作用、取消、补偿和人工门。
- [ ] 消融分别移除分镜、沙箱、质量门、人工门和领域包。
- [ ] 结果标明哪些简化会触发安全或科学硬失败。

## T051 — 完成全产品安全与隐私红队

**What to build:** 对成熟产品主接缝执行跨账户、提示注入、恶意文档、SSRF、邀请、重放、离线撤权、删除复活和沙箱逃逸红队。

**Blocked by:** T035 — 完成跨媒体一致性与多模态发布门；T039 — 完成因果同步、冲突分支、撤权和删除墓碑；T047 — 完成领域包失效、撤销、重验证与受信回滚

**关联需求：** REQ-SAFE-01、REQ-ID-01、REQ-MM-01

**规划书章节：** P4-C18、P5-C20、P5-C22

**关键合同：** ThreatCase、SecurityEvent、ScopeEnvelope、Tombstone、SandboxPolicy

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)

**建议测试接缝：** 通过认证项目任务生命周期执行规定攻击集，验证每个攻击的拒绝、用户可见状态、审计和恢复结果。

- [ ] 所有规定威胁都有可复现攻击步骤、预期拒绝和审计证据。
- [ ] 跨账户泄漏、越权工具、删除复活、秘密泄漏和沙箱逃逸实例为零。
- [ ] 权限和数据异常不能从缓存、索引、备份或降级模型旁路。
- [ ] 每个发现和修复都进入长期固定回归集。

## T052 — 完成全产品隔离、观测、SLO 与告警验证

**What to build:** 在全部主要产品通道已经存在后，证明认证、项目、保险库、摄入、索引、教学、表达、多模态、协作、领域包、评测和后台任务都遵守同一隔离与观测合同，并完成各工作负载的 SLO 和告警演练。

**Blocked by:** T051 — 完成全产品安全与隐私红队；T024 — 完成间隔复习与交错练习调度；T037 — 完成机构管理域与管理员边界；T047 — 完成领域包失效、撤销、重验证与受信回滚；T048 — 建立版本化评测套件、案例、运行锁和结果包；T010 — 建立运行观测与 SLO 基础设施

**关联需求：** REQ-ID-01、REQ-ID-02、REQ-SAFE-01、REQ-EVAL-01、REQ-OPS-01

**规划书章节：** P3-C15、P4-C16、P4-C17、P4-C18、P5-C21

**关键合同：** ScopeEnvelope、全产品隔离矩阵、RunSummary、SLI/SLO 注册表、AlertOwnership、演练证据包

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[协作与同步边界](.scratch/science-companion-plan/decisions/13-collaboration-organization-sync-boundaries.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 两个用户、两个项目和两个机构依次执行摄入、检索、学习、表达、媒体、协作、领域包和评测任务，验证所有数据通道零越权，并从统一追踪重建成功、重试、降级和闭锁过程，随后演练具名告警。

- [ ] 数据库、API、保险库、缓存、索引、对象存储、队列、后台任务和观测系统通过完整跨账户与跨项目隔离矩阵。
- [ ] 交互、摄入、检索、教学、表达、媒体、评测和治理工作负载均登记并实测 SLI/SLO。
- [ ] trace、metric、log、审计和告警能重建主要成功与失败路径，且不包含私人正文、密钥或越权对象信息。
- [ ] 每个关键告警完成触发、路由、处置、恢复和关闭演练，并绑定责任人、运行手册与证据。

## T053 — 完成全产品响应式与无障碍实测

**What to build:** 让真实用户通过移动端、键盘、屏幕阅读器、文本缩放和减少动画完成注册、项目任务、学习、表达、画像治理、多模态创作、协作与治理工作台的关键路径，并关闭所有阻塞性无障碍缺陷。

**Blocked by:** T024 — 完成间隔复习与交错练习调度；T029 — 完成表达版本比较与发布门；T035 — 完成跨媒体一致性与多模态发布门；T037 — 完成机构管理域与管理员边界；T047 — 完成领域包失效、撤销、重验证与受信回滚；T002 — 建立项目主壳、设计系统、响应式与无障碍基线

**关联需求：** REQ-SCI-02、REQ-MM-01、REQ-ID-01、REQ-SAFE-01、REQ-SCOPE-01

**规划书章节：** P2-C04、P2-C07、P2-C08、P2-C09、P2-C10、P5-C21

**关键合同：** 全产品关键任务矩阵、AccessibilityConformanceReport、响应式视口合同、辅助技术实测记录

**必读资料：** [前端信息架构](.scratch/science-companion-plan/decisions/09-frontend-information-architecture.md)、[多模态科学研究](.scratch/science-companion-plan/research/06-multimodal-science-studio.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 在手机与桌面视口中，键盘和屏幕阅读器用户以 200% 文本缩放及减少动画设置完成六条黄金路径，验证功能、状态、证据、错误和恢复结果与默认视觉路径等价。

- [ ] 注册登录、项目任务、学习、表达、画像、媒体、协作和治理关键路径均具有自动化可访问性与响应式回归。
- [ ] 键盘、主流屏幕阅读器、200% 文本缩放、移动视口和减少动画完成规定任务且无功能损失。
- [ ] 图表、公式、时间媒体、交互、实时状态和错误恢复具有等价文本、数据或顺序操作路径。
- [ ] 独立用户实测关闭全部阻塞性问题，并把发现加入长期回归与发行证据。

## T054 — 完成容量、背压、长稳与单位任务成本验证

**What to build:** 让完整系统在代表性科学任务负载下给出吞吐、尾延迟、队列、资源、成本、背压和降级边界。

**Blocked by:** T035 — 完成跨媒体一致性与多模态发布门；T049 — 完成科学、教学、画像与表达基线和消融；T050 — 完成多模态、编排与领域包基线和消融；T052 — 完成全产品隔离、观测、SLO 与告警验证

**关联需求：** REQ-EVAL-01、REQ-OPS-01

**规划书章节：** P4-C19、P5-C20、P5-C21

**关键合同：** WorkloadProfile、CapacityResult、BackpressurePolicy、CostResult

**必读资料：** [成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)

**建议测试接缝：** 在代表性交互、摄入、媒体、评测和治理负载下运行主生命周期，观察尾延迟、背压、成本与合法降级。

- [ ] 分别测量交互、摄入、检索、表达、多模态、评测和治理工作负载。
- [ ] 报告 P50/P95/P99、吞吐、等待、Token、CPU/GPU、内存、存储、失败与单位成功任务成本。
- [ ] 背压优先保护认证、授权、交互和安全治理任务。
- [ ] 成本优化不能关闭证据、权限、质量或审计门。

## T055 — 完成故障注入、备份恢复与状态重放

**What to build:** 让 PostgreSQL、持久工作流、Redis、对象存储、Qwen、沙箱和网络故障进入合法降级，并能恢复授权、墓碑、任务和产物状态。

**Blocked by:** T039 — 完成因果同步、冲突分支、撤权和删除墓碑；T047 — 完成领域包失效、撤销、重验证与受信回滚；T010 — 建立运行观测与 SLO 基础设施

**关联需求：** REQ-OPS-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P4-C17、P4-C19、P5-C21

**关键合同：** BackupManifest、RecoveryRun、WorkflowReplay、FailureInjection、RecoveryPoint

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 在用户任务执行和离线撤权期间注入基础设施故障，恢复后验证无重复副作用、墓碑优先和合法任务终态。

- [ ] 每类故障有可复现注入条件、预期状态、告警和恢复步骤。
- [ ] 恢复后先重放撤权、密钥时期和删除墓碑，再开放私人访问。
- [ ] 工作流恢复不重复外部副作用，并能继续、取消或补偿。
- [ ] 备份恢复、对象缺失、索引重建和包失效均形成实测证据。

## T056 — 完成供应链、SBOM、签名与秘密治理

**What to build:** 让 Python、Node、系统依赖、镜像、模型、领域包和评测资产均有版本、摘要、许可、漏洞裁决和签名。

**Blocked by:** T008 — 建立四种生产运行合同骨架；T046 — 完成专家工作台、三签与灰度发行

**关联需求：** REQ-OPS-01、REQ-SAFE-01、REQ-EVAL-01

**规划书章节：** P4-C18、P4-C19、P5-C21

**关键合同：** SBOM、ArtifactDigest、SignatureRecord、VulnerabilityDecision、SecretReference

**必读资料：** [系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 构建候选产物并故意加入未知来源或高危依赖，验证摘要、SBOM、签名、秘密扫描和候选闭锁。

- [ ] 发布候选生成完整 SBOM、许可证清单和漏洞裁决记录。
- [ ] 构建产物和镜像固定 digest 并验证签名。
- [ ] 生产进程使用非 root、最小能力、只读基础和受控秘密引用。
- [ ] 高危漏洞、未知来源或签名异常会闭锁候选并触发影响分析。

## T057 — 证明四种生产部署完全等价

**What to build:** 在全新环境中证明手动分进程、统一 CLI、Docker 和 Podman 对同一产品生命周期具有相同配置、迁移、健康、数据和恢复语义。

**Blocked by:** T052 — 完成全产品隔离、观测、SLO 与告警验证；T055 — 完成故障注入、备份恢复与状态重放；T056 — 完成供应链、SBOM、签名与秘密治理

**关联需求：** REQ-DEV-01、REQ-OPS-01、REQ-EVAL-01

**规划书章节：** P4-C19、P5-C21

**关键合同：** CONTRACT-RUN-01、DeploymentRunLock、迁移/健康/备份/恢复等价合同

**必读资料：** [Qwen 与技术栈研究](.scratch/science-companion-plan/research/07-qwen-and-technology-stack.md)、[系统架构决策](.scratch/science-companion-plan/decisions/11-system-architecture-and-governance.md)、[成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)

**建议测试接缝：** 在四个全新环境中执行完全相同的认证项目任务、故障与恢复脚本，比较状态、数据、审计和复现结果。

- [ ] 四种路径都验证安装摘要、doctor、迁移、启动和优雅停止。
- [ ] 四种路径都通过注册、登录、项目、任务、队列、对象存储和隔离冒烟。
- [ ] 四种路径都完成备份、恢复、升级、回滚或前滚修复。
- [ ] 四种路径导出相同格式的健康、日志、追踪、审计和复现信息。

## T058 — 汇集 G0—G5 证据并形成成熟发行基线

**What to build:** 让产品、科学、教学、安全、数据、无障碍、工程和运营责任人基于锁定候选完成 G0—G5 裁决，形成首个成熟发行资格。

**Blocked by:** T049 — 完成科学、教学、画像与表达基线和消融；T050 — 完成多模态、编排与领域包基线和消融；T051 — 完成全产品安全与隐私红队；T053 — 完成全产品响应式与无障碍实测；T054 — 完成容量、背压、长稳与单位任务成本验证；T057 — 证明四种生产部署完全等价

**关联需求：** REQ-SCOPE-01、REQ-SCOPE-02、REQ-EVAL-01、REQ-SAFE-01、REQ-OPS-01

**规划书章节：** P5-C20、P5-C21、P5-C22

**关键合同：** MatureReleaseBaseline、ReleaseDecision、G0—G5 证据门、风险关闭合同

**必读资料：** [成熟开发路线](.scratch/science-companion-plan/decisions/14-development-roadmap-and-stage-acceptance.md)、[持续评测研究](.scratch/science-companion-plan/research/10-continuous-evaluation.md)

**建议测试接缝：** 对锁定发行候选重放六条黄金路径、硬门、故障恢复和四部署等价证据，只有所有独立责任人合法签字后形成成熟发行基线。

- [ ] G0 证明代码、依赖、数据、模型、领域包、迁移、镜像和文档完整可复现。
- [ ] G1 与 G2 证明确定性不变量、权限、引用、删除、安全和科学硬门全部通过。
- [ ] G3 与 G4 证明目标能力改进、关键切片无不可接受伤害，并通过独立专家、用户和无障碍实测。
- [ ] G5 证明容量、成本、降级、备份、恢复、监控、回滚和事故响应已演练。
- [ ] 发行范围扫描确认不包含作品演示、比赛答辩、宣传视频、路演话术、营销活动或评分展示脚本。
- [ ] 无未关闭关键安全、科学、数据完整性或恢复风险时，才生成成熟发行基线。
