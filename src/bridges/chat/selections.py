"""对话级插件选择的校验、失效清洗与工具上下文编译（Issue 36）。

「选择项目、选择插件」两个入口的选择状态随对话持久化（conversations.
plugin_selection 列）；可用集合 = 当前账户「已安装且启用」的插件：
SKILL 插件（内置或用户包，enabled）与 MCP 服务器（enabled）。插件被
停用、卸载或撤权后立即从可用集合消失：读取会话或生成时逐项校验，
失效项被清洗写回并携带中文影响解释（removed_selections），清除后
生成上下文不再携带旧工具集合（Verification 3）。

生成时把有效选择编译为「本对话可用工具」独立系统块注入模型，未选中
任何插件时不注入；MCP 调用载荷在服务层校验「必须被本对话选中」，未
选中的 MCP 一律拒绝调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bridges.chat.repository import ConversationRepository
from bridges.contracts.chat import ChatPluginSelectionItem, RemovedPluginSelection
from bridges.mcp.service import McpService
from bridges.plugins.service import PluginService
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True)
class SelectionValidation:
    """一次选择清洗的结果：有效选择 + 失效解释（含中文原因）。"""

    valid: list[ChatPluginSelectionItem] = field(default_factory=list)
    removed: list[RemovedPluginSelection] = field(default_factory=list)


@dataclass(frozen=True)
class SelectionResolution:
    """生成前解析结果：有效选择、失效解释与工具上下文。"""

    valid: list[ChatPluginSelectionItem] = field(default_factory=list)
    removed: list[RemovedPluginSelection] = field(default_factory=list)
    context: str | None = None


def selection_key(item: ChatPluginSelectionItem) -> tuple[str, str]:
    return (item.kind, item.plugin_id)


class ChatSelectionsService:
    """对话插件选择域：可用集合解析、校验、清洗与工具上下文编译。

    未挂载插件/MCP 服务（内存测试环境）时不校验也不注入工具上下文，
    保持与无插件概念环境一致；挂载后全部按账户作用域强制隔离。
    """

    def __init__(
        self,
        repository: ConversationRepository,
        database: BridgesDatabase | None = None,
        plugin_service: PluginService | None = None,
        mcp_service: McpService | None = None,
    ) -> None:
        self._repo = repository
        self._database = database
        self._plugins = plugin_service
        self._mcp = mcp_service

    # ------------------------------------------------------------------
    # 校验与清洗
    # ------------------------------------------------------------------

    def validate_items(
        self, account_id: str, items: list[ChatPluginSelectionItem]
    ) -> SelectionValidation:
        """校验一组候选选择；返回有效项与失效解释（不写库）。

        创建/更新会话时使用：逐项确认存在且当前账户启用，非法项返回
        具体中文原因，由调用方决定拒绝（422）或清洗。
        """
        skill_names, skill_reasons = self._skill_availability(account_id)
        mcp_names, mcp_reasons = self._mcp_availability(account_id)
        valid: list[ChatPluginSelectionItem] = []
        removed: list[RemovedPluginSelection] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = selection_key(item)
            if key in seen:
                continue
            seen.add(key)
            if item.kind == "skill":
                failure_reason: str | None
                if item.plugin_id not in skill_names:
                    failure_reason = "插件未安装，请先在插件中心安装。"
                else:
                    failure_reason = skill_reasons.get(item.plugin_id)
                if failure_reason is None:
                    valid.append(item)
                else:
                    removed.append(
                        RemovedPluginSelection(
                            kind="skill",
                            plugin_id=item.plugin_id,
                            name=skill_names.get(item.plugin_id, item.plugin_id),
                            reason=failure_reason,
                        )
                    )
            else:
                failure_reason = (
                    "MCP 服务器未安装，请先在插件中心安装。"
                    if item.plugin_id not in mcp_names
                    else mcp_reasons.get(item.plugin_id)
                )
                if failure_reason is None:
                    valid.append(item)
                else:
                    removed.append(
                        RemovedPluginSelection(
                            kind="mcp",
                            plugin_id=item.plugin_id,
                            name=mcp_names.get(item.plugin_id, item.plugin_id),
                            reason=failure_reason,
                        )
                    )
        return SelectionValidation(valid=valid, removed=removed)

    def resolve(
        self, account_id: str, conversation_id: str
    ) -> SelectionResolution:
        """读取会话选择 → 校验清洗（写回失效项）→ 编译工具上下文。

        生成与消息发送前调用：失效项从会话列中清洗写回（不再携带旧
        上下文），removed 供前端一次性解释影响。
        """
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None or not record.plugin_selection:
            return SelectionResolution()
        raw_items = [
            ChatPluginSelectionItem(**entry)
            for entry in record.plugin_selection
            if isinstance(entry, dict)
        ]
        if self._plugins is None and self._mcp is None:
            return SelectionResolution(valid=raw_items)
        result = self.validate_items(account_id, raw_items)
        # 失效项清洗写回：保证后续生成不再携带旧工具集合。
        if result.removed:
            self._repo.update_conversation(
                account_id,
                conversation_id,
                plugin_selection=[item.model_dump() for item in result.valid],
                updated_at=_now(),
            )
        context = self._compile_context(account_id, result.valid)
        return SelectionResolution(
            valid=result.valid, removed=result.removed, context=context
        )

    def revoke_mcp_selection(self, account_id: str, mcp_id: str) -> None:
        """撤权后把该 MCP 从账户全部会话的选择中移除（动作时立即移除）。

        ``revoke_permissions`` 成功后在 API 层调用：撤权意味着此前对话
        对旧权限清单的选择授权不再成立，从可用集合与持久化选择中移除。
        """
        self._remove_selection(account_id, "mcp", mcp_id)

    def _remove_selection(
        self, account_id: str, kind: str, plugin_id: str
    ) -> None:
        """从账户全部会话的选择中移除指定插件条目（动作时立即移除）。"""
        if self._database is None:
            return
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            "SELECT conversation_id, plugin_selection FROM conversations"
            " WHERE account_id = ? AND plugin_selection IS NOT NULL",
            (account_id,),
        ).fetchall()
        now = _iso_now()
        for row in rows:
            entries = _parse_selection(row["plugin_selection"])
            kept = [
                entry
                for entry in entries
                if not (entry.get("kind") == kind and entry.get("plugin_id") == plugin_id)
            ]
            if len(kept) != len(entries):
                scoped.execute(
                    "UPDATE conversations SET plugin_selection = ?, updated_at = ?"
                    " WHERE conversation_id = ? AND account_id = ?",
                    (
                        _json_dumps(kept) if kept else None,
                        now,
                        str(row["conversation_id"]),
                        account_id,
                    ),
                )

    # ------------------------------------------------------------------
    # 工具上下文
    # ------------------------------------------------------------------

    def _compile_context(
        self, account_id: str, items: list[ChatPluginSelectionItem]
    ) -> str | None:
        if not items:
            return None
        names: dict[tuple[str, str], str] = {}
        capabilities: dict[tuple[str, str], list[str]] = {}
        categories: dict[tuple[str, str], list[str]] = {}
        if self._plugins is not None:
            plugin_listing = self._plugins.list_plugins(account_id)
            for manifest in plugin_listing.builtin:
                key = ("skill", manifest.skill_id)
                names[key] = manifest.name
                capabilities[key] = list(manifest.capabilities)
                categories[key] = list(manifest.data_categories)
            for package in plugin_listing.user:
                key = ("skill", package.plugin_id)
                names[key] = package.name
                capabilities[key] = list(package.capabilities)
                categories[key] = list(package.data_categories)
        if self._mcp is not None:
            mcp_listing = self._mcp.list_servers(account_id)
            for server in mcp_listing.servers:
                key = ("mcp", server.mcp_id)
                names[key] = server.name
                if server.description:
                    capabilities[key] = [server.description]
                categories[key] = list(server.permissions.data_categories)
        lines = ["本对话启用了以下插件工具，回答中只能引用本清单列出的插件能力："]
        for item in items:
            key = selection_key(item)
            name = names.get(key, item.plugin_id)
            caps = capabilities.get(key, [])
            cats = categories.get(key, [])
            cap_text = "；".join(caps) if caps else "未声明"
            cat_text = "、".join(cats) if cats else "不接收对话数据"
            kind_text = "SKILL 插件" if item.kind == "skill" else "MCP 服务器"
            lines.append(
                f"- {kind_text}「{name}」（{item.plugin_id}）：能力：{cap_text}"
                f"；数据类别：{cat_text}。"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 可用性解析
    # ------------------------------------------------------------------

    def _skill_availability(
        self, account_id: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """返回 (显示名表, 失效原因表)；不在原因表的 skill 标识视为可用。"""
        names: dict[str, str] = {}
        reasons: dict[str, str] = {}
        if self._plugins is None:
            return names, reasons
        listing = self._plugins.list_plugins(account_id)
        available: set[str] = set()
        for manifest in listing.builtin:
            names[manifest.skill_id] = manifest.name
            if manifest.enabled:
                available.add(manifest.skill_id)
        for package in listing.user:
            names[package.plugin_id] = package.name
            if package.status.value == "installed" and package.enabled:
                available.add(package.plugin_id)
            elif package.plugin_id not in available:
                reasons[package.plugin_id] = _status_reason(package.status.value)
        # 内置停用与已卸载等未入库的标识统一补解释。
        for plugin_id in set(names) - available:
            if plugin_id not in reasons:
                reasons[plugin_id] = "插件已停用，可在插件中心重新启用。"
        return names, reasons

    def _mcp_availability(
        self, account_id: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """返回 (显示名表, 失效原因表)；不在原因表的 mcp 标识视为可用。"""
        names: dict[str, str] = {}
        reasons: dict[str, str] = {}
        if self._mcp is None:
            return names, reasons
        listing = self._mcp.list_servers(account_id)
        for server in listing.servers:
            names[server.mcp_id] = server.name
            if not server.enabled:
                reasons[server.mcp_id] = "服务器已停用，可在插件中心重新启用。"
        return names, reasons


def _status_reason(status: str) -> str:
    if status == "install_failed":
        return "插件安装失败，需重新安装后选择。"
    if status == "disabled":
        return "插件已停用，可在插件中心重新启用。"
    return "插件不可用，请先在插件中心安装。"


def _parse_selection(value: Any) -> list[dict[str, Any]]:
    import json

    try:
        parsed = json.loads(str(value))
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


def _json_dumps(value: list[dict[str, Any]]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
