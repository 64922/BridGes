import { KeySettings } from "@/components/account/KeySettings";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "密钥设置 — BridGes",
};

export default function KeySettingsPage() {
  return (
    <MainContent>
      <KeySettings />
    </MainContent>
  );
}
