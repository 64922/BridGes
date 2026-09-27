import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

import { AccountMenu } from "./AccountMenu";
import { AuthProvider } from "@/context/AuthContext";
import type { User } from "@/context/AuthContext";

const USER: User = {
  id: "acct-menu-test",
  username: "桥见知行",
  qq_email: "123456789@qq.com",
  avatar_choice: "bridge",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: "2026-09-27T00:00:00Z",
  updated_at: "2026-09-27T00:00:00Z",
};

/** AuthProvider 挂载即校验会话；只为让 useAuth() 可用，不参与菜单断言。 */
function stubSessionFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        account: USER,
        session: {
          id: "session-1",
          account_id: USER.id,
          created_at: "2026-09-27T00:00:00Z",
          expires_at: "2026-09-27T08:00:00Z",
          revoked_at: null,
        },
        subject: {
          account_id: USER.id,
          session_id: "session-1",
          auth_method: "password",
          device_id: null,
          memberships: [],
        },
      }),
    }))
  );
}

function renderMenu() {
  return render(
    <AuthProvider>
      <AccountMenu user={USER} />
    </AuthProvider>
  );
}

function openMenu() {
  const trigger = screen.getByRole("button", { name: /账户菜单：桥见知行/ });
  fireEvent.click(trigger);
  return screen.getAllByRole("menuitem");
}

describe("账户菜单入口", () => {
  beforeEach(() => {
    push.mockClear();
    stubSessionFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("固定四项顺序：设置、切换账号、个人资料、退出登录", async () => {
    renderMenu();
    const items = openMenu();
    expect(items.map((item) => item.textContent)).toEqual([
      "设置",
      "切换账号",
      "个人资料",
      "退出登录",
    ]);
  });

  it("设置与个人资料落在真实账户页，不指向模板页", async () => {
    renderMenu();
    const items = openMenu();

    fireEvent.click(items[0]);
    expect(push).toHaveBeenCalledWith("/account/settings");

    fireEvent.click(screen.getByRole("button", { name: /账户菜单：桥见知行/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "个人资料" }));
    expect(push).toHaveBeenCalledWith("/account/settings/profile");

    for (const call of push.mock.calls) {
      expect(call[0]).not.toContain("/templates");
    }
  });

  it("菜单里没有密钥直达入口，密钥仍须经设置页", async () => {
    renderMenu();
    const items = openMenu();
    expect(items.some((item) => item.textContent?.includes("密钥"))).toBe(false);
  });
});
