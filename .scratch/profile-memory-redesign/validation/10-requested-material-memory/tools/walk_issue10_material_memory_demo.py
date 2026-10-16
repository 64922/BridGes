"""画像工单 10 走查：明确请求的文件/照片材料更新（确定性）。

覆盖七个用户可见旅程（真实 SQLite 存储 + 真实材料读取组合
``_designated_material_reader`` + 真实来源核验 ``_material_source_checker``，
只把模型网关替换为脚本化替身，不调用任何外部模型）：

[1] 普通上传/阅读/任务分析：即使消息带附件，也不触发任何画像处理。
[2] 本人文件材料：明确指令 → 登记后台处理（如实约束不声称已记住）→
    解析完成后提取 → 有来源事实（对象 + 页码 + 片段）→ 下一轮采用；
    同轮不采用。
[3] 拒绝与如实：他人材料（室友的简历）零读取零写入；没有材料时如实
    说找不到；聊天约束不声称已记住。
[4] 不可用如实：解析未完成有界等待后如实超时；解析为空/失败/多人材料/
    主体不明分别如实报告，不伪造引用。
[5] 编辑/删除失效：材料被替换或撤回后，旧片段不再展示、派生采用同步
    失效；重新绑定后恢复可用（排除不删除条目）。
[6] 照片材料：整图 OCR（qwen_ocr）→ 提取 → 事实定位为「整图」。
[7] 重启与跨账户：run_kind 与材料来源跨重启保留；跨账户看不到任何
    条目或材料；聊天文案按状态如实。

用法（在仓库根目录执行，<本目录> 指本走查所在目录）：
    python "<本目录>/tools/walk_issue10_material_memory_demo.py" \
        --out "<本目录>/evidence/demo-walkthrough-<stamp>.txt"

脚本只使用临时库（``.tmp/issue10-demo-*``），不读取或记录任何真实数据。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.api.app_support import (  # noqa: E402
    _designated_material_reader,
    _material_source_checker,
)
from bridges.chat.attachments_repository import AttachmentRepository  # noqa: E402
from bridges.chat.repository import ConversationRepository, MessageRecord  # noqa: E402
from bridges.chat.turn import profile_memory_context  # noqa: E402
from bridges.contracts.ai import ModelCallStatus  # noqa: E402
from bridges.contracts.atomic_profile import (  # noqa: E402
    AtomicProfileMaterialBlockedReason,
    AtomicProfileMaterialSourceStatus,
    AtomicProfileMaterialStatus,
)
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus  # noqa: E402
from bridges.contracts.profile_extraction import (  # noqa: E402
    ProfileExtractionKind,
    ProfileExtractionOutcome,
    ProfileExtractionStatus,
)
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    RECALL_MATERIAL_INVALID_REASON,
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.automatic import (  # noqa: E402
    MATERIAL_MAX_WAIT_ATTEMPTS,
    AutomaticProfileService,
    GatewayAutomaticProfileExtractor,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.materials import (  # noqa: E402
    MATERIAL_MISSING_ERROR,
    MATERIAL_MULTIPLE_SUBJECTS_ERROR,
    MATERIAL_NOT_OWNED_ERROR,
    MATERIAL_PARSE_FAILED_ERROR,
    MATERIAL_PARSING_ERROR,
    MATERIAL_SUBJECT_UNCERTAIN_ERROR,
    MATERIAL_UNREADABLE_ERROR,
)
from bridges.profiles.purpose import build_purpose  # noqa: E402
from bridges.retrieval.repository import RetrievalRepository  # noqa: E402
from bridges.storage.database import BridgesDatabase  # noqa: E402
from bridges.storage.object_store import EncryptedFileObjectStore  # noqa: E402
from bridges.storage.repository import BridgesObjectRepository  # noqa: E402

ACCOUNT = "demo-alice"
OTHER_ACCOUNT = "demo-bob"
CONVERSATION = "conversation-1"
DIRECTIVE = "这是我的简历，请更新我的背景"
RESUME_QUERY = "某某大学怎么样"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class _Script:
    def __init__(self, out: list[str]) -> None:
        self._out = out
        self.failures = 0

    def say(self, text: str) -> None:
        print(text)
        self._out.append(text)

    def check(self, ok: bool, label: str) -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        self.say(f"    [{status}] {label}")


class _ScriptedGateway:
    """脚本化模型网关：按能力返回确定性结果（OCR 文本 / 材料抽取输出）。"""

    def __init__(
        self,
        *,
        ocr_text: str = "",
        material_output: dict[str, Any] | None = None,
        ocr_lock: bool = True,
    ) -> None:
        self.calls: list[str] = []
        self.ocr_text = ocr_text
        self.ocr_lock = ocr_lock
        self.material_output = material_output or {"subject": "user", "items": []}

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        capability = str(args[0])
        self.calls.append(capability)
        if capability == "qwen_ocr":
            output: dict[str, Any] = {"content": self.ocr_text}
        else:
            payload = kwargs.get("payload") or {}
            assert payload.get("output_contract") == "profile-material-extraction-v1"
            output = self.material_output
        return SimpleNamespace(
            lock=SimpleNamespace() if self.ocr_lock or capability != "qwen_ocr" else None,
            status=ModelCallStatus.SUCCESS,
            output=output,
            error_code=None,
            error_message=None,
        )


class _CountingReader:
    """给真实材料读取器计数，证明「零读取」不是空口声明。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.list_calls = 0
        self.read_calls = 0

    def list_designated_materials(self, *args: Any, **kwargs: Any) -> Any:
        self.list_calls += 1
        return self._inner.list_designated_materials(*args, **kwargs)

    def read_material(self, *args: Any, **kwargs: Any) -> Any:
        self.read_calls += 1
        return self._inner.read_material(*args, **kwargs)


class _Stack:
    """真实 SQLite + 真实材料读取组合（网关为脚本化替身）。"""

    def __init__(self, path: Path, gateway: _ScriptedGateway) -> None:
        self.database = BridgesDatabase(path)
        self.database.initialize()
        self.objects = BridgesObjectRepository(
            self.database,
            EncryptedFileObjectStore(path.parent / f"{path.stem}-objects", "walk-demo-key"),
        )
        self.objects.ensure_account(ACCOUNT, "demo-alice@example.com")
        self.attachments = AttachmentRepository(self.database)
        self.conversations = ConversationRepository(self.database)
        with contextlib.suppress(Exception):  # 复开同一库时对话已存在
            self.conversations.create_conversation(
                account_id=ACCOUNT,
                conversation_id=CONVERSATION,
                title="走查会话",
                mode="companion",
                created_at=datetime.now(UTC),
            )
        retrieval = RetrievalRepository(self.database)
        reader = _designated_material_reader(
            attachments=self.attachments,
            conversations=self.conversations,
            retrieval=retrieval,
            objects=self.objects,
            gateway=gateway,
        )
        self.reader = _CountingReader(reader)
        self.four = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=SqliteFourDimensionProfileRepository(self.database),
        )
        self.atomic = AtomicProfileService(
            self.four,
            SqliteAtomicProfileRepository(self.database),
            material_source_checker=_material_source_checker(
                self.attachments, self.objects
            ),
        )
        self.state = SqliteAutomaticProfileRepository(self.database)
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four,
            repository=self.state,
            extractor=GatewayAutomaticProfileExtractor(gateway),
            message_reader=self.conversations.get_message,
            atomic_profile_service=self.atomic,
            material_reader=self.reader,
        )
        self.gateway = gateway

    # -- 测试夹具：真实对象 + 真实绑定行 -------------------------------------

    def add_message(self, message_id: str, content: str) -> None:
        """登记用户消息（材料工作器的会话与来源校验需要真实消息行）。"""
        now = datetime.now(UTC)
        with contextlib.suppress(Exception):  # 同一消息重复登记（J1 多次同步）
            self.conversations.insert_message(
                MessageRecord(
                    message_id=message_id,
                    conversation_id=CONVERSATION,
                    account_id=ACCOUNT,
                    role=ChatMessageRole.USER,
                    attempt_number=1,
                    status=ChatMessageStatus.DONE,
                    content=content,
                    thinking=None,
                    error_code=None,
                    error_message=None,
                    duration_ms=None,
                    model_id=None,
                    run_lock_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    def attach_file(
        self,
        message_id: str,
        *,
        filename: str,
        text: str,
        chunks: list[tuple[str, int, str]],
        doc_status: str | None = "ready",
    ) -> Any:
        """创建文件对象并绑定到消息；chunks 为 (正文, 页码, 章节)。"""
        obj = self.objects.create_object(
            ACCOUNT, filename, text.encode("utf-8"), "application/pdf"
        )
        now = _now_iso()
        with self.database.transaction():
            self.database.connection.execute(
                "INSERT INTO chat_attachments(object_id, account_id, conversation_id,"
                " message_id, upload_id, media_type, status, ordinal, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'bound', 0, ?, ?)",
                (
                    obj.object_id,
                    ACCOUNT,
                    CONVERSATION,
                    message_id,
                    f"upload-{obj.object_id}",
                    "application/pdf",
                    now,
                    now,
                ),
            )
            if doc_status is None:
                return obj
            self.database.connection.execute(
                "INSERT INTO document_records(document_id, account_id, object_id,"
                " conversation_id, content_hash, parser_version, status, source,"
                " project_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'parser-walk', ?, 'chat_attachment', ?, ?, ?)",
                (
                    f"doc-{obj.object_id}",
                    ACCOUNT,
                    obj.object_id,
                    CONVERSATION,
                    obj.content_hash,
                    doc_status,
                    CONVERSATION,
                    now,
                    now,
                ),
            )
            for index, (chunk_text, page, section) in enumerate(chunks):
                self.database.connection.execute(
                    "INSERT INTO document_chunks(chunk_id, document_id, account_id,"
                    " chunk_index, content, section_title, page_number, start_offset,"
                    " end_offset, content_hash, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        f"doc-{obj.object_id}:{index}",
                        f"doc-{obj.object_id}",
                        ACCOUNT,
                        index,
                        chunk_text,
                        section,
                        page,
                        len(chunk_text),
                        hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                        now,
                        now,
                    ),
                )
        return obj

    def attach_photo(self, message_id: str, *, filename: str) -> Any:
        obj = self.objects.create_object(
            ACCOUNT, filename, b"PNG-DEMO-BYTES", "image/png"
        )
        now = _now_iso()
        with self.database.transaction():
            self.database.connection.execute(
                "INSERT INTO chat_attachments(object_id, account_id, conversation_id,"
                " message_id, upload_id, media_type, status, ordinal, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'bound', 0, ?, ?)",
                (
                    obj.object_id,
                    ACCOUNT,
                    CONVERSATION,
                    message_id,
                    f"upload-{obj.object_id}",
                    "image/png",
                    now,
                    now,
                ),
            )
        return obj

    def rebind_attachment(self, message_id: str, object_id: str) -> None:
        """把消息绑定替换为新对象版本；绑定行不存在时重新插入（模拟恢复）。"""

        now = _now_iso()
        with self.database.transaction():
            cursor = self.database.connection.execute(
                "UPDATE chat_attachments SET object_id = ?, updated_at = ?"
                " WHERE account_id = ? AND conversation_id = ? AND message_id = ?",
                (object_id, now, ACCOUNT, CONVERSATION, message_id),
            )
            if cursor.rowcount == 0:
                self.database.connection.execute(
                    "INSERT INTO chat_attachments(object_id, account_id, conversation_id,"
                    " message_id, upload_id, media_type, status, ordinal, created_at,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, 'application/pdf', 'bound',"
                    " 0, ?, ?)",
                    (
                        object_id,
                        ACCOUNT,
                        CONVERSATION,
                        message_id,
                        f"upload-{object_id}",
                        now,
                        now,
                    ),
                )

    def detach_message(self, message_id: str) -> None:
        """删除消息的附件绑定行（模拟撤回/删除材料）。"""

        with self.database.transaction():
            self.database.connection.execute(
                "DELETE FROM chat_attachments"
                " WHERE account_id = ? AND conversation_id = ? AND message_id = ?",
                (ACCOUNT, CONVERSATION, message_id),
            )

    # -- 驱动 ----------------------------------------------------------------

    def sync(self, content: str, *, message_id: str):
        self.add_message(message_id, content)
        return self.automatic.process_synchronous_controls(
            ACCOUNT,
            conversation_id=CONVERSATION,
            message_id=message_id,
            content=content,
            run_id=f"run-{message_id}",
            mode="companion",
        )

    def tick(self) -> str:
        return self.automatic.run_retry_tick()

    def adopted(self, query: str, *, current: str | None = None):
        return self.atomic.compile_adopted_slice(
            ACCOUNT,
            run_id=f"slice-{query}",
            purpose=build_purpose(mode="companion", query=query),
            current_user_message_id=current,
        )

    def runs_for(self, message_id: str):
        return [
            run
            for run in self.state.list_runs(ACCOUNT)
            if run.message_id == message_id
        ]

    def tasks_for(self, message_id: str):
        return [
            task
            for task in self.state.list_tasks(ACCOUNT)
            if task.message_id == message_id
        ]

    def close(self) -> None:
        self.database.close()


def _item_output(object_ref: str, quote: str, *, subject: str = "user") -> dict[str, Any]:
    return {
        "subject": subject,
        "items": [
            {
                "dimension": None,
                "material_ref": object_ref,
                "normalized_value": quote,
                "fact_text": quote,
                "evidence_quote": quote,
                "reliability": 0.9,
                "action": "create",
            }
        ]
        if subject == "user"
        else [],
    }


# ---------------------------------------------------------------------------
# 旅程
# ---------------------------------------------------------------------------


def journey_1_ordinary_usage(script: _Script, tmp: Path) -> None:
    script.say("[1] 普通上传/阅读/任务分析：附件在场也不触发画像")
    stack = _Stack(tmp / "ordinary.db", _ScriptedGateway())
    stack.attach_file(
        "m-plain",
        filename="简历.pdf",
        text="姓名：张三\n本科就读于某某大学",
        chunks=[("姓名：张三", 1, "基本信息"), ("本科就读于某某大学", 2, "教育经历")],
    )
    results = [
        stack.sync(content, message_id="m-plain")
        for content in ("看看这份简历", "帮我分析一下这份成绩单", "我上传了简历")
    ]
    script.check(all(result is None for result in results), "三条普通消息都不构成画像指令")
    script.check(
        stack.runs_for("m-plain") == [] and stack.tasks_for("m-plain") == [],
        "零运行、零任务",
    )
    script.check(stack.reader.list_calls == 0 and stack.reader.read_calls == 0, "零材料读取")
    script.check(stack.gateway.calls == [], "零模型调用")
    script.check(stack.atomic.list_items(ACCOUNT) == [], "画像条目 0 条")
    stack.close()


def journey_2_own_resume(script: _Script, tmp: Path) -> None:
    script.say("[2] 本人简历：处理登记 → 提取 → 有来源事实 → 下一轮采用")
    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "resume.db", gateway)
    resume = stack.attach_file(
        "m-resume",
        filename="张三-简历.pdf",
        text="姓名：张三\n本科就读于某某大学\n技能：Python 与数据分析",
        chunks=[
            ("姓名：张三", 1, "基本信息"),
            ("本科就读于某某大学", 2, "教育经历"),
            ("技能：Python 与数据分析", 2, "技能"),
        ],
    )
    gateway.material_output = _item_output(resume.object_id, "本科就读于某某大学")

    result = stack.sync(DIRECTIVE, message_id="m-resume")
    assert result is not None and result.material is not None
    run = stack.runs_for("m-resume")[0]
    copy = profile_memory_context(result.material.context_metadata())
    script.say(
        f"    同步结果：run={run.status.value}/{run.run_kind.value}，"
        f"材料={result.material.status.value}，材料条数={result.material.material_count}"
    )
    script.check(
        run.status is ProfileExtractionStatus.PENDING
        and run.run_kind is ProfileExtractionKind.MATERIAL
        and result.material.status is AtomicProfileMaterialStatus.PROCESSING,
        "登记后台处理（PENDING + material）",
    )
    script.check(
        copy is not None and "不得声称已经记住材料内容" in copy,
        "聊天约束：处理中不得声称已记住",
    )
    script.check(gateway.calls == [], "同步阶段零模型调用（解析完成前不提取）")

    message = stack.tick()
    items = stack.atomic.list_items(ACCOUNT)
    script.check("succeeded" in message and len(items) == 1, "后台处理完成并写入 1 条事实")
    item = items[0]
    source = item.source_materials[0]
    script.say(
        f"    事实：{item.text}；来源：{source.filename} {source.locator}「{source.quote}」"
    )
    script.check(item.text == "本科就读于某某大学", "事实正文来自材料原文")
    script.check(
        item.source_message_ids == ["m-resume"]
        and source.object_id == resume.object_id
        and source.content_hash == resume.content_hash
        and source.locator == "第 2 页 · 教育经历"
        and source.quote == "本科就读于某某大学",
        "来源可查看：对象/页码/章节/原话齐全",
    )
    done = stack.runs_for("m-resume")[0]
    script.check(
        done.outcome is ProfileExtractionOutcome.SUCCEEDED_WRITTEN
        and done.source.value == "qwen_model",
        "运行如实记录：成功写入且来源为模型调用",
    )

    projection = stack.atomic.item_evidence(ACCOUNT, item.profile_item_id)
    projected = projection.material_sources[0]
    script.check(
        projected.status is AtomicProfileMaterialSourceStatus.AVAILABLE
        and projected.quote == "本科就读于某某大学"
        and projected.locator == "第 2 页 · 教育经历",
        "证据投影：材料仍可用时展示定位与原话",
    )

    same_turn = stack.adopted(RESUME_QUERY, current="m-resume")
    script.check(
        not same_turn.adopted_items
        and same_turn.excluded_items[0].exclusion_reason == "本轮刚整理，下一轮才使用",
        "同轮不采用（下一轮才使用）",
    )
    next_turn = stack.adopted(RESUME_QUERY)
    script.check(
        [entry.fact_text for entry in next_turn.adopted_items] == ["本科就读于某某大学"],
        "下一轮相关提问采用该事实",
    )
    stack.close()


def journey_3_refused(script: _Script, tmp: Path) -> None:
    script.say("[3] 拒绝与如实：他人材料零读取；没有材料如实说明")
    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "refused.db", gateway)

    missing = stack.sync("请根据我的成绩单更新我的背景", message_id="m-nomaterial")
    assert missing is not None and missing.material is not None
    missing_copy = profile_memory_context(missing.material.context_metadata())
    script.check(
        missing.material.blocked_reason
        is AtomicProfileMaterialBlockedReason.MATERIAL_MISSING
        and missing.run.last_error == MATERIAL_MISSING_ERROR,
        "没有材料如实说明（material_missing）",
    )
    script.check(
        missing_copy is not None and "没有找到本次指令对应的可处理材料" in missing_copy,
        "聊天约束：如实说明找不到材料",
    )

    stack.attach_file(
        "m-roommate",
        filename="室友-简历.pdf",
        text="姓名：李四\n本科就读于另一所大学",
        chunks=[("姓名：李四", 1, "基本信息")],
    )
    third = stack.sync("这是我室友的简历，请更新我的背景", message_id="m-roommate")
    assert third is not None and third.material is not None
    copy = profile_memory_context(third.material.context_metadata())
    script.say(
        "    室友材料："
        f"{third.material.status.value} / "
        f"{third.material.blocked_reason.value if third.material.blocked_reason else ''}"
    )
    script.check(
        third.material.status is AtomicProfileMaterialStatus.UNRESOLVED
        and third.material.blocked_reason
        is AtomicProfileMaterialBlockedReason.MATERIAL_NOT_OWNED,
        "他人材料如实拒绝（not_owned）",
    )
    script.check(
        stack.reader.read_calls == 0
        and gateway.calls == []
        and stack.tasks_for("m-roommate") == []
        and stack.runs_for("m-roommate")[0].last_error == MATERIAL_NOT_OWNED_ERROR,
        "零读取、零模型调用、零任务，记账留稳定原因",
    )
    script.check(
        copy is not None and "只能处理用户本人的材料" in copy,
        "聊天约束：不得假装记住他人材料",
    )
    stack.close()


def journey_4_unavailable(script: _Script, tmp: Path) -> None:
    script.say("[4] 不可用如实：等待/超时、为空、失败、多人、主体不明")

    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "pending.db", gateway)
    stack.attach_file(
        "m-parsing",
        filename="简历.pdf",
        text="姓名：张三",
        chunks=[("姓名：张三", 1, "基本信息")],
        doc_status="parsing",
    )
    assert stack.sync(DIRECTIVE, message_id="m-parsing") is not None
    first = stack.tick()
    script.check("pending" in first, "解析未完成：有界等待（pending）而非失败")
    for _ in range(MATERIAL_MAX_WAIT_ATTEMPTS - 1):
        stack.tick()
    run = stack.runs_for("m-parsing")[0]
    script.check(
        run.last_error == MATERIAL_PARSING_ERROR and stack.atomic.list_items(ACCOUNT) == [],
        f"超过 {MATERIAL_MAX_WAIT_ATTEMPTS} 次尝试后如实报告解析超时",
    )
    stack.close()

    for label, doc_status, expected in (
        ("解析为空", "empty", MATERIAL_UNREADABLE_ERROR),
        ("解析失败", "error", MATERIAL_PARSE_FAILED_ERROR),
    ):
        gateway = _ScriptedGateway()
        stack = _Stack(tmp / f"bad-{doc_status}.db", gateway)
        stack.attach_file(
            "m-bad",
            filename="简历.pdf",
            text="（不可读）",
            chunks=[("（不可读）", 1, "基本信息")],
            doc_status=doc_status,
        )
        assert stack.sync(DIRECTIVE, message_id="m-bad") is not None
        for _ in range(MATERIAL_MAX_WAIT_ATTEMPTS):
            stack.tick()
        run = stack.runs_for("m-bad")[0]
        script.check(
            run.last_error == expected
            and stack.atomic.list_items(ACCOUNT) == []
            and gateway.calls == [],
            f"{label}：如实报告且零写入零模型调用",
        )
        stack.close()

    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "multi.db", gateway)
    stack.attach_file(
        "m-multi",
        filename="多人材料.pdf",
        text="姓名：张三\n姓名：李四",
        chunks=[("姓名：张三", 1, "基本信息"), ("姓名：李四", 2, "基本信息")],
    )
    assert stack.sync(DIRECTIVE, message_id="m-multi") is not None
    stack.tick()
    run = stack.runs_for("m-multi")[0]
    script.check(
        run.last_error == MATERIAL_MULTIPLE_SUBJECTS_ERROR
        and stack.atomic.list_items(ACCOUNT) == []
        and gateway.calls == [],
        "多人材料：拒绝提取（零模型调用，不猜测归属）",
    )
    stack.close()

    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "other-subject.db", gateway)
    subject = stack.attach_file(
        "m-other",
        filename="简历.pdf",
        text="姓名：张三\n本科就读于某某大学",
        chunks=[("姓名：张三", 1, "基本信息"), ("本科就读于某某大学", 2, "教育经历")],
    )
    gateway.material_output = {
        "subject": "other",
        "items": [
            {
                "dimension": None,
                "material_ref": subject.object_id,
                "normalized_value": "本科就读于某某大学",
                "fact_text": "他人就读于某某大学",
                "evidence_quote": "本科就读于某某大学",
                "reliability": 0.9,
                "action": "create",
            }
        ],
    }
    assert stack.sync(DIRECTIVE, message_id="m-other") is not None
    stack.tick()
    run = stack.runs_for("m-other")[0]
    script.check(
        run.last_error == MATERIAL_SUBJECT_UNCERTAIN_ERROR
        and stack.atomic.list_items(ACCOUNT) == [],
        "模型判定主体非本人：如实报告主体不明（零写入）",
    )
    stack.close()


def journey_5_invalidation(script: _Script, tmp: Path) -> None:
    script.say("[5] 编辑/删除失效：旧片段不再展示、派生采用同步失效")
    gateway = _ScriptedGateway()
    stack = _Stack(tmp / "invalidate.db", gateway)
    resume = stack.attach_file(
        "m-resume",
        filename="张三-简历.pdf",
        text="姓名：张三\n本科就读于某某大学",
        chunks=[("姓名：张三", 1, "基本信息"), ("本科就读于某某大学", 2, "教育经历")],
    )
    gateway.material_output = _item_output(resume.object_id, "本科就读于某某大学")
    assert stack.sync(DIRECTIVE, message_id="m-resume") is not None
    stack.tick()
    item = stack.atomic.list_items(ACCOUNT)[0]
    script.check(
        [entry.fact_text for entry in stack.adopted(RESUME_QUERY).adopted_items]
        == ["本科就读于某某大学"],
        "前置：材料可用时正常采用",
    )

    replaced = stack.objects.create_object(
        ACCOUNT,
        "张三-简历-v2.pdf",
        "姓名：张三\n本科就读于某某大学（更新版）".encode(),
        "application/pdf",
    )
    stack.rebind_attachment("m-resume", replaced.object_id)
    projection = stack.atomic.item_evidence(ACCOUNT, item.profile_item_id)
    projected = projection.material_sources[0]
    slice_ = stack.adopted(RESUME_QUERY)
    script.say(
        "    材料被替换："
        f"quote={projected.quote}，quote_status={projected.quote_status.value}"
    )
    script.check(
        projected.quote is None and projected.quote_status.value == "source_unavailable",
        "旧片段不再展示（不伪造引用）",
    )
    script.check(
        not slice_.adopted_items and slice_.excluded_items[0].exclusion_reason
        == RECALL_MATERIAL_INVALID_REASON,
        "派生采用同步失效（材料来源已删除或权限失效）",
    )
    script.check(
        stack.atomic.list_items(ACCOUNT)[0].text == "本科就读于某某大学",
        "条目本身保留（排除只影响采用，不删除）",
    )

    stack.detach_message("m-resume")
    projection = stack.atomic.item_evidence(ACCOUNT, item.profile_item_id)
    script.check(
        projection.material_sources[0].status is AtomicProfileMaterialSourceStatus.DELETED,
        "撤回/删除材料后核验为 DELETED",
    )

    stack.rebind_attachment("m-resume", resume.object_id)
    projection = stack.atomic.item_evidence(ACCOUNT, item.profile_item_id)
    script.check(
        projection.material_sources[0].status is AtomicProfileMaterialSourceStatus.AVAILABLE
        and projection.material_sources[0].quote == "本科就读于某某大学"
        and [entry.fact_text for entry in stack.adopted(RESUME_QUERY).adopted_items]
        == ["本科就读于某某大学"],
        "重新绑定原材料后恢复展示与采用",
    )
    stack.close()


def journey_6_photo(script: _Script, tmp: Path) -> None:
    script.say("[6] 照片材料：整图 OCR → 提取 → 定位「整图」")
    gateway = _ScriptedGateway(ocr_text="姓名：张三\n教育经历\n硕士就读于某某大学")
    stack = _Stack(tmp / "photo.db", gateway)
    photo = stack.attach_photo("m-photo", filename="简历照片.png")
    gateway.material_output = _item_output(photo.object_id, "硕士就读于某某大学")

    assert stack.sync("这是成绩单照片，请更新我的背景", message_id="m-photo") is not None
    stack.tick()
    items = stack.atomic.list_items(ACCOUNT)
    run = stack.runs_for("m-photo")[0]
    script.check(
        gateway.calls == ["qwen_ocr", "qwen_profile_extraction"]
        and len(items) == 1,
        "先整图识别再提取（qwen_ocr → qwen_profile_extraction）",
    )
    source = items[0].source_materials[0]
    script.check(
        items[0].text == "硕士就读于某某大学"
        and source.locator == "整图"
        and source.media_type == "image/png"
        and source.quote == "硕士就读于某某大学",
        "照片事实来源：整图 + 原话片段",
    )
    script.check(
        run.outcome is ProfileExtractionOutcome.SUCCEEDED_WRITTEN,
        "照片材料运行成功写入",
    )
    stack.close()

    gateway = _ScriptedGateway(
        ocr_text="姓名：张三\n硕士就读于某某大学", ocr_lock=False
    )
    stack = _Stack(tmp / "photo-no-lock.db", gateway)
    stack.attach_photo("m-photo", filename="简历照片.png")
    assert stack.sync("这是成绩单照片，请更新我的背景", message_id="m-photo") is not None
    for _ in range(MATERIAL_MAX_WAIT_ATTEMPTS):
        stack.tick()
    run = [
        item
        for item in stack.state.list_runs(ACCOUNT)
        if item.message_id == "m-photo"
    ][0]
    script.check(
        run.last_error == "profile_extraction_material_read_failed"
        and stack.atomic.list_items(ACCOUNT) == []
        and set(gateway.calls) == {"qwen_ocr"},
        "OCR 未产生运行锁：失败关闭，零写入并如实记录",
    )
    stack.close()


def journey_7_restart_and_isolation(script: _Script, tmp: Path) -> None:
    script.say("[7] 重启与跨账户：run_kind/来源保留；跨账户不可见")
    path = tmp / "restart.db"
    gateway = _ScriptedGateway()
    stack = _Stack(path, gateway)
    resume = stack.attach_file(
        "m-resume",
        filename="张三-简历.pdf",
        text="姓名：张三\n本科就读于某某大学",
        chunks=[("姓名：张三", 1, "基本信息"), ("本科就读于某某大学", 2, "教育经历")],
    )
    gateway.material_output = _item_output(resume.object_id, "本科就读于某某大学")
    assert stack.sync(DIRECTIVE, message_id="m-resume") is not None
    stack.tick()
    item = stack.atomic.list_items(ACCOUNT)[0]
    stack.close()

    reopened = _Stack(path, gateway)
    runs = reopened.runs_for("m-resume")
    restored = reopened.atomic.get_item(ACCOUNT, item.profile_item_id)
    script.check(
        runs[0].run_kind is ProfileExtractionKind.MATERIAL
        and runs[0].outcome is ProfileExtractionOutcome.SUCCEEDED_WRITTEN,
        "重启后运行 kind 与结果一致",
    )
    script.check(
        restored.source_materials[0].quote == "本科就读于某某大学"
        and restored.source_materials[0].locator == "第 2 页 · 教育经历",
        "重启后材料来源（对象/定位/原话）保留",
    )
    projection = reopened.atomic.item_evidence(ACCOUNT, item.profile_item_id)
    script.check(
        projection.material_sources[0].status
        is AtomicProfileMaterialSourceStatus.AVAILABLE,
        "重启后材料仍可核验",
    )

    reopened.objects.ensure_account(OTHER_ACCOUNT, "demo-bob@example.com")
    script.check(
        reopened.atomic.list_items(OTHER_ACCOUNT) == [],
        "跨账户：看不到任何条目",
    )
    foreign = reopened.reader.list_designated_materials(
        OTHER_ACCOUNT,
        conversation_id=CONVERSATION,
        message_id="m-resume",
        lookback=3,
        max_materials=6,
    )
    script.check(foreign == [], "跨账户：读不到会话里的材料")
    reopened.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 10 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        "临时库 .tmp/issue10-demo-*；模型网关为脚本化替身"
    )

    tmp_root = REPO_ROOT / ".tmp" / f"issue10-demo-{stamp}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    for journey in (
        journey_1_ordinary_usage,
        journey_2_own_resume,
        journey_3_refused,
        journey_4_unavailable,
        journey_5_invalidation,
        journey_6_photo,
        journey_7_restart_and_isolation,
    ):
        script.say("")
        journey(script, tmp_root)

    script.say("")
    script.say(
        f"结果：{'全部通过' if script.failures == 0 else f'{script.failures} 项失败'}"
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
