"""真实 Qwen 图片生成与编辑适配器（Issue 31）。

qwen-image-2.0-pro 走 DashScope **同步多模态生成**（POST
``/api/v1/services/aigc/multimodal-generation/generation``，
``input.messages`` 结构）：异步的 text2image 服务路径对该模型返回 400
（url error），multimodal-generation 异步模式又被 403 拒绝——同步调用
直接返回结果图片 URL，由调用方下载字节转存账户对象库。适配器按 payload
的 ``kind`` 分四种模式，全部经 ModelGateway 进入不可变运行锁：

- ``submit``：生成（prompt）或编辑（prompt + base_image data URL）→
  返回同步结果 URL（云端生成耗时可达数十秒，超时独立放宽）；
- ``poll``：单次查询云端任务状态（保留：历史异步任务兼容）；
- ``fetch``：下载临时结果 URL 字节；
- ``cancel``：尽力取消云端任务（本地取消是权威）。

编辑图片字节在调用方（图片服务）从账户对象库读出后以 data URL 形式
随请求体直传——请求只携带编辑所需图片与提示，不发送完整项目目录、
画像或任何账户秘密。失败分类（429/401/5xx/超时）复用 qwen_client，
网关据以判定是否可重试；任何路径都不切换模型或模拟成功。
"""

from __future__ import annotations

import base64
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
DEFAULT_IMAGE_SIZE = "1024*1024"

#: 同步生成请求超时：云端生成单张图片可达数十秒，远超默认 60 秒。
_SYNC_GENERATION_TIMEOUT_SECONDS = 180.0

#: 结果下载超时（供应商临时 URL 可能较大）。
_RESULT_DOWNLOAD_TIMEOUT_SECONDS = 120.0

#: 支持的图片媒体类型（下载字节判型）。
SUPPORTED_RESULT_MEDIA_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}


class QwenImageAdapter(CapabilityAdapter):
    """固定图片模型（qwen-image-2.0-pro-2026-06-22）的异步任务适配器。"""

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
            message=f"未知图片任务模式：{kind}。",
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
                message="图片请求必须包含非空提示词。",
                retryable=False,
            )
        base_image = payload.get("base_image")
        if base_image is not None and (
            not isinstance(base_image, str) or not base_image
        ):
            raise AdapterError(
                code="invalid_base_image",
                message="编辑请求的图片来源无效。",
                retryable=False,
            )

        # 同步多模态生成（官方格式）：生成时内容为纯文本提示；编辑时
        # 来源图片 data URL 与提示词同消息传入。异步 text2image 服务
        # 路径对 qwen-image-2.0-pro 返回 400 url error，不可用。
        content: list[dict[str, Any]] = [{"text": prompt}]
        if base_image:
            # 编辑：来源图片字节以 data URL 形式随请求体直传（最小授权
            # 上下文，不落供应商侧持久对象），供应商按图片内容约束在
            # 原图上执行指令。
            content.insert(0, {"image": base_image})

        body: dict[str, Any] = {
            "model": capability.model_id,
            "input": {
                "messages": [{"role": "user", "content": content}],
            },
            "parameters": {
                "size": str(payload.get("size") or DEFAULT_IMAGE_SIZE),
                "n": int(payload.get("n") or 1),
            },
        }

        response_body = self._client.dashscope_native(
            "/api/v1/services/aigc/multimodal-generation/generation",
            body,
            timeout=_SYNC_GENERATION_TIMEOUT_SECONDS,
        )
        result_url = self._extract_sync_result_url(response_body)
        if not result_url:
            raise AdapterError(
                code="task_rejected",
                message="图片生成任务未被接受，请稍后重试。",
                retryable=True,
            )
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
                message="图片任务查询返回格式异常。",
                retryable=False,
            )
        cloud_status = str(output.get("status") or "").upper()
        if cloud_status in ("SUCCEEDED", "COMPLETED"):
            result_url = self._extract_result_url(output)
            if not result_url:
                raise AdapterError(
                    code="empty_result",
                    message="图片任务完成但没有返回图片。",
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
        # PENDING / RUNNING 及其他中间状态：保持 running，下一轮再查。
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
                message="缺少图片结果地址。",
                retryable=False,
            )
        try:
            response = self._download_client.get(result_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterError(
                code="download_failed",
                message=f"图片结果下载失败：{exc}",
                retryable=True,
            ) from exc
        image_bytes = response.content
        if not image_bytes:
            raise AdapterError(
                code="empty_image",
                message="图片结果为空。",
                retryable=True,
            )
        media_type = str(
            response.headers.get("content-type")
            or self._guess_media_type(result_url)
            or "image/png"
        ).split(";")[0].strip().lower()
        if media_type not in SUPPORTED_RESULT_MEDIA_TYPES:
            media_type = "image/png"
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "image_bytes": image_bytes,
                "media_type": media_type,
            },
            usage={
                "bytes": len(image_bytes),
            },
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_result_url(output: dict[str, Any]) -> str | None:
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
    def _extract_sync_result_url(response_body: dict[str, Any]) -> str | None:
        """从同步多模态响应提取结果图片 URL。

        同步生成的响应为 ``output.choices[0].message.content`` 列表，
        其中承载 ``{"image": "<临时 URL>"}`` 项；编辑场景同构。
        """
        output = response_body.get("output")
        if not isinstance(output, dict):
            return None
        choices = output.get("choices")
        if not (isinstance(choices, list) and choices):
            return None
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, list):
            return None
        for item in content:
            if not isinstance(item, dict):
                continue
            url = item.get("image")
            if isinstance(url, str) and url:
                return url
        return None

    @staticmethod
    def _guess_media_type(url: str) -> str | None:
        lowered = url.lower()
        for suffix, media_type in (
            (".png", "image/png"),
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".webp", "image/webp"),
            (".gif", "image/gif"),
        ):
            if lowered.endswith(suffix):
                return media_type
        return None


def image_data_url(image_bytes: bytes, media_type: str) -> str:
    """把来源图片字节编码为请求体内联 data URL（编辑最小上下文）。"""
    mime = media_type if media_type.startswith("image/") else "image/png"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


__all__ = [
    "DEFAULT_IMAGE_SIZE",
    "QwenImageAdapter",
    "SUPPORTED_RESULT_MEDIA_TYPES",
    "image_data_url",
]
