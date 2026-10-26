"""Phase 2 coverage tests focused on storage manager edge and failure paths.

These tests target remaining untested branches in `storage_manager`
with explicit AAA structure and isolated fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.managers.storage_manager import StorageManager


def _build_storage(tmp_path: Path) -> StorageManager:
    """Create a storage manager configured for isolated filesystem tests."""
    return StorageManager(tmp_path / "data", {})


def test_save_with_metadata_returns_failure_when_atomic_replace_fails(tmp_path: Path) -> None:
    """save_with_metadata cleans temporary files and returns failure on replace errors."""
    # Arrange
    storage = _build_storage(tmp_path)
    data = '"header","value"\n"row1","1"'.encode("shift_jis")

    # Act
    with patch("pathlib.Path.replace", side_effect=OSError("replace failed")):
        result = storage.save_with_metadata(data=data, data_type="phase2_test", year=2025, period=1)

    # Assert
    assert result.success is False
    assert result.error is not None
    assert "replace failed" in result.error
    temp_files = list((tmp_path / "data").glob(".phase2_test_2025_01_*.tmp"))
    assert temp_files == []


def test_check_path_safety_validation_handles_runtime_error(tmp_path: Path) -> None:
    """Path safety validation returns invalid when unexpected runtime errors occur."""
    # Arrange
    storage = _build_storage(tmp_path)
    file_path = tmp_path / "data" / "x.csv"

    # Act
    with patch.object(Path, "is_symlink", side_effect=RuntimeError("boom")):
        result = storage._check_path_safety_validation(file_path)

    # Assert
    assert result["valid"] is False
    assert any("Failed to check path safety" in error for error in result["errors"])


def test_get_metadata_returns_none_on_invalid_json(tmp_path: Path) -> None:
    """get_metadata gracefully handles malformed JSON metadata files."""
    # Arrange
    storage = _build_storage(tmp_path)
    target_file = storage.base_path / "sample.csv"
    target_file.write_text("x", encoding="utf-8")
    (storage.metadata_dir / "sample.json").write_text("{invalid", encoding="utf-8")

    # Act
    metadata = storage.get_metadata(target_file)

    # Assert
    assert metadata is None


def test_normalize_metadata_covers_v1_0_migration_paths(tmp_path: Path) -> None:
    """_normalize_metadata populates compatibility fields for legacy v1.0 metadata."""
    # Arrange
    storage = _build_storage(tmp_path)
    legacy = {
        "filename": "legacy.csv",
        "timestamp": "2025-01-01T00:00:00+00:00",
        "row_count": 7,
        "sha256_hash": "abc",
        "file_size": 10,
        "year": 2025,
        "period": 1,
        "period_type": "weekly",
    }

    # Act
    normalized = storage._normalize_metadata(legacy)

    # Assert
    assert normalized["created_at"] == "2025-01-01T00:00:00+00:00"
    assert normalized["updated_at"] == "2025-01-01T00:00:00+00:00"
    assert normalized["line_count"] == 7
    assert normalized["checksum_algorithm"] == "sha256"
    assert normalized["temporal"]["year"] == 2025
    assert normalized["hash"]["value"] == "abc"
