"use client";

import { useEffect, useRef, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { uploadChatAttachment } from "@/lib/api";
import type { HumanizerSkillInput, HumanizerTaskContract } from "@/lib/api";

/** 四类体裁（与 SKILL 体裁合同一一对应，不共用泛化模板）。 */
export const HUMANIZER_GENRES: readonly { value: string; label: string; hint: string }[] = [
  { value: "popular_science", label: "科普文案", hint: "面向非专业读者，含类比与边界" },
  { value: "lecture_script", label: "课程讲稿", hint: "含学习目标、理解检查与练习停顿" },
  { value: "research_report", label: "科研汇报", hint: "观察与解释分离，含局限与下一步" },
  { value: "paper_assist", label: "论文写作", hint: "结构/语言/引用核查与披露提醒" },
] as const;

interface HumanizerDialogProps {
  open: boolean;
  onClose: () => void;
  /** 已存在的真实对话（对话页提供）；新聊天页不提供，用 ensureConversation 预建。 */
  conversationId?: string;
  /** 新聊天页的延迟建会话钩子（选择文件时预建空对话）。 */
  ensureConversation?: () => Promise<string | undefined>;
  /** 提交：宿主执行真实发送（真实消息流，不伪造结果）；返回是否成功。 */
  onSubmit: (
    content: string,
    skillInput: HumanizerSkillInput,
    attachmentIds: string[]
  ) => Promise<boolean>;
}

interface PickedFile {
  id: string;
  file: File;
  filename: string;
  uploadId: string;
  objectId?: string;
  status: "uploading" | "uploaded" | "error";
  progress: number;
  error?: string;
}

const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

function newId(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "上传失败，请重试。";
}

/**
 * 文章人味化任务对话框（Issue 28）。
 *
 * 两条路径（改写/生成）在同一对话框内选择：改写接受粘贴文本或当前账户
 * 文件，生成收集主题、受众、体裁、渠道与硬约束；提交走真实消息流程
 * （任务契约随用户消息落库，重试沿用），不在此处伪造任何工具结果。
 */
export function HumanizerDialog({
  open,
  onClose,
  conversationId,
  ensureConversation,
  onSubmit,
}: HumanizerDialogProps) {
  const [path, setPath] = useState<HumanizerSkillInput["contract"]["path"]>("rewrite");
  const [sourceText, setSourceText] = useState("");
  const [topic, setTopic] = useState("");
  const [genre, setGenre] = useState<HumanizerSkillInput["contract"]["genre"]>("popular_science");
  const [audience, setAudience] = useState("");
  const [channel, setChannel] = useState("");
  const [lengthTarget, setLengthTarget] = useState("");
  const [constraints, setConstraints] = useState("");
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const preparedConversationRef = useRef<string | undefined>(conversationId);

  useEffect(() => {
    preparedConversationRef.current = conversationId;
  }, [conversationId]);

  // 关闭时清空表单（下次打开保持干净起点，任务输入不跨任务残留）
  useEffect(() => {
    if (!open) return;
    setFormError("");
    setSubmitting(false);
  }, [open]);

  const resolveConversation = async (): Promise<string> => {
    if (preparedConversationRef.current) return preparedConversationRef.current;
    if (!ensureConversation) throw new Error("当前没有可用的对话，请稍后重试。");
    const created = await ensureConversation();
    if (!created) throw new Error("无法创建对话，请稍后重试。");
    preparedConversationRef.current = created;
    return created;
  };

  const startUpload = async (item: PickedFile): Promise<void> => {
    setFiles((current) =>
      current.map((candidate) =>
        candidate.id === item.id ? { ...candidate, status: "uploading", progress: 0, error: undefined } : candidate
      )
    );
    try {
      const target = await resolveConversation();
      const projection = await uploadChatAttachment(
        target,
        item.file,
        item.uploadId,
        (loaded, total) =>
          setFiles((current) =>
            current.map((candidate) =>
              candidate.id === item.id
                ? {
                    ...candidate,
                    progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0,
                  }
                : candidate
            )
          )
      );
      setFiles((current) =>
        current.map((candidate) =>
          candidate.id === item.id ? { ...candidate, status: "uploaded", objectId: projection.object_id, progress: 100 } : candidate
        )
      );
    } catch (error) {
      setFiles((current) =>
        current.map((candidate) =>
          candidate.id === item.id ? { ...candidate, status: "error", error: errorMessage(error) } : candidate
        )
      );
    }
  };

  const pickFiles = async (list: FileList | null) => {
    if (!list) return;
    setFormError("");
    const picked = Array.from(list)
      .filter((file) => file.size > 0)
      .map((file) => ({
        id: newId("humanizer-file"),
        file,
        filename: file.name,
        uploadId: newId("upload"),
        status: "uploading" as const,
        progress: 0,
        error: file.size > MAX_ATTACHMENT_BYTES ? "文件超过 10 MB 大小限制，请压缩后重试。" : undefined,
      }));
    if (picked.length === 0) {
      setFormError("文件为空，无法上传。");
      return;
    }
    setFiles((current) => [...current, ...picked]);
    await Promise.all(picked.filter((item) => !item.error).map(startUpload));
  };

  const removeFile = (id: string) => {
    setFiles((current) => current.filter((item) => item.id !== id));
  };

  const buildContract = (): HumanizerSkillInput | null => {
    const hardConstraints = constraints
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const attachmentIds = files
      .filter((item) => item.status === "uploaded" && item.objectId)
      .map((item) => item.objectId!);
    const hasSource = sourceText.trim().length > 0;
    const hasFile = attachmentIds.length > 0;

    if (path === "rewrite" && !hasSource && !hasFile) {
      setFormError("请粘贴要改写的原文，或选择当前账户的文件。");
      return null;
    }
    if (path === "generate" && !topic.trim()) {
      setFormError("请填写要生成的文章主题。");
      return null;
    }
    if (files.some((item) => item.status === "uploading")) {
      setFormError("文件仍在上传，请等待完成或先移除。");
      return null;
    }
    if (files.some((item) => item.status === "error")) {
      setFormError("存在上传失败的文件，请移除后重试。");
      return null;
    }
    return {
      skill_id: "bridges-humanizer",
      contract: {
        path,
        genre,
        ...(path === "generate" ? { topic: topic.trim() } : {}),
        ...(hasSource ? { source_text: sourceText.trim() } : {}),
        ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
        ...(audience.trim() ? { audience: audience.trim() } : {}),
        ...(channel.trim() ? { channel: channel.trim() } : {}),
        ...(lengthTarget.trim() ? { length_target: lengthTarget.trim() } : {}),
        ...(hardConstraints.length > 0 ? { hard_constraints: hardConstraints } : {}),
      },
    };
  };

  const buildContent = (input: HumanizerSkillInput): string => {
    const genreLabel =
      HUMANIZER_GENRES.find((item) => item.value === input.contract.genre)?.label ??
      input.contract.genre;
    const subject =
      input.contract.path === "generate"
        ? input.contract.topic ?? ""
        : (input.contract.source_text?.slice(0, 30) ?? files[0]?.filename ?? "原文");
    return input.contract.path === "generate"
      ? `文章人味化（${genreLabel}）：生成《${subject}》`
      : `文章人味化（${genreLabel}）：改写《${subject}》`;
  };

  const submit = async () => {
    if (submitting) return;
    const input = buildContract();
    if (!input) return;
    setSubmitting(true);
    setFormError("");
    try {
      const accepted = await onSubmit(buildContent(input), input, input.contract.attachment_ids ?? []);
      if (accepted === false) {
        setSubmitting(false);
        return;
      }
      // 成功后由宿主关闭对话框并进入消息流
      onClose();
    } catch (error) {
      setFormError(errorMessage(error));
      setSubmitting(false);
    }
  };

  const genreLabel = HUMANIZER_GENRES.find((item) => item.value === genre)?.label ?? "";
  const ready =
    path === "rewrite"
      ? sourceText.trim().length > 0 || files.some((item) => item.status === "uploaded")
      : topic.trim().length > 0;
  const canSubmit = ready && !submitting && !files.some((item) => item.status === "uploading");

  const fieldStyle: React.CSSProperties = {
    width: "100%",
    padding: "var(--space-2) var(--space-3)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    background: "var(--color-surface)",
    color: "var(--color-text)",
    fontSize: "var(--text-sm)",
    fontFamily: "inherit",
    boxSizing: "border-box",
  };
  const labelStyle: React.CSSProperties = {
    display: "block",
    marginBottom: "var(--space-1)",
    fontSize: "var(--text-sm)",
    color: "var(--color-text-secondary)",
  };
  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "var(--space-3)",
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="文章人味化"
      description="在保持科学事实、限定条件与引用关系的前提下改进表达；改写与生成两条路径，四类体裁各自使用独立表达规则。"
    >
      <div style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-4)" }} aria-label="任务路径">
        {(
          [
            { value: "rewrite", label: "改写文章" },
            { value: "generate", label: "生成文章" },
          ] as const
        ).map((tab) => (
          <Button
            key={tab.value}
            variant={path === tab.value ? "primary" : "secondary"}
            size="sm"
            onClick={() => {
              setPath(tab.value);
              setFormError("");
            }}
            aria-pressed={path === tab.value}
            data-testid={`humanizer-tab-${tab.value}`}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        {path === "rewrite" ? (
          <>
            <div>
              <label htmlFor="humanizer-source" style={labelStyle}>
                原文（粘贴文本）
              </label>
              <textarea
                id="humanizer-source"
                data-testid="humanizer-source-text"
                value={sourceText}
                onChange={(event) => setSourceText(event.target.value)}
                placeholder="粘贴要改写的科学内容…"
                rows={6}
                style={{ ...fieldStyle, resize: "vertical" }}
              />
            </div>
            <div>
              <label style={labelStyle}>或选择当前账户文件（PDF / Word / 文本）</label>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,.txt,.md,image/*"
                multiple
                hidden
                data-testid="humanizer-file-input"
                onChange={(event) => {
                  void pickFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()}>
                <Icon name="uploadFile" size={16} aria-hidden />
                选择文件
              </Button>
              {files.length > 0 && (
                <ul
                  role="list"
                  data-testid="humanizer-file-list"
                  style={{ margin: "var(--space-2) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-1)" }}
                >
                  {files.map((item) => (
                    <li key={item.id} style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}>
                      <Icon name="documentPage" size={16} aria-hidden />
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.filename}</span>
                      <span style={{ color: "var(--color-text-secondary)" }}>
                        {item.status === "uploading"
                          ? `上传中 ${item.progress}%`
                          : item.status === "error"
                            ? (item.error ?? "上传失败")
                            : "已上传"}
                      </span>
                      <Button variant="secondary" size="sm" onClick={() => removeFile(item.id)}>
                        移除
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        ) : (
          <div>
            <label htmlFor="humanizer-topic" style={labelStyle}>
              主题<span style={{ color: "var(--color-status-error)" }}>*</span>
            </label>
            <input
              id="humanizer-topic"
              data-testid="humanizer-topic-input"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="例如：为什么人的睡眠时长随着年龄变化"
              style={fieldStyle}
            />
          </div>
        )}

        <div>
          <label htmlFor="humanizer-genre" style={labelStyle}>
            体裁<span style={{ color: "var(--color-status-error)" }}>*</span>
          </label>
          <select
            id="humanizer-genre"
            data-testid="humanizer-genre-select"
            value={genre}
            onChange={(event) =>
              setGenre(event.target.value as HumanizerSkillInput["contract"]["genre"])
            }
            style={fieldStyle}
          >
            {HUMANIZER_GENRES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}（{item.hint}）
              </option>
            ))}
          </select>
        </div>

        <div style={gridStyle}>
          <div>
            <label htmlFor="humanizer-audience" style={labelStyle}>
              受众（可选）
            </label>
            <input
              id="humanizer-audience"
              data-testid="humanizer-audience-input"
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              placeholder="例如：大一新生、课题组同行"
              style={fieldStyle}
            />
          </div>
          <div>
            <label htmlFor="humanizer-channel" style={labelStyle}>
              渠道（可选）
            </label>
            <input
              id="humanizer-channel"
              data-testid="humanizer-channel-input"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              placeholder="例如：公众号、课堂、组会汇报"
              style={fieldStyle}
            />
          </div>
        </div>

        <div style={gridStyle}>
          <div>
            <label htmlFor="humanizer-length" style={labelStyle}>
              长度目标（可选）
            </label>
            <input
              id="humanizer-length"
              data-testid="humanizer-length-input"
              value={lengthTarget}
              onChange={(event) => setLengthTarget(event.target.value)}
              placeholder="例如：800 字、10 分钟"
              style={fieldStyle}
            />
          </div>
          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              当前体裁：{genreLabel}
            </span>
          </div>
        </div>

        <div>
          <label htmlFor="humanizer-constraints" style={labelStyle}>
            硬约束（可选，每行一条）
          </label>
          <textarea
            id="humanizer-constraints"
            data-testid="humanizer-constraints-input"
            value={constraints}
            onChange={(event) => setConstraints(event.target.value)}
            placeholder={"例如：\n必须保留「个体差异很大，时长只是参考」这一限定条件。\n不得声称睡眠时长决定健康水平。\n全文不超过 1200 字。"}
            rows={3}
            style={{ ...fieldStyle, resize: "vertical" }}
          />
        </div>

        {formError && (
          <p role="alert" data-testid="humanizer-form-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {formError}
          </p>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
          <Button variant="secondary" onClick={onClose} data-testid="humanizer-cancel">
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={!canSubmit}
            data-testid="humanizer-submit"
          >
            <Icon name="humanize" size={16} aria-hidden />
            {submitting ? "正在发送…" : path === "rewrite" ? "开始改写" : "开始生成"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
