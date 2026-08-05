"""内置只读 SKILL 注册表（Issue 28）。

SkillRegistry 记录随应用发布的内置 SKILL 清单：稳定注册标识、固定版本、
只读来源与能力说明。默认内置、不依赖用户手工上传或 `.env`；版本进入
能力注册与运行审计。插件治理页（Issue 34）只消费本注册表做展示与启停，
不得修改 SKILL 内容。
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


def _humanizer_manifest() -> HumanizerSkillManifest:
    """bridges-humanizer 的固定注册清单（与 SKILL.md 版本一致）。"""
    return HumanizerSkillManifest(
        skill_id="bridges-humanizer",
        name="文章人味化",
        version="1.0.0",
        description=(
            "原创净室人味化 SKILL：在保持科学事实、限定条件与引用关系的前提下，"
            "按科普文案/课程讲稿/科研汇报/论文写作四类体裁改进表达的自然度、"
            "任务适配度与可读性；支持改写已有文本与按主题生成，固定输出最终文本、"
            "修改明细、理由、事实核查与未决问题，并受文本级事实锁约束。"
        ),
        read_only=True,
        source="BridGes 原创净室实现（见 SKILL/CLEAN_ROOM.md 来源清洁记录）",
        license="原创，零第三方复用（净室声明）",
        capabilities=[
            "改写已有文本（粘贴或当前账户文件）",
            "按主题/受众/体裁/渠道/硬约束生成新文章",
            "四类体裁独立表达规则（不共用泛化模板）",
            "文本级事实锁：数值/单位/对象关系/限定条件/公式/引用/结论强度",
            "固定输出合同：最终文本/修改明细/每项理由/事实核查/未决问题",
        ],
        registration_version=_REGISTRATION_VERSION,
        registered_at=datetime.now(UTC),
    )


def create_builtin_registry() -> SkillRegistry:
    """创建并注册全部默认内置 SKILL。"""
    registry = SkillRegistry()
    registry.register(_humanizer_manifest())
    return registry
