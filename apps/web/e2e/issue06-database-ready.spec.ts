import { createHash } from "crypto";
import { expect, test } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 06 真实跨进程 smoke：从空 E2E DB 启动后端，等待 database-ready，
 * 验证 schema 版本、run_id、数据库路径指纹，并通过真实 HTTP 注册账户、
 * 创建首轮会话、读取/列出会话，确保响应中不存在 "no such table"。
 *
 * run_id/路径指纹核对不依赖进程间环境变量传递：Playwright 的 webServer
 * env 只注入服务进程，而配置模块又在每个 worker 进程独立求值；因此这里
 * 以磁盘为真相——后端报告的 run_id 必须对应本 run 真实创建的
 * `.tmp/e2e-run/run-<run_id>` 目录，且该目录下 bridges.db 的路径指纹与
 * 后端报告一致，证明 API 使用的就是本 run 的数据库。
 */

// 本文件位于 apps/web/e2e/，仓库根目录需上溯三级。
const REPO_ROOT = path.resolve(__dirname, "../../..");

function expectedRunDir(runId: string): string {
  return process.env.E2E_DATA_DIR
    ? path.resolve(REPO_ROOT, process.env.E2E_DATA_DIR)
    : path.resolve(REPO_ROOT, ".tmp", "e2e-run", runId);
}

function databasePathFingerprint(runDir: string): string {
  const databasePath = path.join(runDir, "bridges.db");
  return createHash("sha256").update(databasePath).digest("hex").slice(0, 8);
}

test("从空 DB 启动后 database-ready 并通过真实 repository 查询", async ({
  page,
}) => {
  // page.request 与浏览器上下文共享会话 Cookie：注册后即已认证，
  // 后续 API 调用走同一真实身份（独立 request fixture 无 Cookie）。
  const request = page.request;

  // 1. 等待并校验 database-ready：schema 已就绪、run_id 与路径指纹一致。
  const healthResponse = await request.get("/api/health/ready");
  expect(healthResponse.status()).toBe(200);
  const health = (await healthResponse.json()) as {
    ready: string;
    dependencies: Array<{ name: string; status: string; message?: string }>;
    extensions: {
      run_id?: string;
      database_path_fingerprint?: string;
      database_schema_version?: number;
      expected_database_schema_version?: number;
      database_schema_tables?: string[];
    };
  };
  expect(health.ready).toBe("pass");

  const dbDep = health.dependencies.find((d) => d.name === "database_schema_ready");
  expect(dbDep).toBeDefined();
  expect(dbDep!.status).toBe("pass");

  // schema 版本契约：后端报告的实际版本必须等于当前程序支持版本。
  expect(health.extensions.database_schema_version).toBe(
    health.extensions.expected_database_schema_version
  );
  expect(health.extensions.database_schema_version).toBeGreaterThanOrEqual(44);
  // 核心契约表可查询：迁移审计表必须在列，缺表场景（Issue 06 根因）绝不允许。
  const tables = health.extensions.database_schema_tables ?? [];
  expect(tables).toContain("conversations");
  expect(tables).toContain("learning_project_migration_conversations");

  // run_id / 路径指纹契约：本 run 的隔离目录真实存在，且后端报告的
  // 指纹与磁盘上该目录内 bridges.db 的指纹一致（不公开完整敏感路径）。
  const runId = health.extensions.run_id;
  expect(runId).toMatch(/^run-\d+-[a-z0-9]{6}$/);
  const runDir = expectedRunDir(runId!);
  expect(fs.existsSync(path.join(runDir, "bridges.db"))).toBe(true);
  expect(health.extensions.database_path_fingerprint).toBe(
    databasePathFingerprint(runDir)
  );

  // 2. 真实浏览器注册账户。
  const credentials = uniqueCredentials("i6");
  await signUp(page, credentials.username, credentials.qqEmail, "issue06-smoke-password");

  // 3. 通过 API 创建首轮会话；如果 DB 缺少迁移审计表，此处会 500。
  const firstTurnResponse = await request.post("/api/chat/first-turn", {
    data: {
      content: "Issue 06 数据库就绪测试",
      idempotency_key: `issue06-e2e-${runId}`,
    },
  });
  expect(
    firstTurnResponse.status(),
    await firstTurnResponse.text()
  ).toBe(201);
  const firstTurnBody = (await firstTurnResponse.json()) as {
    conversation: { conversation_id: string };
  };
  const conversationId = firstTurnBody.conversation.conversation_id;
  expect(conversationId).toBeTruthy();

  // 4. 读取会话详情（触发 learning_project_migration_conversations 子查询）。
  const detailResponse = await request.get(
    `/api/chat/conversations/${conversationId}`
  );
  expect(detailResponse.status()).toBe(200);
  const detail = (await detailResponse.json()) as {
    conversation_id: string;
    messages: Array<{ role: string }>;
  };
  expect(detail.conversation_id).toBe(conversationId);

  // 5. 列出会话（同样触发迁移审计表查询）。
  const listResponse = await request.get("/api/chat/conversations");
  expect(listResponse.status()).toBe(200);
  const list = (await listResponse.json()) as {
    conversations: Array<{ conversation_id: string }>;
  };
  expect(list.conversations.map((c) => c.conversation_id)).toContain(conversationId);

  // 6. 刷新页面后仍能通过 repository 读取同一会话。
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(
    page.getByTestId("chat-thread").getByText("Issue 06 数据库就绪测试", { exact: true })
  ).toBeVisible();
});

// Issue 06（AC6/Observability）：失败时把脱敏的 schema 诊断产物写入本 run
// 目录（schema 版本、对象名清单与健康快照；不包含密码、邮件正文、Key 或
// 完整数据库转储；路径以指纹代替，不公开完整敏感绝对路径）。
test.afterEach(async ({ request }, testInfo) => {
  if (testInfo.status === "passed") return;
  try {
    const healthResponse = await request.get("/api/health/ready");
    const health = (await healthResponse.json()) as {
      ready?: string;
      dependencies?: Array<{ name: string; status: string }>;
      extensions?: {
        run_id?: string;
        database_path_fingerprint?: string;
        database_schema_version?: number;
        expected_database_schema_version?: number;
        database_schema_tables?: string[];
      };
    };
    const runId = health.extensions?.run_id;
    if (!runId) return;
    const runDir = expectedRunDir(runId);
    if (!fs.existsSync(runDir)) return;
    const diagnostics = {
      written_at: new Date().toISOString(),
      run_id: runId,
      database_path_fingerprint: health.extensions?.database_path_fingerprint,
      schema_version: health.extensions?.database_schema_version,
      expected_schema_version: health.extensions?.expected_database_schema_version,
      ready: health.ready,
      dependencies: health.dependencies,
      schema_tables: health.extensions?.database_schema_tables,
    };
    fs.writeFileSync(
      path.join(runDir, "schema-diagnostics.json"),
      JSON.stringify(diagnostics, null, 2),
      "utf-8"
    );
  } catch {
    // 诊断写入失败不掩盖原始测试失败
  }
});
