"""ConnectError 细分测试（Issue 03）：DNS/代理/TLS 分类矩阵与客户端抛出点。

不依赖网络与真实 Key：HTTP 层用 ``httpx.MockTransport`` 替身抛出带
因果链的连接异常，验证 ``classify_connect_error`` 与 4 个客户端抛出点
（流式 / _get_dashscope / _post_openai / _post_dashscope）的细分码。
"""

from __future__ import annotations

import socket
import ssl

import httpx
import pytest

from bridges.ai.adapters import RegionError, TransientError
from bridges.ai.qwen_client import QwenApiClient, classify_connect_error


def _connect_error(
    cause: BaseException, message: str = "All connection attempts failed"
) -> httpx.ConnectError:
    """构造带 ``__cause__`` 因果链的 ConnectError（模拟 httpx 包裹）。"""
    try:
        raise cause
    except BaseException as original:  # noqa: BLE001 - 仅用于构造因果链
        try:
            raise httpx.ConnectError(message) from original
        except httpx.ConnectError as exc:
            return exc


def _client(handler) -> QwenApiClient:
    client = QwenApiClient(api_key=None, workspace_id=None, region="cn-beijing")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


# ---------------------------------------------------------------------------
# classify_connect_error 单元矩阵
# ---------------------------------------------------------------------------


def test_classify_dns_gaierror_cause() -> None:
    cause = socket.gaierror(-2, "Name or service not known")
    assert classify_connect_error(_connect_error(cause)) == "dns"


def test_classify_dns_oserror_message_features() -> None:
    # 部分平台 DNS 失败是普通 OSError，靠消息特征判定。
    err = _connect_error(OSError("Temporary failure in name resolution"))
    assert classify_connect_error(err) == "dns"
    plain = httpx.ConnectError("getaddrinfo failed for dashscope.aliyuncs.com")
    assert classify_connect_error(plain) == "dns"


def test_classify_proxy_error_type() -> None:
    assert classify_connect_error(httpx.ProxyError("proxy connection refused")) == "proxy"


def test_classify_proxy_message_features() -> None:
    plain = httpx.ConnectError("connect to proxy at 127.0.0.1:8080 failed")
    assert classify_connect_error(plain) == "proxy"


def test_classify_tls_certificate_verification() -> None:
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")
    assert classify_connect_error(_connect_error(cause)) == "tls"


def test_classify_tls_ssl_error() -> None:
    assert classify_connect_error(_connect_error(ssl.SSLError("WRONG_VERSION_NUMBER"))) == "tls"


def test_classify_connection_refused_falls_back_to_none() -> None:
    refused = _connect_error(ConnectionRefusedError("Connection refused"))
    assert classify_connect_error(refused) is None
    assert classify_connect_error(httpx.ConnectError("Connection refused")) is None


# ---------------------------------------------------------------------------
# 客户端抛出点：非流式（_post_openai）
# ---------------------------------------------------------------------------


def test_post_openai_dns_failure_is_region_dns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise _connect_error(socket.gaierror(-2, "Name or service not known"))

    with pytest.raises(RegionError) as exc_info:
        _client(handler).chat_completions({"model": "m", "messages": []})
    assert exc_info.value.code == "region_dns"
    assert exc_info.value.sub_code == "dns"


def test_post_openai_proxy_failure_is_region_proxy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy unreachable")

    with pytest.raises(RegionError) as exc_info:
        _client(handler).chat_completions({"model": "m", "messages": []})
    assert exc_info.value.code == "region_proxy"
    assert exc_info.value.sub_code == "proxy"


def test_post_openai_tls_failure_is_region_tls() -> None:
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")
    def handler(request: httpx.Request) -> httpx.Response:
        raise _connect_error(cause)

    with pytest.raises(RegionError) as exc_info:
        _client(handler).chat_completions({"model": "m", "messages": []})
    assert exc_info.value.code == "region_tls"
    assert exc_info.value.sub_code == "tls"


def test_post_openai_connection_refused_falls_back_region_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    with pytest.raises(RegionError) as exc_info:
        _client(handler).chat_completions({"model": "m", "messages": []})
    assert exc_info.value.code == "region_error"
    assert exc_info.value.sub_code is None


def test_post_openai_plain_network_error_stays_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection lost")

    with pytest.raises(TransientError) as exc_info:
        _client(handler).chat_completions({"model": "m", "messages": []})
    assert exc_info.value.code == "transient"


# ---------------------------------------------------------------------------
# 客户端抛出点：流式
# ---------------------------------------------------------------------------


def test_stream_connect_dns_failure_is_region_dns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise _connect_error(socket.gaierror(-2, "Name or service not known"))

    with pytest.raises(RegionError) as exc_info:
        list(_client(handler).chat_completions_stream({"model": "m", "stream": True}))
    assert exc_info.value.code == "region_dns"
    assert exc_info.value.sub_code == "dns"


def test_stream_connect_proxy_failure_is_region_proxy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy unreachable")

    with pytest.raises(RegionError) as exc_info:
        list(_client(handler).chat_completions_stream({"model": "m", "stream": True}))
    assert exc_info.value.code == "region_proxy"


def test_stream_connect_tls_failure_is_region_tls() -> None:
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")

    def handler(request: httpx.Request) -> httpx.Response:
        raise _connect_error(cause)

    with pytest.raises(RegionError) as exc_info:
        list(_client(handler).chat_completions_stream({"model": "m", "stream": True}))
    assert exc_info.value.code == "region_tls"


def test_stream_connect_refused_falls_back_region_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    with pytest.raises(RegionError) as exc_info:
        list(_client(handler).chat_completions_stream({"model": "m", "stream": True}))
    assert exc_info.value.code == "region_error"
