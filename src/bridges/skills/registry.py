"""内置只读 SKILL 注册表（Issue 28）。

SkillRegistry 记录随应用发布的内置 SKILL 清单：稳定注册标识、固定版本、
只读来源与能力说明。默认内置、不依赖用户手工上传或 `.env`；版本进入
能力注册与运行审计。插件治理页（Issue 34）只消费本注册表做展示与启停，
不得修改 SKILL 内容。

Issue 21：唯一内置 SKILL（文章人味化）已随旧入口退役移除，注册表保留为
校验与展示缝（当前无内置条目），不再注册任何已退役能力。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from bridges.contracts.humanizer import HumanizerSkillManifest

_REGISTRATION_VERSION = "1"


class SkillRegistryError(Exception):
    """注册表错误：未知标识或重复注册冲突。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SkillRegistry:
    """线程安全的内置 SKILL 注册表（内存，启动时注册）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._skills: dict[str, HumanizerSkillManifest] = {}

    def register(self, manifest: HumanizerSkillManifest) -> None:
        """注册一个只读内置 SKILL；同标识重复注册必须版本一致。"""
        with self._lock:
            existing = self._skills.get(manifest.skill_id)
            if existing is not None:
                if existing.version != manifest.version:
                    raise SkillRegistryError(
                        "version_conflict",
                        f"SKILL {manifest.skill_id} 已注册版本 {existing.version}，"
                        f"拒绝注册版本 {manifest.version}（内置 SKILL 版本固定）。",
                    )
                return
            self._skills[manifest.skill_id] = manifest

    def get(self, skill_id: str) -> HumanizerSkillManifest:
        """按稳定标识读取；未知标识抛出错误。"""
        with self._lock:
            manifest = self._skills.get(skill_id)
            if manifest is None:
                raise SkillRegistryError(
                    "skill_not_found", f"未注册的 SKILL 标识：{skill_id}"
                )
            return manifest

    def list_builtin(self) -> list[HumanizerSkillManifest]:
        """列出全部内置 SKILL（供插件页展示与审计）。"""
        with self._lock:
            return sorted(
                self._skills.values(), key=lambda m: m.skill_id
            )

    def is_builtin(self, skill_id: str) -> bool:
        """判断某标识是否为内置只读 SKILL。"""
        with self._lock:
            return skill_id in self._skills


def create_builtin_registry() -> SkillRegistry:
    """创建内置 SKILL 注册表；Issue 21 后已无内置 SKILL（人味化退役）。"""
    return SkillRegistry()
