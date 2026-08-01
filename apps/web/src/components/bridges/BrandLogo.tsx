interface BrandLogoProps {
  variant?: "horizontal" | "icon";
  theme?: "light" | "dark" | "auto";
  width?: number;
}

/**
 * BridGes 原创品牌标识。
 *
 * 资产位于 /public/brand（SVG 源文件 + PNG 尺寸导出），
 * 清单与设计说明见 docs/design/0003-bridges-brand-assets.md。
 */
export function BrandLogo({ variant = "horizontal", theme = "auto", width }: BrandLogoProps) {
  const defaultWidth = variant === "horizontal" ? 140 : 32;
  const resolvedWidth = width ?? defaultWidth;

  if (theme !== "auto") {
    const src = `/brand/bridges-logo-${variant}${theme === "dark" ? "-dark" : ""}.svg`;
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={src} alt="BridGes" width={resolvedWidth} style={{ height: "auto", display: "block" }} />
    );
  }

  return (
    <span className="bridges-logo" style={{ width: resolvedWidth }}>
      {/* eslint-disable @next/next/no-img-element */}
      <img
        className="bridges-logo-light"
        src={`/brand/bridges-logo-${variant}.svg`}
        alt="BridGes"
        width={resolvedWidth}
      />
      <img
        className="bridges-logo-dark"
        src={`/brand/bridges-logo-${variant}-dark.svg`}
        alt="BridGes"
        width={resolvedWidth}
      />
      {/* eslint-enable @next/next/no-img-element */}
    </span>
  );
}
