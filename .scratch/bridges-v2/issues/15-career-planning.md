# 15 — 职业规划

**What to build:** 用户显式选择职业规划后，可依据与目标岗位、城市和阶段相符的公开岗位样本获得有来源边界的建议。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `career` 进入 `career.parse → career.plan → career.collect → career.filter → career.analyze → career.advise`。计划节点展示目标岗位与城市的实际查询；采集节点只读公开职位卡和详情，保留原链接、抓取／发布日期与薪资原文；过滤节点先排除相邻岗位、过期与重复样本，分析节点才归纳技能及可比较薪资。缺岗位意图时持久化澄清，样本不足时停止总体推断。普通聊天只提供明确的一键建议。

- [x] 菜单与 chip 可选择职业模块；提取并显示原始岗位、阶段和城市，岗位含糊时澄清，不把相邻岗位暗换为目标岗位。
- [x] 只把公开可读、职位及城市匹配的岗位纳入主样本；去重并标记抓取时间、发布日期、薪资原文、要求与直达链接。
- [x] 检索计划分别尝试公开招聘职位、企业招聘页和校招页；不可访问字段留空，来源不足时只展示实际取得的岗位。
- [x] 技能和薪资分析标出样本数、日期、地区及计薪单位；不兼容薪资不混算，小样本不称为全国市场均值。
- [x] 无可用岗位或部分来源失效时展示实际查询与证据缺口；公开来源可得性和岗位精确匹配经真实样本验证。

## Comments

### 实现摘要（2026-09-26，分支 `v2/15-career-planning`，worktree `../BridGes-15-career-planning`）

**设计前提（本机真实样本实测，见下节）：** 招聘站点在本机直读的可读性既差又不稳定——
BOSS 直聘的岗位链接一律返回「请稍候」反爬页，应届生求职网给出的是市级列表页，gaoxiaojob
同一链接在相隔数分钟的两次运行里分别 `read` 与 `access_restricted`。因此本模块不承诺
「一定检索到样本」，而是把样本的来源写在明面上：三条来源各发一条带岗位锚点／城市／阶段
的实际查询，**能读到岗位页才进主样本**，读不到就降级为未核实候选链接，并在正文里列出
实际查询词与证据缺口。搜索摘要只当候选说明，绝不当作岗位页正文——这条在收尾实测里救过
一次：搜索投影的结果项根本没有 `content` 字段（`tests/career_plan` 用真实的
`WebSearchProjection` 类型钉住了这个形状）。

**后端**（`tests/career_plan/` 73 例：core 33 / module_flow 13 / sources 27，全绿）：

- `lexicon.py` / `parsing.py`（`career.parse`）：岗位锚点逐字保留用户原话，同义名才归入
  同族（`Java后端`／`后端开发工程师`），相邻岗位（前端、测试、算法…）只用于「单列建议」；
  城市、阶段（`2027 届`／实习／校招）、经验说法与届别分别记录；岗位意图缺失或过于含糊
  （如「有哪些岗位」）时只问一个问题，并把原始请求写回消息作为恢复载荷，下一轮的岗位取自
  回答、原始请求与城市／阶段仍以首次提问为准。
- `planning.py`（`career.plan`）：三条来源查询分别面向公开招聘职位页（`zhipin.com`）、
  企业招聘页与校招页（`yingjiesheng.com`），查询词只用岗位锚点原话 + 城市 + 阶段 + 固定
  字面量；**筛选条件只列真正执行的判定**（岗位名、城市、排除相邻／过期／重复），阶段与
  经验不写进筛选条件（写了不做等于空头承诺），多城市时只按第一个城市检索并在计划里点明
  其余城市本轮未检索。
- `searching.py`（`career.collect` 的检索边界）：经允许的搜索服务发查询，逐条记录查询词、
  状态、取得条数与失败原因（`CareerQueryRecord`）；候选说明取搜索投影的 `snippet` /
  `content_summary`。
- `collecting.py`：只做公开 GET（固定 UA，无 Cookie、登录或签名绕过），单页 ≤1MB、8s 预算，
  分类为 `read／partial／access_restricted／unrecognized／not_found／timeout／error／
  cancelled`；解析优先结构化招聘数据（`JobPosting`），缺失时退到元数据 + 正文结构，**拿不到
  的字段一律留空**（公司名只在页面自己声明雇主时才取，站点名不当雇主）；薪资原文取页面里
  命中薪资写法的那一整行，发布日期保留原文并换算成日期。
- `filtering.py`（`career.filter`）：先剔除不是岗位页、相邻岗位、标题不命中、城市不符、
  城市无法核对、已过期与重复的候选，逐条给出中文依据；主样本只留「公开可读 + 岗位名命中
  + 城市可核对且相符」的岗位。
- `analyzing.py` / `advising.py`（`career.analyze` / `career.advise`）：技能只从页面要求
  原文里取；薪资按计薪单位分别归并（元/月、元/天、元/年），不可比较的写法（面议、缺单位、
  无数字）不混算；口径里写明样本量、发布日期跨度、地区与计薪单位，样本量低于阈值时
  `small_sample` 且 `overall_inference_stopped`，措辞固定为「本轮检索所得，不是全国市场
  均值」；建议区分「证据」与「推断」，相邻岗位单列。
- `presenting.py` / `service.py`：正文与分区全部由真实证据渲染，本模块**不调用模型**
  （流程测试断言 `adapter.calls == 0`）；六个节点顺序执行、停止在节点边界生效；终态
  `success／links_only／empty／error／stopped` 与澄清等待都由真实证据决定，查询状态在正文里
  一律回显中文（不回显英文枚举值）；失败与停止都写回同一条消息（迁移 57 的
  `messages.career_plan`，`SCHEMA_VERSION = 57`，键 53/54/55/56/57 连续），与 Issue 11/13/14
  的 `paper_search`／`learning_resources`／`tieba_research` 同构。
- 父图与 API：`AVAILABLE_MODULE_IDS` 加入 `career`，仍未接入的模块继续明确拒绝，普通聊天
  只给一键建议（不点不检索任何外部来源）；「未接入模块」反例随模块接入再次换人：`career`
  接入后，原先拿它当反例的 **6 处**用例改用仍未接入的 `github`
  （`tests/chat/test_v2_02_resumable_runs.py` 2 处、`tests/commute`、`tests/paper`、
  `tests/resources`、`tests/tieba` 各 1 处，注释与 docstring 同步改掉）；
  当前未接入的只剩 `github`。

**前端**（vitest 21 文件 / 164 例全绿；`tsc --noEmit` 与 `next lint` 干净）：

- `+` 菜单与 chip 新增「职业规划」（新增 `careerPlan` 图标）；父图节点进度补 `career.*`
  六项中文标签。
- `CareerPlanCard`：澄清／检索中／成功／仅链接／空／失败／停止七态在同一条消息内渲染，
  含原始请求与识别的岗位方向、三条来源的实际查询与筛选条件、每次外部调用记录、主样本字段
  （公司、城市、薪资原文、发布日期、抓取时间、要求、直达链接）、统计口径、证据与推断分开的
  建议、被剔除样本的分类与依据、证据边界与重试；查询记录复用已有的共享组件
  `QueryRecordList`，不重复实现。
- 等待恢复 `pendingClarificationModule` 覆盖 `career`：只看最新一条携带模块投影的消息，它带
  等待就恢复该 chip，否则不恢复（`success／links_only／empty／失败／停止` 都算本轮已有结论）。

**code-review 双轴（Standards/Spec）复核与修复：**

- 搜索投影字段用错（真实缺陷，测试未覆盖、实测发现）：原实现读 `item.content`，而搜索投影
  里没有这个字段，真实路径上必然 `AttributeError`；改为 `snippet or content_summary`，并用
  真实的 `WebSearchProjection` 类型补了一例回归测试。
- 薪资原文取的是定长截窗：真实页面上取到 `'师【2027校园招聘】\n18-26K * 16薪\n后端工程师'`
  这种碎片；改为返回命中薪资写法的那一整行（实测同页得 `'18-26K * 16薪'`，另有 `'面议'`）。
- 公司名兜底用了 `og:site_name`（站点名，即招聘网站自己的名字），既误导用户又会让去重键把
  不同公司的岗位判成重复；现在只在结构化数据声明雇主时取公司，元数据兜底不再填公司。
- 筛选条件宣称了未执行的判定（阶段、经验）；现只列真正执行的判定，经验要求改由「证据边界」
  如实说明「本轮没有做经验过滤，样本里的经验要求逐条展示，由你自己核对」。
- 多城市只用了第一个城市却没有任何说明；现在计划里明确写出「其余城市本轮未检索」。
- 澄清等待在失败／停止后仍会恢复更早的澄清，导致用户下一条消息被接上旧请求的城市；现把
  `error／stopped` 与本轮已有结论同等对待（等待恢复语义与前端卡片一致）。
- 正文与停止正文里回显英文枚举值（`success／cancelled`）；统一改走中文状态映射。
- `CareerRejectedSample.kind` 的描述只列了 4 类而实际有 7 类；补齐后重新生成了
  `openapi.json` 与 `packages/contracts/src/generated.ts`。
- 清理死代码：未被引用的 `JobPageReadError`、`default_plan_text`（及其 `build_plan` 导入）。
- 未采纳（记录在案）：`_Run`／`_finalize`／`_pending_wait` 等编排骨架与论文、贴吧模块同形，
  目前是三处并行实现，抽共享实现会同时改动已上线的两个模块，留作后续重构；父图对模块子图的
  派发已是第五份拷贝，同理。

### 真实样本验证（2026-09-26，本机实测）

探针（仓库外 `bt15-probe/live_probe6.py`）**直接调用模块自身的管线**——`parse_career_request`
→ `build_plan` → `WebSearchServiceAdapter` → `HttpJobPageReader` → `filter_candidates` →
`analyze_samples`，不是另写一套逻辑；只发公开查询、只读公开页面，不打印或落盘任何凭据。

**第 1 段：模块自带的检索计划在真实来源上的可得性**

| 请求 | 三条查询 | 真实候选的读取结果 | 主样本 |
| --- | --- | --- | --- |
| Java 后端开发 · 北京 | 三条全 `success`（取得 5／1／4 条） | BOSS 三条岗位链接全是「请稍候 - BOSS直聘」反爬页（`unrecognized`）；快手 joinus 页 `read` 但不是岗位页；应届生 4 条是市级列表页 | **0**，如实给零样本口径并停止总体推断 |
| Java 后端开发 · 南昌 | 三条全 `success`（取得 5／1／2 条） | 猎聘南昌页 `read`（城市 南昌、薪资原文 `7-8k`、原文 `更新时间：2026-09-26`）；gaoxiaojob 页 `access_restricted`；应届生 2 条列表页 | **1**（另 1 条 `city_unverified` 剔除），地区=南昌、小样本、停止总体推断 |
| Java 后端开发 · 北京 · 2027 届 | 三条全 `success`（取得 5／1／5 条；校招查询确实带上 `2027届`） | 同上（BOSS 反爬、快手非岗位页、应届生列表页） | **0**（另 1 条 `title_mismatch`） |

**第 2 段：真实可读页上的岗位精确匹配（阳性与阴性对照，同一批页面换请求）**

| 真实页面 | 读取结果 | 「Java 后端开发 · 北京」的判定 |
| --- | --- | --- |
| 牛客网 `nowcoder.com/jobs/detail/466930` | `read`：城市 北京、薪资原文 `18-26K * 16薪`、发布 `2026年9月10` | **进主样本**；薪资归并为 元/月 18000–26000、中位 22000、样本量 1、`small_sample=True` |
| 高校人才网 `gaoxiaojob.com/hotword/...` | `read`：页面未给城市、薪资原文 `面议` | `city_unverified` 剔除（城市核对不了就不纳入，不猜） |
| 北大武汉人工智能研究院 `whai.pku.edu.cn/info/1158/1288.htm` | `read`：城市 北京、发布 `2024-10-31` | `expired` 剔除（超出新鲜度阈值） |
| 猎聘搜索页 `liepin.com/zhaopin/?key=java` | `unrecognized` | 不进候选（不是岗位页） |

- 同一批页面改成「城市杭州」：牛客页按 `city` 剔除，依据是「页面城市是『北京』，与你要求的
  『杭州』不符」——**不把相邻城市或相邻岗位暗换**成目标。
- 同一批页面改成目标「前端开发 · 北京」：牛客页按 `adjacent` 剔除并单列建议，依据写明
  「命中相邻岗位『后端开发工程师』」，绝不并入目标岗位的统计。
- 可读性波动也被记录：gaoxiaojob 同一链接在两次运行里分别 `read` 与
  `access_restricted`，南昌那一轮因此从 1 个样本变成 0 个样本；模块两次都按实际取得的证据
  如实呈现——这正是验收 5 要的「展示实际查询与证据缺口」。
- 已知局限（记录在案）：岗位页与市级招聘列表页在 HTML 结构上并不总能区分，实测有一轮把
  猎聘的市级列表页当成岗位卡收进主样本（它的标题与正文确实声明了岗位名、城市与薪资原文）。
  模块的处置是照页面自述收录并逐条给出原文依据，样本量口径本身也只用「本轮检索所得」——
  若后续要收紧，方向是给列表页识别加结构特征，而不是猜。

### 全量回归与基线比对（2026-09-26）

跑法两侧完全一致：`pytest tests -q --tb=no -rfE -p no:randomly
--ignore=tests/humanize_eval`，另 `--deselect` 三条本机会挂死的 `test_start_fails_*`，
两侧各自独立仓外 `--basetemp`，**两侧都不设 `PYTHONPATH`**（使子进程导入问题对两边
同等生效）。`--ignore=tests/humanize_eval` 是既有约定：该目录 211 条在整树跑动里会
因 `conftest` 同名模块冲突报 `ImportError`（本票第一轮跑动漏了这条，多出的 4 条
失败全部来自此，已剔除后重跑）。

| 侧 | 提交 | 结果 |
| --- | --- | --- |
| main | `90a06e1`（本票开发期基线） | **273 失败 / 3912 通过 / 40 跳过 / 2 错误** |
| 本分支（合并 main 前） | `a5952a5` | **273 失败 / 3982 通过 / 43 跳过 / 2 错误** |

- 收集数 main 4230 → 分支 4303：**+73 恰为 `tests/career_plan/` 三个文件的用例数**
  （core 33 / module_flow 13 / sources 27，逐文件 `--collect-only` 比对），无其他增减。
- 失败用例名集合双向比对（`-rfE` 名单）：**仅分支独有 2 项、仅 main 独有 2 项**：
  - 仅分支独有：`tests/closeout/test_api_boot.py` 两例 —— worktree 无仓库 `.venv`
    的环境产物（带 `PYTHONPATH=src` 单跑即 2 passed，与 Issue 11/14 工单记录同一产物）。
  - 仅 main 独有：`tests/runtime/test_runtime_contract.py` 两个受端口占用的启动用例
    （`test_startup_full_journey_...`、`test_startup_reports_chinese_error_when_port_is_occupied`）
    —— 这一对就是 `NEEDS_WEB_BUILD` 门控的两例：worktree 没有 `apps/web/.next`
    生产构建产物，因此在分支侧转跳过、在 main 侧真跑并失败。
  - 账目对平：Δ通过 +70 = 新增 73 − api_boot 2（分支侧由通过转失败）− 1（另一条环境
    相关用例在 worktree 侧由通过转跳过）；Δ跳过 +3 = 上述 2 条 `NEEDS_WEB_BUILD`
    ＋ 那 1 条；Δ失败 0 = api_boot 2 转失败 − 上述 2 条转跳过。跳过项的具体名单未逐条
    记录，`-rfE` 只列失败名。
- 静态检查：mypy 两侧均 **115 处 / 23 文件**，本票新增代码 **0 处**；ruff
  `src/bridges/career_plan` 与 `tests/career_plan` **零 finding**（全仓既有 591 处为各模块
  历史遗留，未触碰）。
- 前端：`tsc --noEmit` 干净；vitest 合并前 main 侧 **21 文件 / 169 通过**、合并树
  **22 文件 / 183 通过**（Δ +14 = `CareerPlanCard.test.tsx` 9 例 + chip 1 例 +
  `chat-modules.test.ts` 职业规划 4 例）；`next lint` 无新增 warning（既有三条来自
  `ImageTaskCard.tsx`、另一个图片位置与 `Composer.tsx`）。
- 本机共享桌面库 `%LOCALAPPDATA%\BridGes\data` 未被本次开发与验证触碰（迁移只在临时目录
  实测，只读探针实测现库 v54 / 30 列 / 164 条消息）；以 main 启动只会顺序补跑
  55→58 四条迁移。

### 合并 main 的接缝处理与合并后验证（2026-09-26）

收尾时 main 已从 `90a06e1` 前进到 **`27d3d02`**（Issue 16 GitHub 项目推荐 `689e330`
与 Issue 17 学习页与预览并入成 `a40d202` 之后，又有一条 Issue 16 的工单补记），
分支先 `git merge main` 再验证。12 个文件冲突，
绝大多数是「两侧各加一条模块登记」，按**两者都保留**处理（`api/main.py` 两处、
`chat/repository.py` 六处、`chat/service.py`、`contracts/chat.py`、`MessageList.tsx`
两处、`Icon.tsx`、`lib/api.ts`、`Composer.test.tsx`）；三处判定口径必须改：

- **迁移号让位 57 → 58**：Issue 16 已占 57（`messages.github_projects`），本票的
  `messages.career_plan` 整体改号为 **58**，`SCHEMA_VERSION = 58`。改号后实测三条路径
  （全部在临时目录，未触碰本机共享库）：① 全新库 → v58 且含 `career_plan` 与
  `github_projects`（34 列）；② 造一个 v57 旧库（删该列并把版本戳改回 57）→
  `initialize()` 后补回该列、版本戳升到 58；③ 重复 `initialize()` 幂等（34 列不变）。
  迁移键 1–58 连续无缺号。
- **「未接入模块」反例第三次换人，且反例池这次用尽**：六个日常模块
  （`paper／commute／resources／tieba／career／github`）现已全部接入，`ChatModuleId`
  枚举里已没有「契约合法但子图未接入」的取值——未知取值在请求契约层就被 422 拒掉
  （既有用例 `test_module_id_rejected_before_dispatch_when_unknown` 覆盖此处）。
  六条拒绝用例（chat 2 处、commute／paper／resources／tieba／career 各 1 处）改为把
  `career` 临时从 `AVAILABLE_MODULE_IDS` 里摘掉，复现「契约内但子图尚未接入」的构造，
  验证的仍是 `select_explicit_module` 的同一条拒绝逻辑；前端
  `chatModuleLabel` 的「未接入取值不回显英文 ID」反例同理由 `career` 改为不存在的标识。
  父图那道门保留并改写注释：它守的是「新增模块 ID 先上契约、子图随后接入」的过渡状态。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 不手工合并，按合并后的代码
  重新生成（299 paths / **695** schemas，较 main 的 682 多出本票 13 个 schema）；
  `tests/contracts` 与 `tests/architecture` 合并后复跑 **30 passed**。

**合并树全量回归**（同一命令、同一 deselect 集、同一 `--ignore`；两侧各自仓外
`--basetemp`，均不设 `PYTHONPATH`）：

| 侧 | 提交 | 结果 |
| --- | --- | --- |
| main | `27d3d02` | **273 失败 / 3970 通过 / 40 跳过 / 2 错误** |
| 合并树（分支 + main） | `42e2334` | **273 失败 / 4041 通过 / 42 跳过 / 2 错误** |

- 收集数 main 4288 → 合并树 4361：**+73 仍恰为 `tests/career_plan/` 三个文件**
  （core 33 / module_flow 13 / sources 27），无其他增减。
- 失败用例名集合双向比对（两侧各 275 条失败名，`-rfE` 名单）：**仅合并树独有 2 项、
  仅 main 独有 2 项**，且与合并前那一对完全相同：
  - 仅合并树独有：`tests/closeout/test_api_boot.py` 两例（worktree 无 `.venv` 的环境产物）。
  - 仅 main 独有：`tests/runtime/test_runtime_contract.py` 的两例（`NEEDS_WEB_BUILD`
    门控，worktree 无 `apps/web/.next` → 合并树侧跳过）。
  - 账目完全对平：Δ通过 +71 = 新增 73 − api_boot 2；Δ跳过 +2 = 上述两条
    `NEEDS_WEB_BUILD`；Δ失败 0 = api_boot 2 转失败 − 上述 2 条转跳过。
- 本票新增的 `tests/career_plan` 73 例在合并树上**全部真跑通过**（无 skip、无失败）；
  `tests/chat`、`tests/commute`、`tests/paper`、`tests/resources`、`tests/tieba` 里被改写
  的六条拒绝用例也复跑通过（合并前先单独跑过一遍：6 个目录 **294 passed**）。
- 静态检查：mypy main **115 处 / 23 文件** vs 合并树 **115 处 / 23 文件**（同一命令、
  同一文件集合，本票新增代码 0 处）；ruff 同一组文件 main **592** vs 合并树 **594**，
  差集只有 `src/bridges/api/main.py` 的 E402 从 103 条变 106 条（本票新增的 3 行模块
  装配导入，与该文件既有的「导入写在 `logger = ...` 之后」惯例同款，Issue 14 同期为
  +4），另有 `tests/chat/test_v2_02_resumable_runs.py` 由 25 条降到 24 条（改写拒绝用例
  时合并/缩短了长行）；新增文件 **零 finding**。
- 前端：`tsc --noEmit` 干净；vitest main **21 文件 / 169 通过** vs 合并树
  **22 文件 / 183 通过**（Δ +14 = `CareerPlanCard.test.tsx` 9 例 + `Composer.test.tsx`
  职业规划 chip 1 例 + `chat-modules.test.ts` 职业规划 4 例）；`next lint` 无 error、
  无新增 warning。
- 合并后 main 的 `SCHEMA_VERSION` 为 **58**；本机共享桌面库仍是 v54 且**未被本次合并与
  验证触碰**（只读探针实测 30 列 / 164 条消息），下一次以 main 启动会顺序补跑
  55（资料）、56（贴吧）、57（GitHub）、58（职业规划）四条迁移。

### 跨模块发现（不在本票范围，留给后续）

- `src/bridges/tieba/searching.py:234` 用 `snippet=result.content or ""`，而搜索投影的结果项
  没有 `content` 字段——与本票修掉的那个缺陷同源，真实路径上贴吧搜索摘要会
  `AttributeError`。已并入已合并的 Issue 14 代码，本票不改（越界），建议单独提票。
