import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Menu } from "./Menu";

const triggerRect = {
  left: 220,
  top: 100,
  right: 252,
  bottom: 132,
  width: 32,
  height: 32,
  x: 220,
  y: 100,
  toJSON: () => ({}),
};

const menuRect = {
  left: 0,
  top: 0,
  right: 192,
  bottom: 80,
  width: 192,
  height: 80,
  x: 0,
  y: 0,
  toJSON: () => ({}),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderMenu() {
  vi.spyOn(window, "innerWidth", "get").mockReturnValue(260);
  vi.spyOn(window, "innerHeight", "get").mockReturnValue(200);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.getAttribute("role") === "menu") return menuRect;
    if (this.getAttribute("aria-haspopup") === "menu") return triggerRect;
    return {
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    };
  });

  render(
    createElement(Menu, {
      ariaLabel: "会话操作：测试会话",
      trigger: createElement("span", null, "更多"),
      items: [
        { label: "置顶" },
        { label: "改名" },
        { label: "删除", danger: true },
      ],
    })
  );
}

describe("Menu 浮层定位", () => {
  it("通过 portal 脱离滚动祖先，并在右侧和底部空间不足时回退到视口内", () => {
    renderMenu();

    fireEvent.click(screen.getByRole("button", { name: "会话操作：测试会话" }));

    const menu = screen.getByRole("menu");
    expect(menu.parentElement).toBe(document.body);
    expect(menu.style.position).toBe("fixed");
    expect(menu.style.left).toBe("60px");
    expect(menu.style.top).toBe("12px");
  });

  it("打开后触发器滚动时持续更新浮层位置，并在卸载时清理浮层", () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: "会话操作：测试会话" });
    fireEvent.click(trigger);

    const menu = screen.getByRole("menu");
    triggerRect.top = 24;
    triggerRect.bottom = 56;
    fireEvent.scroll(window);

    expect(menu.style.top).toBe("64px");

    cleanup();
    expect(document.querySelector('[role="menu"]')).toBeNull();
  });
});
