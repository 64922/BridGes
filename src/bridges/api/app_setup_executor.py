from __future__ import annotations

import os
import threading

from fastapi import FastAPI

from bridges.chat import ConversationRepository
from bridges.chat.run_executor import GenerationRunExecutor
from bridges.speech.service import SpeechService


def configure_generation_executor(app: FastAPI) -> None:
    bridges_database = app.state.bridges_database
    model_gateway = app.state.model_gateway
    object_repository = app.state.object_repository
    if app.state.bridges_database is None:
        return
    app.state.generation_executor = GenerationRunExecutor(
        app.state.chat_service,
        bridges_database,
        profile_extraction_service=app.state.automatic_profile_service,
        summary_service=app.state.chat_summary_service,
    )
    app.state.generation_executor_stop = threading.Event()

    def _start_generation_executor() -> None:
        environment = (
            app.state.settings.environment
            if app.state.settings is not None
            else "production"
        )
        # test 环境（确定性适配器驱动）默认不自动启动执行器线程，由
        # pytest 显式驱动 run_tick 或自行启动线程，避免与同步消费生成
        # 器的既有测试竞态；e2e（真实部署形态）经环境变量显式启用。
        force_executor = (
            os.environ.get("BRIDGES_GENERATION_EXECUTOR", "").strip().lower()
            in {"1", "true", "yes"}
        )
        if environment.lower() == "test" and not force_executor:
            return
        thread = threading.Thread(
            target=app.state.generation_executor.run_loop,
            kwargs={
                "stop": app.state.generation_executor_stop,
                "emit": lambda line: None,  # noqa: E731 - 执行器心跳静默
            },
            name="generation-executor",
            daemon=True,
        )
        thread.start()
        app.state.generation_executor_thread = thread

    def _stop_generation_executor() -> None:
        app.state.generation_executor_stop.set()
        thread = getattr(app.state, "generation_executor_thread", None)
        if thread is not None:
            thread.join(timeout=30)

    app.router.add_event_handler("startup", _start_generation_executor)
    app.router.add_event_handler("shutdown", _stop_generation_executor)
    # Issue 30: 听写与单条回答朗读（固定 ASR/TTS 快照）。复用同一
    # 模型网关（固定模型标识进运行记录）、账户对象库（朗读音频按
    # 账户隔离留存）与对话仓库；听写音频不落盘，失败只重试同一快照。
    app.state.speech_service = SpeechService(
        gateway=model_gateway,
        object_repository=object_repository,
        chat_repository=ConversationRepository(bridges_database),
        observability_service=app.state.observability_service,
    )
