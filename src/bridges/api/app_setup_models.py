from __future__ import annotations

import threading

from fastapi import FastAPI

from bridges.ai import StubQwenAdapter
from bridges.ai.production import build_production_composition
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.ai.startup_check import log_qwen_startup_connectivity_warnings
from bridges.ai.stream_script_fixture import ScriptedChatStreamAdapter
from bridges.api.app_support import (
    _profile_neighbor_reader,
)
from bridges.chat.turn import CHAT_CAPABILITY_NAME
from bridges.closeout.fixtures import CloseoutQwenAdapter
from bridges.profiles import (
    AutomaticProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    RuleBasedAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
)


def configure_model_services(app: FastAPI) -> None:
    profile_database = app.state.profile_database
    profile_conversation_repository = app.state.profile_conversation_repository
    # 注册表与 adapter 不再各自编写模型字面量。
    settings = app.state.settings
    # V2 Issue 09：运行配置（用户手填并验证通过的主模型 ID）由状态表承载，
    # 网关每次调用读取；注册表与生产组合门禁仍以出厂批准矩阵为准。
    run_model_config = RunModelConfigProvider(app.state.state_store)
    production_composition = build_production_composition(
        settings, model_config_provider=run_model_config
    )
    capability_registry = production_composition.registry
    model_gateway = production_composition.gateway
    app.state.run_model_config_provider = run_model_config
    # 设置页更换 Qwen 密钥时就地轮换这个共享客户端（Issue 09）。
    app.state.qwen_client = production_composition.qwen_client

    # Issue 03：启动连通性自检（非阻塞、仅告警）。test 环境（确定性
    # 适配器驱动）与未配置全局 Qwen Key 时不执行；其余环境在后台线程
    # 做 DNS 预检、代理环境变量可用性与 workspace 形态校验——异常只
    # 输出可操作警告日志，绝不阻断启动、不记录 Key 等敏感信息。
    if (
        settings is not None
        and settings.environment.lower() != "test"
        and production_composition.global_key_configured
    ):
        threading.Thread(
            target=log_qwen_startup_connectivity_warnings,
            args=(settings,),
            name="qwen-startup-connectivity-check",
            daemon=True,
        ).start()

    # Issue 41（AC3）：StubQwenAdapter 只注册在显式 test 环境（本地与 CI
    # 测试确定性，与 /_test/* 端点同一门控）——development/production
    # 配置绝不注册任何 Stub：真实模型能力未配置全局凭据时保持未绑定，由
    # 网关返回明确的"未绑定适配器"阻塞结果；内置 deterministic 工具能力
    # （领域包校验器）由领域包运行时直接执行。qwen_force_stub 环境开关
    # 已随 Issue 41 移除，任何环境都无法通过配置项开启生产假成功。
    app.state.test_chat_stream_script = None
    if settings is not None and settings.environment.lower() == "test":
        stub_adapter = (
            CloseoutQwenAdapter()
            if settings.closeout_fixture_mode
            else StubQwenAdapter()
        )
        # Issue 06：聊天能力包一层脚本化流式适配器，供浏览器生命周期验收
        # 经 /_test/chat-stream-script 注入 chunk 边界、延迟与停止/重连场景；
        # 未命中脚本时完全委托同一确定性替身。其余能力仍直接注册替身。
        for capability in capability_registry.list_active():
            if model_gateway.is_adapter_registered(capability.name, capability.version):
                continue
            if capability.name == CHAT_CAPABILITY_NAME:
                scripted_adapter = ScriptedChatStreamAdapter(stub_adapter)
                model_gateway.register_adapter(
                    capability.name, capability.version, scripted_adapter
                )
                app.state.test_chat_stream_script = scripted_adapter
            else:
                model_gateway.register_adapter(
                    capability.name, capability.version, stub_adapter
                )
    app.state.capability_registry = capability_registry
    app.state.model_gateway = model_gateway

    # Issue 15：消息持久化后、生成前执行默认画像预处理；测试环境使用确定性抽取器，
    # 其他环境使用固定版本的结构化画像能力，失败时由同一执行器驱动持久重试。
    # Issue 13：qwen_model 分支经 Issue 10 的 ModelRunLockRecorder 为每次真实
    # 调用持久化不可变运行锁；本地规则分支不产生任何锁。
    automatic_profile_repository = (
        SqliteAutomaticProfileRepository(profile_database)
        if profile_database is not None
        else InMemoryAutomaticProfileRepository()
    )
    app.state.four_dimension_profile_service.set_observation_delete_callback(
        automatic_profile_repository.delete_observations_for_record
    )
    automatic_profile_extractor = (
        RuleBasedAutomaticProfileExtractor()
        if settings is not None and settings.environment.lower() == "test"
        else GatewayAutomaticProfileExtractor(
            model_gateway,
            # 改进工单 03/04：画像提取调用与主对话/摘要共用运行配置额度快照
            # 与每次调用版本合同。
            model_config_provider=run_model_config,
            # 改进工单 17：必要邻近用户原文只用于解析回指。
            neighbor_reader=(
                _profile_neighbor_reader(profile_conversation_repository)
                if profile_conversation_repository is not None
                else None
            ),
        )
    )
    app.state.automatic_profile_service = AutomaticProfileService(
        four_dimension_service=app.state.four_dimension_profile_service,
        repository=automatic_profile_repository,
        extractor=automatic_profile_extractor,
        message_reader=(
            profile_conversation_repository.get_message
            if profile_conversation_repository is not None
            else None
        ),
        observability_service=app.state.observability_service,
        lock_recorder=(
            SqliteModelRunLockRecorder(profile_database)
            if profile_database is not None
            else None
        ),
        atomic_profile_service=app.state.atomic_profile_service,
    )
    # Issue 02：跨重启恢复是显式启动动作，构造不再启动业务执行；
    # 恢复时刻与外部可见行为（启动时重新入队中断的抽取）保持不变。
    app.state.automatic_profile_service.recover_inflight_runs()

    # Issue 11: 持久化流式聊天纵向切片。对话/消息/运行锁写入 bridges.db；
    # 未配置持久化数据目录（内存模式，仅测试/E2E）时不挂载聊天服务，
    # 路由返回"对话存储未启用"，绝不静默降级到内存。
