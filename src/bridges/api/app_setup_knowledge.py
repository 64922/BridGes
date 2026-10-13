from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI

from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.chat import AttachmentRepository, ChatAttachmentService, ConversationRepository
from bridges.image.service import ImageService
from bridges.ingestion.embedding import QwenEmbeddingPort
from bridges.ingestion.ocr import QwenOcrPort
from bridges.ingestion.service import IngestionService
from bridges.knowledge_base import KnowledgeBaseService
from bridges.learning_projects import LearningProjectService
from bridges.learning_projects.migration import ProjectMigrationService
from bridges.mcp.runtime import McpRuntime
from bridges.mcp.service import McpService
from bridges.persistence import SqliteStateStore
from bridges.plugins.service import PluginService
from bridges.retirement import retire_user_extensions
from bridges.retrieval.service import LayeredRetrievalService
from bridges.skills import create_builtin_registry
from bridges.video.service import VideoService


def configure_knowledge_services(app: FastAPI) -> None:
    settings = app.state.settings
    state_store = app.state.state_store
    bridges_database = app.state.bridges_database
    model_gateway = app.state.model_gateway
    app.state.mcp_runtime = None
    if app.state.bridges_database is None:
        return
    skill_registry = create_builtin_registry()
    app.state.skill_registry = skill_registry
    object_repository = getattr(app.state, "object_repository", None)
    if object_repository is not None:
        # Issue 17: 文档摄取服务（API 进程只做入队/重试/投影，处理在后台
        # 执行器进程）。GQ-05：查询向量与 worker 摄取/重建共用同一全局
        # Embedding 端口；可用性由构造与调用结果决定，不再依赖账户探测。
        # Issue 15：生产 Embedding 经同一 ModelGateway（Issue 09 固定矩阵
        # + 真实 adapter）调用，并用统一 recorder 逐批次持久化运行锁。
        embedding_port = QwenEmbeddingPort(
            api_key=(settings.qwen_api_key if settings is not None else None),
            gateway=model_gateway,
            recorder=SqliteModelRunLockRecorder(bridges_database),
        )
        # Issue 09：更换 Qwen 密钥时同步更新向量化端口的凭据判定来源
        # （向量模型本身仍独立固定，不随主模型改变）。
        app.state.embedding_port = embedding_port
        # Issue 14：知识库 OCR 统一走 Issue 09 生产组合的注册能力与
        # Issue 10 运行记录接缝（网关解析固定模型/区域/重试政策，锁由
        # recorder 幂等持久化），端口不再自建 client/adapter。
        ocr_port = QwenOcrPort(
            gateway=model_gateway,
            recorder=SqliteModelRunLockRecorder(bridges_database),
        )
        app.state.ingestion_service = IngestionService(
            database=bridges_database,
            object_repository=object_repository,
            embedding=embedding_port,
            ocr=ocr_port,
        )
        # Issue 45：附件服务经 chat 域仓库访问数据库（chat_attachments
        # / chat_attachment_cancellations 属主 AttachmentRepository，
        # conversations 属主 ConversationRepository，objects 属主
        # BridgesObjectRepository）。V2 Issue 06：文件草稿上传即入队
        # 解析（解析/分块/索引由后台执行器完成），因此注入同一摄取
        # 服务实例；照片草稿不入队，仍以多模态图片直读。
        app.state.chat_attachment_service = ChatAttachmentService(
            bridges_database,
            object_repository,
            attachment_repository=AttachmentRepository(bridges_database),
            conversation_repository=ConversationRepository(bridges_database),
            ingestion_service=app.state.ingestion_service,
        )
        app.state.learning_project_migration_service = ProjectMigrationService(
            database=bridges_database,
            object_repository=object_repository,
            ingestion_service=app.state.ingestion_service,
        )
        # Issue 18: 全局本地知识库（材料不绑定对话，复用摄取状态机）。
        app.state.knowledge_base_service = KnowledgeBaseService(
            bridges_database,
            object_repository,
            app.state.ingestion_service,
            app.state.learning_project_migration_service,
        )
        # Issue 19: 文件夹式学习项目（对话归属 + 项目级文件，复用摄取状态机）。
        app.state.learning_project_service = LearningProjectService(
            bridges_database,
            object_repository,
            app.state.ingestion_service,
            ConversationRepository(bridges_database),
        )
        # Issue 20: 分层本地检索（API 进程只读检索 + 写入检索记录）。查询
        # 向量经唯一的全局百炼运行凭据调用固定 Embedding 模型（GQ-05）。
        app.state.retrieval_service = LayeredRetrievalService(
            database=bridges_database,
            embedding=embedding_port,
            # Issue 45：跨域读取经属主仓库构造注入（conversations/
            # chat_attachments 属 chat 域、objects 属 storage 域），
            # 检索服务不再直读非己表。
            conversation_repository=ConversationRepository(bridges_database),
            attachment_repository=AttachmentRepository(bridges_database),
            object_repository=object_repository,
        )
        # Issue 31: 图片生成与编辑（固定 qwen-image-2.0-pro-2026-06-22）。
        # API 进程只做提交/查询/取消/重试与资产管理；云端轮询在后台
        # 执行器进程内按租约执行，任务与消息投影原子落库。
        app.state.image_service = ImageService(
            database=bridges_database,
            gateway=model_gateway,
            object_repository=object_repository,
            chat_repository=ConversationRepository(bridges_database),
            observability_service=app.state.observability_service,
        )
        # Issue 32: 文生视频（固定 wan2.7-t2v-2026-06-12，ADR-0007
        # Wan 例外）。API 进程只做提交/查询/取消/重试与资产管理；云端
        # 轮询在后台执行器进程内按租约执行，任务与消息投影原子落库。
        app.state.video_service = VideoService(
            database=bridges_database,
            gateway=model_gateway,
            object_repository=object_repository,
            chat_repository=ConversationRepository(bridges_database),
            observability_service=app.state.observability_service,
        )
        # Issue 34：SKILL 插件中心。内置包随应用发布（当前只剩两个本地
        # 解析能力，不再展示已退役能力），用户包经安全闭锁后按账户安装
        # 到对象库与 skill_packages 表；启停/卸载/演示全部账户作用域
        # 并写审计。
        mcp_pid_dir = Path(cast(SqliteStateStore, state_store).path).parent / "mcp-pids"
        mcp_runtime = McpRuntime(pid_dir=mcp_pid_dir)
        app.state.mcp_runtime = mcp_runtime
        retire_user_extensions(bridges_database, runtime=mcp_runtime)
        app.state.plugin_service = PluginService(
            database=bridges_database,
            object_repository=object_repository,
            observability_service=app.state.observability_service,
            skill_registry=skill_registry,
        )
        # Issue 35：显式授权 MCP 插件管理。安装描述按账户持久化（版本锁/
        # 来源/完整性哈希），进程在独立受限环境中惰性运行；pid 文件落数据
        # 目录用于重启后回收异常退出遗留的孤儿进程（AC3 重启恢复合法配置，
        # 不继承僵尸进程）；关闭时停止全部进程。
        # isinstance 收窄在联合类型上不保留：显式 cast。
        mcp_service = McpService(
            database=bridges_database,
            object_repository=object_repository,
            observability_service=app.state.observability_service,
            runtime=mcp_runtime,
            scope_enforcer=app.state.scope_enforcer,
        )
        # 应用启动：回收上次异常退出遗留的孤儿 MCP 进程（pid 文件兜底）。
        mcp_service.reap_orphans()
        app.state.mcp_service = mcp_service
