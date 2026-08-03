import { StateBlock } from "@/components/bridges/StateBlock";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "用户画像 — BridGes",
};

/**
 * 用户画像（Issue 12 稳定入口，原「画像与记忆中心」）。
 *
 * 后端尚未开放画像与记忆管理能力，因此这里呈现真实、可操作的空状态：
 * 说明当前账户没有画像内容的原因，并给出真实的下一步（返回新聊天）。
 */
export default function ProfileCenterPage() {
  return (
    <MainContent>
      <section aria-labelledby="profile-title" style={{ maxWidth: "46rem", marginInline: "auto" }}>
        <h1 id="profile-title" className="sc-section-title">
          用户画像
        </h1>
        <StateBlock
          kind="empty"
          title="当前账户还没有画像内容"
          description="当前版本尚未开放画像观察与记忆管理，因此这里没有任何画像条目。你可以先回到新聊天继续学习互动；画像能力开放后，本页会展示由真实互动沉淀的画像。"
          actionLabel="返回新聊天"
          actionHref="/"
        />
      </section>
    </MainContent>
  );
}
