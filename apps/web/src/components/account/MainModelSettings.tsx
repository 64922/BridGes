"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { FormField } from "@/components/bridges/FormField";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import type { ModelCapabilities, ModelSettings, ModelValidationReport } from "@/lib/api";
import { fetchModelSettings, replaceModelConfiguration } from "@/lib/api";

import styles from "./KeyAndModelSettings.module.css";

/** 能力展示顺序（与后端核对的能力集合一致）。 */
const CAPABILITY_FIELDS: { key: keyof ModelCapabilities; label: string }[] = [
  { key: "text", label: "文本" },
  { key: "image", label: "图片" },
  { key: "tool_calling", label: "工具调用" },
  { key: "structured_output", label: "结构化输出" },
];

/** 密钥未配置时字段附近的操作顺序说明（与后端返回的指引同一句口径）。 */
const CREDENTIAL_FIRST_GUIDANCE =
  "当前没有可用的 Qwen 密钥：请先在上方「Qwen 凭据」中输入密钥并验证保存，再填写并验证主模型 ID。";

function formatTokenCount(value: number | null | undefined): string {
  if (typeof value !== "number" || value <= 0) return "未提供";
  return `${value.toLocaleString("zh-CN")} tokens`;
}

function formatValidationTime(value: string | null | undefined): string {
  if (!value) return "尚无验证记录";
  return `最近验证：${new Date(value).toLocaleString("zh-CN")}`;
}

function CapabilityList({
  capabilities,
  testIdPrefix = "capability",
}: {
  capabilities: ModelCapabilities;
  testIdPrefix?: string;
}) {
  return (
    <ul className={styles.capabilityList}>
      {CAPABILITY_FIELDS.map(({ key, label }) => {
        const supported = capabilities[key];
        return (
          <li
            key={key}
            className={styles.capabilityRow}
            data-testid={`${testIdPrefix}-${key}`}
          >
            <Icon name={supported ? "check" : "cross"} size={16} aria-hidden />
            <span className={styles.capabilityLabel}>{label}</span>
            <span className={supported ? styles.capabilityOk : styles.capabilityMissing}>
              {supported ? "支持" : "不支持"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ValidationReport({ report }: { report: ModelValidationReport }) {
  const checks = report.checks ?? [];
  return (
    <div className={styles.report} data-testid="model-validation-report">
      <p className={styles.reportTitle}>
        {report.passed ? "最近一次验证：通过" : "最近一次验证：未通过"}
      </p>
      <p className={styles.validationTime}>候选模型 ID：{report.model_id}</p>
      {!report.passed && report.message && (
        <p className={styles.errorText}>{report.message}</p>
      )}
      {report.passed ? (
        <p className={styles.validationTime}>
          上下文长度：{formatTokenCount(report.context_window)}；最大输入额度：
          {formatTokenCount(report.max_input_tokens)}
        </p>
      ) : (
        // 只有拿到元数据证据时才展示候选能力档案：纯元数据查询失败时能力
        // 是「未核对」而不是「不支持」，不能替用户下结论。
        checks.length > 0 && (
          <>
            <p className={styles.validationTime}>
              候选模型上下文长度：{formatTokenCount(report.context_window)}
            </p>
            <CapabilityList
              capabilities={report.capabilities}
              testIdPrefix="candidate-capability"
            />
          </>
        )
      )}
      {checks.length > 0 && (
        <ul className={styles.checkList}>
          {checks.map((check, index) => (
            <li key={`${check.capability}-${check.source}-${index}`} className={styles.checkRow}>
              <Icon name={check.ok ? "check" : "cross"} size={16} aria-hidden />
              <span>{check.label}</span>
              <span className={styles.checkSource}>
                {check.source === "probe" ? "真实调用" : "百炼元数据"}
              </span>
              <span className={check.ok ? styles.capabilityOk : styles.capabilityMissing}>
                {check.ok ? "通过" : check.message ?? "未通过"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function MainModelSettings({ reloadKey = 0 }: { reloadKey?: number }) {
  const [model, setModel] = useState<ModelSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [candidate, setCandidate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setModel(await fetchModelSettings());
      setLoadError(null);
    } catch (cause) {
      setLoadError(
        cause instanceof Error ? cause.message : "暂时无法读取主模型配置。"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setSaving(true);
    try {
      const result = await replaceModelConfiguration(candidate.trim());
      setModel(result);
      setCandidate("");
      setSuccess(
        result.last_validation?.message ?? "验证通过，已从下一条消息起使用新配置。"
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "验证失败，请检查模型 ID 后重试。");
      // 失败结论（逐项能力与具体原因）由服务端保留，重新读取以显示候选模型
      // 到底哪一项没通过；读取失败时保留原展示，不覆盖已知状态。
      try {
        setModel(await fetchModelSettings());
      } catch {
        /* 保留当前展示 */
      }
    } finally {
      setSaving(false);
    }
  };

  const credentialConfigured = model?.credential_configured ?? true;

  return (
    <section className={styles.card} aria-labelledby="main-model-title">
      <div className={styles.cardHeader}>
        <div>
          <h2 id="main-model-title" className={styles.cardTitle}>Qwen 主模型</h2>
          <p className={styles.description}>
            主对话、视觉识别和 OCR 都按这里的运行配置调用。配置通过验证后原子生效：
            正在进行的轮次保持启动时的模型锁，历史回复记录的模型不改写。
          </p>
        </div>
        {model && (
          <span className={`${styles.status} ${styles.configured}`} data-testid="model-source">
            {model.source === "settings" ? "已验证配置" : "出厂默认"}
          </span>
        )}
      </div>

      {loading && <p className={styles.notice} role="status">正在读取模型配置…</p>}
      {loadError && <ErrorSummary title="模型配置暂不可用" errors={[loadError]} />}
      {model?.error && (
        <ErrorSummary
          title="运行配置读取失败，当前仍按出厂默认模型运行"
          errors={[model.error]}
        />
      )}

      {model && (
        <dl className={styles.facts}>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>实际模型 ID</dt>
            <dd className={styles.factValue} data-testid="active-model-id">
              {model.model_id}
            </dd>
          </div>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>上下文长度</dt>
            <dd className={styles.factValue}>{formatTokenCount(model.context_window)}</dd>
          </div>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>最大输入额度</dt>
            <dd className={styles.factValue}>{formatTokenCount(model.max_input_tokens)}</dd>
          </div>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>能力</dt>
            <dd className={styles.factValue}>
              <CapabilityList capabilities={model.capabilities} />
            </dd>
          </div>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>配置版本</dt>
            <dd className={styles.factValue}>
              第 {model.revision} 版 · 元数据合同 {model.metadata_version}
            </dd>
          </div>
          <div className={styles.factRow}>
            <dt className={styles.factTerm}>验证时间</dt>
            <dd className={styles.factValue}>{formatValidationTime(model.validated_at)}</dd>
          </div>
        </dl>
      )}

      {model?.last_validation && <ValidationReport report={model.last_validation} />}

      <form className={styles.form} onSubmit={submit}>
        <FormField
          id="qwen-main-model-id"
          label="Qwen 主模型 ID"
          value={candidate}
          onChange={setCandidate}
          placeholder="输入百炼模型 ID"
          hint="没有预设列表：只接受你手填的完整模型 ID，提交后会先查百炼元数据再用真实调用探测。"
          required
        />
        {!credentialConfigured && (
          <p className={styles.guidance} data-testid="model-credential-guidance">
            {CREDENTIAL_FIRST_GUIDANCE}
          </p>
        )}
        {error && <ErrorSummary title="主模型验证失败" errors={[error]} />}
        {success && (
          <p className={styles.success} role="status">
            {success}
          </p>
        )}
        <div className={styles.actions}>
          <Button type="submit" isLoading={saving} disabled={loading}>
            {saving ? "正在验证并保存…" : "验证并保存"}
          </Button>
        </div>
      </form>
    </section>
  );
}
