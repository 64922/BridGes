import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  GithubRecommendation,
  HumanizerResultProjection,
  JobSample,
} from "@/lib/api";
import {
  careerPlan,
  githubProjects,
  learningResources,
} from "@/lib/chat-modules.test-support";
import type { ChatMessageView } from "@/lib/chat-message-view";
import { copyTextToClipboard } from "@/lib/clipboard";

import { AttemptHistory } from "./AttemptHistory";
import { AssistantActions } from "./AssistantActions";
import { HistoricalExpression } from "./HistoricalExpression";
import { HistoricalResults } from "./HistoricalResults";
import { AssistantBody } from "./MessageBody";

vi.mock("@/lib/clipboard", () => ({
  copyTextToClipboard: vi.fn(async () => true),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function message(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "",
    status: "done",
    ...overrides,
  };
}

function humanizer(overrides: Partial<HumanizerResultProjection> = {}): HumanizerResultProjection {
  return {
    task_id: "task-1",
    skill_id: "bridges-humanizer",
    skill_version: "1.0.0",
    path: "rewrite",
    genre: null,
    contract: {
      path: "rewrite",
      source_text: "原文",
      allow_assumptions: false,
      allow_first_person: false,
    },
    status: "done",
    repair_attempts: 0,
    writing_call_count: 1,
    process_state: "done",
    output: {
      final_text: "已交付的历史正文",
      edits: [],
      fact_check: [],
      open_questions: [],
      quality_status: "ok",
    },
    article: {
      projection_version: "1",
      audit_version: "1",
      delivery_status: "delivered",
      material_state: "sufficient",
      final_text: "已交付的历史正文",
      fidelity: {
        passed: true,
        blocking_count: 0,
        needs_confirmation_count: 0,
        items: [],
      },
      style_review: {
        finding_count: 0,
        warning_count: 0,
        suggestion_count: 0,
        items: [],
      },
      revision: null,
      evidence: [],
      confirmations: [],
    },
    ...overrides,
  };
}

const sample = {
  url: "https://www.zhipin.com/job_detail/abc.html",
  source: "boss",
  source_label: "公开招聘职位",
  title: "Java 后端开发工程师",
  company: "某某科技",
  city: "南昌",
  salary_raw: "15-25K",
} as JobSample;

const recommendation = {
  rank: 1,
  full_name: "demo/bookswap",
  html_url: "https://github.com/demo/bookswap",
  coverage: "whole",
  coverage_note: "覆盖了全部必要功能，按整体项目呈现。",
  language: "Python",
  required_feature_count: 2,
  required_supported_count: 1,
  runtime_verified: false,
} as GithubRecommendation;

describe("退役结果历史呈现", () => {
  it("资料推荐展示当时条目并拒绝恢复启动入口", () => {
    render(
      <HistoricalResults
        message={message({
          learningResources: learningResources({
            items: [
              {
                order: 1,
                kind: "book",
                title: "深度学习",
                creator: "Ian Goodfellow",
                year: 2016,
                source: "douban",
                url: "https://example.com/book",
                stage: "入门",
                reason_zh: "系统建立基础。",
                match_basis: "标题命中「深度学习」。",
                role: "main",
                purpose_zh: "先建立整体框架。",
                evidence_level: "title",
                read_scope: "目录",
                content_covered: false,
                suitability_basis: "目录覆盖基础概念。",
                unverified: ["未读正文"],
              },
            ],
          }),
        })}
      />
    );

    const section = screen.getByTestId("historical-resources");
    expect(section.textContent).toContain("深度学习");
    expect(section.textContent).toContain("未核实：未读正文");
    expect(screen.getByTestId("historical-resources-status").textContent).toContain(
      "已完成"
    );
    expect(screen.getByTestId("historical-empty-body-note")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /重试|重新生成|开始/ })).toBeNull();
    expect(screen.getByRole("link", { name: "深度学习" }).getAttribute("href")).toBe(
      "https://example.com/book"
    );
  });

  it("职业规划展示岗位样本与建议，失败状态不冒充完成", () => {
    const plan = careerPlan({
      samples: [sample],
      advices: [
        {
          kind: "action",
          title: "补一个项目",
          detail: "用真实项目证明后端能力。",
          inference: false,
          priority: 1,
        },
      ],
    });
    const { rerender } = render(
      <HistoricalResults message={message({ careerPlan: plan })} />
    );

    expect(screen.getByTestId("historical-career-samples").textContent).toContain(
      "Java 后端开发工程师"
    );
    expect(screen.getByTestId("historical-career-advices").textContent).toContain(
      "补一个项目"
    );

    rerender(
      <HistoricalResults
        message={message({
          careerPlan: careerPlan({
            status: "error",
            samples: [],
            error_message: "当时招聘来源读取失败。",
          }),
        })}
      />
    );
    expect(screen.getByTestId("historical-career-plan-status").textContent).toContain(
      "失败"
    );
    expect(screen.getByTestId("historical-career-empty").textContent).toContain(
      "当时招聘来源读取失败。"
    );
    expect(screen.queryByText("已完成")).toBeNull();
  });

  it("GitHub 结果如实标注仅元数据，并保留覆盖依据", () => {
    render(
      <HistoricalResults
        message={message({
          githubProjects: githubProjects({
            status: "metadata_only",
            recommendations: [recommendation],
          }),
        })}
      />
    );

    expect(screen.getByTestId("historical-github-projects-status").textContent).toContain(
      "仅取得元数据"
    );
    const items = screen.getByTestId("historical-github-recommendations");
    expect(items.textContent).toContain("demo/bookswap");
    expect(items.textContent).toContain("必要功能 1/2");
    expect(items.textContent).toContain("仅静态读取");
  });

  it("不安全的旧链接只显示文本，不生成可点击伪协议", () => {
    render(
      <HistoricalResults
        message={message({
          learningResources: learningResources({
            items: [
              {
                order: 1,
                kind: "video",
                title: "旧视频",
                source: "bilibili",
                url: "javascript:alert(1)",
                stage: "入门",
                reason_zh: "当时找到的视频。",
                match_basis: "标题命中。",
                role: "supplement",
                purpose_zh: "补充理解。",
                evidence_level: "title",
                read_scope: "标题",
                content_covered: false,
                suitability_basis: "仅标题。",
              },
            ],
          }),
        })}
      />
    );

    expect(screen.getByText(/历史链接不可用/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /旧视频/ })).toBeNull();
  });
});

describe("旧表达与旧尝试", () => {
  it("未交付候选不进入正文、不被复制，也不提供重试", () => {
    const failed = humanizer({
      status: "error",
      output: { final_text: "违规候选正文", edits: [], fact_check: [], open_questions: [], quality_status: "ok" },
      article: {
        ...humanizer().article!,
        delivery_status: "failed",
        final_text: null,
        fidelity: {
          passed: false,
          blocking_count: 1,
          needs_confirmation_count: 0,
          items: [
            {
              code: "unattributed_claim",
              severity: "blocking",
              category: "无来源新增",
              note: "新增结论没有来源绑定。",
            },
          ],
        },
      },
    });
    render(
      <>
        <AssistantBody message={message({ humanizer: failed })} />
        <HistoricalExpression result={failed} />
        <AssistantActions message={message({ humanizer: failed })} />
      </>
    );

    expect(screen.getByTestId("humanizer-not-delivered")).toBeTruthy();
    expect(screen.queryByText("违规候选正文")).toBeNull();
    expect(screen.getByTestId("humanizer-copy-text").hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "没有可复制正文" }).hasAttribute("disabled")).toBe(
      true
    );
    expect(screen.queryByRole("button", { name: /重试|重新生成/ })).toBeNull();
  });

  it("已交付旧表达只在正文出现一次，复制取得最终正文", () => {
    const delivered = humanizer();
    render(
      <>
        <AssistantBody message={message({ humanizer: delivered })} />
        <HistoricalExpression result={delivered} />
        <AssistantActions message={message({ humanizer: delivered })} />
      </>
    );

    expect(screen.getAllByText("已交付的历史正文")).toHaveLength(1);
    expect(screen.getByTestId("humanizer-delivery-status").textContent).toContain(
      "正文保留在消息正文中"
    );
    fireEvent.click(screen.getByTestId("humanizer-copy-text"));
    expect(copyTextToClipboard).toHaveBeenCalledWith("已交付的历史正文");
  });

  it("旧版结果读取保留既有投影遥测，不携带正文", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<HistoricalExpression result={humanizer({ article: undefined })} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      { body: string },
    ];
    expect(url).toBe("/api/chat/humanizer/events");
    const payload = JSON.parse(init.body) as Record<string, unknown>;
    expect(payload.event).toBe("legacy_read");
    expect(payload.legacy).toBe(true);
    expect(init.body).not.toContain("已交付的历史正文");
  });

  it("旧尝试的正文按尝试保留，不与最新回答混写", () => {
    render(
      <AttemptHistory
        attempts={[
          {
            attemptNumber: 1,
            status: "error",
            errorMessage: "旧失败",
            content: "第一次尝试已经保存的正文",
          },
        ]}
      />
    );

    expect(screen.getByText(/第 1 次尝试（失败）：旧失败/)).toBeTruthy();
    expect(screen.getByTestId("attempt-content-1").textContent).toBe(
      "第一次尝试已经保存的正文"
    );
  });
});
