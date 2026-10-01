"""指代解析与可追溯锚点的共享合同（改进工单 11）。

本模块把 ``docs/上下文工程/改进方案.md`` 决策 6 与 ``docs/workflow/
orchestration.md`` 第 3 节的定位顺序固化为类型，供后续消费者共用：

1. 定位顺序：明确任务/产物指代 → 当前任务 → 相关近期前文 → 同会话原文检索；
   依据目标、对象、列表/结果版本与消息 ID，而不是「最近一个模块」或模型
   自报高置信。
2. 解析输出是**共同任务描述 + 可追溯锚点**：理解（工单 12）、摘要定位
   （13）、原材料选择（14）与任务查询（15/19）读同一份结果。
3. 指代解析是**只读定位**：它不产生新的用户条件；条件只能由用户明示经
   工单 08 的任务领域落地。

锚点只携带消息/对象 ID、短标签、列表版本与采用/排除原因；完整原文留在
会话仓库，需要时由消费者按 ID 读取。诊断（``rejected``、诊断字段）不含
完整私人正文。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.tasks import TaskCondition

#: 本模块合同的版本标识；解析输出携带该值供跨版本读取与审计。
REFERENCE_CONTRACT_VERSION = "reference-v1"


class ReferenceStatus(StrEnum):
    """一次指代解析的结果状态。"""

    #: 请求没有可识别的指代/续接信号（普通新话题）。
    NONE = "none"
    #: 唯一对象已定位（含已在近期原文中、无需补回的情形）。
    RESOLVED = "resolved"
    #: 部分命中：已定位部分锚点，但仍有引用内容未找到（缺失项见
    #: ``missing_requirements``）；不得当作完整定位。
    PARTIAL = "partial"
    #: 实质歧义：多个同样合理的对象会改变结果；只问一个必要问题。
    AMBIGUOUS = "ambiguous"
    #: 有指代信号但未能定位任何原文/对象：给出真实缺口。
    UNRESOLVED = "unresolved"


class AnchorKind(StrEnum):
    """解析锚点的种类。"""

    TASK = "task"
    CONDITION = "condition"
    MESSAGE = "message"
    RESULT_LIST = "result_list"
    LIST_ITEM = "list_item"


class ReferenceAnchor(BaseModel):
    """一个可追溯锚点。

    ``adopted`` 表示该锚点是否作为解析结果被采用；未采用项保留在
    ``rejected`` 中并给出原因。``label`` 是短标签（如条件类别、列表名、
    论文标题/仓库名），不是完整私人正文。
    """

    anchor_id: str
    kind: AnchorKind
    label: str
    message_ids: list[str] = Field(default_factory=list)
    object_id: str | None = Field(
        default=None,
        description="结果对象标识（论文/仓库等）；无对象时为 null。",
    )
    list_version: int | None = Field(default=None, description="同一类结果列表的版本号（1 起）。")
    adopted: bool = True
    reason: str | None = Field(default=None, description="采用或排除原因（中文短说明）。")


class TaskBriefCondition(BaseModel):
    """共同任务描述中的一条条件（只读回显，不产生新条件）。"""

    condition_id: str
    kind: str
    text: str
    status: str
    effective: bool = Field(description="是否仍有效（status == effective）。")
    source_message_id: str
    supersedes_condition_id: str | None = None


class ReferenceTaskBrief(BaseModel):
    """指代解析产出的共同任务描述（供理解/检索/画像/模块共用）。"""

    task_id: str | None = None
    goal: str = ""
    version: int | None = None
    status: str | None = None
    conditions: list[TaskBriefCondition] = Field(default_factory=list)


class ReferenceClarification(BaseModel):
    """实质歧义时的一个必要澄清问题。

    ``task_id``/``expected_version`` 让消费者（工单 12）把回答绑定到任务
    版本（工单 08 的等待合同）；解析本身不写任何等待或条件。
    """

    question: str
    options: list[str] = Field(default_factory=list)
    task_id: str | None = None
    expected_version: int | None = None


class ReferenceResolution(BaseModel):
    """一次指代解析的完整输出（共同任务描述 + 锚点 + 诊断）。"""

    contract_version: str = REFERENCE_CONTRACT_VERSION
    status: ReferenceStatus = ReferenceStatus.NONE
    task: ReferenceTaskBrief | None = None
    anchors: list[ReferenceAnchor] = Field(default_factory=list)
    #: 采用的材料消息 ID（含近期原文与需要补回的旧原文，按会话时间序）。
    adopted_message_ids: list[str] = Field(default_factory=list)
    #: 已采用但不在本轮近期原文中的消息 ID（需要按原文补回）。
    recovered_message_ids: list[str] = Field(default_factory=list)
    #: 采用的结果对象 ID（论文/仓库等）。
    adopted_object_ids: list[str] = Field(default_factory=list)
    #: 纠正关系说明：message_id → 中文短说明（已被纠正/已撤销，不复活旧值）。
    correction_notes: dict[str, str] = Field(default_factory=dict)
    #: 未能定位的引用内容（短标签，不是完整正文）；非空不得当作完整定位。
    missing_requirements: list[str] = Field(default_factory=list)
    clarification: ReferenceClarification | None = None
    #: 未被采用的候选锚点及原因（诊断不含完整私人正文）。
    rejected: list[ReferenceAnchor] = Field(default_factory=list)

    def adopted_anchor_ids(self) -> list[str]:
        """已采用锚点的稳定 ID 列表（诊断与审计用）。"""
        return [anchor.anchor_id for anchor in self.anchors if anchor.adopted]

    def rejected_reasons(self) -> list[dict[str, str]]:
        """未采用锚点的 ID 与原因（诊断不含正文）。"""
        return [
            {"anchor_id": anchor.anchor_id, "reason": anchor.reason or ""}
            for anchor in self.rejected
        ]


class ReferenceTaskContext(BaseModel):
    """指代解析的任务输入快照（含全部状态的条件，供纠正链读取）。

    由工单 08 的任务领域提供；解析只读该快照，绝不写回。
    ``source_message_ids`` 是当前任务版本的来源消息（含条件来源），
    续接时作为可追溯锚点。
    """

    task_id: str
    goal: str
    version: int
    status: str
    conditions: list[TaskCondition] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
