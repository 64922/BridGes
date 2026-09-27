import { MainContent } from "@/components/layout/MainContent";
import { KeyAndModelSettings } from "@/components/account/KeyAndModelSettings";
import { safeChatReturnPath } from "@/lib/settings-links";

export const metadata = {
  title: "密钥与模型管理 — BridGes",
};

/**
 * 密钥与模型管理页。
 *
 * 通勤消息卡会带 `?return_to=/chat/<id>#<凭据分区>` 跳进来，页首据此给出
 * 「返回原会话」入口（原消息与失败结果不动）。返回路径来自查询串，只接受
 * 站内会话路径，其他取值按没有处理。
 */
export default function KeyAndModelSettingsPage({
  searchParams,
}: {
  searchParams?: { return_to?: string | string[] };
}) {
  const raw = searchParams?.return_to;
  const returnTo = safeChatReturnPath(Array.isArray(raw) ? raw[0] : raw);
  return (
    <MainContent>
      <KeyAndModelSettings returnTo={returnTo} />
    </MainContent>
  );
}
