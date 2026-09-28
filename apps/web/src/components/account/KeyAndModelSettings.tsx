"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";

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
import {
  AMAP_BROWSER_MAP_ANCHOR,
  AMAP_WEB_SERVICE_ANCHOR,
} from "@/lib/settings-links";

import styles from "./KeyAndModelSettings.module.css";
import { MainModelSettings } from "./MainModelSettings";
import { QWEN_CREDENTIAL_FIRST_GUIDANCE, formatValidationTime } from "./qwen-settings-copy";

type CredentialGroup = "qwen" | "tavily" | "amap_web_service" | "amap_browser_map";

/** 可由消息卡锚点定位的分区（只认这两个固定 id，不做任意元素聚焦）。 */
const ANCHORED_SECTIONS = [AMAP_WEB_SERVICE_ANCHOR, AMAP_BROWSER_MAP_ANCHOR];

function CredentialState({ status }: { status?: CredentialStatus }) {
  if (!status) return <span className={styles.status}>读取中</span>;
  return (
    <span
      className={`${styles.status} ${status.configured ? styles.configured : ""}`}
      data-testid="credential-status"
    >
      {status.configured ? "已配置" : "未配置"}
      {status.effective_source === "environment" && "（环境变量提供）"}
      {status.effective_source === "credential_store" && "（凭据库）"}
    </span>
  );
}

/**
 * 保存成功后的说明：以服务端返回的 ``message`` 为准。
 *
 * 服务端才知道"这次到底证明了什么"——浏览器地图只证明了成对真实请求，被环境
 * 变量遮蔽的保存并不会生效。前端固定文案会把这些差异抹平，因此只在服务端没有
 * 给出说明时才回落到本地兜底句。
 */
function SavedNotice({ status, fallback }: { status?: CredentialStatus; fallback: string }) {
  return (
    <p className={styles.success} role="status">
      {status?.message ?? fallback}
    </p>
  );
}

/** 浏览器路径（地图代理）回写的真实运行结论。 */
function RuntimeEvidence({ status }: { status?: CredentialStatus }) {
  if (!status?.runtime_evidence) return null;
  return (
    <p className={styles.validationTime} data-testid="credential-runtime-evidence">
      {status.runtime_evidence}
    </p>
  );
}

export function KeyAndModelSettings({ returnTo = null }: { returnTo?: string | null }) {
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

  // 从消息卡带锚点跳进来时把键盘焦点移到目标凭据分区：浏览器只按锚点滚动，
  // 焦点仍停在页首，读屏与键盘用户拿不到「已到哪一组凭据」的上下文。
  useEffect(() => {
    const anchor = window.location.hash.replace(/^#/, "");
    if (!ANCHORED_SECTIONS.includes(anchor)) return;
    document.getElementById(anchor)?.focus();
  }, []);

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
        {returnTo && (
          <p>
            <Link href={returnTo} className={styles.backLink} data-testid="settings-return-to-chat">
              返回原会话
            </Link>
          </p>
        )}
        <header>
          <p className={styles.eyebrow}>账户设置 · 密钥与模型</p>
          <h1 className={styles.title}>密钥与模型管理</h1>
          <p className={styles.lead}>
            新密钥会先经过只读验证，验证成功后才替换当前凭据。已有密钥不会回显；每次更换请重新输入。
            保存成功后新凭据立即生效（后台任务无需重启）：卡上会写明这次究竟验证了什么，
            以及当前真正生效的值来自凭据库还是环境变量（环境变量优先）。
          </p>
        </header>

        {loading && <p className={styles.notice} role="status">正在读取凭据状态…</p>}
        {loadError && <ErrorSummary title="凭据状态暂不可用" errors={[loadError]} />}
        {settings?.store_error && (
          <ErrorSummary
            title="凭据库读取失败，以下状态可能不是最新"
            errors={[settings.store_error]}
          />
        )}

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
              {saved.qwen && (
                <SavedNotice status={statusFor("qwen")} fallback="Qwen 凭据已验证并保存。" />
              )}
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
              {saved.tavily && (
                <SavedNotice
                  status={statusFor("tavily")}
                  fallback="Tavily 凭据已验证并保存。"
                />
              )}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("tavily")} disabled={loading}>
                  {isSaving("tavily") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>

          <section
            id={AMAP_WEB_SERVICE_ANCHOR}
            tabIndex={-1}
            className={styles.card}
            aria-labelledby="amap-web-title"
          >
            <div className={styles.cardHeader}>
              <div>
                <h2 id="amap-web-title" className={styles.cardTitle}>高德 Web 服务</h2>
                <p className={styles.description}>
                  服务端路线规划与地点查询使用此 Key；探测只查询固定公开地址。缺少它时路线与地点查询不可用，
                  但已有路线信息不受影响，底图显示另由下方浏览器地图凭据决定。
                </p>
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
              {saved.amap_web_service && (
                <SavedNotice
                  status={statusFor("amap_web_service")}
                  fallback="高德 Web 服务凭据已验证并保存。"
                />
              )}
              <div className={styles.actions}>
                <Button type="submit" isLoading={isSaving("amap_web_service")} disabled={loading}>
                  {isSaving("amap_web_service") ? "正在验证并保存…" : "验证并保存"}
                </Button>
              </div>
            </form>
          </section>

          <section
            id={AMAP_BROWSER_MAP_ANCHOR}
            tabIndex={-1}
            className={styles.card}
            aria-labelledby="amap-js-title"
          >
            <div className={styles.cardHeader}>
              <div>
                <h2 id="amap-js-title" className={styles.cardTitle}>高德浏览器地图</h2>
                <p className={styles.description}>
                  只用于在页面里显示地图底图。保存时做两件事：只读加载器正文前缀判断可达
                  （拿到脚本不等于 Key 有效），再按高德官方代理方案在服务端把安全码与 Key
                  一起送到数据服务，用一次真实地理编码请求证明这对凭据可用。
                  底图能否真正渲染只能在浏览器首次请求底图时确认，结论由地图代理回写。
                  缺少它时底图不可用，但已取得的路线、距离、耗时与路段文字照常可用。
                </p>
              </div>
              <CredentialState status={statusFor("amap_browser_map")} />
            </div>
            <p className={styles.validationTime}>{formatValidationTime(statusFor("amap_browser_map")?.last_validated_at ?? null)}</p>
            <RuntimeEvidence status={statusFor("amap_browser_map")} />
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
                <SavedNotice
                  status={statusFor("amap_browser_map")}
                  fallback="JS API Key 与安全码已完成一次成对的真实数据服务请求并保存。"
                />
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
