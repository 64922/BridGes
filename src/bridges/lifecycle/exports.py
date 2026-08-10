"""账户数据导出（Issue 37，AC1-2）。

导出是一个可阅读且可机器处理的 JSON 文档：稳定标识（账户 ID/用户名/
邮箱）、导出时间、逐类别数据（对话/消息/画像及版本/历史归档材料/迁移审计/历史投递
审计/扩展停用审计/扩展调用审计/资产清单/文档与检索内容/引用关系），每行保留
原始列（时间/来源/关系字段），足以审阅画像闭环与内容归属。导出绝不
包含其他账户数据、对象二进制字节、凭据、会话令牌、密码哈希或运行
密钥——这些内容根本不在 bridges.db 业务表之外被读取。预览在确认前
给出逐类别条数与预计大小。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.lifecycle import (
    DataLifecycleError,
    ExportCategoryProjection,
    ExportPreviewProjection,
)
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.identity.service import IdentityService
from bridges.lifecycle.catalog import (
    category_preview,
    export_rows,
)
from bridges.observability.service import ObservabilityService
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError

#: 导出文档格式版本（恢复/审阅工具按此兼容）。
EXPORT_FORMAT_VERSION = 1


class ExportService:
    """当前账户数据的导出服务（导出范围预览与 JSON 文档生成）。"""

    def __init__(
        self,
        database: BridgesDatabase,
        identity_service: IdentityService,
        observability_service: ObservabilityService,
    ) -> None:
        self._database = database
        self._identity = identity_service
        self._observability = observability_service

    def preview(self, account_id: str) -> ExportPreviewProjection:
        """返回导出范围与预计大小（确认前可见，不读取数据正文）。"""
        items = category_preview(self._database, account_id)
        categories = [
            ExportCategoryProjection(
                category=category.key,
                label=category.label,
                item_count=count,
                estimated_bytes=estimated,
            )
            for category, count, estimated in items
        ]
        return ExportPreviewProjection(
            categories=categories,
            total_items=sum(item.item_count for item in categories),
            total_estimated_bytes=sum(item.estimated_bytes for item in categories),
            secrets_omitted=True,
        )

    def export_data(self, account_id: str) -> tuple[str, bytes]:
        """生成当前账户的导出 JSON 文档，返回 (文件名, 字节)。

        类别名固定（审阅工具/测试依赖稳定键）；每张表的行保留原始列，
        JSON 列（思考/搜索/画像/媒体/插件选择等投影）原样携带。对象只
        出元数据清单（资产清单），不读取任何对象字节。
        """
        account = self._identity.get_account(account_id)
        if account is None:
            raise DataLifecycleError(
                "account_not_found", "账户不存在或没有访问权限。", 404
            )
        document: dict[str, Any] = {
            "format_version": EXPORT_FORMAT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "bridges-export/1",
            "account": {
                "account_id": account_id,
                "username": account.username,
                "qq_email": account.qq_email,
            },
            "secrets_omitted": True,
            "categories": {},
        }
        details: dict[str, Any] = {"categories": {}}
        total_items = 0
        total_estimated = 0
        try:
            for category, count, estimated in category_preview(
                self._database, account_id
            ):
                rows: list[dict[str, Any]] = []
                for table in category.tables:
                    rows.extend(export_rows(self._database, account_id, table))
                document["categories"][category.key] = {
                    "label": category.label,
                    "items": rows,
                }
                details["categories"][category.key] = count
                total_items += count
                total_estimated += estimated
        except StorageError as exc:
            raise DataLifecycleError(
                "export_unavailable", str(exc), 503
            ) from exc
        payload = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
        self._observability.log_audit(
            actor_account_id=account_id,
            action=AuditAction.EXPORT_CREATE,
            result=AuditResult.SUCCESS,
            details={
                "categories": details["categories"],
                "total_items": total_items,
                "estimated_bytes": total_estimated,
                "payload_bytes": len(payload),
            },
        )
        filename = (
            f"bridges-export-{account_id[:8]}-"
            f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        )
        return filename, payload
