interface MainContentProps {
  children: React.ReactNode;
}

/**
 * Main content landmark.
 *
 * - Uses `<main>` with `id="main-content"` so the skip link has a target.
 * - `tabIndex={-1}` allows the element to receive programmatic focus without
 *   appearing in the normal tab order.
 */
export function MainContent({ children }: MainContentProps) {
  return (
    <main
      id="main-content"
      tabIndex={-1}
      data-testid="main-content"
      className="main-content"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: "calc(100vh - var(--shell-chrome-height, var(--topbar-height)))",
      }}
    >
      {children}
    </main>
  );
}
