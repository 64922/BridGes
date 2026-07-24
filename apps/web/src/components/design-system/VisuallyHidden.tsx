interface VisuallyHiddenProps {
  children: React.ReactNode;
}

/**
 * Render text that is visually hidden but available to screen readers.
 *
 * Use this to add redundant text labels to icon-only controls or to provide
 * extra context that is already conveyed visually.
 */
export function VisuallyHidden({ children }: VisuallyHiddenProps) {
  return <span className="sc-visually-hidden">{children}</span>;
}
