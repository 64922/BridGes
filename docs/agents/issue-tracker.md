# 问题跟踪器：本地 Markdown

本仓库的任务和 PRD 以 Markdown 文件形式存放在 `.scratch/` 中。

## 约定

- 每项功能使用一个目录：`.scratch/<feature-slug>/`
- PRD 路径为 `.scratch/<feature-slug>/PRD.md`
- 实现任务存放在 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号
- 分诊状态记录在任务文件顶部附近的 `Status:` 字段中
- 评论和讨论记录追加在文件末尾的 `## Comments` 标题下

## 当技能要求“发布到问题跟踪器”时

在 `.scratch/<feature-slug>/` 下创建文件；目录不存在时一并创建。

## 当技能要求“读取相关任务”时

读取引用路径对应的文件。用户通常会直接提供文件路径或任务编号。

## Wayfinder 操作约定

`wayfinder` 使用一个地图文件和每项任务对应的子文件：

- **地图**：`.scratch/<effort>/map.md`，保存笔记、已有决策和待探索区域
- **子任务**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 开始编号
- **类型**：通过 `Type:` 记录 `research`、`prototype`、`grilling` 或 `task`
- **状态**：通过 `Status:` 记录 `claimed` 或 `resolved`
- **依赖**：通过 `Blocked by: NN, NN` 记录；列出的任务全部解决后才解除阻塞
- **任务前沿**：扫描开放、未阻塞且无人认领的任务，编号最小者优先
- **认领**：开始工作前将状态改为 `claimed` 并保存
- **解决**：在 `## Answer` 下追加答案，将状态改为 `resolved`，并在地图的已有决策部分追加摘要和链接
