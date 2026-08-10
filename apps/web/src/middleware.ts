import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { findRetiredPageRoute } from "@/lib/retired-routes";

const SESSION_COOKIE_NAME = "bridges_session";

const PUBLIC_PATHS = [
  "/",
  "/login",
  "/register",
  "/recover",
];

const PUBLIC_PREFIXES = [
  "/_next",
  "/api",
  "/favicon.ico",
  "/static",
  "/brand",
];

const DEVELOPMENT_ONLY_PREFIXES = ["/templates"];

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) {
    return true;
  }
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function privateResponse(): NextResponse {
  const response = NextResponse.next();
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

async function observeRetiredPage(request: NextRequest, endpointId: string): Promise<void> {
  const headers = new Headers();
  const probeHeader = request.headers.get("x-bridges-compatibility-probe");
  if (probeHeader) headers.set("x-bridges-compatibility-probe", "1");
  try {
    await fetch(
      new URL(`/api/compatibility/pages/${encodeURIComponent(endpointId)}`, request.url),
      { method: "POST", headers, cache: "no-store" }
    );
  } catch {
    // 观测服务短暂不可用时仍保持兼容重定向；部署门禁会通过计数缺失发现问题。
  }
}

/**
 * Server-side authentication guard for authenticated routes.
 *
 * The middleware checks for the presence of the HttpOnly session cookie. It does
 * not validate the token (the API does that on every request); its job is to
 * keep unauthenticated users out of the (app) layout and to route authenticated
 * users away from the public login/register pages.
 */
export async function middleware(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME);
  const hasSession = Boolean(sessionCookie?.value);

  const retiredPage = findRetiredPageRoute(pathname, request.nextUrl.search);
  if (retiredPage) {
    // 先记录固定路由标识，再做单次 307；不读取旧页面组件或旧模块数据。
    await observeRetiredPage(request, retiredPage.endpointId);
    return NextResponse.redirect(new URL(retiredPage.replacementPath, request.url), 307);
  }

  if (DEVELOPMENT_ONLY_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    if (process.env.NODE_ENV !== "development") {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return NextResponse.next();
  }

  if (isPublicPath(pathname)) {
    // Authenticated users don't need to see login/register pages: they go
    // straight to the new-chat home at "/".
    if (hasSession && (pathname === "/login" || pathname === "/register")) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return hasSession ? privateResponse() : NextResponse.next();
  }

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("return_to", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return privateResponse();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|.*\\..*).*)"],
};
