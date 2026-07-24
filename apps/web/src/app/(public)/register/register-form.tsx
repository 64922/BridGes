"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";

export default function RegisterForm() {
  const router = useRouter();
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors: string[] = [];
    if (!email.trim()) {
      nextErrors.push("请输入邮箱地址。");
    }
    if (!password.trim()) {
      nextErrors.push("请输入密码。");
    }
    if (password.length < 12) {
      nextErrors.push("密码长度至少为 12 位。");
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
    try {
      await register(email, password, agreed);
      router.push("/account");
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "注册失败，请稍后重试。"]);
    } finally {
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
            <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)", minHeight: "var(--target-size)" }}>
              <input
                id="agree"
                name="agree"
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                style={{ marginTop: "0.625rem", minWidth: "var(--space-4)", minHeight: "var(--space-4)" }}
              />
              <label
                htmlFor="agree"
                style={{
                  display: "flex",
                  alignItems: "center",
                  minHeight: "var(--target-size)",
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-secondary)",
                  cursor: "pointer",
                }}
              >
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
