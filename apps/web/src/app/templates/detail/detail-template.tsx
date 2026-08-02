"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import { TemplateShell } from "@/components/bridges/TemplateShell";
import { useTemplateState } from "@/components/bridges/use-template-state";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import { copyTextToClipboard } from "@/lib/clipboard";

const META_ROWS: [string, string][] = [
  ["来源类型", "本地知识库文档"],
  ["所属账户", "示例账户"],
  ["索引版本", "emb-v2（Embedding qwen-text-embedding-v3 · 1024 维）"],
  ["最近更新", "2026-08-01 21:14"],
  ["对象 ID", "obj_9f2c-拉格朗日方程推导笔记"],
];

const CITATIONS = [
  "《经典力学笔记》第 4 章「最小作用量原理」，第 61–64 页，2026-03 扫描归档",
  "Goldstein, H. Classical Mechanics, 3rd ed., §2.1–2.3（书目信息，本地书签）",
  "对话「拉格朗日方程的物理意义是什么」中的 BridGes 回答，2026-08-02，已核对引用",
];

const DETAIL_SPEECH_TEXT =
  "拉格朗日方程推导笔记。从最小作用量到欧拉拉格朗日方程。核心关系为：对每个广义坐标，拉格朗日量对广义速度的偏导数随时间的导数，减去拉格朗日量对广义坐标的偏导数，等于零。";

/**
 * 详情页桌面模板：能力结果详情 —— 标题、状态、元数据、
 * 依据与引用、正文（含公式与表格）以及操作行。
 * 状态：正常 / 加载中 / 空（内容不存在）/ 错误 / 未登录。
 */
export function DetailTemplate() {
  const searchParams = useSearchParams();
  const section = searchParams.get("section") ?? "knowledge";
  const id = searchParams.get("id") ?? "doc1";

  const [state, setState] = useTemplateState("normal");
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const [checkedSections, setCheckedSections] = useState<number | null>(null);
  const [reading, setReading] = useState(false);
  const [speechError, setSpeechError] = useState("");
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  const toggleReading = () => {
    if (reading) {
      window.speechSynthesis.cancel();
      utteranceRef.current = null;
      setReading(false);
      return;
    }
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      setSpeechError("当前浏览器不支持朗读。");
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(DETAIL_SPEECH_TEXT);
    utterance.lang = "zh-CN";
    utterance.onend = () => {
      utteranceRef.current = null;
      setReading(false);
    };
    utterance.onerror = () => {
      utteranceRef.current = null;
      setReading(false);
      setSpeechError("朗读失败，请检查系统语音设置后重试。");
    };
    setSpeechError("");
    utteranceRef.current = utterance;
    setReading(true);
    window.speechSynthesis.speak(utterance);
  };

  useEffect(
    () => () => {
      if (utteranceRef.current) window.speechSynthesis.cancel();
    },
    [],
  );

  const renderBody = () => {
    if (state === "loading") {
      return <StateBlock kind="loading" title="正在加载详情…" />;
    }
    if (state === "error") {
      return (
        <StateBlock
          kind="error"
          title="详情加载失败"
          description="读取对象时出现异常，请重试。"
          actionLabel="重试"
          onAction={() => setState("recovery")}
        />
      );
    }
    if (state === "permission") {
      return (
        <StateBlock
          kind="permission"
          title="需要登录"
          description="该内容属于其他账户或你尚未登录，无权查看。"
          actionLabel="前往登录"
          onAction={() => {
            window.location.href = "/templates/login";
          }}
        />
      );
    }
    if (state === "empty") {
      return (
        <StateBlock
          kind="empty"
          title="没有找到该内容"
          description={`对象 ${id} 不存在、已被删除或已被撤回。它可能曾在「${section}」中。`}
          actionLabel="返回列表"
          onAction={() => {
            window.location.href = `/templates/list?section=${section}`;
          }}
        />
      );
    }
    if (state === "success") {
      return (
        <StateBlock
          kind="success"
          title="详情操作已完成"
          description="当前页面的本地复制、朗读或完整性检查已完成。"
          actionLabel="返回详情"
          onAction={() => setState("normal")}
        />
      );
    }
    if (state === "recovery") {
      return (
        <StateBlock
          kind="recovery"
          title="详情已恢复"
          description="加载错误已清除，可以重新查看内容。"
          actionLabel="返回详情"
          onAction={() => setState("normal")}
        />
      );
    }
    return (
      <article style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)", minWidth: 0 }}>
        <header style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <StatusBadge status="qualified" label="已索引" />
            <StatusBadge status="evidence_bound" label="已绑定证据" />
          </div>
          <h1 style={{ fontSize: "var(--text-2xl)", overflowWrap: "break-word" }}>
            拉格朗日方程推导笔记：从最小作用量到欧拉-拉格朗日方程
          </h1>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            能力结果详情 · 检索依据与引用可随时核对
          </p>
        </header>

        <div
          role="toolbar"
          aria-label="详情操作"
          style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}
        >
          <Button
            variant="secondary"
            size="sm"
            aria-label="复制引用"
            onClick={async () => {
              const copied = await copyTextToClipboard(CITATIONS.join("\n"));
              setCopyStatus(copied ? "success" : "error");
              window.setTimeout(() => setCopyStatus("idle"), 2000);
            }}
          >
            <Icon name="copy" size={16} aria-hidden />
            {copyStatus === "success" ? "已复制引用" : "复制引用"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            aria-label="检查内容完整性"
            onClick={() => {
              setCheckedSections(document.querySelectorAll("[data-detail-section]").length);
            }}
          >
            <Icon name="check" size={16} aria-hidden />
            检查内容完整性
          </Button>
          <Button
            variant="secondary"
            size="sm"
            aria-label={reading ? "停止朗读" : "朗读正文"}
            aria-pressed={reading}
            onClick={toggleReading}
          >
            <Icon name="readAloud" size={16} aria-hidden />
            {reading ? "停止朗读" : "朗读正文"}
          </Button>
          {reading && (
            <span role="status" style={{ alignSelf: "center", fontSize: "var(--text-sm)", color: "var(--color-accent-primary)" }}>
              朗读中…
            </span>
          )}
          {copyStatus === "error" && (
            <span role="alert" style={{ alignSelf: "center", fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
              复制失败，请检查浏览器权限。
            </span>
          )}
          {checkedSections !== null && (
            <span role="status" style={{ alignSelf: "center", fontSize: "var(--text-sm)", color: "var(--color-status-success)" }}>
              已检查 {checkedSections} 个内容区块，均可读取。
            </span>
          )}
          {speechError && (
            <span role="alert" style={{ alignSelf: "center", fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
              {speechError}
            </span>
          )}
        </div>

        <section aria-labelledby="meta-heading" className="sc-card" data-detail-section>
          <h2 id="meta-heading" style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-3)" }}>
            元数据
          </h2>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--text-sm)" }}>
              <tbody>
                {META_ROWS.map(([key, value]) => (
                  <tr key={key}>
                    <th
                      scope="row"
                      style={{
                        textAlign: "left",
                        whiteSpace: "nowrap",
                        padding: "var(--space-2) var(--space-3)",
                        border: "1px solid var(--color-border)",
                        backgroundColor: "var(--color-bg-secondary)",
                        color: "var(--color-text-secondary)",
                        fontWeight: 500,
                      }}
                    >
                      {key}
                    </th>
                    <td
                      style={{
                        padding: "var(--space-2) var(--space-3)",
                        border: "1px solid var(--color-border)",
                        color: "var(--color-text-primary)",
                        overflowWrap: "break-word",
                      }}
                    >
                      {value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="content-heading" data-detail-section>
          <h2 id="content-heading" style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-2)" }}>
            正文摘要
          </h2>
          <p style={{ overflowWrap: "break-word", color: "var(--color-text-primary)" }}>
            本笔记从最小作用量原理出发，对广义坐标 q 与广义速度 q̇ 写出拉格朗日量
            L = T − V，再对路径做变分，得到欧拉-拉格朗日方程：
          </p>
          <p
            style={{
              margin: "var(--space-3) 0",
              padding: "var(--space-3) var(--space-4)",
              borderLeft: "3px solid var(--color-accent-primary)",
              backgroundColor: "var(--color-bg-secondary)",
              borderRadius: "0 var(--radius-md) var(--radius-md) 0",
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-lg)",
              overflowX: "auto",
              whiteSpace: "nowrap",
            }}
          >
            δS = δ∫ L(q, q̇, t) dt = 0  ⟹  ∂L/∂q − d/dt (∂L/∂q̇) = 0
          </p>
          <p style={{ overflowWrap: "break-word", color: "var(--color-text-primary)" }}>
            笔记随后用单摆、阿特伍德机和双摆三个例子演示坐标选择如何吸收约束力，
            并给出了每个例子的量纲检查与极限情形验证。
          </p>
        </section>

        <section aria-labelledby="citation-heading" className="sc-card" data-detail-section>
          <h2 id="citation-heading" style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-3)" }}>
            依据与引用
          </h2>
          <ol
            style={{
              paddingLeft: "var(--space-5)",
              listStyle: "decimal",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
            }}
          >
            {CITATIONS.map((citation) => (
              <li key={citation} style={{ overflowWrap: "break-word" }}>
                {citation}
              </li>
            ))}
          </ol>
        </section>
      </article>
    );
  };

  return (
    <TemplateShell activeModule={section}>
      <div
        style={{
          width: "100%",
          maxWidth: "var(--chat-column-width)",
          margin: "0 auto",
          padding: "var(--space-6)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-4)",
        }}
      >
        {renderBody()}
      </div>
    </TemplateShell>
  );
}
