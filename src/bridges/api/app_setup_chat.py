from __future__ import annotations

from fastapi import FastAPI

from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.api.app_support import (
    _amap_web_service_key,
    _current_task_reference,
)
from bridges.chat import ChatService, ConversationRepository
from bridges.chat.summary import ChatSummaryService, GatewaySummaryExtractor
from bridges.commute.service import CommuteService
from bridges.commute.sources import AmapRouteClient
from bridges.tasks.repository import TaskRepository
from bridges.tasks.service import TaskService


def configure_chat_services(app: FastAPI) -> None:
    bridges_database = app.state.bridges_database
    model_gateway = app.state.model_gateway
    run_model_config = app.state.run_model_config_provider
    if app.state.bridges_database is None:
        return
    app.state.commute_amap_client = AmapRouteClient(
        key_provider=lambda: _amap_web_service_key(app),
        observability=app.state.observability_service,
    )
    # 改进工单 10：通勤内核的提交守卫读取当前任务版本（惰性求值：
    # task_service 在下方创建，调用发生在请求期）。
    app.state.commute_service = CommuteService(
        amap=app.state.commute_amap_client,
        task_version_provider=lambda account_id, conversation_id: (
            _current_task_reference(app, account_id, conversation_id)
        ),
    )
    app.router.add_event_handler("shutdown", app.state.commute_amap_client.close)
    # 改进工单 08：跨轮任务领域服务。写模型归任务领域仓库（独立账户
    # 域表），API 只做投影；关系由工单 12 的主智能体理解后经
    # POST /tasks/turns 提交，本服务按代码规则裁决。工单 11 起同时作为
    # 指代解析的任务查询接缝（只读）注入 ChatService。
    app.state.task_service = TaskService(
        TaskRepository(bridges_database)
    )
    # 改进工单 13：有界历史摘要缓存 + 回答后有界后台准备。结构化抽取
    # 走固定能力（模型由运行配置解析并记录运行锁），同步补齐与后台
    # 任务都受超时/调用次数/队列重试上限约束；失败只留明确缺口。
    app.state.chat_summary_service = ChatSummaryService(
        database=bridges_database,
        conversation_repository=ConversationRepository(bridges_database),
        extractor=GatewaySummaryExtractor(
            model_gateway,
            model_config_provider=run_model_config,
            lock_recorder=SqliteModelRunLockRecorder(bridges_database),
            database=bridges_database,
        ),
        observability=app.state.observability_service,
    )
    # 工单 18：删除/忘掉/纠正撤回来源后，按依赖失效依赖该事实的历史
    # 摘要——已发往云端的上下文无法收回，钩子只阻止后续调用继续复用。
    app.state.atomic_profile_service.set_revocation_listener(
        app.state.chat_summary_service
    )
    app.state.chat_service = ChatService(
        repository=ConversationRepository(bridges_database),
        gateway=model_gateway,
        model_config_provider=run_model_config,
        attachment_service=getattr(app.state, "chat_attachment_service", None),
        retrieval_service=getattr(app.state, "retrieval_service", None),
        web_search_service=getattr(app.state, "web_search_service", None),
        arxiv_search_service=getattr(app.state, "arxiv_search_service", None),
        paper_search_service=getattr(app.state, "paper_search_service", None),
        tieba_research_service=getattr(
            app.state, "tieba_research_service", None
        ),
        career_plan_service=getattr(app.state, "career_plan_service", None),
        github_projects_service=getattr(
            app.state, "github_projects_service", None
        ),
        learning_resources_service=getattr(
            app.state, "learning_resources_service", None
        ),
        commute_service=getattr(app.state, "commute_service", None),
        profile_service=getattr(app.state, "profile_service", None),
        teaching_progress_service=getattr(
            app.state, "teaching_progress_service", None
        ),
        four_dimension_profile_service=getattr(
            app.state, "four_dimension_profile_service", None
        ),
        observability_service=app.state.observability_service,
        career_planner_service=app.state.career_planner_service,
        selections_service=app.state.chat_selections_service,
        mcp_service=getattr(app.state, "mcp_service", None),
        automatic_profile_service=app.state.automatic_profile_service,
        atomic_profile_service=getattr(app.state, "atomic_profile_service", None),
        task_service=app.state.task_service,
        summary_service=app.state.chat_summary_service,
    )
    # Issue 02：持久化生成运行的后台执行器（ADR-0013）。API 进程内
    # 受监督线程按租约领取生成运行并执行——HTTP/SSE 只创建与订阅。
    # test 环境（确定性适配器驱动）不自动启动线程，由测试显式驱动
    # run_tick 或自行启动，避免与同步消费生成器的既有测试竞态。
