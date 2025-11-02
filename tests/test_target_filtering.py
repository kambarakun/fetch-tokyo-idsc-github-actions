"""
target-weeksとtarget-monthsパラメータのテスト
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# パスの設定
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_data import DataCollector
from src.fetchers.enhanced_fetcher import FetchParams


class TestTargetFiltering(unittest.TestCase):
    """ターゲット週・月フィルタリング機能のテスト"""

    def setUp(self):
        """テストの準備"""
        # モックの設定作成
        self.mock_config = MagicMock()

        # collectionサブ設定
        self.mock_config.collection = MagicMock()
        self.mock_config.collection.data_types_to_collect = ["sentinel_weekly_gender", "sentinel_monthly_age"]
        self.mock_config.collection.start_year = 2025
        self.mock_config.collection.end_year = 2025
        self.mock_config.collection.batch_size = 10
        self.mock_config.collection.incremental_mode = False
        self.mock_config.collection.max_execution_time_hours = 2

        # storageサブ設定
        self.mock_config.storage = MagicMock()
        self.mock_config.storage.base_directory = "data/raw"
        self.mock_config.storage.auto_commit = False
        self.mock_config.storage.commit_message_template = "test commit"
        self.mock_config.storage.keep_shift_jis = True

    @patch("scripts.fetch_data.datetime")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_target_weeks_filtering(self, mock_storage_class, mock_fetcher_class, mock_datetime):
        """対象週フィルタリングのテスト"""
        # 2025年の第52週を現在週として設定（確実に52週が存在する状態にする）
        mock_datetime.now.return_value.year = 2025
        mock_datetime.now.return_value.isocalendar.return_value = (2025, 52, 1)

        # 対象週を1週と52週のみに設定
        target_weeks = [1, 52]

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=False,
            force_update=False,
            target_weeks=target_weeks,
            target_months=None,
        )

        # _generate_all_paramsメソッドを直接テスト
        params = collector._generate_all_params("sentinel_weekly_gender", 2025, 2025, is_monthly=False)

        # 生成されたパラメータの週番号を確認
        generated_weeks = {int(p.start_sub_period) for p in params}

        # 1週と52週のみが含まれることを確認
        self.assertIn(1, generated_weeks)
        self.assertIn(52, generated_weeks)
        # 他の週（例：2週）が含まれないことを確認
        self.assertNotIn(2, generated_weeks)
        self.assertNotIn(10, generated_weeks)
        self.assertNotIn(30, generated_weeks)

    @patch("scripts.fetch_data.datetime")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_target_months_filtering(self, mock_storage_class, mock_fetcher_class, mock_datetime):
        """対象月フィルタリングのテスト"""
        # 2025年12月を現在月として設定（確実に12月まで存在する状態にする）
        mock_datetime.now.return_value.year = 2025
        mock_datetime.now.return_value.month = 12

        # 対象月を1月と12月のみに設定
        target_months = [1, 12]

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=False,
            force_update=False,
            target_weeks=None,
            target_months=target_months,
        )

        # _generate_all_paramsメソッドを直接テスト
        params = collector._generate_all_params("sentinel_monthly_age", 2025, 2025, is_monthly=True)

        # 生成されたパラメータの月番号を確認
        generated_months = {int(p.start_sub_period) for p in params}

        # 1月と12月が含まれることを確認
        self.assertIn(1, generated_months)
        self.assertIn(12, generated_months)
        # 他の月（例：2月、6月）が含まれないことを確認
        self.assertNotIn(2, generated_months)
        self.assertNotIn(6, generated_months)

    @patch("scripts.fetch_data.datetime")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_combined_filtering(self, mock_storage_class, mock_fetcher_class, mock_datetime):
        """週と月の両方のフィルタリングのテスト"""
        # 2025年の第52週12月を現在として設定
        mock_datetime.now.return_value.year = 2025
        mock_datetime.now.return_value.isocalendar.return_value = (2025, 52, 1)
        mock_datetime.now.return_value.month = 12

        # 対象週を10, 11週、対象月を3, 4月に設定
        target_weeks = [10, 11]
        target_months = [3, 4]

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=False,
            force_update=False,
            target_weeks=target_weeks,
            target_months=target_months,
        )

        # 週次データのパラメータ生成
        weekly_params = collector._generate_all_params("sentinel_weekly_gender", 2025, 2025, is_monthly=False)
        generated_weeks = {int(p.start_sub_period) for p in weekly_params}

        # 10週と11週のみが含まれることを確認
        self.assertEqual(len(generated_weeks), 2)
        self.assertIn(10, generated_weeks)
        self.assertIn(11, generated_weeks)
        self.assertNotIn(1, generated_weeks)
        self.assertNotIn(52, generated_weeks)

        # 月次データのパラメータ生成
        monthly_params = collector._generate_all_params("sentinel_monthly_age", 2025, 2025, is_monthly=True)
        generated_months = {int(p.start_sub_period) for p in monthly_params}

        # 3月と4月のみが含まれることを確認
        self.assertEqual(len(generated_months), 2)
        self.assertIn(3, generated_months)
        self.assertIn(4, generated_months)
        self.assertNotIn(1, generated_months)
        self.assertNotIn(12, generated_months)

    @patch("scripts.fetch_data.datetime")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_no_filtering(self, mock_storage_class, mock_fetcher_class, mock_datetime):
        """フィルタリングなしの場合のテスト"""
        # 2025年の第52週を現在週として設定（確実な状態）
        mock_datetime.now.return_value.year = 2025
        mock_datetime.now.return_value.isocalendar.return_value = (2025, 52, 1)
        mock_datetime.now.return_value.month = 12

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=False,
            force_update=False,
            target_weeks=None,
            target_months=None,
        )

        # 週次データのパラメータ生成
        weekly_params = collector._generate_all_params("sentinel_weekly_gender", 2025, 2025, is_monthly=False)
        generated_weeks = {int(p.start_sub_period) for p in weekly_params}

        # 52週すべてが生成されることを確認
        self.assertEqual(len(generated_weeks), 52)
        # 1週から52週まですべて含まれることを確認
        for week in range(1, 53):
            self.assertIn(week, generated_weeks)

        # 月次データのパラメータ生成
        monthly_params = collector._generate_all_params("sentinel_monthly_age", 2025, 2025, is_monthly=True)
        generated_months = {int(p.start_sub_period) for p in monthly_params}

        # 12ヶ月すべてが生成されることを確認
        self.assertEqual(len(generated_months), 12)
        # 1月から12月まですべて含まれることを確認
        for month in range(1, 13):
            self.assertIn(month, generated_months)

    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_filter_missing_params_with_targets(self, mock_storage_class, mock_fetcher_class):
        """ターゲット指定時の欠損パラメータフィルタリングのテスト"""
        target_weeks = [1, 2, 52]

        # モックフェッチャーの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher

        # get_missing_dataの戻り値を設定
        # 週1は既存なので、週2と52のみが返される
        expected_missing = [
            FetchParams(
                start_year="2025",
                start_sub_period="2",
                end_year="2025",
                end_sub_period="2",
                data_type="sentinel_weekly_gender",
                report_type="1",
            ),
            FetchParams(
                start_year="2025",
                start_sub_period="52",
                end_year="2025",
                end_sub_period="52",
                data_type="sentinel_weekly_gender",
                report_type="1",
            ),
        ]
        mock_fetcher.get_missing_data.return_value = expected_missing

        # 既存ファイルのモック（週1のファイルが存在）
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        existing_files = [Path("data/raw/sentinel_weekly_gender_2025_01.csv")]
        mock_storage.get_existing_files.return_value = existing_files

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=True,  # skip_existingをTrueにして、get_missing_dataが呼ばれるようにする
            force_update=False,
            target_weeks=target_weeks,
            target_months=None,
        )

        # _collect_data_typeを呼び出す
        collector._collect_data_type("sentinel_weekly_gender", 2025, 2025)

        # get_missing_dataが正しいパラメータで呼ばれたことを確認
        mock_fetcher.get_missing_data.assert_called_once_with(
            "sentinel_weekly_gender", existing_files, 2025, 2025, target_weeks, None
        )

    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_week_53_handling(self, mock_storage_class, mock_fetcher_class):
        """53週がある年の処理のテスト"""
        # 2020年は53週まである
        target_weeks = [52, 53]

        collector = DataCollector(
            self.mock_config,
            dry_run=True,
            skip_existing=False,
            force_update=False,
            target_weeks=target_weeks,
            target_months=None,
        )

        # 2020年のデータを生成（53週まであることを確認）
        params = collector._generate_all_params("sentinel_weekly_gender", 2020, 2020, is_monthly=False)

        # 生成されたパラメータの週番号を確認
        generated_weeks = {int(p.start_sub_period) for p in params}

        # 52週と53週が含まれることを確認
        self.assertIn(52, generated_weeks)
        self.assertIn(53, generated_weeks)
        # 54週以上は存在しないことを確認
        self.assertNotIn(54, generated_weeks)
        self.assertNotIn(55, generated_weeks)

        # target_weeksによるフィルタリングが正しく動作することを確認
        self.assertEqual(len(params), 2)  # 52週と53週のみ

    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_invalid_target_weeks(self, mock_storage_class, mock_fetcher_class):
        """無効な週番号のバリデーションテスト"""
        # モックフェッチャーの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage

        # 無効な週番号を含むtarget_weeks
        from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher

        fetcher = EnhancedEpidemicDataFetcher(Mock())

        # 無効な週番号（0と54）でValueErrorが発生することを確認
        with self.assertRaises(ValueError) as cm:
            fetcher.get_missing_data("sentinel_weekly_gender", [], 2025, 2025, [0, 54], None)

        self.assertIn("無効な週番号", str(cm.exception))
        self.assertIn("[0, 54]", str(cm.exception))

    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.StorageManager")
    def test_invalid_target_months(self, mock_storage_class, mock_fetcher_class):
        """無効な月番号のバリデーションテスト"""
        # モックフェッチャーの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage

        # 無効な月番号を含むtarget_months
        from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher

        fetcher = EnhancedEpidemicDataFetcher(Mock())

        # 無効な月番号（0と13）でValueErrorが発生することを確認
        with self.assertRaises(ValueError) as cm:
            fetcher.get_missing_data("sentinel_monthly_age", [], 2025, 2025, None, [0, 13])

        self.assertIn("無効な月番号", str(cm.exception))
        self.assertIn("[0, 13]", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
