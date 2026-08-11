"""统一的四维画像消息信号分类。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


PROFILE_SIGNAL_CLASSIFIER_VERSION = "profile-signal-v2"


class ProfileSignalCategory(StrEnum):
    """画像流水线共享的消息级分类。"""

    NO_SIGNAL = "no_signal"
    EXPLICIT_SELF = "explicit_self"
    HIGH_CONFIDENCE_SELF = "high_confidence_self"
    BEHAVIOR_OBSERVATION = "behavior_observation"
    AMBIGUOUS = "ambiguous"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ProfileSignalClassification:
    """分类结果及其稳定原因码。"""

    category: ProfileSignalCategory
    reason_code: str
    confidence: float
    strategy_version: str = PROFILE_SIGNAL_CLASSIFIER_VERSION

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
    r"(?:朋友|同学|同事|家人|老师|他|她|他们|她们)(?:是|想|喜欢|在|要|正在|的)?)"
)
_HYPOTHETICAL = re.compile(r"(?:假设|假如|如果|设想|模拟|扮演|角色扮演)")
_NEGATION = re.compile(r"(?:不想|不喜欢|不要|别|不是|没有|无需|不再|拒绝|避免)")
_SENSITIVE = re.compile(
    r"(?:焦虑|抑郁|情绪|健康|疾病|病史|诊断|人格|心理|政治|宗教|"
    r"身份证|身份信息|财务|密码|密钥|邮箱|手机号|住址|地址|精确位置|"
    r"私信|私人通信)"
)

_EXPLICIT_PROFILE = re.compile(
    r"(?:^|[，。；：:,\s])(?:"
    r"我(?:的|目前|现在|对|喜欢|计划|打算|想|要|准备|正在|在|是|在读|就读|研究)"
    r"|(?:给|帮)我规划)"
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

        if self._has_ambiguous_profile_expression(text):
            return self._result(
                ProfileSignalCategory.AMBIGUOUS,
                "ambiguous_profile_candidate",
                0.5,
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
    ) -> ProfileSignalClassification:
        return ProfileSignalClassification(
            category=category,
            reason_code=reason_code,
            confidence=confidence,
            strategy_version=self.version,
        )

    @staticmethod
    def _forbidden_reason(text: str) -> str | None:
        if _QUOTED_CONTEXT.search(text):
            return "quoted_or_relayed_text"
        if _HYPOTHETICAL.search(text):
            return "hypothetical_or_role_play"
        if _THIRD_PARTY.search(text):
            return "third_party_statement"
        if _NEGATION.search(text):
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


__all__ = [
    "PROFILE_SIGNAL_CLASSIFIER_VERSION",
    "ProfileSignalCategory",
    "ProfileSignalClassification",
    "ProfileSignalClassifier",
]
