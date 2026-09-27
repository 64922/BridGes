"""错误文案映射统一测试（Issue 03）。

验证：chat/turn 与 image/video/speech 的模型调用类文案来自
``MODEL_CALL_ERROR_MESSAGES_ZH`` 同一映射源；``client_error_*`` 在
聊天路径呈现中文通用文案，不再漏英文内部消息；region_* 子码可重试。
"""

from __future__ import annotations

from bridges.ai.errors import MODEL_CALL_ERROR_MESSAGES_ZH
from bridges.chat.turn import error_is_retryable, user_facing_error


def test_shared_model_call_codes_are_single_source_for_chat() -> None:
    """共享映射中的每个码在聊天路径呈现同一文案（唯一来源）。"""
    for code, text in MODEL_CALL_ERROR_MESSAGES_ZH.items():
        assert user_facing_error(code, "internal fallback") == text


def test_chat_client_error_prefix_maps_to_chinese() -> None:
    """client_error_<status> 呈现中文文案，英文内部消息不再漏给用户。"""
    result = user_facing_error("client_error_400", "Qwen client error (400). vendor detail")
    assert result == (
        "模型拒绝了本次请求（HTTP 400），重试不会恢复；请检查请求参数与该模型是否可用。"
    )
    assert "vendor detail" not in result
    assert "Qwen client error" not in result


def test_rejected_client_errors_do_not_promise_recovery() -> None:
    """issue 04：4xx 请求拒绝不得提示"稍后重试"，瞬时 4xx 仍保留重试指引。

    共享映射被聊天、知识库、图片、语音共用，因此文案不假定失败的是
    "主模型"（各能力绑定的模型不同）。
    """
    for status in ("400", "404", "413", "422"):
        text = user_facing_error(f"client_error_{status}", "internal")
        assert "重试不会恢复" in text
        assert "稍后重试" not in text
        assert "主模型" not in text
    for status in ("408", "429"):
        assert user_facing_error(f"client_error_{status}", "internal") == (
            f"模型服务返回错误（HTTP {status}），请稍后重试。"
        )


def test_region_subcodes_present_actionable_texts() -> None:
    assert "DNS" in user_facing_error("region_dns", None)
    assert "代理" in user_facing_error("region_proxy", None)
    assert "证书" in user_facing_error("region_tls", None)


def test_region_subcodes_are_user_retryable() -> None:
    for code in ("region_error", "region_dns", "region_proxy", "region_tls"):
        assert error_is_retryable(code)


def test_unknown_code_keeps_fallback_semantics() -> None:
    assert user_facing_error("unknown_code", "internal message") == "internal message"
    assert user_facing_error("unknown_code") == "生成过程出现内部错误，请重试。"
    assert user_facing_error(None, "internal message") == "internal message"


def test_image_video_speech_delegate_to_shared_mapping() -> None:
    """三服务的模型调用类码与聊天同源；特有码仍走本地表。"""
    from bridges.image.service import _user_facing_error as image_error
    from bridges.speech.service import _user_facing_error as speech_error
    from bridges.video.service import _user_facing_error as video_error

    for mapper in (image_error, video_error, speech_error):
        # 共享码：细分码、限流、鉴权全部命中同一来源。
        shared = MODEL_CALL_ERROR_MESSAGES_ZH
        assert mapper("region_dns", "internal", "default") == shared["region_dns"]
        assert mapper("region_proxy", "internal", "default") == shared["region_proxy"]
        assert mapper("region_tls", "internal", "default") == shared["region_tls"]
        assert mapper("rate_limit", "internal", "default") == shared["rate_limit"]
        assert mapper("auth_error", "internal", "default") == shared["auth_error"]
        # client_error_* 同样命中中文通用文案。
        assert mapper("client_error_429", "Qwen client error (429).", "default") == (
            "模型服务返回错误（HTTP 429），请稍后重试。"
        )
        # 服务特有码仍走本地表。
        assert "未通过验证" in mapper("capability_not_verified", "internal", "default")
        # 完全未映射时保留网关原始消息（既有语义不回归）。
        assert mapper("unknown_code", "vendor raw message", "default") == "vendor raw message"
