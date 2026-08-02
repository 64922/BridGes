"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { FormField } from "@/components/bridges/FormField";
import { PasswordField } from "@/components/bridges/PasswordField";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";

const FROM_NOTICES: Record<string, string> = {
  logout: "你已安全退出，会话已撤销。",
  switch: "请使用目标账户的用户名或 QQ 邮箱登录。",
};

/**
 * 登录表单：单一标识字段（当前用户名或 QQ 邮箱）+ 密码。
 *
 * 失败提示统一为后端返回的中文消息，不区分标识是否存在；字段级错误在提交前
 * 于客户端给出并把焦点移动到第一个错误字段。
 */
export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // 只允许站内路径，避免 return_to 被用作开放重定向。
  const rawReturnTo = searchParams.get("return_to") || "/";
  const returnTo = rawReturnTo.startsWith("/") && !rawReturnTo.startsWith("//") ? rawReturnTo : "/";
  const fromNotice = FROM_NOTICES[searchParams.get("from") || ""];
  const { login } = useAuth();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [summaryErrors, setSummaryErrors] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    if (!identifier.trim()) {
      nextErrors.identifier = "请输入用户名或 QQ 邮箱。";
    }
    if (!password) {
      nextErrors.password = "请输入密码。";
    }
    setFieldErrors(nextErrors);
    setSummaryErrors([]);
    const firstInvalid = Object.keys(nextErrors)[0];
    if (firstInvalid) {
      requestAnimationFrame(() => document.getElementById(firstInvalid)?.focus());
      return;
    }

    setIsSubmitting(true);
    try {
      await login(identifier.trim(), password);
      router.push(returnTo);
    } catch (err) {
      setSummaryErrors([
        err instanceof TypeError
          ? "网络异常，请检查连接后重试。"
          : err instanceof Error
            ? err.message
            : "登录失败，请稍后重试。",
      ]);
      setIsSubmitting(false);
    }
  };

  return (
    <main
      id="main-content"
      tabIndex={-1}
      data-testid="main-content"
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-6)",
      }}
    >
      <div className="sc-card" style={{ width: "100%", maxWidth: "24rem" }}>
        <div style={{ marginBottom: "var(--space-6)" }}>
          <BrandLogo variant="horizontal" width={150} />
        </div>

        <h1
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "var(--text-2xl)",
            marginBottom: "var(--space-2)",
          }}
        >
          登录
        </h1>
        <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-6)" }}>
          使用当前用户名或 QQ 邮箱登录同一个账户。
        </p>

        {fromNotice && (
          <p
            role="status"
            style={{
              marginBottom: "var(--space-4)",
              padding: "var(--space-3)",
              borderRadius: "var(--radius-md)",
              backgroundColor: "var(--color-bg)",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
            }}
          >
            {fromNotice}
          </p>
        )}

        <ErrorSummary errors={summaryErrors} />

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <FormField
              id="identifier"
              label="用户名或 QQ 邮箱"
              autoComplete="username"
              placeholder="例如 桥桥 或 123456@qq.com"
              value={identifier}
              onChange={setIdentifier}
              error={fieldErrors.identifier}
              required
            />
            <PasswordField
              id="password"
              label="密码"
              autoComplete="current-password"
              value={password}
              onChange={setPassword}
              error={fieldErrors.password}
              required
            />
            <Button type="submit" variant="primary" isLoading={isSubmitting} aria-label="登录">
              登录
            </Button>
          </div>
        </form>

        {isSubmitting && <LoadingStatus message="正在登录…" />}

        <p
          style={{
            marginTop: "var(--space-6)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          还没有账户？{" "}
          <Link href="/register" style={{ color: "var(--color-accent-primary)" }}>
            注册 BridGes
          </Link>
        </p>
      </div>
    </main>
  );
}
