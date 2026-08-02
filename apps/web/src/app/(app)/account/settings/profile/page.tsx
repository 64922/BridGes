import { PersonalProfileSettings } from "@/components/account/PersonalProfileSettings";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "个人资料 — BridGes",
};

export default function PersonalProfilePage() {
  return (
    <MainContent>
      <PersonalProfileSettings />
    </MainContent>
  );
}
