"""
base_fetcherのユニットテスト
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.base_fetcher import TokyoEpidemicSurveillanceFetcher


class TestTokyoEpidemicSurveillanceFetcher(unittest.TestCase):
    """TokyoEpidemicSurveillanceFetcherのテスト"""

    def setUp(self):
        """テストのセットアップ"""
        self.fetcher = TokyoEpidemicSurveillanceFetcher()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_weekly_gender(self, mock_post):
        """定点監視 週報告分 男女別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"test,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_weekly_gender(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"test,data\n1,2")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_weekly_age(self, mock_post):
        """定点監視 週報告分 年齢階級別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"age,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_weekly_age(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"age,data\n1,2")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_weekly_health_center(self, mock_post):
        """定点監視 週報告分 保健所別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"health_center,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_weekly_health_center(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"health_center,data\n1,2")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_weekly_medical_district(self, mock_post):
        """定点監視 週報告分 医療圏別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"medical_district,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_weekly_medical_district(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"medical_district,data\n1,2")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_monthly_gender(self, mock_post):
        """定点監視 月報告分 男女別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"monthly,gender,data\n1,2,3"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_monthly_gender(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"monthly,gender,data\n1,2,3")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_monthly_age(self, mock_post):
        """定点監視 月報告分 年齢階級別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"monthly,age,data\n1,2,3"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_monthly_age(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"monthly,age,data\n1,2,3")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_monthly_health_center(self, mock_post):
        """定点監視 月報告分 保健所別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"monthly,health_center,data\n1,2,3"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_monthly_health_center(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"monthly,health_center,data\n1,2,3")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_monthly_medical_district(self, mock_post):
        """定点監視 月報告分 医療圏別集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"monthly,medical_district,data\n1,2,3"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_monthly_medical_district(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"monthly,medical_district,data\n1,2,3")
        mock_post.assert_called_once()

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_notifiable_weekly(self, mock_post):
        """全数把握監視 週報告分 届出患者数集計表CSVの取得テスト"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"notifiable,weekly,data\n1,2,3"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_notifiable_weekly(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証
        self.assertEqual(result, b"notifiable,weekly,data\n1,2,3")
        mock_post.assert_called_once()

    # ========== エラーハンドリングのテスト ==========

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_http_error_404(self, mock_post):
        """HTTP 404エラー時に適切にHTTPErrorが発生することを確認"""
        # モックレスポンス (404 Not Found)
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_post.return_value = mock_response

        # HTTPErrorが発生することを確認
        with self.assertRaises(requests.HTTPError):
            self.fetcher.fetch_csv_sentinel_weekly_gender(
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
            )

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_http_error_500(self, mock_post):
        """HTTP 500エラー時に適切にHTTPErrorが発生することを確認"""
        # モックレスポンス (500 Internal Server Error)
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Internal Server Error")
        mock_post.return_value = mock_response

        # HTTPErrorが発生することを確認
        with self.assertRaises(requests.HTTPError):
            self.fetcher.fetch_csv_sentinel_weekly_gender(
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
            )

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_timeout(self, mock_post):
        """タイムアウト時に適切にTimeoutが発生することを確認"""
        # Timeoutをシミュレート
        mock_post.side_effect = requests.Timeout("Connection timeout")

        # Timeoutが発生することを確認
        with self.assertRaises(requests.Timeout):
            self.fetcher.fetch_csv_sentinel_weekly_gender(
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
            )

    # ========== POSTパラメータの検証テスト ==========

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_fetch_csv_sentinel_weekly_gender_parameters(self, mock_post):
        """定点監視 週報告分 男女別集計表のPOSTパラメータ検証"""
        # モックレスポンス
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"test,data\n1,2"
        mock_post.return_value = mock_response

        # データ取得
        result = self.fetcher.fetch_csv_sentinel_weekly_gender(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

        # 検証: 結果
        self.assertEqual(result, b"test,data\n1,2")

        # 検証: POSTが1回呼ばれたか
        mock_post.assert_called_once()

        # 検証: 正しいURLとパラメータが使用されたか
        call_args = mock_post.call_args
        called_url = call_args[0][0]
        called_data = call_args[1]["data"]
        called_timeout = call_args[1]["timeout"]

        # URLの検証 (dlwgender.doを含むこと)
        self.assertIn("dlwgender.do", called_url)
        self.assertIn("epidinfo", called_url)

        # パラメータの検証
        expected_data = {
            "val(reportType)": "1",  # 男女別集計表
            "val(prefCode)": "13",  # 東京都
            "val(hcCode)": "00",  # 全て
            "val(epidCode)": "00",  # 全て
            "val(startYear)": "2025",
            "val(startSubPeriod)": "1",
            "val(endYear)": "2025",
            "val(endSubPeriod)": "1",
            "val(totalMode)": "0",
        }
        self.assertEqual(called_data, expected_data)
        self.assertEqual(called_timeout, 30)

    @patch("src.fetchers.base_fetcher.requests.Session.post")
    def test_custom_request_timeout_is_forwarded(self, mock_post):
        """カスタムtimeoutが共通POST経路へ渡ることを確認"""
        mock_response = Mock(status_code=200, content=b"test")
        mock_post.return_value = mock_response
        fetcher = TokyoEpidemicSurveillanceFetcher(timeout=(3.05, 27.0))

        fetcher.fetch_csv_sentinel_weekly_gender()

        self.assertEqual(mock_post.call_args.kwargs["timeout"], (3.05, 27.0))


if __name__ == "__main__":
    unittest.main()
