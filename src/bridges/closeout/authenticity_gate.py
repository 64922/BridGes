"""Issue 17：全功能 Qwen 真实性发布门（production-composition 门）。

门禁把 Issue 09–16 的局部保证组合成一次可重复、失败关闭、可供发布
负责人复核的全功能证据：

1. **能力清单完整性**（``manifest``）：公开 API 路由、聊天动作与生产
   服务必须全部被 ``PRODUCTION_CAPABILITY_MANIFEST`` 唯一分类；发现
   未分类能力、retired 误覆盖活跃路由、退役能力复活时失败关闭。
2. **静态与组合检查**（``scans`` + ``composition``）：业务服务直连
   Qwen 客户端、散落模型字面量、外部检索继承 Qwen Key、Stub/确定性
   替身进入生产接线均失败并点名 capability 与调用点；每个
   ``qwen_model`` 能力绑定真实 adapter 且模型来自批准矩阵。
3. **审计接缝检查**：每次真实 Qwen/百炼调用必须产生并持久化独立运行
   锁；一次业务操作的多次调用不得折叠成一条假锁（live suite 逐能力
   探针 + 重启后复查）。
4. **受控 live suite**（仅 ``--real-probes`` 显式 opt-in）：使用同一个
   安装级全局 Key，对各类活跃模型能力执行最小真实探针，探针使用专用
   业务 run（``release-probe`` 账户域），随后重启数据库复查锁证据。
5. **脱敏报告与非零退出码**：报告只含 build、capability、类别、
   provider、固定 model ID、status、latency、lock ID 与脱敏错误码；
   任何失败/未证实都使命令非零退出，绝不静默跳过失败能力。

本模块不读取、打印、哈希、回显或持久化 Key 内容；probe 只判断凭据
"已配置/未配置"（``is_global_qwen_key_configured``）。live suite 需要
真实凭据时，把 ``settings.qwen_api_key`` 的引用交给批准组合的端口/
adapter 使用，门禁自身不接触 Key 值。
"""

from __future__ import annotations

import base64
import contextlib
import io
import math
import os
import struct
import time
import types
import wave
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.composition import (
    MISSING_GLOBAL_QWEN_KEY,
    PRODUCTION_TEST_ADAPTER,
    validate_production_composition,
)
from bridges.ai.fixed_models import MODEL_BY_CAPABILITY
from bridges.ai.ports import EmbeddingContext, EmbeddingOperation
from bridges.ai.production import (
    ProductionComposition,
    build_production_composition,
)
from bridges.closeout.manifest import (
    CAPABILITY_MANIFEST_VERSION,
    DIRECT_CLIENT_BYPASS,
    MISSING_ADAPTER,
    MISSING_RUN_LOCK,
    PRODUCTION_CAPABILITY_MANIFEST,
    PRODUCTION_STUB,
    CapabilityCategory,
    CapabilityManifestEntry,
    check_manifest_completeness,
    discover_api_routes,
)
from bridges.closeout.scans import run_all_scans
from bridges.config import Settings, get_settings
from bridges.contracts.ai import BusinessRef, ModelCallStatus, ModelRunLock
from bridges.contracts.workflows import RunContextEnvelope
from bridges.ingestion.embedding import QwenEmbeddingPort
from bridges.storage.database import BridgesDatabase

#: live suite 使用的专用探针账户（不混入普通用户历史；锁作为发布证据保留）。
PROBE_ACCOUNT_ID = "release-gate-probe"
#: live suite 探针 run 前缀（每次运行附加唯一 token）。
PROBE_RUN_PREFIX = "release-gate-authn"
#: 探针业务对象类型（model_run_lock_links.object_type）。
PROBE_OBJECT_TYPE = "release_probe"

#: 门禁自身稳定的额外错误码。
CANCEL_PROBE_UNAVAILABLE = "cancel_probe_unavailable"
LIVE_PROBE_FAILED = "live_probe_failed"
LIVE_PROBE_INCONCLUSIVE = "live_probe_inconclusive"
LOCK_VERIFICATION_FAILED = "lock_verification_failed"
RETIRED_CONTRACT_FAILED = "retired_contract_failed"
LOCAL_JOURNEY_MODEL_CALL = "local_journey_model_call"
EXTERNAL_PROVIDER_FAILED = "external_provider_failed"

#: 退役契约探针：每条 retired 路由模式的代表请求（方法, 路径）。
#: 路径中的 ``*`` 由门禁替换为示例参数；全部返回 410 + 稳定错误码。
RETIRED_PROBE_SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/expression/drafts", "legacy_expression_retired"),
    ("POST", "/expression/drafts/*/publish", "legacy_expression_retired"),
    ("POST", "/media/publish", "legacy_media_retired"),
    ("POST", "/media/storyboards", "legacy_media_retired"),
    ("GET", "/reminders", "reminders_retired"),
    ("GET", "/reminders/smtp", "reminders_retired"),
    ("POST", "/plugins", "user_extensions_retired"),
    ("POST", "/mcp", "user_extensions_retired"),
    ("GET", "/learning/review-tasks/*", "learning_review_retired"),
)


def _probe_category_provider(capability: str) -> tuple[str, str]:
    """按清单返回 (类别, provider)；未知能力回退到 qwen_model/qwen。"""
    from bridges.closeout.manifest import CapabilityCategory as _Category

    for entry in PRODUCTION_CAPABILITY_MANIFEST:
        if entry.model_capability == capability:
            provider = "wan" if capability == "qwen_wan" else "qwen"
            return entry.category.value, provider
    return _Category.QWEN_MODEL.value, "qwen"


@dataclass(frozen=True)
class LiveProbeResult:
    """一次真实能力探针的脱敏结果（只含报告字段，无正文）。"""

    capability: str
    status: str  # passed / failed / inconclusive
    approved_model_id: str
    category: str = ""  # qwen_model / external_non_qwen（清单类别）
    provider: str = ""  # qwen / wan / external
    actual_model_id: str | None = None
    latency_ms: int = 0
    lock_ids: tuple[str, ...] = ()
    expected_locks: int = 0
    error_code: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        # 未显式给定类别/提供方时按能力名从清单推导（外部提供方探针显式给定）。
        if not self.category:
            category, provider = _probe_category_provider(self.capability)
            object.__setattr__(self, "category", category)
            object.__setattr__(self, "provider", provider)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "category": self.category,
            "provider": self.provider,
            "approved_model_id": self.approved_model_id,
            "actual_model_id": self.actual_model_id,
            "latency_ms": self.latency_ms,
            "lock_ids": list(self.lock_ids),
            "expected_locks": self.expected_locks,
            "error_code": self.error_code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class GateViolation:
    """门禁一级违规：稳定错误码 + 目标 + 脱敏说明。"""

    code: str
    target: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "target": self.target, "detail": self.detail}


def _latency_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _code_version() -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _probe_run_id(capability: str, token: str) -> str:
    return f"{PROBE_RUN_PREFIX}:{token}:{capability}"


def _run_context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id=PROBE_ACCOUNT_ID,
        project_id="release-gate",
        workflow_name="release_gate_authenticity",
        workflow_version=str(CAPABILITY_MANIFEST_VERSION),
        submitted_at=datetime.now(UTC),
    )


def _business_ref(run_id: str, operation: str, attempt_ordinal: int = 1) -> BusinessRef:
    return BusinessRef(
        object_type=PROBE_OBJECT_TYPE,
        object_id=run_id,
        operation=operation,
        attempt_ordinal=attempt_ordinal,
        is_primary=True,
    )


# ---------------------------------------------------------------------------
# 1. 确定性检查
# ---------------------------------------------------------------------------


def check_manifest(routes: Sequence[Any], registry: CapabilityRegistry) -> list[GateViolation]:
    """能力清单完整性：未分类/重复/retired 复活均失败。"""
    violations = check_manifest_completeness(routes, registry)
    return [
        GateViolation(v.code, v.target, v.detail)
        for v in violations
    ]


def check_static_scans() -> list[GateViolation]:
    """静态扫描：直连 bypass/模型字面量/外部 Key 继承/确定性替身。"""
    return [
        GateViolation(v.code, f"{v.path}:{v.line}", v.target)
        for v in run_all_scans()
    ]


def check_production_composition(
    composition: ProductionComposition,
    *,
    manifest: tuple[CapabilityManifestEntry, ...] = PRODUCTION_CAPABILITY_MANIFEST,
) -> list[GateViolation]:
    """按清单类别核对生产组合：

    - 每个 ``qwen_model`` 能力必须注册在批准矩阵中且绑定真实 adapter
      （复用 Issue 09 校验器：漂移/Stub/cassette/missing 一律失败）；
    - 清单声明的 model capability 未注册 → ``missing_adapter`` 点名；
    - 退役能力名复活由清单检查（``check_manifest`` → ``check_retired_registry``）
      负责，本函数只核对活跃 qwen_model 接线。
    """
    violations: list[GateViolation] = []
    registry = composition.registry
    for entry in manifest:
        if entry.category != CapabilityCategory.QWEN_MODEL:
            continue
        assert entry.model_capability is not None
        try:
            registry.get(entry.model_capability, "1")
        except Exception:  # noqa: BLE001 - 未注册即缺 adapter
            violations.append(
                GateViolation(
                    MISSING_ADAPTER,
                    entry.model_capability,
                    f"qwen_model 能力 {entry.id} 的 model capability 未注册，"
                    "生产组合缺失真实接线。",
                )
            )

    composition_violations = validate_production_composition(
        registry,
        composition.gateway,
        global_key_configured=composition.global_key_configured,
        cassette_enabled=composition.cassette_enabled,
        vision_ocr_compatibility_required=False,
    )
    for violation in composition_violations:
        code = violation.code
        if code == PRODUCTION_TEST_ADAPTER:
            code = PRODUCTION_STUB
        violations.append(GateViolation(code, violation.capability, violation.detail))
    return violations


def check_retired_contract(app: Any) -> list[GateViolation]:
    """退役契约探针：代表性 retired 路由必须稳定返回 410 与稳定错误码。

    使用 TestClient 对本应用进程内请求（无网络）；退役处理器在鉴权
    依赖之前/之后均会抛出 410，因此探针以 ``require_subject`` 覆盖注入
    门禁专用主体，确保请求到达 410 判定点。任何非 410（或缺失稳定错误
    码）都使门禁以 ``retired_contract_failed`` 失败关闭。
    """
    from fastapi.testclient import TestClient

    from bridges.api.auth import require_subject
    from bridges.contracts.identity import AuthMethod, SubjectContext

    def _gate_subject() -> SubjectContext:
        return SubjectContext(
            account_id=PROBE_ACCOUNT_ID,
            session_id="gate-retired-probe",
            auth_method=AuthMethod.SERVICE,
        )

    app.dependency_overrides[require_subject] = _gate_subject
    violations: list[GateViolation] = []
    try:
        with TestClient(app) as client:
            for method, path_template, expected_code in RETIRED_PROBE_SAMPLES:
                path = path_template.replace("*", "probe-item-1")
                try:
                    response = client.request(method, path)
                except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
                    violations.append(
                        GateViolation(
                            RETIRED_CONTRACT_FAILED,
                            f"{method} {path}",
                            f"退役路由探针异常（{exc.__class__.__name__.lower()}），未确认 410。",
                        )
                    )
                    continue
                if response.status_code != 410:
                    violations.append(
                        GateViolation(
                            RETIRED_CONTRACT_FAILED,
                            f"{method} {path}",
                            f"退役路由应返回 410，实际 {response.status_code}。",
                        )
                    )
                    continue
                body = response.json() if response.content else {}
                detail = body.get("detail") if isinstance(body, dict) else None
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(detail, dict):
                    error = error or detail.get("error")
                if expected_code and error != expected_code:
                    violations.append(
                        GateViolation(
                            RETIRED_CONTRACT_FAILED,
                            f"{method} {path}",
                            f"退役错误码漂移：期望 {expected_code}，实际 {error or 'missing'}。",
                        )
                    )
                    continue
                # Test plan 5：410 响应必须携带中文迁移说明（message 非空）。
                message = body.get("message") if isinstance(body, dict) else None
                if isinstance(detail, dict):
                    message = message or detail.get("message")
                if not message or not any("\u4e00" <= ch <= "\u9fff" for ch in str(message)):
                    violations.append(
                        GateViolation(
                            RETIRED_CONTRACT_FAILED,
                            f"{method} {path}",
                            "退役响应缺少中文迁移说明。",
                        )
                    )
    finally:
        app.dependency_overrides.pop(require_subject, None)
    return violations


def run_local_journey_spy_probes(app: Any, database: BridgesDatabase) -> list[GateViolation]:
    """代表性本地旅程探针：登录/账户、会话列表、画像编辑、材料 CRUD、
    关键词检索不得调用模型或生成运行锁。

    对生产组合网关注入计数 spy（``invoke``/``stream`` 包装），驱动真实
    API 请求（test 环境确定性 adapter 装配），随后核对：
    - 网关调用增量必须为 0（``local_journey_model_call``）；
    - 探针账户的 ``model_run_locks`` 行数必须为 0（``missing_run_lock``
      的反向：本地旅程不得伪造锁）。
    """
    from fastapi.testclient import TestClient

    gateway = getattr(app.state, "model_gateway", None)
    if gateway is None:
        return [
            GateViolation(
                DIRECT_CLIENT_BYPASS,
                "local-journey-probe",
                "应用未装配 model_gateway，本地旅程探针无法计数。",
            )
        ]

    call_count = {"invoke": 0, "stream": 0}
    original_invoke = gateway.invoke
    original_stream = gateway.stream

    def _counting_invoke(*args: Any, **kwargs: Any) -> Any:
        call_count["invoke"] += 1
        return original_invoke(*args, **kwargs)

    def _counting_stream(*args: Any, **kwargs: Any) -> Any:
        call_count["stream"] += 1
        return original_stream(*args, **kwargs)

    gateway.invoke = types.MethodType(_counting_invoke, gateway)
    gateway.stream = types.MethodType(_counting_stream, gateway)

    violations: list[GateViolation] = []
    account_id: str | None = None
    qq_email = f"{int(time.time() * 1000) % 10**10}@qq.com"
    try:
        with TestClient(app) as client:
            # 1. 登录/账户：注册 + 会话查询
            register = client.post(
                "/auth/register",
                json={
                    "username": "gate-probe-user",
                    "qq_email": qq_email,
                    "password": "release-gate-probe-password-17",
                },
            )
            if register.status_code not in {200, 201}:
                violations.append(
                    GateViolation(
                        LOCAL_JOURNEY_MODEL_CALL,
                        "POST /auth/register",
                        f"注册探针未按预期成功（{register.status_code}），本地旅程无法执行。",
                    )
                )
            else:
                account_id = (register.json() or {}).get("account", {}).get("id")
                # 2. 会话列表
                conversations = client.get("/chat/conversations")
                if conversations.status_code not in {200, 201}:
                    violations.append(
                        GateViolation(
                            LOCAL_JOURNEY_MODEL_CALL,
                            "GET /chat/conversations",
                            f"会话列表探针未按预期成功（{conversations.status_code}）。",
                        )
                    )
                # 3. 关键词检索（空查询也走统一搜索路径）
                search = client.get("/search", params={"q": "Transformer"})
                if search.status_code not in {200, 201}:
                    violations.append(
                        GateViolation(
                            LOCAL_JOURNEY_MODEL_CALL,
                            "GET /search",
                            f"关键词检索探针未按预期成功（{search.status_code}）。",
                        )
                    )
                # 4. 画像管理（四维画像读取/状态；编辑路由以不存在记录触发
                #    404/422 也属于真实路由调用，同样不得调用模型）
                profile_status = client.get("/profiles/status")
                if profile_status.status_code not in {200, 201}:
                    violations.append(
                        GateViolation(
                            LOCAL_JOURNEY_MODEL_CALL,
                            "GET /profiles/status",
                            f"画像状态探针未按预期成功（{profile_status.status_code}）。",
                        )
                    )
                profile_edit = client.patch(
                    "/profiles/four-dimensions/probe-missing-record",
                    json={"value": "探针编辑值"},
                )
                if profile_edit.status_code not in {200, 201, 204, 404, 422}:
                    violations.append(
                        GateViolation(
                            LOCAL_JOURNEY_MODEL_CALL,
                            "PATCH /profiles/four-dimensions/{record_id}",
                            f"画像编辑探针异常（{profile_edit.status_code}）。",
                        )
                    )
                # 5. 材料 CRUD（知识库材料列表）
                materials = client.get("/knowledge-base/materials")
                if materials.status_code not in {200, 201}:
                    violations.append(
                        GateViolation(
                            LOCAL_JOURNEY_MODEL_CALL,
                            "GET /knowledge-base/materials",
                            f"材料 CRUD 探针未按预期成功（{materials.status_code}）。",
                        )
                    )
    finally:
        gateway.invoke = original_invoke
        gateway.stream = original_stream

    total_calls = call_count["invoke"] + call_count["stream"]
    if total_calls != 0:
        violations.append(
            GateViolation(
                LOCAL_JOURNEY_MODEL_CALL,
                "local-deterministic-journeys",
                f"本地旅程产生了 {total_calls} 次模型网关调用（invoke {call_count['invoke']}，"
                f"stream {call_count['stream']}），local_deterministic 能力不得消费模型。",
            )
        )
    if account_id is not None:
        try:
            rows = database.scoped(account_id).execute(
                "SELECT COUNT(*) AS n FROM model_run_locks WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            lock_count = int(rows["n"]) if rows is not None else 0
        except Exception:  # noqa: BLE001 - 表缺失等环境问题按失败关闭
            lock_count = -1
        if lock_count != 0:
            violations.append(
                GateViolation(
                    MISSING_RUN_LOCK,
                    "local-deterministic-journeys",
                    f"本地旅程生成了 {lock_count} 条模型运行锁，本地能力不得伪造锁。",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# 2. live suite：真实能力探针
# ---------------------------------------------------------------------------


def _known_text_png_bytes() -> bytes:
    """生成含已知文字的最小 PNG（PyMuPDF，无网络依赖）。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    marker_text = "BRIDGES-QWEN-2026"
    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_text((40, 115), marker_text, fontsize=40, fontname="helv")
    pixmap = page.get_pixmap(dpi=144)
    return bytes(pixmap.tobytes("png"))


def _tone_wav_bytes(duration_seconds: float = 1.0, sample_rate: int = 8000) -> bytes:
    """生成 1 秒正弦单音 WAV（stdlib，无网络依赖）。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(duration_seconds * sample_rate)):
            value = int(32767 * 0.25 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        wav.writeframes(bytes(frames))
    return buffer.getvalue()


def _invoke_and_record(
    gateway: Any,
    recorder: Any,
    capability: str,
    payload: dict[str, Any],
    run_id: str,
    operation: str,
    expected_locks: int = 1,
) -> LiveProbeResult:
    """执行一次网关调用并立即持久化返回锁；锁缺失即 ``missing_run_lock``。"""
    started = time.monotonic()
    approved = MODEL_BY_CAPABILITY[capability]
    try:
        result = gateway.invoke(capability, "1", _run_context(run_id), payload)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=expected_locks,
            error_code=exc.__class__.__name__.lower(),
            detail="探针调用异常。",
        )
    latency_ms = _latency_ms(started)
    lock = result.lock
    if lock is None:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=None,
            latency_ms=latency_ms,
            expected_locks=expected_locks,
            error_code=MISSING_RUN_LOCK,
            detail="网关调用未产生不可变运行锁。",
        )
    lock_ids: list[str] = []
    try:
        persisted = recorder.record(
            lock,
            business_ref=_business_ref(run_id, operation, attempt_ordinal=1),
        )
        lock_ids.append(persisted.lock_id)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=lock.actual_model_id,
            latency_ms=latency_ms,
            lock_ids=(lock.lock_id,),
            expected_locks=expected_locks,
            error_code=exc.__class__.__name__.lower(),
            detail="运行锁持久化失败。",
        )
    if result.status != ModelCallStatus.SUCCESS:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=lock.actual_model_id,
            latency_ms=latency_ms,
            lock_ids=tuple(lock_ids),
            expected_locks=expected_locks,
            error_code=result.error_code or LIVE_PROBE_FAILED,
            detail="供应商调用未成功。",
        )
    return LiveProbeResult(
        capability=capability,
        status="passed",
        approved_model_id=approved,
        actual_model_id=lock.actual_model_id,
        latency_ms=latency_ms,
        lock_ids=tuple(lock_ids),
        expected_locks=expected_locks,
    )


def _probe_submit_cancel(
    gateway: Any,
    recorder: Any,
    capability: str,
    submit_payload: dict[str, Any],
    token: str,
    operation_prefix: str,
    *,
    global_key_configured: bool,
) -> LiveProbeResult:
    """提交 → 取云任务 ID → 立即取消：每次真实供应商动作各落一条锁。

    取消只在拿到真实 ``cloud_task_id`` 后发起；拿不到（任务未上云/已
    终态）时以 ``cancel_probe_unavailable`` 失败关闭，绝不伪造取消成功。
    """
    started = time.monotonic()
    approved = MODEL_BY_CAPABILITY[capability]
    if not global_key_configured:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            expected_locks=2,
            error_code=MISSING_GLOBAL_QWEN_KEY,
            detail="未配置安装级全局 Qwen Key，提交/取消探针无法执行。",
        )
    run_id = _probe_run_id(capability, token)
    lock_ids: list[str] = []
    try:
        result = gateway.invoke(capability, "1", _run_context(run_id), submit_payload)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=2,
            error_code=exc.__class__.__name__.lower(),
            detail="提交探针异常。",
        )
    submit_lock = result.lock
    if submit_lock is None or result.status != ModelCallStatus.SUCCESS:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=submit_lock.actual_model_id if submit_lock else None,
            latency_ms=_latency_ms(started),
            lock_ids=(submit_lock.lock_id,) if submit_lock else (),
            expected_locks=2,
            error_code=(
                result.error_code
                if result.status != ModelCallStatus.SUCCESS
                else MISSING_RUN_LOCK
            ),
            detail="提交调用未成功或未产生锁。",
        )
    try:
        persisted = recorder.record(
            submit_lock,
            business_ref=_business_ref(run_id, f"release_gate:{operation_prefix}:submit"),
        )
        lock_ids.append(persisted.lock_id)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=submit_lock.actual_model_id,
            latency_ms=_latency_ms(started),
            lock_ids=(submit_lock.lock_id,),
            expected_locks=2,
            error_code=exc.__class__.__name__.lower(),
            detail="提交锁持久化失败。",
        )

    output = result.output if isinstance(result.output, dict) else {}
    cloud_task_id = str(output.get("cloud_task_id") or "") if output else ""
    if not cloud_task_id:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=submit_lock.actual_model_id,
            latency_ms=_latency_ms(started),
            lock_ids=tuple(lock_ids),
            expected_locks=2,
            error_code=CANCEL_PROBE_UNAVAILABLE,
            detail="提交成功但没有可安全取消的云任务 ID，取消真实性无法证明，失败关闭。",
        )
    cancel_payload: dict[str, Any] = {"kind": "cancel", "cloud_task_id": cloud_task_id}
    cancel_result = gateway.invoke(capability, "1", _run_context(run_id), cancel_payload)
    cancel_lock = cancel_result.lock
    if cancel_lock is None:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=submit_lock.actual_model_id,
            latency_ms=_latency_ms(started),
            lock_ids=tuple(lock_ids),
            expected_locks=2,
            error_code=MISSING_RUN_LOCK,
            detail="取消调用未产生运行锁。",
        )
    try:
        persisted_cancel = recorder.record(
            cancel_lock,
            business_ref=_business_ref(run_id, f"release_gate:{operation_prefix}:cancel"),
        )
        lock_ids.append(persisted_cancel.lock_id)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=cancel_lock.actual_model_id,
            latency_ms=_latency_ms(started),
            lock_ids=tuple(lock_ids),
            expected_locks=2,
            error_code=exc.__class__.__name__.lower(),
            detail="取消锁持久化失败。",
        )
    if cancel_result.status != ModelCallStatus.SUCCESS:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=cancel_lock.actual_model_id,
            latency_ms=_latency_ms(started),
            lock_ids=tuple(lock_ids),
            expected_locks=2,
            error_code=cancel_result.error_code or LIVE_PROBE_FAILED,
            detail="供应商取消未确认成功（本地取消权威不受影响，但真实性证据不足）。",
        )
    return LiveProbeResult(
        capability=capability,
        status="passed",
        approved_model_id=approved,
        actual_model_id=cancel_lock.actual_model_id,
        latency_ms=_latency_ms(started),
        lock_ids=tuple(lock_ids),
        expected_locks=2,
    )


def _probe_embedding(
    gateway: Any,
    recorder: Any,
    settings: Settings,
    token: str,
    *,
    global_key_configured: bool,
) -> tuple[LiveProbeResult, LiveProbeResult]:
    """入库与查询 Embedding 探针：两个真实远端批次，各一条锁。"""
    inconclusive = LiveProbeResult(
        capability="qwen_embedding",
        status="inconclusive",
        approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
        expected_locks=1,
        error_code=MISSING_GLOBAL_QWEN_KEY,
        detail="未配置安装级全局 Qwen Key，Embedding 探针无法执行。",
    )
    if not global_key_configured:
        return inconclusive, inconclusive
    port = QwenEmbeddingPort(
        api_key=settings.qwen_api_key,
        gateway=gateway,
        recorder=recorder,
    )
    write_run = _probe_run_id("qwen_embedding", f"{token}-write")
    query_run = _probe_run_id("qwen_embedding", f"{token}-query")

    write_result = LiveProbeResult(
        capability="qwen_embedding",
        status="failed",
        approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
        error_code="embedding_probe_failed",
        detail="入库探针未执行。",
    )
    query_result = write_result
    started = time.monotonic()
    try:
        vectors = port.embed(
            PROBE_ACCOUNT_ID,
            ["Transformer 架构使用注意力机制。"],
            context=EmbeddingContext(
                operation=EmbeddingOperation.INGESTION_WRITE,
                run_id=write_run,
                object_type=PROBE_OBJECT_TYPE,
                object_id=write_run,
            ),
        )
        write_ok = (
            isinstance(vectors, list)
            and len(vectors) == 1
            and isinstance(vectors[0], list)
            and len(vectors[0]) > 0
        )
        write_result = LiveProbeResult(
            capability="qwen_embedding",
            status="passed" if write_ok else "failed",
            approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            actual_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=None if write_ok else "embedding_empty_vector",
            detail="" if write_ok else "入库探针返回空向量。",
        )
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        write_result = LiveProbeResult(
            capability="qwen_embedding",
            status="failed",
            approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=getattr(exc, "code", None) or exc.__class__.__name__.lower(),
            detail="入库 Embedding 探针异常。",
        )

    query_started = time.monotonic()
    try:
        query_vectors = port.embed(
            PROBE_ACCOUNT_ID,
            ["注意力机制是什么？"],
            context=EmbeddingContext(
                operation=EmbeddingOperation.RETRIEVAL_QUERY,
                run_id=query_run,
                object_type=PROBE_OBJECT_TYPE,
                object_id=query_run,
            ),
        )
        query_ok = (
            isinstance(query_vectors, list)
            and len(query_vectors) == 1
            and isinstance(query_vectors[0], list)
            and len(query_vectors[0]) > 0
        )
        query_result = LiveProbeResult(
            capability="qwen_embedding",
            status="passed" if query_ok else "failed",
            approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            actual_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            latency_ms=_latency_ms(query_started),
            expected_locks=1,
            error_code=None if query_ok else "embedding_empty_vector",
            detail="" if query_ok else "查询探针返回空向量。",
        )
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        query_result = LiveProbeResult(
            capability="qwen_embedding",
            status="failed",
            approved_model_id=MODEL_BY_CAPABILITY["qwen_embedding"],
            latency_ms=_latency_ms(query_started),
            expected_locks=1,
            error_code=getattr(exc, "code", None) or exc.__class__.__name__.lower(),
            detail="查询 Embedding 探针异常。",
        )
    return write_result, query_result


def _invoke_and_record_with_contract(
    gateway: Any,
    recorder: Any,
    capability: str,
    payload: dict[str, Any],
    run_id: str,
    operation: str,
    *,
    expected_locks: int = 1,
    contract: str = "non_empty",
    known_token: str = "BRIDGES-QWEN-2026",
) -> LiveProbeResult:
    """invoke + 落锁 + 输出合同校验（非空/含已知文字），一次调用完成。"""
    started = time.monotonic()
    approved = MODEL_BY_CAPABILITY[capability]
    try:
        result = gateway.invoke(capability, "1", _run_context(run_id), payload)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=expected_locks,
            error_code=exc.__class__.__name__.lower(),
            detail="探针调用异常。",
        )
    latency_ms = _latency_ms(started)
    lock = result.lock
    if lock is None:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=latency_ms,
            expected_locks=expected_locks,
            error_code=MISSING_RUN_LOCK,
            detail="网关调用未产生不可变运行锁。",
        )
    lock_ids: list[str] = []
    try:
        persisted = recorder.record(
            lock,
            business_ref=_business_ref(run_id, operation, attempt_ordinal=1),
        )
        lock_ids.append(persisted.lock_id)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=lock.actual_model_id,
            latency_ms=latency_ms,
            lock_ids=(lock.lock_id,),
            expected_locks=expected_locks,
            error_code=exc.__class__.__name__.lower(),
            detail="运行锁持久化失败。",
        )
    if result.status != ModelCallStatus.SUCCESS:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=lock.actual_model_id,
            latency_ms=latency_ms,
            lock_ids=tuple(lock_ids),
            expected_locks=expected_locks,
            error_code=result.error_code or LIVE_PROBE_FAILED,
            detail="供应商调用未成功。",
        )
    content = ""
    if isinstance(result.output, dict):
        raw = result.output.get("content")
        if isinstance(raw, str):
            content = raw
    if contract == "non_empty":
        output_ok = bool(content.strip())
    elif contract == "known_token":
        output_ok = bool(content.strip()) and known_token.casefold() in content.casefold()
    else:
        output_ok = True
    if not output_ok:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            actual_model_id=lock.actual_model_id,
            latency_ms=latency_ms,
            lock_ids=tuple(lock_ids),
            expected_locks=expected_locks,
            error_code="vision_ocr_contract_failed",
            detail="真实调用成功但输出为空或不含已知文字，合同未满足。",
        )
    return LiveProbeResult(
        capability=capability,
        status="passed",
        approved_model_id=approved,
        actual_model_id=lock.actual_model_id,
        latency_ms=latency_ms,
        lock_ids=tuple(lock_ids),
        expected_locks=expected_locks,
    )


def _probe_career(
    gateway: Any,
    recorder: Any,
    token: str,
    *,
    global_key_configured: bool,
) -> LiveProbeResult:
    """Career 真实生成探针：经业务服务走真实结构化调用并落 generation 锁。"""
    from bridges.career.service import CareerPlannerService

    capability = "qwen_structured_output"
    approved = MODEL_BY_CAPABILITY[capability]
    if not global_key_configured:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code=MISSING_GLOBAL_QWEN_KEY,
            detail="未配置安装级全局 Qwen Key，Career 探针无法执行。",
        )
    if gateway.get_adapter(capability, "1") is None:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code="no_adapter",
            detail="生产组合未绑定真实适配器。",
        )
    run_id = _probe_run_id("career", token)
    started = time.monotonic()
    try:
        service = CareerPlannerService(gateway=gateway, run_lock_recorder=recorder)
        events = list(
            service.run_task(
                PROBE_ACCOUNT_ID,
                "conv-career-probe",
                "msg-career-probe",
                "生涯规划：数据分析方向怎么安排",
                mode="companion",
                run_context=_run_context(run_id),
                profile_enabled=False,
                profile_used=False,
                profile_items=[],
            )
        )
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=exc.__class__.__name__.lower(),
            detail="Career 探针执行异常。",
        )
    locks = recorder.list_locks_by_run(PROBE_ACCOUNT_ID, run_id)
    generation_locks = [
        lock
        for lock in locks
        if any(ref.operation == "career_generation" for ref in lock.business_refs)
    ]
    if not generation_locks:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=MISSING_RUN_LOCK,
            detail="Career 真实调用未产生 career_generation 锁。",
        )
    all_success = all(
        lock.status == ModelCallStatus.SUCCESS for lock in generation_locks
    )
    final_status: Any = "unknown"
    with contextlib.suppress(IndexError):
        final_status = getattr(getattr(events[-1], "result", None), "status", None)
    return LiveProbeResult(
        capability=capability,
        status="passed" if all_success else "failed",
        approved_model_id=approved,
        actual_model_id=generation_locks[0].actual_model_id,
        latency_ms=_latency_ms(started),
        lock_ids=tuple(lock.lock_id for lock in locks),
        expected_locks=len(locks),
        error_code=None if all_success else "career_lock_failed",
        detail=(
            f"Career 探针生成 {len(locks)} 条真实锁（generation+repair），"
            f"领域终态 {final_status}。"
        ),
    )


def _probe_humanizer(
    gateway: Any,
    recorder: Any,
    token: str,
    *,
    global_key_configured: bool,
) -> LiveProbeResult:
    """Humanizer 首稿探针：经业务服务走真实结构化调用并落 draft 锁。"""
    from bridges.contracts.humanizer import (
        HumanizerPath,
        HumanizerSkillInput,
        HumanizerTaskContract,
    )
    from bridges.skills import create_builtin_registry
    from bridges.skills.humanizer.service import HumanizerService

    capability = "qwen_structured_output"
    approved = MODEL_BY_CAPABILITY[capability]
    if not global_key_configured:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code=MISSING_GLOBAL_QWEN_KEY,
            detail="未配置安装级全局 Qwen Key，Humanizer 探针无法执行。",
        )
    if gateway.get_adapter(capability, "1") is None:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code="no_adapter",
            detail="生产组合未绑定真实适配器。",
        )
    run_id = _probe_run_id("humanizer", token)
    started = time.monotonic()
    try:
        service = HumanizerService(
            registry=create_builtin_registry(),
            gateway=gateway,
            run_lock_recorder=recorder,
        )
        events = list(
            service.run_task(
                PROBE_ACCOUNT_ID,
                "conv-humanizer-probe",
                "msg-humanizer-probe",
                HumanizerSkillInput(
                    skill_id="bridges-humanizer",
                    contract=HumanizerTaskContract(
                        path=HumanizerPath.REWRITE,
                        source_text="Transformer 是一种使用注意力机制的神经网络架构。",
                    ),
                ),
                _run_context(run_id),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=exc.__class__.__name__.lower(),
            detail="Humanizer 探针执行异常。",
        )
    locks = recorder.list_locks_by_run(PROBE_ACCOUNT_ID, run_id)
    draft_locks = [
        lock
        for lock in locks
        if any(ref.operation == "humanizer_draft" for ref in lock.business_refs)
    ]
    if not draft_locks:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=MISSING_RUN_LOCK,
            detail="Humanizer 真实调用未产生 humanizer_draft 锁。",
        )
    all_success = all(lock.status == ModelCallStatus.SUCCESS for lock in draft_locks)
    final_status: Any = "unknown"
    with contextlib.suppress(IndexError):
        final_status = getattr(getattr(events[-1], "result", None), "status", None)
    return LiveProbeResult(
        capability=capability,
        status="passed" if all_success else "failed",
        approved_model_id=approved,
        actual_model_id=draft_locks[0].actual_model_id,
        latency_ms=_latency_ms(started),
        lock_ids=tuple(lock.lock_id for lock in locks),
        expected_locks=len(locks),
        error_code=None if all_success else "humanizer_lock_failed",
        detail=(
            f"Humanizer 探针生成 {len(locks)} 条真实锁（draft+revision），"
            f"领域终态 {final_status}。"
        ),
    )


def _probe_profile(
    gateway: Any,
    recorder: Any,
    token: str,
    *,
    global_key_configured: bool,
) -> LiveProbeResult:
    """Profile 歧义信号抽取探针：真实画像 adapter 调用 + 落锁。"""
    from bridges.profiles.automatic import GatewayAutomaticProfileExtractor
    from bridges.profiles.signals import ProfileSignalCategory, ProfileSignalClassification

    capability = "qwen_profile_extraction"
    approved = MODEL_BY_CAPABILITY[capability]
    if not global_key_configured:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code=MISSING_GLOBAL_QWEN_KEY,
            detail="未配置安装级全局 Qwen Key，Profile 探针无法执行。",
        )
    if gateway.get_adapter(capability, "1") is None:
        return LiveProbeResult(
            capability=capability,
            status="inconclusive",
            approved_model_id=approved,
            error_code="no_adapter",
            detail="生产组合未绑定真实适配器。",
        )
    run_id = _probe_run_id("profile", token)
    started = time.monotonic()
    collected: list[ModelRunLock] = []
    try:
        extractor = GatewayAutomaticProfileExtractor(gateway=gateway)
        classification = ProfileSignalClassification(
            category=ProfileSignalCategory.AMBIGUOUS,
            reason_code="release-gate-probe",
            confidence=0.8,
        )
        output = extractor.extract(
            account_id=PROBE_ACCOUNT_ID,
            conversation_id="conv-profile-probe",
            message_id="msg-profile-probe",
            content="我最近在系统学习数据分析。",
            run_id=run_id,
            signal_classification=classification,
            lock_sink=collected.append,
        )
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=getattr(exc, "code", None) or exc.__class__.__name__.lower(),
            detail="Profile 探针执行异常。",
        )
    lock_ids: list[str] = []
    try:
        for lock in collected:
            persisted = recorder.record(
                lock,
                business_ref=_business_ref(run_id, "release_gate:profile_extraction"),
            )
            lock_ids.append(persisted.lock_id)
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            expected_locks=1,
            error_code=exc.__class__.__name__.lower(),
            detail="Profile 锁持久化失败。",
        )
    output_ok = bool(getattr(output, "items", None))
    if not collected or not output_ok:
        return LiveProbeResult(
            capability=capability,
            status="failed",
            approved_model_id=approved,
            latency_ms=_latency_ms(started),
            lock_ids=tuple(lock_ids),
            expected_locks=1,
            error_code=MISSING_RUN_LOCK if not collected else "profile_empty_output",
            detail="Profile 真实调用未产生锁或输出为空。",
        )
    return LiveProbeResult(
        capability=capability,
        status="passed",
        approved_model_id=approved,
        actual_model_id=collected[0].actual_model_id,
        latency_ms=_latency_ms(started),
        lock_ids=tuple(lock_ids),
        expected_locks=len(collected),
        detail=f"Profile 歧义信号抽取 {len(collected)} 条真实锁。",
    )


def run_live_suite(
    composition: ProductionComposition,
    recorder: Any,
    settings: Settings,
    token: str,
) -> list[LiveProbeResult]:
    """对全部活跃 ``qwen_model`` 能力执行最小真实探针（专用探针 run）。

    缺少安装级全局 Key 或真实适配器时所有探针返回 ``inconclusive``
    （``missing_global_qwen_key``/``no_adapter``），门禁失败关闭。
    """
    gateway = composition.gateway
    probes: list[LiveProbeResult] = []

    def run(
        capability: str,
        payload: dict[str, Any],
        operation: str,
        token_suffix: str = "",
        *,
        contract: str = "",
        expected_locks: int = 1,
    ) -> LiveProbeResult:
        if not composition.global_key_configured:
            return LiveProbeResult(
                capability=capability,
                status="inconclusive",
                approved_model_id=MODEL_BY_CAPABILITY[capability],
                error_code=MISSING_GLOBAL_QWEN_KEY,
                detail="未配置安装级全局 Qwen Key，真实探针无法执行。",
            )
        adapter = gateway.get_adapter(capability, "1")
        if adapter is None:
            return LiveProbeResult(
                capability=capability,
                status="inconclusive",
                approved_model_id=MODEL_BY_CAPABILITY[capability],
                error_code="no_adapter",
                detail="生产组合未绑定真实适配器。",
            )
        run_id = _probe_run_id(capability, f"{token}-{token_suffix}" if token_suffix else token)
        if contract:
            return _invoke_and_record_with_contract(
                gateway,
                recorder,
                capability,
                payload,
                run_id,
                f"release_gate:{operation}",
                expected_locks=expected_locks,
                contract=contract,
            )
        return _invoke_and_record(
            gateway,
            recorder,
            capability,
            payload,
            run_id,
            f"release_gate:{operation}",
        )

    probes.append(
        run(
            "qwen_text_chat",
            {"prompt": "请用一句中文介绍 Transformer。", "temperature": 0.2, "max_tokens": 96},
            "chat",
        )
    )
    # Humanizer/Career：经业务服务完成真实结构化调用（每次调用独立落锁，
    # 修订/修复场景保留每条锁；领域终态只作报告说明，不以解析失败形成完成态）。
    probes.append(
        _probe_humanizer(
            gateway,
            recorder,
            token,
            global_key_configured=composition.global_key_configured,
        )
    )
    probes.append(
        _probe_career(
            gateway,
            recorder,
            token,
            global_key_configured=composition.global_key_configured,
        )
    )
    # Profile 歧义分支：真实画像 adapter + 运行锁（本地分支零调用零锁由
    # 本地旅程 spy 探针与 Issue 13 领域测试证明）。
    probes.append(
        _probe_profile(
            gateway,
            recorder,
            token,
            global_key_configured=composition.global_key_configured,
        )
    )
    png = _known_text_png_bytes()
    vision_payload: dict[str, Any] = {
        "image_base64": base64.b64encode(png).decode("ascii"),
        "mime_type": "image/png",
        "temperature": 0.0,
        "max_tokens": 2048,
    }
    probes.append(
        run(
            "qwen_vision",
            {**vision_payload, "prompt": "Describe the text written in this image."},
            "vision",
            contract="non_empty",
        )
    )
    probes.append(
        run(
            "qwen_ocr",
            {
                **vision_payload,
                "prompt": "Extract all visible text from this image. Do not add commentary.",
            },
            "ocr",
            contract="known_token",
        )
    )
    wav = _tone_wav_bytes()
    probes.append(
        run(
            "qwen_asr_short",
            {
                "audio_base64": base64.b64encode(wav).decode("ascii"),
                "mime_type": "audio/wav",
                "duration_seconds": 1.0,
                "temperature": 0.0,
                "max_tokens": 256,
            },
            "asr_short",
        )
    )
    probes.append(
        run(
            "qwen_asr_long",
            {
                "audio_base64": base64.b64encode(wav).decode("ascii"),
                "mime_type": "audio/wav",
                "duration_seconds": 1.0,
                "temperature": 0.0,
                "max_tokens": 256,
            },
            "asr_long",
        )
    )
    probes.append(
        run(
            "qwen_tts",
            {"text": "你好，这是一条朗读探针。"},
            "tts",
        )
    )
    probes.append(
        _probe_submit_cancel(
            gateway,
            recorder,
            "qwen_image",
            {"kind": "submit", "prompt": "一张极简的蓝色圆形", "size": "1024*1024", "n": 1},
            token,
            "image",
            global_key_configured=composition.global_key_configured,
        )
    )
    probes.append(
        _probe_submit_cancel(
            gateway,
            recorder,
            "qwen_wan",
            {"kind": "submit", "prompt": "一只猫安静地走过",
             "size": "1280*720", "duration_seconds": 5},
            token,
            "video",
            global_key_configured=composition.global_key_configured,
        )
    )
    embedding_write, embedding_query = _probe_embedding(
        gateway, recorder, settings, token, global_key_configured=composition.global_key_configured
    )
    probes.extend([embedding_write, embedding_query])
    return probes


def verify_locks_after_restart(
    database_path: Path,
    probes: Sequence[LiveProbeResult],
) -> list[GateViolation]:
    """重启数据库后复查：全部探针锁仍存在且业务关联完整。

    以只读方式重新打开同一 SQLite 文件（模拟进程重启），按
    ``account_id + run_id`` 查询每条探针锁；锁缺失或数量不足即
    ``lock_verification_failed`` 失败关闭。
    """
    from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder

    violations: list[GateViolation] = []
    try:
        reopened = BridgesDatabase(database_path)
        reopened.initialize()
    except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
        return [
            GateViolation(
                LOCK_VERIFICATION_FAILED,
                "restart",
                f"重启后数据库无法打开（{exc.__class__.__name__.lower()}）。",
            )
        ]
    try:
        recorder = SqliteModelRunLockRecorder(reopened)
        for probe in probes:
            if not probe.lock_ids:
                # 未产生锁的探针（无 Key/inconclusive）没有可复查的证据；
                # 失败探针只要产生了锁就必须在重启后仍可查（Issue 17 AC：
                # 无论成功、失败、超时、重试还是修复都形成持久化运行锁）。
                continue
            persisted = [recorder.get_lock(lock_id, PROBE_ACCOUNT_ID) for lock_id in probe.lock_ids]
            missing = [
                lock_id
                for lock_id, record in zip(probe.lock_ids, persisted, strict=False)
                if record is None
            ]
            if missing:
                violations.append(
                    GateViolation(
                        LOCK_VERIFICATION_FAILED,
                        probe.capability,
                        f"重启后 {len(missing)}/{len(probe.lock_ids)} 条锁丢失。",
                    )
                )
                continue
            linked = [
                record
                for record in persisted
                if record is not None and record.business_refs
            ]
            if len(linked) != len(probe.lock_ids):
                violations.append(
                    GateViolation(
                        LOCK_VERIFICATION_FAILED,
                        probe.capability,
                        "重启后部分锁缺少业务关联（release_probe 链接缺失）。",
                    )
                )
        # 双账户隔离抽查：探针账户之外的账户不得读到探针锁。
        try:
            foreign = recorder.list_locks_by_run("other-account", PROBE_RUN_PREFIX)
            if foreign:
                violations.append(
                    GateViolation(
                        LOCK_VERIFICATION_FAILED,
                        "account-isolation",
                        "其他账户可读到探针 run 的锁，账户隔离被破坏。",
                    )
                )
        except Exception:  # noqa: BLE001 - 隔离抽查失败按失败关闭
            violations.append(
                GateViolation(
                    LOCK_VERIFICATION_FAILED,
                    "account-isolation",
                    "账户隔离抽查执行失败。",
                )
            )
    finally:
        reopened.close()
    return violations


def run_external_provider_probes() -> list[LiveProbeResult]:
    """真实 Tavily/arXiv 提供方探针（复用 Issue 04 发布探针，仅 --real-probes）。

    检索阶段不读取/不发送 Qwen Key（静态扫描证明）；提供方失败不会
    生成模型成功锁（失败时报告 ``external_provider_failed`` 失败关闭）。
    """
    from bridges.closeout.release_gate import run_real_provider_probes

    probes: list[LiveProbeResult] = []
    for evidence in run_real_provider_probes():
        probes.append(
            LiveProbeResult(
                capability=f"provider:{evidence.provider}",
                status=evidence.status,
                approved_model_id="",
                category="external_non_qwen",
                provider="external",
                latency_ms=evidence.duration_ms,
                error_code=evidence.error_category,
                detail="external_non_qwen 提供方真实探针。",
            )
        )
    return probes


# ---------------------------------------------------------------------------
# 3. 门禁装配与报告
# ---------------------------------------------------------------------------


@dataclass
class AuthenticityGateReport:
    """脱敏的发布门证据摘要。"""

    code_version: str
    manifest_version: int
    environment: str
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    deterministic_checks: list[dict[str, Any]] = field(default_factory=list)
    violations: list[GateViolation] = field(default_factory=list)
    live_probes: list[LiveProbeResult] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    release_ready: bool = False

    @property
    def status(self) -> str:
        return "passed" if self.release_ready else "blocked"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "qwen_authenticity_gate",
            "status": self.status,
            "release_ready": self.release_ready,
            "generated_at": self.generated_at,
            "code_version": self.code_version,
            "manifest_version": self.manifest_version,
            "environment": self.environment,
            "deterministic_checks": self.deterministic_checks,
            "violations": [violation.as_dict() for violation in self.violations],
            "live_probes": [probe.as_dict() for probe in self.live_probes],
            "risks": list(self.risks),
        }


def build_gate_app() -> Any:
    """构建门禁使用的应用实例（test 环境 + 临时 SQLite 数据目录）。

    路由完整性核对、退役契约探针与本地旅程 spy 探针复用同一实例；
    临时 SQLite 使聊天/检索/知识库服务真实挂载（内存模式会 503）。
    调用方必须已设置 ``BRIDGES_ENVIRONMENT``；未配置数据库时本函数
    自建临时数据目录并注入 ``BRIDGES_DATABASE_URL``/``BRIDGES_SECRET_KEY``。
    """
    from bridges.api.main import create_app
    from bridges.config import get_settings as _get_settings

    settings = _get_settings()
    if settings.database_url is None or not str(settings.database_url):
        import tempfile as _tempfile

        data_dir = _tempfile.mkdtemp(prefix="release-gate-app-")
        os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{Path(data_dir).as_posix()}/bridges.db"
        if not os.environ.get("BRIDGES_SECRET_KEY", "").strip():
            os.environ["BRIDGES_SECRET_KEY"] = "release-gate-app-secret-key-0001"
        _get_settings.cache_clear()
    return create_app(None)


def run_authenticity_gate(
    *,
    real_probes: bool = False,
    app: Any | None = None,
    settings: Settings | None = None,
    database_path: Path | None = None,
    report_dir: Path | None = None,
) -> AuthenticityGateReport:
    """运行 Issue 17 真实性发布门；只有 ``real_probes=True`` 才访问真实 Qwen。

    返回脱敏报告；``status == "blocked"`` 时调用方必须非零退出。
    """
    if settings is None:
        settings = get_settings()
    # 门禁自建应用：test 环境 + 临时 SQLite（本地旅程 spy 需要真实服务挂载）。
    owned_app = app is None
    if owned_app:
        os.environ["BRIDGES_ENVIRONMENT"] = "test"
        if database_path is not None:
            os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
        get_settings.cache_clear()
    app_instance = app if app is not None else build_gate_app()
    token = f"{os.getpid()}-{int(time.time() * 1000)}"
    risks: list[str] = []
    violations: list[GateViolation] = []
    deterministic_checks: list[dict[str, Any]] = []

    # 1. 能力清单完整性（路由来自真实应用注册）。
    started = time.monotonic()
    registry = getattr(app_instance.state, "capability_registry", None)
    if registry is None:
        registry = CapabilityRegistry()
        from bridges.ai.production import register_builtin_capabilities

        register_builtin_capabilities(registry)
    routes = discover_api_routes(app_instance)
    manifest_violations = check_manifest(routes, registry)
    violations.extend(manifest_violations)
    deterministic_checks.append(
        {
            "name": "manifest-completeness",
            "status": "passed" if not manifest_violations else "failed",
            "duration_ms": _latency_ms(started),
            "routes": len(routes),
        }
    )

    # 2. 静态扫描。
    started = time.monotonic()
    scan_violations = check_static_scans()
    violations.extend(scan_violations)
    deterministic_checks.append(
        {
            "name": "static-scans",
            "status": "passed" if not scan_violations else "failed",
            "duration_ms": _latency_ms(started),
        }
    )

    # 3. 生产组合（production-like；真实 Key 缺失时由组合校验失败关闭）。
    os.environ["BRIDGES_ENVIRONMENT"] = "production"
    get_settings.cache_clear()
    production_settings = get_settings()
    composition = build_production_composition(production_settings)
    started = time.monotonic()
    composition_violations = check_production_composition(composition)
    violations.extend(composition_violations)
    deterministic_checks.append(
        {
            "name": "production-composition",
            "status": "passed" if not composition_violations else "failed",
            "duration_ms": _latency_ms(started),
            "global_key_configured": composition.global_key_configured,
            "cassette_enabled": composition.cassette_enabled,
        }
    )
    if not real_probes:
        risks.append("real_probes_not_run")
    if not composition.global_key_configured:
        risks.append("missing_global_qwen_key")

    # 4. 退役路由契约探针（进程内，无网络）。
    started = time.monotonic()
    retired_violations = check_retired_contract(app_instance)
    violations.extend(retired_violations)
    deterministic_checks.append(
        {
            "name": "retired-contract",
            "status": "passed" if not retired_violations else "failed",
            "duration_ms": _latency_ms(started),
        }
    )

    # 5. 本地旅程 spy 探针（代表性 local_deterministic 旅程 0 调用 0 锁）。
    # 使用应用自身挂载的 SQLite（门禁自建应用时是临时数据目录），保证
    # 聊天/检索/知识库服务真实挂载；锁计数以同一数据库为准。
    probe_database: BridgesDatabase | None = getattr(
        app_instance.state, "bridges_database", None
    )
    close_probe_database = False
    probe_database_path: Path | None = None
    if probe_database is not None:
        candidate = getattr(probe_database, "path", None)
        if candidate is not None:
            probe_database_path = Path(candidate)
    if probe_database is None and database_path is not None:
        probe_database = BridgesDatabase(database_path)
        probe_database.initialize()
        probe_database_path = database_path
        close_probe_database = True
    started = time.monotonic()
    local_violations: list[GateViolation] = []
    if probe_database is None:
        local_violations = [
            GateViolation(
                LOCAL_JOURNEY_MODEL_CALL,
                "local-deterministic-journeys",
                "门禁应用未挂载 SQLite，本地旅程 spy 无法执行（失败关闭）。",
            )
        ]
    else:
        try:
            local_violations = run_local_journey_spy_probes(app_instance, probe_database)
        except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
            local_violations = [
                GateViolation(
                    LOCAL_JOURNEY_MODEL_CALL,
                    "local-deterministic-journeys",
                    f"本地旅程 spy 探针执行异常（{exc.__class__.__name__.lower()}）。",
                )
            ]
    violations.extend(local_violations)
    deterministic_checks.append(
        {
            "name": "local-journey-spy",
            "status": "passed" if not local_violations else "failed",
            "duration_ms": _latency_ms(started),
        }
    )

    # 6. live suite（--real-probes 显式 opt-in）。
    live_probes: list[LiveProbeResult] = []
    if real_probes:
        if probe_database is None:
            raise RuntimeError("live suite 需要可写探针数据库。")
        from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder

        recorder = SqliteModelRunLockRecorder(probe_database)
        started = time.monotonic()
        try:
            live_probes = run_live_suite(composition, recorder, production_settings, token)
        except Exception as exc:  # noqa: BLE001 - 探针只输出稳定分类
            live_probes = [
                LiveProbeResult(
                    capability="live-suite",
                    status="failed",
                    approved_model_id="",
                    error_code=exc.__class__.__name__.lower(),
                    detail="live suite 执行异常。",
                )
            ]
        deterministic_checks.append(
            {
                "name": "live-suite",
                "status": (
                    "passed"
                    if live_probes and all(p.status == "passed" for p in live_probes)
                    else "failed"
                ),
                "duration_ms": _latency_ms(started),
                "probe_count": len(live_probes),
                "database_path": str(probe_database_path),
            }
        )
        # 7. 重启后锁复查（只读重新打开同一数据库）。
        if close_probe_database and probe_database is not None:
            probe_database.close()
            close_probe_database = False
        if probe_database_path is None:
            raise RuntimeError("live suite 需要可写探针数据库路径。")
        restart_path = probe_database_path
        started = time.monotonic()
        restart_violations = verify_locks_after_restart(restart_path, live_probes)
        violations.extend(restart_violations)
        deterministic_checks.append(
            {
                "name": "lock-restart-verification",
                "status": "passed" if not restart_violations else "failed",
                "duration_ms": _latency_ms(started),
            }
        )
        # 8. 外部提供方真实探针（Tavily/arXiv）。
        external_probes = run_external_provider_probes()
        live_probes.extend(external_probes)
        failed_external = [
            probe for probe in external_probes if probe.status == "failed"
        ]
        if failed_external:
            violations.append(
                GateViolation(
                    EXTERNAL_PROVIDER_FAILED,
                    ",".join(probe.capability for probe in failed_external),
                    "外部提供方真实探针失败（不会生成模型成功锁）。",
                )
            )
    elif probe_database is not None and close_probe_database:
        probe_database.close()
        close_probe_database = False

    # 汇总风险与结论。
    if violations:
        risks.append("gate_violations")
    failed_probes = [probe for probe in live_probes if probe.status == "failed"]
    inconclusive_probes = [
        probe for probe in live_probes if probe.status == "inconclusive"
    ]
    if failed_probes:
        risks.append(LIVE_PROBE_FAILED)
    if inconclusive_probes:
        risks.append(LIVE_PROBE_INCONCLUSIVE)
    deterministic_ok = all(
        check["status"] in {"passed", "skipped"} for check in deterministic_checks
    )
    # 真实性门只有在显式 --real-probes 且全部探针证实后才可发布：
    # 未运行 live suite、探针失败或 inconclusive 都不得通过（Issue 17
    # "缺少全局 Key、真实网络或供应商权限时整体状态为失败或 inconclusive，
    # 不得通过发布门"）。
    probes_ok = bool(
        real_probes and live_probes and all(p.status == "passed" for p in live_probes)
    )
    release_ready = deterministic_ok and not violations and probes_ok

    report = AuthenticityGateReport(
        code_version=_code_version(),
        manifest_version=CAPABILITY_MANIFEST_VERSION,
        environment="production-like",
        deterministic_checks=deterministic_checks,
        violations=violations,
        live_probes=live_probes,
        risks=risks,
        release_ready=release_ready,
    )
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "authenticity-report.json").write_text(
            __import__("json").dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """门禁 CLI：``--real-probes`` 显式 opt-in 真实探针，失败非零退出。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="运行 Issue 17 全功能 Qwen 真实性发布门。")
    parser.add_argument(
        "--real-probes",
        action="store_true",
        help="显式访问真实 Qwen/Tavily/arXiv 执行最小真实探针（需安装级全局 Qwen Key）。",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(".tmp") / "authenticity-gate",
        help="脱敏 JSON 报告目录。",
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        default=None,
        help="live suite 探针数据库路径（默认使用临时目录）。",
    )
    args = parser.parse_args(argv)
    report = run_authenticity_gate(
        real_probes=args.real_probes,
        database_path=args.database_path,
        report_dir=args.report_dir,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.status == "blocked":
        return 1
    return 0


__all__ = [
    "AuthenticityGateReport",
    "CANCEL_PROBE_UNAVAILABLE",
    "EXTERNAL_PROVIDER_FAILED",
    "GateViolation",
    "LIVE_PROBE_FAILED",
    "LIVE_PROBE_INCONCLUSIVE",
    "LiveProbeResult",
    "LOCK_VERIFICATION_FAILED",
    "LOCAL_JOURNEY_MODEL_CALL",
    "PROBE_ACCOUNT_ID",
    "RETIRED_CONTRACT_FAILED",
    "RETIRED_PROBE_SAMPLES",
    "check_manifest",
    "check_production_composition",
    "check_retired_contract",
    "check_static_scans",
    "main",
    "run_authenticity_gate",
    "run_external_provider_probes",
    "run_live_suite",
    "run_local_journey_spy_probes",
    "verify_locks_after_restart",
]
