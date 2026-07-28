"""Phase 1 coverage uplift tests for issue #302.

This test module targets uncovered edge cases and error paths to raise
test coverage from 87% to 93% with a focus on critical code paths:

Coverage targets:
- config_manager: Validation failures, I/O errors, default fallbacks
- enhanced_fetcher: HTTP errors, retry logic, boundary conditions, edge cases
- storage_manager: Hash index operations, validation limits, git integration
- validators: Subprocess errors, timeout handling, data quality checks

Test strategy:
- Use AAA (Arrange-Act-Assert) pattern for clarity
- Mock external dependencies (network, subprocess, file I/O)
- Focus on error paths and boundary conditions
- Test realistic failure scenarios

Related:
- Issue #302: Phase 1 coverage improvement plan
- Target: 92% minimum coverage enforced in CI
"""

from __future__ import annotations

import csv
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from requests import Response
from requests.exceptions import HTTPError

from src.fetchers.enhanced_fetcher import DataFetcherConfig, EnhancedEpidemicDataFetcher, FetchResult, RetryHandler
from src.managers.config_manager import ConfigurationManager
from src.managers.storage_manager import CommitResult, GitHandler, StorageManager
from src.models.metadata import HashInfo, Metadata, TemporalInfo
from src.validators.gender_sum_validator import GenderSumValidator
from src.validators.quality_validator import QualityValidator


def test_config_manager_load_config_raises_on_validation_error(tmp_path: Path) -> None:
    """Test that load_config raises ValueError when configuration validation fails."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
        schedule:
          cron: ""
        storage:
          base_directory: ""
        collection:
          batch_size: 0
          data_types: []
        """,
        encoding="utf-8",
    )

    manager = ConfigurationManager(config_path)
    with pytest.raises(ValueError, match="Configuration validation failed"):
        manager.load_config()


def test_config_manager_load_config_reraises_io_error() -> None:
    """Test that load_config re-raises OSError when file I/O fails."""
    manager = ConfigurationManager(Path("dummy.yml"))

    with (
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "open", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        manager.load_config()


def test_config_manager_validate_config_detects_required_fields_and_future_year() -> None:
    """Test that validation detects missing required fields and warns about future years."""
    manager = ConfigurationManager()
    config = manager._get_default_config()
    config.schedule.cron_expression = ""
    config.storage.base_directory = ""
    config.collection.end_year = datetime.now(UTC).year + 1

    result = manager.validate_config(config)

    assert result.is_valid is False
    assert "Cron expression is required" in result.errors
    assert "Base directory is required" in result.errors
    assert any("is in the future" in warning for warning in result.warnings)


def test_config_manager_get_enabled_data_types_loads_config_when_missing() -> None:
    """Test that get_enabled_data_types lazily loads config when not present."""
    manager = ConfigurationManager()
    config = manager._get_default_config()
    config.data_types[0].enabled = False

    def load_and_set() -> object:
        manager.config = config
        return config

    with patch.object(manager, "load_config", side_effect=load_and_set) as mock_load:
        enabled = manager.get_enabled_data_types()

    mock_load.assert_called_once()
    assert all(dt.enabled for dt in enabled)


def _make_http_error(status_code: int, headers: dict[str, str] | None = None) -> HTTPError:
    """Helper to create HTTPError with specific status code and headers."""
    error = HTTPError("http error")
    response = Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    error.response = response
    return error


def test_retry_handler_rate_limit_detection_variants() -> None:
    """Test rate limit detection for various HTTP status codes and headers."""
    handler = RetryHandler(DataFetcherConfig(enable_jitter=False))

    assert handler._is_rate_limit_error(_make_http_error(429)) is True
    assert handler._is_rate_limit_error(_make_http_error(403, {"Retry-After": "1"})) is True
    assert handler._is_rate_limit_error(_make_http_error(403, {})) is False
    assert handler._is_rate_limit_error(_make_http_error(500)) is False
    assert handler._is_rate_limit_error(HTTPError("no response")) is False


@pytest.mark.asyncio
async def test_retry_handler_reraises_unexpected_value_error() -> None:
    """Test that retry handler re-raises ValueError that is not a parse error."""
    handler = RetryHandler(DataFetcherConfig(max_retries=1, enable_jitter=False))

    async def raise_value_error() -> None:
        raise ValueError("invalid")

    with pytest.raises(ValueError, match="invalid"):
        await handler.execute_with_retry(raise_value_error)


@pytest.mark.asyncio
async def test_retry_handler_raises_when_retry_loop_never_runs() -> None:
    """Test that retry handler fails explicitly when max_retries is negative."""
    handler = RetryHandler(DataFetcherConfig(max_retries=-1, enable_jitter=False))

    called = {"value": False}

    async def should_not_be_called() -> None:
        called["value"] = True

    with pytest.raises(RuntimeError, match="Retry loop exited unexpectedly"):
        await handler.execute_with_retry(should_not_be_called)
    assert called["value"] is False


def test_enhanced_fetcher_fetch_with_retry_handles_unexpected_exception() -> None:
    """Test that fetch_with_retry handles unexpected RuntimeError gracefully."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with patch.object(fetcher.retry_handler, "execute_with_retry", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = fetcher.fetch_with_retry(lambda **_: b"ok", data_type="sentinel_weekly_gender", report_type="1")

    assert result.success is False
    assert isinstance(result.error, RuntimeError)


def test_enhanced_fetcher_fetch_date_range_unknown_type_raises() -> None:
    """Test that fetch_date_range raises ValueError for unknown data types."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with pytest.raises(ValueError, match="Unknown data type"):
        fetcher.fetch_date_range("unknown_type", (2025, 1), (2025, 1))


def test_enhanced_fetcher_fetch_date_range_monthly_rollover() -> None:
    """Test that fetch_date_range handles monthly year rollover correctly."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with (
        patch("time.sleep") as mock_sleep,
        patch.object(fetcher, "fetch_with_retry", return_value=FetchResult(success=True, data=b"x")) as mock_fetch,
    ):
        fetcher.fetch_date_range("sentinel_monthly_age", (2025, 12), (2026, 1))

    assert mock_fetch.call_count == 2
    assert mock_sleep.call_count == 2
    periods = [call.kwargs["start_sub_period"] for call in mock_fetch.call_args_list]
    assert periods == ["12", "1"]


def test_enhanced_fetcher_get_missing_data_invalid_targets_raise() -> None:
    """Test that get_missing_data raises ValueError for invalid week/month numbers."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with pytest.raises(ValueError, match="無効な週番号"):
        fetcher.get_missing_data("sentinel_weekly_gender", [], target_weeks=[0])

    with pytest.raises(ValueError, match="無効な月番号"):
        fetcher.get_missing_data("sentinel_monthly_age", [], target_months=[13])


def test_enhanced_fetcher_get_missing_data_uses_current_year_when_end_year_none() -> None:
    """Test that get_missing_data defaults to current year when end_year is None."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))
    current_year = datetime.now(UTC).year

    with patch.object(fetcher, "_get_weeks_in_year", return_value=1):
        missing = fetcher.get_missing_data(
            "sentinel_weekly_gender",
            [],
            start_year=current_year,
            end_year=None,
            target_weeks=[1],
        )

    assert len(missing) == 1
    assert missing[0].start_year == str(current_year)


def test_enhanced_fetcher_create_metadata_includes_period_range_suffix() -> None:
    """Test that _create_metadata correctly formats date_range with period suffix."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    metadata = fetcher._create_metadata(
        b"data",
        {
            "data_type": "sentinel_weekly_gender",
            "report_type": "1",
            "start_year": "2025",
            "start_sub_period": "1",
            "end_year": "2025",
            "end_sub_period": "2",
        },
    )

    assert metadata.date_range == "20251-2"


def test_enhanced_fetcher_get_source_url_returns_none_when_endpoint_missing() -> None:
    """Test that _get_source_url returns None when endpoint is not in map."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with patch.dict(fetcher.ENDPOINT_MAP, {}, clear=True):
        assert fetcher._get_source_url("unknown") is None


def test_enhanced_fetcher_parse_existing_files_rejects_out_of_range_year() -> None:
    """Test that _parse_existing_files filters out files with invalid years."""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    params = fetcher._parse_existing_files([Path("sentinel_weekly_gender_1800_01.csv")], "sentinel_weekly_gender")
    assert params == []


def test_gender_sum_validator_read_source_file_handles_timeout_and_nonzero(tmp_path: Path) -> None:
    """Test that _read_source_file handles subprocess timeout and non-zero exit codes."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source_file = raw_dir / "sample.csv"
    source_file.write_bytes("テスト\n".encode("shift_jis"))
    validator = GenderSumValidator(raw_dir)

    with patch(
        "src.validators.gender_sum_validator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="iconv", timeout=30),
    ):
        assert validator._read_source_file(source_file) is None
        assert validator._file_cache[source_file] is None

    with patch("src.validators.gender_sum_validator.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1, stderr="", stdout="")
        assert validator._read_source_file(source_file) is None


def test_gender_sum_validator_header_and_section_fallbacks(tmp_path: Path) -> None:
    """Test header detection and section extraction fallback behavior."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source_file = raw_dir / "sample.csv"
    source_file.write_bytes(b"dummy")
    validator = GenderSumValidator(raw_dir)

    content_with_total_header = '"性別","男女合計"\n"","疾病A","疾病B"\n"地域1","1","2"\n'
    with patch.object(validator, "_read_source_file", return_value=content_with_total_header):
        header = validator._get_header(source_file, section="unknown_section")
        assert header == ["", "疾病A", "疾病B"]

    content_without_header = '"性別","男女合計"\n"note"\n"note2"\n'
    with patch.object(validator, "_read_source_file", return_value=content_without_header):
        assert validator._get_header(source_file, section="total") == []

    content_missing_total = (
        '"性別","男性"\n""\n"","疾病A"\n"地域1","1"\n' '"性別","女性"\n""\n"","疾病A"\n"地域1","1"\n'
    )
    with patch.object(validator, "_read_source_file", return_value=content_missing_total):
        sections = validator._extract_gender_sections(source_file)
        assert sections == {"male": [], "female": [], "total": []}


def test_quality_validator_records_completed_result_with_affected_rows(tmp_path: Path) -> None:
    """Test that quality validator properly records validation issues with affected rows."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    validator = QualityValidator(raw_dir)
    validator.gender_sum_validator = Mock()
    validator.gender_sum_validator.validate.return_value = {
        "check_type": "gender_sum_consistency",
        "validation_status": "completed",
        "message": "mismatch",
        "details": {
            "source_file": "sample.csv",
            "affected_count": 2,
            "truncated": False,
            "affected_locations": [{"location": "A"}],
        },
    }

    quality = validator.validate("sample.csv", "sentinel_weekly_age", {})
    assert len(quality["issues"]) == 1


def test_metadata_from_legacy_raw_handles_verification_and_missing_source() -> None:
    """Test that from_legacy_raw correctly migrates verification status and handles missing sources."""
    legacy = {
        "filename": "raw.csv",
        "year": 2025,
        "period": 1,
        "period_type": "weekly",
        "sha256_hash": "hash",
        "verification": {"status": "failed", "errors": ["bad"]},
    }

    metadata = Metadata.from_legacy_raw(legacy)

    assert metadata.verification is not None
    assert metadata.verification.status == "failed"
    assert metadata.sources == []


def test_metadata_from_legacy_processed_detects_female_and_total_gender() -> None:
    """Test that from_legacy_processed correctly detects female and total gender markers."""
    source_meta = Metadata(
        name="source",
        filename="source.csv",
        path="source.csv",
        profile="tokyo-idsc-raw",
        data_type="sentinel_weekly_age",
        temporal=TemporalInfo(year=2025, period=1, period_type="weekly"),
        bytes=100,
        hash=HashInfo(algorithm="sha256", value="source_hash"),
        encoding="shift_jis",
        created="2025-01-01T00:00:00Z",
        modified="2025-01-01T00:00:00Z",
    )

    for marker in ("female", "total"):
        legacy = {
            "source": "data/raw/source.csv",
            "outputs": [{"path": f"data/processed/output_{marker}_file.csv", "size_bytes": 1500}],
            "metadata": {
                "year": "2025",
                "period": "1",
                "frequency": "weekly",
                "category": "sentinel",
                "aggregation": "age",
            },
        }

        metadata = Metadata.from_legacy_processed(legacy, source_meta)
        assert metadata._process is not None
        assert metadata._process.gender == marker


def test_git_handler_add_files_skips_missing_files(tmp_path: Path) -> None:
    """Test that add_files returns True without calling git for non-existent files."""
    # Arrange
    git_handler = GitHandler(auto_commit=True)
    missing_file = tmp_path / "missing.csv"

    # Act & Assert
    with patch("subprocess.run") as mock_run:
        result = git_handler.add_files([missing_file])
        assert result is True
        mock_run.assert_not_called()


def test_git_handler_add_files_returns_false_on_git_error(tmp_path: Path) -> None:
    """Test that add_files returns False when git add command fails."""
    # Arrange
    git_handler = GitHandler(auto_commit=True)
    existing_file = tmp_path / "exists.csv"
    existing_file.write_text("x", encoding="utf-8")

    # Act & Assert
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["git", "add"], stderr="fatal"),
    ):
        result = git_handler.add_files([existing_file])
        assert result is False


def test_git_handler_commit_returns_failure_on_commit_error() -> None:
    """Test that commit returns failure result when git commit fails."""
    # Arrange
    git_handler = GitHandler(auto_commit=True)

    # Act
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            Mock(returncode=1),  # git diff exits with 1 (changes exist)
            subprocess.CalledProcessError(1, ["git", "commit"], stderr="commit failed"),
        ]
        result = git_handler.commit("msg")

    # Assert
    assert result.success is False
    assert "commit failed" in (result.error or "")


def test_git_handler_configure_user_returns_false_on_config_error() -> None:
    """Test that configure_user returns False when git config fails."""
    # Arrange
    git_handler = GitHandler(auto_commit=True)

    # Act & Assert
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["git", "config"], stderr="config failed"),
    ):
        result = git_handler.configure_user()
        assert result is False


def test_storage_manager_commit_changes_skips_when_auto_commit_disabled(tmp_path: Path) -> None:
    """Test that commit_changes returns early when auto_commit is disabled."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    result = storage.commit_changes()

    # Assert
    assert result.message == "Auto commit disabled"


def test_storage_manager_commit_changes_skips_when_not_git_repo(tmp_path: Path) -> None:
    """Test that commit_changes returns early when not in a git repository."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": True})

    # Act
    with patch.object(storage.git_handler, "is_git_repo", return_value=False):
        result = storage.commit_changes()

    # Assert
    assert result.message == "Not a git repository"


def test_storage_manager_commit_changes_creates_commit_with_japanese_message(tmp_path: Path) -> None:
    """Test that commit_changes creates commit with Japanese message including データ更新."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": True})

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
        storage.commit_changes()

    # Assert
    assert "データ更新" in mock_commit.call_args.args[0]


def test_storage_manager_load_hash_index_returns_empty_dict_on_invalid_json(tmp_path: Path) -> None:
    """Test that _load_hash_index returns empty dict when JSON is invalid."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    storage.hash_index_file.write_text("{invalid", encoding="utf-8")

    # Act
    result = storage._load_hash_index()

    # Assert
    assert result == {}


def test_storage_manager_remove_from_hash_index_deletes_single_path(tmp_path: Path) -> None:
    """Test that _remove_from_hash_index removes entry when hash has single path."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    storage.hash_index = {"hash1": str(tmp_path / "a.csv")}

    # Act
    storage._remove_from_hash_index("hash1", str(tmp_path / "a.csv"))

    # Assert
    assert "hash1" not in storage.hash_index


def test_storage_manager_remove_from_hash_index_converts_list_to_string(tmp_path: Path) -> None:
    """Test that _remove_from_hash_index converts list to string when one path remains."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    storage.hash_index = {"hash2": ["a.csv", "b.csv"]}

    # Act
    storage._remove_from_hash_index("hash2", "a.csv")

    # Assert
    assert storage.hash_index["hash2"] == "b.csv"


def test_storage_manager_remove_from_hash_index_deletes_last_item_in_list(tmp_path: Path) -> None:
    """Test that _remove_from_hash_index removes entry when last item in list is removed."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    storage.hash_index = {"hash3": ["x.csv"]}

    # Act
    storage._remove_from_hash_index("hash3", "x.csv")

    # Assert
    assert "hash3" not in storage.hash_index


def test_storage_manager_remove_from_hash_index_raises_on_io_error(tmp_path: Path) -> None:
    """Test that _remove_from_hash_index propagates OSError when file write fails."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    storage.hash_index = {"hash4": ["z.csv"]}

    # Act & Assert
    with patch.object(Path, "open", side_effect=OSError("disk error")), pytest.raises(OSError, match="disk error"):
        storage._remove_from_hash_index("hash4", "z.csv")


def test_storage_manager_count_lines_returns_none_for_non_bytes_data(tmp_path: Path) -> None:
    """Test that _count_lines returns None for data that is not bytes."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    class BadLineData:
        def __bool__(self) -> bool:
            return True

    # Act
    result = storage._count_lines(BadLineData())  # type: ignore[arg-type]

    # Assert
    assert result is None


def test_storage_manager_normalize_timestamp_handles_string_input(tmp_path: Path) -> None:
    """Test that _normalize_timestamp correctly handles string timestamps."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    result = storage._normalize_timestamp("2025-01-01T00:00:00")

    # Assert
    assert "T" in result


def test_storage_manager_normalize_timestamp_falls_back_for_invalid_input(tmp_path: Path) -> None:
    """Test that _normalize_timestamp falls back to current time for invalid input."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    result = storage._normalize_timestamp(123)  # type: ignore[arg-type]

    # Assert
    assert "T" in result


def test_storage_manager_check_file_size_validation_warns_on_threshold(tmp_path: Path) -> None:
    """Test that _check_file_size_validation issues warnings near size threshold."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with (
        patch("src.managers.storage_manager.VALIDATION_MAX_FILE_SIZE_MB", 0.001),
        patch("src.managers.storage_manager.VALIDATION_SIZE_WARNING_THRESHOLD", 0.5),
    ):
        result = storage._check_file_size_validation(b"x" * 700)

    # Assert
    assert result["valid"] is True
    assert result["warnings"]


def test_storage_manager_check_file_size_validation_fails_on_too_large(tmp_path: Path) -> None:
    """Test that _check_file_size_validation fails when file exceeds maximum size."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with (
        patch("src.managers.storage_manager.VALIDATION_MAX_FILE_SIZE_MB", 0.001),
        patch("src.managers.storage_manager.VALIDATION_SIZE_WARNING_THRESHOLD", 0.5),
    ):
        result = storage._check_file_size_validation(b"x" * 2000)

    # Assert
    assert result["valid"] is False
    assert result["errors"]


def test_storage_manager_check_encoding_validation_fails_on_decode_error(tmp_path: Path) -> None:
    """Test that _check_encoding_validation fails when data cannot be decoded."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    class BadData:
        def decode(self, *_args, **_kwargs):
            raise ValueError("bad")

    # Act
    result = storage._check_encoding_validation(BadData())  # type: ignore[arg-type]

    # Assert
    assert result["valid"] is False
    assert result["errors"]


def test_storage_manager_check_csv_format_validation_fails_on_too_many_lines(tmp_path: Path) -> None:
    """Test that _check_csv_format_validation fails when line count exceeds maximum."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with patch("src.managers.storage_manager.VALIDATION_MAX_LINE_COUNT", 1):
        result = storage._check_csv_format_validation("h1\nh2\n".encode("shift_jis"))

    # Assert
    assert result["valid"] is False
    assert any("Too many lines" in error for error in result["errors"])


def test_storage_manager_check_csv_format_validation_fails_on_too_few_lines(tmp_path: Path) -> None:
    """Test that _check_csv_format_validation fails when line count is below minimum."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with patch("src.managers.storage_manager.VALIDATION_MIN_LINE_COUNT", 3):
        result = storage._check_csv_format_validation("h1\n".encode("shift_jis"))

    # Assert
    assert result["valid"] is False
    assert any("Too few lines" in error for error in result["errors"])


def test_storage_manager_check_csv_format_validation_fails_on_too_many_columns(tmp_path: Path) -> None:
    """Test that _check_csv_format_validation fails when column count exceeds maximum."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with patch("src.managers.storage_manager.VALIDATION_MAX_COLUMN_COUNT", 2):
        result = storage._check_csv_format_validation("a,b,c\n".encode("shift_jis"))

    # Assert
    assert result["valid"] is False
    assert any("Too many columns" in error for error in result["errors"])


def test_storage_manager_check_csv_format_validation_fails_on_csv_error(tmp_path: Path) -> None:
    """Test that _check_csv_format_validation handles csv.Error gracefully."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    # Act
    with patch("src.managers.storage_manager.csv.reader", side_effect=csv.Error("bad csv")):
        result = storage._check_csv_format_validation(b"any")

    # Assert
    assert result["valid"] is False
    assert any("CSV format error" in error for error in result["errors"])


def test_storage_manager_check_csv_format_validation_fails_on_os_error(tmp_path: Path) -> None:
    """Test that _check_csv_format_validation handles OSError during decoding."""
    # Arrange
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    class OsErrorData:
        def decode(self, *_args, **_kwargs):
            raise OSError("decode failed")

    # Act
    result = storage._check_csv_format_validation(OsErrorData())  # type: ignore[arg-type]

    # Assert
    assert result["valid"] is False
    assert any("Failed to check CSV format" in error for error in result["errors"])
