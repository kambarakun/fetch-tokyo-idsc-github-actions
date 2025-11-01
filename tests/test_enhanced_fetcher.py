"""
拡張フェッチャーのユニットテスト
"""

import asyncio
import hashlib
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.enhanced_fetcher import (
    DataFetcherConfig,
    EnhancedEpidemicDataFetcher,
    FetchParams,
    FetchResult,
    FileMetadata,
    RateLimiter,
    RetryHandler,
)


class TestRetryHandler(unittest.TestCase):
    """RetryHandlerのテスト"""

    def setUp(self):
        self.config = DataFetcherConfig(max_retries=3, base_delay=1.0, max_delay=10.0, enable_jitter=False)
        self.handler = RetryHandler(self.config)

    def test_calculate_delay(self):
        """遅延計算のテスト"""
        # ジッターなしでテスト
        self.assertEqual(self.handler.calculate_delay(0), 1.0)
        self.assertEqual(self.handler.calculate_delay(1), 2.0)
        self.assertEqual(self.handler.calculate_delay(2), 4.0)
        self.assertEqual(self.handler.calculate_delay(3), 8.0)
        self.assertEqual(self.handler.calculate_delay(4), 10.0)  # max_delay

    def test_calculate_delay_with_jitter(self):
        """ジッター付き遅延計算のテスト"""
        self.config.enable_jitter = True
        handler = RetryHandler(self.config)

        delay = handler.calculate_delay(1)
        # ベース遅延2.0 + ジッター0-0.5
        self.assertGreaterEqual(delay, 2.0)
        self.assertLessEqual(delay, 2.5)

    @patch("asyncio.sleep")
    async def test_execute_with_retry_success(self, mock_sleep):
        """成功時のリトライ処理テスト"""

        async def success_func():
            return "success"

        result = await self.handler.execute_with_retry(success_func)
        self.assertEqual(result, "success")
        mock_sleep.assert_not_called()

    @patch("asyncio.sleep")
    async def test_execute_with_retry_eventual_success(self, mock_sleep):
        """最終的に成功するリトライ処理のテスト"""
        call_count = 0

        async def eventual_success_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection failed")
            return "success"

        result = await self.handler.execute_with_retry(eventual_success_func)
        self.assertEqual(result, "success")
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("asyncio.sleep")
    async def test_execute_with_retry_max_exceeded(self, mock_sleep):
        """最大リトライ回数超過のテスト"""

        async def always_fail_func():
            raise ConnectionError("Always fails")

        with self.assertRaises(ConnectionError):
            await self.handler.execute_with_retry(always_fail_func)

        self.assertEqual(mock_sleep.call_count, 3)


class TestRateLimiter(unittest.TestCase):
    """RateLimiterのテスト"""

    @patch("time.time")
    @patch("asyncio.sleep")
    async def test_rate_limiting(self, mock_sleep, mock_time):
        """レート制限のテスト"""
        # 時刻をモック
        mock_time.side_effect = [0, 0.5, 1.5]

        limiter = RateLimiter(min_delay=1.0)

        # 最初のリクエスト
        await limiter.wait_if_needed()
        mock_sleep.assert_not_called()

        # 2回目のリクエスト（0.5秒後）
        await limiter.wait_if_needed()
        mock_sleep.assert_called_once_with(0.5)

        # 3回目のリクエスト（1.5秒後）
        mock_sleep.reset_mock()
        await limiter.wait_if_needed()
        mock_sleep.assert_not_called()


class TestEnhancedEpidemicDataFetcher(unittest.TestCase):
    """EnhancedEpidemicDataFetcherのテスト"""

    def setUp(self):
        self.config = DataFetcherConfig(max_retries=2, base_delay=0.1, timeout=10, rate_limit_delay=0.1)
        self.fetcher = EnhancedEpidemicDataFetcher(self.config)

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_with_retry_success(self, mock_post):
        """正常なデータ取得のテスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"test,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得（基本フェッチャーのパラメータのみを渡す）
        result = self.fetcher.fetch_with_retry(
            self.fetcher.fetch_csv_sentinel_weekly_gender,
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data, b"test,data\n1,2")
        self.assertIsNotNone(result.metadata)
        self.assertEqual(result.metadata.file_size, len(b"test,data\n1,2"))

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_with_retry_failure(self, mock_post):
        """データ取得失敗のテスト"""
        mock_post.side_effect = ConnectionError("Network error")

        result = self.fetcher.fetch_with_retry(
            self.fetcher.fetch_csv_sentinel_weekly_gender,
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertIsNotNone(result.error)

    def test_get_weeks_in_year(self):
        """年の週数取得のテスト"""
        # 通常年
        self.assertEqual(self.fetcher._get_weeks_in_year(2023), 52)
        # 53週の年
        self.assertEqual(self.fetcher._get_weeks_in_year(2020), 53)

    def test_get_report_type(self):
        """レポートタイプ取得のテスト"""
        self.assertEqual(self.fetcher._get_report_type("sentinel_weekly_gender"), "1")
        self.assertEqual(self.fetcher._get_report_type("sentinel_weekly_age"), "0")
        self.assertEqual(self.fetcher._get_report_type("sentinel_monthly_gender"), "15")
        self.assertEqual(self.fetcher._get_report_type("notifiable_weekly"), "20")
        self.assertEqual(self.fetcher._get_report_type("unknown"), "0")

    def test_create_metadata(self):
        """メタデータ生成のテスト"""
        data = b"test data"
        params = {
            "data_type": "sentinel_weekly_gender",
            "report_type": "1",
            "start_year": "2025",
            "start_sub_period": "1",
            "end_year": "2025",
            "end_sub_period": "1",
        }

        metadata = self.fetcher._create_metadata(data, params)

        self.assertEqual(metadata.data_type, "sentinel_weekly_gender")
        self.assertEqual(metadata.file_size, len(data))
        self.assertEqual(metadata.sha256_hash, hashlib.sha256(data).hexdigest())
        self.assertIn("2025", metadata.date_range)

    def test_get_missing_data(self):
        """欠損データ特定のテスト（新形式）"""
        # 新形式の既存ファイルのモック
        existing_files = [
            Path("sentinel_weekly_gender_2025_01.csv"),
            Path("sentinel_weekly_gender_2025_03.csv"),
        ]

        missing_params = self.fetcher.get_missing_data(
            "sentinel_weekly_gender", existing_files, start_year=2025, end_year=2025
        )

        # 2025年の週2が欠損として検出されるはず
        missing_weeks = [int(p.start_sub_period) for p in missing_params]
        self.assertIn(2, missing_weeks)
        self.assertNotIn(1, missing_weeks)
        self.assertNotIn(3, missing_weeks)

    def test_parse_both_filename_formats(self):
        """新旧ファイル名形式の解析テスト"""
        # 新旧両方の形式を含むファイルリスト
        existing_files = [
            Path("sentinel_weekly_gender_2025_01.csv"),  # 新形式
            Path("sentinel_weekly_gender_2025_2_20250108_120000.csv"),  # 旧形式
            Path("sentinel_weekly_gender_2025_03.csv"),  # 新形式
        ]

        # private methodの直接テスト
        params = self.fetcher._parse_existing_files(existing_files, "sentinel_weekly_gender")

        # 3つのファイルすべてが正しく解析される
        self.assertEqual(len(params), 3)

        # 各ファイルの週番号が正しく抽出される
        periods = sorted([int(p.start_sub_period) for p in params])
        self.assertEqual(periods, [1, 2, 3])

    @patch("time.sleep")
    def test_fetch_date_range(self, mock_sleep):
        """日付範囲での一括取得のテスト"""
        with patch.object(self.fetcher, "fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = FetchResult(success=True, data=b"test", metadata=None)

            results = self.fetcher.fetch_date_range("sentinel_weekly_gender", start_date=(2025, 1), end_date=(2025, 3))

            self.assertEqual(len(results), 3)
            self.assertEqual(mock_fetch.call_count, 3)
            # レート制限の確認
            self.assertEqual(mock_sleep.call_count, 3)


class TestFetchParams(unittest.TestCase):
    """FetchParamsのテスト"""

    def test_fetch_params_creation(self):
        """FetchParams作成のテスト"""
        params = FetchParams(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
            data_type="sentinel_weekly_gender",
            report_type="1",
        )

        self.assertEqual(params.start_year, "2025")
        self.assertEqual(params.start_sub_period, "1")
        self.assertEqual(params.pref_code, "13")  # デフォルト値
        self.assertEqual(params.hc_code, "00")  # デフォルト値


class TestFileMetadata(unittest.TestCase):
    """FileMetadataのテスト"""

    def test_file_metadata_creation(self):
        """FileMetadata作成のテスト"""
        now = datetime.now()
        metadata = FileMetadata(
            filename="test.csv",
            data_type="sentinel_weekly_gender",
            date_range="2025_1",
            timestamp=now,
            file_size=1024,
            sha256_hash="abc123",
        )

        self.assertEqual(metadata.filename, "test.csv")
        self.assertEqual(metadata.encoding, "shift_jis")  # デフォルト値
        self.assertEqual(metadata.file_size, 1024)


if __name__ == "__main__":
    # 非同期テストのサポート
    def async_test(coro):
        def wrapper(*args, **kwargs):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro(*args, **kwargs))
            finally:
                loop.close()

        return wrapper

    # 非同期テストメソッドをラップ
    for attr_name in dir(TestRetryHandler):
        attr = getattr(TestRetryHandler, attr_name)
        if callable(attr) and attr_name.startswith("test_") and asyncio.iscoroutinefunction(attr):
            setattr(TestRetryHandler, attr_name, async_test(attr))

    for attr_name in dir(TestRateLimiter):
        attr = getattr(TestRateLimiter, attr_name)
        if callable(attr) and attr_name.startswith("test_") and asyncio.iscoroutinefunction(attr):
            setattr(TestRateLimiter, attr_name, async_test(attr))

    unittest.main()
