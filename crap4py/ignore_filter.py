"""Ignore-comment suppression for gate violations.

Port of crap4dart 8abb64c ("ignore comments: gate-specific granularity")
with Dart's ``//`` comment syntax adapted to Python's ``#``. Two marker
families:

- legacy blanket: ``# crap:ignore`` (line scope) and ``# crap:ignore-file``
  (file scope) suppress every ignorable gate;
- granular: ``# ignore:<gate-id>`` and ``# ignore-file:<gate-id>`` — one id
  per marker, Dart lint style; several markers on one line union; an empty
  id (``# ignore:``) is a blanket; a comma id is ONE unknown id (comma
  lists are inert) and unknown ids match nothing.

Line scope covers the violation's line and the run of whole-line ignore
markers stacked directly above it (the walk stops at the first non-marker
line). File scope reads the first 5 lines. Violations without a line
number are only suppressed by file-scoped markers.

Suppression is opt-in per gate: only gates in :data:`IGNORABLE_GATES`
honor markers. Markers are matched textually on the line, exactly like
upstream — a marker inside a string literal on a violating line suppresses
too (upstream has the same property).
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

#: Legacy blanket line marker (substring match, like upstream; note that a
#: ``crap:ignore-file`` line therefore also carries the line marker).
LEGACY_LINE_MARKER = "crap:ignore"
#: Legacy blanket file marker (substring match within the first 5 lines).
LEGACY_FILE_MARKER = "crap:ignore-file"

#: Granular line rule: one gate id per marker; several markers union.
_LINE_RULE = re.compile(r"#\s*ignore:([^\s#]*)")
#: Granular file rule (first 5 lines of a file).
_FILE_RULE = re.compile(r"#\s*ignore-file:([^\s#]*)")

#: Gates that honor ignore markers — crap4py's hardcoded stand-in for
#: upstream's per-gate ``ignorable: true`` config flag (the flag surface
#: has no config system, so the registry IS the opt-in). Excluded:
#: ``folder-structure`` (directory-level findings nothing line- or
#: file-scoped can address), ``similarity``/``memory`` (informational,
#: exit 0), ``profile``/``skill`` (not gates).
IGNORABLE_GATES = frozenset(
    {
        "banned_imports",
        "class_size",
        "complexity",
        "duplication",
        "file_naming",
        "inline_record_types",
        "magic_constants",
        "mutation",
        "nesting",
        "scattered_constants",
        "string_hazards",
        "test_assertions",
        "unused_code",
        "unused_files",
        "weight_of_class",
    }
)


def filter_ignored(gate_id: str, result, project_root):
    """Drop ``result.violations`` suppressed by ignore markers.

    The shared violation path of every ignorable gate: runs after the
    gate's scan, before printing and exit-code aggregation. Returns
    ``result`` untouched for non-ignorable gates, empty results, or when
    no violation matched a marker.
    """
    if gate_id not in IGNORABLE_GATES or not result.violations:
        return result
    root = Path(project_root)
    sources: dict[str, list[str] | None] = {}
    kept = [v for v in result.violations if not _suppressed(v, gate_id, root, sources)]
    if len(kept) == len(result.violations):
        return result
    return replace(result, violations=kept)


def _suppressed(violation, gate_id: str, root: Path, sources: dict) -> bool:
    target = getattr(violation, "file", violation)
    path = Path(target)
    if not path.is_absolute():
        path = root / path
    lines = _file_lines(path, sources)
    if lines is None:
        return False
    if _file_scope_ignores(lines, gate_id):
        return True
    line = getattr(violation, "line", None)
    return line is not None and _line_scope_ignores(lines, line, gate_id)


def _file_lines(path: Path, sources: dict) -> list[str] | None:
    key = str(path)
    if key not in sources:
        try:
            sources[key] = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            sources[key] = None
    return sources[key]


def _file_scope_ignores(lines: list[str], gate_id: str) -> bool:
    blanket = False
    ids: set[str] = set()
    for text in lines[:5]:
        if LEGACY_FILE_MARKER in text:
            blanket = True
        marker = _marker_ids(text, _FILE_RULE)
        if marker is None:
            blanket = True
        else:
            ids |= marker
    return blanket or gate_id in ids


def _line_scope_ignores(lines: list[str], line: int, gate_id: str) -> bool:
    if _line_suppresses(_at(lines, line), gate_id):
        return True
    i = line - 1
    while i >= 1:
        above = _at(lines, i)
        if _line_suppresses(above, gate_id):
            return True
        if above is None or not _whole_line_marker(above):
            return False
        i -= 1
    return False


def _line_suppresses(text: str | None, gate_id: str) -> bool:
    return text is not None and (
        LEGACY_LINE_MARKER in text or _matches(_marker_ids(text, _LINE_RULE), gate_id)
    )


def _whole_line_marker(text: str) -> bool:
    trimmed = text.strip()
    return trimmed.startswith("# ignore:") or trimmed.startswith("# ignore-file:")


def _at(lines: list[str], line: int) -> str | None:
    return lines[line - 1] if 1 <= line <= len(lines) else None


def _marker_ids(text: str, rule: re.Pattern) -> frozenset[str] | None:
    """Gate ids carried by one line's markers; ``None`` = blanket (empty id)."""
    ids = set()
    for match in rule.finditer(text):
        id = match.group(1)
        if not id:
            return None
        ids.add(id)
    return frozenset(ids)


def _matches(ids: frozenset[str] | None, gate_id: str) -> bool:
    return ids is None or gate_id in ids
