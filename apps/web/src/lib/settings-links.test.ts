import { describe, expect, it } from "vitest";

import {
  AMAP_BROWSER_MAP_ANCHOR,
  AMAP_WEB_SERVICE_ANCHOR,
  credentialSettingsHref,
  safeChatReturnPath,
} from "./settings-links";

describe("safeChatReturnPath", () => {
  it("接受站内会话路径", () => {
    expect(safeChatReturnPath("/chat/abc-123")).toBe("/chat/abc-123");
  });

  it("拒绝外站与异常形态，只当没有返回路径", () => {
    for (const value of [
      null,
      undefined,
      "",
      "/",
      "/account/settings",
      "https://evil.example/chat/1",
      "//evil.example/chat/1",
      "/chat/",
      "/chat//evil.example",
      "/chat/..\\..\\evil.example",
      "/chat/1?next=https://evil.example",
    ]) {
      expect(safeChatReturnPath(value), `${String(value)} 不应被接受`).toBeNull();
    }
  });
});

describe("credentialSettingsHref", () => {
  it("无原会话时只带分区锚点", () => {
    expect(credentialSettingsHref(AMAP_WEB_SERVICE_ANCHOR)).toBe(
      "/account/settings/models#amap-web-service"
    );
    expect(credentialSettingsHref(AMAP_BROWSER_MAP_ANCHOR, null)).toBe(
      "/account/settings/models#amap-browser-map"
    );
  });

  it("有原会话时把返回路径放在查询串，锚点仍在末尾", () => {
    expect(credentialSettingsHref(AMAP_WEB_SERVICE_ANCHOR, "/chat/abc")).toBe(
      "/account/settings/models?return_to=%2Fchat%2Fabc#amap-web-service"
    );
  });

  it("返回路径不合法时忽略它，不把它拼进链接", () => {
    expect(credentialSettingsHref(AMAP_BROWSER_MAP_ANCHOR, "//evil.example")).toBe(
      "/account/settings/models#amap-browser-map"
    );
  });
});
