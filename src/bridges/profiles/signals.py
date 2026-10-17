"""统一的画像消息信号分类。

改进工单 04：本地规则不再否决自然自述，也不再代替语义补提取。分类结果
改为承担三件事：

- **调度预检**：只有空消息与确定性的硬禁止消息（引用/转述、假设扮演、
  第三方、敏感内容，且无任何合规片段）不进入语义提取；其余消息交给
  语义抽取提出候选（``ProfileExtractionSource.QWEN_MODEL``），本地规则
  只对行为观察与明确更正保留确定性分支。
- **证据片段核验**：语义候选引用的原话片段按本地规则判定主体归属
  （自述/模糊/行为/禁止）、否定与一次性状态，服务端据此决定提交、
  降级观察或拒绝。
- **自然自述识别**：第一人称陈述句、习惯/频率表达与可复用偏好约束被
  识别为自然自述（``natural_self_description``），不再依赖固定句式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from bridges.contracts.profiles import FourDimension


PROFILE_SIGNAL_CLASSIFIER_VERSION = "profile-signal-v3"


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
class SegmentClassification:
    """一条消息内部片段（分句）的分类与原始区间。

    改进工单 17：混合消息不再因某一片段（第三方、敏感、一次性情绪）被整句
    丢弃；逐片段检查后，合规片段仍可参与抽取。区间是原消息的 Unicode 码点
    半开区间 ``[start, end)``。
    """

    start: int
    end: int
    text: str
    classification: ProfileSignalClassification


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
    r"(?:引用|引述|据说|有人说|原文|摘录|转述|转发|"
    r"(?:助手|模型)(?:说|总结)|(?:文章|材料|文档)内容)|[“\"「『].*[”\"」』]"
)
_THIRD_PARTY = re.compile(
    r"(?:我(?:的)?(?:朋友|同学|同事|家人|老师)|(?:^|[，。；：:\s])"
    r"(?:第三方|朋友|同学|同事|家人|老师|网友|别人|他|她|他们|她们)"
    r"(?:是|想|喜欢|在|要|正在|的|说)?"
    r")"
)
_HYPOTHETICAL = re.compile(r"(?:假设|假如|如果|设想|模拟|扮演|角色扮演)")
_NEGATION = re.compile(r"(?:不想|不喜欢|不要|别|不是|没有|无需|不再|拒绝|避免)")
#: 自我指向的否定偏好（「我不喜欢长篇回答」）是合法可复用事实；它与
#: 「我不想学习 X」等否定意图不同，后者仍按否定陈述保持禁止。
_PREFERENCE_NEGATION = re.compile(r"我(?:现在|目前)?不(?:太)?(?:喜欢|爱|偏好)")
#: 改进工单 17 的可复用信号预检：本地规则覆盖不到的明确偏好与约束
#: （「以后先给结论」「每天 30 分钟」）交给语义模型提出候选，而不是整句跳过。
_REUSABLE_PREFERENCE = re.compile(
    r"(?:以后|之后|下次|今后|平时|每次)[^。！？!?；;\n]{0,40}?"
    r"(?:先|再|别|不用|不要|尽量|多|少|只)"
    r"|(?:每天|每周|每轮)[^。！？!?；;\n]{0,20}(?:分钟|小时|时间|道题|篇)"
    r"|(?:先|再)(?:给|讲|说|写|列|画|用)"
)
_SENSITIVE = re.compile(
    r"(?:焦虑|抑郁|情绪|健康|疾病|病史|诊断|人格|心理(?!学)|性格|政治|宗教|"
    r"佛教|基督教|伊斯兰教|道教|天主教|犹太教|糖尿病|癌症|高血压|"
    r"身份证|身份信息|财务|密码|密钥|邮箱|手机号|住址|地址|精确位置|"
    r"私信|私人通信)"
)
#: 改进工单 04：短期情绪与临时状态不构成长期事实（「今天好累」）。
#: 分类为 NO_SIGNAL 但保留独立原因码，供抽取与审计区分「无可记事实」的
#: 具体理由；显式敏感词（焦虑/抑郁等）仍按 FORBIDDEN 处理，更严格。
#: 一次性拒绝具体提案（「我不用这个刷题计划」）：不是长期自我事实，也不
#: 进入自动抽取队列；不能扩大成「我不吃辣」这类稳定自我描述（工单 20）。
ONE_OFF_REFUSAL = re.compile(
    r"(?:不用|不要|不需要|不想用|不想再|拒绝|不采用|不采纳)"
    r"(?:这个|那个|这种|这项|这份|这套|你的|您的)"
    r"[^。！？!?；;\n]{0,20}"
    r"(?:计划|方案|安排|建议|方法|刷题|练习|课程|任务|题库|清单)"
)
_TRANSIENT_STATE = re.compile(
    r"(?:今天|现在|刚刚|刚才|这会儿|最近|这两天)[^。！？!?；;]{0,10}?"
    r"(?:好累|累死|累了|有点累|好烦|烦躁|郁闷|难受|难过|心情|开心|"
    r"不高兴|生气|紧张|压力)"
    r"|(?:心情|情绪)(?:不太|有点|很)?(?:好|差|低|糟)"
)

_EXPLICIT_PROFILE = re.compile(
    r"(?:^|[，。；：:,\s])(?:"
    r"我(?:的|目前|现在|对|喜欢|计划|打算|想|要|准备|正在|在|是|在读|就读|研究)"
    r"|我(?:现在|目前)?不(?:太)?(?:喜欢|爱|偏好)"
    r"|(?:给|帮)我规划)"
)
#: 纠错语义的单一定义处（画像工单 06 起同时供原子画像分类「更正」使用）：
#: 改主意/换方向属于纠错；明确认错（说错/记错/写错…）同样是纠错。
_CORRECTION_MARKER = re.compile(
    r"(?:改主意|换方向|改变方向|转向|说错|记错|搞错|弄错|记混|写错|更正|纠正|"
    r"不是.{0,40}?(?:而是|是)|不感兴趣.{0,20}更喜欢|"
    r"(?:修改|改|换|替换|调整|更新)(?:成|为|一下))"
)
#: 公开别名：原子画像修订分类复用同一纠错语义（画像工单 06）。
CORRECTION_MARKER = _CORRECTION_MARKER
_CORRECTION_REPLACEMENT = re.compile(
    r"(?:修改|改|换|替换|调整|更新)(?:[^，。；;:：]{0,24})?(?:成|为)\s*"
    r"([^。！？!?；;，,]+)"
)
#: 否定式更正：``不是大四，是大三``（含「之前说错了」式的显式认错）；
#: 间隔限制在短句内，``我不是不想学，只是没时间`` 不构成更正。
_CORRECTION_NEGATED_REPLACEMENT = re.compile(
    r"不是[^，。；;]{1,40}?[，,、]?\s*(?:而是|是)\s*([^。！？!?；;，,]+)"
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
#: 改进工单 17：同一消息内承前省略主语的并列自述（「我喜欢跑步，也喜欢
#: 爬山」）。只认显式并列词开头，不把裸「喜欢 X」晋升为自述。
_ELIDED_SELF = re.compile(
    r"^(?:我\s*)?(?:也|还|又|同时)\s*"
    r"(?:(?:很|比较|特别)?(?:喜欢|爱)|(?:(?:想|要|准备|正在|在)\s*)?学(?:习)?|研究)"
)
_QUESTION_LIKE = re.compile(
    r"^(?:(?:目前|现在)?(?:大[一二三四]|研[一二三]|本科(?:生)?|研究生|"
    r"硕士(?:生)?|博士(?:生)?|大学生|学生|目标|计划|打算|规划)?"
    r"(?:是|为|：|:)?\s*"
    r"(?:如何|怎么|为什么|什么|能否|请问|是否|有没有))"
)
#: 改进工单 04：第一人称疑问不进入自然自述（「我该怎么学」「要不要继续」
#: 「这样对吗」）；它们保持行为观察语义，不产生长期事实。
_NATURAL_QUESTION = re.compile(
    r"^我(?:现在|目前)?(?:该|应该|需要|要不要|是否|能不能|可不可以|"
    r"如何|怎么|为什么|想请问|想问一下)"
    r"|(?:吗|呢)[？?]?$"
)
#: 改进工单 04：自然自述的通用判定（不依赖具体谓词词表）。第一人称
#: 主语的陈述句（可带并列/转折连接词），或带习惯与频率标记的省略主语
#: 陈述，视为自然自述候选；是否构成稳定事实由语义抽取提出、服务端核验。
_NATURAL_SELF = re.compile(
    r"^(?:但|但是|不过|然后|而且|另外|还有)?(?:我|我家|我的)"
    r"|(?:一般|平常|通常|总是|经常|习惯|平时|偶尔|每(?:天|周|晚|次|轮))"
)
_BEHAVIOR_PREFIX = re.compile(
    r"^(?:找|搜索|查找|检索|查|看|阅读|推荐|了解|介绍|什么是|"
    r"如何|怎么|为什么|能否|请问)\s*\S+"
)
#: 改进工单 17：混合消息逐片段检查的切分边界（句子与分句）。
_SEGMENT_DELIMITERS = re.compile(r"[。！？!?；;\n，,]+")
#: 即使片段内出现合规词语也不能作为用户事实的硬禁止原因；这些片段只能
#: 被丢弃，不能降级成观察，也不能因整消息分类而否决其他合规片段。
HARD_FORBIDDEN_REASONS = frozenset(
    {
        "quoted_or_relayed_text",
        "hypothetical_or_role_play",
        "third_party_statement",
        "sensitive_content",
    }
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

        if _TRANSIENT_STATE.search(text):
            return self._result(
                ProfileSignalCategory.NO_SIGNAL, "transient_state", 0.9
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
        if _NATURAL_QUESTION.search(text):
            return self._result(
                ProfileSignalCategory.BEHAVIOR_OBSERVATION,
                "natural_question",
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
        if _ELIDED_SELF.search(text):
            return self._result(
                ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
                "elided_subject_self_statement",
                0.95,
            )
        if _HIGH_GOAL.search(text) or _HIGH_LEARNING.search(text):
            return self._result(
                ProfileSignalCategory.HIGH_CONFIDENCE_SELF,
                "subject_omitted_learning_statement",
                0.95,
            )

        # 改进工单 04：自然自述不再依赖固定句式（「我叫小林」「我养了一只
        # 猫叫团子」「周末一般会去爬山」）；关键词规则只做主体归属与调度
        # 预检，稳定事实由语义提取提出并复核。
        if _NATURAL_SELF.search(text) or _REUSABLE_PREFERENCE.search(text):
            return self._result(
                ProfileSignalCategory.EXPLICIT_SELF,
                "natural_self_description",
                0.8,
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

    def classify_segments(self, content: str) -> list[SegmentClassification]:
        """按分句切分消息并逐片段分类（改进工单 17）。

        混合消息（第三方、引用、敏感片段与合规自述并存）不再整句丢弃：
        每个片段保留原消息码点区间，抽取与验证按片段决定写不写。
        """

        segments: list[SegmentClassification] = []
        position = 0
        for match in _SEGMENT_DELIMITERS.finditer(content):
            self._append_segment(segments, content, position, match.start())
            position = match.end()
        self._append_segment(segments, content, position, len(content))
        return segments

    def _append_segment(
        self,
        segments: list[SegmentClassification],
        content: str,
        start: int,
        end: int,
    ) -> None:
        text = content[start:end]
        if not text.strip():
            return
        segments.append(
            SegmentClassification(
                start=start,
                end=end,
                text=text,
                classification=self.classify(text),
            )
        )

    def has_reusable_signal(self, content: str) -> bool:
        """本地预检：消息是否含任何可复用画像信号（允许混合内容）。

        整消息分类可处理、任一片段可处理，或出现明确偏好/约束表达时为真。
        工单 04 之后自然自述属于可处理信号；本方法只用于审计与兼容读取，
        抽取调度以 :meth:`is_schedulable` 为准（规则不再否决自然自述）。
        """

        if self.classify(content).should_process:
            return True
        if any(
            segment.classification.should_process
            for segment in self.classify_segments(content)
        ):
            return True
        return bool(_REUSABLE_PREFERENCE.search(content))

    def is_schedulable(
        self,
        content: str,
        classification: ProfileSignalClassification | None = None,
    ) -> bool:
        """语义提取调度预检（工单 04）。

        关键词规则不再否决自然自述：只有空消息与确定性的硬禁止消息
        （整消息及所有片段都被引用/假设/第三方/敏感规则否决）不进入
        语义提取队列；其余消息交给语义抽取提出候选，由证据核验与提交
        边界决定写不写。
        """

        if not content.strip():
            return False
        effective = classification or self.extraction_classification(content)
        return effective.category != ProfileSignalCategory.FORBIDDEN

    def extraction_classification(self, content: str) -> ProfileSignalClassification:
        """给出抽取实际使用的有效分类（整消息优先，其次合规片段）。

        ``classify`` 的整消息结果保留给既有投影与审计；抽取路径用本方法，
        避免旧的整消息禁止标签否决所有有效片段。片段命中按自述 > 模糊 >
        行为观察的优先级选择，并在原因码上保留 ``|segment`` 标记。
        """

        whole = self.classify(content)
        if whole.should_process:
            return whole
        priority = {
            ProfileSignalCategory.EXPLICIT_SELF: 0,
            ProfileSignalCategory.HIGH_CONFIDENCE_SELF: 1,
            ProfileSignalCategory.AMBIGUOUS: 2,
            ProfileSignalCategory.BEHAVIOR_OBSERVATION: 3,
        }
        best: ProfileSignalClassification | None = None
        best_rank = len(priority)
        for segment in self.classify_segments(content):
            classification = segment.classification
            if not classification.should_process:
                continue
            rank = priority.get(classification.category, len(priority))
            if rank < best_rank:
                best = classification
                best_rank = rank
        if best is not None:
            return ProfileSignalClassification(
                category=best.category,
                reason_code=f"{best.reason_code}|segment",
                confidence=best.confidence,
                strategy_version=best.strategy_version,
                correction_intent=best.correction_intent,
            )
        if _REUSABLE_PREFERENCE.search(content):
            return ProfileSignalClassification(
                category=ProfileSignalCategory.AMBIGUOUS,
                reason_code="semantic_profile_candidate",
                confidence=0.5,
                strategy_version=self.version,
            )
        return whole

    @staticmethod
    def _forbidden_reason(text: str) -> str | None:
        if _QUOTED_CONTEXT.search(text):
            return "quoted_or_relayed_text"
        if _HYPOTHETICAL.search(text):
            return "hypothetical_or_role_play"
        if _THIRD_PARTY.search(text):
            return "third_party_statement"
        if ONE_OFF_REFUSAL.search(text):
            return "one_off_refusal"
        if _NEGATION.search(text) and not (
            _PREFERENCE_NEGATION.search(text)
            or _CORRECTION_NEGATED_REPLACEMENT.search(text)
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
    "HARD_FORBIDDEN_REASONS",
    "ONE_OFF_REFUSAL",
    "ProfileSignalCategory",
    "ProfileSignalClassification",
    "ProfileCorrectionIntent",
    "ProfileSignalClassifier",
    "SegmentClassification",
]
