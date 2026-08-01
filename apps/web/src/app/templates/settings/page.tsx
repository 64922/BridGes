import { Suspense } from "react";

import { SettingsTemplate } from "./settings-template";

export const metadata = { title: "BridGes — 设置模板" };

export default function SettingsTemplatePage() {
  return (
    <Suspense>
      <SettingsTemplate />
    </Suspense>
  );
}
