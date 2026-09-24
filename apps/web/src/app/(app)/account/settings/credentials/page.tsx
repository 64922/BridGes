import { MainContent } from "@/components/layout/MainContent";
import { CredentialSettings } from "@/components/account/CredentialSettings";

export const metadata = {
  title: "搜索与地图凭据 — BridGes",
};

export default function CredentialSettingsPage() {
  return (
    <MainContent>
      <CredentialSettings />
    </MainContent>
  );
}
