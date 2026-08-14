"""Issue 39 AC9：直接对象标识的服务端账户授权回归测试。

覆盖：媒体对象、分镜、场景规格、沙箱运行、验证报告——猜测/复用其他
账户的标识必须统一按「不存在」拒绝，不泄漏对象是否存在或归属。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.media import (
    ChartGenerationRequest,
    ChartMark,
    SandboxRunRequest,
    StoryboardGenerationRequest,
)
from bridges.media import MediaGenerationService, SandboxService, StoryboardService
from bridges.media.generation import DeterministicSpecGenerator
from bridges.media.storyboard_service import (
    DeterministicStoryboardGenerator,
    InMemorySandboxRuntime,
)

_EMAIL_COUNTER = 0


def _register(client: TestClient, tag: str) -> dict[str, Any]:
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    response = client.post(
        "/auth/register",
        json={
            "username": f"authz-{tag}",
            "qq_email": f"1003{_EMAIL_COUNTER:04d}@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _account_id(client: TestClient) -> str:
    return client.get("/me").json()["account_id"]


def _create_chart(client: TestClient) -> str:
    """Create a media object directly in the app's in-memory store."""
    account_id = _account_id(client)
    temp_service = MediaGenerationService(spec_generator=DeterministicSpecGenerator())
    request = ChartGenerationRequest(
        title="授权测试图表",
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
    obj = temp_service.generate_chart(request, account_id=account_id).media_object
    client.app.state.media_generation_service._media_objects[obj.media_object_id] = obj
    return obj.media_object_id


def _create_storyboard(client: TestClient) -> str:
    """Create a storyboard directly in the app's in-memory store."""
    account_id = _account_id(client)
    temp_service = StoryboardService(generator=DeterministicStoryboardGenerator())
    request = StoryboardGenerationRequest(
        title="授权测试分镜",
        teaching_objectives=["测试目标"],
        media_type="animation",
    )
    storyboard = temp_service.generate_storyboard(
        request, account_id=account_id
    ).storyboard
    client.app.state.storyboard_service._storyboards[storyboard.storyboard_id] = storyboard
    return storyboard.storyboard_id


def _run_sandbox(client: TestClient, storyboard_id: str) -> str:
    """Create a sandbox run directly in the app's in-memory store."""
    account_id = _account_id(client)
    temp_service = SandboxService(runtime=InMemorySandboxRuntime())
    run = temp_service.run(
        SandboxRunRequest(
            storyboard_id=storyboard_id,
            source_code="import math\nx = 42\nprint(x)\n",
            code_language="python",
        ),
        account_id=account_id,
    )
    app_service = client.app.state.sandbox_service
    app_service._runs[run.run_id] = run
    app_service._run_accounts[run.run_id] = account_id
    app_service._run_sources[run.run_id] = "import math\nx = 42\nprint(x)\n"
    return run.run_id


def test_media_object_not_readable_by_other_account() -> None:
    """A 账户的媒体对象标识，B 账户读取必须 404（猜测标识不能读取）。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice9")
    _register(bob, "bob9")
    object_id = _create_chart(alice)

    response = bob.get(f"/media/objects/{object_id}")
    assert response.status_code == 404
    # 归属账户可读
    response = alice.get(f"/media/objects/{object_id}")
    assert response.status_code == 200


def test_media_object_spec_update_is_retired_410() -> None:
    """对象规格更新入口已退役，任何已认证请求均返回 410。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice10")
    _register(bob, "bob10")
    object_id = _create_chart(alice)

    response = bob.put(
        f"/media/objects/{object_id}/spec",
        params={"spec_json": "{}"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_media_retired"


def test_storyboard_not_readable_by_other_account() -> None:
    """A 账户的分镜标识，B 账户读取必须 404。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice11")
    _register(bob, "bob11")
    storyboard_id = _create_storyboard(alice)

    response = bob.get(f"/media/storyboards/{storyboard_id}")
    assert response.status_code == 404
    response = alice.get(f"/media/storyboards/{storyboard_id}")
    assert response.status_code == 200


def test_storyboard_validate_is_retired_410() -> None:
    """分镜验证入口已退役（原实现会修改分镜状态），任何请求返回 410。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice12")
    _register(bob, "bob12")
    storyboard_id = _create_storyboard(alice)
    _run_sandbox(alice, storyboard_id)

    response = bob.get(
        f"/media/storyboards/{storyboard_id}/validate",
        params={"run_id": "run-1"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_media_retired"


def test_sandbox_run_not_readable_by_other_account() -> None:
    """A 账户的沙箱运行标识，B 账户读取必须 404。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice13")
    _register(bob, "bob13")
    storyboard_id = _create_storyboard(alice)
    run_id = _run_sandbox(alice, storyboard_id)

    response = bob.get(f"/media/sandbox-runs/{run_id}")
    assert response.status_code == 404
    response = alice.get(f"/media/sandbox-runs/{run_id}")
    assert response.status_code == 200


def test_sandbox_repair_is_retired_410() -> None:
    """沙箱修复入口已退役，任何已认证请求均返回 410。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice14")
    _register(bob, "bob14")
    storyboard_id = _create_storyboard(alice)
    run_id = _run_sandbox(alice, storyboard_id)

    response = bob.post(
        f"/media/sandbox-runs/{run_id}/repair",
        json={"patch": "x = 1", "code_language": "python"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_media_retired"


def test_accessibility_bundle_create_is_retired_410() -> None:
    """无障碍包生成入口已退役，任何已认证请求均返回 410。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice15")
    _register(bob, "bob15")
    storyboard_id = _create_storyboard(alice)

    response = bob.post(
        "/media/accessibility/bundles",
        json={
            "target_kind": "storyboard",
            "target_id": storyboard_id,
            "media_type": "animation",
        },
    )
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_media_retired"


# ---------------------------------------------------------------------------
# Verification 2：双账户并发操作不串号（Issue 39）
# ---------------------------------------------------------------------------


def test_concurrent_two_account_operations_do_not_cross_contaminate(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """两个账户并发创建媒体对象：结果互不串号。

    旧 /media 写入口已退役，本测试直接操作内存服务存储验证读取隔离。
    """
    import threading

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'conc.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "test-secret-key-32bytes-xxxxxxxx")
    get_settings.cache_clear()
    app = create_app()
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def _worker(tag: str, email_tail: str) -> None:
        try:
            client = TestClient(app)
            response = client.post(
                "/auth/register",
                json={
                    "username": f"conc-{tag}",
                    "qq_email": f"1004{email_tail}@qq.com",
                    "password": "correct-horse-12",
                },
            )
            assert response.status_code == 201, response.text
            account_id = response.json()["account"]["id"]
            # 对话（现行产品域）与媒体对象（内存域）各自隔离。
            conv = client.post("/chat/conversations", json={})
            assert conv.status_code == 201, conv.text
            conv_id = conv.json()["conversation_id"]
            object_id = _create_chart(client)
            results.append(
                {
                    "tag": tag,
                    "account_id": account_id,
                    "conv_id": conv_id,
                    "object_id": object_id,
                }
            )
        except BaseException as exc:  # noqa: BLE001 - 收集线程异常统一断言
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=("alpha", "501")),
        threading.Thread(target=_worker, args=("beta", "502")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert len(results) == 2
    alpha_result = next(r for r in results if r["tag"] == "alpha")
    beta_result = next(r for r in results if r["tag"] == "beta")
    first = alpha_result
    second = beta_result
    assert first["account_id"] != second["account_id"]
    assert first["conv_id"] != second["conv_id"]

    # 交叉读取：对方账户无法读取本方对话/媒体对象，本方仍可读（无串号）。
    alice = TestClient(app)
    bob = TestClient(app)
    assert alice.post(
        "/auth/login",
        json={"identifier": "conc-alpha", "password": "correct-horse-12"},
    ).status_code == 200
    assert bob.post(
        "/auth/login",
        json={"identifier": "conc-beta", "password": "correct-horse-12"},
    ).status_code == 200
    assert bob.get(f"/chat/conversations/{first['conv_id']}").status_code == 404
    assert alice.get(f"/chat/conversations/{second['conv_id']}").status_code == 404
    assert alice.get(f"/chat/conversations/{first['conv_id']}").status_code == 200
    assert bob.get(f"/chat/conversations/{second['conv_id']}").status_code == 200
    assert bob.get(f"/media/objects/{first['object_id']}").status_code == 404
    assert alice.get(f"/media/objects/{second['object_id']}").status_code == 404
    assert alice.get(f"/media/objects/{first['object_id']}").status_code == 200
    assert bob.get(f"/media/objects/{second['object_id']}").status_code == 200
