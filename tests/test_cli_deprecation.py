"""Tests for legacy CLI deprecation utilities."""

from __future__ import annotations

import pytest

from src.cli._deprecation import (
    DEFAULT_ISSUE_URL,
    DEFAULT_REMOVED_ON,
    build_legacy_script_deprecation_message,
    warn_legacy_script_deprecation,
)


def test_build_legacy_script_deprecation_message_defaults() -> None:
    message = build_legacy_script_deprecation_message(
        script_path="scripts/fetch_data.py",
        replacement_command="fetch-data",
    )

    assert "scripts/fetch_data.py is deprecated" in message
    assert DEFAULT_REMOVED_ON in message
    assert "uv run fetch-data" in message
    assert DEFAULT_ISSUE_URL in message


def test_warn_legacy_script_deprecation_with_custom_values() -> None:
    with pytest.warns(FutureWarning) as record:
        warn_legacy_script_deprecation(
            script_path="scripts/process_data.py",
            replacement_command="process-data",
            removed_on="2099-01-01",
            issue_url="https://example.com/issue/312",
        )

    warning_message = str(record[0].message)
    assert "scripts/process_data.py is deprecated" in warning_message
    assert "2099-01-01" in warning_message
    assert "uv run process-data" in warning_message
    assert "https://example.com/issue/312" in warning_message
