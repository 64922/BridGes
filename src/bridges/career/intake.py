"""生涯规划最小必需输入判断（Issue 09 intake）。

信息不足时不应先做长模型调用：生涯助手在启动完整规划生成之前，用
确定性规则（不依赖模型判断）检查用户意图文本是否已说明「目标方向」
与「当前阶段」两个最影响方案的信息。任一缺失时只返回一个关键澄清
问题（方向优先于阶段），由聊天分支直接展示给用户，不启动检索、
不调用结构化模型——避免「等 95 秒后连接中断」式的无谓等待。

时间范围不单独列为缺口：当前阶段（年级/身份）足以推导时间线；
「未来 7 天行动」等近期粒度由生成提示词保证。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 目标方向词表：领域/职业/岗位核心词。命中任一即视为意图中已给出
#: 明确方向（「数据」「产品」「算法」等），「计算机/软件」等基础
#: 领域词也算方向（说明已有偏好或正在探索的领域）。
_DIRECTION_WORDS: tuple[str, ...] = (
    # 数据/算法/AI
    "数据",
    "算法",
    "人工智能",
    "机器学习",
    "深度学习",
    "大模型",
    "自然语言",
    "计算机视觉",
    # 工程
    "前端",
    "后端",
    "全栈",
    "软件",
    "硬件",
    "测试",
    "运维",
    "安全",
    "嵌入式",
    "网络",
    # 产品/设计/运营/市场
    "产品",
    "设计",
    "运营",
    "市场",
    "营销",
    "销售",
    "公关",
    "内容",
    "电商",
    # 职能
    "财务",
    "会计",
    "审计",
    "人力",
    "行政",
    "管理",
    "咨询",
    "投资",
    "金融",
    "量化",
    "证券",
    "银行",
    # 科研/教育/医疗/法律/传媒
    "科研",
    "学术",
    "研究",
    "教师",
    "教育",
    "医生",
    "医学",
    "临床",
    "药学",
    "护士",
    "法律",
    "律师",
    "法务",
    "新闻",
    "媒体",
    "翻译",
    "外语",
    # 行业
    "游戏",
    "动画",
    "影视",
    "建筑",
    "土木",
    "机械",
    "电子",
    "电气",
    "自动化",
    "能源",
    "物流",
    "供应链",
    "地产",
    "餐饮",
    "体育",
    "音乐",
    "艺术",
    "生物",
    "化学",
    "物理",
    "数学",
    "计算机",
)

#: 当前阶段词表：年级/学历/身份。命中任一即视为意图中已给出当前
#: 阶段（可推导时间线）。「工作 N 年」由 ``_WORK_YEARS`` 正则补充。
_STAGE_WORDS: tuple[str, ...] = (
    "大一",
    "大二",
    "大三",
    "大四",
    "大五",
    "研一",
    "研二",
    "研三",
    "博一",
    "博二",
    "博三",
    "博四",
    "高一",
    "高二",
    "高三",
    "初中",
    "高中",
    "本科",
    "硕士",
    "博士",
    "研究生",
    "大专",
    "专科",
    "高职",
    "应届",
    "毕业",
    "在校",
    "在读",
    "在职",
    "实习",
    "待业",
)

#: 「工作/工作 N 年」等在职年限（N 为数字）。
_WORK_YEARS = re.compile(r"(?:工作|从业|干了|做了)\s*[一二三四五六七八九十\d]+\s*年")


@dataclass(frozen=True, slots=True)
class IntakeAssessment:
    """最小输入判断结论。

    ``enough`` 为 False 时，``missing_dimension`` 与 ``question`` 给出
    唯一需要用户补充的维度与可直接展示的澄清问题。
    """

    enough: bool
    missing_dimension: str | None
    question: str | None


_QUESTION_BY_DIMENSION: dict[str, str] = {
    "direction": (
        "为了给你可执行的规划，先告诉我：你目前最想探索哪个方向？"
        "比如数据分析、产品、算法、运营或其他。"
    ),
    "stage": (
        "你目前处于哪个阶段？比如大一到大四、研究生、应届或在职，"
        "我好按你的时间线安排路径。"
    ),
}


def assess_intake(intent: str) -> IntakeAssessment:
    """判断意图文本是否具备启动完整规划的最小必需信息。

    确定性规则：命中方向词 + 命中阶段词即足够；否则按「方向优先于
    阶段」返回唯一一个最影响方案的关键澄清问题。
    """
    text = (intent or "").strip()
    has_direction = any(word in text for word in _DIRECTION_WORDS)
    has_stage = _has_stage(text)
    if has_direction and has_stage:
        return IntakeAssessment(enough=True, missing_dimension=None, question=None)
    missing = "stage" if has_direction else "direction"
    return IntakeAssessment(
        enough=False,
        missing_dimension=missing,
        question=_QUESTION_BY_DIMENSION[missing],
    )


def _has_stage(text: str) -> bool:
    """意图文本是否已说明当前阶段（年级/学历/身份词或「工作 N 年」）。"""
    return any(word in text for word in _STAGE_WORDS) or _WORK_YEARS.search(text) is not None


__all__ = [
    "IntakeAssessment",
    "assess_intake",
]
