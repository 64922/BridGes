"""真实提供方 smoke（Test plan 6，默认跳过）。

在显式允许真实提供方的开发环境运行一次三方生成与多裁判 smoke，保存
脱敏运行摘要和机器可重放的裁判包。普通 CI 不运行本测试：
``BRIDGES_HUMANIZE_EVAL_REAL_SMOKE=1`` 且配置了
``BRIDGES_QWEN_API_KEY`` 时才执行。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bridges.humanize_eval.cases import validate_cases

_REAL_SMOKE_ENV = "BRIDGES_HUMANIZE_EVAL_REAL_SMOKE"


def _real_smoke_enabled() -> bool:
    return os.environ.get(_REAL_SMOKE_ENV, "") == "1"


pytestmark = pytest.mark.skipif(
    not _real_smoke_enabled(),
    reason="真实提供方 smoke 需显式启用（BRIDGES_HUMANIZE_EVAL_REAL_SMOKE=1）。",
)


def test_real_three_sut_generation_and_judge_smoke(tmp_path: Path):
    """真实三方生成 + 多裁判 + 脱敏摘要（真实凭据必须可用）。"""
    from bridges.humanize_eval.generation import QwenGenerationPort
    from bridges.humanize_eval.judges import build_judges
    from bridges.humanize_eval.runner import HumanizeRunner

    assert validate_cases() == []
    port = QwenGenerationPort()
    if not port.configured:
        pytest.skip("未配置 BRIDGES_QWEN_API_KEY，跳过真实 smoke。")
    judges = build_judges(port)
    runner = HumanizeRunner(
        outdir=tmp_path,
        workspace=Path.cwd(),
        port=port,
        judges=judges,
        anon_seed=2026,
        allow_real=True,
    )
    summary = runner.run()
    assert summary.sut_status == {
        "current-production": "success",
        "candidate": "success",
        "humanizer-zh-reference": "success",
    }, summary.sut_status
    assert summary.packet_id
    # 脱敏摘要不包含私人正文。
    summary_text = summary.model_dump_json()
    assert "番茄工作法" not in summary_text
    assert "时间块" not in summary_text
    print(f"真实 smoke 完成：{summary.run_id}，结论 {summary.verdict}")
