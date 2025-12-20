"""Version parsing utilities.

This module provides utilities for parsing and comparing semantic version strings.
Used across migration scripts to ensure consistent version handling.
"""


def parse_version(version_string: str) -> tuple[int, ...]:
    """Parse semantic version string to tuple for comparison.

    This function converts version strings like "1.2.0" to tuples like (1, 2, 0)
    for correct version comparison. String comparison fails for versions like
    "1.10.0" >= "1.2.0" (returns False incorrectly).

    Args:
        version_string: Semantic version string (e.g., "1.2.0")

    Returns:
        Tuple of integers for comparison (e.g., (1, 2, 0))

    Raises:
        ValueError: If version string cannot be parsed

    Examples:
        >>> parse_version("1.2.0")
        (1, 2, 0)
        >>> parse_version("1.10.0") >= parse_version("1.2.0")
        True
        >>> parse_version("2.0") < parse_version("2.1")
        True
    """
    try:
        return tuple(int(part) for part in version_string.split("."))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid version string: {version_string}") from e
