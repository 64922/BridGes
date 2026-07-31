import { MainContent } from "@/components/layout/MainContent";

import { WorkbenchDetail } from "./workbench-detail";

export const metadata = {
  title: "领域包专家工作台详情 — Science Companion",
};

export default function WorkbenchDetailPage({
  params,
}: {
  params: { packId: string; version: string };
}) {
  return (
    <MainContent>
      <WorkbenchDetail packId={params.packId} version={params.version} />
    </MainContent>
  );
}
