"""工单 43：全部用户可见自然语言来源接入策略/模板（任务 5 交叉审计）。

只做集成接缝审计：公共注册表结构校验通过、六模块与学习全部登记且状态
覆盖完整、正式路径清单的接线/证据文件存在。领域真实状态语义由各票与
38 的桌面验收覆盖，本测试不重复声明。
"""

from __future__ import annotations

from pathlib import Path

from bridges.evaluation.expression_spec import FORMAL_PATHS
from bridges.orchestration.planner import CompositePlanner
from bridges.state_copy.catalog import STATE_COPY_ENTRIES
from bridges.state_copy.registry import (
    validate_state_copy_registry,
)
from bridges.state_copy.types import STATE_COPY_VERSION, CopyCategory, CopyStrategy

_MODULE_OWNER_ALIASES = {"career": "career_plan"}
_ALLOWED_RENDER_KINDS = {
    "model_with_policy",
    "deterministic_renderer",
    "fixed_template",
    "composite",
}


def test_state_copy_registry_validates_and_covers_all_module_and_study_owners() -> None:
    validate_state_copy_registry()

    owners = {
        entry.owner
        for entry in STATE_COPY_ENTRIES
        if entry.category in {CopyCategory.MODULE, CopyCategory.STUDY}
    }
    production_modules = {
        _MODULE_OWNER_ALIASES.get(module_id, module_id)
        for module_id in CompositePlanner().registry.modules
    }
    assert production_modules <= owners
    assert "study" in owners
    assert STATE_COPY_VERSION
    assert len(STATE_COPY_ENTRIES) >= 200

    # 每条来源要么有固定模板，要么登记确定性渲染器/模型适配接缝。
    for entry in STATE_COPY_ENTRIES:
        if entry.strategy is CopyStrategy.FIXED_TEMPLATE:
            assert entry.text and entry.text.strip(), entry.path
        else:
            assert entry.renderer, entry.path


def test_formal_paths_have_existing_seams_and_evidence() -> None:
    root = Path(__file__).resolve().parents[2]

    assert {path.render_kind for path in FORMAL_PATHS} <= _ALLOWED_RENDER_KINDS
    for path in FORMAL_PATHS:
        assert path.seam, path.path_id
        evidence_path = path.evidence.partition("::")[0]
        assert (root / evidence_path).exists(), path.path_id
