"""Phase 3 auxiliary coverage tests for config/storage/metadata/validator branches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import yaml

from src.managers.config_manager import ConfigurationManager
from src.managers.storage_manager import StorageManager
from src.models.metadata import Metadata
from src.validators.gender_sum_validator import GenderSumValidator


def _build_storage(tmp_path: Path) -> StorageManager:
    """Create storage manager for isolated branch tests."""
    return StorageManager(tmp_path / "raw", {"auto_commit": False})


def test_config_manager_load_config_emits_warning_logs(tmp_path: Path) -> None:
    """load_config logs warnings when validation returns warnings but no errors."""
    # Arrange
    config_path = tmp_path / "config.yml"
    current_year = datetime.now(UTC).year
    config_path.write_text(
        yaml.safe_dump(
            {
                "collection": {"start_year": 1999, "end_year": current_year + 1},
                "schedule": {"cron": "0 2 * * 1"},
            }
        ),
        encoding="utf-8",
    )
    manager = ConfigurationManager(config_path=config_path)

    # Act
    with patch("src.managers.config_manager.logger.warning") as mock_warning:
        manager.load_config()

    # Assert
    assert mock_warning.call_count >= 1


def test_config_manager_validate_config_covers_non_future_end_year_branch() -> None:
    """validate_config handles end_year that is set but not in the future."""
    # Arrange
    manager = ConfigurationManager()
    config = manager._get_default_config()
    config.collection.end_year = datetime.now(UTC).year

    # Act
    result = manager.validate_config(config)

    # Assert
    assert result.is_valid is True
    assert all("in the future" not in warning for warning in result.warnings)


def test_config_manager_parse_config_skips_optional_sections() -> None:
    """_parse_config keeps defaults when schedule/collection sections are absent."""
    # Arrange
    manager = ConfigurationManager()
    config_dict: dict[str, Any] = {"storage": {"base_directory": "custom/raw"}}

    # Act
    config = manager._parse_config(config_dict)

    # Assert
    assert config.schedule.cron_expression == "0 2 * * 1"
    assert config.collection.batch_size == 50
    assert config.storage.base_directory == "custom/raw"


def test_storage_save_with_metadata_handles_missing_temp_file_during_cleanup(tmp_path: Path) -> None:
    """save_with_metadata handles cleanup branch when temp file does not exist."""
    # Arrange
    storage = _build_storage(tmp_path)
    data = "h,v\n1,2\n".encode("shift_jis")
    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path.name.endswith(".tmp"):
            return False
        return original_exists(path)

    # Act
    with (
        patch("pathlib.Path.replace", side_effect=OSError("replace failed")),
        patch.object(Path, "exists", autospec=True, side_effect=fake_exists),
    ):
        result = storage.save_with_metadata(data=data, data_type="phase3", year=2025, period=1)

    # Assert
    assert result.success is False
    assert result.error is not None


def test_remove_from_hash_index_updates_file_when_list_entry_does_not_contain_path(tmp_path: Path) -> None:
    """_remove_from_hash_index still persists index when list does not contain target path."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h": ["a.csv", "b.csv"]}

    # Act
    storage._remove_from_hash_index("h", "missing.csv")

    # Assert
    with storage.hash_index_file.open(encoding="utf-8") as file:
        persisted = file.read()
    assert '"h"' in persisted
    assert storage.hash_index["h"] == ["a.csv", "b.csv"]


def test_normalize_metadata_skips_default_fallbacks_when_fields_already_present(tmp_path: Path) -> None:
    """_normalize_metadata keeps explicit v1.0 compatibility fields without overwriting them."""
    # Arrange
    storage = _build_storage(tmp_path)
    metadata = {
        "metadata_version": "1.0",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-02T00:00:00+00:00",
        "line_count": 10,
        "checksum_algorithm": "sha256",
        "hash": {"algorithm": "sha256", "value": "abc"},
    }

    # Act
    normalized = storage._normalize_metadata(metadata)

    # Assert
    assert normalized["created_at"] == "2024-01-01T00:00:00+00:00"
    assert normalized["updated_at"] == "2024-01-02T00:00:00+00:00"
    assert normalized["line_count"] == 10
    assert normalized["checksum_algorithm"] == "sha256"
    assert normalized["temporal"]["year"] is None
    assert normalized["hash"]["value"] == "abc"


def test_cleanup_old_files_counts_deleted_when_metadata_json_is_missing(tmp_path: Path) -> None:
    """cleanup_old_files increments deleted count even when metadata json is absent."""
    # Arrange
    storage = _build_storage(tmp_path)
    old_file = storage.base_path / "old.csv"
    old_file.write_text("x\n", encoding="utf-8")
    old_created = (datetime.now(UTC) - timedelta(days=400)).isoformat()

    # Act
    with patch.object(storage, "get_metadata", return_value={"created_at": old_created}):
        deleted = storage.cleanup_old_files(days_to_keep=365)

    # Assert
    assert deleted == 1
    assert old_file.exists() is False


def test_metadata_from_legacy_processed_handles_filename_without_gender_token() -> None:
    """from_legacy_processed keeps gender None when filename has no explicit gender token."""
    # Arrange
    legacy = {
        "filename": "normalized_sentinel_weekly_age_2025_01.csv",
        "path": "processed/normalized_sentinel_weekly_age_2025_01.csv",
        "source": "raw/sentinel_weekly_age_2025_01.csv",
        "metadata": {"year": 2025, "period": 1, "frequency": "weekly"},
    }

    # Act
    metadata = Metadata.from_legacy_processed(legacy)

    # Assert
    assert metadata._process is not None
    assert metadata._process.gender is None


def test_gender_sum_validator_clear_cache_empties_entries(tmp_path: Path) -> None:
    """clear_cache removes all cached file conversion entries."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    key = tmp_path / "raw" / "sample.csv"
    validator._file_cache[key] = "content"

    # Act
    validator.clear_cache()

    # Assert
    assert validator._file_cache == {}


def test_gender_sum_validator_ignores_duplicate_location_and_value_errors(tmp_path: Path) -> None:
    """_validate_gender_sum records one mismatch per location and skips value errors."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    source_path = tmp_path / "raw" / "sample.csv"
    source_filename = "sample.csv"
    sections = {
        "male": [["loc", "10", "bad"], ["loc", "9", "8"]],
        "female": [["loc", "5", "1"], ["loc", "3", "1"]],
        "total": [["loc", "1", "2"], ["loc", "1", "2"]],
    }

    # Act
    with (
        patch.object(validator, "_extract_gender_sections", return_value=sections),
        patch.object(validator, "_get_header", return_value=["loc", "col1", "col2"]),
    ):
        result = validator._validate_gender_sum(source_path, source_filename)

    # Assert
    assert result["details"]["affected_count"] == 1
    assert result["details"]["affected_locations"][0]["location"] == "loc"


def test_gender_sum_validator_covers_value_error_continue_branch(tmp_path: Path) -> None:
    """_validate_gender_sum continues when numeric conversion raises ValueError."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    source_path = tmp_path / "raw" / "sample.csv"
    sections = {
        "male": [["loc", "x"]],
        "female": [["loc", "1"]],
        "total": [["loc", "1"]],
    }

    # Act
    with (
        patch.object(validator, "_extract_gender_sections", return_value=sections),
        patch.object(validator, "_get_header", return_value=["loc", "col1"]),
    ):
        result = validator._validate_gender_sum(source_path, "sample.csv")

    # Assert
    assert result["validation_status"] == "completed"
    assert result["details"]["affected_count"] == 0


def test_gender_sum_validator_read_source_file_nonzero_with_stderr(tmp_path: Path) -> None:
    """_read_source_file handles non-zero iconv return with stderr branch."""
    # Arrange
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source_file = raw_dir / "sample.csv"
    source_file.write_bytes("x\n".encode("shift_jis"))
    validator = GenderSumValidator(raw_dir)

    # Act
    with patch("src.validators.gender_sum_validator.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1, stderr="decode error", stdout="")
        content = validator._read_source_file(source_file)

    # Assert
    assert content is None
    assert validator._file_cache[source_file] is None


def test_gender_sum_validator_extract_gender_sections_handles_none_content(tmp_path: Path) -> None:
    """_extract_gender_sections returns empty sections when source read fails."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    source_file = tmp_path / "raw" / "missing.csv"

    # Act
    with patch.object(validator, "_read_source_file", return_value=None):
        sections = validator._extract_gender_sections(source_file)

    # Assert
    assert sections == {"male": [], "female": [], "total": []}


def test_gender_sum_validator_extract_gender_sections_skips_unknown_section_marker(tmp_path: Path) -> None:
    """_extract_gender_sections ignores unsupported gender marker values."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    source_file = tmp_path / "raw" / "sample.csv"
    content = "\n".join(['"性別","不明"', '"性別","男性"', '"","疾病"', '"loc","1"'])

    # Act
    with patch.object(validator, "_read_source_file", return_value=content):
        sections = validator._extract_gender_sections(source_file)

    # Assert
    assert sections == {"male": [], "female": [], "total": []}


def test_gender_sum_validator_get_header_returns_empty_when_content_missing(tmp_path: Path) -> None:
    """_get_header returns empty list when source content is unavailable."""
    # Arrange
    validator = GenderSumValidator(tmp_path)
    source_file = tmp_path / "raw" / "sample.csv"

    # Act
    with patch.object(validator, "_read_source_file", return_value=None):
        header = validator._get_header(source_file, section="total")

    # Assert
    assert header == []
