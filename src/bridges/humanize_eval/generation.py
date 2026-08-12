"""真实模型生成端口（Issue 01 tracer bullet）。

评测链路通过 ``GenerationPort`` 协议调用生产模型适配器：生产实现使用
``QwenApiClient`` 与全局百炼运行凭据（GQ-01 单一事实源），测试使用
确定性假实现。无凭据时返回 ``not_configured`` 状态并列出缺项，绝不
回退为假成功；脚本化答案也不被当作模型输出（见 suts 模块）。

裁判与生成共用本端口的隔离语义：每次 ``generate`` 都是全新上下文，
不继承任何会话历史、隐藏状态或工具记录。
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.ai.qwen_client import QwenApiClient
from bridges.config import Settings
from bridges.credentials.global_credential import resolve_global_qwen_key


class GenerationStatus(StrEnum):
    """生成状态：success / not_configured / failed。"""

    SUCCESS = "success"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


class GenerationParameters(BaseModel):
    """非秘密采样参数，进入运行锁。"""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    seed: int = Field(default=2026)
    max_tokens: int = Field(default=2048, ge=1)
    retries: int = Field(default=2, ge=0)


class GenerationResult(BaseModel):
    """一次模型调用的结果与观察锁。"""

    text: str = ""
    model_id: str = Field(description="实际使用的模型快照。")
    parameters: dict[str, float | int] = Field(
        default_factory=dict, description="非秘密采样参数（temperature/top_p/seed 等）。"
    )
    status: GenerationStatus = Field(
        default=GenerationStatus.SUCCESS,
        description="success / not_configured / failed。",
    )
    retry_count: int = Field(default=0)
    latency_ms: int = Field(default=0)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(
        default=None, description="可读中文错误（不含秘密正文）。"
    )

    @property
    def ok(self) -> bool:
        return self.status is GenerationStatus.SUCCESS


class GenerationPort(Protocol):
    """生成端口：一次调用 = 一个全新上下文（无历史、无隐藏状态）。"""

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        params: GenerationParameters,
    ) -> GenerationResult: ...


class QwenGenerationPort:
    """生产生成端口：QwenApiClient + 全局凭据。

    ``api_key`` 缺失时客户端处于 playback-only（绝不发起真实网络调用）；
    本端口在无凭据时直接返回 ``not_configured``，不做任何尝试性调用。
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model_id: str = CHAT_MODEL_ID,
    ) -> None:
        settings = settings or Settings()
        self._model_id = model_id
        self._region = settings.qwen_region
        self._api_key = resolve_global_qwen_key(settings)

    @property
    def configured(self) -> bool:
        """全局凭据是否已配置（真实调用前置条件）。"""
        return self._api_key is not None

    def _missing_items(self) -> list[str]:
        return [] if self.configured else ["BRIDGES_QWEN_API_KEY 未配置"]

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        params: GenerationParameters,
    ) -> GenerationResult:
        if not self.configured:
            return GenerationResult(
                text="",
                model_id=self._model_id,
                parameters=params.model_dump(),
                status=GenerationStatus.NOT_CONFIGURED,
                error_code="not_configured",
                error_message="缺少全局百炼运行凭据；运行结果为 inconclusive。",
            )
        body: dict[str, object] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": params.temperature,
            "top_p": params.top_p,
            "seed": params.seed,
            "max_tokens": params.max_tokens,
        }
        client = QwenApiClient(
            api_key=self._api_key,
            workspace_id=None,
            region=self._region,
        )
        retries = 0
        started = time.monotonic()
        while True:
            try:
                response = client.chat_completions(body)
                text = _extract_completion_text(response)
                return GenerationResult(
                    text=text,
                    model_id=self._model_id,
                    parameters=params.model_dump(),
                    status=GenerationStatus.SUCCESS,
                    retry_count=retries,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:  # AdapterError 分类由客户端保证
                retries += 1
                if retries > params.retries:
                    return GenerationResult(
                        text="",
                        model_id=self._model_id,
                        parameters=params.model_dump(),
                        status=GenerationStatus.FAILED,
                        retry_count=params.retries,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error_code=getattr(exc, "code", "generation_failed"),
                        error_message=(
                            "模型调用失败，运行结果不得标记通过；"
                            f"详情：{type(exc).__name__}"
                        ),
                    )


def _extract_completion_text(response_body: dict[str, object]) -> str:
    """从 OpenAI 兼容响应中提取首个 choice 的文本。"""
    choices = response_body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""
