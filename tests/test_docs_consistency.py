#!/usr/bin/env python3
"""
エージェント向け文書 (AGENTS.md / CLAUDE.md) と開発手順書の整合性テスト (issue #729)

旧 CLAUDE.md は 1,500 行まで肥大化し、存在しないコマンドや機能しない確認手順が
長期間残っていた。行数の上限、`uv run` のコマンド名、参照パスの実在、
SHA pin 確認コマンドの動作を固定し、同じドリフトの再発を防ぐ。
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_deprecated_cli_usage import check_file

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
DEVELOPMENT_MD = REPO_ROOT / "docs" / "development.md"
DOCS = (
    AGENTS_MD,
    CLAUDE_MD,
    DEVELOPMENT_MD,
    REPO_ROOT / "docs" / "markdown-style.md",
)

AGENTS_MD_MAX_LINES = 200
CLAUDE_MD_MAX_LINES = 30

# re.ASCII: without it `\w` matches Japanese, so prose such as "uv run で実行" would be
# picked up as a command named "で実行".
UV_RUN_PATTERN = re.compile(r"uv run(?: --(?:locked|no-sync|frozen))* ([\w<>.-]+)", re.ASCII)
UV_RUN_EXTRA_COMMANDS = {"pytest", "pre-commit", "python", "python3"}

INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
LINE_SUFFIX_PATTERN = re.compile(r":\d+(-\d+)?$")
NON_PATH_CHARS = (" ", "*", "<", "{", "$")

SHA_PIN_COMMAND_PREFIX = "git grep -nP '"
SHA_PIN_SHA = "c18668ad3cf93ea998bef934396af7bb5c839dc7"


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _lines_outside_fences(path: Path) -> list[tuple[int, str]]:
    """Return (line number, text) pairs that are not inside fenced code blocks."""
    result: list[tuple[int, str]] = []
    in_fence = False
    for line_no, line in enumerate(_read_lines(path), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append((line_no, line))
    return result


def _project_scripts() -> set[str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return set(tomllib.load(f)["project"]["scripts"])


def _sha_pin_pattern() -> re.Pattern[str]:
    for line in _read_lines(DEVELOPMENT_MD):
        stripped = line.strip()
        if stripped.startswith(SHA_PIN_COMMAND_PREFIX):
            pattern = stripped[len(SHA_PIN_COMMAND_PREFIX) :].split("'", 1)[0]
            return re.compile(pattern)
    raise AssertionError(f"{DEVELOPMENT_MD.relative_to(REPO_ROOT)}: no line starts with {SHA_PIN_COMMAND_PREFIX!r}")


def test_agents_md_stays_within_line_budget():
    # Arrange / Act
    line_count = len(_read_lines(AGENTS_MD))

    # Assert
    assert line_count <= AGENTS_MD_MAX_LINES, f"AGENTS.md: {line_count} lines (max {AGENTS_MD_MAX_LINES})"


def test_claude_md_is_thin_adapter_importing_agents_md():
    # Arrange / Act
    lines = _read_lines(CLAUDE_MD)

    # Assert
    assert "@AGENTS.md" in lines, "CLAUDE.md: no line consisting of only '@AGENTS.md'"
    assert len(lines) <= CLAUDE_MD_MAX_LINES, f"CLAUDE.md: {len(lines)} lines (max {CLAUDE_MD_MAX_LINES})"


def test_documented_uv_run_commands_exist():
    # Arrange
    allowed = _project_scripts() | UV_RUN_EXTRA_COMMANDS
    unknown: list[str] = []

    # Act
    for doc in DOCS:
        for line_no, line in enumerate(_read_lines(doc), start=1):
            for command in UV_RUN_PATTERN.findall(line):
                if not command.startswith("<") and command not in allowed:
                    unknown.append(f"{doc.relative_to(REPO_ROOT)}:{line_no}: uv run {command}")

    # Assert
    assert unknown == [], "unknown uv run commands:\n" + "\n".join(unknown)


def test_documented_repo_paths_exist():
    # Arrange
    root_entries = {entry.name for entry in REPO_ROOT.iterdir()}
    missing: list[str] = []

    # Act
    for doc in DOCS:
        for line_no, line in _lines_outside_fences(doc):
            for code in INLINE_CODE_PATTERN.findall(line):
                if any(char in code for char in NON_PATH_CHARS):
                    continue
                if code.split("/", 1)[0] not in root_entries:
                    continue
                path = LINE_SUFFIX_PATTERN.sub("", code.split("::", 1)[0])
                if not (REPO_ROOT / path).exists():
                    missing.append(f"{doc.relative_to(REPO_ROOT)}:{line_no}: {code}")

    # Assert
    assert missing == [], "documented paths that do not exist:\n" + "\n".join(missing)


def test_sha_pin_check_command_detects_tag_refs():
    # Arrange
    pattern = _sha_pin_pattern()
    tag_refs = ["      - uses: actions/checkout@v4", "        uses: astral-sh/setup-uv@v4"]
    pinned_refs = [
        f"        uses: astral-sh/setup-uv@{SHA_PIN_SHA} # v10.2.0",
        "    uses: ./.github/workflows/_fetch-data-common.yml",
    ]
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))

    # Act
    workflow_hits = [
        f"{workflow.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}"
        for workflow in workflows
        for line_no, line in enumerate(_read_lines(workflow), start=1)
        if pattern.search(line)
    ]

    # Assert
    assert all(pattern.search(line) for line in tag_refs)
    assert not any(pattern.search(line) for line in pinned_refs)
    assert workflows, "no workflow files found"
    assert workflow_hits == [], "unpinned external actions:\n" + "\n".join(workflow_hits)


def test_agents_md_has_no_deprecated_cli_usage():
    # AGENTS.md is outside the SCAN_GLOBS of scripts/check_deprecated_cli_usage.py, so check it here.
    # Act
    violations = check_file(AGENTS_MD)

    # Assert
    assert violations == [], "\n".join(f"AGENTS.md:{v.line_no}: {v.line}" for v in violations)
