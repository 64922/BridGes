"""V2 Issue 09：百炼模型元数据的精确查询与缺失判定。"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from bridges.ai.model_metadata import (
    MODEL_METADATA_ERR_CREDENTIAL_INVALID,
    MODEL_METADATA_ERR_INCOMPLETE,
    MODEL_METADATA_ERR_MODEL_NOT_FOUND,
    MODEL_METADATA_ERR_UNAVAILABLE,
    MODEL_METADATA_VERSION,
    BailianModelMetadataSource,
    ModelMetadataError,
    parse_model_metadata,
)

_MODEL_ID = "qwen-probe-model"


def _model_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "model": _MODEL_ID,
        "capabilities": ["TG", "VU"],
        "features": ["function-calling", "structured-outputs"],
        "inference_metadata": {"request_modality": ["Text", "Image"]},
        "model_info": {"context_window": 131_072, "max_input_tokens": 130_048},
    }
    entry.update(overrides)
    return entry


def _payload(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"success": True, "output": {"total": len(entries), "models": entries}}


def test_metadata_declares_text_image_tools_structured_and_context() -> None:
    metadata = parse_model_metadata(_MODEL_ID, _payload([_model_entry()]))

    assert metadata.model_id == _MODEL_ID
    assert metadata.capabilities.text
    assert metadata.capabilities.image
    assert metadata.capabilities.tool_calling
    assert metadata.capabilities.structured_output
    assert metadata.context_window == 131_072
    assert metadata.max_input_tokens == 130_048
    assert metadata.metadata_version == MODEL_METADATA_VERSION


def test_metadata_keeps_one_typed_literal_contract_version() -> None:
    # 元数据版本是激活记录的一部分：解析合同变化必须显式递增。
    assert MODEL_METADATA_VERSION == "bailian-model-list-v1"


def test_fuzzy_or_missing_identifiers_are_not_accepted() -> None:
    with pytest.raises(ModelMetadataError) as missing:
        parse_model_metadata(_MODEL_ID, _payload([]))
    assert missing.value.code == MODEL_METADATA_ERR_MODEL_NOT_FOUND

    # 供应商的前缀命中不是精确命中：只接受 model 字段完全一致的条目。
    with pytest.raises(ModelMetadataError) as fuzzy:
        parse_model_metadata(_MODEL_ID, _payload([_model_entry(model="qwen-probe")]))
    assert fuzzy.value.code == MODEL_METADATA_ERR_MODEL_NOT_FOUND


@pytest.mark.parametrize(
    "entry",
    [
        _model_entry(capabilities=None, inference_metadata=None),
        _model_entry(features=None),
        _model_entry(model_info={"max_input_tokens": 1000}),
        _model_entry(model_info=None),
    ],
)
def test_incomplete_metadata_cannot_be_used(entry: dict[str, object]) -> None:
    with pytest.raises(ModelMetadataError) as excinfo:
        parse_model_metadata(_MODEL_ID, _payload([entry]))

    assert excinfo.value.code == MODEL_METADATA_ERR_INCOMPLETE
    assert "无法核对" in excinfo.value.message


def test_declared_capability_absence_is_reported_as_false() -> None:
    metadata = parse_model_metadata(
        _MODEL_ID,
        _payload(
            [
                _model_entry(
                    capabilities=["TG"],
                    inference_metadata={"request_modality": ["Text"]},
                    features=["structured-outputs"],
                )
            ]
        ),
    )

    assert metadata.capabilities.text is True
    assert metadata.capabilities.image is False
    assert metadata.capabilities.tool_calling is False
    assert metadata.capabilities.structured_output is True


def _source(handler: object) -> BailianModelMetadataSource:
    return BailianModelMetadataSource(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
        api_key=SecretStr("sk-probe-secret"),
        workspace_id=None,
        region="cn-beijing",
    )


def test_query_uses_exact_model_filter_and_bearer_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_payload([_model_entry()]))

    metadata = _source(handler).query(_MODEL_ID)

    assert metadata.model_id == _MODEL_ID
    assert "model=" + _MODEL_ID in str(seen["url"])
    assert seen["authorization"] == "Bearer sk-probe-secret"


def test_query_reports_rejected_key_without_leaking_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api key sk-probe-secret"})

    with pytest.raises(ModelMetadataError) as excinfo:
        _source(handler).query(_MODEL_ID)

    assert excinfo.value.code == MODEL_METADATA_ERR_CREDENTIAL_INVALID
    assert "sk-probe-secret" not in excinfo.value.message


def test_query_reports_network_and_server_failures_as_unavailable() -> None:
    def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    for handler in (network_error, server_error):
        with pytest.raises(ModelMetadataError) as excinfo:
            _source(handler).query(_MODEL_ID)
        assert excinfo.value.code == MODEL_METADATA_ERR_UNAVAILABLE
        assert "稍后重试" in excinfo.value.message or "重试" in excinfo.value.message
