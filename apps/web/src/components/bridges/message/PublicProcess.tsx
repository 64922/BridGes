"use client";

import { useEffect, useState } from "react";

import type { ChatThinking } from "@/lib/chat-message";

import type { ChatMessageView } from "./types";

const STAGE_LABEL: Record<string, string> = {
  queued: "排队中",
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
  "paper.search": "检索 arXiv",
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
 * 可折叠思考摘要：生成中默认展开；完成后折叠为「已思考（用时 X 秒）」。
 * 内容只包含可公开的步骤、采用的证据、工具调用进度与质量检查结论，
 * 不展示原始思维链。耗时来自真实生成生命周期（duration_ms）。
 */
export function ThinkingSummary({
  thinking,
  streaming,
}: {
  thinking: ChatThinking;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(streaming);

  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  const collapsedLabel =
    thinking.seconds !== null
      ? `已思考（用时 ${thinking.seconds} 秒）`
      : streaming
        ? "正在思考…"
        : "已思考";

  const sections: { label: string; items: string[]; numbered: boolean }[] = [];
  if (thinking.steps.length > 0) {
    sections.push({ label: "处理步骤", items: thinking.steps, numbered: true });
  }
  if (thinking.evidence.length > 0) {
    sections.push({ label: "采用的证据", items: thinking.evidence, numbered: false });
  }
  if (thinking.tools.length > 0) {
    sections.push({ label: "工具进度", items: thinking.tools, numbered: false });
  }
  if (thinking.quality.length > 0) {
    sections.push({ label: "质量检查", items: thinking.quality, numbered: false });
  }

  return (
    <details
      open={open}
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
      data-testid="thinking-summary"
      style={{
        marginBottom: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-2) var(--space-3)",
        backgroundColor: "var(--color-bg-secondary)",
        transition: "border-color 150ms ease",
      }}
    >
      <summary
        role="button"
        aria-expanded={open}
        style={{
          cursor: "pointer",
          fontSize: "var(--text-sm)",
          color: streaming ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          minHeight: "var(--target-size)",
          padding: "0 var(--space-1)",
        }}
      >
        {streaming && <ThinkingSpinner />}
        {collapsedLabel}
      </summary>
      <div
        style={{
          marginTop: "var(--space-1)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
          fontSize: "var(--text-sm)",
          color: "var(--color-text-secondary)",
        }}
      >
        {sections.map((section) => (
          <div key={section.label}>
            <p
              style={{
                margin: 0,
                marginBottom: "var(--space-1)",
                fontWeight: 600,
                color: "var(--color-text-secondary)",
              }}
            >
              {section.label}
            </p>
            {section.numbered ? (
              <ol
                style={{
                  margin: 0,
                  paddingLeft: "var(--space-5)",
                  listStyle: "decimal",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {section.items.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ol>
            ) : (
              <ul
                style={{
                  margin: 0,
                  paddingLeft: "var(--space-5)",
                  listStyle: "disc",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {section.items.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

export function GenerationStageLine({ message }: { message: ChatMessageView }) {
  if (message.status !== "streaming") return null;
  const label = message.node
    ? (NODE_LABEL[message.node.node] ?? "正在生成回答")
    : message.stage
      ? (STAGE_LABEL[message.stage.stage] ?? "正在生成回答")
      : "正在生成回答";
  return (
    <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
      {label}…
    </p>
  );
}
