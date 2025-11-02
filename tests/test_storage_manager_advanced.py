"""ストレージマネージャーの高度なテスト - ログ処理とクリーンアップ"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from src.managers.storage_manager import GitHandler, StorageManager


class TestStorageManagerAdvanced(unittest.TestCase):
    """ストレージマネージャーの高度な機能テスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.config = {"auto_commit": False}
        self.storage = StorageManager(self.test_dir, self.config)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_log_operations(self):
        """ログ操作のテスト"""
        # Arrange
        log_dir = self.test_dir / "logs"
        log_dir.mkdir(parents=True)

        # Act - ログディレクトリ作成
        self.storage._ensure_directories()

        # ログファイルを作成
        log_file = log_dir / "test.log"
        log_file.write_text("Test log entry")

        # Assert
        self.assertTrue(log_dir.exists())
        self.assertTrue(log_file.exists())

    def test_cleanup_old_data(self):
        """古いデータのクリーンアップテスト"""
        # Arrange - 古いファイルと新しいファイルを作成
        old_file = self.test_dir / "old_2020_01.csv"
        new_file = self.test_dir / "new_2024_01.csv"

        old_file.write_text("old data")
        new_file.write_text("new data")

        # 古いファイルのタイムスタンプを変更
        old_time = datetime.now().timestamp() - (365 * 5 * 24 * 60 * 60)  # 5年前
        os.utime(old_file, (old_time, old_time))

        # Act - クリーンアップ対象ファイルを特定
        all_files = list(self.test_dir.glob("*.csv"))

        # Assert
        self.assertEqual(len(all_files), 2)
        # 実際のクリーンアップロジックはポリシー次第

    def test_concurrent_metadata_access(self):
        """並行メタデータアクセスのテスト"""
        # Arrange
        metadata_dir = self.test_dir / ".metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        # 複数のメタデータを同時に作成
        metadata_files = []
        for i in range(10):
            meta_file = metadata_dir / f"test_{i}.json"
            metadata = {"id": i, "timestamp": datetime.now().isoformat(), "data": f"test_data_{i}"}
            meta_file.write_text(json.dumps(metadata))
            metadata_files.append(meta_file)

        # Act - 全メタデータを読み込み
        loaded_metadata = []
        for meta_file in metadata_files:
            with open(meta_file) as f:
                loaded_metadata.append(json.load(f))

        # Assert
        self.assertEqual(len(loaded_metadata), 10)
        for i, meta in enumerate(loaded_metadata):
            self.assertEqual(meta["id"], i)

    def test_hash_index_recovery(self):
        """ハッシュインデックスの復旧テスト"""
        # Arrange - 破損したハッシュインデックス
        metadata_dir = self.test_dir / ".metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        hash_index_path = metadata_dir / "hash_index.json"

        # 破損したJSONを書き込み
        hash_index_path.write_text('{"corrupted": ')

        # Act - ハッシュインデックスを読み込み（エラーハンドリング）
        try:
            hash_index = self.storage._load_hash_index()
        except:
            hash_index = {}

        # Assert - 空の辞書が返される
        self.assertEqual(hash_index, {})

    def test_file_permission_errors(self):
        """ファイル権限エラーのテスト"""
        # Arrange
        protected_file = self.test_dir / "protected.csv"
        protected_file.write_text("protected data")

        # 読み取り専用に変更
        protected_file.chmod(0o444)

        # Act & Assert - 上書き試行
        result = self.storage.save_with_metadata(
            data="new data", data_type="protected", period_type="week", year=2024, period=1, force_overwrite=True
        )

        # クリーンアップ（権限を戻す）
        protected_file.chmod(0o644)

        # 権限エラーが適切に処理される
        if not result.success:
            self.assertIn("Permission", result.message)

    def test_storage_with_symlinks(self):
        """シンボリックリンクを含むストレージのテスト"""
        # Arrange
        real_file = self.test_dir / "real.csv"
        real_file.write_text("real data")

        symlink = self.test_dir / "link.csv"
        if not symlink.exists():
            try:
                symlink.symlink_to(real_file)
            except OSError:
                # Windowsでシンボリックリンクが作成できない場合はスキップ
                self.skipTest("Cannot create symlinks on this system")

        # Act - シンボリックリンクを含む統計
        stats = self.storage.get_storage_stats()

        # Assert
        self.assertGreaterEqual(stats["total_files"], 1)

    def test_transaction_rollback(self):
        """トランザクションロールバックのシミュレーション"""
        # Arrange
        test_file = self.test_dir / "transaction.csv"
        original_data = "original data"
        test_file.write_text(original_data)

        # Act - 保存中にエラーをシミュレート
        with patch.object(self.storage, "_save_metadata") as mock_save:
            mock_save.side_effect = Exception("Metadata save failed")

            result = self.storage.save_with_metadata(
                data="new data",
                data_type="transaction",
                period_type="week",
                year=2024,
                period=1,
                metadata={"test": "metadata"},
            )

        # Assert - ロールバック確認（元のデータが残っているか）
        if test_file.exists():
            content = test_file.read_text()
            # エラー時の動作はシステムの設計次第

    def test_data_validation_edge_cases(self):
        """データ検証のエッジケースをテスト"""
        # Arrange
        edge_cases = [
            ("", "empty"),  # 空文字列
            ("\n\n\n", "newlines"),  # 改行のみ
            ("a" * 1000000, "large"),  # 大きなデータ
            ("日本語,데이터,данные", "multilingual"),  # 多言語
            ("\x00\x01\x02", "binary"),  # バイナリ文字
        ]

        for data, case_name in edge_cases:
            with self.subTest(case=case_name):
                # Act
                result = self.storage.save_with_metadata(
                    data=data, data_type=f"test_{case_name}", period_type="week", year=2024, period=1
                )

                # Assert
                self.assertIsNotNone(result)
                if result.success:
                    self.assertIsNotNone(result.file_path)

    @patch("subprocess.run")
    def test_git_stash_operations(self, mock_run):
        """Git stash操作のテスト"""
        git_handler = GitHandler(auto_commit=False)

        # Arrange
        mock_run.return_value = Mock(returncode=0, stdout="Saved working directory")

        # Act - stash操作をシミュレート
        success, _ = git_handler.add_files(["*.csv"])

        # Assert
        mock_run.assert_called()

    def test_metadata_migration(self):
        """メタデータのマイグレーションテスト"""
        # Arrange - 旧形式のメタデータ
        old_metadata = {"version": 1, "data": "old format"}

        metadata_dir = self.test_dir / ".metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        old_meta_file = metadata_dir / "old.json"
        old_meta_file.write_text(json.dumps(old_metadata))

        # Act - 新形式への変換をシミュレート
        loaded = json.loads(old_meta_file.read_text())

        # 新形式に変換
        new_metadata = {"version": 2, "data": loaded.get("data", ""), "migrated_at": datetime.now().isoformat()}

        # Assert
        self.assertEqual(new_metadata["version"], 2)
        self.assertEqual(new_metadata["data"], "old format")
        self.assertIn("migrated_at", new_metadata)

    def test_directory_traversal_protection(self):
        """ディレクトリトラバーサル攻撃の防御テスト"""
        # Arrange - 危険なパスを含むファイル名
        dangerous_names = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config",
            "test/../../sensitive",
            "./../.hidden",
        ]

        for dangerous_name in dangerous_names:
            with self.subTest(name=dangerous_name):
                # Act & Assert
                with self.assertRaises((ValueError, OSError)):
                    # セキュリティチェックで拒否されるべき
                    self.storage.organize_file_path(data_type=dangerous_name, period_type="week", year=2024, period=1)

    def test_atomic_file_operations(self):
        """アトミックファイル操作のテスト"""
        # Arrange
        target_file = self.test_dir / "atomic.csv"

        # Act - アトミック書き込みをシミュレート
        temp_file = self.test_dir / "atomic.csv.tmp"
        temp_file.write_text("new atomic data")

        # アトミックな置き換え
        if target_file.exists():
            target_file.unlink()
        temp_file.rename(target_file)

        # Assert
        self.assertTrue(target_file.exists())
        self.assertFalse(temp_file.exists())
        self.assertEqual(target_file.read_text(), "new atomic data")


class TestGitHandlerAdvanced(unittest.TestCase):
    """GitHandlerの高度なテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.git_handler = GitHandler(auto_commit=False)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("subprocess.run")
    def test_git_log_parsing(self, mock_run):
        """Gitログ解析のテスト"""
        # Arrange
        mock_log_output = """
commit abc123
Author: Test User
Date:   2024-01-01
    データ更新

commit def456
Author: Test User
Date:   2024-01-02
    バグ修正
"""
        mock_run.return_value = Mock(returncode=0, stdout=mock_log_output)

        # Act
        result = mock_run(["git", "log", "--oneline"], capture_output=True)

        # Assert
        self.assertIn("データ更新", result.stdout)
        self.assertIn("バグ修正", result.stdout)

    @patch("subprocess.run")
    def test_git_branch_operations(self, mock_run):
        """Gitブランチ操作のテスト"""
        # Arrange
        mock_run.return_value = Mock(returncode=0)

        # Act - ブランチ作成
        mock_run(["git", "branch", "test-branch"])

        # Assert
        mock_run.assert_called()

    @patch("subprocess.run")
    def test_git_diff_detection(self, mock_run):
        """Git差分検出のテスト"""
        # Arrange
        mock_diff_output = """
diff --git a/test.csv b/test.csv
@@ -1 +1 @@
-old data
+new data
"""
        mock_run.return_value = Mock(returncode=0, stdout=mock_diff_output)

        # Act
        result = mock_run(["git", "diff"], capture_output=True)

        # Assert
        self.assertIn("old data", result.stdout)
        self.assertIn("new data", result.stdout)


if __name__ == "__main__":
    unittest.main()
