"""校园通勤模块的公开合同（V2 Issue 12）。

编排合同（``.scratch/bridges-v2/issues/12-campus-commute.md``）与
``docs/v2/workflows.md`` 第 3 节要求：地点先经校内别名和真实 POI 解析、候选
冲突时持久化等待用户选择；路线节点保存**对应方式**的原始距离、基础耗时、
路径点和步骤；核验失败则停止生成地图线。本模块把这些要求固化为投影类型：

- :class:`CommuteMode`：三种方式各自对应高德独立的路线能力，不互相顶替；
- :class:`CommutePlace`：解析后的地点（坐标只来自高德 POI，绝不臆造）；
- :class:`CommuteRouteStep`：文字路段（指令、道路名、距离）；
- :class:`CommuteBreakBuffer`：课间规则缓冲（``Asia/Shanghai``，明示非实时人流）；
- :class:`CommuteRouteProjection`：随助手消息持久化的完整状态。

对外（API/前端）只暴露这一份投影：用户既能看到实际使用的地点与方式，也能
看到路径点缺失、楼门吸附过远、校外被拒等真实局限。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.modules import ModuleQueryRecord, ModuleWaitState

#: 高德坐标串分隔符（``"lng,lat"``，经度在前，纬度在后）。
COORDINATE_SEPARATOR = ","


class CommuteRouteStatus(StrEnum):
    """通勤模块的用户可见状态（同一消息内如实显示）。"""

    CLARIFICATION = "clarification"
    SUCCESS = "success"
    #: 路线服务返回成功但未取得可绘制的路径点：只展示已证实的信息与局限。
    UNVERIFIED = "unverified"
    ERROR = "error"
    STOPPED = "stopped"


class CommuteMode(StrEnum):
    """出行方式（仅步行、自行车、电动车；三者是高德各自独立的能力）。"""

    WALKING = "walking"
    BICYCLING = "bicycling"
    ELECTROBIKE = "electrobike"


class CommutePlaceRole(StrEnum):
    """地点在路线中的角色（澄清问题按其区分「起点/终点」）。"""

    ORIGIN = "origin"
    DESTINATION = "destination"


#: 方式 → 中文标签（正文、卡片与澄清问题共用一份，避免两处各写一张表）。
MODE_LABELS: dict[str, str] = {
    CommuteMode.WALKING: "步行",
    CommuteMode.BICYCLING: "自行车",
    CommuteMode.ELECTROBIKE: "电动车",
}

#: 方式 → 高德路线规划 2.0 接口路径（每种方式只用自己的结果，不拿别的顶替）。
MODE_ENDPOINTS: dict[str, str] = {
    CommuteMode.WALKING: "/v5/direction/walking",
    CommuteMode.BICYCLING: "/v5/direction/bicycling",
    CommuteMode.ELECTROBIKE: "/v5/direction/electrobike",
}

#: 角色 → 中文标签。
PLACE_ROLE_LABELS: dict[str, str] = {
    CommutePlaceRole.ORIGIN: "起点",
    CommutePlaceRole.DESTINATION: "终点",
}

#: 澄清缺失项分类（等待状态 ``context`` 的稳定字段值）。
MISSING_ORIGIN = "origin"
MISSING_DESTINATION = "destination"
MISSING_MODE = "mode"
#: 无法定位「我这里」等指代：只追问，不猜坐标。
MISSING_ORIGIN_UNLOCATABLE = "origin_unlocatable"
MISSING_DESTINATION_UNLOCATABLE = "destination_unlocatable"
#: 校内 POI 有多个候选：列出候选请用户选择。
MISSING_ORIGIN_CHOICE = "origin_choice"
MISSING_DESTINATION_CHOICE = "destination_choice"


class CommutePlaceCandidate(BaseModel):
    """一个真实取得的 POI 候选（澄清候选冲突时逐项列出）。"""

    name: str = Field(description="高德返回的 POI 名称。")
    location: str | None = Field(default=None, description="高德返回的坐标串 lng,lat。")
    address: str | None = Field(default=None, description="高德返回的地址。")
    poi_id: str | None = Field(default=None, description="高德 POI 标识。")
    district: str | None = Field(default=None, description="高德返回的区县名。")
    campus: bool = Field(default=False, description="名称或地址是否确认指向华东交通大学。")


class CommutePlace(BaseModel):
    """解析后的地点：坐标只来自高德 POI 检索结果。"""

    role: CommutePlaceRole = Field(description="起点或终点。")
    original_phrase: str = Field(description="用户原话中的地点写法（逐字保留）。")
    query: str = Field(
        description="实际发送给高德的最小检索词；沿用上一轮候选时为产生该候选的检索词。"
    )
    name: str = Field(description="高德返回的 POI 名称。")
    location: str = Field(description="高德返回的坐标串 lng,lat（绝不臆造）。")
    address: str | None = Field(default=None, description="高德返回的地址。")
    poi_id: str | None = Field(default=None, description="高德 POI 标识。")
    district: str | None = Field(default=None, description="高德返回的区县名。")
    campus_verified: bool = Field(
        default=False, description="是否确认属于华东交通大学校内或校门。"
    )
    match_basis: str = Field(description="匹配与范围判定依据（命中什么、核对了什么）。")
    unverified: list[str] = Field(
        default_factory=list, description="本地点未核实项（例如未实测楼门可通行）。"
    )


class CommuteRouteStep(BaseModel):
    """一个文字路段（高德返回的原始指令、道路名与路段距离）。"""

    index: int = Field(ge=1, description="路段序号（从 1 开始，按高德返回顺序）。")
    instruction: str = Field(description="高德返回的行走指令。")
    road_name: str | None = Field(default=None, description="高德返回的道路名。")
    distance_m: int | None = Field(default=None, ge=0, description="本段距离（米）。")


class CommuteBreakBuffer(BaseModel):
    """课间规则缓冲：当前时刻是否落在八个课间点前后十分钟内。

    这是**规则估计**，不是实时人流数据；``rule_note`` 每次都必须随结果展示。
    """

    in_window: bool = Field(description="当前时刻是否命中课间前后十分钟窗口。")
    matched_break_time: str | None = Field(
        default=None, description="命中的课间时间点 HH:MM；未命中为 None。"
    )
    minutes_away: int | None = Field(
        default=None, description="与命中时间点的分钟差（绝对值）。"
    )
    added_minutes: int = Field(default=0, ge=0, description="建议在高德耗时之外增加分钟数。")
    checked_at: datetime = Field(description="判定时刻（含时区）。")
    timezone: str = Field(description="判定所用时区，固定 Asia/Shanghai。")
    rule_note: str = Field(description="中文规则说明（明示不是实时人流数据）。")


class CommuteRouteProjection(BaseModel):
    """通勤模块随助手消息持久化的完整投影。"""

    status: CommuteRouteStatus = Field(description="本轮模块状态。")
    mode: CommuteMode | None = Field(default=None, description="本轮出行方式。")
    mode_label: str | None = Field(default=None, description="方式中文标签。")
    mode_phrase: str | None = Field(default=None, description="用户原话中的方式写法。")
    origin: CommutePlace | None = Field(default=None, description="解析后的起点。")
    destination: CommutePlace | None = Field(default=None, description="解析后的终点。")
    origin_candidates: list[CommutePlaceCandidate] = Field(
        default_factory=list, description="起点候选（澄清冲突时列出）。"
    )
    destination_candidates: list[CommutePlaceCandidate] = Field(
        default_factory=list, description="终点候选（澄清冲突时列出）。"
    )
    distance_m: int | None = Field(default=None, ge=0, description="高德返回的路线距离（米）。")
    base_duration_seconds: int | None = Field(
        default=None, ge=0, description="高德返回的基础耗时（秒，仅本轮方式）。"
    )
    suggested_total_seconds: int | None = Field(
        default=None, ge=0, description="基础耗时加缓冲后的建议总时间（秒）。"
    )
    steps: list[CommuteRouteStep] = Field(
        default_factory=list, description="高德返回的文字路段。"
    )
    polyline: list[str] = Field(
        default_factory=list, description="高德返回的路径点（按顺序的坐标串）。"
    )
    path_verified: bool = Field(
        default=False, description="是否取得可绘制的路径点（否则不生成地图线）。"
    )
    buffer: CommuteBreakBuffer | None = Field(
        default=None, description="课间规则缓冲判定。"
    )
    queries: list[ModuleQueryRecord] = Field(
        default_factory=list, description="本轮全部外部调用的统一记录（查询/证据/时间/错误）。"
    )
    evidence_notes: list[str] = Field(
        default_factory=list, description="证据边界说明（吸附过远、路径点缺失、校外被拒等）。"
    )
    pending: ModuleWaitState | None = Field(
        default=None, description="跨轮次等待状态（澄清问题）；无等待为 None。"
    )
    resolved_at: datetime | None = Field(default=None, description="路线核验完成时间。")
    error_code: str | None = Field(default=None, description="失败分类码。")
    error_message: str | None = Field(default=None, description="可操作的中文错误说明。")
    retryable: bool = Field(default=False, description="本轮失败是否可重试。")


class CommuteClarification(BaseModel):
    """一条待用户回答的澄清（每轮只问一项）。"""

    question: str = Field(description="向用户提出的那一个问题（中文，只问一项）。")
    missing: str = Field(
        description="缺失项分类：origin/destination/mode/…_unlocatable/…_choice。"
    )
    role: CommutePlaceRole | None = Field(default=None, description="涉及的地点在路线中的角色。")
    candidates: list[CommutePlaceCandidate] = Field(
        default_factory=list, description="候选地点（仅 *_choice 类澄清携带）。"
    )


class CommuteRequestAnalysis(BaseModel):
    """``route.parse`` 的解析结果（保留原话，缺哪项只问哪项）。"""

    raw_text: str = Field(description="本轮用户原文。")
    mode: CommuteMode | None = Field(default=None, description="解析出的出行方式。")
    mode_phrase: str | None = Field(default=None, description="用户原话中的方式写法。")
    mode_candidates: list[CommuteMode] = Field(
        default_factory=list, description="原话并列提到多种方式时的候选（需用户选择）。"
    )
    origin_phrase: str | None = Field(default=None, description="起点原话。")
    destination_phrase: str | None = Field(default=None, description="终点原话。")
    origin_unlocatable: bool = Field(
        default=False, description="起点是「我这里」类无法定位的指代。"
    )
    destination_unlocatable: bool = Field(
        default=False, description="终点是「我这里」类无法定位的指代。"
    )
    #: 上一轮用户已从候选中选定的地点：直接采用，不再重新检索（避免反复澄清）。
    origin_place: CommutePlace | None = Field(
        default=None, description="候选中已选定的起点（跳过重新解析）。"
    )
    destination_place: CommutePlace | None = Field(
        default=None, description="候选中已选定的终点（跳过重新解析）。"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="解析置信度（0–1）。")
    clarification: CommuteClarification | None = Field(
        default=None, description="缺失或含糊时的单一澄清问题；无需澄清为 None。"
    )
