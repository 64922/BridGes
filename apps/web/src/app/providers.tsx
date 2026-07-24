"use client";

import { AnnouncerProvider, RouteAnnouncer } from "@/components/design-system/Announcer";
import { AuthProvider } from "@/context/AuthContext";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AnnouncerProvider>
      <AuthProvider>
        <RouteAnnouncer />
        {children}
      </AuthProvider>
    </AnnouncerProvider>
  );
}
