"""画像工单 03：开放原子记忆的唯一提交边界。

自动提取、明确更正与用户权威操作（记住、行内编辑、目标生命周期、删除、
忘掉）都直接写入原子条目；本模块是这组写入的提交边界归属地，回答四个
问题：

- **一次提交包含哪些写入**：开放事实（原子条目）与自动提取记账必须同成
  同败；任一步失败就整批回滚，不留下会被后续读取当成成功的部分画像。记账
  行仍由调用方按自己的端口写（它比本模块更清楚一个批次记什么），但写在与
  :meth:`ProfileCommit.write_facts` 相同的边界内。
- **失败后保留什么**：尝试记录（重试任务状态、失败审计、模型运行锁）
  不是业务成功结果，由调用方在回滚之后另开事务保留，供有界重试与审计
  使用。
- **用户权威的写入**：聊天更正走 :meth:`ProfileCommit.write_correction`，
  首次处理与后台重试共用同一实现。删除／忘掉的墓碑在提交边界内，之后对
  **存量**旧四维来源的撤回走 :meth:`ProfileCommit.withdraw_item_sources`，
  逐条隔离且幂等——撤回失败不回滚已提交的墓碑，重复执行不重写业务结果。
  新写入不再产生四维记录，因此新条目没有内部来源可撤回；撤回只服务迁移
  前的存量条目与「记住」时关联到的旧记录。
- **谁决定存储形态**：事务边界由各仓库 adapter 自己声明——共用同一
  ``BridgesDatabase`` 的 SQLite 仓库并入外层事务，内存仓库各自建立可
  回滚快照。调用方不判断仓库类型、连接或事务开关。

失败落在保护点的哪一侧、之后能不能重试，也由本模块回答：

- **保护点之内**：删除／忘掉的墓碑、编辑的换键与抑制键都在提交边界里。
  边界内任一步失败即整批回滚，用户可见结果与失败前相同，入口可以整轮
  重试（纠正路径也一样，见 :meth:`ProfileCommit.write_correction`）。
- **保护点之后**：只剩存量来源撤回一步。它失败时墓碑已经提交，删除照常
  成立——条目不会回到活动列表或记忆切片，残留状态只是「底层旧四维记录
  仍是活动的」，也就是 :meth:`ProfileCommit.recover_source_withdrawals`
  的扫描对象。撤回因此逐条隔离：一条失败不影响其余条目，失败只以
  ``FAILED`` 结论返回并可再次重试（``forget`` 保持本轮删除结论，
  ``delete_item`` 原样上抛但墓碑有效）。
- **恢复不依赖进程内对象**：待补工作只从持久墓碑重新推导，因此连接或
  module 重建之后仍能补齐。补齐后的可见结果是「条目保持删除、来源记录
  转为已撤回」；已完成或没有来源的条目收敛为 ``ALREADY_WITHDRAWN``／
  ``NO_SOURCE``，重复调用不重写业务结果，也不影响用户新建或编辑的条目。
  恢复入口是公开方法，由调用方（服务层或运维动作）在重启后按账户调用；
  本模块不自行调度，也不新增台账——残留状态本身就可重复识别。

事实身份解析、去重、用户编辑保护与墓碑规则留在原子画像 implementation；
本模块只声明「哪些写入属于同一次提交」与「先墓碑、后撤回存量来源」的顺序。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
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
class ProfileEvidenceFragment:
    """一条跨轮附属证据片段（工单 05；只引用真实来源，绝不伪造）。

    调用方已按来源消息完成定位与主体、否定、时间、条件核验；本对象只
    承载「来源消息标识 + 该消息中真实出现的原话片段」。
    """

    message_id: str
    quote: str


@dataclass(frozen=True)
class ProfileFactSubmission:
    """一条待写入的开放事实提案（唯一写入合同）。

    字段与原子服务的事实写入一一对应：主体/关系/对象/范围由原子服务的
    确定性规则从 ``text`` 解析（``dimension`` 只作解析提示），主题由调用方
    以开放词汇提供（可空、多选、可扩展），证据只引用真实来源，绝不伪造。
    工单 05：``evidence_fragments`` 是跨轮多片段证据，一并并入来源与
    逐来源原话记录。
    """

    text: str
    #: 旧四维抽取提示；帮助关系解析（如裸值「考研」「软件工程专业」）。
    dimension: FourDimension | None = None
    #: 开放主题（可空、多选、可扩展）；不决定事实身份，也不构成写入门槛。
    topics: tuple[str, ...] = ()
    #: 证据把握度档位（沿用旧档位，用于召回门与排序），不改变事实身份。
    confidence: FourDimensionConfidence = FourDimensionConfidence.MEDIUM
    #: 精确来源原话；缺失时为空，不伪造引语。
    evidence_quote: str | None = None
    #: 证据来源消息标识；缺失时条目没有来源定位。
    evidence_message_id: str | None = None
    #: 跨轮附属证据片段（已核验）；通常引用当前消息之前的用户消息。
    evidence_fragments: tuple[ProfileEvidenceFragment, ...] = ()


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
    """开放事实、提取记账与存量来源撤回的提交边界。

    调用方在同一进程内复用同一个实例：``transaction()`` 打开一次提交，
    ``write_facts()``/``write_correction()`` 在其中写入开放事实，
    ``withdraw_item_sources()`` 在墓碑提交之后撤回存量来源。
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
        # 顺序固定：一次提交里承担同行或同败责任的仓库依次并入边界。自动
        # 提取的边界包含记账仓库（``state``）与原子条目仓库；旧四维仓库
        # 不再参与新写入，只在墓碑提交之后承接存量来源撤回。SQLite 仓库
        # 共用连接时只有第一次调用真正 BEGIN，其余并入；内存仓库各自
        # 建立可回滚快照，同一组业务对象一起回滚。并入规则在 adapter
        # 内部（见 ``bridges.profiles.transactions``），这里不判断仓库类型。
        participants: list[ProfileCommitParticipant] = []
        if state is not None:
            participants.append(state)
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

    def write_facts(
        self,
        account_id: str,
        facts: Iterable[ProfileFactSubmission],
        *,
        source_at: datetime | None = None,
    ) -> list[str]:
        """把开放事实直接写入原子条目，返回真正落库的条目标识。

        必须在 :meth:`transaction` 内调用；否则每条事实各自成一次提交。

        ``source_at`` 是来源消息的原始时间，作为「下周/下个月」这类相对
        时间的锚；缺省时按写入当天锚定（前台首次处理仍显式传入）。

        条目未写入是正常结果，不是系统失败：正文为空、用户已把该条目改成
        别的正文时，写入按用户权威跳过；用户已删除（撤回）同一条事实时，
        墓碑抑制复活、同样跳过。调用方的「空结果」因此只表示没有可写事实。
        """

        if self._items is None:
            return []
        item_ids: list[str] = []
        for fact in facts:
            item = self._items.commit_fact(account_id, fact, source_at=source_at)
            if item is not None:
                item_ids.append(item.profile_item_id)
        return list(dict.fromkeys(item_ids))

    def write_correction(
        self,
        account_id: str,
        *,
        dimension: FourDimension,
        content: str,
    ) -> tuple[AtomicProfileItem | None, bool]:
        """聊天纠正直写原子条目：返回 ``(被修正条目, 是否写入)``。

        必须在 :meth:`transaction` 内调用。目标定位（同抽取提示的最近活动
        条目）与用户权威规则留在原子服务，返回语义与旧四维纠正一致：没有
        可修正条目时返回 ``(None, False)``；修正已提交时返回 ``(item, True)``。
        首次处理与后台重试共用这一实现，不会出现只记账、不落事实的分叉。
        """

        if self._items is None:
            return None, False
        return self._items.correct_fact(
            account_id, dimension=dimension, text=content
        )

    def withdraw_item_sources(
        self,
        account_id: str,
        items: Iterable[AtomicProfileItem],
    ) -> list[SourceWithdrawal]:
        """撤回原子条目底层的存量四维来源；逐条隔离、幂等。

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
        且仍指向存量来源记录的条目（见
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
