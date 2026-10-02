"""有界历史摘要缓存：生成、复用、失效与后台准备（改进工单 13）。

``docs/上下文工程/改进方案.md`` §2 与决策 4/5 的落地：

- **按覆盖片段缓存**：一条 :class:`~bridges.contracts.summaries.HistorySummary`
  覆盖一段连续、已完成且有明确边界的原始消息；来源指纹（消息 ID + 角色 +
  正文的确定性哈希）随实例保存，任一条来源被删除或修改即失配失效。
- **重建回到原文**：新片段只从原始消息生成；已有有效缓存直接复用，绝不把
  上一版摘要再喂给模型，避免逐版压缩积累偏差。
- **后台准备为主、限时补齐为辅**：回答后按预算压力与新原文量把任务放入既有
  ``TaskQueue``（账户/会话隔离由载荷与仓库强制）；缓存缺失且确需压缩时才在
  编译期做**一次**限时同步补齐，超时/失败不阻塞当前请求，保留预算允许的
  原文与明确缺口。
- **失败不写猜测**：生成输出经确定性校验（长度上限、数字与否定必须在原文
  中出现）；校验失败只记审计与缺口，不落缓存。来源在生成期间被删除/修改时
  保存守卫直接丢弃该实例。

本模块只拥有摘要任务/缓存/实例版本；有效任务条件归工单 08，原文解析归
工单 11，事实抑制由工单 18 接入（本票提供 ``invalidate_conversation`` 接缝）。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from bridges.ai.model_gateway import ModelGateway
from bridges.ai.model_quota import RunModelQuota, build_run_model_quota
from bridges.ai.payload_budget import TOKEN_ESTIMATE_VERSION
from bridges.ai.ports import ModelRunLockRecorder
from bridges.ai.run_model_config import (
    RunModelConfigProvider,
    factory_run_model_config,
)
from bridges.chat.budget import RunBudget
from bridges.chat.context_compiler import CONTEXT_BUDGET_VERSION, estimate_tokens
from bridges.chat.repository import ConversationRepository, MessageRecord
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.contracts.ai import (
    BusinessRef,
    CallContractVersions,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.summaries import (
    SUMMARY_BACKGROUND_TIMEOUT_MS,
    SUMMARY_CONTRACT_VERSION,
    SUMMARY_GENERATOR_VERSION,
    SUMMARY_ITEM_MAX_CHARS,
    SUMMARY_MAX_ATTEMPTS,
    SUMMARY_MAX_CALLS_PER_TASK,
    SUMMARY_MAX_OBJECT_CLUES,
    SUMMARY_MAX_OPEN_QUESTIONS,
    SUMMARY_MAX_SOURCE_TOKENS,
    SUMMARY_MIN_NEW_MESSAGES,
    SUMMARY_OUTPUT_TOKENS,
    SUMMARY_SYNC_TIMEOUT_MS,
    SUMMARY_TEXT_MAX_CHARS,
    HistorySummary,
    SummarySourceMessage,
    SummaryStatus,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.runtime.queue import (
    Claim,
    RetryKind,
    TaskQueue,
)
from bridges.storage.database import BridgesDatabase

#: 后台摘要队列名（复用 Issue 43 统一领取契约与租约恢复）。
SUMMARY_QUEUE = "chat_summary"
#: 摘要任务租约（秒）：长于单次模型调用，短于可接受恢复窗口。
SUMMARY_LEASE_SECONDS = 120.0
#: 摘要生成调用的能力名（固定结构化输出；模型由运行配置解析并记录在锁里）。
SUMMARY_CAPABILITY_NAME = "qwen_structured_output"
SUMMARY_CAPABILITY_VERSION = "1"


class SummaryUnavailableError(Exception):
    """摘要生成不可用（能力/额度/网络）；``retryable`` 决定后台是否重排。"""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class SummaryRejectedError(Exception):
    """摘要输出未通过确定性校验（不写入缓存，不当作依据）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def source_fingerprint(messages: Sequence[SummarySourceMessage]) -> str:
    """覆盖范围原文的确定性指纹（ID + 角色 + 正文；顺序敏感）。"""
    payload = json.dumps(
        [[item.message_id, item.role, item.content] for item in messages],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint_records(messages: Sequence[MessageRecord]) -> str:
    return source_fingerprint(
        [_message_record_source(item) for item in messages]
    )


def _message_record_source(item: MessageRecord) -> SummarySourceMessage:
    return SummarySourceMessage(
        message_id=item.message_id,
        role=item.role.value,
        content=item.content,
    )


_NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])[0-9]+(?:\.[0-9]+)?%?(?![A-Za-z0-9_.])")
_NEGATION_MARKERS = ("不", "没", "未", "无", "别", "禁止", "取消", "撤销")


class SummaryDraft:
    """通过校验的摘要草稿（纯内存值对象）。"""

    __slots__ = ("object_clues", "open_questions", "text")

    def __init__(
        self,
        *,
        text: str,
        object_clues: Sequence[str],
        open_questions: Sequence[str],
    ) -> None:
        self.text = text
        self.object_clues = tuple(object_clues)
        self.open_questions = tuple(open_questions)


def validate_summary_output(output: dict[str, Any], source_text: str) -> SummaryDraft:
    """对模型输出执行确定性校验；不通过抛 :class:`SummaryRejectedError`。

    校验规则（摘要只是背景/定位线索）：

    1. 结构：``summary`` 非空字符串，``object_clues`` / ``open_questions``
       为字符串列表；长度与条数有上限。
    2. 数值保真：生成内容中的每个数字串必须在来源原文中出现——捏造数值
       直接拒绝。
    3. 否定保真：生成内容出现的否定词若在来源原文中完全不存在，视为改写
       语义，拒绝。该规则保守（宁可少缓存，不可写错）。
    """

    if not isinstance(output, dict):
        raise SummaryRejectedError("输出不是 JSON 对象。")
    text = output.get("summary")
    if not isinstance(text, str) or not text.strip():
        raise SummaryRejectedError("summary 字段为空。")
    text = " ".join(text.split())
    if len(text) > SUMMARY_TEXT_MAX_CHARS:
        raise SummaryRejectedError("summary 超出长度上限。")
    raw_clues = output.get("object_clues", [])
    raw_questions = output.get("open_questions", [])
    if not isinstance(raw_clues, list) or not isinstance(raw_questions, list):
        raise SummaryRejectedError("线索/开放问题字段不是列表。")
    clues = _validate_items(raw_clues, SUMMARY_MAX_OBJECT_CLUES, "对象线索")
    questions = _validate_items(
        raw_questions, SUMMARY_MAX_OPEN_QUESTIONS, "开放问题"
    )
    generated = "\n".join([text, *clues, *questions])
    source_numbers = set(_NUMERIC_TOKEN_RE.findall(source_text))
    for token in _NUMERIC_TOKEN_RE.findall(generated):
        if token not in source_numbers:
            raise SummaryRejectedError(f"生成内容出现来源中没有的数值：{token}")
    # 否定保真：生成内容出现否定词、而来源完全没有否定语义时视为改写，拒绝。
    generated_negated = any(marker in generated for marker in _NEGATION_MARKERS)
    source_negated = any(marker in source_text for marker in _NEGATION_MARKERS)
    if generated_negated and not source_negated:
        raise SummaryRejectedError("生成内容出现来源中没有的否定语义。")
    if source_negated and not generated_negated:
        raise SummaryRejectedError("生成内容遗漏来源中的否定语义。")
    return SummaryDraft(text=text, object_clues=clues, open_questions=questions)


def _validate_items(
    items: Sequence[Any], limit: int, label: str
) -> list[str]:
    if len(items) > limit:
        raise SummaryRejectedError(f"{label}条数超出上限。")
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise SummaryRejectedError(f"{label}含非法条目。")
        compact = " ".join(item.split())
        if len(compact) > SUMMARY_ITEM_MAX_CHARS:
            raise SummaryRejectedError(f"{label}单条超出长度上限。")
        cleaned.append(compact)
    return cleaned


class SummaryExtraction:
    """一次摘要抽取的结果（输出 + 可取得的用量计数）。"""

    __slots__ = ("input_tokens", "output", "output_tokens")

    def __init__(
        self,
        *,
        output: dict[str, Any],
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.output = output
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class SummaryExtractor(Protocol):
    """摘要模型调用的可替换端口（生产用网关，测试用确定性替身）。"""

    version: str

    def extract(
        self,
        *,
        run_context: RunContextEnvelope,
        sources: Sequence[SummarySourceMessage],
        timeout_ms: int,
    ) -> SummaryExtraction: ...


class GatewaySummaryExtractor:
    """经固定结构化能力调用一次摘要生成（每次调用有独立超时与版本合同）。"""

    version = SUMMARY_GENERATOR_VERSION

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        model_config_provider: RunModelConfigProvider | None = None,
        lock_recorder: ModelRunLockRecorder | None = None,
        database: BridgesDatabase | None = None,
    ) -> None:
        self._gateway = gateway
        self._model_config_provider = model_config_provider
        self._lock_recorder = lock_recorder
        self._database = database

    def _resolve_quota(self) -> RunModelQuota:
        snapshot = (
            self._model_config_provider.snapshot()
            if self._model_config_provider is not None
            else None
        )
        return build_run_model_quota(snapshot or factory_run_model_config())

    def extract(
        self,
        *,
        run_context: RunContextEnvelope,
        sources: Sequence[SummarySourceMessage],
        timeout_ms: int,
    ) -> SummaryExtraction:
        quota = self._resolve_quota()
        if not quota.is_verified:
            raise SummaryUnavailableError(
                "summary_quota_unverified",
                "摘要调用的模型额度未经验证。",
                retryable=False,
            )
        budget = RunBudget(run_context.run_id, total_ms=max(1, timeout_ms))
        if self._database is not None and run_context.workflow_name == "history-summary-sync":
            ledger = RunBudgetLedgerRepository(self._database)
            snapshot = ledger.load(run_context.account_id, run_context.run_id)
            if snapshot is None or not snapshot.active:
                raise SummaryUnavailableError(
                    "summary_run_inactive", "主运行预算不可用。", retryable=False
                )
            budget = RunBudget(
                run_context.run_id,
                deadline_utc=min(
                    datetime.now(UTC) + timedelta(milliseconds=timeout_ms),
                    snapshot.plan.deadline_at - timedelta(
                        milliseconds=snapshot.plan.verify_deliver_reserve_ms
                    ),
                ),
                ledger=ledger,
                account_id=run_context.account_id,
            )
        payload = {
            "messages": [
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": _render_sources(sources)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": SUMMARY_OUTPUT_TOKENS,
        }
        result = self._gateway.invoke(
            SUMMARY_CAPABILITY_NAME,
            SUMMARY_CAPABILITY_VERSION,
            run_context,
            payload,
            budget=budget,
            model_quota=quota,
            call_contract=CallContractVersions(
                prompt_version=SUMMARY_GENERATOR_VERSION,
                input_schema_version="summary-source-v1",
                output_schema_version="summary-json-v1",
                recipe_version="summary-cache-v1",
                context_compile_version=CONTEXT_BUDGET_VERSION,
                quality_policy_version="summary-validation-v1",
                estimate_version=TOKEN_ESTIMATE_VERSION,
                quota_version=quota.quota_version,
            ),
        )
        self._record_lock(result.lock, run_context)
        if result.status in (ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED):
            output = result.output if isinstance(result.output, dict) else None
            if output is None:
                raise SummaryUnavailableError(
                    "summary_output_invalid",
                    "摘要调用没有返回结构化对象。",
                    retryable=False,
                )
            usage = result.lock.usage if result.lock is not None else None
            return SummaryExtraction(
                output=output,
                input_tokens=usage.get("prompt_tokens") if usage is not None else None,
                output_tokens=(
                    usage.get("completion_tokens") if usage is not None else None
                ),
            )
        retryable = result.status == ModelCallStatus.RETRYABLE_FAIL
        raise SummaryUnavailableError(
            result.error_code or "summary_call_failed",
            result.error_message or "摘要调用失败。",
            retryable=retryable,
        )

    def _record_lock(
        self, lock: ModelRunLock | None, run_context: RunContextEnvelope
    ) -> None:
        """每次真实调用（含失败）的不可变锁交给记录器（若有）。"""
        if lock is None or self._lock_recorder is None:
            return
        self._lock_recorder.record(
            lock,
            business_ref=BusinessRef(
                object_type="conversation",
                object_id=run_context.project_id,
                operation="summarize_history",
                is_primary=True,
            ),
        )


class ConversationSummaryRepository:
    """``conversation_summaries`` 表的属主仓库（chat/ 域，账户隔离）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    # -- 读取 -------------------------------------------------------------

    def list_active(
        self, account_id: str, conversation_id: str
    ) -> list[HistorySummary]:
        """返回仍有效的摘要实例（按生成时间序）。"""
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM conversation_summaries"
            " WHERE account_id = ? AND conversation_id = ? AND status = ?"
            " ORDER BY created_at, summary_id",
            (account_id, conversation_id, SummaryStatus.ACTIVE.value),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    # -- 写入 -------------------------------------------------------------

    def generation(self, account_id: str, conversation_id: str) -> int:
        """持久失效代次，阻止失效发生前启动的生成回写。"""
        row = self._db.scoped(account_id).execute(
            "SELECT generation FROM conversation_summary_generations"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        ).fetchone()
        return int(row["generation"]) if row is not None else 0

    def reserve_sync_attempt(self, account_id: str, conversation_id: str, run_id: str) -> bool:
        """每个运行只取得一次同步补齐机会；与停止/冻结预算在同一事务检查。"""
        ledger = RunBudgetLedgerRepository(self._db)
        with self._db.transaction():
            snapshot = ledger.load(account_id, run_id)
            if snapshot is None or not snapshot.active or not ledger.can_wait_until(
                account_id, run_id, datetime.now(UTC)
            ):
                return False
            return self._db.scoped(account_id).execute(
                "INSERT OR IGNORE INTO conversation_summary_sync_attempts"
                " (account_id, conversation_id, run_id) VALUES (?, ?, ?)",
                (account_id, conversation_id, run_id),
            ).rowcount == 1

    def save_if_sources_unchanged(
        self, summary: HistorySummary, *, expected_generation: int = 0,
        active_run_id: str | None = None,
    ) -> bool:
        """来源未变时保存并失效重叠实例；来源已变/被删则丢弃（取消守卫）。

        保存与「重读来源 + 指纹核对 + 重叠失效」在同一事务内：生成期间
        来源被修改、会话被删除或并发实例写入都不会产生过期/重叠依据。
        """
        with self._db.transaction():
            if active_run_id is not None:
                snapshot = RunBudgetLedgerRepository(self._db).load(
                    summary.account_id, active_run_id
                )
                run = ConversationRepository(self._db).get_generation_run(
                    summary.account_id, active_run_id
                )
                if (snapshot is not None and not snapshot.active) or (
                    run is not None
                    and (run.stop_requested or run.status not in {"queued", "running"})
                ):
                    return False
            if self.generation(summary.account_id, summary.conversation_id) != expected_generation:
                return False
            records = self._message_slice(
                summary.account_id,
                summary.conversation_id,
                summary.covered_first_message_id,
                summary.covered_last_message_id,
            )
            if records is None:
                return False
            if len(records) != summary.covered_message_count:
                return False
            if source_fingerprint(records) != summary.source_fingerprint:
                return False
            message_order = self._message_order(
                summary.account_id, summary.conversation_id
            )
            order_index = {message_id: index for index, message_id in enumerate(message_order)}
            first = order_index.get(summary.covered_first_message_id)
            last = order_index.get(summary.covered_last_message_id)
            if first is None or last is None:
                return False
            overlapping: list[str] = []
            for existing in self.list_active(
                summary.account_id, summary.conversation_id
            ):
                if existing.summary_id == summary.summary_id:
                    continue
                e_first = order_index.get(existing.covered_first_message_id)
                e_last = order_index.get(existing.covered_last_message_id)
                if e_first is None or e_last is None:
                    overlapping.append(existing.summary_id)
                    continue
                if e_first <= last and e_last >= first:
                    overlapping.append(existing.summary_id)
            if overlapping:
                self._invalidate_locked(
                    summary.account_id,
                    summary.conversation_id,
                    overlapping,
                    reason="superseded_by_overlap",
                    now=summary.created_at,
                )
            self._insert_locked(summary)
        return True

    def invalidate(
        self,
        account_id: str,
        conversation_id: str,
        summary_ids: Sequence[str],
        *,
        reason: str,
        now: datetime,
    ) -> int:
        """失效指定实例（保留行供审计，不再复用）。返回影响行数。"""
        if not summary_ids:
            return 0
        with self._db.transaction():
            return self._invalidate_locked(
                account_id, conversation_id, list(summary_ids), reason=reason, now=now
            )

    def invalidate_conversation(
        self,
        account_id: str,
        conversation_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> int:
        """失效会话的全部有效实例（事实抑制/来源整体失效接缝）。"""
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "INSERT INTO conversation_summary_generations"
                " (account_id, conversation_id, generation)"
                " SELECT ?, ?, 1 WHERE EXISTS"
                " (SELECT 1 FROM conversations WHERE account_id = ? AND conversation_id = ?)"
                " ON CONFLICT(account_id, conversation_id) DO UPDATE"
                " SET generation = generation + 1",
                (account_id, conversation_id, account_id, conversation_id),
            )
            rows = self._db.scoped(account_id).execute(
                "SELECT summary_id FROM conversation_summaries"
                " WHERE account_id = ? AND conversation_id = ? AND status = ?",
                (account_id, conversation_id, SummaryStatus.ACTIVE.value),
            ).fetchall()
            summary_ids = [str(row["summary_id"]) for row in rows]
            if not summary_ids:
                return 0
            return self._invalidate_locked(
                account_id,
                conversation_id,
                summary_ids,
                reason=reason,
                now=now,
            )

    def delete_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> int:
        """删除会话全部摘要（会话删除级联；在事务内调用）。"""
        cursor = self._db.scoped(account_id).execute(
            "DELETE FROM conversation_summaries"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
        self._db.scoped(account_id).execute(
            "DELETE FROM conversation_summary_sync_attempts"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
        self._db.scoped(account_id).execute(
            "DELETE FROM conversation_summary_generations"
            " WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
        return cursor.rowcount

    # -- 内部 -------------------------------------------------------------

    def _invalidate_locked(
        self,
        account_id: str,
        conversation_id: str,
        summary_ids: Sequence[str],
        *,
        reason: str,
        now: datetime,
    ) -> int:
        placeholders = ",".join("?" for _ in summary_ids)
        cursor = self._db.scoped(account_id).execute(
            "UPDATE conversation_summaries SET status = ?,"
            " invalidated_reason = ?, invalidated_at = ?, updated_at = ?"
            " WHERE account_id = ? AND conversation_id = ? AND status = ?"
            f" AND summary_id IN ({placeholders})",
            (
                SummaryStatus.INVALIDATED.value,
                reason,
                _iso(now),
                _iso(now),
                account_id,
                conversation_id,
                SummaryStatus.ACTIVE.value,
                *summary_ids,
            ),
        )
        return cursor.rowcount

    def _insert_locked(self, summary: HistorySummary) -> None:
        self._db.scoped(summary.account_id).execute(
            "INSERT INTO conversation_summaries"
            "(summary_id, account_id, conversation_id, contract_version,"
            " instance_version, covered_first_message_id, covered_last_message_id,"
            " covered_message_count, source_fingerprint, summary_text,"
            " object_clues_json, open_questions_json, status, invalidated_reason,"
            " invalidated_at, input_tokens, output_tokens, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary.summary_id,
                summary.account_id,
                summary.conversation_id,
                summary.contract_version,
                summary.instance_version,
                summary.covered_first_message_id,
                summary.covered_last_message_id,
                summary.covered_message_count,
                summary.source_fingerprint,
                summary.text,
                json.dumps(summary.object_clues, ensure_ascii=False),
                json.dumps(summary.open_questions, ensure_ascii=False),
                summary.status.value,
                summary.invalidated_reason,
                _iso(summary.invalidated_at) if summary.invalidated_at else None,
                summary.input_tokens,
                summary.output_tokens,
                _iso(summary.created_at),
                _iso(summary.updated_at),
            ),
        )

    def _message_order(
        self, account_id: str, conversation_id: str
    ) -> list[str]:
        rows = self._db.scoped(account_id).execute(
            "SELECT message_id FROM messages"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END,"
            " attempt_number, message_id",
            (account_id, conversation_id),
        ).fetchall()
        return [str(row["message_id"]) for row in rows]

    def _message_slice(
        self,
        account_id: str,
        conversation_id: str,
        first_message_id: str,
        last_message_id: str,
    ) -> list[SummarySourceMessage] | None:
        """按会话仓库同一顺序取覆盖范围原文（含被跳过消息，供指纹守卫）。"""
        rows = self._db.scoped(account_id).execute(
            "SELECT message_id, role, content FROM messages"
            " WHERE account_id = ? AND conversation_id = ?"
            " ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END,"
            " attempt_number, message_id",
            (account_id, conversation_id),
        ).fetchall()
        messages = [
            SummarySourceMessage(
                message_id=str(row["message_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
            )
            for row in rows
        ]
        index = {message.message_id: position for position, message in enumerate(messages)}
        first = index.get(first_message_id)
        last = index.get(last_message_id)
        if first is None or last is None or first > last:
            return None
        return messages[first : last + 1]


class ChatSummaryService:
    """摘要缓存、后台准备与限时补齐的唯一入口（账户/会话隔离）。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        conversation_repository: ConversationRepository,
        extractor: SummaryExtractor,
        observability: ObservabilityService | None = None,
        repository: ConversationSummaryRepository | None = None,
        queue: TaskQueue | None = None,
    ) -> None:
        self._db = database
        self._conversations = conversation_repository
        self._extractor = extractor
        self._observability = observability
        self._repository = repository or ConversationSummaryRepository(database)
        self._queue = queue or TaskQueue(database)
        self._queue.set_lease_seconds(SUMMARY_QUEUE, SUMMARY_LEASE_SECONDS)

    # ------------------------------------------------------------------
    # 读取与失效
    # ------------------------------------------------------------------

    def valid_summaries(
        self,
        account_id: str,
        conversation_id: str,
        *,
        messages: Sequence[MessageRecord] | None = None,
    ) -> list[HistorySummary]:
        """返回当前仍可复用的实例；失配的实例就地失效（不删行）。

        失效依据：生成口径变化、合同版本变化、来源边界缺失/条数变化或
        指纹失配（来源被删除、修改）。恢复后的下一轮不会复用旧依据。
        """
        records = (
            list(messages)
            if messages is not None
            else self._conversations.list_messages(account_id, conversation_id)
        )
        order_index = {
            record.message_id: index for index, record in enumerate(records)
        }
        active = self._repository.list_active(account_id, conversation_id)
        valid: list[HistorySummary] = []
        stale: list[tuple[HistorySummary, str]] = []
        for item in active:
            reason = self._invalidity(item, records, order_index)
            if reason is None:
                valid.append(item)
            else:
                stale.append((item, reason))
        if stale:
            now = datetime.now(UTC)
            self._repository.invalidate(
                account_id,
                conversation_id,
                [item.summary_id for item, _ in stale],
                reason="source_or_version_changed",
                now=now,
            )
            for item, reason in stale:
                self._audit(
                    account_id,
                    AuditAction.HISTORY_SUMMARY_INVALIDATED,
                    AuditResult.BLOCKED,
                    details={
                        "summary_id": item.summary_id,
                        "reason": reason,
                        "covered_first_message_id": item.covered_first_message_id,
                        "covered_last_message_id": item.covered_last_message_id,
                    },
                )
        return valid

    def invalidate_conversation(
        self, account_id: str, conversation_id: str, *, reason: str
    ) -> int:
        """失效会话全部有效实例（事实抑制/来源整体失效接缝，工单 18 接入）。"""
        count = self._repository.invalidate_conversation(
            account_id, conversation_id, reason=reason, now=datetime.now(UTC)
        )
        if count:
            self._audit(
                account_id,
                AuditAction.HISTORY_SUMMARY_INVALIDATED,
                AuditResult.BLOCKED,
                details={"reason": reason, "invalidated_count": count},
            )
        return count

    def _invalidity(
        self,
        item: HistorySummary,
        records: Sequence[MessageRecord],
        order_index: dict[str, int],
    ) -> str | None:
        if item.contract_version != SUMMARY_CONTRACT_VERSION:
            return "contract_version_changed"
        if item.instance_version != self._extractor.version:
            return "generator_version_changed"
        first = order_index.get(item.covered_first_message_id)
        last = order_index.get(item.covered_last_message_id)
        if first is None or last is None or first > last:
            return "source_range_missing"
        covered = records[first : last + 1]
        if len(covered) != item.covered_message_count:
            return "source_count_changed"
        if _fingerprint_records(covered) != item.source_fingerprint:
            return "source_changed"
        return None

    # ------------------------------------------------------------------
    # 后台准备（回答后）
    # ------------------------------------------------------------------

    def schedule_background(
        self,
        account_id: str,
        conversation_id: str,
        *,
        boundary_message_id: str,
    ) -> None:
        """按预算压力与新原文量登记后台准备；短对话/已覆盖不调用模型。"""
        records = self._conversations.list_messages(account_id, conversation_id)
        valid = self.valid_summaries(
            account_id, conversation_id, messages=records
        )
        order_index = {
            record.message_id: index for index, record in enumerate(records)
        }
        boundary = order_index.get(boundary_message_id)
        if boundary is None:
            return
        first_missing = self._first_missing(valid, order_index)
        if boundary - first_missing + 1 < SUMMARY_MIN_NEW_MESSAGES:
            return
        fingerprint = _fingerprint_records(records[: boundary + 1])
        generation = self._repository.generation(account_id, conversation_id)
        # 同一来源边界的重登记不能清零已经耗尽的重试额度。
        existing = self._db.connection.execute(
            "SELECT payload_json, status FROM task_claims WHERE queue_name = ? AND task_key = ?",
            (SUMMARY_QUEUE, conversation_id),
        ).fetchone()
        if existing is not None:
            previous = json.loads(existing["payload_json"] or "{}")
            if (
                existing["status"] != "completed"
                and previous.get("boundary_message_id") == boundary_message_id
                and previous.get("source_fingerprint") == fingerprint
                and previous.get("generation") == generation
            ):
                return
        self._queue.enqueue(
            SUMMARY_QUEUE,
            conversation_id,
            payload={
                "account_id": account_id,
                "boundary_message_id": boundary_message_id,
                "source_fingerprint": fingerprint,
                "generation": generation,
            },
        )

    def prepare_sync(
        self,
        account_id: str,
        conversation_id: str,
        *,
        boundary_message_id: str,
        run_id: str,
    ) -> list[HistorySummary]:
        """缓存缺失且确需压缩时的**一次**限时同步补齐；失败返回空列表。"""
        try:
            ledger = RunBudgetLedgerRepository(self._db)
            if ledger.load(account_id, run_id) is not None and not (
                self._repository.reserve_sync_attempt(account_id, conversation_id, run_id)
            ):
                return []
            return self._prepare(
                account_id,
                conversation_id,
                boundary_message_id=boundary_message_id,
                timeout_ms=SUMMARY_SYNC_TIMEOUT_MS,
                max_calls=1,
                run_id=run_id,
                synchronous=True,
            )
        except Exception as exc:  # noqa: BLE001 - 补齐失败不阻塞当前请求
            self._audit(
                account_id,
                AuditAction.HISTORY_SUMMARY_PREPARED,
                AuditResult.RETRYABLE_FAIL,
                details={
                    "reason": _error_reason(exc),
                    "boundary_message_id": boundary_message_id,
                    "mode": "sync",
                },
            )
            return []

    def run_tick(self) -> str:
        """后台执行一件摘要准备任务（由生成执行器每轮调用一次）。"""
        claim = self._queue.claim_next(SUMMARY_QUEUE, "chat-summary")
        if claim is None:
            return "chat-summary: 无待处理任务。"
        return self._run_claim(claim)

    @property
    def pending_count(self) -> int:
        """待处理摘要任务数（测试与运维观测）。"""
        return self._queue.pending_count(SUMMARY_QUEUE)

    def _run_claim(self, claim: Claim) -> str:
        payload = claim.payload or {}
        account_id = payload.get("account_id")
        boundary = payload.get("boundary_message_id")
        conversation_id = claim.task_key
        if not account_id or not boundary:
            self._queue.complete(claim)
            return "chat-summary: 队列载荷缺边界，已跳过。"
        try:
            prepared = self._prepare(
                str(account_id),
                conversation_id,
                boundary_message_id=str(boundary),
                timeout_ms=SUMMARY_BACKGROUND_TIMEOUT_MS,
                max_calls=SUMMARY_MAX_CALLS_PER_TASK,
                run_id=claim.claim_id,
            )
        except SummaryUnavailableError as exc:
            if exc.retryable:
                exhausted = self._queue.requeue(
                    claim,
                    retry_kind=RetryKind.EXPONENTIAL,
                    reason=exc.code,
                    max_attempts=SUMMARY_MAX_ATTEMPTS,
                )
            else:
                self._queue.fail(claim, exc.code)
                exhausted = True
            if exhausted:
                self._audit(
                    str(account_id),
                    AuditAction.HISTORY_SUMMARY_PREPARED,
                    AuditResult.BLOCKED,
                    details={"reason": exc.code, "mode": "background"},
                )
            return f"chat-summary: {exc.code}。"
        except Exception as exc:  # noqa: BLE001 - 单任务异常按退避重排
            exhausted = self._queue.requeue(
                claim,
                retry_kind=RetryKind.EXPONENTIAL,
                reason=_error_reason(exc),
                max_attempts=SUMMARY_MAX_ATTEMPTS,
            )
            return f"chat-summary: 本轮准备失败（{_error_reason(exc)}）。"
        self._queue.complete(claim)
        if not prepared:
            return "chat-summary: 无需准备（已覆盖或来源变化）。"
        return f"chat-summary: 已准备 {len(prepared)} 段有界摘要。"

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def _prepare(
        self,
        account_id: str,
        conversation_id: str,
        *,
        boundary_message_id: str,
        timeout_ms: int,
        max_calls: int,
        run_id: str,
        synchronous: bool = False,
    ) -> list[HistorySummary]:
        generation = self._repository.generation(account_id, conversation_id)
        records = self._conversations.list_messages(account_id, conversation_id)
        valid = self.valid_summaries(
            account_id, conversation_id, messages=records
        )
        order_index = {
            record.message_id: index for index, record in enumerate(records)
        }
        boundary = order_index.get(boundary_message_id)
        if boundary is None:
            return []
        mode = "sync" if max_calls == 1 else "background"
        prepared: list[HistorySummary] = []
        deadline = time.monotonic() + timeout_ms / 1000
        for _ in range(max_calls):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            first_missing = self._first_missing(valid, order_index)
            if first_missing > boundary:
                break
            segment = self._select_segment(records, first_missing, boundary)
            if segment is None:
                break
            summary = self._generate_segment(
                account_id,
                conversation_id,
                segment,
                timeout_ms=remaining_ms,
                run_id=run_id,
                synchronous=synchronous,
            )
            if summary is None:
                break
            if not self._repository.save_if_sources_unchanged(
                summary, expected_generation=generation,
                active_run_id=run_id if synchronous else None,
            ):
                self._audit(
                    account_id,
                    AuditAction.HISTORY_SUMMARY_PREPARED,
                    AuditResult.BLOCKED,
                    details={
                        "reason": "source_changed_before_save",
                        "mode": mode,
                    },
                )
                break
            self._audit(
                account_id,
                AuditAction.HISTORY_SUMMARY_PREPARED,
                AuditResult.SUCCESS,
                details={
                    "summary_id": summary.summary_id,
                    "instance_version": summary.instance_version,
                    "covered_first_message_id": summary.covered_first_message_id,
                    "covered_last_message_id": summary.covered_last_message_id,
                    "covered_message_count": summary.covered_message_count,
                    "input_tokens": summary.input_tokens,
                    "output_tokens": summary.output_tokens,
                    "mode": mode,
                },
            )
            valid.append(summary)
            prepared.append(summary)
        return prepared

    def _select_segment(
        self,
        records: Sequence[MessageRecord],
        start_index: int,
        boundary_index: int,
    ) -> _SummarySegment | None:
        """从缺口起点向后取连续片段，来源 token 有上限（最旧在前截取）。"""
        if start_index > boundary_index:
            return None
        slice_records = list(records[start_index : boundary_index + 1])
        sources: list[SummarySourceMessage] = []
        used_tokens = 0
        last_included = start_index - 1
        for offset, record in enumerate(slice_records):
            if record.role == ChatMessageRole.ASSISTANT and (
                record.status != ChatMessageStatus.DONE
            ):
                continue
            cost = estimate_tokens(record.content)
            if sources and used_tokens + cost > SUMMARY_MAX_SOURCE_TOKENS:
                break
            if not sources and cost > SUMMARY_MAX_SOURCE_TOKENS:
                break
            sources.append(_message_record_source(record))
            used_tokens += cost
            last_included = start_index + offset
        if len(sources) < SUMMARY_MIN_NEW_MESSAGES:
            return None
        covered = records[start_index : last_included + 1]
        return _SummarySegment(
            records=list(covered),
            sources=sources,
        )

    def _generate_segment(
        self,
        account_id: str,
        conversation_id: str,
        segment: _SummarySegment,
        *,
        timeout_ms: int,
        run_id: str,
        synchronous: bool = False,
    ) -> HistorySummary | None:
        run_context = RunContextEnvelope(
            run_id=run_id if synchronous else f"{run_id}:summary",
            account_id=account_id,
            project_id=conversation_id,
            workflow_name="history-summary-sync" if synchronous else "history-summary",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )
        # 元数据中的消息 ID 不能替原文证明某个数值存在。
        source_text = "\n".join(source.content for source in segment.sources)
        try:
            extraction = self._extractor.extract(
                run_context=run_context,
                sources=segment.sources,
                timeout_ms=timeout_ms,
            )
            draft = validate_summary_output(extraction.output, source_text)
        except SummaryRejectedError as exc:
            self._audit(
                account_id,
                AuditAction.HISTORY_SUMMARY_PREPARED,
                AuditResult.BLOCKED,
                details={"reason": exc.reason, "stage": "validation"},
            )
            return None
        except SummaryUnavailableError:
            raise
        now = datetime.now(UTC)
        return HistorySummary(
            summary_id=f"sum-{secrets.token_urlsafe(12)}",
            account_id=account_id,
            conversation_id=conversation_id,
            contract_version=SUMMARY_CONTRACT_VERSION,
            instance_version=self._extractor.version,
            covered_first_message_id=segment.records[0].message_id,
            covered_last_message_id=segment.records[-1].message_id,
            covered_message_count=len(segment.records),
            source_fingerprint=_fingerprint_records(segment.records),
            text=draft.text,
            object_clues=draft.object_clues,
            open_questions=draft.open_questions,
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _first_missing(
        valid: Sequence[HistorySummary], order_index: dict[str, int]
    ) -> int:
        """返回从会话起点起连续覆盖之后的第一个缺口下标（无缺口为 len）。"""
        ranges: list[tuple[int, int]] = []
        for item in valid:
            first = order_index.get(item.covered_first_message_id)
            last = order_index.get(item.covered_last_message_id)
            if first is None or last is None or first > last:
                continue
            ranges.append((first, last))
        ranges.sort()
        next_expected = 0
        for first, last in ranges:
            if first > next_expected:
                break
            next_expected = max(next_expected, last + 1)
        return next_expected

    def _audit(
        self,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        *,
        details: dict[str, Any],
    ) -> None:
        if self._observability is None:
            return
        try:
            self._observability.log_audit(
                actor_account_id=account_id,
                action=action,
                result=result,
                reason="有界历史摘要缓存记录。",
                details=details,
            )
        except Exception:  # noqa: BLE001 - 审计失败不阻断生成/保存
            return


class _SummarySegment:
    """一段待摘要的连续原始消息（records 是完整覆盖范围）。"""

    __slots__ = ("records", "sources")

    def __init__(
        self,
        *,
        records: list[MessageRecord],
        sources: list[SummarySourceMessage],
    ) -> None:
        self.records = records
        self.sources = sources


_SUMMARY_SYSTEM_PROMPT = (
    "你在为一个长期对话生成**较早片段的背景摘要**，供后续轮次定位与理解"
    "背景，不是用户事实，也不得改变任何任务条件、学习题目或阶段。"
    "只根据提供的原始消息总结，禁止编造、禁止引入原文没有的数值或否定；"
    "保留明确的对象线索、已确认结果、开放问题与纠正关系；"
    "用户明确的约束与纠正以原文为准。输出 JSON 对象，字段："
    '{"summary": "不超过 1200 字的背景摘要",'
    ' "object_clues": ["对象/名词线索"], "open_questions": ["未解决问题"]}。'
    "没有线索或问题时给空列表。"
)


def _render_sources(sources: Sequence[SummarySourceMessage]) -> str:
    lines = [
        f"[{item.message_id}] "
        f"{'用户' if item.role == ChatMessageRole.USER.value else '助手'}："
        f"{item.content}"
        for item in sources
    ]
    return "以下是需要整理的原始消息（带消息 ID）：\n" + "\n".join(lines)


def _row_to_summary(row: Any) -> HistorySummary:
    return HistorySummary(
        summary_id=str(row["summary_id"]),
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        contract_version=str(row["contract_version"]),
        instance_version=str(row["instance_version"]),
        covered_first_message_id=str(row["covered_first_message_id"]),
        covered_last_message_id=str(row["covered_last_message_id"]),
        covered_message_count=int(row["covered_message_count"]),
        source_fingerprint=str(row["source_fingerprint"]),
        text=str(row["summary_text"]),
        object_clues=json.loads(row["object_clues_json"] or "[]"),
        open_questions=json.loads(row["open_questions_json"] or "[]"),
        status=SummaryStatus(str(row["status"])),
        invalidated_reason=(
            str(row["invalidated_reason"])
            if row["invalidated_reason"] is not None
            else None
        ),
        invalidated_at=(
            datetime.fromisoformat(str(row["invalidated_at"]))
            if row["invalidated_at"] is not None
            else None
        ),
        input_tokens=(
            int(row["input_tokens"]) if row["input_tokens"] is not None else None
        ),
        output_tokens=(
            int(row["output_tokens"]) if row["output_tokens"] is not None else None
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _iso(value: datetime) -> str:
    return value.isoformat()


def _error_reason(exc: BaseException) -> str:
    if isinstance(exc, (SummaryUnavailableError, SummaryRejectedError)):
        return exc.code if isinstance(exc, SummaryUnavailableError) else exc.reason
    return type(exc).__name__


__all__ = [
    "SUMMARY_QUEUE",
    "ChatSummaryService",
    "ConversationSummaryRepository",
    "GatewaySummaryExtractor",
    "SummaryExtraction",
    "SummaryExtractor",
    "SummaryRejectedError",
    "SummaryUnavailableError",
    "source_fingerprint",
    "validate_summary_output",
]
