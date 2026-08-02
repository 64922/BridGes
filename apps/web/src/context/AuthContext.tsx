"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import type { components } from "@bridges/contracts";
import {
  ApiError,
  addDeviceAccount,
  fetchSession,
  logoutAllDeviceAccounts,
  logoutCurrentDeviceAccount,
  login as apiLogin,
  logout as apiLogout,
  reauthenticateDeviceAccount,
  register as apiRegister,
  switchDeviceAccount,
} from "@/lib/api";

export type User = components["schemas"]["Account"];
export type AuthState = "loading" | "authenticated" | "unauthenticated" | "error";

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  authState: AuthState;
  sessionError: string | null;
  accountRevision: number;
  login: (identifier: string, password: string) => Promise<void>;
  register: (username: string, qqEmail: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  addAccount: (identifier: string, password: string) => Promise<boolean>;
  switchAccount: (sessionId: string) => Promise<boolean>;
  reauthenticateAccount: (sessionId: string, password: string) => Promise<boolean>;
  logoutCurrentAccount: () => Promise<User | null>;
  logoutAllAccounts: () => Promise<void>;
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
  const [accountRevision, setAccountRevision] = useState(0);
  const authRequestRef = useRef(0);

  const checkSession = useCallback(async () => {
    const requestId = ++authRequestRef.current;
    setIsLoading(true);
    // Keep an already-authorized subtree mounted during a background refresh
    // so successful form feedback and local focus are not discarded.
    setAuthState((current) =>
      current === "authenticated" ? current : "loading"
    );
    setSessionError(null);
    try {
      const data = await fetchSession();
      if (requestId !== authRequestRef.current) return;
      setUser(data.account);
      setAuthState("authenticated");
    } catch (error) {
      if (requestId !== authRequestRef.current) return;
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
      if (requestId === authRequestRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const login = useCallback(async (identifier: string, password: string) => {
    const requestId = ++authRequestRef.current;
    const data = await apiLogin(identifier, password);
    if (requestId !== authRequestRef.current) return;
    setUser(data.account);
    setAuthState("authenticated");
    setSessionError(null);
  }, []);

  const register = useCallback(async (username: string, qqEmail: string, password: string) => {
    const requestId = ++authRequestRef.current;
    const data = await apiRegister(username, qqEmail, password);
    if (requestId !== authRequestRef.current) return;
    setUser(data.account);
    setAuthState("authenticated");
    setSessionError(null);
  }, []);

  const logout = useCallback(async () => {
    const requestId = ++authRequestRef.current;
    await apiLogout();
    if (requestId !== authRequestRef.current) return;
    setUser(null);
    setAuthState("unauthenticated");
    setSessionError(null);
  }, []);

  const applyDeviceAccount = useCallback((account: User | null) => {
    setAccountRevision((revision) => revision + 1);
    setUser(account);
    setAuthState(account ? "authenticated" : "unauthenticated");
    setSessionError(null);
  }, []);

  const addAccount = useCallback(
    async (identifier: string, password: string) => {
      const requestId = ++authRequestRef.current;
      const operationId = Date.now() * 1000 + (requestId % 1000);
      const data = await addDeviceAccount(identifier, password, operationId);
      if (requestId !== authRequestRef.current) return false;
      applyDeviceAccount(data.current_account ?? null);
      return true;
    },
    [applyDeviceAccount]
  );

  const switchAccount = useCallback(
    async (sessionId: string) => {
      const requestId = ++authRequestRef.current;
      const operationId = Date.now() * 1000 + (requestId % 1000);
      const data = await switchDeviceAccount(sessionId, operationId);
      if (requestId !== authRequestRef.current) return false;
      applyDeviceAccount(data.current_account ?? null);
      return true;
    },
    [applyDeviceAccount]
  );

  const reauthenticateAccount = useCallback(
    async (sessionId: string, password: string) => {
      const requestId = ++authRequestRef.current;
      const operationId = Date.now() * 1000 + (requestId % 1000);
      const data = await reauthenticateDeviceAccount(sessionId, password, operationId);
      if (requestId !== authRequestRef.current) return false;
      applyDeviceAccount(data.current_account ?? null);
      return true;
    },
    [applyDeviceAccount]
  );

  const logoutCurrentAccount = useCallback(async () => {
    const requestId = ++authRequestRef.current;
    const operationId = Date.now() * 1000 + (requestId % 1000);
    const data = await logoutCurrentDeviceAccount(operationId);
    if (requestId !== authRequestRef.current) return null;
    applyDeviceAccount(data.current_account ?? null);
    return data.current_account ?? null;
  }, [applyDeviceAccount]);

  const logoutAllAccounts = useCallback(async () => {
    const requestId = ++authRequestRef.current;
    const operationId = Date.now() * 1000 + (requestId % 1000);
    await logoutAllDeviceAccounts(operationId);
    if (requestId !== authRequestRef.current) return;
    applyDeviceAccount(null);
  }, [applyDeviceAccount]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        authState,
        sessionError,
        accountRevision,
        login,
        register,
        logout,
        addAccount,
        switchAccount,
        reauthenticateAccount,
        logoutCurrentAccount,
        logoutAllAccounts,
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
