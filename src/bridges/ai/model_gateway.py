"""Model gateway: resolve logical capabilities and produce immutable run locks.

The gateway is the only place that maps a logical capability name to a concrete
vendor model. It enforces:

- registered, verified capabilities only;
- bounded retry for rate-limit and transient failures;
- same-region, verified fallbacks only;
- no cross-region fallback and no fallback to unverified/disabled models;
- immutable ``ModelRunLock`` records for every attempt path.
"""

from __future__ import annotations

import random
import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.ai.adapters import (
    REQUEST_TIMEOUT_SECONDS_KEY,
    AdapterError,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    StreamEvent,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from bridges.ai.run_model_config import RunModelConfigProvider, configured_model_id
from bridges.contracts.ai import (
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallResult,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.workflows import RunContextEnvelope

if TYPE_CHECKING:
    from bridges.chat.budget import RunBudget


#: Issue 03：流式建连阶段自动重试开关（可回滚；关闭后等价旧行为——建连
#: 失败立即以 error 事件结束）。只对「尚未下发任何 delta」的建连失败
#: 生效；已开始输出后的中断不重试（语义同 ``stream_interrupted``）。
STREAM_CONNECT_RETRY_ENABLED = True
#: 流式建连阶段最多自动重试次数（短退避；总尝试次数 = 重试次数 + 1）。
STREAM_CONNECT_RETRY_MAX_RETRIES = 1
#: 流式建连重试的短退避（秒）。
STREAM_CONNECT_RETRY_BACKOFF_SECONDS = 1.0


class ModelGatewayError(Exception):
    """Domain error for gateway-level failures."""


class ModelGateway:
    """Resolve capabilities, invoke adapters, and record run locks."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        model_config_provider: RunModelConfigProvider | None = None,
    ) -> None:
        self._registry = registry
        self._model_config_provider = model_config_provider
        self._adapters: dict[tuple[str, str], CapabilityAdapter] = {}

    def register_adapter(
        self,
        capability_name: str,
        capability_version: str,
        adapter: CapabilityAdapter,
    ) -> None:
        """Bind an adapter to a registered capability.

        Multiple capabilities may share the same adapter instance.
        """
        self._adapters[(capability_name, capability_version)] = adapter

    def is_adapter_registered(self, capability_name: str, capability_version: str) -> bool:
        """Return whether an adapter has already been bound to a capability."""
        return (capability_name, capability_version) in self._adapters

    def get_adapter(
        self, capability_name: str, capability_version: str
    ) -> CapabilityAdapter | None:
        """Return the adapter bound to a capability, or None when unbound.

        Issue 09：生产组合校验器经此只读访问器检查每个活跃 capability 的
        真实适配器绑定，不直接触碰内部存储。
        """
        return self._adapters.get((capability_name, capability_version))

    def _effective_capability(
        self, capability: CapabilityRecord, model_override: str | None
    ) -> CapabilityRecord:
        """解析本次调用实际使用的模型绑定（V2 Issue 09）。

        优先级：**运行级锁定**（``model_override``，进行中的轮次沿用启动时
        的模型）高于**运行配置**（用户在设置中验证保存的主模型，每次调用
        读取，新旧会话的下一轮即生效），两者都没有时保持出厂矩阵绑定。

        仅覆盖运行配置声明的主对话/结构化/画像/视觉/OCR 能力；向量化等其余
        能力永远取注册表绑定（``configured_model_id`` 返回 None）。返回的
        记录同时携带该模型的输入额度，漂移守卫与运行锁都以实际模型为准。
        """
        model_id = model_override
        if model_id is None and self._model_config_provider is not None:
            config = self._model_config_provider.snapshot()
            model_id = configured_model_id(capability.name, config)
            if model_id is not None and model_id != capability.model_id:
                return capability.model_copy(
                    update={
                        "model_id": model_id,
                        "max_input_tokens": config.max_input_tokens,
                    }
                )
        if not model_id or model_id == capability.model_id:
            return capability
        return capability.model_copy(update={"model_id": model_id})

    def invoke(
        self,
        capability_name: str,
        capability_version: str,
        run_context: RunContextEnvelope,
        payload: dict[str, Any] | None = None,
        budget: RunBudget | None = None,
        model_override: str | None = None,
    ) -> ModelCallResult:
        """Invoke a capability and return a result with an immutable run lock.

        The gateway never silently crosses regions or swaps to an unverified
        model. All attempted capabilities are recorded in the lock's fallback
        path.

        ``budget``（Issue 06 第七轮）：传入 RunBudget 时每次尝试的超时按
        「剩余预算 − 交接预留」截断（单一预算常量来源），重试前检查剩余
        预算放不放得下「退避 + 最小调用窗口 + 交接预留」——放不下直接以
        真实错误终态收尾，不再等待退避或发起重试。未传入时保持既有
        行为（适配器默认超时，重试只受 RetryPolicy 约束）。

        ``model_override``（V2 Issue 09）：本运行的模型锁定；进行中的轮次
        传入启动时解析的模型 ID，换运行配置不会中途切换本轮模型。
        """
        payload = payload or {}
        try:
            primary = self._registry.get(capability_name, capability_version)
        except CapabilityRegistryError as exc:
            lock = ModelRunLock(
                lock_id=secrets.token_urlsafe(16),
                run_id=run_context.run_id,
                account_id=run_context.account_id,
                project_id=run_context.project_id,
                capability_name=capability_name,
                capability_version=capability_version,
                actual_model_id=None,
                region="unknown",
                parameters={},
                prompt_version="unknown",
                input_output_contract="unknown",
                fallback_path=[f"{capability_name}@{capability_version}"],
                status=ModelCallStatus.BLOCKED,
                retry_count=0,
                degradation_reason=str(exc),
                error_code="unregistered_capability",
                error_message=str(exc),
                created_at=datetime.now(UTC),
            )
            return ModelCallResult(
                status=ModelCallStatus.BLOCKED,
                lock=lock,
                error_code="unregistered_capability",
                error_message=str(exc),
                degradation_reason=str(exc),
            )

        primary = self._effective_capability(primary, model_override)
        if primary.status != CapabilityStatus.VERIFIED:
            return self._blocked_result(
                run_context,
                primary,
                "capability_not_verified",
                f"能力未通过验证：{capability_name}@{capability_version}。",
            )

        adapter = self._adapters.get((capability_name, capability_version))
        if adapter is None:
            return self._blocked_result(
                run_context,
                primary,
                "no_adapter",
                f"能力没有绑定适配器：{capability_name}@{capability_version}。",
            )

        attempted: list[str] = []
        primary_result, primary_lock = self._invoke_capability(
            primary,
            adapter,
            run_context,
            payload,
            attempted,
            budget=budget,
        )
        if primary_result.status in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
            return primary_result

        # Only retryable failures (rate-limit / transient) may fall back.
        # Region, auth and safety failures fail closed immediately.
        if primary_result.status != ModelCallStatus.RETRYABLE_FAIL:
            return primary_result

        # Primary exhausted its retry budget; attempt a verified same-region fallback.
        # Check prohibited conditions first — if any match the primary's error,
        # no fallback is allowed regardless of availability.
        if primary.fallback_policy.prohibited_when:
            for condition in primary.fallback_policy.prohibited_when:
                if condition in (primary_result.error_code or "") or condition in (
                    primary_result.degradation_reason or ""
                ):
                    return self._blocked_result(
                        run_context,
                        primary,
                        "fallback_prohibited",
                        f"禁止降级条件触发：{condition}，不允许使用备选能力。",
                        attempted=attempted,
                    )

        fallback = self._resolve_fallback(primary.fallback_policy, attempted)
        if fallback is None:
            return primary_result

        fallback_adapter = self._adapters.get((fallback.name, fallback.version))
        if fallback_adapter is None:
            return self._blocked_result(
                run_context,
                primary,
                "fallback_no_adapter",
                f"备选能力没有绑定适配器：{fallback.name}@{fallback.version}。",
                attempted=attempted,
            )

        # Issue 06 第七轮：备选调用同样受预算重试门约束——剩余预算放不下
        # 「一次最小调用窗口 + 交接预留」时不再发起备选调用，以主能力的
        # 真实错误终态收尾（绝不带着真实错误再吃一次调用窗口）。
        if budget is not None and not budget.can_retry_model_call():
            return primary_result

        fallback_result, _ = self._invoke_capability(
            fallback,
            fallback_adapter,
            run_context,
            payload,
            attempted,
            budget=budget,
        )
        return fallback_result

    def stream(
        self,
        capability_name: str,
        capability_version: str,
        run_context: RunContextEnvelope,
        payload: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> Iterator[StreamEvent]:
        """流式调用一个能力，逐块产出事件并在结束时附带不可变运行锁。

        与 ``invoke`` 的差异：真正的流式适配器路径不做网关级自动重试与
        降级——聊天以"新建助手尝试"作为用户级重试机制（Issue 11）。
        Issue 03 例外：建连阶段（尚未下发任何 delta）的 ``RegionError``
        自动重试一次（短退避约 1 秒，重试次数计入运行锁遥测）；已开始
        输出后的中断不重试。连接阶段失败直接以 error 事件结束；已开始
        输出后的失败同样以 error 事件结束并保留已接收正文。无
        ``stream_call`` 的适配器（如测试替身）降级为一次性 ``invoke``，
        复用其重试/降级语义。能力未注册/未验证/未绑定适配器时产出带
        BLOCKED 运行锁的 error 事件，绝不静默成功。
        """
        payload = payload or {}
        try:
            primary = self._registry.get(capability_name, capability_version)
        except CapabilityRegistryError as exc:
            lock = ModelRunLock(
                lock_id=secrets.token_urlsafe(16),
                run_id=run_context.run_id,
                account_id=run_context.account_id,
                project_id=run_context.project_id,
                capability_name=capability_name,
                capability_version=capability_version,
                actual_model_id=None,
                region="unknown",
                parameters={},
                prompt_version="unknown",
                input_output_contract="unknown",
                fallback_path=[f"{capability_name}@{capability_version}"],
                status=ModelCallStatus.BLOCKED,
                retry_count=0,
                degradation_reason=str(exc),
                error_code="unregistered_capability",
                error_message=str(exc),
                created_at=datetime.now(UTC),
            )
            yield StreamEvent(
                kind="error",
                error_code="unregistered_capability",
                error_message=str(exc),
                lock=lock,
            )
            return

        primary = self._effective_capability(primary, model_override)
        if primary.status != CapabilityStatus.VERIFIED:
            blocked = self._blocked_result(
                run_context,
                primary,
                "capability_not_verified",
                f"能力未通过验证：{capability_name}@{capability_version}。",
            )
            assert blocked.lock is not None
            yield StreamEvent(
                kind="error",
                error_code=blocked.error_code,
                error_message=blocked.error_message,
                lock=blocked.lock,
            )
            return

        adapter = self._adapters.get((capability_name, capability_version))
        if adapter is None:
            blocked = self._blocked_result(
                run_context,
                primary,
                "no_adapter",
                f"能力没有绑定适配器：{capability_name}@{capability_version}。",
            )
            assert blocked.lock is not None
            yield StreamEvent(
                kind="error",
                error_code=blocked.error_code,
                error_message=blocked.error_message,
                lock=blocked.lock,
            )
            return

        stream_call = getattr(adapter, "stream_call", None)
        if stream_call is None:
            # 非流式适配器（如测试替身）降级为一次性调用：完整回答作为
            # 单个 delta 输出，错误沿用 invoke 的分类结果。
            result = self.invoke(
                capability_name,
                capability_version,
                run_context,
                payload,
                model_override=model_override,
            )
            if result.status == ModelCallStatus.SUCCESS and result.lock is not None:
                content = ""
                if isinstance(result.output, dict):
                    content = str(result.output.get("content") or "")
                if content:
                    yield StreamEvent(kind="delta", delta=content)
                yield StreamEvent(kind="done", lock=result.lock, usage=result.lock.usage)
            else:
                yield StreamEvent(
                    kind="error",
                    error_code=result.error_code,
                    error_message=result.error_message,
                    lock=result.lock,
                )
            return

        actual_model_id: str | None = primary.model_id
        usage: dict[str, Any] | None = None
        # Issue 03：流式建连阶段自动重试（尚未下发任何 delta 的 RegionError
        # 重试一次，短退避；重试次数计入运行锁遥测）。已下发 delta 后的
        # 中断不重试——语义同 ``stream_interrupted``，以 error 事件收尾。
        connect_retry_count = 0
        delivered_any_chunk = False
        while True:
            try:
                for chunk in stream_call(primary, run_context, payload):
                    delivered_any_chunk = True
                    if chunk.kind == "delta":
                        yield StreamEvent(kind="delta", delta=chunk.delta)
                    elif chunk.kind == "error":
                        lock, event = self._stream_error_event(
                            run_context,
                            primary,
                            error_code=chunk.error_code,
                            error_message=chunk.error_message,
                            degradation_reason=chunk.error_message,
                            retry_count=connect_retry_count,
                            payload=payload,
                        )
                        yield event
                        return
                    elif chunk.kind == "done":
                        reported = chunk.actual_model_id
                        # Issue 09：流式结束块上报的实际模型与批准 ID 不一致时
                        # 同样失败关闭（``actual_model_mismatch``），运行锁如实
                        # 记录漂移值。
                        if reported is not None and reported != primary.model_id:
                            lock = self._build_mismatch_lock(
                                run_context,
                                primary,
                                reported,
                                [f"{primary.name}@{primary.version}"],
                                retry_count=connect_retry_count,
                                payload=payload,
                            )
                            yield StreamEvent(
                                kind="error",
                                error_code="actual_model_mismatch",
                                error_message=lock.error_message,
                                lock=lock,
                            )
                            return
                        actual_model_id = reported or primary.model_id
                        usage = chunk.usage
                break
            except RegionError as exc:
                if (
                    not delivered_any_chunk
                    and STREAM_CONNECT_RETRY_ENABLED
                    and connect_retry_count < STREAM_CONNECT_RETRY_MAX_RETRIES
                ):
                    connect_retry_count += 1
                    time.sleep(STREAM_CONNECT_RETRY_BACKOFF_SECONDS)
                    continue
                lock, event = self._stream_error_event(
                    run_context,
                    primary,
                    error_code=exc.code,
                    error_message=exc.message,
                    degradation_reason=str(exc),
                    retry_count=connect_retry_count,
                    payload=payload,
                )
                yield event
                return
            except (RateLimitError, TransientError, AuthError, AdapterError) as exc:
                lock, event = self._stream_error_event(
                    run_context,
                    primary,
                    error_code=exc.code,
                    error_message=exc.message,
                    degradation_reason=str(exc),
                    retry_count=connect_retry_count,
                    payload=payload,
                )
                yield event
                return

        lock = self._build_lock(
            run_context,
            primary,
            ModelCallStatus.SUCCESS,
            [f"{primary.name}@{primary.version}"],
            retry_count=connect_retry_count,
            actual_model_id=actual_model_id,
            usage=usage,
            payload=payload,
        )
        yield StreamEvent(kind="done", lock=lock, usage=usage)

    def _stream_error_event(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        *,
        error_code: str | None,
        error_message: str | None,
        degradation_reason: str | None,
        retry_count: int,
        payload: dict[str, Any] | None,
    ) -> tuple[ModelRunLock, StreamEvent]:
        """构造流式失败锁与 error 事件（Issue 03：三条错误路径共用）。

        锁状态按稳定错误码折叠（region_* 子码 → BLOCKED），重试次数
        如实计入运行锁遥测。
        """
        lock = self._build_lock(
            run_context,
            capability,
            self._lock_status_for_code(error_code or "unknown"),
            [f"{capability.name}@{capability.version}"],
            retry_count=retry_count,
            degradation_reason=degradation_reason,
            actual_model_id=capability.model_id,
            error_code=error_code,
            error_message=error_message,
            payload=payload,
        )
        event = StreamEvent(
            kind="error",
            error_code=error_code,
            error_message=error_message,
            lock=lock,
        )
        return lock, event

    @staticmethod
    def _lock_status_for_code(error_code: str) -> ModelCallStatus:
        """按稳定错误码映射运行锁状态。

        限流与瞬时故障属于可重试失败；鉴权、区域与其余供应商拒绝
        一律失败关闭（fail closed），与 ``invoke`` 的分类一致。
        """
        if error_code in {"rate_limit", "transient", "stream_interrupted"}:
            return ModelCallStatus.RETRYABLE_FAIL
        return ModelCallStatus.BLOCKED

    def _resolve_fallback(
        self,
        policy: FallbackPolicy,
        attempted: list[str],
    ) -> CapabilityRecord | None:
        if not policy.fallback_capability_name or not policy.fallback_capability_version:
            return None
        try:
            candidate = self._registry.get(
                policy.fallback_capability_name,
                policy.fallback_capability_version,
            )
        except CapabilityRegistryError:
            return None
        if candidate.status != CapabilityStatus.VERIFIED:
            return None
        if policy.allow_same_region_only:
            primary_name = attempted[0] if attempted else None
            if primary_name:
                try:
                    primary = self._registry.get(*primary_name.rsplit("@", 1))
                except CapabilityRegistryError:
                    return None
                if candidate.region != primary.region:
                    return None
        return candidate

    def _invoke_capability(
        self,
        capability: CapabilityRecord,
        adapter: CapabilityAdapter,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
        attempted: list[str],
        budget: RunBudget | None = None,
    ) -> tuple[ModelCallResult, ModelRunLock]:
        attempted.append(f"{capability.name}@{capability.version}")
        retry_policy = capability.retry_policy
        retry_count = 0

        for attempt in range(1, retry_policy.max_attempts + 1):
            # Issue 06 第七轮：按剩余预算截断单次调用超时（单一预算常量
            # 来源），经保留载荷键透传给适配器的 HTTP 客户端；未传入预算
            # 时不注入（适配器使用默认超时）。
            call_payload = payload
            if budget is not None:
                timeout_seconds = budget.model_call_timeout_ms() / 1000
                call_payload = {**payload, REQUEST_TIMEOUT_SECONDS_KEY: timeout_seconds}
            try:
                adapter_result = adapter.call(capability, run_context, call_payload)
            except (RateLimitError, TransientError) as exc:
                retry_count = attempt - 1
                if attempt < retry_policy.max_attempts:
                    backoff = retry_policy.backoff_seconds * (2 ** (attempt - 1))
                    if retry_policy.jitter:
                        backoff *= random.uniform(0.5, 1.5)
                    # Issue 06 第七轮：预算放不下「退避 + 一次最小调用窗口 +
                    # 交接预留」时不重试——以真实错误终态收尾（重试次数如实
                    # 记为已发生次数），绝不再等退避、再吃一次调用窗口。
                    if budget is not None and not budget.can_retry_model_call(
                        backoff_ms=int(backoff * 1000)
                    ):
                        lock = self._build_lock(
                            run_context,
                            capability,
                            ModelCallStatus.RETRYABLE_FAIL,
                            attempted,
                            retry_count,
                            degradation_reason=str(exc),
                            error_code=exc.code,
                            error_message=exc.message,
                            payload=call_payload,
                        )
                        return (
                            ModelCallResult(
                                status=ModelCallStatus.RETRYABLE_FAIL,
                                lock=lock,
                                error_code=exc.code,
                                error_message=exc.message,
                                degradation_reason=str(exc),
                            ),
                            lock,
                        )
                    if backoff > 0:
                        time.sleep(backoff)
                    continue
                # Exhausted retries on this capability.
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.RETRYABLE_FAIL,
                    attempted,
                    retry_count,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=call_payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.RETRYABLE_FAIL,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )
            except (RegionError, AuthError) as exc:
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.BLOCKED,
                    attempted,
                    retry_count=attempt - 1,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.BLOCKED,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )
            except AdapterError as exc:
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.BLOCKED,
                    attempted,
                    retry_count=attempt - 1,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.BLOCKED,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )

            # Issue 09：实际返回模型与批准 ID 不一致时调用失败关闭，绝不
            # 只记录警告后继续——运行锁如实记录漂移的实际模型，作为门禁
            # ``actual_model_mismatch`` 的可复核证据。
            actual_model_id = adapter_result.actual_model_id
            if actual_model_id is not None and actual_model_id != capability.model_id:
                lock = self._build_mismatch_lock(
                    run_context,
                    capability,
                    actual_model_id,
                    attempted,
                    retry_count=attempt - 1,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.BLOCKED,
                        lock=lock,
                        error_code="actual_model_mismatch",
                        error_message=lock.error_message,
                        degradation_reason=lock.degradation_reason,
                    ),
                    lock,
                )

            lock = self._build_lock(
                run_context,
                capability,
                ModelCallStatus.SUCCESS,
                attempted,
                retry_count=attempt - 1,
                actual_model_id=actual_model_id,
                usage=adapter_result.usage,
                payload=call_payload,
            )
            return (
                ModelCallResult(
                    status=ModelCallStatus.SUCCESS,
                    lock=lock,
                    output=adapter_result.output,
                ),
                lock,
            )

        # Unreachable, but keeps mypy happy when the loop has no normal exit.
        lock = self._build_lock(
            run_context,
            capability,
            ModelCallStatus.BLOCKED,
            attempted,
            retry_count,
            degradation_reason="unexpected empty invocation",
            error_code="unexpected",
            error_message="Unexpected empty invocation path.",
            payload=payload,
        )
        return (
            ModelCallResult(
                status=ModelCallStatus.BLOCKED,
                lock=lock,
                error_code="unexpected",
                error_message="Unexpected empty invocation path.",
            ),
            lock,
        )

    def _build_mismatch_lock(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        actual_model_id: str,
        fallback_path: list[str],
        retry_count: int,
        payload: dict[str, Any] | None = None,
    ) -> ModelRunLock:
        """构造 ``actual_model_mismatch`` 失败锁（Issue 09 invoke/stream 共用）。

        运行锁如实记录漂移的实际模型 ID，作为门禁报告的可复核证据。
        """
        return self._build_lock(
            run_context,
            capability,
            ModelCallStatus.BLOCKED,
            fallback_path,
            retry_count=retry_count,
            degradation_reason=(
                f"实际返回模型 {actual_model_id} 与批准模型 "
                f"{capability.model_id} 不一致。"
            ),
            actual_model_id=actual_model_id,
            error_code="actual_model_mismatch",
            error_message=(
                f"实际返回模型 {actual_model_id} 与批准模型 "
                f"{capability.model_id} 不一致，调用失败关闭。"
            ),
            payload=payload,
        )

    def _build_lock(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        status: ModelCallStatus,
        fallback_path: list[str],
        retry_count: int,
        degradation_reason: str | None = None,
        actual_model_id: str | None = None,
        usage: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ModelRunLock:
        return ModelRunLock(
            lock_id=secrets.token_urlsafe(16),
            run_id=run_context.run_id,
            account_id=run_context.account_id,
            project_id=run_context.project_id,
            capability_name=capability.name,
            capability_version=capability.version,
            actual_model_id=actual_model_id or capability.model_id,
            region=capability.region,
            parameters=self._capture_parameters(payload),
            prompt_version=capability.prompt_version,
            input_output_contract=f"{capability.name}:{capability.input_schema_version}->{capability.output_schema_version}",
            fallback_path=list(fallback_path),
            status=status,
            retry_count=retry_count,
            degradation_reason=degradation_reason,
            error_code=error_code,
            error_message=error_message,
            created_at=datetime.now(UTC),
            usage=usage,
        )

    @staticmethod
    def _capture_parameters(payload: dict[str, Any] | None) -> dict[str, Any]:
        """Extract non-secret invocation parameters from payload.

        Only well-known parameter keys are captured; unknown keys are
        silently ignored to avoid leaking sensitive payload fields into
        the immutable run lock.
        """
        params: dict[str, Any] = {}
        for key in ("temperature", "max_tokens", "top_p"):
            if payload and key in payload:
                params[key] = payload[key]
        # Issue 06 第七轮：记录按剩余预算截断后的单次调用超时（秒）——
        # 结构化技能审计可据此区分「完整调用窗口」与「被预算截断的调用」。
        if payload and REQUEST_TIMEOUT_SECONDS_KEY in payload:
            params[REQUEST_TIMEOUT_SECONDS_KEY] = payload[REQUEST_TIMEOUT_SECONDS_KEY]
        # TTS-specific parameters (T062). These are captured when present so
        # the run lock records the voice, language and format used.
        for key in ("voice", "language_type", "format", "sample_rate"):
            if payload and key in payload:
                params[key] = payload[key]
        # Embedding metadata (Issue 15): batch size/ordinal, provider and the
        # fixed dimension/normalization contract. Input texts are deliberately
        # absent from this whitelist, so embedding payloads never leak into the
        # lock. Measured latency is stamped by the embedding port after the
        # call returns.
        for key in (
            "batch_size",
            "batch_ordinal",
            "normalization",
            "dimensions",
            "provider",
        ):
            if payload and key in payload:
                params[key] = payload[key]
        policy = payload.get("global_writing_policy") if payload else None
        if isinstance(policy, dict):
            # 只记录版本、模式、切片数量等脱敏诊断字段，绝不把画像正文
            # 或系统提示写进模型运行锁。
            for source_key, target_key in (
                ("version", "global_writing_policy_version"),
                ("mode", "global_writing_policy_mode"),
                ("form", "global_writing_policy_form"),
                ("rule_count", "global_writing_policy_rule_count"),
                ("profile_slice_id", "global_writing_policy_profile_slice_id"),
                ("profile_item_count", "global_writing_policy_profile_item_count"),
                ("snapshot_complete", "global_writing_policy_snapshot_complete"),
                ("fallback_reason", "global_writing_policy_fallback_reason"),
                ("degradation_reason", "global_writing_policy_degradation_reason"),
            ):
                if source_key in policy:
                    params[target_key] = policy[source_key]
        if not params:
            params = {"temperature": 0.7, "max_tokens": 1024}
        return params

    def _blocked_result(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        error_code: str,
        error_message: str,
        attempted: list[str] | None = None,
    ) -> ModelCallResult:
        lock = self._build_lock(
            run_context,
            capability,
            ModelCallStatus.BLOCKED,
            attempted or [f"{capability.name}@{capability.version}"],
            retry_count=0,
            degradation_reason=error_message,
            error_code=error_code,
            error_message=error_message,
        )
        return ModelCallResult(
            status=ModelCallStatus.BLOCKED,
            lock=lock,
            error_code=error_code,
            error_message=error_message,
            degradation_reason=error_message,
        )
