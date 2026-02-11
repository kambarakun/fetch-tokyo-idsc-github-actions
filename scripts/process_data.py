#!/usr/bin/env python3
"""Compatibility shim for process-data CLI."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from src.cli._deprecation import warn_legacy_script_deprecation
    from src.cli.process_data import main as _cli_main

    warn_legacy_script_deprecation(
        script_path="scripts/process_data.py",
        replacement_command="process-data",
    )
    try:
        _cli_main()
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(1)
else:
    from src.cli import process_data as _impl

    main = _impl.main
    sys.modules[__name__] = _impl
