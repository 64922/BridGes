import { SkipLink } from "@/components/design-system/SkipLink";

/**
 * Minimal layout for public pages.
 *
 * Public pages do not use the authenticated application shell, but they still
 * provide a skip link and a single main landmark.
 */
export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <SkipLink />
      {children}
    </>
  );
}
