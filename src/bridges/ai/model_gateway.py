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
from typing import Any

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    StreamEvent,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from bridges.contracts.ai import (
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallResult,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.workflows import RunContextEnvelope


class ModelGatewayError(Exception):
    """Domain error for gateway-level failures."""


class ModelGateway:
    """Resolve capabilities, invoke adapters, and record run locks."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry
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

    def invoke(
        self,
        capability_name: str,
        capability_version: str,
        run_context: RunContextEnvelope,
        payload: dict[str, Any] | None = None,
    ) -> ModelCallResult:
        """Invoke a capability and return a result with an immutable run lock.

        The gateway never silently crosses regions or swaps to an unverified
        model. All attempted capabilities are recorded in the lock's fallback
        path.
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

        fallback_result, _ = self._invoke_capability(
            fallback,
            fallback_adapter,
            run_context,
            payload,
            attempted,
        )
        return fallback_result

    def stream(
        self,
        capability_name: str,
        capability_version: str,
        run_context: RunContextEnvelope,
        payload: dict[str, Any] | None = None,
    ) -> Iterator[StreamEvent]:
        """流式调用一个能力，逐块产出事件并在结束时附带不可变运行锁。

        与 ``invoke`` 的差异：真正的流式适配器路径不做网关级自动重试与
        降级——聊天以"新建助手尝试"作为用户级重试机制（Issue 11）。
        连接阶段失败（尚未产出任何增量）直接以 error 事件结束；已开始
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
        try:
            for chunk in stream_call(primary, run_context, payload):
                if chunk.kind == "delta":
                    yield StreamEvent(kind="delta", delta=chunk.delta)
                elif chunk.kind == "error":
                    lock = self._build_lock(
                        run_context,
                        primary,
                        self._lock_status_for_code(chunk.error_code or "unknown"),
                        [f"{primary.name}@{primary.version}"],
                        retry_count=0,
                        degradation_reason=chunk.error_message,
                        actual_model_id=primary.model_id,
                        error_code=chunk.error_code,
                        error_message=chunk.error_message,
                        payload=payload,
                    )
                    yield StreamEvent(
                        kind="error",
                        error_code=chunk.error_code,
                        error_message=chunk.error_message,
                        lock=lock,
                    )
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
                            retry_count=0,
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
        except (RateLimitError, TransientError, RegionError, AuthError, AdapterError) as exc:
            lock = self._build_lock(
                run_context,
                primary,
                self._lock_status_for_code(exc.code),
                [f"{primary.name}@{primary.version}"],
                retry_count=0,
                degradation_reason=str(exc),
                actual_model_id=primary.model_id,
                error_code=exc.code,
                error_message=exc.message,
                payload=payload,
            )
            yield StreamEvent(
                kind="error",
                error_code=exc.code,
                error_message=exc.message,
                lock=lock,
            )
            return

        lock = self._build_lock(
            run_context,
            primary,
            ModelCallStatus.SUCCESS,
            [f"{primary.name}@{primary.version}"],
            retry_count=0,
            actual_model_id=actual_model_id,
            usage=usage,
            payload=payload,
        )
        yield StreamEvent(kind="done", lock=lock, usage=usage)

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
    ) -> tuple[ModelCallResult, ModelRunLock]:
        attempted.append(f"{capability.name}@{capability.version}")
        retry_policy = capability.retry_policy
        retry_count = 0

        for attempt in range(1, retry_policy.max_attempts + 1):
            try:
                adapter_result = adapter.call(capability, run_context, payload)
            except (RateLimitError, TransientError) as exc:
                retry_count = attempt - 1
                if attempt < retry_policy.max_attempts:
                    if retry_policy.backoff_seconds > 0:
                        backoff = retry_policy.backoff_seconds * (2 ** (attempt - 1))
                        if retry_policy.jitter:
                            backoff *= random.uniform(0.5, 1.5)
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
                    payload=payload,
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
                payload=payload,
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
        # TTS-specific parameters (T062). These are captured when present so
        # the run lock records the voice, language and format used.
        for key in ("voice", "language_type", "format", "sample_rate"):
            if payload and key in payload:
                params[key] = payload[key]
        # Embedding metadata (Issue 15): batch size/ordinal and the fixed
        # dimension/normalization contract. Input texts are deliberately absent
        # from this whitelist, so embedding payloads never leak into the lock.
        for key in ("batch_size", "batch_ordinal", "normalization", "dimensions"):
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
