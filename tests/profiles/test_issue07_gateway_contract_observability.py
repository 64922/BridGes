"""Issue 07：画像网关合同、失败语义和页面状态的公共接缝测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bridges.ai import ModelGateway
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter
from bridges.ai.qwen_client import QwenApiClient
from bridges.api.main import create_app
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    RetryPolicy,
    StructuredOutputFormat,
)
from bridges.contracts.profile_extraction import (
    ProfileExtractionOutcome,
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimension
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.profiles import (
    AutomaticProfileError,
    AutomaticProfileService,
    FourDimensionProfileService,
    GatewayAutomaticProfileExtractor,
    InMemoryAutomaticProfileRepository,
    InMemoryFourDimensionProfileRepository,
)


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-1",
        account_id="account-1",
        project_id="conversation-1",
        workflow_name="profile-extraction",
        workflow_version="1",
        submitted_at=datetime.now(UTC),
    )


def _profile_capability(*, retry: RetryPolicy | None = None) -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_profile_extraction",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.6-flash",
        input_schema_version="profile-message-v1",
        output_schema_version="profile-extraction-v1",
        structured_output_format=StructuredOutputFormat.JSON_OBJECT,
        retry_policy=retry or RetryPolicy(max_attempts=1, backoff_seconds=0),
    )


def _profile_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3.6-flash",
            "choices": [{"message": {"content": content}}],
        },
    )


def test_profile_gateway_uses_declared_json_object_and_real_adapter() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _profile_response(
            json.dumps(
                {
                    "items": [
                        {
                            "dimension": "knowledge_interest",
                            "normalized_value": "Transformer",
                            "evidence_ref": "message-1",
                            "reliability": 0.9,
                            "action": "observe",
                        }
                    ]
                }
            )
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = CapabilityRegistry()
    registry.register(_profile_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_profile_extraction", "1", QwenStructuredOutputAdapter(client)
    )

    output = GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)).extract(
        account_id="account-1",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
    )

    assert output.items[0].normalized_value == "Transformer"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "json_schema" not in captured["body"]

    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    repository = InMemoryAutomaticProfileRepository()
    service = AutomaticProfileService(
        four_dimension_service=target,
        repository=repository,
        extractor=GatewayAutomaticProfileExtractor(gateway),
    )
    persisted = service.preprocess_message(
        "account-1",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )
    assert persisted.run.outcome == ProfileExtractionOutcome.SUCCEEDED_OBSERVED
    assert persisted.observed_count == 1


@pytest.mark.parametrize(
    "field,value",
    [("normalized_value", 42), ("evidence_ref", True), ("reliability", "0.9")],
)
def test_profile_output_contract_rejects_coercible_field_types(
    field: str, value: object
) -> None:
    item: dict[str, object] = {
        "dimension": "knowledge_interest",
        "normalized_value": "Transformer",
        "evidence_ref": "message-1",
        "reliability": 0.9,
        "action": "observe",
    }
    item[field] = value

    with pytest.raises(ValidationError):
        ProfileExtractionOutput.model_validate({"items": [item]})


@pytest.mark.parametrize(
    "malformed",
    [
        [],
        {"data": []},
        {"项目": []},
        {
            "items": [
                {
                    "dimension": "knowledge_interest",
                    "normalized_value": 42,
                    "evidence_ref": "message-1",
                    "reliability": 0.9,
                    "action": "observe",
                }
            ]
        },
    ],
)
def test_profile_adapter_rejects_malformed_contract_without_duplicate_call(
    malformed: object,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _profile_response(json.dumps(malformed))

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = CapabilityRegistry()
    registry.register(_profile_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_profile_extraction", "1", QwenStructuredOutputAdapter(client)
    )

    with pytest.raises(AutomaticProfileError) as exc_info:
        GatewayAutomaticProfileExtractor(gateway).extract(
            account_id="account-1",
            conversation_id="conversation-1",
            message_id="message-1",
            content="我可能想学习 Transformer",
            run_id="run-1",
        )

    assert exc_info.value.code == "profile_extraction_contract_invalid"
    assert calls == 1


@pytest.mark.parametrize(
    "message,finish_reason,expected_code",
    [
        (None, None, "profile_extraction_contract_invalid"),
        ({"refusal": "blocked"}, None, "safety_refusal"),
        ({"content": "{}"}, "content_filter", "safety_refusal"),
    ],
)
def test_profile_adapter_rejects_non_contract_response_without_retry(
    message: object, finish_reason: str | None, expected_code: str
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "qwen3.6-flash",
                "choices": [
                    {"message": message, "finish_reason": finish_reason}
                ],
            },
        )

    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = CapabilityRegistry()
    registry.register(_profile_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_profile_extraction", "1", QwenStructuredOutputAdapter(client)
    )

    with pytest.raises(AutomaticProfileError) as exc_info:
        GatewayAutomaticProfileExtractor(gateway).extract(
            account_id="account-1",
            conversation_id="conversation-1",
            message_id="message-1",
            content="我可能想学习 Transformer",
            run_id="run-1",
        )

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is False
    assert calls == 1


@pytest.mark.parametrize(
    "error_code",
    [
        "client_error_400",
        "client_error_422",
        "auth_error",
        "safety_refusal",
        "no_adapter",
        "capability_not_verified",
        "profile_extraction_contract_invalid",
        "structured_output_parse_failed",
    ],
)
def test_permanent_profile_failure_does_not_create_retry_task(error_code: str) -> None:
    class _BlockedGateway:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            return SimpleNamespace(
                status=ModelCallStatus.BLOCKED,
                output=None,
                error_code=error_code,
                error_message="safe upstream detail",
            )

    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    gateway = _BlockedGateway()
    repository = InMemoryAutomaticProfileRepository()
    observability = ObservabilityService()
    service = AutomaticProfileService(
        four_dimension_service=target,
        repository=repository,
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
        observability_service=observability,
    )

    result = service.preprocess_message(
        "account-1",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.status == ProfileExtractionStatus.EXHAUSTED
    assert result.run.outcome == ProfileExtractionOutcome.PERMANENT_FAILURE
    assert service.list_retry_tasks("account-1") == []
    assert service.profile_status("account-1").status == "failed"
    assert gateway.calls == 1
    metrics = observability.profile_metrics_snapshot()
    assert metrics["profile_extraction_total:permanent_failure"] == 1
    assert metrics["profile_permanent_failure_total:total"] == 1


def test_transient_profile_failure_retries_and_recovers() -> None:
    class _RetryThenSuccessGateway:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args: object, **kwargs: object) -> SimpleNamespace:
            del args, kwargs
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    status=ModelCallStatus.RETRYABLE_FAIL,
                    output=None,
                    error_code="transient",
                    error_message="temporary provider failure",
                )
            return SimpleNamespace(
                status=ModelCallStatus.SUCCESS,
                output={
                    "items": [
                        {
                            "dimension": FourDimension.KNOWLEDGE_INTEREST.value,
                            "normalized_value": "Transformer",
                            "evidence_ref": "message-1",
                            "reliability": 0.9,
                            "action": "observe",
                        }
                    ]
                },
                error_code=None,
                error_message=None,
            )

    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    gateway = _RetryThenSuccessGateway()
    repository = InMemoryAutomaticProfileRepository()
    service = AutomaticProfileService(
        four_dimension_service=target,
        repository=repository,
        extractor=GatewayAutomaticProfileExtractor(cast(ModelGateway, gateway)),
    )

    first = service.preprocess_message(
        "account-1",
        conversation_id="conversation-1",
        message_id="message-1",
        content="我可能想学习 Transformer",
        run_id="run-1",
        mode="companion",
    )
    assert first.run.status == ProfileExtractionStatus.PENDING
    assert first.run.outcome == ProfileExtractionOutcome.PENDING_RETRY
    assert len(service.list_retry_tasks("account-1")) == 1

    service.run_retry_tick()

    task = service.list_retry_tasks("account-1")[0]
    run = repository.get_run(
        "account-1", "message-1", first.run.extractor_version, first.run.source_hash
    )
    assert task.status == ProfileExtractionStatus.SUCCEEDED
    assert run is not None
    assert run.outcome == ProfileExtractionOutcome.SUCCEEDED_OBSERVED
    assert gateway.calls == 2


def test_profile_status_is_account_scoped_and_distinguishes_empty_and_failed() -> None:
    target = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    repository = InMemoryAutomaticProfileRepository()
    service = AutomaticProfileService(
        four_dimension_service=target,
        repository=repository,
    )

    assert service.profile_status("account-1").status == "empty"
    assert service.profile_status("account-2").status == "empty"

    result = service.preprocess_message(
        "account-1",
        conversation_id="conversation-1",
        message_id="message-1",
        content="今天天气不错",
        run_id="run-1",
        mode="companion",
    )

    assert result.run.outcome == ProfileExtractionOutcome.NO_SIGNAL
    assert service.profile_status("account-1").status == "empty"
    assert service.profile_status("account-2").status == "empty"


def test_profile_status_api_is_authenticated_and_returns_minimal_projection() -> None:
    client = TestClient(create_app())
    assert client.get("/profiles/status").status_code == 401

    registered = client.post(
        "/auth/register",
        json={
            "username": "Issue07",
            "qq_email": "070707@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert registered.status_code == 201, registered.text

    response = client.get("/profiles/status")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "empty",
        "has_records": False,
        "can_retry": False,
    }
