# mypy: disable-error-code="misc"

"""聊天服务的流式生成方法。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bridges.ai.adapters import StreamEvent
from bridges.ai.model_quota import (
    RUN_MODEL_QUOTA_CONFIG_KEY,
    RunModelQuota,
)
from bridges.chat.budget import load_run_budget
from bridges.chat.repository import GenerationRunRecord, MessageRecord
from bridges.chat.run_budget_ledger import (
    RunBudgetLedgerRepository,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    logger,
)
from bridges.chat.terminal import stopped_outcome
from bridges.contracts.chat import (
    ChatMessageProjection,
    ChatMessageStatus,
    ChatRunStatus,
)
from bridges.contracts.understanding import (
    understanding_from_snapshot,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.study.goal_planning import StudyGoalPlanner
from bridges.study.goal_turn import StudyGoalTurn, StudyGoalTurnService
from bridges.study.goals import StudyGoalRepository
from bridges.study.local_progress import StudyLocalProgressRepository
from bridges.study.versions import STUDY_TUTOR_GRAPH_VERSION

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceStreamMixin:
    def stream_generation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        run_context: RunContextEnvelope,
        until_user_message_id: str | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
        compiled_messages: list[dict[str, str]] | None = None,
        model_id: str | None = None,
        context_budget: dict[str, object] | None = None,
        model_quota: dict[str, object] | None = None,
    ) -> Iterator[StreamEvent]:
        """驱动一次生成（委托给回合编排深模块，接口与语义不变）。

        管线（分层检索、教学证据门、联网/论文搜索、最小画像切片编译与
        披露、提示词组装、流式收敛）由 ``TurnOrchestrator.stream_turn``
        执行（Issue 42 架构加深）；事件经此处原样透传给 API 层。停止
        信号与终态收敛语义不变：用户停止/切换账户导致的客户端断开都会
        把消息收敛到明确终态，绝不留 streaming 僵尸。

        ``compiled_messages``（V2 Issue 03）为日常父图编译的模型就绪
        上下文；传入时模型历史以它为准，未传入回退既有组装。
        ``context_budget`` 是同一编译记录的预算快照（V2 Issue 08）：传入时
        本轮画像块按剩余输入预算裁剪，与编译器共用同一预算口径。
        ``model_quota``（改进工单 03）是本轮启动时锁定的运行额度快照字典，
        与编译器读同一份数据；网关据此记录实际模型与输入额度。
        """
        quota = (
            RunModelQuota.model_validate(model_quota)
            if model_quota is not None
            else None
        )
        # 改进工单 12：正文硬条件（不要联网/只用本地/只查论文）进入生成门禁；
        # 学习模式的强制补搜索同样受该门禁约束，语义扩展不得放宽。
        run = self._repo.get_run_by_message(account_id, assistant_message_id)
        study_tutor = (
            run is not None and run.graph_version == STUDY_TUTOR_GRAPH_VERSION
        )
        persist_study_progress = None
        study_goal_block: str | None = None
        if study_tutor and run is not None:
            user_message = self._repo.get_message(account_id, run.user_message_id)
            if user_message is not None:
                progress_repository = StudyLocalProgressRepository(self._repo.database)
                # Issue 07：只有新系统目标或确需修订的回合才调用规划角色；
                # 规划失败/普通提问返回 None，行为与 02 导师一致。
                goal_service, goal_turn = self._plan_study_goal_turn(
                    run, user_message, compiled_messages
                )
                if goal_turn is not None:
                    study_goal_block = goal_turn.prompt_block or None

                def persist_study_progress(content: str) -> None:
                    progress_repository.save_completed_turn_in_transaction(
                        self._repo, run, user_message, content
                    )
                    if goal_turn is not None:
                        # 同一终态事务：目标/路线与消息 done、局部进展一起提交。
                        goal_service.commit(goal_turn, run, user_message)

        understanding = understanding_from_snapshot(
            (run.config or {}).get("understanding") if run is not None else None
        )
        web_search_allowed = (
            understanding is None or not understanding.blocks_network
        )
        if understanding is not None and understanding.blocks_knowledge_base:
            use_knowledge_base = False
        yield from self._turn.stream_turn(
            account_id,
            conversation_id,
            assistant_message_id,
            run_context,
            until_user_message_id=until_user_message_id,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
            gateway=self._gateway,
            compiled_messages=compiled_messages,
            model_override=model_id,
            model_quota=quota,
            context_budget=context_budget,
            web_search_allowed=web_search_allowed,
            study_tutor=study_tutor,
            study_goal_block=study_goal_block,
            persist_study_progress=persist_study_progress,
        )
        # 改进工单 17：不在执行器后台链路上的直接编排（测试/脚本）同样
        # 只在回答正常完成后登记普通画像提取；调度幂等，后台执行器路径
        # 由执行器自行登记也不会重复计数。
        self._schedule_profile_extraction_after_turn(
            account_id, assistant_message_id
        )

    def _plan_study_goal_turn(
        self: ChatService,
        run: GenerationRunRecord,
        user_message: MessageRecord,
        compiled_messages: list[dict[str, str]] | None,
    ) -> tuple[StudyGoalTurnService, StudyGoalTurn | None]:
        """按已读取的目标/路线状态决定是否调用规划角色（Issue 07）。

        规划调用经统一网关与运行预算账本计数（与 02 导师同一账本）；
        规划失败返回 None，由调用方按导师正常教学并如实降级。
        """

        service = StudyGoalTurnService(
            StudyGoalRepository(self._repo.database),
            StudyGoalPlanner(self._gateway),
        )
        quota = RunModelQuota.from_config(
            (run.config or {}).get(RUN_MODEL_QUOTA_CONFIG_KEY)
        )
        budget = load_run_budget(
            self._repo, run.account_id, run.run_id, mode_hint="study"
        )
        turn = service.plan_for_turn(
            run=run,
            user_message=user_message,
            compiled_messages=compiled_messages,
            model_quota=quota,
            budget=budget,
        )
        return service, turn

    def _schedule_profile_extraction_after_turn(
        self: ChatService, account_id: str, assistant_message_id: str
    ) -> None:
        """回答正常完成后登记普通画像提取任务（改进工单 17）。"""

        if self._automatic_profiles is None:
            return
        message = self._repo.get_message(account_id, assistant_message_id)
        if message is None or message.status != ChatMessageStatus.DONE:
            return
        run = self._repo.get_run_by_message(account_id, assistant_message_id)
        if run is None:
            return
        user_message = self._repo.get_message(account_id, run.user_message_id)
        if user_message is None:
            return
        try:
            self._automatic_profiles.schedule_message_extraction(
                account_id,
                conversation_id=run.conversation_id,
                message_id=run.user_message_id,
                content=user_message.content,
                run_id=run.run_id,
            )
        except Exception:  # noqa: BLE001 - 辅助入队不终止生成流
            logger.exception("画像提取任务登记失败：run_id=%s", run.run_id)
    def stop_generation(
        self: ChatService, account_id: str, conversation_id: str, message_id: str
    ) -> ChatMessageProjection:
        """停止进行中的生成（显式操作）；幂等，已终态直接返回当前状态。

        Issue 02：停止写入运行表的 stop_requested（跨进程真相源），并
        设置同进程停止信号；后台执行器在安全检查点收敛为 stopped。本
        方法等待执行器收敛（≤4 秒）；无执行器运行（测试环境/执行器被
        长模型调用阻塞）时由生成终态 module 兜底收敛——消息、终态事件
        与运行一次到位，订阅端不因执行器缺位悬挂。终态由原子守卫保证
        唯一写入，绝不会被执行器迟到的终态改写成 done。
        """
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if message.status != ChatMessageStatus.STREAMING:
            return self._project_message(message)

        with self._repo.connection_lock():
            run = self._repo.get_run_by_message(account_id, message_id)
        if run is not None and run.status in {
            ChatRunStatus.QUEUED.value,
            ChatRunStatus.RUNNING.value,
        }:
            self._repo.request_generation_stop(account_id, run.run_id)
            RunBudgetLedgerRepository(self._repo.database).close(
                account_id=account_id, run_id=run.run_id, now=datetime.now(UTC)
            )
            # 改进工单 12：停止当前运行即暂停当前任务（可续接，不清条件）。
            self._pause_task_for_stop(
                account_id=account_id,
                conversation_id=conversation_id,
                source_message_id=message_id,
            )
            entry = self._lifecycle.signal_and_started(message_id)
            if entry is not None:
                entry[0].set()
            # 等待执行器在安全检查点收敛；终态后直接返回（含真实耗时）。
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                current = self._repo.get_message(account_id, message_id)
                if current is None or current.status != ChatMessageStatus.STREAMING:
                    if current is not None:
                        self._lifecycle.unregister(message_id)
                        return self._project_message(current)
                    break
                time.sleep(0.1)
            # 超时：执行器不存在或仍被长调用阻塞——按既有语义兜底收敛，
            # 执行器下个安全检查点发现消息已终态后停止并收敛运行。
            message = self._repo.get_message(account_id, message_id)
            if message is None:
                raise ChatDomainError(
                    "message_not_found", "消息不存在或没有访问权限。", 404
                )

        entry = self._lifecycle.signal_and_started(message_id)
        stop_event = entry[0] if entry is not None else None
        started = entry[1] if entry is not None else None
        if stop_event is not None:
            stop_event.set()
        # 生成线程尚未启动（或跨进程遗留消息）时按创建时间估算耗时；
        # 已启动时按真实单调起点计算——耗时随终态写入消息记录。
        if started is None:
            duration_ms = max(
                1, int((datetime.now(UTC) - message.created_at).total_seconds() * 1000)
            )
        else:
            duration_ms = max(1, int((time.monotonic() - started) * 1000))
        # 先收敛状态再注销活跃标记：避免并发读取在两者之间把仍处于
        # streaming 的消息误判为陈旧中断（终态由原子守卫保证单一写入）。
        # 思考摘要与搜索/教学卡片的取消投影由终态 module 统一派生；
        # run_id 传 None（无运行记录，如直接编排的生成）只收敛消息。
        stopped_run = self._repo.get_run_by_message(account_id, message_id)
        self.terminal.converge(
            account_id,
            stopped_run.run_id if stopped_run is not None else None,
            message_id,
            fallback=stopped_outcome(duration_ms=duration_ms),
        )
        if stopped_run is not None:
            # 改进工单 09：用户停止即关闭本次运行预算账本——此后任何路径
            # 都不再有新调用（明确继续/重试会创建新运行、新账本）。
            RunBudgetLedgerRepository(self._repo.database).close(
                account_id=account_id,
                run_id=stopped_run.run_id,
                now=datetime.now(UTC),
            )
        self._lifecycle.unregister(message_id)
        finalized = self._repo.get_message(account_id, message_id)
        if finalized is None:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        return self._project_message(finalized)
    def stop_account_generations(self: ChatService, account_id: str) -> int:
        """停止该账户全部进行中的生成（账户删除编排调用）。

        Issue 02：先向全部非终态运行写入 stop_requested（跨进程真相
        源），再对仍处于 streaming 的消息逐个发同进程停止信号；后台
        执行器在下次收敛点按既有终止语义收尾。返回停止的消息数量。
        """
        self._repo.request_stop_account_runs(account_id, datetime.now(UTC))
        for run in self._repo.database.scoped(account_id).execute(
            "SELECT run_id FROM run_budget_ledger WHERE account_id = ? AND status != 'closed'",
            (account_id,),
        ).fetchall():
            RunBudgetLedgerRepository(self._repo.database).close(
                account_id=account_id, run_id=str(run["run_id"]), now=datetime.now(UTC)
            )
        stopped = 0
        for conversation in self._repo.list_conversations(account_id):
            for message in self._repo.list_messages(account_id, conversation.conversation_id):
                if message.status != ChatMessageStatus.STREAMING:
                    continue
                entry = self._lifecycle.signal_and_started(message.message_id)
                if entry is not None:
                    entry[0].set()
                stopped += 1
        return stopped
