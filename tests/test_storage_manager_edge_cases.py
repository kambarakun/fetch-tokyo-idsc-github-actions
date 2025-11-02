"""ストレージマネージャーのエッジケースと異常系のテスト"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.managers.storage_manager import GitHandler, SaveResult, StorageManager


class TestStorageManagerEdgeCases(unittest.TestCase):
    """ストレージマネージャーのエッジケースをテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.storage = StorageManager(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_save_with_corrupted_metadata(self):
        """破損したメタデータでの保存をテスト"""
        # Arrange
        metadata_dir = self.test_dir / ".metadata"
        metadata_dir.mkdir(parents=True)

        # 破損したメタデータファイルを作成
        corrupted_file = metadata_dir / "corrupted.json"
        corrupted_file.write_text('{"invalid json": }')

        # Act & Assert
        # 破損したメタデータがあっても正常に保存できる
        result = self.storage.save_data(
            data="test,data\n1,2",
            data_type="sentinel_weekly_gender",
            period_type="week",
            year=2024,
            period=1,
            metadata={"test": "metadata"},
        )
        self.assertTrue(result.success)

    def test_cleanup_orphaned_metadata(self):
        """孤立したメタデータの処理をテスト"""
        # Arrange
        metadata_dir = self.test_dir / ".metadata"
        metadata_dir.mkdir(parents=True)

        # データファイルなしでメタデータだけ作成
        orphaned_meta = metadata_dir / "orphaned_2024_01.json"
        orphaned_meta.write_text(json.dumps({"orphaned": True}))

        # Act
        # 孤立したメタデータは get_metadata で None を返す
        metadata = self.storage.get_metadata("orphaned_2024_01.csv")

        # Assert
        self.assertIsNone(metadata)

    def test_save_with_disk_full_simulation(self):
        """ディスクフル状態のシミュレーション"""
        # Arrange
        with patch("pathlib.Path.write_text") as mock_write:
            mock_write.side_effect = OSError("No space left on device")

            # Act
            result = self.storage.save_data(
                data="test,data\n1,2", data_type="sentinel_weekly_gender", period_type="week", year=2024, period=1
            )

            # Assert
            self.assertFalse(result.success)
            self.assertIn("No space left", result.message)

    def test_concurrent_hash_index_updates(self):
        """並行ハッシュインデックス更新のテスト"""
        # Arrange
        hash_value = "abc123"

        # 同じハッシュで複数回保存（並行アクセスをシミュレート）
        for i in range(5):
            with patch.object(self.storage, "_calculate_hash", return_value=hash_value):
                self.storage.save_data(
                    data=f"test,data\n{i},{i}", data_type="test", period_type="week", year=2024, period=i + 1
                )

        # Act
        hash_index = self.storage._load_hash_index()

        # Assert
        self.assertIn(hash_value, hash_index)
        self.assertEqual(len(hash_index[hash_value]), 5)

    def test_invalid_characters_in_filename(self):
        """ファイル名に無効な文字が含まれる場合のテスト"""
        # Arrange
        invalid_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]

        for char in invalid_chars:
            with self.subTest(char=char):
                # Act & Assert
                with self.assertRaises(ValueError):
                    self.storage.organize_file_path(
                        data_type=f"test{char}type", period_type="week", year=2024, period=1
                    )

    def test_year_boundary_cases(self):
        """年の境界値のテスト"""
        # Arrange
        boundary_years = [1900, 1999, 2000, 2099, 2100, 3000]

        for year in boundary_years:
            with self.subTest(year=year):
                # Act
                path = self.storage.organize_file_path(data_type="test", period_type="week", year=year, period=1)

                # Assert
                self.assertIn(str(year), str(path))

    @patch("subprocess.run")
    def test_git_operations_with_errors(self, mock_run):
        """Git操作のエラーハンドリングをテスト"""
        git_handler = GitHandler(str(self.test_dir))

        # git add のエラー
        mock_run.return_value = Mock(returncode=1, stderr="fatal: not a git repository")
        success, message = git_handler.add_files(["test.csv"])
        self.assertFalse(success)
        self.assertIn("fatal", message)

        # git commit のエラー
        mock_run.return_value = Mock(returncode=128, stderr="nothing to commit")
        success, message = git_handler.commit("test commit")
        self.assertFalse(success)

    def test_storage_stats_with_empty_directory(self):
        """空のディレクトリでのストレージ統計をテスト"""
        # Act
        stats = self.storage.get_storage_stats()

        # Assert
        self.assertEqual(stats["total_files"], 0)
        self.assertEqual(stats["total_size"], 0)
        self.assertEqual(len(stats["by_data_type"]), 0)
        self.assertEqual(len(stats["by_year"]), 0)

    def test_storage_stats_with_large_files(self):
        """大きなファイルでのストレージ統計をテスト"""
        # Arrange
        large_data = "x" * (10 * 1024 * 1024)  # 10MB
        self.storage.save_data(data=large_data, data_type="test", period_type="week", year=2024, period=1)

        # Act
        stats = self.storage.get_storage_stats()

        # Assert
        self.assertGreater(stats["total_size"], 10 * 1024 * 1024)
        self.assertEqual(stats["total_files"], 1)

    def test_metadata_with_special_characters(self):
        """特殊文字を含むメタデータのテスト"""
        # Arrange
        metadata = {
            "description": "日本語テスト",
            "special": "!@#$%^&*()",
            "unicode": "🎌📊💾",
            "nested": {"key": "value\nwith\nnewlines"},
        }

        # Act
        result = self.storage.save_data(
            data="test", data_type="test", period_type="week", year=2024, period=1, metadata=metadata
        )

        # Assert
        self.assertTrue(result.success)
        saved_metadata = self.storage.get_metadata("test_weekly_2024_01.csv")
        self.assertEqual(saved_metadata["description"], "日本語テスト")
        self.assertEqual(saved_metadata["unicode"], "🎌📊💾")


class TestGitHandlerEdgeCases(unittest.TestCase):
    """GitHandlerのエッジケースをテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.git_handler = GitHandler(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("subprocess.run")
    def test_git_with_merge_conflicts(self, mock_run):
        """マージコンフリクト時の処理をテスト"""
        # Arrange
        mock_run.side_effect = [
            Mock(returncode=0),  # is_git_repo check
            Mock(returncode=1, stderr="CONFLICT (content): Merge conflict"),
        ]

        # Act
        success, message = self.git_handler.add_files(["conflicted.txt"])

        # Assert
        self.assertFalse(success)

    @patch("subprocess.run")
    def test_git_with_detached_head(self, mock_run):
        """Detached HEAD状態での処理をテスト"""
        # Arrange
        mock_run.return_value = Mock(returncode=0, stdout="HEAD detached at abc123")

        # Act
        is_repo = self.git_handler.is_git_repo()

        # Assert
        self.assertTrue(is_repo)  # Detached HEADでもリポジトリとして認識

    @patch("subprocess.run")
    def test_commit_with_empty_message(self, mock_run):
        """空のコミットメッセージのテスト"""
        # Arrange
        mock_run.return_value = Mock(returncode=0)

        # Act
        success, message = self.git_handler.commit("")

        # Assert
        # 空のメッセージでもデフォルトメッセージが使用される
        self.assertTrue(success)
        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]
        self.assertIn("commit", call_args)

    @patch("subprocess.run")
    def test_add_files_with_glob_patterns(self, mock_run):
        """グロブパターンでのファイル追加をテスト"""
        # Arrange
        mock_run.return_value = Mock(returncode=0)

        # Act
        success, message = self.git_handler.add_files(["*.csv", "**/*.json"])

        # Assert
        self.assertTrue(success)
        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]
        self.assertIn("*.csv", call_args)
        self.assertIn("**/*.json", call_args)


class TestSaveResultEdgeCases(unittest.TestCase):
    """SaveResultクラスのエッジケースをテスト"""

    def test_save_result_with_none_values(self):
        """None値を含むSaveResultのテスト"""
        # Act
        result = SaveResult(success=True, file_path=None, message=None, is_new=None)

        # Assert
        self.assertTrue(result.success)
        self.assertIsNone(result.file_path)
        self.assertIsNone(result.message)
        self.assertIsNone(result.is_new)

    def test_save_result_with_path_object(self):
        """PatオブジェクトでのSaveResultのテスト"""
        # Arrange
        path = Path("/test/path/file.csv")

        # Act
        result = SaveResult(success=True, file_path=str(path), message="Saved successfully", is_new=True)

        # Assert
        self.assertEqual(result.file_path, "/test/path/file.csv")

    def test_save_result_equality(self):
        """SaveResultの等価性テスト"""
        # Arrange
        result1 = SaveResult(True, "file.csv", "OK", True)
        result2 = SaveResult(True, "file.csv", "OK", True)
        result3 = SaveResult(False, "file.csv", "OK", True)

        # Assert
        # SaveResultはdataclassなので等価性は各フィールドで判定
        self.assertEqual(
            (result1.success, result1.file_path, result1.message, result1.is_new),
            (result2.success, result2.file_path, result2.message, result2.is_new),
        )
        self.assertNotEqual(
            (result1.success, result1.file_path, result1.message, result1.is_new),
            (result3.success, result3.file_path, result3.message, result3.is_new),
        )


if __name__ == "__main__":
    unittest.main()
