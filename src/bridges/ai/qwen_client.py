"""Low-level Qwen OpenAI-compatible API client with cassette record/playback.

The client intentionally does not know about capabilities or the model gateway.
It only translates HTTP status codes into the stable ``AdapterError`` taxonomy
used by the gateway for retry/fallback decisions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    RateLimitError,
    RegionError,
    TransientError,
)


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
        timeout: float = 60.0,
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

    def chat_completions(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST /chat/completions and return the parsed response body.

        Raises AdapterError subclasses so the gateway can classify the failure.
        """
        return self._post_openai("/chat/completions", request_body, "Qwen")

    def embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST /embeddings and return the parsed response body.

        Used by the fixed knowledge-base vectorization capability probe
        (ADR-0009). Raises AdapterError subclasses for gateway classification.
        """
        return self._post_openai("/embeddings", request_body, "Qwen")

    def dashscope_native(
        self, path: str, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        """POST to a DashScope-native service endpoint and return the body.

        Used by the fixed image and video generation capability probes
        (ADR-0009). DashScope native service endpoints use the public domain
        regardless of region (unlike the OpenAI-compatible endpoint, which
        varies by region/workspace), so the base URL is not region-derived.
        Raises AdapterError subclasses for gateway classification.
        """
        url = f"https://dashscope.aliyuncs.com{path}"
        return self._post_dashscope(url, request_body, "Qwen DashScope", "native_error")

    def _post_openai(
        self,
        path: str,
        request_body: dict[str, Any],
        noun: str,
    ) -> dict[str, Any]:
        """POST to an OpenAI-compatible endpoint with cassette and error taxonomy."""
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
            response = self._client.post(url, json=request_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientError(f"{noun} request timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"{noun} regional endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"{noun} network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"{noun} HTTP error: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError(f"{noun} rate limit (429).")
        if response.status_code in (401, 403):
            raise AuthError(f"{noun} authentication/authorization failed.")
        if response.status_code >= 500:
            raise TransientError(f"{noun} server error ({response.status_code}).")
        if response.status_code >= 400:
            raise AdapterError(
                code=f"client_error_{response.status_code}",
                message=f"{noun} client error ({response.status_code}).",
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
    ) -> dict[str, Any]:
        """POST to a DashScope-native endpoint with cassette and error taxonomy."""
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

        try:
            response = self._client.post(url, json=request_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientError(f"{noun} request timeout: {exc}") from exc
        except httpx.ConnectError as exc:
            raise RegionError(f"{noun} endpoint unreachable: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"{noun} network error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"{noun} HTTP error: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitError(f"{noun} rate limit (429).")
        if response.status_code in (401, 403):
            raise AuthError(f"{noun} authentication/authorization failed.")
        if response.status_code >= 500:
            raise TransientError(f"{noun} server error ({response.status_code}).")
        if response.status_code >= 400:
            raise AdapterError(
                code=f"client_error_{response.status_code}",
                message=f"{noun} client error ({response.status_code}).",
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
