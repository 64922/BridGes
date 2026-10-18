import { screen } from "@testing-library/react";

import type { TurnResultProjection } from "@/lib/api";
import type { ChatMessageView } from "@/lib/chat-message-view";

export function projection(
  overrides: Partial<TurnResultProjection> = {}
): TurnResultProjection {
  return {
    version: "turn-result-v1",
    outcome: "complete",
    outcome_label: "本轮已完成。",
    trust: "qualified",
    trust_label: "结果已通过核验。",
    capability_list: ["paper"],
    actual_module_id: "paper",
    requested_module_id: null,
    route_source: "body_intent",
    delivered: [
      {
        module_id: "paper",
        label: "论文",
        state: "success",
        trust: "qualified",
        detail: "",
      },
    ],
    blocked: [],
    gaps: [],
    recovery: null,
    wait_reason: null,
    task_id: null,
    task_version: null,
    ...overrides,
  };
}

export function message(
  overrides: Partial<ChatMessageView> = {}
): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    attemptNumber: 1,
    plainText: "已完成的正文",
    status: "done",
    turnResult: projection(),
    ...overrides,
  };
}

export function textOf(testId: string): string {
  return screen.getByTestId(testId).textContent ?? "";
}
