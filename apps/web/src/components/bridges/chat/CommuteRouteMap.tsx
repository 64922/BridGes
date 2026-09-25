"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/design-system/Icon";
import { fetchCommuteMapConfig } from "@/lib/api";

/**
 * 高德 JavaScript API 2.0 的最小运行面：本组件只用底图、折线与起终点标注。
 * 只声明实际用到的成员，避免把第三方 SDK 的整体类型引入应用代码。
 */
interface AmapOverlay {
  readonly overlayKind: "polyline" | "marker";
}
interface AmapMap {
  add(overlays: AmapOverlay | AmapOverlay[]): void;
  zoomIn(): void;
  zoomOut(): void;
  setFitView(overlays?: AmapOverlay[]): void;
  destroy(): void;
}
interface AmapNamespace {
  Map: new (container: HTMLElement, options?: Record<string, unknown>) => AmapMap;
  Polyline: new (options: Record<string, unknown>) => AmapOverlay;
  Marker: new (options: Record<string, unknown>) => AmapOverlay;
}

declare global {
  interface Window {
    AMap?: AmapNamespace;
    // 高德官方代理方案：这里只放同源代理地址，安全密钥始终由后端追加。
    _AMapSecurityConfig?: { serviceHost?: string };
  }
}

const SCRIPT_ELEMENT_ID = "bridges-amap-js-api";
const SCRIPT_BASE_URL = "https://webapi.amap.com/maps";
const LINE_COLOR = "#2563eb";

let scriptPromise: Promise<void> | null = null;

/**
 * 加载高德 JS API 脚本（同一页面只加载一次；更换 Key 后需刷新页面）。
 *
 * Key 必然出现在加载器 URL 里，属于公开字段；安全密钥不经浏览器。
 */
function loadAmapScript(jsApiKey: string): Promise<void> {
  if (window.AMap) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.id = SCRIPT_ELEMENT_ID;
    script.async = true;
    script.src = `${SCRIPT_BASE_URL}?v=2.0&key=${encodeURIComponent(jsApiKey)}`;
    script.onload = () => resolve();
    script.onerror = () => {
      scriptPromise = null;
      reject(new Error("高德地图脚本加载失败"));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

/** 高德路径点串 "lng,lat" → 数字坐标；格式不合法的点丢弃，不补造坐标。 */
function parsePath(points: readonly string[]): [number, number][] {
  const parsed: [number, number][] = [];
  for (const point of points) {
    const [lng, lat] = point.split(",").map((value) => Number(value.trim()));
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      parsed.push([lng, lat]);
    }
  }
  return parsed;
}

type MapState = "loading" | "unavailable" | "ready" | "failed";

/**
 * 校园通勤路线地图（V2 Issue 12）。
 *
 * 只画服务端从高德取得的路径点：少于两个合法点时地图不建、也不标出猜测位置。
 * 底图凭据缺失、安全密钥缺失或脚本加载失败都在原位写明原因并指向设置页；
 * 距离、耗时与文字路段由路线卡其余部分照常呈现。
 */
export function CommuteRouteMap({
  polyline,
  originLabel,
  destinationLabel,
}: {
  polyline: readonly string[];
  originLabel?: string | null;
  destinationLabel?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<AmapMap | null>(null);
  const [state, setState] = useState<MapState>("loading");
  const [notice, setNotice] = useState<string>("");

  const pointCount = parsePath(polyline).length;
  const drawable = pointCount >= 2;
  // 路径点内容键：数组按引用变化会让每次渲染都重建地图，用内容做唯一依据。
  const pathKey = polyline.join("|");
  const originTitle = originLabel ?? "起点";
  const destinationTitle = destinationLabel ?? "终点";

  useEffect(() => {
    if (!drawable) {
      setState("unavailable");
      setNotice("路径点少于两个，无法绘制路线线，也不标出猜测位置。");
      return;
    }
    let disposed = false;
    setState("loading");
    const points = parsePath(pathKey.split("|"));
    void (async () => {
      try {
        const config = await fetchCommuteMapConfig();
        if (disposed) return;
        if (!config.configured || !config.js_api_key) {
          setState("unavailable");
          setNotice(
            config.notice ??
              "未配置高德浏览器地图凭据，地图底图不可用；距离与耗时不受影响。"
          );
          return;
        }
        if (!config.security_code_configured || !config.service_host_path) {
          setState("unavailable");
          setNotice(
            config.notice ??
              "缺少高德地图安全密钥，地图无法加载；请在设置页补填后重试。"
          );
          return;
        }
        window._AMapSecurityConfig = {
          serviceHost: `${window.location.origin}${config.service_host_path}`,
        };
        await loadAmapScript(config.js_api_key);
        if (disposed) return;
        const AMap = window.AMap;
        const container = containerRef.current;
        if (!AMap || !container) {
          setState("failed");
          setNotice("地图脚本已加载，但没有可用的地图对象，请刷新后重试。");
          return;
        }
        const map = new AMap.Map(container, {
          zoom: 16,
          center: points[0],
          viewMode: "2D",
        });
        mapRef.current = map;
        const line = new AMap.Polyline({
          path: points,
          strokeColor: LINE_COLOR,
          strokeWeight: 5,
          strokeOpacity: 0.9,
          lineJoin: "round",
        });
        const originMarker = new AMap.Marker({
          position: points[0],
          title: originTitle,
        });
        const destinationMarker = new AMap.Marker({
          position: points[points.length - 1],
          title: destinationTitle,
        });
        map.add([line, originMarker, destinationMarker]);
        map.setFitView([line, originMarker, destinationMarker]);
        setState("ready");
      } catch (error) {
        if (disposed) return;
        setState("failed");
        setNotice(
          error instanceof Error
            ? `地图加载失败：${error.message}。距离、耗时与文字路段不受影响。`
            : "地图加载失败，距离、耗时与文字路段不受影响。"
        );
      }
    })();
    return () => {
      disposed = true;
      mapRef.current?.destroy();
      mapRef.current = null;
    };
  }, [pathKey, drawable, originTitle, destinationTitle]);

  if (!drawable) {
    return (
      <p
        data-testid="commute-route-map-unavailable"
        style={{ margin: 0, color: "var(--color-status-wait)" }}
      >
        {notice || "没有取得可核验的路径点，因此没有绘制任何路线线。"}
      </p>
    );
  }

  return (
    <div
      data-testid={`commute-route-map-${state}`}
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}
    >
      <div
        ref={containerRef}
        data-testid="commute-route-map-canvas"
        role="img"
        aria-label={`校园通勤路线地图：${originTitle} 到 ${destinationTitle}，共 ${pointCount} 个高德返回的路径点`}
        style={{
          width: "100%",
          height: "15rem",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border)",
          backgroundColor: "var(--color-bg-secondary)",
          overflow: "hidden",
        }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
        }}
      >
        <button
          type="button"
          data-testid="commute-route-zoom-in"
          onClick={() => mapRef.current?.zoomIn()}
          disabled={state !== "ready"}
          aria-label="放大地图"
          title="放大地图"
          style={zoomButtonStyle}
        >
          <Icon name="plus" size={14} aria-hidden />
        </button>
        <button
          type="button"
          data-testid="commute-route-zoom-out"
          onClick={() => mapRef.current?.zoomOut()}
          disabled={state !== "ready"}
          aria-label="缩小地图"
          title="缩小地图"
          style={zoomButtonStyle}
        >
          <Icon name="minus" size={14} aria-hidden />
        </button>
        <span>
          {state === "ready"
            ? `已按高德返回的 ${pointCount} 个路径点画线；可拖拽平移、滚轮缩放。`
            : state === "loading"
              ? "正在加载地图…"
              : notice}
        </span>
      </div>
    </div>
  );
}

const zoomButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  padding: 0,
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-sm)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-secondary)",
  cursor: "pointer",
};
