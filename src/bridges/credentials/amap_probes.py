"""高德凭据探测与失败诊断（设置页两张卡与地图代理共用的唯一分类源）。

判定原则（工单 01）：

- **不按正文体积判失败**：JS API 加载器正文实测约 968 KB（占位 Key 968,594
  字节），按体积判失败会把有效 Key 判成失败，因此加载器探测只读前缀做可达性
  与形状判断；
- **不把「拿到脚本」当「Key 有效」**：加载器对有效与无效 Key 返回同一份脚本
  （四个错误串在真实正文里出现 0 次），有效性只能由一次真实数据服务请求证明；
- **官方代理方案**：``jscode`` 由服务端追加，与 JS API Key 成对送到
  ``restapi.amap.com``，配对不对就以官方的 ``INVALID_USER_SCODE`` /
  ``USERKEY_PLAT_NOMATCH`` 等错误码如实回应；
- 失败分类只依据官方错误码表（符号名 ``info`` 与数字码 ``infocode`` 成对维护）；
  未识别时如实转述数字码，**绝不回显 ``info``**（它可能是上游回显的凭据正文），
  也绝不臆断语义。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

#: JS API 加载器地址（Key 必然出现在该 URL 里，属于公开字段）。
LOADER_ENDPOINT = "https://webapi.amap.com/maps"
#: 加载器正文只读这么多个字节做可达性与形状判断（真实正文约 968 KB）。
LOADER_INSPECT_BYTES = 8_192
#: 一次真实数据服务探测使用的固定公开地址与城市（不含任何用户数据）。
GEOCODE_PATH = "v3/geocode/geo"
PROBE_ADDRESS = "华东交通大学南昌校区"
PROBE_CITY = "南昌"

#: 稳定失败分类码（前端与测试据此断言，不依赖中文文案）。
REASON_KEY_INVALID = "amap_key_invalid"
REASON_KEY_RECYCLED = "amap_key_recycled"
REASON_ACCOUNT_SUSPENDED = "amap_account_suspended"
REASON_PLATFORM_MISMATCH = "amap_platform_mismatch"
REASON_SERVICE_NOT_ENABLED = "amap_service_not_enabled"
REASON_PERMISSION_DENIED = "amap_permission_denied"
REASON_SECURITY_CODE_MISMATCH = "amap_security_code_mismatch"
REASON_SIGNATURE_ENABLED = "amap_signature_enabled"
REASON_IP_RESTRICTED = "amap_ip_restricted"
REASON_DOMAIN_RESTRICTED = "amap_domain_restricted"
REASON_QUOTA_EXCEEDED = "amap_quota_exceeded"
REASON_RATE_LIMITED = "amap_rate_limited"
REASON_BAD_REQUEST = "amap_bad_request"
REASON_UPSTREAM_UNREACHABLE = "amap_upstream_unreachable"
REASON_UPSTREAM_ERROR = "amap_upstream_error"
REASON_BAD_PAYLOAD = "amap_bad_payload"
REASON_UNKNOWN = "amap_unknown_failure"


@dataclass(frozen=True)
class AmapDiagnosis:
    """一次高德失败的分类结论：稳定分类码 + 可操作中文诊断。"""

    reason: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class LoaderProbe:
    """JS API 加载器探测结论。

    ``reachable`` 只说明加载器可达且返回了非空脚本；它**不是** Key 有效的
    证据。``diagnosis`` 只在探测本身没跑通（上游不可达／异常响应）时给出。
    """

    reachable: bool
    diagnosis: AmapDiagnosis | None = None


@dataclass(frozen=True)
class _Reason:
    reason: str
    message: str
    retryable: bool = False


#: 符号名（info）→ 分类。文案统一把「控制台」写成操作落点，顺序是「先核对
#: 值、再核对平台与服务、最后核对开关与额度」，便于用户按顺序排查。
_BY_INFO: Mapping[str, _Reason] = {
    "INVALID_USER_KEY": _Reason(
        REASON_KEY_INVALID,
        "高德拒绝了该 Key（值不正确或已过期）：请核对控制台里的 Key 是否完整、"
        "是否已被删除或过期，再重新填入。",
    ),
    "USER_KEY_RECYCLED": _Reason(
        REASON_KEY_RECYCLED,
        "该 Key 已在控制台删除：请使用仍在使用的 Key，或重新申请一个。",
    ),
    "INVALID_REQUEST": _Reason(
        REASON_ACCOUNT_SUSPENDED,
        "高德账号处于被封禁状态：请在控制台核对账号状态后再试。",
    ),
    "USERKEY_PLAT_NOMATCH": _Reason(
        REASON_PLATFORM_MISMATCH,
        "该 Key 与绑定平台不符：最常见的原因是把「Web 服务」Key 填到了浏览器地图卡"
        "（或反过来）。请在控制台确认该 Key 所属平台与卡片要求一致。",
    ),
    "SERVICE_NOT_AVAILABLE": _Reason(
        REASON_SERVICE_NOT_ENABLED,
        "该 Key 没有此项服务的权限：请在控制台为该 Key 勾选对应服务"
        "（Web 服务卡需要「地理编码」等 Web 服务；浏览器地图卡需要「Web 端(JS API)」）"
        "，保存后重新验证。",
    ),
    "INSUFFICIENT_PRIVILEGES": _Reason(
        REASON_PERMISSION_DENIED,
        "该 Key 权限不足，服务请求被拒绝：请在控制台核对该 Key 已开通的服务范围。",
    ),
    "NO_EFFECTIVE_INTERFACE": _Reason(
        REASON_PERMISSION_DENIED,
        "该 Key 的接口权限已过期：请在控制台续期或重新申请后重试。",
    ),
    "INVALID_USER_SCODE": _Reason(
        REASON_SECURITY_CODE_MISMATCH,
        "安全码与 Key 不匹配：请核对本卡填写的安全密钥与控制台里该 JS API Key 的"
        "「安全密钥」是否为同一次申请（两者必须成对使用）。",
    ),
    "INVALID_USER_SIGNATURE": _Reason(
        REASON_SIGNATURE_ENABLED,
        "该 Key 开启了数字签名校验：请在控制台关闭「数字签名」，或改用未开启签名的 Key。",
    ),
    "INVALID_USER_IP": _Reason(
        REASON_IP_RESTRICTED,
        "请求来源 IP 不在白名单内：请在控制台把本机公网 IP 加入白名单，或取消 IP 白名单限制。",
    ),
    "INVALID_USER_DOMAIN": _Reason(
        REASON_DOMAIN_RESTRICTED,
        "该 Key 绑定的域名无效：请在控制台核对域名白名单，或把白名单留空后重试。",
    ),
    "DAILY_QUERY_OVER_LIMIT": _Reason(
        REASON_QUOTA_EXCEEDED,
        "该 Key 今日调用量已达上限：请明天再试、在控制台提升配额，或更换 Key。",
    ),
    "USER_DAILY_QUERY_OVER_LIMIT": _Reason(
        REASON_QUOTA_EXCEEDED,
        "账号维度的日调用量已达上限：请在控制台提升配额或明天再试。",
    ),
    "ABROAD_DAILY_QUERY_OVER_LIMIT": _Reason(
        REASON_QUOTA_EXCEEDED,
        "该 Key 的调用量已达上限：请稍后再试或在控制台提升配额。",
    ),
    "QUOTA_PLAN_RUN_OUT": _Reason(
        REASON_QUOTA_EXCEEDED,
        "该账号的调用余额已耗尽：请在控制台充值或调整配额后重试。",
    ),
    "SERVICE_EXPIRED": _Reason(
        REASON_QUOTA_EXCEEDED,
        "购买的服务已到期：请在控制台续费后重试。",
    ),
    "ACCESS_TOO_FREQUENT": _Reason(
        REASON_RATE_LIMITED,
        "请求过于频繁：请稍后重试。",
        retryable=True,
    ),
    "IP_QUERY_OVER_LIMIT": _Reason(
        REASON_RATE_LIMITED,
        "该 IP 访问超限：请稍后重试或在控制台提升配额。",
        retryable=True,
    ),
    "QPS_HAS_EXCEEDED_THE_LIMIT": _Reason(
        REASON_RATE_LIMITED, "请求并发超限：请稍后重试。", retryable=True
    ),
    "CQPS_HAS_EXCEEDED_THE_LIMIT": _Reason(
        REASON_RATE_LIMITED, "服务总并发超限：请稍后重试。", retryable=True
    ),
    "CKQPS_HAS_EXCEEDED_THE_LIMIT": _Reason(
        REASON_RATE_LIMITED, "该 Key 的接口并发超限：请稍后重试。", retryable=True
    ),
    "CUQPS_HAS_EXCEEDED_THE_LIMIT": _Reason(
        REASON_RATE_LIMITED, "账号的接口并发超限：请稍后重试。", retryable=True
    ),
    "INVALID_PARAMS": _Reason(
        REASON_BAD_REQUEST, "请求参数非法：这是 BridGes 侧的问题，请提交反馈。"
    ),
    "MISSING_REQUIRED_PARAMS": _Reason(
        REASON_BAD_REQUEST, "请求缺少必填参数：这是 BridGes 侧的问题，请提交反馈。"
    ),
    "NOT_SUPPORT_HTTPS": _Reason(
        REASON_BAD_REQUEST,
        "该服务不支持 HTTPS 请求：请在控制台确认该 Key 所属服务是否支持 HTTPS。",
    ),
    "GATEWAY_TIMEOUT": _Reason(
        REASON_UPSTREAM_ERROR, "高德网关超时：请稍后重试。", retryable=True
    ),
    "SERVER_IS_BUSY": _Reason(
        REASON_UPSTREAM_ERROR, "高德服务器繁忙：请稍后重试。", retryable=True
    ),
    "ENGINE_RESPONSE_DATA_ERROR": _Reason(
        REASON_UPSTREAM_ERROR, "高德服务响应失败：请稍后重试。", retryable=True
    ),
    "UNKNOWN_ERROR": _Reason(
        REASON_UPSTREAM_ERROR, "高德返回未知错误：请稍后重试。", retryable=True
    ),
}

#: 数字码（infocode）→ 分类，取自官方 Web 服务错误码表
#: https://lbs.amap.com/api/webservice/guide/tools/info ；与符号名成对维护，
#: 两者任一命中即可分类（正常情况下上游同时给出两者）。
_BY_INFOCODE: Mapping[str, _Reason] = {
    "10001": _BY_INFO["INVALID_USER_KEY"],
    "10002": _BY_INFO["SERVICE_NOT_AVAILABLE"],
    "10003": _BY_INFO["DAILY_QUERY_OVER_LIMIT"],
    "10004": _BY_INFO["ACCESS_TOO_FREQUENT"],
    "10005": _BY_INFO["INVALID_USER_IP"],
    "10006": _BY_INFO["INVALID_USER_DOMAIN"],
    "10007": _BY_INFO["INVALID_USER_SIGNATURE"],
    "10008": _BY_INFO["INVALID_USER_SCODE"],
    "10009": _BY_INFO["USERKEY_PLAT_NOMATCH"],
    "10010": _BY_INFO["IP_QUERY_OVER_LIMIT"],
    "10011": _BY_INFO["NOT_SUPPORT_HTTPS"],
    "10012": _BY_INFO["INSUFFICIENT_PRIVILEGES"],
    "10013": _BY_INFO["USER_KEY_RECYCLED"],
    "10014": _BY_INFO["QPS_HAS_EXCEEDED_THE_LIMIT"],
    "10015": _BY_INFO["GATEWAY_TIMEOUT"],
    "10016": _BY_INFO["SERVER_IS_BUSY"],
    "10019": _BY_INFO["CQPS_HAS_EXCEEDED_THE_LIMIT"],
    "10020": _BY_INFO["CKQPS_HAS_EXCEEDED_THE_LIMIT"],
    "10021": _BY_INFO["CUQPS_HAS_EXCEEDED_THE_LIMIT"],
    "10026": _BY_INFO["INVALID_REQUEST"],
    "10029": _BY_INFO["ABROAD_DAILY_QUERY_OVER_LIMIT"],
    "10041": _BY_INFO["NO_EFFECTIVE_INTERFACE"],
    "10044": _BY_INFO["USER_DAILY_QUERY_OVER_LIMIT"],
    "10045": _BY_INFO["ABROAD_DAILY_QUERY_OVER_LIMIT"],
    "20000": _BY_INFO["INVALID_PARAMS"],
    "20001": _BY_INFO["MISSING_REQUIRED_PARAMS"],
    "20002": _BY_INFO["NOT_SUPPORT_HTTPS"],
    "40000": _BY_INFO["QUOTA_PLAN_RUN_OUT"],
    "40002": _BY_INFO["SERVICE_EXPIRED"],
    "40003": _BY_INFO["QUOTA_PLAN_RUN_OUT"],
}


def classify_amap_failure(infocode: object, info: object) -> AmapDiagnosis:
    """把一次高德失败响应分类为稳定分类码与可操作中文诊断。

    只依据官方错误码表；未识别时如实转述数字码并归类为未知失败。``info``
    只用于匹配，**绝不进入文案**：上游可能把请求里的值原样回显在该字段里。
    """
    code = str(infocode or "").strip()
    symbol = str(info or "").strip().upper()
    known = _BY_INFO.get(symbol) or _BY_INFOCODE.get(code)
    if known is not None:
        return AmapDiagnosis(known.reason, known.message, known.retryable)
    # 3 开头的官方数字码是「服务响应失败」大类（如 30001、32200）。
    if code.startswith("3"):
        return AmapDiagnosis(
            REASON_UPSTREAM_ERROR, "高德服务响应失败：请稍后重试。", retryable=True
        )
    detail = f"（错误码 {code}）" if code else "（未提供错误码）"
    return AmapDiagnosis(
        REASON_UNKNOWN,
        f"高德拒绝了本次验证{detail}：请核对 Key 的平台、已开通服务与额度后重试。",
    )


def diagnose_payload(payload: Any) -> AmapDiagnosis | None:
    """判定一次高德响应；返回 ``None`` 表示成功（``status=1`` 且无失败码）。"""
    if not isinstance(payload, dict):
        return AmapDiagnosis(
            REASON_BAD_PAYLOAD, "高德返回了无法解析的内容：请稍后重试。", retryable=True
        )
    infocode = str(payload.get("infocode") or "").strip()
    if str(payload.get("status")) == "1" and infocode in {"", "10000"}:
        return None
    return classify_amap_failure(infocode, payload.get("info"))


def probe_loader(client: httpx.Client, key: str) -> LoaderProbe:
    """探测 JS API 加载器可达性与响应形状；不给出任何 Key 有效性的结论。

    只读前缀就停止读取：真实加载器正文约 968 KB，全量读取并设体积上限会把
    有效 Key 判成失败（工单 01 证据 A）。
    """
    try:
        with client.stream(
            "GET",
            LOADER_ENDPOINT,
            params={"v": "2.0", "key": key},
            follow_redirects=False,
        ) as response:
            status_code = response.status_code
            prefix = (
                _read_prefix(response, LOADER_INSPECT_BYTES)
                if status_code == 200
                else b""
            )
    except httpx.HTTPError as exc:
        return LoaderProbe(
            False,
            AmapDiagnosis(
                REASON_UPSTREAM_UNREACHABLE,
                f"无法连接高德地图加载器（{exc.__class__.__name__}）："
                "请检查本机网络与代理设置后重试。",
                retryable=True,
            ),
        )
    if status_code != 200:
        return LoaderProbe(
            False,
            AmapDiagnosis(
                REASON_UPSTREAM_ERROR,
                f"高德地图加载器返回 HTTP {status_code}：请稍后重试。",
                retryable=True,
            ),
        )
    if not prefix.strip():
        return LoaderProbe(
            False,
            AmapDiagnosis(
                REASON_BAD_PAYLOAD,
                "高德地图加载器返回了空正文：请稍后重试。",
                retryable=True,
            ),
        )
    return LoaderProbe(True)


def probe_data_service(
    client: httpx.Client,
    *,
    key: str,
    security_code: str | None = None,
    path: str = GEOCODE_PATH,
    address: str = PROBE_ADDRESS,
    city: str = PROBE_CITY,
) -> AmapDiagnosis | None:
    """用一次真实数据服务请求判定凭据是否可用；``None`` 表示通过。

    浏览器地图按官方代理方案在服务端追加 ``jscode``（安全密钥永不下发浏览器）；
    Web 服务卡不带 ``jscode``。两类失败都能落到具体原因：探测跑不通（上游不可达、
    非 200、无法解析）与「上游明确拒绝了这个值」是不同结论，不可混为一谈。
    """
    params: dict[str, str] = {
        "key": key,
        "address": address,
        "city": city,
        "output": "JSON",
    }
    if security_code:
        params["jscode"] = security_code
    try:
        response = client.get(
            f"https://restapi.amap.com/{path.lstrip('/')}",
            params=params,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return AmapDiagnosis(
            REASON_UPSTREAM_UNREACHABLE,
            f"无法连接高德数据服务（{exc.__class__.__name__}）："
            "请检查本机网络与代理设置后重试。",
            retryable=True,
        )
    if response.status_code != 200:
        return AmapDiagnosis(
            REASON_UPSTREAM_ERROR,
            f"高德数据服务返回 HTTP {response.status_code}：请稍后重试。",
            retryable=True,
        )
    try:
        payload = response.json()
    except ValueError:
        return AmapDiagnosis(
            REASON_BAD_PAYLOAD, "高德返回了无法解析的内容：请稍后重试。", retryable=True
        )
    return diagnose_payload(payload)


def _read_prefix(response: httpx.Response, limit: int) -> bytes:
    """读满 ``limit`` 字节即停止（不把整份加载器读进来）。"""
    prefix = bytearray()
    for chunk in response.iter_bytes():
        prefix.extend(chunk)
        if len(prefix) >= limit:
            break
    return bytes(prefix[:limit])


__all__ = [
    "GEOCODE_PATH",
    "LOADER_ENDPOINT",
    "LOADER_INSPECT_BYTES",
    "PROBE_ADDRESS",
    "PROBE_CITY",
    "AmapDiagnosis",
    "LoaderProbe",
    "classify_amap_failure",
    "diagnose_payload",
    "probe_data_service",
    "probe_loader",
]
