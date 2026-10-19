import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  TiebaPostProjection,
  TiebaRunMetadata,
  TiebaSummaryCitation,
} from "@/lib/api";

import { PostRow, citationLocation, postReadScope } from "./TiebaEvidenceRows";
import { RunMetadataRow } from "./TiebaRunMetadataRow";

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
      context_only: false,
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
  contract_version: "tieba-run-metadata-v3",
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
  read_reply_count: 3,
  candidate_count: 9,
  usable_candidate_count: 4,
  read_post_count: 1,
  qualified_evidence_count: 1,
  context_only_evidence_count: 1,
  excluded_evidence_count: 2,
  exclusion_reasons: { out_of_time_range: 1, campus_conflict: 1 },
  source_urls: [post.url],
  first_pass_post_count: 2,
  deepened_post_count: 1,
  physical_request_count: 4,
  request_breakdown: [
    {
      request_type: "search",
      purpose: "tieba.search",
      count: 2,
      network_ms: 1200,
      queued_ms: 300,
    },
    {
      request_type: "read",
      purpose: "tieba.read",
      count: 1,
      network_ms: 800,
      queued_ms: 0,
    },
  ],
  stage_timings_ms: { "tieba.search": 1500, "tieba.read": 900 },
  remaining_work_ms: 175000,
  closeout_reasons: {
    search: "completed",
    read: "evidence_sufficient",
    official: "skipped",
  },
  browser_cleanup: {
    count: 1,
    max_ms: 400,
    tolerance_ms: 13000,
    timed_out: 0,
  },
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
          context_only: false,
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
          context_only: false,
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

  it("旧记录没有分页字段时不伪装成 0/0 页成果", () => {
    const legacy = { ...post, pages_read: 0, pages_limit: 0 };
    expect(postReadScope(legacy)).toContain("旧记录未保存分页范围");
    expect(postReadScope(legacy)).not.toContain("0/0");

    render(<PostRow post={legacy} />);
    const text = screen.getByTestId("tieba-post-8123456789").textContent ?? "";
    expect(text).toContain("旧记录未保存分页范围");
    expect(text).not.toContain("已读 0/0 页");
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
    expect(text).toContain("候选 9 条");
    expect(text).toContain("合格证据 1 条");
    expect(text).toContain("仅上下文 1 条");
    expect(text).toContain("时间范围外 1 条");
    expect(text).toContain("明确其他校区 1 条");
    expect(text).toContain("只读取第 1 页");
  });

  it("运行记录展示总结版本、证据与调用计量", () => {
    render(
      <RunMetadataRow
        metadata={{
          ...metadata,
          contract_version: "tieba-run-metadata-v3",
          summary: {
            contract_version: "tieba-summary-run-v1",
            prompt_version: "tieba-summary-v8",
            input_schema_version: "tieba-read-evidence-v4",
            output_schema_version: "tieba-summary-claims-v3",
            read_evidence_count: 10,
            model_evidence_count: 8,
            omitted_evidence_count: 2,
            batch_count: 2,
            succeeded_batch_count: 1,
            failed_batch_count: 1,
            model_call_count: 4,
            claim_count: 3,
            supported_claim_count: 2,
            summary_supported: false,
          },
        }}
      />
    );
    const text = screen.getByTestId("tieba-summary-metrics").textContent ?? "";
    expect(text).toContain("总结提示词 tieba-summary-v8");
    expect(text).toContain("送入证据 8/10 条");
    expect(text).toContain("省略 2 条");
    expect(text).toContain("批次 1/2（失败 1）");
    expect(text).toContain("总结模型调用 4 次");
    expect(text).toContain("观点 2/3");
    expect(text).toContain("仅观点通过核验");
  });

  it("帖子展示排除原因与仅上下文发言", () => {
    render(
      <PostRow
        post={{
          ...post,
          scope_excluded_reasons: { campus_conflict: 1 },
          replies: [
            {
              ...(post.replies?.[0] ?? {
                content: "",
                is_original_poster: false,
                nested: false,
                nested_preview: false,
                context_only: false,
              }),
              temporal_scope: "historical",
              context_only: true,
            },
          ],
        }}
      />
    );

    expect(
      screen.getByTestId("tieba-post-8123456789-scope-exclusions").textContent
    ).toContain("明确其他校区 1 条");
    const replies = screen.getByTestId("tieba-post-8123456789-replies");
    expect(replies.textContent).toContain("时间分类：历史");
    expect(replies.textContent).toContain("仅上下文，不作为引用依据");
  });

  it("运行记录展示请求节奏、阶段耗时、剩余预算与收尾原因", () => {
    render(<RunMetadataRow metadata={metadata} />);
    const text = screen.getByTestId("tieba-run-metadata").textContent ?? "";
    expect(text).toContain("首轮覆盖 2 帖 · 深入 1 帖");
    expect(text).toContain("剩余工作预算 175.0 秒");
    expect(text).toContain(
      "检索（tieba.search） 2 次 · 网络 1.2 秒 · 排队 300 毫秒"
    );
    expect(text).toContain("阶段耗时：检索 1.5 秒 · 读取 900 毫秒");
    expect(text).toContain("收尾：检索 完成 · 读取 证据足够 · 官方 未执行");
    expect(text).toContain(
      "浏览器清理 1 次 · 最大 400 毫秒 · 容差 13.0 秒"
    );
  });
})
