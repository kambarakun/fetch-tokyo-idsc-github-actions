#!/usr/bin/env python3
"""Compatibility shim for cleanup-all-zero-data CLI."""

from __future__ import annotations

from src.cli.cleanup_all_zero_data import *  # noqa: F403
from src.cli.cleanup_all_zero_data import main as _cli_main


def _exit_code(value: object) -> int:
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    import warnings

    warnings.warn(
        "scripts/cleanup_all_zero_data.py is deprecated; use 'uv run cleanup-all-zero-data' instead.",
        FutureWarning,
        stacklevel=2,
    )
    raise SystemExit(_exit_code(_cli_main()))
