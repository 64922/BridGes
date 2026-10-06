"""锁定配对实际代码与评测合同，脏树不再只用 HEAD 冒充版本。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def source_lock(tree: Path, harness: Path) -> dict[str, Any]:
    """只保存相对路径与内容摘要，不包含凭据或本地业务数据。"""
    files = {
        path.relative_to(tree).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((tree / "src" / "bridges").rglob("*.py"))
    }
    harness_files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(harness.glob("issue42_pairing_*.py"))
    }
    payload = "\n".join(f"{name}:{digest}" for name, digest in files.items())
    return {
        "source_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "source_files": files,
        "harness_files": harness_files,
        "clock": "UTC wall clock + monotonic elapsed",
        "scope": "实际源文件包含提示/Schema/配方；外部实时来源未冻结，不构成严格等价配对",
    }
