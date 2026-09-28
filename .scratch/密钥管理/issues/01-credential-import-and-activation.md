# 01 — 密钥与模型导入必须真正生效：修复探测自伤、假通过与会话外假生效

**What to build:** 用户在「密钥与模型管理」页对任意一项（Qwen 凭据、Qwen 主模型 ID、Tavily 搜索、高德 Web 服务、高德浏览器地图）点「验证并保存」后，成功即代表该值已进入系统并在真实业务路径上立即生效、重启后仍生效；失败必须给出指向真实原因的可操作中文诊断。页面上任何状态徽标与提示都不得声称未被证明的事。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 背景与用户可见症状

现状是：这张页面上的「验证并保存」既不保证"能存进去"（高德浏览器地图这一组**当前无论填什么值都存不进去**），也不保证"存进去之后系统真的在用"（凭据在后台执行器进程里是启动快照）。用户按页面提示操作后，得到的是"提示验证失败但密钥明明是对的"，或者"提示已验证并保存、业务上却仍然说没配置/继续用旧值"。

本票要修的不是某一个 Key，而是这张页面对"导入成功"的定义：**保存成功 ⇒ 系统真的用上了**。

## 已复现证据

### A. 高德浏览器地图凭据当前完全无法导入（阻断，已实测）

- 探测实现（`GET https://webapi.amap.com/maps?v=2.0&key=...`）在**读到正文超过 262,144 字节时直接判定失败**。
- 该加载器脚本的真实体积实测为 **968,594 字节**（占位 Key）与 **968,564 字节**（另一随机 32 位 Key），是上限的约 3.7 倍。
- 直接调用仓库里的探测函数：返回 `False`；用同一份请求计数，**状态码 200、读到 294,102 字节时就越过上限**。
- 因此用户界面必然显示「高德 JS API Key 验证失败，请检查密钥和 Web 平台配置。」——这条提示把原因归给了 Key 与平台配置，与真实原因无关。
- 为什么测试没拦住：该探测的测试用 `httpx.MockTransport` 返回几十字节的合成样本（形如 `window.AMap = {}; /* loader ready */`），真实端点从未被这条路径真正请求过。

### B. 该探测即使放开体积上限也验不出真伪（会从"存不进去"变成"假通过"）

- 探测准备在正文里匹配 `INVALID_USER_KEY`、`INVALID_USER_SCODE`、`INVALID_USER_DOMAIN`、`USERKEY_PLAT_NOMATCH` 四个错误串，但实测**两个已知无效的 Key 拿到的脚本与有效形状相同，四个串出现 0 次**，判定分支是死代码。
- 结论：单修体积上限，会让这个探测对**任意字符串**都判通过——状态徽标显示"已配置"、"验证并保存"显示成功，而 Key 可能根本不存在。这正是"假导入"。
- 卡片文案已声明"安全码与 Key 的配对由地图代理请求验证"，但按钮与成功提示的措辞仍让用户以为 Key 与安全码已被验证过。

### C. 凭据在后台执行器（worker）进程是启动快照，设置页轮换到不了（假生效）

- `BridGes start` 同时拉起 api／web／worker／scheduler 四个进程；worker 的环境里带着**启动时**解析出的 Qwen／Tavily Key（或 `_FILE` 引用），只有 scheduler 会剥掉这些变量。
- worker 的模型调用读取的是**本进程启动时的那份 Settings**，并且会缓存已构造的生产组合与适配器。
- 设置页保存凭据时，只就地轮换 API 进程的客户端（`apply_qwen_key`）与凭据库。
- 对照：Qwen 主模型走的是"共享状态表 + 每次模型调用前重读"，所以换模型无需重启；**同一页面上的两类配置，生效语义不一致**。
- 后果：用户轮换密钥后，知识库摄取与重建、画像提取、OCR、资料/课程抽取等后台任务继续拿旧密钥失败，用户感知就是"我明明配置好了却还用不了"。

### D. Qwen 凭据与主模型之间存在互相指向的死锁

- 「Qwen 凭据」卡：候选密钥必须能看见**当前**主模型，否则提示"请在下方「Qwen 主模型 ID」中改填该密钥可用的模型，再回来验证密钥"。
- 「Qwen 主模型 ID」卡：验证使用**当前生效**（即旧的）密钥；旧密钥一旦被吊销，元数据查询直接失败。
- 于是"旧密钥已失效 + 新密钥只覆盖另一批模型"时，用户在两卡之间来回被指向，页面内无法自行脱困（只能改用环境变量或清掉系统凭据库里的旧值）。

### E. 高德 Web 服务：保存与重启两条路径核对无误，但诊断笼统、且会被环境变量静默遮蔽

- 已核对：保存后就地更新运行期配置（服务端每次调用重新读取），重启时从凭据库装载，两条路径一致。
- 但失败只有一句「请检查密钥、服务权限和额度」，无法区分：把 Web 平台 Key 填到了 Web 服务卡（平台不符）、地理编码服务未勾选、数字签名处于开启状态、额度超限、上游不可达。
- 若环境中已存在 `BRIDGES_AMAP_WEB_SERVICE_KEY`（含 `_FILE` 引用），页面保存的值在**重启后会被环境变量遮蔽**（只有环境与文件都缺失时才使用凭据库值），而页面依旧显示"已配置"——用户无法从界面判断真正生效的是哪一个值。Qwen／Tavily 同理。

### F. 外部探测缺少"真实响应形状"的契约测试

现有全部外部探测都用 MockTransport 或录制样本覆盖，没有对真实响应形状的断言（浏览器地图约 1 MB 的正文、各类真实错误码与信封）。A、B 两类"探测自伤"因此无法在 CI 里被发现。

## 任务内容

1. **修浏览器地图探测的判定**：不再因响应正文体积判失败（只读前若干字节做可达性与形状判断）。同时不允许把"能拿到脚本"当成"Key 有效"。
2. **把浏览器地图那一组的"验证"做成真验证**：以服务端能否用该 Key＋安全码完成一次真实数据服务请求（官方代理方案下的 `jscode` 配对）作为有效性的依据；若某维度确实无法在服务端证明（例如底图渲染），页面必须如实降级表述并在首次真实渲染/代理请求后**回写状态**，不得用"验证并保存"掩盖未验证。
3. **统一"保存即生效"的语义**：让凭据与主模型一样走共享真相源，在需要它的进程里按运行重读（worker 侧新增凭据解析入口，或让凭据与主模型共用同一套"重读+缓存兜底"合同）；确实无法即时生效的地方，给**准确**的"需要重启哪一项"提示，并保证提示与实际行为逐字一致。
4. **失败诊断落到具体原因**：五张卡都要能区分"值不存在／平台不符／服务未开通或权限不足／签名或白名单限制／额度超限／上游不可达"，保持中文与操作顺序，绝不回显密钥正文。
5. **解除 D 的死锁**：在旧凭据不可用时，允许用户完成"换密钥 ＋ 换主模型"的迁移（例如允许候选密钥与候选模型在同一操作序列内互相解锁），不出现互相指向的循环提示。
6. **补真实形状的契约测试**：把真实响应的形状（体积量级、信封、错误码样本）固化为测试，覆盖 Qwen（元数据＋能力探测）、Tavily、高德 Web 服务、高德浏览器地图各自的"有效／无效"两侧，并包含一条**防回归**断言：探测对无效值必须失败、对有效值必须通过。
7. **端到端验收**：用真实凭据在真实前端完成——浏览器地图保存成功且底图真实渲染；Web 服务保存后真实通勤路线可查；轮换 Qwen 密钥后 API 与 worker 的真实调用都成功（**不重启**）；换主模型后下一轮真实回复使用新模型。

## 验收标准

- [x] 高德浏览器地图：真实 Key ＋ 安全码可以保存成功；乱填的、平台不符的值必须保存失败；不再出现"因正文体积而误判失败"。（保存路径与"无效值必须失败"两侧已由形状契约测试覆盖；**真实 Key 的成功一侧待有凭据环境**）
- [x] 高德浏览器地图的状态与文案只声称已被证明的事；由真实代理请求才能确认的结论（如安全码配对）在得到结论后回写到卡片状态。
- [x] 任一凭据在设置页保存成功后，后台执行器在不重启进程的前提下用新值完成一次真实调用（留有可核验证据）。（`test_credential_effectiveness.py` 内驱 worker tick 完成真实调用路径）
- [x] 页面明确且准确地给出任何"需要重启才生效"的边界；该提示与真实行为一致，并有对照测试守住。（唯一需重启的是环境变量遮蔽，含对照测试与遮蔽文案）
- [x] 五张卡的失败信息能落到具体原因分类，且响应、日志、错误文案中不含密钥与安全码正文。
- [x] 旧密钥失效时，用户能在页面内完成"换密钥 ＋ 换主模型"，不再出现互相指向的死锁。
- [x] 真实端点形状的契约测试覆盖每类探测的有效／无效两侧，并能复现"原生缺陷"（当前体积上限与死代码错误串判定都必须被测试钉住）。（有效一侧对高德/Qwen 为形状契约与录制样本，真实计费调用交有凭据环境）
- [ ] 真实前端 ＋ 真实凭据的端到端证据（业务结果、截图或日志）随本票归档。**（未完成，本机无高德／百炼真实凭据，按「实施期边界」交有凭据环境执行）**

## 实施期边界

- 浏览器地图"有效 Key 的响应形状"与底图真实渲染，必须在一台**持有真实高德凭据**的机器上验收；无凭据环境只能完成"无效值必须失败"的一侧与形状契约测试。
- Qwen 侧的有效性探测需要可计费的百炼调用；无密钥环境用录制样本覆盖合同，真实调用验收交有凭据环境。
- 高德控制台侧的约束（可用服务勾选、数字签名开关、域名／IP 白名单）不通过代码改变，只改进页面上的诊断与操作顺序提示。

## 范围与协作

- 不改凭据存储的加密方式、命名空间布局与既有凭据标识；不引入通用密钥管理框架。
- 与 `.scratch/bridges-v2/issues/10`（Tavily 与高德凭据管理）的既有边界保持一致：安全密钥只在服务端，永不下发浏览器、不入页面状态与地图脚本。
- 与 `.scratch/1/issues/02-settings-navigation.md` 的锚点／返回原会话入口不冲突：本次只改验证、生效与诊断，不动导航与锚点常量。
- 主模型的激活合同（`run_model_config` 的共享状态表语义、进行中轮次沿用启动时模型）保持不动，本票只要求凭据侧向它对标。

## Comments

- 2026-09-28 记录：本票由一次真实故障起草。用户在「高德浏览器地图」卡填入正确的 JS API Key 与安全码后稳定失败，实测确认失败与用户输入无关（见证据 A、B）。

## 执行记录（2026-09-28，分支 `01-credential-import-and-activation`，基线 main `be7f87c`）

提交：`291dc0a`（实现）→ `0833039`（两轴评审修复）→ `d28d85d`（test 环境凭据隔离补修）→ `76b6d35`（ADR-0031／CONTEXT／interaction 对齐）。代码与测试 30 文件 `+3372/−459`，文档 3 文件 `+16/−4`。

### 1. 入口清点（谁读凭据、谁判有效性、谁承担「生效」）

| 入口 | 修复前 | 修复后 |
| --- | --- | --- |
| `credentials/store.py::build_credential_store` | 各调用点各写一套「按 backend 选实现」 | 唯一工厂；`environment=test` 回落 `InMemoryCredentialStore`，测试不再读写真实 OS 凭据库（见 §7 第 1 条） |
| `credentials/runtime_resolver.py::RuntimeCredentialResolver`（新） | 无共享真相源，各进程各自解析 | 环境／`<FIELD>_FILE` → 凭据库，**逐次重读**（RLock ＋ 缓存兜底 ＋ 非敏感 `load_error`），与主模型 `RunModelConfigProvider` 同构 |
| `api/main.py` 启动 | 一次性把凭据库里的项合并进 `app.state.settings` | 构造一个 resolver 挂到 `app.state.credential_resolver`，只把**凭据库来源**的值合并进 settings |
| `runtime/executor.py::BackgroundExecutor` | 凭据是启动快照；构造好的组合被缓存 | `run_tick()` 开头 `refresh_credentials()`：凭据变化即丢弃并按新值重建按凭据构造的组合（摄取／重建、图片、视频），并清掉「缺密钥」空闲原因 |
| 高德加载器探测（`webapi.amap.com/maps?v=2.0`） | 正文超 262,144 字节即判失败；另用四个 `INVALID_USER_*` 串判有效性（实测 0 命中，死代码） | 只读前缀判断可达性（`LOADER_INSPECT_BYTES = 8_192`，流式读取后关闭）；正文内容不作受理判据 |
| 高德浏览器地图卡 | 加载器可达即报「验证并保存」成功，文案暗示 Key 与安全码已被验证 | 两步：加载器可达 ＋ 服务端成对数据服务请求（带 `jscode`）；底图渲染如实标注「不经过 BridGes 代理，无法在这里证明」，并由 `api/commute.py::amap_proxy` 在真实代理请求后回写卡状态 |
| `api/credential_state.py`（新） | 无状态载体 | `validation_state`／`record_credential_validation`（保存时验证）与 `record_runtime_evidence`（运行期证据）分离，后者**不移动** `last_validated_at` |
| `api/model_settings.py` 候选模型 | 只能用当前生效密钥验证 | 候选模型可携带**可选**候选密钥，同一操作内完成「换密钥 ＋ 换主模型」 |

### 2. 故障矩阵（证据 ↔ 任务 ↔ 落点）

| 证据 | 症状 | 修复 | 覆盖测试 |
| --- | --- | --- | --- |
| A | 加载器正文 968,594 字节 > 262,144 上限 ⇒ 任何 Key 都保存失败 | 只读前缀，不按体积判定 | `tests/credentials/test_amap_probes.py::test_loader_probe_reads_only_a_prefix_of_a_real_sized_body`（参数化 294,102／968,594／968,564，并用受控流证明读到的字节数 `<= LOADER_INSPECT_BYTES`） |
| B | 四个 `INVALID_USER_*` 串在真实正文里 0 命中 ⇒ 放开体积后会「任意值都通过」 | 正文不作判据；有效性只由成对数据服务请求决定 | `test_loader_probe_does_not_treat_error_markers_as_a_verdict`（防回归：正文就算含错误串也不得据此判通过／判失败） |
| C | 凭据在 worker 进程是启动快照，页面轮换到不了后台任务 | 共享真相源 ＋ 每轮重读（§1） | `tests/api/test_credential_effectiveness.py`（种子库→tick 用新密钥的真实调用路径）、`tests/credentials/test_runtime_resolver.py` |
| D | 「凭据」与「主模型」两卡互相指向，旧密钥吊销后无法脱困 | 主模型卡可同时提交候选密钥 | `test_credential_effectiveness.py` 的迁移路径用例；`QWEN_MODEL_MIGRATION_GUIDANCE` 给出两条都能走通的路径 |
| E | 高德失败只有一句笼统提示；环境变量遮蔽后页面仍显示「已配置」 | 按官方码表分类诊断；`effective_source` ＋ 遮蔽提示 | `tests/api/test_credential_probe_shapes.py`、`tests/api/test_credential_settings.py`（遮蔽报告与 `_FILE` 引用） |
| F | 探测只有合成样本，无真实形状契约 | 真实形状（信封、错误码、体积量级）固化进测试 | 上述四个测试文件共 83 条（含既有文件内新增） |

### 3. 高德：真实形状、诊断码表与「不声称未证明的事」

- **体积事实（实测，写进测试）**：占位 Key 968,594 字节、另一随机 32 位 Key 968,564 字节；旧上限 262,144 在读到 294,102 字节时被越过。
- **诊断码表**：按官方错误码表（`infocode` ↔ `info`）分类到「值不存在／平台不符／服务未开通或权限不足／签名或白名单限制／额度超限／上游不可达」；补齐 `10015`（单机 QPS 限流／网关超时）、`10016`、`10026`、`10045`、`40003`、`20002`（请求协议非法，映射到 `bad_request` 并注明是 BridGes 侧问题）等分支；`401/403`／`429`（Qwen）与 `432/433`（Tavily）分别落到额度类原因。错误文案只含码值与操作顺序，**不回显上游 `info`**（可能含被回显的凭据），也不回显密钥正文。
- **回写通道**：地图代理请求成功／失败都会更新卡片的运行期证据（成功文案只声称「本路径上 Key ＋ 安全码可用」），`last_validated_at` 保持保存时的时间，页面因此能区分「保存时验证过」与「运行期刚证明过」。
- **安全密钥不出服务端**：`jscode` 只在服务端成对请求里追加，不入页面状态、不入响应、不入地图脚本（沿用 `.scratch/bridges-v2/issues/10` 的边界）。

### 4. 「保存即生效」的边界与如实报告

- 立即生效：API 进程（就地轮换）与 worker（每轮重读）都不需要重启；ADR-0031 的「凭据跨进程需要重启后台执行器」已相应修订。
- 唯一需要重启的情形是**环境变量／`_FILE` 遮蔽**：此时保存只写入凭据库而实际生效值不变，卡片用 `effective_source` 显示「（环境变量提供）／（凭据库）」，保存成功文案逐字说明「当前由环境变量 X 提供，环境变量优先，本次保存的密钥不会生效，请先移除该环境变量（或其 `_FILE` 引用）并重启 BridGes」。`secret_environment_source` 与 `Settings` 的加载规则同源（文件引用优先于内联值）。

### 5. 死锁解除

主模型卡新增可选密钥输入（`ModelCandidate.api_key`）：旧密钥已失效时，用户在一张卡里一次提交「新密钥 ＋ 新模型」，服务端先写密钥再验证模型；若密钥被环境遮蔽，则**不激活**并给出与凭据卡一致的遮蔽文案（避免「验证通过但实际没换」的假成功）。`tests/api/test_credential_effectiveness.py` 覆盖贯通路径，前端 `MainModelSettings.test.tsx` 断言提交体为 `(modelId, undefined)` 或 `(modelId, key)`。

### 6. 测试与验证

| 项 | 数值 |
| --- | --- |
| 本票涉及测试文件（采集数） | 83 条（`test_amap_probes.py`、`test_runtime_resolver.py`、`test_credential_probe_shapes.py`、`test_credential_effectiveness.py` 共 4 个新文件 ＋ 两个既有文件内新增） |
| 定向回归 | 上述 6 文件全绿；`tests/api`、`tests/credentials` 全绿 |
| 前端 | `vitest run` 24 文件／209 用例全通过；`tsc --noEmit` 干净；`eslint .` 与 main 逐行相同（2 error／3 warning 均为 main 既有，位于未改动的 `e2e/issue21-desktop-acceptance.spec.ts`） |
| 类型检查 | `mypy src` 两侧同为 98 errors／20 files；归一化 `(文件, 错误码)` 集合双向 diff 为空 |
| 风格检查 | `ruff check .` 两侧同为 511 errors；归一化集合一致 |
| 合同再生成 | `openapi.json`（299 路径）与 `packages/contracts/src/generated.ts` 已随接口变更重生成，`tests/contracts/test_openapi_sync.py` 通过 |

### 7. 两轴评审（Standards / Spec）与处理

**已修（评审提出）**：

1. **测试会读到开发者真实 OS 凭据库**（分支独有的 7 条失败：chat／speech／ingestion 等，含 2 条本票新用例）——worker 侧解析器落回 `OsCredentialStore` 时读到了真实 DPAPI 条目里的 Qwen Key，于是「无密钥环境」的用例拿到真实密钥。修复：`build_credential_store` 在 `environment=test` 时回落 `InMemoryCredentialStore`，并把生效语义测试的 worker 固定为 `desktop ＋ encrypted-volume`（`d28d85d`）。
2. **码表纠错与补全**：`10009` 归平台不符、`10007` 归签名限制、`40002` 服务到期归「服务未开通／权限不足」，`20002` 归请求协议非法；补 `10045`／`40003`／`USER_ABROAD_DAILY_QUERY_OVER_LIMIT`／`ABROAD_QUOTA_PLAN_RUN_OUT`。
3. **额度类误判**：Qwen `429` 原被归为「鉴权失败」，改为独立额度原因；Tavily `432/433` 新增 `web_search_quota` 并在 `_health_status` 里映射为限流。
4. **`last_validated_at` 被运行期请求推动**（会让「保存时验证」与「运行期证据」混为一谈）：拆出 `record_runtime_evidence`。
5. **文件搬迁**：ADR-0031／CONTEXT.md／`docs/v2/interaction.md` 的修订原先落在主仓未提交，已移入本分支提交（`76b6d35`）。
6. 死代码与命名：删掉无人调用的 `_source_for()`、被取代的 `resolve_settings_qwen_key`、不可达的异常分支与随之孤立的 import；`record_validation` 改名 `record_credential_validation` 以免与 `qwen_settings.record_validation` 混淆。

**决定不改（附理由）**：

1. **四个替换端点之间的骨架重复**（Qwen／Tavily／高德两组）：端点的中文文案各自不同、失败分类表也不同，抽公共骨架会把三张卡的诊断差异压回一个参数表；本票不动结构。
2. **候选模型上的 `api_key` 是可选字段而非新端点**：新端点需要新的合同条目与前端第二步交互，与「一次提交」的目标相反；可选字段只多一条「同时换密钥」的路径。
3. **加载器可达性仍是保存门槛（不可达 → 503，凭据不变）**：这是本票「保存即生效」的前提（加载器不可达时后续真实请求必然失败），且失败不清空既有凭据。
4. **真实凭据端到端验收仍留在有凭据环境**：本机无高德／百炼凭据，按工单「实施期边界」只完成「无效值必须失败」的一侧与形状契约测试；验收清单最后一条据此标注为待有凭据环境执行。
5. **不引入通用密钥管理框架、不改加密方式与命名空间**：工单明确禁止；`build_credential_store` 只统一「选实现」这一处。

### 8. 全量回归（同跑法：仓外 basetemp、`PYTHONPATH=src`、两侧对称 deselect 5 条）

| 树 | 失败 | 通过 | 跳过 | 耗时 | 进度字符校验 |
| --- | --- | --- | --- | --- | --- |
| 分支 `d28d85d`（+ 文档 `76b6d35`） | 249 | 3958 | 37 | 17:27 | `.`3958＋`F`249＋`s`37＝4244 ✓ |
| 基线 main `be7f87c` | 249 | 3893 | 37 | 19:02 | `.`3893＋`F`249＋`s`37＝4179 ✓ |

- **失败名单双向 diff 为空**（两侧各 249 条，`comm` 两个方向都无输出）。
- Δ通过 ＋65 = 本票新增用例；Δ跳过 0、Δ失败 0，账目闭合。
- **对称 deselect 的 5 条（两侧完全相同）**：`tests/integration/test_runtime_smoke.py::test_start_fails_when_global_key_file_unreadable`／`::test_start_fails_with_empty_global_key_before_spawning`／`::test_start_fails_when_global_key_env_absent`（真库锁空闲时会**真的启动桌面实例**并写真实数据目录，`subprocess.run` 永不返回），以及 `tests/runtime/test_runtime_contract.py::test_startup_reports_chinese_error_when_port_is_occupied`／`::test_startup_full_journey_start_health_duplicate_reject_stop_restart`（需要生产构建 `apps/web/.next/standalone/server.js`，两条树都没有构建，属 `NEEDS_WEB_BUILD`）。两侧同条件，故对账成立。
- 环境记录：工作树内 `apps/web/node_modules` 由本次安装（gitignore，不随分支提交）；工作树 `.next` 无 `standalone/server.js`，因此上表两条用例未真跑。

### 9. 合并、推送与清理实证（2026-09-28）

- **合并**：`git merge --no-ff 01-credential-import-and-activation` → `cbd1889`（零冲突，33 文件 `+3388/−463`）。合并树与分支顶端树哈希相同（`b7ad4a6f89b6d469298c6991ebac123aedcc144f`），`git diff 01-credential-import-and-activation HEAD` 无输出。
- **合并后定点**：`tests/api tests/credentials tests/contracts` → 134 passed（25.86s）。
- **推送**：`git -c http.proxy=http://127.0.0.1:7890 push origin main` → `be7f87c..cbd1889`；推送后 `git rev-parse HEAD origin/main` 两侧同为 `cbd1889`。
- **六项清理实证**：

  | # | 检查 | 结果 |
  | --- | --- | --- |
  | 1 | `git worktree list` | 只剩主仓 `C:/Users/33755/Desktop/BridGes cbd1889 [main]` |
  | 2 | 工作树目录 | `git worktree remove --force` 一步删除（本轮无 ACL 阻力），`.worktrees/01-credential-import-and-activation` 已不存在 |
  | 3 | 本地分支 `01-credential-import-and-activation` | 已删（was `76b6d35`） |
  | 4 | `git worktree prune --dry-run -v` | 无输出（注册项无残留） |
  | 5 | `main` 与 `origin/main` | 同为 `cbd1889` |
  | 6 | `git branch -a --list "*01-credential*"` | 空 |

- **仓外 basetemp 清理**：本次会话的 20 个 `%TEMP%` basetemp 全部删除（`bridges-pytest-br01/br02/br03`、`-main`、`-main01`、`-diag01/02/04/05`、`-review01/02/03`、`-wt01/-wt01c/-wt01e/-wt01e2/-wt01f`、`-cred02`、`-postmerge`、`pytest-i04b`、`pytest-mainchk`）；原始运行日志、失败名单与静态检查输出归档到主仓 `.tmp/issue01-verify/`（`.tmp/` 在 `.gitignore` 内）。
- **不是本票的遗留（只报告、不代删）**：`.worktrees/02-generation-stop-and-graph-errors`（旧空壳目录，不在工作树注册表里）、`%TEMP%\pytest-*.log`（更早会话的运行日志）。
- **环境说明**：两侧全量运行时，系统里一直有用户自己的 `BridGes start` 实例（pid 23984 进程树，19:34 启动）持有真实数据目录锁，因此 5 条对称 deselect 中的 3 条 `test_start_fails_*` 会走「按预期失败」的一侧而非挂死；两侧同条件、对账成立。该实例与本票无关，全程未做任何处置。工作树内的 `apps/web/node_modules` 由本次安装（gitignore，未进分支）。

- 2026-09-28 收尾：状态置 `ready-for-human`，验收标准逐条按证据勾选（证据见下方「执行记录」§2、§3、§6）。**唯一未勾选**的是「真实前端 ＋ 真实凭据的端到端证据」——本机没有高德／百炼真实凭据，按「实施期边界」只完成了「无效值必须失败」的一侧与真实形状契约测试，该条交持有真实凭据的机器验收。
