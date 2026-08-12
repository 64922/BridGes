import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";

const PLACEHOLDER = "和 BridGes 一起学习，可以搜论文、人味化你的文章、生涯规划或者生成图片或视频";

afterEach(() => {
  cleanup();
});

describe("Composer 输入框", () => {
  it.each(["conversation", "new-chat"] as const)(
    "%s 变体显示统一的能力提示占位文案",
    (variant) => {
      render(<Composer variant={variant} onSend={vi.fn()} />);

      expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe(PLACEHOLDER);
    }
  );
});
