from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status

from bridges.ai.stream_script_fixture import ScriptedChatStreamAdapter
from bridges.api import (
    auth,
    chat,
    compatibility,
    credentials,
    domain_packs,
    evaluation,
    expression,
    ingestion,
    institution,
    knowledge_base,
    learning_project_migration,
    learning_projects,
    model_settings,
    projects,
    science,
    scope,
    search,
    sharing,
    skills,
    sync,
    tasks,
    vault,
    workflows,
)
from bridges.api import commute as commute_api
from bridges.api import tieba as tieba_compatibility_api
from bridges.api.data import router as data_router
from bridges.api.image import router as image_router
from bridges.api.mcp import router as mcp_router
from bridges.api.media import router as media_router
from bridges.api.plugins import router as plugins_router
from bridges.api.reminder import router as reminder_router
from bridges.api.speech import router as speech_router
from bridges.api.video import router as video_router
from bridges.contracts.workflows import RunProjection
from bridges.identity import IdentityService
from bridges.learning.api import router as learning_router
from bridges.profiles.api import router as profiles_router
from bridges.profiles.legacy_api import router as legacy_profiles_router
from bridges.workflows import WorkflowError, WorkflowService


def register_routes(app: FastAPI) -> None:
    settings = app.state.settings

    app.include_router(auth.router)
    app.include_router(credentials.router)
    app.include_router(commute_api.router)
    app.include_router(model_settings.router)
    app.include_router(compatibility.router)
    app.include_router(tieba_compatibility_api.router)
    app.include_router(chat.router)
    app.include_router(ingestion.router)
    app.include_router(knowledge_base.router)
    app.include_router(learning_project_migration.router)
    app.include_router(learning_projects.router)
    app.include_router(search.router)
    app.include_router(domain_packs.router)
    app.include_router(projects.router)
    app.include_router(vault.router)
    app.include_router(sharing.router)
    app.include_router(sync.router)
    app.include_router(institution.router)
    app.include_router(profiles_router)
    app.include_router(legacy_profiles_router)
    app.include_router(workflows.router)
    app.include_router(scope.router)
    app.include_router(evaluation.router)
    app.include_router(science.router)
    app.include_router(expression.router)
    app.include_router(media_router)
    app.include_router(learning_router)
    app.include_router(speech_router)
    app.include_router(image_router)
    app.include_router(video_router)
    app.include_router(reminder_router)
    app.include_router(plugins_router)
    app.include_router(mcp_router)
    app.include_router(skills.router)
    app.include_router(data_router)
    # 改进工单 08：跨轮任务投影与关系落地入口。
    app.include_router(tasks.router)

    @app.get("/me", response_model=dict[str, Any])
    async def me(subject: auth.SubjectDep) -> dict[str, Any]:
        """Protected demo route: returns the resolved subject.

        T003 uses this route to prove that API endpoints share the same
        authentication guard as the Web frontend.
        """
        return {"account_id": subject.account_id, "session_id": subject.session_id}

    if settings is not None and settings.environment.lower() == "test":
        # Issue 41（AC3/AC10）：测试专用端点只在 test 环境注册，生产配置不
        # 暴露任何 `/_test/` 路由。
        @app.get("/_test/recovery-token", response_model=dict[str, Any])
        async def test_recovery_token(qq_email: str) -> dict[str, Any]:
            """Test-only endpoint to retrieve a recovery token without email delivery.

            This endpoint is prefixed with `/_test/` and is only safe because the
            identity service is local. It must not be exposed in production.
            """
            from bridges.identity import IdentityError

            service: IdentityService = app.state.identity_service
            try:
                token = service.test_create_recovery_token(qq_email)
            except IdentityError as exc:
                return {"error": str(exc)}
            return {"token": token}

        @app.post("/_test/chat-stream-script", response_model=dict[str, Any])
        async def test_configure_chat_stream_script(
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            """Test-only endpoint to script deterministic chat streaming.

            Issue 06：E2E 用它注入分块正文与块间延迟，经真实 API、执行器和
            SSE 链路驱动浏览器；未命中脚本的回合仍走确定性替身。仅在显式
            test 环境注册（与其他 ``/_test/`` 端点同一门控）。
            """
            adapter: ScriptedChatStreamAdapter | None = getattr(
                app.state, "test_chat_stream_script", None
            )
            if adapter is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"error": "chat_stream_script_unavailable"},
                )
            try:
                adapter.configure(
                    match=str(payload.get("match", "")),
                    chunks=[str(chunk) for chunk in payload.get("chunks", [])],
                    delay_ms=int(payload.get("delay_ms", 0)),
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "invalid_chat_stream_script",
                        "message": str(exc),
                    },
                ) from exc
            return {"configured": True, "match": payload.get("match", "")}

    def _get_workflow_service(request: Request) -> WorkflowService:
        service: WorkflowService | None = getattr(request.app.state, "workflow_service", None)
        if service is None:
            raise RuntimeError("WorkflowService not attached to application state.")
        return service

    if settings is not None and settings.environment.lower() == "test":
        @app.post("/_test/runs/{run_id}/advance", response_model=RunProjection)
        async def test_advance_run(
            run_id: str,
            subject: auth.SubjectDep,
            service: WorkflowService = Depends(_get_workflow_service),  # noqa: B008
        ) -> RunProjection:
            """Test-only endpoint to deterministically advance a run by one node."""
            try:
                return service.advance_run(subject.account_id, run_id)
            except WorkflowError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": "workflow_transition_failed", "message": str(exc)},
                ) from exc
