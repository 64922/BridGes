"""Issue 10 人工冒烟：显式提供测试账户 Key，逐项执行真实能力探测。

用法（Key 只通过环境变量显式提供，绝不写入仓库或 .env）：

    BRIDGES_SMOKE_QWEN_KEY=sk-... conda run -n agent python scripts/smoke_key_probes.py

脚本会：
1. 用内存凭据替身保存显式提供的 Key（不触碰真实凭据库）；
2. 对 ADR-0009 固定六能力逐项执行真实探测（非用户数据）；
3. 打印每项状态与中文原因；
4. 校验探测响应、审计事件与进程输出都不包含完整 Key。

自动化测试不得把 Key 写入仓库或 .env；本脚本只供人工冒烟环境显式使用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.contracts.credentials import ProbeStatus  # noqa: E402
from bridges.credentials.probes import CapabilityProbeService  # noqa: E402
from bridges.credentials.service import KeyCredentialService  # noqa: E402
from bridges.credentials.store import InMemoryCredentialStore  # noqa: E402
from bridges.observability.service import ObservabilityService  # noqa: E402

_SMOKE_KEY_ENV = "BRIDGES_SMOKE_QWEN_KEY"


def main() -> int:
    key_value = os.environ.get(_SMOKE_KEY_ENV, "").strip()
    if not key_value:
        print(
            f"未提供测试账户 Key：请显式设置 {_SMOKE_KEY_ENV}=sk-... 后重试。"
            "自动化测试不要求也不允许把 Key 写入仓库或 .env。"
        )
        return 2

    service = KeyCredentialService(
        credential_store=InMemoryCredentialStore(),
        probe_service=CapabilityProbeService(),
        observability_service=ObservabilityService(),
        sync_probes=True,
    )
    account_id = "smoke-account"
    key = SecretStr(key_value)

    service.save(account_id, key)
    projection = service.get_projection(account_id)
    print(f"已保存测试 Key（尾号 {projection.key_tail}），开始逐项真实探测…\n")

    failed = 0
    for capability in projection.capabilities:
        status = "可用" if capability.status == ProbeStatus.AVAILABLE else "不可用"
        print(f"  [{status}] {capability.display_name}（{capability.model_id}）")
        if capability.message and capability.status != ProbeStatus.AVAILABLE:
            print(f"          原因：{capability.message}")
        if capability.status != ProbeStatus.AVAILABLE:
            failed += 1

    # 无 Key 泄漏校验：审计事件与投影 JSON 均不得包含完整 Key。
    leaked = key_value in projection.model_dump_json()
    events = service._observability.list_audit_events(account_id=account_id)
    leaked = leaked or any(
        key_value in event.model_dump_json() for event in events
    )
    if leaked:
        print("\n失败：完整 Key 出现在响应或审计事件中！")
        return 1
    print("\n校验通过：API 投影与审计事件均不含完整 Key。")
    print(f"探测完成：6 项中 {6 - failed} 项可用，{failed} 项不可用。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
