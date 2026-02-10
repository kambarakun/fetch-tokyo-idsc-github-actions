"""Phase 3 coverage tests for data processor and fetcher edge branches."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, SupportsIndex
from unittest.mock import Mock, patch

import pytest

from src.fetchers.base_fetcher import TokyoEpidemicSurveillanceFetcher
from src.fetchers.enhanced_fetcher import DataFetcherConfig, RetryHandler
from src.processors.data_processor import DataProcessor


def _build_processor(tmp_path: Path) -> DataProcessor:
    """Create a data processor with isolated raw/processed directories."""
    data_dir = tmp_path / "data"
    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    return DataProcessor(data_dir)


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write a UTF-8 CSV file for processor-internal validation tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(rows)


def test_process_file_returns_not_found_for_missing_source(tmp_path: Path) -> None:
    """process_file returns a clear error when source file is missing."""
    # Arrange
    processor = _build_processor(tmp_path)
    missing_file = tmp_path / "data" / "raw" / "notifiable_weekly_2025_01.csv"

    # Act
    result = processor.process_file(missing_file)

    # Assert
    assert result.success is False
    assert result.error is not None
    assert "File not found" in result.error


def test_process_file_handles_unknown_category(tmp_path: Path) -> None:
    """process_file fails safely when parsed metadata has an unknown category."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "notifiable_weekly_2025_01.csv"
    source_file.write_text("header\n", encoding="shift_jis")

    # Act
    with patch.object(processor, "_extract_metadata_from_filename", return_value={"category": "mystery"}):
        result = processor.process_file(source_file)

    # Assert
    assert result.success is False
    assert result.error == "Unknown category: mystery"


def test_process_file_catches_metadata_parsing_exception(tmp_path: Path) -> None:
    """process_file catches parser exceptions and returns a failed result."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "sentinel_weekly_age_2025_01.csv"
    source_file.write_text("dummy\n", encoding="shift_jis")

    # Act
    with patch.object(processor, "_extract_metadata_from_filename", side_effect=ValueError("bad metadata")):
        result = processor.process_file(source_file)

    # Assert
    assert result.success is False
    assert result.source_path == source_file
    assert result.error == "bad metadata"


def test_process_all_counts_failed_files(tmp_path: Path) -> None:
    """process_all counts failed files and records error details."""
    # Arrange
    processor = _build_processor(tmp_path)
    raw_dir = tmp_path / "data" / "raw"
    (raw_dir / "notifiable_weekly_2025_01.csv").write_text("疾病名,報告数\nインフルエンザ,1\n", encoding="shift_jis")
    (raw_dir / "invalid_name.csv").write_text("x\n", encoding="shift_jis")

    # Act
    result = processor.process_all()

    # Assert
    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["errors"][0]["file"] == "invalid_name.csv"


def test_process_notifiable_returns_failure_when_data_header_missing(tmp_path: Path) -> None:
    """_process_notifiable returns failure when no disease header row exists."""
    # Arrange
    processor = _build_processor(tmp_path)
    metadata = {"category": "notifiable", "frequency": "weekly", "year": "2025", "period": "01"}
    source_file = tmp_path / "data" / "raw" / "notifiable_weekly_2025_01.csv"

    # Act
    result = processor._process_notifiable(["集計期間開始週,2025\n", "注釈のみ\n"], source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "データ開始行が見つかりません"


def test_process_notifiable_handles_write_failure(tmp_path: Path) -> None:
    """_process_notifiable returns failure when output write raises OSError."""
    # Arrange
    processor = _build_processor(tmp_path)
    metadata = {"category": "notifiable", "frequency": "weekly", "year": "2025", "period": "01"}
    source_file = tmp_path / "data" / "raw" / "notifiable_weekly_2025_01.csv"
    lines = ["疾病名,報告数\n", "インフルエンザ,1\n"]
    output_file = tmp_path / "data" / "processed" / "normalized_notifiable_weekly_2025_01.csv"
    original_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == output_file and isinstance(mode, str) and mode.startswith("w"):
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    # Act
    with patch.object(Path, "open", autospec=True, side_effect=failing_open):
        result = processor._process_notifiable(lines, source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "全数報告処理中にエラーが発生しました"


def test_process_gender_sections_skips_none_output_and_tracks_total(tmp_path: Path) -> None:
    """_process_gender_sections skips None output and records total file path."""
    # Arrange
    processor = _build_processor(tmp_path)
    metadata = {"aggregation": "age", "category": "sentinel", "frequency": "weekly", "year": "2025", "period": "01"}
    gender_sections = [{"gender": "男性"}, {"gender": "男女合計"}]
    total_file = tmp_path / "data" / "processed" / "normalized_sentinel_weekly_age_total_2025_01.csv"

    # Act
    with patch.object(processor, "_save_gender_section", side_effect=[None, total_file]):
        output_files, male_file, female_file, found_total_file, gender_info = processor._process_gender_sections(
            ["x\n"], gender_sections, metadata
        )

    # Assert
    assert output_files == [total_file]
    assert male_file is None
    assert female_file is None
    assert found_total_file == total_file
    assert gender_info[total_file] == "total"


def test_process_gender_sections_covers_unknown_gender_fallthrough(tmp_path: Path) -> None:
    """_process_gender_sections falls through when gender is neither male/female/total."""
    # Arrange
    processor = _build_processor(tmp_path)
    metadata = {"aggregation": "age", "category": "sentinel", "frequency": "weekly", "year": "2025", "period": "01"}
    unknown_file = tmp_path / "data" / "processed" / "normalized_sentinel_weekly_age_unknown_2025_01.csv"
    gender_sections = [{"gender": "不明"}]

    # Act
    with patch.object(processor, "_save_gender_section", return_value=unknown_file):
        output_files, male_file, female_file, total_file, gender_info = processor._process_gender_sections(
            ["x\n"], gender_sections, metadata
        )

    # Assert
    assert output_files == [unknown_file]
    assert male_file is None
    assert female_file is None
    assert total_file is None
    assert gender_info[unknown_file] == "unknown"


def test_validate_total_file_skips_on_empty_check_error(tmp_path: Path) -> None:
    """_validate_total_file exits when empty-file check raises OSError."""
    # Arrange
    processor = _build_processor(tmp_path)
    total_file = tmp_path / "data" / "processed" / "total.csv"

    # Act
    with (
        patch.object(processor, "_is_empty_data_file", side_effect=OSError("io error")),
        patch("src.processors.data_processor.logger.warning") as mock_warning,
    ):
        processor._validate_total_file(total_file, tmp_path / "m.csv", tmp_path / "f.csv")

    # Assert
    assert any("totalセクションの空判定に失敗しました" in str(call.args[0]) for call in mock_warning.call_args_list)


def test_process_sentinel_returns_failure_when_no_output_file_created(tmp_path: Path) -> None:
    """_process_sentinel returns failure when section processing yields no files."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "sentinel_weekly_age_2025_01.csv"
    metadata = {"aggregation": "age", "category": "sentinel", "frequency": "weekly", "year": "2025", "period": "01"}

    # Act
    with (
        patch.object(processor, "_detect_gender_sections", return_value=[{"gender": "男性"}]),
        patch.object(processor, "_process_gender_sections", return_value=([], None, None, None, {})),
    ):
        result = processor._process_sentinel(["x\n"], source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "出力ファイルが生成されませんでした"


def test_process_sentinel_handles_processing_exception(tmp_path: Path) -> None:
    """_process_sentinel catches value errors and returns failed result."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "sentinel_weekly_age_2025_01.csv"
    metadata = {"aggregation": "age", "category": "sentinel", "frequency": "weekly", "year": "2025", "period": "01"}

    # Act
    with patch.object(processor, "_detect_gender_sections", side_effect=ValueError("boom")):
        result = processor._process_sentinel(["x\n"], source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "定点監視処理中にエラーが発生しました"


def test_process_sentinel_simple_returns_failure_without_data_header(tmp_path: Path) -> None:
    """_process_sentinel_simple fails when it cannot find a data header row."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "sentinel_weekly_gender_2025_01.csv"
    metadata = {
        "aggregation": "gender",
        "category": "sentinel",
        "frequency": "weekly",
        "year": "2025",
        "period": "01",
    }

    # Act
    result = processor._process_sentinel_simple(["注釈のみ\n"], source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "データ開始行が見つかりません"


def test_process_sentinel_simple_handles_write_exception(tmp_path: Path) -> None:
    """_process_sentinel_simple catches output write errors."""
    # Arrange
    processor = _build_processor(tmp_path)
    source_file = tmp_path / "data" / "raw" / "sentinel_weekly_gender_2025_01.csv"
    metadata = {
        "aggregation": "gender",
        "category": "sentinel",
        "frequency": "weekly",
        "year": "2025",
        "period": "01",
    }
    output_file = tmp_path / "data" / "processed" / "normalized_sentinel_weekly_gender_2025_01.csv"
    original_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == output_file and isinstance(mode, str) and mode.startswith("w"):
            raise OSError("write failed")
        return original_open(path, *args, **kwargs)

    # Act
    with patch.object(Path, "open", autospec=True, side_effect=failing_open):
        result = processor._process_sentinel_simple(["疾病名,件数\n", "インフルエンザ,1\n"], source_file, metadata)

    # Assert
    assert result.success is False
    assert result.error == "定点監視単純処理中にエラーが発生しました"


def test_detect_gender_sections_covers_len_guard_and_unknown_gender(tmp_path: Path) -> None:
    """_detect_gender_sections handles malformed split and unknown gender values."""
    # Arrange
    processor = _build_processor(tmp_path)

    class WeirdLine(str):
        def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
            return ["性別"]

    lines = [WeirdLine("性別,"), '性別,"その他"']

    # Act
    sections = processor._detect_gender_sections(lines)

    # Assert
    assert sections == []


def test_save_gender_section_returns_none_when_no_section_data(tmp_path: Path) -> None:
    """_save_gender_section returns None when extractor returns empty rows."""
    # Arrange
    processor = _build_processor(tmp_path)
    section = {"gender": "男性", "start_line": 0}
    metadata = {"category": "sentinel", "frequency": "weekly", "aggregation": "age", "year": "2025", "period": "01"}

    # Act
    with patch.object(processor, "_extract_section_data", return_value=[]):
        output = processor._save_gender_section(["x\n"], section, metadata)

    # Assert
    assert output is None


def test_save_gender_section_handles_extractor_exception(tmp_path: Path) -> None:
    """_save_gender_section returns None when extractor raises ValueError."""
    # Arrange
    processor = _build_processor(tmp_path)
    section = {"gender": "男性", "start_line": 0}
    metadata = {"category": "sentinel", "frequency": "weekly", "aggregation": "age", "year": "2025", "period": "01"}

    # Act
    with patch.object(processor, "_extract_section_data", side_effect=ValueError("bad section")):
        output = processor._save_gender_section(["x\n"], section, metadata)

    # Assert
    assert output is None


def test_extract_section_data_skips_blank_comment_and_stops_at_total(tmp_path: Path) -> None:
    """_extract_section_data skips blank/comment rows and stops at the total row."""
    # Arrange
    processor = _build_processor(tmp_path)
    lines = [
        '性別,"男性"\n',
        "前置き\n",
        "疾病名,インフルエンザ,RSウイルス\n",
        "\n",
        "*注釈行\n",
        "0歳,1,2\n",
        '"合計",1,2\n',
        "0-4歳,9,9\n",
    ]
    section = {"gender": "男性", "start_line": 0}

    # Act
    extracted = processor._extract_section_data(lines, section)

    # Assert
    assert extracted == ["疾病名,インフルエンザ,RSウイルス\n", "0歳,1,2\n", '"合計",1,2\n']


def test_extract_metadata_from_filename_handles_attribute_error() -> None:
    """_extract_metadata_from_filename returns None when filename is not a string."""
    # Arrange
    processor = DataProcessor(Path("data"))

    # Act
    metadata = processor._extract_metadata_from_filename(None)  # type: ignore[arg-type]

    # Assert
    assert metadata is None


def test_verify_total_calculation_logs_row_count_mismatch(tmp_path: Path) -> None:
    """_verify_total_calculation exits early when row counts differ."""
    # Arrange
    processor = _build_processor(tmp_path)
    male_file = tmp_path / "data" / "processed" / "male.csv"
    female_file = tmp_path / "data" / "processed" / "female.csv"
    total_file = tmp_path / "data" / "processed" / "total.csv"
    _write_csv(male_file, [["h", "x"], ["r1", "1"], ["r2", "2"]])
    _write_csv(female_file, [["h", "x"], ["r1", "1"]])
    _write_csv(total_file, [["h", "x"], ["r1", "2"]])

    # Act
    with patch("src.processors.data_processor.logger.warning") as mock_warning:
        processor._verify_total_calculation(male_file, female_file, total_file)

    # Assert
    assert any("行数不一致" in str(call.args[0]) for call in mock_warning.call_args_list)


def test_verify_total_calculation_handles_empty_total_data_header_branch(tmp_path: Path) -> None:
    """_verify_total_calculation handles empty data arrays without header processing."""
    # Arrange
    processor = _build_processor(tmp_path)
    file_path = tmp_path / "data" / "processed" / "any.csv"

    # Act
    with (
        patch.object(processor, "_read_csv_data", side_effect=[[], [], []]),
        patch("src.processors.data_processor.logger.info") as mock_info,
    ):
        processor._verify_total_calculation(file_path, file_path, file_path)

    # Assert
    assert any("total検証OK" in str(call.args[0]) for call in mock_info.call_args_list)


def test_verify_total_calculation_skips_zero_zero_positive_total(tmp_path: Path) -> None:
    """_verify_total_calculation skips metadata-like rows where male/female are zero."""
    # Arrange
    processor = _build_processor(tmp_path)
    male_file = tmp_path / "data" / "processed" / "male_skip.csv"
    female_file = tmp_path / "data" / "processed" / "female_skip.csv"
    total_file = tmp_path / "data" / "processed" / "total_skip.csv"
    header = ["区分", "インフルエンザ"]
    _write_csv(male_file, [header, ["合計", "0"]])
    _write_csv(female_file, [header, ["合計", "0"]])
    _write_csv(total_file, [header, ["合計", "5"]])

    # Act
    with patch("src.processors.data_processor.logger.info") as mock_info:
        processor._verify_total_calculation(male_file, female_file, total_file)

    # Assert
    assert any("total検証OK" in str(call.args[0]) for call in mock_info.call_args_list)


def test_verify_total_calculation_skips_value_error_cells(tmp_path: Path) -> None:
    """_verify_total_calculation ignores non-numeric cells via ValueError fallback."""
    # Arrange
    processor = _build_processor(tmp_path)
    male_file = tmp_path / "data" / "processed" / "male_value.csv"
    female_file = tmp_path / "data" / "processed" / "female_value.csv"
    total_file = tmp_path / "data" / "processed" / "total_value.csv"
    header = ["区分", "インフルエンザ"]
    _write_csv(male_file, [header, ["合計", "N/A"]])
    _write_csv(female_file, [header, ["合計", "1"]])
    _write_csv(total_file, [header, ["合計", "2"]])

    # Act
    with (
        patch("src.processors.data_processor.logger.warning") as mock_warning,
        patch("src.processors.data_processor.logger.info") as mock_info,
    ):
        processor._verify_total_calculation(male_file, female_file, total_file)

    # Assert
    assert any("数値変換失敗" in str(call.args[0]) for call in mock_warning.call_args_list)
    assert any("total検証OK" in str(call.args[0]) for call in mock_info.call_args_list)


def test_verify_total_calculation_warns_when_many_mismatches(tmp_path: Path) -> None:
    """_verify_total_calculation emits warning branch when mismatches are 10+."""
    # Arrange
    processor = _build_processor(tmp_path)
    male_file = tmp_path / "data" / "processed" / "male_many.csv"
    female_file = tmp_path / "data" / "processed" / "female_many.csv"
    total_file = tmp_path / "data" / "processed" / "total_many.csv"
    header = ["区分", "インフルエンザ"]
    male_rows = [header] + [[f"r{i}", "1"] for i in range(10)]
    female_rows = [header] + [[f"r{i}", "1"] for i in range(10)]
    total_rows = [header] + [[f"r{i}", "5"] for i in range(10)]
    _write_csv(male_file, male_rows)
    _write_csv(female_file, female_rows)
    _write_csv(total_file, total_rows)

    # Act
    with patch("src.processors.data_processor.logger.warning") as mock_warning:
        processor._verify_total_calculation(male_file, female_file, total_file)

    # Assert
    assert any("10件の不一致" in str(call.args[0]) for call in mock_warning.call_args_list)


def test_verify_total_calculation_handles_read_exception(tmp_path: Path) -> None:
    """_verify_total_calculation catches read exceptions and continues safely."""
    # Arrange
    processor = _build_processor(tmp_path)
    file_path = tmp_path / "data" / "processed" / "missing.csv"

    # Act
    with (
        patch.object(processor, "_read_csv_data", side_effect=OSError("read failed")),
        patch("src.processors.data_processor.logger.exception") as mock_exception,
    ):
        processor._verify_total_calculation(file_path, file_path, file_path)

    # Assert
    assert any("total検証失敗" in str(call.args[0]) for call in mock_exception.call_args_list)


def test_verify_cross_dataset_consistency_handles_missing_required_files(tmp_path: Path) -> None:
    """_verify_cross_dataset_consistency skips periods with missing files."""
    # Arrange
    processor = _build_processor(tmp_path)
    existing = processor.processed_dir / "normalized_sentinel_weekly_age_total_2025_01.csv"
    existing.write_text("合計,1\n", encoding="utf-8")
    missing = processor.processed_dir / "missing.csv"

    # Act
    with (
        patch.object(
            processor,
            "_collect_periods_for_verification",
            return_value={"weekly_2025_01": {"age": existing, "health_center": missing}},
        ),
        patch.object(processor, "_extract_total_row") as mock_extract_total_row,
    ):
        processor._verify_cross_dataset_consistency()

    # Assert
    mock_extract_total_row.assert_not_called()


def test_verify_cross_dataset_consistency_skips_when_total_row_missing(tmp_path: Path) -> None:
    """_verify_cross_dataset_consistency skips when any total row is absent."""
    # Arrange
    processor = _build_processor(tmp_path)
    age = processor.processed_dir / "normalized_sentinel_weekly_age_total_2025_01.csv"
    hc = processor.processed_dir / "normalized_sentinel_weekly_health_center_total_2025_01.csv"
    age.write_text("x\n", encoding="utf-8")
    hc.write_text("y\n", encoding="utf-8")

    # Act
    with (
        patch.object(
            processor,
            "_collect_periods_for_verification",
            return_value={"weekly_2025_01": {"age": age, "health_center": hc}},
        ),
        patch.object(processor, "_extract_total_row", side_effect=[None, ["合計", "1"]]),
        patch("src.processors.data_processor.logger.debug") as mock_debug,
    ):
        processor._verify_cross_dataset_consistency()

    # Assert
    assert any("合計行が見つかりません" in str(call.args[0]) for call in mock_debug.call_args_list)


def test_verify_cross_dataset_consistency_logs_column_length_mismatch_and_many_diffs(tmp_path: Path) -> None:
    """_verify_cross_dataset_consistency handles column-size mismatch and >3 diff summary."""
    # Arrange
    processor = _build_processor(tmp_path)
    age = processor.processed_dir / "normalized_sentinel_weekly_age_total_2025_01.csv"
    hc = processor.processed_dir / "normalized_sentinel_weekly_health_center_total_2025_01.csv"
    age.write_text("x\n", encoding="utf-8")
    hc.write_text("y\n", encoding="utf-8")
    age_total = ["合計", "10", "11", "12", "13", "14"]
    hc_total = ["合計", "20", "21", "22", "23"]

    # Act
    with (
        patch.object(
            processor,
            "_collect_periods_for_verification",
            return_value={"weekly_2025_01": {"age": age, "health_center": hc}},
        ),
        patch.object(processor, "_extract_total_row", side_effect=[age_total, hc_total]),
        patch("src.processors.data_processor.logger.debug") as mock_debug,
        patch("src.processors.data_processor.logger.warning") as mock_warning,
    ):
        processor._verify_cross_dataset_consistency()

    # Assert
    assert any("列数不一致" in str(call.args[0]) for call in mock_debug.call_args_list)
    assert any("他1列で不一致" in str(call.args[0]) for call in mock_warning.call_args_list)


def test_verify_cross_dataset_consistency_handles_exceptions_per_period(tmp_path: Path) -> None:
    """_verify_cross_dataset_consistency keeps processing when one period raises."""
    # Arrange
    processor = _build_processor(tmp_path)
    age = processor.processed_dir / "normalized_sentinel_weekly_age_total_2025_01.csv"
    hc = processor.processed_dir / "normalized_sentinel_weekly_health_center_total_2025_01.csv"
    age.write_text("x\n", encoding="utf-8")
    hc.write_text("y\n", encoding="utf-8")

    # Act
    with (
        patch.object(
            processor,
            "_collect_periods_for_verification",
            return_value={"weekly_2025_01": {"age": age, "health_center": hc}},
        ),
        patch.object(processor, "_extract_total_row", side_effect=OSError("boom")),
        patch("src.processors.data_processor.logger.exception") as mock_exception,
    ):
        processor._verify_cross_dataset_consistency()

    # Assert
    assert any("整合性チェックエラー" in str(call.args[0]) for call in mock_exception.call_args_list)


def test_collect_periods_for_verification_skips_invalid_shape_and_unsupported_aggregation(tmp_path: Path) -> None:
    """_collect_periods_for_verification skips malformed names and medical_district files."""
    # Arrange
    processor = _build_processor(tmp_path)
    malformed = processor.processed_dir / "normalized_sentinel_weekly_total_2025.csv"
    unsupported = processor.processed_dir / "normalized_sentinel_weekly_medical_district_total_2025_01.csv"
    malformed.write_text("x\n", encoding="utf-8")
    unsupported.write_text("x\n", encoding="utf-8")

    # Act
    periods = processor._collect_periods_for_verification()

    # Assert
    assert periods == {}


def test_extract_total_row_returns_none_when_reader_raises(tmp_path: Path) -> None:
    """_extract_total_row returns None when CSV reading raises OSError."""
    # Arrange
    processor = _build_processor(tmp_path)
    test_file = tmp_path / "data" / "processed" / "broken.csv"

    # Act
    with patch.object(processor, "_read_csv_data", side_effect=OSError("broken")):
        row = processor._extract_total_row(test_file)

    # Assert
    assert row is None


def test_base_fetcher_post_request_covers_unreachable_guard() -> None:
    """_post_request raises RuntimeError when raise_for_status unexpectedly does not raise."""
    # Arrange
    fetcher = TokyoEpidemicSurveillanceFetcher()
    response = Mock(status_code=500, content=b"")
    response.raise_for_status.return_value = None

    # Act / Assert
    with patch.object(fetcher.session, "post", return_value=response), pytest.raises(RuntimeError, match="Unreachable"):
        fetcher._post_request("dlwgender.do", "1")


@pytest.mark.asyncio
async def test_retry_handler_raises_when_retry_config_is_corrupted() -> None:
    """execute_with_retry fails explicitly when max_retries is unexpectedly negative."""
    # Arrange
    handler = RetryHandler(DataFetcherConfig(max_retries=1, enable_jitter=False))
    handler.config.max_retries = -1

    async def noop() -> str:
        return "ok"

    # Act / Assert
    with pytest.raises(RuntimeError, match="Retry loop exited unexpectedly"):
        await handler.execute_with_retry(noop)
