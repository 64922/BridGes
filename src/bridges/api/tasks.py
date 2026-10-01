"""跨轮任务 API（改进工单 08）。

对外暴露有来源的跨轮任务、不可变版本、有效条件与澄清等待的**只读投影**，
以及一个关系落地入口 ``POST /tasks/turns``：它接收工单 12 的主智能体理解
结果（关系、目标、条件、缺失字段），由 :class:`~bridges.tasks.service.TaskService`
按代码规则裁决并持久化，返回结果投影。

设计要点：

- 写模型归任务领域仓库；本路由只做参数校验、账户作用域与投影序列化，
  不复制任何状态机规则。
- 读取一律按会话或任务定位，并经 ``scoped(account_id)`` 强制账户隔离，
  跨账户访问返回 404。
- ``POST /tasks/turns`` 是确定性入口：同一输入可重放（API 多轮重放证据），
  乐观版本冲突返回 409，普通聊天（无目标无条件）不创建任务。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from bridges.api.auth import SubjectDep
from bridges.contracts.tasks import (
    TaskCondition,
    TaskEvent,
    TaskProjection,
    TaskTurnRequest,
    TaskTurnResult,
    TaskVersion,
    TaskWait,
)
from bridges.tasks.repository import (
    TaskError,
    TaskNotFound,
    TaskStateConflict,
    TaskVersionConflict,
)
from bridges.tasks.service import TaskService

router = APIRouter(prefix="/tasks", tags=["跨轮任务"])


class TaskErrorResponse(BaseModel):
    """任务接口的稳定错误体。"""

    error: str = Field(description="稳定错误码。")
    message: str = Field(description="可操作的中文说明。")


class TaskWaitCreateRequest(BaseModel):
    """登记一个澄清等待（生产由澄清节点调用；等待不保留工作租约）。"""

    conversation_id: str = Field(min_length=1)
    question: str = Field(min_length=1, description="已向用户提出的那一个问题。")
    missing_fields: list[str] = Field(
        min_length=1, description="等待补齐的缺失字段（答复须补齐其中至少一项）。"
    )
    origin_message_id: str = Field(min_length=1, description="提出问题的助手消息 ID。")
    source_message_id: str = Field(min_length=1, description="触发等待的用户消息 ID。")
    task_id: str | None = Field(
        default=None, description="目标任务；缺省用会话当前任务。"
    )


def _get_task_service(request: Request) -> TaskService:
    service: TaskService | None = getattr(request.app.state, "task_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=TaskErrorResponse(
                error="tasks_unavailable",
                message="任务存储未启用，当前实例拒绝任务读写。",
            ).model_dump(),
        )
    return service


TaskServiceDep = Annotated[TaskService, Depends(_get_task_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=TaskErrorResponse(error=code, message=message).model_dump(),
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=list[TaskProjection],
    responses={status.HTTP_404_NOT_FOUND: {"model": TaskErrorResponse}},
)
def list_conversation_tasks(
    conversation_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> list[TaskProjection]:
    """列出某会话的全部任务投影（含有效条件与未决等待）。"""
    return service.list_projections(subject.account_id, conversation_id)


@router.get(
    "/conversations/{conversation_id}/events",
    response_model=list[TaskEvent],
)
def list_conversation_task_events(
    conversation_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> list[TaskEvent]:
    """列出某会话的任务审计事件（append-only）。"""
    return service.repository.list_events(subject.account_id, conversation_id)


@router.get(
    "/{task_id}",
    response_model=TaskProjection,
    responses={status.HTTP_404_NOT_FOUND: {"model": TaskErrorResponse}},
)
def get_task(
    task_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> TaskProjection:
    """读取单个任务的当前投影。"""
    projection = service.projection(subject.account_id, task_id)
    if projection is None:
        raise _error(status.HTTP_404_NOT_FOUND, "task_not_found", "任务不存在或不属于当前账户。")
    return projection


@router.get(
    "/{task_id}/versions",
    response_model=list[TaskVersion],
)
def list_task_versions(
    task_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> list[TaskVersion]:
    """列出任务的不可变版本快照（按版本号升序）。"""
    return service.list_versions(subject.account_id, task_id)


@router.get(
    "/{task_id}/conditions",
    response_model=list[TaskCondition],
)
def list_task_conditions(
    task_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> list[TaskCondition]:
    """列出任务的逐条条件（含草案、线索与被取代值，供审计）。"""
    return service.list_conditions(subject.account_id, task_id)


@router.get(
    "/{task_id}/waits",
    response_model=list[TaskWait],
)
def list_task_waits(
    task_id: str,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> list[TaskWait]:
    """列出任务的澄清等待（含历史终态，供审计）。"""
    return service.list_waits(subject.account_id, task_id)


@router.post(
    "/waits",
    response_model=TaskWait,
    responses={status.HTTP_404_NOT_FOUND: {"model": TaskErrorResponse}},
)
def open_task_wait(
    body: TaskWaitCreateRequest,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> TaskWait:
    """登记一个澄清等待：任务进入等待态，等待不保留工作租约。"""
    try:
        return service.open_wait(
            subject.account_id,
            conversation_id=body.conversation_id,
            question=body.question,
            missing_fields=body.missing_fields,
            origin_message_id=body.origin_message_id,
            source_message_id=body.source_message_id,
            task_id=body.task_id,
        )
    except TaskNotFound as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "task_not_found", str(exc)) from exc
    except TaskError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "task_error", str(exc)) from exc


@router.post(
    "/turns",
    response_model=TaskTurnResult,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": TaskErrorResponse},
        status.HTTP_409_CONFLICT: {"model": TaskErrorResponse},
    },
)
def apply_task_turn(
    body: TaskTurnRequest,
    subject: SubjectDep,
    service: TaskServiceDep,
) -> TaskTurnResult:
    """落地一轮任务关系：解析关系、生成版本、绑定条件、处置等待。

    确定性入口：同一请求可重放，乐观版本冲突返回 409。
    """
    try:
        return service.apply_turn(subject.account_id, body)
    except TaskVersionConflict as exc:
        raise _error(status.HTTP_409_CONFLICT, "task_version_conflict", str(exc)) from exc
    except TaskStateConflict as exc:
        raise _error(status.HTTP_409_CONFLICT, "task_state_conflict", str(exc)) from exc
    except TaskNotFound as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "task_not_found", str(exc)) from exc
    except TaskError as exc:
        raise _error(
            status.HTTP_400_BAD_REQUEST, "task_error", str(exc)
        ) from exc
