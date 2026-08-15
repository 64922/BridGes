"""启动连通性自检测试（Issue 03）：DNS/代理/workspace 形态失败日志断言。

使用注入的假 resolver 模拟各失败形态；验证警告文案可操作、不抛出、
不记录 Key 等敏感信息；正常环境无警告。
"""

from __future__ import annotations

import socket

import pytest

from bridges.ai.qwen_client import qwen_base_url_host
from bridges.ai.startup_check import (
    check_qwen_startup_connectivity,
    log_qwen_startup_connectivity_warnings,
)
from bridges.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"qwen_workspace_id": None, "qwen_region": "cn-beijing"}
    base.update(overrides)
    return Settings(**base)


def _ok_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]


def _failing_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    raise socket.gaierror(-2, "Name or service not known")


def test_healthy_environment_yields_no_warnings() -> None:
    assert check_qwen_startup_connectivity(_settings(), resolver=_ok_resolver) == []


def test_dns_failure_yields_actionable_warning() -> None:
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_failing_resolver)
    assert len(warnings) == 1
    assert "无法解析 Qwen 服务域名" in warnings[0]
    assert "dashscope.aliyuncs.com" in warnings[0]
    assert "请检查 DNS 或代理设置" in warnings[0]


def test_dns_failure_does_not_raise() -> None:
    # 自检绝不抛出：非 OSError 的解析器异常也被折叠为可操作警告。
    def weird_resolver(host: str, port: int) -> list[tuple[object, ...]]:
        raise RuntimeError("resolver backend broken")

    warnings = check_qwen_startup_connectivity(_settings(), resolver=weird_resolver)
    assert len(warnings) == 1
    assert "DNS 预检异常" in warnings[0]


def test_invalid_workspace_shape_yields_warning() -> None:
    bad = _settings(qwen_workspace_id="Bad_Workspace!")
    warnings = check_qwen_startup_connectivity(bad, resolver=_ok_resolver)
    assert any("BRIDGES_QWEN_WORKSPACE_ID" in w for w in warnings)


def test_valid_workspace_shape_no_warning() -> None:
    good = _settings(qwen_workspace_id="my-workspace-1")
    warnings = check_qwen_startup_connectivity(good, resolver=_ok_resolver)
    assert not any("BRIDGES_QWEN_WORKSPACE_ID" in w for w in warnings)


def test_malformed_proxy_env_yields_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "localhost:8080")
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_ok_resolver)
    assert any("缺少协议前缀" in w for w in warnings)


def test_unsupported_proxy_scheme_yields_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "ftp://proxy.example.com:21")
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_ok_resolver)
    assert any("不受支持的协议" in w for w in warnings)


def test_healthy_proxy_env_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_ok_resolver)
    assert not any("代理环境变量" in w for w in warnings)


def test_log_warning_level(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bridges.ai.startup_check.check_qwen_startup_connectivity",
        lambda settings: [
            "启动自检：无法解析 Qwen 服务域名 dashscope.aliyuncs.com，请检查 DNS 或代理设置。"
        ],
    )
    with caplog.at_level("WARNING", logger="bridges.ai.startup_check"):
        log_qwen_startup_connectivity_warnings(_settings())
    assert any("无法解析 Qwen 服务域名" in r.message for r in caplog.records)


def test_all_set_proxy_vars_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个非空代理环境变量都校验：合法与畸形变量并存时畸形者仍告警。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("HTTP_PROXY", "localhost:3128")
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_ok_resolver)
    assert any("HTTP_PROXY" in w and "缺少协议前缀" in w for w in warnings)


def test_warnings_do_not_contain_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """不记录 Key 等敏感信息：警告文本不包含任何疑似凭据。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://user:sekret@proxy.example.com:8080")
    warnings = check_qwen_startup_connectivity(_settings(), resolver=_failing_resolver)
    joined = "\n".join(warnings)
    assert "sekret" not in joined
    assert "BRIDGES_QWEN_API_KEY" not in joined


def test_host_uses_workspace_and_region() -> None:
    assert qwen_base_url_host(None, "cn-beijing") == "dashscope.aliyuncs.com"
    assert qwen_base_url_host("my-ws", "ap-southeast-1") == "my-ws.ap-southeast-1.maas.aliyuncs.com"
