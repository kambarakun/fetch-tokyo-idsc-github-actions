"""基本フェッチャーのテスト - APIレスポンス処理とエラーハンドリング"""

import unittest
from unittest.mock import Mock, patch

import requests

from src.fetchers.base_fetcher import TokyoEpidemicSurveillanceFetcher


class TestBaseFetcher(unittest.TestCase):
    """基本フェッチャーの詳細なテスト"""

    def setUp(self):
        self.fetcher = TokyoEpidemicSurveillanceFetcher()

    @patch("requests.Session.post")
    def test_post_request_success(self, mock_post):
        """POSTリクエスト成功時の処理をテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"test,data\n1,2"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

        # Assert
        self.assertEqual(result, b"test,data\n1,2")
        mock_post.assert_called_once()

    @patch("requests.Session.post")
    def test_post_request_failure_status_codes(self, mock_post):
        """様々なHTTPエラーステータスコードのテスト"""
        # Arrange
        error_codes = [400, 403, 404, 500, 502, 503]

        for status_code in error_codes:
            with self.subTest(status_code=status_code):
                mock_response = Mock()
                mock_response.status_code = status_code
                mock_post.return_value = mock_response

                # Act & Assert
                with self.assertRaises(Exception) as context:
                    self.fetcher._post_request(
                        "http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0"
                    )
                self.assertIn(str(status_code), str(context.exception))

    @patch("requests.Session.post")
    def test_fetch_sentinel_weekly_gender(self, mock_post):
        """定点監視週報（性別）データ取得のテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"gender,count\nM,100\nF,200"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher.fetch_csv_sentinel_weekly_gender(
            start_year="2024", start_sub_period="1", end_year="2024", end_sub_period="10"
        )

        # Assert
        self.assertEqual(result, b"gender,count\nM,100\nF,200")
        call_args = mock_post.call_args
        self.assertIn("data", call_args.kwargs)
        self.assertEqual(call_args.kwargs["data"]["val(startYear)"], "2024")

    @patch("requests.Session.post")
    def test_fetch_sentinel_weekly_age(self, mock_post):
        """定点監視週報（年齢）データ取得のテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"age,count\n0-9,50\n10-19,30"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher.fetch_csv_sentinel_weekly_age()

        # Assert
        self.assertIsNotNone(result)
        self.assertIn(b"age", result)

    @patch("requests.Session.post")
    def test_fetch_sentinel_monthly_age(self, mock_post):
        """定点監視月報（年齢）データ取得のテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"month,age,count\n1,0-9,100"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher.fetch_csv_sentinel_monthly_age(
            start_year="2024", start_sub_period="1", end_year="2024", end_sub_period="12"
        )

        # Assert
        self.assertIsNotNone(result)
        self.assertIn(b"month", result)

    @patch("requests.Session.post")
    def test_fetch_notifiable_weekly(self, mock_post):
        """届出疾患週報データ取得のテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"disease,count\nInfluenza,1000"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher.fetch_csv_notifiable_weekly()

        # Assert
        self.assertIsNotNone(result)
        self.assertIn(b"disease", result)

    @patch("requests.Session.post")
    def test_fetch_sentinel_special_weekly_gender(self, mock_post):
        """定点監視特別週報（性別）データ取得のテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"special,gender,count"
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher.fetch_csv_sentinel_special_weekly_gender()

        # Assert
        self.assertIsNotNone(result)
        self.assertIn(b"special", result)

    @patch("requests.Session.post")
    def test_timeout_handling(self, mock_post):
        """タイムアウト処理のテスト"""
        # Arrange
        mock_post.side_effect = requests.Timeout("Request timeout")

        # Act & Assert
        with self.assertRaises(requests.Timeout):
            self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

    @patch("requests.Session.post")
    def test_connection_error_handling(self, mock_post):
        """接続エラー処理のテスト"""
        # Arrange
        mock_post.side_effect = requests.ConnectionError("Connection failed")

        # Act & Assert
        with self.assertRaises(requests.ConnectionError):
            self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

    def test_endpoint_map_completeness(self):
        """エンドポイントマップの完全性をテスト"""
        # Assert
        self.assertIn("1", self.fetcher.ENDPOINT_MAP)
        self.assertIn("2", self.fetcher.ENDPOINT_MAP)
        self.assertIn("5", self.fetcher.ENDPOINT_MAP)

        # 各エンドポイントがファイル名であることを確認
        for _, endpoint in self.fetcher.ENDPOINT_MAP.items():
            self.assertTrue(endpoint.endswith(".do"))
            self.assertIsInstance(endpoint, str)

    @patch("requests.Session")
    def test_session_initialization(self, mock_session):
        """セッション初期化のテスト"""
        # Act
        fetcher = TokyoEpidemicSurveillanceFetcher()

        # Assert
        mock_session.assert_called_once()
        self.assertIsNotNone(fetcher.session)

    @patch("requests.Session.post")
    def test_large_data_handling(self, mock_post):
        """大きなデータの処理をテスト"""
        # Arrange
        # 100,000行のCSVデータを生成
        large_data = b"header1,header2\n"
        for i in range(100000):
            large_data += f"row{i},data{i}\n".encode()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = large_data
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

        # Assert
        self.assertEqual(len(result), len(large_data))
        self.assertTrue(result.startswith(b"header1"))

    @patch("requests.Session.post")
    def test_empty_response_handling(self, mock_post):
        """空のレスポンス処理をテスト"""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b""
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

        # Assert
        self.assertEqual(result, b"")

    @patch("requests.Session.post")
    def test_binary_safe_content(self, mock_post):
        """バイナリセーフなコンテンツ処理をテスト"""
        # Arrange
        # Shift-JISエンコーディングのテストデータ
        shift_jis_data = "日本語データ".encode("shift_jis")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = shift_jis_data
        mock_post.return_value = mock_response

        # Act
        result = self.fetcher._post_request("http://example.com", "1", "2024", "1", "2024", "52", "13", "00", "00", "0")

        # Assert
        self.assertEqual(result, shift_jis_data)
        # Shift-JISとしてデコード可能
        decoded = result.decode("shift_jis")
        self.assertEqual(decoded, "日本語データ")


if __name__ == "__main__":
    unittest.main()
