"""WCAG contrast checker for the BridGes design tokens.

Usage: python scripts/check_contrast.py
Exits non-zero if any required pair fails its threshold.
"""
from __future__ import annotations

import sys


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def contrast(fg: str, bg: str) -> float:
    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# (label, foreground, background, minimum ratio)
PAIRS: list[tuple[str, str, str, float]] = [
    # ---- light theme ----
    ("light text-primary / bg-primary", "#1F1E1A", "#FAF9F5", 4.5),
    ("light text-secondary / bg-primary", "#4A463C", "#FAF9F5", 4.5),
    ("light text-tertiary / bg-primary", "#6B6659", "#FAF9F5", 4.5),
    ("light text-primary / surface", "#1F1E1A", "#FFFFFF", 4.5),
    ("light text-secondary / surface", "#4A463C", "#FFFFFF", 4.5),
    ("light text-tertiary / surface", "#6B6659", "#FFFFFF", 4.5),
    ("light text-secondary / bg-secondary", "#4A463C", "#F2EFE6", 4.5),
    ("light on-accent / accent-primary", "#FFFFFF", "#A84B2A", 4.5),
    ("light on-accent / accent-primary-hover", "#FFFFFF", "#8C3E22", 4.5),
    ("light accent-primary / bg-primary (图标/边框 3:1)", "#A84B2A", "#FAF9F5", 3.0),
    ("light accent-secondary / surface (链接)", "#1D4ED8", "#FFFFFF", 4.5),
    ("light accent-secondary / bg-primary (链接)", "#1D4ED8", "#FAF9F5", 4.5),
    ("light focus-ring / bg-primary (3:1)", "#1D4ED8", "#FAF9F5", 3.0),
    ("light success / success-bg", "#166534", "#DCFCE7", 4.5),
    ("light wait / wait-bg", "#92400E", "#FEF3C7", 4.5),
    ("light error / error-bg", "#B91C1C", "#FEE2E2", 4.5),
    ("light info / info-bg", "#1D4ED8", "#DBEAFE", 4.5),
    ("light unknown / unknown-bg", "#525252", "#F5F5F5", 4.5),
    ("light error / surface", "#B91C1C", "#FFFFFF", 4.5),
    # ---- dark theme ----
    ("dark text-primary / bg-primary", "#F5F2EA", "#201E19", 4.5),
    ("dark text-secondary / bg-primary", "#CFC9B8", "#201E19", 4.5),
    ("dark text-tertiary / bg-primary", "#A8A291", "#201E19", 4.5),
    ("dark text-primary / surface", "#F5F2EA", "#2A2822", 4.5),
    ("dark text-secondary / surface", "#CFC9B8", "#2A2822", 4.5),
    ("dark text-tertiary / surface", "#A8A291", "#2A2822", 4.5),
    ("dark on-accent / accent-primary", "#241207", "#E1936B", 4.5),
    ("dark on-accent / accent-primary-hover", "#241207", "#EAA582", 4.5),
    ("dark accent-primary / bg-primary (图标/边框 3:1)", "#E1936B", "#201E19", 3.0),
    ("dark accent-secondary / surface (链接)", "#93B4FD", "#2A2822", 4.5),
    ("dark accent-secondary / bg-primary (链接)", "#93B4FD", "#201E19", 4.5),
    ("dark focus-ring / bg-primary (3:1)", "#93B4FD", "#201E19", 3.0),
    ("dark success / success-bg", "#86EFAC", "#14532D", 4.5),
    ("dark wait / wait-bg", "#FCD34D", "#713F12", 4.5),
    ("dark error / error-bg", "#FCA5A5", "#7F1D1D", 4.5),
    ("dark info / info-bg", "#93B4FD", "#1E3A8A", 4.5),
    ("dark unknown / unknown-bg", "#D4D4D4", "#404040", 4.5),
    ("dark error / surface", "#FCA5A5", "#2A2822", 4.5),
]


def main() -> int:
    failures = 0
    for label, fg, bg, minimum in PAIRS:
        ratio = contrast(fg, bg)
        ok = ratio >= minimum
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}  {ratio:5.2f}:1 (>= {minimum}:1)  {label}  {fg} on {bg}")
    print(f"\n{len(PAIRS) - failures}/{len(PAIRS)} 对通过对比度检查")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
