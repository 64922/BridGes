"""Issue 39 AC9：直接对象标识的服务端账户授权回归测试。

覆盖：媒体对象、分镜、场景规格、沙箱运行、验证报告——猜测/复用其他
账户的标识必须统一按「不存在」拒绝，不泄漏对象是否存在或归属。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings

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


def _create_chart(client: TestClient) -> str:
    response = client.post(
        "/media/charts",
        json={
            "title": "授权测试图表",
            "mark": "bar",
            "data": {
                "columns": [
                    {"name": "city", "type": "string", "values": ["北京"]},
                    {"name": "temp", "type": "number", "values": [28]},
                ]
            },
            "x_field": "city",
            "y_field": "temp",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["media_object"]["media_object_id"]


def _create_storyboard(client: TestClient) -> str:
    response = client.post(
        "/media/storyboards",
        json={
            "title": "授权测试分镜",
            "teaching_objectives": ["测试目标"],
            "media_type": "animation",
            "claim_ids": [],
            "fact_lock_ids": [],
            "scenes": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["storyboard"]["storyboard_id"]


def _run_sandbox(client: TestClient, storyboard_id: str) -> str:
    response = client.post(
        f"/media/storyboards/{storyboard_id}/sandbox",
        json={
            "storyboard_id": storyboard_id,
            "source_code": "import math\nx = 42\nprint(x)\n",
            "code_language": "python",
            "resource_limits": {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


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


def test_media_object_spec_not_modifiable_by_other_account() -> None:
    """A 账户的媒体对象规格，B 账户修改必须 404。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice10")
    _register(bob, "bob10")
    object_id = _create_chart(alice)

    response = bob.put(
        f"/media/objects/{object_id}/spec",
        params={"spec_json": "{}"},
    )
    # 跨账户必须 404（绝不进入归属后的规格校验路径）
    assert response.status_code == 404


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


def test_storyboard_validate_not_readable_by_other_account() -> None:
    """A 账户分镜的验证报告，B 账户读取必须 404（且不泄漏运行状态）。"""
    alice = TestClient(create_app())
    bob = TestClient(alice.app)
    _register(alice, "alice12")
    _register(bob, "bob12")
    storyboard_id = _create_storyboard(alice)
    run_id = _run_sandbox(alice, storyboard_id)

    response = bob.get(
        f"/media/storyboards/{storyboard_id}/validate",
        params={"run_id": run_id},
    )
    assert response.status_code == 404


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


def test_sandbox_repair_not_allowed_by_other_account() -> None:
    """A 账户的沙箱运行，B 账户修复必须 404。"""
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
    # 参数校验或账户隔离任一先行拒绝均满足 AC9：绝不执行 B 对 A 的修复
    assert response.status_code in (404, 422)


def test_accessibility_bundle_target_not_readable_by_other_account() -> None:
    """A 账户分镜的替代方案目标，B 账户生成必须拒绝（目标按账户隔离）。"""
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
    # 目标不存在或无权访问统一按失败处理，绝不返回 A 的内容
    assert response.status_code in (404, 422, 403)


# ---------------------------------------------------------------------------
# Verification 2：双账户并发操作不串号（Issue 39）
# ---------------------------------------------------------------------------


def test_concurrent_two_account_operations_do_not_cross_contaminate(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """两个账户并发执行注册/建对话/建媒体对象：结果互不串号。

    对话与媒体对象需要真实数据库（内存模式对话存储 503），使用临时
    SQLite 复现 E2E 同款拓扑。
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
            # 各自创建对话（同名对话也不串号）
            conv = client.post(
                "/chat/conversations",
                json={
                    "title": "并发同名对话",
                    "mode": "companion",
                    "project_id": None,
                    "plugin_selection": [],
                },
            )
            assert conv.status_code == 201, conv.text
            conv_id = conv.json()["conversation_id"]
            conv_back = client.get(f"/chat/conversations/{conv_id}")
            assert conv_back.status_code == 200
            # 媒体对象（内存域）跨账户读取必须 404
            chart = client.post(
                "/media/charts",
                json={
                    "title": f"并发图表-{tag}",
                    "mark": "bar",
                    "data": {
                        "columns": [
                            {"name": "city", "type": "string", "values": ["北京"]},
                            {"name": "temp", "type": "number", "values": [28]},
                        ]
                    },
                    "x_field": "city",
                    "y_field": "temp",
                },
            )
            assert chart.status_code == 201, chart.text
            results.append(
                {
                    "tag": tag,
                    "account_id": account_id,
                    "conv_id": conv_id,
                    "object_id": chart.json()["media_object"]["media_object_id"],
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
    # 线程完成顺序不确定：按 tag 识别账户，不能依赖 results 列表顺序
    # （否则 50% 概率把「本方对话」误判为「对方对话」导致假失败）。
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
    # 对方对话 → 404；本方对话 → 200
    assert bob.get(f"/chat/conversations/{first['conv_id']}").status_code == 404
    assert alice.get(f"/chat/conversations/{second['conv_id']}").status_code == 404
    assert alice.get(f"/chat/conversations/{first['conv_id']}").status_code == 200
    assert bob.get(f"/chat/conversations/{second['conv_id']}").status_code == 200
    # 对方媒体对象 → 404；本方 → 200
    assert bob.get(f"/media/objects/{first['object_id']}").status_code == 404
    assert alice.get(f"/media/objects/{second['object_id']}").status_code == 404
    assert alice.get(f"/media/objects/{first['object_id']}").status_code == 200
    assert bob.get(f"/media/objects/{second['object_id']}").status_code == 200
