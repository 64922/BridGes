"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";

import styles from "./InspectorPanel.module.css";

interface InspectorSection {
  title: string;
  items: { term: string; value: string }[];
}

const defaultSections: InspectorSection[] = [
  {
    title: "画像与记忆切片",
    items: [
      { term: "本次调用", value: "0 条" },
      { term: "未使用敏感项", value: "未采集" },
    ],
  },
  {
    title: "项目材料和证据覆盖",
    items: [
      { term: "来源", value: "未导入" },
      { term: "EvidenceSet", value: "空" },
    ],
  },
  {
    title: "领域包与事实锁",
    items: [
      { term: "领域包", value: "未选择" },
      { term: "事实锁", value: "无" },
    ],
  },
  {
    title: "模型能力与运行锁",
    items: [
      { term: "能力", value: "未注册" },
      { term: "运行锁", value: "无" },
    ],
  },
  {
    title: "当前授权范围与对象域",
    items: [
      { term: "对象域", value: "个人保险库" },
      { term: "授权版本", value: "T002" },
    ],
  },
  {
    title: "发布风险、质量门和待确认项",
    items: [
      { term: "质量门", value: "未启动" },
      { term: "待确认", value: "无" },
    ],
  },
];

/**
 * Context inspector panel.
 *
 * Displays why the current task behaves as it does, what it uses, and what is
 * missing. On desktop it is a persistent right rail; on mobile it becomes a
 * bottom drawer toggled by a floating button.
 */
export function InspectorPanel({ sections = defaultSections }: { sections?: InspectorSection[] }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className={styles.mobileToggle}>
        <Button
          variant="primary"
          size="md"
          aria-expanded={open}
          aria-controls="context-inspector"
          aria-label={open ? "关闭上下文检查器" : "打开上下文检查器"}
          onClick={() => setOpen((prev) => !prev)}
          data-testid="inspector-toggle"
        >
          <Icon name="search" size={18} aria-hidden />
          上下文检查器
        </Button>
      </div>
      <aside
        id="context-inspector"
        aria-label="上下文检查器"
        className={`${styles.inspector} ${open ? styles.open : ""}`}
        data-testid="context-inspector"
      >
        <h2 className="sc-landmark-label">上下文检查器</h2>
        {sections.map((section) => (
          <section key={section.title} className={styles.section}>
            <h3 className={styles.sectionTitle}>{section.title}</h3>
            <dl className={styles.definitionList}>
              {section.items.map((item) => (
                <div key={item.term} className={styles.definitionRow}>
                  <dt className={styles.definitionTerm}>{item.term}</dt>
                  <dd className={styles.definitionValue}>{item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </aside>
    </>
  );
}
