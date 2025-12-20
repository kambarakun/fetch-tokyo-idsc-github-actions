"""Quality validator for processed data files.

Coordinates multiple validation checks and generates quality metadata
conforming to schema v1.2.0.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .gender_sum_validator import GenderSumValidator


class QualityValidator:
    """Orchestrates quality validation checks for processed data."""

    def __init__(self, raw_data_dir: Path):
        """Initialize quality validator.

        Args:
            raw_data_dir: Path to raw data directory
        """
        self.raw_data_dir = Path(raw_data_dir)
        self.gender_sum_validator = GenderSumValidator(raw_data_dir)

    def validate(self, source_filename: str, data_type: str, processing_metadata: dict[str, Any]) -> dict[str, Any]:
        """Validate a processed data file and generate quality metadata.

        Args:
            source_filename: Name of the source file (e.g., "sentinel_weekly_age_2024_01.csv")
            data_type: Type of data (e.g., "sentinel_weekly_age")
            processing_metadata: Processing metadata containing gender info, etc.

        Returns:
            Quality metadata dict conforming to v1.2.0 schema
        """
        validation_timestamp = datetime.now(UTC).isoformat()

        # Collect validation issues
        issues = []

        # Run gender sum validation for applicable data types
        gender_result = self.gender_sum_validator.validate(source_filename, data_type)
        if gender_result:
            # Add to issues based on validation status:
            # - "completed" with errors: data has issues, user should know
            # - "failed": validation process failed, user should know
            # - "skipped": validation not applicable, no need to notify
            if gender_result["validation_status"] == "completed":
                if gender_result["details"]["affected_count"] > 0:
                    issues.append(gender_result)
                # If completed with 0 errors, don't add to issues (clean data)
            elif gender_result["validation_status"] == "failed":
                # Validation process failed (e.g., file read error, parsing error)
                # User should be notified about this
                issues.append(gender_result)
            # "skipped" status: validation not applicable, don't add to issues

        # Build quality metadata according to v1.2.0 schema
        return {
            "validation_timestamp": validation_timestamp,
            "validation_status": "completed",  # Overall validation process status
            "issues": issues,
        }
