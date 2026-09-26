"""资料模块的确定性词表与规则（无 I/O）。

这里只放「解析与排序」需要的稳定词表：学习层次的候选与关键词、学习目的的
常见说法、中文专业名词到英文查询词的对照，以及判断适用阶段的入门／进阶标
记。模型不参与解析，因此同一句话在任何一次运行里都解析出相同结果。
"""

from __future__ import annotations

from bridges.resources.contracts import ResourcesLevel, ResourcesLevelCandidate

#: 学习层次的三个候选（澄清时逐项列出，用户回答后据此解析）。
LEVEL_CANDIDATES: tuple[ResourcesLevelCandidate, ...] = (
    ResourcesLevelCandidate(
        key=ResourcesLevel.BEGINNER,
        label="零基础入门：还没接触过，先要看得懂",
        keywords=("零基础", "没基础", "完全没有", "小白", "入门", "初学", "新手", "从头"),
    ),
    ResourcesLevelCandidate(
        key=ResourcesLevel.BASIC,
        label="有一定基础：学过一点，要巩固与应付考试",
        keywords=("有基础", "学过", "有点基础", "巩固", "复习", "考试", "备考", "期末", "补基础"),
    ),
    ResourcesLevelCandidate(
        key=ResourcesLevel.ADVANCED,
        label="进阶提高：要在项目或研究里深入用",
        keywords=("进阶", "深入", "提高", "精通", "项目", "实战", "科研", "论文复现", "面试"),
    ),
)

#: 层次关键词（回答或原话命中即判定层次；同一句命中多个时取最靠后的一个）。
LEVEL_HINTS: dict[str, tuple[str, ...]] = {
    ResourcesLevel.BEGINNER: (
        "零基础", "没基础", "没有基础", "完全没有基础", "小白", "新手", "初学",
        "入门", "从头学", "没学过", "0基础",
    ),
    ResourcesLevel.BASIC: (
        "有基础", "有点基础", "有一定基础", "学过", "巩固", "复习", "备考",
        "准备考试", "应付考试", "期末考试", "补基础", "基础不牢",
    ),
    ResourcesLevel.ADVANCED: (
        "进阶", "深入", "提高", "精通", "项目实战", "做项目", "科研", "面试",
        "论文复现", "高级", "底层",
    ),
}

#: 学习目的的常见说法 → 正文里的中文说明（用于选择理由与阶段措辞）。
GOAL_HINTS: dict[str, tuple[str, ...]] = {
    "备考": ("考试", "备考", "期末", "考研", "四级", "六级", "测验", "考证书"),
    "入门了解": ("入门", "了解", "看看是什么", "科普", "先了解"),
    "项目实战": ("项目", "实战", "做出来", "动手", "搭建", "开发一个"),
    "科研": ("科研", "做研究", "论文", "复现", "实验"),
    "工作面试": ("面试", "找工作", "求职", "实习", "招聘"),
}

#: 中文专业名词 → 英文查询词（只作**扩展**，原词始终保留在查询里）。
TERM_ENGLISH: dict[str, str] = {
    "机器学习": "machine learning",
    "深度学习": "deep learning",
    "强化学习": "reinforcement learning",
    "迁移学习": "transfer learning",
    "神经网络": "neural network",
    "大模型": "large language model",
    "自然语言处理": "natural language processing",
    "计算机视觉": "computer vision",
    "数据分析": "data analysis",
    "数据挖掘": "data mining",
    "数据结构": "data structures",
    "算法": "algorithms",
    "操作系统": "operating system",
    "计算机网络": "computer networks",
    "计算机组成": "computer organization",
    "编译原理": "compilers",
    "数据库": "database",
    "离散数学": "discrete mathematics",
    "线性代数": "linear algebra",
    "概率论": "probability theory",
    "高等数学": "calculus",
    "微积分": "calculus",
    "数理统计": "mathematical statistics",
    "数字电路": "digital circuits",
    "信号与系统": "signals and systems",
    "通信原理": "communication principles",
    "自动控制": "automatic control",
    "电气工程": "electrical engineering",
    "机械设计": "mechanical design",
    "土木工程": "civil engineering",
    "前端开发": "frontend development",
    "后端开发": "backend development",
    "全栈开发": "full stack development",
    "爬虫": "web scraping",
    "分布式系统": "distributed systems",
    "计算机图形学": "computer graphics",
    "软件工程": "software engineering",
    "信息安全": "information security",
    "知识蒸馏": "knowledge distillation",
    "注意力机制": "attention mechanism",
    "图神经网络": "graph neural network",
    "目标检测": "object detection",
    "推荐系统": "recommender system",
    "大语言模型": "large language model",
    "提示工程": "prompt engineering",
    "强化学习与决策": "reinforcement learning",
    "电路分析": "circuit analysis",
    "模拟电路": "analog circuits",
    "数字信号处理": "digital signal processing",
    "电力系统": "power systems",
    "理论力学": "theoretical mechanics",
    "材料力学": "mechanics of materials",
    "工程热力学": "engineering thermodynamics",
    "有机化学": "organic chemistry",
    "物理化学": "physical chemistry",
    "生物化学": "biochemistry",
    "会计学": "accounting",
    "微观经济学": "microeconomics",
    "宏观经济学": "macroeconomics",
    "管理学": "management",
    "统计学": "statistics",
    "运筹学": "operations research",
}

#: 提取主题词时要剥掉的中文意图／指代词（不是专业名词的一部分）。
#: 中文没有词边界，按子串剥离；拉丁词另见 ``LATIN_INTENT_WORDS``。
INTENT_STOPWORDS: tuple[str, ...] = (
    "我想学", "我想学习", "我要学", "我想", "我要", "想学", "想学习", "打算学",
    "帮我", "帮忙", "请帮我", "请帮", "给我", "给我推荐", "推荐一下", "推荐",
    "找一下", "找找", "找", "有没有", "剩下的", "一些", "几本", "几门", "几条",
    "学习资料", "资料", "教材", "书籍", "图书", "书", "视频", "教程", "课程",
    "网课", "公开课", "入门", "进阶", "零基础", "基础", "我", "你", "的", "了",
    "吧", "呢", "吗", "啊",
)

#: 提取主题词时要剥掉的拉丁意图词（**按词边界**剥离，避免把 Transformer
#: 里的 "a"、Python 里的 "on" 当意图词切掉）。
#: 注意：``learning``／``studying`` 这类同时是专业名词组成部分的词不在此列，
#: 否则「deep learning」会被切成「deep」。
LATIN_INTENT_WORDS: tuple[str, ...] = (
    "learn", "study", "book", "books", "video", "videos", "tutorial",
    "tutorials", "course", "courses", "recommend", "recommendation",
    "want", "wanna", "please", "how", "the", "for", "about", "some", "any",
    "give", "me", "my", "i", "a", "an", "to", "of",
)

#: 图书／视频的入门标记（阶段判定：命中越多越靠前）。
BEGINNER_MARKERS: tuple[str, ...] = (
    "入门", "基础", "初学", "新手", "小白", "从零", "零基础", "第一本",
    "introduction", "introductory", "beginner", "basics", "for dummies",
    "first", "starting", "getting started", "primer", "essentials",
)

#: 图书／视频的进阶标记（阶段判定：命中越多越靠后）。
ADVANCED_MARKERS: tuple[str, ...] = (
    "进阶", "深入", "高级", "精通", "实战", "原理", "内幕", "源码",
    "advanced", "in depth", "in-depth", "deep dive", "expert", "mastering",
    "handbook", "internals", "pro", "professional",
)

#: 视频查询词的层次后缀（让公网发现的视频与本轮层次对齐）。
VIDEO_QUERY_SUFFIX: dict[str, str] = {
    ResourcesLevel.BEGINNER: "零基础 入门 教程",
    ResourcesLevel.BASIC: "系统 讲解",
    ResourcesLevel.ADVANCED: "进阶 深入",
}

#: 图书查询词不接受的主题词（来源命中这些词的条目会被排除并计数）。
TOPIC_STOPWORDS: frozenset[str] = frozenset(
    {
        "都行", "随便", "任何", "什么", "怎么", "如何", "哪个",
        "anything", "whatever", "something", "somehow",
    }
)
