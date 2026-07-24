"use client";

import type { components } from "@science-companion/contracts";

export type HealthProjection = components["schemas"]["HealthProjection"];
export type HealthStatus = components["schemas"]["HealthStatus"];
export type Account = components["schemas"]["Account"];
export type AuthResponse = components["schemas"]["AuthResponse"];
export type SessionResponse = components["schemas"]["SessionResponse"];
export type AuthError = components["schemas"]["AuthError"];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

async function parseAuthError(res: Response): Promise<string> {
  try {
    const body: { detail?: AuthError } = await res.json();
    return body.detail?.message || `请求失败（${res.status}）`;
  } catch {
    return `请求失败（${res.status}）`;
  }
}

export async function fetchHealthSummary(options?: { signal?: AbortSignal }): Promise<HealthProjection> {
  const res = await fetch(`${API_BASE}/health`, {
    cache: "no-store",
    signal: options?.signal,
  });
  if (!res.ok) {
    throw new Error(`Health fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchSession(): Promise<SessionResponse> {
  const res = await fetch(`${API_BASE}/auth/session`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function register(
  email: string,
  password: string,
  agreedToTerms: boolean
): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ email, password, agreed_to_terms: agreedToTerms }),
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
}

export function statusText(status: HealthStatus): string {
  switch (status) {
    case "pass":
      return "正常";
    case "fail":
      return "异常";
    case "unknown":
      return "未知";
    default:
      return String(status);
  }
}
