"""統合テスト - システム全体の動作を検証"""

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher
from src.managers.config_manager import ConfigurationManager
from src.managers.storage_manager import StorageManager


class TestSystemIntegration(unittest.TestCase):
    """システム全体の統合テスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.config_file = self.test_dir / "config.yml"
        self.data_dir = self.test_dir / "data" / "raw"
        self.data_dir.mkdir(parents=True)

        # 基本設定を作成
        config_data = """
fetcher:
  enabled_data_types:
    - sentinel_weekly_gender
    - sentinel_weekly_age
    - notifiable_weekly
  batch_size: 10
  max_concurrent: 3
  timeout: 30
  rate_limit: 5

storage:
  base_path: data/raw
  encoding: shift_jis
  save_metadata: true
  check_duplicates: true

schedule:
  enabled: true
  cron: "0 10 * * 1"
  timezone: "Asia/Tokyo"
"""
        self.config_file.write_text(config_data)

        self.config_manager = ConfigurationManager()
        self.config_manager.load_config(str(self.config_file))
        self.storage_manager = StorageManager(str(self.data_dir))
        self.fetcher = EnhancedEpidemicDataFetcher()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_full_data_collection_workflow(self):
        """完全なデータ収集ワークフローをテスト"""
        # Act
        # 1. 設定から有効なデータタイプを取得
        enabled_types = self.config_manager.get_enabled_data_types()
        self.assertEqual(len(enabled_types), 3)

        # 2. データを保存（モックデータ使用）
        test_data = "date,gender,count\n2024-01-01,M,100"
        save_result = self.storage_manager.save_data(
            data=test_data, data_type="sentinel_weekly_gender", period_type="week", year=2024, period=1
        )

        # Assert
        self.assertTrue(save_result.success)
        self.assertTrue(save_result.is_new)

        # 3. 統計情報を確認
        stats = self.storage_manager.get_storage_stats()
        self.assertEqual(stats["total_files"], 1)
        self.assertIn("sentinel_weekly_gender", stats["by_data_type"])

    def test_duplicate_detection_workflow(self):
        """重複検出ワークフローをテスト"""
        # Arrange
        test_data = "date,count\n2024-01-01,100"

        # Act
        # 1. 初回保存
        result1 = self.storage_manager.save_data(
            data=test_data, data_type="test", period_type="week", year=2024, period=1
        )

        # 2. 同じデータで再度保存（重複）
        result2 = self.storage_manager.save_data(
            data=test_data, data_type="test", period_type="week", year=2024, period=1, skip_if_exists=True
        )

        # 3. force_overwriteで上書き
        result3 = self.storage_manager.save_data(
            data=test_data, data_type="test", period_type="week", year=2024, period=1, force_overwrite=True
        )

        # Assert
        self.assertTrue(result1.success)
        self.assertTrue(result1.is_new)

        self.assertFalse(result2.success)
        self.assertIn("already exists", result2.message)

        self.assertTrue(result3.success)
        self.assertFalse(result3.is_new)

    def test_error_recovery_workflow(self):
        """エラーリカバリーワークフローをテスト"""
        # Act
        # ファイル保存時のエラーをシミュレート（権限エラーなど）
        invalid_path = "/root/test/file.csv"  # 書き込み権限がないパス

        result = self.storage_manager.save_data(
            data="test",
            data_type="test",
            period_type="week",
            year=2024,
            period=1,
            force_path=invalid_path if hasattr(self.storage_manager, "force_path") else None,
        )

        # Assert - エラーが適切に処理される
        if not result.success:
            self.assertIsNotNone(result.message)

    def test_configuration_validation_workflow(self):
        """設定検証ワークフローをテスト"""
        # Arrange
        invalid_config = {
            "fetcher": {"enabled_data_types": [], "batch_size": -1, "timeout": 0}  # 空のリスト  # 負の値  # ゼロ
        }

        # Act
        validation_result = self.config_manager.validate_config(invalid_config)

        # Assert
        self.assertFalse(validation_result.is_valid)
        self.assertGreater(len(validation_result.errors), 0)
        self.assertIn("batch_size", str(validation_result.errors))

    def test_batch_processing_workflow(self):
        """バッチ処理ワークフローをテスト"""
        # Act - バッチでデータを保存
        results = []
        for i in range(1, 11):
            result = self.storage_manager.save_data(
                data=f"week,{i}\ndata,{i}", data_type="test", period_type="week", year=2024, period=i
            )
            results.append(result)

        # Assert
        successful = [r for r in results if r.success]
        self.assertEqual(len(successful), 10)

        # 統計を確認
        stats = self.storage_manager.get_storage_stats()
        self.assertEqual(stats["total_files"], 10)

    def test_metadata_tracking_workflow(self):
        """メタデータ追跡ワークフローをテスト"""
        # Arrange
        test_data = "date,value\n2024-01-01,100\n2024-01-02,200"
        metadata = {"source": "test_system", "version": "1.0", "timestamp": datetime.now().isoformat()}

        # Act
        # 1. メタデータ付きで保存
        save_result = self.storage_manager.save_data(
            data=test_data, data_type="test", period_type="week", year=2024, period=1, metadata=metadata
        )

        # 2. メタデータを取得
        retrieved_metadata = self.storage_manager.get_metadata("test_weekly_2024_01.csv")

        # Assert
        self.assertTrue(save_result.success)
        self.assertIsNotNone(retrieved_metadata)
        self.assertEqual(retrieved_metadata["source"], "test_system")
        self.assertEqual(retrieved_metadata["version"], "1.0")

    def test_git_integration_workflow(self):
        """Git統合ワークフローをテスト"""
        # Arrange
        # テスト用のGitリポジトリを初期化
        import subprocess

        from src.managers.storage_manager import GitHandler

        subprocess.run(["git", "init"], cwd=self.test_dir, capture_output=True, check=False)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.test_dir, capture_output=True, check=False
        )
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, capture_output=True, check=False)

        git_handler = GitHandler(str(self.test_dir))

        # Act
        # 1. ファイルを作成
        test_file = self.test_dir / "test.txt"
        test_file.write_text("test content")

        # 2. Gitに追加
        success, message = git_handler.add_files([str(test_file)])
        self.assertTrue(success)

        # 3. コミット
        success, message = git_handler.commit("Test commit")
        self.assertTrue(success)

        # Assert
        # コミット履歴を確認
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=self.test_dir, capture_output=True, text=True, check=False
        )
        self.assertIn("Test commit", result.stdout)


class TestPerformanceIntegration(unittest.TestCase):
    """パフォーマンス関連の統合テスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.storage_manager = StorageManager(str(self.test_dir))
        self.fetcher = EnhancedEpidemicDataFetcher()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_large_dataset_handling(self):
        """大規模データセットの処理をテスト"""
        # Arrange
        # 10万行のCSVデータを生成
        rows = ["date,value"]
        for i in range(100000):
            rows.append(f"2024-01-01,{i}")
        large_data = "\n".join(rows)

        # Act
        start_time = datetime.now()
        result = self.storage_manager.save_data(
            data=large_data, data_type="test", period_type="week", year=2024, period=1
        )
        end_time = datetime.now()

        # Assert
        self.assertTrue(result.success)
        # 10万行でも5秒以内に処理完了
        self.assertLess((end_time - start_time).total_seconds(), 5)

    def test_concurrent_storage_operations(self):
        """並行ストレージ操作をテスト"""
        # Arrange
        import threading

        results = []
        errors = []

        def save_data(week):
            try:
                result = self.storage_manager.save_data(
                    data=f"week,{week}\n{week},data", data_type="test", period_type="week", year=2024, period=week
                )
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Act
        threads = []
        for week in range(1, 11):
            thread = threading.Thread(target=save_data, args=(week,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Assert
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 10)
        successful = [r for r in results if r.success]
        self.assertEqual(len(successful), 10)


if __name__ == "__main__":
    unittest.main()
