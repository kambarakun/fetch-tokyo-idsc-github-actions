from __future__ import annotations

import csv
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from requests.exceptions import HTTPError

from src.fetchers.enhanced_fetcher import DataFetcherConfig, EnhancedEpidemicDataFetcher, FetchResult, RetryHandler
from src.managers.config_manager import ConfigurationManager
from src.managers.storage_manager import CommitResult, GitHandler, StorageManager
from src.models.metadata import HashInfo, Metadata, TemporalInfo
from src.validators.gender_sum_validator import GenderSumValidator
from src.validators.quality_validator import QualityValidator


def test_config_manager_load_config_raises_on_validation_error(tmp_path: Path) -> None:
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
    manager = ConfigurationManager(Path("dummy.yml"))

    with (
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "open", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        manager.load_config()


def test_config_manager_validate_config_detects_required_fields_and_future_year() -> None:
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
    error = HTTPError("http error")
    error.response = SimpleNamespace(status_code=status_code, headers=headers or {})
    return error


def test_retry_handler_rate_limit_detection_variants() -> None:
    handler = RetryHandler(DataFetcherConfig(enable_jitter=False))

    assert handler._is_rate_limit_error(_make_http_error(429)) is True
    assert handler._is_rate_limit_error(_make_http_error(403, {"Retry-After": "1"})) is True
    assert handler._is_rate_limit_error(_make_http_error(403, {})) is False
    assert handler._is_rate_limit_error(_make_http_error(500)) is False
    assert handler._is_rate_limit_error(HTTPError("no response")) is False


@pytest.mark.asyncio
async def test_retry_handler_reraises_unexpected_value_error() -> None:
    handler = RetryHandler(DataFetcherConfig(max_retries=1, enable_jitter=False))

    async def raise_value_error() -> None:
        raise ValueError("invalid")

    with pytest.raises(ValueError, match="invalid"):
        await handler.execute_with_retry(raise_value_error)


@pytest.mark.asyncio
async def test_retry_handler_returns_none_when_retry_loop_never_runs() -> None:
    handler = RetryHandler(DataFetcherConfig(max_retries=-1, enable_jitter=False))

    called = {"value": False}

    async def should_not_be_called() -> None:
        called["value"] = True

    result = await handler.execute_with_retry(should_not_be_called)

    assert result is None
    assert called["value"] is False


def test_enhanced_fetcher_fetch_with_retry_handles_unexpected_exception() -> None:
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with patch.object(fetcher.retry_handler, "execute_with_retry", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = fetcher.fetch_with_retry(lambda **_: b"ok", data_type="sentinel_weekly_gender", report_type="1")

    assert result.success is False
    assert isinstance(result.error, RuntimeError)


def test_enhanced_fetcher_fetch_date_range_unknown_type_raises() -> None:
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with pytest.raises(ValueError, match="Unknown data type"):
        fetcher.fetch_date_range("unknown_type", (2025, 1), (2025, 1))


def test_enhanced_fetcher_fetch_date_range_monthly_rollover() -> None:
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
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with pytest.raises(ValueError, match="無効な週番号"):
        fetcher.get_missing_data("sentinel_weekly_gender", [], target_weeks=[0])

    with pytest.raises(ValueError, match="無効な月番号"):
        fetcher.get_missing_data("sentinel_monthly_age", [], target_months=[13])


def test_enhanced_fetcher_get_missing_data_uses_current_year_when_end_year_none() -> None:
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
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    with patch.dict(fetcher.ENDPOINT_MAP, {}, clear=True):
        assert fetcher._get_source_url("unknown") is None


def test_enhanced_fetcher_parse_existing_files_rejects_out_of_range_year() -> None:
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False))

    params = fetcher._parse_existing_files([Path("sentinel_weekly_gender_1800_01.csv")], "sentinel_weekly_gender")
    assert params == []


def test_gender_sum_validator_read_source_file_handles_timeout_and_nonzero(tmp_path: Path) -> None:
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


def test_git_handler_and_storage_branch_paths(tmp_path: Path) -> None:
    git_handler = GitHandler(auto_commit=True)

    with patch("subprocess.run") as mock_run:
        assert git_handler.add_files([tmp_path / "missing.csv"]) is True
        mock_run.assert_not_called()

    existing = tmp_path / "exists.csv"
    existing.write_text("x", encoding="utf-8")

    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["git", "add"], stderr="fatal"),
    ):
        assert git_handler.add_files([existing]) is False

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            Mock(returncode=1),
            subprocess.CalledProcessError(1, ["git", "commit"], stderr="commit failed"),
        ]
        result = git_handler.commit("msg")
        assert result.success is False
        assert "commit failed" in (result.error or "")

    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["git", "config"], stderr="config failed"),
    ):
        assert git_handler.configure_user() is False

    storage = StorageManager(tmp_path / "data", {"auto_commit": False})
    disabled = storage.commit_changes()
    assert disabled.message == "Auto commit disabled"

    storage.git_handler.auto_commit = True
    with patch.object(storage.git_handler, "is_git_repo", return_value=False):
        not_repo = storage.commit_changes()
    assert not_repo.message == "Not a git repository"

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
    assert "データ更新" in mock_commit.call_args.args[0]


def test_storage_manager_hash_and_validation_edge_paths(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data", {"auto_commit": False})

    storage.hash_index_file.write_text("{invalid", encoding="utf-8")
    assert storage._load_hash_index() == {}

    storage.hash_index = {"hash1": str(tmp_path / "a.csv")}
    storage._remove_from_hash_index("hash1", str(tmp_path / "a.csv"))
    assert "hash1" not in storage.hash_index

    storage.hash_index = {"hash2": ["a.csv", "b.csv"]}
    storage._remove_from_hash_index("hash2", "a.csv")
    assert storage.hash_index["hash2"] == "b.csv"

    storage.hash_index = {"hash3": ["x.csv"]}
    storage._remove_from_hash_index("hash3", "x.csv")
    assert "hash3" not in storage.hash_index

    storage.hash_index = {"hash4": ["z.csv"]}
    with patch.object(Path, "open", side_effect=OSError("disk error")), pytest.raises(OSError, match="disk error"):
        storage._remove_from_hash_index("hash4", "z.csv")

    class BadLineData:
        def __bool__(self) -> bool:
            return True

    assert storage._count_lines(BadLineData()) is None  # type: ignore[arg-type]

    normalized = storage._normalize_timestamp("2025-01-01T00:00:00")
    assert "T" in normalized

    fallback = storage._normalize_timestamp(123)  # type: ignore[arg-type]
    assert "T" in fallback

    with (
        patch("src.managers.storage_manager.VALIDATION_MAX_FILE_SIZE_MB", 0.001),
        patch("src.managers.storage_manager.VALIDATION_SIZE_WARNING_THRESHOLD", 0.5),
    ):
        warning = storage._check_file_size_validation(b"x" * 700)
        assert warning["valid"] is True
        assert warning["warnings"]

        too_large = storage._check_file_size_validation(b"x" * 2000)
        assert too_large["valid"] is False
        assert too_large["errors"]

    class BadData:
        def decode(self, *_args, **_kwargs):
            raise ValueError("bad")

    encoding_result = storage._check_encoding_validation(BadData())  # type: ignore[arg-type]
    assert encoding_result["valid"] is False
    assert encoding_result["errors"]

    with patch("src.managers.storage_manager.VALIDATION_MAX_LINE_COUNT", 1):
        too_many_lines = storage._check_csv_format_validation("h1\nh2\n".encode("shift_jis"))
    assert too_many_lines["valid"] is False
    assert any("Too many lines" in error for error in too_many_lines["errors"])

    with patch("src.managers.storage_manager.VALIDATION_MIN_LINE_COUNT", 3):
        too_few_lines = storage._check_csv_format_validation("h1\n".encode("shift_jis"))
    assert too_few_lines["valid"] is False
    assert any("Too few lines" in error for error in too_few_lines["errors"])

    with patch("src.managers.storage_manager.VALIDATION_MAX_COLUMN_COUNT", 2):
        too_many_columns = storage._check_csv_format_validation("a,b,c\n".encode("shift_jis"))
    assert too_many_columns["valid"] is False
    assert any("Too many columns" in error for error in too_many_columns["errors"])

    with patch("src.managers.storage_manager.csv.reader", side_effect=csv.Error("bad csv")):
        csv_error = storage._check_csv_format_validation(b"any")
    assert csv_error["valid"] is False
    assert any("CSV format error" in error for error in csv_error["errors"])

    class OsErrorData:
        def decode(self, *_args, **_kwargs):
            raise OSError("decode failed")

    os_error = storage._check_csv_format_validation(OsErrorData())  # type: ignore[arg-type]
    assert os_error["valid"] is False
    assert any("Failed to check CSV format" in error for error in os_error["errors"])
