"""知识库图片 OCR 端口。

摄取服务只依赖本文件定义的端口，不在解析器内发起网络请求。生产端口复用
既有 Qwen OCR 适配器与客户端；无全局凭据或调用失败时向上抛出领域错误，
由摄取服务回退到图片元数据。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.ai.qwen_client import CassetteStore, QwenApiClient
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)
from bridges.contracts.workflows import RunContextEnvelope

# 与既有 media Qwen OCR 管线保持同一固定 prompt；这里不导入 media 包，
# 避免 media → chat → ingestion 的模块循环。
OCR_IMAGE_PROMPT = (
    "Extract all visible text from this scientific image. "
    "Preserve the reading order. Do not add commentary."
)


class OcrError(Exception):
    """图片文字识别失败；message 为面向用户的中文原因。"""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class OcrPort(Protocol):
    """图片文字识别端口：生产实现调用 Qwen，测试注入确定性替身。"""

    def extract(self, account_id: str, content: bytes, media_type: str) -> str:
        """提取图片中的可见文字；失败抛出 OcrError。"""


def _qwen_ocr_capability(region: str) -> CapabilityRecord:
    """构造与既有 qwen_ocr 注册表一致的适配器能力快照。"""
    return CapabilityRecord(
        name="qwen_ocr",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region=region,
        model_id="qwen-vl-ocr",
        input_schema_version="image-ocr-v1",
        output_schema_version="ocr-text-v1",
        supported_modalities=["text", "image"],
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0),
        prompt_version="2026-07-24",
    )


class QwenOcrPort:
    """经既有 Qwen OCR 适配器执行单张图片文字识别。"""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        region: str = "cn-beijing",
        workspace_id: str | None = None,
        cassette_dir: str | None = None,
        record_mode: bool = False,
    ) -> None:
        self._api_key = api_key
        cassette_store = CassetteStore(Path(cassette_dir)) if cassette_dir else None
        client = QwenApiClient(
            api_key=api_key,
            workspace_id=workspace_id,
            region=region,
            cassette_store=cassette_store,
            record_mode=record_mode,
        )
        self._adapter = QwenOcrAdapter(client)
        self._capability = _qwen_ocr_capability(region)

    def extract(self, account_id: str, content: bytes, media_type: str) -> str:
        """只把当前图片与固定 prompt 发送到 Qwen OCR。"""
        if self._api_key is None or not self._api_key.get_secret_value():
            raise OcrError(
                "图片文字识别未启用：未配置全局百炼运行凭据，请检查启动服务的全局配置。"
            )
        if not content:
            raise OcrError("图片文字识别失败：图片内容为空。")

        try:
            result = self._adapter.call(
                self._capability,
                RunContextEnvelope(
                    run_id=f"knowledge-base-ocr-{account_id}",
                    account_id=account_id,
                    project_id="",
                    workflow_name="knowledge_base_ingestion",
                    workflow_version="1",
                    submitted_at=datetime.now(UTC),
                ),
                payload={
                    "image_base64": base64.b64encode(content).decode("ascii"),
                    "mime_type": media_type,
                    "prompt": OCR_IMAGE_PROMPT,
                    "temperature": 0.01,
                    "max_tokens": 4096,
                },
            )
        except AuthError as exc:
            raise OcrError(
                "图片文字识别失败：全局百炼凭据无效或没有 OCR 模型权限，"
                "请检查启动服务的全局配置与权限。"
            ) from exc
        except RegionError as exc:
            raise OcrError("图片文字识别失败：区域接入点不可达，请检查网络。") from exc
        except RateLimitError as exc:
            raise OcrError(
                "图片文字识别失败：请求过于频繁（限流），稍后重试即可。", retryable=True
            ) from exc
        except TransientError as exc:
            raise OcrError(
                "图片文字识别失败：服务暂时不可用或网络异常，稍后重试即可。", retryable=True
            ) from exc
        except AdapterError as exc:
            raise OcrError(
                "图片文字识别失败：OCR 服务调用未成功。", retryable=exc.retryable
            ) from exc

        text = result.output.get("content", "")
        if not isinstance(text, str) or not text.strip():
            raise OcrError("图片文字识别失败：OCR 未返回可用文字。")
        return text.strip()
