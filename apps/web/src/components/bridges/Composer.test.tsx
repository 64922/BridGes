import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";
import type { ChatModuleSelectionId } from "@/lib/chat-modules";
import {
  listChatAttachmentDrafts,
  removeChatAttachmentDraft,
  uploadChatAttachmentDraft,
} from "@/lib/api";

const PLACEHOLDER = "输入消息，开始日常对话";

vi.mock("@/lib/api", () => ({
  transcribeDictation: vi.fn(),
  uploadChatAttachmentDraft: vi.fn(),
  listChatAttachmentDrafts: vi.fn().mockResolvedValue([]),
  removeChatAttachmentDraft: vi.fn().mockResolvedValue(undefined),
  chatAttachmentDraftContentUrl: (objectId: string) => `/api/chat/attachment-drafts/${objectId}/content`,
}));

function draftProjection(
  objectId: string,
  filename: string,
  mediaType = "image/png",
  ingestionStatus = "none",
  ingestionError: string | null = null
) {
  return {
    object_id: objectId,
    original_filename: filename,
    media_type: mediaType,
    content_length: 1024,
    content_hash: "hash",
    ingestion_status: ingestionStatus,
    ingestion_error: ingestionError,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const PDF_MEDIA_TYPE = "application/pdf";

function pngFile(name = "截图.png", size = 100): File {
  const file = new File([new Uint8Array(size)], name, { type: "image/png" });
  return file;
}

function pdfFile(name = "讲义.pdf", size = 100): File {
  return new File([new Uint8Array(size)], name, { type: "application/pdf" });
}

function renderComposer(onSend = vi.fn().mockResolvedValue(true)) {
  render(<Composer onSend={onSend} />);
  return onSend;
}

function pickFiles(files: File[]) {
  const composer = screen.getByTestId("composer");
  const fileInput = composer.querySelector('input[type="file"]') as HTMLInputElement;
  if (!fileInput) throw new Error("缺少隐藏的文件选择 input");
  Object.defineProperty(fileInput, "files", { value: files, configurable: true });
  fireEvent.change(fileInput);
}

async function waitForThumb(filename: string) {
  return await waitFor(() => screen.getAllByText(filename)[0]);
}

/** V2 Issue 11：模块选择是受控的，用宿主状态组件验证选择→chip→移除。 */
function ModuleHarness({
  onSend = vi.fn(),
}: {
  onSend?: (text: string, attachmentIds: string[]) => Promise<boolean> | boolean | void;
}) {
  const [moduleId, setModuleId] = useState<ChatModuleSelectionId | null>(null);
  return (
    <Composer onSend={onSend} moduleId={moduleId} onModuleChange={setModuleId} />
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(listChatAttachmentDrafts).mockResolvedValue([]);
  vi.mocked(removeChatAttachmentDraft).mockResolvedValue(undefined);
});

describe("Composer 输入框", () => {
  it.each(["conversation", "new-chat"] as const)(
    "%s 变体显示日常对话占位文案",
    (variant) => {
      render(<Composer variant={variant} onSend={vi.fn()} />);

      expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe(PLACEHOLDER);
    }
  );

  it("学习首轮必须先上传照片，文字会保留", async () => {
    vi.mocked(uploadChatAttachmentDraft).mockResolvedValue(
      draftProjection("study-photo", "书页.png"),
    );
    const onSend = vi.fn().mockResolvedValue(true);
    render(<Composer variant="new-chat" mode="study" onSend={onSend} />);

    const textbox = screen.getByRole("textbox") as HTMLTextAreaElement;
    const send = screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement;
    expect(textbox.placeholder).toBe("上传本节书页照片开始预习");
    fireEvent.change(textbox, { target: { value: "这是本节第一张" } });
    expect(send.disabled).toBe(true);

    pickFiles([pngFile("书页.png")]);
    await waitForThumb("书页.png");
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("这是本节第一张", ["study-photo"]));
  });

  it.each(["new-chat", "conversation"] as const)("学习 %s 不能把文件作为书页发送", async (variant) => {
    vi.mocked(uploadChatAttachmentDraft).mockResolvedValue(
      draftProjection("study-file", "讲义.pdf", PDF_MEDIA_TYPE),
    );
    const onSend = vi.fn();
    render(<Composer variant={variant} mode="study" onSend={onSend} />);
    pickFiles([pdfFile()]);
    await waitForThumb("讲义.pdf");
    expect(screen.getByRole("alert").textContent).toContain("请移除文件附件");
    expect((screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement).disabled).toBe(true);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("学习发送失败保留文字、照片与调整后的页序", async () => {
    vi.mocked(uploadChatAttachmentDraft).mockImplementation(async (file: Blob) =>
      draftProjection((file as File).name, (file as File).name),
    );
    const onSend = vi.fn().mockResolvedValue(false);
    render(<Composer variant="new-chat" mode="study" onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "本节书页" } });
    pickFiles([pngFile("a.png"), pngFile("b.png", 200)]);
    await waitForThumb("b.png");
    fireEvent.click(screen.getByRole("button", { name: "将 b.png 上移" }));
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("本节书页", ["b.png", "a.png"]));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("本节书页");
    expect(screen.getByText("a.png")).toBeTruthy();
    expect(screen.getByText("b.png")).toBeTruthy();
  });
});

describe("Composer 照片附件（Issue 05）", () => {
  beforeEach(() => {
    vi.mocked(uploadChatAttachmentDraft).mockImplementation(async (file: Blob) => {
      const name = (file as File).name ?? "图片.png";
      return draftProjection(`obj-${name}`, name);
    });
  });

  it("选择图片后上传为草稿，缩略图显示文件名、类型与页序", async () => {
    renderComposer();

    pickFiles([pngFile("合同扫描.png")]);
    await waitForThumb("合同扫描.png");

    expect(screen.getByText("PNG")).toBeTruthy();
    expect(screen.getByText("第 1 张")).toBeTruthy();
    expect(uploadChatAttachmentDraft).toHaveBeenCalledTimes(1);
  });

  it("纯附件（无文字）即可发送，onSend 收到按序 attachmentIds", async () => {
    const onSend = renderComposer();

    pickFiles([pngFile("a.png"), pngFile("b.png", 200)]);
    await waitForThumb("b.png");

    const send = screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    fireEvent.click(send);

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith("", ["obj-a.png", "obj-b.png"]);
  });

  it("粘贴图片文件会创建草稿", async () => {
    renderComposer();

    const textarea = screen.getByRole("textbox");
    fireEvent.paste(textarea, { clipboardData: { files: [pngFile("粘贴.png")] } });

    await waitForThumb("粘贴.png");
  });

  it("拖入图片文件会创建草稿", async () => {
    renderComposer();

    const composer = screen.getByTestId("composer");
    fireEvent.drop(composer, { dataTransfer: { files: [pngFile("拖入.png")] } });

    await waitForThumb("拖入.png");
  });

  it("不支持的类型显示中文原因，且不清空其他草稿", async () => {
    renderComposer();

    pickFiles([pngFile("第一张.png")]);
    await waitForThumb("第一张.png");

    pickFiles([new File([new Uint8Array(10)], "成绩单.csv", { type: "text/csv" })]);

    await waitFor(() => screen.getByText(/暂不支持该文件类型/));
    // 中文原因说明当前支持的类型，用户知道该换什么。
    expect(screen.getByText(/PDF、DOCX、TXT、Markdown/)).toBeTruthy();
    expect(screen.getAllByText("第一张.png").length).toBeGreaterThan(0);
    // 被拒绝的文件不会创建草稿。
    expect(uploadChatAttachmentDraft).toHaveBeenCalledTimes(1);
  });

  it("超过 10MB 的图片显示中文原因", async () => {
    renderComposer();

    pickFiles([pngFile("太大.png", 11 * 1024 * 1024)]);

    await waitFor(() => screen.getByText(/超过 10 MB/));
    expect(uploadChatAttachmentDraft).not.toHaveBeenCalled();
  });

  it("上移/下移按钮调整页序且键盘可达", async () => {
    renderComposer();

    pickFiles([pngFile("one.png"), pngFile("two.png", 200)]);
    await waitForThumb("two.png");

    fireEvent.click(screen.getByRole("button", { name: "将 two.png 上移" }));

    const list = screen.getByTestId("composer-attachments");
    const text = list.textContent ?? "";
    expect(text.indexOf("two.png")).toBeLessThan(text.indexOf("one.png"));
    // 边界禁用：首张不可再上移，末张不可再下移。
    expect(
      (screen.getByRole("button", { name: "将 two.png 上移" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "将 one.png 下移" }) as HTMLButtonElement).disabled
    ).toBe(true);
    // 下移可把顺序还原。
    fireEvent.click(screen.getByRole("button", { name: "将 two.png 下移" }));
    const restored = (screen.getByTestId("composer-attachments").textContent ?? "");
    expect(restored.indexOf("one.png")).toBeLessThan(restored.indexOf("two.png"));
  });

  it("移除按钮删除草稿并调用后端删除", async () => {
    renderComposer();

    pickFiles([pngFile("要删的.png")]);
    await waitForThumb("要删的.png");

    fireEvent.click(screen.getByRole("button", { name: "移除附件 要删的.png" }));

    await waitFor(() => expect(screen.queryByText("要删的.png")).toBeNull());
    expect(removeChatAttachmentDraft).toHaveBeenCalledWith("obj-要删的.png");
  });

  it("挂载时恢复账户既有草稿", async () => {
    vi.mocked(listChatAttachmentDrafts).mockResolvedValue([
      draftProjection("obj-old", "旧照片.png"),
    ]);

    renderComposer();

    await waitForThumb("旧照片.png");
  });

  it("预览读取失败时在附件旁显示中文原因", async () => {
    vi.mocked(listChatAttachmentDrafts).mockResolvedValue([
      draftProjection("obj-broken", "坏图.png"),
    ]);

    renderComposer();
    await waitForThumb("坏图.png");

    // 同源 /content 请求失败（503）：img 触发 onError，附件旁出现中文原因。
    fireEvent.error(screen.getByAltText("照片预览：坏图.png"));

    await waitFor(() => screen.getByText(/照片内容当前无法读取/));
    expect(screen.getAllByText("坏图.png").length).toBeGreaterThan(0);
  });

  it("发送失败时保留文字与附件等待重试", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("发送失败"));

    render(<Composer onSend={onSend} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "看看这张" } });
    pickFiles([pngFile("保留.png")]);
    await waitForThumb("保留.png");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("看看这张");
    expect(screen.getAllByText("保留.png").length).toBeGreaterThan(0);
  });

  it("发送成功后清空文字与附件", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<Composer onSend={onSend} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "带图消息" } });
    pickFiles([pngFile("成功.png")]);
    await waitForThumb("成功.png");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    await waitFor(() => expect(screen.queryByText("成功.png")).toBeNull());
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });
});

describe("Composer 文件附件（V2 Issue 06）", () => {
  beforeEach(() => {
    vi.mocked(uploadChatAttachmentDraft).mockImplementation(
      async (file: Blob, filename: string) => {
        const name = (file as File).name ?? filename;
        return name.endsWith(".pdf")
          ? draftProjection(`obj-${name}`, name, PDF_MEDIA_TYPE, "queued")
          : draftProjection(`obj-${name}`, name);
      }
    );
  });

  it("选择 PDF 后上传为文档卡片草稿，显示类型、大小与解析状态", async () => {
    renderComposer();

    pickFiles([pdfFile("统计讲义.pdf")]);
    await waitForThumb("统计讲义.pdf");

    // 文件不是图片：不出缩略图，出文档卡片。
    expect(screen.getByTestId("composer-file-card").textContent).toContain("PDF");
    expect(screen.getByTestId("composer-file-card").textContent).toContain("1 KB");
    expect(screen.queryByAltText("照片预览：统计讲义.pdf")).toBeNull();
    // 解析在发送前就开始了：状态可见（排队解析中）。
    expect(screen.getByTestId("ingestion-status-queued").textContent).toContain("排队解析中");
    expect(uploadChatAttachmentDraft).toHaveBeenCalledTimes(1);
  });

  it("解析失败时草稿旁给出中文原因，且不清空其他草稿", async () => {
    vi.mocked(uploadChatAttachmentDraft).mockImplementation(
      async (file: Blob, filename: string) => {
        const name = (file as File).name ?? filename;
        return name === "坏文件.pdf"
          ? draftProjection(
              `obj-${name}`,
              name,
              PDF_MEDIA_TYPE,
              "error",
              "PDF 解析失败：文件已损坏，无法读取正文。"
            )
          : draftProjection(`obj-${name}`, name);
      }
    );

    renderComposer();

    pickFiles([pngFile("好照片.png"), pdfFile("坏文件.pdf")]);
    await waitForThumb("坏文件.pdf");

    expect(screen.getByTestId("ingestion-status-error").textContent).toContain("解析失败");
    expect(screen.getByText(/PDF 解析失败：文件已损坏/)).toBeTruthy();
    // 失败只影响该文件：照片草稿与正文都保留，仍可发送。
    expect(screen.getAllByText("好照片.png").length).toBeGreaterThan(0);
    expect(
      (screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });

  it("解析未完成时轮询刷新草稿状态（排队 → 已解析）", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(listChatAttachmentDrafts).mockResolvedValue([
        draftProjection("obj-poll", "轮询.pdf", PDF_MEDIA_TYPE, "processing"),
      ]);

      render(<Composer onSend={vi.fn()} />);
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByTestId("ingestion-status-processing").textContent).toContain("解析中");

      vi.mocked(listChatAttachmentDrafts).mockResolvedValue([
        draftProjection("obj-poll", "轮询.pdf", PDF_MEDIA_TYPE, "ready"),
      ]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });

      expect(screen.getByTestId("ingestion-status-ready").textContent).toContain(
        "已解析，可引用"
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("照片与文件混合按选择顺序发送", async () => {
    const onSend = renderComposer();

    pickFiles([pngFile("照片.png")]);
    await waitForThumb("照片.png");
    pickFiles([pdfFile("讲义.pdf")]);
    await waitForThumb("讲义.pdf");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith("", ["obj-照片.png", "obj-讲义.pdf"]);
  });

  it("超过 10MB 的文件显示中文原因且不上传", async () => {
    renderComposer();

    pickFiles([pdfFile("超大.pdf", 11 * 1024 * 1024)]);

    await waitFor(() => screen.getByText(/超过 10 MB/));
    expect(uploadChatAttachmentDraft).not.toHaveBeenCalled();
  });
});

describe("Composer 显式模块菜单（V2 Issue 11）", () => {
  it("`+` 菜单有中文可访问名称、aria-expanded 状态与中文菜单项", () => {
    render(<Composer onSend={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "添加功能或文件" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("menu", { name: "添加功能或文件" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /添加照片和文件/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /论文搜索/ })).toBeTruthy();
  });

  it("选择论文搜索后显示可移除 chip，移除后回到普通聊天且文字不丢失", () => {
    render(<ModuleHarness />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "量子纠错综述" } });
    fireEvent.click(screen.getByRole("button", { name: "添加功能或文件" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /论文搜索/ }));

    const chip = screen.getByTestId("composer-module-chip");
    expect(chip.textContent).toContain("论文搜索");
    // 已输入文字不因选择/移除模块而丢失
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("量子纠错综述");

    fireEvent.click(screen.getByRole("button", { name: "移除论文搜索模块" }));

    expect(screen.queryByTestId("composer-module-chip")).toBeNull();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("量子纠错综述");
  });

  it("键盘可完成打开、移动与激活，Esc 关闭后焦点回到 `+`", async () => {
    render(<ModuleHarness />);
    const trigger = screen.getByRole("button", { name: "添加功能或文件" });
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);

    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);

    // 聚焦的菜单项是原生 button：浏览器把 Enter/Space 转成 click，
    // 这里直接触发同一激活路径。
    fireEvent.click(items[1]);
    expect(screen.getByTestId("composer-module-chip").textContent).toContain("论文搜索");

    // 重新打开后 Esc：菜单关闭且焦点归还触发按钮（归还发生在下一帧）。
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.keyDown(screen.getAllByRole("menuitem")[0], { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("Tab 与点击菜单外都关闭菜单", () => {
    render(<ModuleHarness />);
    const trigger = screen.getByRole("button", { name: "添加功能或文件" });

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.keyDown(screen.getAllByRole("menuitem")[0], { key: "Tab" });
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(trigger);
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  // V2 Issue 14：贴吧信息搜集与其他模块共用同一套菜单/chip 机制。
  it("`+` 菜单可选择贴吧信息搜集，chip 可移除且文字不丢失", () => {
    render(<ModuleHarness />);

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "华东交通大学吧里最近的宿舍条件怎么样" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加功能或文件" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /贴吧信息搜集/ }));

    expect(screen.getByTestId("composer-module-chip").textContent).toContain("贴吧信息搜集");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "华东交通大学吧里最近的宿舍条件怎么样"
    );

    fireEvent.click(screen.getByRole("button", { name: "移除贴吧信息搜集模块" }));

    expect(screen.queryByTestId("composer-module-chip")).toBeNull();
  });

  // V2 Issue 15：职业规划与其他模块共用同一套菜单/chip 机制。
  it("`+` 菜单可选择职业规划，chip 可移除且文字不丢失", () => {
    render(<ModuleHarness />);

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "我想找 Java 后端开发的工作，城市南昌" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加功能或文件" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /职业规划/ }));

    expect(screen.getByTestId("composer-module-chip").textContent).toContain("职业规划");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "我想找 Java 后端开发的工作，城市南昌"
    );

    fireEvent.click(screen.getByRole("button", { name: "移除职业规划模块" }));
  });

  // V2 Issue 16：GitHub 项目推荐与其他模块共用同一套菜单/chip 机制。
  it("`+` 菜单可选择 GitHub 项目推荐，chip 可移除且文字不丢失", () => {
    render(<ModuleHarness />);

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "我想做一个校园二手书交换平台" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加功能或文件" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /GitHub 项目推荐/ }));

    expect(screen.getByTestId("composer-module-chip").textContent).toContain("GitHub 项目推荐");
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "我想做一个校园二手书交换平台"
    );

    fireEvent.click(screen.getByRole("button", { name: "移除GitHub 项目推荐模块" }));

    expect(screen.queryByTestId("composer-module-chip")).toBeNull();
  });
});
