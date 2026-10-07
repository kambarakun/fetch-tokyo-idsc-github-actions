"""Golden tests that pin processed output for real-shaped raw data.

The raw fixtures are byte copies of data/raw files at commit 2bafd0135 (Shift_JIS, CRLF).
The processed fixtures are byte copies of data/processed at the same commit, except the two
medical_district total files, which this suite introduced and checked against the age totals.
"""

import csv
import io
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.cli.check_data_status import expected_processed_outputs
from src.processors.data_processor import DataProcessor

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURES_DIR = FIXTURES_DIR / "raw"
PROCESSED_FIXTURES_DIR = FIXTURES_DIR / "processed"
RAW_FIXTURES = sorted(RAW_FIXTURES_DIR.glob("*.csv"))

# A fixed mtime far in the past: any rewrite during reprocessing replaces it with the current time.
PAST_MTIME_NS = 1_000_000_000_000_000_000


def _processor_with_raw(tmp_path: Path, raw_files: list[Path]) -> tuple[DataProcessor, Path]:
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    for raw_file in raw_files:
        shutil.copy(raw_file, raw_dir / raw_file.name)
    return DataProcessor(data_dir), data_dir


def _process_all(processor: DataProcessor, data_dir: Path) -> dict[str, list[str]]:
    outputs = {}
    for raw_file in sorted((data_dir / "raw").glob("*.csv")):
        result = processor.process_file(raw_file)
        assert result.success, (raw_file.name, result.error)
        outputs[raw_file.name] = sorted(output.name for output in result.output_files)
    return outputs


def _total_row_by_column(path: Path) -> dict[str, str]:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    total_row = next(row for row in rows if row and row[0] == "合計")
    return dict(zip(rows[0], total_row, strict=False))


def _fixed_datetime(moment: datetime) -> type[datetime]:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return moment if tz is None else moment.astimezone(tz)

    return FixedDatetime


def test_fixture_inventory_covers_every_data_type() -> None:
    # Arrange / Act
    raw_names = [path.name for path in RAW_FIXTURES]

    # Assert: 9 data types plus the header-only medical_district total period
    assert len(raw_names) == 10
    assert "sentinel_weekly_medical_district_2005_10.csv" in raw_names
    assert len(list(PROCESSED_FIXTURES_DIR.glob("*.csv"))) == 23


def test_processed_output_matches_golden_bytes(tmp_path: Path) -> None:
    # Arrange
    processor, data_dir = _processor_with_raw(tmp_path, RAW_FIXTURES)

    # Act
    _process_all(processor, data_dir)

    # Assert
    produced = {path.name: path.read_bytes() for path in (data_dir / "processed").glob("*.csv")}
    golden = {path.name: path.read_bytes() for path in PROCESSED_FIXTURES_DIR.glob("*.csv")}
    assert sorted(produced) == sorted(golden)
    mismatched = [name for name in sorted(golden) if produced[name] != golden[name]]
    assert mismatched == []


@pytest.mark.parametrize("raw_file", RAW_FIXTURES, ids=lambda path: path.stem)
def test_processor_outputs_match_check_data_status_expectation(tmp_path: Path, raw_file: Path) -> None:
    # Arrange
    processor, data_dir = _processor_with_raw(tmp_path, [raw_file])
    raw_copy = data_dir / "raw" / raw_file.name

    # Act
    result = processor.process_file(raw_copy)

    # Assert
    assert result.success
    assert sorted(output.name for output in result.output_files) == expected_processed_outputs(raw_copy)


def test_header_only_medical_district_total_is_not_output(tmp_path: Path) -> None:
    # Arrange
    raw_file = RAW_FIXTURES_DIR / "sentinel_weekly_medical_district_2005_10.csv"
    processor, data_dir = _processor_with_raw(tmp_path, [raw_file])

    # Act
    result = processor.process_file(data_dir / "raw" / raw_file.name)

    # Assert
    assert sorted(output.name for output in result.output_files) == [
        "normalized_sentinel_weekly_medical_district_female_2005_10.csv",
        "normalized_sentinel_weekly_medical_district_male_2005_10.csv",
    ]


@pytest.mark.parametrize(
    ("district_total", "age_total", "expected_common_columns"),
    [
        (
            "normalized_sentinel_weekly_medical_district_total_2025_10.csv",
            "normalized_sentinel_weekly_age_total_2025_10.csv",
            23,
        ),
        (
            "normalized_sentinel_monthly_medical_district_total_2025_06.csv",
            "normalized_sentinel_monthly_age_total_2025_06.csv",
            9,
        ),
    ],
)
def test_medical_district_total_row_matches_age_total_row(
    tmp_path: Path, district_total: str, age_total: str, expected_common_columns: int
) -> None:
    # Arrange
    processor, data_dir = _processor_with_raw(tmp_path, RAW_FIXTURES)
    _process_all(processor, data_dir)
    processed_dir = data_dir / "processed"

    # Act
    district_row = _total_row_by_column(processed_dir / district_total)
    age_row = _total_row_by_column(processed_dir / age_total)

    # Assert: the district total is the published total, so it agrees with the age table by column name
    common_columns = [column for column in district_row if column and column in age_row]
    assert len(common_columns) == expected_common_columns
    assert {column: district_row[column] for column in common_columns} == {
        column: age_row[column] for column in common_columns
    }


def test_reprocessing_unchanged_raw_rewrites_nothing(tmp_path: Path) -> None:
    # Arrange
    processor, data_dir = _processor_with_raw(tmp_path, RAW_FIXTURES)
    _process_all(processor, data_dir)
    processed_dir = data_dir / "processed"
    outputs = sorted(processed_dir.glob("*.csv"))
    for output in outputs:
        os.utime(output, ns=(PAST_MTIME_NS, PAST_MTIME_NS))
    metadata_before = {path.name: path.read_bytes() for path in (processed_dir / ".metadata").glob("*.json")}

    # Act
    _process_all(processor, data_dir)

    # Assert
    assert [output.name for output in outputs if output.stat().st_mtime_ns != PAST_MTIME_NS] == []
    metadata_after = {path.name: path.read_bytes() for path in (processed_dir / ".metadata").glob("*.json")}
    assert metadata_after == metadata_before
    assert len(metadata_after) == len(outputs)


def test_reprocessing_revised_raw_keeps_created_and_updates_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    raw_name = "sentinel_weekly_age_2025_10.csv"
    processor, data_dir = _processor_with_raw(tmp_path, [RAW_FIXTURES_DIR / raw_name])
    raw_file = data_dir / "raw" / raw_name
    metadata_file = data_dir / "processed" / ".metadata" / "normalized_sentinel_weekly_age_male_2025_10.json"
    first_run = datetime(2026, 1, 1, tzinfo=UTC)
    second_run = datetime(2026, 2, 1, tzinfo=UTC)
    monkeypatch.setattr("src.processors.data_processor.datetime", _fixed_datetime(first_run))
    processor.process_file(raw_file)
    before = json.loads(metadata_file.read_text(encoding="utf-8"))
    # The first male data row (〜5ヶ月) starts with "0","3","12"; change 3 to 4.
    raw_bytes = raw_file.read_bytes()
    assert raw_bytes.count(b'"0","3","12"') >= 1
    raw_file.write_bytes(raw_bytes.replace(b'"0","3","12"', b'"0","4","12"', 1))
    monkeypatch.setattr("src.processors.data_processor.datetime", _fixed_datetime(second_run))

    # Act
    processor.process_file(raw_file)

    # Assert
    after = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert before["created"] == first_run.isoformat()
    assert after["created"] == first_run.isoformat()
    assert after["modified"] == second_run.isoformat()
    assert after["hash"]["value"] != before["hash"]["value"]
    assert after["_process"]["source_hash"] != before["_process"]["source_hash"]


@pytest.mark.parametrize(
    ("existing_content", "created_kept"),
    [
        pytest.param("{not json", False, id="unreadable-json"),
        pytest.param("[]", False, id="not-an-object"),
        pytest.param("{}", False, id="object-without-created"),
        pytest.param(
            json.dumps({"created": "2020-01-01T00:00:00+00:00", "metadata_version": "1.1.0"}),
            True,
            id="older-metadata-version",
        ),
    ],
)
def test_existing_metadata_that_differs_is_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_content: str, created_kept: bool
) -> None:
    # Arrange
    raw_name = "notifiable_weekly_2025_10.csv"
    processor, data_dir = _processor_with_raw(tmp_path, [RAW_FIXTURES_DIR / raw_name])
    metadata_file = data_dir / "processed" / ".metadata" / "normalized_notifiable_weekly_2025_10.json"
    metadata_file.write_text(existing_content, encoding="utf-8")
    run_at = datetime(2026, 3, 1, tzinfo=UTC)
    monkeypatch.setattr("src.processors.data_processor.datetime", _fixed_datetime(run_at))

    # Act
    processor.process_file(data_dir / "raw" / raw_name)

    # Assert
    rewritten = json.loads(metadata_file.read_text(encoding="utf-8"))
    expected_created = "2020-01-01T00:00:00+00:00" if created_kept else run_at.isoformat()
    assert rewritten["created"] == expected_created
    assert rewritten["modified"] == run_at.isoformat()
    assert rewritten["hash"]["value"]
