#!/usr/bin/env python3
"""Compatibility shim for fetch-data CLI."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from src.cli._deprecation import warn_legacy_script_deprecation
    from src.cli.fetch_data import main as _cli_main

    warn_legacy_script_deprecation(
        script_path="scripts/fetch_data.py",
        replacement_command="fetch-data",
    )
    try:
        _cli_main()
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(1)
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
