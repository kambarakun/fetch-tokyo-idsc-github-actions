"""Utilities for deprecating legacy script entrypoints."""

from __future__ import annotations

import warnings
from typing import Final

DEFAULT_REMOVED_ON: Final[str] = "2026-03-15"
DEFAULT_ISSUE_URL: Final[str] = "https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/issues/312"


def build_legacy_script_deprecation_message(
    script_path: str,
    replacement_command: str,
    *,
    removed_on: str = DEFAULT_REMOVED_ON,
    issue_url: str = DEFAULT_ISSUE_URL,
) -> str:
    """Build a standardized deprecation message for legacy script shims."""
    return (
        f"{script_path} is deprecated and will be removed on {removed_on}. "
        f"Use 'uv run {replacement_command}' instead. "
        f"See {issue_url} for migration details."
    )


def warn_legacy_script_deprecation(
    script_path: str,
    replacement_command: str,
    *,
    removed_on: str = DEFAULT_REMOVED_ON,
    issue_url: str = DEFAULT_ISSUE_URL,
    stacklevel: int = 2,
) -> None:
    """Emit a FutureWarning for a deprecated legacy script shim."""
    warnings.warn(
        build_legacy_script_deprecation_message(
            script_path=script_path,
            replacement_command=replacement_command,
            removed_on=removed_on,
            issue_url=issue_url,
        ),
        FutureWarning,
        stacklevel=stacklevel,
    )
