"""真实 Wan 文生视频适配器（Issue 32，ADR-0007 唯一非 Qwen 系列例外）。

DashScope 视频合成是原生异步任务：提交（POST
``/api/v1/services/aigc/video-generation/video-synthesis``）返回
``task_id``，后台执行器每轮用 GET ``/api/v1/tasks/{task_id}`` 单次
轮询，终态成功后下载临时结果 URL 的字节交给调用方转存账户对象库。
适配器按 payload 的 ``kind`` 分四种模式，全部经 ModelGateway 进入
不可变运行锁：

- ``submit``：提示词 → 返回 ``cloud_task_id``（或同步结果 URL）；
- ``poll``：单次查询云端任务状态 → RUNNING / SUCCEEDED(带结果 URL) /
  FAILED(带原因)；响应字段兼容 ``status`` 与 ``task_status``，结果
  地址优先 ``video_url``、兜底 ``results[0].url``；
- ``fetch``：下载临时结果 URL 字节；
- ``cancel``：尽力取消云端任务。

固定模型 wan2.7-t2v-2026-06-12 与固定尺寸 1280*720（与能力矩阵探测
参数一致，用户不可选）。失败分类（429/401/5xx/超时）复用 qwen_client，
网关据以判定是否可重试；任何路径都不切换模型或模拟成功。请求只携带
提示词，不发送完整聊天、画像、项目目录或任何账户秘密。
"""

from __future__ import annotations

from typing import Any

import httpx

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
)
from bridges.ai.qwen_client import QwenApiClient
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope

#: 固定生成尺寸（与能力矩阵探测参数一致，用户不可选）。
DEFAULT_VIDEO_SIZE = "1280*720"

#: 结果下载超时（视频文件较大，允许更长等待）。
_RESULT_DOWNLOAD_TIMEOUT_SECONDS = 300.0

#: 支持的视频媒体类型（下载字节判型）。
SUPPORTED_RESULT_MEDIA_TYPES = {
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/quicktime",
}


class QwenWanAdapter(CapabilityAdapter):
    """固定视频模型（wan2.7-t2v-2026-06-12）的异步任务适配器。

    Wan 是 ADR-0007 批准的模型矩阵唯一非 Qwen 系列例外：使用全局百炼
    运行凭据，遵守固定模型绑定与运行锁合同，只是模型系列不是 Qwen。
    """

    def __init__(
        self,
        client: QwenApiClient,
        download_client: httpx.Client | None = None,
    ) -> None:
        self._client = client
        self._download_client = download_client or httpx.Client(
            timeout=_RESULT_DOWNLOAD_TIMEOUT_SECONDS
        )

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        kind = str(payload.get("kind") or "submit")
        if kind == "submit":
            return self._submit(capability, payload)
        if kind == "poll":
            return self._poll(capability, payload)
        if kind == "fetch":
            return self._fetch(capability, payload)
        if kind == "cancel":
            return self._cancel(capability, payload)
        raise AdapterError(
            code="unknown_mode",
            message=f"未知视频任务模式：{kind}。",
            retryable=False,
        )

    def _cancel(
        self, capability: CapabilityRecord, payload: dict[str, Any]
    ) -> AdapterResult:
        """尽力取消云端任务；本地取消是权威，失败由调用方静默处理。"""
        cloud_task_id = str(payload.get("cloud_task_id") or "")
        if not cloud_task_id:
            raise AdapterError(
                code="missing_cloud_task_id",
                message="缺少云端任务标识。",
                retryable=False,
            )
        response_body = self._client.dashscope_task_cancel(cloud_task_id)
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"cancelled": True},
            usage=response_body.get("usage") if isinstance(
                response_body.get("usage"), dict
            ) else None,
        )

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------

    def _submit(
        self, capability: CapabilityRecord, payload: dict[str, Any]
    ) -> AdapterResult:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise AdapterError(
                code="missing_prompt",
                message="视频请求必须包含非空提示词。",
                retryable=False,
            )
        body: dict[str, Any] = {
            "model": capability.model_id,
            "input": {"prompt": prompt},
            "parameters": {
                "size": str(payload.get("size") or DEFAULT_VIDEO_SIZE),
            },
        }
        # 视频合成是异步优先服务：必须带 X-DashScope-Async: enable 头，
        # 否则服务端以 403 AccessDenied（does not support synchronous
        # calls）拒绝同步调用。
        response_body = self._client.dashscope_native(
            "/api/v1/services/aigc/video-generation/video-synthesis",
            body,
            async_call=True,
        )
        output = response_body.get("output")
        if not isinstance(output, dict):
            raise AdapterError(
                code="invalid_response",
                message="视频生成接口返回格式异常。",
                retryable=False,
            )
        task_id = output.get("task_id")
        if isinstance(task_id, str) and task_id:
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"cloud_task_id": task_id},
                usage=response_body.get("usage") if isinstance(
                    response_body.get("usage"), dict
                ) else None,
            )
        # 供应商也可能同步返回结果 URL（同探测语义）。
        result_url = self._extract_result_url(output)
        if result_url:
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "cloud_task_id": "",
                    "result_url": result_url,
                },
                usage=response_body.get("usage") if isinstance(
                    response_body.get("usage"), dict
                ) else None,
            )
        raise AdapterError(
            code="task_rejected",
            message="视频生成任务未被接受，请稍后重试。",
            retryable=True,
        )

    # ------------------------------------------------------------------
    # 单次轮询
    # ------------------------------------------------------------------

    def _poll(
        self, capability: CapabilityRecord, payload: dict[str, Any]
    ) -> AdapterResult:
        cloud_task_id = str(payload.get("cloud_task_id") or "")
        if not cloud_task_id:
            raise AdapterError(
                code="missing_cloud_task_id",
                message="缺少云端任务标识。",
                retryable=False,
            )
        response_body = self._client.dashscope_task_get(cloud_task_id)
        output = response_body.get("output")
        if not isinstance(output, dict):
            raise AdapterError(
                code="invalid_response",
                message="视频任务查询返回格式异常。",
                retryable=False,
            )
        # 兼容 DashScope 原生任务的两种状态字段（status / task_status）。
        cloud_status = str(
            output.get("status") or output.get("task_status") or ""
        ).upper()
        if cloud_status in ("SUCCEEDED", "COMPLETED"):
            result_url = self._extract_result_url(output)
            if not result_url:
                raise AdapterError(
                    code="empty_result",
                    message="视频任务完成但没有返回视频。",
                    retryable=False,
                )
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "cloud_status": "SUCCEEDED",
                    "result_url": result_url,
                },
            )
        if cloud_status in ("FAILED", "CANCELED", "CANCELLED", "UNKNOWN"):
            error_message = str(
                output.get("message") or output.get("code") or "云端生成失败"
            )
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "cloud_status": "FAILED",
                    "error_message": error_message,
                },
            )
        # PENDING / RUNNING / PROCESSING 及其他中间状态：保持生成中，
        # 下一轮再查。
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"cloud_status": "RUNNING"},
        )

    # ------------------------------------------------------------------
    # 下载结果字节
    # ------------------------------------------------------------------

    def _fetch(
        self, capability: CapabilityRecord, payload: dict[str, Any]
    ) -> AdapterResult:
        result_url = str(payload.get("result_url") or "")
        if not result_url:
            raise AdapterError(
                code="missing_result_url",
                message="缺少视频结果地址。",
                retryable=False,
            )
        try:
            response = self._download_client.get(result_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterError(
                code="download_failed",
                message=f"视频结果下载失败：{exc}",
                retryable=True,
            ) from exc
        video_bytes = response.content
        if not video_bytes:
            raise AdapterError(
                code="empty_video",
                message="视频结果为空。",
                retryable=True,
            )
        media_type = str(
            response.headers.get("content-type")
            or self._guess_media_type(result_url)
            or "video/mp4"
        ).split(";")[0].strip().lower()
        if media_type not in SUPPORTED_RESULT_MEDIA_TYPES:
            media_type = "video/mp4"
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "video_bytes": video_bytes,
                "media_type": media_type,
            },
            usage={
                "bytes": len(video_bytes),
            },
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_result_url(output: dict[str, Any]) -> str | None:
        """提取视频结果地址：优先 ``video_url``，兜底 ``results[0].url``。"""
        direct = output.get("video_url")
        if isinstance(direct, str) and direct:
            return str(direct)
        results = output.get("results")
        if (
            isinstance(results, list)
            and results
            and isinstance(results[0], dict)
            and isinstance(results[0].get("url"), str)
        ):
            return str(results[0]["url"])
        return None

    @staticmethod
    def _guess_media_type(url: str) -> str | None:
        lowered = url.lower()
        for suffix, media_type in (
            (".mp4", "video/mp4"),
            (".webm", "video/webm"),
            (".ogg", "video/ogg"),
            (".mov", "video/quicktime"),
            (".m4v", "video/mp4"),
        ):
            if lowered.endswith(suffix):
                return media_type
        return None


__all__ = [
    "DEFAULT_VIDEO_SIZE",
    "QwenWanAdapter",
    "SUPPORTED_RESULT_MEDIA_TYPES",
]
