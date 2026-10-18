"use client";

import type { ReactNode } from "react";

import type {
  CareerPlanProjection,
  GithubProjectsProjection,
  LearningResourcesProjection,
} from "@/lib/api";
import {
  isExternalHistoricalHref,
  safeHistoricalHref,
} from "@/lib/historical-results";
import { QueryRecordList } from "../chat/QueryRecordList";

const RESOURCES_STATUS: Record<string, string> = {
  clarification: "等待澄清",
  searching: "检索未完成",
  success: "已完成",
  empty: "没有结果",
  error: "失败",
  stopped: "已停止",
};

const CAREER_STATUS: Record<string, string> = {
  clarification: "等待澄清",
  success: "已完成",
  links_only: "仅取得链接",
  empty: "没有结果",
  error: "失败",
  stopped: "已停止",
};

const GITHUB_STATUS: Record<string, string> = {
  clarification: "等待澄清",
  searching: "检索未完成",
  success: "已完成",
  metadata_only: "仅取得元数据",
  empty: "没有结果",
  error: "失败",
  stopped: "已停止",
};

const CAREER_SOURCE_LABELS: Record<string, string> = {
  job_board: "招聘网站",
  company_site: "企业官网",
  other: "其他来源",
};

const GITHUB_SOURCE_LABELS: Record<string, string> = {
  search: "GitHub 搜索",
  readme: "README",
  metadata: "仓库元数据",
  repo: "仓库",
};

const RESOURCE_SOURCE_LABELS: Record<string, string> = {
  bilibili: "哔哩哔哩",
  douban: "豆瓣",
  web: "网页",
  search: "搜索",
};

function statusLabel(labels: Record<string, string>, status: string): string {
  return labels[status] ?? "历史状态未核实";
}

function HistoricalSection({
  testId,
  title,
  status,
  children,
  details,
}: {
  testId: string;
  title: string;
  status: string;
  children: ReactNode;
  details?: ReactNode;
}) {
  return (
    <section
      data-testid={testId}
      style={{
        marginTop: "var(--space-3)",
        borderTop: "1px solid var(--color-border-subtle)",
        paddingTop: "var(--space-3)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "var(--text-sm)", fontWeight: 600 }}>
          {title}
          <span
            style={{
              marginLeft: "var(--space-2)",
              color: "var(--color-text-tertiary)",
              fontSize: "var(--text-xs)",
              fontWeight: 400,
            }}
          >
            历史结果 · 只读
          </span>
        </h3>
        <span
          data-testid={`${testId}-status`}
          style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-xs)" }}
        >
          {status}
        </span>
      </div>
      <div style={{ marginTop: "var(--space-2)" }}>{children}</div>
      {details ? (
        <details data-testid={`${testId}-details`} style={{ marginTop: "var(--space-2)" }}>
          <summary
            style={{
              cursor: "pointer",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-xs)",
            }}
          >
            查看当时依据与调用记录
          </summary>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
              marginTop: "var(--space-2)",
              fontSize: "var(--text-xs)",
              color: "var(--color-text-secondary)",
            }}
          >
            {details}
          </div>
        </details>
      ) : null}
      <p
        style={{
          margin: "var(--space-2) 0 0",
          color: "var(--color-text-tertiary)",
          fontSize: "var(--text-xs)",
        }}
      >
        该能力已退役。以下内容来自当时保存的结果，页面不会重新执行或自动恢复；外部来源未重新核对。
      </p>
    </section>
  );
}

function HistoryField({ label, children }: { label: string; children: ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <p style={{ margin: 0 }}>
      <strong>{label}：</strong>
      {children}
    </p>
  );
}

function HistoricalLink({
  href,
  children,
}: {
  href: string | null | undefined;
  children: ReactNode;
}) {
  const safeHref = safeHistoricalHref(href);
  if (!safeHref) {
    return (
      <span>
        {children}
        <span style={{ marginLeft: "var(--space-1)", color: "var(--color-text-tertiary)" }}>
          （历史链接不可用）
        </span>
      </span>
    );
  }
  const external = isExternalHistoricalHref(safeHref);
  return (
    <a
      href={safeHref}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer noopener" : undefined}
    >
      {children}
    </a>
  );
}

export function LearningResourcesHistory({
  resources,
}: {
  resources: LearningResourcesProjection;
}) {
  const items = resources.items ?? [];
  const assumptions = resources.assumptions ?? [];
  const details = (
    <>
      <HistoryField label="原始说法">{resources.original_phrase}</HistoryField>
      <HistoryField label="实际查询词">{resources.final_query}</HistoryField>
      <HistoryField label="学习层级">
        {[resources.level_label, resources.level_basis].filter(Boolean).join(" · ")}
      </HistoryField>
      <HistoryField label="学习目的">{resources.goal}</HistoryField>
      <HistoryField label="扩展词">{resources.expansions?.join("、")}</HistoryField>
      <HistoryField label="假设">{assumptions.join("；")}</HistoryField>
      <HistoryField label="路径校验">
        {resources.path_verified ? "当时已校验" : "当时未完成路径校验"}
      </HistoryField>
      <HistoryField label="证据说明">{resources.evidence_notes?.join("；")}</HistoryField>
      <HistoryField label="错误">
        {[resources.error_code, resources.error_message].filter(Boolean).join(" · ")}
      </HistoryField>
      <QueryRecordList
        records={resources.queries ?? []}
        testId="historical-resources-queries"
        sourceLabels={RESOURCE_SOURCE_LABELS}
      />
    </>
  );

  return (
    <HistoricalSection
      testId="historical-resources"
      title="学习资料推荐"
      status={statusLabel(RESOURCES_STATUS, resources.status)}
      details={details}
    >
      {items.length > 0 ? (
        <ol
          data-testid="historical-resources-items"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
          }}
        >
          {items.map((item) => (
            <li key={`${item.order}-${item.title}`}>
              <div style={{ fontWeight: 600 }}>
                <HistoricalLink href={item.url}>{item.title}</HistoricalLink>
                <span style={{ marginLeft: "var(--space-2)", color: "var(--color-text-tertiary)" }}>
                  {item.kind === "book" ? "图书" : "视频"}
                  {item.creator ? ` · ${item.creator}` : ""}
                  {item.year ? ` · ${item.year}` : ""}
                </span>
              </div>
              {item.stage ? <div>{item.stage}</div> : null}
              {item.reason_zh ? <p style={{ margin: 0 }}>{item.reason_zh}</p> : null}
              {item.unverified && item.unverified.length > 0 ? (
                <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
                  未核实：{item.unverified.join("、")}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p style={{ margin: 0 }} data-testid="historical-resources-empty">
          {resources.error_message ??
            (resources.status === "clarification"
              ? "本轮等待补充条件，没有生成资料清单。"
              : resources.status === "stopped"
                ? "本轮已停止，没有保存完成的资料清单。"
                : "本轮没有保存可展示的资料条目。")}
        </p>
      )}
    </HistoricalSection>
  );
}

export function CareerPlanHistory({ plan }: { plan: CareerPlanProjection }) {
  const samples = plan.samples ?? [];
  const advices = plan.advices ?? [];
  const details = (
    <>
      <HistoryField label="原始请求">{plan.original_request}</HistoryField>
      <HistoryField label="岗位目标">
        {[plan.family_title, plan.job_terms?.join("、")].filter(Boolean).join(" · ")}
      </HistoryField>
      <HistoryField label="阶段与地点">
        {[plan.stage, plan.cities?.join("、"), plan.graduation_year]
          .filter(Boolean)
          .join(" · ")}
      </HistoryField>
      <HistoryField label="约束">{plan.constraints?.join("；")}</HistoryField>
      <HistoryField label="经验要求">{plan.experience_hint}</HistoryField>
      <HistoryField label="统计口径">
        {plan.analysis
          ? `当时统计 ${plan.analysis.sample_count} 条样本${
              plan.analysis.published_span ? `，发布时间范围 ${plan.analysis.published_span}` : ""
            }`
          : null}
      </HistoryField>
      <HistoryField label="相邻方向">
        {plan.adjacent_suggestions
          ?.map((item) => `${item.title}：${item.reason}`)
          .join("；")}
      </HistoryField>
      <HistoryField label="剔除记录">
        {plan.rejected?.map((item) => `${item.title}：${item.evidence}`).join("；")}
      </HistoryField>
      <HistoryField label="证据边界">{plan.evidence_boundary?.join("；")}</HistoryField>
      <HistoryField label="错误">
        {[plan.error_code, plan.error_message].filter(Boolean).join(" · ")}
      </HistoryField>
      <QueryRecordList
        records={plan.queries ?? []}
        testId="historical-career-queries"
        sourceLabels={CAREER_SOURCE_LABELS}
      />
    </>
  );

  return (
    <HistoricalSection
      testId="historical-career-plan"
      title="职业规划"
      status={statusLabel(CAREER_STATUS, plan.status)}
      details={details}
    >
      {plan.family_title || plan.topic || plan.original_request ? (
        <div data-testid="historical-career-summary">
          {plan.family_title ? (
            <p style={{ margin: 0, fontWeight: 600 }}>{plan.family_title}</p>
          ) : null}
          {plan.topic && plan.topic !== plan.family_title ? (
            <p style={{ margin: 0 }}>{plan.topic}</p>
          ) : null}
          {plan.original_request ? (
            <p style={{ margin: 0, color: "var(--color-text-secondary)" }}>
              原始请求：{plan.original_request}
            </p>
          ) : null}
        </div>
      ) : null}
      {samples.length > 0 ? (
        <ul
          data-testid="historical-career-samples"
          style={{
            margin: "var(--space-2) 0 0",
            paddingLeft: "var(--space-5)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {samples.map((sample, index) => (
            <li key={`${sample.url}-${index}`}>
              <HistoricalLink href={sample.url}>{sample.title || sample.url}</HistoricalLink>
              <span style={{ marginLeft: "var(--space-2)", color: "var(--color-text-tertiary)" }}>
                {[sample.company, sample.city, sample.salary_raw, sample.source_label]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p style={{ margin: "var(--space-2) 0 0" }} data-testid="historical-career-empty">
          {plan.error_message ??
            plan.empty_reason ??
            (plan.status === "clarification"
              ? "本轮等待补充条件，没有形成岗位清单。"
              : "本轮没有保存可展示的岗位样本。")}
        </p>
      )}
      {advices.length > 0 ? (
        <ul
          data-testid="historical-career-advices"
          style={{
            margin: "var(--space-2) 0 0",
            paddingLeft: "var(--space-5)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {advices.map((advice, index) => (
            <li key={`${advice.title}-${index}`}>
              <strong>{advice.title}</strong>
              {advice.detail ? `：${advice.detail}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </HistoricalSection>
  );
}

export function GithubProjectsHistory({ projects }: { projects: GithubProjectsProjection }) {
  const recommendations = projects.recommendations ?? [];
  const details = (
    <>
      <HistoryField label="原始请求">{projects.original_request}</HistoryField>
      <HistoryField label="核心场景">{projects.scenario}</HistoryField>
      <HistoryField label="必要功能">{projects.features?.join("、")}</HistoryField>
      <HistoryField label="可选功能">{projects.optional_features?.join("、")}</HistoryField>
      <HistoryField label="技术词">{projects.tech_terms?.join("、")}</HistoryField>
      <HistoryField label="约束">
        {[
          projects.constraints?.technical?.length
            ? `技术：${projects.constraints.technical.join("、")}`
            : null,
          projects.constraints?.license?.length
            ? `许可：${projects.constraints.license.join("、")}`
            : null,
          projects.constraints?.runtime?.length
            ? `运行：${projects.constraints.runtime.join("、")}`
            : null,
          projects.constraints?.excluded?.length
            ? `排除：${projects.constraints.excluded.join("、")}`
            : null,
        ]
          .filter(Boolean)
          .join("；")}
      </HistoryField>
      <HistoryField label="剔除记录">
        {projects.rejected?.map((item) => `${item.full_name}：${item.reason}`).join("；")}
      </HistoryField>
      <HistoryField label="错误">
        {[projects.error_code, projects.error_message].filter(Boolean).join(" · ")}
      </HistoryField>
      <QueryRecordList
        records={projects.queries ?? []}
        testId="historical-github-queries"
        sourceLabels={GITHUB_SOURCE_LABELS}
      />
    </>
  );

  return (
    <HistoricalSection
      testId="historical-github-projects"
      title="GitHub 项目推荐"
      status={statusLabel(GITHUB_STATUS, projects.status)}
      details={details}
    >
      {recommendations.length > 0 ? (
        <ol
          data-testid="historical-github-recommendations"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
          }}
        >
          {recommendations.map((item) => (
            <li key={`${item.rank}-${item.full_name}`}>
              <div style={{ fontWeight: 600 }}>
                <HistoricalLink href={item.html_url}>{item.full_name}</HistoricalLink>
                <span style={{ marginLeft: "var(--space-2)", color: "var(--color-text-tertiary)" }}>
                  {item.coverage === "whole" ? "整体项目" : "组件项目"}
                  {item.language ? ` · ${item.language}` : ""}
                </span>
              </div>
              {item.description ? <p style={{ margin: 0 }}>{item.description}</p> : null}
              {item.coverage_note ? <p style={{ margin: 0 }}>{item.coverage_note}</p> : null}
              <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
                必要功能 {item.required_supported_count}/{item.required_feature_count}
                {item.runtime_verified ? " · 当时取得运行证据" : " · 仅静态读取"}
              </p>
            </li>
          ))}
        </ol>
      ) : (
        <p style={{ margin: 0 }} data-testid="historical-github-empty">
          {projects.error_message ??
            projects.empty_reason ??
            (projects.status === "clarification"
              ? "本轮等待补充条件，没有形成仓库清单。"
              : "本轮没有保存可展示的仓库结果。")}
        </p>
      )}
    </HistoricalSection>
  );
}
