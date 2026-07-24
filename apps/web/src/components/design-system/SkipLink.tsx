"use client";

/**
 * Skip-to-content link.
 *
 * Hidden until focused, then appears at the top of the page so keyboard users
 * can bypass repeated navigation and jump straight to #main-content.
 */
export function SkipLink() {
  return (
    <a
      href="#main-content"
      className="sc-visually-hidden"
      data-testid="skip-link"
    >
      跳转到主内容
    </a>
  );
}
