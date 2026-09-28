"""Issue 04/05：画像跨记录写入的提交边界归属地。

自动提取要同时维护三类记录：四维来源记录（内部提取与冲突消解的依据）、
它对应的用户可见原子条目（镜像），以及同一批次的提取记账（运行记录与
观察记录）。用户权威操作（聊天更正、删除/忘掉后的来源撤回）维护的是
同一对记录，且「墓碑先落地、再撤回来源」的顺序是有意的防复活保护。
本模块是这组写入的提交边界归属地，回答四个问题：

- **一次提交包含哪些写入**：四维来源、原子镜像与提取记账必须同成同败；
  任一步失败就整批回滚，不留下会被后续读取当成成功的部分画像。记账行
  仍由调用方按自己的端口写（它比本模块更清楚一个批次记什么），但写在与
  :meth:`ProfileCommit.write_records` 相同的边界内。
- **失败后保留什么**：尝试记录（重试任务状态、失败审计、模型运行锁）
  不是业务成功结果，由调用方在回滚之后另开事务保留，供有界重试与审计
  使用。
- **用户权威的成对写入**：聊天更正走 :meth:`ProfileCommit.write_correction`，
  首次处理与后台重试共用同一实现，来源与镜像不因路径不同而分叉；墓碑
  提交后的来源撤回走 :meth:`ProfileCommit.withdraw_item_sources`，逐条
  隔离且幂等——撤回失败不回滚已提交的墓碑，重复执行不重写业务结果。
  指令识别、目标匹配与用户请求校验留在各自入口，本模块只负责「先写哪张
  记录、再撤回哪个来源」的顺序。
- **谁决定存储形态**：事务边界由各仓库 adapter 自己声明——共用同一
  ``BridgesDatabase`` 的 SQLite 仓库并入外层事务，内存仓库各自建立可
  回滚快照。调用方不判断仓库类型、连接或事务开关。

失败落在保护点的哪一侧、之后能不能重试，也由本模块回答：

- **保护点之内**：删除／忘掉的墓碑、编辑的换键与抑制键都在提交边界里。
  边界内任一步失败即整批回滚，用户可见结果与失败前相同，入口可以整轮
  重试（纠正路径也一样，见 :meth:`ProfileCommit.write_correction`）。
- **保护点之后**：只剩来源撤回一步。它失败时墓碑已经提交，删除照常成立
  ——条目不会回到活动列表或记忆切片，残留状态只是「底层来源记录仍是活
  动的」，也就是 :meth:`ProfileCommit.recover_source_withdrawals` 的扫描
  对象。撤回因此逐条隔离：一条失败不影响其余条目，失败只以 ``FAILED``
  结论返回并可再次重试（``forget`` 保持本轮删除结论，``delete_item`` 原样
  上抛但墓碑有效）。
- **恢复不依赖进程内对象**：待补工作只从持久墓碑重新推导，因此连接或
  module 重建之后仍能补齐。补齐后的可见结果是「条目保持删除、来源记录
  转为已撤回」；已完成或没有来源的条目收敛为 ``ALREADY_WITHDRAWN``／
  ``NO_SOURCE``，重复调用不重写业务结果，也不影响用户新建或编辑的条目。

抽取范围、阈值、提示词与抽取侧去重规则不在本模块：它们留在自动提取的
implementation 中；用户权威规则（版本冲突、条目去重、旧正文抑制、墓碑）
留在原子画像的 implementation 中，本模块负责把它们与来源撤回编排成序。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from bridges.contracts.profiles import (
    FourDimension,
    FourDimensionConfidence,
    FourDimensionRecordStatus,
)
from bridges.profiles.adapters import ProfileError

if TYPE_CHECKING:
    # 自动提取编排器与原子画像服务都会在运行时导入本模块，因此实现类型
    # 只在这里取名字，避免运行时循环导入。
    from bridges.contracts.atomic_profile import AtomicProfileItem
    from bridges.contracts.profiles import FourDimensionProfileRecord
    from bridges.profiles.atomic import AtomicProfileService
    from bridges.profiles.automatic import AutomaticProfileRepository
    from bridges.profiles.four_dimensions import FourDimensionProfileService

logger = logging.getLogger(__name__)


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


class SourceWithdrawalStatus(Enum):
    """一条来源撤回的结论；除 ``FAILED`` 外，重复执行收敛到同一结论。"""

    WITHDRAWN = "withdrawn"
    ALREADY_WITHDRAWN = "already_withdrawn"
    NO_SOURCE = "no_source"
    FAILED = "failed"


@dataclass(frozen=True)
class SourceWithdrawal:
    """单个原子条目的来源撤回结果（不含画像正文）。

    ``FAILED`` 携带原始异常供调用方决定上抛或记账；其余结论在重试同一
    撤回操作时保持不变或收敛为 ``ALREADY_WITHDRAWN``，不会重复写业务
    结果，也不会影响用户新建或编辑的条目。
    """

    profile_item_id: str
    record_id: str | None
    status: SourceWithdrawalStatus
    error: Exception | None = None


class ProfileCommit:
    """四维来源与原子镜像的提交边界与成对写入。

    调用方在同一进程内复用同一个实例：``transaction()`` 打开一次提交，
    ``write_records()``/``write_correction()`` 在其中写入跨记录结果，
    ``withdraw_item_sources()`` 在墓碑提交之后撤回来源。
    """

    def __init__(
        self,
        *,
        records: FourDimensionProfileService,
        state: AutomaticProfileRepository | None = None,
        items: AtomicProfileService | None = None,
    ) -> None:
        self._records = records
        self._items = items
        # 顺序固定：已挂载的仓库依次并入边界。自动提取的边界包含记账
        # 仓库（``state``）；用户权威操作只需要跨记录两方。SQLite 仓库
        # 共用连接时只有第一次调用真正 BEGIN，其余并入；内存仓库各自
        # 建立可回滚快照，同一组业务对象一起回滚。并入规则在 adapter
        # 内部（见 ``bridges.profiles.transactions``），这里不判断仓库类型。
        participants: list[ProfileCommitParticipant] = []
        if state is not None:
            participants.append(state)
        participants.append(records)
        if items is not None:
            participants.append(items)
        self._participants: list[ProfileCommitParticipant] = participants

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
        *,
        from_replay: bool = False,
    ) -> list[str]:
        """按顺序写入四维记录并立刻镜像原子条目，返回去重后的记录标识。

        必须在 :meth:`transaction` 内调用；否则每条记录各自成一次提交。

        ``from_replay`` 标记本次写入来自旧消息重放，四维仓库据此保护用户
        纠正过的记录不被改回旧值；新消息的自动写入保持既有证据阶梯。

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
                from_replay=from_replay,
            )
            if self._items is not None:
                self._items.mirror_record(
                    account_id,
                    record,
                    evidence_message_id=submission.evidence_message_id,
                )
            record_ids.append(record.record_id)
        return list(dict.fromkeys(record_ids))

    def write_correction(
        self,
        account_id: str,
        *,
        dimension: FourDimension,
        content: str,
    ) -> tuple[FourDimensionProfileRecord | None, bool]:
        """聊天更正的成对写入：先按维度纠正四维记录，成功后立刻镜像。

        必须在 :meth:`transaction` 内调用。目标定位、保护阈值（反复纠错）
        与版本校验留在四维服务，返回 ``(record, changed)`` 语义不变；
        本方法只保证「记录已改、镜像跟上」是同一个动作——首次处理与后台
        重试共用这一实现，重试不再出现只写来源、不更新条目的分叉。
        """

        record, changed = self._records.correct_record(
            account_id,
            dimension=dimension,
            content=content,
            manage_transaction=False,
        )
        if changed and record is not None and self._items is not None:
            self._items.mirror_record(account_id, record)
        return record, changed

    def withdraw_item_sources(
        self,
        account_id: str,
        items: Iterable[AtomicProfileItem],
    ) -> list[SourceWithdrawal]:
        """撤回原子条目底层的四维来源记录；逐条隔离、幂等。

        必须在墓碑（或抑制键）提交之后调用：调用方不判断「先写哪张记录、
        再撤回哪个来源」，本方法是撤回一步的唯一归属地。每条撤回独立
        成败——一条失败不中断其余条目，失败明细随结果返回并记入日志，
        已提交的墓碑不因此回滚（用户可见的删除不因内部失败重新可见）。
        重复执行安全：已撤回或没有来源的条目收敛为非 ``FAILED`` 结论，
        不重写业务结果。
        """

        outcomes: list[SourceWithdrawal] = []
        for item in items:
            record_id = item.source_record_id
            if record_id is None:
                outcomes.append(
                    SourceWithdrawal(
                        profile_item_id=item.profile_item_id,
                        record_id=None,
                        status=SourceWithdrawalStatus.NO_SOURCE,
                    )
                )
                continue
            outcome = self._withdraw_one(account_id, item, record_id)
            if outcome.status is SourceWithdrawalStatus.FAILED:
                logger.warning(
                    "画像来源撤回失败（可安全重试）：%s / %s / %s",
                    item.profile_item_id,
                    record_id,
                    outcome.error,
                )
            outcomes.append(outcome)
        return outcomes

    def recover_source_withdrawals(self, account_id: str) -> list[SourceWithdrawal]:
        """按持久残留状态补齐来源撤回：扫描墓碑条目，幂等重跑撤回。

        跨重启恢复的唯一入口。待补工作只从存储里的墓碑重新推导——已删除
        且仍指向来源记录的条目（见
        :meth:`AtomicProfileService.tombstoned_items_with_sources`），因此
        模块重建、进程内对象全部丢失之后仍然有效，不需要通用任务框架或
        额外记账表。已完成或没有来源的条目收敛为 ``ALREADY_WITHDRAWN``／
        ``NO_SOURCE``，不重写业务结果；仍未完成的条目保持 ``FAILED`` 并可
        再次调用本方法重试。扫描按账户作用域，不碰其他账户的条目。

        恢复后的可见结果：条目保持删除（不在列表与记忆切片里），底层来源
        记录转为已撤回；用户在这期间新建或编辑的条目不受影响。
        """

        if self._items is None:
            return []
        return self.withdraw_item_sources(
            account_id, self._items.tombstoned_items_with_sources(account_id)
        )

    def _withdraw_one(
        self,
        account_id: str,
        item: AtomicProfileItem,
        record_id: str,
    ) -> SourceWithdrawal:
        try:
            record = self._records.get_record(account_id, record_id)
        except ProfileError:
            # 来源记录不存在或无权访问：没有可撤回的东西，视为已撤回。
            return SourceWithdrawal(
                profile_item_id=item.profile_item_id,
                record_id=record_id,
                status=SourceWithdrawalStatus.ALREADY_WITHDRAWN,
            )
        except Exception as exc:  # noqa: BLE001 - 存储故障同样逐条隔离
            return SourceWithdrawal(
                profile_item_id=item.profile_item_id,
                record_id=record_id,
                status=SourceWithdrawalStatus.FAILED,
                error=exc,
            )
        if record.status != FourDimensionRecordStatus.ACTIVE:
            return SourceWithdrawal(
                profile_item_id=item.profile_item_id,
                record_id=record_id,
                status=SourceWithdrawalStatus.ALREADY_WITHDRAWN,
            )
        try:
            self._records.withdraw_record(account_id, record_id)
        except Exception as exc:  # noqa: BLE001 - 撤回失败逐条隔离，不中断其余条目
            return SourceWithdrawal(
                profile_item_id=item.profile_item_id,
                record_id=record_id,
                status=SourceWithdrawalStatus.FAILED,
                error=exc,
            )
        return SourceWithdrawal(
            profile_item_id=item.profile_item_id,
            record_id=record_id,
            status=SourceWithdrawalStatus.WITHDRAWN,
        )
