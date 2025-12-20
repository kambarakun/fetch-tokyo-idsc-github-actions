"""migrate_metadata_v1_2_0.py のテストモジュール."""

import json
import tempfile
from pathlib import Path

import pytest

from scripts.migrate_metadata_v1_2_0 import migrate_all, migrate_metadata_file, parse_version


class TestParseVersion:
    """parse_version関数のテスト."""

    def test_parse_version_basic(self) -> None:
        """基本的なバージョン文字列をパースできる."""
        assert parse_version("1.0.0") == (1, 0, 0)
        assert parse_version("1.2.0") == (1, 2, 0)
        assert parse_version("2.0.0") == (2, 0, 0)

    def test_parse_version_two_segments(self) -> None:
        """2セグメントのバージョンをパースできる."""
        assert parse_version("1.0") == (1, 0)
        assert parse_version("2.5") == (2, 5)

    def test_parse_version_comparison(self) -> None:
        """パースされたバージョンを比較できる."""
        # 正しい比較 (タプル比較)
        assert parse_version("1.10.0") >= parse_version("1.2.0")
        assert parse_version("1.9") < parse_version("1.10")
        assert parse_version("2.0.0") > parse_version("1.99.99")

    def test_parse_version_string_comparison_bug(self) -> None:
        """文字列比較のバグが修正されていることを確認."""
        # 文字列比較では "1.10.0" >= "1.2.0" が False (誤)
        # タプル比較では (1, 10, 0) >= (1, 2, 0) が True (正)
        assert parse_version("1.10.0") >= parse_version("1.2.0")
        assert parse_version("1.10.0") >= parse_version("1.9.0")

    def test_parse_version_invalid_format(self) -> None:
        """不正なバージョンフォーマットでValueErrorを発生させる."""
        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("invalid")

        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("1.a.0")

        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("")

    def test_parse_version_none(self) -> None:
        """Noneを渡すとValueErrorを発生させる."""
        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version(None)  # type: ignore

    def test_parse_version_with_spaces(self) -> None:
        """スペースを含むバージョンでValueErrorを発生させる."""
        with pytest.raises(ValueError, match="Invalid version string"):
            parse_version("1 2 0")


class TestMigrateMetadataFile:
    """migrate_metadata_file関数のテスト."""

    def test_migrate_v1_1_0_to_v1_2_0(self) -> None:
        """v1.1.0からv1.2.0への基本変換."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # v1.1.0形式のメタデータを作成
            v1_1_metadata = {
                "metadata_version": "1.1.0",
                "name": "test",
                "filename": "test.csv",
                "path": "raw/test.csv",
                "profile": "tokyo-idsc-raw",
                "data_type": "sentinel_weekly_gender",
                "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
                "bytes": 1000,
                "lines": 50,
                "hash": {"algorithm": "sha256", "value": "hash123"},
                "encoding": "shift_jis",
                "created": "2025-01-01T00:00:00+00:00",
                "modified": "2025-01-01T00:00:00+00:00",
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(v1_1_metadata, f)

            # マイグレーション実行
            result = migrate_metadata_file(metadata_path, dry_run=False, backup=False)

            assert result is True

            # ファイルが更新されていることを確認
            with metadata_path.open("r", encoding="utf-8") as f:
                migrated = json.load(f)

            assert migrated["metadata_version"] == "1.2.0"
            assert "quality" in migrated
            assert migrated["quality"]["validation_status"] == "skipped"
            assert migrated["quality"]["issues"] == []
            assert "validation_timestamp" in migrated["quality"]

    def test_migrate_skip_already_v1_2_0(self) -> None:
        """既にv1.2.0のファイルはスキップされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # v1.2.0形式のメタデータを作成
            v1_2_metadata = {
                "metadata_version": "1.2.0",
                "name": "test",
                "filename": "test.csv",
                "path": "raw/test.csv",
                "profile": "tokyo-idsc-raw",
                "data_type": "sentinel_weekly_gender",
                "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
                "bytes": 1000,
                "lines": 50,
                "hash": {"algorithm": "sha256", "value": "hash123"},
                "encoding": "shift_jis",
                "created": "2025-01-01T00:00:00+00:00",
                "modified": "2025-01-01T00:00:00+00:00",
                "quality": {
                    "validation_timestamp": "2025-01-01T00:00:00+00:00",
                    "validation_status": "skipped",
                    "issues": [],
                },
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(v1_2_metadata, f)

            original_timestamp: str = v1_2_metadata["quality"]["validation_timestamp"]  # type: ignore[index]

            # マイグレーション実行
            result = migrate_metadata_file(metadata_path, dry_run=False, backup=False)

            assert result is True

            # ファイルが変更されていないことを確認
            with metadata_path.open("r", encoding="utf-8") as f:
                unchanged = json.load(f)

            assert unchanged["metadata_version"] == "1.2.0"
            assert unchanged["quality"]["validation_timestamp"] == original_timestamp

    def test_migrate_skip_higher_version(self) -> None:
        """v1.2.0より高いバージョンはスキップされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # v1.10.0形式のメタデータを作成
            v1_10_metadata = {
                "metadata_version": "1.10.0",
                "name": "test",
                "filename": "test.csv",
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(v1_10_metadata, f)

            # マイグレーション実行
            result = migrate_metadata_file(metadata_path, dry_run=False, backup=False)

            assert result is True

            # ファイルが変更されていないことを確認
            with metadata_path.open("r", encoding="utf-8") as f:
                unchanged = json.load(f)

            assert unchanged["metadata_version"] == "1.10.0"

    def test_migrate_skip_invalid_version(self) -> None:
        """不正なバージョンのファイルはスキップされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # 不正なバージョンのメタデータを作成
            invalid_metadata = {
                "metadata_version": "invalid",
                "name": "test",
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(invalid_metadata, f)

            # マイグレーション実行
            result = migrate_metadata_file(metadata_path, dry_run=False, backup=False)

            assert result is False

    def test_migrate_dry_run(self) -> None:
        """ドライランではファイルが変更されない."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # v1.1.0形式のメタデータを作成
            v1_1_metadata = {
                "metadata_version": "1.1.0",
                "name": "test",
                "filename": "test.csv",
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(v1_1_metadata, f)

            # ドライラン実行
            result = migrate_metadata_file(metadata_path, dry_run=True, backup=False)

            assert result is True

            # ファイルが変更されていないことを確認
            with metadata_path.open("r", encoding="utf-8") as f:
                unchanged = json.load(f)

            assert unchanged["metadata_version"] == "1.1.0"
            assert "quality" not in unchanged

    def test_migrate_with_backup(self) -> None:
        """バックアップが作成される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir)

            # v1.1.0形式のメタデータを作成
            v1_1_metadata = {
                "metadata_version": "1.1.0",
                "name": "test",
                "filename": "test.csv",
            }

            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(v1_1_metadata, f)

            # マイグレーション実行 (バックアップ有効)
            result = migrate_metadata_file(metadata_path, dry_run=False, backup=True)

            assert result is True

            # バックアップファイルが作成されていることを確認
            backup_path = metadata_dir / "test.json.v1.1.0.bak"
            assert backup_path.exists()

            # バックアップの内容を確認
            with backup_path.open("r", encoding="utf-8") as f:
                backup = json.load(f)

            assert backup["metadata_version"] == "1.1.0"
            assert "quality" not in backup

    def test_migrate_nonexistent_file(self) -> None:
        """存在しないファイルはスキップされる."""
        nonexistent_path = Path("/nonexistent/test.json")
        result = migrate_metadata_file(nonexistent_path, dry_run=False, backup=False)
        assert result is False


class TestMigrateAll:
    """migrate_all関数のテスト."""

    def test_migrate_all_basic(self) -> None:
        """複数ファイルのマイグレーションが実行される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            metadata_dir.mkdir()

            # 複数のv1.1.0形式のメタデータを作成
            for i in range(3):
                v1_1_metadata = {
                    "metadata_version": "1.1.0",
                    "name": f"test{i}",
                    "filename": f"test{i}.csv",
                }
                metadata_path = metadata_dir / f"test{i}.json"
                with metadata_path.open("w", encoding="utf-8") as f:
                    json.dump(v1_1_metadata, f)

            # マイグレーション実行
            stats = migrate_all(metadata_dir, dry_run=False, backup=False)

            assert stats["total"] == 3
            assert stats["migrated"] == 3
            assert stats["skipped"] == 0
            assert stats["failed"] == 0

    def test_migrate_all_mixed_versions(self) -> None:
        """異なるバージョンが混在する場合の動作."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            metadata_dir.mkdir()

            # v1.1.0形式のメタデータ
            v1_1_metadata = {
                "metadata_version": "1.1.0",
                "name": "test1",
                "filename": "test1.csv",
            }
            with (metadata_dir / "test1.json").open("w", encoding="utf-8") as f:
                json.dump(v1_1_metadata, f)

            # v1.2.0形式のメタデータ
            v1_2_metadata = {
                "metadata_version": "1.2.0",
                "name": "test2",
                "filename": "test2.csv",
                "quality": {
                    "validation_timestamp": "2025-01-01T00:00:00+00:00",
                    "validation_status": "skipped",
                    "issues": [],
                },
            }
            with (metadata_dir / "test2.json").open("w", encoding="utf-8") as f:
                json.dump(v1_2_metadata, f)

            # マイグレーション実行
            stats = migrate_all(metadata_dir, dry_run=False, backup=False)

            assert stats["total"] == 2
            assert stats["migrated"] == 1  # v1.1.0のみマイグレーション
            assert stats["skipped"] == 1  # v1.2.0はスキップ
            assert stats["failed"] == 0

    def test_migrate_all_nonexistent_directory(self) -> None:
        """存在しないディレクトリの場合."""
        nonexistent_dir = Path("/nonexistent/metadata")
        stats = migrate_all(nonexistent_dir, dry_run=False, backup=False)

        assert stats["total"] == 0
        assert stats["migrated"] == 0
        assert stats["skipped"] == 0
        assert stats["failed"] == 0
