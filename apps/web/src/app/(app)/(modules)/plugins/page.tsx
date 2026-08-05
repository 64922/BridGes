import { PluginCenter } from "@/components/plugins/PluginCenter";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "插件 — BridGes",
};

/**
 * 插件（Issue 34）：SKILL 插件中心。
 *
 * 内置插件（PDF / Documents / bridges-humanizer）随应用发布、版本固定、
 * 只读不可卸载，账户可启停并真实演示；用户可上传声明式 SKILL 包，
 * 安装前完成安全闭锁检查与内容预览确认，按账户启停、卸载与失败恢复。
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
      </section>
    </MainContent>
  );
}
