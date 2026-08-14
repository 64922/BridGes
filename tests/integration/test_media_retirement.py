"""Issue 08：旧 `/media` 写入口退役回归测试。

覆盖：
- 命令清单中全部入口对已认证账户稳定返回 410，响应包含统一错误码、中文说明、
  替代路径和稳定 endpoint ID；
- 合法 JSON、畸形 JSON、空正文、超大正文和未知对象 ID 在认证后均得到同构 410；
- 退役入口不调用旧 media services，不修改数据库/对象/任务/发布记录；
- `/media` 下任何未分类的非 GET 路由或已知带写副作用的 GET 使测试失败；
- 保留的 GET 路由经快照证明只读。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from bridges.api.main import create_app
from bridges.api.media import (
    LEGACY_MEDIA_WRITE_COMMANDS,
    READ_ONLY_MEDIA_GETS,
)
from bridges.contracts.media import (
    AccessibilityBundleRequest,
    AccessibilityTargetKind,
    ChartGenerationRequest,
    ChartMark,
    MediaPublishRequest,
    SandboxRunRequest,
    StoryboardGenerationRequest,
)
from bridges.media import (
    AccessibilityService,
    MediaGenerationService,
    MediaPublishService,
    SandboxService,
    StoryboardService,
)
from bridges.media.accessibility_service import DeterministicNarrationSynthesizer
from bridges.media.generation import DeterministicSpecGenerator
from bridges.media.storyboard_service import (
    DeterministicStoryboardGenerator,
    InMemorySandboxRuntime,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def registered_user() -> Any:
    def _make(
        client: TestClient, username: str, qq_email: str, password: str
    ) -> dict[str, Any]:
        response = client.post(
            "/auth/register",
            json={"username": username, "qq_email": qq_email, "password": password},
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _make


_AUTH_COUNTER = 0


def _authenticate(client: TestClient, registered_user: Any) -> str:
    global _AUTH_COUNTER
    _AUTH_COUNTER += 1
    user = registered_user(
        client,
        f"media-retire-{_AUTH_COUNTER}",
        f"1005{_AUTH_COUNTER:03d}@qq.com",
        "correct-horse-12",
    )
    return user["account"]["id"]


# Expand path templates to concrete URLs for the test client.
def _concrete_url(path_template: str) -> str:
    return (
        path_template.replace("{project_id}", "project-1")
        .replace("{asset_id}", "asset-1")
        .replace("{object_id}", "object-1")
        .replace("{storyboard_id}", "storyboard-1")
        .replace("{run_id}", "run-1")
        .replace("{bundle_id}", "bundle-1")
        .replace("{record_id}", "record-1")
    )


@pytest.mark.parametrize(
    "method, path_template, endpoint_id, replacement_path",
    LEGACY_MEDIA_WRITE_COMMANDS,
)
def test_legacy_media_write_command_returns_410(
    client: TestClient,
    registered_user: Any,
    method: str,
    path_template: str,
    endpoint_id: str,
    replacement_path: str,
) -> None:
    _authenticate(client, registered_user)
    url = _concrete_url(path_template)

    response = client.request(method, url)
    assert response.status_code == 410, (method, url, response.text)

    detail = response.json()["detail"]
    assert detail["error"] == "legacy_media_retired"
    assert "聊天入口" in detail["message"]
    if endpoint_id.startswith("legacy.media.assets."):
        assert "全局知识库" in detail["message"], (method, url, detail)
    else:
        assert "没有一对一替代功能" in detail["message"], (method, url, detail)
    assert detail["replacement_path"] == replacement_path
    assert detail["endpoint"] == endpoint_id
    assert detail["service_version"]


def test_retired_endpoints_ignore_malformed_and_empty_bodies(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)

    bodies = [
        b"",
        b"{not-json",
        b'{"extra": "ignored"}',
        b'{"' + b'x' * 4096 + b'": "y"}',
    ]
    for method, path_template, _endpoint_id, _replacement in LEGACY_MEDIA_WRITE_COMMANDS:
        url = _concrete_url(path_template)
        for body in bodies:
            response = client.request(method, url, content=body)
            assert response.status_code == 410, (method, url, len(body), response.text)
            assert (
                response.json()["detail"]["error"] == "legacy_media_retired"
            )


def test_retired_endpoints_require_authentication_first(
    client: TestClient,
) -> None:
    """未认证请求遵循统一认证边界（401），不会泄露 410 合同或对象信息。"""
    for method, path_template, _endpoint_id, _replacement in LEGACY_MEDIA_WRITE_COMMANDS:
        url = _concrete_url(path_template)
        anonymous = client.request(method, url)
        assert anonymous.status_code == 401, (method, url, anonymous.text)
        assert "legacy_media_retired" not in anonymous.text

        bad_token = client.request(
            method, url, headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert bad_token.status_code == 401, (method, url, bad_token.text)


def test_retired_endpoints_do_not_query_path_objects(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)

    # Unknown IDs should still return 410, not 404, because the handler never
    # reaches the service layer.
    unknown_urls = [
        ("POST", "/media/assets/unknown-asset/revoke"),
        ("PUT", "/media/objects/unknown-object/spec"),
        ("GET", "/media/storyboards/unknown-storyboard/validate"),
        ("POST", "/media/accessibility/bundles/unknown-bundle/playback"),
    ]
    for method, url in unknown_urls:
        response = client.request(method, url)
        assert response.status_code == 410, (method, url, response.text)


def test_retired_endpoints_do_not_call_services(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)

    # Replace every legacy media service on app.state with a spy that raises.
    service_names = [
        "media_ingestion_service",
        "media_generation_service",
        "storyboard_service",
        "sandbox_service",
        "accessibility_service",
        "media_publish_service",
        "claim_evidence_service",
    ]
    originals: dict[str, Any] = {}
    for name in service_names:
        originals[name] = getattr(client.app.state, name, None)
        setattr(client.app.state, name, MagicMock())
    try:
        for method, path_template, _endpoint_id, _replacement in LEGACY_MEDIA_WRITE_COMMANDS:
            spy = client.app.state.media_generation_service
            spy.reset_mock()
            url = _concrete_url(path_template)
            response = client.request(method, url)
            assert response.status_code == 410, (method, url, response.text)
            # Zero service access: retired handlers must not inject, query or
            # call any legacy media service (covers DB/object/task/publish
            # records and model_run_locks — none can change without a call).
            for name in service_names:
                service = getattr(client.app.state, name)
                assert service.method_calls == [], (
                    f"{method} {url} 触发了 {name}: {service.method_calls}"
                )
    finally:
        for name, original in originals.items():
            setattr(client.app.state, name, original)


def test_compatibility_probe_is_counted_separately(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)

    real = client.post("/media/charts", json={"title": "probe-test"})
    assert real.status_code == 410

    probe = client.post(
        "/media/charts",
        json={"title": "probe-test"},
        headers={"X-Bridges-Compatibility-Probe": "true"},
    )
    assert probe.status_code == 410

    metrics = client.app.state.compatibility_metrics.snapshot()
    assert metrics["routes"]["legacy.media.charts.create"] == {
        "real": 1,
        "probe": 1,
    }


def test_retired_response_does_not_echo_private_context(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)
    response = client.post(
        "/media/assets/secret-asset/revoke",
        json={"sensitive": "payload"},
    )
    assert response.status_code == 410
    raw = response.text
    assert "secret-asset" not in raw
    assert "sensitive" not in raw
    assert "media-retire" not in raw


def test_repeated_retired_calls_are_idempotent(
    client: TestClient, registered_user: Any
) -> None:
    """重复调用返回同构 410，不产生对象/任务/记录变化（仅计数递增）。"""
    _authenticate(client, registered_user)

    first = client.post("/media/charts", json={"title": "重复调用"})
    second = client.post("/media/charts", json={"title": "重复调用"})
    third = client.post("/media/charts", content=b"{not-json")

    assert first.status_code == second.status_code == third.status_code == 410
    assert first.json()["detail"] == second.json()["detail"] == third.json()["detail"]

    metrics = client.app.state.compatibility_metrics.snapshot()
    assert metrics["routes"]["legacy.media.charts.create"] == {"real": 3, "probe": 0}


def test_model_run_locks_are_not_touched_by_retired_commands(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    registered_user: Any,
) -> None:
    """退役命令不新增/更新 model_run_locks、任务队列或对象存储（SQLite 验证）。"""
    import sqlite3

    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    client = TestClient(create_app())
    _authenticate(client, registered_user)

    db_path = str(tmp_path / "bridges.db")

    def _table_counts() -> dict[str, int]:
        with sqlite3.connect(db_path) as conn:
            tables = ["model_run_locks", "task_claims", "generation_runs", "objects"]
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in tables
            }

    before = _table_counts()
    for method, path_template, _endpoint_id, _replacement in LEGACY_MEDIA_WRITE_COMMANDS:
        response = client.request(method, _concrete_url(path_template))
        assert response.status_code == 410
    after = _table_counts()
    assert before == after, f"退役命令写入了数据库表：{before} -> {after}"
    assert before == {
        "model_run_locks": 0,
        "task_claims": 0,
        "generation_runs": 0,
        "objects": 0,
    }, before


def test_media_route_coverage_requires_classification(
    client: TestClient,
) -> None:
    """Every /media route must be either retired or explicitly read-only."""
    retired_paths = {
        (method.upper(), path_template)
        for method, path_template, _, _ in LEGACY_MEDIA_WRITE_COMMANDS
    }
    read_only_paths = {
        (method.upper(), path_template) for method, path_template in READ_ONLY_MEDIA_GETS
    }

    for route in client.app.routes:
        if not isinstance(route, Route):
            continue
        if not route.path.startswith("/media"):
            continue
        for method in route.methods or []:
            if method.upper() == "HEAD":
                continue
            key = (method.upper(), route.path)
            assert key in retired_paths or key in read_only_paths, (
                f"未分类的 /media 路由：{method} {route.path}"
            )


def test_production_composition_has_no_deterministic_generators(
    client: TestClient,
) -> None:
    """生产组合不再实例化仅供退役写命令使用的 deterministic 能力。"""
    assert client.app.state.media_generation_service._spec_generator is None
    assert client.app.state.storyboard_service._generator is None
    assert client.app.state.sandbox_service._runtime is None
    assert client.app.state.accessibility_service._synthesizer is None
    # 仍服务历史只读的 repository/service 未删除，且能完成跨账户隔离读取。
    assert client.app.state.media_ingestion_service is not None
    assert client.app.state.media_publish_service is not None


def test_read_only_get_endpoints_are_side_effect_free(
    client: TestClient, registered_user: Any
) -> None:
    _authenticate(client, registered_user)

    # Snapshot the in-memory stores before and after read attempts.
    generation_service = client.app.state.media_generation_service
    storyboard_service = client.app.state.storyboard_service
    sandbox_service = client.app.state.sandbox_service
    accessibility_service = client.app.state.accessibility_service
    publish_service = client.app.state.media_publish_service

    before = {
        "media_objects": len(generation_service._media_objects),
        "storyboards": len(storyboard_service._storyboards),
        "runs": len(sandbox_service._runs),
        "bundles": len(accessibility_service._bundles),
        "playback_states": len(accessibility_service._playback_states),
        "publish_records": len(publish_service._publish_records),
    }

    read_urls = [
        ("GET", "/media/assets/unknown-asset"),
        ("GET", "/media/objects/unknown-object"),
        ("GET", "/media/storyboards/unknown-storyboard"),
        ("GET", "/media/sandbox-runs/unknown-run"),
        ("GET", "/media/accessibility/bundles/unknown-bundle"),
        ("GET", "/media/accessibility/bundles/unknown-bundle/validate"),
        ("GET", "/media/publish/unknown-record"),
        ("GET", "/media/publish"),
    ]
    for method, url in read_urls:
        response = client.request(method, url)
        # Unknown objects return 404; the point is they did not mutate state.
        assert response.status_code in (200, 404), (method, url, response.text)

    after = {
        "media_objects": len(generation_service._media_objects),
        "storyboards": len(storyboard_service._storyboards),
        "runs": len(sandbox_service._runs),
        "bundles": len(accessibility_service._bundles),
        "playback_states": len(accessibility_service._playback_states),
        "publish_records": len(publish_service._publish_records),
    }

    assert before == after, "只读 GET 路由产生了副作用"


def _seed_media_object(client: TestClient, account_id: str) -> str:
    """Create a chart directly in the app's media object store."""
    request = ChartGenerationRequest(
        title="只读测试图表",
        mark=ChartMark.BAR,
        data={
            "columns": [
                {"name": "city", "data_type": "string"},
                {"name": "temp", "data_type": "number"},
            ],
            "rows": [
                {"values": {"city": "北京", "temp": 28}},
            ],
        },
        x_field="city",
        y_field="temp",
    )
    temp_service = MediaGenerationService(spec_generator=DeterministicSpecGenerator())
    result = temp_service.generate_chart(request, account_id=account_id)
    obj = result.media_object
    client.app.state.media_generation_service._media_objects[obj.media_object_id] = obj
    return obj.media_object_id


def _seed_storyboard(client: TestClient, account_id: str) -> str:
    """Create a storyboard directly in the app's storyboard store."""
    request = StoryboardGenerationRequest(
        title="只读测试分镜",
        teaching_objectives=["测试目标"],
        media_type="animation",
    )
    temp_service = StoryboardService(generator=DeterministicStoryboardGenerator())
    result = temp_service.generate_storyboard(request, account_id=account_id)
    sb = result.storyboard
    client.app.state.storyboard_service._storyboards[sb.storyboard_id] = sb
    return sb.storyboard_id


def _seed_sandbox_run(client: TestClient, account_id: str) -> str:
    """Create a sandbox run directly in the app's sandbox store."""
    sb_id = _seed_storyboard(client, account_id)
    temp_service = SandboxService(runtime=InMemorySandboxRuntime())
    run = temp_service.run(
        SandboxRunRequest(
            storyboard_id=sb_id,
            source_code="output = 'ok'",
            code_language="python",
        ),
        account_id=account_id,
    )
    client.app.state.sandbox_service._runs[run.run_id] = run
    client.app.state.sandbox_service._run_accounts[run.run_id] = account_id
    client.app.state.sandbox_service._run_sources[run.run_id] = "output = 'ok'"
    return run.run_id


def _seed_accessibility_bundle(client: TestClient, account_id: str) -> str:
    """Create an accessibility bundle directly in the app's accessibility store."""
    temp_storyboard_service = StoryboardService(generator=DeterministicStoryboardGenerator())
    request = StoryboardGenerationRequest(
        title="只读测试分镜",
        teaching_objectives=["测试目标"],
        media_type="animation",
    )
    storyboard = temp_storyboard_service.generate_storyboard(
        request, account_id=account_id
    ).storyboard
    temp_service = AccessibilityService(
        storyboard_service=temp_storyboard_service,
        narration_synthesizer=DeterministicNarrationSynthesizer(),
    )
    bundle = temp_service.generate_bundle(
        AccessibilityBundleRequest(
            target_kind=AccessibilityTargetKind.STORYBOARD,
            target_id=storyboard.storyboard_id,
        ),
        account_id=account_id,
    )
    client.app.state.accessibility_service._bundles[bundle.bundle_id] = bundle
    return bundle.bundle_id


def _seed_publish_record(client: TestClient, account_id: str) -> str:
    """Create a publish record directly in the app's publish store."""
    record = MediaPublishService(
        generation_service=MediaGenerationService(
            spec_generator=DeterministicSpecGenerator()
        ),
        storyboard_service=StoryboardService(
            generator=DeterministicStoryboardGenerator()
        ),
    ).publish(
        account_id,
        MediaPublishRequest(
            media_object_ids=[], storyboard_ids=[], accessibility_bundle_ids=[]
        ),
    )
    client.app.state.media_publish_service._publish_records[record.record_id] = record
    return record.record_id


def test_read_only_get_endpoints_return_existing_objects(
    client: TestClient, registered_user: Any
) -> None:
    account_id = _authenticate(client, registered_user)

    object_id = _seed_media_object(client, account_id)
    storyboard_id = _seed_storyboard(client, account_id)
    run_id = _seed_sandbox_run(client, account_id)
    bundle_id = _seed_accessibility_bundle(client, account_id)
    record_id = _seed_publish_record(client, account_id)

    # 快照可观察状态：读取成功后不允许任何字段变化（AC6 只读证明）。
    generation_service = client.app.state.media_generation_service
    storyboard_service = client.app.state.storyboard_service
    sandbox_service = client.app.state.sandbox_service
    accessibility_service = client.app.state.accessibility_service
    publish_service = client.app.state.media_publish_service

    def _state_fingerprint() -> dict[str, Any]:
        storyboard = storyboard_service._storyboards[storyboard_id]
        bundle = accessibility_service._bundles[bundle_id]
        return {
            "media_object": generation_service._media_objects[object_id].model_dump(),
            "storyboard": storyboard.model_dump(),
            "run": sandbox_service._runs[run_id].model_dump(),
            "bundle": bundle.model_dump(),
            "playback_states": dict(accessibility_service._playback_states),
            "publish_record": publish_service._publish_records[record_id].model_dump(),
        }

    before = _state_fingerprint()

    response = client.get(f"/media/objects/{object_id}")
    assert response.status_code == 200, response.text
    assert response.json()["media_object_id"] == object_id

    response = client.get(f"/media/storyboards/{storyboard_id}")
    assert response.status_code == 200, response.text
    assert response.json()["storyboard_id"] == storyboard_id

    response = client.get(f"/media/sandbox-runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == run_id

    response = client.get(f"/media/accessibility/bundles/{bundle_id}")
    assert response.status_code == 200, response.text
    assert response.json()["bundle_id"] == bundle_id

    response = client.get(f"/media/accessibility/bundles/{bundle_id}/validate")
    assert response.status_code == 200, response.text
    assert "valid" in response.json()

    response = client.get(f"/media/publish/{record_id}")
    assert response.status_code == 200, response.text
    assert response.json()["record_id"] == record_id

    response = client.get("/media/publish")
    assert response.status_code == 200, response.text
    assert any(r["record_id"] == record_id for r in response.json())

    assert _state_fingerprint() == before, "成功读取既有对象后状态发生了变化"


def test_read_only_get_endpoints_reject_cross_account_access(
    client: TestClient, registered_user: Any
) -> None:
    alice = TestClient(client.app)
    bob = TestClient(client.app)
    alice_id = _authenticate(alice, registered_user)
    _authenticate(bob, registered_user)
    object_id = _seed_media_object(alice, alice_id)

    response = bob.get(f"/media/objects/{object_id}")
    assert response.status_code == 404, response.text


def test_chat_image_chain_does_not_route_to_legacy_media_services(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7：现代聊天图片生成不调用任何旧 /media service（媒体服务替换为抛错 spy）。"""
    import json as _json

    from bridges.ai import CapabilityRegistry, ModelGateway
    from bridges.ai.adapters import AdapterResult
    from bridges.config import get_settings
    from bridges.contracts.ai import (
        CapabilityKind,
        CapabilityRecord,
        CapabilityStatus,
        RetryPolicy,
    )

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'img.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    register = client.post(
        "/auth/register",
        json={
            "username": "img-retire-check",
            "qq_email": "1005666@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert register.status_code == 201, register.text

    # 可编程图片适配器：submit → poll → fetch，固定模型 ID。
    model_id = "qwen-image-2.0-pro-2026-06-22"
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    class _ProgrammableImageAdapter:
        def __init__(self) -> None:
            self.script = [
                {"output": {"cloud_task_id": "cloud-1"}},
                {"output": {"cloud_status": "SUCCEEDED", "result_url": "http://img.local/r.png"}},
                {"output": {"image_bytes": image_bytes, "media_type": "image/png"}},
            ]

        def call(
            self,
            capability: CapabilityRecord,
            run_context: Any,
            payload: dict[str, Any],
        ) -> AdapterResult:
            step = self.script.pop(0) if self.script else {"output": {}}
            return AdapterResult(actual_model_id=model_id, output=step["output"])

    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_image",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=model_id,
            input_schema_version="image-prompt-v1",
            output_schema_version="image-task-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_image", "1", _ProgrammableImageAdapter())
    app.state.image_service._gateway = gateway  # noqa: SLF001 - 测试注入替身

    # 旧 media services 全部替换为 spy：任何访问都会在断言中暴露。
    service_names = [
        "media_ingestion_service",
        "media_generation_service",
        "storyboard_service",
        "sandbox_service",
        "accessibility_service",
        "media_publish_service",
        "claim_evidence_service",
    ]
    originals = {name: getattr(app.state, name, None) for name in service_names}
    spies = {name: MagicMock() for name in service_names}
    for name in service_names:
        setattr(app.state, name, spies[name])
    try:
        conversation = client.post("/chat/conversations", json={})
        assert conversation.status_code == 201, conversation.text
        conv_id = conversation.json()["conversation_id"]

        created = client.post(
            f"/chat/conversations/{conv_id}/messages",
            json={"content": "生成一张桥的素描", "image": {"kind": "generate", "prompt": "一座桥"}},
        )
        assert created.status_code == 200, created.text
        app.state.generation_executor.run_tick()

        assistant_id = created.json()["assistant_message"]["message_id"]
        events: list[tuple[str, dict[str, Any]]] = []
        with client.stream(
            "GET",
            f"/chat/conversations/{conv_id}/messages/{assistant_id}/events",
            params={"cursor": 0},
        ) as stream:
            body = "\n".join(stream.iter_lines())
        for block in body.split("\n\n"):
            lines = [line for line in block.split("\n") if line]
            event_name = None
            data: list[str] = []
            for line in lines:
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data.append(line[len("data:"):].strip())
            if event_name and data:
                events.append((event_name, _json.loads("\n".join(data))))
        assert any(name == "started" for name, _ in events)
        assert any(name == "image" for name, _ in events)
        assert any(name == "done" for name, _ in events)

        # 后台轮次收敛为 succeeded（现代图片主链真实工作）。
        app.state.image_service.process_pending()
        app.state.image_service.process_pending()
        app.state.image_service.process_pending()
        messages = client.get(f"/chat/conversations/{conv_id}").json()["messages"]
        assistant = next(m for m in messages if m["role"] == "assistant")
        assert assistant["image"]["status"] == "succeeded"
        assert assistant["image"]["model_id"] == model_id
    finally:
        for name, original in originals.items():
            setattr(app.state, name, original)

    # 现代图片主链全程未触碰旧 media services。
    for name in service_names:
        service = spies[name]
        assert service.method_calls == [], f"聊天图片主链调用了 {name}: {service.method_calls}"
