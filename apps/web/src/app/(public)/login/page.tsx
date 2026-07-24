import { Suspense } from "react";

import LoginForm from "./login-form";

export const metadata = {
  title: "登录 — Science Companion",
};

/**
 * Server component wrapper that provides page-level metadata.
 *
 * The login form itself is a client component (LoginForm) so it can manage
 * form state and validation; metadata cannot be exported from "use client"
 * components in the App Router.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={<div style={{ minHeight: "100vh" }} />}>
      <LoginForm />
    </Suspense>
  );
}
