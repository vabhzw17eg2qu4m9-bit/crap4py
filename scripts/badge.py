#!/usr/bin/env python3
"""Emit a shields.io-style flat SVG badge. CLI: badge.py <label> <value> <color> <out.svg>.

Rendering lives in the package (crap4py.badge) so the memory command's
--badge output and this script share one implementation; the CI
commit-badges job runs this after `pip install -e .`.
"""

import sys

from crap4py.badge import badge


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
