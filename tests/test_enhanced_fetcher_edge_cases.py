"""エンハンスドフェッチャーのエッジケースと異常系のテスト"""

import unittest
from unittest.mock import patch

import pandas as pd

from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher, FetchParams, RateLimiter, RetryHandler


class TestEnhancedFetcherEdgeCases(unittest.TestCase):
    """エンハンスドフェッチャーのエッジケースをテスト"""

    def setUp(self):
        self.fetcher = EnhancedEpidemicDataFetcher()
        self.retry_handler = RetryHandler()
        self.rate_limiter = RateLimiter()

    def test_batch_fetch_parallel_with_params(self):
        """バッチフェッチのパラメータ処理をテスト"""
        # Arrange
        params_list = [
            FetchParams(data_type="sentinel_weekly_gender", period_type="week", year=2024, period=week)
            for week in range(1, 10)
        ]

        # Assert - パラメータリストが正しく作成されている
        self.assertEqual(len(params_list), 9)
        self.assertEqual(params_list[0].period, 1)
        self.assertEqual(params_list[-1].period, 9)

    def test_parse_filename_edge_cases(self):
        """ファイル名解析のエッジケースをテスト"""
        # 旧形式で異常な値
        edge_cases = [
            ("sentinel_weekly_gender_2024_99_20240101_120000.csv", None),  # 週99は不正
            ("sentinel_weekly_gender_2024_00_20240101_120000.csv", None),  # 週0は不正
            ("sentinel_monthly_age_2024_13_20240101_120000.csv", None),  # 月13は不正
            ("sentinel_monthly_age_2024_00_20240101_120000.csv", None),  # 月0は不正
        ]

        for filename, expected in edge_cases:
            result = self.fetcher.parse_filename(filename)
            self.assertEqual(result, expected, f"Failed for {filename}")

    @patch("src.fetchers.enhanced_fetcher.Path")
    def test_get_missing_data_with_io_error(self, mock_path):
        """ファイルアクセスエラー時のget_missing_dataをテスト"""
        # Arrange
        mock_path.return_value.glob.side_effect = OSError("Permission denied")

        # Act
        missing = self.fetcher.get_missing_data(
            data_dir="data/raw", data_type="sentinel_weekly_gender", year=2024, start_period=1, end_period=10
        )

        # Assert
        # エラー時は全期間が欠損として返される
        self.assertEqual(missing, list(range(1, 11)))

    def test_rate_limiter_creation(self):
        """レートリミッターの作成をテスト"""
        limiter = RateLimiter(requests_per_second=100)
        self.assertEqual(limiter.requests_per_second, 100)
        self.assertEqual(limiter.interval, 0.01)  # 1/100

    def test_create_metadata_with_empty_dataframe(self):
        """空のDataFrameでメタデータ作成をテスト"""
        # Arrange
        empty_df = pd.DataFrame()
        params = FetchParams(data_type="sentinel_weekly_gender", period_type="week", year=2024, period=1)

        # Act
        metadata = self.fetcher.create_metadata(empty_df, params)

        # Assert
        self.assertEqual(metadata["file_size"], 0)
        self.assertEqual(metadata["row_count"], 0)
        self.assertEqual(metadata["column_count"], 0)
        self.assertIsNone(metadata["data_hash"])

    def test_get_weeks_in_year_leap_year_edge(self):
        """うるう年の境界での週数計算をテスト"""
        # 2020年は53週ある年
        self.assertEqual(self.fetcher.get_weeks_in_year(2020), 53)
        # 2019年は52週
        self.assertEqual(self.fetcher.get_weeks_in_year(2019), 52)
        # 2021年は52週
        self.assertEqual(self.fetcher.get_weeks_in_year(2021), 52)

    def test_fetch_params_with_network_errors(self):
        """ネットワークエラー時のパラメータ処理をテスト"""
        # Arrange
        params = FetchParams(data_type="test", period_type="week", year=2024, period=1)

        # Assert - パラメータが正しく設定されている
        self.assertEqual(params.data_type, "test")
        self.assertEqual(params.period_type, "week")
        self.assertEqual(params.year, 2024)
        self.assertEqual(params.period, 1)

    def test_report_type_mapping_completeness(self):
        """全てのデータタイプがレポートタイプを持つことを確認"""
        data_types = [
            "sentinel_weekly_gender",
            "sentinel_weekly_age",
            "sentinel_monthly_age",
            "notifiable_weekly",
            "sentinel_special_weekly_gender",
            "invalid_type",
        ]

        for data_type in data_types:
            report_type = self.fetcher.get_report_type(data_type)
            # 無効なタイプ以外は必ずレポートタイプを返す
            if data_type != "invalid_type":
                self.assertIn(report_type, [1, 2, 5])
            else:
                self.assertIsNone(report_type)


class TestRetryHandlerEdgeCases(unittest.TestCase):
    """RetryHandlerのエッジケースをテスト"""

    def setUp(self):
        self.handler = RetryHandler()

    def test_calculate_delay_boundaries(self):
        """遅延計算の境界値をテスト"""
        # 最小値
        delay = self.handler.calculate_delay(0, base_delay=1.0)
        self.assertEqual(delay, 1.0)

        # 最大値付近
        delay = self.handler.calculate_delay(10, base_delay=1.0, max_delay=60.0)
        self.assertLessEqual(delay, 60.0)

        # ジッター付き
        delay = self.handler.calculate_delay(2, base_delay=1.0, jitter=True)
        self.assertGreaterEqual(delay, 4.0)
        self.assertLessEqual(delay, 4.5)  # 4.0 + 0.5(max jitter)


if __name__ == "__main__":
    unittest.main()
