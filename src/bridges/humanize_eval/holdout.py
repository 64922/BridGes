"""holdout 冻结、哈希校验与解封审计（Issue 09）。

holdout 在实现调优前冻结哈希：清单记录每个 holdout case 的内容哈希与
冻结提交；策略开发与普通运行只能访问 development 输出。发布 Issue 12
才可解封：解封事件写入 append-only 审计日志，且一次性（已解封的 holdout
永久标记为已使用，不能重新伪装成未见数据）。

清单文件：``.scratch/人味化改进/holdout/manifest.json``
审计日志：``.scratch/人味化改进/holdout/audit.log``（append-only）
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

#: holdout 资产目录（相对仓库根）。
HOLDOUT_REL = Path(".scratch") / "人味化改进" / "holdout"
MANIFEST_FILENAME = "manifest.json"
AUDIT_FILENAME = "audit.log"


class HoldoutManifest(BaseModel):
    """冻结清单：holdout case 内容哈希 + 冻结信息 + 一次性解封记录。"""

    corpus_version: str = Field(description="冻结时的语料版本。")
    frozen_commit: str = Field(description="冻结时的 git HEAD 提交。")
    frozen_at: str = Field(description="冻结时间（ISO 8601）。")
    sealed_cases: dict[str, str] = Field(
        description="case_id -> 内容哈希（冻结时的真实内容）。"
    )
    unsealed: bool = Field(default=False, description="是否已解封（一次性，不可逆）。")
    unsealed_at: str = Field(default="", description="解封时间。")
    unsealed_reason: str = Field(default="", description="解封理由（审计）。")


class HoldoutError(Exception):
    """holdout 冻结/访问违规（运行必须拒绝）。"""


class HoldoutAuditLog:
    """append-only 审计日志：事件逐行追加，绝不覆盖旧事件。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class HoldoutController:
    """holdout 清单装载、哈希校验与访问控制（默认拒绝提前读取）。"""

    def __init__(
        self,
        workspace: Path,
        *,
        manifest_path: Path | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.manifest_path = manifest_path or (
            self.workspace / HOLDOUT_REL / MANIFEST_FILENAME
        )
        self.audit = HoldoutAuditLog(
            audit_path or (self.workspace / HOLDOUT_REL / AUDIT_FILENAME)
        )

    # -- 清单 ----------------------------------------------------------------

    def load_manifest(self) -> HoldoutManifest | None:
        """读取冻结清单；文件不存在返回 None（未冻结 = 无 holdout 保护）。"""
        if not self.manifest_path.is_file():
            return None
        try:
            payload = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
            return HoldoutManifest.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HoldoutError(
                f"holdout 清单损坏：{self.manifest_path}（{exc}）"
            ) from exc

    def build_manifest(
        self,
        sealed_cases: dict[str, str],
        *,
        corpus_version: str,
        frozen_commit: str | None = None,
    ) -> HoldoutManifest:
        """构建冻结清单（冻结提交自动取 git HEAD；读不到时记录为 unknown）。"""
        commit = frozen_commit or self._head_commit()
        return HoldoutManifest(
            corpus_version=corpus_version,
            frozen_commit=commit,
            frozen_at=datetime.now(UTC).isoformat(),
            sealed_cases=dict(sealed_cases),
        )

    def save_manifest(self, manifest: HoldoutManifest) -> None:
        """写清单文件（存在则拒绝覆盖：冻结不可原地修改）。"""
        if self.manifest_path.exists():
            raise HoldoutError(
                f"holdout 清单已存在，拒绝覆盖：{self.manifest_path}"
            )
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

    # -- 校验与访问控制 ------------------------------------------------------

    def validate_against(self, case_hashes: dict[str, str]) -> list[str]:
        """校验冻结哈希与当前注册表一致；不一致列出问题（运行必须拒绝）。"""
        manifest = self.load_manifest()
        if manifest is None:
            return []
        problems: list[str] = []
        for case_id, frozen_hash in manifest.sealed_cases.items():
            current = case_hashes.get(case_id)
            if current is None:
                problems.append(f"holdout 案例 {case_id} 已从注册表移除")
            elif current != frozen_hash:
                problems.append(
                    f"holdout 案例 {case_id} 内容哈希变化（冻结 {frozen_hash[:12]}…，"
                    f"当前 {current[:12]}…），禁止在解封前修改；请升版本。"
                )
        return problems

    def sealed_ids(self) -> set[str]:
        """当前清单中的 holdout case 集合（空 = 未冻结）。"""
        manifest = self.load_manifest()
        return set(manifest.sealed_cases) if manifest else set()

    def runnable_case_ids(self, all_case_ids: list[str]) -> list[str]:
        """运行允许的 case 集合：未解封时只含 development；解封后含全部。"""
        manifest = self.load_manifest()
        if manifest is None:
            return list(all_case_ids)
        if manifest.unsealed:
            return list(all_case_ids)
        sealed = set(manifest.sealed_cases)
        return [case_id for case_id in all_case_ids if case_id not in sealed]

    def check_access(self, *, requested_holdout: bool = False) -> None:
        """运行前访问门：未解封时请求 holdout 一律拒绝（提前读取）。"""
        manifest = self.load_manifest()
        if manifest is None:
            return
        if not requested_holdout:
            return
        if not manifest.unsealed:
            raise HoldoutError(
                "holdout 未解封：禁止提前读取冻结案例（发布 Issue 12 才可解封）。"
            )

    # -- 解封与审计 ----------------------------------------------------------

    def unseal(self, reason: str) -> HoldoutManifest:
        """解封 holdout：一次性、不可逆；事件写入审计日志。"""
        manifest = self.load_manifest()
        if manifest is None:
            raise HoldoutError("没有冻结清单，无需解封。")
        if manifest.unsealed:
            raise HoldoutError(
                "holdout 已解封并标记为已使用，不能重新伪装成未见数据。"
            )
        if not reason.strip():
            raise HoldoutError("解封必须提供理由（审计）。")
        now = datetime.now(UTC).isoformat()
        updated = manifest.model_copy(
            update={"unsealed": True, "unsealed_at": now, "unsealed_reason": reason}
        )
        self.audit.record(
            {
                "event": "holdout_unseal",
                "corpus_version": manifest.corpus_version,
                "reason": reason,
                "commit": self._head_commit(),
                "at": now,
            }
        )
        # 覆盖更新清单：解封本身是审批动作，审计日志是真相源。
        self.manifest_path.write_text(
            updated.model_dump_json(indent=2), encoding="utf-8"
        )
        return updated

    def audit_access(self, *, case_ids: list[str], commit: str) -> None:
        """记录一次 holdout 访问（解封后运行 holdout 案例时）。"""
        self.audit.record(
            {
                "event": "holdout_access",
                "case_ids": ",".join(sorted(case_ids)),
                "commit": commit,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    def audit_events(self) -> list[dict[str, str]]:
        """读取全部审计事件（只读，供测试与报告）。"""
        if not self.audit.path.is_file():
            return []
        events: list[dict[str, str]] = []
        for line in self.audit.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
        return events

    def _head_commit(self) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.workspace), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            return proc.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"
