"""内置只读插件清单（Issue 34）。

bridges-pdf 与 bridges-documents 随应用发布并默认安装，展示固定版本、
能力、来源与授权；两者都是本地解析能力，版本随应用固定。

Issue 21：文章人味化（bridges-humanizer）已退役，内置清单不再包含它，
不再把已退役能力展示为可用。
"""

from __future__ import annotations

from bridges.contracts.plugins import BuiltinPluginManifest

BUILTIN_PLUGIN_IDS = ("bridges-pdf", "bridges-documents")


def _pdf_manifest() -> BuiltinPluginManifest:
    return BuiltinPluginManifest(
        skill_id="bridges-pdf",
        name="PDF 文档解析",
        version="1.0.0",
        description=(
            "随应用发布的本地 PDF 解析能力：在受支持附件的真实处理链路中"
            "提取 PDF 页面文本与结构（页码/章节），供聊天上下文、检索与"
            "引用使用；解析只在本机进行，不上传第三方。"
        ),
        source="BridGes 内置实现（PyMuPDF 本地解析）",
        license="内置能力，随 BridGes 应用授权使用",
        capabilities=[
            "解析 PDF 附件并提取文本（带页码与章节结构）",
            "解析结果进入本地摄取与检索链路",
            "全部解析在本机完成，不发送到第三方",
        ],
        data_categories=["当前对话的 PDF 附件文件与其中的文本"],
        read_only=True,
        demo_kind="parse",
    )


def _documents_manifest() -> BuiltinPluginManifest:
    return BuiltinPluginManifest(
        skill_id="bridges-documents",
        name="Documents 文档解析",
        version="1.0.0",
        description=(
            "随应用发布的本地文档解析能力：处理受支持的 DOCX、TXT、"
            "Markdown 与图片附件，提取文本与结构（章节/元数据），供聊天"
            "上下文、检索与引用使用；解析只在本机进行，不上传第三方。"
        ),
        source="BridGes 内置实现（XML/UTF-8/图像元数据本地解析）",
        license="内置能力，随 BridGes 应用授权使用",
        capabilities=[
            "解析 DOCX/TXT/Markdown 附件并提取文本（带章节结构）",
            "读取图片元数据（尺寸/类型/大小）",
            "解析结果进入本地摄取与检索链路",
            "全部解析在本机完成，不发送到第三方",
        ],
        data_categories=["当前对话的文档与图片附件文件及其中的文本"],
        read_only=True,
        demo_kind="parse",
    )


def create_builtin_plugin_manifests() -> list[BuiltinPluginManifest]:
    """创建全部内置插件清单（本地解析能力，均为只读展示）。"""
    return [_pdf_manifest(), _documents_manifest()]


__all__ = ["BUILTIN_PLUGIN_IDS", "create_builtin_plugin_manifests"]
