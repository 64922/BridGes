import { MainContent } from "@/components/layout/MainContent";
import { KeyAndModelSettings } from "@/components/account/KeyAndModelSettings";

export const metadata = {
  title: "密钥与模型管理 — BridGes",
};

export default function KeyAndModelSettingsPage() {
  return (
    <MainContent>
      <KeyAndModelSettings />
    </MainContent>
  );
}
