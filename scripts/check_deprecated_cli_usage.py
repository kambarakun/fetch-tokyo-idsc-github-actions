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
    "docs/**/*.md",
)

EXCLUDED_PATHS = {
    Path("scripts/check_deprecated_cli_usage.py"),
    # 検出ロジック自体のテストでは意図的に違反文字列を含むため除外
    Path("tests/test_check_deprecated_cli_usage.py"),
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
    """7対象shimの旧導線パターンをまとめてコンパイル。

    Note: `\\b` を二重エスケープしていた既存実装は word boundary として機能せず、
    パターン全体がリテラル `\\b...` を要求していたため違反を一切検出できなかった。
    本実装は単一の raw f-string でword boundaryを正しく表現する。
    """
    patterns: list[re.Pattern[str]] = []
    for name in DEPRECATED_SCRIPT_NAMES:
        patterns.extend(
            [
                # `python scripts/X.py` / `uv run python scripts/X.py` / `python3 ...` を統合
                re.compile(rf"(?:\buv\s+run\s+)?\bpython(?:3)?\s+scripts/{name}\.py\b"),
                # `python -m scripts.X` / `uv run python -m scripts.X`
                re.compile(rf"(?:\buv\s+run\s+)?\bpython(?:3)?\s+-m\s+scripts\.{name}\b"),
                # `from scripts.X import` / `import scripts.X`
                re.compile(rf"\bfrom\s+scripts\.{name}\s+import\b"),
                re.compile(rf"\bimport\s+scripts\.{name}\b"),
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
    """Return deprecated usage hits from a file.

    同一行で複数パターンが一致しても1件のみ報告する (重複排除)。
    """
    text = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    reported_lines: set[int] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "deprecated-usage: allow" in line:
            continue
        if line_no in reported_lines:
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
                reported_lines.add(line_no)
                break
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
        print("No deprecated scripts/usage found for issue #312 targets.")
        return 0

    print("Deprecated scripts/usage detected (issue #312 targets):")
    for violation in violations:
        print(f"  {violation.path}:{violation.line_no}: {violation.line}")
    print("Use 'uv run <command>' or import from 'src.cli.<module>' instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
