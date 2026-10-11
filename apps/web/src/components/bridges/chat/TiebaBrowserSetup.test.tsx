import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { manageTiebaBrowser } from "@/lib/api";
import { TiebaBrowserSetup } from "./TiebaBrowserSetup";

vi.mock("@/lib/api", () => ({ manageTiebaBrowser: vi.fn() }));
afterEach(cleanup);
beforeEach(() => { vi.mocked(manageTiebaBrowser).mockReset(); });

it("完成人工登录并保存会话后才重新检索", async () => {
  vi.mocked(manageTiebaBrowser).mockResolvedValue({ state: "opened", message: "请完成登录" });
  const retry = vi.fn();
  render(<TiebaBrowserSetup onRetry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "打开专用贴吧浏览器" }));
  await screen.findByRole("button", { name: "已完成，保存并重新检索" });
  expect(retry).not.toHaveBeenCalled();
  vi.mocked(manageTiebaBrowser).mockResolvedValue({ state: "closed", message: "会话已保存" });
  fireEvent.click(screen.getByRole("button", { name: "已完成，保存并重新检索" }));
  await waitFor(() => expect(retry).toHaveBeenCalledOnce());
  expect(manageTiebaBrowser).toHaveBeenLastCalledWith("complete");
});

it("打开失败时给出原因并允许重试", async () => {
  vi.mocked(manageTiebaBrowser).mockImplementation(async () => { throw new Error("Chrome 未安装"); });
  render(<TiebaBrowserSetup />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "打开专用贴吧浏览器" })); });
  expect(screen.getByRole("status").textContent).toContain("Chrome 未安装");
  expect(screen.getByRole("button", { name: "打开专用贴吧浏览器" }).hasAttribute("disabled")).toBe(false);
});
