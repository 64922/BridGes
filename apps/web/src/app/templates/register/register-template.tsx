"use client";

import { useState } from "react";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { FormField } from "@/components/bridges/FormField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { useTemplateState } from "@/components/bridges/use-template-state";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";

/**
 * 注册页桌面模板。
 *
 * 字段与 CONTEXT.md 的账户模型一致：用户名、QQ 邮箱（纯数字@qq.com）、
 * 密码与确认密码。状态：正常 / 提交中 / 空 / 错误 / 未登录 / 成功 / 恢复。
 */
export function RegisterTemplate() {
  const [state, setState] = useTemplateState("normal");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors: Record<string, string> = {};
    if (!username.trim()) errors.username = "请输入用户名。";
    if (!/^\d+@qq\.com$/.test(email.trim())) errors.email = "QQ 邮箱应为纯数字 QQ 号加 @qq.com。";
    if (password.length < 12) errors.password = "密码至少需要 12 个字符。";
    if (confirm !== password) errors.confirm = "两次输入的密码不一致。";
    setFieldErrors(errors);
    const firstInvalid = Object.keys(errors)[0];
    if (firstInvalid) {
      requestAnimationFrame(() => document.getElementById(firstInvalid)?.focus());
    } else {
      setState("success");
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
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-4)",
        padding: "var(--space-6)",
      }}
    >
      <div className="sc-card" style={{ width: "100%", maxWidth: "26rem" }}>
        <div style={{ marginBottom: "var(--space-4)" }}>
          <BrandLogo variant="horizontal" width={150} />
        </div>
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--space-2)" }}>注册</h1>
        <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-6)" }}>
          创建你的 BridGes 账户，开始长期的科学学习与表达陪伴。
        </p>

        {state === "permission" && (
          <div
            role="alert"
            style={{
              padding: "var(--space-3)",
              marginBottom: "var(--space-4)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-status-wait)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-sm)",
            }}
          >
            注册需要先退出当前设备上的其他账户会话。
          </div>
        )}

        {state === "error" && (
          <>
            <ErrorSummary errors={["该 QQ 邮箱已注册，可直接登录或更换邮箱。"]} />
            <Button variant="secondary" size="sm" onClick={() => setState("recovery")}>
              恢复注册表单
            </Button>
          </>
        )}

        {state === "success" && (
          <StateBlock
            kind="success"
            title="注册表单验证通过"
            description="此开发模板只验证字段与交互，不创建真实账户。"
            actionLabel="返回注册表单"
            onAction={() => setState("normal")}
          />
        )}

        {state === "recovery" && (
          <StateBlock
            kind="recovery"
            title="注册表单已恢复"
            description="错误提示已清除，可以重新填写。"
            actionLabel="重新填写"
            onAction={() => setState("normal")}
          />
        )}

        {state !== "success" && state !== "recovery" && <form onSubmit={handleSubmit} noValidate>
          <fieldset
            disabled={state === "loading"}
            style={{ border: "none", display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
          >
            <FormField
              id="username"
              label="用户名"
              autoComplete="username"
              required
              value={username}
              onChange={setUsername}
              error={fieldErrors.username}
              hint={state === "empty" ? "表单尚未填写：用户名可随时修改，大小写不敏感唯一。" : undefined}
            />
            <FormField
              id="email"
              label="QQ 邮箱"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={setEmail}
              error={fieldErrors.email}
              placeholder="例如 123456@qq.com"
            />
            <FormField
              id="password"
              label="密码"
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={setPassword}
              error={fieldErrors.password}
              hint="至少 12 个字符。"
            />
            <FormField
              id="confirm"
              label="确认密码"
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={setConfirm}
              error={fieldErrors.confirm}
            />
            <Button type="submit" variant="primary" isLoading={state === "loading"} aria-label="注册">
              注册
            </Button>
          </fieldset>
        </form>}

        {state === "loading" && <LoadingStatus message="正在创建账户…" />}

        <p style={{ marginTop: "var(--space-4)", fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          已有账户？<a href="/templates/login">直接登录</a>
        </p>
      </div>
    </main>
  );
}
