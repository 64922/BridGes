import { Suspense } from "react";

import { RegisterTemplate } from "./register-template";

export const metadata = { title: "BridGes — 注册模板" };

export default function RegisterTemplatePage() {
  return (
    <Suspense>
      <RegisterTemplate />
    </Suspense>
  );
}
