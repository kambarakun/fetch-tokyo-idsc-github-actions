"""Integration tests for validators using actual data."""

from pathlib import Path

import pytest

from src.validators.gender_sum_validator import GenderSumValidator
from src.validators.quality_validator import QualityValidator


class TestGenderSumValidatorIntegration:
    """Integration tests using actual raw data files."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        """Setup test environment."""
        self.raw_dir = Path("data/raw")
        if not self.raw_dir.exists():
            pytest.skip("data/raw directory not found")
        self.validator = GenderSumValidator(self.raw_dir)

    def test_validate_with_actual_data(self) -> None:
        """Test validation with actual sentinel data if available."""
        # Find actual sentinel data files
        medical_district_files = list(self.raw_dir.glob("sentinel_weekly_medical_district_*.csv"))
        health_center_files = list(self.raw_dir.glob("sentinel_weekly_health_center_*.csv"))
        age_files = list(self.raw_dir.glob("sentinel_weekly_age_*.csv"))

        if not (medical_district_files or health_center_files or age_files):
            pytest.skip("No actual sentinel data files found")

        # Test medical district validation
        if medical_district_files:
            test_file = medical_district_files[0]
            result = self.validator.validate(
                test_file.name,
                "sentinel_weekly_medical_district",
            )
            assert result is not None
            assert result["check_type"] == "gender_sum_consistency"
            assert result["validation_status"] in ["completed", "skipped"]
            assert "details" in result
            assert "affected_count" in result["details"]
            assert "truncated" in result["details"]
            assert isinstance(result["details"]["affected_locations"], list)

        # Test health center validation
        if health_center_files:
            test_file = health_center_files[0]
            result = self.validator.validate(
                test_file.name,
                "sentinel_weekly_health_center",
            )
            assert result is not None
            assert result["check_type"] == "gender_sum_consistency"

        # Test age validation
        if age_files:
            test_file = age_files[0]
            result = self.validator.validate(
                test_file.name,
                "sentinel_weekly_age",
            )
            assert result is not None
            assert result["check_type"] == "gender_sum_consistency"

    def test_validate_returns_none_for_non_gender_data(self) -> None:
        """Test that non-gender-split data returns None."""
        result = self.validator.validate(
            "sentinel_weekly_gender_2024_01.csv",
            "sentinel_weekly_gender",
        )
        assert result is None

    def test_validate_returns_none_for_missing_file(self) -> None:
        """Test that missing file returns None."""
        result = self.validator.validate(
            "nonexistent_file.csv",
            "sentinel_weekly_age",
        )
        assert result is None


class TestQualityValidatorIntegration:
    """Integration tests for QualityValidator."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        """Setup test environment."""
        self.raw_dir = Path("data/raw")
        if not self.raw_dir.exists():
            pytest.skip("data/raw directory not found")
        self.validator = QualityValidator(self.raw_dir)

    def test_validate_generates_quality_metadata(self) -> None:
        """Test that quality metadata is generated."""
        # Test with a typical filename
        processing_meta = {
            "source_name": "sentinel_weekly_age_2024_01",
            "source_hash": "abc123",
            "processing_time_seconds": 0.001,
            "gender": "male",
        }

        quality = self.validator.validate(
            "sentinel_weekly_age_2024_01.csv",
            "sentinel_weekly_age",
            processing_meta,
        )

        assert quality is not None
        assert "validation_timestamp" in quality
        assert "validation_status" in quality
        assert quality["validation_status"] in ["completed", "skipped", "failed"]
        assert "issues" in quality
        assert isinstance(quality["issues"], list)

    def test_validate_with_non_gender_data(self) -> None:
        """Test validation with non-gender-split data."""
        processing_meta = {
            "source_name": "sentinel_weekly_gender_2024_01",
            "source_hash": "abc123",
            "processing_time_seconds": 0.001,
            "gender": None,
        }

        quality = self.validator.validate(
            "sentinel_weekly_gender_2024_01.csv",
            "sentinel_weekly_gender",
            processing_meta,
        )

        assert quality is not None
        assert quality["validation_status"] == "completed"
        assert quality["issues"] == []
