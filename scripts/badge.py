#!/usr/bin/env python3
"""Emit a shields.io-style flat SVG badge. CLI: badge.py <label> <value> <color> <out.svg>."""

import math
import sys


def width(text: str) -> int:
    return math.ceil(len(text) * 6.5) + 10


def badge(label: str, value: str, color: str) -> str:
    lw, vw = width(label), width(value)
    w = lw + vw
    title = f"{label}: {value}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" '
        f'viewBox="0 0 {w} 20" role="img" aria-label="{title}">\n'
        f"  <title>{title}</title>\n"
        f'  <rect width="{lw}" height="20" rx="3" fill="#555"/>\n'
        f'  <rect x="{lw}" width="{vw}" height="20" rx="3" fill="{color}"/>\n'
        f'  <rect width="{w}" height="20" rx="3" fill="none"/>\n'
        f'  <g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" '
        f'font-size="11" text-anchor="middle">\n'
        f'    <text x="{lw / 2}" y="14">{label}</text>\n'
        f'    <text x="{lw + vw / 2}" y="14">{value}</text>\n'
        f"  </g>\n</svg>\n"
    )


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: badge.py <label> <value> <color> <out.svg>", file=sys.stderr)
        return 1
    _, label, value, color, out = sys.argv
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(badge(label, value, color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
