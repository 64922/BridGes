"""主模型真实能力探测（V2 Issue 09）。

元数据只给出供应商的**声明值**；本模块用候选/当前 Qwen 密钥对候选模型做
最小真实调用，逐项证实文本、图片、工具调用与结构化输出，任何一项不通过都
不允许激活（``docs/v2/architecture.md`` §7）。四个探测各发一次极小请求：

- 文本：一条 ``ping`` 消息，要求返回非空文本；
- 图片：8×8 白色 PNG 的内联 data URL + 一个指向性问题，要求返回非空文本；
- 工具调用：注册单个 ``record_ping`` 工具并要求模型调用它，检查
  ``message.tool_calls``；
- 结构化输出：``response_format={"type":"json_object"}`` + 要求只输出 JSON，
  检查返回正文可解析为 JSON 对象。

探测只在设置页的「验证并保存」路径上执行，不参与启动自检（ADR-0024 只要求
启动可读凭据，不做可能计费的全量探测）。失败原因按类别给出稳定错误码与中文
说明，正文经 ``scrub_value`` 二次脱敏并截断，错误信息绝不包含密钥、鉴权头或
完整响应。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bridges.ai.adapters import AdapterError, AuthError, RegionError, TransientError
from bridges.ai.qwen_client import QwenApiClient, choice_text, first_choice
from bridges.contracts.ai import ModelCapabilities
from bridges.observability.scrubber import scrub_value

#: 单次探测请求的超时（秒）：探测是交互操作，不占用运行预算。
PROBE_REQUEST_TIMEOUT_SECONDS = 30.0
#: 探测请求的最大输出 token（足够回答/发起工具调用，最小化成本）。
PROBE_MAX_TOKENS = 32
#: 探测工具名（固定，便于在响应中断言归因）。
PROBE_TOOL_NAME = "record_ping"
#: 8×8 白色 PNG（探测图片输入用的最小合法图片，无任何用户数据）。
PROBE_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAD0lEQVR42mP4jwMwDC0JALoev0GJ"
    "6La7AAAAAElFTkSuQmCC"
)

#: 能力字段 → 面向用户的中文标签（设置页逐项展示）。
CAPABILITY_LABELS: dict[str, str] = {
    "text": "文本",
    "image": "图片",
    "tool_calling": "工具调用",
    "structured_output": "结构化输出",
}
#: 主模型必须全部通过的探测顺序（元数据声明的四项能力）。
PROBED_CAPABILITIES: tuple[str, ...] = (
    "text",
    "image",
    "tool_calling",
    "structured_output",
)

#: 稳定错误码。
PROBE_ERR_CREDENTIAL_INVALID = "credential_invalid"
PROBE_ERR_MODEL_NOT_FOUND = "model_not_found"
PROBE_ERR_UNAVAILABLE = "probe_unavailable"
PROBE_ERR_OUTPUT_CONTRACT = "probe_output_contract"
PROBE_ERR_REJECTED = "probe_rejected"

_MODEL_NOT_FOUND_HINTS = ("not exist", "not found", "does not exist", "no such model")


def _safe_text(value: Any, limit: int = 200) -> str:
    """脱敏并截断供应商文本（错误信息绝不带回密钥或完整响应）。"""
    scrubbed = scrub_value(value)
    text = scrubbed if isinstance(scrubbed, str) else str(scrubbed)
    return text[:limit]


def _tool_calls(choice: dict[str, Any]) -> list[Any]:
    message = choice.get("message")
    if not isinstance(message, dict):
        return []
    calls = message.get("tool_calls")
    return calls if isinstance(calls, list) else []


@dataclass(frozen=True)
class ProbeOutcome:
    """单项能力的探测结论（通过或明确失败原因）。"""

    capability: str
    ok: bool
    error_code: str | None = None
    message: str | None = None

    @property
    def label(self) -> str:
        return CAPABILITY_LABELS.get(self.capability, self.capability)


def _text_output(choice: dict[str, Any]) -> str:
    return choice_text(choice).strip()


def _check_text(choice: dict[str, Any]) -> str | None:
    if not _text_output(choice):
        return "模型返回了空文本输出。"
    return None


def _check_image(choice: dict[str, Any]) -> str | None:
    if not _text_output(choice):
        return "模型没有描述这张图片（返回空文本）。"
    return None


def _check_tool_calling(choice: dict[str, Any]) -> str | None:
    if not _tool_calls(choice):
        return "模型没有按要求发起工具调用。"
    return None


def _check_structured_output(choice: dict[str, Any]) -> str | None:
    raw = _text_output(choice)
    if not raw:
        return "结构化输出模式下模型返回了空文本。"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "结构化输出模式下模型返回的不是合法 JSON。"
    if not isinstance(parsed, dict):
        return "结构化输出模式下模型返回的 JSON 不是对象。"
    return None


class ModelCapabilityProbe:
    """用候选密钥对候选模型执行四项最小真实调用探测。"""

    def __init__(self, client: QwenApiClient) -> None:
        self._client = client

    def run(
        self, *, model_id: str, capabilities: ModelCapabilities
    ) -> list[ProbeOutcome]:
        """按 ``capabilities`` 声明逐项探测，返回全部结论（不短路）。

        逐项都执行完，设置页才能一次给出完整的能力表；任一不通过都由调用方
        拒绝激活。
        """
        probes: list[tuple[str, dict[str, Any], Callable[[dict[str, Any]], str | None]]] = []
        if capabilities.text:
            probes.append(("text", self._text_payload(model_id), _check_text))
        if capabilities.image:
            probes.append(("image", self._image_payload(model_id), _check_image))
        if capabilities.tool_calling:
            probes.append(
                ("tool_calling", self._tool_payload(model_id), _check_tool_calling)
            )
        if capabilities.structured_output:
            probes.append(
                (
                    "structured_output",
                    self._structured_payload(model_id),
                    _check_structured_output,
                )
            )
        return [self._run_one(name, payload, check) for name, payload, check in probes]

    # -- 单项执行 --------------------------------------------------------

    def _run_one(
        self,
        capability: str,
        payload: dict[str, Any],
        check: Callable[[dict[str, Any]], str | None],
    ) -> ProbeOutcome:
        label = CAPABILITY_LABELS.get(capability, capability)
        try:
            body = self._client.chat_completions(
                payload, timeout=PROBE_REQUEST_TIMEOUT_SECONDS
            )
        except AdapterError as exc:
            return self._failure(capability, label, exc)
        try:
            choice = first_choice(body)
        except AdapterError as exc:
            return ProbeOutcome(
                capability=capability,
                ok=False,
                error_code=PROBE_ERR_OUTPUT_CONTRACT,
                message=f"{label}探测失败：{_safe_text(exc.message)}",
            )
        reason = check(choice)
        if reason is not None:
            return ProbeOutcome(
                capability=capability,
                ok=False,
                error_code=PROBE_ERR_OUTPUT_CONTRACT,
                message=f"{label}探测失败：{reason}",
            )
        return ProbeOutcome(capability=capability, ok=True)

    def _failure(self, capability: str, label: str, exc: AdapterError) -> ProbeOutcome:
        if isinstance(exc, AuthError):
            return ProbeOutcome(
                capability=capability,
                ok=False,
                error_code=PROBE_ERR_CREDENTIAL_INVALID,
                message=(
                    f"{label}探测被拒绝（密钥鉴权失败）；请先更换 Qwen 密钥，"
                    "再验证主模型 ID。"
                ),
            )
        if self._looks_like_model_missing(exc):
            return ProbeOutcome(
                capability=capability,
                ok=False,
                error_code=PROBE_ERR_MODEL_NOT_FOUND,
                message=(
                    f"{label}探测失败：百炼没有这个模型 ID，或当前密钥无权访问它。"
                ),
            )
        if isinstance(exc, (TransientError, RegionError)):
            return ProbeOutcome(
                capability=capability,
                ok=False,
                error_code=PROBE_ERR_UNAVAILABLE,
                message=(
                    f"{label}探测未能完成（{exc.code}）；请检查网络与代理后重试。"
                ),
            )
        return ProbeOutcome(
            capability=capability,
            ok=False,
            error_code=PROBE_ERR_REJECTED,
            message=f"{label}探测失败：{exc.code}。{_safe_text(exc.message)}",
        )

    @staticmethod
    def _looks_like_model_missing(exc: AdapterError) -> bool:
        if exc.code in {"client_error_404", "model_not_found"}:
            return True
        lowered = exc.message.casefold()
        return "model" in lowered and any(
            hint in lowered for hint in _MODEL_NOT_FOUND_HINTS
        )

    # -- 探测载荷 --------------------------------------------------------

    @staticmethod
    def _text_payload(model_id: str) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": "回复：ok"}],
            "max_tokens": PROBE_MAX_TOKENS,
            "stream": False,
        }

    @staticmethod
    def _image_payload(model_id: str) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": PROBE_IMAGE_DATA_URL}},
                        {"type": "text", "text": "这张图片是什么颜色？只回答颜色。"},
                    ],
                }
            ],
            "max_tokens": PROBE_MAX_TOKENS,
            "stream": False,
        }

    @staticmethod
    def _tool_payload(model_id: str) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {"role": "user", "content": f"调用 {PROBE_TOOL_NAME} 记录 value=ok。"}
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": PROBE_TOOL_NAME,
                        "description": "记录一个探测值。",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "max_tokens": PROBE_MAX_TOKENS,
            "stream": False,
        }

    @staticmethod
    def _structured_payload(model_id: str) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "只输出 JSON 对象，不要任何其他文字。"},
                {"role": "user", "content": '输出 {"status": "ok"}。'},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": PROBE_MAX_TOKENS,
            "stream": False,
        }
