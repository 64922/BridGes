import { Suspense } from "react";

import { ListTemplate } from "./list-template";

export const metadata = { title: "BridGes — 列表模板" };

export default function ListTemplatePage() {
  return (
    <Suspense>
      <ListTemplate />
    </Suspense>
  );
}
