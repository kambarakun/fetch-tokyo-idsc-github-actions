#!/usr/bin/env python3
"""Compatibility shim for process-data CLI."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    import warnings

    from src.cli.process_data import main as _cli_main

    warnings.warn(
        "scripts/process_data.py is deprecated; use 'uv run process-data' instead.",
        FutureWarning,
        stacklevel=2,
    )
    _cli_main()
    raise SystemExit(0)
else:
    from src.cli import process_data as _impl

    main = _impl.main
    sys.modules[__name__] = _impl
