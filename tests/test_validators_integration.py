"""Integration tests for validators using real-shaped raw fixtures.

The fixtures under tests/fixtures/raw are byte copies of data/raw files (Shift_JIS, CRLF),
so these tests neither read nor write the repository's data/ directory.
"""

import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.validators.gender_sum_validator import GenderSumValidator
from src.validators.quality_validator import QualityValidator

RAW_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "raw"


def _copy_raw_fixtures(raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for fixture in RAW_FIXTURES_DIR.glob("*.csv"):
        shutil.copy(fixture, raw_dir / fixture.name)
    return raw_dir


def _write_row_count_mismatch_raw(path: Path) -> None:
    """Write a weekly raw whose male section has one more row than female/total."""
    footer = '"集計期間終了週","x"'
    header = '"","疾病A","疾病B"'
    rows = [
        '"性別","男性"',
        "",
        header,
        '"a","1","2"',
        '"b","1","2"',
        footer,
        '"性別","女性"',
        "",
        header,
        '"a","1","2"',
        footer,
        '"性別","男女合計"',
        "",
        header,
        '"a","2","4"',
    ]
    path.write_bytes(("\n".join(rows) + "\n").encode("shift_jis"))


class TestGenderSumValidatorIntegration:
    """Validation results for each real-shaped raw fixture."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        self.raw_dir = _copy_raw_fixtures(tmp_path / "raw")
        self.validator = GenderSumValidator(self.raw_dir)

    @pytest.mark.parametrize(
        ("source_name", "data_type", "record_count"),
        [
            ("sentinel_monthly_age_2025_06.csv", "sentinel_monthly_age", 17),
            ("sentinel_monthly_health_center_2025_06.csv", "sentinel_monthly_health_center", 32),
            ("sentinel_monthly_medical_district_2025_06.csv", "sentinel_monthly_medical_district", 14),
            ("sentinel_weekly_age_2025_10.csv", "sentinel_weekly_age", 21),
            ("sentinel_weekly_health_center_2025_10.csv", "sentinel_weekly_health_center", 32),
        ],
    )
    def test_gender_split_fixture_is_validated_without_mismatch(
        self, source_name: str, data_type: str, record_count: int
    ) -> None:
        # Act
        result = self.validator.validate(source_name, data_type)

        # Assert: the monthly 集計期間開始月/終了月 rows no longer leak into the sections
        assert result == {
            "check_type": "gender_sum_consistency",
            "validation_status": "completed",
            "message": f"No mismatch observed in {record_count} record(s)",
            "details": {
                "source_file": source_name,
                "affected_count": 0,
                "truncated": False,
                "affected_locations": [],
            },
        }

    def test_weekly_medical_district_reports_upstream_female_defect(self) -> None:
        # Act
        result = self.validator.validate(
            "sentinel_weekly_medical_district_2025_10.csv", "sentinel_weekly_medical_district"
        )

        # Assert: the weekly district female section repeats the male value where the true count is 0
        assert result is not None
        assert result["validation_status"] == "completed"
        assert result["details"]["affected_count"] == 11
        assert result["details"]["truncated"] is True
        assert len(result["details"]["affected_locations"]) == GenderSumValidator._MAX_ERROR_SAMPLES
        assert result["details"]["affected_locations"][0] == {
            "location": "区中央部",
            "column": "不明発しん症",
            "row_index": 3,
            "male": 1,
            "female": 1,
            "total": 1,
            "expected": 2,
        }

    def test_header_only_total_is_skipped_as_sections_unavailable(self) -> None:
        # Act
        result = self.validator.validate(
            "sentinel_weekly_medical_district_2005_10.csv", "sentinel_weekly_medical_district"
        )

        # Assert
        assert result is not None
        assert result["validation_status"] == "skipped"
        assert result["details"]["skip_reason"] == "sections_unavailable"

    def test_row_count_mismatch_is_skipped_with_reason(self) -> None:
        # Arrange
        _write_row_count_mismatch_raw(self.raw_dir / "sentinel_weekly_age_2025_01.csv")

        # Act
        result = self.validator.validate("sentinel_weekly_age_2025_01.csv", "sentinel_weekly_age")

        # Assert
        assert result is not None
        assert result["validation_status"] == "skipped"
        assert result["message"] == "Validation skipped: row count mismatch (male=2, female=1, total=1)"
        assert result["details"]["skip_reason"] == "row_count_mismatch"

    @pytest.mark.parametrize(
        ("source_name", "data_type"),
        [
            ("sentinel_weekly_gender_2025_10.csv", "sentinel_weekly_gender"),
            ("sentinel_monthly_gender_2025_06.csv", "sentinel_monthly_gender"),
            ("notifiable_weekly_2025_10.csv", "notifiable_weekly"),
        ],
    )
    def test_validate_returns_none_for_non_gender_split_data(self, source_name: str, data_type: str) -> None:
        # Act / Assert
        assert self.validator.validate(source_name, data_type) is None

    def test_validate_returns_none_for_missing_file(self) -> None:
        # Act / Assert
        assert self.validator.validate("nonexistent_file.csv", "sentinel_weekly_age") is None

    def test_data_type_filtering_strict_matching(self) -> None:
        """Only exact data types in _APPLICABLE_DATA_TYPES are validated, not substrings."""
        assert "sentinel_weekly_age" in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_weekly_medical_district" in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_weekly_health_center" in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_monthly_age" in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_monthly_medical_district" in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_monthly_health_center" in GenderSumValidator._APPLICABLE_DATA_TYPES

        assert "sentinel_weekly_age_group" not in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_weekly_age_v2" not in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "age" not in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "medical_district_summary" not in GenderSumValidator._APPLICABLE_DATA_TYPES
        assert "sentinel_weekly_gender" not in GenderSumValidator._APPLICABLE_DATA_TYPES


class TestGenderSumValidatorEdgeCases:
    """Edge case tests for GenderSumValidator."""

    def test_path_traversal_protection(self, tmp_path: Path) -> None:
        # Arrange
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        outside = tmp_path / "outside.csv"
        outside.write_bytes("テスト\n".encode("shift_jis"))
        validator = GenderSumValidator(raw_dir)

        # Act
        result = validator._read_source_file(raw_dir / ".." / "outside.csv")

        # Assert
        assert result is None
        assert validator._file_cache == {}

    def test_cache_lru_eviction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setattr(GenderSumValidator, "_MAX_CACHE_SIZE", 3)
        validator = GenderSumValidator(tmp_path)
        files = []
        for index in range(4):
            path = tmp_path / f"file_{index}.csv"
            path.write_bytes("テスト\n".encode("shift_jis"))
            files.append(path)

        # Act
        for path in files:
            validator._read_source_file(path)

        # Assert: the first file read is evicted, the last three stay cached
        assert list(validator._file_cache) == files[1:]

    def test_extract_gender_sections_with_zero_start_line(self, tmp_path: Path) -> None:
        """_extract_gender_sections handles a section that starts at line 0."""
        # Arrange: like the raw files, a separator line precedes each following 性別 line
        csv_content = """"性別","男性"
""
"","疾病A","疾病B"
"地域1","10","5"
"地域2","8","3"
""
"性別","女性"
""
"","疾病A","疾病B"
"地域1","8","4"
"地域2","6","2"
""
"性別","男女合計"
""
"","疾病A","疾病B"
"地域1","18","9"
"地域2","14","5"
"""
        source = tmp_path / "sentinel_weekly_age_2025_01.csv"
        source.write_bytes(csv_content.encode("shift_jis"))
        validator = GenderSumValidator(tmp_path)

        # Act
        sections = validator._extract_gender_sections(source)

        # Assert
        assert sections == {
            "male": [["地域1", "10", "5"], ["地域2", "8", "3"]],
            "female": [["地域1", "8", "4"], ["地域2", "6", "2"]],
            "total": [["地域1", "18", "9"], ["地域2", "14", "5"]],
        }


class TestQualityValidatorIntegration:
    """Integration tests for QualityValidator."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        self.raw_dir = _copy_raw_fixtures(tmp_path / "raw")
        self.validator = QualityValidator(self.raw_dir)

    def test_clean_monthly_data_has_no_issues(self) -> None:
        # Act
        quality = self.validator.validate("sentinel_monthly_age_2025_06.csv", "sentinel_monthly_age", {})

        # Assert
        assert quality["validation_status"] == "completed"
        assert quality["issues"] == []
        assert quality["validation_timestamp"]

    def test_mismatch_is_recorded_in_issues(self) -> None:
        # Act
        quality = self.validator.validate(
            "sentinel_weekly_medical_district_2025_10.csv", "sentinel_weekly_medical_district", {}
        )

        # Assert
        assert [(issue["validation_status"], issue["details"]["affected_count"]) for issue in quality["issues"]] == [
            ("completed", 11)
        ]

    def test_sections_unavailable_skip_is_not_an_issue(self) -> None:
        # Act
        quality = self.validator.validate(
            "sentinel_weekly_medical_district_2005_10.csv", "sentinel_weekly_medical_district", {}
        )

        # Assert
        assert quality["validation_status"] == "completed"
        assert quality["issues"] == []

    def test_row_count_mismatch_skip_is_recorded_in_issues(self) -> None:
        # Arrange
        _write_row_count_mismatch_raw(self.raw_dir / "sentinel_weekly_age_2025_01.csv")

        # Act
        quality = self.validator.validate("sentinel_weekly_age_2025_01.csv", "sentinel_weekly_age", {})

        # Assert
        assert [
            (issue["check_type"], issue["validation_status"], issue["details"]["skip_reason"])
            for issue in quality["issues"]
        ] == [("gender_sum_consistency", "skipped", "row_count_mismatch")]

    def test_validate_with_non_gender_data(self) -> None:
        # Act
        quality = self.validator.validate("sentinel_weekly_gender_2025_10.csv", "sentinel_weekly_gender", {})

        # Assert
        assert quality["validation_status"] == "completed"
        assert quality["issues"] == []

    def test_validate_records_failed_validation(self) -> None:
        """Failed validations are recorded in issues."""
        # Arrange
        failed = {
            "check_type": "gender_sum_consistency",
            "validation_status": "failed",
            "message": "Validation failed: file read error",
            "details": {
                "source_file": "test.csv",
                "affected_count": 0,
                "truncated": False,
                "affected_locations": [],
            },
        }
        self.validator.gender_sum_validator = Mock()
        self.validator.gender_sum_validator.validate.return_value = failed

        # Act
        quality = self.validator.validate("sentinel_weekly_age_2025_10.csv", "sentinel_weekly_age", {})

        # Assert
        assert quality["validation_status"] == "completed"
        assert quality["issues"] == [failed]
