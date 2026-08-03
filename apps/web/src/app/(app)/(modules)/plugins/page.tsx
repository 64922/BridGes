import { StateBlock } from "@/components/bridges/StateBlock";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "插件 — BridGes",
};

/**
 * 插件（Issue 12 稳定入口）。
 *
 * 后端尚无插件管理能力，因此这里呈现真实、可操作的空状态：
 * 说明当前账户没有可用插件的原因，并给出真实的下一步（返回新聊天）。
 */
export default function PluginsPage() {
  return (
    <MainContent>
      <section aria-labelledby="plugins-title" style={{ maxWidth: "46rem", marginInline: "auto" }}>
        <h1 id="plugins-title" className="sc-section-title">
          插件
        </h1>
        <StateBlock
          kind="empty"
          title="当前账户没有可用插件"
          description="当前版本尚未开放插件管理，因此没有任何插件可以查看或启用。你可以先回到新聊天继续使用内置对话能力；插件能力开放后，本页会展示可真实启用的插件。"
          actionLabel="返回新聊天"
          actionHref="/"
        />
      </section>
    </MainContent>
  );
}
