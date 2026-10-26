"""
年境界での週番号計算とデータ取得の検証テスト

このテストモジュールは、ISO週番号の年境界での正確性を検証します。
特に以下のケースをカバーします:
- 年末の週が翌年に属する場合(例: 2024年12月30日は2025年第1週)
- 年始の週が前年に属する場合(例: 2025年1月1日は2024年第52/53週)
- 2週前のデータ取得が年をまたぐ場合(例: 2025年第1週の2週前は2024年第51週)
"""

import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.cli.fetch_data import DataCollector
from src.fetchers import periods
from src.fetchers.enhanced_fetcher import DataFetcherConfig, EnhancedEpidemicDataFetcher
from src.managers.config_manager import CollectionConfig, DataCollectionConfig, StorageConfig
from src.managers.storage_manager import StorageManager


class TestYearBoundaryWeekCalculation(unittest.TestCase):
    """年境界でのISO週番号計算の検証"""

    def test_year_end_week_belongs_to_next_year(self):
        """年末の週が翌年の第1週に属するケース"""
        # 2024年12月30日(月曜日)は2025年第1週
        test_date = date(2024, 12, 30)
        iso_year, iso_week, _ = test_date.isocalendar()

        self.assertEqual(iso_year, 2025, "2024年12月30日はISO年では2025年")
        self.assertEqual(iso_week, 1, "2024年12月30日はISO週では第1週")

    def test_year_start_week_belongs_to_previous_year(self):
        """年始の週が前年の最終週に属するケース"""
        # 2023年1月1日(日曜日)は2022年第52週
        test_date = date(2023, 1, 1)
        iso_year, iso_week, _ = test_date.isocalendar()

        self.assertEqual(iso_year, 2022, "2023年1月1日はISO年では2022年")
        self.assertEqual(iso_week, 52, "2023年1月1日はISO週では第52週")

    def test_two_weeks_ago_crosses_year_boundary(self):
        """2週前の計算が年をまたぐケース"""
        # 2025年1月6日(月曜日、第2週)の2週前は2024年第52週
        current_date = date(2025, 1, 6)
        two_weeks_ago = current_date - timedelta(weeks=2)

        current_iso = current_date.isocalendar()
        two_weeks_ago_iso = two_weeks_ago.isocalendar()

        self.assertEqual(current_iso.year, 2025)
        self.assertEqual(current_iso.week, 2)
        self.assertEqual(two_weeks_ago_iso.year, 2024)
        self.assertEqual(two_weeks_ago_iso.week, 52)

    def test_week_53_exists_in_leap_years(self):
        """閏年で第53週が存在するケース"""
        # 2020年は第53週が存在する
        last_day_2020 = date(2020, 12, 31)
        _, iso_week, _ = last_day_2020.isocalendar()

        self.assertEqual(iso_week, 53, "2020年には第53週が存在する")

    def test_storage_manager_month_from_week_year_boundary(self):
        """StorageManagerの週番号→月変換が年境界で正確"""
        config = {"storage": {"auto_commit": False}}
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StorageManager(base_path=Path(tmpdir), config=config)

            # 2025年第1週(2024年12月30日-2025年1月5日)の開始日は12月
            # ISO週は月曜日始まりなので、週の開始日の月を返す
            month_week1 = manager._get_month_from_week(2025, 1)
            self.assertEqual(month_week1, 12, "2025年第1週の開始日(月曜日)は12月")

            # 2024年第52週(2024年12月23日-29日)は12月
            month_week52 = manager._get_month_from_week(2024, 52)
            self.assertEqual(month_week52, 12, "2024年第52週は12月")

            # 通常の週(年境界をまたがない)
            # 2025年第2週(2025年1月6日-12日)は1月
            month_week2 = manager._get_month_from_week(2025, 2)
            self.assertEqual(month_week2, 1, "2025年第2週は1月")


class TestYearBoundaryDataFetching(unittest.TestCase):
    """年境界でのデータ取得の検証"""

    def setUp(self):
        """テスト用の設定"""
        self.config = DataFetcherConfig()
        self.fetcher = EnhancedEpidemicDataFetcher(self.config)
        self.fetcher.session = Mock()

    def test_fetch_across_year_boundary_weeks(self):
        """年をまたぐ週のデータ取得が正常に動作"""
        # 2024年52週のパラメータ
        weeks_in_2024 = self.fetcher._get_weeks_in_year(2024)
        self.assertIn(weeks_in_2024, [52, 53], "2024年の週数は52または53")

        # 2025年第1週もISO週番号で正しく計算できることを確認
        weeks_in_2025 = self.fetcher._get_weeks_in_year(2025)
        self.assertGreaterEqual(weeks_in_2025, 52, "2025年も最低52週ある")

        # 年境界をまたぐ場合のISO週番号計算が正確
        # 2024年12月30日は2025年第1週になる
        year_end_date = date(2024, 12, 30)
        iso_cal = year_end_date.isocalendar()
        self.assertEqual(iso_cal.year, 2025, "2024年12月30日はISO年2025")
        self.assertEqual(iso_cal.week, 1, "2024年12月30日は第1週")

    def test_get_weeks_in_year_accuracy(self):
        """年ごとの週数取得の正確性"""
        # 通常年(52週)
        weeks_2023 = self.fetcher._get_weeks_in_year(2023)
        self.assertEqual(weeks_2023, 52, "2023年は52週")

        # 第53週がある年
        weeks_2020 = self.fetcher._get_weeks_in_year(2020)
        self.assertEqual(weeks_2020, 53, "2020年は53週")

        # 2024年
        weeks_2024 = self.fetcher._get_weeks_in_year(2024)
        self.assertIn(weeks_2024, [52, 53], "2024年は52または53週")


class TestWorkflowYearBoundaryScenarios(unittest.TestCase):
    """GitHub Actionsワークフローでの年境界シナリオ検証"""

    def test_monday_morning_execution_across_year(self):
        """月曜朝実行時の年境界処理(2025年1月6日のケース)"""
        # 2025年1月6日(月曜日)の実行を想定
        execution_date = date(2025, 1, 6)

        current_week = execution_date.isocalendar().week
        previous_week = (execution_date - timedelta(weeks=1)).isocalendar()
        two_weeks_ago = (execution_date - timedelta(weeks=2)).isocalendar()

        # 現在週: 2025年第2週
        self.assertEqual(execution_date.isocalendar().year, 2025)
        self.assertEqual(current_week, 2)

        # 前週: 2025年第1週
        self.assertEqual(previous_week.year, 2025)
        self.assertEqual(previous_week.week, 1)

        # 2週前: 2024年第52週
        self.assertEqual(two_weeks_ago.year, 2024)
        self.assertEqual(two_weeks_ago.week, 52)

        # GitHub Actionsでの週番号指定を検証
        target_weeks = f"{two_weeks_ago.week},{previous_week.week},{current_week}"
        self.assertEqual(target_weeks, "52,1,2", "正しい週番号のカンマ区切り形式")

    def test_december_execution_normal_case(self):
        """12月の通常実行(年境界をまたがないケース)"""
        # 2024年12月16日(月曜日)の実行を想定
        execution_date = date(2024, 12, 16)

        current_week = execution_date.isocalendar().week
        previous_week = (execution_date - timedelta(weeks=1)).isocalendar()
        two_weeks_ago = (execution_date - timedelta(weeks=2)).isocalendar()

        # 全て2024年内
        self.assertEqual(execution_date.isocalendar().year, 2024)
        self.assertEqual(previous_week.year, 2024)
        self.assertEqual(two_weeks_ago.year, 2024)

        # 連続した週番号
        self.assertEqual(current_week - previous_week.week, 1)
        self.assertEqual(previous_week.week - two_weeks_ago.week, 1)


class TestISOWeekFormatting(unittest.TestCase):
    """ISO週番号のフォーマット検証"""

    def test_zero_padding_for_single_digit_weeks(self):
        """1桁の週番号がゼロパディングされることを確認"""
        # ファイル名形式: sentinel_weekly_gender_2025_01.csv
        data_type = "sentinel_weekly_gender"
        year = 2025
        week = 1

        # ゼロパディング形式
        filename = f"{data_type}_{year}_{week:02d}.csv"
        self.assertEqual(filename, "sentinel_weekly_gender_2025_01.csv")

        # 2桁の場合
        week_52 = 52
        filename_52 = f"{data_type}_{year}_{week_52:02d}.csv"
        self.assertEqual(filename_52, "sentinel_weekly_gender_2025_52.csv")

    def test_parse_filename_with_year_boundary(self):
        """年境界のファイル名解析"""
        # 2024年第52週のファイル
        filename_2024 = "sentinel_weekly_gender_2024_52.csv"
        parts = filename_2024.replace(".csv", "").split("_")

        year = int(parts[-2])
        week = int(parts[-1])

        self.assertEqual(year, 2024)
        self.assertEqual(week, 52)

        # 2025年第1週のファイル
        filename_2025 = "sentinel_weekly_gender_2025_01.csv"
        parts = filename_2025.replace(".csv", "").split("_")

        year = int(parts[-2])
        week = int(parts[-1].lstrip("0") or "0")

        self.assertEqual(year, 2025)
        self.assertEqual(week, 1)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _jst_at(utc_text: str) -> datetime:
    """What now_jst() returns at the given UTC instant"""
    return _utc(utc_text).astimezone(periods.JST)


@pytest.mark.parametrize(
    ("now", "monthly", "year", "expected"),
    [
        ("2026-09-24T08:00", False, 2026, 39),
        ("2026-09-24T08:00", False, 2025, 52),
        ("2026-09-24T08:00", False, 2027, 0),
        ("2026-09-24T08:00", True, 2026, 9),
        ("2026-09-24T08:00", True, 2025, 12),
        ("2026-09-24T08:00", True, 2027, 0),
        # 2025-12-30 is ISO 2026-W01: the calendar year 2025 keeps all 52 weeks
        ("2025-12-30T08:00", False, 2025, 52),
        ("2025-12-30T08:00", False, 2026, 1),
        ("2025-12-30T08:00", True, 2025, 12),
        ("2025-12-30T08:00", True, 2026, 0),
        # 2027-01-01 is ISO 2026-W53: no week of 2027 exists yet
        ("2027-01-01T08:00", False, 2027, 0),
        ("2027-01-01T08:00", False, 2026, 53),
        ("2027-01-01T08:00", True, 2027, 1),
        ("2028-01-01T08:00", False, 2028, 0),
        # 2026-12-31T16:00Z is already 2027-01-01 in JST
        ("2026-12-31T16:00", False, 2027, 0),
        ("2026-12-31T16:00", False, 2026, 53),
        ("2026-12-31T16:00", True, 2027, 1),
    ],
)
def test_max_period_for_year_table(now, monthly, year, expected):
    """The upper bound uses the JST ISO week-year / week, and the JST year / month"""
    assert periods.max_period_for_year(year, monthly=monthly, now=_utc(now)) == expected


def test_now_jst_is_aware_tokyo_time():
    """now_jst returns an aware datetime at UTC+9"""
    assert periods.now_jst().utcoffset() == timedelta(hours=9)


def test_weeks_in_year_counts_iso_weeks():
    """weeks_in_year returns 53 for long ISO years and 52 otherwise"""
    assert [periods.weeks_in_year(y) for y in (2020, 2025, 2026, 2027)] == [53, 52, 53, 52]


def test_get_weeks_in_year_methods_delegate_to_periods(tmp_path):
    """Both _get_weeks_in_year methods keep answering through periods.weeks_in_year"""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0))
    collector = _collector(tmp_path)

    assert [fetcher._get_weeks_in_year(y) for y in (2025, 2026)] == [52, 53]
    assert [collector._get_weeks_in_year(y) for y in (2025, 2026)] == [52, 53]


def _weekly_files(tmp_path: Path, year: int, last_week: int) -> list[Path]:
    return [tmp_path / f"sentinel_weekly_gender_{year}_{week:02d}.csv" for week in range(1, last_week + 1)]


def _periods(params) -> list[str]:
    return [f"{p.start_year}-{p.start_sub_period}" for p in params]


def _collector(tmp_path: Path) -> DataCollector:
    config = DataCollectionConfig(
        collection=CollectionConfig(start_year=2025, data_types_to_collect=["sentinel_weekly_gender"]),
        storage=StorageConfig(base_directory=str(tmp_path / "raw")),
    )
    return DataCollector(config, mode="force")


def test_get_missing_data_at_year_boundary():
    """52 weeks of 2025 on 2025-12-30, and no week (but January) of 2027 on 2027-01-01"""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0))

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2025-12-30T08:00")):
        missing_2025 = fetcher.get_missing_data("sentinel_weekly_gender", [], 2025, 2025)
    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2027-01-01T08:00")):
        missing_2027 = fetcher.get_missing_data("sentinel_weekly_gender", [], 2027, 2027)
        monthly_2027 = fetcher.get_missing_data("sentinel_monthly_gender", [], 2027, 2027)

    assert _periods(missing_2025) == [f"2025-{week}" for week in range(1, 53)]
    assert missing_2027 == []
    assert _periods(monthly_2027) == ["2027-1"]


def test_get_missing_data_at_year_boundary_defaults_end_year_to_jst_year():
    """end_year=None means the JST calendar year, already 2027 at 2026-12-31T16:00Z"""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0))

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-12-31T16:00")):
        missing = fetcher.get_missing_data("sentinel_monthly_gender", [], 2027)

    assert _periods(missing) == ["2027-1"]


def test_generate_all_params_at_year_boundary(tmp_path):
    """52 weeks of 2025 on 2025-12-30, and no week (but January) of 2027 on 2027-01-01"""
    collector = _collector(tmp_path)

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2025-12-30T08:00")):
        params_2025 = collector._generate_all_params("sentinel_weekly_gender", 2025, 2025, False)
    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2027-01-01T08:00")):
        params_2027 = collector._generate_all_params("sentinel_weekly_gender", 2027, 2027, False)
        monthly_2027 = collector._generate_all_params("sentinel_monthly_gender", 2027, 2027, True)

    assert _periods(params_2025) == [f"2025-{week}" for week in range(1, 53)]
    assert params_2027 == []
    assert _periods(monthly_2027) == ["2027-1"]


def test_daily_window_across_new_year_keeps_previous_year_weeks(tmp_path):
    """The daily window (target_weeks) across New Year still finds the previous year's last weeks"""
    fetcher = EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0))

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2025-12-29T08:00")):
        before = fetcher.get_missing_data(
            "sentinel_weekly_gender", _weekly_files(tmp_path, 2025, 50), 2025, 2025, target_weeks=[51, 52, 1]
        )
    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-01-05T08:00")):
        after = fetcher.get_missing_data(
            "sentinel_weekly_gender", _weekly_files(tmp_path, 2025, 51), 2025, 2026, target_weeks=[52, 1, 2]
        )

    assert _periods(before) == ["2025-51", "2025-52"]
    assert _periods(after) == ["2025-52", "2026-1", "2026-2"]


if __name__ == "__main__":
    unittest.main()
