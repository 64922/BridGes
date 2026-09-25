import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StudyProgress } from "./StudyProgress";

describe("学习阶段与页级证据", () => {
  it("刷新后展示等待位置和用户补录来源，阶段不可点击", () => {
    render(
      <StudyProgress
        study={{
          subsection_id: "section-1",
          stage: "awaiting_pages",
          wait_reason: "unclear_page",
          pages: [
            {
              ordinal: 1,
              object_id: "photo-1",
              content_hash: "hash-1",
              model_id: "test-model",
              replaced_object_ids: [],
              fragments: [
                {
                  fragment_id: "user:1",
                  kind: "formula",
                  position: "中部公式",
                  text: "y=ax+b",
                  confidence: 1,
                  source: "user",
                },
              ],
              unclear: [{ position: "右上角", reason: "符号模糊" }],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("书页识别").getAttribute("aria-current")).toBe("step");
    expect(screen.queryByRole("button", { name: "辅导" })).toBeNull();
    expect(screen.getByText(/书页有待补拍/)).toBeTruthy();
    screen.getByText(/已识别书页与证据/).click();
    expect(screen.getByText(/用户补录/)).toBeTruthy();
    expect(screen.getByText(/符号模糊/)).toBeTruthy();
  });
});
