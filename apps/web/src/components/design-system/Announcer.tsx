"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

interface AnnouncerContextValue {
  /** Announce a message to screen readers via the polite live region. */
  announce: (message: string) => void;
}

const AnnouncerContext = createContext<AnnouncerContextValue | null>(null);

export function useAnnouncer() {
  const context = useContext(AnnouncerContext);
  if (!context) {
    throw new Error("useAnnouncer must be used within an AnnouncerProvider");
  }
  return context.announce;
}

interface AnnouncerProviderProps {
  children: React.ReactNode;
}

/**
 * Provides a single polite aria-live region for route and status announcements.
 *
 * Consumers should call `announce()` with a short, human-readable message. The
 * region is rendered off-screen but remains in the accessibility tree so screen
 * readers can read it without affecting the visual layout.
 */
export function AnnouncerProvider({ children }: AnnouncerProviderProps) {
  const [message, setMessage] = useState("");

  const announce = useCallback((next: string) => {
    setMessage("");
    // Force a DOM mutation so the live region registers a change even when the
    // previous and next messages are identical.
    requestAnimationFrame(() => {
      setMessage(next);
    });
  }, []);

  return (
    <AnnouncerContext.Provider value={{ announce }}>
      {children}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sc-visually-hidden"
        data-testid="route-announcer"
      >
        {message}
      </div>
    </AnnouncerContext.Provider>
  );
}

/**
 * Announces route changes to screen readers.
 *
 * App Router does not expose route-change events, so this component watches the
 * current pathname and document title. It announces a short message whenever
 * either changes.
 */
export function RouteAnnouncer() {
  const announce = useAnnouncer();
  const pathname = usePathname();
  const previousPathRef = useRef<string | null>(null);

  useEffect(() => {
    const title = document.title;
    const message = title
      ? `已导航到 ${title}，路径 ${pathname}`
      : `已导航到 ${pathname}`;

    if (previousPathRef.current === null) {
      // Initial route: delay slightly so screen readers read the page title
      // naturally before the live region interrupts.
      const timer = setTimeout(() => {
        announce(message);
      }, 100);
      previousPathRef.current = pathname;
      return () => clearTimeout(timer);
    }

    if (previousPathRef.current !== pathname) {
      announce(message);
      previousPathRef.current = pathname;
    }
  }, [announce, pathname]);

  return null;
}
