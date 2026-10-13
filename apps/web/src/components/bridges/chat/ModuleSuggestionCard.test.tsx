import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModuleSuggestionProjection } from "@/lib/api";
import { ModuleSuggestionCard } from "./ModuleSuggestionCard";

afterEach(cleanup);

describe("日常模块历史建议", () => {
  it.each(["resources", "career", "github"] as const)(
    "退役模块 %s 的历史建议不再显示启动按钮",
    (moduleId) => {
      const suggestion: ModuleSuggestionProjection = {
        module_id: moduleId,
        label: "历史建议",
        reason: "历史原因",
        text: "原始请求",
        needs_disambiguation: false,
      };
      render(<ModuleSuggestionCard suggestion={suggestion} onUse={vi.fn()} />);
      expect(screen.queryByTestId("module-suggestion-use")).toBeNull();
    }
  );
});
