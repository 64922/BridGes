"""静态架构测试：生产路径禁止出现受控模型字面量（Issue 09 AC）。

规则：
- 批准矩阵的模型 ID（``bridges.ai.fixed_models`` 常量值）只允许出现在
  ``src/bridges/ai/fixed_models.py`` 的赋值里；其他生产模块出现精确等值
  字符串常量即违规（AST 扫描，report 文件、行号与 capability）；
- 未批准的历史模型 ID（``qwen3.6-flash``、``qwen-vl-ocr``、
  ``qwen3-vl-plus``）不允许出现在生产路径的任何文本中（含注释与
  docstring），出现即违规；
- fixture/cassette 中的历史供应商响应不在扫描范围（非 ``.py`` 生产
  模块），且不得成为生产配置来源。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from bridges.ai.fixed_models import MODEL_BY_CAPABILITY

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 批准矩阵的全部模型 ID（AST 精确匹配用）。
APPROVED_MODEL_IDS = frozenset(MODEL_BY_CAPABILITY.values())
#: 未批准的历史模型 ID：生产路径任何文本都不允许（含注释/docstring）。
BANNED_MODEL_IDS = frozenset(
    {
        "qwen3.6-flash",
        "qwen-vl-ocr",
        "qwen3-vl-plus",
    }
)
#: 允许定义批准模型字面量的唯一生产模块。
FIXED_MODELS_MODULE = "src/bridges/ai/fixed_models.py"

#: 反向映射：model ID -> 绑定它的 capability（报告用）。
_MODEL_TO_CAPABILITIES: dict[str, str] = {
    model_id: "/".join(
        name for name, bound in MODEL_BY_CAPABILITY.items() if bound == model_id
    )
    for model_id in set(MODEL_BY_CAPABILITY.values())
}


@dataclass(frozen=True)
class LiteralHit:
    """一次模型字面量违规：文件、行号、ID 与关联 capability。"""

    path: str
    line: int
    model_id: str
    capability: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "line": str(self.line),
            "model_id": self.model_id,
            "capability": self.capability,
        }


def _module_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def scan_production_model_literals(root: Path = REPO_ROOT) -> list[LiteralHit]:
    """扫描生产模块（src/bridges）中的受控模型字面量。

    批准 ID：AST 常量精确等值检查（docstring/注释不参与，但固定模型
    模块本身豁免）；未批准 ID：整文件文本检查（任何出现都算违规）。
    """
    hits: list[LiteralHit] = []
    source_root = root / "src" / "bridges"
    for path in sorted(source_root.rglob("*.py")):
        relative = _module_relative(path, root)
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        if relative == FIXED_MODELS_MODULE:
            # 唯一允许批准字面量的模块；未批准 ID 仍不允许。
            for banned in BANNED_MODEL_IDS:
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if banned in line:
                        hits.append(
                            LiteralHit(relative, line_no, banned, "legacy-unapproved")
                        )
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in APPROVED_MODEL_IDS
            ):
                hits.append(
                    LiteralHit(
                        relative,
                        node.lineno,
                        node.value,
                        _MODEL_TO_CAPABILITIES.get(node.value, "unknown"),
                    )
                )
        for banned in BANNED_MODEL_IDS:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if banned in line:
                    hits.append(
                        LiteralHit(relative, line_no, banned, "legacy-unapproved")
                    )
    return hits


def test_no_controlled_model_literals_in_production_paths() -> None:
    hits = scan_production_model_literals()
    assert hits == [], (
        "生产路径出现受控模型字面量（Issue 09 要求全部来自 "
        "bridges.ai.fixed_models）：\n"
        + "\n".join(f"{hit.path}:{hit.line} {hit.model_id} ({hit.capability})" for hit in hits)
    )


def test_scan_flags_literal_in_non_fixed_modules(tmp_path: Path) -> None:
    """负向控制：扫描器必须能发现生产模块中的违规字面量。"""
    fake_bridges = tmp_path / "src" / "bridges" / "ai"
    fake_bridges.mkdir(parents=True)
    (fake_bridges / "fake_module.py").write_text(
        'model_id = "qwen3.6-flash"\n', encoding="utf-8"
    )
    hits = scan_production_model_literals(tmp_path)
    assert any(hit.model_id == "qwen3.6-flash" for hit in hits)


def test_scan_allows_only_fixed_models_for_approved_ids(tmp_path: Path) -> None:
    """正负对照：批准 ID 在 fixed_models.py 中合法，其他模块非法。"""
    fixed = tmp_path / "src" / "bridges" / "ai" / "fixed_models.py"
    fixed.parent.mkdir(parents=True)
    approved = next(iter(APPROVED_MODEL_IDS))
    fixed.write_text(f'CHAT_MODEL_ID = "{approved}"\n', encoding="utf-8")
    assert scan_production_model_literals(tmp_path) == []

    (tmp_path / "src" / "bridges" / "ai" / "other.py").write_text(
        f'model_id = "{approved}"\n', encoding="utf-8"
    )
    hits = scan_production_model_literals(tmp_path)
    assert any(hit.model_id == approved for hit in hits)


@pytest.mark.parametrize("banned", sorted(BANNED_MODEL_IDS))
def test_banned_ids_are_rejected_even_in_comments(tmp_path: Path, banned: str) -> None:
    module = tmp_path / "src" / "bridges" / "ai" / "legacy_comment.py"
    module.parent.mkdir(parents=True)
    module.write_text(f'# legacy comment mentioning {banned}\n', encoding="utf-8")
    hits = scan_production_model_literals(tmp_path)
    assert any(hit.model_id == banned for hit in hits)
