import { AppShell } from "@/components/layout/AppShell";

/**
 * Account-level layout.
 *
 * Uses the authenticated application shell with account-focused navigation.
 */
export default function AccountLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
