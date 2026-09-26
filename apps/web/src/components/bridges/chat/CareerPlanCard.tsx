"use client";

import { Icon } from "@/components/design-system/Icon";
import { QueryRecordList } from "./QueryRecordList";
import type {
  CareerAdviceItem,
  CareerPlanProjection,
  CareerQueryPlanItem,
  CareerRejectedSample,
  JobSample,
  SalaryInterval,
} from "@/lib/api";

/** V2 Issue 15：职业规划模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认求职目标",
  success: "职业规划",
  links_only: "只取得岗位链接（未纳入样本）",
  empty: "没有读到可用的公开岗位",
  error: "职业规划未完成",
  stopped: "已停止岗位检索",
};

/** 检索提供方的中文名（来源词汇表属于模块自己的领域）。 */
const QUERY_SOURCE_LABELS: Record<string, string> = {
  tavily: "公网搜索服务",
};

const READ_STATUS_LABELS: Record<string, string> = {
  read: "已读到岗位页",
  partial: "只读到部分内容",
  access_restricted: "访问受限（要求登录或验证），未绕过",
  unrecognized: "页面结构无法解析",
  not_found: "页面不存在或已下线",
  timeout: "读取超时",
  error: "读取失败",
  cancelled: "已取消",
};

const REJECTION_LABELS: Record<string, string> = {
  adjacent: "相邻岗位（单列，不并入样本）",
  city: "城市不符",
  city_unverified: "城市无法核对",
  expired: "已过期或已下线",
  duplicate: "重复岗位",
  not_job: "不是岗位详情页",
  title_mismatch: "岗位名不匹配",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 一条检索计划：来源、实际查询词、筛选条件与理由。 */
function PlanRow({ item }: { item: CareerQueryPlanItem }) {
  return (
    <li data-testid={`career-plan-${item.source}`}>
      <strong>{item.source_label}</strong>
      {" · 查询词：「"}
      {item.query}
      {"」"}
      {item.filters?.length ? ` · 筛选条件：${item.filters.join("；")}` : ""}
      <span style={{ color: "var(--color-text-tertiary)" }}> · {item.reason}</span>
    </li>
  );
}

/** 一个真实读到的岗位样本：要么字段留空，要么来自页面原文。 */
function SampleRow({ sample }: { sample: JobSample }) {
  const requirements = sample.requirements ?? [];
  const skills = sample.skills ?? [];
  return (
    <li
      data-testid={`career-sample-${sample.url}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        paddingBottom: "var(--space-2)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <a href={sample.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600 }}>
        {sample.title || sample.url}
      </a>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        公司：{sample.company ?? "页面未给出"} · 城市：{sample.city ?? "页面未给出"} · 薪资原文：
        {sample.salary_raw ?? "页面未给出"} · 发布日期：
        {sample.published_raw ?? "页面未给出"}
        {sample.published_date ? `（${sample.published_date}）` : ""}
        {sample.experience ? ` · 经验：${sample.experience}` : ""}
        {sample.education ? ` · 学历：${sample.education}` : ""}
      </span>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        来源：{sample.source_label} · 抓取于 {formatTime(sample.retrieved_at)} ·{" "}
        {READ_STATUS_LABELS[sample.read_status] ?? sample.read_status}
      </span>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        岗位匹配依据：{sample.title_evidence} · 城市核对：{sample.city_evidence}
      </span>
      {skills.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)" }}>
          命中的技能关键词：{skills.join("、")}
        </span>
      )}
      {requirements.length > 0 && (
        <ul
          data-testid={`career-sample-${sample.url}-requirements`}
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "disc",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
            fontSize: "var(--text-xs)",
          }}
        >
          {requirements.map((requirement, index) => (
            <li key={`requirement-${index}`}>要求原文：{requirement}</li>
          ))}
        </ul>
      )}
      {sample.error_message && (
        <span style={{ color: "var(--color-status-wait)" }}>
          未取得完整内容：{sample.error_message}
        </span>
      )}
    </li>
  );
}

/** 同一计薪单位下的区间：样本量、地区与薪资原文逐条留痕。 */
function SalaryRow({ interval }: { interval: SalaryInterval }) {
  return (
    <li data-testid={`career-salary-${interval.unit}`}>
      {interval.unit}：{interval.amount_min.toLocaleString("zh-CN")}–
      {interval.amount_max.toLocaleString("zh-CN")}（中位{" "}
      {interval.amount_median.toLocaleString("zh-CN")}；样本 {interval.sample_count} 个；地区：
      {interval.cities?.join("、") || "页面未给出"}）
      {interval.small_sample && (
        <span style={{ color: "var(--color-status-wait)" }}>
          {" "}
          · 样本量偏少，只按本区间读数，不作为市场均值
        </span>
      )}
      {interval.raws?.length ? (
        <span style={{ color: "var(--color-text-tertiary)" }}>
          {" "}
          · 薪资原文：{interval.raws.join("｜")}
        </span>
      ) : null}
    </li>
  );
}

/** 一条建议：证据与推断分开标注，依据逐条列出。 */
function AdviceRow({ advice }: { advice: CareerAdviceItem }) {
  const basis = advice.basis ?? [];
  return (
    <li data-testid={`career-advice-${advice.kind}`}>
      <strong>[{advice.inference ? "推断" : "证据"}]</strong> {advice.title}：{advice.detail}
      {basis.length > 0 && (
        <ul
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "circle",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-tertiary)",
          }}
        >
          {basis.map((item, index) => (
            <li key={`basis-${index}`}>依据：{item}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** 被剔除的候选：分类 + 剔除依据（不静默丢弃）。 */
function RejectedRow({ candidate }: { candidate: CareerRejectedSample }) {
  return (
    <li style={{ color: "var(--color-text-tertiary)" }}>
      {REJECTION_LABELS[candidate.kind] ?? candidate.kind}：{candidate.title || candidate.url}（
      {candidate.evidence}）
    </li>
  );
}

/**
 * 职业规划结果卡（V2 Issue 15）。
 *
 * 只渲染投影里的真实内容：实际执行的检索计划与查询词、公开可读且匹配的岗位
 * 样本（含抓取时间、发布日期、薪资原文、要求与直达链接）、逐条留痕的剔除依据、
 * 按计薪单位分开的薪资区间与样本口径，以及区分证据与推断的建议。拿不到页面时
 * 如实展示「未核实链接」与证据缺口，绝不用模型记忆补足岗位内容，也绝不把
 * 小样本说成全国市场均值。
 */
export function CareerPlanCard({
  plan,
  streaming,
  onRetry,
}: {
  plan: CareerPlanProjection | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  if (!plan) return null;
  const shellStyle: React.CSSProperties = {
    margin: "var(--space-3) 0 0",
    padding: "var(--space-3)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    backgroundColor: "var(--color-bg-secondary)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--space-2)",
    fontSize: "var(--text-sm)",
    color: "var(--color-text-secondary)",
  };
  const failed = plan.status === "error";
  const waiting = plan.status === "clarification";
  const degraded = plan.status === "links_only";
  const samples = plan.samples ?? [];
  const planItems = plan.plan ?? [];
  const queries = plan.queries ?? [];
  const candidateLinks = plan.candidate_links ?? [];
  const rejected = plan.rejected ?? [];
  const advices = plan.advices ?? [];
  const adjacent = plan.adjacent_suggestions ?? [];
  const notes = plan.evidence_boundary ?? [];
  const constraints = plan.constraints ?? [];
  const analysis = plan.analysis ?? null;

  return (
    <section
      data-testid={`career-plan-card-${plan.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming}
      style={{
        ...shellStyle,
        borderColor: failed ? "var(--color-status-error)" : "var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name={failed ? "alert" : waiting ? "info" : "careerPlan"} size={16} aria-hidden />
        <strong style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}>
          {STATUS_TITLES[plan.status] ?? "职业规划"}
        </strong>
        {plan.status === "success" && (
          <span
            data-testid="career-plan-count"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-success-bg)",
              color: "var(--color-status-success)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            样本 {samples.length} 个
          </span>
        )}
        {degraded && (
          <span
            data-testid="career-plan-degraded"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            仅链接
          </span>
        )}
      </div>

      {/* AC1：原始岗位、阶段与城市逐字展示，岗位含糊时先澄清。 */}
      <p style={{ margin: 0 }}>
        目标岗位：
        <strong>{plan.family_title ?? plan.job_terms?.join("、") ?? "未给出"}</strong>
        {plan.family_title && plan.job_terms?.length ? (
          <>
            {" · 岗位锚点（原话）："}
            <strong>{plan.job_terms.join("、")}</strong>
          </>
        ) : null}
        {" · 阶段："}
        {plan.stage ?? "未提出"}
        {plan.graduation_year != null ? `（${plan.graduation_year} 届）` : ""}
        {" · 城市："}
        {plan.cities?.length ? plan.cities.join("、") : "未给出"}
      </p>
      <p style={{ margin: 0 }}>原始请求：{plan.original_request}</p>
      {constraints.length > 0 && (
        <p style={{ margin: 0 }}>其他约束：{constraints.join("、")}</p>
      )}

      {waiting && plan.pending && (
        <div
          data-testid="career-plan-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {plan.pending.question}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)" }}>
            直接在下方回复即可；回复会带着「职业规划」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {plan.error_message ?? "本轮职业规划未完成。"}
          {plan.error_code ? `（错误码：${plan.error_code}）` : ""}
        </p>
      )}

      {plan.empty_reason && <p style={{ margin: 0 }}>{plan.empty_reason}</p>}

      {/* AC3：检索计划分别覆盖公开招聘、企业招聘页与校招页，不访问的字段留空。 */}
      {planItems.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            检索计划（实际执行的查询）
          </p>
          <ul
            data-testid="career-plan-items"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {planItems.map((item) => (
              <PlanRow key={`${item.source}-${item.query}`} item={item} />
            ))}
          </ul>
        </div>
      )}

      <QueryRecordList
        records={queries}
        testId="career-plan-queries"
        sourceLabels={QUERY_SOURCE_LABELS}
      />

      {/* AC2：主样本只含公开可读、岗位与城市都匹配的岗位，字段逐项留痕。 */}
      {samples.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            公开可读且匹配的岗位样本
          </p>
          <ul
            data-testid="career-plan-samples"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
            }}
          >
            {samples.map((sample) => (
              <SampleRow key={sample.url} sample={sample} />
            ))}
          </ul>
        </div>
      )}

      {/* AC4：统计口径逐项标出样本量、日期、地区与计薪单位。 */}
      {analysis && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            技能与薪资分析（只基于上面的样本）
          </p>
          <p style={{ margin: 0 }} data-testid="career-analysis-scope">
            样本口径：{analysis.sample_scope_note}
          </p>
          <p style={{ margin: 0, fontSize: "var(--text-xs)" }}>
            城市构成：
            {analysis.city_composition?.length
              ? analysis.city_composition.map((item) => `${item.city} ${item.count} 个`).join("、")
              : "页面未给出"}{" "}
            · 发布日期范围：{analysis.published_span ?? "页面未给出可用日期"}
          </p>
          {analysis.skill_stats?.length ? (
            <ul
              data-testid="career-skill-stats"
              style={{
                margin: 0,
                paddingLeft: "var(--space-5)",
                listStyle: "disc",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
                fontSize: "var(--text-xs)",
              }}
            >
              {analysis.skill_stats.map((stat) => (
                <li key={stat.term}>
                  {stat.term}：出现于 {stat.count}/{analysis.sample_count} 个样本
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0, fontSize: "var(--text-xs)" }}>
              技能关键词：样本要求原文里没有命中词表内的技能词。
            </p>
          )}
          {analysis.salary_intervals?.length ? (
            <ul
              data-testid="career-salary-intervals"
              style={{
                margin: 0,
                paddingLeft: "var(--space-5)",
                listStyle: "disc",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
                fontSize: "var(--text-xs)",
              }}
            >
              {analysis.salary_intervals.map((interval) => (
                <SalaryRow key={interval.unit} interval={interval} />
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0, fontSize: "var(--text-xs)" }}>
              薪资区间：没有可比较的薪资原文，未形成任何区间。
            </p>
          )}
          {analysis.incomparable_notes?.length ? (
            <ul
              data-testid="career-incomparable-notes"
              style={{
                margin: 0,
                paddingLeft: "var(--space-5)",
                listStyle: "circle",
                fontSize: "var(--text-xs)",
                color: "var(--color-text-tertiary)",
              }}
            >
              {analysis.incomparable_notes.map((note, index) => (
                <li key={`incomparable-${index}`}>未并入区间：{note}</li>
              ))}
            </ul>
          ) : null}
          {analysis.overall_inference_stopped && (
            <p
              data-testid="career-inference-stopped"
              style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}
            >
              样本不足，已停止总体推断：以上只是本轮检索所得，不代表总体市场情况。
            </p>
          )}
        </div>
      )}

      {adjacent.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            相邻岗位建议（单列，未并入上面的样本统计）
          </p>
          <ul
            data-testid="career-adjacent-suggestions"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {adjacent.map((item, index) => (
              <li key={`adjacent-${index}`}>
                {item.title}：{item.reason}
                {item.sample_count > 0
                  ? `（本轮检索到该岗位页面 ${item.sample_count} 个，已按相邻岗位剔除）`
                  : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {advices.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            可执行建议（区分证据与推断）
          </p>
          <ul
            data-testid="career-advices"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {advices.map((advice, index) => (
              <AdviceRow key={`advice-${index}`} advice={advice} />
            ))}
          </ul>
        </div>
      )}

      {rejected.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            已剔除的候选（共 {rejected.length} 条，逐条留痕）
          </p>
          <ul
            data-testid="career-rejected"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {rejected.map((candidate, index) => (
              <RejectedRow key={`${candidate.url}-${index}`} candidate={candidate} />
            ))}
          </ul>
        </div>
      )}

      {candidateLinks.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            未核实的候选链接（未纳入样本）
          </p>
          <ul
            data-testid="career-candidate-links"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {candidateLinks.map((link) => (
              <li key={link.url} style={{ color: "var(--color-text-secondary)" }}>
                <a href={link.url} target="_blank" rel="noreferrer">
                  {link.title || link.url}
                </a>
                <span style={{ color: "var(--color-status-wait)" }}> · {link.note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="career-plan-notes"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {notes.map((note, index) => (
              <li key={`note-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        本轮完成于 {formatTime(plan.completed_at)}
      </p>

      {plan.retryable && onRetry && (
        <button
          type="button"
          data-testid="career-plan-retry"
          onClick={onRetry}
          style={{
            alignSelf: "flex-start",
            minHeight: "var(--target-size)",
            padding: "var(--space-1) var(--space-3)",
            border: "1px solid var(--color-status-error)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "transparent",
            color: "var(--color-status-error)",
            font: "inherit",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <Icon name="retry" size={14} aria-hidden /> 重试职业规划
        </button>
      )}
    </section>
  );
}
