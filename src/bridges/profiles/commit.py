"""Issue 04：一次自动画像写入的提交边界与跨记录写入。

自动提取要同时维护三类记录：四维来源记录（内部提取与冲突消解的依据）、
它对应的用户可见原子条目（镜像），以及同一批次的提取记账（运行记录与
观察记录）。本模块是这组写入的提交边界归属地，回答三个问题：

- **一次提交包含哪些写入**：四维来源、原子镜像与提取记账必须同成同败；
  任一步失败就整批回滚，不留下会被后续读取当成成功的部分画像。记账行
  仍由调用方按自己的端口写（它比本模块更清楚一个批次记什么），但写在与
  :meth:`ProfileCommit.write_records` 相同的边界内。
- **失败后保留什么**：尝试记录（重试任务状态、失败审计、模型运行锁）
  不是业务成功结果，由调用方在回滚之后另开事务保留，供有界重试与审计
  使用。
- **谁决定存储形态**：事务边界由各仓库 adapter 自己声明——共用同一
  ``BridgesDatabase`` 的 SQLite 仓库并入外层事务，内存仓库各自建立可
  回滚快照。调用方不判断仓库类型、连接或事务开关。

抽取范围、阈值、提示词、去重与用户权威规则不在本模块：它们分别留在
自动提取与原子画像的 implementation 中。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.atomic import AtomicProfileService
from bridges.profiles.four_dimensions import FourDimensionProfileService

if TYPE_CHECKING:
    # 自动提取编排器在运行时导入本模块，因此仓库端口只在这里取名字。
    from bridges.profiles.automatic import AutomaticProfileRepository


class ProfileCommitParticipant(Protocol):
    """一次提交里承担同行或同败责任的画像仓库。"""

    def transaction(self) -> AbstractContextManager[None]: ...


@dataclass(frozen=True)
class ProfileRecordSubmission:
    """一条待提交的四维记录及其原子镜像依据。

    字段与 ``upsert_automatic_record`` 一一对应：本模块只负责把「写入四维
    记录」与「镜像成原子条目」当成一次写入执行，取值由调用方的抽取与
    晋升规则决定，因此这里不设策略默认值。
    """

    dimension: FourDimension
    content: str
    action: str
    confidence: FourDimensionConfidence
    migration_version: str
    evidence_quote: str | None = None
    evidence_message_id: str | None = None
    change_note: str | None = None


class ProfileCommit:
    """四维来源与原子镜像的提交边界与成对写入。

    调用方在同一进程内复用同一个实例：``transaction()`` 打开一次提交，
    ``write_records()`` 在其中写入跨记录结果。
    """

    def __init__(
        self,
        *,
        state: AutomaticProfileRepository,
        records: FourDimensionProfileService,
        items: AtomicProfileService | None = None,
    ) -> None:
        self._records = records
        self._items = items
        # 顺序固定：记账仓库先开边界，跨记录仓库依次并入。SQLite 三个仓库
        # 共用连接时只有第一次调用真正 BEGIN，其余并入；内存仓库各自建立
        # 可回滚快照，同一组业务对象一起回滚。并入规则在 adapter 内部
        # （见 ``bridges.profiles.transactions``），这里不判断仓库类型。
        self._participants: list[ProfileCommitParticipant] = [state, records]
        if items is not None:
            self._participants.append(items)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """一次提交的事务边界：任一步失败则整批回滚。"""

        with ExitStack() as stack:
            for participant in self._participants:
                stack.enter_context(participant.transaction())
            yield

    def write_records(
        self,
        account_id: str,
        submissions: Iterable[ProfileRecordSubmission],
    ) -> list[str]:
        """按顺序写入四维记录并立刻镜像原子条目，返回去重后的记录标识。

        必须在 :meth:`transaction` 内调用；否则每条记录各自成一次提交。

        镜像不写入是正常结果，不是系统失败：正文为空、用户已把该条目改成
        别的正文时，镜像按用户权威跳过，本方法照常返回该记录标识（调用方
        的「空结果」因此只表示没有可写事实）。用户已删除（撤回）同一条
        事实是另一回事：四维仓库会拒绝复活并抛出错误，由调用方按既有有界
        重试策略处理，耗尽后不产生成功结果。
        """

        record_ids: list[str] = []
        for submission in submissions:
            record = self._records.upsert_automatic_record(
                account_id,
                dimension=submission.dimension,
                content=submission.content,
                action=submission.action,
                confidence=submission.confidence,
                evidence_quote=submission.evidence_quote,
                evidence_message_id=submission.evidence_message_id,
                change_note=submission.change_note,
                migration_version=submission.migration_version,
            )
            if self._items is not None:
                self._items.mirror_record(
                    account_id,
                    record,
                    evidence_message_id=submission.evidence_message_id,
                )
            record_ids.append(record.record_id)
        return list(dict.fromkeys(record_ids))
