import { McpCenter } from "@/components/mcp/McpCenter";
import { PluginCenter } from "@/components/plugins/PluginCenter";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "插件 — BridGes",
};

/**
 * 插件（Issue 34/35）：SKILL 插件中心 + MCP 插件中心。
 *
 * - SKILL：内置插件（PDF / Documents / bridges-humanizer）随应用发布、
 *   版本固定、只读不可卸载，账户可启停并真实演示；用户可上传声明式
 *   SKILL 包，安装前完成安全闭锁检查与内容预览确认。
 * - MCP：按账户安装固定版本 + 完整性锁定的 MCP 安装描述，安装前逐项
 *   预览权限清单；受限进程运行、敏感操作再次确认、撤权/启停/卸载。
 */
export default function PluginsPage() {
  return (
    <MainContent>
      <section
        aria-labelledby="plugins-title"
        style={{ maxWidth: "52rem", marginInline: "auto" }}
      >
        <h1 id="plugins-title" className="sc-section-title">
          插件
        </h1>
        <PluginCenter />
        <McpCenter />
      </section>
    </MainContent>
  );
}
