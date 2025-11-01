"""
データ収集機能のテスト（skip_existing, force_update オプション）
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fetch_data import DataCollector
from src.fetchers.enhanced_fetcher import FetchParams, FetchResult, FileMetadata
from src.managers.config_manager import DataCollectionConfig
from src.managers.storage_manager import SaveResult


class TestDataCollectorOptions(unittest.TestCase):
    """DataCollectorのオプション機能テスト"""

    def setUp(self):
        """テストセットアップ"""
        # モック設定
        self.mock_config = Mock(spec=DataCollectionConfig)
        self.mock_config.collection = Mock()
        self.mock_config.collection.incremental_mode = False
        self.mock_config.collection.batch_size = 10
        self.mock_config.collection.data_types_to_collect = ["test_data"]
        self.mock_config.collection.max_execution_time_hours = 6
        self.mock_config.storage = Mock()
        self.mock_config.storage.auto_commit = False
        self.mock_config.storage.base_directory = "data/raw"
        self.mock_config.storage.commit_message_template = "Test commit"
        self.mock_config.storage.keep_shift_jis = True

    @patch("scripts.fetch_data.StorageManager")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    def test_skip_existing_option(self, mock_fetcher_class, mock_storage_class):
        """skip_existingオプションのテスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.get_missing_data.return_value = []  # 欠損データなし
        mock_fetcher.fetch_methods = {"test_data": Mock()}

        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_storage.get_existing_files.return_value = ["file1.csv", "file2.csv"]

        # skip_existing=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, skip_existing=True)

        # データ収集実行
        collector._collect_data_type("test_data", 2024, 2024)

        # get_existing_filesとget_missing_dataが呼ばれたことを確認
        mock_storage.get_existing_files.assert_called_once_with(data_type="test_data")
        mock_fetcher.get_missing_data.assert_called_once()

    @patch("scripts.fetch_data.StorageManager")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    def test_force_update_option(self, mock_fetcher_class, mock_storage_class):
        """force_updateオプションのテスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.fetch_methods = {"test_data": Mock()}

        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage

        # force_update=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, force_update=True)

        # _generate_all_paramsをモック
        with patch.object(collector, "_generate_all_params") as mock_generate:
            mock_generate.return_value = []  # パラメータなし
            collector._collect_data_type("test_data", 2024, 2024)

            # _generate_all_paramsが呼ばれたことを確認（全データ取得）
            mock_generate.assert_called_once_with("test_data", 2024, 2024, False)

        # get_existing_filesが呼ばれていないことを確認（スキップしない）
        mock_storage.get_existing_files.assert_not_called()

    @patch("scripts.fetch_data.StorageManager")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    def test_force_update_with_save(self, mock_fetcher_class, mock_storage_class):
        """force_updateオプションでの保存処理テスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher

        # フェッチメソッドのモック
        mock_fetch_method = Mock()
        mock_fetcher.fetch_methods = {"test_data": mock_fetch_method}

        # フェッチ結果のモック
        from datetime import datetime

        mock_result = FetchResult(
            success=True,
            data=b"test data",
            fetch_time=1.0,
            metadata=FileMetadata(
                filename="test.csv",
                data_type="test_data",
                date_range="2024-01",
                timestamp=datetime.now(),
                file_size=9,
                sha256_hash="test_hash",
                encoding="shift_jis",
            ),
        )
        mock_fetcher.fetch_with_retry.return_value = mock_result

        # ストレージのモック
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_save_result = SaveResult(success=True, is_duplicate=False)
        mock_storage.save_with_metadata.return_value = mock_save_result

        # force_update=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, force_update=True)

        # テスト用パラメータを生成
        test_params = FetchParams(
            start_year="2024",
            start_sub_period="1",
            end_year="2024",
            end_sub_period="1",
            data_type="test_data",
            report_type="1",
        )

        # _generate_all_paramsをモック
        with patch.object(collector, "_generate_all_params") as mock_generate:
            mock_generate.return_value = [test_params]

            # データ収集実行
            stats = collector.collect_data(data_types=["test_data"], start_year=2024, end_year=2024)

            # save_with_metadataがforce_overwrite=Trueで呼ばれたことを確認
            mock_storage.save_with_metadata.assert_called_once()
            call_args = mock_storage.save_with_metadata.call_args
            self.assertEqual(call_args[1]["force_overwrite"], True)

            # 統計情報の確認
            self.assertEqual(stats["successful"], 1)
            self.assertEqual(stats["failed"], 0)

    def test_conflicting_options(self):
        """skip_existingとforce_updateの同時指定エラーテスト"""
        # メインスクリプトでのオプション競合チェックをテスト
        import contextlib

        from scripts.fetch_data import main

        # コマンドライン引数をモック
        test_args = [
            "--skip-existing",
            "--force-update",
            "--start-year",
            "2024",
            "--end-year",
            "2024",
        ]

        with (
            patch("sys.argv", ["fetch_data.py"] + test_args),
            patch("sys.exit") as mock_exit,
            patch("scripts.fetch_data.setup_logging") as mock_logging,
        ):
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            # ConfigurationManagerをモック
            with patch("scripts.fetch_data.ConfigurationManager"), contextlib.suppress(SystemExit):
                main()

            # エラーログが出力されたことを確認
            mock_logger.error.assert_called_once_with("--skip-existing と --force-update は同時に指定できません")
            # sys.exit(1)が最初に呼ばれたことを確認
            first_call = mock_exit.call_args_list[0]
            self.assertEqual(first_call[0][0], 1)


class TestDataCollectorIntegration(unittest.TestCase):
    """DataCollectorの統合テスト"""

    @patch("scripts.fetch_data.StorageManager")
    @patch("scripts.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("scripts.fetch_data.ConfigurationManager")
    def test_normal_mode_without_options(self, mock_config_manager_class, mock_fetcher_class, mock_storage_class):
        """オプションなしの通常モード"""
        # 設定マネージャーのモック
        mock_config_manager = Mock()
        mock_config_manager_class.return_value = mock_config_manager
        mock_config = Mock(spec=DataCollectionConfig)
        mock_config.collection = Mock()
        mock_config.collection.incremental_mode = True
        mock_config.collection.batch_size = 10
        mock_config.collection.max_execution_time_hours = 6
        mock_config.storage = Mock()
        mock_config.storage.auto_commit = False
        mock_config.storage.base_directory = "data/raw"
        mock_config.storage.commit_message_template = "Test commit"
        mock_config.storage.keep_shift_jis = True
        mock_config_manager.load_config.return_value = mock_config

        # フェッチャーのモック
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.get_missing_data.return_value = []
        mock_fetcher.fetch_methods = {}

        # ストレージのモック
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_storage.get_existing_files.return_value = []

        # オプションなしでコレクター作成
        collector = DataCollector(mock_config, dry_run=False, skip_existing=False, force_update=False)

        # incremental_mode=Trueの場合の動作確認
        collector._collect_data_type("test_data", 2024, 2024)

        # incremental_modeでget_missing_dataが呼ばれることを確認
        mock_storage.get_existing_files.assert_called_once()
        mock_fetcher.get_missing_data.assert_called_once()


if __name__ == "__main__":
    unittest.main()
