import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CareerPlanCard } from "./CareerPlanCard";
import type { CareerPlanProjection, JobSample } from "@/lib/api";

afterEach(cleanup);

function projection(overrides: Partial<CareerPlanProjection> = {}): CareerPlanProjection {
  return {
    status: "success",
    topic: "后端开发",
    original_request: "我想找 Java 后端开发的工作，城市南昌",
    job_terms: ["Java 后端开发"],
    family_title: "后端开发",
    stage: "应届生",
    graduation_year: 2026,
    cities: ["南昌"],
    constraints: [],
    plan: [
      {
        source: "boss",
        source_label: "公开招聘职位",
        query: "zhipin.com Java 后端开发 南昌 招聘",
        reason: "先从公开招聘职位里取匹配岗位。",
        filters: ["岗位名需匹配目标岗位", "城市需可核对为南昌"],
      },
    ],
    queries: [
      {
        source: "boss",
        query: "zhipin.com Java 后端开发 南昌 招聘",
        status: "success",
        evidence_count: 3,
        retrieved_at: "2026-09-26T02:00:00Z",
        retryable: false,
      },
    ],
    samples: [],
    candidate_links: [],
    rejected: [],
    analysis: null,
    advices: [],
    adjacent_suggestions: [],
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: "2026-09-26T02:00:05Z",
    pending: null,
    ...overrides,
  };
}

const sample: JobSample = {
  url: "https://www.zhipin.com/job_detail/abc.html",
  source: "boss",
  source_label: "公开招聘职位",
  title: "Java后端开发工程师",
  company: "某某科技有限公司",
  city: "南昌",
  salary_raw: "15-25K·15薪",
  published_raw: "2026-09-20",
  published_date: "2026-09-20",
  experience: "应届生",
  education: "本科",
  requirements: ["熟悉 Java 与 Spring Boot", "了解 MySQL 索引优化"],
  skills: ["Java", "Spring Boot", "MySQL"],
  title_evidence: "页面岗位名「Java后端开发工程师」命中目标说法「Java 后端开发」。",
  city_evidence: "页面工作地点为「南昌」，与期望城市一致。",
  retrieved_at: "2026-09-26T02:00:02Z",
  read_status: "read",
  error_code: null,
  error_message: null,
};

describe("CareerPlanCard（V2 Issue 15）", () => {
  it("呈现原始岗位、阶段、城市与实际执行的检索计划", () => {
    render(<CareerPlanCard plan={projection()} streaming={false} />);
    const card = screen.getByTestId("career-plan-card-success");
    expect(card.textContent).toContain("后端开发");
    expect(card.textContent).toContain("Java 后端开发");
    expect(card.textContent).toContain("应届生");
    expect(card.textContent).toContain("南昌");
    expect(card.textContent).toContain("我想找 Java 后端开发的工作，城市南昌");
    const items = screen.getByTestId("career-plan-items");
    expect(items.textContent).toContain("zhipin.com Java 后端开发 南昌 招聘");
    expect(items.textContent).toContain("岗位名需匹配目标岗位");
    expect(screen.getByTestId("career-plan-queries").textContent).toContain(
      "zhipin.com Java 后端开发 南昌 招聘"
    );
  });

  it("岗位样本逐项留痕：抓取时间、发布日期、薪资原文、要求与直达链接", () => {
    render(<CareerPlanCard plan={projection({ samples: [sample] })} streaming={false} />);
    const samples = screen.getByTestId("career-plan-samples");
    expect(samples.textContent).toContain("Java后端开发工程师");
    expect(samples.textContent).toContain("某某科技有限公司");
    expect(samples.textContent).toContain("15-25K·15薪");
    expect(samples.textContent).toContain("2026-09-20");
    expect(samples.textContent).toContain("2026/9/26");
    expect(samples.textContent).toContain("南昌");
    const requirements = screen.getByTestId(
      "career-sample-https://www.zhipin.com/job_detail/abc.html-requirements"
    );
    expect(requirements.textContent).toContain("熟悉 Java 与 Spring Boot");
    const link = screen
      .getByTestId("career-sample-https://www.zhipin.com/job_detail/abc.html")
      .querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://www.zhipin.com/job_detail/abc.html");
  });

  it("分析标出样本口径、计薪单位与小样本警示，不称市场均值", () => {
    render(
      <CareerPlanCard
        plan={projection({
          samples: [sample],
          analysis: {
            sample_count: 1,
            city_composition: [{ city: "南昌", count: 1 }],
            published_span: "2026-09-20 至 2026-09-20",
            skill_stats: [{ term: "Java", count: 1, urls: [sample.url] }],
            salary_intervals: [
              {
                unit: "元/月",
                sample_count: 1,
                amount_min: 15000,
                amount_max: 25000,
                amount_median: 20000,
                cities: ["南昌"],
                raws: ["15-25K·15薪"],
                small_sample: true,
              },
            ],
            incomparable_notes: ["「面议」没有计薪单位，未并入任何区间。"],
            sample_scope_note: "本轮 1 个样本，抓取于 2026-09-26，地区：南昌。",
            small_sample: true,
            overall_inference_stopped: true,
          },
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("career-analysis-scope").textContent).toContain("本轮 1 个样本");
    expect(screen.getByTestId("career-salary-元/月").textContent).toContain("15,000");
    expect(screen.getByTestId("career-salary-元/月").textContent).toContain("元/月");
    expect(screen.getByTestId("career-salary-元/月").textContent).toContain("不作为市场均值");
    expect(screen.getByTestId("career-salary-元/月").textContent).toContain("15-25K·15薪");
    expect(screen.getByTestId("career-incomparable-notes").textContent).toContain("面议");
    expect(screen.getByTestId("career-inference-stopped").textContent).toContain(
      "已停止总体推断"
    );
    expect(screen.getByTestId("career-skill-stats").textContent).toContain("1/1");
  });

  it("建议区分证据与推断，并保留相邻岗位单列建议", () => {
    render(
      <CareerPlanCard
        plan={projection({
          samples: [sample],
          advices: [
            {
              kind: "skill",
              title: "补齐 Spring Boot 项目经历",
              detail: "样本要求里两次提到 Spring Boot。",
              basis: ["熟悉 Java 与 Spring Boot"],
              inference: false,
            },
            {
              kind: "action",
              title: "同步看看相邻岗位",
              detail: "相邻岗位也在招人。",
              basis: [],
              inference: true,
            },
          ],
          adjacent_suggestions: [
            { title: "测试开发工程师", reason: "与目标岗位相邻，单独列出。", sample_count: 2 },
          ],
        })}
        streaming={false}
      />
    );
    const advices = screen.getByTestId("career-advices");
    expect(advices.textContent).toContain("[证据]");
    expect(advices.textContent).toContain("[推断]");
    expect(advices.textContent).toContain("熟悉 Java 与 Spring Boot");
    const adjacent = screen.getByTestId("career-adjacent-suggestions");
    expect(adjacent.textContent).toContain("测试开发工程师");
    expect(adjacent.textContent).toContain("已按相邻岗位剔除");
    expect(screen.getByTestId("career-plan-card-success").textContent).toContain(
      "未并入上面的样本统计"
    );
  });

  it("仅链接降级时标注未核实并保留剔除依据", () => {
    render(
      <CareerPlanCard
        plan={projection({
          status: "links_only",
          samples: [],
          empty_reason: "取得的候选岗位页都未能读到正文，未纳入样本。",
          candidate_links: [
            {
              url: "https://www.zhipin.com/job_detail/xyz.html",
              title: "Java后端开发工程师",
              source: "boss",
              source_label: "公开招聘职位",
              note: "未核实：页面要求登录，未绕过访问限制。",
            },
          ],
          rejected: [
            {
              url: "https://www.zhipin.com/job_detail/data.html",
              title: "数据分析师",
              company: null,
              kind: "adjacent",
              evidence: "岗位名属于相邻岗位，未混入主样本。",
            },
          ],
          evidence_boundary: ["公开招聘职位只取得搜索摘要，未取得岗位页正文。"],
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("career-plan-card-links_only")).toBeTruthy();
    expect(screen.getByTestId("career-plan-degraded").textContent).toContain("仅链接");
    const links = screen.getByTestId("career-candidate-links");
    expect(links.textContent).toContain("未绕过访问限制");
    expect(screen.getByTestId("career-rejected").textContent).toContain("相邻岗位");
    expect(screen.getByTestId("career-plan-notes").textContent).toContain("未取得岗位页正文");
  });

  it("澄清状态呈现待答问题", () => {
    render(
      <CareerPlanCard
        plan={projection({
          status: "clarification",
          samples: [],
          pending: {
            module_id: "career",
            kind: "clarification",
            question: "你想找的是哪个岗位方向？",
            origin_message_id: "a-1",
            context: {},
            created_at: "2026-09-26T02:00:00Z",
          },
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("career-plan-clarification").textContent).toContain(
      "你想找的是哪个岗位方向？"
    );
  });

  it("失败状态如实呈现错误与错误码，可按 retryable 重试", () => {
    const onRetry = vi.fn();
    render(
      <CareerPlanCard
        plan={projection({
          status: "error",
          retryable: true,
          error_code: "career_search_timeout",
          error_message: "搜索服务超时，未取得任何岗位。",
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );
    const card = screen.getByTestId("career-plan-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("搜索服务超时，未取得任何岗位。");
    expect(card.textContent).toContain("career_search_timeout");
    screen.getByTestId("career-plan-retry").click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("停止状态如实呈现，且没有可重试按钮", () => {
    render(
      <CareerPlanCard
        plan={projection({ status: "stopped", samples: [], retryable: false })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("career-plan-card-stopped").textContent).toContain(
      "已停止岗位检索"
    );
    expect(screen.queryByTestId("career-plan-retry")).toBeNull();
  });

  it("没有任何投影时不渲染", () => {
    const { container } = render(<CareerPlanCard plan={null} streaming={false} />);
    expect(container.innerHTML).toBe("");
  });
});
