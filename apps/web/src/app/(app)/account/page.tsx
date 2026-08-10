import AccountPageClient from "./account-page";

export const metadata = {
  title: "账户主壳 — BridGes",
};

/**
 * Account landing contains only the account welcome and settings shortcuts;
 * retired project data is not loaded here.
 */
export default function AccountPage() {
  return <AccountPageClient />;
}
