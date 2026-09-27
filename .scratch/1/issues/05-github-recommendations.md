# 05 — 修复 GitHub 中文功能匹配并保留限流前的有效推荐

**What to build:** 用户请求“给我推荐几个智能体项目”时，系统能依据真实仓库证据匹配“智能体”，并在额度耗尽时保留已验证的推荐、解释未检查候选与恢复时间。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 问题与证据

匹配器把辅助词作为任意子串删除，“智能体”里的“能”被删后变成“智 体”；即使证据原文含“智能体”，关键词匹配仍为空。该次真实搜索取得 10 个候选，前两个仓库完成读取后被排除，第三个触发 `github_rate_limit`，其余候选未检查。修复限流无法自动解决中文误判，修复匹配也不能消除真实额度限制。

## 任务内容

1. 以原始请求建立从解析、仓库证据到排序和结果呈现的失败用例，证据明确包含“智能体”；断言候选被正确纳入，不能仅测试某个私有字符串函数。
2. 修正辅助词剥离，优先保留并匹配完整中文原词，只在明确词或句法边界处理辅助表达。覆盖包含“能”等单字的合法术语及无关仓库，避免简单删除过滤规则后扩大误召回。
3. 保持既有证据层级：元数据、README 自述、已读取实现文件分别标记；词汇命中不能升级为“已验证架构或实现”。保留功能覆盖、许可未知和维护状态的限制说明。
4. 在现有缓存、候选上限和额度桶机制内优化读取顺序，先取得足以判断相关性的必要证据，再执行额外文件核查。不要重复建设缓存，也不以无限重试、提高调用并发或新增 Token 管理作为默认修复。
5. 构建第三个仓库限流的服务级场景：前两个候选符合条件时仍输出它们；未读取候选明确标注“未完成检查”，不能说它们不匹配。已有元数据不足以形成强结论时沿用降级证据合同。
6. 统一结果状态与用户文案，区分没有相关结果、部分结果且受限、完全无法检索。以本地时间呈现已知恢复时间；没有恢复时间时不编造，用户重试遵守额度退避。
7. 做有界真实调用确认完整请求可产生证据驱动的结果。外部额度不足时保留真实失败证据，不拿模拟仓库伪装线上推荐。

## 验收标准

- [x] 包含“智能体”的真实形态证据通过完整推荐路径，不再因删去“能”而被误拒绝。
- [x] 无关仓库及仅含通用词的证据仍不能被认定为覆盖功能。
- [x] 第三个候选限流时，前两个符合条件的推荐保留，未检查候选与不匹配候选分别说明。
- [x] 限流后不继续外发不必要请求；已缓存响应按现有有效期复用。
- [x] 结果状态、重试能力和恢复时间表达一致；时间按用户本地时区展示。
- [x] API 元数据、README 和实现文件的证据边界不被放宽，许可状态不被猜测。
- [x] 记录原始请求的真实运行结果及额度前提，模拟限流回归和真实调用结论分开记录。

## 范围与协作

本票独立修复 GitHub 模块，不抽取全局中文分词器，不改贴吧算法，不引入新外部提供方。共享组件或模块类型确需调整时协调后单点修改。

## 执行与验收记录

### 1. 中文匹配复现（任务 1、2）

原始请求「给我推荐几个智能体项目」，证据 README 原文「多智能体协作框架：多个智能体分工完成复杂任务。」

| | `_core("智能体")` | `match_features(...)[0]` |
| --- | --- | --- |
| 修复前（main `f77e8e2`） | `'智 体'` | `matched=False`、`matched_terms=[]`——原文写着「智能体」仍判未覆盖 |
| 修复后（分支） | `'智能体'` | `matched=True`、`matched_terms=['智能体']`、`evidence_kind=readme` |

修法（`ranking.py`）：先拿**完整原词**在证据里连续比对（`_whole_phrase_hit`），整词没出现才退回剥辅助词后的骨架逐字匹配；单字辅助词只在词／句法边界逐字剥离（`_strip_edge_aux`，剥到只剩 `_MIN_CORE_CHARS=2` 字即停），术语内部的「能」不再被删。

### 2. 负例（任务 2、3；验收 2、6）

- `test_fragment_or_generic_only_evidence_is_not_counted_as_coverage`：只有「智能」片段或通篇通用词的证据对「智能体」判未覆盖，仓库被剔除。
- `test_feature_made_only_of_generic_words_is_not_coverage_even_verbatim`（评审补）：要点本身只由通用词拼成时（「用户功能」），即使整句出现在 README（「提供用户功能与系统功能」）也不算覆盖——堵住「整词优先」带来的误召回。
- `test_whole_idea_rejects_repositories_without_identity_overlap`：与 idea 无词汇重合的仓库仍被身份闸门剔除。
- `test_auxiliary_words_are_still_stripped_at_word_boundaries`：边界上的辅助词照剥（「能记账」→ 命中「记账」），术语内的保留。

### 3. 候选读取顺序（任务 4；验收 4）

`inspect_candidates` 改为两遍：第一遍只发每个候选的 README（足以判断相关性），第二遍才回头发根目录清单、许可与实现文件。额度中途用尽时前面读到的 README 一律带回。

- 单测：`test_readme_evidence_comes_before_extra_verification_for_every_candidate` 断言调用序列为 `readme(候选1) → readme(候选2) → contents(候选1)`。
- 真实调用（见 §6）12 次外发里，3 次 README 全部在前，之后才是 `contents`／`LICENSE`／实现文件。
- 未新增缓存、未提高并发、未新增重试：沿用既有进程内缓存（900s／96 条）、`INSPECT_LIMIT=3`、`MAX_QUERIES` 与两个额度桶；额度用尽即不再外发（`test_retry_before_the_reset_sends_no_further_requests`）。

### 4. 服务级限流场景（任务 5、6；验收 3、5、6）

- `test_rate_limited_third_candidate_keeps_the_verified_recommendations`：第三个候选撞限时前两个推荐保留并照常给出证据，未读候选的剔除理由逐字包含「未完成检查（这不代表它不匹配）」。
- `test_metadata_only_degradation_when_rate_limited`：只有元数据时按 `metadata_only` 降级，不把降级说成限流。
- `test_metadata_only_without_a_quota_limit_does_not_claim_one`：没撞限就不出现「上游额度」「恢复」字样，也不给恢复时间。
- 文案与时间：`GithubRateLimitState.reset_at` 按**本地时区**呈现（`presenting.rate_limit_recovery_note` →「预计 YYYY-MM-DD HH:MM（本地时间）恢复」；卡片同一句话术），上游没给就不编造；重试承诺只说一遍。
- 评审补：恢复时间改取**真正撞限的桶**（`GithubApiClient.reset_at_for(BUCKET_CORE)`）——检索 10/分与核心 60/时窗口相差可达一小时，混用会把恢复时间报早（`test_recovery_time_comes_from_the_bucket_that_actually_blocked`）。
- 评审补：额外核查未完成时区分真实原因并逐条说明（额度／清单没取得／检查中断），许可说明不再把 500 之类一并归因成额度限制（`test_unobtained_root_listing_is_not_blamed_on_the_quota`、`test_deep_checks_interrupted_by_the_deadline_are_labeled`、`test_unfinished_deep_checks_are_reported_with_the_real_reason`）。

### 5. 测试命令与结果

```
source activate agent && unset SSL_CERT_FILE && PYTHONPATH=src python -m pytest tests/github tests/contracts -q   # 63 passed
source activate agent && unset SSL_CERT_FILE && PYTHONPATH=src python -m pytest tests -q --tb=no -rfE --basetemp=<仓外>   # 全量，见 §7
PYTHONPATH=src python -m mypy src/bridges/github        # Success: no issues found in 11 source files
ruff check src/bridges/github tests/github              # All checks passed!
cd apps/web && npx tsc --noEmit                         # 干净
cd apps/web && npx vitest run                           # 24 files / 203 tests passed
```

### 6. 真实调用与证据摘要（任务 7）

脚本与完整日志留档在主仓 `.tmp/issue05/`（`probe_live_issue05.py`、`live-probe.log`）。真实部分为 GitHub 公开 REST 与模块编排，只把数据库（临时 SQLite）与模型网关（静默替身）替换掉——GitHub 轮按设计不调用模型。

- **额度前提**：先探 `/rate_limit`（不计费）。第一轮 core 余额 0（撞上别人的窗口），按约定**不发业务请求**、如实记录并停止；待 16:20 窗口重置后重跑。
- **调用计数**：一轮完整请求 12 次外发 = 1 次检索 + 11 次仓库读取（三轮 README、三轮根目录清单、三轮许可、2 个实现文件），核心桶余额 45 → 34，与调用数一致；检索桶 10 → 8 后随窗口重置。两轮真实调用（同请求）结果与调用顺序完全一致。
- **检索**：`GET /search/repositories?q=智能体&per_page=10` → 10 个候选，`INSPECT_LIMIT=3` 检查前 3 个。
- **证据驱动结果**（`status=success`）：3 条推荐 `TeamWiseFlow/xiaobei`、`jd-opensource/joyagent-jdgenie`、`datawhalechina/hello-agents`，三条的功能匹配均为 `智能体：已覆盖（README 自述命中关键词「智能体」）`；前两条证据等级到「实际读取的实现文件」（根目录清单 + LICENSE + 实现文件），第三条只有 README 并如实写明「未读取实现文件，不对内部架构与代码质量作断言」；许可分别为 NOASSERTION／Apache-2.0／NOASSERTION，均不含「可自由复用」的断言。
- **未检查候选**：其余 7 个候选逐条写明「本轮检查数量有上限，未完成检查（这不代表它不匹配）」。
- **限流状态**：`limited=false`、`reset_at=null`，正文/卡片不出现额度与恢复时间字样（本轮确实没撞限）。模拟限流回归见 §4，与真实调用结论分开记录。
- **未读到 README 点名路径**：`TeamWiseFlow/xiaobei` 的 `assets/feature1.jpg`、`assets/feature2.jpg` 实际不存在，局限里如实写「另有 1 个 README 点名路径本轮没有读到」。

### 7. 全量回归与提交版本

两侧同跑法（`PYTHONPATH=src`、仓外 basetemp、不 deselect、不 ignore）：

| | 失败 | 通过 | 跳过 |
| --- | --- | --- | --- |
| 分支 `bf53775` | 251 | 3768 | 39 |
| main 基线 `f77e8e2`（detached 工作树） | 251 | 3753 | 39 |

- 失败名称**双向 diff 为空**（`comm -23` / `comm -13` 均为空），本轮没有引入或修掉任何既有失败。
- Δ通过 +15 = 本票新增用例：`tests/github` 收集数 45 → 60（`test_github_core` +5、`test_github_sources` +6、`test_github_module_flow` +4），逐文件 `--collect-only` 对平，`tests/github` 在分支全量里 0 失败。
- 已知既有失败分布与上一轮一致（`tests/chat` 101、`tests/mcp` 49、`tests/plugins` 39 等），与本票无关。
- 前端：`tsc --noEmit` 干净；`vitest run` 24 文件 / 203 例全过（main 基线 24 / 202，+1 为本票新增的卡片限流用例）。
- 日志留档：主仓 `.tmp/issue05/`（`full-suite-branch.log`、`full-suite-baseline.log`、`live-probe.log`、`probe_live_issue05.py`）。

提交：`150a022`（实现）→ `b5e99b1`（并入 main `f77e8e2`）→ `bf53775`（两轴评审修复）→ <本工单记录>

### 8. 未完成事项

- 真实调用受未认证额度约束：检索 10/分、核心 60/时，本轮实测两桶窗口相差约一小时；未认证额度下的「第三个候选撞限」在真实线上只观察到过（工单背景），本轮的限流场景以可控替身回归覆盖。
- 观察到的 2 次无法归属的核心桶消耗（两轮之间由本机其他进程产生），不影响本轮结论，已记在日志里。
- `docs/v2/interaction.md` §4 只约束「剩余额度不呈现」；恢复时间已在正文与卡片统一呈现，如后续要求卡片与正文逐字相同，需要再抽一层共享文案。

## Comments

**两轴 code-review（实现 `150a022` 之后跑，修复 `bf53775`）**：报告 4 条真实缺陷，全部已修并补测试。

已修：
1. **通用词拼盘被当成功能覆盖**（Spec 轴复现的误召回回归）——整词优先匹配让「用户功能」这种只由通用词拼成的要点，只要整句出现在 README 就算覆盖；现改为「按通用双字词从左到右盖满即不算要点」（`_generic_phrase`），`智能体`／`搜索结果` 这类真术语不受影响。
2. **恢复时间取错桶**——`inspecting` 原来读两个额度桶里**更早**的重置时刻，而检索（10/分）与核心（60/时）窗口相差可达一小时，撞的是核心桶却报检索桶的时间；改为按真正撞限的桶取（`GithubApiClient.reset_at_for`）。
3. **许可说明把非额度原因也说成额度**——清单没取得可能是 500／非清单响应／检查被中断，原文案写死「通常是上游额度限制」；现按 `GithubDeepCheckStatus`（`done`／`rate_limited`／`not_obtained`／`interrupted`）区分，并在局限里逐条说明真实原因。
4. **重试承诺在一行里出现两次**——服务的限流说明抄了调用记录的文案（自带「稍后可重试」），呈现侧又补一次；现由呈现侧统一说一遍，卡片恢复时间与后端统一加「（本地时间）」。

决定不改（留给后续票判断）：
- 本地时间格式化在 `presenting.py` 里是第三份副本（`career_plan`／`tieba` 各有一份）：沿用「按模块各自保留」的既有做法，不做跨模块抽取（本票范围明确不抽全局组件）。
- `_deepen` 把记录追加到调用方传入的列表（出参风格）：与 `_read_license`／`_read_implementation` 返回列表的风格不一致，但改动会打乱记录顺序，判定为风格问题不动。
- 整词命中时 `matched_terms` 从「碎片段」变成「用户原话本身」（如 `['学生可以发布想卖的书']` 取代 `['发布']`）：这是任务 2「优先保留并匹配完整中文原词」的预期结果，已把这条合同写进 `_keyword_hits` 的 docstring 与相关用例注释。

真实调用的额度观察：未认证 GitHub 额度是本机共享资源——探针第一轮 `core` 余额为 0（被本机其他进程/并行会话用掉），行为按约定是「如实记录 + 发零个业务请求 + 等窗口重置」，两轮之间还观察到 2 次无法归属的 core 消耗（不影响结论，已记在 `.tmp/issue05/live-probe.log`）。
