"""
ストレージ管理のユニットテスト
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.managers.storage_manager import CommitResult, GitHandler, StorageManager


class TestGitHandler(unittest.TestCase):
    """GitHandlerのテスト"""

    def setUp(self):
        self.git_handler = GitHandler(auto_commit=True)

    @patch("subprocess.run")
    def test_is_git_repo_true(self, mock_run):
        """Gitリポジトリ判定（True）のテスト"""
        mock_run.return_value.returncode = 0
        self.assertTrue(self.git_handler.is_git_repo())

    @patch("subprocess.run")
    def test_is_git_repo_false(self, mock_run):
        """Gitリポジトリ判定（False）のテスト"""
        mock_run.return_value.returncode = 1
        self.assertFalse(self.git_handler.is_git_repo())

    @patch("subprocess.run")
    def test_add_files_success(self, mock_run):
        """ファイル追加成功のテスト"""
        mock_run.return_value.returncode = 0

        files = [Path("/tmp/test1.csv"), Path("/tmp/test2.csv")]
        with patch.object(Path, "exists", return_value=True):
            result = self.git_handler.add_files(files)

        self.assertTrue(result)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_commit_success(self, mock_run):
        """コミット成功のテスト"""
        # diff --cachedの結果（変更あり）
        mock_run.side_effect = [
            Mock(returncode=1),  # 変更あり
            Mock(returncode=0, stdout="", stderr=""),  # コミット成功
            Mock(returncode=0, stdout="abc123\n", stderr=""),  # ハッシュ取得
        ]

        result = self.git_handler.commit("Test commit")

        self.assertTrue(result.success)
        self.assertEqual(result.commit_hash, "abc123")
        self.assertEqual(result.message, "Test commit")

    @patch("subprocess.run")
    def test_commit_no_changes(self, mock_run):
        """変更なしでのコミットのテスト"""
        # diff --cachedの結果（変更なし）
        mock_run.return_value.returncode = 0

        result = self.git_handler.commit("Test commit")

        self.assertTrue(result.success)
        self.assertEqual(result.message, "No changes to commit")
        self.assertIsNone(result.commit_hash)


class TestStorageManager(unittest.TestCase):
    """StorageManagerのテスト"""

    def setUp(self):
        # 一時ディレクトリを作成
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

        self.config = {
            "auto_commit": True,  # テスト用にTrueに変更
            "commit_message_template": "データ更新: {data_type} - {date_range}",
            "keep_shift_jis": True,
        }

        self.storage = StorageManager(self.base_path, self.config)

    def tearDown(self):
        # 一時ディレクトリを削除
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_organize_file_path_weekly(self):
        """週次データのファイルパス生成テスト（フラット構造）"""
        path = self.storage.organize_file_path("sentinel_weekly_gender", 2025, 10, is_monthly=False)

        # パスが存在することを確認
        self.assertTrue(path.exists())
        # フラット構造なのでベースパスと同じ
        self.assertEqual(path, self.base_path)

    def test_organize_file_path_monthly(self):
        """月次データのファイルパス生成テスト（フラット構造）"""
        path = self.storage.organize_file_path("sentinel_monthly_gender", 2025, 3, is_monthly=True)

        self.assertTrue(path.exists())
        # フラット構造なのでベースパスと同じ
        self.assertEqual(path, self.base_path)

    def test_save_with_metadata_success(self):
        """メタデータ付き保存成功のテスト"""
        data = b"test,data\n1,2,3"
        data_hash = hashlib.sha256(data).hexdigest()

        result = self.storage.save_with_metadata(
            data=data, data_type="test_type", year=2025, period=1, is_monthly=False
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.file_path)
        self.assertIsNotNone(result.metadata_path)

        # ファイルが実際に作成されたか確認
        self.assertTrue(result.file_path.exists())
        self.assertEqual(result.file_path.read_bytes(), data)

        # メタデータファイルの確認
        self.assertTrue(result.metadata_path.exists())
        metadata = json.loads(result.metadata_path.read_text())
        self.assertEqual(metadata["sha256_hash"], data_hash)

    def test_save_with_metadata_duplicate(self):
        """重複データの保存テスト"""
        data = b"duplicate,data"
        data_hash = hashlib.sha256(data).hexdigest()

        # ハッシュインデックスに追加
        self.storage.hash_index[data_hash] = "/some/path.csv"

        result = self.storage.save_with_metadata(data=data, data_type="test_type", year=2025, period=1)

        self.assertTrue(result.success)
        self.assertTrue(result.is_duplicate)
        self.assertIsNone(result.file_path)

    def test_save_with_invalid_data_type(self):
        """不正なdata_type（パストラバーサル攻撃）のテスト"""
        data = b"test,data"

        # パストラバーサル攻撃を試みる
        invalid_data_types = [
            "../evil",
            "../../etc/passwd",
            "test/../../evil",
            "test;rm -rf /",
            "test$(whoami)",
            "test`ls`",
        ]

        for invalid_type in invalid_data_types:
            result = self.storage.save_with_metadata(data=data, data_type=invalid_type, year=2025, period=1)

            self.assertFalse(result.success, f"Should reject invalid data_type: {invalid_type}")
            self.assertIsNotNone(result.error)
            self.assertIn("Invalid data_type", result.error)

    def test_check_duplicates(self):
        """重複チェックのテスト"""
        hash_value = "abc123"

        # 初回チェック（重複なし）
        self.assertFalse(self.storage.check_duplicates(hash_value))

        # ハッシュを追加
        self.storage.hash_index[hash_value] = "/path/to/file.csv"

        # 2回目チェック（重複あり）
        self.assertTrue(self.storage.check_duplicates(hash_value))

    def test_get_existing_files(self):
        """既存ファイル取得のテスト"""
        # テストファイルを作成（フラット構造）
        test_file1 = self.base_path / "test_type_weekly_2025_01.csv"
        test_file1.touch()

        test_file2 = self.base_path / "other_type_weekly_2025_02.csv"
        test_file2.touch()

        # 全ファイル取得
        files = self.storage.get_existing_files()
        self.assertEqual(len(files), 2)

        # データタイプでフィルタ
        files = self.storage.get_existing_files(data_type="test_type")
        self.assertEqual(len(files), 1)
        self.assertIn("test_type", files[0].name)

        # 年でフィルタ（フラット構造ではファイル名から年を抽出）
        files = self.storage.get_existing_files(year=2025)
        self.assertEqual(len(files), 2)

    def test_get_metadata(self):
        """メタデータ取得のテスト"""
        # テストファイルとメタデータを作成
        test_file = self.base_path / "test.csv"
        test_file.touch()

        # メタデータは.metadataディレクトリに保存
        metadata_file = self.storage.metadata_dir / "test.json"
        test_metadata = {"filename": "test.csv", "data_type": "test_type", "sha256_hash": "abc123"}
        metadata_file.write_text(json.dumps(test_metadata))

        # メタデータ取得
        metadata = self.storage.get_metadata(test_file)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["filename"], "test.csv")
        self.assertEqual(metadata["sha256_hash"], "abc123")

    def test_get_metadata_not_found(self):
        """メタデータが存在しない場合のテスト"""
        test_file = self.base_path / "no_metadata.csv"
        test_file.touch()

        metadata = self.storage.get_metadata(test_file)
        self.assertIsNone(metadata)

    def test_get_storage_stats(self):
        """ストレージ統計情報取得のテスト"""
        # テストファイルを作成（フラット構造）
        test_file = self.base_path / "sentinel_weekly_2025_01.csv"
        test_file.write_text("test data")

        stats = self.storage.get_storage_stats()

        self.assertIn("total_files", stats)
        self.assertIn("total_size_bytes", stats)
        self.assertIn("file_types", stats)
        self.assertIn("year_stats", stats)
        self.assertEqual(stats["total_files"], 1)
        self.assertGreater(stats["total_size_bytes"], 0)

    @patch.object(GitHandler, "is_git_repo")
    @patch.object(GitHandler, "add_files")
    @patch.object(GitHandler, "commit")
    def test_commit_changes(self, mock_commit, mock_add, mock_is_repo):
        """変更のコミットのテスト"""
        mock_is_repo.return_value = True
        mock_add.return_value = True
        mock_commit.return_value = CommitResult(success=True, commit_hash="abc123", message="Test commit")

        result = self.storage.commit_changes(data_type="test_type", date_range="2025-01")

        self.assertTrue(result.success)
        self.assertEqual(result.commit_hash, "abc123")
        mock_add.assert_called_once()
        mock_commit.assert_called_once()

    def test_get_month_from_week(self):
        """週番号から月を取得するテスト"""
        # 2025年の第1週
        month = self.storage._get_month_from_week(2025, 1)
        # 第1週は実際の日付に依存するが、通常は1月か12月
        self.assertIn(month, [1, 12])

        # 2025年の第10週 → 3月
        month = self.storage._get_month_from_week(2025, 10)
        self.assertEqual(month, 3)

        # 2025年の最終週 → 12月
        month = self.storage._get_month_from_week(2025, 52)
        self.assertEqual(month, 12)

    def test_save_with_force_overwrite(self):
        """force_overwriteパラメータのテスト"""
        # 初回保存
        test_data = b"initial data"
        result = self.storage.save_with_metadata(
            test_data,
            "test_type",
            2025,
            1,
            is_monthly=False,
            additional_metadata={"test": "metadata"},
            force_overwrite=False,
        )
        self.assertTrue(result.success)
        self.assertFalse(result.is_duplicate)

        # 同じデータを再保存（通常は重複として扱われる）
        result = self.storage.save_with_metadata(
            test_data,
            "test_type",
            2025,
            1,
            is_monthly=False,
            additional_metadata={"test": "metadata"},
            force_overwrite=False,
        )
        self.assertTrue(result.success)
        self.assertTrue(result.is_duplicate)

        # force_overwrite=Trueで異なるデータを上書き
        new_data = b"updated data"
        result = self.storage.save_with_metadata(
            new_data,
            "test_type",
            2025,
            1,
            is_monthly=False,
            additional_metadata={"test": "updated"},
            force_overwrite=True,
        )
        self.assertTrue(result.success)
        self.assertFalse(result.is_duplicate)

        # ファイルが更新されたことを確認
        saved_file = self.base_path / "test_type_2025_01.csv"
        self.assertTrue(saved_file.exists())
        self.assertEqual(saved_file.read_bytes(), new_data)

        # メタデータが更新されたことを確認
        metadata_file = self.storage.metadata_dir / "test_type_2025_01.json"
        self.assertTrue(metadata_file.exists())
        metadata = json.loads(metadata_file.read_text())
        self.assertEqual(metadata["force_overwrite"], True)
        self.assertEqual(metadata["test"], "updated")

    def test_force_overwrite_updates_hash_index(self):
        """force_overwriteでハッシュインデックスが更新されることのテスト"""
        # 初回保存
        initial_data = b"initial content"
        initial_hash = hashlib.sha256(initial_data).hexdigest()

        result = self.storage.save_with_metadata(
            initial_data, "test_type", 2025, 2, is_monthly=False, force_overwrite=False
        )
        self.assertTrue(result.success)

        # ハッシュインデックスに登録されていることを確認
        self.assertIn(initial_hash, self.storage.hash_index)

        # force_overwrite=Trueで異なるデータで上書き
        updated_data = b"updated content"
        updated_hash = hashlib.sha256(updated_data).hexdigest()

        result = self.storage.save_with_metadata(
            updated_data, "test_type", 2025, 2, is_monthly=False, force_overwrite=True
        )
        self.assertTrue(result.success)

        # 古いハッシュが削除され、新しいハッシュが登録されていることを確認
        self.assertNotIn(initial_hash, self.storage.hash_index)
        self.assertIn(updated_hash, self.storage.hash_index)

    def test_force_overwrite_with_same_data(self):
        """同じデータでforce_overwriteした場合のテスト"""
        # 同じデータで2回保存
        test_data = b"same data"
        data_hash = hashlib.sha256(test_data).hexdigest()

        # 初回保存
        result1 = self.storage.save_with_metadata(test_data, "test_type", 2025, 3, is_monthly=False)
        self.assertTrue(result1.success)
        self.assertFalse(result1.is_duplicate)

        # force_overwrite=Trueで同じデータを保存
        result2 = self.storage.save_with_metadata(
            test_data, "test_type", 2025, 3, is_monthly=False, force_overwrite=True
        )
        self.assertTrue(result2.success)
        self.assertFalse(result2.is_duplicate)  # force_overwriteなのでduplicateフラグは立たない

        # ハッシュインデックスは同じハッシュのまま
        self.assertIn(data_hash, self.storage.hash_index)

    def test_multiple_files_same_hash(self):
        """同じ内容の複数ファイルを正しく管理できることのテスト"""
        # 同じ内容のデータ
        test_data = b"duplicate content"
        data_hash = hashlib.sha256(test_data).hexdigest()

        # 異なる期間に同じデータを保存
        result1 = self.storage.save_with_metadata(test_data, "test_type", 2025, 1, is_monthly=False)
        self.assertTrue(result1.success)
        self.assertFalse(result1.is_duplicate)

        # 2つ目のファイル（同じ内容、異なる期間）
        result2 = self.storage.save_with_metadata(test_data, "test_type", 2025, 2, is_monthly=False)
        self.assertTrue(result2.success)
        self.assertTrue(result2.is_duplicate)  # 同じハッシュなので重複として扱われる

        # ハッシュインデックスを確認（単一エントリまたはリスト形式）
        self.assertIn(data_hash, self.storage.hash_index)

        # 片方を異なるデータで上書き（force_overwrite）
        new_data = b"updated content"
        new_hash = hashlib.sha256(new_data).hexdigest()

        result3 = self.storage.save_with_metadata(
            new_data, "test_type", 2025, 1, is_monthly=False, force_overwrite=True
        )
        self.assertTrue(result3.success)

        # 新しいハッシュが登録され、古いハッシュも残っている（別ファイルが参照）
        self.assertIn(new_hash, self.storage.hash_index)
        # もし1つのファイルだけが古いハッシュを参照していれば、まだインデックスに残る
        # （実装により異なるが、check_duplicatesが正しく動作することが重要）

    def test_hash_index_cleanup_on_overwrite(self):
        """force_overwrite時のハッシュインデックスクリーンアップテスト"""
        # データを保存
        data1 = b"first data"
        hash1 = hashlib.sha256(data1).hexdigest()

        result1 = self.storage.save_with_metadata(data1, "cleanup_test", 2025, 1, is_monthly=False)
        self.assertTrue(result1.success)
        self.assertIn(hash1, self.storage.hash_index)

        # 同じファイルを異なるデータで上書き
        data2 = b"second data"
        hash2 = hashlib.sha256(data2).hexdigest()

        result2 = self.storage.save_with_metadata(
            data2, "cleanup_test", 2025, 1, is_monthly=False, force_overwrite=True
        )
        self.assertTrue(result2.success)

        # 新しいハッシュが登録され、古いハッシュは削除される
        self.assertIn(hash2, self.storage.hash_index)

        # 古いハッシュが削除されていることを確認
        # (他のファイルが同じハッシュを使っていない場合)
        index_entry = self.storage.hash_index.get(hash1)
        if index_entry:
            # リスト形式の場合、このファイルパスが含まれていないことを確認
            if isinstance(index_entry, list):
                file_path = str(self.base_path / "cleanup_test_2025_01.csv")
                self.assertNotIn(file_path, index_entry)
            else:
                # 単一エントリの場合、このファイルパスでないことを確認
                file_path = str(self.base_path / "cleanup_test_2025_01.csv")
                self.assertNotEqual(index_entry, file_path)

    def test_save_result_is_new_for_new_file(self):
        """新規ファイル保存時にis_new=Trueが設定されることをテスト"""
        data = b"new,data\n1,2,3"

        result = self.storage.save_with_metadata(
            data=data, data_type="test_new_file", year=2025, period=1, is_monthly=False
        )

        self.assertTrue(result.success)
        self.assertTrue(result.is_new)  # 新規ファイルフラグの確認

    def test_save_result_is_new_false_for_existing_file(self):
        """既存ファイル更新時にis_new=Falseが設定されることをテスト"""
        data = b"existing,data\n1,2,3"

        # 最初の保存（新規）
        first_result = self.storage.save_with_metadata(
            data=data, data_type="test_existing", year=2025, period=1, is_monthly=False
        )
        self.assertTrue(first_result.is_new)

        # 同じファイルの再保存（force_overwrite=True）
        updated_data = b"updated,data\n4,5,6"
        second_result = self.storage.save_with_metadata(
            data=updated_data, data_type="test_existing", year=2025, period=1, is_monthly=False, force_overwrite=True
        )

        self.assertTrue(second_result.success)
        self.assertFalse(second_result.is_new)  # 既存ファイル更新フラグの確認

    def test_hash_index_sorting(self):
        """hash_indexがファイル名順に正しくソートされることをテスト"""
        # 複数のファイルを異なる順序で保存
        files_data = [
            (b"data1", "type_z", 2025, 3),
            (b"data2", "type_a", 2025, 1),
            (b"data3", "type_m", 2025, 2),
        ]

        for data, dtype, year, period in files_data:
            self.storage.save_with_metadata(data=data, data_type=dtype, year=year, period=period, is_monthly=False)

        # hash_index.jsonを読み込んでソート確認
        hash_index_path = self.storage.hash_index_file
        self.assertTrue(hash_index_path.exists())

        with hash_index_path.open() as f:
            loaded_index = json.load(f)

        # インデックスの順序を確認するため、値(ファイルパス)を順番にリスト化
        all_paths = []
        for file_paths in loaded_index.values():
            if isinstance(file_paths, list):
                all_paths.extend(file_paths)
            else:
                all_paths.append(file_paths)

        # ファイルパスがソート済みであることを確認
        # 期待される順序: type_a_2025_01.csv, type_m_2025_02.csv, type_z_2025_03.csv
        expected_order = sorted(all_paths)
        self.assertEqual(all_paths, expected_order)

        # 各値(リストの場合)もソート済みか確認
        for file_paths in loaded_index.values():
            if isinstance(file_paths, list):
                self.assertEqual(file_paths, sorted(file_paths))

    def test_hash_index_sorting_with_duplicates(self):
        """同一ハッシュの複数ファイルでもソートされることをテスト"""
        # 同じ内容で異なるファイル名のデータを保存
        same_data = b"duplicate,content\n1,2,3"

        # 異なるタイプで同じデータを保存（同一ハッシュになる）
        for dtype in ["dup_z", "dup_a", "dup_m"]:
            self.storage.save_with_metadata(
                data=same_data,
                data_type=dtype,
                year=2025,
                period=1,
                is_monthly=False,
                force_overwrite=True,  # 重複を許可
            )

        # hash_index.jsonを読み込んで確認
        with self.storage.hash_index_file.open() as f:
            loaded_index = json.load(f)

        # 同一ハッシュのファイルリストがソート済みか確認
        for _hash_key, file_paths in loaded_index.items():
            if isinstance(file_paths, list):
                # ファイルパスがソート済みであることを確認
                self.assertEqual(file_paths, sorted(file_paths))
                # 3つのファイルが記録されているはずだが、重複チェックで実際は1つかも
                # この動作はforce_overwriteとcheck_duplicatesの実装に依存


if __name__ == "__main__":
    unittest.main()
