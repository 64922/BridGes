"use client";

/**
 * Skip-to-content link.
 *
 * Hidden until focused, then appears at the top of the page so keyboard users
 * can bypass repeated navigation and jump straight to #main-content.
 */
export const SKIP_LINK_ID = "skip-link";

export function SkipLink() {
  return (
    <a
      id={SKIP_LINK_ID}
      href="#main-content"
      className="sc-visually-hidden"
      data-testid="skip-link"
    >
      跳转到主内容
    </a>
  );
}
