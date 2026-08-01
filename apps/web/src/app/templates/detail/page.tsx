import { Suspense } from "react";

import { DetailTemplate } from "./detail-template";

export const metadata = { title: "BridGes — 详情模板" };

export default function DetailTemplatePage() {
  return (
    <Suspense>
      <DetailTemplate />
    </Suspense>
  );
}
