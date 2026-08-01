"use client";

import { useState } from "react";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { FormField } from "@/components/bridges/FormField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { StateSwitcher, type TemplateState } from "@/components/bridges/StateSwitcher";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";

/**
 * 登录页桌面模板。
 *
 * 状态：正常（可真实填写与校验）/ 提交中 / 空（初始未填写）/ 错误（登录失败摘要）/
 * 未登录（会话失效提示）。真实认证流程由 Issue 07 接入。
 */
export function LoginTemplate() {
  const [state, setState] = useState<TemplateState>("normal");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors: Record<string, string> = {};
    if (!email.trim()) errors.email = "请输入 QQ 邮箱地址。";
    if (!password.trim()) errors.password = "请输入密码。";
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
      <StateSwitcher
        value={state}
        onChange={(next) => {
          setState(next);
          setFieldErrors({});
        }}
      />

      <div className="sc-card" style={{ width: "100%", maxWidth: "24rem" }}>
        <div style={{ marginBottom: "var(--space-4)" }}>
          <BrandLogo variant="horizontal" width={150} />
        </div>
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--space-2)" }}>登录</h1>
        <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-6)" }}>
          连接你与知识之桥。账户是画像、对话与学习项目的归属边界。
        </p>

        {state === "permission" && (
          <div
            role="alert"
            style={{
              display: "flex",
              gap: "var(--space-2)",
              alignItems: "flex-start",
              padding: "var(--space-3)",
              marginBottom: "var(--space-4)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-status-wait)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-sm)",
            }}
          >
            你的会话已过期或尚未登录，请重新登录后继续。
          </div>
        )}

        {state === "error" && (
          <>
            <ErrorSummary errors={["邮箱或密码不正确，请检查后重试。"]} />
            <Button variant="secondary" size="sm" onClick={() => setState("recovery")}>
              恢复登录表单
            </Button>
          </>
        )}

        {state === "success" && (
          <StateBlock
            kind="success"
            title="登录表单验证通过"
            description="此开发模板只验证字段与交互，不调用真实认证服务。"
            actionLabel="返回登录表单"
            onAction={() => setState("normal")}
          />
        )}

        {state === "recovery" && (
          <StateBlock
            kind="recovery"
            title="登录表单已恢复"
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
              id="email"
              label="QQ 邮箱"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={setEmail}
              error={fieldErrors.email}
              hint={state === "empty" ? "表单尚未填写：请输入注册时使用的 QQ 邮箱。" : undefined}
              placeholder="例如 123456@qq.com"
            />
            <FormField
              id="password"
              label="密码"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={setPassword}
              error={fieldErrors.password}
            />
            <Button type="submit" variant="primary" isLoading={state === "loading"} aria-label="登录">
              登录
            </Button>
          </fieldset>
        </form>}

        {state === "loading" && <LoadingStatus message="正在验证账户…" />}

        <p style={{ marginTop: "var(--space-4)", fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          还没有账户？<a href="/templates/register">注册 BridGes</a>
        </p>
      </div>
    </main>
  );
}
