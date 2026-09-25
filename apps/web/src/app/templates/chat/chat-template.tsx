"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Composer } from "@/components/bridges/Composer";
import { MessageList, type ChatMessage } from "@/components/bridges/MessageList";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { StateBlock } from "@/components/bridges/StateBlock";
import { TemplateShell, DEMO_RECENTS } from "@/components/bridges/TemplateShell";
import { useTemplateState } from "@/components/bridges/use-template-state";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { copyTextToClipboard } from "@/lib/clipboard";

/* ---------- 富内容块：代码 / 公式 / 表格 / 附件 ---------- */

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  return (
    <figure
      style={{
        margin: "var(--space-3) 0",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
      }}
    >
      <figcaption
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-1) var(--space-3)",
          backgroundColor: "var(--color-bg-secondary)",
          borderBottom: "1px solid var(--color-border)",
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
        }}
      >
        {language}
        <button
          type="button"
          aria-label="复制代码"
          onClick={async () => {
            const copied = await copyTextToClipboard(code);
            setCopyStatus(copied ? "success" : "error");
            window.setTimeout(() => setCopyStatus("idle"), 2000);
          }}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            border: "none",
            background: "none",
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-xs)",
            cursor: "pointer",
            minHeight: "1.75rem",
          }}
        >
          <Icon name="copy" size={14} aria-hidden />
          <span aria-live="polite">
            {copyStatus === "success" ? "已复制" : copyStatus === "error" ? "复制失败" : "复制"}
          </span>
        </button>
      </figcaption>
      <pre
        style={{
          margin: 0,
          padding: "var(--space-3)",
          overflowX: "auto",
          backgroundColor: "var(--color-surface)",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-sm)",
          lineHeight: "var(--line-height-relaxed)",
          color: "var(--color-text-primary)",
        }}
      >
        <code>{code}</code>
      </pre>
    </figure>
  );
}

function Formula({ children }: { children: React.ReactNode }) {
  return (
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
        color: "var(--color-text-primary)",
      }}
    >
      {children}
    </p>
  );
}

function DataTable({ caption, head, rows }: { caption: string; head: string[]; rows: string[][] }) {
  return (
    <div style={{ overflowX: "auto", margin: "var(--space-3) 0" }}>
      <table
        style={{
          borderCollapse: "collapse",
          minWidth: "32rem",
          fontSize: "var(--text-sm)",
        }}
      >
        <caption
          style={{
            textAlign: "left",
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
            marginBottom: "var(--space-1)",
          }}
        >
          {caption}
        </caption>
        <thead>
          <tr>
            {head.map((cell) => (
              <th
                key={cell}
                scope="col"
                style={{
                  textAlign: "left",
                  padding: "var(--space-2) var(--space-3)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-secondary)",
                }}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.join("|")}>
              {row.map((cell, index) => (
                <td
                  key={index}
                  style={{
                    padding: "var(--space-2) var(--space-3)",
                    border: "1px solid var(--color-border)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- 演示消息（含长中文、代码、公式、表格、长文件名、错误） ---------- */

const LONG_PARAGRAPH =
  "拉格朗日力学的核心思想是：系统真实走过的路径，会让作用量取驻值。这句话听起来抽象，但它的好处非常实际——你不再需要逐个分析约束力，" +
  "只需要选好广义坐标，写出动能减势能的拉格朗日量，剩下的交给欧拉-拉格朗日方程。对于被限制在曲面、摆线或连杆上的系统，" +
  "牛顿力学里繁琐的约束力分析在这里被坐标选择一次性吸收掉了，这也是为什么分析力学在处理复杂约束系统时远比矢量力学顺手。";

const DEMO_MESSAGES: ChatMessage[] = [
  {
    id: "m1",
    role: "user",
    plainText: "拉格朗日方程的物理意义是什么？能结合一个带代码和公式的例子讲吗？",
    content: <p>拉格朗日方程的物理意义是什么？能结合一个带代码和公式的例子讲吗？</p>,
  },
  {
    id: "m2",
    role: "assistant",
    plainText: `${LONG_PARAGRAPH} 欧拉-拉格朗日方程：∂L/∂q − d/dt(∂L/∂q̇) = 0。`,
    thinking: {
      seconds: 3,
      steps: [
        "识别问题类型：分析力学概念解释，面向有牛顿力学基础的学习者",
        "从知识库检索《经典力学笔记》中关于最小作用量原理的段落",
        "组织「直觉 → 公式 → 可运行代码 → 常见误区」的解释结构",
      ],
      evidence: ["《经典力学笔记》第三章：最小作用量原理"],
      tools: [],
      quality: ["回答已完整生成并保存"],
    },
    content: (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", minWidth: 0 }}>
        <p style={{ overflowWrap: "break-word" }}>{LONG_PARAGRAPH}</p>
        <Formula>∂L/∂q − d/dt (∂L/∂q̇) = 0</Formula>
        <p style={{ overflowWrap: "break-word" }}>
          下面这段 Python 用数值方法验证单摆的拉格朗日方程给出的角加速度，可以直接运行：
        </p>
        <CodeBlock
          language="python"
          code={
            "import numpy as np\n\n" +
            "def angular_acceleration(theta: float, g: float = 9.81, length: float = 1.0) -> float:\n" +
            '    """由拉格朗日方程 d/dt(∂L/∂θ̇) - ∂L/∂θ = 0 导出：θ̈ = -(g / L) * sin(θ)。"""\n' +
            "    return -(g / length) * np.sin(theta)  # 小角度近似下退化为 -(g / L) * θ\n\n" +
            "for degrees in [5, 30, 60, 90]:\n" +
            "    print(degrees, angular_acceleration(np.radians(degrees)))"
          }
        />
        <DataTable
          caption="表 1：不同摆角下角加速度的精确值与小角度近似对比"
          head={["摆角", "精确值 (rad/s²)", "小角度近似", "相对误差"]}
          rows={[
            ["5°", "-0.855", "-0.856", "0.1%"],
            ["30°", "-4.905", "-5.138", "4.8%"],
            ["90°", "-9.810", "-15.416", "57.1%"],
          ]}
        />
        <p style={{ overflowWrap: "break-word" }}>
          常见误区：小角度近似在摆角超过 30° 后误差迅速放大，写实验报告时必须说明适用范围。
          相关推导已保存到你上传的资料
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              verticalAlign: "middle",
              maxWidth: "100%",
              padding: "0 var(--space-2)",
              margin: "0 var(--space-1)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-secondary)",
              fontSize: "var(--text-sm)",
            }}
          >
            <Icon name="uploadFile" size={14} aria-hidden />
            <span
              style={{
                maxWidth: "18rem",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              2026-春季学期-经典力学-分析力学专题-拉格朗日方程推导笔记-手写扫描版.pdf
            </span>
          </span>
          中。
        </p>
      </div>
    ),
  },
  {
    id: "m3",
    role: "user",
    plainText: "如果摆线有摩擦呢？",
    content: <p>如果摆线有摩擦呢？</p>,
  },
  {
    id: "m4",
    role: "assistant",
    plainText: "",
    status: "error",
    errorText:
      "回答生成失败：与推理服务的连接在等待响应时超时（已等待 30 秒）。这是一个模拟的超长错误信息，用于验证错误提示在窄列中能够完整折行而不遮挡下方的重试等操作按钮。请检查网络后点击「重试」。",
    content: null,
  },
];

/* ---------- 空白新对话：轮播名言 + 居中输入区 + 功能推荐 ---------- */

/* 名言数据与轮换组件共享自 Issue 13 的真实空白态实现
   （components/bridges/RotatingQuote，含减少动态效果支持）。 */

/** 对话框下方的功能推荐（内容对应 1.txt：论文搜索 / 生涯规划助手；文章人味化入口已退役） */
const SUGGESTIONS: { icon: IconName; label: string; prompt: string }[] = [
  { icon: "paperSearch", label: "论文搜索", prompt: "帮我在 arXiv 上找近一年量子纠错的综述论文" },
  { icon: "career", label: "生涯规划助手", prompt: "帮我排一下研究生三年的学习优先级" },
];

const MODE_LABEL: Record<ChatMode, string> = {
  companion: "日常陪伴",
  study: "学习模式",
};

/* ---------- 页面 ---------- */

/**
 * 聊天内容页桌面模板。
 *
 * 复用可折叠侧栏、消息流、消息操作行、思考摘要与输入区；
 * 顶部提供「日常陪伴 / 学习模式」切换（按对话持久化，见 ADR-0022）。
 * 新聊天为空白态：轮播学习名言 + 居中输入区 + 输入区下方功能推荐；
 * 打开对话后输入区沉底。页面状态由系统行为自动转换
 * （打开对话→加载→正常、发送失败→重试），不提供可见的状态切换按钮；
 * 开发验收可用 `?state=` 参数直接落在指定状态。
 */
export function ChatTemplate() {
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("conversation") ?? undefined;
  const stateForced = searchParams.get("state") !== null;
  const active = DEMO_RECENTS.find((item) => item.id === conversationId);

  // 新聊天（无 conversation 参数）落在空白态；打开对话先加载再进入正常内容。
  const [state, setState] = useTemplateState(conversationId ? "loading" : "empty");
  const [messages, setMessages] = useState<ChatMessage[]>(conversationId ? DEMO_MESSAGES : []);
  const [generating, setGenerating] = useState(false);
  const timerRef = useRef<number | null>(null);

  // 对话模式：每个对话持久化一个当前模式，新对话默认日常陪伴（ADR-0022）。
  const modeStorageKey = `bridges-template-chat-mode:${conversationId ?? "new"}`;
  const [mode, setMode] = useState<ChatMode>(
    active?.mode === "学习" ? "study" : "companion",
  );
  useEffect(() => {
    const stored = window.localStorage.getItem(modeStorageKey);
    if (stored === "companion" || stored === "study") {
      setMode(stored);
    } else {
      setMode(active?.mode === "学习" ? "study" : "companion");
    }
  }, [modeStorageKey, active]);

  // 侧栏在同页内切换会话时同步内容：打开对话→加载→正常；回到新聊天→空白态。
  useEffect(() => {
    if (stateForced) return;
    if (conversationId) {
      setMessages(DEMO_MESSAGES);
      setState("loading");
    } else {
      setMessages([]);
      setState("empty");
    }
  }, [conversationId, stateForced, setState]);
  const changeMode = (next: ChatMode) => {
    setMode(next);
    window.localStorage.setItem(modeStorageKey, next);
  };

  // 打开对话时的真实加载过程：加载完成自动进入正常内容（?state= 强制状态时除外）。
  useEffect(() => {
    if (stateForced || state !== "loading") return;
    const timer = window.setTimeout(() => setState("normal"), 600);
    return () => window.clearTimeout(timer);
  }, [state, stateForced, setState]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  // Issue 05：签名对齐 Composer（照片草稿仅在真实后端会话中承载；
  // 本地开发模板不消费附件，attachmentIds 恒为空数组）。
  const handleSend = (text: string, attachmentIds: string[] = []) => {
    void attachmentIds;
    const userMessage: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      plainText: text,
      content: <p style={{ overflowWrap: "break-word" }}>{text}</p>,
    };
    const replyId = `a-${Date.now()}`;
    const reply: ChatMessage = {
      id: replyId,
      role: "assistant",
      plainText: "",
      status: "streaming",
      content: null,
    };
    setMessages((list) => [...list, userMessage, reply]);
    setState("normal");
    setGenerating(true);
    timerRef.current = window.setTimeout(() => {
      setMessages((list) =>
        list.map((item) =>
          item.id === replyId
            ? {
                ...item,
                status: "done" as const,
                plainText: `已收到你的问题「${text}」。这是${MODE_LABEL[mode]}下由本地开发模板生成的确定性回答，用于验证流式消息交互。`,
                content: (
                  <p style={{ overflowWrap: "break-word" }}>
                    已收到你的问题「{text}」。这是{MODE_LABEL[mode]}下由本地开发模板生成的确定性回答，用于验证流式消息交互。
                  </p>
                ),
              }
            : item,
        ),
      );
      setGenerating(false);
    }, 900);
  };

  const handleStop = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setMessages((list) =>
      list.map((item) =>
        item.status === "streaming"
          ? {
              ...item,
              status: "done" as const,
              plainText: "已停止生成。",
              content: <p style={{ color: "var(--color-text-tertiary)" }}>已停止生成。</p>,
            }
          : item,
      ),
    );
    setGenerating(false);
  };

  const composerAvailable = state === "normal" || state === "empty";

  const handleRetry = (id: string) => {
    setMessages((list) =>
      list.map((item) =>
        item.id === id
          ? {
              ...item,
              status: "done" as const,
              plainText: "已在本地重新生成模板回答。",
              content: <p>已在本地重新生成模板回答。</p>,
            }
          : item,
      ),
    );
  };

  const footerNote = (
    <p
      style={{
        textAlign: "center",
        fontSize: "var(--text-xs)",
        color: "var(--color-text-tertiary)",
      }}
    >
      BridGes 的回答会标注依据与来源；重要内容请核对引用。
    </p>
  );

  const renderBody = () => {
    if (state === "loading") {
      return <StateBlock kind="loading" title="正在加载对话…" description="正在从本地数据库恢复这条对话的消息记录。" />;
    }
    if (state === "error") {
      return (
        <StateBlock
          kind="error"
          title="对话加载失败"
          description="读取本地数据库时出现异常。你的数据没有丢失，请重试。"
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
          description="对话属于你的个人账户，登录后才能查看。未登录时不会加载任何对话内容。"
          actionLabel="前往登录"
          onAction={() => {
            window.location.href = "/templates/login";
          }}
        />
      );
    }
    if (state === "success") {
      return (
        <StateBlock
          kind="success"
          title="对话已保存"
          description="消息与附件名称已写入当前开发模板的内存状态。"
          actionLabel="返回对话"
          onAction={() => setState("normal")}
        />
      );
    }
    if (state === "recovery") {
      return (
        <StateBlock
          kind="recovery"
          title="对话已恢复"
          description="错误状态已清除，可以返回并继续输入。"
          actionLabel="继续对话"
          onAction={() => setState("normal")}
        />
      );
    }
    return (
      <div
        style={{
          flex: 1,
          width: "100%",
          maxWidth: "var(--chat-column-width)",
          margin: "0 auto",
          padding: "var(--space-6)",
        }}
      >
        <MessageList messages={messages} onRetry={handleRetry} />
      </div>
    );
  };

  return (
    <TemplateShell activeConversation={conversationId}>
      <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "var(--space-3)",
            padding: "var(--space-3) var(--space-6)",
            borderBottom: "1px solid var(--color-border)",
            flexShrink: 0,
          }}
        >
          <h1
            style={{
              fontFamily: "var(--font-sans)",
              fontSize: "var(--text-base)",
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {active ? active.title : "新聊天"}
          </h1>
          <ModeToggle value={mode} onChange={changeMode} />
        </div>

        {state === "empty" ? (
          <div
            data-testid="chat-scroll-region"
            style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column" }}
          >
            <div
              data-testid="state-empty"
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: "var(--space-5)",
                padding: "var(--space-6)",
              }}
            >
              <div
                style={{
                  width: "100%",
                  maxWidth: "var(--chat-column-width)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-4)",
                }}
              >
                <RotatingQuote />
                <Composer onSend={handleSend} generating={generating} onStop={handleStop} />
                <ul
                  role="list"
                  aria-label="功能推荐"
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    justifyContent: "center",
                    gap: "var(--space-2)",
                  }}
                >
                  {SUGGESTIONS.map((item) => (
                    <li key={item.label}>
                      <button
                        type="button"
                        onClick={() => handleSend(item.prompt)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "var(--space-2)",
                          minHeight: "var(--target-size)",
                          padding: "var(--space-2) var(--space-4)",
                          border: "1px solid var(--color-border)",
                          borderRadius: "var(--radius-full)",
                          backgroundColor: "var(--color-surface)",
                          color: "var(--color-text-secondary)",
                          fontSize: "var(--text-sm)",
                          cursor: "pointer",
                        }}
                      >
                        <Icon name={item.icon} size={18} aria-hidden />
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
                {footerNote}
              </div>
            </div>
          </div>
        ) : (
          <>
            <div
              data-testid="chat-scroll-region"
              style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column" }}
            >
              {renderBody()}
            </div>

            <div
              style={{
                width: "100%",
                maxWidth: "var(--chat-column-width)",
                margin: "0 auto",
                padding: "var(--space-3) var(--space-6) var(--space-4)",
                flexShrink: 0,
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-2)",
              }}
            >
              {composerAvailable ? (
                <Composer onSend={handleSend} generating={generating} onStop={handleStop} />
              ) : (
                <p
                  role="status"
                  data-testid="composer-unavailable"
                  style={{
                    padding: "var(--space-3)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-lg)",
                    backgroundColor: "var(--color-bg-secondary)",
                    color: "var(--color-text-secondary)",
                    textAlign: "center",
                    fontSize: "var(--text-sm)",
                  }}
                >
                  {state === "loading"
                    ? "对话恢复完成后即可继续输入。"
                    : state === "error"
                      ? "请先重试恢复对话，再继续输入。"
                      : state === "permission"
                        ? "登录后才能发送消息或选择附件。"
                        : "返回正常对话后即可继续输入。"}
                </p>
              )}
              {footerNote}
            </div>
          </>
        )}
      </div>
    </TemplateShell>
  );
}
