"use client";

import { useEffect, useRef, useState } from "react";
import { manageTiebaBrowser } from "@/lib/api";
import { Button } from "@/components/design-system/Button";

/** 用户手动验证或登录，完成后才释放目录并重试采集。 */
export function TiebaBrowserSetup({ onRetry }: { onRetry?: () => void }) {
  const [state, setState] = useState<"idle" | "opening" | "opened" | "closing">("idle");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function act(action: "open" | "complete") {
    if (state === "opening" || state === "closing") return;
    setState(action === "open" ? "opening" : "closing");
    try {
      const result = await manageTiebaBrowser(action);
      if (!mounted.current) return;
      setState(action === "open" ? "opened" : "idle");
      setNotice(result.message);
      if (action === "complete") onRetry?.();
    } catch (error) {
      if (!mounted.current) return;
      setState(action === "open" ? "idle" : "opened");
      setNotice(error instanceof Error ? error.message : "贴吧窗口操作失败，请重试。");
    }
  }
  const busy = state === "opening" || state === "closing";
  return <div>
    <p>吧内检索需要可用的专用浏览器会话。请手动完成安全验证或登录；会话按 BridGes 账户保存在本机。</p>
    <Button variant="secondary" disabled={busy} onClick={() => void act(state === "opened" ? "complete" : "open")}>
      {state === "opening" ? "正在打开…" : state === "closing" ? "正在保存…" : state === "opened" ? "已完成，保存并重新检索" : "打开专用贴吧浏览器"}
    </Button>
    {notice && <p role="status">{notice}</p>}
  </div>;
}
