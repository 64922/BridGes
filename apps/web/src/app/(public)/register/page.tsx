import RegisterForm from "./register-form";

export const metadata = {
  title: "注册 — BridGes",
};

/**
 * Server component wrapper that provides page-level metadata.
 *
 * The registration form itself is a client component (RegisterForm) so it can
 * manage form state and validation; metadata cannot be exported from "use client"
 * components in the App Router.
 */
export default function RegisterPage() {
  return <RegisterForm />;
}
