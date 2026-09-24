## Agent skills

### Issue tracker

任务通过本仓库 `.scratch/` 下的本地 Markdown 文件跟踪，不使用外部 PR 作为分诊入口。参见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个默认分诊状态：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human` 和 `wontfix`。参见 `docs/agents/triage-labels.md`。

### Domain docs

本仓库采用单一上下文布局：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。参见 `docs/agents/domain.md`。

### 项目开发环境

智能体开发此项目时，需要在conda环境“agent”下开发。