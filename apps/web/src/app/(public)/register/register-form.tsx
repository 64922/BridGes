"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";

export default function RegisterForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(false);
    const nextErrors: string[] = [];
    if (!email.trim()) {
      nextErrors.push("请输入邮箱地址。");
    }
    if (!password.trim()) {
      nextErrors.push("请输入密码。");
    }
    if (password !== confirmPassword) {
      nextErrors.push("两次输入的密码不一致。");
    }
    if (!agreed) {
      nextErrors.push("请同意服务条款和隐私政策。");
    }
    if (nextErrors.length > 0) {
      setErrors(nextErrors);
      return;
    }

    setErrors([]);
    setIsSubmitting(true);
    setTimeout(() => {
      setIsSubmitting(false);
      setSubmitted(true);
    }, 300);
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
        <h1
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "var(--text-2xl)",
            marginBottom: "var(--space-2)",
          }}
        >
          注册
        </h1>
        <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-6)" }}>
          注册即创建你的个人学习、项目与记忆归属边界。
        </p>

        <ErrorSummary errors={errors} />
        {submitted && (
          <p role="status" aria-live="polite" style={{ color: "var(--color-status-success)", marginBottom: "var(--space-4)" }}>
            注册请求已提交（T003 将连接真实认证服务）。
          </p>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <label htmlFor="email">邮箱</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                style={{
                  minHeight: "var(--target-size)",
                  padding: "0.625rem var(--space-3)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border-strong)",
                  fontSize: "var(--text-base)",
                }}
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <label htmlFor="password">密码</label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                style={{
                  minHeight: "var(--target-size)",
                  padding: "0.625rem var(--space-3)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border-strong)",
                  fontSize: "var(--text-base)",
                }}
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <label htmlFor="confirmPassword">确认密码</label>
              <input
                id="confirmPassword"
                name="confirmPassword"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                style={{
                  minHeight: "var(--target-size)",
                  padding: "0.625rem var(--space-3)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border-strong)",
                  fontSize: "var(--text-base)",
                }}
              />
            </div>
            <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
              <input
                id="agree"
                name="agree"
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                style={{ marginTop: "0.25rem" }}
              />
              <label htmlFor="agree" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
                我已阅读并同意服务条款和隐私政策
              </label>
            </div>
            <Button type="submit" variant="primary" isLoading={isSubmitting} aria-label="注册">
              注册
            </Button>
          </div>
        </form>

        {isSubmitting && <LoadingStatus message="正在注册…" />}
      </div>
    </main>
  );
}
