"""FastAPI 应用组合根；各生命周期阶段由专职模块装配。"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from bridges.api.app_foundation import build_base_app
from bridges.api.app_health import register_health
from bridges.api.app_routes import register_routes
from bridges.api.app_setup_application import configure_application_services
from bridges.api.app_setup_closeout import configure_closeout_services
from bridges.api.app_setup_core import configure_core_services
from bridges.api.app_setup_models import configure_model_services
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.credentials.store import CredentialStorePort
from bridges.persistence import StateStore


def create_app(
    state_store: StateStore | None = None,
    *,
    runtime_credential_store: CredentialStorePort | None = None,
    credential_probe_http_client: httpx.Client | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = build_base_app(
        state_store,
        runtime_credential_store=runtime_credential_store,
        credential_probe_http_client=credential_probe_http_client,
    )
    register_health(app)
    configure_core_services(app, arxiv_service_factory=ArxivSearchService)
    configure_model_services(app)
    configure_application_services(app)
    configure_closeout_services(app)
    register_routes(app)
    return app
