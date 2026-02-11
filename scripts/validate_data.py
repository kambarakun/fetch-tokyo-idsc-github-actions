#!/usr/bin/env python3
"""Compatibility shim for validate-data CLI."""

from __future__ import annotations

from src.cli.validate_data import *  # noqa: F403
from src.cli.validate_data import main as _cli_main

if __name__ == "__main__":
    from src.cli._deprecation import warn_legacy_script_deprecation

    warn_legacy_script_deprecation(
        script_path="scripts/validate_data.py",
        replacement_command="validate-data",
    )
    _cli_main()
