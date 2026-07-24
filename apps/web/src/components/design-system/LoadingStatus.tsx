interface LoadingStatusProps {
  message?: string;
}

/**
 * Polite loading indicator.
 *
 * Uses `role="status"` and `aria-live="polite"` so screen readers announce
 * the loading state without interrupting the user.
 */
export function LoadingStatus({ message = "加载中…" }: LoadingStatusProps) {
  return (
    <p role="status" aria-live="polite" data-testid="loading-status">
      {message}
    </p>
  );
}
