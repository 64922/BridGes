import { AtomicProfileCenter } from "@/components/account/profile/AtomicProfileCenter";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "用户画像 — BridGes",
};

/**
 * 用户画像（V2 Issue 08：无固定类别的原子长期信息列表）。
 *
 * 页面只读写原子条目：逐条修改与删除、行内保存／取消、删除先确认，空态
 * 说明提取边界。旧四维记录与旧治理面仅保留为后端能力（转成原子列表后由
 * 后端迁移台账负责对账），页面不再按类别分组展示。
 */
export default function ProfileCenterPage() {
  return (
    <MainContent>
      <AtomicProfileCenter />
    </MainContent>
  );
}
