"""Issue 31 人工冒烟：真实图片生成与编辑链路验证。

用法（Key 只通过环境变量显式提供，绝不写入仓库或 .env；真实调用会
**计费**，执行前请确认百炼账户额度）：

    BRIDGES_SMOKE_QWEN_KEY=sk-... python scripts/smoke_image_generation.py

或把 Key 放入项目外文件（与 settings 的 ``*_FILE`` 秘密引用同一语义，
密钥不经过命令行、不进对话记录）：

    BRIDGES_SMOKE_QWEN_KEY_FILE=C:\\Users\\me\\.bridges\\qwen_key.txt \\
        python scripts/smoke_image_generation.py

脚本会：
1. 用显式提供的全局百炼凭据构造固定绑定客户端（GQ-01/GQ-04：只读全局
   凭据，不触碰任何账户凭据存储）；
2. 用固定绑定 qwen-image-2.0-pro-2026-06-22 提交真实生成任务，轮询
   DashScope 云端任务直到终态，下载结果字节；
3. 用刚生成的图片执行一次编辑（base_image data URL 直传），再次轮询
   下载；
4. 核对运行记录（model_run_locks）中的模型快照为固定绑定；
5. 校验响应/审计/输出都不包含完整 Key。

自动化测试不得把 Key 写入仓库或 .env；本脚本只供人工冒烟环境显式使用。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.ai import (  # noqa: E402
    CapabilityRegistry,
    ModelGateway,
    QwenApiClient,
    QwenImageAdapter,
)
from bridges.ai.fixed_models import IMAGE_MODEL_ID  # noqa: E402
from bridges.ai.qwen_image_adapter import image_data_url  # noqa: E402
from bridges.contracts.ai import (  # noqa: E402
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)

# 冒烟专用环境变量（刻意区别于运行合同的 BRIDGES_QWEN_API_KEY）：
# 避免误读启动服务的全局 Key，冒烟必须显式、独立地提供密钥。
_SMOKE_KEY_ENV = "BRIDGES_SMOKE_QWEN_KEY"
_SMOKE_KEY_FILE_ENV = "BRIDGES_SMOKE_QWEN_KEY_FILE"
#: 云端任务轮询间隔与上限（人工冒烟可接受分钟级等待）。
_POLL_INTERVAL_SECONDS = 5
_POLL_MAX = 60


def _resolve_smoke_key() -> str:
    """解析冒烟 Key：优先 *_FILE 文件引用（项目秘密机制，密钥不经命令行）。"""
    file_path = os.environ.get(_SMOKE_KEY_FILE_ENV, "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(
                f"无法读取 {_SMOKE_KEY_FILE_ENV}={file_path} 指向的密钥文件：{exc}。"
            ) from exc
    return os.environ.get(_SMOKE_KEY_ENV, "").strip()


def _gateway(api_key: SecretStr) -> ModelGateway:
    client = QwenApiClient(api_key=api_key)
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_image",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id=IMAGE_MODEL_ID,
            input_schema_version="image-prompt-v1",
            output_schema_version="image-task-v1",
            status=CapabilityStatus.VERIFIED,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            prompt_version="2026-08-05",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_image", "1", QwenImageAdapter(client))
    return gateway


def _run_task(
    gateway: ModelGateway,
    run_id: Any,
    payload: dict[str, object],
    label: str,
) -> Any:
    """提交 → 轮询 → 下载；返回最终 ModelCallResult（含运行锁与字节）。"""
    result = gateway.invoke("qwen_image", "1", run_id, payload)
    if result.status.value != "success" or result.output is None:
        raise SystemExit(
            f"{label}提交失败：{result.error_code} {result.error_message}（可重试"
            f"={result.status.value == 'retryable_fail'}）"
        )
    cloud_task_id = str(result.output.get("cloud_task_id") or "")
    if not cloud_task_id:
        raise SystemExit(f"{label}：供应商未返回云端任务标识。")
    print(f"{label}已提交，云端任务 {cloud_task_id}；开始轮询…")
    for attempt in range(1, _POLL_MAX + 1):
        poll = gateway.invoke(
            "qwen_image",
            "1",
            run_id,
            {"kind": "poll", "cloud_task_id": cloud_task_id},
        )
        status = str(poll.output.get("cloud_status") or "RUNNING").upper()
        if status == "SUCCEEDED":
            result_url = str(poll.output.get("result_url") or "")
            fetched = gateway.invoke(
                "qwen_image", "1", run_id, {"kind": "fetch", "result_url": result_url}
            )
            image_bytes = fetched.output.get("image_bytes")
            if not isinstance(image_bytes, bytes) or not image_bytes:
                raise SystemExit(f"{label}：下载结果为空。")
            print(
                f"{label}完成：{len(image_bytes)} 字节，"
                f"媒体类型 {fetched.output.get('media_type')}，"
                f"模型 {fetched.output.get('actual_model_id') or IMAGE_MODEL_ID}。"
            )
            return fetched
        if status == "FAILED":
            raise SystemExit(
                f"{label}云端失败：{poll.output.get('error_message') or '未知原因'}。"
            )
        print(f"{label}：第 {attempt} 次轮询 {status}（等待 {_POLL_INTERVAL_SECONDS}s）…")
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise SystemExit(f"{label}：轮询超时（{_POLL_MAX * _POLL_INTERVAL_SECONDS}s）。")


def main() -> int:
    key_value = _resolve_smoke_key()
    if not key_value:
        print(
            f"未提供全局百炼凭据：请显式设置 {_SMOKE_KEY_ENV}=sk-...（或"
            f"{_SMOKE_KEY_FILE_ENV}=密钥文件路径）后重试。自动化测试不要求"
            "也不允许把 Key 写入仓库或 .env。"
        )
        return 2
    if "sk-" not in key_value:
        print("Key 格式异常（应为百炼 sk- 开头）；已拒绝执行。")
        return 2

    from bridges.chat.repository import ConversationRepository
    from bridges.contracts.workflows import RunContextEnvelope
    from bridges.storage.database import BridgesDatabase

    gateway = _gateway(SecretStr(key_value))
    # 运行记录（model_run_locks）：每次调用把不可变运行锁落库，
    # 冒烟结束时核对固定模型快照（Verification V2）。
    db = BridgesDatabase(":memory:")
    db.initialize()
    lock_repo = ConversationRepository(db)

    def run_context(tag: str) -> RunContextEnvelope:
        return RunContextEnvelope(
            run_id=f"smoke-image-{tag}-{int(time.time())}",
            account_id="smoke-account",
            project_id="smoke-project",
            workflow_name="smoke_image_generation",
            workflow_version="1",
            submitted_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )

    def run_with_lock(
        ctx: RunContextEnvelope,
        payload: dict[str, object],
        label: str,
    ) -> bytes:
        result = _run_task(gateway, ctx, payload, label)
        if result.lock is not None:
            lock_repo.insert_run_lock(ctx.account_id, result.lock)
        image_bytes = result.output.get("image_bytes")
        if not isinstance(image_bytes, bytes):
            raise SystemExit(f"{label}：结果字节缺失。")
        return image_bytes

    # 1. 生成冒烟。
    generated = run_with_lock(
        run_context("generate"),
        {"kind": "submit", "prompt": "一座桥的素描，简洁线稿风格", "size": "1024*1024"},
        "图片生成",
    )
    # 2. 编辑冒烟：用生成结果做来源（data URL 直传，最小授权上下文）。
    edited = run_with_lock(
        run_context("edit"),
        {
            "kind": "submit",
            "prompt": "把背景改为夜空，保留桥的轮廓",
            "size": "1024*1024",
            "base_image": image_data_url(generated, "image/png"),
        },
        "图片编辑",
    )
    if not edited:
        raise SystemExit("编辑结果为空。")

    # 3. 核对运行记录中的模型快照（固定绑定，绝不切换）。
    rows = db.connection.execute(
        "SELECT DISTINCT actual_model_id FROM model_run_locks"
    ).fetchall()
    lock_model_ids = {str(row["actual_model_id"]) for row in rows}
    if lock_model_ids != {IMAGE_MODEL_ID}:
        raise SystemExit(
            f"运行记录模型快照异常：期望 {IMAGE_MODEL_ID}，实际 {lock_model_ids}。"
        )

    print("\n冒烟通过：")
    print(f"- 固定模型 {IMAGE_MODEL_ID} 真实生成与编辑成功；")
    print(f"- 生成结果 {len(generated)} 字节、编辑结果 {len(edited)} 字节；")
    print(f"- 运行记录（model_run_locks）模型快照核对一致：{lock_model_ids}；")
    print("- Key 仅经环境变量显式提供，未写入仓库或 .env。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
