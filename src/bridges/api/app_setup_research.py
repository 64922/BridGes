from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.api.app_support import (
    _current_task_reference,
    _paper_metadata_client,
)
from bridges.career.service import CareerPlannerService
from bridges.chat import ConversationRepository
from bridges.chat.selections import ChatSelectionsService
from bridges.paper.discovery import CrossrefPaperSource, OpenAlexPaperSource
from bridges.paper.intent import PaperResearchIntentAnalyzer
from bridges.paper.presenting import PaperSummaryGenerator
from bridges.paper.service import PaperSearchService
from bridges.paper.sources import ArxivPaperSource, MetadataEnricher
from bridges.search import SearchService
from bridges.tieba.browser import BrowserTiebaSearch, BrowserTiebaThreadReader
from bridges.tieba.browser_runtime import TiebaBrowserRuntime
from bridges.tieba.official import HttpOfficialSiteReader
from bridges.tieba.reading import HttpTiebaThreadReader
from bridges.tieba.searching import WebSearchServiceAdapter
from bridges.tieba.service import TiebaResearchService


def configure_research_services(app: FastAPI) -> None:
    bridges_database = app.state.bridges_database
    model_gateway = app.state.model_gateway
    data_dir = app.state.data_dir
    use_closeout_fixtures = app.state.use_closeout_fixtures
    if app.state.bridges_database is None:
        return
    app.state.search_service = SearchService(bridges_database)
    # Issue 29：生涯规划编排服务（复用画像切片编译、学习记录、检索与
    # 联网证据合同；六类输出由结构化模型生成并确定性复核）。Issue 12：
    # 注入 Issue 10 统一运行锁 recorder，每次结构化调用立即幂等持久化，
    # 缺审计证据或持久化失败时由服务失败关闭，不提升为完成态。
    app.state.career_planner_service = CareerPlannerService(
        gateway=model_gateway,
        profile_service=getattr(app.state, "profile_service", None),
        learning_service=app.state.learning_service,
        observability_service=app.state.observability_service,
        run_lock_recorder=SqliteModelRunLockRecorder(bridges_database),
    )
    # Issue 36：对话级插件选择域（校验/失效清洗/工具上下文编译）。
    # 可用集合来自插件中心（SKILL 已安装且启用）与 MCP 服务器（已
    # 安装且启用），选择随对话持久化；撤权动作从全部会话选择移除。
    app.state.chat_selections_service = ChatSelectionsService(
        repository=ConversationRepository(bridges_database),
        database=bridges_database,
        plugin_service=getattr(app.state, "plugin_service", None),
        mcp_service=getattr(app.state, "mcp_service", None),
    )
    # V2 Issue 11：论文搜索模块子图——arXiv 主来源复用既有受限服务，
    # Crossref／OpenAlex 只作有限补充（不装配时对应步骤如实标注缺口），
    # 中文概述经同一模型网关生成并受证据门控。
    paper_enricher = (
        None
        if use_closeout_fixtures
        else MetadataEnricher(
            client=_paper_metadata_client(),
            observability=app.state.observability_service,
        )
    )
    paper_sources: dict[str, Any] = {
        "arxiv": ArxivPaperSource(app.state.arxiv_search_service),
    }
    if not use_closeout_fixtures:
        paper_sources.update({
            "openalex": OpenAlexPaperSource(
                client=_paper_metadata_client(),
                observability=app.state.observability_service,
            ),
            "crossref": CrossrefPaperSource(
                client=_paper_metadata_client(),
                observability=app.state.observability_service,
            ),
        })
    # 改进工单 10：论文内核的提交守卫读取当前任务版本（惰性求值：
    # task_service 在本装配段之后创建，调用发生在请求期）。
    app.state.paper_search_service = PaperSearchService(
        source=paper_sources["arxiv"],
        sources=paper_sources,
        enricher=paper_enricher,
        summarizer=PaperSummaryGenerator(model_gateway),
        # 工单 24：生产 judge/reviewer 复用同一登记网关、运行额度与
        # 载荷预算；每批候选一次语义判断，比较/冲突触发独立复核。
        gateway=model_gateway,
        intent_analyzer_factory=(
            None if use_closeout_fixtures else PaperResearchIntentAnalyzer
        ),
        task_version_provider=lambda account_id, conversation_id: (
            _current_task_reference(app, account_id, conversation_id)
        ),
    )
    app.router.add_event_handler("shutdown", app.state.paper_search_service.close)
    # 贴吧原生检索使用账户隔离会话；公开索引只作有记录的后备，官方路径独立。
    public_tieba_search = WebSearchServiceAdapter(app.state.web_search_service)
    tieba_browser = TiebaBrowserRuntime(data_dir) if data_dir is not None else None
    app.state.tieba_browser_runtime = tieba_browser
    if tieba_browser is not None:
        app.router.add_event_handler("shutdown", tieba_browser.close)
    app.state.tieba_research_service = TiebaResearchService(
        search=(
            BrowserTiebaSearch(tieba_browser, public_tieba_search)
            if tieba_browser else public_tieba_search
        ),
        reader=HttpTiebaThreadReader(),
        reader_for_account=(
            (lambda account_id: BrowserTiebaThreadReader(tieba_browser, account_id))
            if tieba_browser else None
        ),
        read_deadline_seconds=35.0 if tieba_browser else 12.0,
        official_reader=HttpOfficialSiteReader(),
        model_gateway=app.state.model_gateway,
        # 改进工单 27：贴吧节点内核的提交守卫读取当前任务版本（惰性求值）。
        task_version_provider=lambda account_id, conversation_id: (
            _current_task_reference(app, account_id, conversation_id)
        ),
        account_deleting=(
            tieba_browser.account_is_deleting if tieba_browser is not None else None
        ),
    )
    app.router.add_event_handler(
        "shutdown", app.state.tieba_research_service.close
    )
    # V2 Issue 15：职业规划模块子图——检索同样复用唯一通用公网搜索服务，
    # 岗位页按公开 GET 有界读取；只把公开可读且岗位与城市都匹配的岗位纳入
    # 主样本，读不到页面时如实降级为「未核实链接」。
