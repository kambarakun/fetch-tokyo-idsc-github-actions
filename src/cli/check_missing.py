#!/usr/bin/env python3
"""Validate continuity of weekly and monthly raw CSV datasets."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

Frequency = Literal["weekly", "monthly"]
Period = tuple[int, int]

DATA_TYPE_FREQUENCIES: dict[str, Frequency] = {
    "sentinel_weekly_gender": "weekly",
    "sentinel_weekly_age": "weekly",
    "sentinel_weekly_health_center": "weekly",
    "sentinel_weekly_medical_district": "weekly",
    "notifiable_weekly": "weekly",
    "sentinel_monthly_gender": "monthly",
    "sentinel_monthly_age": "monthly",
    "sentinel_monthly_health_center": "monthly",
    "sentinel_monthly_medical_district": "monthly",
}
DATA_TYPES = tuple(DATA_TYPE_FREQUENCIES)
# Keep provider availability boundaries explicit so a deleted earliest file is still detected as a gap.
DATA_TYPE_START_PERIODS: dict[str, Period] = {
    "sentinel_weekly_gender": (2000, 14),
    "sentinel_weekly_age": (2000, 1),
    "sentinel_weekly_health_center": (2000, 1),
    "sentinel_weekly_medical_district": (2000, 14),
    "notifiable_weekly": (2000, 1),
    "sentinel_monthly_gender": (2000, 4),
    "sentinel_monthly_age": (2000, 1),
    "sentinel_monthly_health_center": (2000, 1),
    "sentinel_monthly_medical_district": (2000, 1),
}

FILENAME_PATTERN = re.compile(
    r"^(?P<data_type>.+)_(?P<year>\d{4})_(?P<period>\d{1,2})\.csv$",
    re.IGNORECASE,
)

DEFAULT_WEEKLY_LAG = 2
DEFAULT_MONTHLY_LAG = 1
JST = ZoneInfo("Asia/Tokyo")


def weeks_in_year(year: int) -> int:
    """Return the number of ISO weeks in a year."""
    return date(year, 12, 28).isocalendar().week


def _period_to_dict(period: Period | None) -> dict[str, int] | None:
    if period is None:
        return None
    return {"year": period[0], "period": period[1]}


def _maximum_period(frequency: Frequency, year: int) -> int:
    return 12 if frequency == "monthly" else weeks_in_year(year)


def _monthly_watermark(as_of: date, lag: int) -> Period:
    month_index = as_of.year * 12 + as_of.month - 1 - lag
    year, zero_based_month = divmod(month_index, 12)
    return year, zero_based_month + 1


def _weekly_watermark(as_of: date, lag: int) -> Period:
    watermark_date = as_of - timedelta(weeks=lag)
    iso_year, iso_week, _ = watermark_date.isocalendar()
    return iso_year, iso_week


@dataclass
class ContinuityReport:
    """Continuity result for one supported dataset."""

    data_type: str
    frequency: Frequency
    start_year: int
    end_year: int
    expected_count: int
    actual_count: int
    target_start: Period | None
    target_end: Period | None
    watermark: Period
    missing_periods: list[dict[str, Any]] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    is_valid: bool = True
    error_messages: list[str] = field(default_factory=list)


class ContinuityValidator:
    """Validate the canonical set of Tokyo IDSC raw data files."""

    def __init__(
        self,
        data_dir: Path,
        *,
        as_of: date | None = None,
        weekly_lag: int = DEFAULT_WEEKLY_LAG,
        monthly_lag: int = DEFAULT_MONTHLY_LAG,
    ) -> None:
        if weekly_lag < 0 or monthly_lag < 0:
            raise ValueError("publication lag must be zero or greater")

        self.data_dir = data_dir
        self.as_of = as_of or datetime.now(JST).date()
        self.weekly_lag = weekly_lag
        self.monthly_lag = monthly_lag
        self.logger = logging.getLogger(__name__)
        self.watermarks: dict[Frequency, Period] = {
            "weekly": _weekly_watermark(self.as_of, weekly_lag),
            "monthly": _monthly_watermark(self.as_of, monthly_lag),
        }

    def validate_all(self, start_year: int | None = None, end_year: int | None = None) -> dict[str, ContinuityReport]:
        """Validate every supported dataset."""
        return {data_type: self.validate_data_type(data_type, start_year, end_year) for data_type in DATA_TYPES}

    def validate_data_type(
        self, data_type: str, start_year: int | None = None, end_year: int | None = None
    ) -> ContinuityReport:
        """Validate one dataset within its effective requested range."""
        try:
            frequency = DATA_TYPE_FREQUENCIES[data_type]
        except KeyError as error:
            raise ValueError(f"未対応のデータタイプです: {data_type}") from error

        watermark = self.watermarks[frequency]
        existing_periods, unexpected_files = self._collect_periods(data_type, frequency)
        availability_start = DATA_TYPE_START_PERIODS[data_type]

        requested_start = (start_year, 1) if start_year is not None else availability_start
        target_start = max(requested_start, availability_start)

        requested_end = (end_year, _maximum_period(frequency, end_year)) if end_year is not None else watermark
        target_end = min(requested_end, watermark)

        expected_periods = self._generate_expected_periods(frequency, target_start, target_end)
        periods_in_scope = existing_periods & expected_periods
        missing_periods = [
            {
                "year": year,
                "period": period,
                "type": frequency,
                "filename": f"{data_type}_{year}_{period:02d}.csv",
            }
            for year, period in sorted(expected_periods - periods_in_scope)
        ]

        error_messages: list[str] = []
        if not existing_periods:
            error_messages.append("データファイルが見つかりません")
        if missing_periods:
            error_messages.append(f"{len(missing_periods)}件の欠損期間があります")

        effective_start = target_start if expected_periods else None
        effective_end = target_end if expected_periods else None
        report = ContinuityReport(
            data_type=data_type,
            frequency=frequency,
            start_year=target_start[0],
            end_year=target_end[0],
            expected_count=len(expected_periods),
            actual_count=len(periods_in_scope),
            target_start=effective_start,
            target_end=effective_end,
            watermark=watermark,
            missing_periods=missing_periods,
            unexpected_files=unexpected_files,
            is_valid=bool(existing_periods) and not missing_periods,
            error_messages=error_messages,
        )
        if not report.is_valid:
            self.logger.warning("%s: %d missing periods", data_type, len(missing_periods))
        return report

    def _collect_periods(self, data_type: str, frequency: Frequency) -> tuple[set[Period], list[str]]:
        periods: set[Period] = set()
        unexpected_files: list[str] = []
        expected_prefix = f"{data_type.lower()}_"

        for file_path in self.data_dir.rglob("*.csv"):
            if not file_path.name.lower().startswith(expected_prefix):
                continue

            match = FILENAME_PATTERN.fullmatch(file_path.name)
            if match is None or match.group("data_type").lower() != data_type.lower():
                unexpected_files.append(str(file_path.relative_to(self.data_dir)))
                continue

            year = int(match.group("year"))
            period = int(match.group("period"))
            if not 1900 <= year <= 2100 or not 1 <= period <= _maximum_period(frequency, year):
                unexpected_files.append(str(file_path.relative_to(self.data_dir)))
                continue
            periods.add((year, period))

        return periods, sorted(unexpected_files)

    @staticmethod
    def _generate_expected_periods(frequency: Frequency, start: Period, end: Period) -> set[Period]:
        if start > end:
            return set()

        expected: set[Period] = set()
        for year in range(start[0], end[0] + 1):
            first_period = start[1] if year == start[0] else 1
            last_period = end[1] if year == end[0] else _maximum_period(frequency, year)
            expected.update((year, period) for period in range(first_period, last_period + 1))
        return expected

    def generate_report(
        self,
        reports: dict[str, ContinuityReport],
        output_format: Literal["json", "text", "markdown"] = "json",
        *,
        requested_start_year: int | None = None,
        requested_end_year: int | None = None,
    ) -> str:
        """Render validation results for machines or humans."""
        if output_format == "json":
            return self._generate_json_report(reports, requested_start_year, requested_end_year)
        if output_format == "text":
            return self._generate_text_report(reports)
        if output_format == "markdown":
            return self._generate_markdown_report(reports)
        raise ValueError(f"不正な出力形式です: {output_format}")

    def _generate_json_report(
        self,
        reports: dict[str, ContinuityReport],
        requested_start_year: int | None,
        requested_end_year: int | None,
    ) -> str:
        valid_count = sum(report.is_valid for report in reports.values())
        expected_count = sum(report.expected_count for report in reports.values())
        actual_count = sum(report.actual_count for report in reports.values())
        missing_count = sum(len(report.missing_periods) for report in reports.values())
        payload = {
            "summary": {
                "is_valid": valid_count == len(reports),
                "data_type_count": len(reports),
                "valid_type_count": valid_count,
                "invalid_type_count": len(reports) - valid_count,
                "expected_count": expected_count,
                "actual_count": actual_count,
                "missing_count": missing_count,
                "requested_start_year": requested_start_year,
                "requested_end_year": requested_end_year,
                "watermarks": {
                    "weekly": {
                        "as_of": self.as_of.isoformat(),
                        "lag": self.weekly_lag,
                        "year": self.watermarks["weekly"][0],
                        "period": self.watermarks["weekly"][1],
                    },
                    "monthly": {
                        "as_of": self.as_of.isoformat(),
                        "lag": self.monthly_lag,
                        "year": self.watermarks["monthly"][0],
                        "period": self.watermarks["monthly"][1],
                    },
                },
            },
            "data_types": {
                data_type: {
                    "frequency": report.frequency,
                    "start_year": report.start_year,
                    "end_year": report.end_year,
                    "target_period": {
                        "start": _period_to_dict(report.target_start),
                        "end": _period_to_dict(report.target_end),
                    },
                    "watermark": _period_to_dict(report.watermark),
                    "expected_count": report.expected_count,
                    "actual_count": report.actual_count,
                    "missing_count": len(report.missing_periods),
                    "is_valid": report.is_valid,
                    "missing_periods": report.missing_periods,
                    "unexpected_files": report.unexpected_files,
                    "error_messages": report.error_messages,
                }
                for data_type, report in reports.items()
            },
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    @staticmethod
    def _generate_text_report(reports: dict[str, ContinuityReport]) -> str:
        lines = ["データ連続性検証レポート", "=" * 80]
        for data_type, report in reports.items():
            status = "✅ 正常" if report.is_valid else "❌ 欠損あり"
            target_start = _period_to_dict(report.target_start)
            target_end = _period_to_dict(report.target_end)
            lines.extend(
                [
                    f"{data_type}: {status}",
                    f"  対象: {target_start} - {target_end}",
                    f"  期待数: {report.expected_count} / 実数: {report.actual_count} / 欠損: {len(report.missing_periods)}",
                ]
            )
        total_missing = sum(len(report.missing_periods) for report in reports.values())
        invalid_count = sum(not report.is_valid for report in reports.values())
        lines.extend(["", f"検証対象: {len(reports)}種 / 異常: {invalid_count}種 / 欠損: {total_missing}件"])
        return "\n".join(lines)

    def _generate_markdown_report(self, reports: dict[str, ContinuityReport]) -> str:
        lines = ["# データ連続性検証レポート", "", f"実行基準日: {self.as_of.isoformat()}", ""]
        lines.extend(
            [
                "| データタイプ | 対象期間 | 期待数 | 実数 | 欠損 | 状態 |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for data_type, report in reports.items():
            status = "✅" if report.is_valid else "❌"
            target = f"{report.target_start} - {report.target_end}"
            lines.append(
                f"| {data_type} | {target} | {report.expected_count} | {report.actual_count} | "
                f"{len(report.missing_periods)} | {status} |"
            )
        return "\n".join(lines)


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("0以上の整数を指定してください")
    return parsed


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("YYYY-MM-DD形式で指定してください") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="週次・月次データの連続性を検証")
    parser.add_argument("data_dir", nargs="?", default="data/raw", help="データディレクトリ")
    parser.add_argument("--start-year", type=int, help="検証開始年")
    parser.add_argument("--end-year", type=int, help="検証終了年")
    parser.add_argument("--data-type", choices=DATA_TYPES, help="検証するデータタイプ")
    parser.add_argument("--as-of", type=_iso_date, help="公開watermarkの基準日 (YYYY-MM-DD)")
    parser.add_argument(
        "--weekly-lag",
        type=_non_negative_int,
        default=DEFAULT_WEEKLY_LAG,
        help=f"週次公開lag (既定: {DEFAULT_WEEKLY_LAG}週)",
    )
    parser.add_argument(
        "--monthly-lag",
        type=_non_negative_int,
        default=DEFAULT_MONTHLY_LAG,
        help=f"月次公開lag (既定: {DEFAULT_MONTHLY_LAG}か月)",
    )
    parser.add_argument("--format", choices=["json", "text", "markdown"], default="text", help="出力形式")
    parser.add_argument("--output", type=Path, help="レポート保存先")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="ERROR",
        help="ログレベル",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run continuity validation and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.start_year is not None and args.end_year is not None and args.start_year > args.end_year:
        parser.error("--start-year は --end-year 以下で指定してください")

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")
    data_dir = Path(args.data_dir).expanduser().resolve()
    if not data_dir.is_dir():
        print(f"データディレクトリが見つかりません: {data_dir}", file=sys.stderr)
        return 1

    validator = ContinuityValidator(
        data_dir,
        as_of=args.as_of,
        weekly_lag=args.weekly_lag,
        monthly_lag=args.monthly_lag,
    )
    if args.data_type:
        reports = {args.data_type: validator.validate_data_type(args.data_type, args.start_year, args.end_year)}
    else:
        reports = validator.validate_all(args.start_year, args.end_year)

    output = validator.generate_report(
        reports,
        args.format,
        requested_start_year=args.start_year,
        requested_end_year=args.end_year,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"レポートを保存しました: {args.output}")
    else:
        print(output)

    return 0 if all(report.is_valid for report in reports.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
