"""OCR 与视觉图片请求构造的单一来源（issue 04）。

图片内容块（``{"type": "image_url", ...}``）的形态只在这里定义：OCR 适配器、
视觉适配器与设置页的主模型图片能力探测全部经同一函数构造，因此"探测通过但
业务请求必然被拒"的参数形态差异不可能再出现。

``min_pixels`` / ``max_pixels`` 是供应商**可选**参数：不传时由服务端按自身
默认值处理，该默认值对当前模型必然有效（issue 04 用用户原始教材页实测：省略
两个参数、以及 ``min_pixels=65536`` 都被接受，而 ``min_pixels=3072`` 被
``InternalError.Algo.InvalidParameter`` 以 400 拒绝）。因此本模块只在调用方
显式给出像素参数时透传，既不自行发明"通用默认值"，也不把某个模型实测出的
下限当成所有模型的通用阈值。
"""

from __future__ import annotations

from typing import Any


def image_data_url(image_base64: str, mime_type: str | None) -> str:
    """由 base64 图片正文与声明 MIME 构造内联 data URL。"""
    declared = (mime_type or "image/png").split(";")[0].strip()
    return f"data:{declared};base64,{image_base64}"


def image_content_part(
    data_url: str,
    *,
    min_pixels: int | None = None,
    max_pixels: int | None = None,
) -> dict[str, Any]:
    """构造图片内容块；像素参数只在调用方给出时随请求发送。

    调用方未给出时不写入键，交由服务端按其默认值处理——适配器与能力探测
    共用本函数，两者发出的请求形态因此完全一致。
    """
    part: dict[str, Any] = {"type": "image_url", "image_url": {"url": data_url}}
    if min_pixels is not None:
        part["min_pixels"] = min_pixels
    if max_pixels is not None:
        part["max_pixels"] = max_pixels
    return part
