import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CommuteRouteProjection } from "@/lib/api";

import { CommuteDetailPanel } from "./CommuteDetailPanel";

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
        status: "timeout",
        evidence_count: 0,
        retrieved_at: "2026-09-25T01:40:01Z",
        retryable: true,
        error_message: "高德路线规划超时。",
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

afterEach(cleanup);

describe("CommuteDetailPanel", () => {
  it("按需显示完整 POI、长步骤、时间缓冲、调用记录与地图文字等价", () => {
    render(
      <CommuteDetailPanel
        selection={{ messageId: "a-1", route: projection() }}
        onClose={() => {}}
      />
    );

    const panel = screen.getByTestId("commute-detail-panel");
    expect(panel.getAttribute("aria-modal")).toBe("false");
    expect(panel.textContent).toContain("华东交通大学图书馆");
    expect(screen.getByTestId("commute-detail-place-origin").textContent).toContain(
      "已核实校内或校门"
    );
    expect(screen.getByTestId("commute-detail-place-origin").textContent).toContain(
      "「图书馆」"
    );
    expect(screen.getByTestId("commute-detail-step-1").textContent).toContain("向东步行");
    expect(screen.getByTestId("commute-detail-step-2").textContent).toContain("右转进入食堂路");
    expect(screen.getByTestId("commute-detail-map-text").textContent).toContain(
      "3 个路径点"
    );
    expect(screen.getByTestId("commute-detail-map-text").textContent).toContain(
      "文字等价"
    );
    expect(screen.getByTestId("commute-detail-buffer").textContent).toContain(
      "不是实时人流数据"
    );
    expect(screen.getByTestId("commute-detail-time").textContent).toContain(
      "不是保证到达时间"
    );
    expect(screen.getByTestId("commute-detail-queries").textContent).toContain(
      "高德地点检索"
    );
    expect(screen.getByTestId("commute-detail-queries").textContent).toContain(
      "高德路线规划"
    );
    expect(screen.getByTestId("commute-detail-queries").textContent).toContain(
      "高德路线规划超时"
    );
    expect(screen.getByTestId("commute-detail-notes").textContent).toContain(
      "没有拿其他方式的耗时替代"
    );
  });

  it("未确认校内的地点不会被写成已核实", () => {
    render(
      <CommuteDetailPanel
        selection={{
          messageId: "a-1",
          route: projection({
            origin: {
              ...projection().origin!,
              campus_verified: false,
              unverified: ["未实测楼门是否可通行。"],
            },
          }),
        }}
        onClose={() => {}}
      />
    );

    const origin = screen.getByTestId("commute-detail-place-origin").textContent ?? "";
    expect(origin).toContain("未确认校内");
    expect(origin).not.toContain("已核实校内或校门");
    expect(screen.getByTestId("commute-detail-unverified-origin").textContent).toContain(
      "未实测楼门是否可通行"
    );
  });

  it("失败状态显示真实错误与错误码", () => {
    render(
      <CommuteDetailPanel
        selection={{
          messageId: "a-1",
          route: projection({
            status: "error",
            error_code: "amap_timeout",
            error_message: "高德路线规划超时。",
            retryable: true,
          }),
        }}
        onClose={() => {}}
      />
    );

    expect(screen.getByTestId("commute-detail-error").textContent).toContain(
      "高德路线规划超时"
    );
    expect(screen.getByTestId("commute-detail-error").textContent).toContain("amap_timeout");
  });

  it("Esc 与关闭按钮都交给调用方关闭", () => {
    const onClose = vi.fn();
    render(
      <CommuteDetailPanel
        selection={{ messageId: "a-1", route: projection() }}
        onClose={onClose}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("commute-detail-close"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("没有选择时不渲染面板", () => {
    const { container } = render(
      <CommuteDetailPanel selection={null} onClose={() => {}} />
    );
    expect(container.textContent).toBe("");
  });
});
