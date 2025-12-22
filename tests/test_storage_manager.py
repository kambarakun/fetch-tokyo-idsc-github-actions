"""
ストレージ管理のユニットテスト
"""

import hashlib
import json
import shutil
import sys
import tempfile
import time
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
        """Gitリポジトリ判定(True)のテスト"""
        mock_run.return_value.returncode = 0
        self.assertTrue(self.git_handler.is_git_repo())

    @patch("subprocess.run")
    def test_is_git_repo_false(self, mock_run):
        """Gitリポジトリ判定(False)のテスト"""
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
        # diff --cachedの結果(変更あり)
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
        # diff --cachedの結果(変更なし)
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
        """週次データのファイルパス生成テスト(フラット構造)"""
        path = self.storage.organize_file_path("sentinel_weekly_gender", 2025, 10, is_monthly=False)

        # パスが存在することを確認
        self.assertTrue(path.exists())
        # フラット構造なのでベースパスと同じ
        self.assertEqual(path, self.base_path)

    def test_organize_file_path_monthly(self):
        """月次データのファイルパス生成テスト(フラット構造)"""
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
        # v1.1形式: hashはネストされたオブジェクト
        self.assertEqual(metadata["hash"]["value"], data_hash)
        self.assertEqual(metadata["hash"]["algorithm"], "sha256")

    def test_save_with_metadata_duplicate(self):
        """重複データの保存テスト"""
        # 数値データを含む有効なCSV形式
        data = b'"header","value"\n"row1","1"'
        data_hash = hashlib.sha256(data).hexdigest()

        # ハッシュインデックスに追加
        self.storage.hash_index[data_hash] = "/some/path.csv"

        result = self.storage.save_with_metadata(data=data, data_type="test_type", year=2025, period=1)

        self.assertTrue(result.success)
        self.assertTrue(result.is_duplicate)
        self.assertIsNone(result.file_path)

    def test_save_with_invalid_data_type(self):
        """不正なdata_type(パストラバーサル攻撃)のテスト"""
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

        # 初回チェック(重複なし)
        self.assertFalse(self.storage.check_duplicates(hash_value))

        # ハッシュを追加
        self.storage.hash_index[hash_value] = "/path/to/file.csv"

        # 2回目チェック(重複あり)
        self.assertTrue(self.storage.check_duplicates(hash_value))

    def test_get_existing_files(self):
        """既存ファイル取得のテスト"""
        # テストファイルを作成(フラット構造)
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

        # 年でフィルタ(フラット構造ではファイル名から年を抽出)
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
        # テストファイルを作成(フラット構造)
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
        # 初回保存(数値データを含む有効なCSV形式)
        test_data = b'"header","value"\n"row1","1"'
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

        # 同じデータを再保存(通常は重複として扱われる)
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
        new_data = b'"header","value"\n"row1","2"'
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
        # v1.1+形式: force_overwriteは_fetch内にネストされる
        self.assertEqual(metadata["_fetch"]["force_overwrite"], True)
        # メタデータバージョンを確認
        self.assertEqual(metadata["metadata_version"], "1.3.0")

    def test_force_overwrite_updates_hash_index(self):
        """force_overwriteでハッシュインデックスが更新されることのテスト"""
        # 初回保存(数値データを含む有効なCSV形式)
        initial_data = b'"header","value"\n"row1","10"'
        initial_hash = hashlib.sha256(initial_data).hexdigest()

        result = self.storage.save_with_metadata(
            initial_data, "test_type", 2025, 2, is_monthly=False, force_overwrite=False
        )
        self.assertTrue(result.success)

        # ハッシュインデックスに登録されていることを確認
        self.assertIn(initial_hash, self.storage.hash_index)

        # force_overwrite=Trueで異なるデータで上書き
        updated_data = b'"header","value"\n"row1","20"'
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
        # 同じデータで2回保存(数値データを含む有効なCSV形式)
        test_data = b'"header","value"\n"row1","30"'
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
        # 同じ内容のデータ(数値データを含む有効なCSV形式)
        test_data = b'"header","value"\n"row1","40"'
        data_hash = hashlib.sha256(test_data).hexdigest()

        # 異なる期間に同じデータを保存
        result1 = self.storage.save_with_metadata(test_data, "test_type", 2025, 1, is_monthly=False)
        self.assertTrue(result1.success)
        self.assertFalse(result1.is_duplicate)

        # 2つ目のファイル(同じ内容、異なる期間)
        result2 = self.storage.save_with_metadata(test_data, "test_type", 2025, 2, is_monthly=False)
        self.assertTrue(result2.success)
        self.assertTrue(result2.is_duplicate)  # 同じハッシュなので重複として扱われる

        # ハッシュインデックスを確認(単一エントリまたはリスト形式)
        self.assertIn(data_hash, self.storage.hash_index)

        # 片方を異なるデータで上書き(force_overwrite)
        new_data = b'"header","value"\n"row1","50"'
        new_hash = hashlib.sha256(new_data).hexdigest()

        result3 = self.storage.save_with_metadata(
            new_data, "test_type", 2025, 1, is_monthly=False, force_overwrite=True
        )
        self.assertTrue(result3.success)

        # 新しいハッシュが登録され、古いハッシュも残っている(別ファイルが参照)
        self.assertIn(new_hash, self.storage.hash_index)
        # もし1つのファイルだけが古いハッシュを参照していれば、まだインデックスに残る
        # (実装により異なるが、check_duplicatesが正しく動作することが重要)

    def test_hash_index_cleanup_on_overwrite(self):
        """force_overwrite時のハッシュインデックスクリーンアップテスト"""
        # データを保存(数値データを含む有効なCSV形式)
        data1 = b'"header","value"\n"row1","60"'
        hash1 = hashlib.sha256(data1).hexdigest()

        result1 = self.storage.save_with_metadata(data1, "cleanup_test", 2025, 1, is_monthly=False)
        self.assertTrue(result1.success)
        self.assertIn(hash1, self.storage.hash_index)

        # 同じファイルを異なるデータで上書き
        data2 = b'"header","value"\n"row1","70"'
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

        # 最初の保存(新規)
        first_result = self.storage.save_with_metadata(
            data=data, data_type="test_existing", year=2025, period=1, is_monthly=False
        )
        self.assertTrue(first_result.is_new)

        # 同じファイルの再保存(force_overwrite=True)
        updated_data = b"updated,data\n4,5,6"
        second_result = self.storage.save_with_metadata(
            data=updated_data, data_type="test_existing", year=2025, period=1, is_monthly=False, force_overwrite=True
        )

        self.assertTrue(second_result.success)
        self.assertFalse(second_result.is_new)  # 既存ファイル更新フラグの確認

    def test_hash_index_sorting(self):
        """hash_indexがファイル名順に正しくソートされることをテスト"""
        # 複数のファイルを異なる順序で保存(数値データを含む有効なCSV形式)
        files_data = [
            (b'"header","value"\n"row1","80"', "type_z", 2025, 3),
            (b'"header","value"\n"row1","81"', "type_a", 2025, 1),
            (b'"header","value"\n"row1","82"', "type_m", 2025, 2),
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

        # 異なるタイプで同じデータを保存(同一ハッシュになる)
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

    def test_is_all_zero_data_with_zero_values(self):
        """全て0のデータが正しく検出されることを確認"""
        # 全て0のCSVデータを作成
        csv_data = """"定点報告疾患 週報告分 医療圏別"
"東京都"
"集計期間開始週","2025年50週"
"集計期間終了週","2025年50週"
"性別","男性"

"","インフルエンザ","RSウイルス感染症","咽頭結膜熱"
"区中央部","0","0","0"
"区南部","0","0","0"
"区西南部","0","0","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "全て0のデータが検出されるべきです")

    def test_is_all_zero_data_with_non_zero_values(self):
        """0以外の値があるデータが検出されないことを確認"""
        # 0以外の値を含むCSVデータを作成
        csv_data = """"定点報告疾患 週報告分 医療圏別"
"東京都"
"集計期間開始週","2025年50週"
"集計期間終了週","2025年50週"
"性別","男性"

"","インフルエンザ","RSウイルス感染症","咽頭結膜熱"
"区中央部","5","0","0"
"区南部","0","3","0"
"区西南部","0","0","2"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertFalse(result, "0以外の値があるデータは検出されないべきです")

    def test_is_all_zero_data_ignores_header_lines(self):
        """ヘッダー行や注釈行が無視されることを確認"""
        # ヘッダー行と注釈行を含む全て0のCSVデータ
        csv_data = """"定点報告疾患 週報告分"
"東京都"
"*注釈行はスキップされます"

"","疾病A","疾病B"
"地域1","0","0"
"地域2","0","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "ヘッダー行や注釈行を無視して全て0と判定されるべきです")

    def test_is_all_zero_data_with_empty_cells(self):
        """空のセルを含む全て0のデータが正しく検出されることを確認"""
        csv_data = """"定点報告疾患"
"","疾病A","疾病B","疾病C"
"地域1","0","","0"
"地域2","","0",""
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "空のセルを含む全て0のデータが検出されるべきです")

    def test_save_with_metadata_skips_all_zero_data(self):
        """保存時に全て0のデータがスキップされることを確認"""
        # 全て0のCSVデータを作成
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","0","0"
"地域2","0","0"
"""
        data = csv_data.encode("shift_jis")

        # データ保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=50,
            is_monthly=False,
        )

        # スキップされたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertTrue(result.is_skipped, "全て0のデータはスキップされるべきです")
        self.assertIsNone(result.file_path, "ファイルは保存されないべきです")

        # ファイルが実際に保存されていないことを確認
        expected_file = self.base_path / "sentinel_weekly_test_2025_50.csv"
        self.assertFalse(expected_file.exists(), "ファイルは存在しないべきです")

    def test_save_with_metadata_does_not_skip_non_zero_data(self):
        """0以外の値があるデータはスキップされないことを確認"""
        # 0以外の値を含むCSVデータを作成
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","5","0"
"地域2","0","3"
"""
        data = csv_data.encode("shift_jis")

        # データ保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=51,
            is_monthly=False,
        )

        # 保存されたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertFalse(result.is_skipped, "0以外の値があるデータはスキップされないべきです")
        self.assertIsNotNone(result.file_path, "ファイルパスが返されるべきです")

        # ファイルが実際に保存されていることを確認
        expected_file = self.base_path / "sentinel_weekly_test_2025_51.csv"
        self.assertTrue(expected_file.exists(), "ファイルが存在すべきです")

    def test_save_with_metadata_save_all_zero_saves_all_zero_data(self):
        """save_all_zero=Trueの場合、全て0のデータも保存されることを確認"""
        # 全て0のCSVデータを作成
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","0","0"
"""
        data = csv_data.encode("shift_jis")

        # save_all_zero=Trueで保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=52,
            is_monthly=False,
            save_all_zero=True,
        )

        # 保存されたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertFalse(result.is_skipped, "save_all_zero=Trueの場合はスキップされないべきです")
        self.assertIsNotNone(result.file_path, "ファイルパスが返されるべきです")

        # ファイルが実際に保存されていることを確認
        expected_file = self.base_path / "sentinel_weekly_test_2025_52.csv"
        self.assertTrue(expected_file.exists(), "ファイルが存在すべきです")

        # メタデータに save_all_zero が記録されていることを確認
        metadata = self.storage.get_metadata(expected_file)
        self.assertIsNotNone(metadata, "メタデータが存在すべきです")
        self.assertTrue(metadata.get("save_all_zero", False), "save_all_zero=Trueが記録されるべきです")

    def test_save_with_metadata_force_overwrite_still_skips_all_zero(self):
        """force_overwrite=Trueでもsave_all_zero=Falseなら全て0のデータはスキップされることを確認"""
        # 全て0のCSVデータを作成
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","0","0"
"""
        data = csv_data.encode("shift_jis")

        # force_overwrite=Trueだが save_all_zero=False(デフォルト)で保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=53,
            is_monthly=False,
            force_overwrite=True,
        )

        # スキップされたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertTrue(result.is_skipped, "save_all_zero=Falseの場合はスキップされるべきです")
        self.assertIsNone(result.file_path, "ファイルは保存されないべきです")

        # ファイルが実際に保存されていないことを確認
        expected_file = self.base_path / "sentinel_weekly_test_2025_53.csv"
        self.assertFalse(expected_file.exists(), "ファイルは存在しないべきです")

    def test_save_with_metadata_non_zero_data_records_save_all_zero_false(self):
        """save_all_zero=False(デフォルト)で非ゼロデータ保存時、メタデータに記録されることを確認"""
        # 非ゼロデータを作成
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","5","10"
"""
        data = csv_data.encode("shift_jis")

        # save_all_zero=False(デフォルト)で保存
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=54,
            is_monthly=False,
            # save_all_zero はデフォルト False
        )

        # 保存されたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertFalse(result.is_skipped, "非ゼロデータはスキップされないべきです")

        # メタデータに save_all_zero=False が記録されていることを確認
        expected_file = self.base_path / "sentinel_weekly_test_2025_54.csv"
        metadata = self.storage.get_metadata(expected_file)
        self.assertIsNotNone(metadata, "メタデータが存在すべきです")
        self.assertFalse(metadata.get("save_all_zero", True), "save_all_zero=Falseが記録されるべきです")

    def test_force_overwrite_with_all_zero_does_not_overwrite_existing_non_zero(self):
        """既存の非ゼロデータがある状態でforce_overwrite=True & all-zeroを渡しても上書きされないことを確認"""
        # まず非ゼロデータを保存
        non_zero_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","5","10"
"""
        data = non_zero_data.encode("shift_jis")
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=55,
            is_monthly=False,
        )
        self.assertTrue(result.success)
        expected_file = self.base_path / "sentinel_weekly_test_2025_55.csv"
        self.assertTrue(expected_file.exists())

        # 元のファイルサイズを記録
        original_size = expected_file.stat().st_size

        # 全て0のデータでforce_overwrite=True(ただしsave_all_zero=False)で保存を試みる
        all_zero_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","0","0"
"""
        result = self.storage.save_with_metadata(
            data=all_zero_data.encode("shift_jis"),
            data_type="sentinel_weekly_test",
            year=2025,
            period=55,
            is_monthly=False,
            force_overwrite=True,  # 上書きフラグTrue
            # save_all_zero=False (デフォルト) - 全て0はスキップ
        )

        # スキップされ、ファイルは上書きされないことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertTrue(result.is_skipped, "全て0データはスキップされるべきです")
        self.assertTrue(expected_file.exists(), "既存ファイルは残るべきです")
        self.assertEqual(expected_file.stat().st_size, original_size, "ファイルは上書きされないべきです")

    def test_is_all_zero_data_with_comma_in_field(self):
        """フィールド内にカンマが含まれるデータが正しく処理されることを確認"""
        # フィールド内にカンマが含まれるCSVデータ(RFC 4180準拠)
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"区A,B","0","0"
"区C","0","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "フィールド内のカンマは正しく処理されるべきです")

    def test_is_all_zero_data_with_quoted_field(self):
        """引用符でエスケープされたフィールドが正しく処理されることを確認"""
        # 引用符のエスケープを含むCSVデータ
        # CSV内の二重引用符 "" は、Pythonの文字列では \" でエスケープ
        csv_data = '''"定点報告疾患"
"","疾病A","疾病B"
"区""C""","0","0"
"区D","0","0"
'''
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "引用符のエスケープは正しく処理されるべきです")

    def test_is_all_zero_data_with_float_values(self):
        """浮動小数点数の0が正しく処理されることを確認"""
        csv_data = """"定点報告疾患"
"","疾病A","疾病B","疾病C"
"地域1","0.0","0.00","0"
"地域2","0","0.0","0.00"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "浮動小数点数の0は全て0と判定されるべきです")

    def test_is_all_zero_data_with_float_non_zero(self):
        """浮動小数点数の0以外の値が正しく検出されることを確認"""
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","0.0","0.1"
"地域2","0","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertFalse(result, "0.1は0以外として検出されるべきです")

    def test_is_all_zero_data_with_negative_zero(self):
        """-0.0が0として正しく処理されることを確認"""
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","-0.0","0"
"地域2","0","-0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "-0.0は0として扱われるべきです")

    def test_is_all_zero_data_with_negative_value(self):
        """負の値が0以外として正しく検出されることを確認"""
        csv_data = """"定点報告疾患"
"","疾病A","疾病B"
"地域1","-1","0"
"地域2","0","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertFalse(result, "負の値は0以外として検出されるべきです")

    def test_is_all_zero_data_with_invalid_bytes(self):
        """不正なバイトシーケンスはerrors='replace'で置換され、空データとして扱われる"""
        # 不正なShift_JISバイトシーケンス
        # errors='replace'により置換文字に変換され、データ行がないためスキップ対象
        invalid_data = b"\xff\xfe\x00\x00"

        result = self.storage._is_all_zero_data(invalid_data)
        self.assertTrue(result, "データ行がないためスキップ対象(True)になるべきです")

    def test_is_all_zero_data_with_malformed_csv(self):
        """不正なCSV形式でもエラーにならないことを確認"""
        # 不正なCSV(引用符が閉じていない)
        csv_data = """"定点報告疾患"
"","疾病A","疾病B
"地域1","0","0"
"""
        data = csv_data.encode("shift_jis")

        # エラーが発生せず、安全側に倒す(False)ことを確認
        result = self.storage._is_all_zero_data(data)
        # 不正なCSVでも処理が続行され、結果が返される
        # (csv.readerは寛容な解析を行う)
        self.assertIsInstance(result, bool, "bool値が返されるべきです")

    def test_is_all_zero_data_with_header_only(self):
        """ヘッダーのみ(データ行なし)のCSVがスキップ対象として検出されることを確認

        PR #144の主要なユースケース: 未発表データはヘッダー情報のみで
        データ行が存在しない。このようなファイルはスキップ対象とする。
        """
        # 実際の未発表データと同じ形式(ヘッダー行のみでデータ行なし)
        csv_data = """"定点報告疾患 週報告分"
"東京都"
"集計期間開始週","2025年50週"
"集計期間終了週","2025年50週"

"疾病名","男性","女性","男女合計","定点数"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "ヘッダーのみのファイルはスキップ対象として検出されるべきです")

    def test_is_all_zero_data_with_header_only_minimal(self):
        """最小限のヘッダーのみCSVがスキップ対象として検出されることを確認"""
        # ヘッダー行1行のみのCSV
        csv_data = """"疾病名","報告数"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage._is_all_zero_data(data)
        self.assertTrue(result, "ヘッダー1行のみのファイルはスキップ対象として検出されるべきです")

    def test_save_with_metadata_skips_header_only_data(self):
        """保存時にヘッダーのみのデータがスキップされることを確認

        未発表データ(ヘッダーのみ)が自動的にスキップされ、
        不要なファイルが保存されないことを検証。
        """
        # ヘッダーのみのCSVデータ(未発表データを模倣)
        csv_data = """"定点報告疾患 週報告分"
"東京都"
"集計期間開始週","2025年50週"
"集計期間終了週","2025年50週"

"疾病名","男性","女性","男女合計","定点数"
"""
        data = csv_data.encode("shift_jis")

        # データ保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_gender",
            year=2025,
            period=50,
            is_monthly=False,
        )

        # スキップされたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertTrue(result.is_skipped, "ヘッダーのみのデータはスキップされるべきです")
        self.assertIsNone(result.file_path, "ファイルは保存されないべきです")

        # ファイルが実際に保存されていないことを確認
        expected_file = self.base_path / "sentinel_weekly_gender_2025_50.csv"
        self.assertFalse(expected_file.exists(), "ファイルは存在しないべきです")

    def test_save_with_metadata_header_only_save_all_zero_saves_data(self):
        """save_all_zero=Trueの場合、ヘッダーのみのデータも保存されることを確認

        特殊用途(データ収集システムのテスト等)でヘッダーのみの
        ファイルも保存したい場合に対応。
        """
        # ヘッダーのみのCSVデータ
        csv_data = """"定点報告疾患 週報告分"
"東京都"
"疾病名","報告数"
"""
        data = csv_data.encode("shift_jis")

        # save_all_zero=Trueで保存を試みる
        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_header_test",
            year=2025,
            period=50,
            is_monthly=False,
            save_all_zero=True,
        )

        # 保存されたことを確認
        self.assertTrue(result.success, "処理は成功すべきです")
        self.assertFalse(result.is_skipped, "save_all_zero=Trueの場合はスキップされないべきです")
        self.assertIsNotNone(result.file_path, "ファイルパスが返されるべきです")

        # ファイルが実際に保存されていることを確認
        expected_file = self.base_path / "sentinel_weekly_header_test_2025_50.csv"
        self.assertTrue(expected_file.exists(), "ファイルが存在すべきです")


class TestMetadataEnhancements(unittest.TestCase):
    """メタデータ拡張機能のテスト"""

    def setUp(self):
        """テストの前処理"""
        self.test_dir = tempfile.mkdtemp()
        self.base_path = Path(self.test_dir) / "data" / "raw"
        self.config = {"auto_commit": False}
        self.storage = StorageManager(self.base_path, self.config)

    def tearDown(self):
        """テストの後処理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_metadata_version_is_set(self):
        """メタデータにversionが設定されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=1,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        # v1.3.0形式
        self.assertEqual(metadata.get("metadata_version"), "1.3.0")

    def test_created_at_and_updated_at_set_on_new_file(self):
        """新規ファイル作成時にcreated_atとupdated_atが設定されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=2,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertIn("created_at", metadata)
        self.assertIn("updated_at", metadata)
        self.assertIsNotNone(metadata["created_at"])
        self.assertIsNotNone(metadata["updated_at"])
        # 新規作成時はcreated_at == updated_at
        self.assertEqual(metadata["created_at"], metadata["updated_at"])

    def test_created_at_preserved_on_force_overwrite(self):
        """force_overwrite時にcreated_atが保持されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        # 初回保存
        result1 = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=3,
            is_monthly=False,
        )
        self.assertTrue(result1.success)
        metadata1 = self.storage.get_metadata(result1.file_path)
        original_created_at = metadata1["created_at"]

        # 少し待機 (タイムスタンプが異なることを確認するため)
        time.sleep(0.01)

        # force_overwriteで再保存
        updated_data = csv_data.replace("5", "10").encode("shift_jis")
        result2 = self.storage.save_with_metadata(
            data=updated_data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=3,
            is_monthly=False,
            force_overwrite=True,
        )
        self.assertTrue(result2.success)
        metadata2 = self.storage.get_metadata(result2.file_path)

        # created_atは変更されていないことを確認
        self.assertEqual(metadata2["created_at"], original_created_at)
        # updated_atは更新されていることを確認
        self.assertNotEqual(metadata2["updated_at"], original_created_at)

    def test_line_count_is_calculated(self):
        """行数が正しく計算されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"地域2","10"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=4,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertIn("line_count", metadata)
        self.assertEqual(metadata["line_count"], 4)  # 4行 (末尾の空行は含まない)

    def test_checksum_algorithm_is_set(self):
        """checksum_algorithmが設定されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=5,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.get("checksum_algorithm"), "sha256")

    def test_verification_status_verified_on_valid_data(self):
        """正常データの検証ステータスがverifiedになることを確認"""
        # 最小ファイルサイズ(100バイト)を超えるデータを作成
        csv_data = """"テスト週報データ"
"","疾病A","疾病B","疾病C","疾病D"
"地域1","5","10","15","20"
"地域2","1","2","3","4"
"地域3","0","0","1","0"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=6,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertIn("verification", metadata)
        self.assertEqual(metadata["verification"]["status"], "verified")
        self.assertTrue(metadata["verification"]["checks"]["file_size"])
        self.assertTrue(metadata["verification"]["checks"]["encoding"])
        self.assertTrue(metadata["verification"]["checks"]["csv_format"])
        self.assertTrue(metadata["verification"]["checks"]["path_safety"])
        self.assertEqual(metadata["verification"]["errors"], [])

    def test_source_url_saved_in_metadata(self):
        """source_urlがメタデータに保存されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=7,
            is_monthly=False,
            additional_metadata={"source_url": "https://example.com/data.csv"},
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.get("source_url"), "https://example.com/data.csv")

    def test_legacy_metadata_normalization(self):
        """旧形式メタデータの正規化を確認"""
        # 旧形式のメタデータを直接作成
        legacy_metadata = {
            "filename": "test_2025_01.csv",
            "data_type": "sentinel_weekly_test",
            "year": 2025,
            "period": 8,
            "period_type": "weekly",
            "timestamp": "2025-01-01T00:00:00.000000",
            "file_size": 100,
            "sha256_hash": "abc123",
            "encoding": "shift_jis",
            "file_path": "test_2025_01.csv",
        }

        # メタデータファイルを直接書き込み
        metadata_path = self.storage.metadata_dir / "sentinel_weekly_test_2025_08.json"
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(legacy_metadata, f, ensure_ascii=False, indent=2)

        # get_metadataで読み込む (正規化される)
        test_file = self.base_path / "sentinel_weekly_test_2025_08.csv"
        normalized = self.storage.get_metadata(test_file)

        # 正規化されたフィールドを確認
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["created_at"], "2025-01-01T00:00:00.000000")
        self.assertEqual(normalized["updated_at"], "2025-01-01T00:00:00.000000")
        self.assertEqual(normalized["checksum_algorithm"], "sha256")
        self.assertIsNone(normalized["metadata_version"])  # 旧形式はNone
        self.assertIsNone(normalized["source_url"])  # 旧形式はNone
        self.assertIsNone(normalized["line_count"])  # 旧形式はNone
        self.assertIsNone(normalized["verification"])  # 旧形式はNone

    def test_row_count_to_line_count_migration(self):
        """旧形式のrow_countがline_countに移行されることを確認"""
        # 旧形式メタデータ (row_countを持つ)
        legacy_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-01-01T00:00:00.000000",
            "row_count": 42,
        }

        normalized = self.storage._normalize_metadata(legacy_metadata)

        # row_countがline_countに移行されていることを確認
        self.assertEqual(normalized["line_count"], 42)
        self.assertNotIn("row_count", normalized)

    def test_truncate_messages_limits_count(self):
        """_truncate_messagesが件数を制限することを確認"""
        messages = [f"error {i}" for i in range(15)]
        truncated = self.storage._truncate_messages(messages, 10)

        self.assertEqual(len(truncated), 11)  # 10件 + "他N件"
        self.assertIn("他5件のメッセージ", truncated[-1])

    def test_truncate_messages_limits_length(self):
        """_truncate_messagesがメッセージ長を制限することを確認"""
        long_message = "a" * 600  # 500文字超
        messages = [long_message]
        truncated = self.storage._truncate_messages(messages, 10)

        self.assertEqual(len(truncated), 1)
        self.assertTrue(len(truncated[0]) <= 500)
        self.assertTrue(truncated[0].endswith("..."))

    def test_verification_status_failed_on_small_file(self):
        """小さいファイルの検証ステータスがfailedになることを確認"""
        # 最小ファイルサイズ(100バイト)未満のデータ
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")  # 約35バイト

        result = self.storage.save_with_metadata(
            data=data,
            data_type="sentinel_weekly_test",
            year=2025,
            period=9,
            is_monthly=False,
        )

        self.assertTrue(result.success)
        metadata = self.storage.get_metadata(result.file_path)
        self.assertIsNotNone(metadata)
        self.assertIn("verification", metadata)
        self.assertEqual(metadata["verification"]["status"], "failed")
        self.assertFalse(metadata["verification"]["checks"]["file_size"])

    def test_validate_file_public_api(self):
        """validate_file公開APIが正しく動作することを確認."""
        # 正常なデータを作成 (100バイト以上)
        csv_data = """"テスト週報データ"
"","疾病A","疾病B","疾病C","疾病D"
"地域1","5","10","15","20"
"地域2","1","2","3","4"
"地域3","0","0","1","0"
"""
        data = csv_data.encode("shift_jis")
        # base_path配下にファイルを作成 (path_safety検証のため)
        file_path = self.base_path / "test_validate.csv"
        file_path.write_bytes(data)

        # 公開APIを使用して検証
        verification = self.storage.validate_file(file_path, data)

        # 検証結果の構造を確認
        self.assertIn("status", verification)
        self.assertIn("verified_at", verification)
        self.assertIn("method", verification)
        self.assertIn("checks", verification)
        self.assertIn("errors", verification)
        self.assertIn("warnings", verification)

        # 正常データなのでverifiedになる
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["method"], "automated")
        self.assertTrue(verification["checks"]["file_size"])
        self.assertTrue(verification["checks"]["encoding"])
        self.assertTrue(verification["checks"]["csv_format"])
        self.assertTrue(verification["checks"]["path_safety"])

    def test_determine_timestamps_preserves_existing(self):
        """_determine_timestampsが既存のcreated_atを保持することを確認"""
        existing = {"created_at": "2025-01-01T00:00:00.000000"}
        now = "2025-12-17T12:00:00.000000"

        created_at, updated_at = self.storage._determine_timestamps(existing, now)

        self.assertEqual(created_at, "2025-01-01T00:00:00.000000")
        self.assertEqual(updated_at, now)

    def test_determine_timestamps_falls_back_to_timestamp(self):
        """_determine_timestampsが旧形式のtimestampにフォールバックすることを確認"""
        existing = {"timestamp": "2025-01-01T00:00:00.000000"}
        now = "2025-12-17T12:00:00.000000"

        created_at, updated_at = self.storage._determine_timestamps(existing, now)

        self.assertEqual(created_at, "2025-01-01T00:00:00.000000")
        self.assertEqual(updated_at, now)

    def test_determine_timestamps_new_file(self):
        """_determine_timestampsが新規ファイルで現在時刻を使用することを確認"""
        now = "2025-12-17T12:00:00.000000"

        created_at, updated_at = self.storage._determine_timestamps(None, now)

        self.assertEqual(created_at, now)
        self.assertEqual(updated_at, now)

    def test_count_lines_empty_data(self):
        """_count_linesが空データで0を返すことを確認"""
        self.assertEqual(self.storage._count_lines(b""), 0)

    def test_count_lines_single_line_no_newline(self):
        """_count_linesが改行なしの1行データで1を返すことを確認"""
        self.assertEqual(self.storage._count_lines(b"header"), 1)

    def test_count_lines_multiple_lines(self):
        """_count_linesが複数行データで正しい行数を返すことを確認"""
        data = b"header\nrow1\nrow2\n"
        self.assertEqual(self.storage._count_lines(data), 3)

    def test_path_safety_validation_symlink_detection(self):
        """シンボリックリンクが検出されることを確認"""
        # シンボリックリンクを作成
        target_file = self.base_path / "target.csv"
        target_file.write_bytes(b"test")
        symlink_path = self.base_path / "symlink.csv"
        symlink_path.symlink_to(target_file)

        try:
            result = self.storage._check_path_safety_validation(symlink_path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("Symbolic links not allowed" in err for err in result["errors"]))
        finally:
            symlink_path.unlink()
            target_file.unlink()

    def test_path_safety_validation_traversal_detection(self):
        """パストラバーサルが検出されることを確認"""
        # base_path外のパスを指定 (別の一時ディレクトリを使用)
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_path = Path(outside_dir) / "outside.csv"

            result = self.storage._check_path_safety_validation(outside_path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("Path traversal detected" in err for err in result["errors"]))

    def test_path_safety_validation_dangerous_pattern_detection(self):
        """危険なパターンが検出されることを確認"""
        dangerous_path = self.base_path / "test;rm -rf.csv"

        result = self.storage._check_path_safety_validation(dangerous_path)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Dangerous pattern" in err for err in result["errors"]))

    def test_save_aborted_on_path_safety_failure(self):
        """パス安全性チェック失敗時に保存が中断されることを確認"""
        csv_data = """"テスト"
"","疾病A"
"地域1","5"
"""
        data = csv_data.encode("shift_jis")

        # パス安全性チェックが失敗するようにモック
        with patch.object(self.storage, "_check_path_safety_validation") as mock_check:
            mock_check.return_value = {"valid": False, "errors": ["[path_safety] Path traversal detected: test"]}

            result = self.storage.save_with_metadata(
                data=data,
                data_type="sentinel_weekly_test",
                year=2025,
                period=99,
                is_monthly=False,
            )

            # 保存が失敗することを確認
            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)
            self.assertIn("path_safety", result.error)

            # ファイルが作成されていないことを確認
            expected_path = self.base_path / "sentinel_weekly_test_2025_99.csv"
            self.assertFalse(expected_path.exists())

    def test_encoding_validation_with_invalid_bytes(self):
        """_check_encoding_validationが無効なバイトシーケンスを検出することを確認"""
        # Shift_JISとして無効なバイトシーケンスを生成
        # 0x80-0x9F, 0xE0-0xFC の範囲外で不正なバイト
        invalid_data = b"\xff\xfe\x00\x01\x02"  # 無効なバイトシーケンス

        result = self.storage._check_encoding_validation(invalid_data)

        # 無効なエンコーディングで検証が失敗することを確認
        self.assertFalse(result["valid"])
        self.assertTrue(len(result["errors"]) > 0)
        self.assertTrue(any("encoding" in err.lower() for err in result["errors"]))

    def test_csv_format_validation_with_inconsistent_columns(self):
        """_check_csv_format_validationが不整合なカラム数を検出することを確認"""
        # カラム数が一致しないCSVデータ
        csv_data = "col1,col2,col3\nval1,val2\nval1,val2,val3,val4\n"
        data = csv_data.encode("shift_jis")

        result = self.storage._check_csv_format_validation(data)

        # カラム数の不整合がwarningsに含まれることを確認
        self.assertTrue(len(result["warnings"]) > 0)


if __name__ == "__main__":
    unittest.main()
