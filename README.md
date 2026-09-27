<div align="center">
  <img src="apps/web/public/brand/bridges-logo-on-dark.svg" alt="BridGes" width="320">
  <p>面向华东交通大学学生的校园学习助手</p>
  <p>日常交流 · 校园信息 · 书页学习 · 职业准备</p>
</div>

BridGes V2 是在个人电脑上运行、通过桌面浏览器使用的聊天应用。你可以围绕日常问题交流，主动选择六个任务模块查找资料，也可以上传教材书页，完成一个小节的预习、辅导、复盘与总结。

项目面向华东交通大学学生的使用场景，不核验学籍。账户数据在本机按账户隔离保存；模型理解与生成、联网检索和地图查询仍会调用外部服务，并非离线应用。

本说明对应仓库中的 V2 实现。产品边界由 [ADR-0030](docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md) 和 [V2 产品契约](docs/v2/product-contract.md)定义；模型与凭据更新规则见 [ADR-0031](docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md)。V2 文档保留了设计阶段的背景说明，不能仅凭其中的“待实施”或“已批准”判断当前交付状态。

## 可以用它做什么

### 日常陪伴与六个模块

新聊天默认进入日常陪伴。直接输入即可普通聊天；点击输入框左侧的 `+`，选择模块后发送请求。选中的模块显示为可移除标签，同一日常会话可以切换模块并保留前文。系统可以建议使用某个模块，但需要你明确选择后才会启动。

| 模块 | 用法示例 | 结果与边界 |
| --- | --- | --- |
| 论文搜索 | “找 Transformer 注意力机制的入门论文” | 以 arXiv 为主检索，提供论文链接、选择理由和阅读顺序；区分摘要信息与实际取得的全文。 |
| 校园通勤 | “从图书馆步行到教学楼” | 按实际定位结果和高德路线返回距离、耗时与地图；支持步行、自行车、电动车，地点或方式不明时先澄清。 |
| 学习资料推荐 | “我有 Python 基础，想学数据分析” | 推荐可核验的图书与哔哩哔哩视频，说明适用基础和学习顺序；来源不足时不凑数。 |
| 贴吧信息搜集 | “查一下华东交通大学吧里关于宿舍的讨论” | 发现本校贴吧公开帖子；取不到回复时只给帖链，不编造回复总结。 |
| 职业规划 | “我想找南昌的 Java 后端实习，该准备什么？” | 根据目标岗位和城市整理公开职位样本与技能要求，标注来源、日期和样本范围。 |
| GitHub 项目推荐 | “找可以参考的校园二手书交换平台项目” | 优先找整体功能相近的仓库，说明可借鉴部分、许可与局限；组件项目会单独说明覆盖范围。 |

外部平台限流、页面不可访问、凭据缺失或证据不足时，模块会说明失败或缺口。校内路线覆盖、贴吧回复和公开职位样本尤其依赖当次可得性；本地验收通过不代表这些服务始终可用。详见 [V2 可行性与上线门](docs/v2/feasibility.md)。

### 从书页开始的学习模式

在新聊天右上角选择「学习模式」，上传**同一本书、同一个小节的照片**。首轮需要可处理的图片，纯文本或 PDF 不能单独启动书页学习。

1. **识别书页**：按上传页序识别文字、公式和图表，关键内容不清楚时要求补拍或补充。
2. **辅助预习**：给出带着阅读的问题，暂不要求作答。
3. **辅导**：围绕本节内容自由提问，回答引用书页；补充资料与书页原文分开呈现。
4. **逐题复盘**：学完后开始，一次一题。错误或不完整回答直接获得正确答案与解释，不要求反复补答同一题。
5. **总结**：根据实际作答总结掌握情况。复盘可以暂停回辅导，再继续未问的题；下一小节另开会话。

两种模式都在首条消息成功创建会话时锁定。已有会话不能中途切换模式，需要新建聊天重新选择。

### 附件、知识库与画像

- **聊天附件**：支持 PDF、DOCX、TXT、Markdown 和 PNG／JPEG／GIF／WebP 图片，单个文件不超过 10 MB。可选择文件、拖入或粘贴图片，发送前预览、移除与调整页序。附件归属当前会话，不自动进入知识库。
- **账户知识库**：从侧栏主动上传长期使用的讲义、笔记、简历或教材章节。后台解析后通过关键词与向量混合检索提供相关片段与定位引用。
- **用户画像**：从用户明确表达中提取可复用的原子信息，逐条修改或删除。用户修改优先，删除后的信息不会因重放旧消息自动恢复。
- **历史与数据管理**：保留会话历史、账户隔离、数据导出、备份和删除入口。

V2 已关闭文章人味化专用任务、图片／视频生成、旧科学文章创作、旧学习计划写入口及回答朗读等退役入口；已有结果仍保留只读查看与导出兼容。自然表达规则直接用于正常回答生成。

## 快速开始

在仓库根目录操作。项目开发统一使用 Conda 的 `agent` 环境：Python 3.11、Node.js 24 和 npm。Python 依赖由 `pyproject.toml` 管理，前端依赖由 `apps/web/package-lock.json` 锁定。

```powershell
# 首次创建环境
conda env create -f environment.yml
conda activate agent

# 安装后端与开发依赖
python -m pip install -e ".[dev]"

# 启动桌面应用
BridGes start
```

已有 `agent` 环境时，第一步改为 `conda env update -n agent -f environment.yml`，然后重新激活并安装 Python 依赖。

如果仓库移动过，或以前从其他工作树安装过，即使 `pip` 显示已安装 `bridges`，可编辑安装也可能仍指向旧路径。出现 `No module named bridges` 时，在当前仓库根目录重新执行 `python -m pip install -e ".[dev]"`。

默认的 `desktop` 启动流程会：

1. 创建本机配置和数据目录。
2. 按需执行 `npm ci` 和 `npm run build`，安装并构建 Web。
3. 在交互式终端隐藏询问 Qwen API Key 与 Tavily API Key，保存到操作系统凭据库；后续启动复用。
4. 执行数据库迁移，启动 Web、API 和后台执行器，并等待就绪。

Qwen 凭据是正式启动的必需项。当前交互式桌面首启也要求 Tavily 输入非空，直接回车会取消启动；非交互启动、显式 profile 或容器允许缺少 Tavily，相关搜索会提示未配置。首次安装需要网络。应用配置**不自动读取 `.env`**，无需创建该文件。

启动后打开 [BridGes 桌面页面](http://127.0.0.1:3000)。[API 就绪检查](http://127.0.0.1:8000/health/ready)返回 `ready: pass` 表示服务就绪，不表示外部模型和所有数据源已通过真实调用验证。按 `Ctrl+C` 停止应用。

## 密钥与模型配置

在账户菜单的设置页管理 Qwen、Tavily、高德凭据及 Qwen 主模型。凭据由当前安装实例管理，并非每个登录账户各自配置一套。

| 配置 | 用途 | 缺失或更新时的行为 |
| --- | --- | --- |
| Qwen API Key | 对话、图片理解、OCR、向量化等模型调用 | 首次启动必须提供；设置页验证候选值成功后才替换。 |
| Qwen 主模型 ID | 主对话、视觉理解与 OCR | 手填精确 ID，经元数据核对和真实能力探测后保存；失败保留原配置。 |
| Tavily API Key | 通用公网检索及依赖它的资料搜集 | 交互式桌面首启要求输入；其他启动方式可缺省，之后在设置页补充。 |
| 高德 Web Service Key | 地点与路线查询 | 未配置时通勤模块提示补充。 |
| 高德 JavaScript Key／安全密钥 | 交互地图展示及代理校验 | 与路线 Key 分开配置。 |

模型切换从下一条消息生效，在途轮次保留启动时的模型，历史回复不会改写。向量模型与索引版本独立固定，不随主模型切换。设置页验证会调用外部服务；Qwen 凭据更换后 API 就地生效，其他进程需重启读取，具体边界见 [ADR-0031](docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md)。

自动化部署可使用 `BRIDGES_*` 环境变量或密钥文件引用，例如 `BRIDGES_QWEN_API_KEY_FILE`。显式 `development`／`production` 启动不会执行桌面初始化，需要自行提供数据库、状态密钥和 Qwen 凭据。完整说明见[运行与配置手册](infra/manual/README.md)。

## 开发与验证

以下命令在已激活的 `agent` 环境执行。前端安装与单元检查：

```powershell
python -m pip check
python -m pytest -q
python -m mypy src

cd apps/web
npm ci
npm run typecheck
npm run test:unit
npm run build
cd ../..
```

桌面交互验收覆盖 1280×720、1440×900、1920×1080 三个视口。Playwright 默认寻找仓库 `.venv`，使用 Conda 时须明确指定 Python；下面为 PowerShell 示例：

```powershell
$env:BRIDGES_PYTHON = (python -c "import sys; print(sys.executable)")
cd apps/web
npx playwright install chromium
npm run test:e2e -- e2e/issue21-desktop-acceptance.spec.ts
cd ../..
```

验收脚本启动独立测试服务，默认需要 3000／8000 等测试端口空闲。它检查正式 UI、API 与执行器链路；其中的外部来源可能走真实网络，也可能在专用夹具配置下被替代。测试结果应区分本地功能验证与供应商可得性验证，既有验收记录见 [Issue 21](.scratch/bridges-v2/issues/21-legacy-exit-and-history-regression.md)。

| 命令 | 用途 |
| --- | --- |
| `BridGes start` | 默认桌面启动，自动准备配置与 Web 构建。 |
| `BridGes start --profile development` | 使用预先提供的运行配置，启动前端开发服务器。 |
| `BridGes api` / `BridGes web --dev` / `BridGes worker` | 分进程运行；API 与后台执行器需要完整运行配置。 |
| `BridGes doctor` | 检查当前进程读取到的配置；不会自动加载桌面 `config.json`。 |
| `BridGes migrate` | 对当前配置的数据库执行迁移。 |

## Docker / Podman

容器使用标准 Python／Node.js 镜像，不需要宿主机安装 Conda。先通过宿主机环境提供 `BRIDGES_QWEN_API_KEY`；Tavily 和高德变量可按需提供。也可挂载密钥文件并使用 `_FILE` 引用，见[配置手册](infra/manual/README.md)。

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
# 或使用 Podman
podman-compose -f infra/compose/docker-compose.yml up --build
```

Compose 启动 Web、API、后台执行器和旧提醒兼容清理进程。API、后台执行器与清理进程共享 `bridges-data` 数据卷；Web 不挂载账户数据。容器中的凭据使用加密卷保存。修改宿主机注入配置后需重建相关容器；不要删除数据卷来升级应用。

## 数据存放与隐私

默认桌面状态目录如下，可在启动前通过 `BRIDGES_HOME` 指定其他目录：

| 系统 | 默认目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\BridGes` |
| macOS | `~/Library/Application Support/BridGes` |
| Linux | `$XDG_DATA_HOME/BridGes`，未设置时为 `~/.local/share/BridGes` |

目录中保存桌面配置、本地 SQLite 数据库、加密对象及运行状态。账户数据按稳定账户 ID 隔离；API 凭据保存在系统凭据库或容器加密凭据卷中，不应提交到仓库。账户设置提供数据导出、备份和删除操作；导出备份不包含供应商密钥。

“本地保存”不等于“内容不出本机”：生成回答、OCR 与向量化会向模型服务提交必要材料，搜索与地图会发送必要查询。只面向个人电脑的桌面浏览器，不承诺移动端适配或离线模型运行。

## 项目结构与文档

| 路径 | 内容 |
| --- | --- |
| `apps/web/` | Next.js／React 桌面前端、组件测试与 Playwright 验收。 |
| `src/bridges/` | FastAPI、CLI、LangGraph 对话编排、六模块、学习流程、存储与检索。 |
| `packages/contracts/`、`openapi.json` | 前后端共享的 API 契约及生成类型。 |
| `tests/` | 后端单元、契约、集成与迁移测试。 |
| `infra/`、`scripts/` | 容器与手动运行配置、诊断及验收脚本。 |
| `docs/v2/`、`docs/adr/` | V2 设计基线与架构决策。 |
| `.scratch/bridges-v2/issues/` | V2 本地任务与实现验收记录。 |

- [V2 文档索引](docs/v2/README.md)：产品、交互、工作流、架构与交付计划。
- [运行与配置手册](infra/manual/README.md)：进程、环境变量、凭据与容器配置。
- [领域上下文](CONTEXT.md)与[数据表归属](docs/table-owners.md)：领域边界与存储责任。
- [桌面设计规范](DESIGN.md)：界面视觉规范。

本项目采用 [Apache-2.0 许可证](LICENSE)。
