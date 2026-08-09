"""Issue 09：自然语言视频路由的公共合同测试。"""

from __future__ import annotations

from bridges.routing import MainCapability, NaturalLanguageRouter, RouteStatus


def test_video_request_compiles_versioned_generation_contract() -> None:
    route = NaturalLanguageRouter().classify(
        "生成一段 10 秒的竖屏短视频：镜头从雨后的街道慢慢推进到一盏暖黄色的路灯。"
    )

    assert route.status == RouteStatus.MATCHED
    assert route.main_capability == MainCapability.VIDEO
    assert route.video is not None
    assert route.video.prompt == "镜头从雨后的街道慢慢推进到一盏暖黄色的路灯"
    assert route.video.duration_seconds == 10
    assert route.video.aspect_ratio == "9:16"
    assert route.video.size == "720*1280"
    assert route.video.model_id == "wan2.7-t2v-2026-06-12"
    assert route.video.account_object_domain == "account"


def test_chinese_duration_overrides_the_default() -> None:
    route = NaturalLanguageRouter().classify("生成一段十秒视频：海浪拍岸")

    assert route.status == RouteStatus.MATCHED
    assert route.video is not None
    assert route.video.duration_seconds == 10


def test_video_discussion_does_not_trigger_generation() -> None:
    route = NaturalLanguageRouter().classify("这个视频讲了什么？")

    assert route.status == RouteStatus.ORDINARY
    assert route.main_capability == MainCapability.ORDINARY_CHAT
    assert route.video is None


def test_video_production_discussion_does_not_trigger_generation() -> None:
    route = NaturalLanguageRouter().classify("这个视频是怎么制作的？")

    assert route.status == RouteStatus.ORDINARY
    assert route.main_capability == MainCapability.ORDINARY_CHAT
    assert route.video is None


def test_video_synonym_extracts_scene_without_calling_a_model() -> None:
    route = NaturalLanguageRouter().classify("把雨后的街道做成一段短视频")

    assert route.status == RouteStatus.MATCHED
    assert route.main_capability == MainCapability.VIDEO
    assert route.video is not None
    assert route.video.prompt == "雨后的街道"


def test_vague_video_request_clarifies_before_submission() -> None:
    route = NaturalLanguageRouter().classify("帮我生成一个视频")

    assert route.status == RouteStatus.CLARIFY
    assert route.main_capability == MainCapability.CLARIFICATION
    assert route.error_code == "video_missing_scene"
    assert route.clarification_question


def test_unsupported_video_editing_is_rejected_without_fallback() -> None:
    route = NaturalLanguageRouter().classify("把这个视频剪辑成更短的版本并加字幕")

    assert route.status == RouteStatus.REJECTED
    assert route.main_capability == MainCapability.VIDEO
    assert route.error_code == "video_editing_unsupported"
    assert route.video is None


def test_invalid_video_parameter_is_rejected_before_model_submission() -> None:
    route = NaturalLanguageRouter().classify(
        "生成一段 11 秒的横屏视频：海浪拍岸。"
    )

    assert route.status == RouteStatus.REJECTED
    assert route.main_capability == MainCapability.VIDEO
    assert route.error_code == "video_duration_unsupported"
    assert route.video is None


def test_video_and_paper_requests_are_mutually_exclusive() -> None:
    route = NaturalLanguageRouter().classify("搜索量子纠错论文并生成一段实验室视频")

    assert route.status == RouteStatus.CLARIFY
    assert route.main_capability == MainCapability.CLARIFICATION
    assert route.error_code == "multiple_capabilities"
    assert route.video is None


def test_video_and_humanizer_requests_are_mutually_exclusive() -> None:
    route = NaturalLanguageRouter().classify("把这段话改得更像人类并生成一段视频：海浪拍岸")

    assert route.status == RouteStatus.CLARIFY
    assert route.main_capability == MainCapability.CLARIFICATION
    assert route.error_code == "multiple_capabilities"
