"""书页识别、知识范围与预习的独立持久化阶段图。"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from bridges.ai.adapters import StreamEvent
from bridges.chat.checkpoints import RepositoryCheckpointSaver
from bridges.chat.run_executor import chat_run_context
from bridges.chat.turn import failed_thinking, finalize_message, initial_thinking
from bridges.contracts.ai import ModelCallStatus, ModelRunLock
from bridges.contracts.chat import ChatMessageStatus, ChatMode, ChatStreamNodeData
from bridges.contracts.study import (
    StudyFragment,
    StudyPage,
    StudyQuestion,
    StudyState,
    StudyUnclear,
    StudyUnit,
)
from bridges.storage.database import BridgesDatabase

STUDY_GRAPH_VERSION = "study-pages-v1"


class _RecognizedFragment(BaseModel):
    kind: Literal["text", "formula", "chart"]
    position: str = Field(min_length=1)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class _Recognition(BaseModel):
    same_section: bool
    fragments: list[_RecognizedFragment] = Field(min_length=1)
    unclear: list[StudyUnclear] = Field(default_factory=list)


class _Mapped(BaseModel):
    units: list[StudyUnit] = Field(min_length=1)


class _Preview(BaseModel):
    questions: list[StudyQuestion] = Field(min_length=1)


class StudyWorkflowError(Exception):
    def __init__(self, node: str, code: str, message: str) -> None:
        self.node = node
        self.code = code
        self.message = message
        super().__init__(message)


class StudyRepository:
    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    def get(self, account_id: str, conversation_id: str) -> StudyState | None:
        row = (
            self._db.scoped(account_id)
            .execute(
                "SELECT state_json FROM study_states WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            .fetchone()
        )
        return StudyState.model_validate_json(row["state_json"]) if row else None

    def save(self, account_id: str, conversation_id: str, state: StudyState) -> None:
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                "INSERT INTO study_states (account_id, conversation_id, state_json, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(account_id, conversation_id)"
                " DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (
                    account_id,
                    conversation_id,
                    state.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )


class _GraphState(TypedDict, total=False):
    wait: bool
    answer: str


class StudyWorkflow:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._repo = service._repo
        self._attachments = service._require_attachment_service()
        self._states = StudyRepository(self._repo.database)

    def state(self, account_id: str, conversation_id: str) -> StudyState | None:
        return self._states.get(account_id, conversation_id)

    def run(
        self,
        run: Any,
        *,
        on_event: Callable[[StreamEvent], None],
        stop_event: threading.Event | None,
    ) -> str | None:
        started = time.monotonic()
        last_lock: ModelRunLock | None = None
        current_node = "study.recognize"
        state = self._states.get(run.account_id, run.conversation_id) or StudyState(
            subsection_id=run.conversation_id
        )
        user = self._repo.get_message(run.account_id, run.user_message_id)
        if user is None:
            raise StudyWorkflowError(current_node, "message_not_found", "学习消息不存在。")
        attachments = self._attachments.list_for_message(
            run.account_id, run.conversation_id, run.user_message_id
        )
        duplicate_count = 0

        def node(name: str, body: Callable[[], _GraphState]) -> Any:
            def execute(_: _GraphState, config: RunnableConfig) -> _GraphState:
                nonlocal current_node
                del config
                current_node = name
                if stop_event is not None and stop_event.is_set():
                    raise StudyWorkflowError(name, "stopped", "学习处理已停止。")
                self._repo.update_generation_progress(run.account_id, run.run_id, current_node=name)
                on_event(
                    StreamEvent(
                        kind="node",
                        node=ChatStreamNodeData(
                            message_id=run.assistant_message_id, node=name, status="started"
                        ),
                    )
                )
                began = time.monotonic()
                result = body()
                on_event(
                    StreamEvent(
                        kind="node",
                        node=ChatStreamNodeData(
                            message_id=run.assistant_message_id,
                            node=name,
                            status="completed",
                            duration_ms=max(1, int((time.monotonic() - began) * 1000)),
                        ),
                    )
                )
                return result

            return execute

        def invoke(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal last_lock
            if capability == "qwen_structured_output":
                payload = {
                    **payload,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是跨学科教材助教，只依据给定书页证据，输出有效 JSON。",
                        },
                        {"role": "user", "content": payload["prompt"]},
                    ],
                }
            result = self._service._gateway.invoke(
                capability,
                "1",
                chat_run_context(run.account_id, run.conversation_id, run.run_id),
                payload=payload,
                model_override=(run.config or {}).get("run_model_id"),
            )
            if result.status not in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
                raise StudyWorkflowError(
                    current_node,
                    result.error_code or "study_model_failed",
                    "书页处理模型暂时不可用，请稍后重试。",
                )
            last_lock = result.lock
            if not isinstance(result.output, dict):
                raise StudyWorkflowError(
                    current_node, "study_model_invalid", "模型结果不完整，请重试。"
                )
            return result.output

        def recognize() -> _GraphState:
            nonlocal duplicate_count
            state.stage = "recognizing"
            self._states.save(run.account_id, run.conversation_id, state)
            known = {page.content_hash for page in state.pages}
            duplicates = 0
            added = 0
            supplemented = False
            for attachment in attachments:
                if stop_event is not None and stop_event.is_set():
                    raise StudyWorkflowError(current_node, "stopped", "学习处理已停止。")
                if attachment.content_hash in known:
                    duplicates += 1
                    duplicate_count += 1
                    continue
                unresolved_before = [page for page in state.pages if page.unclear]
                target_match = re.search(r"补拍第\s*(\d+)\s*页", user.content)
                replacement = (
                    next(
                        (
                            page
                            for page in unresolved_before
                            if page.ordinal == int(target_match.group(1))
                        ),
                        None,
                    )
                    if target_match
                    else (
                        unresolved_before[0]
                        if len(unresolved_before) == 1
                        and (not user.content.strip() or "补拍" in user.content)
                        else None
                    )
                )
                record, content = self._attachments.download(
                    run.account_id, run.conversation_id, attachment.object_id
                )
                encoded = base64.b64encode(content).decode("ascii")
                ocr = invoke(
                    "qwen_ocr",
                    {
                        "image_base64": encoded,
                        "mime_type": record.media_type,
                        "prompt": (
                            "按阅读顺序逐字识别这页教材的文字、公式与图表标签；"
                            "看不清的符号写[不清]，不要猜测。"
                        ),
                        "task": None,
                        "temperature": 0.01,
                    },
                )
                ocr_text = ocr.get("content")
                if not isinstance(ocr_text, str) or not ocr_text.strip():
                    raise StudyWorkflowError(
                        current_node, "study_ocr_empty", "书页文字识别失败，请重试或补拍。"
                    )
                earlier = [fragment.text for page in state.pages for fragment in page.fragments]
                vision = invoke(
                    "qwen_vision",
                    {
                        "image_base64": encoded,
                        "mime_type": record.media_type,
                        "temperature": 0.01,
                        "prompt": (
                            "只输出 JSON 对象，字段 same_section(boolean),"
                            " fragments(数组：kind 为 text/formula/chart、"
                            "position 为页面位置、text 为所见内容、confidence 为 0-1),"
                            " unclear(数组：position、reason)。"
                            "逐段保留公式和图表；任何看不清或低置信内容必须列入 unclear，不得猜测。"
                            "后续页是否同一小节参考既有片段："
                            + json.dumps(earlier[:12], ensure_ascii=False)
                            + "；独立 OCR 结果仅供核对："
                            + ocr_text[:9000]
                        ),
                    },
                )
                try:
                    parsed = _Recognition.model_validate_json(str(vision.get("content", "")))
                except ValidationError as exc:
                    raise StudyWorkflowError(
                        current_node, "study_recognition_invalid", "书页结构识别不完整，请重试。"
                    ) from exc
                ordinal = replacement.ordinal if replacement else len(state.pages) + 1
                fragments = [
                    StudyFragment(
                        fragment_id=f"{record.object_id}:{index}",
                        kind=item.kind,
                        position=item.position,
                        text=item.text,
                        confidence=item.confidence,
                    )
                    for index, item in enumerate(parsed.fragments, 1)
                ]
                unclear = list(parsed.unclear)
                unclear.extend(
                    StudyUnclear(position=item.position, reason="关键内容识别置信度低")
                    for item in parsed.fragments
                    if item.confidence < 0.7
                    and not any(issue.position == item.position for issue in unclear)
                )
                if state.pages and not parsed.same_section:
                    unclear.append(
                        StudyUnclear(position="整页", reason="疑似不同小节，请确认或在新对话上传")
                    )
                page = StudyPage(
                    object_id=record.object_id,
                    ordinal=ordinal,
                    content_hash=record.content_hash,
                    model_id=(last_lock.actual_model_id or "") if last_lock else "",
                    replaced_object_ids=(
                        [*replacement.replaced_object_ids, replacement.object_id]
                        if replacement
                        else []
                    ),
                    fragments=fragments,
                    unclear=unclear,
                )
                if replacement:
                    state.pages[ordinal - 1] = page
                else:
                    state.pages.append(page)
                known.add(record.content_hash)
                added += 1
                self._states.save(run.account_id, run.conversation_id, state)
            if not attachments and user.content.strip() and state.wait_reason == "unclear_page":
                match = re.search(r"第\s*(\d+)\s*页", user.content)
                if match:
                    ordinal = int(match.group(1))
                    supplement_page = next(
                        (item for item in state.pages if item.ordinal == ordinal), None
                    )
                    if supplement_page is not None and supplement_page.unclear:
                        matches = [
                            issue
                            for issue in supplement_page.unclear
                            if issue.position in user.content
                        ]
                        if len(matches) != 1:
                            matches = (
                                supplement_page.unclear
                                if len(supplement_page.unclear) == 1
                                else []
                            )
                        if matches:
                            issue = matches[0]
                            supplement_page.fragments.append(
                                StudyFragment(
                                    fragment_id=f"user:{user.message_id}",
                                    kind="text",
                                    position=issue.position,
                                    text=user.content,
                                    confidence=1,
                                    source="user",
                                )
                            )
                            supplement_page.unclear.remove(issue)
                            supplemented = True
                            self._states.save(run.account_id, run.conversation_id, state)
            unresolved = [page for page in state.pages if page.unclear]
            if unresolved:
                state.stage = "awaiting_pages"
                state.wait_reason = "unclear_page"
                self._states.save(run.account_id, run.conversation_id, state)
                self._repo.update_generation_progress(
                    run.account_id, run.run_id, wait_reason=state.wait_reason
                )
                details = "；".join(
                    f"第{page.ordinal}页{issue.position}：{issue.reason}"
                    for page in unresolved
                    for issue in page.unclear
                )
                return {
                    "wait": True,
                    "answer": (
                        (f"检测到{duplicates}张重复书页，已跳过。" if duplicates else "")
                        + f"这些位置还看不清：{details}。"
                        "请补拍对应位置，或按“第N页+位置……”补录文字。"
                    ),
                }
            if duplicates and not added and not supplemented and run.attempt_number == 1:
                state.stage = "awaiting_pages" if not state.units else "tutoring"
                self._states.save(run.account_id, run.conversation_id, state)
                return {"wait": True, "answer": "书页重复，请检查页序后重新发送。"}
            if not state.pages:
                return {"wait": True, "answer": "请上传本节书页照片后开始预习。"}
            return {"wait": False}

        def map_units() -> _GraphState:
            fragments = [
                {
                    "id": fragment.fragment_id,
                    "page": page.ordinal,
                    "kind": fragment.kind,
                    "position": fragment.position,
                    "text": fragment.text,
                    "source": fragment.source,
                }
                for page in state.pages
                for fragment in page.fragments
                if fragment.confidence >= 0.7
                and not (
                    fragment.source == "photo"
                    and any(
                        correction.source == "user"
                        and correction.position == fragment.position
                        for correction in page.fragments
                    )
                )
            ]
            mapped = invoke(
                "qwen_structured_output",
                {
                    "prompt": (
                        '只输出 JSON 对象 {"units":[{"title":"知识点",'
                        '"fragment_ids":["片段ID"],"core":true}]}。'
                        "只依据这些片段提炼知识点，引用真实片段 ID，"
                        "覆盖核心概念、关系、应用和易错处。"
                        + json.dumps(fragments, ensure_ascii=False)
                    ),
                    "temperature": 0.01,
                },
            )
            try:
                units = _Mapped.model_validate(mapped).units
            except ValidationError as exc:
                raise StudyWorkflowError(
                    current_node, "study_map_invalid", "知识范围映射不完整，请重试。"
                ) from exc
            valid_ids = {fragment["id"] for fragment in fragments}
            cited_ids = {fragment_id for unit in units for fragment_id in unit.fragment_ids}
            if cited_ids - valid_ids or any(
                not any(
                    fragment.fragment_id in cited_ids
                    for fragment in page.fragments
                )
                for page in state.pages
            ):
                raise StudyWorkflowError(
                    current_node, "study_map_invalid", "知识点未覆盖每页书页依据，请重试。"
                )
            state.units = units
            state.stage = "preview"
            state.wait_reason = None
            self._states.save(run.account_id, run.conversation_id, state)
            return {}

        def preview() -> _GraphState:
            result = invoke(
                "qwen_structured_output",
                {
                    "prompt": (
                        '只输出 JSON 对象 {"questions":[{"question":"预习问题",'
                        '"unit_titles":["知识点标题"]}]}。'
                        "提出有深度的阅读引导问题，不要求学生现在回答；每个核心知识点至少被一个问题覆盖。"
                        + json.dumps(
                            [unit.model_dump() for unit in state.units], ensure_ascii=False
                        )
                    ),
                    "temperature": 0.2,
                },
            )
            try:
                questions = _Preview.model_validate(result).questions
            except ValidationError as exc:
                raise StudyWorkflowError(
                    current_node, "study_preview_invalid", "预习问题生成不完整，请重试。"
                ) from exc
            titles = {unit.title for unit in state.units}
            covered = {title for question in questions for title in question.unit_titles}
            if covered - titles or any(
                unit.core and unit.title not in covered for unit in state.units
            ):
                raise StudyWorkflowError(
                    current_node, "study_preview_invalid", "预习问题未覆盖本节重点，请重试。"
                )
            state.questions = questions
            state.stage = "tutoring"
            self._states.save(run.account_id, run.conversation_id, state)
            scope = "、".join(
                f"{unit.title}（"
                + "、".join(
                    f"第{page.ordinal}页{fragment.position}"
                    for page in state.pages
                    for fragment in page.fragments
                    if fragment.fragment_id in unit.fragment_ids
                )
                + "）"
                for unit in state.units
            )
            questions_text = "\n".join(
                f"{index}. {question.question}" for index, question in enumerate(questions, 1)
            )
            return {
                "answer": (
                    (f"检测到{duplicate_count}张重复书页，已跳过。\n\n" if duplicate_count else "")
                    + f"已识别本节范围：{scope}\n\n"
                    f"预习时可以带着这些问题阅读，暂不需要作答：\n{questions_text}"
                )
            }

        graph = StateGraph(_GraphState)
        graph.add_node("study.recognize", node("study.recognize", recognize))
        graph.add_node("study.map", node("study.map", map_units))
        graph.add_node("study.preview", node("study.preview", preview))
        graph.add_edge(START, "study.recognize")
        graph.add_conditional_edges(
            "study.recognize",
            lambda result: END if result.get("wait") else "study.map",
            {END: END, "study.map": "study.map"},
        )
        graph.add_edge("study.map", "study.preview")
        graph.add_edge("study.preview", END)
        saver = RepositoryCheckpointSaver(
            self._repo.database,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
        )
        compiled = graph.compile(checkpointer=saver)
        config: RunnableConfig = {
            "configurable": {
                "thread_id": run.conversation_id,
                "checkpoint_ns": run.run_id,
            }
        }
        try:
            output = compiled.invoke(
                None if saver.get_tuple(saver.run_config()) is not None else {}, config
            )
            answer = output.get("answer") or "已保存书页，请继续补拍。"
            self._repo.update_message_content(
                run.account_id, run.assistant_message_id, answer, datetime.now(UTC)
            )
            finalize_message(
                self._repo,
                run.account_id,
                run.assistant_message_id,
                status=ChatMessageStatus.DONE,
                error_code=None,
                error_message=None,
                duration_ms=None,
                model_id=last_lock.actual_model_id if last_lock else None,
                lock=last_lock,
                started=started,
                now=datetime.now(UTC),
            )
            return None
        except StudyWorkflowError as exc:
            message_status = (
                ChatMessageStatus.STOPPED if exc.code == "stopped" else ChatMessageStatus.ERROR
            )
            finalize_message(
                self._repo,
                run.account_id,
                run.assistant_message_id,
                status=message_status,
                error_code=exc.code,
                error_message=f"在「{exc.node}」步骤失败：{exc.message}",
                duration_ms=None,
                model_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(initial_thinking(ChatMode.STUDY), exc.code),
            )
            on_event(StreamEvent(kind="error", error_code=exc.code, error_message=exc.message))
            return "error"
        except Exception:
            finalize_message(
                self._repo,
                run.account_id,
                run.assistant_message_id,
                status=ChatMessageStatus.ERROR,
                error_code="study_internal_error",
                error_message=f"在「{current_node}」步骤失败，请重试。",
                duration_ms=None,
                model_id=None,
                started=started,
                now=datetime.now(UTC),
                thinking=failed_thinking(initial_thinking(ChatMode.STUDY), "study_internal_error"),
            )
            on_event(
                StreamEvent(
                    kind="error",
                    error_code="study_internal_error",
                    error_message=f"在「{current_node}」步骤失败，请重试。",
                )
            )
            return "error"
