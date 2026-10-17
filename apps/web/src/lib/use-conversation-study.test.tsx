import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  correctStudyRecord,
  deleteStudyRecord,
  restartStudyConversation,
  retryStudyDiagnosis,
} from "@/lib/api";

import { useConversationStudy } from "./use-conversation-study";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    code: string | null;
    status: number;
    constructor(message: string, code: string | null, status: number) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }
  return {
    ApiError,
    retryStudyDiagnosis: vi.fn(),
    correctStudyRecord: vi.fn(),
    deleteStudyRecord: vi.fn(),
    getStudyRestartOptions: vi.fn(),
    restartStudyConversation: vi.fn(),
  };
});

const load = vi.fn().mockResolvedValue(null);
const reportError = vi.fn();
const announce = vi.fn();
const router = { push: vi.fn() };

function setup() {
  return renderHook(() =>
    useConversationStudy({
      conversationId: "c-1",
      load,
      reportError,
      announce,
      router,
    })
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  load.mockResolvedValue(null);
});

afterEach(cleanup);

describe("学习操作保持原服务调用", () => {
  it("诊断重试调用原服务并刷新权威历史", async () => {
    vi.mocked(retryStudyDiagnosis).mockResolvedValue({} as never);
    const { result } = setup();
    await act(async () => {
      await result.current.retryDiagnosis("task-1");
    });
    expect(retryStudyDiagnosis).toHaveBeenCalledWith("c-1", "task-1");
    expect(load).toHaveBeenCalledWith(true);
    expect(announce).toHaveBeenCalledWith("已重新排队核验");
  });

  it("记录纠正携带记录版本，冲突时保留失败并刷新", async () => {
    vi.mocked(correctStudyRecord).mockRejectedValue(new Error("版本冲突"));
    const { result } = setup();
    let accepted = true;
    await act(async () => {
      accepted = await result.current.correctRecord("record-1", "我的纠正", 3);
    });
    expect(correctStudyRecord).toHaveBeenCalledWith("c-1", "record-1", {
      note: "我的纠正",
      expected_version: 3,
    });
    expect(accepted).toBe(false);
    expect(reportError).toHaveBeenCalledWith("版本冲突");
    expect(load).toHaveBeenCalledWith(true);
  });

  it("删除记录按版本调用原服务", async () => {
    vi.mocked(deleteStudyRecord).mockResolvedValue(undefined);
    const { result } = setup();
    await act(async () => {
      await result.current.deleteRecord("record-1", 4);
    });
    expect(deleteStudyRecord).toHaveBeenCalledWith("c-1", "record-1", 4);
    expect(announce).toHaveBeenCalledWith("已删除学习记录");
  });

  it("续学失败重试复用同一幂等键，成功后跳转新会话", async () => {
    vi.mocked(restartStudyConversation)
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValueOnce({
        conversation: { conversation_id: "c-new" },
      } as never);
    const { result } = setup();
    await act(async () => {
      expect(
        await result.current.restart.onSubmit({
          goalId: null,
          objectIds: ["object-1"],
          message: "继续学习",
        })
      ).toBe(false);
      expect(
        await result.current.restart.onSubmit({
          goalId: null,
          objectIds: ["object-1"],
          message: "继续学习",
        })
      ).toBe(true);
    });
    const calls = vi.mocked(restartStudyConversation).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1][1].idempotency_key).toBe(calls[0][1].idempotency_key);
    expect(router.push).toHaveBeenCalledWith("/chat/c-new");
  });
});
