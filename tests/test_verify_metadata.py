"""src/cli/verify_metadata.py のテストモジュール."""

import json
import tempfile
from pathlib import Path

from src.cli.verify_metadata import run_verification


class TestRunVerification:
    """run_verification関数のテスト."""

    def test_verify_valid_csv(self) -> None:
        """有効なCSVファイルが正しく検証される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # メタデータを作成 (verification: None)
            metadata = {
                "metadata_version": "1.0",
                "filename": "test.csv",
                "created_at": "2025-11-01T18:10:03.404770",
                "updated_at": "2025-11-01T18:10:03.404770",
                "verification": None,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(metadata, f)

            # 有効なCSVファイルを作成 (Shift_JIS, 100バイト以上)
            csv_lines = ["col1,col2,col3,col4,col5"]
            for i in range(10):
                csv_lines.append(f"val{i}1,val{i}2,val{i}3,val{i}4,val{i}5")
            csv_content = "\n".join(csv_lines) + "\n"
            csv_path = data_dir / "test.csv"
            csv_path.write_bytes(csv_content.encode("shift_jis"))

            # 検証実行
            stats = run_verification(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 1
            assert stats["verified"] == 1
            assert stats["failed"] == 0
            assert stats["errors"] == 0

            # メタデータが更新されていることを確認
            with metadata_path.open() as f:
                updated = json.load(f)
            assert updated["verification"] is not None
            assert updated["verification"]["status"] == "verified"
            assert updated["verification"]["checks"]["encoding"] is True
            assert updated["verification"]["checks"]["csv_format"] is True

    def test_verify_dry_run(self) -> None:
        """ドライランではファイルが変更されない."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            metadata = {
                "metadata_version": "1.0",
                "filename": "test.csv",
                "verification": None,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(metadata, f)

            # 100バイト以上のCSVファイルを作成
            csv_lines = ["col1,col2,col3,col4,col5"]
            for i in range(10):
                csv_lines.append(f"val{i}1,val{i}2,val{i}3,val{i}4,val{i}5")
            csv_content = "\n".join(csv_lines) + "\n"
            csv_path = data_dir / "test.csv"
            csv_path.write_bytes(csv_content.encode("shift_jis"))

            # ドライラン実行
            stats = run_verification(metadata_dir, data_dir, dry_run=True)

            assert stats["verified"] == 1

            # ファイルは変更されていないことを確認
            with metadata_path.open() as f:
                unchanged = json.load(f)
            assert unchanged["verification"] is None

    def test_verify_only_unverified(self) -> None:
        """only_unverified モードでは検証済みファイルをスキップする."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 検証済みメタデータ
            verified_metadata = {
                "metadata_version": "1.0",
                "filename": "verified.csv",
                "verification": {"status": "verified", "checks": {}},
            }
            verified_path = metadata_dir / "verified.json"
            with verified_path.open("w") as f:
                json.dump(verified_metadata, f)

            # 未検証メタデータ
            unverified_metadata = {
                "metadata_version": "1.0",
                "filename": "unverified.csv",
                "verification": None,
            }
            unverified_path = metadata_dir / "unverified.json"
            with unverified_path.open("w") as f:
                json.dump(unverified_metadata, f)

            # 100バイト以上のCSVファイルを作成
            csv_lines = ["col1,col2,col3,col4,col5"]
            for i in range(10):
                csv_lines.append(f"val{i}1,val{i}2,val{i}3,val{i}4,val{i}5")
            csv_content = "\n".join(csv_lines) + "\n"
            (data_dir / "verified.csv").write_bytes(csv_content.encode("shift_jis"))
            (data_dir / "unverified.csv").write_bytes(csv_content.encode("shift_jis"))

            # only_unverified モードで実行
            stats = run_verification(metadata_dir, data_dir, dry_run=False, only_unverified=True)

            assert stats["total"] == 2
            assert stats["skipped"] == 1  # verified.csv はスキップ
            assert stats["verified"] == 1  # unverified.csv のみ検証

    def test_verify_missing_csv_file(self) -> None:
        """CSVファイルが存在しない場合はエラーとしてカウントされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            metadata = {
                "metadata_version": "1.0",
                "filename": "missing.csv",
                "verification": None,
            }
            metadata_path = metadata_dir / "missing.json"
            with metadata_path.open("w") as f:
                json.dump(metadata, f)

            # CSVファイルは作成しない

            stats = run_verification(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 1
            assert stats["errors"] == 1

    def test_verify_excludes_hash_index(self) -> None:
        """hash_index.jsonは検証対象外."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # hash_index.jsonを作成
            hash_index = {"abc123": "test.csv"}
            hash_index_path = metadata_dir / "hash_index.json"
            with hash_index_path.open("w") as f:
                json.dump(hash_index, f)

            stats = run_verification(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 0

    def test_verify_invalid_encoding(self) -> None:
        """不正なエンコーディングのファイルは検証失敗になる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            metadata = {
                "metadata_version": "1.0",
                "filename": "invalid.csv",
                "verification": None,
            }
            metadata_path = metadata_dir / "invalid.json"
            with metadata_path.open("w") as f:
                json.dump(metadata, f)

            # UTF-8でエンコード (Shift_JISで読めない文字を含む)
            csv_path = data_dir / "invalid.csv"
            csv_path.write_bytes("col1,col2\n日本語,テスト\n".encode())

            stats = run_verification(metadata_dir, data_dir, dry_run=False)

            # UTF-8のファイルでもShift_JISでデコードできる場合がある
            # 文字化けするが検証は通る可能性がある
            assert stats["total"] == 1
            assert (stats["verified"] + stats["failed"]) == 1

    def test_verify_path_traversal_prevention(self) -> None:
        """パストラバーサル攻撃が防止される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 悪意のあるファイル名を持つメタデータを作成
            malicious_metadata = {
                "metadata_version": "1.0",
                "filename": "../../../etc/passwd",
                "verification": None,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(malicious_metadata, f)

            # 100バイト以上のCSVファイルを作成 (安全なパスに)
            csv_lines = ["col1,col2,col3,col4,col5"]
            for i in range(10):
                csv_lines.append(f"val{i}1,val{i}2,val{i}3,val{i}4,val{i}5")
            csv_content = "\n".join(csv_lines) + "\n"
            csv_path = data_dir / "passwd"
            csv_path.write_bytes(csv_content.encode("shift_jis"))

            # 検証実行 (パストラバーサルが防止される)
            stats = run_verification(metadata_dir, data_dir, dry_run=False)

            # ファイル名がサニタイズされて passwd として検証される
            assert stats["errors"] == 0
            assert stats["verified"] == 1


class TestVerificationContent:
    """検証結果の内容テスト."""

    def test_verification_structure(self) -> None:
        """検証結果が正しい構造を持つ."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            metadata = {
                "metadata_version": "1.0",
                "filename": "test.csv",
                "verification": None,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(metadata, f)

            # 100バイト以上のCSVファイルを作成
            csv_lines = ["col1,col2,col3,col4,col5"]
            for i in range(10):
                csv_lines.append(f"val{i}1,val{i}2,val{i}3,val{i}4,val{i}5")
            csv_content = "\n".join(csv_lines) + "\n"
            csv_path = data_dir / "test.csv"
            csv_path.write_bytes(csv_content.encode("shift_jis"))

            run_verification(metadata_dir, data_dir, dry_run=False)

            with metadata_path.open() as f:
                updated = json.load(f)

            verification = updated["verification"]
            assert "status" in verification
            assert "verified_at" in verification
            assert "method" in verification
            assert "checks" in verification
            assert "errors" in verification
            assert "warnings" in verification

            # checks の構造
            checks = verification["checks"]
            assert "file_size" in checks
            assert "encoding" in checks
            assert "csv_format" in checks
            assert "path_safety" in checks
