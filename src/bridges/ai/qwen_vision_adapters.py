"""Real Qwen OCR and vision adapters for T061.

These adapters replace the deterministic placeholders used by the media and
science ingestion pipelines. They translate logical ``qwen_ocr`` and
``qwen_vision`` invocations into Qwen OpenAI-compatible Chat Completions
requests that carry base64-encoded images, and return normalized outputs that
the ingestion layer turns into ``DerivedAsset`` payloads with locators and
confidence marks.
"""

from __future__ import annotations

from typing import Any

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    CapabilityAdapter,
)
from bridges.ai.qwen_client import QwenApiClient, first_choice
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


def _data_url_for_image(image_base64: str, mime_type: str | None) -> str:
    """Build a data URL from a base64-encoded image string."""
    declared = (mime_type or "image/png").split(";")[0].strip()
    return f"data:{declared};base64,{image_base64}"


class QwenOcrAdapter(CapabilityAdapter):
    """Adapter for ``qwen_ocr``.

    The payload should contain:

    - ``image_base64`` (required): base64-encoded image bytes.
    - ``mime_type`` (optional): MIME type of the image, default ``image/png``.
    - ``prompt`` (optional): task-specific prompt. A sensible default is used
      when absent.
    - ``task`` (optional): hint such as ``advanced_recognition``,
      ``table_parsing`` or ``formula_recognition``. Recorded in the output
      but not interpreted by the adapter.
    - ``temperature`` / ``max_tokens`` (optional): forwarded to the model.

    The adapter returns the model's raw text content so the extraction layer
    can parse it according to the requested task.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        image_base64 = payload.get("image_base64")
        if not image_base64 or not isinstance(image_base64, str):
            raise AdapterError(
                code="missing_image",
                message="OCR payload must contain a base64-encoded image.",
                retryable=False,
            )

        data_url = _data_url_for_image(image_base64, payload.get("mime_type"))
        prompt = str(
            payload.get("prompt")
            or "Extract all visible text, formulas, tables and regions from this scientific image."
        )
        task = payload.get("task", "advanced_recognition")

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                        "min_pixels": payload.get("min_pixels", 3072),
                        "max_pixels": payload.get("max_pixels", 8388608),
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        request_body: dict[str, Any] = {
            "model": capability.model_id,
            "messages": messages,
            "temperature": payload.get("temperature", 0.01),
            "max_tokens": payload.get("max_tokens", 4096),
        }
        # The OCR-specific task hint is passed as an extra body parameter when
        # supported by the underlying endpoint.
        if task:
            request_body["ocr_options"] = {"task": task}

        response_body = self._client.chat_completions(request_body)
        choice = first_choice(response_body)
        content = choice.get("message", {}).get("content", "")
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={
                "content": content,
                "task": task,
            },
            usage=response_body.get("usage"),
        )


class QwenVisionAdapter(CapabilityAdapter):
    """Adapter for ``qwen_vision``.

    The payload accepts the same image fields as ``QwenOcrAdapter`` plus a
    ``prompt`` describing what the caller wants to know about the image. This
    capability is used for general visual understanding, key-frame
    interpretation and cross-modal consistency checks.
    """

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        image_base64 = payload.get("image_base64")
        if not image_base64 or not isinstance(image_base64, str):
            raise AdapterError(
                code="missing_image",
                message="Vision payload must contain a base64-encoded image.",
                retryable=False,
            )

        data_url = _data_url_for_image(image_base64, payload.get("mime_type"))
        prompt = str(
            payload.get("prompt")
            or (
                "Describe the scientific image, including figures, axes, "
                "labels and key visual elements."
            )
        )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                        "min_pixels": payload.get("min_pixels", 3072),
                        "max_pixels": payload.get("max_pixels", 8388608),
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        request_body: dict[str, Any] = {
            "model": capability.model_id,
            "messages": messages,
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": payload.get("max_tokens", 2048),
        }

        response_body = self._client.chat_completions(request_body)
        choice = first_choice(response_body)
        content = choice.get("message", {}).get("content", "")
        return AdapterResult(
            actual_model_id=response_body.get("model") or capability.model_id,
            output={"content": content},
            usage=response_body.get("usage"),
        )
