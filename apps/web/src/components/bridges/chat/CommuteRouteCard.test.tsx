import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommuteRouteCard } from "./CommuteRouteCard";
import type { CommuteRouteProjection } from "@/lib/api";

/** 假的 AMap 命名空间：只实现本组件真正调用的成员，并记录调用。 */
interface FakeAmapCalls {
  zoomIn: number;
  zoomOut: number;
  fitted: number;
  destroyed: number;
  added: number;
  polylinePaths: unknown[];
}

function installFakeAmap(): FakeAmapCalls {
  const calls: FakeAmapCalls = {
    zoomIn: 0,
    zoomOut: 0,
    fitted: 0,
    destroyed: 0,
    added: 0,
    polylinePaths: [],
  };
  class FakeMap {
    constructor(
      readonly container: HTMLElement,
      readonly options: Record<string, unknown>
    ) {}
    add(): void {
      calls.added += 1;
    }
    setFitView(): void {
      calls.fitted += 1;
    }
    zoomIn(): void {
      calls.zoomIn += 1;
    }
    zoomOut(): void {
      calls.zoomOut += 1;
    }
    destroy(): void {
      calls.destroyed += 1;
    }
  }
  class FakePolyline {
    constructor(readonly options: Record<string, unknown>) {
      calls.polylinePaths.push(options.path);
    }
  }
  class FakeMarker {
    constructor(readonly options: Record<string, unknown>) {}
  }
  Object.defineProperty(window, "AMap", {
    configurable: true,
    writable: true,
    value: { Map: FakeMap, Polyline: FakePolyline, Marker: FakeMarker },
  });
  return calls;
}

function stubMapConfig(config: Record<string, unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => config,
    }))
  );
}

const CONFIGURED_MAP = {
  configured: true,
  js_api_key: "js-key",
  // 后端下发的是相对 API 基地址的路径（默认基地址为 /api）
  service_host_path: "/commute/amap-proxy",
  security_code_configured: true,
  notice: null,
};

function projection(
  overrides: Partial<CommuteRouteProjection> = {}
): CommuteRouteProjection {
  return {
    status: "success",
    mode: "walking",
    mode_label: "步行",
    mode_phrase: "走过去",
    origin: {
      role: "origin",
      original_phrase: "图书馆",
      query: "华东交通大学图书馆",
      name: "华东交通大学图书馆",
      location: "115.860000,28.680000",
      address: "双港东大街808号",
      poi_id: "B001",
      district: "青山湖区",
      campus_verified: true,
      match_basis: "高德返回名称含「华东交通大学」。",
      unverified: [],
    },
    destination: {
      role: "destination",
      original_phrase: "南区食堂",
      query: "华东交通大学南区食堂",
      name: "华东交通大学南区学生食堂",
      location: "115.865000,28.682000",
      address: "双港东大街808号",
      poi_id: "B002",
      district: "青山湖区",
      campus_verified: true,
      match_basis: "高德返回名称含「华东交通大学」。",
      unverified: [],
    },
    origin_candidates: [],
    destination_candidates: [],
    distance_m: 1800,
    base_duration_seconds: 1400,
    suggested_total_seconds: 1700,
    steps: [
      {
        index: 1,
        instruction: "向东步行 200 米",
        road_name: "学府路",
        distance_m: 200,
      },
      {
        index: 2,
        instruction: "右转进入食堂路",
        road_name: null,
        distance_m: 400,
      },
    ],
    polyline: ["115.860000,28.680000", "115.862000,28.681000", "115.865000,28.682000"],
    path_verified: true,
    buffer: {
      in_window: true,
      matched_break_time: "09:40",
      minutes_away: 5,
      added_minutes: 5,
      checked_at: "2026-09-25T01:40:00+00:00",
      timezone: "Asia/Shanghai",
      rule_note: "课间高峰按固定时间点规则估计，这是规则估计，不是实时人流数据。",
    },
    queries: [
      {
        source: "amap_place",
        query: "华东交通大学图书馆",
        status: "success",
        evidence_count: 1,
        retrieved_at: "2026-09-25T01:40:00Z",
        retryable: false,
      },
      {
        source: "amap_route",
        query: "步行 115.860000,28.680000 → 115.865000,28.682000",
        status: "success",
        evidence_count: 1,
        retrieved_at: "2026-09-25T01:40:01Z",
        retryable: false,
      },
    ],
    evidence_notes: ["本轮只使用高德的步行结果，没有拿其他方式的耗时替代。"],
    pending: null,
    resolved_at: "2026-09-25T01:40:01Z",
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

beforeEach(() => {
  stubMapConfig(CONFIGURED_MAP);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "AMap");
});

describe("CommuteRouteCard（V2 Issue 12）", () => {
  it("成功结果按交互顺序给关键信息、地图、路线文字与证据边界", async () => {
    const calls = installFakeAmap();
    render(<CommuteRouteCard route={projection()} streaming={false} />);

    const card = screen.getByTestId("commute-route-card-success");
    // AC5：关键信息在卡内可读，且与投影数值一致
    const facts = screen.getByTestId("commute-route-facts").textContent ?? "";
    expect(facts).toContain("华东交通大学图书馆");
    expect(facts).toContain("华东交通大学南区学生食堂");
    expect(facts).toContain("1.8 公里");
    expect(facts).toContain("23 分钟"); // 1400 秒基础耗时（与服务端同一口径取整）
    expect(facts).toContain("28 分钟"); // 1700 秒建议总时间（基础耗时 + 5 分钟缓冲）
    expect(screen.getByTestId("commute-route-mode").textContent).toContain("步行");
    expect(screen.getByTestId("commute-route-mode").textContent).toContain("走过去");
    expect(facts).toContain("可能人多");
    expect(facts).toContain("09:40");

    // 课间规则声明：明确不是实时人流数据
    expect(screen.getByTestId("commute-route-buffer-note").textContent).toContain(
      "不是实时人流数据"
    );

    // 路线文字保持高德返回顺序
    const steps = screen.getByTestId("commute-route-steps").textContent ?? "";
    expect(steps.indexOf("向东步行 200 米")).toBeLessThan(steps.indexOf("右转进入食堂路"));

    // 地图：只按高德返回的路径点画线
    const canvas = await screen.findByTestId("commute-route-map-canvas");
    expect(canvas.getAttribute("aria-label")).toContain("3 个高德返回的路径点");
    await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());
    expect(calls.polylinePaths[0]).toEqual([
      [115.86, 28.68],
      [115.862, 28.681],
      [115.865, 28.682],
    ]);
    expect(calls.added).toBe(1);
    expect(calls.fitted).toBe(1);

    // 外部调用记录与证据边界
    const queries = screen.getByTestId("commute-route-queries").textContent ?? "";
    expect(queries).toContain("高德地点检索");
    expect(queries).toContain("高德路线规划");
    expect(screen.getByTestId("commute-route-notes").textContent).toContain(
      "没有拿其他方式的耗时替代"
    );
    expect(card.getAttribute("role")).toBe("status");
  });

  it("把安全密钥交给后端代理：浏览器只拿到同源代理地址", async () => {
    installFakeAmap();
    render(<CommuteRouteCard route={projection()} streaming={false} />);
    await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());

    // 高德官方代理方案：serviceHost 指向本应用的代理路由，安全密钥不经浏览器
    expect(window._AMapSecurityConfig?.serviceHost).toBe(
      `${window.location.origin}/api/commute/amap-proxy`
    );
    expect(window._AMapSecurityConfig).not.toHaveProperty("securityJsCode");
  });

  it("地图可缩放：放大与缩小按钮驱动地图对象", async () => {
    const calls = installFakeAmap();
    render(<CommuteRouteCard route={projection()} streaming={false} />);
    await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());

    fireEvent.click(screen.getByTestId("commute-route-zoom-in"));
    fireEvent.click(screen.getByTestId("commute-route-zoom-out"));
    expect(calls.zoomIn).toBe(1);
    expect(calls.zoomOut).toBe(1);
  });

  it("三种方式各自显示本轮的方式、距离与耗时，不串用其他方式数值", async () => {
    installFakeAmap();
    const modes = [
      { mode: "walking" as const, label: "步行", distance: 1800, seconds: 1400 },
      { mode: "bicycling" as const, label: "自行车", distance: 2400, seconds: 600 },
      { mode: "electrobike" as const, label: "电动车", distance: 2400, seconds: 480 },
    ];
    for (const item of modes) {
      cleanup();
      render(
        <CommuteRouteCard
          route={projection({
            mode: item.mode,
            mode_label: item.label,
            distance_m: item.distance,
            base_duration_seconds: item.seconds,
            suggested_total_seconds: item.seconds,
          })}
          streaming={false}
        />
      );
      const facts = screen.getByTestId("commute-route-facts").textContent ?? "";
      expect(screen.getByTestId("commute-route-mode").textContent).toContain(item.label);
      expect(facts).toContain(
        item.distance === 1800 ? "1.8 公里" : "2.4 公里"
      );
      for (const other of modes) {
        if (other.label === item.label) continue;
        expect(screen.getByTestId("commute-route-mode").textContent).not.toContain(
          other.label
        );
      }
      await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());
    }
  });

  it("未取得路径点时只显示真实地点并说明没有画线", async () => {
    const calls = installFakeAmap();
    render(
      <CommuteRouteCard
        route={projection({
          status: "unverified",
          polyline: [],
          path_verified: false,
          suggested_total_seconds: null,
          evidence_notes: ["本轮没有取得可核验的路径点，因此没有绘制任何路线线。"],
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("commute-route-card-unverified")).toBeTruthy();
    expect(screen.getByTestId("commute-route-map-unavailable").textContent).toContain(
      "路线线"
    );
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
    // 已证实的地点、距离与路段仍然呈现
    expect(screen.getByTestId("commute-route-facts").textContent).toContain("1.8 公里");
    expect(screen.getByTestId("commute-route-steps").textContent).toContain("向东步行 200 米");
    expect(calls.polylinePaths).toHaveLength(0);
  });

  it("缺少浏览器地图凭据时地图位置可见降级，路线数据照常显示", async () => {
    installFakeAmap();
    stubMapConfig({
      configured: false,
      js_api_key: null,
      service_host_path: null,
      security_code_configured: false,
      notice: "未配置高德浏览器地图凭据：请在设置页「高德凭据」中填写后重试。",
    });
    render(<CommuteRouteCard route={projection()} streaming={false} />);

    await waitFor(() =>
      expect(screen.getByTestId("commute-route-map-unavailable")).toBeTruthy()
    );
    expect(screen.getByTestId("commute-route-map-unavailable").textContent).toContain(
      "设置页"
    );
    expect(screen.getByTestId("commute-route-facts").textContent).toContain("1.8 公里");
  });

  it("澄清状态逐项列出高德返回的候选并说明回复方式", () => {
    render(
      <CommuteRouteCard
        route={projection({
          status: "clarification",
          origin_candidates: [
            {
              name: "华东交通大学图书馆",
              location: "115.860000,28.680000",
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
          pending: {
            module_id: "commute",
            kind: "clarification",
            question: "「图书馆」在高德匹配到多个地点，你要从哪一个出发？",
            origin_message_id: "assistant-1",
            context: {},
            created_at: "2026-09-25T01:40:00Z",
          },
          distance_m: null,
          base_duration_seconds: null,
          suggested_total_seconds: null,
          steps: [],
          polyline: [],
          path_verified: false,
        })}
        streaming={false}
      />
    );

    const card = screen.getByTestId("commute-route-clarification").textContent ?? "";
    expect(card).toContain("你要从哪一个出发");
    expect(screen.getByTestId("commute-route-origin-candidates").textContent).toContain(
      "南昌市图书馆"
    );
    expect(screen.getByTestId("commute-route-origin-candidates").textContent).toContain(
      "未确认校内"
    );
    expect(card).toContain("回复序号或地点名称");
    // 未确认地点前不给路线数字
    expect(screen.queryByTestId("commute-route-facts")).toBeNull();
  });

  it("凭据缺失等失败显示原因、错误码与重试入口", () => {
    const onRetry = vi.fn();
    render(
      <CommuteRouteCard
        route={projection({
          status: "error",
          error_code: "amap_not_configured",
          error_message: "尚未配置高德 Web Service Key，请在设置页填写后重试。",
          retryable: true,
          distance_m: null,
          base_duration_seconds: null,
          suggested_total_seconds: null,
          steps: [],
          polyline: [],
          path_verified: false,
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );

    const card = screen.getByTestId("commute-route-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("尚未配置高德 Web Service Key");
    expect(card.textContent).toContain("amap_not_configured");

    fireEvent.click(screen.getByTestId("commute-route-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("停止状态如实显示已停止并保留已发出的查询", () => {
    render(
      <CommuteRouteCard
        route={projection({
          status: "stopped",
          distance_m: null,
          base_duration_seconds: null,
          suggested_total_seconds: null,
          steps: [],
          polyline: [],
          path_verified: false,
        })}
        streaming={false}
      />
    );

    const card = screen.getByTestId("commute-route-card-stopped").textContent ?? "";
    expect(card).toContain("已停止校园通勤");
    expect(screen.getByTestId("commute-route-queries").textContent).toContain(
      "华东交通大学图书馆"
    );
  });

  it("无投影时不渲染任何内容", () => {
    const { container } = render(<CommuteRouteCard route={null} streaming={false} />);
    expect(container.textContent).toBe("");
  });
});
