#!/usr/bin/env python3
"""Compatibility shim for the canonical check-missing CLI."""

import sys
from importlib import import_module
from pathlib import Path

# Direct execution starts with scripts/ on sys.path; expose the repository package without requiring installation.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_canonical = import_module("src.cli.check_missing")
ContinuityReport = _canonical.ContinuityReport
ContinuityValidator = _canonical.ContinuityValidator
main = _canonical.main

__all__ = ["ContinuityReport", "ContinuityValidator", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
