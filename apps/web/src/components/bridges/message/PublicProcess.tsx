"use client";

import { useId, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import type { ChatMessageView } from "@/lib/chat-message-view";
import { IMAGE_STATUS_LABELS, VIDEO_STATUS_LABELS } from "@/lib/media-status";

import { ContextNoteDetails } from "./ContextNoteDetails";

const STAGE_LABEL: Record<string, string> = {
  queued: "排队等待中",
  local_retrieval: "检索本地资料",
  public_search: "搜索公开来源",
  model_generation: "生成回答中",
  quality_check: "核验引用与质量",
  repair: "修复与重试",
  finalizing: "整理收尾",
};

const NODE_LABEL: Record<string, string> = {
  validate_turn: "校验回合",
  compile_context: "编译上下文",
  select_explicit_module: "选择模块",
  invoke_subgraph_or_chat: "生成回答",
  verify_output: "核验输出",
  persist_result: "保存结果",
  "study.recognize": "识别本节书页",
  "study.map": "核对知识范围",
  "study.preview": "整理预习问题",
  "study.tutor": "依据书页解答",
  "study.finish_pages": "更新本节书页",
  "study.plan_review": "安排本节复盘题",
  "study.grade": "核对本题回答",
  "study.pause_review": "暂停复盘回辅导",
  "study.summarize": "整理本节总结",
  "paper.parse": "理解论文请求",
  "paper.plan": "规划论文检索",
  "paper.search": "检索学术来源",
  "paper.enrich": "核对论文来源",
  "paper.rank": "筛选与排序论文",
  "paper.present": "整理论文结果",
  "tieba.parse": "理解贴吧问题",
  "tieba.search": "检索贴吧帖子",
  "tieba.read": "读取帖子页面",
  "tieba.summarize": "整理吧友说法",
  "tieba.verify_official": "核对学校官方页面",
  "github.parse": "理解项目想法",
  "github.search": "检索公开仓库",
  "github.inspect": "核对仓库证据",
  "github.rank": "按功能匹配排序",
  "github.present": "整理推荐结果",
  "career.parse": "理解求职目标",
  "career.plan": "制定检索计划",
  "career.collect": "读取公开岗位",
  "career.filter": "筛选匹配岗位",
  "career.analyze": "归纳技能与薪资",
  "career.advise": "给出求职建议",
  "resources.parse": "理解学习需求",
  "resources.search_books": "检索图书书目",
  "resources.search_videos": "查找哔哩哔哩视频",
  "resources.rank": "筛选与排序资料",
  "resources.present": "整理资料清单",
  "route.parse": "理解通勤请求",
  "route.resolve": "定位起终点",
  "route.request": "查询高德路线",
  "route.buffer": "计算课间缓冲",
  "route.present": "整理路线结果",
};

const CONTEXT_TOOL_MARKER = "你已授权的用户背景信息";

function publicTools(tools: string[]): string[] {
  // 服务端把上下文披露也写入 thinking.tools（工单 27）；这里由专门的
  // 上下文说明承载，避免展开后同一披露出现两次。
  return tools.filter((item) => !item.includes(CONTEXT_TOOL_MARKER));
}

function ThinkingSpinner() {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 12,
        height: 12,
        borderRadius: "50%",
        border: "2px solid var(--color-border-strong)",
        borderTopColor: "var(--color-accent-primary)",
        animation: "thinking-spin 0.8s linear infinite",
        flexShrink: 0,
      }}
    />
  );
}

/**
 * 单行公开进度：只使用已经发生的节点、阶段、耗时与公开摘要，
 * 不模拟计时、不展示内部推理。生成中和历史回放都默认收起。
 */
export function publicProcessLabel(message: ChatMessageView): string {
  if (message.status === "streaming") {
    if (message.node) {
      return `${NODE_LABEL[message.node.node] ?? "正在生成回答"}…`;
    }
    if (message.stage?.status === "active") {
      return `${STAGE_LABEL[message.stage.stage] ?? "正在生成回答"}…`;
    }
    return "正在生成回答…";
  }
  if (message.status === "error") return "本轮生成失败";
  if (message.status === "stopped") return "本轮已停止";
  if (message.thinking?.seconds != null) {
    return `回答已生成 · 用时 ${message.thinking.seconds} 秒`;
  }
  return "回答已生成";
}

function ProcessList({
  title,
  items,
  numbered = false,
}: {
  title: string;
  items: string[];
  numbered?: boolean;
}) {
  if (items.length === 0) return null;
  const listStyle = {
    margin: 0,
    paddingLeft: "var(--space-5)",
    display: "flex",
    flexDirection: "column" as const,
    gap: "var(--space-1)",
    color: "var(--color-text-secondary)",
  };
  const entries = items.map((item, index) => (
    <li key={`${item}-${index}`}>{item}</li>
  ));
  return (
    <div>
      <p
        style={{
          margin: 0,
          marginBottom: "var(--space-1)",
          fontWeight: 600,
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
        }}
      >
        {title}
      </p>
      {numbered ? (
        <ol style={{ ...listStyle, listStyle: "decimal" }}>{entries}</ol>
      ) : (
        <ul style={{ ...listStyle, listStyle: "disc" }}>{entries}</ul>
      )}
    </div>
  );
}

function MediaTaskSection({
  mediaTask,
}: {
  mediaTask: { kind: string; status: string; prompt: string } | null;
}) {
  if (!mediaTask) return null;
  return (
    <div data-testid="public-process-media">
      <p
        style={{
          margin: 0,
          marginBottom: "var(--space-1)",
          fontWeight: 600,
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
        }}
      >
        媒体任务
      </p>
      <p style={{ margin: 0 }}>
        {mediaTask.kind}生成 · {mediaTask.status} · {mediaTask.prompt}
      </p>
      <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
        媒体任务在后台独立进行；状态与结果在正文旁就近显示。
      </p>
    </div>
  );
}

export function PublicProcess({
  message,
  conversationId,
}: {
  message: ChatMessageView;
  conversationId?: string;
}) {
  const [open, setOpen] = useState(false);
  const generatedId = useId();
  const detailsId = `public-process-details-${generatedId.replace(/:/g, "")}`;
  const streaming = message.status === "streaming";
  const thinking = message.thinking;
  const tools = publicTools(thinking?.tools ?? []);
  const mediaTask = message.image
    ? {
        kind: "图片",
        status: IMAGE_STATUS_LABELS[message.image.status],
        prompt: message.image.prompt,
      }
    : message.video
      ? {
          kind: "视频",
          status: VIDEO_STATUS_LABELS[message.video.status],
          prompt: message.video.prompt,
        }
      : null;
  const hasThinkingDetails =
    (thinking?.steps.length ?? 0) > 0 ||
    (thinking?.evidence.length ?? 0) > 0 ||
    tools.length > 0 ||
    (thinking?.quality.length ?? 0) > 0 ||
    mediaTask != null;

  return (
    <section
      data-testid="public-process"
      data-status={message.status ?? "done"}
      aria-label="本轮公开过程"
      style={{ marginBottom: "var(--space-3)" }}
    >
      <button
        type="button"
        data-testid="public-process-toggle"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen((value) => !value)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          width: "100%",
          minHeight: "var(--target-size)",
          padding: "0 var(--space-1)",
          border: "none",
          backgroundColor: "transparent",
          color:
            message.status === "streaming"
              ? "var(--color-accent-primary)"
              : "var(--color-text-secondary)",
          font: "inherit",
          fontSize: "var(--text-sm)",
          fontWeight: 500,
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        {streaming && <ThinkingSpinner />}
        <span data-testid="public-process-label">{publicProcessLabel(message)}</span>
        <span
          aria-hidden="true"
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            transform: open ? "rotate(180deg)" : "none",
            color: "var(--color-text-tertiary)",
          }}
        >
          <Icon name="chevronDown" size={16} />
        </span>
      </button>

      {open && (
        <div
          id={detailsId}
          data-testid="public-process-details"
          style={{
            marginTop: "var(--space-2)",
            paddingTop: "var(--space-2)",
            borderTop: "1px solid var(--color-border)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          {!hasThinkingDetails && (
            <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
              {streaming
                ? "目前还没有可公开的步骤或来源。"
                : "本轮没有更多可公开的步骤或来源。"}
            </p>
          )}
          {thinking && (
            <>
              <ProcessList title="公开步骤" items={thinking.steps} numbered />
              <ProcessList title="来源说明" items={thinking.evidence} />
              <ProcessList title="工具进度" items={tools} />
              <ProcessList title="质量检查" items={thinking.quality} />
            </>
          )}
          <MediaTaskSection mediaTask={mediaTask} />
          <ContextNoteDetails
            note={message.contextNote}
            streaming={streaming}
            conversationId={conversationId}
            messageId={message.id}
          />
        </div>
      )}
    </section>
  );
}
