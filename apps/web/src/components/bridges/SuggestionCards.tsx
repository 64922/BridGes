"use client";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { CHAT_TOOL_INTENTS, IMAGE_TOOL_LABEL, VIDEO_TOOL_LABEL } from "@/lib/chat-tools";

export interface SuggestionCard {
  icon: IconName;
  label: string;
  description: string;
  /** 点击后预填进输入区的结构化意图（不直接产生工具结果） */
  prefill: string;
}

/** V2 issue 04：文章人味化入口退役，空白态保留论文搜索与生涯规划两张建议卡。 */
const SUGGESTION_DESCRIPTIONS: Record<string, string> = {
  "论文搜索": "描述研究主题，先聊清需求再检索",
  "生涯规划助手": "说明你的阶段与目标，一起排优先级",
};

export const SUGGESTION_CARDS: readonly SuggestionCard[] = CHAT_TOOL_INTENTS.filter(
  (tool) =>
    tool.label !== IMAGE_TOOL_LABEL && tool.label !== VIDEO_TOOL_LABEL
).map((tool) => ({
  icon: tool.icon,
  label: tool.label,
  description: SUGGESTION_DESCRIPTIONS[tool.label] ?? "",
  prefill: tool.prefix,
}));

interface SuggestionCardsProps {
  /** 点击卡片：按参考网页逻辑预填输入区，走正常消息流（授权/审计/保存不跳过） */
  onPrefill: (prefill: string) => void;
}

/**
 * 建议卡列表：真实按钮，键盘可达；悬停与焦点有过渡反馈，
 * 长文案在窄列内折行，不破坏桌面布局。
 */
export function SuggestionCards({ onPrefill }: SuggestionCardsProps) {
  return (
    <ul
      role="list"
      aria-label="建议入口"
      data-testid="suggestion-cards"
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: "var(--space-3)",
        margin: 0,
        padding: 0,
        listStyle: "none",
      }}
    >
      {SUGGESTION_CARDS.map((card) => (
        <li key={card.label} style={{ display: "flex" }}>
          <button
            type="button"
            onClick={() => onPrefill(card.prefill)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: "var(--space-1)",
              width: "13rem",
              maxWidth: "100%",
              minHeight: "var(--target-size)",
              padding: "var(--space-3) var(--space-4)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-lg)",
              backgroundColor: "var(--color-surface)",
              boxShadow: "var(--shadow-sm)",
              color: "var(--color-text-primary)",
              fontSize: "var(--text-sm)",
              textAlign: "left",
              cursor: "pointer",
              transition:
                "border-color var(--motion-duration-fast) var(--motion-easing), box-shadow var(--motion-duration-fast) var(--motion-easing)",
            }}
            onMouseEnter={(event) => {
              event.currentTarget.style.borderColor = "var(--color-border-strong)";
              event.currentTarget.style.boxShadow = "var(--shadow-md)";
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.borderColor = "var(--color-border)";
              event.currentTarget.style.boxShadow = "var(--shadow-sm)";
            }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-2)",
                fontWeight: 600,
              }}
            >
              <span aria-hidden="true" style={{ color: "var(--color-accent-primary)", display: "inline-flex" }}>
                <Icon name={card.icon} size={20} aria-hidden />
              </span>
              {card.label}
            </span>
            <span style={{ color: "var(--color-text-tertiary)", overflowWrap: "break-word" }}>
              {card.description}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
