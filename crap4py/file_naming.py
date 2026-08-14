"""``file-naming`` subcommand: flags mechanical source file names.

Generic dumping-ground stems (``utils.py``) and numeric suffixes
(``batch1.py``) usually mean code was split without a domain boundary
— port of the crap4dart ``file_naming`` gate.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .analyzer import _relative_to_root
from .args import UsageErrorParser
from .files import PathLike, expand_paths, find_source_files, is_test_file

GENERIC_STEMS = frozenset(
    {
        "common",
        "core",
        "general",
        "helper",
        "helpers",
        "misc",
        "shared",
        "stuff",
        "temp",
        "tmp",
        "types",
        "util",
        "utils",
        "utilities",
        "utility",
        "various",
    }
)

# Technical stems where trailing digits carry meaning (upstream
# FileNamingGateConfig.defaultAllowedStems) — allowed to end in digits.
ALLOWED_NUMERIC_STEMS = frozenset(
    {
        "aes128",
        "aes192",
        "aes256",
        "arm32",
        "arm64",
        "base32",
        "base64",
        "crc8",
        "crc16",
        "crc32",
        "f16",
        "f32",
        "f64",
        "h264",
        "h265",
        "http2",
        "http3",
        "i18n",
        "i2c",
        "int8",
        "int16",
        "int32",
        "int64",
        "ipv4",
        "ipv6",
        "l10n",
        "a11y",
        "md5",
        "oauth1",
        "oauth2",
        "sha1",
        "sha256",
        "sha384",
        "sha512",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "utf8",
        "utf16",
        "utf32",
        "w3c",
        "webgl2",
        "x509",
        "x86",
        "x64",
    }
)

_NUMERIC_SUFFIX = re.compile(r"[a-z_][0-9]+$")


@dataclass(frozen=True, slots=True)
class NamingViolation:
    """A single mechanical file name finding."""

    file: str
    message: str


@dataclass(frozen=True, slots=True)
class NamingResult:
    """Violations found plus how many files were checked."""

    violations: list[NamingViolation]
    checked: int


def run(argv: list[str], project_root: Path) -> int:
    """Entry point for ``crap4py file-naming [paths...]``. Exit 2 iff violations."""
    args = _build_parser().parse_args(argv)
    files = (
        expand_paths(args.paths, project_root) if args.paths else find_source_files(project_root)
    )
    if not files:
        print("No Python files to check.")
        return 0
    result = check_files(files, project_root)
    for violation in result.violations:
        print(f"{violation.file}: {violation.message}")
    print(summary(result))
    return 2 if result.violations else 0


def check_files(files: Iterable[PathLike], project_root: PathLike) -> NamingResult:
    """Check each file's name, skipping test files and test directories."""
    root = Path(project_root)
    violations: list[NamingViolation] = []
    checked = 0
    for file_path in files:
        p = Path(file_path)
        if is_test_file(p, root):
            continue
        checked += 1
        message = violation_for(p)
        if message is not None:
            violations.append(NamingViolation(_relative_to_root(p, root), message))
    return NamingResult(violations, checked)


def violation_for(file_path: PathLike) -> str | None:
    """Return the violation message for a file name, or None when acceptable."""
    stem = Path(file_path).stem
    lower = stem.lower()
    if lower in GENERIC_STEMS:
        return (
            f'generic name "{stem}.py" — split by domain instead '
            "of accumulating unrelated declarations"
        )
    if _NUMERIC_SUFFIX.search(lower) and lower not in ALLOWED_NUMERIC_STEMS:
        return (
            f'numeric suffix in "{stem}.py" — split by domain instead '
            "of numbered parts (batch1, part2, v2 ...)"
        )
    return None


def summary(result: NamingResult) -> str:
    """One-line summary printed after the violations."""
    if result.violations:
        return f"{len(result.violations)}/{result.checked} files with mechanical names"
    return f"{result.checked} files have domain-meaningful names"


def _build_parser() -> UsageErrorParser:
    parser = UsageErrorParser(
        prog="crap4py file-naming",
        description="Check source file names for mechanical names.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    parser.add_argument(
        "paths", nargs="*", help="explicit files or directories (default: normal selection)"
    )
    return parser
