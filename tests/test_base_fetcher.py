"""
base_fetcherのユニットテスト
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
