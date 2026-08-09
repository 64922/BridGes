import { FourDimensionProfileCenter } from "@/components/account/profile/FourDimensionProfileCenter";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "用户画像 — BridGes",
};

/**
 * 用户画像（Issue 14：四维画像展开期用户页面）。
 *
 * 默认页面只展示四类画像、内容和首次稳定记录时间，支持修改与撤回；头像由
 * 个人资料页面负责，旧治理面在兼容期保留为后端只读能力。
 */
export default function ProfileCenterPage() {
  return (
    <MainContent>
      <FourDimensionProfileCenter />
    </MainContent>
  );
}
