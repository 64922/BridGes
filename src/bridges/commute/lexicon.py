"""校园通勤词表：方式别名、校内地点别名与课间时间点（V2 Issue 12）。

三张表的定位不同，不能互相顶替：

1. **方式别名**是用户措辞到高德三种能力（步行／自行车／电动车）的固定映射；
   同一句里并列出现多种方式时进入澄清，不替用户选一种。
2. **校内地点别名**只是**检索词模板**：命中的别名用于构造高德 POI 检索词，
   模块从不据此断言某个建筑是否存在，也不内置信箱坐标——地点是否存在、
   坐标是多少，全部由高德 POI 返回决定，取不到就请用户补充楼名或入口。
3. **课间时间点**是固定的八个时间（``docs/v2/workflows.md`` 第 3 节），
   ``route.buffer`` 按 ``Asia/Shanghai`` 判断前后十分钟窗口并加五分钟规则缓冲；
   这是规则估计，绝不是实时人流数据。
"""

from __future__ import annotations

from dataclasses import dataclass

from bridges.commute.contracts import CommuteMode

#: 校内范围判定词（POI 名称或地址命中才算确认属于本校）。
CAMPUS_KEYWORD = "华东交通大学"

#: 校内范围的其他写法（高德 POI 名称偶用简称）。
CAMPUS_TOKENS: tuple[str, ...] = (CAMPUS_KEYWORD, "华东交大", "华交大")

#: POI 检索的城市限定（华东交通大学南昌校区）。
PLACE_CITY = "南昌"

#: 方式别名 → 方式。``骑车`` 归入自行车、``电动车/电瓶车`` 归入电动车是固定
#: 归一化，不是对用户意图的猜测；投影同时保留 ``mode_phrase`` 原话，用户可在
#: 下一条消息纠正。
MODE_ALIASES: dict[str, tuple[CommuteMode, ...]] = {
    "步行": (CommuteMode.WALKING,),
    "走路": (CommuteMode.WALKING,),
    "徒步": (CommuteMode.WALKING,),
    "走过去": (CommuteMode.WALKING,),
    "自行车": (CommuteMode.BICYCLING,),
    "单车": (CommuteMode.BICYCLING,),
    "共享单车": (CommuteMode.BICYCLING,),
    "骑车": (CommuteMode.BICYCLING,),
    "骑行": (CommuteMode.BICYCLING,),
    "脚踏车": (CommuteMode.BICYCLING,),
    "骑单车": (CommuteMode.BICYCLING,),
    "骑自行车": (CommuteMode.BICYCLING,),
    "电动车": (CommuteMode.ELECTROBIKE,),
    "电瓶车": (CommuteMode.ELECTROBIKE,),
    "电动自行车": (CommuteMode.ELECTROBIKE,),
    "电驴": (CommuteMode.ELECTROBIKE,),
    "小电驴": (CommuteMode.ELECTROBIKE,),
    "骑电瓶车": (CommuteMode.ELECTROBIKE,),
    "骑电动车": (CommuteMode.ELECTROBIKE,),
}

#: 别名按长度降序（长别名优先匹配，避免「骑车」抢先命中「骑电动车」）。
MODE_ALIAS_ORDER: tuple[tuple[str, CommuteMode], ...] = tuple(
    sorted(
        ((alias, mode) for alias, modes in MODE_ALIASES.items() for mode in modes),
        key=lambda item: (-len(item[0]), item[0]),
    )
)


@dataclass(frozen=True)
class CampusAlias:
    """一个校内地点别名的检索词模板（检索词由校名 + ``poi_keyword`` 组成）。"""

    key: str
    aliases: tuple[str, ...]
    poi_keyword: str
    note: str


#: 校内地点别名表。别名只用于**构造检索词**：模块不据此断言 POI 存在性，
#: 也不内置坐标；高德返回多个校内候选时列出候选请用户选择。
CAMPUS_ALIASES: tuple[CampusAlias, ...] = (
    CampusAlias(
        "gate_generic", ("校门", "校门口", "大门", "正门", "门卫"), "校门", "校门（可能多个）"
    ),
    CampusAlias("gate_south", ("南门", "南校门", "南大门", "南区大门"), "南门", "南门"),
    CampusAlias("gate_north", ("北门", "北校门", "北大门", "北区大门"), "北门", "北门"),
    CampusAlias("gate_east", ("东门", "东校门", "东大门"), "东门", "东门"),
    CampusAlias("gate_west", ("西门", "西校门", "西大门"), "西门", "西门"),
    CampusAlias("library", ("图书馆", "图书室", "阅览室"), "图书馆", "图书馆"),
    CampusAlias("canteen", ("食堂", "饭堂", "餐厅", "饭馆"), "食堂", "食堂（可能多个）"),
    CampusAlias("dormitory", ("宿舍", "宿舍楼", "寝室", "公寓"), "宿舍", "宿舍楼（可能多个）"),
    CampusAlias("teaching_building", ("教学楼", "教室", "上课楼"), "教学楼", "教学楼"),
    CampusAlias("laboratory", ("实验楼", "实验室", "实验中心"), "实验楼", "实验楼"),
    CampusAlias("stadium", ("体育场", "操场", "田径场", "跑道"), "体育场", "体育场"),
    CampusAlias("gymnasium", ("体育馆", "球馆"), "体育馆", "体育馆"),
    CampusAlias("hospital", ("校医院", "医务室", "诊所"), "校医院", "校医院"),
    CampusAlias(
        "administration", ("行政楼", "办公楼", "教务处", "办公楼群"), "办公楼", "行政／办公楼"
    ),
    CampusAlias("south_zone", ("南区", "南院", "南校区"), "南区", "南区"),
    CampusAlias("north_zone", ("北区", "北院", "北校区"), "北区", "北区"),
)

#: 别名按长度降序，长别名优先（「南门」优先于「门」类泛称）。
CAMPUS_ALIAS_ORDER: tuple[tuple[str, CampusAlias], ...] = tuple(
    sorted(
        ((alias, item) for item in CAMPUS_ALIASES for alias in item.aliases),
        key=lambda pair: (-len(pair[0]), pair[0]),
    )
)

#: 无法定位的指代（``我这里`` 等）：只追问具体楼名或入口，绝不猜坐标。
UNLOCATABLE_MARKERS: tuple[str, ...] = (
    "我现在的位置",
    "我当前位置",
    "我现在所在的位置",
    "我所在的位置",
    "我站的位置",
    "我当前的位置",
    "我的位置",
    "我在的地方",
    "我这里",
    "我这儿",
    "我这",
    "当前位置",
    "现在的位置",
    "这里",
    "这儿",
    "那里",
    "那边",
    "这边",
    "那儿",
)

#: 地点短语中要剥离的意图词／连接词（解析用，不改变用户原话的记录）。
PLACE_STOPWORDS: tuple[str, ...] = (
    "怎么走过去",
    "怎么过去",
    "怎么走",
    "怎么去",
    "怎么到达",
    "如何到达",
    "如何走",
    "要多久",
    "需要多久",
    "多久能到",
    "多久可以到",
    "多长时间",
    "多少时间",
    "几分钟",
    "远不远",
    "近不近",
    "方便吗",
    "出发",
    "开始",
    "怎么",
    "如何",
    "怎样",
    "咋",
    "一下",
    "的路线",
    "路线",
    "通勤",
    "帮我",
    "请问",
    "我想",
    "打算",
    "规划",
    "谢谢",
    "呢",
    "吗",
    "啊",
)

#: 地点短语的连接词（正则无法覆盖的散装写法）。
PLACE_CONNECTORS: tuple[str, ...] = ("走到", "骑到", "前往", "从", "到", "去", "往", "在")

#: 八个课间时间点（``Asia/Shanghai``），格式 HH:MM。
BREAK_TIMES: tuple[str, ...] = (
    "07:50",
    "09:30",
    "10:00",
    "12:00",
    "14:20",
    "16:00",
    "16:30",
    "18:10",
)

#: 课间前后窗口（分钟，含边界）。
BREAK_WINDOW_MINUTES = 10

#: 命中窗口时在高德耗时之外增加的规则缓冲（分钟）。
BREAK_BUFFER_MINUTES = 5

#: 课间判定的固定时区（八点与窗口都按此解释）。
CAMPUS_TIMEZONE = "Asia/Shanghai"

#: 明确说明这不是实时人流数据（随每次命中展示）。
BREAK_RULE_NOTE = (
    "课间高峰按规则估计（抵达时间落在课间点前后 10 分钟内），不是实时人流数据。"
)

#: 普通聊天建议启动通勤模块的信号词：既要有出行意图，也要有地点线索。
COMMUTE_INTENT_HINTS: tuple[str, ...] = (
    "怎么走",
    "怎么去",
    "怎么过去",
    "怎么到达",
    "多久能到",
    "多久到",
    "要多久",
    "多长时间能到",
    "路线",
    "通勤",
    "走过去",
    "骑车去",
    "骑电动车",
    "骑车过去",
)

#: 校内线索词（判断这一句是否在说校内出行；别名部分直接取自别名表，
#: 避免两处各写一张表而走样，另有几个不属于任何地点的范围词）。
CAMPUS_HINTS: tuple[str, ...] = (
    "校区",
    "校内",
    "校园",
    *(alias for item in CAMPUS_ALIASES for alias in item.aliases),
)

#: 指代含糊时不给一键建议（无法据此启动一次可核验的通勤）。
VAGUE_SIGNALS: tuple[str, ...] = (
    "随便",
    "都行",
    "不知道",
    "看看有什么",
    "哪里都行",
)
