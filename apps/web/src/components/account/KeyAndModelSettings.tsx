"use client";

import { useEffect, useState, type FormEvent } from "react";

import { FormField } from "@/components/bridges/FormField";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import type { CredentialSettings as CredentialSettingsValue, CredentialStatus } from "@/lib/api";
import {
  fetchCredentialSettings,
  replaceAmapBrowserMapCredential,
  replaceAmapWebServiceCredential,
  replaceQwenCredential,
  replaceTavilyCredential,
} from "@/lib/api";

import styles from "./KeyAndModelSettings.module.css";
import { MainModelSettings } from "./MainModelSettings";

type CredentialGroup = "qwen" | "tavily" | "amap_web_service" | "amap_browser_map";

/** 密钥未配置时字段附近的操作顺序说明（与后端指引同一句口径）。 */
const QWEN_CREDENTIAL_FIRST_GUIDANCE =
  "尚未配置 Qwen 密钥：请先在这里输入密钥并验证保存，再到下方「Qwen 主模型 ID」验证并保存模型。";

function formatValidationTime(value: string | null): string {
  if (!value) return "尚无验证记录";
  return `最近验证：${new Date(value).toLocaleString("zh-CN")}`;
}

function CredentialState({ status }: { status?: CredentialStatus }) {
  if (!status) return <span className={styles.status}>读取中</span>;
  return (
    <span
      className={`${styles.status} ${status.configured ? styles.configured : ""}`}
      data-testid="credential-status"
    >
      {status.configured ? "已配置" : "未配置"}
    </span>
  );
}

export function KeyAndModelSettings() {
  const [settings, setSettings] = useState<CredentialSettingsValue | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keys, setKeys] = useState({
    qwen: "",
    tavily: "",
    amapWebService: "",
    amapJs: "",
    amapSecurity: "",
  });
  const [saving, setSaving] = useState<CredentialGroup | null>(null);
  const [errors, setErrors] = useState<Partial<Record<CredentialGroup, string>>>({});
  const [saved, setSaved] = useState<Partial<Record<CredentialGroup, boolean>>>({});
  // 密钥更新成功后主模型卡片要重新读取「密钥是否可用」。
  const [modelReloadKey, setModelReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    fetchCredentialSettings()
      .then((result) => {
        if (active) setSettings(result);
      })
      .catch((cause: unknown) => {
        if (active) {
          setLoadError(
            cause instanceof Error ? cause.message : "暂时无法读取凭据状态。"
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const setCredentialStatus = (group: CredentialGroup, status: CredentialStatus) => {
    setSettings((current) => {
      if (!current) return current;
      if (group === "qwen") return { ...current, qwen: status };
      if (group === "tavily") return { ...current, tavily: status };
      if (group === "amap_web_service") {
        return { ...current, amap: { ...current.amap, web_service: status } };
      }
      return { ...current, amap: { ...current.amap, browser_map: status } };
    });
  };

  const submit = async (
    event: FormEvent<HTMLFormElement>,
    group: CredentialGroup,
    save: () => Promise<CredentialStatus>,
    clear: () => void
  ) => {
    event.preventDefault();
    setErrors((current) => ({ ...current, [group]: undefined }));
    setSaved((current) => ({ ...current, [group]: false }));
    setSaving(group);
    try {
      const result = await save();
      setCredentialStatus(group, result);
      clear();
      setSaved((current) => ({ ...current, [group]: true }));
      if (group === "qwen") setModelReloadKey((current) => current + 1);
    } catch (cause) {
      setErrors((current) => ({
        ...current,
        [group]: cause instanceof Error ? cause.message : "验证失败，请检查凭据后重试。",
      }));
    } finally {
      setSaving(null);
    }
  };

  const isSaving = (group: CredentialGroup) => saving === group;
  const statusFor = (group: CredentialGroup) => {
    if (group === "qwen") return settings?.qwen;
    if (group === "tavily") return settings?.tavily;
    if (group === "amap_web_service") return settings?.amap.web_service;
    return settings?.amap.browser_map;
  };

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header>
          <p className={styles.eyebrow}>账户设置 · 密钥与模型</p>
          <h1 className={styles.title}>密钥与模型管理</h1>
          <p className={styles.lead}>
            新密钥会先经过只读验证，验证成功后才替换当前凭据。已有密钥不会回显；每次更换请重新输入。
            Qwen 凭据与主模型按「先密钥、后模型」的顺序分别验证保存，失败时原设置保持有效。
          </p>
        </header>

        {loading && <p className={styles.notice} role="status">正在读取凭据状态…</p>}
        {loadError && <ErrorSummary title="凭据状态暂不可用" errors={[loadError]} />}

        <div className={styles.stack}>
          <section className={styles.card} aria-labelledby="qwen-credential-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="qwen-credential-title" className={styles.cardTitle}>Qwen 凭据</h2>
                <p className={styles.description}>
                  主对话、视觉识别与 OCR 的全局运行密钥。验证会先查该密钥可见的模型信息，再做一次最小真实调用。
                  知识库向量模型与索引版本单独固定，不随此处更换。
                </p>
              </div>
              <CredentialState status={statusFor("qwen")} />
            </div>
            <p className={styles.validationTime}>{formatValidationTime(statusFor("qwen")?.last_validated_at ?? null)}</p>
            {statusFor("qwen")?.error && <p className={styles.errorText}>{statusFor("qwen")?.error}</p>}
            <form
              className={styles.form}
              onSubmit={(event) =>
                submit(
                  event,
                  "qwen",
                  () => replaceQwenCredential(keys.qwen),
                  () => setKeys((current) => ({ ...current, qwen: "" }))
                )
              }
            >
              <FormField
                id="qwen-api-key"
                label="Qwen API Key"
                type="password"
                value={keys.qwen}
                onChange={(value) => setKeys((current) => ({ ...current, qwen: value }))}
                placeholder="输入新密钥"
                autoComplete="new-password"
                required
              />
              {statusFor("qwen")?.configured === false && !errors.qwen && (
                <p className={styles.guidance}>{QWEN_CREDENTIAL_FIRST_GUIDANCE}</p>
              )}
              {errors.qwen && <ErrorSummary title="Qwen 密钥验证失败" errors={[errors.qwen]} />}
              {saved.qwen && <p className={styles.success} role="status">Qwen 凭据已验证并保存。</p>}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("qwen")} disabled={loading}>
                  {isSaving("qwen") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>

          <MainModelSettings reloadKey={modelReloadKey} />

          <section className={styles.card} aria-labelledby="tavily-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="tavily-title" className={styles.cardTitle}>Tavily 搜索</h2>
                <p className={styles.description}>用于联网搜索。验证会发送固定的最小只读搜索请求。</p>
              </div>
              <CredentialState status={statusFor("tavily")} />
            </div>
            <p className={styles.validationTime}>{formatValidationTime(statusFor("tavily")?.last_validated_at ?? null)}</p>
            {statusFor("tavily")?.error && <p className={styles.errorText}>{statusFor("tavily")?.error}</p>}
            <form
              className={styles.form}
              onSubmit={(event) =>
                submit(
                  event,
                  "tavily",
                  () => replaceTavilyCredential(keys.tavily),
                  () => setKeys((current) => ({ ...current, tavily: "" }))
                )
              }
            >
              <FormField
                id="tavily-api-key"
                label="Tavily API Key"
                type="password"
                value={keys.tavily}
                onChange={(value) => setKeys((current) => ({ ...current, tavily: value }))}
                placeholder="输入新密钥"
                autoComplete="new-password"
                required
              />
              {errors.tavily && <ErrorSummary title="Tavily 验证失败" errors={[errors.tavily]} />}
              {saved.tavily && <p className={styles.success} role="status">Tavily 凭据已验证并保存。</p>}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("tavily")} disabled={loading}>
                  {isSaving("tavily") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>

          <section className={styles.card} aria-labelledby="amap-web-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="amap-web-title" className={styles.cardTitle}>高德 Web 服务</h2>
                <p className={styles.description}>服务端路线与地理编码使用此 Key；探测只查询固定公开地址。</p>
              </div>
              <CredentialState status={statusFor("amap_web_service")} />
            </div>
            <p className={styles.validationTime}>{formatValidationTime(statusFor("amap_web_service")?.last_validated_at ?? null)}</p>
            {statusFor("amap_web_service")?.error && <p className={styles.errorText}>{statusFor("amap_web_service")?.error}</p>}
            <form
              className={styles.form}
              onSubmit={(event) =>
                submit(
                  event,
                  "amap_web_service",
                  () => replaceAmapWebServiceCredential(keys.amapWebService),
                  () => setKeys((current) => ({ ...current, amapWebService: "" }))
                )
              }
            >
              <FormField
                id="amap-web-service-key"
                label="高德 Web 服务 Key"
                type="password"
                value={keys.amapWebService}
                onChange={(value) => setKeys((current) => ({ ...current, amapWebService: value }))}
                placeholder="输入新密钥"
                autoComplete="new-password"
                required
              />
              {errors.amap_web_service && <ErrorSummary title="高德 Web 服务验证失败" errors={[errors.amap_web_service]} />}
              {saved.amap_web_service && <p className={styles.success} role="status">高德 Web 服务凭据已验证并保存。</p>}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("amap_web_service")} disabled={loading}>
                  {isSaving("amap_web_service") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>

          <section className={styles.card} aria-labelledby="amap-js-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="amap-js-title" className={styles.cardTitle}>高德浏览器地图</h2>
                <p className={styles.description}>
                  对 JS API 加载器做只读探测；安全码与 Key 的配对由地图代理请求验证。
                </p>
              </div>
              <CredentialState status={statusFor("amap_browser_map")} />
            </div>
            <p className={styles.validationTime}>{formatValidationTime(statusFor("amap_browser_map")?.last_validated_at ?? null)}</p>
            {statusFor("amap_browser_map")?.error && <p className={styles.errorText}>{statusFor("amap_browser_map")?.error}</p>}
            <form
              className={styles.form}
              onSubmit={(event) =>
                submit(
                  event,
                  "amap_browser_map",
                  () => replaceAmapBrowserMapCredential(keys.amapJs, keys.amapSecurity),
                  () => setKeys((current) => ({ ...current, amapJs: "", amapSecurity: "" }))
                )
              }
            >
              <FormField
                id="amap-js-api-key"
                label="高德 JS API Key（Web 平台）"
                type="password"
                value={keys.amapJs}
                onChange={(value) => setKeys((current) => ({ ...current, amapJs: value }))}
                placeholder="输入新密钥"
                autoComplete="new-password"
                required
              />
              <FormField
                id="amap-security-js-code"
                label="高德 JS API 安全码"
                type="password"
                value={keys.amapSecurity}
                onChange={(value) => setKeys((current) => ({ ...current, amapSecurity: value }))}
                placeholder="输入新安全码"
                autoComplete="new-password"
                required
                hint="只提交到 BridGes 并与 JS API Key 成组保存在服务端；不会返回到页面状态或写入地图脚本。"
              />
              {errors.amap_browser_map && <ErrorSummary title="高德浏览器地图验证失败" errors={[errors.amap_browser_map]} />}
              {saved.amap_browser_map && (
                <p className={styles.success} role="status">
                  JS API Key 已完成加载器连通性探测并保存。安全码只保存在服务端；与 Key 的配对会由地图代理请求实际验证。
                </p>
              )}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("amap_browser_map")} disabled={loading}>
                  {isSaving("amap_browser_map") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}
