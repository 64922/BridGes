"""SKILL 插件中心服务（Issue 34）。

内置插件随应用发布并默认安装：清单来自 ``plugins/registry``（humanizer
条目与既有 SKILL 注册表同源），账户级启停状态持久化到
``account_skill_states``。用户上传包经 ``PluginPackageChecker`` 安全
闭锁后按账户安装：zip 字节进加密对象库（账户隔离 + 待清理回收），
清单/状态/固定版本进 ``skill_packages``。安装被拒进入可恢复的
``install_failed`` 状态，绝不污染运行注册表；每次安装、启停、卸载与
演示调用都写审计，但审计与投影都不携带包内容、脚本正文或附件正文。
"""

from __future__ import annotations

import json
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from bridges.chat.attachments import sniff_media_type
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.plugins import (
    BuiltinPluginManifest,
    BuiltinPluginProjection,
    PluginCheckResult,
    PluginDemoProjection,
    PluginError,
    PluginListProjection,
    PluginStatus,
    UserPluginProjection,
)
from bridges.ingestion.parsers import ParsedDocument, ParseError, parse_document
from bridges.plugins.checker import PluginPackageChecker
from bridges.plugins.registry import create_builtin_plugin_manifests
from bridges.skills.registry import SkillRegistry
from bridges.storage.database import BridgesDatabase
from bridges.storage.repository import BridgesObjectRepository

_MAX_DEMO_BYTES = 10 * 1024 * 1024
_DEMO_PREVIEW_CHARS = 500

# 审计 details 白名单：只记录标识与计数，绝不携带包内容或正文。
_PACKAGE_COLUMNS = (
    "package_id",
    "account_id",
    "plugin_id",
    "version",
    "name",
    "description",
    "source",
    "license",
    "capabilities",
    "data_categories",
    "status",
    "object_id",
    "file_count",
    "content_length",
    "failure_reason",
    "installed_at",
    "updated_at",
)
_PACKAGE_SELECT = ", ".join(_PACKAGE_COLUMNS)

# 审计 details 白名单：只记录标识、计数与声明的数据类别（AC8 要求记录
# 授权数据类别；数据类别是声明文本，绝不携带包内容或附件正文）。
_AUDIT_DETAIL_KEYS = (
    "plugin_id",
    "version",
    "file_count",
    "content_length",
    "data_categories",
    "parser_version",
    "pages",
    "sections",
    "char_count",
    "reason",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in details.items() if k in _AUDIT_DETAIL_KEYS}


class PluginService:
    """插件中心的账户作用域编排服务。"""

    def __init__(
        self,
        *,
        database: BridgesDatabase,
        object_repository: BridgesObjectRepository,
        observability_service: Any,
        skill_registry: SkillRegistry | None = None,
        checker: PluginPackageChecker | None = None,
        clock: Any = None,
    ) -> None:
        self._database = database
        self._objects = object_repository
        self._observability = observability_service
        self._registry = skill_registry
        self._checker = checker or PluginPackageChecker()
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # 内置清单与账户状态
    # ------------------------------------------------------------------

    def _manifests(self) -> list[BuiltinPluginManifest]:
        return create_builtin_plugin_manifests(self._registry)

    def list_plugins(self, account_id: str) -> PluginListProjection:
        builtin = [
            BuiltinPluginProjection(
                **manifest.model_dump(),
                enabled=self._builtin_enabled(account_id, manifest.skill_id),
            )
            for manifest in self._manifests()
        ]
        user = self._list_user_rows(account_id)
        return PluginListProjection(
            builtin=builtin,
            user=[self._row_to_projection(row) for row in user],
        )

    def _builtin_enabled(self, account_id: str, skill_id: str) -> bool:
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT enabled FROM account_skill_states"
            " WHERE account_id = ? AND plugin_id = ?",
            (account_id, skill_id),
        ).fetchone()
        if row is None:
            # UPSERT 惰性建行：并发首查不撞 UNIQUE，默认启用。
            scoped.execute(
                "INSERT INTO account_skill_states (account_id, plugin_id,"
                " enabled, updated_at) VALUES (?, ?, 1, ?)"
                " ON CONFLICT(account_id, plugin_id) DO NOTHING",
                (account_id, skill_id, _now()),
            )
            return True
        return bool(row[0])

    # ------------------------------------------------------------------
    # 用户包：检查 / 安装 / 启停 / 卸载
    # ------------------------------------------------------------------

    def check_package(self, filename: str, content: bytes) -> PluginCheckResult:
        """安装前检查：纯函数，不落库、不落对象（取消无残留）。"""
        if not filename.lower().endswith(".zip"):
            raise PluginError(
                "unsupported_package",
                "请上传 .zip 格式的声明式 SKILL 包。",
                status_code=422,
            )
        return self._checker.check(content)

    def install_package(
        self, account_id: str, filename: str, content: bytes
    ) -> UserPluginProjection:
        """确认安装：重跑安全闭锁，通过后按账户持久化并审计。"""
        if not filename.lower().endswith(".zip"):
            raise PluginError(
                "unsupported_package",
                "请上传 .zip 格式的声明式 SKILL 包。",
                status_code=422,
            )
        result = self._checker.check(content)
        if not result.ok or result.skill_id is None:
            plugin_id = result.skill_id or "unknown-package"
            reason = "；".join(result.rejected_reasons) or "安装检查未通过。"
            # 与内置同名的坏包不落失败记录（避免「我的插件」出现内置同名卡）。
            if self._find_builtin(plugin_id) is None:
                self._record_failure(account_id, plugin_id=plugin_id, reason=reason)
            self._audit(
                account_id,
                AuditAction.PLUGIN_INSTALL,
                AuditResult.BLOCKED,
                {"plugin_id": plugin_id, "reason": reason},
            )
            raise PluginError(
                "unsupported_package",
                "安装检查未通过",
                status_code=422,
                retryable=True,
            )
        assert result.version is not None
        self._reject_builtin_collision(result.skill_id)
        stored = self._objects.create_object(
            account_id,
            filename,
            content,
            media_type="application/zip",
        )
        try:
            scoped = self._database.scoped(account_id)
            existing = scoped.execute(
                "SELECT status FROM skill_packages"
                " WHERE account_id = ? AND plugin_id = ?",
                (account_id, result.skill_id),
            ).fetchone()
            package_id = str(uuid.uuid4())
            if existing is None:
                scoped.execute(
                    "INSERT INTO skill_packages ("
                    " package_id, account_id, plugin_id, version, name,"
                    " description, source, license, capabilities,"
                    " data_categories, status, object_id, file_count,"
                    " content_length, failure_reason, installed_at,"
                    " updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'installed',"
                    " ?, ?, ?, NULL, ?, ?)",
                    (
                        package_id,
                        account_id,
                        result.skill_id,
                        result.version,
                        result.name,
                        result.description,
                        result.source,
                        result.license,
                        json.dumps(result.capabilities, ensure_ascii=False),
                        json.dumps(result.data_categories, ensure_ascii=False),
                        stored.object_id,
                        result.file_count,
                        result.total_bytes,
                        _now(),
                        _now(),
                    ),
                )
            elif existing[0] == PluginStatus.INSTALL_FAILED.value:
                scoped.execute(
                    "UPDATE skill_packages SET version = ?, name = ?,"
                    " description = ?, source = ?, license = ?,"
                    " capabilities = ?, data_categories = ?, status ="
                    " 'installed', object_id = ?, file_count = ?,"
                    " content_length = ?, failure_reason = NULL,"
                    " updated_at = ? WHERE account_id = ? AND plugin_id = ?",
                    (
                        result.version,
                        result.name,
                        result.description,
                        result.source,
                        result.license,
                        json.dumps(result.capabilities, ensure_ascii=False),
                        json.dumps(result.data_categories, ensure_ascii=False),
                        stored.object_id,
                        result.file_count,
                        result.total_bytes,
                        _now(),
                        account_id,
                        result.skill_id,
                    ),
                )
                row = scoped.execute(
                    "SELECT package_id FROM skill_packages"
                    " WHERE account_id = ? AND plugin_id = ?",
                    (account_id, result.skill_id),
                ).fetchone()
                assert row is not None
                package_id = row[0]
            else:
                self._objects.delete_object(account_id, stored.object_id)
                raise PluginError(
                    "package_conflict",
                    f"已安装同标识插件 {result.skill_id}（版本 {result.version}）；"
                    "如需替换请先卸载现有版本。",
                    status_code=409,
                )
            row = scoped.execute(
                f"SELECT {_PACKAGE_SELECT} FROM skill_packages"
                " WHERE account_id = ? AND package_id = ?",
                (account_id, package_id),
            ).fetchone()
            assert row is not None
        except PluginError:
            raise
        except Exception as exc:
            self._objects.delete_object(account_id, stored.object_id)
            raise PluginError(
                "install_failed",
                f"安装失败：{exc}",
                status_code=503,
                retryable=True,
            ) from exc
        self._audit(
            account_id,
            AuditAction.PLUGIN_INSTALL,
            AuditResult.SUCCESS,
            {
                "plugin_id": result.skill_id,
                "version": result.version,
                "file_count": result.file_count,
                "content_length": result.total_bytes,
                "data_categories": result.data_categories,
            },
        )
        return self._row_to_projection(row)

    def set_enabled(
        self, account_id: str, plugin_id: str, enabled: bool
    ) -> None:
        """启用/停用插件（内置与用户包统一入口）。"""
        manifest = self._find_builtin(plugin_id)
        scoped = self._database.scoped(account_id)
        if manifest is not None:
            scoped.execute(
                "INSERT INTO account_skill_states (account_id, plugin_id,"
                " enabled, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(account_id, plugin_id) DO UPDATE SET"
                " enabled = excluded.enabled, updated_at = excluded.updated_at",
                (account_id, plugin_id, 1 if enabled else 0, _now()),
            )
            self._audit(
                account_id,
                AuditAction.PLUGIN_ENABLE if enabled else AuditAction.PLUGIN_DISABLE,
                AuditResult.SUCCESS,
                {"plugin_id": plugin_id, "version": manifest.version},
            )
            return
        row = scoped.execute(
            "SELECT package_id, plugin_id, version, status FROM skill_packages"
            " WHERE account_id = ? AND plugin_id = ?",
            (account_id, plugin_id),
        ).fetchone()
        if row is None:
            raise PluginError(
                "plugin_not_found",
                "未找到该插件，可能已被卸载或不属于当前账户。",
                status_code=404,
            )
        status = PluginStatus(row[3])
        if status == PluginStatus.INSTALL_FAILED:
            raise PluginError(
                "install_failed",
                "该插件安装未完成，不能启用；请重新上传修正后的包。",
                status_code=409,
                retryable=True,
            )
        scoped.execute(
            "UPDATE skill_packages SET status = ?, updated_at = ?"
            " WHERE account_id = ? AND plugin_id = ?",
            (
                PluginStatus.INSTALLED.value if enabled else PluginStatus.DISABLED.value,
                _now(),
                account_id,
                plugin_id,
            ),
        )
        self._audit(
            account_id,
            AuditAction.PLUGIN_ENABLE if enabled else AuditAction.PLUGIN_DISABLE,
            AuditResult.SUCCESS,
            {"plugin_id": plugin_id, "version": row[2]},
        )

    def uninstall(self, account_id: str, plugin_id: str) -> None:
        """卸载用户包：删除记录与对象（待清理回收），内置包拒绝。"""
        if self._find_builtin(plugin_id) is not None:
            raise PluginError(
                "builtin_not_mutable",
                "内置插件随应用发布，不能卸载；只能停用。",
                status_code=403,
            )
        scoped = self._database.scoped(account_id)
        row = scoped.execute(
            "SELECT version, object_id FROM skill_packages"
            " WHERE account_id = ? AND plugin_id = ?",
            (account_id, plugin_id),
        ).fetchone()
        if row is None:
            raise PluginError(
                "plugin_not_found",
                "未找到该插件，可能已被卸载或不属于当前账户。",
                status_code=404,
            )
        scoped.execute(
            "DELETE FROM skill_packages WHERE account_id = ? AND plugin_id = ?",
            (account_id, plugin_id),
        )
        if row[1]:
            # 物理删除失败由待清理回收轮重试，卸载语义不依赖它。
            with suppress(Exception):
                self._objects.delete_object(account_id, row[1])
        self._audit(
            account_id,
            AuditAction.PLUGIN_UNINSTALL,
            AuditResult.SUCCESS,
            {"plugin_id": plugin_id, "version": row[0]},
        )

    # ------------------------------------------------------------------
    # 内置能力演示（真实解析路径）
    # ------------------------------------------------------------------

    def demo(
        self, account_id: str, skill_id: str, filename: str, content: bytes
    ) -> PluginDemoProjection:
        """内置 PDF/Documents 的演示：对附件执行真实解析并返回统计。"""
        manifest = self._find_builtin(skill_id)
        if manifest is None or manifest.demo_kind != "parse":
            raise PluginError(
                "demo_unsupported",
                "该插件不支持附件解析演示；请在聊天中使用它。",
                status_code=400,
            )
        if not self._builtin_enabled(account_id, skill_id):
            raise PluginError(
                "plugin_disabled",
                "该插件已停用，请先启用后再演示。",
                status_code=409,
            )
        if len(content) > _MAX_DEMO_BYTES:
            self._audit(
                account_id,
                AuditAction.PLUGIN_INVOKE,
                AuditResult.BLOCKED,
                {"plugin_id": skill_id, "version": manifest.version, "reason": "演示附件超过上限"},
            )
            raise PluginError(
                "demo_too_large",
                f"演示附件超过上限：允许 {_MAX_DEMO_BYTES // 1024 // 1024}MB。",
                status_code=422,
            )
        try:
            media_type = sniff_media_type(filename, content)
            parsed: ParsedDocument = parse_document(content, filename, media_type)
        except ParseError as exc:
            self._audit(
                account_id,
                AuditAction.PLUGIN_INVOKE,
                AuditResult.BLOCKED,
                {
                    "plugin_id": skill_id,
                    "version": manifest.version,
                    "reason": "解析失败",
                },
            )
            raise PluginError(
                "demo_parse_failed",
                f"解析失败：{exc}",
                status_code=422,
            ) from exc
        preview = parsed.text[: _DEMO_PREVIEW_CHARS]
        self._audit(
            account_id,
            AuditAction.PLUGIN_INVOKE,
            AuditResult.SUCCESS,
            {
                "plugin_id": skill_id,
                "version": manifest.version,
                "parser_version": parsed.parser_version,
                "pages": parsed.page_count,
                "sections": parsed.section_count,
                "char_count": len(parsed.text),
            },
        )
        return PluginDemoProjection(
            skill_id=skill_id,
            name=manifest.name,
            version=manifest.version,
            filename=filename,
            parser_version=parsed.parser_version,
            pages=parsed.page_count,
            sections=parsed.section_count,
            char_count=len(parsed.text),
            preview=preview,
            media_type=media_type,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _find_builtin(self, skill_id: str) -> BuiltinPluginManifest | None:
        for manifest in self._manifests():
            if manifest.skill_id == skill_id:
                return manifest
        return None

    def _reject_builtin_collision(self, plugin_id: str) -> None:
        if self._find_builtin(plugin_id) is not None:
            raise PluginError(
                "package_conflict",
                f"插件标识 {plugin_id} 与内置插件冲突，请更换标识。",
                status_code=409,
            )

    def _record_failure(self, account_id: str, plugin_id: str, reason: str) -> None:
        """安装被拒时保留一条可恢复的失败记录（不进入运行注册表）。"""
        scoped = self._database.scoped(account_id)
        now = _now()
        existing = scoped.execute(
            "SELECT package_id FROM skill_packages"
            " WHERE account_id = ? AND plugin_id = ?",
            (account_id, plugin_id),
        ).fetchone()
        if existing is None:
            scoped.execute(
                "INSERT INTO skill_packages ("
                " package_id, account_id, plugin_id, version, name,"
                " description, source, license, capabilities,"
                " data_categories, status, object_id, file_count,"
                " content_length, failure_reason, installed_at,"
                " updated_at"
                ") VALUES (?, ?, ?, '', ?, NULL, NULL, NULL, '[]', '[]',"
                " 'install_failed', NULL, 0, 0, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    account_id,
                    plugin_id,
                    plugin_id,
                    reason,
                    now,
                    now,
                ),
            )
        else:
            scoped.execute(
                "UPDATE skill_packages SET status = 'install_failed',"
                " failure_reason = ?, updated_at = ?"
                " WHERE account_id = ? AND package_id = ?",
                (reason, now, account_id, existing[0]),
            )

    def _list_user_rows(self, account_id: str) -> list[tuple[Any, ...]]:
        scoped = self._database.scoped(account_id)
        rows = scoped.execute(
            f"SELECT {_PACKAGE_SELECT} FROM skill_packages WHERE account_id = ?"
            " ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
        return [row for row in rows if row is not None]

    def _row_to_projection(self, row: tuple[Any, ...]) -> UserPluginProjection:
        (
            package_id,
            _account_id,
            plugin_id,
            version,
            name,
            description,
            source,
            license_,
            capabilities,
            data_categories,
            status,
            object_id,
            file_count,
            content_length,
            failure_reason,
            installed_at,
            updated_at,
        ) = row
        return UserPluginProjection(
            package_id=package_id,
            plugin_id=plugin_id,
            name=name,
            version=version,
            description=description,
            source=source,
            license=license_,
            capabilities=_load_list(capabilities),
            data_categories=_load_list(data_categories),
            status=PluginStatus(status),
            enabled=status == PluginStatus.INSTALLED.value,
            object_id=object_id,
            file_count=file_count,
            content_length=content_length,
            failure_reason=failure_reason,
            installed_at=datetime.fromisoformat(installed_at),
            updated_at=datetime.fromisoformat(updated_at),
        )

    def _audit(
        self,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        details: dict[str, Any],
    ) -> None:
        self._observability.log_audit(
            actor_account_id=account_id,
            action=action,
            result=result,
            reason=None,
            details=_safe_details(details),
        )


def _load_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item) for item in value]
    except (ValueError, TypeError):
        pass
    return []


__all__ = ["PluginService"]
