import { StateBlock } from "@/components/bridges/StateBlock";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "本地知识库 — BridGes",
};

/**
 * 本地知识库（Issue 12 稳定入口）。
 *
 * 后端尚未开放材料导入能力，因此这里呈现真实、可操作的空状态：
 * 说明当前账户没有内容的原因，并给出真实的下一步（返回新聊天）。
 * 后续模块 Issue 将在本入口内替换为列表与详情闭环。
 */
export default function KnowledgeBasePage() {
  return (
    <MainContent>
      <section aria-labelledby="knowledge-base-title" style={{ maxWidth: "46rem", marginInline: "auto" }}>
        <h1 id="knowledge-base-title" className="sc-section-title">
          本地知识库
        </h1>
        <StateBlock
          kind="empty"
          title="当前账户还没有知识库内容"
          description="当前版本尚未开放材料导入，因此无法上传或索引任何文件，这里没有任何条目。你可以先回到新聊天继续提问；材料导入开放后，本页会展示你导入的真实材料。"
          actionLabel="返回新聊天"
          actionHref="/"
        />
      </section>
    </MainContent>
  );
}
