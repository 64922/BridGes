"use client";

import type { ChatMessageView } from "@/lib/chat-message-view";
import { historicalBodyIsMissing } from "@/lib/historical-results";

import {
  CareerPlanHistory,
  GithubProjectsHistory,
  LearningResourcesHistory,
} from "./HistoricalModuleSections";
import { HistoricalExpression } from "./HistoricalExpression";

/** 退役日常模块与旧表达结果的历史只读入口；活动模块不经过这里。 */
export function HistoricalResults({ message }: { message: ChatMessageView }) {
  const hasRetiredResult =
    Boolean(message.learningResources) ||
    Boolean(message.careerPlan) ||
    Boolean(message.githubProjects) ||
    Boolean(message.humanizer);
  if (!hasRetiredResult) return null;

  return (
    <div data-testid="historical-results">
      {historicalBodyIsMissing(message) ? (
        <p
          data-testid="historical-empty-body-note"
          style={{
            margin: 0,
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-xs)",
          }}
        >
          本轮没有保存可读的自然语言正文，以下为当时的结构化结果。
        </p>
      ) : null}
      {message.learningResources ? (
        <LearningResourcesHistory resources={message.learningResources} />
      ) : null}
      {message.careerPlan ? <CareerPlanHistory plan={message.careerPlan} /> : null}
      {message.githubProjects ? (
        <GithubProjectsHistory projects={message.githubProjects} />
      ) : null}
      {message.humanizer ? <HistoricalExpression result={message.humanizer} /> : null}
    </div>
  );
}
