"""Low-level Qwen OpenAI-compatible API client with cassette record/playback.

The client intentionally does not know about capabilities or the model gateway.
It only translates HTTP status codes into the stable ``AdapterError`` taxonomy
used by the gateway for retry/fallback decisions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from httpx import USE_CLIENT_DEFAULT
from pydantic import SecretStr

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.model_call_budget import MODEL_CALL_DEFAULT_TIMEOUT_SECONDS
from bridges.observability.scrubber import scrub_value


def _upstream_error_message(response: httpx.Response) -> str | None:
    """提取并脱敏供应商错误 message，避免把完整响应写入业务错误。"""

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    candidate: Any = error.get("message") if isinstance(error, dict) else None
    if not isinstance(candidate, str):
        candidate = payload.get("message")
    if not isinstance(candidate, str) or not candidate:
        return None
    scrubbed = scrub_value(candidate)
    return scrubbed[:500] if isinstance(scrubbed, str) else None


def _client_error_message(noun: str, response: httpx.Response) -> str:
    message = f"{noun} client error ({response.status_code})."
    upstream = _upstream_error_message(response)
    return f"{message} {upstream}" if upstream else message


def _with_upstream_error(message: str, response: httpx.Response) -> str:
    upstream = _upstream_error_message(response)
    return f"{message} {upstream}" if upstream else message


def first_choice(response_body: dict[str, Any]) -> dict[str, Any]:
    """提取并校验 Chat Completions 响应的首个 choice（各适配器共用的协议层规则）。

    空 choices 或非法结构抛 ``AdapterError(empty_response)``；这是供应商
    协议层行为，各能力适配器共享同一实现，不再各自复制。
    """
    choices = response_body.get("choices")
    if not choices or not isinstance(choices, list):
        raise AdapterError(
            code="empty_response",
            message="Qwen response contained no choices.",
            retryable=False,
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise AdapterError(
            code="empty_response",
            message="Qwen response contained no choices.",
            retryable=False,
        )
    return choice


def choice_text(choice: dict[str, Any]) -> str:
    """提取 choice 的助手文本（``message.content``，兼容 ASR 转录响应）。"""
    return str(choice.get("message", {}).get("content", ""))


class CassetteStore:
    """Simple JSON cassette store for record/playback of Qwen HTTP calls.

    Cassettes are keyed by a stable hash of the request body. They store the
    request body and response body only — no Authorization headers, no API keys,
    no secret parameters.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _key(self, request_body: dict[str, Any]) -> str:
        canonical = json.dumps(request_body, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return digest

    def load(self, request_body: dict[str, Any]) -> dict[str, Any] | None:
        """Return the recorded response body for a request, or None."""
        path = self.base_dir / f"{self._key(request_body)}.json"
        if not path.exists():
            return None
        cassette = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cassette, dict):
            return None
        response = cassette.get("response")
        if not isinstance(response, dict):
            return None
        return response

    def save(self, request_body: dict[str, Any], response_body: dict[str, Any]) -> None:
        """Save a sanitized request/response pair to the cassette directory."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{self._key(request_body)}.json"
        cassette = {
            "request": request_body,
            "response": response_body,
        }
        path.write_text(
            json.dumps(cassette, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class QwenApiClient:
    """HTTP client for Qwen OpenAI-compatible and DashScope-native endpoints.

    When ``api_key`` is None, the client operates in playback-only mode and never
    makes a real network call. When ``record_mode`` is True and a cassette store
    is configured, real calls are made and their responses are recorded.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        workspace_id: str | None,
        region: str,
        cassette_store: CassetteStore | None = None,
        record_mode: bool = False,
        #: 默认请求超时（秒）；单一常量来源为
        #: ``bridges.model_call_budget.MODEL_CALL_DEFAULT_TIMEOUT_SECONDS``。
        #: 网关按剩余预算截断后经 ``chat_completions(timeout=...)`` 覆盖。
        timeout: float = MODEL_CALL_DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._region = region
        self._cassette_store = cassette_store
        self._record_mode = record_mode
        self._client = httpx.Client(timeout=timeout)

    @property
    def base_url(self) -> str:
        """Build the regional OpenAI-compatible Base URL.

        If a workspace id is configured, use the business-space regional endpoint.
        Otherwise fall back to the public DashScope compatible endpoint.
        """
        if self._workspace_id:
            return (
                f"https://{self._workspace_id}.{self._region}.maas.aliyuncs.com"
                "/compatible-mode/v1"
            )
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def tts_base_url(self) -> str:
        """Build the DashScope-native TTS endpoint URL.

        The Qwen TTS API uses the DashScope multimodal-generation endpoint,
        not the OpenAI-compatible chat completions endpoint.
        """
        return (
            "https://dashscope.aliyuncs.com/api/v1"
            "/services/aigc/multimodal-generation/generation"
        )

    def chat_completions(
        self,
        request_body: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /chat/completions and return the parsed response body.

        ``timeout`` 覆盖客户端默认超时（秒）：网关按剩余预算截断后经
        适配器传入；None 使用客户端默认值。
        Raises AdapterError subclasses so the gateway can classify the failure.
        """
        return self._post_openai("/chat/completions", request_body, "Qwen", timeout=timeout)

    def chat_completions_stream(self, request_body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """流式 POST /chat/completions，逐条产出解析后的 SSE 数据对象。

        请求体必须包含 ``"stream": true``；按 ``data:`` 行产出 dict
        （``[DONE]`` 哨兵在内部消费）。HTTP 错误按与 ``chat_completions``
        相同的 ``AdapterError`` 分类抛出，供网关统一路由。

        流式不参与 cassette 录制/回放：cassette 模式是探测与非流式适配器
        的同步便利设施，流式测试通过注入 ``httpx.MockTransport`` 完成。
        """
        url = f"{self.base_url}/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"

        try:
            with self._client.stream("POST", url, json=request_body, headers=headers) as response:
                if 400 <= response.status_code < 500:
                    response.read()
                if response.status_code == 429:
                    raise RateLimitError(
                        _with_upstream_error("Qwen rate limit (429).", response)
                    )
                if response.status_code in (401, 403):
                    raise AuthError(
                        _with_upstream_error(
                            "Qwen authentication/authorization failed.", response
                        )
                    )
                if response.status_code >= 500:
                    raise TransientError(f"Qwen server error ({response.status_code}).")
                if response.status_code >= 400:
                    raise AdapterError(
                        code=f"client_error_{response.status_code}",
                        message=_client_error_message("Qwen", response),
                        retryable=False,
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise TransientError(
                            f"Qwen stream returned invalid SSE data: {exc}"
                        ) from exc
        except httpx.TimeoutException as exc:
            raise TransientError(f"Qwen stream timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"Qwen stream regional endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"Qwen stream network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"Qwen stream HTTP error: {exc}") from exc

    def embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST /embeddings and return the parsed response body.

        Used by the fixed knowledge-base vectorization capability probe
        (ADR-0009). Raises AdapterError subclasses for gateway classification.
        """
        return self._post_openai("/embeddings", request_body, "Qwen")

    def dashscope_native(
        self,
        path: str,
        request_body: dict[str, Any],
        *,
        async_call: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST to a DashScope-native service endpoint and return the body.

        Used by the fixed image, video and ASR capability adapters (ADR-0009).
        DashScope native service endpoints use the public domain regardless of
        region (unlike the OpenAI-compatible endpoint, which varies by
        region/workspace), so the base URL is not region-derived.

        ``async_call`` 为 True 时附加 ``X-DashScope-Async: enable`` 请求头：
        视频合成等异步优先服务缺该头会被 403 AccessDenied 拒绝（"does not
        support synchronous calls"，实测）；图片/语音走同步服务，保持 False。
        ``timeout`` 覆盖默认请求超时（同步图片生成耗时可超过默认 60 秒）。
        Raises AdapterError subclasses for gateway classification.
        """
        url = f"https://dashscope.aliyuncs.com{path}"
        return self._post_dashscope(
            url,
            request_body,
            "Qwen DashScope",
            "native_error",
            async_call=async_call,
            timeout=timeout,
        )

    def dashscope_task_get(self, task_id: str) -> dict[str, Any]:
        """GET a DashScope native asynchronous task status (Issue 31).

        The image generation service submits a task with
        :meth:`dashscope_native` and then polls this endpoint once per worker
        tick. A synthetic request body is used as the cassette key so the
        record/playback path works identically to POST calls; it contains no
        secret parameters.
        """
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        return self._get_dashscope(url, {"task_id": task_id}, "Qwen DashScope")

    def dashscope_task_cancel(self, task_id: str) -> dict[str, Any]:
        """Cancel a DashScope native asynchronous task (Issue 31, best effort).

        The image service calls this when the user cancels a task; local
        cancellation is authoritative, so failures here are suppressed by the
        caller and never block the user-visible cancel result.

        官方取消端点为 POST /api/v1/tasks/{task_id}/cancel；旧实现的
        PUT ?action=cancel 会被服务端以 405 拒绝（实测）。
        """
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}/cancel"
        return self._post_dashscope(url, {}, "Qwen DashScope", "native_error")

    def _get_dashscope(
        self,
        url: str,
        cassette_key: dict[str, Any],
        noun: str,
    ) -> dict[str, Any]:
        """GET a DashScope-native endpoint with cassette and error taxonomy."""
        if self._cassette_store is not None and not self._record_mode:
            recorded = self._cassette_store.load(cassette_key)
            if recorded is not None:
                return recorded
            if self._api_key is None:
                raise AdapterError(
                    code="cassette_missing",
                    message="No cassette for this request and no API key configured.",
                    retryable=False,
                )
        if self._record_mode and self._cassette_store is not None:
            recorded = self._cassette_store.load(cassette_key)
            if recorded is not None:
                return recorded

        headers: dict[str, str] = {}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"

        try:
            response = self._client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientError(f"{noun} request timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"{noun} endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"{noun} network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"{noun} HTTP error: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError(_with_upstream_error(f"{noun} rate limit (429).", response))
        if response.status_code in (401, 403):
            raise AuthError(
                _with_upstream_error(f"{noun} authentication/authorization failed.", response)
            )
        if response.status_code >= 500:
            raise TransientError(f"{noun} server error ({response.status_code}).")
        if response.status_code >= 400:
            raise AdapterError(
                code=f"client_error_{response.status_code}",
                message=_client_error_message(noun, response),
                retryable=False,
            )

        try:
            response_body = response.json()
        except Exception as exc:
            raise TransientError(f"{noun} returned invalid JSON: {exc}") from exc

        if not isinstance(response_body, dict):
            raise TransientError(f"{noun} returned a non-object JSON response.")

        if self._record_mode and self._cassette_store is not None:
            self._cassette_store.save(cassette_key, response_body)

        return response_body

    def _post_openai(
        self,
        path: str,
        request_body: dict[str, Any],
        noun: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST to an OpenAI-compatible endpoint with cassette and error taxonomy.

        ``timeout`` 覆盖客户端默认请求超时（秒）；None 表示使用客户端
        默认超时（httpx 的 ``timeout=None`` 会禁用超时，绝不能透传）。
        """
        if self._cassette_store is not None and not self._record_mode:
            recorded = self._cassette_store.load(request_body)
            if recorded is not None:
                return recorded
            if self._api_key is None:
                raise AdapterError(
                    code="cassette_missing",
                    message="No cassette for this request and no API key configured.",
                    retryable=False,
                )

        if self._record_mode and self._cassette_store is not None:
            recorded = self._cassette_store.load(request_body)
            if recorded is not None:
                return recorded

        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"

        try:
            response = self._client.post(
                url,
                json=request_body,
                headers=headers,
                timeout=timeout if timeout is not None else USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"{noun} request timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"{noun} regional endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"{noun} network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"{noun} HTTP error: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError(_with_upstream_error(f"{noun} rate limit (429).", response))
        if response.status_code in (401, 403):
            raise AuthError(
                _with_upstream_error(f"{noun} authentication/authorization failed.", response)
            )
        if response.status_code >= 500:
            raise TransientError(f"{noun} server error ({response.status_code}).")
        if response.status_code >= 400:
            raise AdapterError(
                code=f"client_error_{response.status_code}",
                message=_client_error_message(noun, response),
                retryable=False,
            )

        try:
            response_body = response.json()
        except Exception as exc:
            raise TransientError(f"{noun} returned invalid JSON: {exc}") from exc

        if not isinstance(response_body, dict):
            raise TransientError(f"{noun} returned a non-object JSON response.")

        if self._record_mode and self._cassette_store is not None:
            self._cassette_store.save(request_body, response_body)

        return response_body

    def text_to_speech(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST to the DashScope TTS endpoint and return the parsed response body.

        The TTS API is a DashScope-native endpoint (not OpenAI-compatible).
        The response contains a temporary audio URL valid for 24 hours; callers
        must transfer the audio to controlled storage before expiry.

        Raises AdapterError subclasses so the gateway can classify the failure.
        """
        return self._post_dashscope(self.tts_base_url, request_body, "Qwen TTS", "tts_error")

    def _post_dashscope(
        self,
        url: str,
        request_body: dict[str, Any],
        noun: str,
        error_code_prefix: str,
        *,
        async_call: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST to a DashScope-native endpoint with cassette and error taxonomy.

        ``async_call`` 为 True 时附加 ``X-DashScope-Async: enable`` 请求头
        （异步优先服务必需）；``timeout`` 覆盖客户端默认超时。
        """
        if self._cassette_store is not None and not self._record_mode:
            recorded = self._cassette_store.load(request_body)
            if recorded is not None:
                return recorded
            if self._api_key is None:
                raise AdapterError(
                    code="cassette_missing",
                    message="No cassette for this request and no API key configured.",
                    retryable=False,
                )

        if self._record_mode and self._cassette_store is not None:
            recorded = self._cassette_store.load(request_body)
            if recorded is not None:
                return recorded

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        if async_call:
            headers["X-DashScope-Async"] = "enable"

        try:
            response = self._client.post(
                url, json=request_body, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"{noun} request timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"{noun} endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"{noun} network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"{noun} HTTP error: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError(_with_upstream_error(f"{noun} rate limit (429).", response))
        if response.status_code in (401, 403):
            raise AuthError(
                _with_upstream_error(f"{noun} authentication/authorization failed.", response)
            )
        if response.status_code >= 500:
            raise TransientError(f"{noun} server error ({response.status_code}).")
        if response.status_code >= 400:
            raise AdapterError(
                code=f"client_error_{response.status_code}",
                message=_client_error_message(noun, response),
                retryable=False,
            )

        try:
            response_body = response.json()
        except Exception as exc:
            raise TransientError(f"{noun} returned invalid JSON: {exc}") from exc

        if not isinstance(response_body, dict):
            raise TransientError(f"{noun} returned a non-object JSON response.")

        # The DashScope native API may return HTTP 200 with an error status_code
        # in the response body. Classify these using the body status code.
        body_status = response_body.get("status_code")
        if isinstance(body_status, int) and body_status != 200:
            code_str = str(response_body.get("code") or "")
            message_str = str(response_body.get("message") or "")
            if body_status == 429:
                raise RateLimitError(f"{noun} rate limit: {message_str}")
            if body_status in (401, 403):
                raise AuthError(f"{noun} auth error: {message_str}")
            if body_status >= 500:
                raise TransientError(f"{noun} server error ({body_status}): {message_str}")
            raise AdapterError(
                code=f"{error_code_prefix}_{body_status}",
                message=f"{noun} error ({body_status}): {message_str or code_str}",
                retryable=False,
            )

        if self._record_mode and self._cassette_store is not None:
            self._cassette_store.save(request_body, response_body)

        return response_body
