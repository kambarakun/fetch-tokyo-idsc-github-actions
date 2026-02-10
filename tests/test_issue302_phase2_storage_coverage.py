"""Phase 2 coverage tests focused on storage manager edge and failure paths.

These tests target remaining untested branches in `storage_manager` and `GitHandler`
with explicit AAA structure and isolated fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.managers.storage_manager import CommitResult, GitHandler, StorageManager


def _build_storage(tmp_path: Path, *, auto_commit: bool = False) -> StorageManager:
    """Create a storage manager configured for isolated filesystem tests."""
    return StorageManager(tmp_path / "data", {"auto_commit": auto_commit})


def test_git_handler_configure_user_success() -> None:
    """GitHandler returns True when both git config commands succeed."""
    # Arrange
    git_handler = GitHandler(auto_commit=True)

    # Act
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0)
        result = git_handler.configure_user()

    # Assert
    assert result is True
    assert mock_run.call_count == 2


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


def test_commit_changes_uses_explicit_message_without_template_resolution(tmp_path: Path) -> None:
    """commit_changes passes explicit message through without reformatting."""
    # Arrange
    storage = _build_storage(tmp_path, auto_commit=True)

    # Act
    with (
        patch.object(storage.git_handler, "is_git_repo", return_value=True),
        patch.object(storage.git_handler, "add_files", return_value=True),
        patch.object(
            storage.git_handler,
            "commit",
            return_value=CommitResult(success=True, commit_hash="abc", message="ok"),
        ) as mock_commit,
    ):
        result = storage.commit_changes(message="manual commit message", data_type="x", date_range="y")

    # Assert
    assert result.success is True
    mock_commit.assert_called_once_with("manual commit message")


def test_remove_from_hash_index_returns_early_for_unknown_hash(tmp_path: Path) -> None:
    """_remove_from_hash_index is a no-op when hash key does not exist."""
    # Arrange
    storage = _build_storage(tmp_path)

    # Act
    storage._remove_from_hash_index("missing", "path.csv")

    # Assert
    assert storage.hash_index == {}


def test_remove_from_hash_index_keeps_string_entry_when_path_differs(tmp_path: Path) -> None:
    """String hash entries remain when removal target path does not match."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h": "a.csv"}

    # Act
    storage._remove_from_hash_index("h", "b.csv")

    # Assert
    assert storage.hash_index["h"] == "a.csv"


def test_remove_from_hash_index_list_preserves_multiple_remaining_paths(tmp_path: Path) -> None:
    """List hash entries remain lists when more than one path remains."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h": ["a.csv", "b.csv", "c.csv"]}

    # Act
    storage._remove_from_hash_index("h", "a.csv")

    # Assert
    assert storage.hash_index["h"] == ["b.csv", "c.csv"]


def test_add_to_hash_index_ignores_duplicate_entries(tmp_path: Path) -> None:
    """_add_to_hash_index does not duplicate existing entries for string or list formats."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h1": "a.csv", "h2": ["x.csv"]}

    # Act
    storage._add_to_hash_index("h1", "a.csv")
    storage._add_to_hash_index("h2", "x.csv")

    # Assert
    assert storage.hash_index == {"h1": "a.csv", "h2": ["x.csv"]}


def test_sort_hash_index_skips_duplicate_paths_in_existing_list(tmp_path: Path) -> None:
    """_sort_hash_index preserves unique paths when duplicate entries are present."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h": ["a.csv", "b.csv", "b.csv"]}

    # Act
    sorted_index = storage._sort_hash_index_by_filename()

    # Assert
    assert sorted_index["h"] == ["a.csv", "b.csv"]


def test_update_hash_index_raises_when_write_fails(tmp_path: Path) -> None:
    """_update_hash_index raises and logs when persisting hash index fails."""
    # Arrange
    storage = _build_storage(tmp_path)
    original_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == storage.hash_index_file:
            raise OSError("write failed")
        return original_open(path, *args, **kwargs)

    # Act / Assert
    with (
        patch.object(Path, "open", autospec=True, side_effect=failing_open),
        pytest.raises(OSError, match="write failed"),
    ):
        storage._update_hash_index("h", "path.csv")


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


def test_get_storage_stats_covers_type_and_year_branch_matrix(tmp_path: Path) -> None:
    """get_storage_stats handles unknown types, repeated types, and unmatched year patterns."""
    # Arrange
    storage = _build_storage(tmp_path)
    files = {
        "sentinel_monthly_gender_2025_01.csv": "a",
        "sentinel_monthly_age_2025_02.csv": "b",
        "notifiable_weekly_2024_03.csv": "c",
        "unknown_data.csv": "d",
    }
    for name, content in files.items():
        (storage.base_path / name).write_text(content, encoding="utf-8")

    # Act
    stats = storage.get_storage_stats()

    # Assert
    assert stats["total_files"] == 4
    assert stats["file_types"]["sentinel_monthly"]["count"] == 2
    assert stats["file_types"]["notifiable"]["count"] == 1
    assert stats["year_stats"][2025]["count"] == 2
    assert stats["year_stats"][2024]["count"] == 1


def test_hash_index_file_is_updated_when_remove_writes(tmp_path: Path) -> None:
    """_remove_from_hash_index persists updates to hash index file."""
    # Arrange
    storage = _build_storage(tmp_path)
    storage.hash_index = {"h": ["a.csv", "b.csv"]}

    # Act
    storage._remove_from_hash_index("h", "a.csv")

    # Assert
    with storage.hash_index_file.open(encoding="utf-8") as file:
        persisted = json.load(file)
    assert persisted["h"] == "b.csv"
