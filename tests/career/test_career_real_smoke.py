"""Issue 12 可选真实 smoke：最小 Career 规划请求的真实 Qwen 锁审计。

仅在显式开关 ``BRIDGES_CAREER_REAL_SMOKE=1`` 且已配置安装级全局 Qwen
Key（``BRIDGES_QWEN_API_KEY`` / ``_FILE``）时执行；否则跳过（本机无
Key 时结果为不执行，不影响默认回归）。执行时：

- 使用 Issue 09 生产组合（真实 Qwen adapter），禁用 Stub/fixture/
  cassette/网络录制（cassette 已配置时跳过，不混用回放证据）；
- 完成一次最小规划请求（无画像、无联网证据），断言恰好一条
  ``career_generation:1`` 锁并关联账户/会话/助手消息/业务 run/固定模型；
- 进程重启语义：用同一 SQLite 文件新建 service/recorder 实例再次查询
  真实锁，验证持久化审计闭环。

修复分支（``career_repair:2``）不在此处覆盖：真实结构非法不可稳定触发，
也不允许以 fake 响应冒充；该分支由 fake 合同测试（test_career_service.py
/ test_career_model_run_locks.py）覆盖。

代码只判断凭据是否已配置（``is_global_qwen_key_configured``），绝不读取、
打印、哈希、回显或持久化 Key。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.production import build_production_composition
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.career.service import (
    CAREER_LOCK_OBJECT_TYPE,
    CAREER_OPERATION_GENERATION,
    CareerPlannerService,
)
from bridges.config import get_settings
from bridges.contracts.ai import ModelCallStatus
from bridges.contracts.career import CareerPlanningStatus
from bridges.contracts.workflows import RunContextEnvelope
from bridges.credentials.global_credential import is_global_qwen_key_configured
from bridges.learning import InMemoryLearningRepository, LearningService
from bridges.observability.service import ObservabilityService
from bridges.storage import BridgesDatabase

_REAL_SMOKE_ENV = "BRIDGES_CAREER_REAL_SMOKE"
_FIXED_STRUCTURED_MODEL = MODEL_BY_CAPABILITY["qwen_structured_output"]


def _real_smoke_requested() -> bool:
    return os.environ.get(_REAL_SMOKE_ENV, "").strip() == "1"


def _build_real_composition() -> Any:
    """生产组合（真实 adapter）；环境切到 production-like 后重建配置。"""
    previous_environment = os.environ.get("BRIDGES_ENVIRONMENT")
    os.environ["BRIDGES_ENVIRONMENT"] = "production"
    get_settings.cache_clear()
    try:
        settings = get_settings()
        return build_production_composition(settings)
    finally:
        if previous_environment is None:
            os.environ.pop("BRIDGES_ENVIRONMENT", None)
        else:
            os.environ["BRIDGES_ENVIRONMENT"] = previous_environment
        get_settings.cache_clear()


def _skip_reason() -> str | None:
    if not _real_smoke_requested():
        return (
            f"未显式开启 {_REAL_SMOKE_ENV}=1，跳过真实 smoke"
            "（默认回归保持离线确定性）。"
        )
    settings = get_settings()
    if not is_global_qwen_key_configured(settings):
        return "未配置安装级全局 Qwen Key（missing_global_qwen_key），真实 smoke 不可执行。"
    composition = _build_real_composition()
    if not composition.global_key_configured:
        return "生产组合未配置全局 Key，真实 smoke 不可执行。"
    if composition.cassette_enabled:
        return "检测到 cassette 目录配置，真实 smoke 禁用回放，跳过。"
    return None


@pytest.fixture
def real_composition() -> Any:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    return _build_real_composition()


def test_real_smoke_minimal_career_plan_records_real_generation_lock(
    tmp_path: Path, real_composition: Any
) -> None:
    """真实 Qwen 最小规划：恰好一条 career_generation:1 锁，重启后可查。"""
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    service = CareerPlannerService(
        gateway=real_composition.gateway,
        learning_service=LearningService(repository=InMemoryLearningRepository()),
        observability_service=ObservabilityService(),
        run_lock_recorder=SqliteModelRunLockRecorder(database),
    )
    run_context = RunContextEnvelope(
        run_id="career-real-smoke",
        account_id="smoke-account",
        project_id="smoke-project",
        workflow_name="career_real_smoke",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )
    events = list(
        service.run_task(
            "smoke-account",
            "smoke-conv",
            "smoke-message",
            "生涯规划：数据分析方向怎么安排",
            mode="companion",
            run_context=run_context,
            profile_enabled=False,
            profile_used=False,
            profile_items=[],
            web_search_projection=None,
            arxiv_search_projection=None,
        )
    )
    projection = events[-1].result
    assert projection is not None, "真实 smoke 必须产出结果投影"
    assert projection.status == CareerPlanningStatus.DONE
    assert projection.run_id == "career-real-smoke"
    assert len(projection.run_lock_refs) == 1, "正常生成必须恰好一条锁"
    ref = projection.run_lock_refs[0]
    assert ref.operation == CAREER_OPERATION_GENERATION
    assert ref.attempt_ordinal == 1

    # 重启语义：同一 SQLite 文件上的新实例查询真实锁
    restarted = SqliteModelRunLockRecorder(database)
    locks = restarted.list_locks_by_run("smoke-account", "career-real-smoke")
    assert len(locks) == 1
    lock = locks[0]
    assert lock.lock_id == ref.lock_id
    assert lock.status == ModelCallStatus.SUCCESS
    assert lock.capability_name == "qwen_structured_output"
    assert lock.actual_model_id == _FIXED_STRUCTURED_MODEL
    assert lock.account_id == "smoke-account"

    by_ref = restarted.list_locks_by_business_ref(
        "smoke-account", CAREER_LOCK_OBJECT_TYPE, "smoke-message"
    )
    assert len(by_ref) == 1
    assert by_ref[0].lock_id == ref.lock_id
    conv_refs = [
        item
        for item in by_ref[0].business_refs
        if item.object_type == "conversation" and item.object_id == "smoke-conv"
    ]
    assert len(conv_refs) == 1
