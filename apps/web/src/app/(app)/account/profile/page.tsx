import { ProfileCenter } from "@/components/account/profile/ProfileCenter";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "用户画像 — BridGes",
};

/**
 * 用户画像（Issue 25：数字分身画像中心与静态头像）。
 *
 * 侧边栏「用户画像」进入的完整桌面页面：九类画像记录分区、每条记录的证据/
 * 时间/范围/敏感级别/状态、增改撤冻删与导出、版本历史抽屉、候选确认与静态
 * 头像管理。
 */
export default function ProfileCenterPage() {
  return (
    <MainContent>
      <ProfileCenter />
    </MainContent>
  );
}
