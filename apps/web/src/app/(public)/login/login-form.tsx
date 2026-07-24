"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";

export default function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
    if (nextErrors.length > 0) {
      setErrors(nextErrors);
      return;
    }

    setErrors([]);
    setIsSubmitting(true);
    // T003 will wire this to the real authentication API.
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
      <div
        className="sc-card"
        style={{
          width: "100%",
          maxWidth: "24rem",
        }}
      >
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
          账户是个人画像、学习路径、项目和运行记录的归属边界。
        </p>

        <ErrorSummary errors={errors} />
        {submitted && (
          <p role="status" aria-live="polite" style={{ color: "var(--color-status-success)", marginBottom: "var(--space-4)" }}>
            登录请求已提交（T003 将连接真实认证服务）。
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
                autoComplete="current-password"
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
            <Button type="submit" variant="primary" isLoading={isSubmitting} aria-label="登录">
              登录
            </Button>
          </div>
        </form>

        {isSubmitting && <LoadingStatus message="正在登录…" />}
      </div>
    </main>
  );
}
