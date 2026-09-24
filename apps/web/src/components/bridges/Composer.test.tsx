import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";

const PLACEHOLDER = "输入消息，开始日常对话";

afterEach(() => {
  cleanup();
});

describe("Composer 输入框", () => {
  it.each(["conversation", "new-chat"] as const)(
    "%s 变体显示日常对话占位文案",
    (variant) => {
      render(<Composer variant={variant} onSend={vi.fn()} />);

      expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe(PLACEHOLDER);
    }
  );
});
