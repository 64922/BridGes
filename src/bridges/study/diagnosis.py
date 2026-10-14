"""Issue 05：后台独立诊断服务。

回复交付后由受监督执行器领取诊断任务：独立核对题目、实际回答、提示
程度、冻结评价依据、知识上下文与可用来源，形成带依据的诊断结论并归并
到账户级学习档案。边界：

- 模型调用在回复事务之外，不占用前台回复等待；任务与回复/证据在同一
  事务登记，崩溃不留下孤立成功回复或丢失任务。
- 幂等单元为「账户 + 证据身份 + 输入版本 + 诊断协议」；重复调度、租约
  恢复与已完成任务不会重复记账或再次改学情。
- 失败保留原档案，有界重试耗尽后允许针对同一任务手动重试；来源失效与
  版本冲突不覆盖更新证据。
- 输出合同校验：拒绝百分比、人格、心理、注意力与整体能力标签。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from bridges.chat.budget import RunBudget
from bridges.chat.repository import ConversationRepository
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.study_diagnosis import (
    DIAGNOSIS_CONTRACT_VERSION,
    StudyDiagnosisStatus,
    StudyDiagnosisTaskProjection,
    StudyDiagnosisVerdict,
)
from bridges.contracts.workflows import RunContextEnvelope
from bridges.runtime.queue import Claim, RetryKind, TaskQueue
from bridges.storage.database import BridgesDatabase
from bridges.study.diagnosis_prompt import (
    STUDY_DIAGNOSIS_JSON_SCHEMA,
    STUDY_DIAGNOSIS_SYSTEM_PROMPT,
)
from bridges.study.diagnosis_repository import (
    StudyDiagnosisCandidate,
    StudyDiagnosisRepository,
    StudyDiagnosisResult,
    StudyDiagnosisTaskRecord,
    evidence_input_version,
)
from bridges.study.verification import StudyEvidenceRecord, StudyVerificationRepository

#: 诊断专用队列名（共享 TaskQueue 的领取/租约/重试调度）。
DIAGNOSIS_QUEUE = "study-diagnosis"
_DIAGNOSIS_CAPABILITY = "qwen_structured_output"
_DIAGNOSIS_CAPABILITY_VERSION = "1"
_MAX_OUTPUT_TOKENS = 1200
_MAX_ATTEMPTS = 3
_BATCH_SIZE = 3
_LEASE_SECONDS = 120.0
_DIAGNOSIS_BUDGET_MS = 90_000
_ANSWER_LIMIT = 2000
_HINT_MESSAGE_LIMIT = 3
_HINT_TEXT_LIMIT = 200
_RETRY_REASON = "诊断服务暂时不可用，稍后将自动重试。"
_CONTRACT_REASON = "诊断结果不符合输出合同，未写入档案。"
_PERMANENT_REASON = "诊断能力未配置或不可用。"
_INVALID_SOURCE_REASON = "来源证据已失效，未形成诊断。"
_INVALID_VERSION_REASON = "证据输入版本已变化，未形成诊断。"

_VERDICTS = frozenset(item.value for item in StudyDiagnosisVerdict)
_MASTERY_VERDICTS = frozenset(
    {
        StudyDiagnosisVerdict.INDEPENDENT_COMPLETION.value,
        StudyDiagnosisVerdict.HINTED_COMPLETION.value,
    }
)
#: 禁止进入档案的标签与百分比（诊断输出合同；见票据「不生成百分比、
#: 人格、心理、注意力或整体能力标签」）。标签词按完整短语匹配，避免
#: 误伤「心理学」等正当学科名称。
_FORBIDDEN_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*%|百分比|掌握度|掌握百分比|人格|心理诊断|心理状态|"
    r"注意力问题|能力等级|总体能力"
)


class StudyDiagnosisError(Exception):
    """诊断任务操作域错误（API 层映射为 404/409）。"""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class _RetryableDiagnosisError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _PermanentDiagnosisError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _DiagnosisOutput(BaseModel):
    """结构化诊断输出；空字符串表示未提供该字段。"""

    subject: str = ""
    knowledge_title: str = ""
    usage_context: str = ""
    verdict: str = ""
    performance: str = ""
    supporting: list[str] = Field(default_factory=list)
    refuting: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    next_validation: str = ""


class StudyDiagnosisService:
    """后台诊断的登记、领取、执行与手动重试。"""

    def __init__(
        self,
        database: BridgesDatabase,
        gateway: Any,
        *,
        queue: TaskQueue | None = None,
        worker_name: str = "study-diagnosis",
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._database = database
        self._gateway = gateway
        self._queue = queue or TaskQueue(database)
        self._queue.set_lease_seconds(DIAGNOSIS_QUEUE, _LEASE_SECONDS)
        self._repository = StudyDiagnosisRepository(database)
        self._verification = StudyVerificationRepository(database)
        self._conversations = ConversationRepository(database)
        self._worker_name = worker_name
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------
    # 登记（在回复终态事务内调用）与手动重试
    # ------------------------------------------------------------------

    def schedule_in_transaction(self, candidate: StudyDiagnosisCandidate) -> str:
        """登记任务并入队；与回复/证据同一事务，失败整体回滚。"""

        task_id = self._repository.create_task_in_transaction(candidate)
        self._queue.enqueue(
            DIAGNOSIS_QUEUE,
            task_id,
            payload={
                "account_id": candidate.account_id,
                "task_id": task_id,
                "conversation_id": candidate.conversation_id,
            },
        )
        return task_id

    def retry_task(
        self,
        account_id: str,
        task_id: str,
        *,
        conversation_id: str | None = None,
    ) -> StudyDiagnosisTaskProjection:
        """针对失败任务的手动重试（同一任务、同一幂等单元）。"""

        task = self._repository.get_task(account_id, task_id)
        if task is None or (
            conversation_id is not None and task.conversation_id != conversation_id
        ):
            raise StudyDiagnosisError("task_not_found", "诊断任务不存在或没有访问权限。", 404)
        if task.status != StudyDiagnosisStatus.FAILED:
            raise StudyDiagnosisError(
                "task_not_failed", "只有失败的诊断任务可以手动重试。", 409
            )
        if not self._repository.reset_for_retry(account_id, task_id):
            raise StudyDiagnosisError(
                "task_not_failed", "诊断任务状态已变化，请刷新后重试。", 409
            )
        self._queue.enqueue(
            DIAGNOSIS_QUEUE,
            task_id,
            payload={
                "account_id": account_id,
                "task_id": task_id,
                "conversation_id": task.conversation_id,
            },
        )
        updated = self._repository.get_task(account_id, task_id)
        if updated is None:  # pragma: no cover - 状态行刚更新过
            raise StudyDiagnosisError("task_not_found", "诊断任务不存在或没有访问权限。", 404)
        return updated.projection()

    # ------------------------------------------------------------------
    # 执行器 tick
    # ------------------------------------------------------------------

    def run_tick(self) -> str:
        """处理至多 ``_BATCH_SIZE`` 个诊断任务并返回中文摘要。"""

        handled = 0
        failures = 0
        seen: set[str] = set()
        while handled + failures < _BATCH_SIZE:
            claim = self._queue.claim_next(DIAGNOSIS_QUEUE, self._worker_name)
            if claim is None:
                break
            if claim.claim_id in seen:
                # 本轮的同类任务已处理过：释放租约（不计数、短退避），
                # 留给下一轮，避免单轮内无限重试同一任务。
                self._queue.requeue(
                    claim,
                    retry_kind=RetryKind.FIXED,
                    reason="轮次边界释放",
                    backoff_seconds=1.0,
                    count_attempt=False,
                )
                continue
            seen.add(claim.claim_id)
            if self._run_claim(claim):
                handled += 1
            else:
                failures += 1
        if handled == 0 and failures == 0:
            return "study-diagnosis: 无待处理任务。"
        return f"study-diagnosis: 处理 {handled} 件，失败 {failures} 件。"

    def _run_claim(self, claim: Claim) -> bool:
        payload = claim.payload or {}
        account_id = str(payload.get("account_id") or "")
        task_id = str(payload.get("task_id") or claim.task_key)
        task = (
            self._repository.get_task(account_id, task_id) if account_id else None
        )
        if task is None or task.status in {
            StudyDiagnosisStatus.SUCCEEDED,
            StudyDiagnosisStatus.INVALIDATED,
        }:
            # 已完成/已失效的任务：队列行收尾，不重复记账、不改学情。
            self._queue.complete(claim)
            return True
        generation = self._repository.mark_running(account_id, task_id)
        if generation is None:
            self._queue.complete(claim)
            return True
        try:
            self._diagnose(task, generation)
        except _RetryableDiagnosisError as exc:
            exhausted = self._queue.requeue(
                claim,
                retry_kind=RetryKind.FIXED,
                reason=exc.reason,
                max_attempts=self._max_attempts,
                backoff_seconds=0,
            )
            if exhausted:
                self._repository.mark_failed(account_id, task_id, exc.reason)
            else:
                self._repository.mark_pending_after_failure(account_id, task_id)
            return False
        except _PermanentDiagnosisError as exc:
            self._queue.fail(claim, exc.reason)
            self._repository.mark_failed(account_id, task_id, exc.reason)
            return False
        except Exception:  # noqa: BLE001 - 单任务异常兜底为有界重试
            exhausted = self._queue.requeue(
                claim,
                retry_kind=RetryKind.EXPONENTIAL,
                reason="诊断执行异常",
                max_attempts=self._max_attempts,
            )
            if exhausted:
                self._repository.mark_failed(account_id, task_id, _RETRY_REASON)
            else:
                self._repository.mark_pending_after_failure(account_id, task_id)
            return False
        self._queue.complete(claim)
        return True

    # ------------------------------------------------------------------
    # 单次诊断
    # ------------------------------------------------------------------

    def _diagnose(self, task: StudyDiagnosisTaskRecord, generation: int) -> None:
        evidence = self._verification.get_evidence(task.account_id, task.evidence_id)
        if (
            evidence is None
            or not evidence.answered
            or evidence.semantics.value == "unverified"
        ):
            self._repository.mark_invalidated(
                task.account_id, task.task_id, _INVALID_SOURCE_REASON
            )
            return
        if evidence_input_version(evidence) != task.input_version:
            self._repository.mark_invalidated(
                task.account_id, task.task_id, _INVALID_VERSION_REASON
            )
            return
        request = self._build_request(task, evidence)
        output = self._invoke(task, request)
        result = _validated_result(output, check_kind=evidence.check_kind.value)
        self._repository.commit_result(
            task,
            generation,
            result,
            evidence_at=evidence.created_at,
        )

    def _build_request(
        self, task: StudyDiagnosisTaskRecord, evidence: StudyEvidenceRecord
    ) -> dict[str, Any]:
        answer = ""
        if evidence.answer_message_id:
            message = self._conversations.get_message(
                task.account_id, evidence.answer_message_id
            )
            if message is None:
                raise _RetryableDiagnosisError("实际回答消息暂不可读。")
            answer = " ".join(str(message.content).split())[:_ANSWER_LIMIT]
        hints: list[str] = []
        for message_id in evidence.hint_message_ids[:_HINT_MESSAGE_LIMIT]:
            hint = self._conversations.get_message(task.account_id, message_id)
            if hint is not None:
                hints.append(
                    " ".join(str(hint.content).split())[:_HINT_TEXT_LIMIT]
                )
        return {
            "protocol_version": DIAGNOSIS_CONTRACT_VERSION,
            "check_kind": evidence.check_kind.value,
            "question": evidence.question,
            "student_answer": answer,
            "assistance": evidence.assistance.value,
            "hint_count": len(evidence.hint_message_ids),
            "recent_hints": hints,
            "knowledge_context": (
                evidence.knowledge_context or task.knowledge_context
            ),
            "evaluation_basis": task.basis.model_dump(mode="json"),
            "sources": [
                {
                    "filename": str(ref.get("filename") or ""),
                    "page_number": ref.get("page_number"),
                    "section_title": ref.get("section_title"),
                }
                for ref in evidence.source_refs[:8]
            ],
        }

    def _invoke(
        self, task: StudyDiagnosisTaskRecord, request: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "messages": [
                {"role": "system", "content": STUDY_DIAGNOSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        request, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            ],
            "json_schema": STUDY_DIAGNOSIS_JSON_SCHEMA,
            "temperature": 0,
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        run_context = RunContextEnvelope(
            run_id=f"diagnosis:{task.task_id}",
            account_id=task.account_id,
            project_id=task.conversation_id,
            workflow_name="study-diagnosis",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )
        budget = RunBudget(f"diagnosis:{task.task_id}", total_ms=_DIAGNOSIS_BUDGET_MS)
        try:
            call = self._gateway.invoke(
                _DIAGNOSIS_CAPABILITY,
                _DIAGNOSIS_CAPABILITY_VERSION,
                run_context,
                payload=payload,
                budget=budget,
            )
        except Exception as exc:  # noqa: BLE001 - 服务失败转有界重试
            raise _RetryableDiagnosisError(_RETRY_REASON) from exc
        status = getattr(call.status, "value", "")
        if status in {"success", "degraded"} and isinstance(call.output, dict):
            return call.output
        if status == "retryable_fail":
            raise _RetryableDiagnosisError(_RETRY_REASON)
        raise _PermanentDiagnosisError(_PERMANENT_REASON)


def _validated_result(output: dict[str, Any], *, check_kind: str) -> StudyDiagnosisResult:
    """确定性校验诊断输出；违反合同即重试，绝不写入档案。"""

    try:
        parsed = _DiagnosisOutput.model_validate(output)
    except ValidationError as exc:
        raise _RetryableDiagnosisError(_CONTRACT_REASON) from exc
    verdict = parsed.verdict.strip()
    if verdict not in _VERDICTS:
        raise _RetryableDiagnosisError(_CONTRACT_REASON)
    subject = _short_text(parsed.subject, 120)
    title = _short_text(parsed.knowledge_title, 120)
    usage_context = _short_text(parsed.usage_context, 200)
    if not subject or not title:
        # 无学科/标题的知识点无法形成稳定身份，不得生成无依据缺口。
        raise _RetryableDiagnosisError(_CONTRACT_REASON)
    if check_kind == "open_probe" and verdict in _MASTERY_VERDICTS:
        # 开放探查只记录理解线索，不晋升为完成判断。
        verdict = StudyDiagnosisVerdict.UNDETERMINED.value
    texts = [
        subject,
        title,
        usage_context,
        parsed.performance,
        *parsed.supporting,
        *parsed.refuting,
        parsed.uncertainty,
        parsed.next_validation,
    ]
    if any(_FORBIDDEN_PATTERN.search(text) for text in texts):
        raise _RetryableDiagnosisError(_CONTRACT_REASON)
    return StudyDiagnosisResult(
        subject=subject,
        title=title,
        usage_context=usage_context,
        verdict=StudyDiagnosisVerdict(verdict),
        performance=_short_text(parsed.performance, 400),
        supporting=[_short_text(item, 160) for item in parsed.supporting[:6] if item.strip()],
        refuting=[_short_text(item, 160) for item in parsed.refuting[:6] if item.strip()],
        uncertainty=_short_text(parsed.uncertainty, 400),
        next_validation=_short_text(parsed.next_validation, 300),
    )


def _short_text(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]
