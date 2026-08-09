"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { FormField } from "@/components/bridges/FormField";
import { PasswordField } from "@/components/bridges/PasswordField";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";

const QQ_EMAIL_PATTERN = /^\d+@qq\.com$/;
const USERNAME_PATTERN = /^[^@\s]{1,32}$/;

/**
 * 注册表单：用户名 + 纯数字 QQ 邮箱 + 密码。
 *
 * 注册时不发送邮箱验证码；QQ 邮箱只校验格式与唯一性，邮件提醒需之后在个人
 * 设置中配置 SMTP 授权码并完成自发自收验证后才能启用（ADR-0017）。
 */
export default function RegisterForm() {
  const router = useRouter();
  const { register } = useAuth();

  const [username, setUsername] = useState("");
  const [qqEmail, setQqEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [summaryErrors, setSummaryErrors] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    if (!USERNAME_PATTERN.test(username.trim())) {
      nextErrors.username = "用户名需为 1-32 个字符，不能包含空格或 @ 符号。";
    }
    if (!QQ_EMAIL_PATTERN.test(qqEmail.trim().toLowerCase())) {
      nextErrors.qqEmail = "QQ 邮箱应为纯数字 QQ 号加 @qq.com。";
    }
    if (password.length < 12) {
      nextErrors.password = "密码长度至少为 12 位。";
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
      await register(username.trim(), qqEmail.trim(), password);
      router.push("/");
    } catch (err) {
      setSummaryErrors([
        err instanceof TypeError
          ? "网络异常，请检查连接后重试。"
          : err instanceof Error
            ? err.message
            : "注册失败，请稍后重试。",
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
          注册
        </h1>
        <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-6)" }}>
          注册即创建你的个人学习、项目与记忆归属边界。
        </p>

        <ErrorSummary errors={summaryErrors} />

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <FormField
              id="username"
              label="用户名"
              autoComplete="username"
              placeholder="例如 桥桥"
              value={username}
              onChange={setUsername}
              error={fieldErrors.username}
              hint="公开显示名称，之后可以修改；登录时也可使用。"
              required
            />
            <FormField
              id="qqEmail"
              label="QQ 邮箱"
              type="email"
              autoComplete="email"
              placeholder="例如 123456@qq.com"
              value={qqEmail}
              onChange={setQqEmail}
              error={fieldErrors.qqEmail}
              hint="仅支持纯数字 QQ 号加 @qq.com。该地址仅用于登录和账户识别，注册时不会发送验证码。"
              required
            />
            <PasswordField
              id="password"
              label="密码"
              autoComplete="new-password"
              value={password}
              onChange={setPassword}
              error={fieldErrors.password}
              hint="至少 12 位。"
              required
            />
            <Button type="submit" variant="primary" isLoading={isSubmitting} aria-label="注册">
              注册
            </Button>
          </div>
        </form>

        {isSubmitting && <LoadingStatus message="正在注册…" />}

        <p
          style={{
            marginTop: "var(--space-6)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          已有账户？{" "}
          <Link href="/login" style={{ color: "var(--color-accent-primary)" }}>
            直接登录
          </Link>
        </p>
      </div>
    </main>
  );
}
