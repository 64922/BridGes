"use client";

import { createContext, useContext } from "react";

export interface User {
  id: string;
  name: string;
  email: string;
}

interface AuthContextValue {
  user: User;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Stub authentication provider for T002.
 *
 * T003 will replace this with real session management. Until then, the shell
 * renders as if a demo user is signed in so the authenticated main shell and
 * project pages can be exercised by accessibility and responsive tests.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const value: AuthContextValue = {
    user: {
      id: "demo-user",
      name: "演示用户",
      email: "demo@example.com",
    },
    isAuthenticated: true,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
