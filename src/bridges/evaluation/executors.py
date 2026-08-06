"""评测案例执行器与评测环境（Issue 40）。

评测环境在临时数据库上装配生产服务（真实编排 + 可编程模型网关与外围
替身），每个维度执行器通过生产缝驱动被测系统，产出结构化产物、工具
记录、状态轨迹与模型调用锁——这些原始结果随后由 metrics 模块计算
确定性指标，由 judges 模块完成裁判评分。

被测系统的差异只在特性开关：完整 BridGes 启用全部特性，基线只做纯
模型调用，参考方法不调用模型（reference_method 模块），消融关闭单一
特性。可编程网关按案例脚本响应：脚本编码「模型质量随上下文变化」的
固定行为，证据/画像/体裁规则被正确注入时给出高质量响应，否则给出
对应缺陷响应，使 A/B 对比可复现、可解释。
"""

from __future__ import annotations

import http.server
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from bridges.ai.adapters import AdapterResult
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.ai.model_gateway import ModelGateway
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.career.service import CareerPlannerService
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.contracts.evaluation_suite import (
    EvalCase,
    ToolCallRecord,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.credentials.probes import CapabilityProbeService
from bridges.credentials.store import CredentialStorePort
from bridges.image.service import ImageService
from bridges.ingestion.embedding import DeterministicEmbeddingPort
from bridges.ingestion.index import VersionedIndex
from bridges.ingestion.service import IngestionService
from bridges.learning.adapters import InMemoryLearningRepository
from bridges.learning.service import LearningService
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.observability.service import ObservabilityService
from bridges.profiles.service import ProfileService
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.reminder.service import ReminderService
from bridges.reminder.smtp import QqMailGateway
from bridges.retrieval.service import LayeredRetrievalService
from bridges.skills.humanizer.service import HumanizerService
from bridges.skills.registry import create_builtin_registry
from bridges.speech.service import SpeechService
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)
from bridges.video.service import VideoService
from bridges.web_search.service import WebSearchService

#: 合成评测账户的 QQ 邮箱（纯数字 @qq.com，与产品账户格式一致）。
EVAL_QQ_EMAIL = "10000000@qq.com"

#: 固定模型矩阵（与 credentials/matrix.py 同源：能力名 -> 固定模型快照）。
MODEL_BY_CAPABILITY: dict[str, str] = {
    "qwen_text_chat": "qwen3.7-plus-2026-05-26",
    "qwen_structured_output": "qwen3.7-plus-2026-05-26",
    "qwen_asr_short": "qwen3-asr-flash",
    "qwen_tts": "qwen3-tts-flash-2025-11-27",
    "qwen_image": "qwen-image-2.0-pro-2026-06-22",
    "qwen_wan": "wan2.7-t2v-2026-06-12",
}
#: 本地资产服务器端口基址（TTS/图片/视频下载）。
_EVAL_ASSET_PORT = 18763



class CaseOutcome:
    """一个案例的原始执行结果（指标与裁判的输入）。"""

    def __init__(
        self,
        *,
        outputs: dict[str, Any],
        tool_records: list[ToolCallRecord],
        trajectory: list[str],
        model_locks: list[ModelRunLock],
        latency_ms: int,
    ) -> None:
        self.outputs = outputs
        self.tool_records = tool_records
        self.trajectory = trajectory
        self.model_locks = model_locks
        self.latency_ms = latency_ms


class CaseExecutionError(Exception):
    """案例执行错误：携带稳定错误码与中文原因。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class LocalAssetServer:
    """本地确定性资产服务器：为 TTS/图片/视频下载提供固定字节。"""

    def __init__(self, assets: dict[str, bytes], media_types: dict[str, str]) -> None:
        self._assets = assets
        self._media_types = media_types
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = _EVAL_ASSET_PORT

    def start(self) -> None:
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = assets.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", media_types.get(self.path,
                    "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # 静默
                return

        assets = self._assets
        media_types = self._media_types
        server = http.server.HTTPServer(("127.0.0.1", self._port), _Handler)
        self._server = server
        # 短轮询间隔：shutdown() 等待时间 = poll_interval（默认 0.5s 太慢）。
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01),
            daemon=True,
        )
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._port}{path}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


class ScriptedAdapter:
    """可编程能力适配器：按案例脚本响应模型调用，记录全部载荷。

    脚本规则（``case.initial_state["script"]``）按载荷文本子串首中优先；
    空 needle 为默认规则。结构化能力（humanizer/生涯）的答案是字典，
    聊天/听写/朗读/图片/视频按各自适配器输出形状返回。
    """

    def __init__(self, case_scripts: Callable[[str], dict[str, Any] | None]) -> None:
        self._case_scripts = case_scripts
        self.captured_payloads: list[dict[str, Any]] = []
        self._failed_tasks: set[str] = set()
        #: 已注入失败的案例（重试再提交不重复注入）。
        self._injected_failures: set[str] = set()
        #: 当前执行的案例（服务内部自建 run_context 时按此回退）。
        self._current_case_id: str | None = None

    def set_current_case(self, case_id: str) -> None:
        """设置当前执行案例（执行器在案例开始前调用）。"""
        self._current_case_id = case_id

    def _script(self, run_id: str) -> dict[str, Any] | None:
        case_id = self._case_id_from_run(run_id)
        if case_id is None:
            case_id = self._current_case_id
        if case_id is None:
            return None
        return self._case_scripts(case_id)

    @staticmethod
    def _case_id_from_run(run_id: str) -> str | None:
        """从运行标识还原案例标识（案例标识可含连字符，尾部固定三段序号）。"""
        if not run_id.startswith("eval-"):
            return None
        body = run_id[len("eval-") :]
        parts = body.rsplit("-", 3)
        # parts 形如 [<case_id>, <seed>, <execution>, <turn>]。
        if len(parts) != 4:
            return None
        return parts[0]

    def _rule_answer(self, script: dict[str, Any], payload: dict[str, Any]) -> Any:
        text = "\n".join(
            str(message.get("content", ""))
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        )
        for rule in script.get("rules", []):
            needle = str(rule.get("match", ""))
            if needle == "" or needle in text:
                return rule.get("answer")
        return ""

    # ------------------------------------------------------------------
    # 能力分派
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        run_id = run_context.run_id
        script = self._script(run_id)
        self.captured_payloads.append(
            {
                "run_id": run_id,
                "capability": capability.name,
                "payload": payload,
            }
        )

        if capability.name == "qwen_text_chat":
            answer = self._rule_answer(script or {}, payload)
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"content": str(answer)},
            )
        if capability.name == "qwen_structured_output":
            answer = self._rule_answer(script or {}, payload)
            if isinstance(answer, dict):
                return AdapterResult(
                    actual_model_id=capability.model_id,
                    output=answer,
                )
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"content": str(answer)},
            )
        if capability.name == "qwen_asr_short":
            transcript = str((script or {}).get("transcript", ""))
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={"transcript": transcript, "language": "zh-CN"},
            )
        if capability.name == "qwen_tts":
            audio = (script or {}).get("tts_audio_bytes", b"")
            audio_url = "" if not audio else self._server_url("/eval-tts.wav")
            return AdapterResult(
                actual_model_id=capability.model_id,
                output={
                    "audio_url": audio_url,
                    "audio_id": "eval-tts",
                    "expires_at": 9999999999,
                    "mime_type": "audio/wav",
                    "processed_text": str(payload.get("text", "")),
                    "degraded_pronunciation_notes": [],
                },
            )
        if capability.name == "qwen_image":
            return self._image(script or {}, payload)
        if capability.name == "qwen_wan":
            return self._video(script or {}, payload)
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={"content": ""},
        )

    def _server_url(self, path: str) -> str:
        # 由 EvalEnvironment 注入。
        if self._assets is None:
            return ""
        return self._assets.url(path)

    _assets: LocalAssetServer | None = None

    def _image(self, script: dict[str, Any], payload: dict[str, Any]) -> AdapterResult:
        kind = str(payload.get("kind", ""))
        cloud_task_id = str(payload.get("cloud_task_id", ""))
        if kind == "submit":
            task_key = f"img-{secrets.token_urlsafe(6)}"
            case_id = self._current_case_id or ""
            if script.get("fail_first_poll") and case_id not in self._injected_failures:
                self._failed_tasks.add(task_key)
                self._injected_failures.add(case_id)
            return AdapterResult(
                actual_model_id=None,
                output={"cloud_task_id": task_key},
            )
        if kind == "poll":
            if cloud_task_id in self._failed_tasks:
                self._failed_tasks.discard(cloud_task_id)
                return AdapterResult(
                    actual_model_id=None,
                    output={"cloud_status": "FAILED", "error_message": "注入的云端失败"},
                )
            image_bytes = script.get("image_bytes", b"")
            if not image_bytes:
                return AdapterResult(
                    actual_model_id=None,
                    output={"cloud_status": "RUNNING"},
                )
            return AdapterResult(
                actual_model_id=None,
                output={
                    "cloud_status": "SUCCEEDED",
                    "result_url": self._server_url("/eval-img.png"),
                },
            )
        if kind == "fetch":
            return AdapterResult(
                actual_model_id=None,
                output={
                    "image_bytes": script.get("image_bytes", b"eval-image"),
                    "media_type": "image/png",
                },
            )
        return AdapterResult(actual_model_id=None, output={"cloud_status": "RUNNING"})

    def _video(self, script: dict[str, Any], payload: dict[str, Any]) -> AdapterResult:
        kind = str(payload.get("kind", ""))
        cloud_task_id = str(payload.get("cloud_task_id", ""))
        if kind == "submit":
            task_key = f"video-{secrets.token_urlsafe(6)}"
            case_id = self._current_case_id or ""
            if script.get("fail_first_poll") and case_id not in self._injected_failures:
                self._failed_tasks.add(task_key)
                self._injected_failures.add(case_id)
            return AdapterResult(
                actual_model_id=None,
                output={"cloud_task_id": task_key},
            )
        if kind == "poll":
            if cloud_task_id in self._failed_tasks:
                self._failed_tasks.discard(cloud_task_id)
                return AdapterResult(
                    actual_model_id=None,
                    output={"cloud_status": "FAILED", "error_message": "注入的云端失败"},
                )
            video_bytes = script.get("video_bytes", b"")
            if not video_bytes:
                return AdapterResult(
                    actual_model_id=None,
                    output={"cloud_status": "RUNNING"},
                )
            return AdapterResult(
                actual_model_id=None,
                output={
                    "cloud_status": "SUCCEEDED",
                    "result_url": self._server_url("/eval-video.mp4"),
                },
            )
        if kind == "fetch":
            return AdapterResult(
                actual_model_id=None,
                output={
                    "video_bytes": script.get("video_bytes", b"eval-video"),
                    "media_type": "video/mp4",
                },
            )
        if kind == "cancel":
            return AdapterResult(actual_model_id=None, output={"cancelled": True})
        return AdapterResult(actual_model_id=None, output={"cloud_status": "RUNNING"})

    # ------------------------------------------------------------------
    # CapabilityAdapter / StreamingCapabilityAdapter 协议
    # ------------------------------------------------------------------

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return self._dispatch(capability, run_context, payload)

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Iterator[Any]:
        result = self._dispatch(capability, run_context, payload)
        content = str(result.output.get("content", ""))
        if content:
            yield from _split_chunks(content)


def _split_chunks(content: str) -> list[Any]:
    """把完整回答按字切分为流式增量块。"""
    from bridges.ai.adapters import StreamChunk

    return [StreamChunk(delta=char) for char in content]


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
        prompt_version="1",
    )


def _structured_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_structured_output",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="structured-v1",
        output_schema_version="structured-v1",
        prompt_version="1",
    )


def _capability(name: str, model_id: str) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=model_id,
        input_schema_version=f"{name}-v1",
        output_schema_version=f"{name}-v1",
        prompt_version="1",
    )


class EvalEnvironment:
    """评测环境：临时数据库上装配生产服务（真实编排 + 可编程网关）。

    ``template_path``：已初始化（迁移完成）的模板数据库文件；提供时直接
    拷贝为 ``db_path``，跳过重复迁移，保证每个案例执行从同一确定性状态
    开始（跨案例/跨种子不累积知识库与画像状态）。
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        template_path: str | Path | None = None,
    ) -> None:
        if template_path is not None:
            if db_path is None:
                raise ValueError("使用模板库时必须提供 db_path。")
            import shutil

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template_path, db_path)
        self.database = BridgesDatabase(db_path or ":memory:")
        if template_path is None:
            self.database.initialize()
        root = Path(db_path).parent if db_path else Path(tempfile.gettempdir())
        self.object_repository = BridgesObjectRepository(
            self.database,
            EncryptedFileObjectStore(
                root / f"eval-objects-{id(self)}",
                encryption_key=SecretStr("eval-secret-key"),
            ),
        )
        self.observability = ObservabilityService()
        # 合成评测账户（不来自真实用户）；注册返回稳定内部账户 ID。
        self.account_id = self.object_repository.register_account(EVAL_QQ_EMAIL)
        self.conversations = ConversationRepository(self.database)
        self.registry = CapabilityRegistry()
        for record in (
            _chat_capability(),
            _structured_capability(),
            _capability("qwen_asr_short", "qwen3-asr-flash"),
            _capability("qwen_tts", "qwen3-tts-flash-2025-11-27"),
            _capability("qwen_image", "qwen-image-2.0-pro-2026-06-22"),
            _capability("qwen_wan", "wan2.7-t2v-2026-06-12"),
        ):
            self.registry.register(record)
        self.scripted = ScriptedAdapter(self._script_for_case)
        self.gateway = ModelGateway(self.registry)
        for name in (
            "qwen_text_chat",
            "qwen_structured_output",
            "qwen_asr_short",
            "qwen_tts",
            "qwen_image",
            "qwen_wan",
        ):
            self.gateway.register_adapter(name, "1", self.scripted)

        self.probe_service = CapabilityProbeService(state_store=None)
        self._seed_embedding_probe()
        self.embedding = DeterministicEmbeddingPort()
        self.ingestion = IngestionService(
            database=self.database,
            object_repository=self.object_repository,
            probe_service=self.probe_service,
            embedding=self.embedding,
            index=VersionedIndex(self.database, self.embedding),
        )
        self.retrieval = LayeredRetrievalService(
            database=self.database,
            embedding=self.embedding,
            probe_service=self.probe_service,
            object_repository=self.object_repository,
        )
        self.profiles = ProfileService(repository=SqliteProfileRepository(self.database))
        self.learning = LearningService(repository=InMemoryLearningRepository())
        self.skill_registry = create_builtin_registry()
        self.humanizer = HumanizerService(
            registry=self.skill_registry,
            gateway=self.gateway,
            retrieval_service=self.retrieval,
            observability_service=self.observability,
        )
        self.career = CareerPlannerService(
            gateway=self.gateway,
            profile_service=self.profiles,
            learning_service=self.learning,
            observability_service=self.observability,
        )
        self.speech = SpeechService(
            gateway=self.gateway,
            object_repository=self.object_repository,
            chat_repository=self.conversations,
            observability_service=self.observability,
            # 本地资产下载不走系统代理（环境代理会劫持 127.0.0.1）。
            download_client=httpx.Client(timeout=10.0, trust_env=False),
        )
        self.image = ImageService(
            database=self.database,
            gateway=self.gateway,
            object_repository=self.object_repository,
            chat_repository=self.conversations,
            observability_service=self.observability,
        )
        self.video = VideoService(
            database=self.database,
            gateway=self.gateway,
            object_repository=self.object_repository,
            chat_repository=self.conversations,
            observability_service=self.observability,
        )
        self.reminder = ReminderService(
            database=self.database,
            credential_store=_InMemorySmtpStore(),
            observability_service=self.observability,
            qq_email_provider=lambda account_id: EVAL_QQ_EMAIL,
            profile_service=self.profiles,
            gateway=_FakeMailGateway(),
            verify_async=False,
        )
        self.mail_gateway = self.reminder._gateway  # noqa: SLF001 - 同包注入失败用
        self.web_search = WebSearchService(
            client=_FakeSearchClient(), observability=self.observability
        )
        self.arxiv_search = ArxivSearchService(
            client=_FakeArxivClient(), observability=self.observability
        )
        self.chat = ChatService(
            repository=self.conversations,
            gateway=self.gateway,
            retrieval_service=self.retrieval,
            web_search_service=self.web_search,
            arxiv_search_service=self.arxiv_search,
            profile_service=self.profiles,
            observability_service=self.observability,
            humanizer_service=self.humanizer,
            career_planner_service=self.career,
            image_service=self.image,
            video_service=self.video,
            teaching_service=TeachingTurnService(),
        )

        # 本地资产服务器（TTS/图片/视频下载端点）。
        self.assets = LocalAssetServer(
            assets={
                "/eval-tts.wav": b"eval-tts-audio-bytes",
                "/eval-img.png": b"eval-image-png-bytes",
                "/eval-video.mp4": b"eval-video-mp4-bytes",
            },
            media_types={
                "/eval-tts.wav": "audio/wav",
                "/eval-img.png": "image/png",
                "/eval-video.mp4": "video/mp4",
            },
        )
        self.assets.start()
        self.scripted._assets = self.assets  # noqa: SLF001 - 同模块注入

    def _script_for_case(self, case_id: str) -> dict[str, Any] | None:
        case = self.cases.get(case_id)
        if case is None:
            return None
        script: dict[str, Any] = case.initial_state.get("script", {})
        return script

    cases: dict[str, EvalCase] = {}

    def _seed_embedding_probe(self) -> None:
        self.probe_service._put_record(  # noqa: SLF001 - 与 tests/ingestion/conftest 同模式
            self.account_id,
            ProbeRecord(
                probe_id=f"probe-{EVAL_QQ_EMAIL}",
                capability_id="embedding",
                model_id="text-embedding-v4",
                region="cn-beijing",
                parameters={"dimensions": 1024},
                status=ProbeStatus.AVAILABLE,
                probed_at=datetime.now(UTC),
            ),
        )

    def seed_knowledge_base(self, case: EvalCase) -> None:
        """把案例的知识库材料摄取为全局知识库（可检索证据）。"""
        docs = case.initial_state.get("kb_docs", [])
        for doc in docs:
            stored = self.object_repository.create_object(
                self.account_id,
                str(doc.get("filename", "材料.txt")),
                str(doc.get("content", "")).encode("utf-8"),
                media_type="text/plain",
            )
            self.ingestion.enqueue(self.account_id, stored.object_id)
        self.ingestion.process_pending()

    def run_context(self, case: EvalCase, seed: int, execution_index: int,
        turn: int) -> RunContextEnvelope:
        return RunContextEnvelope(
            run_id=f"eval-{case.case_id}-{seed}-{execution_index}-{turn}",
            account_id=self.account_id,
            project_id=self.account_id,
            workflow_name="chat",
            workflow_version="1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            submitted_at=datetime.now(UTC),
        )

    def close(self) -> None:
        self.assets.stop()
        self.database.close()


class _InMemorySmtpStore(CredentialStorePort):
    """SMTP 授权码的内存凭据存储（不落盘真实密钥）。"""

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[str, str] = {}

    def save(self, account_id: str, secret: SecretStr) -> None:
        self._values[account_id] = secret.get_secret_value()

    def get(self, account_id: str) -> SecretStr | None:
        value = self._values.get(account_id)
        return SecretStr(value) if value is not None else None

    def delete(self, account_id: str) -> None:
        self._values.pop(account_id, None)


class _FakeMailGateway(QqMailGateway):
    """确定性邮件网关替身：按脚本返回发送结果，失败可注入。"""

    def __init__(self) -> None:
        super().__init__(imap_connect=lambda: None)
        self.outcome = "sent"
        self.sent: list[dict[str, Any]] = []

    def send_mail(
        self,
        *,
        from_addr: str,
        to_addr: str,
        auth_code: str,
        subject: str,
        body: str,
        message_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        if self.outcome == "fail":
            raise _SmtpError("smtp_transient", "注入的邮件服务暂时不可用", retryable=True)
        self.sent.append(
            {
                "from_addr": from_addr,
                "to_addr": to_addr,
                "subject": subject,
                "message_id": message_id,
            }
        )
        return "250 OK"

    def verify_self_send_receive(
        self, *, email: str, auth_code: str | None
    ) -> str:
        return "250 OK"


class _SmtpError(Exception):
    def __init__(self, code: str, message: str, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class _FakeSearchClient:
    """确定性 DuckDuckGo 替身：记录查询并返回固定结果（用于教学强制联网）。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[Any]:
        self.queries.append(query)
        from bridges.web_search.contracts import WebSearchResult

        return [
            WebSearchResult(
                result_id=f"eval-web-{index}",
                title=f"公开资料 {index + 1}",
                site="example.edu",
                url=f"https://example.edu/doc/{index + 1}",
                snippet=f"关于「{query[:20]}」的公开资料片段 {index + 1}。",
                accessed_at=datetime.now(UTC),
            )
            for index in range(2)
        ]


class _FakeArxivClient:
    """确定性 arXiv 替身：记录查询并返回固定论文元数据。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, *, max_results: int = 5) -> list[Any]:
        self.queries.append(query)
        from bridges.arxiv_mcp.contracts import ArxivPaper

        return [
            ArxivPaper(
                arxiv_id="2501.00001",
                title=f"关于 {query[:30]} 的评测论文",
                authors=["Eval Author"],
                published_at=datetime(2025, 1, 1, tzinfo=UTC),
                abs_url="https://arxiv.org/abs/2501.00001",
                pdf_url="https://arxiv.org/pdf/2501.00001",
                abstract="合成评测论文摘要。",
            )
        ]


__all__ = [
    "CaseOutcome",
    "CaseExecutionError",
    "EvalEnvironment",
    "ScriptedAdapter",
    "LocalAssetServer",
    "EVAL_QQ_EMAIL",
    "ModelCallStatus",
    "ToolCallRecord",
    "MODEL_BY_CAPABILITY",
    "_FakeMailGateway",
    "_FakeSearchClient",
]
