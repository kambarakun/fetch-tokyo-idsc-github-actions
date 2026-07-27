#!/usr/bin/env python3
"""Compatibility shim for the canonical check-missing CLI."""

from src.cli.check_missing import ContinuityReport, ContinuityValidator, main

__all__ = ["ContinuityReport", "ContinuityValidator", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
