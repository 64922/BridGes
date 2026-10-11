# 论文搜索改造交接

## 目标与状态

按 [PRD.md](PRD.md) 改造论文搜索：模型动态理解研究意图，多来源检索并按身份融合，严格执行硬条件，进行一次有限缺口补搜与轻量引用扩展，交付聚焦推荐、证据、来源/引用路径，并区分全文链接与本轮实际阅读范围。

改造已实施。论文范围测试、类型检查、前端测试/构建、OpenAPI 同步和三类来源的真实查询均已完成。全仓后端检查有论文范围外失败，结果见下文。

## 仓库状态与约束

- 代码审查比较基准为用户确认的 `HEAD`：`65e5b63720a05520f7721a4e81b2959f0432edb2`。
- 工作区原有大量 Tieba 未提交改动，且与本次论文工作混在一起。保留所有内容，不提交混合工作区。
- 开发 Python 命令使用 Conda `agent` 环境。后续 `conda run` 因 Windows DLL 重定位错误无法启动时，改用同一环境的 `C:\Users\33755\anaconda3\envs\agent\python.exe`；前端命令通过 `CONDA_PREFIX` 与 `CONDA_DEFAULT_ENV=agent` 环境执行。
- 没有数据库 schema 变化，不需要数据迁移。

## 实施结果

- 新增模型驱动研究意图分析；提交完整请求与当前有效条件，验证结构化产物及下游实际查询。首次意图分析失败时停止外部检索并保留重试路径。
- arXiv、OpenAlex、Crossref 并行首轮检索；来源查询、候选数和失败状态分别留账。按 DOI、arXiv ID、OpenAlex ID 或来源原生 ID 融合，不按近似标题合并。
- 年份、来源、指定论文、排除项和篇数为硬条件。仅在发现具体概念缺口、预算允许且查询实质变化时补搜一次；少量 OpenAlex 引用扩展只保留真实引用边，扩展候选重新筛选。
- 结果保留通用候选、匹配证据、相关度、来源和发现路径。前端呈现研究问题、重点论文、可展开候选、引用信息；全文链接与实际阅读范围分别显示。未知年份不会被补成公元 1 年、奠基工作或较新研究。
- 更新 `docs/workflow/daily-workflows.md` 第 3 节和 `docs/v2/workflows.md`；新增 [ADR-0039](../../docs/adr/0039-research-question-driven-paper-discovery.md)。
- 从当前 FastAPI 应用重新生成 `openapi.json`（313 个 paths），再生成 `packages/contracts/src/generated.ts`。确认 Tieba 路由与投影类型仍存在；`apps/web/src/lib/api.ts` 导出论文投影类型。

## PRD 验收

行为回归覆盖 CNN、轴承故障诊断、Transformer 交通流预测、CNN 小样本图像分类、动态词表外概念、硬条件、首次意图失败、来源/引用失败、预算限制、真实引用边、并行首轮和全文链接/读取范围展示。

Crossref 实际 API 曾返回 `date-parts: [[null]]`，触发 `int(None)` 异常。解析现会跳过缺失日期并尝试其他日期字段，未知年份保持未知；相关回归通过。

### 真实来源查询

均以 `convolutional neural network image classification` 为检索请求，通过仓库适配器实际请求公共来源：

| 来源 | 结果 | 候选数 | 耗时 | 备注 |
| --- | --- | ---: | ---: | --- |
| OpenAlex | success | 2 | 约 0.73 秒 | 示例标题：Evaluating Explanations of Convolutional Neural Network Image Classifications |
| arXiv | success | 1 | 约 0.98 秒 | 示例标题：Healthy Counterfactual Generation via Diffusion Inpainting for Mammography Classification |
| Crossref | success | 2 | 1.73 秒 | 真实记录日期字段为空；现在候选年份为 `null`，不再抛异常 |

Crossref 查询参数由适配器记录；没有用户身份或敏感信息随查询外发。

## 验证结果

### 后端

| 检查 | 结果 |
| --- | --- |
| `agent` Python：`python -m pytest tests\\paper -q`（允许 Starlette TestClient 使用本地 socket） | **120 passed in 26.62s** |
| `mypy src\\bridges\\paper` | 20 个源码文件无问题 |
| Ruff：新增论文模块及新增论文测试文件 | 全部通过 |
| `pip check` | 通过 |
| 全仓 `python -m pytest -q` | **5728 passed, 37 skipped, 210 failed**，失败落在论文范围外的聊天、插件、退休路由、检索、安全和 Tieba 等目录；由于工作区包含原有混合改动，未把这些失败一概归因于本次或 HEAD |
| 全仓 `ruff check src tests` | 569 个错误；论文新增文件的定向 Ruff 检查通过 |
| 全仓 `mypy src` | 失败，包含论文范围外模块错误；论文目录定向 mypy 通过 |

论文目录第一次在沙箱内运行时卡在 Starlette TestClient 的本地 socketpair；允许本地测试 socket 后 120 项全过。

### 前端与合同

| 检查 | 结果 |
| --- | --- |
| `apps/web`：`npm run typecheck` | 通过 |
| `apps/web`：`npm run test:unit` | **29 个文件、252 个测试通过** |
| `apps/web`：`npm run build` | 通过；有既存 `<img>` 与 Hook dependency 警告 |
| OpenAPI / 生成类型 | 313 个 OpenAPI paths；生成类型含论文字段并保留 Tieba 路由和类型 |
| `git diff --check` | 通过；Git 仅提示部分工作区文件会转换 LF/CRLF |

前端单测临时目录使用仓库内 `.scratch/paper-search-redesign/vitest-temp`，避免 Windows 沙箱临时目录的 `EPERM` 重命名错误。Next.js build 写入 `.next` 时需要提升执行权限。

### 新增文件长度

新增论文源码、测试与 ADR 均少于 500 行；最长的 `discovery.py` 为 485 行，`intent.py` 为 462 行，`test_paper_search_redesign.py` 拆分后为 458 行。

## 尚存检查问题

- 全仓 pytest、Ruff 和 mypy 未全绿，详见上表；论文范围测试、静态检查、前端构建和真实来源查询均已通过。
- 不提交当前混合工作区，Tieba 相关既有改动必须由用户后续单独处理。
- 临时诊断脚本 `_crossref_debug.py`、`_source_check.py` 与 Vitest 临时目录应在验收结束后清理；清理时只删除本任务创建的这些精确路径。

## 官方来源文档

- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [OpenAlex 搜索](https://help.openalex.org/api/searching/) 与 [过滤](https://help.openalex.org/api/filtering/)
- [arXiv API 手册](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md) 与 [使用条款](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/tou.md)
