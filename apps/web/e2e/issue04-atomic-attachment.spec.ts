import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 04 反馈环（必红两条）：附件与消息原子绑定 + 改写默认不检索知识库。
 *
 * 真实链路：真实 HTTP + 真实 SQLite + 真实后台执行器（StubQwenAdapter 只
 * 提供确定性模型回答），上传走真实 XHR 原始字节链路（隐藏 input 用
 * DataTransfer 派发真实 change 事件——Playwright setInputFiles 对
 * display:none + React onChange 的 input 不触发合成事件，实测不产生附件
 * 状态；见 issue01 反馈环经验）。
 *
 * 覆盖验收标准：
 * - 首页人味化改写上传真实小型 DOCX，发送后从 API 读取消息投影断言附件
 *   ID 相同、状态为 bound，检索披露包含该附件且默认关闭知识库；
 * - 全局知识库预置「巴巴博一.jpg」时，默认改写链路既不检索它，也不把
 *   它展示为原文（检索轮次 use_knowledge_base=false、KB 层 disabled、
 *   引用无 knowledge_base 来源）。
 */

const DOCX_PATH = join(
  "e2e",
  "fixtures",
  "issue04",
  "Transformer：AI 大模型的核心基石.docx"
);
const DOCX_NAME = "Transformer：AI 大模型的核心基石.docx";
const KB_IMAGE_PATH = join("e2e", "fixtures", "issue04", "巴巴博一.jpg");
const KB_IMAGE_NAME = "巴巴博一.jpg";
const PASSWORD = "correct-horse-issue04";

/** 首页建议卡打开「文章人味化」对话框。 */
async function openHumanizerDialog(page: Page): Promise<void> {
  await page
    .getByTestId("suggestion-cards")
    .getByRole("button", { name: "文章人味化" })
    .click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
}

/**
 * 用真实文件字节经 DataTransfer 派发 change 事件（真实 XHR 上传链路）。
 * 返回等待中的上传响应，调用方 await 后取 object_id。
 */
function dispatchRealFile(page: Page, testId: string, name: string, path: string) {
  const base64 = readFileSync(join(process.cwd(), path)).toString("base64");
  const uploadResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/chat\/conversations\/[^/]+\/attachments$/.test(response.url())
  );
  void page.getByTestId(testId).evaluate(
    (el, { fileName, mimeType, payload }) => {
      const input = el as HTMLInputElement;
      const bytes = Uint8Array.from(atob(payload), (char) => char.charCodeAt(0));
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(new File([bytes], fileName, { type: mimeType }));
      Object.defineProperty(input, "files", {
        configurable: true,
        value: dataTransfer.files,
      });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { fileName: name, mimeType: "application/octet-stream", payload: base64 }
  );
  return uploadResponse;
}

/** 首页人味化改写：上传真实 DOCX → 等待摄取就绪 → 提交改写。 */
async function humanizerRewriteWithDocx(
  page: Page
): Promise<{ objectId: string; conversationId: string }> {
  await openHumanizerDialog(page);
  const uploadResponse = dispatchRealFile(
    page,
    "humanizer-file-input",
    DOCX_NAME,
    DOCX_PATH
  );
  const uploaded = await uploadResponse;
  const uploadBody = (await uploaded.json()) as {
    object_id: string;
    status: string;
  };
  const conversationId = /\/api\/chat\/conversations\/([^/]+)\/attachments$/
    .exec(uploaded.url())![1]!;
  await expect(page.getByTestId("humanizer-file-list")).toContainText("已上传");

  // 摄取就绪（附件检索层需要文档已入索引；E2E 环境嵌入降级为关键词索引，
  // 解析即完成，仍须等待后台执行器收敛）。
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/api/chat/conversations/${conversationId}/attachments/${uploadBody.object_id}/ingestion`
        );
        return ((await res.json()) as { status?: string }).status ?? "missing";
      },
      { timeout: 30_000, intervals: [500] }
    )
    .toBe("ready");

  await page.getByTestId("humanizer-submit").click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return { objectId: uploadBody.object_id, conversationId };
}

/** 等待助手消息收敛（streaming → done/error），返回权威对话投影。 */
async function convergedProjection(
  page: Page,
  conversationId: string
): Promise<Record<string, any>> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/api/chat/conversations/${conversationId}`
        );
        const body = (await res.json()) as {
          messages?: { role: string; status: string }[];
        };
        const assistant = (body.messages ?? [])
          .filter((message) => message.role === "assistant")
          .at(-1);
        return assistant?.status ?? "missing";
      },
      { timeout: 60_000, intervals: [500] }
    )
    .not.toBe("streaming");
  const res = await page.request.get(`/api/chat/conversations/${conversationId}`);
  return (await res.json()) as Record<string, any>;
}

interface AttachmentProjection {
  object_id: string;
  original_filename: string;
  status: string;
}

interface RetrievalProjection {
  use_knowledge_base: boolean;
  note: string | null;
  layers: { layer: string; status: string }[];
  citations: { source_layer: string }[];
}

/** 权威投影：断言人味化改写消息恰绑定上传的 DOCX，且默认关闭知识库。 */
function expectBoundRewrite(
  projection: Record<string, any>,
  objectId: string,
  knowledgeBaseOff: boolean
): void {
  const messages = projection.messages as Record<string, any>[];
  const user = messages.filter((message) => message.role === "user").at(-1)!;
  const assistant = messages
    .filter((message) => message.role === "assistant")
    .at(-1)!;

  // 消息投影恰有该附件，且已绑定（不是 uploaded 悬浮态）。
  const attachments = (user.attachments ?? []) as AttachmentProjection[];
  expect(attachments).toHaveLength(1);
  expect(attachments[0]!.object_id).toBe(objectId);
  expect(attachments[0]!.original_filename).toBe(DOCX_NAME);
  expect(attachments[0]!.status).toBe("bound");

  // 检索披露：轮次存在、附件层已启用、未出现「本轮未附加文件」、
  // 默认关闭知识库且引用不含知识库来源。
  const retrieval = assistant.retrieval as RetrievalProjection | null;
  expect(retrieval).not.toBeNull();
  expect(retrieval!.use_knowledge_base).toBe(!knowledgeBaseOff);
  const attachmentLayer = retrieval!.layers.find(
    (layer) => layer.layer === "attachment"
  );
  expect(attachmentLayer).toBeDefined();
  expect(attachmentLayer!.status).not.toBe("disabled");
  expect(retrieval!.note ?? "").not.toContain("本轮未附加文件");
  expect(
    retrieval!.citations.every(
      (citation) => citation.source_layer !== "knowledge_base"
    )
  ).toBe(true);
}

test("首页人味化改写上传真实 DOCX：消息投影恰绑定该附件，检索披露一致", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i04a");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const { objectId } = await humanizerRewriteWithDocx(page);

  // 权威投影：用户消息恰绑定上传对象；检索轮次默认关闭知识库。
  const conversationId = page.url().split("/chat/")[1]!.split(/[?#]/)[0]!;
  const projection = await convergedProjection(page, conversationId);
  expectBoundRewrite(projection, objectId, true);
});

test("知识库预置「巴巴博一.jpg」：默认改写链路不检索、不展示为原文", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i04b");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 预置全局知识库不相关图片（真实 JPEG，与聊天附件同一原始字节约定）。
  const imageBytes = readFileSync(join(process.cwd(), KB_IMAGE_PATH));
  const seeded = await page.request.post("/api/knowledge-base/materials", {
    headers: { "X-Bridges-Filename": encodeURIComponent(KB_IMAGE_NAME) },
    data: imageBytes,
  });
  expect(seeded.status()).toBe(201);
  // 等待摄取真正就绪（图片元数据解析在 E2E 环境必然成功；失败/空内容
  // 说明预置本身有问题，测试应大声失败而非带着坏前提继续）。
  await expect
    .poll(
      async () => {
        const res = await page.request.get("/api/knowledge-base/materials");
        const materials = (await res.json()) as {
          filename: string;
          status: string;
        }[];
        return materials.find((material) => material.filename === KB_IMAGE_NAME)
          ?.status;
      },
      { timeout: 30_000, intervals: [500] }
    )
    .toBe("ready");

  const { objectId } = await humanizerRewriteWithDocx(page);

  // 改写默认关闭知识库：KB 层 disabled、引用无 KB 来源、消息仍绑定 DOCX。
  const conversationId = page.url().split("/chat/")[1]!.split(/[?#]/)[0]!;
  const projection = await convergedProjection(page, conversationId);
  expectBoundRewrite(projection, objectId, true);
  const assistant = projection.messages
    .filter((message: Record<string, any>) => message.role === "assistant")
    .at(-1) as Record<string, any>;
  const retrieval = assistant.retrieval as RetrievalProjection | null;
  const kbLayer = retrieval!.layers.find(
    (layer) => layer.layer === "knowledge_base"
  );
  expect(kbLayer).toBeDefined();
  expect(kbLayer!.status).toBe("disabled");
});

test("上传中发送不可用：等待完成或明确取消，不创建空附件消息", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i04c");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 真实会话并进入对话页（Composer 附件路径）。
  const created = await page.request.post("/api/chat/conversations", {
    data: {},
  });
  const conversationId = ((await created.json()) as { conversation_id: string })
    .conversation_id;
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("composer")).toBeVisible();

  // 上传仍走真实服务端，仅人为放慢响应投递 1.5 秒，让「上传中」状态
  // 可被观察（真实上传在本地毫秒级完成，直接断言会闪断）。
  await page.route("**/api/chat/conversations/*/attachments", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });

  const composer = page.getByTestId("composer");
  const sendButton = composer.getByRole("button", { name: "发送消息" });
  dispatchRealFile(page, "composer-file-input", DOCX_NAME, DOCX_PATH);
  // 上传中：发送按钮明确禁用（不能静默发送空附件），chip 展示类型与大小。
  await expect(composer.getByText(/上传中/)).toBeVisible();
  await expect(sendButton).toBeDisabled();
  // chip 展示类型与大小（上传用 octet-stream 原始字节，类型按后缀兜底）。
  await expect(composer.getByText(/DOCX 文件/)).toBeVisible();
  await expect(composer.getByText(/KB/)).toBeVisible();
  // 完成后按钮可用；发送并绑定。
  await expect(composer.getByText("已上传，等待发送")).toBeVisible({
    timeout: 20_000,
  });
  await expect(sendButton).toBeEnabled();
  await composer.getByLabel("输入消息").fill("携带附件发送");
  await sendButton.click();
  await expect(composer.getByLabel("输入消息")).toHaveValue("");
  const projection = await convergedProjection(page, conversationId);
  const user = projection.messages
    .filter((message: Record<string, any>) => message.role === "user")
    .at(-1) as Record<string, any>;
  expect((user.attachments ?? []) as AttachmentProjection[]).toHaveLength(1);
  expect(((user.attachments ?? []) as AttachmentProjection[])[0]!.status).toBe(
    "bound"
  );
});

test("上传后关闭页面再返回：未发送草稿附件可恢复并随下一条消息绑定", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i04d");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const created = await page.request.post("/api/chat/conversations", {
    data: {},
  });
  const conversationId = ((await created.json()) as { conversation_id: string })
    .conversation_id;
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("composer")).toBeVisible();

  // 上传但不发送，直接关闭页面（模拟离开会话）。
  dispatchRealFile(page, "composer-file-input", DOCX_NAME, DOCX_PATH);
  await expect(page.getByText("已上传，等待发送")).toBeVisible({
    timeout: 20_000,
  });

  // 重新打开同一会话：未发送草稿从服务端恢复为待绑定 chip（浏览器
  // session/localStorage 不承担事实源）。
  await page.goto(`/chat/${conversationId}`);
  const composer = page.getByTestId("composer");
  await expect(composer.getByText("已上传，等待发送")).toBeVisible({
    timeout: 20_000,
  });
  // 恢复的 chip 使用服务端嗅探类型（Word），而非客户端原始字节类型。
  await expect(composer.getByText(/Word/)).toBeVisible();

  // 恢复的附件随下一条消息真实绑定。
  await composer.getByLabel("输入消息").fill("恢复草稿后发送");
  await composer.getByRole("button", { name: "发送消息" }).click();
  const projection = await convergedProjection(page, conversationId);
  const user = projection.messages
    .filter((message: Record<string, any>) => message.role === "user")
    .at(-1) as Record<string, any>;
  const attachments = (user.attachments ?? []) as AttachmentProjection[];
  expect(attachments).toHaveLength(1);
  expect(attachments[0]!.original_filename).toBe(DOCX_NAME);
  expect(attachments[0]!.status).toBe("bound");
});
