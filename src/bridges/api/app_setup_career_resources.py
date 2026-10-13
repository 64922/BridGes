from __future__ import annotations

from fastapi import FastAPI

from bridges.api.app_support import (
    CareerBackgroundLoader,
    _current_task_reference,
    _github_api_client,
    _resource_metadata_client,
)
from bridges.career_plan.collecting import HttpJobPageReader
from bridges.career_plan.searching import WebSearchServiceAdapter as CareerWebSearchAdapter
from bridges.career_plan.service import CareerPlanService
from bridges.github.client import GithubApiClient
from bridges.github.inspecting import GithubRepositoryReader
from bridges.github.presenting import GithubInsightGenerator
from bridges.github.searching import GithubApiSearchAdapter
from bridges.github.service import GithubProjectsService
from bridges.resources.reading import OpenLibraryBookInsightReader
from bridges.resources.service import LearningResourcesService
from bridges.resources.sources import (
    BilibiliVideoDiscoverer,
    BilibiliVideoVerifier,
    OpenAlexBookSource,
    OpenLibraryBookSource,
)


def configure_career_resource_services(app: FastAPI) -> None:
    model_gateway = app.state.model_gateway
    use_closeout_fixtures = app.state.use_closeout_fixtures
    if app.state.bridges_database is None:
        return
    app.state.career_plan_service = CareerPlanService(
        search=CareerWebSearchAdapter(app.state.web_search_service),
        reader=HttpJobPageReader(),
        task_version_provider=lambda account_id, conversation_id: (
            _current_task_reference(app, account_id, conversation_id)
        ),
        # 改进工单 29：个人规划分支只经登记提供者读取允许使用的画像切片；
        # 只查岗位分支不会触发该提供者，背景正文也不进入公开检索。
        background_provider=CareerBackgroundLoader(app),
    )
    app.router.add_event_handler("shutdown", app.state.career_plan_service.close)
    # V2 Issue 16：GitHub 项目推荐模块子图——公开仓库检索与证据读取都走
    # GitHub 官方只读 REST API（有限缓存、额度退避与脱敏披露审计），
    # README 只当项目自述，实现文件证据来自实际读取到的路径与内容。
    app.state.github_api_client = GithubApiClient(
        client=_github_api_client(),
        observability=app.state.observability_service,
    )
    app.state.github_projects_service = GithubProjectsService(
        search=GithubApiSearchAdapter(app.state.github_api_client),
        reader=GithubRepositoryReader(app.state.github_api_client),
        insights=GithubInsightGenerator(model_gateway),
        task_version_provider=lambda account_id, conversation_id: (
            _current_task_reference(app, account_id, conversation_id)
        ),
    )
    app.router.add_event_handler(
        "shutdown", app.state.github_api_client.close
    )
    # V2 Issue 13 / 工单 25：学习资料推荐模块子图——图书书目（Open Library
    # 为主、OpenAlex 有限补充）与哔哩哔哩视频（公网搜索发现后逐条核对公开
    # 元数据）；图书按需读取作品页目录/简介作为覆盖证据。收尾夹具模式不装配
    # 外部来源，两条检索如实标注缺口。
    if use_closeout_fixtures:
        app.state.learning_resources_service = LearningResourcesService()
    else:
        app.state.learning_resources_service = LearningResourcesService(
            books=[
                OpenLibraryBookSource(
                    client=_resource_metadata_client(),
                    observability=app.state.observability_service,
                ),
                OpenAlexBookSource(
                    client=_resource_metadata_client(),
                    observability=app.state.observability_service,
                ),
            ],
            discoverer=BilibiliVideoDiscoverer(
                getattr(app.state, "web_search_service", None)
            ),
            verifier=BilibiliVideoVerifier(
                client=_resource_metadata_client(),
                observability=app.state.observability_service,
            ),
            insight_reader=OpenLibraryBookInsightReader(
                client=_resource_metadata_client(),
                observability=app.state.observability_service,
            ),
            task_version_provider=lambda account_id, conversation_id: (
                _current_task_reference(app, account_id, conversation_id)
            ),
        )
    app.router.add_event_handler(
        "shutdown", app.state.learning_resources_service.close
    )
