"""论文模块的术语词表：消歧候选、语境关键词与中英扩展词。

词表是**解析的确定性底座**：它让「孤立 Transformer 先消歧」「机器学习语境
则检索相应主题」这类验收例可被稳定复现与测试，不依赖模型随机性。词表只
覆盖确有歧义或确有译名差异的术语；未命中词表的术语按原词检索，绝不悄悄
替换用户的原词。

新增条目时保持最小：只加真实会产生错误结果的歧义与常见译名。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TermContext:
    """一个术语的候选语境。"""

    key: str
    label: str
    query_terms: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class AmbiguousTerm:
    """有多个真实语义、需要用户澄清的术语。"""

    term: str
    aliases: tuple[str, ...]
    contexts: tuple[TermContext, ...]


#: 需要先消歧的术语（键为小写英文；别名为原文中可能出现的其他写法）。
AMBIGUOUS_TERMS: tuple[AmbiguousTerm, ...] = (
    AmbiguousTerm(
        term="transformer",
        aliases=("transformer", "transformers", "transformer模型", "transformers模型"),
        contexts=(
            TermContext(
                key="machine_learning",
                label="机器学习中的 Transformer 结构（注意力机制、NLP、大模型）",
                query_terms=("transformer", "attention mechanism"),
                keywords=(
                    "机器学习", "深度学习", "神经网络", "注意力", "自然语言", "nlp",
                    "大模型", "大语言模型", "llm", "语言模型", "ai", "人工智能",
                    "文本生成", "机器翻译", "gpt", "bert",
                    "machine learning", "deep learning", "attention", "neural",
                ),
            ),
            TermContext(
                key="power_electronics",
                label="电力系统中的变压器（变电、输配电、电气工程）",
                query_terms=("power transformer", "transformer substation"),
                keywords=(
                    "电力", "变压器", "电网", "变电", "输配电", "电气", "配电",
                    "电压", "高压", "发电", "电流", "电力系统", "power system",
                    "power transformer", "grid", "substation", "voltage",
                ),
            ),
        ),
    ),
    AmbiguousTerm(
        term="diffusion",
        aliases=("diffusion", "diffusion model", "扩散模型"),
        contexts=(
            TermContext(
                key="generative_model",
                label="生成模型中的扩散模型（图像/视频生成）",
                query_terms=("diffusion model", "denoising diffusion"),
                keywords=(
                    "扩散模型", "生成", "图像生成", "文生图", "stable diffusion",
                    "去噪", "generative", "image generation", "denoising",
                ),
            ),
            TermContext(
                key="physics",
                label="物理/材料中的扩散现象（热扩散、粒子扩散）",
                query_terms=("diffusion process", "thermal diffusion"),
                keywords=(
                    "物理",
                    "热扩散",
                    "粒子",
                    "材料",
                    "传质",
                    "thermal",
                    "particle",
                    "physics",
                ),
            ),
        ),
    ),
    AmbiguousTerm(
        term="agent",
        aliases=("agent", "agents", "智能体", "代理"),
        contexts=(
            TermContext(
                key="llm_agent",
                label="大模型智能体（工具调用、多智能体协作）",
                query_terms=("llm agent", "language model agent"),
                keywords=(
                    "智能体", "大模型", "工具调用", "多智能体", "llm", "agentic",
                    "语言模型", "自动化任务", "工作流",
                ),
            ),
            TermContext(
                key="multi_agent_system",
                label="分布式人工智能中的多智能体系统（博弈、仿真）",
                query_terms=("multi-agent system", "multiagent reinforcement learning"),
                keywords=("多智能体系统", "博弈", "仿真", "强化学习", "mas", "game theory"),
            ),
        ),
    ),
)

#: 语境关键词 → 上下文（不含歧义术语的通用指向词对，供前文语境推断使用）。
#: 这里只登记与 AMBIGUOUS_TERMS 的候选语境直接相关的词，未登记的语境不算数。
CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    context.key: context.keywords
    for term in AMBIGUOUS_TERMS
    for context in term.contexts
}

#: 术语 → 英文检索词（中文术语必须译成 arXiv 可检索的英文词；原词同时保留在
#: 查询与展示中）。只登记常见且稳定的译名，未登记的中文术语按原词检索。
TERM_ENGLISH: dict[str, str] = {
    "注意力机制": "attention mechanism",
    "注意力": "attention",
    "大语言模型": "large language model",
    "大模型": "large language model",
    "语言模型": "language model",
    "图神经网络": "graph neural network",
    "目标检测": "object detection",
    "语义分割": "semantic segmentation",
    "图像分类": "image classification",
    "强化学习": "reinforcement learning",
    "迁移学习": "transfer learning",
    "联邦学习": "federated learning",
    "对比学习": "contrastive learning",
    "自监督学习": "self-supervised learning",
    "知识图谱": "knowledge graph",
    "推荐系统": "recommender system",
    "情感分析": "sentiment analysis",
    "问答系统": "question answering",
    "机器翻译": "machine translation",
    "语音识别": "speech recognition",
    "扩散模型": "diffusion model",
    "生成对抗网络": "generative adversarial network",
    "多模态": "multimodal",
    "检索增强生成": "retrieval augmented generation",
    "思维链": "chain of thought",
    "模型压缩": "model compression",
    "知识蒸馏": "knowledge distillation",
    "异常检测": "anomaly detection",
    "时间序列": "time series",
    "因果推断": "causal inference",
    "分布式系统": "distributed system",
    "操作系统": "operating system",
    "计算机网络": "computer network",
    "数据库": "database",
    "编译器": "compiler",
    "算法复杂度": "algorithm complexity",
    "组合优化": "combinatorial optimization",
}

#: 意图词与虚词：从原文中剥离，不参与主题短语（避免把「找」「入门」当主题）。
INTENT_STOPWORDS: tuple[str, ...] = (
    "帮我", "请", "麻烦", "给我", "找", "搜索", "搜", "查", "检索", "推荐",
    "一下", "一些", "几篇", "几篇论文", "论文", "文献", "paper", "papers",
    "相关的", "相关", "关于", "有关", "了解一下", "学习", "入门", "入门级",
    "最新的", "最新", "近年的", "经典的", "经典的论文", "综述", "综述论文",
    "的", "和", "与", "以及", "或者", "在", "上", "里", "方面", "方向",
    "领域", "研究", "想", "要", "看", "读", "search", "find", "for", "about",
    "on", "research", "survey", "review", "tutorial", "introductory", "intro",
    "latest", "recent", "classic", "foundational",
)

#: 学习阶段词：影响排序意图（入门/经典/最新）。
BEGINNER_HINTS: tuple[str, ...] = (
    "入门", "初学", "基础", "新手", "零基础", "刚开始", "beginner", "intro",
    "introductory", "tutorial", "入门级", "自学",
)
CLASSIC_HINTS: tuple[str, ...] = (
    "经典", "奠基", "开山", "里程碑", "最有影响", "必读", "classic",
    "foundational", "seminal", "influential",
)
LATEST_HINTS: tuple[str, ...] = (
    "最新", "最近", "前沿", "近期", "这几年", "近年", "最新进展", "latest",
    "recent", "state of the art", "sota", "前沿进展",
)
SURVEY_HINTS: tuple[str, ...] = ("综述", "回顾", "survey", "review", "综述论文", "文献综述")

#: 「近 N 年」的中文数字写法（时间偏好解析用）。
CHINESE_YEAR_OFFSETS: dict[str, int] = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10,
}
