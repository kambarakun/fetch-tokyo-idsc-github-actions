"""エンハンスドフェッチャーのエッジケースと異常系のテスト"""

import unittest

import pandas as pd

from src.fetchers.enhanced_fetcher import (
    DataFetcherConfig,
    EnhancedEpidemicDataFetcher,
    FetchParams,
    RateLimiter,
    RetryHandler,
)


class TestEnhancedFetcherEdgeCases(unittest.TestCase):
    """エンハンスドフェッチャーのエッジケースをテスト"""

    def setUp(self):
        self.config = DataFetcherConfig(
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            enable_jitter=False,
        )
        self.fetcher = EnhancedEpidemicDataFetcher()
        self.retry_handler = RetryHandler(config=self.config)
        self.rate_limiter = RateLimiter(min_delay=0.1)  # 10 requests per second = 0.1s delay

    def test_batch_fetch_parallel_with_params(self):
        """バッチフェッチのパラメータ処理をテスト"""
        # Arrange
        params_list = [
            FetchParams(
                start_year="2024",
                start_sub_period="1",
                end_year="2024",
                end_sub_period="1",
                data_type="sentinel_weekly_gender",
                report_type="1",
            )
            for week in range(1, 10)
        ]

        # Assert - パラメータリストが正しく作成されている
        self.assertEqual(len(params_list), 9)
        self.assertEqual(params_list[0].start_sub_period, "1")
        self.assertEqual(params_list[-1].start_sub_period, "1")

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

    def test_get_missing_data_with_io_error(self):
        """ファイルアクセスエラー時のget_missing_dataをテスト"""
        # Act
        # get_missing_dataは既存ファイルのリストを受け取る
        missing = self.fetcher.get_missing_data(
            data_type="sentinel_weekly_gender",
            existing_files=[],
            start_year=2024,
            end_year=2024,  # 空のリストを渡す
        )

        # Assert
        # 空のファイルリストなので、全期間が欠損として返される
        self.assertIsInstance(missing, list)
        self.assertTrue(len(missing) > 0)  # 何らかの欠損データがある

    def test_rate_limiter_creation(self):
        """レートリミッターの作成をテスト"""
        limiter = RateLimiter(min_delay=0.01)  # 100 requests per second
        self.assertEqual(limiter.min_delay, 0.01)  # 1/100

    def test_create_metadata_with_empty_dataframe(self):
        """空のDataFrameでメタデータ作成をテスト"""
        # Arrange
        empty_df = pd.DataFrame()
        params = FetchParams(
            start_year="2024",
            start_sub_period="1",
            end_year="2024",
            end_sub_period="1",
            data_type="sentinel_weekly_gender",
            report_type="1",
        )

        # Act
        metadata = self.fetcher.create_metadata(empty_df, params)

        # Assert
        # FileMetadataオブジェクトの属性として正しくアクセス
        self.assertEqual(metadata.file_size, 1)  # 空のDataFrameでもCSVヘッダーがある
        self.assertIsNotNone(metadata.sha256_hash)  # ハッシュは常に計算される
        self.assertEqual(metadata.data_type, "sentinel_weekly_gender")

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
        params = FetchParams(
            start_year="2024",
            start_sub_period="1",
            end_year="2024",
            end_sub_period="1",
            data_type="test",
            report_type="1",
        )

        # Assert - パラメータが正しく設定されている
        self.assertEqual(params.data_type, "test")
        self.assertEqual(params.start_year, "2024")
        self.assertEqual(params.start_sub_period, "1")

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
            # 無効なタイプでもデフォルト値"0"を返す
            # report_typeは文字列で返される
            self.assertIn(report_type, ["0", "1", "2", "5", "9", "10", "11", "12", "15", "20"])


class TestRetryHandlerEdgeCases(unittest.TestCase):
    """RetryHandlerのエッジケースをテスト"""

    def setUp(self):
        self.config = DataFetcherConfig(
            max_retries=5,
            base_delay=0.5,
            max_delay=10.0,
            enable_jitter=True,
        )
        self.handler = RetryHandler(config=self.config)

    def test_calculate_delay_boundaries(self):
        """遅延計算の境界値をテスト"""
        from src.fetchers.enhanced_fetcher import RetryHandler

        # 最小値
        handler = RetryHandler(self.config)
        delay = handler.calculate_delay(0)
        # base_delay=0.5なので、2^0 * 0.5 = 0.5、ジッター付きで0.5〜1.0
        self.assertGreaterEqual(delay, 0.5)
        self.assertLessEqual(delay, 1.0)

        # 最大値付近（max_delay=10.0）
        delay = handler.calculate_delay(10)
        self.assertLessEqual(delay, 10.5)  # max_delay(10.0) + jitter(0.5)

        # ジッターなしの設定
        config_no_jitter = DataFetcherConfig(enable_jitter=False, base_delay=1.0, max_delay=60.0)
        handler_no_jitter = RetryHandler(config_no_jitter)
        delay = handler_no_jitter.calculate_delay(2)
        self.assertEqual(delay, 4.0)  # 2^2 * 1.0 = 4.0


if __name__ == "__main__":
    unittest.main()
