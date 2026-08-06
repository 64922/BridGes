"""固定能力矩阵的真实探测（ADR-0009 合同）。

保存百炼 Key 后，系统以非用户数据逐项真实调用供应商 API：核心对话、
知识库向量化、语音转写、语音朗读、图片生成与编辑、视频生成。每项探测
独立执行，一项失败不影响其他项；失败只重试同一模型绑定，绝不静默降级
到其他模型或 Stub。每次探测生成不可变的 ``ProbeRecord``（含追踪标识
``probe_id``、模型、区域、参数快照），满足可审计与可复现要求。

图片与视频为异步生成任务：探测以"任务提交被接受"作为该能力的真实可用
结论（提交阶段即校验了 Key 权限与模型存在性），完整生成交由后续生成
功能（Issue 31/32）执行。全部探测均使用固定非用户输入，不消费任何
用户数据。
"""

from __future__ import annotations

import base64
import io
import secrets
import struct
import threading
import time
import wave
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.ai.qwen_asr_adapter import QwenAsrAdapter
from bridges.ai.qwen_client import QwenApiClient
from bridges.ai.qwen_tts_adapter import QwenTtsAdapter
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    RetryPolicy,
)
from bridges.contracts.credentials import (
    CapabilityProbeSummary,
    ProbeRecord,
    ProbeStatus,
)
from bridges.credentials.matrix import FIXED_CAPABILITY_MATRIX, CapabilityBinding
from bridges.persistence import StateStore

#: 同一绑定内的最大尝试次数（瞬态失败重试，不更换模型）。
_PROBE_MAX_ATTEMPTS = 2
#: 瞬态重试间隔（秒）。
_PROBE_RETRY_BACKOFF_SECONDS = 0.5
#: PROBING 状态超过该时长视为中断的探测，展示回退为"未探测"。
_STALE_PROBING_TTL_SECONDS = 600

#: 探测状态持久化的 StateStore 命名空间。
_PROBES_NAMESPACE = "key_probes"

#: 探测用非用户固定文本。
_PROBE_TEXT = "测试"
_IMAGE_PROBE_PROMPT = "一座桥的素描"
_VIDEO_PROBE_PROMPT = "一条静静流淌的河"


class ProbeError(Exception):
    """探测失败；message 为面向用户的中文原因。"""

    def __init__(self, message: str, code: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ProbeOutcome:
    """单项探测结果（内部结构，非对外契约）。"""

    def __init__(
        self,
        *,
        success: bool,
        message: str,
        error_code: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.message = message
        self.error_code = error_code
        self.usage = usage


class ProbeRunnerPort(ABC):
    """探测执行端口：生产实现真实调用供应商，测试注入替身。"""

    @abstractmethod
    def probe(
        self, binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        """对固定绑定执行一次真实探测，返回结果与中文说明。"""


class RealCapabilityProbeRunner(ProbeRunnerPort):
    """真实探测执行器：逐项调用 DashScope / OpenAI 兼容端点。"""

    def probe(
        self, binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        kind = binding.probe_kind
        if kind == "chat":
            return self._probe_chat(binding, client)
        if kind == "embedding":
            return self._probe_embedding(binding, client)
        if kind == "asr":
            return self._probe_asr(binding, client)
        if kind == "tts":
            return self._probe_tts(binding, client)
        if kind == "image":
            return self._probe_image(binding, client)
        if kind == "video":
            return self._probe_video(binding, client)
        raise ProbeError(f"未知探测类型：{kind}", code="unknown_probe_kind")

    @staticmethod
    def _probe_chat(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        body: dict[str, Any] = {
            "model": binding.model_id,
            "messages": [{"role": "user", "content": _PROBE_TEXT}],
            "max_tokens": int(binding.parameters.get("max_tokens", 8)),
            "temperature": float(binding.parameters.get("temperature", 0.0)),
        }
        response = client.chat_completions(body)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProbeError("模型返回了空响应，请稍后重试。", code="empty_response")
        return ProbeOutcome(
            success=True,
            message="核心对话探测成功。",
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else None,
        )

    @staticmethod
    def _probe_embedding(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        body: dict[str, Any] = {
            "model": binding.model_id,
            "input": _PROBE_TEXT,
        }
        response = client.embeddings(body)
        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise ProbeError("向量接口返回了空响应，请稍后重试。", code="empty_response")
        first = data[0]
        if not isinstance(first, dict):
            raise ProbeError("向量接口返回格式异常。", code="invalid_response")
        embedding = first.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ProbeError("向量接口返回了空向量。", code="empty_embedding")
        expected_dimensions = int(binding.parameters.get("dimensions", 1024))
        if len(embedding) != expected_dimensions:
            raise ProbeError(
                f"向量维度为 {len(embedding)}，与固定矩阵的 "
                f"{expected_dimensions} 维不符。",
                code="dimension_mismatch",
            )
        return ProbeOutcome(
            success=True,
            message="知识库向量化探测成功（1024 维）。",
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else None,
        )

    @staticmethod
    def _probe_asr(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        adapter = QwenAsrAdapter(client)
        capability = _binding_record(binding, name="qwen_asr_short")
        audio_base64 = _make_probe_wav()
        result = adapter.call(
            capability,
            _probe_run_context(),
            {
                "audio_base64": audio_base64,
                "mime_type": "audio/wav",
                "duration_seconds": float(binding.parameters.get("duration_seconds", 1)),
            },
        )
        if not isinstance(result.output.get("transcript"), str):
            raise ProbeError("语音转写接口返回格式异常。", code="invalid_response")
        return ProbeOutcome(
            success=True,
            message="语音转写探测成功。",
            usage=result.usage,
        )

    @staticmethod
    def _probe_tts(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        adapter = QwenTtsAdapter(client)
        capability = _binding_record(binding, name="qwen_tts")
        result = adapter.call(
            capability,
            _probe_run_context(),
            {
                "text": _PROBE_TEXT,
                "voice": str(binding.parameters.get("voice", "Cherry")),
            },
        )
        audio_url = result.output.get("audio_url")
        if not isinstance(audio_url, str) or not audio_url:
            raise ProbeError("语音朗读接口未返回音频。", code="empty_response")
        return ProbeOutcome(
            success=True,
            message="语音朗读探测成功。",
            usage=result.usage,
        )

    @staticmethod
    def _probe_image(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        # 同步多模态生成（与适配器同构）：成功响应为
        # output.choices[0].message.content 列表中的 {"image": url} 项。
        body: dict[str, Any] = {
            "model": binding.model_id,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": _IMAGE_PROBE_PROMPT}]}
                ]
            },
            "parameters": {
                "size": str(binding.parameters.get("size", "1024*1024")),
                "n": int(binding.parameters.get("n", 1)),
            },
        }
        response = client.dashscope_native(
            "/api/v1/services/aigc/multimodal-generation/generation",
            body,
            timeout=180.0,
        )
        output = response.get("output")
        if not isinstance(output, dict):
            raise ProbeError("图片生成接口返回格式异常。", code="invalid_response")
        choices = output.get("choices")
        content = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices
            else None
        )
        has_image = isinstance(content, list) and any(
            isinstance(item, dict) and isinstance(item.get("image"), str)
            for item in content
        )
        if not has_image:
            raise ProbeError("图片生成任务未被接受。", code="task_rejected")
        return ProbeOutcome(success=True, message="图片生成与编辑探测成功。")

    @staticmethod
    def _probe_video(
        binding: CapabilityBinding, client: QwenApiClient
    ) -> ProbeOutcome:
        body: dict[str, Any] = {
            "model": binding.model_id,
            "input": {"prompt": _VIDEO_PROBE_PROMPT},
            "parameters": {"size": str(binding.parameters.get("size", "1280*720"))},
        }
        # 视频合成是异步优先服务：必须带 X-DashScope-Async: enable 头，
        # 否则 403 AccessDenied（与适配器同构）。
        response = client.dashscope_native(
            "/api/v1/services/aigc/video-generation/video-synthesis",
            body,
            async_call=True,
        )
        output = response.get("output")
        if not isinstance(output, dict) or not output.get("task_id"):
            raise ProbeError("视频生成任务未被接受。", code="task_rejected")
        return ProbeOutcome(success=True, message="视频生成探测成功。")


def _binding_record(
    binding: CapabilityBinding, *, name: str
) -> CapabilityRecord:
    """为适配器构造与固定绑定一致的临时能力记录。"""
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=binding.model_id,
        input_schema_version="probe-v1",
        output_schema_version="probe-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
        fallback_policy=FallbackPolicy(),
        prompt_version="2026-08-03",
    )


def _probe_run_context() -> Any:
    """为适配器调用构造合成运行上下文（探测不产生工作流运行）。"""
    from bridges.contracts.workflows import RunContextEnvelope

    return RunContextEnvelope(
        run_id=f"probe-{secrets.token_urlsafe(8)}",
        account_id="probe",
        project_id="probe",
        workflow_name="capability_probe",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _make_probe_wav() -> str:
    """生成 1 秒静音 WAV（16kHz 单声道 16 位），base64 编码。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<h", 0) * 16000)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class CapabilityProbeService:
    """编排固定矩阵探测，持久化不可变记录并提供状态快照。

    探测失败只停用对应能力：服务状态与 API 路由对该能力返回"不可用"与
    中文原因，登录、资料、密钥修改与不依赖 AI 的本地功能不受影响。
    """

    def __init__(
        self,
        *,
        state_store: StateStore | None = None,
        runner: ProbeRunnerPort | None = None,
        region: str = "cn-beijing",
        workspace_id: str | None = None,
        cassette_dir: str | None = None,
        record_mode: bool = False,
    ) -> None:
        self._state_store = state_store
        self._runner = runner or RealCapabilityProbeRunner()
        self._region = region
        self._workspace_id = workspace_id
        self._cassette_dir = cassette_dir
        self._record_mode = record_mode
        self._records: dict[str, dict[str, ProbeRecord]] = {}
        #: 全局状态锁：保护 _records 映射的所有读写（后台探测线程与
        #: 请求线程并发访问同一映射，逐账户锁不足以保护结构本身）。
        self._records_lock = threading.RLock()
        #: 逐账户探测执行锁：防止同一账户并发重复探测。
        self._locks: dict[str, Any] = {}
        self._locks_guard = threading.Lock()
        self._load_state()

    # ------------------------------------------------------------------
    # 状态读写
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load(_PROBES_NAMESPACE) or {}
        raw = state.get("records")
        if not isinstance(raw, dict):
            return
        with self._records_lock:
            for account_id, per_account in raw.items():
                if not isinstance(per_account, dict):
                    continue
                self._records[account_id] = {
                    capability_id: ProbeRecord.model_validate(value)
                    for capability_id, value in per_account.items()
                    if isinstance(value, dict)
                }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        with self._records_lock:
            records_snapshot = {
                account_id: dict(per_account)
                for account_id, per_account in self._records.items()
            }
        self._state_store.save(
            _PROBES_NAMESPACE,
            {
                "records": {
                    account_id: {
                        capability_id: record.model_dump(mode="json")
                        for capability_id, record in per_account.items()
                    }
                    for account_id, per_account in records_snapshot.items()
                }
            },
        )

    def _record_for(self, account_id: str, capability_id: str) -> ProbeRecord | None:
        with self._records_lock:
            return self._records.get(account_id, {}).get(capability_id)

    def _put_record(self, account_id: str, record: ProbeRecord) -> None:
        with self._records_lock:
            self._records.setdefault(account_id, {})[record.capability_id] = record
        self._persist()

    def _lock_for(self, account_id: str) -> Any:
        with self._locks_guard:
            return self._locks.setdefault(account_id, threading.Lock())

    def reset(self, account_id: str) -> None:
        """清除全部探测记录（保存或删除 Key 后调用）。"""
        with self._records_lock:
            self._records.pop(account_id, None)
        self._persist()

    def mark_unavailable(
        self,
        account_id: str,
        reason: str,
        capability_ids: list[str] | None = None,
    ) -> None:
        """把指定能力标记为不可用（凭据存储等基础设施错误时）。

        后台探测线程遇到基础设施错误时调用，避免页面停留在"探测中"。
        """
        now = datetime.now(UTC)
        for binding in FIXED_CAPABILITY_MATRIX:
            if capability_ids is not None and binding.capability_id not in capability_ids:
                continue
            self._put_record(
                account_id,
                ProbeRecord(
                    probe_id=secrets.token_urlsafe(16),
                    capability_id=binding.capability_id,
                    model_id=binding.model_id,
                    region=self._region,
                    parameters=dict(binding.parameters),
                    status=ProbeStatus.UNAVAILABLE,
                    probed_at=now,
                    error_code="credential_store_unavailable",
                    error_message=reason,
                ),
            )

    def mark_probing(
        self,
        account_id: str,
        capability_ids: list[str] | None = None,
    ) -> None:
        """把指定能力标记为探测中（保存后立即呈现真实状态）。

        仅用于展示与并发防重入；真正执行时 ``run_all``/``run_single`` 会
        为每次尝试创建新的不可变记录。
        """
        now = datetime.now(UTC)
        for binding in FIXED_CAPABILITY_MATRIX:
            if capability_ids is not None and binding.capability_id not in capability_ids:
                continue
            self._put_record(
                account_id,
                ProbeRecord(
                    probe_id=secrets.token_urlsafe(16),
                    capability_id=binding.capability_id,
                    model_id=binding.model_id,
                    region=self._region,
                    parameters=dict(binding.parameters),
                    status=ProbeStatus.PROBING,
                    probed_at=now,
                ),
            )

    # ------------------------------------------------------------------
    # 探测执行
    # ------------------------------------------------------------------

    def _build_client(self, api_key: SecretStr) -> QwenApiClient:
        from bridges.ai.qwen_client import CassetteStore

        cassette_store = (
            CassetteStore(Path(self._cassette_dir)) if self._cassette_dir else None
        )
        return QwenApiClient(
            api_key=api_key,
            workspace_id=self._workspace_id,
            region=self._region,
            cassette_store=cassette_store,
            record_mode=self._record_mode,
        )

    def run_all(self, account_id: str, api_key: SecretStr) -> list[ProbeRecord]:
        """对固定矩阵逐项执行真实探测；单项失败不影响其他项。"""
        with self._lock_for(account_id):
            client = self._build_client(api_key)
            records: list[ProbeRecord] = []
            for binding in FIXED_CAPABILITY_MATRIX:
                records.append(self._run_one(account_id, binding, client))
            return records

    def run_single(
        self, account_id: str, capability_id: str, api_key: SecretStr
    ) -> ProbeRecord:
        """重试单项探测（同一固定绑定，无备用模型）。"""
        binding = next(
            (b for b in FIXED_CAPABILITY_MATRIX if b.capability_id == capability_id),
            None,
        )
        if binding is None:
            raise KeyError(f"未知能力标识：{capability_id}")
        with self._lock_for(account_id):
            client = self._build_client(api_key)
            return self._run_one(account_id, binding, client)

    def _run_one(
        self,
        account_id: str,
        binding: CapabilityBinding,
        client: QwenApiClient,
    ) -> ProbeRecord:
        started = datetime.now(UTC)
        probing = ProbeRecord(
            probe_id=secrets.token_urlsafe(16),
            capability_id=binding.capability_id,
            model_id=binding.model_id,
            region=self._region,
            parameters=dict(binding.parameters),
            status=ProbeStatus.PROBING,
            probed_at=started,
        )
        self._put_record(account_id, probing)
        attempt_count = 0
        outcome: ProbeOutcome | None = None
        for attempt in range(1, _PROBE_MAX_ATTEMPTS + 1):
            attempt_count = attempt
            try:
                outcome = self._runner.probe(binding, client)
                break
            except (AuthError, RegionError) as exc:
                outcome = ProbeOutcome(
                    success=False,
                    message=_chinese_failure(exc),
                    error_code=exc.code,
                )
                break
            except (RateLimitError, TransientError) as exc:
                if attempt < _PROBE_MAX_ATTEMPTS:
                    time.sleep(_PROBE_RETRY_BACKOFF_SECONDS)
                    continue
                outcome = ProbeOutcome(
                    success=False,
                    message=_chinese_failure(exc),
                    error_code=exc.code,
                )
            except AdapterError as exc:
                outcome = ProbeOutcome(
                    success=False,
                    message=_chinese_failure(exc),
                    error_code=exc.code,
                )
                break
            except ProbeError as exc:
                outcome = ProbeOutcome(
                    success=False,
                    message=exc.message,
                    error_code=exc.code,
                )
                break
        if outcome is None:
            outcome = ProbeOutcome(
                success=False,
                message="探测未返回结果，请重试。",
                error_code="unknown",
            )
        completed = datetime.now(UTC)
        record = ProbeRecord(
            probe_id=probing.probe_id,
            capability_id=binding.capability_id,
            model_id=binding.model_id,
            region=self._region,
            parameters=dict(binding.parameters),
            status=(
                ProbeStatus.AVAILABLE if outcome.success else ProbeStatus.UNAVAILABLE
            ),
            probed_at=completed,
            attempt_count=attempt_count,
            duration_ms=max(0, int((completed - started).total_seconds() * 1000)),
            error_code=outcome.error_code,
            error_message=None if outcome.success else outcome.message,
        )
        self._put_record(account_id, record)
        return record

    # ------------------------------------------------------------------
    # 状态快照
    # ------------------------------------------------------------------

    def status_snapshot(self, account_id: str) -> list[CapabilityProbeSummary]:
        """返回固定矩阵逐项探测状态（含中断探测回退为未探测）。"""
        summaries: list[CapabilityProbeSummary] = []
        now = datetime.now(UTC)
        for binding in FIXED_CAPABILITY_MATRIX:
            record = self._record_for(account_id, binding.capability_id)
            if record is None:
                summaries.append(
                    CapabilityProbeSummary(
                        capability_id=binding.capability_id,
                        display_name=binding.display_name,
                        model_id=binding.model_id,
                        status=ProbeStatus.NOT_PROBED,
                        message="尚未探测。",
                        can_retry=False,
                    )
                )
                continue
            status = record.status
            probed_at: datetime | None = record.probed_at
            message = record.error_message or _status_message(status)
            if (
                status == ProbeStatus.PROBING
                and probed_at is not None
                and (now - probed_at).total_seconds() > _STALE_PROBING_TTL_SECONDS
            ):
                status = ProbeStatus.NOT_PROBED
                message = "上次探测中断，请重试。"
                probed_at = None
            summaries.append(
                CapabilityProbeSummary(
                    capability_id=binding.capability_id,
                    display_name=binding.display_name,
                    model_id=binding.model_id,
                    status=status,
                    message=message,
                    can_retry=status in {ProbeStatus.NOT_PROBED, ProbeStatus.UNAVAILABLE},
                    probed_at=probed_at,
                )
            )
        return summaries


def _status_message(status: ProbeStatus) -> str:
    if status == ProbeStatus.PROBING:
        return "正在探测…"
    if status == ProbeStatus.AVAILABLE:
        return "可用。"
    if status == ProbeStatus.UNAVAILABLE:
        return "不可用。"
    return "尚未探测。"


def _chinese_failure(exc: Exception) -> str:
    """把适配器异常翻译为面向用户的中文原因（不回显秘密）。"""
    if isinstance(exc, AuthError):
        return "凭据无效或没有该模型权限，请检查百炼 Key 与模型开通状态。"
    if isinstance(exc, RegionError):
        return "区域接入点不可达，请检查网络与区域配置。"
    if isinstance(exc, RateLimitError):
        return "请求过于频繁（限流），请稍后重试。"
    if isinstance(exc, TransientError):
        return "服务暂时不可用或网络异常，请稍后重试。"
    return "探测失败，请重试。"
