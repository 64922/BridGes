"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import type { components } from "@bridges/contracts";
import { fetchSession, login as apiLogin, logout as apiLogout, register as apiRegister } from "@/lib/api";

export type User = components["schemas"]["Account"];

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, agreedToTerms: boolean) => Promise<void>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Real authentication provider for T003.
 *
 * Manages session state by calling the backend session endpoint on mount and
 * after login/register/logout. The session cookie is HttpOnly and managed by
 * the browser/API, so the frontend never stores the token in JavaScript.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const checkSession = useCallback(async () => {
    try {
      const data = await fetchSession();
      setUser(data.account);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await apiLogin(email, password);
    setUser(data.account);
  }, []);

  const register = useCallback(async (email: string, password: string, agreedToTerms: boolean) => {
    const data = await apiRegister(email, password, agreedToTerms);
    setUser(data.account);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        login,
        register,
        logout,
        refreshSession: checkSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
