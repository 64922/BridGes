"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import type { components } from "@bridges/contracts";
import {
  ApiError,
  fetchSession,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
} from "@/lib/api";

export type User = components["schemas"]["Account"];
export type AuthState = "loading" | "authenticated" | "unauthenticated" | "error";

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  authState: AuthState;
  sessionError: string | null;
  login: (identifier: string, password: string) => Promise<void>;
  register: (username: string, qqEmail: string, password: string) => Promise<void>;
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
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [sessionError, setSessionError] = useState<string | null>(null);

  const checkSession = useCallback(async () => {
    setIsLoading(true);
    // Keep an already-authorized subtree mounted during a background refresh
    // so successful form feedback and local focus are not discarded.
    setAuthState((current) =>
      current === "authenticated" ? current : "loading"
    );
    setSessionError(null);
    try {
      const data = await fetchSession();
      setUser(data.account);
      setAuthState("authenticated");
    } catch (error) {
      setUser(null);
      if (error instanceof ApiError && error.status === 401) {
        setAuthState("unauthenticated");
      } else {
        setAuthState("error");
        setSessionError(
          error instanceof Error ? error.message : "暂时无法验证会话，请重试。"
        );
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const login = useCallback(async (identifier: string, password: string) => {
    const data = await apiLogin(identifier, password);
    setUser(data.account);
    setAuthState("authenticated");
    setSessionError(null);
  }, []);

  const register = useCallback(async (username: string, qqEmail: string, password: string) => {
    const data = await apiRegister(username, qqEmail, password);
    setUser(data.account);
    setAuthState("authenticated");
    setSessionError(null);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
    setAuthState("unauthenticated");
    setSessionError(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        authState,
        sessionError,
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
