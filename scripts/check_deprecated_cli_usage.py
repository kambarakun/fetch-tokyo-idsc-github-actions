#!/usr/bin/env python3
"""Detect deprecated legacy script usage for issue #312 migration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEPRECATED_SCRIPT_NAMES = (
    "fetch_data",
    "process_data",
    "validate_data",
    "verify_metadata",
    "migrate_metadata",
    "check_data_status",
    "cleanup_all_zero_data",
)

SCAN_GLOBS = (
    ".github/workflows/*.yml",
    "README.md",
    "CLAUDE.md",
    "tests/**/*.py",
    "src/**/*.py",
    "scripts/**/*.py",
)

EXCLUDED_PATHS = {
    Path("scripts/check_deprecated_cli_usage.py"),
}

ALLOWLIST = {
    Path("scripts/generate_charts.py"),
    Path("scripts/check_missing.py"),
}


@dataclass(frozen=True)
class Violation:
    """A deprecated usage hit."""

    path: Path
    line_no: int
    line: str
    pattern: str


def _build_patterns() -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for name in DEPRECATED_SCRIPT_NAMES:
        patterns.extend(
            [
                re.compile(rf"\\bpython(?:3)?\\s+scripts/{name}\\.py\\b"),
                re.compile(rf"\\buv\\s+run\\s+python(?:3)?\\s+scripts/{name}\\.py\\b"),
                re.compile(rf"\\bfrom\\s+scripts\\.{name}\\s+import\\b"),
                re.compile(rf"\\bimport\\s+scripts\\.{name}\\b"),
            ]
        )
    return tuple(patterns)


PATTERNS = _build_patterns()


def _iter_scan_files() -> list[Path]:
    candidates: set[Path] = set()
    root = Path()
    for glob_pattern in SCAN_GLOBS:
        for path in root.glob(glob_pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if rel in EXCLUDED_PATHS or rel in ALLOWLIST:
                continue
            candidates.add(rel)
    return sorted(candidates)


def check_file(path: Path) -> list[Violation]:
    """Return deprecated usage hits from a file."""
    text = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "deprecated-usage: allow" in line:
            continue
        for pattern in PATTERNS:
            if pattern.search(line):
                violations.append(
                    Violation(
                        path=path,
                        line_no=line_no,
                        line=line.strip(),
                        pattern=pattern.pattern,
                    )
                )
    return violations


def run_check() -> list[Violation]:
    """Run full repository scan and return violations."""
    violations: list[Violation] = []
    for path in _iter_scan_files():
        violations.extend(check_file(path))
    return violations


def main() -> int:
    """CLI entrypoint."""
    violations = run_check()
    if not violations:
        print("No deprecated scripts/ usage found for issue #312 targets.")
        return 0

    print("Deprecated scripts/ usage detected (issue #312 targets):")
    for violation in violations:
        print(f"  {violation.path}:{violation.line_no}: {violation.line}")
    print("Use 'uv run <command>' or import from 'src.cli.<module>' instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
