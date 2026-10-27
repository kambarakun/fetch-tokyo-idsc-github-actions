#!/usr/bin/env python3
"""Scan the files pre-commit passes for hardcoded secrets with gitleaks (issue #736).

The upstream gitleaks hook runs `gitleaks git --pre-commit --staged`, which only reads the index:
under `pre-commit run --all-files` in CI nothing is staged, so it passes without scanning anything.
This wrapper scans exactly the file names pre-commit passes instead. On a commit those are the
staged files, and pre-commit has stashed the unstaged changes, so the working tree holds the
staged content; under `--all-files` they are every tracked text file, data/ included.
`gitleaks dir` takes a single path, so the files are hard-linked (or copied) into a scratch
directory first.

Output never contains a secret value: gitleaks runs with `--redact` and without `--verbose`, and
only the path, line, rule and fingerprint of each finding are printed. Anything other than a clean
scan or a readable report of findings (missing binary, gitleaks error, unreadable report) fails.

Run it through pre-commit, which installs the pinned gitleaks onto PATH:
`uv run pre-commit run gitleaks --all-files`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# gitleaks exits 1 on its own fatal errors too; a distinct code tells "leaks found" apart from them.
LEAKS_EXIT_CODE = 3
CONFIG_FILE = Path(".gitleaks.toml")
IGNORE_FILE = Path(".gitleaksignore")


def _relative_target(name: str) -> Path | None:
    """Return the repo-relative path to place `name` at, or None for paths outside the repo."""
    path = Path(os.path.normpath(name))
    if path.is_absolute() or path.parts[:1] == ("..",):
        return None
    return path


def _place(source: str, destination: Path) -> None:
    """Hard-link instead of copying where possible: --all-files passes gigabytes of data/."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


class ScanError(Exception):
    """The scan did not run or its result cannot be trusted; never reported as clean."""


def _place_files(names: list[str], target: Path) -> int:
    """Place every file under `target` at its repo-relative path; return the total size in bytes."""
    total_bytes = 0
    for name in names:
        relative = _relative_target(name)
        if relative is None or Path(name).is_symlink() or not Path(name).is_file():
            raise ScanError(f"not a regular file inside the repository: {name}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _place(name, destination)
        total_bytes += destination.stat().st_size
    return total_bytes


def _gitleaks_command(gitleaks: str, report: Path) -> list[str]:
    command = [
        gitleaks,
        "dir",
        ".",
        "--no-banner",
        "--redact",
        "--exit-code",
        str(LEAKS_EXIT_CODE),
        "--report-format",
        "json",
        "--report-path",
        str(report),
    ]
    # Explicit absolute paths: the defaults would look inside the scratch copy.
    if CONFIG_FILE.is_file():
        command += ["--config", str(CONFIG_FILE.resolve())]
    if IGNORE_FILE.is_file():
        command += ["--gitleaks-ignore-path", str(IGNORE_FILE.resolve())]
    return command


def _run_gitleaks(gitleaks: str, target: Path, report: Path) -> list[dict[str, Any]]:
    # Run from inside the scratch copy so reported paths and fingerprints are repo-relative,
    # which is what .gitleaksignore entries match.
    result = subprocess.run(
        _gitleaks_command(gitleaks, report), cwd=target, capture_output=True, text=True, check=False
    )
    if result.returncode not in (0, LEAKS_EXIT_CODE):
        # gitleaks' own log lines; --redact keeps secret values out of them.
        sys.stderr.write(result.stderr)
        raise ScanError(f"gitleaks failed with exit code {result.returncode}")
    try:
        findings = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ScanError("gitleaks wrote no readable report") from error
    if not isinstance(findings, list) or bool(findings) != (result.returncode == LEAKS_EXIT_CODE):
        raise ScanError(f"gitleaks exit code {result.returncode} does not match its report")
    return findings


def main(argv: list[str] | None = None) -> int:
    names = sys.argv[1:] if argv is None else argv
    if not names:
        print("check_secrets: no files to scan")
        return 0

    try:
        gitleaks = shutil.which("gitleaks")
        if gitleaks is None:
            raise ScanError(
                "gitleaks is not on PATH; run this through pre-commit (uv run pre-commit run gitleaks --all-files)"
            )
        with tempfile.TemporaryDirectory(prefix="check-secrets-") as scratch:
            target = Path(scratch, "target")
            total_bytes = _place_files(names, target)
            findings = _run_gitleaks(gitleaks, target, Path(scratch, "report.json"))
    except ScanError as error:
        print(f"check_secrets: {error}", file=sys.stderr)
        return 2

    if not findings:
        print(f"check_secrets: scanned {len(names)} file(s), {total_bytes} bytes: no secrets found")
        return 0

    for finding in findings:
        print(f"{finding['File']}:{finding['StartLine']}: {finding['RuleID']} (fingerprint {finding['Fingerprint']})")
    print(
        f"check_secrets: {len(findings)} candidate secret(s) in {len({f['File'] for f in findings})} file(s); "
        "values are not shown. Remove the secret, or add a narrow reviewed exception (docs/secret-scanning.md).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
