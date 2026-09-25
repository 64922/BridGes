"use client";

import type { ModuleQueryRecord } from "@/lib/api";

/** 一次外部调用的结果分类（模块共用一份中文标签）。 */
const QUERY_STATUS_LABELS: Record<string, string> = {
  success: "成功",
  empty: "无结果",
  skipped: "未执行",
  timeout: "超时",
  cancelled: "已取消",
  rate_limited: "被上游限流",
  error: "失败",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 一次外部调用的真实记录：来源、实际查询词、结果分类、取得时间与错误。 */
function QueryRecordRow({
  record,
  sourceLabels,
}: {
  record: ModuleQueryRecord;
  sourceLabels: Record<string, string>;
}) {
  const failed = record.status === "error" || record.status === "timeout";
  return (
    <li style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-secondary)" }}>
      <strong>{sourceLabels[record.source] ?? record.source}</strong>
      {" · 查询「"}
      {record.query}
      {"」 · "}
      {QUERY_STATUS_LABELS[record.status] ?? record.status}
      {`（${record.evidence_count} 条）`}
      {" · "}
      {formatTime(record.retrieved_at)}
      {/* 缓存命中/上游次数/冷却秒数是内部日志（interaction.md §4），不呈现给用户。 */}
      {record.error_message ? `：${record.error_message}` : ""}
    </li>
  );
}

/**
 * 「本次外部调用记录」清单（两个检索模块共用）。
 *
 * 只呈现用户能核对的内容：真实来源、实际查询词、结果分类、证据条数与取得
 * 时间；内部日志（缓存命中、上游请求次数、冷却剩余）一律不进入界面。
 * ``sourceLabels`` 由各模块给出——来源词汇表属于模块自己的领域。
 */
export function QueryRecordList({
  records,
  testId,
  sourceLabels,
}: {
  records: ModuleQueryRecord[];
  testId: string;
  sourceLabels: Record<string, string>;
}) {
  if (records.length === 0) return null;
  return (
    <div>
      <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
        本次外部调用记录
      </p>
      <ul
        data-testid={testId}
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
        {records.map((record, index) => (
          <QueryRecordRow
            key={`${record.source}-${record.query}-${index}`}
            record={record}
            sourceLabels={sourceLabels}
          />
        ))}
      </ul>
    </div>
  );
}
