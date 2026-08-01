# BridGes 使用全新数据库并封存旧原型数据

现有 Science Companion 数据库不存在真实账户、用户上传材料或有效画像，因此 BridGes 新建干净的 `bridges.db`，不在旧 Schema 上原位迁移，也不自动导入旧工作流对象、Stub 状态或测试数据。旧数据库仅作为带时间戳的只读历史快照保留；只有未来发现明确的真实用户资产时，才另行设计包含预检、备份、迁移报告和失败回滚的一次性导入工具。旧 `.scratch/science-companion-plan/` 与根目录 `tickets.md` 同样保留为历史规划材料，不构成新计划的任务状态；新工作使用独立的 `.scratch/bridges-improvement/`。
