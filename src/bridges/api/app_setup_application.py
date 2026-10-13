"""按服务职责装配数据库依赖的应用服务。"""

from __future__ import annotations

from fastapi import FastAPI

from bridges.api.app_setup_career_resources import configure_career_resource_services
from bridges.api.app_setup_chat import configure_chat_services
from bridges.api.app_setup_executor import configure_generation_executor
from bridges.api.app_setup_knowledge import configure_knowledge_services
from bridges.api.app_setup_research import configure_research_services


def configure_application_services(app: FastAPI) -> None:
    app.state.tieba_browser_runtime = None
    configure_knowledge_services(app)
    configure_research_services(app)
    configure_career_resource_services(app)
    configure_chat_services(app)
    configure_generation_executor(app)
