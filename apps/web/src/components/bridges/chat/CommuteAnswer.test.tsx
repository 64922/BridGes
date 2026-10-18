import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessageView } from "@/lib/chat-message-view";
import type { CommuteRouteProjection, TurnResultProjection } from "@/lib/api";

import { CommuteAnswer } from "./CommuteAnswer";

interface FakeAmapCalls {
  zoomIn: number;
  zoomOut: number;
  destroyed: number;
  added: number;
  polylinePaths: unknown[];
}

function installFakeAmap(): FakeAmapCalls {
  const calls: FakeAmapCalls = {
    zoomIn: 0,
    zoomOut: 0,
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
    setFitView(): void {}
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
    queries: [],
    evidence_notes: ["本轮只使用高德的步行结果。"],
    pending: null,
    resolved_at: "2026-09-25T01:40:01Z",
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

function message(
  route: CommuteRouteProjection,
  overrides: Partial<ChatMessageView> = {}
): ChatMessageView {
  return {
    id: "a-commute",
    role: "assistant",
    plainText: "正文",
    commuteRoute: route,
    ...overrides,
  };
}

function retryableTurnResult(): TurnResultProjection {
  return {
    version: "turn-result-v1",
    outcome: "failed",
    outcome_label: "本轮失败",
    trust: "evidence_bound",
    trust_label: "证据受限",
    requested_module_id: "commute",
    actual_module_id: "commute",
    capability_list: ["commute"],
    route_source: null,
    delivered: [],
    blocked: [],
    gaps: [],
    recovery: {
      action: "retry",
      label: "可以重试本轮",
      retryable: true,
      available_after: null,
    },
    wait_reason: null,
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

describe("CommuteAnswer", () => {
  it("成功路线把地图放在正文之后，并提供按需打开的路线详情", async () => {
    const calls = installFakeAmap();
    const onOpenDetails = vi.fn();
    const route = projection();
    render(
      <CommuteAnswer
        message={message(route)}
        conversationId="conversation-1"
        onOpenDetails={onOpenDetails}
      />
    );

    expect(screen.getByTestId("commute-answer-success")).toBeTruthy();
    expect(screen.queryByText("起点：")).toBeNull();
    expect(screen.queryByTestId("commute-detail-panel")).toBeNull();

    const canvas = await screen.findByTestId("commute-route-map-canvas");
    expect(canvas.getAttribute("aria-label")).toContain("3 个高德返回的路径点");
    await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());
    expect(calls.polylinePaths[0]).toEqual([
      [115.86, 28.68],
      [115.862, 28.681],
      [115.865, 28.682],
    ]);

    fireEvent.click(screen.getByTestId("commute-route-zoom-in"));
    fireEvent.click(screen.getByTestId("commute-route-zoom-out"));
    expect(calls.zoomIn).toBe(1);
    expect(calls.zoomOut).toBe(1);

    const button = screen.getByTestId("commute-answer-details");
    fireEvent.click(button);
    expect(onOpenDetails).toHaveBeenCalledWith("a-commute", route, button);
  });

  it("路线或轮次变化时重建地图，不沿用旧路径", async () => {
    const calls = installFakeAmap();
    const first = projection();
    const { rerender } = render(
      <CommuteAnswer message={message(first)} conversationId="conversation-1" />
    );
    await waitFor(() => expect(screen.getByTestId("commute-route-map-ready")).toBeTruthy());

    const second = projection({
      polyline: ["115.870000,28.690000", "115.872000,28.691000"],
      resolved_at: "2026-09-25T02:00:00Z",
    });
    rerender(
      <CommuteAnswer message={message(second)} conversationId="conversation-1" />
    );

    await waitFor(() => expect(calls.destroyed).toBe(1));
    await waitFor(() =>
      expect(calls.polylinePaths[calls.polylinePaths.length - 1]).toEqual([
        [115.87, 28.69],
        [115.872, 28.691],
      ])
    );
  });

  it("没有可核验路径点时只说明没有画线，不保留空白地图", async () => {
    installFakeAmap();
    const onOpenDetails = vi.fn();
    render(
      <CommuteAnswer
        message={message(
          projection({
            status: "unverified",
            polyline: [],
            path_verified: false,
            suggested_total_seconds: null,
            evidence_notes: ["本轮没有取得可核验的路径点，因此没有绘制任何路线线。"],
          })
        )}
        conversationId="conversation-1"
        onOpenDetails={onOpenDetails}
      />
    );

    expect(screen.getByTestId("commute-answer-unverified")).toBeTruthy();
    expect(screen.getByTestId("commute-route-map-unavailable").textContent).toContain(
      "路线线"
    );
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
    expect(screen.getByTestId("commute-answer-details")).toBeTruthy();
  });

  it("地图配置不可用时正文仍可读，并给出真实原因和凭据入口", async () => {
    installFakeAmap();
    stubMapConfig({
      configured: false,
      js_api_key: null,
      service_host_path: null,
      security_code_configured: false,
      notice: "未配置高德浏览器地图凭据：请在设置页「高德凭据」中填写后重试。",
    });
    render(
      <CommuteAnswer message={message(projection())} conversationId="conversation-1" />
    );

    await waitFor(() =>
      expect(screen.getByTestId("commute-route-map-unavailable")).toBeTruthy()
    );
    expect(screen.getByTestId("commute-route-map-unavailable").textContent).toContain(
      "设置页"
    );
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
    expect(
      screen
        .getByTestId("commute-route-map-credential-action")
        .getAttribute("href")
    ).toBe("/account/settings/models?return_to=%2Fchat%2Fconversation-1#amap-browser-map");
  });

  it("地图脚本读取失败时显示真实错误，而不是空白区域", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      })
    );
    render(
      <CommuteAnswer message={message(projection())} conversationId="conversation-1" />
    );

    await waitFor(() =>
      expect(screen.getByTestId("commute-route-map-failed").textContent).toContain(
        "地图加载失败"
      )
    );
    expect(screen.getByTestId("commute-route-map-failed").textContent).toContain(
      "文字路线"
    );
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
  });

  it("澄清候选逐项来自高德，未确认校内不被写成已核实", () => {
    installFakeAmap();
    render(
      <CommuteAnswer
        message={message(
          projection({
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
              origin_message_id: "a-commute",
              context: {},
              created_at: "2026-09-25T01:40:00Z",
            },
            distance_m: null,
            base_duration_seconds: null,
            suggested_total_seconds: null,
            steps: [],
            polyline: [],
            path_verified: false,
          })
        )}
        conversationId="conversation-1"
      />
    );

    expect(screen.getByTestId("commute-answer-clarification")).toBeTruthy();
    expect(screen.getByTestId("commute-clarification")).toBeTruthy();
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
    const candidates = screen.getByTestId("commute-route-origin-candidates").textContent ?? "";
    expect(candidates).toContain("南昌市图书馆");
    expect(candidates).toContain("未确认校内");
    expect(candidates).toContain("已确认校内");
    expect(screen.getByText(/续接上一轮校园通勤请求/)).toBeTruthy();
  });

  it("澄清入口可以直接把焦点交还输入框", () => {
    installFakeAmap();
    const composer = document.createElement("textarea");
    composer.id = "composer-input";
    document.body.appendChild(composer);
    render(
      <CommuteAnswer
        message={message(
          projection({
            status: "clarification",
            pending: {
              module_id: "commute",
              kind: "clarification",
              question: "你打算步行、骑车还是骑电动车？",
              origin_message_id: "a-commute",
              context: {},
              created_at: "2026-09-25T01:40:00Z",
            },
            distance_m: null,
            base_duration_seconds: null,
            suggested_total_seconds: null,
            steps: [],
            polyline: [],
            path_verified: false,
          })
        )}
        conversationId="conversation-1"
      />
    );

    fireEvent.click(screen.getByTestId("commute-answer-focus-composer"));
    expect(document.activeElement).toBe(composer);
    composer.remove();
  });

  it("路线失败显示错误码、凭据入口与真实重试；交付状态已有恢复时不重复承诺", () => {
    installFakeAmap();
    const onRetry = vi.fn();
    const failed = projection({
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
    });

    const { unmount } = render(
      <CommuteAnswer
        message={message(failed)}
        conversationId="conversation-1"
        onRetry={onRetry}
      />
    );
    expect(screen.getByTestId("commute-error").textContent).toContain(
      "amap_not_configured"
    );
    expect(screen.getByTestId("commute-error").textContent).toContain(
      "尚未配置高德 Web Service Key"
    );
    expect(
      screen
        .getByTestId("commute-route-credential-action")
        .querySelector("a")
        ?.getAttribute("href")
    ).toBe("/account/settings/models?return_to=%2Fchat%2Fconversation-1#amap-web-service");
    fireEvent.click(screen.getByTestId("commute-answer-retry"));
    expect(onRetry).toHaveBeenCalledWith("a-commute");
    unmount();

    render(
      <CommuteAnswer
        message={message(failed, { turnResult: retryableTurnResult() })}
        conversationId="conversation-1"
        onRetry={onRetry}
      />
    );
    expect(screen.queryByTestId("commute-answer-retry")).toBeNull();
  });

  it("停止状态保留详情入口且不绘制猜测路线", () => {
    installFakeAmap();
    render(
      <CommuteAnswer
        message={message(
          projection({
            status: "stopped",
            distance_m: null,
            base_duration_seconds: null,
            suggested_total_seconds: null,
            steps: [],
            polyline: [],
            path_verified: false,
          })
        )}
        conversationId="conversation-1"
        onOpenDetails={() => {}}
      />
    );

    expect(screen.getByTestId("commute-answer-stopped")).toBeTruthy();
    expect(screen.queryByTestId("commute-route-map-canvas")).toBeNull();
    expect(screen.getByTestId("commute-answer-details")).toBeTruthy();
  });

  it("没有通勤投影时不渲染", () => {
    const { container } = render(
      <CommuteAnswer
        message={{ id: "a-1", role: "assistant", plainText: "普通回答" }}
        conversationId="conversation-1"
      />
    );
    expect(container.textContent).toBe("");
  });
});
