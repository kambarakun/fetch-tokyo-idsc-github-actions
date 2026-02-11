#!/usr/bin/env python3
"""Compatibility shim for check-data-status CLI."""

from __future__ import annotations

from src.cli.check_data_status import *  # noqa: F403
from src.cli.check_data_status import main as _cli_main


def _exit_code(value: object) -> int:
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    import warnings

    warnings.warn(
        "scripts/check_data_status.py is deprecated; use 'uv run check-data-status' instead.",
        FutureWarning,
        stacklevel=2,
    )
    raise SystemExit(_exit_code(_cli_main()))
