"""用户可见固定文案的类型与稳定标识（Issue 23）。

本模块只定义类型与枚举，不承载文案本身，也不依赖任何业务包：注册表与
目录可以按状态登记模板，供聊天、检索、模块与学习流程共用同一份「路径 →
真实状态 → 中文文案」清单。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: 固定文案注册表的整体版本；条目级版本由路径与状态共同确定，整体版本
#: 变更表示路径语义或模板选择规则发生不兼容调整。
STATE_COPY_VERSION = "state-copy-v1"


class StateCopyError(RuntimeError):
    """固定文案注册表错误基类。"""


class StateCopyNotFoundError(StateCopyError, KeyError):
    """按路径渲染时路径未登记。"""


class StateCopyRenderError(StateCopyError, ValueError):
    """路径不可按模板渲染或缺少占位值。"""


class StateCopyRegistryError(StateCopyError):
    """注册表自身不完整（重复路径、缺渲染器、类别覆盖缺口等）。"""


class CopyCategory(StrEnum):
    """文案清单的路径类别（工单 23 要求的正式路径划分）。"""

    CLARIFICATION = "clarification"
    PROGRESS = "progress"
    WAIT = "wait"
    STOP = "stop"
    ERROR = "error"
    EMPTY = "empty"
    PARTIAL = "partial"
    DEGRADATION = "degradation"
    MODULE = "module"
    STUDY = "study"


class CopyStrategy(StrEnum):
    """一条路径的文案如何产生（固定模板由状态选择，不交给模型全文重写）。"""

    #: 状态/真实结果直接选择中文模板，本注册表可确定性渲染。
    FIXED_TEMPLATE = "fixed_template"
    #: 由领域渲染器从类型化真实结果确定性生成（路线、引用、价格、时间等字段保真）。
    DETERMINISTIC_RENDERER = "deterministic_renderer"
    #: 在既有用户可见生成调用中适配自然语言（后续领域票据接入表达约束，不新增调用）。
    MODEL_ADAPTED = "model_adapted"


class FailureClass(StrEnum):
    """错误的真实类别：未读取、不支持、未配置、限流/超时、不可核实等分开。"""

    NOT_READ = "not_read"
    UNSUPPORTED = "unsupported"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMIT_TIMEOUT = "rate_limit_timeout"
    UNVERIFIABLE = "unverifiable"
    #: 服务/网络不可达（离线、连接、DNS 失败），与限流/超时分开。
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"
    STATE_CONFLICT = "state_conflict"
    #: 用户主动取消/停止，不是错误。
    STOPPED = "stopped"


class RecoveryAction(StrEnum):
    """错误文案只承诺真实可用的恢复方式。"""

    RETRY = "retry"
    WAIT = "wait"
    RECONFIGURE = "reconfigure"
    ADJUST_REQUEST = "adjust_request"
    REFRESH_STATE = "refresh_state"
    NONE = "none"


@dataclass(frozen=True)
class CopyEntry:
    """一条用户可见来源的登记项（模板、渲染器或生成策略）。"""

    path: str
    category: CopyCategory
    owner: str
    strategy: CopyStrategy
    states: tuple[str, ...]
    text: str | None = None
    renderer: str | None = None
    note: str = ""


@dataclass(frozen=True)
class ErrorTemplate:
    """稳定错误码对应的固定中文模板与真实失败类别/恢复方式。"""

    code: str
    text: str
    failure_class: FailureClass
    recovery: RecoveryAction
    #: 是否属于聊天、图片、视频、语音共用的模型调用类映射。
    shared: bool = False
    #: 携带可靠上下文的领域错误：注册文案/类别/恢复方式，但不进入
    #: 「码 → 固定文案」优先表，避免覆盖节点位置等更具体的真实原因。
    contextual: bool = False
