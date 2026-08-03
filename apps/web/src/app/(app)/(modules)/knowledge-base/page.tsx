import KnowledgeBasePageClient from "./knowledge-base-page-client";

export const metadata = {
  title: "本地知识库 — BridGes",
};

/**
 * 本地知识库（Issue 18 全局材料入口）。
 *
 * 服务端壳仅提供元数据；真实列表、上传、详情与索引操作全部在
 * 客户端组件内完成（与搜索页同一结构）。
 */
export default function KnowledgeBasePage() {
  return <KnowledgeBasePageClient />;
}
