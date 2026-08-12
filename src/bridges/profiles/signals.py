"""统一的四维画像消息信号分类。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from bridges.contracts.profiles import FourDimension


PROFILE_SIGNAL_CLASSIFIER_VERSION = "profile-signal-v2"


class ProfileSignalCategory(StrEnum):
    """画像流水线共享的消息级分类。"""

    NO_SIGNAL = "no_signal"
    EXPLICIT_SELF = "explicit_self"
    HIGH_CONFIDENCE_SELF = "high_confidence_self"
    BEHAVIOR_OBSERVATION = "behavior_observation"
    AMBIGUOUS = "ambiguous"
    FORBIDDEN = "forbidden"
    CORRECTION = "correction"


@dataclass(frozen=True, slots=True)
class ProfileCorrectionIntent:
    """用户明确要求修改画像时解析出的目标维度和新值。"""

    dimension: FourDimension | None
    new_value: str | None


@dataclass(frozen=True, slots=True)
class ProfileSignalClassification:
    """分类结果及其稳定原因码。"""

    category: ProfileSignalCategory
    reason_code: str
    confidence: float
    strategy_version: str = PROFILE_SIGNAL_CLASSIFIER_VERSION
    correction_intent: ProfileCorrectionIntent | None = None

    @property
    def is_self_statement(self) -> bool:
        return self.category in {
            ProfileSignalCategory.EXPLICIT_SELF,
            ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
        }

    @property
    def is_local(self) -> bool:
        return self.category in {
            ProfileSignalCategory.EXPLICIT_SELF,
            ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
            ProfileSignalCategory.BEHAVIOR_OBSERVATION,
            ProfileSignalCategory.CORRECTION,
        }

    @property
    def should_process(self) -> bool:
        return self.category not in {
            ProfileSignalCategory.NO_SIGNAL,
            ProfileSignalCategory.FORBIDDEN,
        }


_QUOTED_CONTEXT = re.compile(
    r"(?:引用|引述|据说|有人说|原文|摘录|转述|转发)|[“\"「『].*[”\"」』]"
)
_THIRD_PARTY = re.compile(
    r"(?:我(?:的)?(?:朋友|同学|同事|家人|老师)|(?:^|[，。；：:\s])"
    r"(?:第三方|朋友|同学|同事|家人|老师|网友|别人|他|她|他们|她们)"
    r"(?:是|想|喜欢|在|要|正在|的|说)?"
    r")"
)
_HYPOTHETICAL = re.compile(r"(?:假设|假如|如果|设想|模拟|扮演|角色扮演)")
_NEGATION = re.compile(r"(?:不想|不喜欢|不要|别|不是|没有|无需|不再|拒绝|避免)")
_SENSITIVE = re.compile(
    r"(?:焦虑|抑郁|情绪|健康|疾病|病史|诊断|人格|心理|性格|政治|宗教|"
    r"佛教|基督教|伊斯兰教|道教|天主教|犹太教|糖尿病|癌症|高血压|"
    r"身份证|身份信息|财务|密码|密钥|邮箱|手机号|住址|地址|精确位置|"
    r"私信|私人通信)"
)

_EXPLICIT_PROFILE = re.compile(
    r"(?:^|[，。；：:,\s])(?:"
    r"我(?:的|目前|现在|对|喜欢|计划|打算|想|要|准备|正在|在|是|在读|就读|研究)"
    r"|(?:给|帮)我规划)"
)
_CORRECTION_MARKER = re.compile(
    r"(?:改主意|换方向|改变方向|转向|不是.{0,40}而是|不感兴趣.{0,20}更喜欢|"
    r"(?:修改|改|换|替换|调整|更新)(?:成|为|一下))"
)
_CORRECTION_REPLACEMENT = re.compile(
    r"(?:修改|改|换|替换|调整|更新)(?:[^，。；;:：]{0,24})?(?:成|为)\s*"
    r"([^。！？!?；;，,]+)"
)
_CORRECTION_NEGATED_REPLACEMENT = re.compile(
    r"不是[^，。；;]+[，,、]?而是\s*([^。！？!?；;，,]+)"
)
_CORRECTION_DISINTERESTED_REPLACEMENT = re.compile(
    r"不感兴趣了?[^，。；;]*[，,、]?\s*(?:现在)?(?:更)?喜欢\s*"
    r"([^。！？!?；;，,]+)"
)
_CORRECTION_DIRECTION = re.compile(
    r"(?:改主意了?|换方向了?|改变方向了?|转向了?)[，,：:]?\s*"
    r"(.+?)(?:[。！？!?；;]|$)"
)
_KNOWLEDGE_HINT = re.compile(
    r"(?i)(?:cnn|transformer|卷积|神经网络|算法|编程|数学|物理|化学|"
    r"生物|历史|哲学|天文|地理|科学|技术|知识|学习|研究|论文|专业)"
)
_ACADEMIC_HINT = re.compile(
    r"(?:大[一二三四]|研[一二三]|本科|研究生|硕士|博士|大学生|学生|专业|学历|年级|在读)"
)
_GOAL_HINT = re.compile(
    r"(?:目标|计划|打算|规划|考研|雅思|托福|考试|毕业|申请|完成|通过|提升)"
)
_HOBBY_HINT = re.compile(
    r"(?:跑步|游泳|运动|音乐|乐器|绘画|画画|摄影|旅行|旅游|游戏|烘焙|"
    r"做饭|园艺|电影|追剧|书法|手工|宠物)"
)
_HIGH_ACADEMIC = re.compile(
    r"^(?:目前|现在)?(?!(?:目标|计划|打算|规划))(?:大[一二三四]|研[一二三]|本科(?:生)?|研究生|"
    r"硕士(?:生)?|博士(?:生)?|大学生|学生|[\u4e00-\u9fffA-Za-z0-9+#.-]{2,32}专业)"
)
_HIGH_GOAL = re.compile(r"^(?:目标|计划|打算|规划)(?:是|为|：|:)?\s*\S+")
_HIGH_LEARNING = re.compile(
    r"^(?!(?:如何|怎么|为什么|什么是))"
    r"(?:(?:想|要|准备|正在|在)\s*)?(?:学(?:习)?|研究)\s*\S+"
)
_QUESTION_LIKE = re.compile(
    r"^(?:(?:目前|现在)?(?:大[一二三四]|研[一二三]|本科(?:生)?|研究生|"
    r"硕士(?:生)?|博士(?:生)?|大学生|学生|目标|计划|打算|规划)?"
    r"(?:是|为|：|:)?\s*"
    r"(?:如何|怎么|为什么|什么|能否|请问|是否|有没有))"
)
_BEHAVIOR_PREFIX = re.compile(
    r"^(?:找|搜索|查找|检索|查|看|阅读|推荐|了解|介绍|什么是|"
    r"如何|怎么|为什么|能否|请问)\s*\S+"
)


class ProfileSignalClassifier:
    """以确定性规则给消息分配唯一的画像信号类别。"""

    version = PROFILE_SIGNAL_CLASSIFIER_VERSION

    def classify(self, content: str) -> ProfileSignalClassification:
        text = content.strip()
        if not text:
            return self._result(ProfileSignalCategory.NO_SIGNAL, "empty", 1.0)

        forbidden_reason = self._forbidden_reason(text)
        if forbidden_reason is not None:
            return self._result(ProfileSignalCategory.FORBIDDEN, forbidden_reason, 1.0)

        correction_intent = self._correction_intent(text)
        if correction_intent is not None:
            return self._result(
                ProfileSignalCategory.CORRECTION,
                (
                    "profile_correction_intent"
                    if correction_intent.dimension is not None
                    and correction_intent.new_value is not None
                    else "profile_correction_unresolved"
                ),
                1.0,
                correction_intent=correction_intent,
            )

        if self._has_ambiguous_profile_expression(text):
            return self._result(
                ProfileSignalCategory.AMBIGUOUS,
                "ambiguous_profile_candidate",
                0.5,
            )

        if _QUESTION_LIKE.search(text):
            return self._result(
                ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                "course_or_profile_question",
                0.9,
            )

        if _EXPLICIT_PROFILE.search(text) and self._has_profile_expression(text):
            return self._result(
                ProfileSignalCategory.EXPLICIT_SELF,
                "explicit_self_statement",
                0.99,
            )

        if _HIGH_ACADEMIC.search(text):
            return self._result(
                ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
                "subject_omitted_academic_statement",
                0.95,
            )
        if _HIGH_GOAL.search(text) or _HIGH_LEARNING.search(text):
            return self._result(
                ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
                "subject_omitted_learning_statement",
                0.95,
            )

        if _BEHAVIOR_PREFIX.search(text):
            return self._result(
                ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                "search_or_question_observation",
                0.9,
            )

        return self._result(ProfileSignalCategory.NO_SIGNAL, "no_profile_signal", 1.0)

    def _result(
        self,
        category: ProfileSignalCategory,
        reason_code: str,
        confidence: float,
        correction_intent: ProfileCorrectionIntent | None = None,
    ) -> ProfileSignalClassification:
        return ProfileSignalClassification(
            category=category,
            reason_code=reason_code,
            confidence=confidence,
            strategy_version=self.version,
            correction_intent=correction_intent,
        )

    @staticmethod
    def _forbidden_reason(text: str) -> str | None:
        if _QUOTED_CONTEXT.search(text):
            return "quoted_or_relayed_text"
        if _HYPOTHETICAL.search(text):
            return "hypothetical_or_role_play"
        if _THIRD_PARTY.search(text):
            return "third_party_statement"
        if _NEGATION.search(text) and not (
            _CORRECTION_NEGATED_REPLACEMENT.search(text)
            or _CORRECTION_DISINTERESTED_REPLACEMENT.search(text)
        ):
            return "negated_statement"
        if _SENSITIVE.search(text):
            return "sensitive_content"
        return None

    @staticmethod
    def _has_profile_expression(text: str) -> bool:
        return bool(
            re.search(
                r"(?:目标|计划|打算|规划|学习|学|研究|感兴趣|喜欢|在读|就读|"
                r"大学|本科|研究生|硕士|博士|专业|学生)",
                text,
            )
        )

    @staticmethod
    def _has_ambiguous_profile_expression(text: str) -> bool:
        return bool(
            re.search(r"(?:我|我的).{0,20}(?:可能|也许|似乎|不确定|倾向于)", text)
        )

    @classmethod
    def _correction_intent(cls, text: str) -> ProfileCorrectionIntent | None:
        if not _CORRECTION_MARKER.search(text):
            return None

        raw_value: str | None = None
        replacement = _CORRECTION_REPLACEMENT.search(text)
        if replacement is not None:
            raw_value = replacement.group(1)
        if raw_value is None:
            replacement = _CORRECTION_NEGATED_REPLACEMENT.search(text)
            if replacement is not None:
                raw_value = replacement.group(1)
        if raw_value is None:
            replacement = _CORRECTION_DISINTERESTED_REPLACEMENT.search(text)
            if replacement is not None:
                raw_value = replacement.group(1)
        if raw_value is None:
            direction = _CORRECTION_DIRECTION.search(text)
            if direction is not None:
                raw_value = direction.group(1)

        value = cls._normalize_correction_value(raw_value)
        dimension = cls._infer_correction_dimension(text, value)
        if dimension is None and not cls._has_explicit_correction_context(text):
            return None
        return ProfileCorrectionIntent(dimension=dimension, new_value=value)

    @staticmethod
    def _has_explicit_correction_context(text: str) -> bool:
        return bool(
            re.search(
                r"(?:画像|关注点|学业情况|学业|兴趣爱好|阶段目标|"
                r"改主意|换方向|改变方向|转向)",
                text,
            )
        )

    @staticmethod
    def _normalize_correction_value(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"\s+", " ", value.strip(" \t\r\n，。；：:、,;.!！？?"))
        normalized = re.sub(
            r"^我(?:现在|目前)?(?:更)?喜欢\s*(?:学(?:习)?|研究)?\s*",
            "",
            normalized,
        )
        normalized = re.sub(
            r"^(?:现在|目前)?(?:想|要|准备|正在|在)\s*(?:学(?:习)?|研究)?\s*",
            "",
            normalized,
        )
        normalized = re.sub(r"^(?:学(?:习)?|研究)\s*", "", normalized)
        normalized = normalized.strip(" \t，,：:")
        return normalized if 1 < len(normalized) <= 200 else None

    @staticmethod
    def _infer_correction_dimension(
        text: str, value: str | None
    ) -> FourDimension | None:
        if re.search(r"(?:阶段目标|目标|计划|打算|规划)", text):
            return FourDimension.STAGE_GOAL
        if re.search(r"(?:学业情况|学业|学校|专业|学历|年级|在读)", text):
            return FourDimension.ACADEMIC_STATUS
        if re.search(r"(?:兴趣爱好|爱好)", text):
            return FourDimension.HOBBY
        if re.search(r"(?:关注点|感兴趣的知识|知识兴趣|学习|学|研究)", text):
            return FourDimension.KNOWLEDGE_INTEREST
        semantic_text = f"{text} {value or ''}"
        if _GOAL_HINT.search(semantic_text):
            return FourDimension.STAGE_GOAL
        if value is not None and _ACADEMIC_HINT.search(value):
            return FourDimension.ACADEMIC_STATUS
        if value is not None and _HOBBY_HINT.search(value):
            return FourDimension.HOBBY
        if value is not None and _KNOWLEDGE_HINT.search(value):
            return FourDimension.KNOWLEDGE_INTEREST
        return None


__all__ = [
    "PROFILE_SIGNAL_CLASSIFIER_VERSION",
    "ProfileSignalCategory",
    "ProfileSignalClassification",
    "ProfileCorrectionIntent",
    "ProfileSignalClassifier",
]
