import { Suspense } from "react";

import { LoginTemplate } from "./login-template";

export const metadata = { title: "BridGes — 登录模板" };

export default function LoginTemplatePage() {
  return (
    <Suspense>
      <LoginTemplate />
    </Suspense>
  );
}
