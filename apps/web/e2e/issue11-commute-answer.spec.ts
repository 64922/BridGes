import { expect, test, type Page, type Route } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-commute";

const SUCCESS_ID = "conv-commute-success";
const CLARIFY_ID = "conv-commute-clarify";
const UNVERIFIED_ID = "conv-commute-unverified";
const ERROR_ID = "conv-commute-error";

const BODY_STEPS = [
  "起点：华东交通大学图书馆，双港东大街808号（坐标 115.860000,28.680000，起点·校内·高德 POI）",
  "终点：华东交通大学南区学生食堂，双港东大街808号（坐标 115.865000,28.682000，终点·校内·高德 POI）",
  "方式：步行（你说的是「走过去」）。",
  "距离：1.8 公里",
  "高德基础耗时：23 分钟（高德估计，不是保证到达时间）。",
  "课间缓冲：可能人多（命中课间点 09:40 前后 5 分钟），建议加 5 分钟。",
  "建议总时间：28 分钟（基础耗时加上课间规则缓冲的估计值，不是保证到达时间）。",
  "地图：已取得高德返回的 3 个路径点，地图按这些路径点绘制在正文下方；完整路段、POI 与调用记录可在「查看路线详情」中读取。",
  "路线要点：",
  "1. 沿学府路向东步行 200 米（学府路），200 米",
  "2. 右转进入食堂路，400 米",
  "3. 到达南区学生食堂，100 米",
  "4. 从食堂东门进入，50 米",
  "其余 1 段路线文字见「查看路线详情」。",
  "证据边界：",
  "- 本轮只使用高德的步行结果，没有拿其他方式的耗时替代。",
  "课间提示按规则估计：课间高峰按固定时间点规则估计，这是规则估计，不是实时人流数据。",
].join("\n");

function place(role: "origin" | "destination", name: string, location: string) {
  return {
    role,
    original_phrase: role === "origin" ? "图书馆" : "南区食堂",
    query: name,
    name,
    location,
    address: "双港东大街808号",
    poi_id: role === "origin" ? "B001" : "B002",
    district: "青山湖区",
    campus_verified: true,
    match_basis: "高德返回名称含「华东交通大学」。",
    unverified: [],
  };
}

const SUCCESS_ROUTE = {
  status: "success",
  mode: "walking",
  mode_label: "步行",
  mode_phrase: "走过去",
  origin: place("origin", "华东交通大学图书馆", "115.860000,28.680000"),
  destination: place("destination", "华东交通大学南区学生食堂", "115.865000,28.682000"),
  origin_candidates: [],
  destination_candidates: [],
  distance_m: 1800,
  base_duration_seconds: 1400,
  suggested_total_seconds: 1700,
  steps: [
    { index: 1, instruction: "沿学府路向东步行 200 米", road_name: "学府路", distance_m: 200 },
    { index: 2, instruction: "右转进入食堂路", road_name: null, distance_m: 400 },
    { index: 3, instruction: "到达南区学生食堂", road_name: null, distance_m: 100 },
    { index: 4, instruction: "从食堂东门进入", road_name: null, distance_m: 50 },
    { index: 5, instruction: "到达目的地", road_name: null, distance_m: 50 },
  ],
  polyline: [
    "115.860000,28.680000",
    "115.862000,28.681000",
    "115.865000,28.682000",
  ],
  path_verified: true,
  buffer: {
    in_window: true,
    matched_break_time: "09:40",
    minutes_away: 5,
    added_minutes: 5,
    checked_at: "2026-10-18T01:40:00+00:00",
    timezone: "Asia/Shanghai",
    rule_note: "课间高峰按固定时间点规则估计，这是规则估计，不是实时人流数据。",
  },
  queries: [
    {
      source: "amap_place",
      query: "华东交通大学图书馆",
      status: "success",
      evidence_count: 1,
      retrieved_at: "2026-10-18T01:40:00Z",
      retryable: false,
    },
    {
      source: "amap_route",
      query: "步行 115.860000,28.680000 → 115.865000,28.682000",
      status: "success",
      evidence_count: 1,
      retrieved_at: "2026-10-18T01:40:01Z",
      retryable: false,
    },
  ],
  evidence_notes: ["本轮只使用高德的步行结果，没有拿其他方式的耗时替代。"],
  pending: null,
  resolved_at: "2026-10-18T01:40:01Z",
  error_code: null,
  error_message: null,
  retryable: false,
};

const CLARIFY_ROUTE = {
  ...SUCCESS_ROUTE,
  status: "clarification",
  origin: null,
  origin_candidates: [],
  destination_candidates: [
    {
      name: "华东交通大学图书馆",
      location: "115.870000,28.750000",
      address: "双港东大街808号",
      poi_id: "B001",
      district: "青山湖区",
      campus: true,
    },
    {
      name: "南昌市图书馆",
      location: "115.900000,28.690000",
      address: "洪都北大道",
      poi_id: "B009",
      district: "东湖区",
      campus: false,
    },
  ],
  distance_m: null,
  base_duration_seconds: null,
  suggested_total_seconds: null,
  steps: [],
  polyline: [],
  path_verified: false,
  buffer: null,
  queries: [],
  evidence_notes: [],
  pending: {
    module_id: "commute",
    kind: "clarification",
    question: "「图书馆」在高德匹配到多个地点，你要去哪一个？",
    origin_message_id: "a-clarify",
    context: { awaiting: "destination" },
    created_at: NOW,
  },
  resolved_at: null,
};

const UNVERIFIED_ROUTE = {
  ...SUCCESS_ROUTE,
  status: "unverified",
  polyline: [],
  path_verified: false,
  suggested_total_seconds: null,
  evidence_notes: ["本轮没有取得可核验的路径点，因此没有绘制任何路线线。"],
};

const ERROR_ROUTE = {
  ...SUCCESS_ROUTE,
  status: "error",
  distance_m: null,
  base_duration_seconds: null,
  suggested_total_seconds: null,
  steps: [],
  polyline: [],
  path_verified: false,
  buffer: null,
  queries: [
    {
      source: "amap_route",
      query: "步行 115.860000,28.680000 → 115.865000,28.682000",
      status: "timeout",
      evidence_count: 0,
      retrieved_at: "2026-10-18T01:40:01Z",
      retryable: true,
      error_message: "高德查询超时，请稍后重试。",
    },
  ],
  evidence_notes: ["路线查询失败，没有取得任何路段时间。"],
  error_code: "amap_timeout",
  error_message: "高德查询超时，请稍后重试。",
  retryable: true,
};

interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  commute_route?: unknown;
  error_code?: string | null;
  error_message?: string | null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
}

function userMessage(conversationId: string, id: string, content: string): MockMessage {
  return {
    message_id: id,
    conversation_id: conversationId,
    role: "user",
    attempt_number: 1,
    status: "done",
    content,
    error_code: null,
    error_message: null,
    duration_ms: null,
    model_id: null,
    run_lock_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function assistantMessage(
  conversationId: string,
  id: string,
  content: string,
  route: unknown,
  status = "done"
): MockMessage {
  return {
    message_id: id,
    conversation_id: conversationId,
    role: "assistant",
    attempt_number: 1,
    status,
    content,
    commute_route: route,
    error_code: status === "error" ? "amap_timeout" : null,
    error_message: status === "error" ? "高德查询超时，请稍后重试。" : null,
    duration_ms: 90,
    model_id: "mock-model",
    run_lock_id: "mock-lock",
    created_at: NOW,
    updated_at: NOW,
  };
}

function conversation(
  conversationId: string,
  title: string,
  messages: MockMessage[]
) {
  return {
    conversation_id: conversationId,
    title,
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
}

const CONVERSATIONS: Record<string, ReturnType<typeof conversation>> = {
  [SUCCESS_ID]: conversation(SUCCESS_ID, "通勤成功", [
    userMessage(SUCCESS_ID, "u-success", "从图书馆步行到南区食堂怎么走"),
    assistantMessage(SUCCESS_ID, "a-success", BODY_STEPS, SUCCESS_ROUTE),
  ]),
  [CLARIFY_ID]: conversation(CLARIFY_ID, "通勤澄清", [
    userMessage(CLARIFY_ID, "u-clarify", "去图书馆怎么走"),
    assistantMessage(
      CLARIFY_ID,
      "a-clarify",
      "「图书馆」在高德匹配到多个地点，你要去哪一个？",
      CLARIFY_ROUTE
    ),
  ]),
  [UNVERIFIED_ID]: conversation(UNVERIFIED_ID, "通勤无路径点", [
    userMessage(UNVERIFIED_ID, "u-unverified", "从图书馆到南区食堂怎么走"),
    assistantMessage(
      UNVERIFIED_ID,
      "a-unverified",
      "已取得地点、距离与耗时，但本轮没有可核验的路径点，因此没有绘制路线线。",
      UNVERIFIED_ROUTE
    ),
  ]),
  [ERROR_ID]: conversation(ERROR_ID, "通勤失败", [
    userMessage(ERROR_ID, "u-error", "从图书馆步行到南区食堂怎么走"),
    assistantMessage(
      ERROR_ID,
      "a-error",
      "校园通勤本轮没有完成。高德查询超时，请稍后重试。可以在下方按真实可用的恢复入口重试同一条请求。",
      ERROR_ROUTE,
      "error"
    ),
  ]),
};

const AMAP_SCRIPT = `
window.__amapCalls = { maps: 0, destroyed: 0, paths: [] };
class FakeMap {
  constructor(container, options) { window.__amapCalls.maps += 1; this.container = container; }
  add() {}
  setFitView() {}
  zoomIn() {}
  zoomOut() {}
  destroy() { window.__amapCalls.destroyed += 1; }
}
class FakePolyline {
  constructor(options) { window.__amapCalls.paths.push(options.path); }
}
class FakeMarker {
  constructor(options) { this.options = options; }
}
window.AMap = { Map: FakeMap, Polyline: FakePolyline, Marker: FakeMarker };
`;

interface MockOptions {
  mapConfigured?: boolean;
  mapConfigFails?: boolean;
}

async function installMock(page: Page, options: MockOptions = {}) {
  const sendBodies: Array<Record<string, unknown>> = [];
  const eventStreams = new Map<string, string>();

  await page.route("**/api/chat/conversations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversations: Object.values(CONVERSATIONS).map((item) => ({
          conversation_id: item.conversation_id,
          title: item.title,
          mode: item.mode,
          message_count: item.messages.length,
          created_at: NOW,
          updated_at: NOW,
        })),
      }),
    });
  });

  for (const conversationId of Object.keys(CONVERSATIONS)) {
    await page.route(
      `**/api/chat/conversations/${conversationId}`,
      async (route: Route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(CONVERSATIONS[conversationId]),
        });
      }
    );

    await page.route(
      `**/api/chat/conversations/${conversationId}/messages`,
      async (route: Route) => {
        if (route.request().method() !== "POST") return;
        const body = JSON.parse(route.request().postData() ?? "{}");
        sendBodies.push(body);
        const conversationState = CONVERSATIONS[conversationId];
        const user = userMessage(
          conversationId,
          `u-follow-${sendBodies.length}`,
          String(body.content ?? "")
        );
        const assistant = assistantMessage(
          conversationId,
          `a-follow-${sendBodies.length}`,
          "已收到补充，继续回答。",
          null
        );
        conversationState.messages.push(user, assistant);
        const events = [
          `event: started\ndata: ${JSON.stringify({
            kind: "started",
            conversation_id: conversationId,
            user_message_id: user.message_id,
            message_id: assistant.message_id,
            attempt_number: 1,
          })}\n\n`,
          `event: delta\ndata: ${JSON.stringify({
            kind: "delta",
            message_id: assistant.message_id,
            delta: "已收到补充，继续回答。",
          })}\n\n`,
          `event: done\ndata: ${JSON.stringify({
            kind: "done",
            message_id: assistant.message_id,
            message: assistant,
          })}\n\n`,
        ].join("");
        eventStreams.set(assistant.message_id, events);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: `run-${assistant.message_id}`,
            cursor: 1,
            user_message: user,
            assistant_message: assistant,
          }),
        });
      }
    );

    await page.route(
      `**/api/chat/conversations/${conversationId}/messages/*/events**`,
      async (route: Route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
          body: eventStreams.get(messageId) ?? "",
        });
      }
    );
  }

  await page.route("**/api/commute/map-config", async (route) => {
    if (options.mapConfigFails) {
      await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
      return;
    }
    const configured = options.mapConfigured ?? true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured,
        js_api_key: configured ? "js-key" : null,
        service_host_path: configured ? "/commute/amap-proxy" : null,
        security_code_configured: configured,
        notice: configured ? null : "未配置高德浏览器地图凭据：请在设置页「高德凭据」中填写后重试。",
      }),
    });
  });

  await page.route(/https:\/\/webapi\.amap\.com\/maps/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: AMAP_SCRIPT,
    });
  });

  return { sendBodies };
}

async function openConversation(page: Page, conversationId: string) {
  const credentials = uniqueCredentials("issue11-commute");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 11 通勤建议、地图与澄清融入对话", () => {
  test("有效路线：正文可独立理解，地图紧邻正文，详情按需打开且不阻塞输入", async ({
    page,
  }, testInfo) => {
    await installMock(page);
    await openConversation(page, SUCCESS_ID);

    const assistant = page.getByTestId("assistant-message").first();
    await expect(assistant).toContainText("起点：华东交通大学图书馆");
    await expect(assistant).toContainText("高德基础耗时：23 分钟");
    await expect(assistant).toContainText("不是保证到达时间");
    await expect(assistant).toContainText("不是实时人流数据");
    await expect(assistant).toContainText("路线要点");
    await expect(assistant).toContainText("沿学府路向东步行 200 米");
    await expect(assistant).toContainText("其余 1 段路线文字见「查看路线详情」");

    await expect(page.getByTestId("commute-route-map-ready")).toBeVisible();
    await expect(page.getByTestId("commute-detail-panel")).toHaveCount(0);
    await expect(page.getByTestId("commute-answer-details")).toBeVisible();

    await page.getByTestId("commute-answer-details").click();
    const panel = page.getByTestId("commute-detail-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("完整路线文字");
    await expect(panel).toContainText("5. 到达目的地");
    await expect(panel).toContainText("本轮只使用高德的步行结果");
    await expect(panel).toContainText("文字等价");

    const composer = page.locator("#composer-input");
    await expect(composer).toBeVisible();
    await composer.fill("查看详情时还能输入");
    await expect(composer).toHaveValue("查看详情时还能输入");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("chat-thread")).toContainText("查看详情时还能输入");
    await expect(panel).toBeVisible();

    const calls = await page.evaluate(
      () => (window as unknown as { __amapCalls?: { paths: unknown[] } }).__amapCalls
    );
    expect(calls?.paths).toEqual([
      [
        [115.86, 28.68],
        [115.862, 28.681],
        [115.865, 28.682],
      ],
    ]);

    await page.screenshot({
      path: testInfo.outputPath("commute-success-1280x720.png"),
      fullPage: false,
    });
  });

  test("模糊地点：展示高德真实候选，不显示路线数字，补充后带模块续接", async ({
    page,
  }) => {
    const api = await installMock(page);
    await openConversation(page, CLARIFY_ID);

    const clarification = page.getByTestId("commute-clarification");
    await expect(clarification).toBeVisible();
    await expect(clarification).toContainText("南昌市图书馆");
    await expect(clarification).toContainText("未确认校内");
    await expect(page.getByTestId("chat-thread")).not.toContainText("高德基础耗时");
    await expect(page.getByTestId("commute-route-map-canvas")).toHaveCount(0);
    await expect(page.getByTestId("composer-module-chip")).toContainText("校园通勤");

    await page.locator("#composer-input").fill("步行");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("chat-thread")).toContainText("步行");
    expect(api.sendBodies.at(-1)?.module_id).toBe("commute");
  });

  test("无路径点：正文保留真实信息，地图位置说明不可用而不是空白", async ({
    page,
  }) => {
    await installMock(page);
    await openConversation(page, UNVERIFIED_ID);

    await expect(page.getByTestId("assistant-message").first()).toContainText(
      "没有可核验的路径点"
    );
    await expect(page.getByTestId("commute-route-map-unavailable")).toContainText(
      "路线线"
    );
    await expect(page.getByTestId("commute-route-map-canvas")).toHaveCount(0);
    await expect(page.getByTestId("commute-answer-details")).toBeVisible();
  });

  test("地图界面失败与地理供应商失败分开呈现", async ({ page }) => {
    await installMock(page, { mapConfigured: false });
    await openConversation(page, SUCCESS_ID);

    await expect(page.getByTestId("assistant-message").first()).toContainText("起点：");
    await expect(page.getByTestId("commute-route-map-unavailable")).toContainText(
      "未配置高德浏览器地图凭据"
    );
    await expect(page.getByTestId("commute-route-map-credential-action")).toBeVisible();
    await expect(page.getByTestId("commute-route-map-canvas")).toHaveCount(0);

    await page.goto(`/chat/${ERROR_ID}`);
    await expect(page.getByTestId("assistant-message").first()).toContainText(
      "高德查询超时"
    );
    await expect(page.getByTestId("commute-error")).toContainText("amap_timeout");
    await expect(page.getByTestId("commute-answer-retry")).toBeVisible();
    await expect(page.getByTestId("commute-route-map-canvas")).toHaveCount(0);
    await expect(page.getByTestId("commute-route-credential-action")).toHaveCount(0);
  });

  test("历史重开可读且不静默刷新路线", async ({ page }) => {
    await installMock(page);
    const requests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes(`/api/chat/conversations/${SUCCESS_ID}/messages`)
      ) {
        requests.push(request.url());
      }
    });

    await openConversation(page, SUCCESS_ID);
    await expect(page.getByTestId("assistant-message").first()).toContainText(
      "沿学府路向东步行 200 米"
    );
    await expect(page.getByTestId("commute-route-map-ready")).toBeVisible();
    expect(requests).toHaveLength(0);

    await page.reload();
    await expect(page.getByTestId("assistant-message").first()).toContainText(
      "沿学府路向东步行 200 米"
    );
    await expect(page.getByTestId("commute-route-map-ready")).toBeVisible();
    expect(requests).toHaveLength(0);

    const calls = await page.evaluate(
      () => (window as unknown as { __amapCalls?: { paths: unknown[] } }).__amapCalls
    );
    expect(calls?.paths).toEqual([
      [
        [115.86, 28.68],
        [115.862, 28.681],
        [115.865, 28.682],
      ],
    ]);
  });
});
