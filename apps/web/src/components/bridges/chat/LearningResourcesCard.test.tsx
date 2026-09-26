import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LearningResourcesCard } from "./LearningResourcesCard";
import type { LearningResourcesProjection } from "@/lib/api";

afterEach(cleanup);

function projection(
  overrides: Partial<LearningResourcesProjection> = {}
): LearningResourcesProjection {
  return {
    status: "success",
    original_phrase: "深度学习",
    normalized_term: "深度学习",
    expansions: ["deep learning"],
    confidence: 0.9,
    goal: "入门了解",
    level_label: "零基础入门",
    level_basis: "你说了「零基础」",
    queries: [
      {
        source: "openlibrary",
        query: "深度学习 deep learning",
        status: "success",
        evidence_count: 3,
        retrieved_at: "2026-09-25T10:00:00Z",
        error_code: null,
        error_message: null,
        retryable: false,
      },
      {
        source: "bilibili",
        query: "BV1pu411o7BE",
        status: "success",
        evidence_count: 1,
        retrieved_at: "2026-09-25T10:00:05Z",
        error_code: null,
        error_message: null,
        retryable: false,
      },
    ],
    final_query: "深度学习 deep learning",
    items: [
      {
        order: 1,
        kind: "book",
        title: "深度学习入门：基于Python的理论与实现",
        creator: "斋藤康毅",
        year: 2018,
        source: "openlibrary",
        url: "https://openlibrary.org/works/OL27442600W",
        stage: "入门",
        reason_zh: "作为「入门」阶段的读书主干；书目信息可核对（ISBN 9787115485586）。",
        match_basis: "命中「深度学习」；来源：Open Library 书目。",
        publisher: "人民邮电出版社",
        isbn: "9787115485586",
        duration_seconds: null,
        published_at: null,
        unverified: ["未阅读正文，难度与写法只按书目信息判断"],
      },
      {
        order: 2,
        kind: "video",
        title: "深度学习零基础入门教程",
        creator: "某 UP 主",
        year: 2024,
        source: "bilibili",
        url: "https://www.bilibili.com/video/BV1pu411o7BE",
        stage: "入门",
        reason_zh: "「入门」阶段的视频讲解，元数据取自哔哩哔哩公开接口；未观看，不对讲授质量下结论。",
        match_basis: "命中「深度学习」；来源：哔哩哔哩公开视频页。",
        publisher: null,
        isbn: null,
        duration_seconds: 480,
        published_at: "2024-05-06T00:00:00Z",
        view_count: 1815690,
        like_count: 45780,
        unverified: ["未观看视频，只核对公开元数据（标题、作者、发布时间、时长、简介）"],
      },
    ],
    requested_books: 2,
    requested_videos: 3,
    evidence_notes: ["本轮实际取得 1 本书、1 条视频（目标 2 本 + 3 条）。", "视频还差 2 条。"],
    pending: null,
    searched_at: "2026-09-25T10:00:06Z",
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

describe("LearningResourcesCard（V2 Issue 13）", () => {
  it("成功结果呈现原词、实际查询词、层次与由浅入深的条目和链接", () => {
    render(<LearningResourcesCard resources={projection()} streaming={false} />);

    expect(screen.getByTestId("resources-card-success")).toBeTruthy();
    expect(screen.getByText(/原始说法/).textContent).toContain("深度学习");
    expect(screen.getByText(/实际查询词/).textContent).toContain("深度学习 deep learning");
    expect(screen.getByText(/学习层次/).textContent).toContain("零基础入门");

    const items = screen.getAllByTestId(/resources-item-/);
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("1. [图书] 深度学习入门");
    expect(items[0].textContent).toContain("适用阶段：入门");
    expect(items[1].textContent).toContain("2. [视频] 深度学习零基础入门教程");
    expect(items[1].textContent).toContain("时长 8 分钟");
    // 公开计数如实显示，并标明是平台计数。
    expect(items[1].textContent).toContain("播放 约 181.6 万");
    expect(items[1].textContent).toContain("点赞 约 4.6 万");
    expect(items[1].textContent).toContain("（平台计数）");
    // 未观看的视频必须标明未核实，不描述不可验证的内容。
    expect(items[1].textContent).toContain("未观看视频");

    const link = screen.getByTestId("resources-2-link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://www.bilibili.com/video/BV1pu411o7BE");
  });

  it("条目不足时显示实际数量与证据边界（不凑数量）", () => {
    render(<LearningResourcesCard resources={projection()} streaming={false} />);

    expect(screen.getByTestId("resources-count").textContent).toContain("实际 2 条");
    expect(screen.getByTestId("resources-count").textContent).toContain("目标 2 本 + 3 条");
    expect(screen.getByTestId("resources-notes").textContent).toContain("视频还差 2 条");
  });

  it("每次外部调用都显示来源、真实查询词与结果分类", () => {
    render(<LearningResourcesCard resources={projection()} streaming={false} />);

    const queries = screen.getByTestId("resources-queries").textContent ?? "";
    expect(queries).toContain("Open Library 书目");
    expect(queries).toContain("查询「深度学习 deep learning」");
    expect(queries).toContain("哔哩哔哩公开接口");
    expect(queries).toContain("查询「BV1pu411o7BE」");
  });

  it("失败时显示错误信息与错误码，并可重试", () => {
    const onRetry = vi.fn();
    render(
      <LearningResourcesCard
        resources={projection({
          status: "error",
          items: [],
          error_code: "openlibrary_offline",
          error_message: "当前无法连接 Open Library，请检查网络后重试。",
          retryable: true,
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );

    const card = screen.getByTestId("resources-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("当前无法连接 Open Library");
    expect(card.textContent).toContain("openlibrary_offline");

    fireEvent.click(screen.getByTestId("resources-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("澄清状态显示问题并说明回复会带着资料模块发出", () => {
    render(
      <LearningResourcesCard
        resources={projection({
          status: "clarification",
          items: [],
          pending: {
            module_id: "resources",
            kind: "clarification",
            question: "你现在的学习层次是哪一档？",
            origin_message_id: "a-1",
            context: { missing: "level" },
            created_at: "2026-09-25T10:00:00Z",
          },
        })}
        streaming={false}
      />
    );

    const clarification = screen.getByTestId("resources-clarification");
    expect(clarification.textContent).toContain("你现在的学习层次是哪一档？");
    expect(clarification.textContent).toContain("学习资料推荐");
  });

  it("没有投影时不渲染卡片", () => {
    render(<LearningResourcesCard resources={null} streaming={false} />);
    expect(screen.queryByTestId(/resources-card-/)).toBeNull();
  });
});
