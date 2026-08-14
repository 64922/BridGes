"""知识库图片 OCR 端口（Issue 14）。

摄取服务只依赖本文件定义的端口，不在解析器内发起网络请求。生产端口把
每次真实页面/图片 OCR 调用统一到 Issue 09 注册的 ``qwen_ocr`` 能力
（经 ``ModelGateway`` 解析固定模型、区域、能力版本与重试政策，全部来自
``bridges.ai.fixed_models`` 单一事实源）与 Issue 10 的运行记录接缝
（``ModelRunLockRecorder``），端口内不再自建生产能力快照，也不直接
构造 client/adapter。

锁语义：

- 每个真正发往 Qwen 的页面/图片请求恰好形成一条独立、幂等、持久化锁：
  ``lock_id`` 由 ``(run_id, page_ordinal, call_ordinal)`` 确定性派生，
  同一 page-call 重放（如崩溃后重试同一轮处理）幂等合并，绝不产生重复行；
- 真正重新发起供应商请求（worker 重试/新一轮摄取 run）使用新 run 与新
  调用序号，旧失败锁原样保留，不被覆盖；
- 成功、鉴权失败、限流、区域错误、网络异常、空输出与适配器失败都保存
  准确状态的锁；空输出以 ``ocr_empty_output`` 失败锁如实记录；
- 未绑定真实适配器（缺全局 Key / 生产门禁失败关闭）时直接抛领域错误，
  不产生伪锁；
- 锁、日志与错误绝不包含 Key、Authorization、图片 bytes、base64、
  OCR prompt 或 OCR 文本（recorder 白名单二次把关）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.ai.errors import ModelRunLockError
from bridges.ai.model_gateway import ModelGateway
from bridges.ai.ports import ModelRunLockRecorder
from bridges.contracts.ai import (
    BusinessRef,
    ModelCallResult,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.workflows import RunContextEnvelope

# 与既有 media Qwen OCR 管线保持同一固定 prompt；这里不导入 media 包，
# 避免 media → chat → ingestion 的模块循环。
OCR_IMAGE_PROMPT = (
    "Extract all visible text from this scientific image. "
    "Preserve the reading order. Do not add commentary."
)

#: 知识库 OCR 统一使用的注册能力（Issue 09 单一事实源中的逻辑名/版本）。
OCR_CAPABILITY_NAME = "qwen_ocr"
OCR_CAPABILITY_VERSION = "1"


class OcrError(Exception):
    """图片文字识别失败；message 为面向用户的中文原因。"""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class OcrPageRequest:
    """一次页级 OCR 调用的完整输入合同（Issue 14 扩展）。

    至少携带账户、知识库对象/文档 ID、摄取 run ID、稳定页/嵌入图片序号、
    调用序号、媒体类型与内容哈希引用；禁止再用可复用的
    ``knowledge-base-ocr-{account_id}`` 作为唯一关联。
    """

    account_id: str = ""
    object_id: str = ""
    document_id: str = ""
    run_id: str = ""
    page_ordinal: int = 1
    call_ordinal: int = 1
    media_type: str = "image/png"
    content_hash: str = ""
    content: bytes = b""


@dataclass(frozen=True)
class OcrPageSummary:
    """一次摄取处理的脱敏 OCR 页数汇总（不含任何文本/内容）。"""

    pages_total: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0


class OcrPort(Protocol):
    """图片文字识别端口：生产实现调用 Qwen，测试注入确定性替身。"""

    def extract(self, request: OcrPageRequest) -> str:
        """识别请求指定的一页/一张图片中的可见文字；失败抛出 OcrError。

        成功或失败（含空输出）都会由实现按 Issue 14 合同持久化页级运行锁。
        """


def _page_lock_id(request: OcrPageRequest) -> str:
    """按 (run, page, call) 确定性派生锁 ID。

    同一摄取 run 内同一页同一次调用重放 → 同一 lock_id → recorder 幂等；
    真正的新调用（新 run 或新调用序号）→ 新 lock_id → 新锁行，旧锁保留。
    """
    return (
        f"kb-ocr:{request.run_id}:p{request.page_ordinal}:"
        f"c{request.call_ordinal}"
    )


class QwenOcrPort:
    """经统一 ModelGateway + ModelRunLockRecorder 执行单页图片文字识别。"""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        recorder: ModelRunLockRecorder,
    ) -> None:
        self._gateway = gateway
        self._recorder = recorder

    def extract(self, request: OcrPageRequest) -> str:
        """只把当前图片与固定 prompt 发往注册的 ``qwen_ocr`` 能力。"""
        if not self._gateway.is_adapter_registered(
            OCR_CAPABILITY_NAME, OCR_CAPABILITY_VERSION
        ):
            # 生产门禁语义：缺少真实适配器（未配置全局 Key、Stub/矩阵漂移
            # 已失败关闭）时 OCR 能力失败关闭，不产生任何伪锁。
            raise OcrError(
                "图片文字识别未启用：未配置全局百炼运行凭据，请检查启动服务的全局配置。"
            )
        if not request.content:
            raise OcrError("图片文字识别失败：图片内容为空。")

        run_context = RunContextEnvelope(
            run_id=request.run_id,
            account_id=request.account_id,
            project_id="",
            workflow_name="knowledge_base_ingestion",
            workflow_version="1",
            submitted_at=datetime.now(UTC),
        )
        result = self._gateway.invoke(
            OCR_CAPABILITY_NAME,
            OCR_CAPABILITY_VERSION,
            run_context,
            payload={
                "image_base64": base64.b64encode(request.content).decode("ascii"),
                "mime_type": request.media_type,
                "prompt": OCR_IMAGE_PROMPT,
                "temperature": 0.01,
                "max_tokens": 4096,
            },
        )
        lock = result.lock
        if lock is None:
            # 网关未产生任何尝试锁（理论上不发生）：失败关闭且不落锁。
            raise OcrError(
                "图片文字识别失败：OCR 服务调用未成功。", retryable=True
            )

        if result.status == ModelCallStatus.SUCCESS:
            output = result.output or {}
            text = output.get("content", "")
            if not isinstance(text, str) or not text.strip():
                self._record(request, self._empty_output_lock(lock))
                raise OcrError("图片文字识别失败：OCR 未返回可用文字。")
            self._record(request, lock)
            return text.strip()

        # 鉴权/限流/区域/网络/适配器失败：先落准确状态的失败锁，再按既有
        # 合同向上抛出领域错误（上层诚实降级，绝不把失败页标成 OCR 成功）。
        self._record(request, lock)
        retryable = result.status == ModelCallStatus.RETRYABLE_FAIL
        raise OcrError(self._message_for(result), retryable=retryable)

    # ------------------------------------------------------------------
    # 锁持久化
    # ------------------------------------------------------------------

    def _record(self, request: OcrPageRequest, lock: ModelRunLock) -> None:
        """把一次真实请求的锁幂等持久化并关联业务对象。

        锁只携带审计所需非秘密字段；业务关联指向知识库文档（
        ``document_id`` 由 ``doc-{object_id}`` 派生，可回查知识库对象），
        页序号进入 operation、调用序号进入 attempt ordinal。
        """
        try:
            self._recorder.record(
                lock.model_copy(update={"lock_id": _page_lock_id(request)}),
                business_ref=BusinessRef(
                    object_type="document",
                    object_id=request.document_id,
                    operation=f"ocr_page:{request.page_ordinal}",
                    attempt_ordinal=request.call_ordinal,
                    is_primary=(
                        request.page_ordinal == 1 and request.call_ordinal == 1
                    ),
                ),
            )
        except ModelRunLockError as exc:
            # Issue 10 失败关闭合同：锁写不进审计就不允许把本次结果当作
            # 「已 OCR」投影；可重试，由上层决定重试或诚实降级。
            raise OcrError(
                "图片文字识别失败：OCR 审计记录写入失败，本次结果未计入。",
                retryable=True,
            ) from exc

    @staticmethod
    def _empty_output_lock(lock: ModelRunLock) -> ModelRunLock:
        """空输出时把网关成功锁调整为准确状态的失败锁（仍是一条锁）。"""
        return lock.model_copy(
            update={
                "status": ModelCallStatus.BLOCKED,
                "error_code": "ocr_empty_output",
                "error_message": "图片文字识别失败：OCR 未返回可用文字。",
                "degradation_reason": "图片文字识别失败：OCR 未返回可用文字。",
            }
        )

    @staticmethod
    def _message_for(result: ModelCallResult) -> str:
        """把网关稳定错误码映射为面向用户的中文原因（不泄露正文）。"""
        code = result.error_code or ""
        if code == "auth_error":
            return (
                "图片文字识别失败：全局百炼凭据无效或没有 OCR 模型权限，"
                "请检查启动服务的全局配置与权限。"
            )
        if code == "region_error":
            return "图片文字识别失败：区域接入点不可达，请检查网络。"
        if code == "rate_limit":
            return "图片文字识别失败：请求过于频繁（限流），稍后重试即可。"
        if code == "transient":
            return "图片文字识别失败：服务暂时不可用或网络异常，稍后重试即可。"
        if code == "actual_model_mismatch":
            return "图片文字识别失败：模型矩阵漂移，调用失败关闭。"
        return "图片文字识别失败：OCR 服务调用未成功。"
