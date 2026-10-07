"""高德凭据探测与失败诊断的契约测试（工单 01）。

真实形状的来源与实测事实（本文件只引用信封字段值与官方错误码表，不复制任何
第三方脚本正文）：

- 官方错误码表：https://lbs.amap.com/api/webservice/guide/tools/info
  （``infocode`` ↔ ``info`` 对照）与
  https://lbs.amap.com/api/javascript-api-v2/guide/abc/errorcode ；
- JS API 加载器：``GET https://webapi.amap.com/maps?v=2.0&key=...`` 返回 200 与
  约 968 KB 的 JavaScript（实测占位 Key 968,594 字节、另一随机 32 位 Key
  968,564 字节）；有效与无效 Key 拿到的正文形状相同，四个 ``INVALID_USER_*``
  错误串出现 0 次，因此加载器不能作为 Key 有效性的判据；
- 数据服务：成对请求的响应是 ``{"status","info","infocode",...}`` 信封，
  失败时 ``status="0"`` 带官方 ``info``/``infocode``。
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from bridges.credentials.amap_probes import (
    GEOCODE_PATH,
    LOADER_INSPECT_BYTES,
    REASON_BAD_PAYLOAD,
    REASON_BAD_REQUEST,
    REASON_DOMAIN_RESTRICTED,
    REASON_KEY_INVALID,
    REASON_KEY_RECYCLED,
    REASON_PLATFORM_MISMATCH,
    REASON_QUOTA_EXCEEDED,
    REASON_RATE_LIMITED,
    REASON_SECURITY_CODE_MISMATCH,
    REASON_SERVICE_NOT_ENABLED,
    REASON_SIGNATURE_ENABLED,
    REASON_UNKNOWN,
    REASON_UPSTREAM_ERROR,
    REASON_UPSTREAM_UNREACHABLE,
    classify_amap_failure,
    diagnose_payload,
    probe_data_service,
    probe_loader,
)

#: 一次成功的地理编码响应（真实信封形状：计数与坐标都是字符串）。
AMAP_OK_ENVELOPE = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "1",
    "geocodes": [
        {
            "formatted_address": "江西省南昌市青山湖区",
            "country": "中国",
            "province": "江西省",
            "city": "南昌市",
            "location": "115.9,28.7",
        }
    ],
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _loader_body(size: int) -> str:
    """生成与实测同量级的加载器正文（约 968 KB 的 JavaScript）。"""
    unit = "window.AMap=window.AMap||{};window.AMap._v='2.0';"
    return (unit * (size // len(unit) + 1))[:size]


@pytest.mark.parametrize(
    ("infocode", "info", "expected_reason"),
    [
        ("10001", "INVALID_USER_KEY", REASON_KEY_INVALID),
        ("10013", "USER_KEY_RECYCLED", REASON_KEY_RECYCLED),
        ("10009", "USERKEY_PLAT_NOMATCH", REASON_PLATFORM_MISMATCH),
        ("10002", "SERVICE_NOT_AVAILABLE", REASON_SERVICE_NOT_ENABLED),
        ("10008", "INVALID_USER_SCODE", REASON_SECURITY_CODE_MISMATCH),
        ("10007", "INVALID_USER_SIGNATURE", REASON_SIGNATURE_ENABLED),
        ("10006", "INVALID_USER_DOMAIN", REASON_DOMAIN_RESTRICTED),
        ("10003", "DAILY_QUERY_OVER_LIMIT", REASON_QUOTA_EXCEEDED),
        ("10004", "ACCESS_TOO_FREQUENT", REASON_RATE_LIMITED),
    ],
)
def test_official_error_codes_map_to_specific_reasons(
    infocode: str, info: str, expected_reason: str
) -> None:
    diagnosis = classify_amap_failure(infocode, info)

    assert diagnosis.reason == expected_reason
    assert diagnosis.message


@pytest.mark.parametrize(
    ("infocode", "info", "expected_reason"),
    [
        ("10012", "INSUFFICIENT_PRIVILEGES", "amap_permission_denied"),
        ("10005", "INVALID_USER_IP", "amap_ip_restricted"),
        ("10041", "NO_EFFECTIVE_INTERFACE", "amap_permission_denied"),
        ("40000", "QUOTA_PLAN_RUN_OUT", REASON_QUOTA_EXCEEDED),
        # 官方表里容易混的两组：20002 是「请求协议非法」（不是 HTTPS 那条），
        # 10011 才是「服务不支持 HTTPS」；40002/40003 分别是到期与海外余额。
        ("20002", "ILLEGAL_REQUEST", REASON_BAD_REQUEST),
        ("10011", "NOT_SUPPORT_HTTPS", REASON_BAD_REQUEST),
        ("40002", "SERVICE_EXPIRED", REASON_SERVICE_NOT_ENABLED),
        ("40003", "ABROAD_QUOTA_PLAN_RUN_OUT", REASON_QUOTA_EXCEEDED),
        ("10015", "GATEWAY_TIMEOUT", REASON_RATE_LIMITED),
        ("10016", "SERVER_IS_BUSY", REASON_UPSTREAM_ERROR),
    ],
)
def test_remaining_official_codes_are_classified(
    infocode: str, info: str, expected_reason: str
) -> None:
    assert classify_amap_failure(infocode, info).reason == expected_reason


def test_expired_service_and_illegal_request_do_not_claim_the_wrong_cause() -> None:
    """到期与协议非法各有具体文案：不混进「额度超限」或「HTTPS 不支持」。"""
    expired = classify_amap_failure("40002", "SERVICE_EXPIRED")
    assert "到期" in expired.message
    assert "HTTPS" not in expired.message

    illegal = classify_amap_failure("20002", "ILLEGAL_REQUEST")
    assert "协议" in illegal.message
    assert "HTTPS" not in illegal.message

    # 10045 的官方符号是 USER_ABROAD_DAILY_QUERY_OVER_LIMIT（与 10029 不同）。
    assert classify_amap_failure(
        "10045", "USER_ABROAD_DAILY_QUERY_OVER_LIMIT"
    ).reason == REASON_QUOTA_EXCEEDED


def test_symbol_and_number_agree_and_unknown_codes_stay_honest() -> None:
    # 只有符号名或只有数字码时都能分类（上游有时只给其中一个）。
    assert classify_amap_failure("", "USERKEY_PLAT_NOMATCH").reason == (
        REASON_PLATFORM_MISMATCH
    )
    assert classify_amap_failure("10008", "").reason == REASON_SECURITY_CODE_MISMATCH

    unknown = classify_amap_failure("99999", "")
    assert unknown.reason == REASON_UNKNOWN
    assert "99999" in unknown.message


def test_upstream_echo_is_never_repeated_in_the_diagnosis() -> None:
    """``info`` 可能是上游回显的请求值（含凭据正文），绝不进入文案。"""
    secret = "amap-js-secret-value-should-not-appear"

    diagnosis = classify_amap_failure("", secret)

    assert diagnosis.reason == REASON_UNKNOWN
    assert secret not in diagnosis.message


def test_engine_response_codes_are_upstream_failures() -> None:
    diagnosis = classify_amap_failure("30001", "ENGINE_RESPONSE_DATA_ERROR")

    assert diagnosis.reason == "amap_upstream_error"
    assert diagnosis.retryable is True


def test_diagnose_payload_accepts_only_a_success_envelope() -> None:
    assert diagnose_payload(AMAP_OK_ENVELOPE) is None
    # 只有 status=1 且没有失败码才算成功；status=1 带失败码也必须判失败。
    mixed = {"status": "1", "info": "SERVICE_NOT_AVAILABLE", "infocode": "10002"}
    assert diagnose_payload(mixed) is not None
    assert diagnose_payload(["not", "a", "mapping"]).reason == REASON_BAD_PAYLOAD
    assert diagnose_payload(None).reason == REASON_BAD_PAYLOAD


class _PrefixOnlyStream(httpx.SyncByteStream):
    """模拟真实加载器的流式正文：读超过给定字节数就让测试失败。

    旧的探测按"正文超过 262,144 字节即判失败"实现（实测正文 294,102 字节处
    就越界），这条流因此是**防回归**装置：探测若还想读完整正文，测试立刻报错。
    """

    CHUNK = 32_768

    def __init__(self, body: bytes, allowed: int) -> None:
        self._body = body
        self._allowed = allowed
        self._sent = 0

    def __iter__(self) -> Iterator[bytes]:
        while self._sent < len(self._body):
            assert self._sent <= self._allowed, "探测读取了超出前缀的正文"
            chunk = self._body[self._sent : self._sent + self.CHUNK]
            self._sent += len(chunk)
            yield chunk


@pytest.mark.parametrize("size", [294_102, 968_594, 968_564])
def test_loader_probe_reads_only_a_prefix_of_a_real_sized_body(size: int) -> None:
    """实测正文体积（294,102 是旧体积上限的越界点）下探测只读前缀即返回。"""
    body = _loader_body(size).encode("utf-8")
    assert len(body) == size
    assert size > LOADER_INSPECT_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/maps"
        assert request.url.params["v"] == "2.0"
        assert "jscode" not in request.url.params
        return httpx.Response(
            200,
            stream=_PrefixOnlyStream(body, allowed=2 * _PrefixOnlyStream.CHUNK),
        )

    with _client(handler) as client:
        outcome = probe_loader(client, "amap-js-key")

    assert outcome.reachable is True
    assert outcome.diagnosis is None


def test_loader_probe_does_not_treat_error_markers_as_a_verdict() -> None:
    """旧判定把正文里的错误串当依据（实测不出现）；现在正文内容不作判定。

    这条测试是**防回归**：加载器正文即使含有那四个错误串，也只能说明"加载器
    给出了响应"，不能据此宣称 Key 有效，也不能据此判失败——有效性只由成对
    数据服务请求决定。
    """
    for marker in (
        "INVALID_USER_KEY",
        "INVALID_USER_SCODE",
        "INVALID_USER_DOMAIN",
        "USERKEY_PLAT_NOMATCH",
    ):
        with _client(lambda request, marker=marker: httpx.Response(200, text=marker)) as client:
            outcome = probe_loader(client, "amap-js-key")
        assert outcome.reachable is True
        assert outcome.diagnosis is None


def test_loader_probe_reports_transport_failures_without_blaming_the_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client(boom) as client:
        outcome = probe_loader(client, "amap-js-key")
    assert outcome.reachable is False
    assert outcome.diagnosis is not None
    assert outcome.diagnosis.reason == REASON_UPSTREAM_UNREACHABLE

    with _client(lambda request: httpx.Response(502, text="bad gateway")) as client:
        outcome = probe_loader(client, "amap-js-key")
    assert outcome.reachable is False
    assert outcome.diagnosis is not None
    assert outcome.diagnosis.reason == "amap_upstream_error"

    with _client(lambda request: httpx.Response(200, text="")) as client:
        outcome = probe_loader(client, "amap-js-key")
    assert outcome.reachable is False
    assert outcome.diagnosis is not None
    assert outcome.diagnosis.reason == REASON_BAD_PAYLOAD


def test_data_service_probe_sends_the_security_code_only_when_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=AMAP_OK_ENVELOPE)

    with _client(handler) as client:
        assert probe_data_service(client, key="web-service-key") is None
        assert (
            probe_data_service(
                client, key="js-key", security_code="js-security-code"
            )
            is None
        )

    assert seen[0].url.path == f"/{GEOCODE_PATH}"
    assert "jscode" not in seen[0].url.params
    assert "platform" not in seen[0].url.params
    assert "s" not in seen[0].url.params
    assert seen[1].url.params["platform"] == "JS"
    assert seen[1].url.params["s"] == "rsv3"
    assert seen[1].url.params["jscode"] == "js-security-code"
    assert seen[1].url.params["key"] == "js-key"


def test_data_service_probe_distinguishes_rejection_from_transport() -> None:
    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": "USERKEY_PLAT_NOMATCH", "infocode": "10009"},
        )

    with _client(rejected) as client:
        diagnosis = probe_data_service(client, key="web-service-key")
    assert diagnosis is not None
    assert diagnosis.reason == REASON_PLATFORM_MISMATCH

    with _client(lambda request: httpx.Response(500, text="oops")) as client:
        diagnosis = probe_data_service(client, key="web-service-key")
    assert diagnosis is not None
    assert diagnosis.reason == "amap_upstream_error"
