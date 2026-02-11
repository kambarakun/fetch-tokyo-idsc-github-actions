#!/usr/bin/env python3
"""Compatibility shim for verify-metadata CLI."""

from __future__ import annotations

from src.cli.verify_metadata import *  # noqa: F403
from src.cli.verify_metadata import main as _cli_main


def _exit_code(value: object) -> int:
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    import warnings

    warnings.warn(
        "scripts/verify_metadata.py is deprecated; use 'uv run verify-metadata' instead.",
        FutureWarning,
        stacklevel=2,
    )
    raise SystemExit(_exit_code(_cli_main()))
