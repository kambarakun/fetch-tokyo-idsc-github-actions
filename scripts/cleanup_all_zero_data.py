#!/usr/bin/env python3
"""Compatibility shim for cleanup-all-zero-data CLI."""

from __future__ import annotations

from src.cli.cleanup_all_zero_data import *  # noqa: F403
from src.cli.cleanup_all_zero_data import main as _cli_main


def _exit_code(value: object) -> int:
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    from src.cli._deprecation import warn_legacy_script_deprecation

    warn_legacy_script_deprecation(
        script_path="scripts/cleanup_all_zero_data.py",
        replacement_command="cleanup-all-zero-data",
    )
    raise SystemExit(_exit_code(_cli_main()))
