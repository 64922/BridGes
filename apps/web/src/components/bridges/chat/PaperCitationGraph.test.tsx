import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PaperCitationGraph } from "./PaperCitationGraph";

afterEach(cleanup);

const edge = {
  citing_candidate_id: "openalex:w2", cited_candidate_id: "openalex:w1",
  citing_title: "后续论文", cited_title: "种子论文", source: "openalex",
};

describe("真实论文引用图", () => {
  it("没有真实边时不提供图或引用关系入口", () => {
    render(<PaperCitationGraph edges={[]} />);
    expect(screen.queryByTestId("paper-search-citations")).toBeNull();
  });

  it("展开后才绘图，保持引用方向和完整文字列表", () => {
    render(<PaperCitationGraph edges={[edge]} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("后续论文 → 种子论文")).toBeTruthy();
    const details = screen.getByTestId("paper-search-citations") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    const diagram = screen.getByRole("img", { name: "论文引用关系图" });
    expect(diagram.querySelectorAll("g").length).toBe(2);
    expect(diagram.querySelectorAll("path[marker-end]").length).toBe(1);
    expect(diagram.textContent).toContain("后续论文");
    expect(diagram.textContent).toContain("种子论文");
    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(screen.queryByRole("img")).toBeNull();
  });
});
