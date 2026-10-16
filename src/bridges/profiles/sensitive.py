"""画像工单 09：敏感自述、使用用途与禁止凭据的确定性分类。

本模块是所有长期信息写入路径共用的纯规则合同（不调用模型）：

- **敏感自述**：健康、心理、财务、身份、信仰等自我描述，只有用户说明用途
  （``usage_purpose``）才允许用于回答；没有用途的敏感自述不进上下文。
- **禁止凭据**：密码、验证码、访问密钥等凭据类信息在任何路径都不长期保存，
  明确要求也不保存；写入运行记账的来源快照先做值遮罩。
- **用途**：解析与匹配全部是确定性字面规则（用途短语 → 二元关键词包含），
  不做语义猜测；没有明确依据时宁可不用，不扩大范围。

边界说明：邮箱、手机号等联系方式不在本词表——明确记住是用户主动行为，
自动路径已由信号分类（``signals``）禁止；本模块只覆盖更高敏的类别。
"""

from __future__ import annotations

import re
from enum import StrEnum


class MemorySensitivity(StrEnum):
    """一条长期信息的敏感级别（工单 09）。"""

    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    CREDENTIAL = "credential"


class CredentialKind(StrEnum):
    """禁止长期保存的凭据类型（只作标注，阻断行为一致）。"""

    PASSWORD = "password"
    VERIFICATION_CODE = "verification_code"
    ACCESS_KEY = "access_key"


#: 敏感自述领域词表：命中即视为敏感自述（须说明用途）。
#: ``心理(?!学)`` 不误伤「心理学兴趣」；健康类只收具体经历/状态词，
#: 不收范围过宽的「健康」本身，避免把普通作息话题全部拦下。
_SENSITIVE_SPHERE = re.compile(
    r"(?:焦虑|抑郁|情绪|心理(?!学)|心理健康|"
    r"过敏|疾病|病史|慢性病|诊断|服药|用药|手术|失眠|残疾|残障|"
    r"身份证|身份信息|财务|收入|负债|存款|"
    r"政治|宗教|信仰|佛教|基督教|伊斯兰教|道教|天主教|犹太教|"
    r"婚姻状况|性取向|私人通信)"
)

#: 凭据名词：密码学/密钥学是学科话题，不是凭据（负向断言排除）。
_PASSWORD_NOUN = re.compile(r"(?i)(?:密码(?!学|术)|口令|passcode|password)")
_VERIFICATION_NOUN = re.compile(r"(?:验证码|校验码|动态码|短信验证码|短信码)")
_ACCESS_KEY_NOUN = re.compile(
    r"(?i)(?:访问密钥|密钥(?!学)|私钥|令牌|"
    r"api[\s_-]?key|secret[\s_-]?key|access[\s_-]?key|token)"
)

#: 凭据赋值：名词后紧跟的值（可选连接词）。值只认 ASCII/数字形态，
#: 不吞中文正文（``密码是我的生日`` 匹配不到值，但仍由名词判定为凭据）。
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:密码(?!学|术)|口令|passcode|password|验证码|校验码|动态码|"
    r"短信验证码|短信码|访问密钥|密钥(?!学)|私钥|令牌|"
    r"api[\s_-]?key|secret[\s_-]?key|access[\s_-]?key|token)"
    r"\s*(?:[:：=＝]|是|为|叫|设成|改成|is)?\s*"
    r"(?P<value>[A-Za-z0-9!@#$%^&*_\-+=.]{3,64})",
    re.IGNORECASE,
)

#: 遮罩占位符：保留句式，只把值替换掉。
REDACTED_PLACEHOLDER = "<已隐藏>"

#: 用途解析的句式优先级：显式用途标记 → 「用于/用来」→ 逗号后的场景从句。
_PURPOSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:用途|使用场景|适用场景|使用场合|用在)\s*(?:是|为|[:：])?\s*"
        r"(?P<purpose>[^，。；;！!？?\n]{2,40})"
    ),
    re.compile(
        r"(?:只用于|专门用于|仅用于|用于|用来)\s*"
        r"(?P<purpose>[^，。；;！!？?\n]{1,40})"
    ),
    re.compile(
        r"[，,]\s*(?P<purpose>[^，。；;！!？?\n]{1,20}?)\s*"
        r"(?:的时候|时候|时|建议|提醒|场景|场合|注意事项)"
    ),
)

#: 用途短语里的通用词：去掉后只剩场景关键词，避免「建议」这类词
#: 让任何查询都命中。剥离只发生在短语首尾，且至少保留两个字符。
_PURPOSE_NOISE = (
    "要注意",
    "需要注意",
    "注意",
    "建议",
    "提醒",
    "事项",
    "相关",
    "场景",
    "场合",
    "的时候",
    "时候",
    "时",
    "用",
    "帮我",
    "给我",
    "的",
)

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}")

#: 敏感自述未说明用途时的用户可见解释（不含原文，可直接展示）。
SENSITIVE_PURPOSE_REQUIRED_MESSAGE = (
    "这条信息属于敏感自述，需要说明用途（例如「用于户外活动提醒」）才能保存；"
    "请补充用途后重试。"
)
#: 凭据类信息的用户可见解释（不含原文，可直接展示）。
CREDENTIAL_MEMORY_MESSAGE = (
    "密码、验证码、密钥等凭据类信息不会被长期保存，即使明确要求也不保存。"
)


def classify_credential(text: str) -> CredentialKind | None:
    """按名词命中判断凭据类型；没有凭据名词时返回 ``None``。"""

    if _VERIFICATION_NOUN.search(text):
        return CredentialKind.VERIFICATION_CODE
    if _PASSWORD_NOUN.search(text):
        return CredentialKind.PASSWORD
    if _ACCESS_KEY_NOUN.search(text):
        return CredentialKind.ACCESS_KEY
    return None


def mentions_credential(text: str) -> bool:
    """文本是否出现凭据名词（明确要求保存凭据的判据）。"""

    return classify_credential(text) is not None


def detect_credential_assignment(text: str) -> bool:
    """文本是否包含「凭据=值」形态的赋值（用于检测与遮罩）。"""

    return _CREDENTIAL_ASSIGNMENT.search(text) is not None


def redact_credentials(text: str) -> str:
    """把凭据赋值中的值替换为占位符；保留句式与其余正文。"""

    def _mask(match: re.Match[str]) -> str:
        value = match.group("value")
        return match.group(0).replace(value, REDACTED_PLACEHOLDER)

    return _CREDENTIAL_ASSIGNMENT.sub(_mask, text)


def classify_memory_sensitivity(text: str) -> MemorySensitivity:
    """确定性分类：凭据 > 敏感自述 > 普通。"""

    if classify_credential(text) is not None:
        return MemorySensitivity.CREDENTIAL
    if _SENSITIVE_SPHERE.search(text):
        return MemorySensitivity.SENSITIVE
    return MemorySensitivity.ORDINARY


def is_sensitive_memory(text: str) -> bool:
    """是否属于需要用途门控的信息（敏感自述或凭据）。"""

    return classify_memory_sensitivity(text) is not MemorySensitivity.ORDINARY


def normalize_usage_purpose(value: str | None) -> str | None:
    """规范化用途：折叠空白、去首尾标点与通用词；空或超长返回 ``None``。"""

    if value is None:
        return None
    purpose = " ".join(str(value).split()).strip("，,。；;、:：")
    changed = True
    while changed and purpose:
        changed = False
        for token in sorted(_PURPOSE_NOISE, key=len, reverse=True):
            if len(purpose) <= len(token) + 1:
                continue
            if purpose.endswith(token):
                purpose = purpose[: -len(token)].strip("，,。；;、 ")
                changed = True
            elif purpose.startswith(token):
                purpose = purpose[len(token) :].strip("，,。；;、 ")
                changed = True
    if not purpose or len(purpose) > 60:
        return None
    return purpose


def parse_usage_purpose(text: str | None) -> str | None:
    """从用户原文解析用途短语；解析不出时返回 ``None``，不猜测。"""

    if not text:
        return None
    for pattern in _PURPOSE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        purpose = normalize_usage_purpose(match.group("purpose"))
        if purpose is not None:
            return purpose
    return None


def usage_purpose_keywords(purpose: str | None) -> list[str]:
    """把用途折算为可字面匹配的关键词：中文长词取二元组，英文/数字取整词。"""

    normalized = normalize_usage_purpose(purpose)
    if not normalized:
        return []
    value = normalized
    for token in _PURPOSE_NOISE:
        value = value.replace(token, " ")
    keywords: list[str] = []
    for token in _TOKEN_RE.findall(value):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            keywords.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            keywords.append(token)
    return keywords


def usage_purpose_matches(purpose: str | None, task_text: str | None) -> bool:
    """用途是否适用于当前任务：任一二元关键词出现在任务文本中即命中。"""

    if not purpose or not task_text:
        return False
    task = str(task_text)
    if not task.strip():
        return False
    keywords = usage_purpose_keywords(purpose)
    return bool(keywords) and any(keyword in task for keyword in keywords)
