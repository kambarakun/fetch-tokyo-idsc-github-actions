#!/usr/bin/env python3
"""Compatibility shim for fetch-data CLI."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    import warnings

    from src.cli.fetch_data import main as _cli_main

    warnings.warn(
        "scripts/fetch_data.py is deprecated; use 'uv run fetch-data' instead.",
        FutureWarning,
        stacklevel=2,
    )
    _cli_main()
    raise SystemExit(0)
else:
    # Keep backward compatibility for imports and monkeypatch targets:
    # importing scripts.fetch_data returns the src.cli.fetch_data module object.
    from src.cli import fetch_data as _impl

    DataCollector = _impl.DataCollector
    main = _impl.main
    setup_logging = _impl.setup_logging
    StorageManager = _impl.StorageManager
    EnhancedEpidemicDataFetcher = _impl.EnhancedEpidemicDataFetcher
    ConfigurationManager = _impl.ConfigurationManager
    datetime = _impl.datetime

    sys.modules[__name__] = _impl
