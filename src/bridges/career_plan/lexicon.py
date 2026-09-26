"""职业规划模块的确定性词表与提取规则。

原词逐字保留：检索锚点由用户说出的岗位词组成，词表只用于**识别**与分类，
不把用户说法替换成另一个岗位。词表里同一族内的 ``synonyms`` 是真正同义的
说法（可以并入同一主样本），``adjacent`` 是相邻岗位（单列建议，绝不混入
主样本，例如目标「Java 后端实习」时的前端与测试岗位）。

岗位识别有两条路径：先认整词（head + tail 组合出的岗位名），再认显式的
岗位框定语（「目标岗位是…」「想找…」）。两条路径都不命中时**不猜**，交给
调用方追问。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 岗位族：同义与相邻
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JobFamily:
    """一个岗位族：同义说法与相邻岗位分开记录。"""

    key: str
    title: str
    synonyms: tuple[str, ...] = ()
    adjacent: tuple[str, ...] = ()
    heads: tuple[str, ...] = ()

    @property
    def all_terms(self) -> tuple[str, ...]:
        return (self.title, *self.synonyms)


#: 岗位族表（覆盖校园招聘常见方向；同义=同一岗位，相邻=不同岗位）。
JOB_FAMILIES: tuple[JobFamily, ...] = (
    JobFamily(
        key="algorithm",
        title="算法工程师",
        synonyms=(
            "算法",
            "算法工程师",
            "算法研发",
            "算法研究员",
            "机器学习工程师",
            "深度学习工程师",
            "AI算法工程师",
        ),
        adjacent=("数据分析师", "数据开发工程师", "后端开发工程师", "数据科学家"),
        heads=("算法", "机器学习", "深度学习", "人工智能", "AI"),
    ),
    JobFamily(
        key="data_analyst",
        title="数据分析师",
        synonyms=("数据分析", "数据分析师", "商业分析", "业务分析", "数据运营", "数据分析员"),
        adjacent=("算法工程师", "数据开发工程师", "数据科学家"),
        heads=("数据分析", "商业分析", "业务分析"),
    ),
    JobFamily(
        key="data_engineer",
        title="数据开发工程师",
        synonyms=(
            "数据开发",
            "数据开发工程师",
            "数据仓库工程师",
            "数仓开发",
            "ETL工程师",
            "大数据开发",
            "数据工程师",
        ),
        adjacent=("数据分析师", "算法工程师", "后端开发工程师"),
        heads=("数据开发", "大数据", "数仓", "ETL"),
    ),
    JobFamily(
        key="backend",
        title="后端开发工程师",
        synonyms=(
            "后端",
            "后端开发",
            "后端工程师",
            "后端开发工程师",
            "服务端",
            "服务端开发",
            "服务端工程师",
            "Java开发",
            "Java工程师",
            "Java后端",
            "Java后端开发",
            "Golang开发",
            "Go开发",
            "Python后端",
            "Python开发",
            "PHP开发",
            "C++开发",
        ),
        adjacent=(
            "前端开发工程师",
            "测试工程师",
            "运维工程师",
            "全栈开发工程师",
            "数据开发工程师",
            "客户端开发工程师",
        ),
        heads=(
            "后端",
            "服务端",
            "Java",
            "Golang",
            "Go",
            "Python",
            "PHP",
            "C++",
            "C#",
            ".NET",
            "Spring",
        ),
    ),
    JobFamily(
        key="frontend",
        title="前端开发工程师",
        synonyms=(
            "前端",
            "前端开发",
            "前端工程师",
            "前端开发工程师",
            "Web前端",
            "Web前端开发",
            "H5开发",
            "大前端",
        ),
        adjacent=("后端开发工程师", "测试工程师", "UI设计师", "全栈开发工程师", "客户端开发工程师"),
        heads=("前端", "Web", "H5", "Vue", "React"),
    ),
    JobFamily(
        key="fullstack",
        title="全栈开发工程师",
        synonyms=("全栈", "全栈开发", "全栈工程师", "全栈开发工程师"),
        adjacent=("前端开发工程师", "后端开发工程师"),
        heads=("全栈",),
    ),
    JobFamily(
        key="mobile",
        title="客户端开发工程师",
        synonyms=(
            "客户端开发",
            "客户端工程师",
            "移动端开发",
            "Android开发",
            "安卓开发",
            "iOS开发",
            "鸿蒙开发",
            "Flutter开发",
        ),
        adjacent=("前端开发工程师", "后端开发工程师", "测试工程师"),
        heads=("客户端", "移动端", "Android", "安卓", "iOS", "鸿蒙", "Flutter"),
    ),
    JobFamily(
        key="qa",
        title="测试工程师",
        synonyms=(
            "测试",
            "测试工程师",
            "软件测试",
            "测试开发",
            "测试开发工程师",
            "自动化测试",
            "测试员",
        ),
        adjacent=("后端开发工程师", "前端开发工程师", "运维工程师"),
        heads=("测试", "QA"),
    ),
    JobFamily(
        key="sre",
        title="运维工程师",
        synonyms=(
            "运维",
            "运维工程师",
            "系统运维",
            "应用运维",
            "SRE",
            "DevOps工程师",
            "云原生工程师",
        ),
        adjacent=("后端开发工程师", "测试工程师", "网络安全工程师"),
        heads=("运维", "SRE", "DevOps", "云原生"),
    ),
    JobFamily(
        key="security",
        title="网络安全工程师",
        synonyms=("网络安全", "网络安全工程师", "安全工程师", "信息安全", "渗透测试", "安全运维"),
        adjacent=("运维工程师", "后端开发工程师"),
        heads=("网络安全", "信息安全", "渗透", "安全"),
    ),
    JobFamily(
        key="embedded",
        title="嵌入式开发工程师",
        synonyms=(
            "嵌入式",
            "嵌入式开发",
            "嵌入式软件工程师",
            "嵌入式工程师",
            "单片机开发",
            "驱动开发",
        ),
        adjacent=("硬件工程师", "自动化工程师", "测试工程师"),
        heads=("嵌入式", "单片机", "驱动", "DSP", "FPGA"),
    ),
    JobFamily(
        key="hardware",
        title="硬件工程师",
        synonyms=("硬件", "硬件工程师", "硬件开发", "电路设计", "PCB设计", "射频工程师"),
        adjacent=("嵌入式开发工程师", "电子工程师", "结构工程师"),
        heads=("硬件", "电路", "PCB", "射频"),
    ),
    JobFamily(
        key="electronics",
        title="电子工程师",
        synonyms=("电子工程师", "电子设计", "模拟电路", "数字电路", "电子研发"),
        adjacent=("通信工程师", "硬件工程师", "嵌入式开发工程师"),
        heads=("电子", "模拟电路", "数字电路"),
    ),
    JobFamily(
        key="telecom",
        title="通信工程师",
        synonyms=("通信工程师", "通信研发", "无线通信", "通信技术"),
        adjacent=("电子工程师", "网络工程师"),
        heads=("通信", "无线"),
    ),
    JobFamily(
        key="network",
        title="网络工程师",
        synonyms=("网络工程师", "网络运维", "网络工程", "网络管理"),
        adjacent=("运维工程师", "通信工程师"),
        heads=("网络工程", "网络运维"),
    ),
    JobFamily(
        key="mechanical",
        title="机械工程师",
        synonyms=("机械", "机械工程师", "机械设计", "机械设计工程师", "机械研发"),
        adjacent=("结构工程师", "工艺工程师", "自动化工程师"),
        heads=("机械",),
    ),
    JobFamily(
        key="structure",
        title="结构工程师",
        synonyms=("结构工程师", "结构设计", "机械结构工程师", "结构设计工程师"),
        adjacent=("机械工程师", "土木工程师", "硬件工程师"),
        heads=("结构",),
    ),
    JobFamily(
        key="electrical",
        title="电气工程师",
        synonyms=("电气", "电气工程师", "电气设计", "电气自动化", "电力工程师"),
        adjacent=("自动化工程师", "硬件工程师"),
        heads=("电气", "电力"),
    ),
    JobFamily(
        key="automation",
        title="自动化工程师",
        synonyms=("自动化", "自动化工程师", "控制工程师", "PLC工程师"),
        adjacent=("电气工程师", "嵌入式开发工程师", "机械工程师"),
        heads=("自动化", "控制工程", "PLC"),
    ),
    JobFamily(
        key="civil",
        title="土木工程师",
        synonyms=("土木", "土木工程师", "土木工程", "施工员", "土建工程师"),
        adjacent=("测绘工程师", "结构工程师"),
        heads=("土木", "施工", "土建"),
    ),
    JobFamily(
        key="surveying",
        title="测绘工程师",
        synonyms=("测绘", "测绘工程师", "测绘工程", "测量员"),
        adjacent=("土木工程师",),
        heads=("测绘", "测量"),
    ),
    JobFamily(
        key="chemical",
        title="化工工程师",
        synonyms=("化工", "化工工程师", "化工工艺", "化学工程师", "化学分析"),
        adjacent=("材料工程师", "工艺工程师", "生物研发"),
        heads=("化工", "化学"),
    ),
    JobFamily(
        key="material",
        title="材料工程师",
        synonyms=("材料", "材料工程师", "材料研发", "材料科学", "金属材料"),
        adjacent=("化工工程师", "机械工程师"),
        heads=("材料", "冶金"),
    ),
    JobFamily(
        key="biology",
        title="生物研发工程师",
        synonyms=("生物研发", "生物技术", "生物工程师", "生物信息", "分子实验员"),
        adjacent=("医药研发", "化工工程师"),
        heads=("生物", "分子"),
    ),
    JobFamily(
        key="pharma",
        title="医药研发员",
        synonyms=("医药研发", "药物研发", "药品研发", "药剂师", "制剂研发"),
        adjacent=("生物研发工程师", "临床研究"),
        heads=("医药", "药物", "药剂", "制剂"),
    ),
    JobFamily(
        key="clinical",
        title="临床研究专员",
        synonyms=("临床研究", "临床监查员", "CRA", "医学事务", "临床协调员"),
        adjacent=("医药研发员", "护理"),
        heads=("临床", "医学事务"),
    ),
    JobFamily(
        key="nursing",
        title="护理",
        synonyms=("护士", "护理", "护理师", "临床护士"),
        adjacent=("临床研究专员", "医师"),
        heads=("护理", "护士"),
    ),
    JobFamily(
        key="physician",
        title="医师",
        synonyms=("医师", "医生", "临床医生", "全科医生"),
        adjacent=("护理", "临床研究专员"),
        heads=("医师", "医生"),
    ),
    JobFamily(
        key="product",
        title="产品经理",
        synonyms=("产品经理", "产品助理", "产品专员", "互联网产品经理", "产品策划"),
        adjacent=("产品运营", "项目经理", "UI设计师", "数据分析师"),
        heads=("产品经理", "产品策划"),
    ),
    JobFamily(
        key="operations",
        title="产品运营",
        synonyms=("产品运营", "运营", "运营专员", "用户运营", "内容运营", "社群运营", "电商运营"),
        adjacent=("产品经理", "市场专员", "新媒体运营"),
        heads=("运营",),
    ),
    JobFamily(
        key="marketing",
        title="市场专员",
        synonyms=("市场专员", "市场营销", "市场推广", "品牌专员"),
        adjacent=("销售", "产品运营"),
        heads=("市场营销", "市场推广", "品牌"),
    ),
    JobFamily(
        key="sales",
        title="销售",
        synonyms=("销售", "销售代表", "销售专员", "客户经理", "大客户销售"),
        adjacent=("市场专员", "售前工程师"),
        heads=("销售", "客户经理", "大客户"),
    ),
    JobFamily(
        key="hr",
        title="人力资源专员",
        synonyms=("人力资源", "人力资源专员", "人事", "人事专员", "招聘专员", "HR"),
        adjacent=("行政专员", "产品运营"),
        heads=("人力资源", "人事", "人力", "招聘", "HR"),
    ),
    JobFamily(
        key="admin",
        title="行政专员",
        synonyms=("行政", "行政专员", "行政助理", "文员"),
        adjacent=("人力资源专员",),
        heads=("行政", "文员"),
    ),
    JobFamily(
        key="accounting",
        title="会计",
        synonyms=("会计", "会计助理", "财务", "财务专员", "出纳", "财务分析"),
        adjacent=("审计", "税务专员"),
        heads=("会计", "财务", "出纳"),
    ),
    JobFamily(
        key="audit",
        title="审计",
        synonyms=("审计", "审计助理", "内审", "内部审计"),
        adjacent=("会计", "税务专员"),
        heads=("审计", "内审"),
    ),
    JobFamily(
        key="tax",
        title="税务专员",
        synonyms=("税务", "税务专员", "税务师"),
        adjacent=("会计", "审计"),
        heads=("税务",),
    ),
    JobFamily(
        key="teacher",
        title="教师",
        synonyms=(
            "教师",
            "老师",
            "中小学教师",
            "语文教师",
            "数学教师",
            "英语教师",
            "物理教师",
            "化学教师",
        ),
        adjacent=("讲师", "教研员", "研究员"),
        heads=("教师", "老师"),
    ),
    JobFamily(
        key="trainer",
        title="讲师",
        synonyms=("讲师", "培训师", "培训讲师", "教研员"),
        adjacent=("教师", "产品运营"),
        heads=("讲师", "培训", "教研"),
    ),
    JobFamily(
        key="researcher",
        title="研究员",
        synonyms=("研究员", "科研助理", "研究助理", "实验室研究员", "研发工程师"),
        adjacent=("教师", "算法工程师", "数据分析师"),
        heads=("研究员", "科研", "研究助理"),
    ),
    JobFamily(
        key="graphic_design",
        title="平面设计师",
        synonyms=("平面设计", "平面设计师", "视觉设计", "视觉设计师", "美工", "品牌设计"),
        adjacent=("UI设计师", "新媒体运营", "产品经理"),
        heads=("平面设计", "视觉设计", "美工"),
    ),
    JobFamily(
        key="ui_design",
        title="UI设计师",
        synonyms=("UI", "UI设计", "UI设计师", "UX设计", "交互设计", "交互设计师", "用户体验设计"),
        adjacent=("平面设计师", "前端开发工程师", "产品经理"),
        heads=("UI", "UX", "交互设计", "用户体验"),
    ),
    JobFamily(
        key="medio_editor",
        title="新媒体运营",
        synonyms=("新媒体运营", "新媒体编辑", "内容编辑", "文案策划", "文案"),
        adjacent=("记者", "产品运营", "平面设计师"),
        heads=("新媒体", "文案", "内容编辑"),
    ),
    JobFamily(
        key="journalist",
        title="记者",
        synonyms=("记者", "采编", "新闻记者"),
        adjacent=("新媒体运营", "编辑"),
        heads=("记者", "采编"),
    ),
    JobFamily(
        key="editor",
        title="编辑",
        synonyms=("编辑", "文字编辑", "图书编辑"),
        adjacent=("新媒体运营", "记者", "翻译"),
        heads=("编辑",),
    ),
    JobFamily(
        key="translator",
        title="翻译",
        synonyms=("翻译", "英语翻译", "笔译", "口译"),
        adjacent=("教师", "编辑"),
        heads=("翻译", "笔译", "口译"),
    ),
    JobFamily(
        key="purchasing",
        title="采购专员",
        synonyms=("采购", "采购专员", "采购工程师"),
        adjacent=("供应链专员", "物流专员"),
        heads=("采购",),
    ),
    JobFamily(
        key="logistics",
        title="物流专员",
        synonyms=("物流", "物流专员", "仓储管理", "供应链管理"),
        adjacent=("采购专员", "供应链专员"),
        heads=("物流", "仓储"),
    ),
    JobFamily(
        key="supply_chain",
        title="供应链专员",
        synonyms=("供应链", "供应链专员", "供应链计划"),
        adjacent=("采购专员", "物流专员"),
        heads=("供应链",),
    ),
    JobFamily(
        key="legal",
        title="法务专员",
        synonyms=("法务", "法务专员", "法务助理", "合规专员"),
        adjacent=("行政专员",),
        heads=("法务", "合规"),
    ),
    JobFamily(
        key="project_manager",
        title="项目经理",
        synonyms=("项目经理", "项目管理", "交付经理"),
        adjacent=("产品经理", "产品运营"),
        heads=("项目经理", "项目管理", "交付"),
    ),
    JobFamily(
        key="support",
        title="技术支持工程师",
        synonyms=("技术支持", "技术支持工程师", "实施工程师", "售后工程师", "售后技术支持"),
        adjacent=("售前工程师", "运维工程师", "销售"),
        heads=("技术支持", "实施", "售后"),
    ),
    JobFamily(
        key="presales",
        title="售前工程师",
        synonyms=("售前", "售前工程师", "解决方案工程师"),
        adjacent=("技术支持工程师", "销售"),
        heads=("售前", "解决方案"),
    ),
    JobFamily(
        key="game",
        title="游戏开发工程师",
        synonyms=("游戏开发", "游戏客户端开发", "游戏服务端开发", "Unity开发", "UE开发"),
        adjacent=("客户端开发工程师", "前端开发工程师", "图形工程师"),
        heads=("游戏", "Unity", "UE", "图形"),
    ),
    JobFamily(
        key="craft",
        title="工艺工程师",
        synonyms=("工艺", "工艺工程师", "工艺设计", "制程工程师"),
        adjacent=("机械工程师", "生产管理", "质量管理"),
        heads=("工艺", "制程"),
    ),
    JobFamily(
        key="production",
        title="生产管理",
        synonyms=("生产管理", "生产计划", "车间管理", "生产主管"),
        adjacent=("工艺工程师", "质量管理"),
        heads=("生产", "车间"),
    ),
    JobFamily(
        key="quality",
        title="质量管理",
        synonyms=("质量管理", "质量工程师", "质检", "QC"),
        adjacent=("测试工程师", "工艺工程师"),
        heads=("质量管理", "质检", "品控"),
    ),
)

#: 词条 → 岗位族（同义词与标题都指向同一族；同名冲突以先出现者为准）。
_TERM_TO_FAMILY: dict[str, JobFamily] = {}
for _family in JOB_FAMILIES:
    for _term in _family.all_terms:
        _TERM_TO_FAMILY.setdefault(_term.casefold(), _family)

#: head 词 → 同 head 的岗位族（目标岗位不在表内时据此找相邻岗位）。
_HEAD_TO_FAMILIES: dict[str, tuple[JobFamily, ...]] = {}
for _family in JOB_FAMILIES:
    for _head in _family.heads:
        _HEAD_TO_FAMILIES.setdefault(_head.casefold(), ())
        _HEAD_TO_FAMILIES[_head.casefold()] = (*_HEAD_TO_FAMILIES[_head.casefold()], _family)

#: 岗位名尾缀（整词识别的右界）。
JOB_TAILS: tuple[str, ...] = (
    "工程师",
    "开发工程师",
    "开发",
    "研发",
    "实习",
    "实习生",
    "助理",
    "专员",
    "主管",
    "经理",
    "总监",
    "设计师",
    "分析师",
    "研究员",
    "架构师",
    "测试",
    "运维",
    "顾问",
    "讲师",
    "教师",
    "技师",
    "技术员",
    "操作员",
    "实验员",
    "销售",
    "运营",
    "会计",
    "出纳",
    "审核",
    "策划",
    "编导",
    "翻译",
    "记者",
    "编辑",
    "医师",
    "药师",
    "护士",
    "施工员",
    "测量员",
    "文员",
    "管培生",
    "管理培训生",
)

#: 岗位名 head 词（整词识别的左界）。
JOB_HEADS: tuple[str, ...] = tuple(
    dict.fromkeys(head for family in JOB_FAMILIES for head in family.heads)
)

#: 单独出现即含歧义、需要先问一句的 head（跨多个岗位族，且用户没给限定词）。
AMBIGUOUS_HEADS: tuple[str, ...] = ("数据", "安全", "售后", "软件", "研发")

#: 显式岗位框定语（其后紧跟岗位名）。
_FRAME = re.compile(
    r"(?:目标岗位|目标职位|岗位|职位|应聘|求职|想找|想应聘|想做|要投|打算投|准备找|准备投|"
    r"找一份|找|投递|面试)\s*(?:是|为|：|:|做|一份)?\s*"
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9+#.]{2,24})"
)

#: 岗位名两侧的装饰与噪声字（框定语后可能粘住的助词与阶段词）。
_TITLE_NOISE = " \t的一了份个种岗位职位工作要求实习校招社招"

#: 最多保留的岗位词数（超出部分只作参考，不改变检索锚点）。
MAX_JOB_TERMS = 3

#: 最多保留的城市数。
MAX_CITIES = 3

#: 最多记录的技能关键词数。
MAX_SKILLS = 30


def _clean_title_edges(value: str) -> str:
    text = value.strip()
    while text and text[0] in _TITLE_NOISE:
        text = text[1:]
    while text and text[-1] in _TITLE_NOISE:
        text = text[:-1]
    return text.strip()


def family_for(term: str) -> JobFamily | None:
    """把岗位说法解析到岗位族；不在表内返回 None（不猜近似岗位）。"""
    text = normalize_for_match(term)
    if not text:
        return None
    exact = _TERM_TO_FAMILY.get(text)
    if exact is not None:
        return exact
    # 整词识别出的说法可能带前后缀（如「Java 后端开发实习」），按包含关系归族，
    # 但只在唯一命中时归族：命中多个族说明本身跨族，宁可不归类。
    matches = {
        family.key
        for family_term, family in _TERM_TO_FAMILY.items()
        if len(family_term) >= 2 and (family_term in text or text in family_term)
    }
    if len(matches) == 1:
        key = next(iter(matches))
        for family in JOB_FAMILIES:
            if family.key == key:
                return family
    return None


def _title_pattern() -> re.Pattern[str]:
    heads = "|".join(re.escape(head) for head in sorted(JOB_HEADS, key=len, reverse=True))
    tails = "|".join(re.escape(tail) for tail in sorted(JOB_TAILS, key=len, reverse=True))
    # head 与 tail 之间只允许很短的中文限定词与分隔空白（如「Java 后端开发」
    # 「高级前端工程师」），避免把两个不相干的词粘成一个岗位名。
    return re.compile(rf"(?:{heads})[\u4e00-\u9fff\s/\-]{{0,4}}?(?:{tails})")


_TITLE_RE = _title_pattern()

#: 明确的岗位名（head + tail 组合）出现位置。
_SPAN_LIMIT = 24


def detect_job_titles(text: str) -> list[str]:
    """按 head+tail 组合识别岗位名原话（按出现顺序去重）。"""
    found: list[str] = []
    seen: set[str] = set()
    for match in _TITLE_RE.finditer(text):
        title = match.group(0)[:_SPAN_LIMIT]
        if title in seen:
            continue
        seen.add(title)
        found.append(title)
    return found


def detect_framed_titles(text: str) -> list[str]:
    """按显式岗位框定语识别岗位名（「目标岗位是…」「想找…的实习」）。"""
    found: list[str] = []
    seen: set[str] = set()
    for match in _FRAME.finditer(text):
        title = _clean_title_edges(match.group("title"))
        if not title or title in seen:
            continue
        # 框定语后可能跟着别的成分（「想找算法工程师的工作」），再收敛一次：
        # 命中整词形态时只取整词，否则保留原话。
        spans = detect_job_titles(title)
        candidate = spans[0] if spans else title
        if not _is_job_like(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        found.append(candidate)
    return found


def _is_job_like(candidate: str) -> bool:
    """框定语后面的成分是否真的像岗位名。

    「想找 Java 后端实习」这类句子里，框定语后面会先粘住一个裸技术词
    （``Java``）——它不是岗位名，收进来会让检索锚点跑偏。只接受三种形态：
    完整岗位词（head+tail）、词表里逐字登记的说法，以及跨族、需要追问的
    裸 head（如「数据」）。
    """
    if len(candidate) < 2:
        return False
    if detect_job_titles(candidate):
        return True
    if candidate.casefold() in _TERM_TO_FAMILY:
        return True
    return candidate in AMBIGUOUS_HEADS


def extract_job_terms(text: str) -> list[str]:
    """提取检索用岗位锚点：先框定语，再整词，按出现顺序去重并限量。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in (*detect_framed_titles(text), *detect_job_titles(text)):
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
        if len(ordered) >= MAX_JOB_TERMS:
            break
    return ordered


def is_job_intent_ambiguous(terms: list[str]) -> bool:
    """岗位意图是否含糊到必须先问一句。

    只有一个裸 head（如「数据」）而跨多个岗位族时算含糊；带上限定词
    （「数据分析师」）或已有多个锚点时不算。
    """
    if not terms:
        return True
    if len(terms) > 1:
        return False
    term = terms[0]
    if family_for(term) is not None:
        return False
    return any(term == head or term.casefold() == head for head in AMBIGUOUS_HEADS)


def synonym_terms(terms: list[str]) -> list[str]:
    """目标岗位的真正同义说法（可以并入主样本）。"""
    collected: list[str] = []
    for term in terms:
        family = family_for(term)
        candidates = family.all_terms if family is not None else (term,)
        for candidate in candidates:
            if candidate not in collected:
                collected.append(candidate)
    return collected


def adjacent_terms(terms: list[str]) -> list[str]:
    """相邻岗位说法（单列建议，绝不混入主样本）。"""
    collected: list[str] = []
    for term in terms:
        family = family_for(term)
        if family is not None:
            candidates = family.adjacent
        else:
            # 目标岗位不在表内时按 head 找同 head 的其他族。
            shared = [
                other
                for head, families in _HEAD_TO_FAMILIES.items()
                if head in term.casefold()
                for other in families
            ]
            candidates = tuple(
                title for other in shared for title in other.all_terms
                if other.all_terms != tuple(terms)
            )
        for candidate in candidates:
            if candidate not in collected and candidate not in terms:
                collected.append(candidate)
    # 相邻词里若包含目标岗位本身的说法，去掉（避免自我剔除）。
    target = set(terms)
    return [item for item in collected if item not in target]


# --------------------------------------------------------------------------
# 城市
# --------------------------------------------------------------------------

#: 城市词表（直辖市、省会与常见招聘城市）。
CITY_TERMS: tuple[str, ...] = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "重庆", "武汉",
    "西安", "天津", "长沙", "郑州", "青岛", "宁波", "东莞", "无锡", "佛山", "合肥",
    "大连", "福州", "厦门", "济南", "温州", "南宁", "昆明", "贵阳", "南昌", "太原",
    "石家庄", "哈尔滨", "长春", "沈阳", "兰州", "银川", "西宁", "乌鲁木齐", "呼和浩特",
    "拉萨", "海口", "三亚", "珠海", "中山", "惠州", "泉州", "常州", "南通", "徐州",
    "嘉兴", "绍兴", "台州", "金华", "烟台", "潍坊", "淄博", "洛阳", "襄阳", "宜昌",
    "株洲", "湘潭", "衡阳", "赣州", "九江", "上饶", "宜春", "吉安", "芜湖", "蚌埠",
    "香港", "澳门", "台北",
)

#: 城市限定语的框定词（「期望城市是…」「在…工作」）。
_CITY_FRAME = re.compile(
    r"(?:期望城市|意向城市|目标城市|城市|地点|工作地|工作城市|base地|所在地)"
    r"\s*(?:是|为|：|:)?\s*(?P<cities>[\u4e00-\u9fff、,，/和与\s]{2,40})"
)


def detect_cities(text: str) -> list[str]:
    """提取城市名（先看显式城市框定语，再全文扫描），按**原话出现顺序**去重限量。"""
    framed: list[str] = []
    for match in _CITY_FRAME.finditer(text):
        for city in CITY_TERMS:
            if city in match.group("cities") and city not in framed:
                framed.append(city)
    candidates = framed or [city for city in CITY_TERMS if city in text]
    # 按用户说出的先后排序：「城市南昌和深圳」要读作南昌在前。
    ordered = sorted(candidates, key=lambda city: text.find(city))
    return ordered[:MAX_CITIES]


# --------------------------------------------------------------------------
# 阶段与经验
# --------------------------------------------------------------------------

#: 毕业阶段词（原话保留用）。
STAGE_TERMS: tuple[str, ...] = (
    "应届生", "应届", "往届", "在校生", "实习生", "实习", "校招", "秋招", "春招",
    "社招", "管培生", "大三", "大四", "大二", "研究生", "硕士", "本科", "博士",
    "研一", "研二", "研三", "即将毕业", "毕业", "全职", "兼职",
)

#: 届别（2026届 / 26届）识别。
_GRADUATION_YEAR = re.compile(r"(?P<year>20\d{2}|\d{2})\s*届")

#: 经验要求词表（岗位卡里的原始说法）。
EXPERIENCE_TERMS: tuple[str, ...] = (
    "经验不限", "不限经验", "应届生", "在校生", "无需经验", "无经验",
    "1年以下", "1年以内", "1-3年", "1~3年", "3-5年", "3~5年", "5-10年", "5~10年",
    "10年以上", "一年以上", "两年以上", "三年以上", "五年以上",
)


def detect_stage(text: str) -> str | None:
    """提取毕业阶段原话（保留用户说法，取最先出现且最长的一个）。"""
    positions = [(text.index(term), term) for term in STAGE_TERMS if term in text]
    if not positions:
        return None
    # 同一位置命中「应届」与「应届生」时取更长的那个，保留用户完整的说法。
    return min(positions, key=lambda item: (item[0], -len(item[1])))[1]


def detect_graduation_year(text: str) -> int | None:
    """提取届别年份；两位写法补全成 2000 年代。"""
    match = _GRADUATION_YEAR.search(text)
    if match is None:
        return None
    raw = match.group("year")
    year = int(raw)
    return year + 2000 if len(raw) == 2 else year


def detect_experience(text: str) -> str | None:
    """提取岗位卡上的经验要求原话。"""
    for term in EXPERIENCE_TERMS:
        if term in text:
            return term
    return None


# --------------------------------------------------------------------------
# 技能关键词
# --------------------------------------------------------------------------

#: 技能词表（编程语言、框架、数据与工具、业务与软技能）。
SKILL_TERMS: tuple[str, ...] = (
    # 语言与基础
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C语言", "C#", "Go", "Golang",
    "Rust", "PHP", "Kotlin", "Swift", "Scala", "R", "MATLAB", "SQL", "Shell", "HTML",
    "CSS", "Verilog", "VHDL",
    # 框架与平台
    "Spring", "Spring Boot", "Spring Cloud", "MyBatis", "Django", "Flask", "FastAPI",
    "Vue", "React", "Angular", "Node.js", "jQuery", "PyTorch", "TensorFlow", "PaddlePaddle",
    "Keras", "Scikit-learn", "Spark", "Hadoop", "Flink", "Hive", "Kafka", "Redis",
    "MySQL", "PostgreSQL", "Oracle", "MongoDB", "Elasticsearch", "ClickHouse", "SQLite",
    "Linux", "Docker", "Kubernetes", "K8s", "Jenkins", "Git", "Maven", "Gradle", "Nginx",
    "AWS", "阿里云", "腾讯云", "华为云", "Azure", "GitLab",
    # 方法与概念
    "数据结构", "算法", "机器学习", "深度学习", "神经网络", "大模型", "LLM", "NLP",
    "自然语言处理", "计算机视觉", "推荐系统", "搜索", "数据挖掘", "特征工程", "数据建模",
    "数据分析", "数据可视化", "统计学", "概率论", "运筹优化", "自动化测试", "单元测试",
    "性能优化", "高并发", "分布式", "微服务", "网络编程", "并发编程", "设计模式",
    "嵌入式", "PLC", "单片机", "AutoCAD", "SolidWorks", "CATIA", "ANSYS", "有限元",
    "PCB", "数字电路", "模拟电路", "信号处理", "通信协议", "TCP/IP", "HTTP",
    "网络安全", "渗透测试", "加密", "运维", "监控",
    # 业务与软技能
    "产品设计", "需求分析", "原型设计", "Axure", "Figma", "Sketch", "Photoshop", "PS",
    "Illustrator", "Premiere", "AfterEffects", "Pr", "剪映", "Excel", "PPT", "Word",
    "PowerBI", "Tableau", "项目管理", "沟通能力", "团队协作", "英语", "CET-6", "CET-4",
    "文字功底", "文案", "市场分析", "财务分析", "会计准则", "审计", "税务",
)

#: 技能词在正文里的匹配顺序：长词优先，避免「C」吃掉「C++」。
_SKILLS_SORTED: tuple[str, ...] = tuple(sorted(SKILL_TERMS, key=len, reverse=True))


def detect_skills(text: str) -> list[str]:
    """从岗位要求原文里提取技能关键词（只认词表内的词，不生成新词）。"""
    found: list[str] = []
    for skill in _SKILLS_SORTED:
        if _skill_present(text, skill):
            found.append(skill)
        if len(found) >= MAX_SKILLS:
            break
    return sorted(found)


def _skill_present(text: str, skill: str) -> bool:
    """技能词匹配：纯字母词按词边界，含中文或符号的词按子串。"""
    if skill.isascii() and skill.isalpha():
        return re.search(rf"(?<![A-Za-z]){re.escape(skill)}(?![A-Za-z])", text, re.I) is not None
    return skill in text


# --------------------------------------------------------------------------
# 来源与页面判定
# --------------------------------------------------------------------------

#: 招聘来源标识（检索计划与分析都按这三类分开统计）。
SOURCE_BOSS = "boss"
SOURCE_CORPORATE = "corporate"
SOURCE_CAMPUS = "campus"

#: 来源中文名（正文与前端共用一份）。
SOURCE_LABELS: dict[str, str] = {
    SOURCE_BOSS: "公开招聘职位（BOSS 等岗位页）",
    SOURCE_CORPORATE: "企业招聘页",
    SOURCE_CAMPUS: "校招页",
}

#: 公开招聘站点主机（岗位页）。
PUBLIC_JOB_HOSTS: tuple[str, ...] = (
    "zhipin.com",
    "zhaopin.com",
    "51job.com",
    "liepin.com",
    "lagou.com",
    "jobs.51job.com",
    "nowcoder.com",
    "iguopin.com",
)

#: 校招站点／路径特征（应届生求职网等，以及企业校招专题）。
CAMPUS_HOSTS: tuple[str, ...] = (
    "yingjiesheng.com",
    "xiaoyuanzhaopin.com",
    "campus.51job.com",
    "nowcoder.com/job",
)

#: 校招路径特征词（企业官网的校招栏目）。
CAMPUS_PATH_HINTS: tuple[str, ...] = (
    "campus",
    "xiaozhao",
    "xiaoyuan",
    "campusrecruit",
    "graduate",
    "校招",
    "校园招聘",
)

#: 企业招聘页路径特征词（公司官网招聘栏目）。
CORPORATE_PATH_HINTS: tuple[str, ...] = (
    "career",
    "careers",
    "job",
    "jobs",
    "recruit",
    "zhaopin",
    "join",
    "hr",
    "talent",
    "招聘",
)


def _host_of(url: str) -> str:
    match = re.match(r"^https?://(?P<host>[^/?#]+)", url.strip(), re.I)
    return (match.group("host") if match is not None else "").casefold()


def is_public_job_url(url: str) -> bool:
    """是否公开招聘岗位页（岗位检索的第一类来源）。"""
    host = _host_of(url)
    return any(host == item or host.endswith(f".{item}") for item in PUBLIC_JOB_HOSTS)


def is_campus_url(url: str) -> bool:
    """是否校招页（校招站点或企业校招栏目）。"""
    lowered = url.strip().casefold()
    host = _host_of(url)
    if any(item in host for item in CAMPUS_HOSTS) or "campus." in host:
        return True
    return any(hint in lowered for hint in CAMPUS_PATH_HINTS)


def is_corporate_url(url: str) -> bool:
    """是否企业招聘页（既不是公开招聘站，也不是校招页的企业官网栏目）。"""
    if is_public_job_url(url) or is_campus_url(url):
        return False
    lowered = url.strip().casefold()
    return any(hint in lowered for hint in CORPORATE_PATH_HINTS)


def classify_source(url: str) -> str | None:
    """把链接分到三类来源；都不像招聘页时返回 None。"""
    if is_campus_url(url):
        return SOURCE_CAMPUS
    if is_public_job_url(url):
        return SOURCE_BOSS
    if is_corporate_url(url):
        return SOURCE_CORPORATE
    return None


#: 岗位名在页面标题里的常见装饰（公司名与城市前缀）。清理只影响**匹配**，
#: 展示始终用页面原文。
def normalize_for_match(value: str) -> str:
    """匹配用的规范化：去空格、统一大小写、全角括号转半角。"""
    text = value.strip().casefold()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


@dataclass(frozen=True)
class TitleMatch:
    """岗位名匹配结果：命中的目标说法与命中的相邻说法。"""

    matched: bool
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    adjacent_hits: tuple[str, ...] = field(default_factory=tuple)


def match_job_title(
    title: str,
    *,
    target_terms: tuple[str, ...],
    adjacent: tuple[str, ...],
) -> TitleMatch:
    """判断页面岗位名是否就是目标岗位（只认目标说法与真正同义名）。

    相邻岗位单独记下，绝不当成目标岗位：目标「Java 后端实习」时，页面上的
    「前端开发工程师」「测试工程师」必须落到 ``adjacent_hits``。
    """
    normalized = normalize_for_match(title)
    if not normalized:
        return TitleMatch(matched=False)
    matched = tuple(
        term for term in target_terms if term and normalize_for_match(term) in normalized
    )
    if matched:
        # 命中的目标说法里若同时含相邻岗位说法，仍以目标说法为准，但把相邻
        # 命中一并记下（例如「算法工程师（数据分析方向）」）。
        adjacent_hits = tuple(
            term
            for term in adjacent
            if term and normalize_for_match(term) in normalized
        )
        return TitleMatch(matched=True, matched_terms=matched, adjacent_hits=adjacent_hits)
    adjacent_hits = tuple(
        term for term in adjacent if term and normalize_for_match(term) in normalized
    )
    return TitleMatch(matched=False, matched_terms=(), adjacent_hits=adjacent_hits)
