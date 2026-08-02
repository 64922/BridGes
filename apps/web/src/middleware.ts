import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

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

/**
 * Server-side authentication guard for authenticated routes.
 *
 * The middleware checks for the presence of the HttpOnly session cookie. It does
 * not validate the token (the API does that on every request); its job is to
 * keep unauthenticated users out of the (app) layout and to route authenticated
 * users away from the public login/register pages.
 */
export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME);
  const hasSession = Boolean(sessionCookie?.value);

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
    return NextResponse.next();
  }

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("return_to", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|.*\\..*).*)"],
};
