"""Integration-focused tests for the canonical continuity validator."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from src.cli.check_missing import DATA_TYPES, ContinuityValidator, main, weeks_in_year

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def write_periods(data_dir: Path, data_type: str, periods: list[tuple[int, int]]) -> None:
    for year, period in periods:
        (data_dir / f"{data_type}_{year}_{period:02d}.csv").write_text("test data", encoding="utf-8")


def periods_for_year(year: int, last_period: int) -> list[tuple[int, int]]:
    return [(year, period) for period in range(1, last_period + 1)]


def test_validates_all_nine_data_types_including_multiword_suffixes(tmp_path: Path) -> None:
    as_of = date(2025, 3, 17)  # ISO week 12; watermarks are week 10 and February.
    for data_type in DATA_TYPES:
        last_period = 2 if "monthly" in data_type else 10
        write_periods(tmp_path, data_type, periods_for_year(2025, last_period))

    validator = ContinuityValidator(tmp_path, as_of=as_of, weekly_lag=2, monthly_lag=1)
    reports = validator.validate_all(start_year=2025, end_year=2025)

    assert set(reports) == set(DATA_TYPES)
    assert reports["sentinel_weekly_health_center"].actual_count == 10
    assert reports["sentinel_weekly_medical_district"].actual_count == 10
    assert reports["sentinel_monthly_health_center"].actual_count == 2
    assert reports["sentinel_monthly_medical_district"].actual_count == 2
    assert all(report.is_valid for report in reports.values())


def test_actual_count_is_limited_to_requested_years_and_watermark(tmp_path: Path) -> None:
    data_type = "sentinel_weekly_age"
    periods = [(2023, 52)]
    periods += periods_for_year(2024, weeks_in_year(2024))
    periods += periods_for_year(2025, 11)  # Week 11 is newer than the watermark.
    write_periods(tmp_path, data_type, periods)

    validator = ContinuityValidator(tmp_path, as_of=date(2025, 3, 17), weekly_lag=2)
    report = validator.validate_data_type(data_type, start_year=2024, end_year=2025)

    assert report.target_start == (2024, 1)
    assert report.target_end == (2025, 10)
    assert report.expected_count == weeks_in_year(2024) + 10
    assert report.actual_count == report.expected_count
    assert report.actual_count <= report.expected_count
    assert report.is_valid


@pytest.mark.parametrize(
    ("data_type", "first_period", "last_period"),
    [
        ("sentinel_weekly_gender", 14, 29),
        ("sentinel_weekly_medical_district", 14, 29),
        ("sentinel_monthly_gender", 4, 6),
    ],
)
def test_default_start_respects_each_data_types_publication_start(
    tmp_path: Path, data_type: str, first_period: int, last_period: int
) -> None:
    write_periods(tmp_path, data_type, [(2000, period) for period in range(first_period, last_period + 1)])

    validator = ContinuityValidator(tmp_path, as_of=date(2000, 7, 31), weekly_lag=2, monthly_lag=1)
    report = validator.validate_data_type(data_type)

    assert report.target_start == (2000, first_period)
    assert report.is_valid


def test_weekly_range_crosses_a_53_week_year(tmp_path: Path) -> None:
    data_type = "notifiable_weekly"
    periods = [*periods_for_year(2020, 53), (2021, 1)]
    write_periods(tmp_path, data_type, periods)

    validator = ContinuityValidator(tmp_path, as_of=date(2021, 1, 18), weekly_lag=2)
    report = validator.validate_data_type(data_type, start_year=2020, end_year=2021)

    assert weeks_in_year(2020) == 53
    assert report.target_end == (2021, 1)
    assert report.expected_count == 54
    assert report.actual_count == 54
    assert report.is_valid


def test_publication_lag_excludes_unpublished_current_periods(tmp_path: Path) -> None:
    write_periods(tmp_path, "sentinel_weekly_gender", periods_for_year(2026, 29))
    write_periods(tmp_path, "sentinel_monthly_age", periods_for_year(2026, 6))

    validator = ContinuityValidator(tmp_path, as_of=date(2026, 7, 27), weekly_lag=2, monthly_lag=1)
    weekly = validator.validate_data_type("sentinel_weekly_gender", start_year=2026, end_year=2026)
    monthly = validator.validate_data_type("sentinel_monthly_age", start_year=2026, end_year=2026)

    assert weekly.watermark == (2026, 29)
    assert weekly.target_end == (2026, 29)
    assert weekly.missing_periods == []
    assert monthly.watermark == (2026, 6)
    assert monthly.target_end == (2026, 6)
    assert monthly.missing_periods == []


def test_real_gap_controls_exit_code_and_json_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_type = "sentinel_weekly_health_center"
    write_periods(tmp_path, data_type, [(2025, 1), (2025, 3)])
    args = [
        str(tmp_path),
        "--data-type",
        data_type,
        "--start-year",
        "2025",
        "--end-year",
        "2025",
        "--as-of",
        "2025-01-27",
        "--weekly-lag",
        "2",
        "--format",
        "json",
    ]

    assert main(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {
        "is_valid": False,
        "data_type_count": 1,
        "valid_type_count": 0,
        "invalid_type_count": 1,
        "expected_count": 3,
        "actual_count": 2,
        "missing_count": 1,
        "requested_start_year": 2025,
        "requested_end_year": 2025,
        "watermarks": {
            "weekly": {"as_of": "2025-01-27", "lag": 2, "year": 2025, "period": 3},
            "monthly": {"as_of": "2025-01-27", "lag": 1, "year": 2024, "period": 12},
        },
    }
    assert payload["data_types"][data_type]["target_period"] == {
        "start": {"year": 2025, "period": 1},
        "end": {"year": 2025, "period": 3},
    }
    assert payload["data_types"][data_type]["missing_periods"] == [
        {
            "year": 2025,
            "period": 2,
            "type": "weekly",
            "filename": "sentinel_weekly_health_center_2025_02.csv",
        }
    ]

    write_periods(tmp_path, data_type, [(2025, 2)])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["is_valid"] is True


def test_missing_data_type_is_invalid(tmp_path: Path) -> None:
    validator = ContinuityValidator(tmp_path, as_of=date(2025, 3, 17))

    report = validator.validate_data_type("sentinel_monthly_medical_district", start_year=2025, end_year=2025)

    assert not report.is_valid
    assert report.actual_count == 0
    assert report.expected_count == 2
    assert len(report.missing_periods) == 2
    assert "データファイルが見つかりません" in report.error_messages


def test_cli_rejects_invalid_year_range_and_lag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as year_error:
        main([str(tmp_path), "--start-year", "2026", "--end-year", "2025"])
    assert year_error.value.code == 2

    with pytest.raises(SystemExit) as lag_error:
        main([str(tmp_path), "--weekly-lag", "-1"])
    assert lag_error.value.code == 2

    with pytest.raises(SystemExit) as date_error:
        main([str(tmp_path), "--as-of", "2025/01/13"])
    assert date_error.value.code == 2

    assert main([str(tmp_path / "missing")]) == 1


def test_empty_effective_range_is_reported_without_inventing_missing_periods(tmp_path: Path) -> None:
    data_type = "notifiable_weekly"
    write_periods(tmp_path, data_type, [(2024, 52)])
    validator = ContinuityValidator(tmp_path, as_of=date(2025, 1, 6), weekly_lag=2)

    report = validator.validate_data_type(data_type, start_year=2025, end_year=2025)
    payload = json.loads(validator.generate_report({data_type: report}, "json"))

    assert report.target_start is None
    assert report.target_end is None
    assert report.expected_count == 0
    assert report.actual_count == 0
    assert report.is_valid
    assert payload["data_types"][data_type]["target_period"] == {"start": None, "end": None}

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty_report = ContinuityValidator(empty_dir, as_of=date(2025, 1, 6), weekly_lag=2).validate_data_type(
        data_type, start_year=2025, end_year=2025
    )
    assert empty_report.is_valid
    assert empty_report.error_messages == []


def test_rejects_unsupported_type_and_collects_malformed_target_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="publication lag"):
        ContinuityValidator(tmp_path, weekly_lag=-1)

    validator = ContinuityValidator(tmp_path, as_of=date(2025, 1, 27), weekly_lag=2)
    with pytest.raises(ValueError, match="未対応"):
        validator.validate_data_type("unsupported")

    write_periods(tmp_path, "sentinel_weekly_age", [(2025, 1), (2025, 2), (2025, 3)])
    (tmp_path / "sentinel_weekly_age_invalid.csv").write_text("bad", encoding="utf-8")
    (tmp_path / "sentinel_weekly_age_2025_54.csv").write_text("bad", encoding="utf-8")
    report = validator.validate_data_type("sentinel_weekly_age", start_year=2025, end_year=2025)

    assert report.is_valid
    assert report.unexpected_files == [
        "sentinel_weekly_age_2025_54.csv",
        "sentinel_weekly_age_invalid.csv",
    ]


def test_markdown_invalid_format_and_output_file_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_type = "sentinel_monthly_age"
    write_periods(tmp_path, data_type, [(2025, 1), (2025, 2)])
    validator = ContinuityValidator(tmp_path, as_of=date(2025, 3, 17), monthly_lag=1)
    report = validator.validate_data_type(data_type, start_year=2025, end_year=2025)

    markdown = validator.generate_report({data_type: report}, "markdown")
    assert "| sentinel_monthly_age |" in markdown
    assert "| データタイプ |" in validator.generate_report({}, "markdown")
    with pytest.raises(ValueError, match="不正な出力形式"):
        validator.generate_report({data_type: report}, cast(Any, "xml"))

    output_path = tmp_path / "reports" / "continuity.json"
    result = main(
        [
            str(tmp_path),
            "--data-type",
            data_type,
            "--start-year",
            "2025",
            "--end-year",
            "2025",
            "--as-of",
            "2025-03-17",
            "--format",
            "json",
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary"]["is_valid"] is True
    assert "レポートを保存しました" in capsys.readouterr().out


def test_legacy_validator_shim_runs_without_an_installed_project() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(PROJECT_ROOT / "scripts" / "validate_continuity.py"), "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "週次・月次データの連続性を検証" in result.stdout
