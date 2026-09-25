"use client";

import { useEffect, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { copyTextToClipboard } from "@/lib/clipboard";
import { CAREER_INTENT_KEYWORDS, CAREER_INTENT_PREFIXES } from "@/lib/chat-tools";
import { chatAttachmentContentUrl } from "@/lib/api";
import type {
  ArxivSearchProjection,
  ChatAttachmentProjection,
  ChatModuleId,
  ContextNoteProjection,
  ModuleSuggestionProjection,
  PaperSearchProjection,
  RetrievalRoundProjection,
  TeachingTurnProjection,
  TiebaResearchProjection,
  WebSearchProjection,
} from "@/lib/api";
import type {
  CareerPlanningProjection,
  ChatStreamCareerData,
  ChatStreamHumanizerData,
  ChatStreamNodeData,
  ChatStreamStageData,
  HumanizerResultProjection,
  ImageTaskProjection,
  ReadAloudProjection,
  VideoTaskProjection,
} from "@/lib/api";
import { ImageTaskCard } from "./chat/ImageTaskCard";
import { VideoTaskCard } from "./chat/VideoTaskCard";
import { ReadAloudControls, type CapabilityAvailability, type ReadAloudControlsHandle } from "./chat/ReadAloudControls";
import { ModuleSuggestionCard } from "./chat/ModuleSuggestionCard";
import { PaperSearchCard } from "./chat/PaperSearchCard";
import { TiebaResearchCard } from "./chat/TiebaResearchCard";
import { ArxivPaperSearchCard } from "./ArxivPaperSearchCard";
import { BrandLogo } from "./BrandLogo";
import { CareerPlanningProcessCard } from "./CareerPlanningProcessCard";
import { CareerPlanningResultCard } from "./CareerPlanningResultCard";
import { ContextNoteCard } from "./ContextNoteCard";
import { HumanizerProcessCard } from "@/components/bridges/HumanizerProcessCard";
import { HumanizerResultCard } from "@/components/bridges/HumanizerResultCard";
import { RetrievalCard } from "./RetrievalCard";
import { TeachingCard } from "./TeachingCard";
import { WebSearchCard } from "./WebSearchCard";
import { chatModuleIcon, chatModuleLabel } from "@/lib/chat-modules";

/** 会话消息只渲染服务端历史与当前自然语言结果卡。 */
export interface ChatThinking {
  /** 可公开的处理步骤（生成中会增长） */
  steps: string[];
  /** 回答采用的证据/来源说明（可空） */
  evidence: string[];
  /** 工具调用进度说明（可空） */
  tools: string[];
  /** 质量检查结论（完成/失败/停止的中文状态，可空） */
  quality: string[];
  /** 生成耗时（秒，来自真实生命周期 duration_ms）；流式中为空 */
  seconds: number | null;
}

/** Issue 06：统一阶段枚举 → 面向用户的中文阶段标签（流式阶段行）。 */
export const STAGE_LABEL: Record<string, string> = {
  queued: "排队中",
  public_search: "搜索公开来源",
  model_generation: "生成回答中",
  quality_check: "核验引用与质量",
  repair: "修复与重试",
  finalizing: "整理收尾",
};

// V2 Issue 02：日常父图节点中文名（node 事件只映射真实开始/完成的节点）。
export const NODE_LABEL: Record<string, string> = {
  validate_turn: "校验回合",
  compile_context: "编译上下文",
  select_explicit_module: "选择模块",
  invoke_subgraph_or_chat: "生成回答",
  verify_output: "核验输出",
  persist_result: "保存结果",
  // V2 Issue 11：论文子图节点（显式派发后逐步显示真实进度）。
  "paper.parse": "理解论文请求",
  "paper.plan": "规划论文检索",
  "paper.search": "检索 arXiv",
  "paper.enrich": "核对论文来源",
  "paper.rank": "筛选与排序论文",
  "paper.present": "整理论文结果",
  // V2 Issue 14：贴吧子图节点（真实节点名，不做美化猜测）。
  "tieba.parse": "理解贴吧问题",
  "tieba.search": "检索贴吧帖子",
  "tieba.read": "读取帖子页面",
  "tieba.summarize": "整理吧友说法",
  "tieba.verify_official": "核对学校官方页面",
};

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** 消息的纯文本（用于复制与朗读） */
  plainText: string;
  /** 富内容（段落、代码块、公式、表格等），由页面组装 */
  content: React.ReactNode;
  thinking?: ChatThinking;
  status?: "done" | "streaming" | "error";
  errorText?: string;
  /** Issue 20：本条助手消息绑定的分层检索轮次（含引用），无轮次为 null */
  retrieval?: RetrievalRoundProjection | null;
  /** Issue 21：本条助手消息绑定的公网搜索状态与真实引用 */
  webSearch?: WebSearchProjection | null;
  /** Issue 22：本条助手消息绑定的 arXiv 搜索状态与真实论文引用 */
  arxivSearch?: ArxivSearchProjection | null;
  /** Issue 23：学习模式的教学目标、证据门与理解检查记录 */
  teaching?: TeachingTurnProjection | null;
  /** Issue 27：本次上下文说明披露（画像切片/材料类别/用途）；无披露为 null */
  contextNote?: ContextNoteProjection | null;
  /** Issue 06：流式中的统一阶段状态（检索/生成/检查/收尾，脱敏） */
  stage?: ChatStreamStageData | null;
  /** V2 Issue 02：进行中的父图节点（started 时非空，completed 即清空）。 */
  node?: ChatStreamNodeData | null;
  /** Issue 28：文章人味化结果投影（助手消息）；非人味化消息为 null */
  humanizer?: HumanizerResultProjection | null;
  /** Issue 28：用户消息的 SKILL 载荷快照（任务摘要展示）；普通消息为 null */
  skill?: unknown;
  /** Issue 28：流式中的文章人味化过程卡状态（五态中文） */
  humanizerProcess?: ChatStreamHumanizerData | null;
  /** Issue 29：生涯规划结果投影（助手消息）；非规划消息为 null */
  careerPlanning?: CareerPlanningProjection | null;
  /** Issue 29：流式中的生涯规划过程卡状态（五态中文） */
  careerProcess?: ChatStreamCareerData | null;
  /** Issue 30：本条助手消息的朗读状态快照（服务端持久化，刷新一致） */
  readAloud?: ReadAloudProjection | null;
  /** Issue 31：本条助手消息的图片任务/资产状态快照（任务卡与资产卡） */
  image?: ImageTaskProjection | null;
  video?: VideoTaskProjection | null;
  /** Issue 05：本轮用户消息绑定的照片附件（按页序）；纯文字消息为空 */
  attachments?: ChatAttachmentProjection[] | null;
  /** V2 Issue 11：用户消息的逐条显式模块标识（重开后不随新选择改变） */
  moduleId?: string | null;
  /** V2 Issue 11：本条助手消息的论文模块状态（查询/来源/等待/失败/停止） */
  paperSearch?: PaperSearchProjection | null;
  /** V2 Issue 14：本条助手消息的贴吧信息搜集状态（已读帖子/帖链降级/官方核验） */
  tiebaResearch?: TiebaResearchProjection | null;
  /** V2 Issue 11：普通聊天中的一键模块建议（只建议，未检索） */
  moduleSuggestion?: ModuleSuggestionProjection | null;
  /** Issue 11：该轮用户消息之下的历史助手尝试（重试保留审计，不静默改写） */
  previousAttempts?: {
    attemptNumber: number;
    status: "done" | "streaming" | "error" | "stopped";
    errorMessage?: string | null;
  }[];
}

interface MessageListProps {
  messages: ChatMessage[];
  onRetry?: (id: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  /** V2 Issue 11：点击模块建议——以该轮用户消息原文显式启动模块 */
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
  conversationId?: string;
  /** Issue 31：图片任务成功（资产落库）后刷新消息列表（正文/投影同步） */
  onRefreshMessages?: () => void;
  /** Issue 30：TTS 能力可用性（账户级探测快照；不可用时禁用朗读入口并说明原因） */
  tts?: CapabilityAvailability;
}

/** Issue 29：该轮是否为生涯规划意图（显式前缀或强触发关键词命中；
 *  与后端 intent.py 规则保持一致，最终意图裁决以服务端确定性检测为准）。 */
function isCareerMessage(message: ChatMessage): boolean {
  return (
    CAREER_INTENT_PREFIXES.some((prefix) => message.plainText.startsWith(prefix)) ||
    CAREER_INTENT_KEYWORDS.some((keyword) => message.plainText.includes(keyword))
  );
}

const actionButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  border: "none",
  borderRadius: "var(--radius-md)",
  backgroundColor: "transparent",
  color: "var(--color-text-tertiary)",
  cursor: "pointer",
};

function MessageAction({
  icon,
  label,
  pressed,
  disabled,
  onClick,
}: {
  icon: IconName;
  label: string;
  pressed?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      style={{
        ...actionButtonStyle,
        color: pressed ? "var(--color-accent-primary)" : "var(--color-text-tertiary)",
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <Icon name={icon} size={18} aria-hidden />
    </button>
  );
}

function AssistantActions({
  message,
  onRetry,
  onReadAloud,
  readAloudPressed = false,
  readAloudDisabled = false,
}: {
  message: ChatMessage;
  onRetry?: (id: string) => void;
  /** Issue 30：朗读入口（消息操作栏按钮；无回调时为模板静态展示） */
  onReadAloud?: () => void;
  /** 生成中/播放中时入口按钮 pressed 态 */
  readAloudPressed?: boolean;
  /** TTS 能力不可用时禁用入口（原因在消息下方说明） */
  readAloudDisabled?: boolean;
}) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const [feedback, setFeedback] = useState<"good" | "bad" | null>(null);

  const copy = async () => {
    const copied = await copyTextToClipboard(message.plainText);
    setCopyStatus(copied ? "success" : "error");
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  return (
    <div
      role="toolbar"
      aria-label="消息操作"
      style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", marginTop: "var(--space-2)" }}
    >
      <MessageAction icon="copy" label={copyStatus === "success" ? "已复制" : "复制"} onClick={copy} />
      {copyStatus !== "idle" && (
        <span
          role={copyStatus === "error" ? "alert" : "status"}
          style={{
            fontSize: "var(--text-xs)",
            color:
              copyStatus === "error"
                ? "var(--color-status-error)"
                : "var(--color-status-success)",
          }}
        >
          {copyStatus === "success" ? "已复制" : "复制失败，请检查浏览器权限"}
        </span>
      )}
      {/* V2 issue 04：人味化写路径退役，历史人味化消息不再提供重试。 */}
      {message.humanizer == null &&
        message.humanizerProcess == null &&
        message.skill == null && (
          <MessageAction
            icon="retry"
            label="重试"
            onClick={() => onRetry?.(message.id)}
          />
        )}
      <MessageAction
        icon="feedbackGood"
        label="回答有帮助"
        pressed={feedback === "good"}
        onClick={() => setFeedback((value) => (value === "good" ? null : "good"))}
      />
      <MessageAction
        icon="feedbackBad"
        label="回答需改进"
        pressed={feedback === "bad"}
        onClick={() => setFeedback((value) => (value === "bad" ? null : "bad"))}
      />
      <MessageAction
        icon="readAloud"
        label="朗读"
        pressed={readAloudPressed}
        disabled={readAloudDisabled}
        onClick={() => onReadAloud?.()}
      />
      {feedback && (
        <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          已在当前页面标记为{feedback === "good" ? "有帮助" : "需改进"}
        </span>
      )}
    </div>
  );
}

/**
 * 可折叠思考摘要（Issue 14）。
 *
 * 生成开始时自动展开并展示进行中的步骤；完成后折叠为精确格式
 * 「已思考（用时 X 秒）」，点击或键盘激活（summary 原生 Enter/Space）
 * 可再次展开。内容只包含可公开的步骤、采用的证据、工具调用进度与
 * 质量检查结论，绝不展示原始思维链。耗时来自真实生成生命周期
 * （duration_ms），不使用硬编码数字。受控 details：显式 role=button
 * 与 aria-expanded，保证 ARIA 状态与折叠规则同步。
 */
function ThinkingSummary({
  thinking,
  streaming,
}: {
  thinking: ChatThinking;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(streaming);

  // 生成完成/失败/停止后自动折叠为「已思考（用时 X 秒）」；
  // 用户手动展开过的消息在刷新重建后保持默认折叠规则。
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
 * 消息流：用户消息气泡靠右，助手消息占整列并带操作行；
 * 思考摘要在生成中自动展开、完成后折叠为「已思考（用时 X 秒）」，可随时展开。
 */
export function MessageList({
  messages,
  onRetry,
  conversationId,
  onStop,
  onTeachingSkip,
  onTeachingBeginnerStart,
  onUseModuleSuggestion,
  tts,
  onRefreshMessages,
}: MessageListProps) {
  // Issue 30：每条助手消息的朗读控制器句柄（供消息操作栏「朗读」按钮桥接）
  const readAloudRefs = useRef(new Map<string, ReadAloudControlsHandle>());
  const [activeReadAloudId, setActiveReadAloudId] = useState<string | null>(null);
  return (
    <ol
      role="list"
      aria-label="对话消息"
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}
    >
      {messages.map((message) => (
        <li key={message.id} id={`msg-${message.id}`}>
          {message.role === "user" ? (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <div
                style={{
                  maxWidth: "85%",
                  padding: "var(--space-3) var(--space-4)",
                  borderRadius: "var(--radius-xl)",
                  backgroundColor: "var(--color-accent-primary-soft)",
                  color: "var(--color-text-primary)",
                  overflowWrap: "break-word",
                }}
              >
                {/* V2 Issue 11：本轮显式选择的模块标识（只读消息记录——
                    重开后历史标识不随输入区的新选择改变）。 */}
                {chatModuleLabel(message.moduleId) && (
                  <span
                    data-testid="user-module-label"
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "var(--space-1)",
                      marginBottom: "var(--space-1)",
                      padding: "2px var(--space-2)",
                      border: "1px solid var(--color-accent-primary)",
                      borderRadius: "var(--radius-full)",
                      fontSize: "var(--text-xs)",
                      fontWeight: 600,
                      color: "var(--color-text-secondary)",
                    }}
                  >
                    <Icon name={chatModuleIcon(message.moduleId)} size={12} aria-hidden />
                    {chatModuleLabel(message.moduleId)}
                  </span>
                )}
                {message.attachments && message.attachments.length > 0 && (
                  <div
                    role="list"
                    aria-label="消息中的照片"
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "var(--space-2)",
                      marginBottom:
                        message.content || message.plainText ? "var(--space-2)" : 0,
                    }}
                  >
                    {message.attachments.map((attachment) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        key={attachment.object_id}
                        role="listitem"
                        src={
                          conversationId
                            ? chatAttachmentContentUrl(conversationId, attachment.object_id)
                            : undefined
                        }
                        alt={`用户发送的照片：${attachment.original_filename}`}
                        style={{
                          width: 96,
                          height: 96,
                          objectFit: "cover",
                          borderRadius: "var(--radius-md)",
                          border: "1px solid var(--color-border)",
                        }}
                      />
                    ))}
                  </div>
                )}
                {message.content}
              </div>
            </div>
          ) : (
            <article style={{ display: "flex", gap: "var(--space-3)", minWidth: 0 }}>
              <span style={{ flexShrink: 0, paddingTop: "var(--space-1)" }} aria-hidden="true">
                <BrandLogo variant="icon" width={24} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p
                  style={{
                    fontWeight: 600,
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-secondary)",
                    marginBottom: "var(--space-1)",
                  }}
                >
                  BridGes
                </p>

                {message.previousAttempts && message.previousAttempts.length > 0 && (
                  <details
                    style={{
                      marginBottom: "var(--space-3)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      padding: "var(--space-2) var(--space-3)",
                      backgroundColor: "var(--color-bg-secondary)",
                      fontSize: "var(--text-sm)",
                    }}
                  >
                    <summary style={{ cursor: "pointer", color: "var(--color-text-secondary)" }}>
                      此问题的前 {message.previousAttempts.length} 次尝试
                    </summary>
                    <ol
                      style={{
                        marginTop: "var(--space-2)",
                        paddingLeft: "var(--space-4)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "var(--space-2)",
                      }}
                    >
                      {message.previousAttempts.map((attempt) => (
                        <li key={attempt.attemptNumber} style={{ color: "var(--color-text-secondary)" }}>
                          {attempt.status === "error" || attempt.status === "stopped" ? (
                            <span style={{ color: "var(--color-status-error)" }}>
                              第 {attempt.attemptNumber} 次尝试（
                              {attempt.status === "stopped" ? "已停止" : "失败"}）
                              {attempt.errorMessage ? `：${attempt.errorMessage}` : ""}
                            </span>
                          ) : (
                            <span>第 {attempt.attemptNumber} 次尝试</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}

                {/* Issue 27：本次上下文说明（画像切片/材料类别/用途披露）。
                    生成中显示 loading 态；完成/关闭/失败按披露状态呈现；
                    终态且无披露（历史消息/画像服务未挂载）不渲染卡片，
                    避免永久 loading。 */}
                {conversationId && (message.contextNote != null || message.status === "streaming") && (
                  <ContextNoteCard
                    note={message.contextNote ?? null}
                    streaming={message.status === "streaming"}
                    conversationId={conversationId}
                    messageId={message.id}
                  />
                )}

                {/* 公网搜索与论文结果保留为能力结果详情，不改变消息正文。 */}
                {conversationId && message.teaching && (
                  <TeachingCard
                    teaching={message.teaching}
                    onSkip={() => onTeachingSkip?.(message.id)}
                    onRetry={() => onRetry?.(message.id)}
                    onBeginnerStart={() => onTeachingBeginnerStart?.(message.id)}
                  />
                )}

                {conversationId && (
                  <ArxivPaperSearchCard
                    search={message.arxivSearch ?? null}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                    onCancel={onStop}
                  />
                )}

                {/* V2 Issue 11：论文模块结果卡（状态/查询词/来源记录/
                    阅读顺序与链接/证据边界/失败与重试）。 */}
                {conversationId && (
                  <PaperSearchCard
                    search={message.paperSearch ?? null}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                  />
                )}

                {/* V2 Issue 14：贴吧信息搜集结果卡（已读帖子与楼层时间/
                    帖链降级/官方核验分区/证据边界/失败与重试）。 */}
                {conversationId && (
                  <TiebaResearchCard
                    research={message.tiebaResearch ?? null}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                  />
                )}

                {/* V2 Issue 11：普通聊天中的一键模块建议（此处没有任何检索，
                    点击后才以原文显式派发子图）。 */}
                {conversationId && message.status !== "streaming" && (
                  <ModuleSuggestionCard
                    suggestion={message.moduleSuggestion ?? null}
                    onUse={(suggestion) =>
                      onUseModuleSuggestion?.(message.id, suggestion.module_id)
                    }
                  />
                )}

                {conversationId && (
                  <WebSearchCard
                    search={message.webSearch ?? null}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                    onCancel={onStop}
                  />
                )}

                {conversationId && (
                  <RetrievalCard
                    retrieval={message.retrieval ?? null}
                    conversationId={conversationId}
                    messageId={message.id}
                  />
                )}

                {/* Issue 28/V2 issue 04：文章人味化过程卡（五态中文）——
                    流式中渲染过程事件；终态由结果卡接管。写路径已退役，
                    过程卡不再提供重试入口。 */}
                {conversationId && (message.humanizerProcess != null || (message.humanizer == null && message.status === "streaming" && message.skill != null)) && (
                  <HumanizerProcessCard
                    data={message.humanizerProcess ?? null}
                    streaming={message.status === "streaming"}
                  />
                )}

                {/* Issue 28/V2 issue 04：文章人味化结果卡（输出合同五要素 +
                    事实锁/引用/体裁复核），终态后随历史加载稳定呈现；仅可
                    查看与复制，不再提供重试。 */}
                {conversationId && message.humanizer != null && (
                  <HumanizerResultCard result={message.humanizer} />
                )}

                {/* Issue 29：生涯规划过程卡（五态中文）——流式中渲染过程
                    事件；终态由结果卡接管。 */}
                {conversationId && (message.careerProcess != null || (message.careerPlanning == null && message.status === "streaming" && isCareerMessage(message))) && (
                  <CareerPlanningProcessCard
                    data={message.careerProcess ?? null}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                  />
                )}

                {/* Issue 29：生涯规划结果卡（六类分区 + 证据 + 边界声明 +
                    逐项反馈），终态后随历史加载稳定呈现。 */}
                {conversationId && message.careerPlanning != null && (
                  <CareerPlanningResultCard
                    result={message.careerPlanning}
                    conversationId={conversationId}
                    messageId={message.id}
                    onRetry={() => onRetry?.(message.id)}
                  />
                )}

                {message.thinking && (
                  <ThinkingSummary
                    thinking={message.thinking}
                    streaming={message.status === "streaming"}
                  />
                )}

                {message.status === "error" && message.skill == null ? (
                  <div
                    role="alert"
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "var(--space-2)",
                      padding: "var(--space-3)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-status-error)",
                      backgroundColor: "var(--color-status-error-bg)",
                      color: "var(--color-status-error)",
                      overflowWrap: "break-word",
                    }}
                  >
                    <Icon name="alert" size={18} aria-hidden />
                    <span style={{ fontSize: "var(--text-sm)" }}>{message.errorText}</span>
                  </div>
                ) : message.humanizer?.article?.delivery_status === "failed" ? (
                  /* Issue 08：交付失败时正文区域明确「未交付」，
                     不把违规候选显示为最终正文；展示稳定失败原因
                     （硬门/材料不足/模型错误各有具体文案，AC 4）。 */
                  <div
                    role="alert"
                    data-testid="humanizer-not-delivered"
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "var(--space-2)",
                      padding: "var(--space-3)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-status-error)",
                      backgroundColor: "var(--color-status-error-bg)",
                      color: "var(--color-status-error)",
                      overflowWrap: "break-word",
                    }}
                  >
                    <Icon name="alert" size={18} aria-hidden />
                    <span style={{ fontSize: "var(--text-sm)" }}>
                      {message.humanizer.error_message ?? "正文未交付。"}
                    </span>
                  </div>
                ) : (
                  message.content
                )}

                {message.status === "streaming" && message.node != null && (
                  <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                    {NODE_LABEL[message.node.node] ?? "正在生成回答"}…
                  </p>
                )}
                {message.status === "streaming" && message.node == null && message.stage != null && (
                  <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                    {STAGE_LABEL[message.stage.stage] ?? "正在生成回答"}…
                  </p>
                )}
                {message.status === "streaming" && message.node == null && message.stage == null && (
                  <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                    正在生成回答…
                  </p>
                )}

                <AssistantActions
                  message={message}
                  onRetry={onRetry}
                  onReadAloud={
                    conversationId
                      ? () => readAloudRefs.current.get(message.id)?.generate()
                      : undefined
                  }
                  readAloudPressed={activeReadAloudId === message.id}
                  readAloudDisabled={tts ? !tts.available : false}
                />

                {conversationId &&
                  message.role === "assistant" &&
                  message.status !== "streaming" &&
                  message.status !== "error" && (
                    <ReadAloudControls
                      ref={(handle) => {
                        if (handle) {
                          readAloudRefs.current.set(message.id, handle);
                        } else {
                          readAloudRefs.current.delete(message.id);
                        }
                      }}
                      conversationId={conversationId}
                      messageId={message.id}
                      projection={message.readAloud ?? null}
                      tts={tts ?? { available: true }}
                      onActivityChange={(active) =>
                        setActiveReadAloudId((current) =>
                          active ? message.id : current === message.id ? null : current
                        )
                      }
                    />
                  )}

                {conversationId &&
                  message.role === "assistant" &&
                  message.image &&
                  message.status !== "streaming" && (
                    <ImageTaskCard
                      conversationId={conversationId}
                      task={message.image}
                      onSucceeded={onRefreshMessages}
                    />
                  )}

                {conversationId &&
                  message.role === "assistant" &&
                  message.video &&
                  message.status !== "streaming" && (
                    <VideoTaskCard
                      conversationId={conversationId}
                      task={message.video}
                      onSucceeded={onRefreshMessages}
                    />
                  )}

              </div>
            </article>
          )}
        </li>
      ))}
    </ol>
  );
}
