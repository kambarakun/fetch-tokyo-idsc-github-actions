"""
メタデータモデルのユニットテスト
"""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.metadata import (
    METADATA_VERSION,
    PROFILE_PROCESSED,
    PROFILE_RAW,
    FetchInfo,
    HashInfo,
    Metadata,
    ProcessInfo,
    TemporalInfo,
    Verification,
    _now_iso,
)


class TestTemporalInfo(unittest.TestCase):
    """TemporalInfoのテスト"""

    def test_to_dict(self):
        """辞書変換のテスト"""
        # Arrange
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")

        # Act
        result = temporal.to_dict()

        # Assert
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["period"], 1)
        self.assertEqual(result["period_type"], "weekly")

    def test_from_dict(self):
        """辞書からの作成テスト"""
        # Arrange
        data = {"year": 2025, "period": 12, "period_type": "monthly"}

        # Act
        temporal = TemporalInfo.from_dict(data)

        # Assert
        self.assertEqual(temporal.year, 2025)
        self.assertEqual(temporal.period, 12)
        self.assertEqual(temporal.period_type, "monthly")


class TestHashInfo(unittest.TestCase):
    """HashInfoのテスト"""

    def test_to_dict(self):
        """辞書変換のテスト"""
        # Arrange
        hash_info = HashInfo(algorithm="sha256", value="abc123")

        # Act
        result = hash_info.to_dict()

        # Assert
        self.assertEqual(result["algorithm"], "sha256")
        self.assertEqual(result["value"], "abc123")

    def test_from_dict(self):
        """辞書からの作成テスト"""
        # Arrange
        data = {"algorithm": "sha256", "value": "def456"}

        # Act
        hash_info = HashInfo.from_dict(data)

        # Assert
        self.assertEqual(hash_info.algorithm, "sha256")
        self.assertEqual(hash_info.value, "def456")


class TestVerification(unittest.TestCase):
    """Verificationのテスト"""

    def test_to_dict_full(self):
        """全フィールドありの辞書変換テスト"""
        # Arrange
        verification = Verification(
            status="verified",
            verified_at="2025-01-01T00:00:00Z",
            method="automated",
            checks={"file_size": True, "encoding": True},
            errors=["error1"],
            warnings=["warning1"],
            details={"column_counts": [10, 20]},
        )

        # Act
        result = verification.to_dict()

        # Assert
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["verified_at"], "2025-01-01T00:00:00Z")
        self.assertEqual(result["method"], "automated")
        self.assertEqual(result["checks"], {"file_size": True, "encoding": True})
        self.assertEqual(result["errors"], ["error1"])
        self.assertEqual(result["warnings"], ["warning1"])
        self.assertEqual(result["details"], {"column_counts": [10, 20]})

    def test_from_dict_minimal(self):
        """最小フィールドからの作成テスト"""
        # Arrange
        data = {"status": "pending"}

        # Act
        verification = Verification.from_dict(data)

        # Assert
        self.assertEqual(verification.status, "pending")
        self.assertIsNone(verification.verified_at)
        self.assertEqual(verification.method, "automated")
        self.assertEqual(verification.checks, {})
        self.assertEqual(verification.errors, [])
        self.assertEqual(verification.warnings, [])
        self.assertEqual(verification.details, {})

    def test_from_dict_with_details(self):
        """v1.3.0のdetailsフィールドを含む作成テスト"""
        # Arrange
        data = {
            "status": "failed",
            "verified_at": "2025-01-01T00:00:00Z",
            "method": "manual",
            "checks": {"csv_format": False},
            "errors": [],
            "warnings": ["[csv_format] Inconsistent column count"],
            "details": {"column_counts": [0, 1, 2, 10]},
        }

        # Act
        verification = Verification.from_dict(data)

        # Assert
        self.assertEqual(verification.status, "failed")
        self.assertEqual(verification.details, {"column_counts": [0, 1, 2, 10]})


class TestFetchInfo(unittest.TestCase):
    """FetchInfoのテスト"""

    def test_to_dict(self):
        """辞書変換のテスト"""
        # Arrange
        fetch_info = FetchInfo(
            source_url="https://example.com",
            fetch_time_seconds=1.5,
            force_overwrite=True,
            save_all_zero=False,
        )

        # Act
        result = fetch_info.to_dict()

        # Assert
        self.assertEqual(result["source_url"], "https://example.com")
        self.assertEqual(result["fetch_time_seconds"], 1.5)
        self.assertEqual(result["force_overwrite"], True)
        self.assertEqual(result["save_all_zero"], False)

    def test_from_dict_minimal(self):
        """最小フィールドからの作成テスト"""
        # Arrange
        data = {}

        # Act
        fetch_info = FetchInfo.from_dict(data)

        # Assert
        self.assertIsNone(fetch_info.source_url)
        self.assertEqual(fetch_info.fetch_time_seconds, 0.0)
        self.assertEqual(fetch_info.force_overwrite, False)
        self.assertEqual(fetch_info.save_all_zero, False)


class TestProcessInfo(unittest.TestCase):
    """ProcessInfoのテスト"""

    def test_to_dict_with_gender(self):
        """性別情報ありの辞書変換テスト"""
        # Arrange
        process_info = ProcessInfo(
            source_name="source_file",
            source_hash="hash123",
            processing_time_seconds=2.5,
            gender="male",
        )

        # Act
        result = process_info.to_dict()

        # Assert
        self.assertEqual(result["source_name"], "source_file")
        self.assertEqual(result["source_hash"], "hash123")
        self.assertEqual(result["processing_time_seconds"], 2.5)
        self.assertEqual(result["gender"], "male")

    def test_from_dict_without_gender(self):
        """性別情報なしの作成テスト"""
        # Arrange
        data = {"source_name": "source_file", "source_hash": "hash456"}

        # Act
        process_info = ProcessInfo.from_dict(data)

        # Assert
        self.assertEqual(process_info.source_name, "source_file")
        self.assertEqual(process_info.source_hash, "hash456")
        self.assertEqual(process_info.processing_time_seconds, 0.0)
        self.assertIsNone(process_info.gender)


class TestMetadata(unittest.TestCase):
    """Metadataのテスト"""

    def setUp(self):
        """テストのセットアップ"""
        self.temporal = TemporalInfo(year=2025, period=1, period_type="weekly")
        self.hash_info = HashInfo(algorithm="sha256", value="test_hash")
        self.verification = Verification(status="verified", verified_at="2025-01-01T00:00:00Z")

    def test_to_dict_minimal(self):
        """最小構成の辞書変換テスト"""
        # Arrange
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertEqual(result["name"], "test_file")
        self.assertEqual(result["filename"], "test_file.csv")
        self.assertEqual(result["profile"], PROFILE_RAW)
        self.assertNotIn("sources", result)
        self.assertNotIn("verification", result)
        self.assertNotIn("quality", result)
        self.assertNotIn("_fetch", result)
        self.assertNotIn("_process", result)

    def test_to_dict_with_sources(self):
        """sources付きの辞書変換テスト"""
        # Arrange
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
            sources=[{"title": "Tokyo IDSC", "path": "https://example.com"}],
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertIn("sources", result)
        self.assertEqual(result["sources"], [{"title": "Tokyo IDSC", "path": "https://example.com"}])

    def test_to_dict_with_verification(self):
        """verification付きの辞書変換テスト"""
        # Arrange
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
            verification=self.verification,
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertIn("verification", result)
        self.assertEqual(result["verification"]["status"], "verified")

    def test_to_dict_with_quality(self):
        """quality付きの辞書変換テスト (v1.2.0)"""
        # Arrange
        quality = {
            "validation_timestamp": "2025-01-01T00:00:00Z",
            "validation_status": "completed",
            "issues": [],
        }
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_PROCESSED,
            data_type="sentinel_weekly_age",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="utf-8",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
            quality=quality,
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertIn("quality", result)
        self.assertEqual(result["quality"]["validation_status"], "completed")

    def test_to_dict_with_fetch(self):
        """_fetch付きの辞書変換テスト"""
        # Arrange
        fetch_info = FetchInfo(source_url="https://example.com", fetch_time_seconds=1.5)
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
            _fetch=fetch_info,
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertIn("_fetch", result)
        self.assertEqual(result["_fetch"]["source_url"], "https://example.com")

    def test_to_dict_with_process(self):
        """_process付きの辞書変換テスト"""
        # Arrange
        process_info = ProcessInfo(source_name="source", source_hash="hash", gender="male")
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="processed/test_file.csv",
            profile=PROFILE_PROCESSED,
            data_type="sentinel_weekly_age",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="utf-8",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
            _process=process_info,
        )

        # Act
        result = metadata.to_dict()

        # Assert
        self.assertIn("_process", result)
        self.assertEqual(result["_process"]["gender"], "male")

    def test_to_json(self):
        """JSON文字列変換のテスト"""
        # Arrange
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        # Act
        json_str = metadata.to_json()
        result = json.loads(json_str)

        # Assert
        self.assertEqual(result["name"], "test_file")
        self.assertEqual(result["metadata_version"], METADATA_VERSION)

    def test_save_and_load(self):
        """ファイル保存・読み込みのテスト"""
        # Arrange
        metadata = Metadata(
            name="test_file",
            filename="test_file.csv",
            path="test_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=self.temporal,
            bytes=1024,
            hash=self.hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_metadata.json"

            # Act: 保存
            metadata.save(file_path)

            # Assert: ファイル存在確認
            self.assertTrue(file_path.exists())

            # Act: 読み込み
            loaded = Metadata.load(file_path)

            # Assert: 内容確認
            self.assertEqual(loaded.name, "test_file")
            self.assertEqual(loaded.filename, "test_file.csv")
            self.assertEqual(loaded.bytes, 1024)

    def test_from_dict(self):
        """辞書からの作成テスト"""
        # Arrange
        data = {
            "metadata_version": "1.3.0",
            "name": "test_file",
            "filename": "test_file.csv",
            "path": "test_file.csv",
            "profile": PROFILE_RAW,
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
            "bytes": 1024,
            "lines": 10,
            "hash": {"algorithm": "sha256", "value": "test_hash"},
            "encoding": "shift_jis",
            "created": "2025-01-01T00:00:00Z",
            "modified": "2025-01-01T00:00:00Z",
            "sources": [{"title": "Tokyo IDSC", "path": "https://example.com"}],
            "verification": {"status": "verified", "verified_at": "2025-01-01T00:00:00Z"},
            "quality": {
                "validation_timestamp": "2025-01-01T00:00:00Z",
                "validation_status": "completed",
                "issues": [],
            },
            "_fetch": {"source_url": "https://example.com", "fetch_time_seconds": 1.5},
        }

        # Act
        metadata = Metadata.from_dict(data)

        # Assert
        self.assertEqual(metadata.name, "test_file")
        self.assertEqual(metadata.temporal.year, 2025)
        self.assertEqual(metadata.hash.value, "test_hash")
        self.assertEqual(metadata.verification.status, "verified")
        self.assertEqual(metadata.quality["validation_status"], "completed")
        self.assertEqual(metadata._fetch.source_url, "https://example.com")

    def test_from_legacy_raw(self):
        """旧形式rawメタデータからの変換テスト"""
        # Arrange
        legacy = {
            "filename": "test_file.csv",
            "year": 2025,
            "period": 1,
            "period_type": "weekly",
            "checksum_algorithm": "sha256",
            "sha256_hash": "legacy_hash",
            "timestamp": "2025-01-01T00:00:00Z",
            "source_url": "https://example.com",
            "fetch_time": 1.5,
            "force_overwrite": True,
            "save_all_zero": False,
            "data_type": "sentinel_weekly_gender",
            "file_size": 2048,
            "line_count": 20,
            "encoding": "shift_jis",
            "file_path": "data/test_file.csv",
        }

        # Act
        metadata = Metadata.from_legacy_raw(legacy)

        # Assert
        self.assertEqual(metadata.name, "test_file")
        self.assertEqual(metadata.filename, "test_file.csv")
        self.assertEqual(metadata.temporal.year, 2025)
        self.assertEqual(metadata.hash.value, "legacy_hash")
        self.assertEqual(metadata.bytes, 2048)
        self.assertEqual(metadata._fetch.source_url, "https://example.com")
        self.assertEqual(len(metadata.sources), 1)

    def test_from_legacy_processed(self):
        """旧形式処理ログからの変換テスト"""
        # Arrange
        legacy = {
            "source": "data/raw/source_file.csv",
            "outputs": [{"path": "data/processed/output_male_file.csv", "size_bytes": 1500}],
            "metadata": {
                "year": "2025",
                "period": "1",
                "frequency": "monthly",
                "category": "sentinel",
                "aggregation": "age",
            },
            "timestamp": "2025-01-01T00:00:00Z",
        }
        source_meta = Metadata(
            name="source_file",
            filename="source_file.csv",
            path="source_file.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_monthly_age",
            temporal=TemporalInfo(year=2025, period=1, period_type="monthly"),
            bytes=2000,
            hash=HashInfo(algorithm="sha256", value="source_hash"),
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        # Act
        metadata = Metadata.from_legacy_processed(legacy, source_meta)

        # Assert
        self.assertEqual(metadata.name, "output_male_file")
        self.assertEqual(metadata.profile, PROFILE_PROCESSED)
        self.assertEqual(metadata.temporal.year, 2025)
        self.assertEqual(metadata._process.source_name, "source_file")
        self.assertEqual(metadata._process.source_hash, "source_hash")
        self.assertEqual(metadata._process.gender, "male")

    def test_create_raw(self):
        """rawメタデータ作成のテスト"""
        # Arrange & Act
        metadata = Metadata.create_raw(
            filename="test_file.csv",
            data_type="sentinel_weekly_gender",
            year=2025,
            period=1,
            period_type="weekly",
            file_size=1024,
            line_count=10,
            sha256_hash="test_hash",
            source_url="https://example.com",
            fetch_time=1.5,
            force_overwrite=True,
            save_all_zero=False,
        )

        # Assert
        self.assertEqual(metadata.profile, PROFILE_RAW)
        self.assertEqual(metadata.encoding, "shift_jis")
        self.assertEqual(metadata.temporal.year, 2025)
        self.assertEqual(metadata._fetch.source_url, "https://example.com")
        self.assertEqual(len(metadata.sources), 1)

    def test_create_raw_without_source_url(self):
        """source_urlなしのrawメタデータ作成テスト"""
        # Arrange & Act
        metadata = Metadata.create_raw(
            filename="test_file.csv",
            data_type="sentinel_weekly_gender",
            year=2025,
            period=1,
            period_type="weekly",
            file_size=1024,
            line_count=10,
            sha256_hash="test_hash",
        )

        # Assert
        self.assertEqual(len(metadata.sources), 0)
        self.assertIsNone(metadata._fetch.source_url)

    def test_create_processed(self):
        """processedメタデータ作成のテスト"""
        # Arrange
        quality = {
            "validation_timestamp": "2025-01-01T00:00:00Z",
            "validation_status": "completed",
            "issues": [],
        }

        # Act
        metadata = Metadata.create_processed(
            filename="male_output.csv",
            data_type="sentinel_weekly_age",
            year=2025,
            period=1,
            period_type="weekly",
            file_size=2048,
            line_count=20,
            sha256_hash="processed_hash",
            source_name="source_file",
            source_hash="source_hash",
            processing_time=2.5,
            gender="male",
            quality=quality,
        )

        # Assert
        self.assertEqual(metadata.profile, PROFILE_PROCESSED)
        self.assertEqual(metadata.encoding, "utf-8")
        self.assertEqual(metadata._process.gender, "male")
        self.assertEqual(metadata.quality["validation_status"], "completed")
        self.assertEqual(metadata.path, "processed/male_output.csv")


class TestNowIso(unittest.TestCase):
    """_now_iso()関数のテスト"""

    def test_now_iso_format(self):
        """ISO 8601形式の時刻文字列が返されることを確認"""
        # Act
        result = _now_iso()

        # Assert
        # ISO 8601形式でパース可能か確認
        parsed = datetime.fromisoformat(result)
        self.assertIsNotNone(parsed)
        # UTCタイムゾーンか確認
        self.assertEqual(parsed.tzinfo, UTC)

    @patch("src.models.metadata.datetime")
    def test_now_iso_mocked(self, mock_datetime):
        """モックを使った時刻取得テスト"""
        # Arrange
        fixed_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        mock_datetime.now.return_value = fixed_time

        # Act
        result = _now_iso()

        # Assert
        mock_datetime.now.assert_called_once_with(UTC)
        self.assertEqual(result, "2025-01-01T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
