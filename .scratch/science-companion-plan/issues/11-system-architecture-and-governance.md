# 收敛系统架构、数据治理与工程质量方案

Type: prototype
Status: resolved
Blocked by: 02, 03, 04, 05, 06, 07, 08, 09, 10

## Question

在已确定的产品能力、智能体协议、科学可信体系、画像治理、多模态链路和技术候选基础上，完整系统应采用怎样的模块边界、服务拓扑、多用户认证与逐账户数据隔离、数据模型、权限体系、加密同步、任务队列、缓存、限流、审计、可观测性、测试策略和故障降级？如何让仅用于本地开发的 Conda `agent` 环境与生产运行合同解耦，同时保证手动分进程部署、统一 CLI 启动、Docker 和 Podman 使用同一配置、迁移、健康检查和可复现工程规范？

## Answer

完整决策见 [系统架构、数据治理与工程质量蓝图](../decisions/11-system-architecture-and-governance.md)，可运行验证见 [系统架构逻辑原型](../prototypes/system-architecture/README.md)。

用户确认采用“设备侧个人保险库 + 云/自托管领域模块化单体 + Temporal 持久工作流 + PostgreSQL 权威控制数据 + 独立强隔离沙箱 + Next.js 项目工作台”。业务能力按深模块组织，不按菜单拆微服务；只有 Web、任务 worker、设备侧个人保险库与沙箱因运行时、安全域或扩缩需求独立部署。PostgreSQL + RLS + FTS + pgvector 承载账户、授权、共享项目、工作流、出处和首个正式检索平面，Redis 仅作可丢失缓存、限流和通知，S3 保存大对象，LangGraph 仅可作为专业节点内部适配器。Conda `agent` 只锁定本地开发工具链；生产不依赖 Conda，并以手动分进程、统一 CLI、Docker、Podman 四种路径共享配置、迁移、健康检查、授权、同步、删除和故障降级合同。
