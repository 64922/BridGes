"""自然语言能力路由与当前账户知识库图片解析。

该模块只做确定性预检和合同编译：不会调用模型、不会读取图片正文，也不会
创建任务。图片任务仍由现有 ``ImageService`` 拥有状态机和供应商边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bridges.contracts.chat import ImageRequestPayload
from bridges.contracts.image import ImageTaskKind
from bridges.contracts.routing import (
    ImageRouteContract,
    ImageRouteParameters,
    RouteCapability,
    RouteDecision,
    RouteOperation,
)
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True)
class RouteRegistration:
    """一个可加性注册的能力定义。"""

    capability: RouteCapability
    version: str
    markers: tuple[str, ...]


class CapabilityRouteRegistry:
    """版本化能力注册表；重复注册必须显式失败，禁止覆盖旧定义。"""

    def __init__(self, registrations: Iterable[RouteRegistration] = ()) -> None:
        self._registrations: dict[RouteCapability, RouteRegistration] = {}
        for registration in registrations:
            self.register(registration)

    def register(self, registration: RouteRegistration) -> None:
        if registration.capability in self._registrations:
            raise ValueError(f"能力已注册：{registration.capability.value}")
        self._registrations[registration.capability] = registration

    def get(self, capability: RouteCapability) -> RouteRegistration | None:
        return self._registrations.get(capability)

    def detect(self, text: str) -> list[RouteCapability]:
        normalized = text.casefold()
        return [
            capability
            for capability, registration in self._registrations.items()
            if any(marker.casefold() in normalized for marker in registration.markers)
        ]


def create_default_route_registry() -> CapabilityRouteRegistry:
    """创建当前产品的可加性路由注册表。

    只有图片能力在本切片执行；其余注册项用于冲突检测，实际分支由对应
    后续切片接管，避免图片路由覆盖论文、人味化、视频或生涯能力。
    """

    return CapabilityRouteRegistry(
        (
            RouteRegistration(RouteCapability.IMAGE, "1", ("图片", "图像", "插画", "海报", "照片")),
            RouteRegistration(RouteCapability.PAPER_SEARCH, "1", ("查论文", "找论文", "论文检索", "arxiv")),
            RouteRegistration(RouteCapability.HUMANIZER, "1", ("润色", "改写", "去模板腔", "更自然")),
            RouteRegistration(RouteCapability.VIDEO, "1", ("生成视频", "做视频", "视频生成")),
            RouteRegistration(RouteCapability.CAREER, "1", ("职业规划", "生涯规划", "规划职业")),
        )
    )


@dataclass(frozen=True)
class _ImageCandidate:
    object_id: str
    filename: str
    media_type: str


_IMAGE_TERMS = (
    "图",
    "图片",
    "图像",
    "照片",
    "插画",
    "海报",
    "这张图",
    "该图片",
    "这幅图",
)
_GENERATE_MARKERS = (
    "生成",
    "画",
    "绘制",
    "制作",
    "做一张",
    "来一张",
    "想要一张",
    "需要一张",
    "给我一张",
)
_EDIT_MARKERS = (
    "编辑",
    "修改图片",
    "改图",
    "重绘",
    "背景换",
    "背景改",
    "风格改",
    "替换",
    "裁剪",
    "去掉",
    "移除",
    "添加",
    "加上",
)
_STRONG_EDIT_MARKERS = (
    "背景换",
    "背景改",
    "风格改",
    "重绘",
    "裁剪",
    "去掉",
    "移除",
    "添加",
    "加上",
)
_FILENAME_RE = re.compile(
    r"[^\s，。；：:、\"“”‘’()（）<>《》]+\.(?:png|jpe?g|webp|gif|bmp)(?=\s|的|背景|风格|$)",
    re.IGNORECASE,
)
_ANY_FILENAME_RE = re.compile(
    r"[^\s，。；：:、\"“”‘’()（）<>《》]+\.[a-z0-9]{1,8}(?=\s|的|背景|风格|$)",
    re.IGNORECASE,
)
_DIMENSION_RE = re.compile(r"(?P<w>\d{3,5})\s*[x×*]\s*(?P<h>\d{3,5})")


class NaturalLanguageImageRouter:
    """把一条普通聊天消息编译成图片合同或一次性澄清问题。"""

    def __init__(
        self,
        database: BridgesDatabase,
        registry: CapabilityRouteRegistry | None = None,
    ) -> None:
        self._database = database
        self._registry = registry or create_default_route_registry()

    def route(self, account_id: str, content: str) -> RouteDecision | None:
        text = " ".join(content.split())
        if not text:
            return None
        image_intent = self._is_image_generation(text) or self._is_image_edit(text)
        if not image_intent:
            return None

        competing = [
            capability
            for capability in self._registry.detect(text)
            if capability != RouteCapability.IMAGE
            and self._is_explicit_competing_request(text, capability)
        ]
        if competing:
            names = "、".join([RouteCapability.IMAGE.value, *(item.value for item in competing)])
            return RouteDecision(
                capability=RouteCapability.IMAGE,
                operation=RouteOperation.CLARIFY,
                confidence=0.99,
                reason="同一消息包含多个独立能力请求。",
                clarification_question=f"这条消息同时包含{names}请求，本轮先执行哪一个？",
                competing_capabilities=[RouteCapability.IMAGE, *competing],
            )

        if self._is_image_edit(text):
            return self._edit_decision(account_id, text)
        return self._generate_decision(text)

    def _generate_decision(self, text: str) -> RouteDecision:
        parameters, parameter_error = self._parameters(text)
        if parameter_error is not None:
            return self._rejected(parameter_error[0], parameter_error[1])
        prompt = self._generate_prompt(text)
        if not prompt:
            return RouteDecision(
                capability=RouteCapability.IMAGE,
                operation=RouteOperation.CLARIFY,
                confidence=0.96,
                reason="识别到图片生成意图，但缺少生成要求。",
                clarification_question="你想生成什么图片？请补充主体或画面要求。",
            )
        contract = ImageRouteContract(
            operation=RouteOperation.GENERATE,
            prompt=prompt,
            parameters=parameters,
        )
        return RouteDecision(
            capability=RouteCapability.IMAGE,
            operation=RouteOperation.GENERATE,
            confidence=0.99,
            reason="识别到明确的图片生成动作和画面要求。",
            contract=contract,
        )

    def _edit_decision(self, account_id: str, text: str) -> RouteDecision:
        parameters, parameter_error = self._parameters(text)
        if parameter_error is not None:
            return self._rejected(parameter_error[0], parameter_error[1])
        candidates = self._knowledge_base_images(account_id)
        referenced = self._referenced_candidates(text, candidates)
        if not referenced:
            unsupported = self._referenced_non_images(account_id, text)
            if unsupported:
                return self._rejected(
                    "unsupported_source",
                    "编辑请求指向的材料不是图片；请指明当前知识库中的一张图片。",
                )
            return RouteDecision(
                capability=RouteCapability.IMAGE,
                operation=RouteOperation.CLARIFY,
                confidence=0.98,
                reason="识别到图片编辑意图，但没有唯一可授权的知识库图片。",
                clarification_question="请指明当前账户知识库中要编辑的图片文件名。",
            )
        if len(referenced) > 1:
            return RouteDecision(
                capability=RouteCapability.IMAGE,
                operation=RouteOperation.CLARIFY,
                confidence=0.98,
                reason="编辑请求匹配到多张知识库图片。",
                clarification_question="你想编辑哪一张图片？请给出文件名。",
            )
        source = referenced[0]
        contract = ImageRouteContract(
            operation=RouteOperation.EDIT,
            prompt=text,
            source_object_id=source.object_id,
            parameters=parameters,
        )
        return RouteDecision(
            capability=RouteCapability.IMAGE,
            operation=RouteOperation.EDIT,
            confidence=0.99,
            reason="识别到明确的图片编辑动作，并匹配到当前账户知识库图片。",
            contract=contract,
        )

    def _knowledge_base_images(self, account_id: str) -> list[_ImageCandidate]:
        rows = self._database.scoped(account_id).execute(
            "SELECT DISTINCT o.object_id, o.original_filename, o.media_type"
            " FROM objects o JOIN document_records r ON r.object_id = o.object_id"
            " WHERE o.account_id = ? AND r.account_id = ?"
            " AND r.source = 'knowledge_base' AND r.status = 'ready'"
            " AND o.status = 'active' AND o.media_type LIKE 'image/%'"
            " ORDER BY o.created_at, o.object_id",
            (account_id, account_id),
        ).fetchall()
        return [
            _ImageCandidate(str(row["object_id"]), str(row["original_filename"]), str(row["media_type"]))
            for row in rows
        ]

    def _referenced_candidates(
        self, text: str, candidates: list[_ImageCandidate]
    ) -> list[_ImageCandidate]:
        normalized = text.casefold()
        explicit = [
            candidate
            for candidate in candidates
            if candidate.filename.casefold() in normalized
            or _stem(candidate.filename).casefold() in normalized
        ]
        if explicit:
            return explicit
        if any(term in text for term in ("这张图", "该图片", "这幅图", "知识库中的图片")) and len(candidates) == 1:
            return candidates
        return []

    def _referenced_non_images(self, account_id: str, text: str) -> bool:
        normalized = text.casefold()
        rows = self._database.scoped(account_id).execute(
            "SELECT DISTINCT o.original_filename FROM objects o"
            " JOIN document_records r ON r.object_id = o.object_id"
            " WHERE o.account_id = ? AND r.account_id = ?"
            " AND r.source = 'knowledge_base' AND o.status = 'active'"
            " AND o.media_type NOT LIKE 'image/%'",
            (account_id, account_id),
        ).fetchall()
        return any(str(row["original_filename"]).casefold() in normalized for row in rows)

    @staticmethod
    def _is_image_generation(text: str) -> bool:
        return (
            any(marker in text for marker in _GENERATE_MARKERS)
            and any(term in text for term in _IMAGE_TERMS)
            and not NaturalLanguageImageRouter._is_image_edit(text)
        )

    @staticmethod
    def _is_image_edit(text: str) -> bool:
        has_target = (
            any(term in text for term in _IMAGE_TERMS)
            or bool(_FILENAME_RE.search(text))
            or bool(_ANY_FILENAME_RE.search(text))
        )
        return any(marker in text for marker in _STRONG_EDIT_MARKERS) or (
            has_target and any(marker in text for marker in _EDIT_MARKERS)
        )

    @staticmethod
    def _is_explicit_competing_request(text: str, capability: RouteCapability) -> bool:
        markers = {
            RouteCapability.PAPER_SEARCH: ("查论文", "找论文", "论文检索", "arxiv"),
            RouteCapability.HUMANIZER: ("润色", "改写", "去模板腔", "更自然"),
            RouteCapability.VIDEO: ("生成视频", "做视频", "视频生成"),
            RouteCapability.CAREER: ("职业规划", "生涯规划", "规划职业"),
        }
        return any(marker.casefold() in text.casefold() for marker in markers.get(capability, ()))

    @staticmethod
    def _generate_prompt(text: str) -> str:
        prompt = text.strip()
        prompt = re.sub(
            r"^(?:请|帮我|给我|我想要?|想要)?\s*(?:生成|绘制|画|制作|做)\s*"
            r"(?:一张|一幅|一张图片|一幅图片|一个)?\s*",
            "",
            prompt,
        )
        return prompt.strip(" ：:，,。")

    @staticmethod
    def _parameters(text: str) -> tuple[ImageRouteParameters, tuple[str, str] | None]:
        match = _DIMENSION_RE.search(text)
        if match:
            width, height = int(match.group("w")), int(match.group("h"))
            if width == height == 1024:
                return ImageRouteParameters(), None
            if width > height and (width, height) == (1536, 1024):
                return ImageRouteParameters(size="1536*1024"), None
            if height > width and (width, height) == (1024, 1536):
                return ImageRouteParameters(size="1024*1536"), None
            return ImageRouteParameters(), (
                "invalid_image_parameters",
                "图片尺寸不在已登记模型的支持范围内，请使用 1024×1024、1536×1024 或 1024×1536。",
            )
        if any(term in text for term in ("横版", "宽屏", "16:9")):
            return ImageRouteParameters(size="1536*1024"), None
        if any(term in text for term in ("竖版", "人像构图", "9:16")):
            return ImageRouteParameters(size="1024*1536"), None
        return ImageRouteParameters(), None

    @staticmethod
    def _rejected(code: str, message: str) -> RouteDecision:
        return RouteDecision(
            capability=RouteCapability.IMAGE,
            operation=RouteOperation.REJECT,
            confidence=0.99,
            reason=message,
            error_code=code,
            error_message=message,
        )


def image_request_from_decision(decision: RouteDecision) -> dict[str, object] | None:
    """把已持久化前的图片路由合同转换为现有图片任务载荷。"""

    if decision.contract is None or decision.operation not in {
        RouteOperation.GENERATE,
        RouteOperation.EDIT,
    }:
        return None
    contract = decision.contract
    return ImageRequestPayload(
        kind=ImageTaskKind(contract.operation.value),
        prompt=contract.prompt,
        source_object_id=contract.source_object_id,
        source_scope="knowledge_base" if contract.source_object_id else None,
        size=contract.parameters.size,
    ).model_dump(mode="json")


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0]


__all__ = [
    "CapabilityRouteRegistry",
    "NaturalLanguageImageRouter",
    "RouteRegistration",
    "create_default_route_registry",
    "image_request_from_decision",
]
