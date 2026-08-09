"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { FormField } from "@/components/bridges/FormField";
import { Menu } from "@/components/bridges/Menu";
import { StateBlock } from "@/components/bridges/StateBlock";
import { TemplateShell, DEMO_RECENTS } from "@/components/bridges/TemplateShell";
import { useTemplateState } from "@/components/bridges/use-template-state";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { Button } from "@/components/design-system/Button";
import { StatusBadge } from "@/components/design-system/StatusBadge";

interface ListItem {
  id: string;
  title: string;
  subtitle: string;
  badge?: { status: React.ComponentProps<typeof StatusBadge>["status"]; label: string };
}

interface ListSection {
  key: string;
  title: string;
  icon: IconName;
  description: string;
  items: ListItem[];
}

const SECTIONS: ListSection[] = [
  {
    key: "conversations",
    title: "最近对话",
    icon: "recent",
    description: "日常陪伴与学习模式的全部对话，按最近活跃排序。",
    items: DEMO_RECENTS.map((item) => ({
      id: item.id,
      title: item.title,
      subtitle: `${item.mode}模式 · 今天`,
    })),
  },
  {
    key: "knowledge",
    title: "本地知识库",
    icon: "knowledgeBase",
    description: "加密保存在本机的个人材料与索引，只属于你的账户。",
    items: [
      {
        id: "doc1",
        title: "2026-春季学期-经典力学-分析力学专题-拉格朗日方程推导笔记-手写扫描版.pdf",
        subtitle: "文档 · 42 页 · 索引版本 emb-v2",
        badge: { status: "qualified", label: "已索引" },
      },
      {
        id: "doc2",
        title: "量子纠错综述阅读清单.md",
        subtitle: "笔记 · 12 条引用 · 索引版本 emb-v2",
        badge: { status: "running", label: "索引中" },
      },
      {
        id: "doc3",
        title: "实验数据-曲线图-2026-08-02.png",
        subtitle: "图片 · 2.4 MB",
        badge: { status: "waiting", label: "待索引" },
      },
    ],
  },
  {
    key: "projects",
    title: "学习项目",
    icon: "learningProject",
    description: "围绕一个学习主题组织相关对话和项目文件的文件夹。",
    items: [
      {
        id: "p1",
        title: "分析力学专题",
        subtitle: "6 条对话 · 3 个文件",
        badge: { status: "running", label: "进行中" },
      },
      {
        id: "p2",
        title: "量子信息入门",
        subtitle: "2 条对话 · 5 个文件",
        badge: { status: "pass", label: "阶段完成" },
      },
    ],
  },
  {
    key: "plugins",
    title: "插件",
    icon: "plugins",
    description: "已安装的声明式 SKILL 与 MCP 服务，按账户隔离。",
    items: [
      {
        id: "s1",
        title: "BridGes 人性化表达 SKILL",
        subtitle: "内置只读 · v0.3.1",
        badge: { status: "pass", label: "已启用" },
      },
      {
        id: "s2",
        title: "arXiv 论文检索（MCP）",
        subtitle: "已声明权限：网络 arxiv.org",
        badge: { status: "waiting", label: "待授权" },
      },
    ],
  },
  {
    key: "profile",
    title: "用户画像",
    icon: "profile",
    description: "证据化画像切片：每条带来源、授权范围与有效期。",
    items: [
      {
        id: "pr1",
        title: "学习目标：通过分析力学课程考试",
        subtitle: "来源：用户明确保存 · 有效期至 2026-12-31",
        badge: { status: "approved", label: "已确认" },
      },
      {
        id: "pr2",
        title: "表达偏好：结论先行，少用被动句",
        subtitle: "来源：对话观察 · 候选画像",
        badge: { status: "waiting", label: "待确认" },
      },
    ],
  },
];

/**
 * 列表页桌面模板：最近对话与各模块列表共用一套行组件、
 * 搜索过滤与状态体系。状态：正常 / 加载中 / 空 / 错误 / 未登录。
 */
export function ListTemplate() {
  const searchParams = useSearchParams();
  const sectionKey = searchParams.get("section") ?? "conversations";
  const section = SECTIONS.find((item) => item.key === sectionKey) ?? SECTIONS[0];

  const [state, setState] = useTemplateState("normal");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState(section.items);
  const [editingItem, setEditingItem] = useState<ListItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [removedItem, setRemovedItem] = useState<ListItem | null>(null);
  const [actionMessage, setActionMessage] = useState("");

  useEffect(() => {
    setItems(section.items);
    setRemovedItem(null);
    setActionMessage("");
  }, [section]);

  const filtered = useMemo(() => {
    const keyword = query.trim();
    if (!keyword) return items;
    return items.filter(
      (item) => item.title.includes(keyword) || item.subtitle.includes(keyword),
    );
  }, [items, query]);

  const renderBody = () => {
    if (state === "loading") {
      return <StateBlock kind="loading" title={`正在加载${section.title}…`} />;
    }
    if (state === "error") {
      return (
        <StateBlock
          kind="error"
          title={`${section.title}加载失败`}
          description="读取本地数据时出现异常，请重试。"
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
          description={`${section.title}属于你的个人账户，登录后才能查看。`}
          actionLabel="前往登录"
          onAction={() => {
            window.location.href = "/templates/login";
          }}
        />
      );
    }
    if (state === "empty" || filtered.length === 0) {
      return (
        <StateBlock
          kind="empty"
          title={
            state === "empty" ? `还没有${section.title}` : `没有匹配「${query.trim()}」的条目`
          }
          description={
            state === "empty"
              ? `当你的${section.title}产生内容后会显示在这里。`
              : "换个关键词试试，或清除搜索条件。"
          }
          actionLabel={filtered.length === 0 && state === "normal" ? "清除搜索" : undefined}
          onAction={filtered.length === 0 && state === "normal" ? () => setQuery("") : undefined}
        />
      );
    }
    if (state === "success") {
      return (
        <StateBlock
          kind="success"
          title={`${section.title}操作已完成`}
          description="条目变更已应用到当前开发模板的列表状态。"
          actionLabel="查看列表"
          onAction={() => setState("normal")}
        />
      );
    }
    if (state === "recovery") {
      return (
        <StateBlock
          kind="recovery"
          title={`${section.title}已恢复`}
          description="加载错误已清除，可以重新查看列表。"
          actionLabel="返回列表"
          onAction={() => setState("normal")}
        />
      );
    }
    return (
      <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        {filtered.map((item) => (
          <li
            key={item.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-1)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-lg)",
              backgroundColor: "var(--color-surface)",
              paddingRight: "var(--space-1)",
            }}
          >
            <Link
              href={`/templates/detail?section=${section.key}&id=${item.id}`}
              aria-label={`打开 ${item.title}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-3)",
                flex: 1,
                minWidth: 0,
                padding: "var(--space-3) var(--space-4)",
                textDecoration: "none",
                color: "var(--color-text-primary)",
              }}
            >
              <span aria-hidden="true" style={{ color: "var(--color-accent-primary)", display: "inline-flex" }}>
                <Icon name={section.icon} size={20} aria-hidden />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    display: "block",
                    fontWeight: 500,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={item.title}
                >
                  {item.title}
                </span>
                <span
                  style={{
                    display: "block",
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-tertiary)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {item.subtitle}
                </span>
              </span>
              {item.badge && <StatusBadge status={item.badge.status} label={item.badge.label} />}
              <Icon name="chevronRight" size={16} aria-hidden />
            </Link>
            <Menu
              ariaLabel={`条目操作：${item.title}`}
              trigger={<Icon name="more" size={18} aria-hidden />}
              triggerStyle={{ width: "var(--target-size)", padding: 0, justifyContent: "center" }}
              items={[
                {
                  label: "固定到顶部",
                  icon: "recent",
                  onSelect: () => {
                    setItems((current) => [item, ...current.filter((candidate) => candidate.id !== item.id)]);
                    setActionMessage(`已将「${item.title}」固定到列表顶部。`);
                  },
                },
                {
                  label: "重命名",
                  icon: "humanize",
                  returnFocus: false,
                  onSelect: () => {
                    setEditingItem(item);
                    setRenameValue(item.title);
                  },
                },
                {
                  label: "从列表移除",
                  icon: "close",
                  danger: true,
                  onSelect: () => {
                    setItems((current) => current.filter((candidate) => candidate.id !== item.id));
                    setRemovedItem(item);
                    setActionMessage(`已从列表移除「${item.title}」。`);
                  },
                },
              ]}
            />
          </li>
        ))}
      </ul>
    );
  };

  return (
    <TemplateShell activeModule={section.key}>
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
        <div>
          <h1 style={{ fontSize: "var(--text-2xl)" }}>{section.title}</h1>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            {section.description}
          </p>
        </div>

        <div>
          <label htmlFor="list-search" className="sc-visually-hidden">
            搜索{section.title}
          </label>
          <input
            id="list-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`搜索${section.title}`}
            style={{
              width: "100%",
              minHeight: "var(--target-size)",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "var(--color-surface)",
              color: "var(--color-text-primary)",
              fontSize: "var(--text-base)",
            }}
          />
        </div>

        {renderBody()}

        {actionMessage && (
          <div
            role="status"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-3)",
              padding: "var(--space-3)",
              border: "1px solid var(--color-status-success)",
              borderRadius: "var(--radius-md)",
              backgroundColor: "var(--color-status-success-bg)",
              color: "var(--color-status-success)",
              fontSize: "var(--text-sm)",
            }}
          >
            <span style={{ flex: 1 }}>{actionMessage}</span>
            {removedItem && (
              <Button
                variant="secondary"
                size="sm"
                aria-label="撤销移除"
                onClick={() => {
                  setItems((current) => [...current, removedItem]);
                  setActionMessage(`已恢复「${removedItem.title}」。`);
                  setRemovedItem(null);
                }}
              >
                撤销
              </Button>
            )}
          </div>
        )}
      </div>

      <Dialog
        open={editingItem !== null}
        onClose={() => setEditingItem(null)}
        title="重命名条目"
        description="新名称只更新当前模板中的本地状态。"
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const nextTitle = renameValue.trim();
            if (!editingItem || !nextTitle) return;
            setItems((current) =>
              current.map((item) =>
                item.id === editingItem.id ? { ...item, title: nextTitle } : item,
              ),
            );
            setActionMessage(`已将条目重命名为「${nextTitle}」。`);
            setEditingItem(null);
          }}
          style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
        >
          <FormField id="rename-list-item" label="条目名称" value={renameValue} onChange={setRenameValue} required />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
            <Button variant="ghost" onClick={() => setEditingItem(null)} aria-label="取消重命名">
              取消
            </Button>
            <Button type="submit" variant="primary" disabled={!renameValue.trim()} aria-label="保存新名称">
              保存
            </Button>
          </div>
        </form>
      </Dialog>
    </TemplateShell>
  );
}
