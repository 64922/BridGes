import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  TiebaPostProjection,
  TiebaRunMetadata,
  TiebaSummaryCitation,
} from "@/lib/api";

import {
  PostRow,
  RunMetadataRow,
  citationLocation,
} from "./TiebaEvidenceRows";

afterEach(cleanup);

const citation: TiebaSummaryCitation = {
  citation_id: "E1",
  url: "https://tieba.baidu.com/p/8123456789",
  reply_id: "reply-3",
  floor: 3,
  posted_at: "2026-09-20 21:14",
  author: "吧友甲",
  nested: false,
  quote: "南区六人间，有空调，热水要刷卡。",
};

const post: TiebaPostProjection = {
  thread_id: "8123456789",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
  read_status: "read",
  pages_read: 1,
  pages_limit: 2,
  pages_complete: false,
  scroll_rounds: 0,
  scroll_rounds_limit: 0,
  total_pages: 3,
  floor_min: 1,
  floor_max: 12,
  replies_obtained: true,
  nested_complete: false,
  nested_incomplete_count: 0,
  replies: [
    {
      floor: 3,
      reply_id: "reply-3",
      parent_reply_id: null,
      temporal_scope: "unknown",
      posted_at: "2026-09-20 21:14",
      author: "吧友甲",
      is_original_poster: false,
      content: "南区六人间，有空调，热水要刷卡。",
      nested: false,
      nested_preview: false,
    },
  ],
  read_error_code: null,
  read_error_message: null,
  discoveries: [
    {
      discovery_type: "reply",
      query: "空调",
      search_page: 2,
      matched_reply: "南区六人间，有空调，热水要刷卡。",
      matched_reply_id: "reply-3",
      matched_author: "吧友甲",
      source: "tieba_browser",
    },
  ],
  retrieved_at: "2026-09-25T02:00:02Z",
};

const metadata: TiebaRunMetadata = {
  contract_version: "tieba-run-metadata-v1",
  run_id: "run-1",
  recipe_version: "tieba-recipe-v3",
  component_versions: { browser_protocol: "tieba-browser-protocol-v2" },
  started_at: "2026-10-18T10:00:00Z",
  completed_at: "2026-10-18T10:00:01.500Z",
  elapsed_ms: 1500,
  request_count: 4,
  search_request_count: 2,
  search_page_count: 3,
  read_request_count: 1,
  official_request_count: 1,
  model_call_count: 3,
  confirmed_post_count: 1,
  read_reply_count: 1,
  source_urls: [post.url],
  unread_scope: ["https://tieba.baidu.com/p/8123456789：只读取第 1 页，页面声明共 3 页。"],
};

describe("贴吧证据行（工单 04）", () => {
  it("引用定位展示发言者，缺失时如实标注", () => {
    expect(citationLocation(citation)).toContain("吧友甲");
    expect(citationLocation(citation)).toContain("第 3 楼");
    expect(citationLocation({ ...citation, nested: true })).toContain(
      "楼中楼（第 3 楼）"
    );
    expect(
      citationLocation({ ...citation, author: null, floor: null, posted_at: null })
    ).toBe("发言者未知 · 楼层未知");
  });

  it("帖子原文区分楼中楼预览、已读进度与父楼层", () => {
    const nestedPost: TiebaPostProjection = {
      ...post,
      nested_complete: false,
      nested_incomplete_count: 1,
      nested_error_message: "第 4 楼楼中楼已读 1/2 页，未确认读完。",
      replies: [
        ...(post.replies ?? []),
        {
          floor: 4,
          reply_id: "reply-4",
          parent_reply_id: null,
          temporal_scope: "unknown",
          posted_at: "2026-08-01 10:00",
          author: "父楼层作者",
          is_original_poster: false,
          content: "北区热水十一点就停了。",
          nested: false,
          nested_preview: false,
          nested_progress: {
            state: "partial",
            total_replies: 17,
            preview_count: 4,
            read_count: 10,
            pages_read: 1,
            pages_limit: 2,
            total_pages: 2,
            complete: false,
            error_code: "tieba_browser_nested_page_limit",
            error_message: "第 4 楼楼中楼已读 1/2 页，未确认读完。",
          },
        },
        {
          floor: 4,
          reply_id: "n5",
          parent_reply_id: "reply-4",
          temporal_scope: "unknown",
          posted_at: "2026-08-05 00:17",
          author: "纠正者",
          is_original_poster: false,
          content: "更正：北区是十一点半停水。",
          nested: true,
          nested_preview: true,
        },
      ],
    };

    render(<PostRow post={nestedPost} />);

    const replies = screen.getByTestId("tieba-post-8123456789-replies");
    expect(replies.textContent).toContain("楼中楼（第 4 楼）");
    expect(replies.textContent).toContain("仅页面预览");
    expect(replies.textContent).toContain("已读部分 · 预览 4 条");
    expect(
      screen.getByTestId("tieba-post-8123456789-nested-boundary").textContent
    ).toContain("未确认读完");
  });

  it("帖子回复保留发言者身份", () => {
    render(<PostRow post={post} />);
    const replies = screen.getByTestId(
      "tieba-post-8123456789-replies"
    );
    expect(replies.textContent).toContain("吧友甲");
    expect(replies.textContent).toContain("第 3 楼");
  });

  it("帖子展示主题与回复两条发现路径", () => {
    render(<PostRow post={post} />);
    const paths = screen.getByTestId("tieba-post-8123456789-discoveries");
    expect(paths.textContent).toContain("回复命中「空调」 · 第 2 页");
  });

  it("运行记录展示请求、模型调用、耗时与未读范围", () => {
    render(<RunMetadataRow metadata={metadata} />);
    const text = screen.getByTestId("tieba-run-metadata").textContent ?? "";
    expect(text).toContain("运行 run-1");
    expect(text).toContain("1.5 秒");
    expect(text).toContain("外部请求 4 次");
    expect(text).toContain("检索 2");
    expect(text).toContain("搜索页 3");
    expect(text).toContain("读取 1");
    expect(text).toContain("模型调用 3 次");
    expect(text).toContain("确认帖子 1 个");
    expect(text).toContain("browser_protocol tieba-browser-protocol-v2");
    expect(text).toContain("只读取第 1 页");
  });
});
