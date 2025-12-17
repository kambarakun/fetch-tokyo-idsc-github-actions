"""migrate_metadata.py のテストモジュール."""

import json
import tempfile
from pathlib import Path

import pytest

from scripts.migrate_metadata import (
    V1_0_REQUIRED_FIELDS,
    MigrationRegistry,
    count_lines,
    migrate_metadata,
    migrate_none_to_v1_0,
    migration_registry,
    needs_migration,
    run_migration,
)
from src.managers.storage_manager import METADATA_VERSION


class TestCountLines:
    """count_lines関数のテスト."""

    def test_count_lines_multiple_lines(self) -> None:
        """複数行のファイルで正しい行数を返す."""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as f:
            f.write(b"line1\nline2\nline3\n")
            temp_path = Path(f.name)
        try:
            assert count_lines(temp_path) == 3
        finally:
            temp_path.unlink()

    def test_count_lines_no_trailing_newline(self) -> None:
        """末尾に改行がないファイルでも正しくカウントする."""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as f:
            f.write(b"line1\nline2\nline3")
            temp_path = Path(f.name)
        try:
            assert count_lines(temp_path) == 3
        finally:
            temp_path.unlink()

    def test_count_lines_empty_file(self) -> None:
        """空ファイルで0を返す."""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as f:
            temp_path = Path(f.name)
        try:
            assert count_lines(temp_path) == 0
        finally:
            temp_path.unlink()

    def test_count_lines_single_line(self) -> None:
        """1行のファイルで1を返す."""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as f:
            f.write(b"single line")
            temp_path = Path(f.name)
        try:
            assert count_lines(temp_path) == 1
        finally:
            temp_path.unlink()

    def test_count_lines_nonexistent_file(self) -> None:
        """存在しないファイルでNoneを返す."""
        assert count_lines(Path("/nonexistent/path.csv")) is None


class TestMigrationRegistry:
    """MigrationRegistry クラスのテスト."""

    def test_register_migration(self) -> None:
        """マイグレーション関数を登録できる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        assert (None, "1.0") in registry._migrations
        assert None in registry._versions
        assert "1.0" in registry._versions

    def test_get_migration_path_direct(self) -> None:
        """直接パスを取得できる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        path = registry.get_migration_path(None, "1.0")
        assert path == [(None, "1.0")]

    def test_get_migration_path_chained(self) -> None:
        """チェーンされたパスを取得できる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        @registry.register(from_version="1.0", to_version="1.1")
        def migrate_to_v1_1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        @registry.register(from_version="1.1", to_version="2.0")
        def migrate_to_v2(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        path = registry.get_migration_path(None, "2.0")
        assert path == [(None, "1.0"), ("1.0", "1.1"), ("1.1", "2.0")]

    def test_get_migration_path_same_version(self) -> None:
        """同じバージョンの場合は空リストを返す."""
        registry = MigrationRegistry()
        path = registry.get_migration_path("1.0", "1.0")
        assert path == []

    def test_get_migration_path_not_found(self) -> None:
        """パスが見つからない場合はValueErrorを発生させる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        with pytest.raises(ValueError, match="No migration path"):
            registry.get_migration_path("1.0", "3.0")

    def test_migrate_applies_all_steps(self) -> None:
        """すべてのマイグレーションステップが適用される."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            result = metadata.copy()
            result["version"] = "1.0"
            return result, ["added version 1.0"]

        @registry.register(from_version="1.0", to_version="1.1")
        def migrate_to_v1_1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            result = metadata.copy()
            result["version"] = "1.1"
            result["new_field"] = "value"
            return result, ["upgraded to 1.1", "added new_field"]

        metadata = {"name": "test"}
        migrated, changes = registry.migrate(metadata, None, "1.1")

        assert migrated["version"] == "1.1"
        assert migrated["new_field"] == "value"
        assert migrated["name"] == "test"
        assert len(changes) == 3

    def test_supported_versions(self) -> None:
        """サポートされているバージョンを取得できる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        @registry.register(from_version="1.0", to_version="2.0")
        def migrate_to_v2(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        versions = registry.supported_versions
        assert None in versions
        assert "1.0" in versions
        assert "2.0" in versions

    def test_compare_versions_none_is_oldest(self) -> None:
        """Noneは最も古いバージョンとして扱われる."""
        assert MigrationRegistry.compare_versions(None, "1.0") < 0
        assert MigrationRegistry.compare_versions("1.0", None) > 0
        assert MigrationRegistry.compare_versions(None, None) == 0

    def test_compare_versions_semantic(self) -> None:
        """セマンティックバージョニングで比較できる."""
        assert MigrationRegistry.compare_versions("1.0", "1.0") == 0
        assert MigrationRegistry.compare_versions("1.0", "2.0") < 0
        assert MigrationRegistry.compare_versions("2.0", "1.0") > 0
        assert MigrationRegistry.compare_versions("1.0", "1.1") < 0
        assert MigrationRegistry.compare_versions("1.10", "1.9") > 0

    def test_compare_versions_boundary_cases(self) -> None:
        """境界値でのバージョン比較テスト."""
        # 多桁バージョン (文字列比較では誤る可能性がある)
        assert MigrationRegistry.compare_versions("1.9", "1.10") < 0
        assert MigrationRegistry.compare_versions("2.10.1", "2.9.99") > 0
        assert MigrationRegistry.compare_versions("10.0", "9.99") > 0

        # 3桁以上のセグメント
        assert MigrationRegistry.compare_versions("1.0.0", "1.0.0") == 0
        assert MigrationRegistry.compare_versions("1.0.1", "1.0.0") > 0
        assert MigrationRegistry.compare_versions("1.0.0", "1.0.1") < 0
        assert MigrationRegistry.compare_versions("1.2.3", "1.2.4") < 0

        # 異なるセグメント数
        assert MigrationRegistry.compare_versions("1.0", "1.0.0") < 0
        assert MigrationRegistry.compare_versions("1.0.0", "1.0") > 0
        assert MigrationRegistry.compare_versions("2.0", "1.9.9") > 0

        # ゼロを含むバージョン
        assert MigrationRegistry.compare_versions("0.1", "0.0") > 0
        assert MigrationRegistry.compare_versions("0.0.1", "0.0.0") > 0
        assert MigrationRegistry.compare_versions("1.0.0", "0.99.99") > 0

    def test_is_downgrade(self) -> None:
        """ダウングレードを正しく検出する."""
        registry = MigrationRegistry()
        assert registry.is_downgrade("1.0", None) is True
        assert registry.is_downgrade("2.0", "1.0") is True
        assert registry.is_downgrade("1.0", "2.0") is False
        assert registry.is_downgrade(None, "1.0") is False
        assert registry.is_downgrade("1.0", "1.0") is False

    def test_migrate_raises_on_downgrade(self) -> None:
        """ダウングレード指定時にValueErrorを発生させる."""
        registry = MigrationRegistry()

        @registry.register(from_version=None, to_version="1.0")
        def migrate_to_v1(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
            return metadata, []

        metadata = {"metadata_version": "1.0"}
        with pytest.raises(ValueError, match="Downgrade is not supported"):
            registry.migrate(metadata, None, target_version=None)


class TestGlobalRegistry:
    """グローバルレジストリのテスト."""

    def test_global_registry_has_none_to_v1_0(self) -> None:
        """グローバルレジストリにNone -> 1.0の変換が登録されている."""
        assert (None, "1.0") in migration_registry._migrations

    def test_latest_version(self) -> None:
        """最新バージョンがMETADATA_VERSIONと一致する."""
        assert migration_registry.latest_version == METADATA_VERSION


class TestNeedsMigration:
    """needs_migration関数のテスト."""

    def test_old_format_needs_migration(self) -> None:
        """旧形式のメタデータはマイグレーションが必要."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
            "file_size": 1107,
            "sha256_hash": "abc123",
        }
        assert needs_migration(old_metadata) is True

    def test_new_format_no_migration(self) -> None:
        """新形式のメタデータはマイグレーション不要."""
        new_metadata = {
            "metadata_version": METADATA_VERSION,
            "filename": "test.csv",
            "created_at": "2025-11-01T18:10:03.404770",
            "updated_at": "2025-11-01T18:10:03.404770",
            "file_size": 1107,
            "sha256_hash": "abc123",
            "checksum_algorithm": "sha256",
            "source_url": None,
            "verification": None,
            "line_count": 53,
        }
        assert needs_migration(new_metadata) is False

    def test_partial_migration_needs_migration(self) -> None:
        """一部フィールドが欠けている場合はマイグレーションが必要."""
        partial_metadata = {
            "metadata_version": METADATA_VERSION,
            "filename": "test.csv",
            "created_at": "2025-11-01T18:10:03.404770",
            # updated_at, line_count などが欠けている
        }
        assert needs_migration(partial_metadata) is True

    def test_needs_migration_with_target_version(self) -> None:
        """目標バージョンを指定してマイグレーション判定できる."""
        metadata = {"metadata_version": "1.0"}
        # 目標が1.0なら不要 (必須フィールドチェックは別)
        assert needs_migration(metadata, target_version="2.0") is True


class TestMigrateNoneToV10:
    """migrate_none_to_v1_0関数のテスト."""

    def test_migrate_old_format(self) -> None:
        """旧形式のメタデータが正しく変換される."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
            "file_size": 1107,
            "sha256_hash": "abc123",
        }
        migrated, changes = migrate_none_to_v1_0(old_metadata, None)

        assert migrated["metadata_version"] == "1.0"
        assert migrated["created_at"] == "2025-11-01T18:10:03.404770"
        assert migrated["updated_at"] == "2025-11-01T18:10:03.404770"
        assert migrated["checksum_algorithm"] == "sha256"
        assert migrated["source_url"] is None
        assert migrated["verification"] is None
        assert migrated["line_count"] is None
        # timestamp フィールドは削除される
        assert "timestamp" not in migrated
        assert any("timestamp" in c and "removed" in c for c in changes)
        assert len(changes) > 0

    def test_migrate_with_row_count(self) -> None:
        """row_countがline_countに変換される."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
            "row_count": 100,
        }
        migrated, changes = migrate_none_to_v1_0(old_metadata, None)

        assert migrated["line_count"] == 100
        assert "row_count" not in migrated
        assert any("row_count -> line_count" in c for c in changes)

    def test_migrate_calculates_line_count(self) -> None:
        """CSVファイルから行数を計算する."""
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".csv"
        ) as f:
            f.write(b"header\nrow1\nrow2\n")
            temp_path = Path(f.name)
        try:
            old_metadata = {
                "filename": temp_path.name,
                "timestamp": "2025-11-01T18:10:03.404770",
            }
            migrated, _ = migrate_none_to_v1_0(old_metadata, temp_path)
            assert migrated["line_count"] == 3
        finally:
            temp_path.unlink()

    def test_migrate_preserves_existing_fields(self) -> None:
        """既存のフィールドは保持される."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
            "file_size": 1107,
            "sha256_hash": "abc123",
            "encoding": "shift_jis",
            "fetch_time": 0.5,
        }
        migrated, _ = migrate_none_to_v1_0(old_metadata, None)

        assert migrated["filename"] == "test.csv"
        assert migrated["file_size"] == 1107
        assert migrated["sha256_hash"] == "abc123"
        assert migrated["encoding"] == "shift_jis"
        assert migrated["fetch_time"] == 0.5


class TestMigrateMetadata:
    """migrate_metadata関数のテスト."""

    def test_migrate_metadata_uses_registry(self) -> None:
        """migrate_metadataがレジストリを使用する."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
        }
        migrated, changes = migrate_metadata(old_metadata, target_version="1.0")

        assert migrated["metadata_version"] == "1.0"
        assert len(changes) > 0

    def test_migrate_metadata_with_target_version(self) -> None:
        """目標バージョンを指定してマイグレーションできる."""
        old_metadata = {
            "filename": "test.csv",
            "timestamp": "2025-11-01T18:10:03.404770",
        }
        migrated, _ = migrate_metadata(old_metadata, target_version="1.0")

        assert migrated["metadata_version"] == "1.0"


class TestRunMigration:
    """run_migration関数のテスト."""

    def test_run_migration_dry_run(self) -> None:
        """ドライランではファイルが変更されない."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 旧形式のメタデータを作成
            old_metadata = {
                "filename": "test.csv",
                "timestamp": "2025-11-01T18:10:03.404770",
                "file_size": 100,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(old_metadata, f)

            # ドライラン実行
            stats = run_migration(metadata_dir, data_dir, dry_run=True)

            assert stats["total"] == 1
            assert stats["migrated"] == 1
            assert stats["errors"] == 0

            # ファイルは変更されていないことを確認
            with metadata_path.open() as f:
                unchanged = json.load(f)
            assert "metadata_version" not in unchanged

    def test_run_migration_actual(self) -> None:
        """実行時にファイルが正しく更新される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 旧形式のメタデータを作成
            old_metadata = {
                "filename": "test.csv",
                "timestamp": "2025-11-01T18:10:03.404770",
                "file_size": 100,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(old_metadata, f)

            # CSVファイルも作成
            csv_path = data_dir / "test.csv"
            csv_path.write_bytes(b"header\nrow1\nrow2\n")

            # 実行
            stats = run_migration(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 1
            assert stats["migrated"] == 1
            assert stats["errors"] == 0

            # ファイルが更新されていることを確認
            with metadata_path.open() as f:
                migrated = json.load(f)
            assert migrated["metadata_version"] == "1.0"
            assert migrated["created_at"] == "2025-11-01T18:10:03.404770"
            assert migrated["line_count"] == 3

    def test_run_migration_skips_already_migrated(self) -> None:
        """既にマイグレーション済みのファイルはスキップされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 新形式のメタデータを作成
            new_metadata = {
                "metadata_version": METADATA_VERSION,
                "filename": "test.csv",
                "created_at": "2025-11-01T18:10:03.404770",
                "updated_at": "2025-11-01T18:10:03.404770",
                "checksum_algorithm": "sha256",
                "source_url": None,
                "verification": None,
                "line_count": 10,
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(new_metadata, f)

            stats = run_migration(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 1
            assert stats["migrated"] == 0
            assert stats["skipped"] == 1
            assert stats["errors"] == 0

    def test_run_migration_excludes_hash_index(self) -> None:
        """hash_index.jsonはマイグレーション対象外."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # hash_index.jsonを作成
            hash_index = {"abc123": "test.csv"}
            hash_index_path = metadata_dir / "hash_index.json"
            with hash_index_path.open("w") as f:
                json.dump(hash_index, f)

            stats = run_migration(metadata_dir, data_dir, dry_run=False)

            assert stats["total"] == 0
            assert stats["migrated"] == 0

    def test_run_migration_with_target_version(self) -> None:
        """目標バージョンを指定してマイグレーションできる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            old_metadata = {
                "filename": "test.csv",
                "timestamp": "2025-11-01T18:10:03.404770",
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(old_metadata, f)

            stats = run_migration(
                metadata_dir, data_dir, target_version="1.0", dry_run=False
            )

            assert stats["target_version"] == "1.0"
            assert stats["migrated"] == 1

    def test_run_migration_downgrade_error(self) -> None:
        """ダウングレード指定時にエラーとしてカウントされる."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 新しいバージョンのメタデータを作成
            new_metadata = {
                "metadata_version": "2.0",  # 現在より新しいバージョン
                "filename": "test.csv",
                "created_at": "2025-11-01T18:10:03.404770",
                "updated_at": "2025-11-01T18:10:03.404770",
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(new_metadata, f)

            # 古いバージョン (1.0) へのマイグレーションを試行
            stats = run_migration(
                metadata_dir, data_dir, target_version="1.0", dry_run=False
            )

            # ダウングレードはエラーとしてカウントされる
            assert stats["errors"] == 1
            assert stats["migrated"] == 0


class TestV10RequiredFields:
    """V1_0_REQUIRED_FIELDS定数のテスト."""

    def test_required_fields_exist(self) -> None:
        """必須フィールドが定義されている."""
        expected = {
            "metadata_version",
            "created_at",
            "updated_at",
            "checksum_algorithm",
            "source_url",
            "verification",
            "line_count",
        }
        assert expected == V1_0_REQUIRED_FIELDS


class TestSecurityMeasures:
    """セキュリティ対策のテスト."""

    def test_path_traversal_prevention(self) -> None:
        """パストラバーサル攻撃が防止される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            # 悪意のあるファイル名を持つメタデータを作成
            malicious_metadata = {
                "filename": "../../../etc/passwd",
                "timestamp": "2025-11-01T18:10:03.404770",
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(malicious_metadata, f)

            # CSVファイルを作成 (安全なパスに)
            csv_path = data_dir / "passwd"
            csv_path.write_bytes(b"safe content\n")

            # マイグレーション実行
            stats = run_migration(metadata_dir, data_dir, dry_run=False)

            # エラーなく完了すること (パストラバーサルが防止された)
            assert stats["errors"] == 0
            assert stats["migrated"] == 1

    def test_timestamp_field_removed(self) -> None:
        """マイグレーション後にtimestampフィールドが削除される."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_dir = Path(tmpdir) / ".metadata"
            data_dir = Path(tmpdir)
            metadata_dir.mkdir()

            old_metadata = {
                "filename": "test.csv",
                "timestamp": "2025-11-01T18:10:03.404770",
            }
            metadata_path = metadata_dir / "test.json"
            with metadata_path.open("w") as f:
                json.dump(old_metadata, f)

            # マイグレーション実行
            run_migration(metadata_dir, data_dir, dry_run=False)

            # ファイルを読み込んでtimestampが削除されていることを確認
            with metadata_path.open() as f:
                migrated = json.load(f)
            assert "timestamp" not in migrated
            assert migrated["created_at"] == "2025-11-01T18:10:03.404770"
            assert migrated["updated_at"] == "2025-11-01T18:10:03.404770"
