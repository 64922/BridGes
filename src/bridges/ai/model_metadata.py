"""百炼模型元数据精确查询（V2 Issue 09）。

用户手填的主模型 ID 不能靠命名约定判断能力：激活前必须向百炼查询该 ID 的
**精确元数据**，核对文本、图片、工具调用、结构化输出与上下文额度。本模块
是这次查询的唯一实现，配套 ``docs/v2/architecture.md`` §7：

- 端点为百炼「查询模型列表」``GET {区域主机}/api/v1/models``，用 ``model``
  查询参数做精确过滤，再在返回列表中按 ``model`` 字段精确比对（供应商可能
  做前缀/模糊匹配，实现侧不接受模糊命中）；
- ``capabilities``（``TG`` 文本生成 / ``VU`` 视觉理解）与
  ``inference_metadata.request_modality`` 给出模态；
- ``features``（``function-calling`` / ``structured-outputs``）给出工具调用与
  结构化输出；
- ``model_info`` 给出 ``context_window`` 与 ``max_input_tokens``；
- 元数据缺失（字段不存在）与模型不存在是两种不同的失败：缺失无法核对能力，
  模型不存在无法激活，两者都给出明确中文原因；
- 只读、无副作用；Authorization 头只发送候选/当前密钥，绝不写入日志与错误
  信息。错误消息只包含模型 ID、能力名与供应商返回的脱敏摘要。

查询结果只是「声明值」：调用方随后必须用真实调用探测（``model_probe``）
验证同一组能力，两者都通过才允许激活。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from bridges.ai.qwen_client import qwen_base_url_host
from bridges.contracts.ai import ModelCapabilities

#: 元数据解析合同版本（激活记录携带；百炼字段变化时必须递增）。
MODEL_METADATA_VERSION = "bailian-model-list-v1"

#: 稳定错误码（设置页与运维据此识别失败类别）。
MODEL_METADATA_ERR_CREDENTIAL_INVALID = "model_metadata_credential_invalid"
MODEL_METADATA_ERR_UNAVAILABLE = "model_metadata_unavailable"
MODEL_METADATA_ERR_MODEL_NOT_FOUND = "model_not_found"
MODEL_METADATA_ERR_INCOMPLETE = "model_metadata_incomplete"

#: 百炼模态编码：文本生成 / 视觉理解。
_CAPABILITY_TEXT = "TG"
_CAPABILITY_VISION = "VU"
#: 百炼功能编码：工具调用 / 结构化输出。
_FEATURE_TOOL_CALLING = "function-calling"
_FEATURE_STRUCTURED_OUTPUT = "structured-outputs"


@dataclass(frozen=True)
class ModelMetadata:
    """一次精确元数据查询的结果（声明值，不含真实调用证据）。"""

    model_id: str
    capabilities: ModelCapabilities
    context_window: int | None
    max_input_tokens: int | None
    metadata_version: str = MODEL_METADATA_VERSION


class ModelMetadataError(Exception):
    """元数据查询失败：稳定错误码 + 面向用户的中文原因（绝不含密钥）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ModelMetadataSource(Protocol):
    """主模型元数据查询端口（测试注入确定性实现）。"""

    def query(self, model_id: str) -> ModelMetadata:
        """按精确模型 ID 查询元数据；失败抛 :class:`ModelMetadataError`。"""


def _entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """从响应体中取出模型条目列表（兼容 output/data 两种信封）。"""
    for container in ("output", "data"):
        section = payload.get(container)
        if isinstance(section, Mapping):
            models = section.get("models") or section.get("data")
            if isinstance(models, list):
                return [item for item in models if isinstance(item, Mapping)]
    models = payload.get("models")
    if isinstance(models, list):
        return [item for item in models if isinstance(item, Mapping)]
    return []


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def parse_model_metadata(model_id: str, payload: Mapping[str, Any]) -> ModelMetadata:
    """把百炼响应解析为精确命中的模型元数据。

    只接受 ``model`` 字段与请求 ID 完全一致的条目；字段缺失按
    ``model_metadata_incomplete`` 拒绝，绝不猜测缺失能力。
    """
    matches = [
        entry for entry in _entries(payload) if entry.get("model") == model_id
    ]
    if not matches:
        raise ModelMetadataError(
            MODEL_METADATA_ERR_MODEL_NOT_FOUND,
            f"百炼模型列表中没有精确匹配的模型 ID：{model_id}。",
        )
    entry = matches[0]

    declared = _string_list(entry.get("capabilities"))
    inference = entry.get("inference_metadata")
    request_modality = (
        _string_list(inference.get("request_modality"))
        if isinstance(inference, Mapping)
        else None
    )
    if declared is None and request_modality is None:
        raise ModelMetadataError(
            MODEL_METADATA_ERR_INCOMPLETE,
            "百炼未返回该模型的模态能力字段（capabilities/inference_metadata），"
            "无法核对文本与图片输入能力。",
        )
    modalities = set(declared or []) | set(request_modality or [])

    features = _string_list(entry.get("features"))
    if features is None:
        raise ModelMetadataError(
            MODEL_METADATA_ERR_INCOMPLETE,
            "百炼未返回该模型的功能字段（features），无法核对工具调用与结构化输出能力。",
        )

    model_info = entry.get("model_info")
    info = model_info if isinstance(model_info, Mapping) else {}
    context_window = _positive_int(info.get("context_window"))
    if context_window is None:
        raise ModelMetadataError(
            MODEL_METADATA_ERR_INCOMPLETE,
            "百炼未返回该模型的上下文长度（model_info.context_window），"
            "无法核对上下文额度。",
        )

    return ModelMetadata(
        model_id=model_id,
        capabilities=ModelCapabilities(
            text=_CAPABILITY_TEXT in modalities or "Text" in modalities,
            image=_CAPABILITY_VISION in modalities or "Image" in modalities,
            tool_calling=_FEATURE_TOOL_CALLING in features,
            structured_output=_FEATURE_STRUCTURED_OUTPUT in features,
        ),
        context_window=context_window,
        max_input_tokens=_positive_int(info.get("max_input_tokens")),
    )


class BailianModelMetadataSource:
    """经百炼「查询模型列表」做精确元数据查询的真实实现。"""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        api_key: SecretStr,
        workspace_id: str | None,
        region: str,
    ) -> None:
        self._http_client = http_client
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._region = region

    @property
    def endpoint(self) -> str:
        """模型列表端点（与 OpenAI-compatible 主机名同源，单一形态规则）。"""
        host = qwen_base_url_host(self._workspace_id, self._region)
        return f"https://{host}/api/v1/models"

    def query(self, model_id: str) -> ModelMetadata:
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            response = self._http_client.get(
                self.endpoint,
                params={"model": model_id},
                headers=headers,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ModelMetadataError(
                MODEL_METADATA_ERR_UNAVAILABLE,
                f"无法连接百炼查询模型信息（{exc.__class__.__name__}）；"
                "请检查网络、代理与区域配置后重试。",
            ) from exc
        if response.status_code in (401, 403):
            raise ModelMetadataError(
                MODEL_METADATA_ERR_CREDENTIAL_INVALID,
                "百炼拒绝了该 Qwen 密钥（鉴权失败）；请先在「Qwen 凭据」中更换密钥，"
                "再验证主模型 ID。",
            )
        if response.status_code >= 400:
            raise ModelMetadataError(
                MODEL_METADATA_ERR_UNAVAILABLE,
                f"百炼模型信息查询失败（HTTP {response.status_code}）；请稍后重试。",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelMetadataError(
                MODEL_METADATA_ERR_UNAVAILABLE,
                "百炼模型信息返回了无法解析的响应；请稍后重试。",
            ) from exc
        if not isinstance(payload, Mapping):
            raise ModelMetadataError(
                MODEL_METADATA_ERR_UNAVAILABLE,
                "百炼模型信息返回了非对象响应；请稍后重试。",
            )
        return parse_model_metadata(model_id, payload)
