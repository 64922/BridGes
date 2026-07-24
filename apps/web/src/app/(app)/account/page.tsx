import AccountPageClient from "./account-page";

export const metadata = {
  title: "账户主壳 — Science Companion",
};

/**
 * Account-level main shell.
 *
 * The authenticated landing page that surfaces the global science companion,
 * project list, and personal centers while keeping each domain visually
 * separated.
 */
export default function AccountPage() {
  return <AccountPageClient />;
}
