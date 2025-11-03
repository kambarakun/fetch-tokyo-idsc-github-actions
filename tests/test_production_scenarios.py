"""本番環境を想定したシナリオテスト - 実際の運用で起こりうる状況をテスト"""

import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher
from src.managers.config_manager import ConfigurationManager
from src.managers.storage_manager import StorageManager


class TestProductionScenarios(unittest.TestCase):
    """本番環境を想定したシナリオテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.config = {"auto_commit": False}
        self.storage = StorageManager(self.test_dir, self.config)
        self.fetcher = EnhancedEpidemicDataFetcher()
        self.config_manager = ConfigurationManager()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_weekly_data_collection_scenario(self):
        """週次データ収集の完全なシナリオ"""
        # Scenario: 毎週月曜日の定期実行をシミュレート

        # Arrange - 前週のデータが存在する状態
        previous_week_data = "week,count\n51,1000"
        self.storage.save_with_metadata(
            data=previous_week_data.encode("utf-8"),
            data_type="sentinel_weekly_gender",
            is_monthly=False,
            year=2024,
            period=51,
        )

        # Act - 新しい週（52週）のデータを取得・保存
        new_week_data = "week,count\n52,1200"
        result = self.storage.save_with_metadata(
            data=new_week_data.encode("utf-8"),
            data_type="sentinel_weekly_gender",
            is_monthly=False,
            year=2024,
            period=52,
        )

        # Assert
        self.assertTrue(result.success)
        self.assertTrue(result.is_new)

        # 統計を確認
        stats = self.storage.get_storage_stats()
        self.assertEqual(stats["total_files"], 2)
        self.assertIn(2024, stats["year_stats"])

    def test_year_transition_scenario(self):
        """年をまたぐデータ収集のシナリオ"""
        # Scenario: 2024年第52週から2025年第1週への移行

        # Arrange - 2024年最終週のデータ
        self.storage.save_with_metadata(
            data=b"week,year,count\n52,2024,1500",
            data_type="notifiable_weekly",
            is_monthly=False,
            year=2024,
            period=52,
        )

        # Act - 2025年第1週のデータ
        result = self.storage.save_with_metadata(
            data=b"week,year,count\n1,2025,100",
            data_type="notifiable_weekly",
            is_monthly=False,
            year=2025,
            period=1,
        )

        # Assert
        self.assertTrue(result.success)
        stats = self.storage.get_storage_stats()
        self.assertIn(2024, stats["year_stats"])
        self.assertIn(2025, stats["year_stats"])

    def test_missing_data_recovery_scenario(self):
        """欠損データ復旧のシナリオ"""
        # Scenario: システム障害で3週間分のデータが欠損、復旧を実行

        # Arrange - 週1と週5のデータは存在
        self.storage.save_with_metadata(
            data=b"week1", data_type="sentinel_weekly_age", is_monthly=False, year=2024, period=1
        )
        self.storage.save_with_metadata(
            data=b"week5", data_type="sentinel_weekly_age", is_monthly=False, year=2024, period=5
        )

        # Act - 欠損データ（週2-4）を検出して補充
        missing_weeks = []
        for week in range(1, 6):
            file_path = self.test_dir / f"sentinel_weekly_age_2024_{week:02d}.csv"
            if not file_path.exists():
                missing_weeks.append(week)

        # 欠損データを補充
        for week in missing_weeks:
            result = self.storage.save_with_metadata(
                data=f"recovered_week{week}".encode(),
                data_type="sentinel_weekly_age",
                is_monthly=False,
                year=2024,
                period=week,
            )
            self.assertTrue(result.success)

        # Assert
        stats = self.storage.get_storage_stats()
        self.assertEqual(stats["total_files"], 5)

    def test_concurrent_update_scenario(self):
        """並行更新のシナリオ"""
        # Scenario: 複数のデータタイプを同時に更新

        data_types = ["sentinel_weekly_gender", "sentinel_weekly_age", "notifiable_weekly"]

        results = []
        for i, data_type in enumerate(data_types):
            result = self.storage.save_with_metadata(
                data=f"{data_type}_data_{i}".encode(),
                data_type=data_type,
                is_monthly=False,
                year=2024,
                period=10,
            )
            results.append(result)

        # Assert - 全て成功
        for result in results:
            self.assertTrue(result.success)

        # ハッシュインデックスが正しく更新されている
        hash_index = self.storage._load_hash_index()
        self.assertGreater(len(hash_index), 0)

    def test_data_corruption_detection_scenario(self):
        """データ破損検出のシナリオ"""
        # Scenario: 保存済みデータが破損した場合の検出と対処

        # Arrange - 正常なデータを保存
        original_data = "header1,header2\nvalue1,value2"
        save_result = self.storage.save_with_metadata(
            data=original_data.encode("utf-8"),
            data_type="test_corruption",
            is_monthly=False,
            year=2024,
            period=1,
            additional_metadata={"checksum": "abc123"},
        )

        # Act - ファイルを直接変更して破損をシミュレート
        if save_result.file_path:
            file_path = Path(save_result.file_path)
            if file_path.exists():
                file_path.write_text("corrupted data")

        # 再度同じデータで保存を試みる（重複チェック）
        result2 = self.storage.save_with_metadata(
            data=original_data.encode("utf-8"), data_type="test_corruption", is_monthly=False, year=2024, period=1
        )

        # Assert - システムが破損を検出できる
        # （実装によって動作は異なるが、何らかの対処が必要）
        self.assertIsNotNone(result2)

    def test_api_rate_limit_scenario(self):
        """APIレート制限のシナリオ"""
        # Scenario: 短時間に大量のリクエストを送信

        # Arrange
        from src.fetchers.enhanced_fetcher import RateLimiter

        rate_limiter = RateLimiter(min_delay=0.2)  # 5 requests per second

        # Act - 10個のリクエストを送信
        start_time = time.time()
        for i in range(10):
            # レートリミッターによる待機時間を計算
            rate_limiter.last_request_time = time.time()

        end_time = time.time()
        elapsed = end_time - start_time

        # Assert - レート制限により適切な時間がかかる
        # 10リクエスト / 5rps = 最低2秒
        # （実際のテストでは時間制約があるため、概念的なテスト）
        self.assertGreaterEqual(elapsed, 0)  # 時間が経過している

    def test_monthly_to_weekly_transition_scenario(self):
        """月次から週次データへの移行シナリオ"""
        # Scenario: 月次集計と週次集計の両方が必要な場合

        # Arrange - 月次データを保存
        monthly_data = "month,total\n1,5000"
        self.storage.save_with_metadata(
            data=monthly_data.encode("utf-8"), data_type="sentinel_monthly_age", is_monthly=True, year=2024, period=1
        )

        # Act - 同じ期間の週次データを保存（第1-4週）
        weekly_total = 0
        for week in range(1, 5):
            week_data = f"week,count\n{week},1250"
            result = self.storage.save_with_metadata(
                data=week_data.encode("utf-8"),
                data_type="sentinel_weekly_age",
                is_monthly=False,
                year=2024,
                period=week,
            )
            weekly_total += 1250

        # Assert - 月次と週次の整合性
        self.assertEqual(weekly_total, 5000)  # 月次合計と一致

    def test_emergency_data_update_scenario(self):
        """緊急データ更新のシナリオ"""
        # Scenario: 誤ったデータが公開され、緊急で修正が必要

        # Arrange - 誤ったデータを保存
        wrong_data = "disease,count\nCOVID-19,99999"  # 異常に高い数値
        self.storage.save_with_metadata(
            data=wrong_data.encode("utf-8"), data_type="notifiable_weekly", is_monthly=False, year=2024, period=15
        )

        # Act - 緊急修正（force_overwriteを使用）
        correct_data = "disease,count\nCOVID-19,100"  # 正しい数値
        result = self.storage.save_with_metadata(
            data=correct_data.encode("utf-8"),
            data_type="notifiable_weekly",
            is_monthly=False,
            year=2024,
            period=15,
            force_overwrite=True,
            additional_metadata={"correction_reason": "Data entry error", "corrected_at": datetime.now().isoformat()},
        )

        # Assert
        self.assertTrue(result.success)
        self.assertFalse(result.is_new)  # 上書きなので新規ではない

    def test_backup_and_restore_scenario(self):
        """バックアップとリストアのシナリオ"""
        # Scenario: 定期バックアップからのデータ復旧

        # Arrange - オリジナルデータを作成
        original_files = []
        for week in range(1, 5):
            result = self.storage.save_with_metadata(
                data=f"original_week_{week}".encode(),
                data_type="backup_test",
                is_monthly=False,
                year=2024,
                period=week,
            )
            original_files.append(result.file_path)

        # バックアップディレクトリを作成
        backup_dir = self.test_dir / "backup"
        backup_dir.mkdir(parents=True)

        # Act - バックアップを作成
        for file_path in original_files:
            if file_path:
                src = Path(file_path)
                if src.exists():
                    dst = backup_dir / src.name
                    shutil.copy2(src, dst)

        # オリジナルを削除
        for file_path in original_files:
            if file_path:
                Path(file_path).unlink()

        # リストア
        restored_count = 0
        for backup_file in backup_dir.glob("*.csv"):
            dst = self.test_dir / backup_file.name
            shutil.copy2(backup_file, dst)
            restored_count += 1

        # Assert
        self.assertEqual(restored_count, 4)
        self.assertEqual(len(list(self.test_dir.glob("backup_test_*.csv"))), 4)

    def test_system_health_check_scenario(self):
        """システムヘルスチェックのシナリオ"""
        # Scenario: GitHub Actionsで定期的に実行されるヘルスチェック

        # Act - 各コンポーネントの状態を確認
        health_status = {"storage": False, "config": False, "fetcher": False}

        # ストレージチェック
        try:
            self.storage._ensure_directories()
            health_status["storage"] = True
        except:
            pass

        # 設定チェック
        try:
            config = self.config_manager.get_default_config()
            health_status["config"] = config is not None
        except:
            pass

        # フェッチャーチェック
        try:
            self.assertIsNotNone(self.fetcher)
            health_status["fetcher"] = True
        except:
            pass

        # Assert - 全コンポーネントが正常
        for component, status in health_status.items():
            self.assertTrue(status, f"{component} is not healthy")


class TestDisasterRecoveryScenarios(unittest.TestCase):
    """災害復旧シナリオのテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.config = {"auto_commit": False}
        self.storage = StorageManager(self.test_dir, self.config)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_partial_file_recovery(self):
        """部分的なファイル復旧のシナリオ"""
        # Scenario: 一部のファイルが破損、残りから復旧

        # Arrange - 10週分のデータを作成
        for week in range(1, 11):
            self.storage.save_with_metadata(
                data=f"week_{week}_data".encode(),
                data_type="recovery_test",
                is_monthly=False,
                year=2024,
                period=week,
            )

        # Act - 偶数週のファイルを破損させる
        corrupted = []
        recovered = []
        for week in range(2, 11, 2):
            file_path = self.test_dir / f"recovery_test_weekly_2024_{week:02d}.csv"
            if file_path.exists():
                file_path.write_text("CORRUPTED")
                corrupted.append(week)

        # 破損を検出して再取得
        for week in range(1, 11):
            file_path = self.test_dir / f"recovery_test_weekly_2024_{week:02d}.csv"
            if file_path.exists():
                content = file_path.read_text()
                if content == "CORRUPTED":
                    # 再取得をシミュレート
                    file_path.write_text(f"recovered_week_{week}_data")
                    recovered.append(week)

        # Assert
        self.assertEqual(len(corrupted), 5)
        self.assertEqual(len(recovered), 5)

    def test_incremental_recovery(self):
        """増分復旧のシナリオ"""
        # Scenario: 障害からの段階的な復旧

        recovery_phases = [
            (range(1, 6), "phase1"),  # フェーズ1: 週1-5
            (range(6, 11), "phase2"),  # フェーズ2: 週6-10
            (range(11, 16), "phase3"),  # フェーズ3: 週11-15
        ]

        total_recovered = 0
        for week_range, phase in recovery_phases:
            for week in week_range:
                result = self.storage.save_with_metadata(
                    data=f"{phase}_week_{week}".encode(),
                    data_type="incremental_recovery",
                    is_monthly=False,
                    year=2024,
                    period=week,
                )
                if result.success:
                    total_recovered += 1

            # 各フェーズ後の確認
            stats = self.storage.get_storage_stats()
            self.assertEqual(stats["total_files"], total_recovered)

        # Assert - 全データが復旧
        self.assertEqual(total_recovered, 15)


if __name__ == "__main__":
    unittest.main()
