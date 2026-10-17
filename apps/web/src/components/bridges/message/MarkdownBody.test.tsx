import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkdownBody } from "./MarkdownBody";

afterEach(cleanup);

describe("助手连续正文", () => {
  it("渲染标题、段落、单换行、列表、代码与表格", () => {
    const text = [
      "## 小标题",
      "",
      "第一段",
      "换行后仍在同一段",
      "",
      "- 列表一",
      "- 列表二",
      "",
      "```ts",
      "const x = 1;",
      "```",
      "",
      "| 名称 | 值 |",
      "| --- | --- |",
      "| A | 1 |",
    ].join("\n");

    const { container } = render(<MarkdownBody text={text} />);

    expect(
      screen.getByRole("heading", { level: 2, name: "小标题" })
    ).toBeTruthy();
    expect(container.querySelector("p br")).toBeTruthy();
    expect(screen.getByText("列表一").tagName).toBe("LI");
    expect(screen.getByText("const x = 1;").closest("pre")).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByRole("cell", { name: "A" })).toBeTruthy();
  });

  it("流式未闭合标记不崩溃，闭合后回到正确容器", () => {
    const { container, rerender } = render(
      <MarkdownBody text={"开头 **加粗"} streaming />
    );
    expect(container.textContent).toContain("**加粗");

    rerender(<MarkdownBody text={"开头 **加粗** 结束"} streaming />);
    expect(screen.getByText("加粗").tagName).toBe("STRONG");

    rerender(<MarkdownBody text={"```python\nprint(1)"} streaming />);
    expect(screen.getByText("print(1)").closest("pre")).toBeTruthy();

    rerender(
      <MarkdownBody
        text={"```python\nprint(1)\n```\n\n后续段落"}
        streaming={false}
      />
    );
    const paragraph = screen.getByText("后续段落");
    expect(paragraph.tagName).toBe("P");
    expect(paragraph.closest("pre")).toBeNull();
  });

  it("原始 HTML、脚本与危险 URL 不成为可执行内容", () => {
    const { container } = render(
      <MarkdownBody
        text={[
          "<script>window.__markdownXss = 1</script>",
          "",
          "<img src=x onerror=alert(1)>",
          "",
          "[危险链接](javascript:alert(1))",
          "",
          "![远程图](https://example.com/tracker.png)",
        ].join("\n")}
      />
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    }
    expect((window as { __markdownXss?: number }).__markdownXss).toBeUndefined();
  });

  it("空流式正文仍显示光标占位，结束后不显示", () => {
    const { container, rerender } = render(
      <MarkdownBody text="" streaming />
    );
    expect(container.firstElementChild?.className).toContain("streamingEmpty");

    rerender(<MarkdownBody text="" streaming={false} />);
    expect(container.firstElementChild?.className).not.toContain("streaming");
  });
});
