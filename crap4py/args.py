"""Shared argparse parser whose usage errors exit 1 (exit 2 is reserved for thresholds)."""

from __future__ import annotations

import argparse
import sys


class UsageErrorParser(argparse.ArgumentParser):
    """Usage errors exit 1 (not argparse's default 2, which we reserve for threshold)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(1)
