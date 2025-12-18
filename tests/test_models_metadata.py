"""メタデータモデルのユニットテスト"""

import sys
import unittest
from pathlib import Path

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
)


class TestTemporalInfo(unittest.TestCase):
    """TemporalInfoのテスト"""

    def test_create_weekly_temporal_info(self):
        """週次の時間情報作成"""
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")

        self.assertEqual(temporal.year, 2025)
        self.assertEqual(temporal.period, 1)
        self.assertEqual(temporal.period_type, "weekly")

    def test_create_monthly_temporal_info(self):
        """月次の時間情報作成"""
        temporal = TemporalInfo(year=2025, period=12, period_type="monthly")

        self.assertEqual(temporal.year, 2025)
        self.assertEqual(temporal.period, 12)
        self.assertEqual(temporal.period_type, "monthly")

    def test_to_dict(self):
        """辞書変換のテスト"""
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")
        data = temporal.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["year"], 2025)
        self.assertEqual(data["period"], 1)
        self.assertEqual(data["period_type"], "weekly")

    def test_from_dict(self):
        """辞書からの作成"""
        data = {"year": 2025, "period": 1, "period_type": "weekly"}
        temporal = TemporalInfo.from_dict(data)

        self.assertIsInstance(temporal, TemporalInfo)
        self.assertEqual(temporal.year, 2025)
        self.assertEqual(temporal.period, 1)
        self.assertEqual(temporal.period_type, "weekly")

    def test_round_trip_conversion(self):
        """往復変換で元のデータが保持されることを確認"""
        original = TemporalInfo(year=2025, period=52, period_type="weekly")
        data = original.to_dict()
        restored = TemporalInfo.from_dict(data)

        self.assertEqual(restored.year, original.year)
        self.assertEqual(restored.period, original.period)
        self.assertEqual(restored.period_type, original.period_type)


class TestHashInfo(unittest.TestCase):
    """HashInfoのテスト"""

    def test_create_sha256_hash(self):
        """SHA256ハッシュ情報作成"""
        hash_info = HashInfo(algorithm="sha256", value="abc123")

        self.assertEqual(hash_info.algorithm, "sha256")
        self.assertEqual(hash_info.value, "abc123")

    def test_to_dict(self):
        """辞書変換のテスト"""
        hash_info = HashInfo(algorithm="sha256", value="abc123")
        data = hash_info.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["algorithm"], "sha256")
        self.assertEqual(data["value"], "abc123")

    def test_from_dict(self):
        """辞書からの作成"""
        data = {"algorithm": "sha256", "value": "abc123"}
        hash_info = HashInfo.from_dict(data)

        self.assertIsInstance(hash_info, HashInfo)
        self.assertEqual(hash_info.algorithm, "sha256")
        self.assertEqual(hash_info.value, "abc123")

    def test_different_algorithms(self):
        """異なるハッシュアルゴリズムのテスト"""
        for algorithm in ["sha256", "sha512", "md5"]:
            hash_info = HashInfo(algorithm=algorithm, value="test_hash")
            self.assertEqual(hash_info.algorithm, algorithm)


class TestVerification(unittest.TestCase):
    """Verificationのテスト"""

    def test_create_verified_status(self):
        """検証済みステータスの作成"""
        verification = Verification(
            status="verified", verified_at="2025-01-01T00:00:00Z", method="automated"
        )

        self.assertEqual(verification.status, "verified")
        self.assertEqual(verification.verified_at, "2025-01-01T00:00:00Z")
        self.assertEqual(verification.method, "automated")

    def test_default_fields(self):
        """デフォルトフィールドの確認"""
        verification = Verification(status="pending")

        self.assertEqual(verification.status, "pending")
        self.assertIsNone(verification.verified_at)
        self.assertEqual(verification.method, "automated")
        self.assertEqual(verification.checks, {})
        self.assertEqual(verification.errors, [])
        self.assertEqual(verification.warnings, [])

    def test_with_errors_and_warnings(self):
        """エラーと警告を含む検証情報"""
        verification = Verification(
            status="failed",
            verified_at="2025-01-01T00:00:00Z",
            checks={"encoding": True, "format": False},
            errors=["Invalid CSV format"],
            warnings=["Missing header"],
        )

        self.assertEqual(verification.status, "failed")
        self.assertEqual(len(verification.errors), 1)
        self.assertEqual(len(verification.warnings), 1)
        self.assertTrue(verification.checks["encoding"])
        self.assertFalse(verification.checks["format"])

    def test_to_dict(self):
        """辞書変換のテスト"""
        verification = Verification(
            status="verified",
            verified_at="2025-01-01T00:00:00Z",
            checks={"test": True},
        )
        data = verification.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["status"], "verified")
        self.assertEqual(data["verified_at"], "2025-01-01T00:00:00Z")
        self.assertEqual(data["checks"], {"test": True})

    def test_from_dict(self):
        """辞書からの作成"""
        data = {
            "status": "verified",
            "verified_at": "2025-01-01T00:00:00Z",
            "method": "automated",
            "checks": {"test": True},
            "errors": [],
            "warnings": [],
        }
        verification = Verification.from_dict(data)

        self.assertIsInstance(verification, Verification)
        self.assertEqual(verification.status, "verified")


class TestFetchInfo(unittest.TestCase):
    """FetchInfoのテスト"""

    def test_create_fetch_info(self):
        """フェッチ情報作成"""
        fetch_info = FetchInfo(
            source_url="https://example.com/data.csv",
            fetch_time_seconds=1.5,
            force_overwrite=False,
            save_all_zero=False,
        )

        self.assertEqual(fetch_info.source_url, "https://example.com/data.csv")
        self.assertEqual(fetch_info.fetch_time_seconds, 1.5)
        self.assertFalse(fetch_info.force_overwrite)
        self.assertFalse(fetch_info.save_all_zero)

    def test_default_fields(self):
        """デフォルトフィールドの確認"""
        fetch_info = FetchInfo()

        self.assertIsNone(fetch_info.source_url)
        self.assertEqual(fetch_info.fetch_time_seconds, 0.0)
        self.assertFalse(fetch_info.force_overwrite)
        self.assertFalse(fetch_info.save_all_zero)

    def test_to_dict(self):
        """辞書変換のテスト"""
        fetch_info = FetchInfo(
            source_url="https://example.com/data.csv",
            fetch_time_seconds=1.5,
        )
        data = fetch_info.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["source_url"], "https://example.com/data.csv")
        self.assertEqual(data["fetch_time_seconds"], 1.5)

    def test_from_dict(self):
        """辞書からの作成"""
        data = {
            "source_url": "https://example.com/data.csv",
            "fetch_time_seconds": 1.5,
            "force_overwrite": True,
            "save_all_zero": False,
        }
        fetch_info = FetchInfo.from_dict(data)

        self.assertIsInstance(fetch_info, FetchInfo)
        self.assertEqual(fetch_info.fetch_time_seconds, 1.5)
        self.assertTrue(fetch_info.force_overwrite)


class TestProcessInfo(unittest.TestCase):
    """ProcessInfoのテスト"""

    def test_create_process_info(self):
        """処理情報作成"""
        process_info = ProcessInfo(
            source_name="source_file",
            source_hash="abc123",
            processing_time_seconds=2.5,
        )

        self.assertEqual(process_info.source_name, "source_file")
        self.assertEqual(process_info.source_hash, "abc123")
        self.assertEqual(process_info.processing_time_seconds, 2.5)

    def test_with_gender(self):
        """性別情報を含む処理情報"""
        process_info = ProcessInfo(
            source_name="source_file",
            source_hash="abc123",
            processing_time_seconds=2.5,
            gender="male",
        )

        self.assertEqual(process_info.gender, "male")

    def test_default_gender_is_none(self):
        """デフォルトの性別はNone"""
        process_info = ProcessInfo(
            source_name="source_file",
            source_hash="abc123",
        )

        self.assertIsNone(process_info.gender)
        self.assertEqual(process_info.processing_time_seconds, 0.0)

    def test_to_dict(self):
        """辞書変換のテスト"""
        process_info = ProcessInfo(
            source_name="source_file",
            source_hash="abc123",
            gender="female",
        )
        data = process_info.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["gender"], "female")

    def test_from_dict(self):
        """辞書からの作成"""
        data = {
            "source_name": "source_file",
            "source_hash": "abc123",
            "processing_time_seconds": 2.5,
            "gender": "total",
        }
        process_info = ProcessInfo.from_dict(data)

        self.assertIsInstance(process_info, ProcessInfo)
        self.assertEqual(process_info.gender, "total")


class TestMetadata(unittest.TestCase):
    """Metadataのテスト"""

    def test_create_raw_metadata(self):
        """生データのメタデータ作成"""
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")
        hash_info = HashInfo(algorithm="sha256", value="abc123")

        metadata = Metadata(
            metadata_version=METADATA_VERSION,
            name="test_data",
            filename="test.csv",
            path="raw/test.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=temporal,
            bytes=1024,
            lines=10,
            hash=hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        self.assertEqual(metadata.metadata_version, METADATA_VERSION)
        self.assertEqual(metadata.name, "test_data")
        self.assertEqual(metadata.profile, PROFILE_RAW)
        self.assertEqual(metadata.data_type, "sentinel_weekly_gender")

    def test_default_fields(self):
        """デフォルトフィールドの確認"""
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")
        hash_info = HashInfo(algorithm="sha256", value="abc123")

        metadata = Metadata(
            name="test_data",
            filename="test.csv",
            path="raw/test.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=temporal,
            bytes=1024,
            hash=hash_info,
            encoding="shift_jis",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )

        self.assertIsNone(metadata.lines)
        self.assertEqual(metadata.encoding, "shift_jis")
        self.assertEqual(metadata.metadata_version, METADATA_VERSION)

    def test_to_dict(self):
        """辞書変換のテスト"""
        temporal = TemporalInfo(year=2025, period=1, period_type="weekly")
        hash_info = HashInfo(algorithm="sha256", value="abc123")

        metadata = Metadata(
            metadata_version=METADATA_VERSION,
            name="test_data",
            filename="test.csv",
            path="raw/test.csv",
            profile=PROFILE_RAW,
            data_type="sentinel_weekly_gender",
            temporal=temporal,
            bytes=1024,
            lines=10,
            hash=hash_info,
            encoding="utf-8",
            created="2025-01-01T00:00:00Z",
            modified="2025-01-01T00:00:00Z",
        )
        data = metadata.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["metadata_version"], METADATA_VERSION)
        self.assertEqual(data["name"], "test_data")
        self.assertEqual(data["encoding"], "utf-8")
        self.assertIsInstance(data["temporal"], dict)
        self.assertIsInstance(data["hash"], dict)

    def test_from_dict_minimal(self):
        """最小限のフィールドで辞書からの作成"""
        data = {
            "metadata_version": METADATA_VERSION,
            "name": "test_data",
            "filename": "test.csv",
            "path": "raw/test.csv",
            "profile": PROFILE_RAW,
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
            "bytes": 1024,
            "hash": {"algorithm": "sha256", "value": "abc123"},
            "encoding": "shift_jis",
            "created": "2025-01-01T00:00:00Z",
            "modified": "2025-01-01T00:00:00Z",
        }
        metadata = Metadata.from_dict(data)

        self.assertIsInstance(metadata, Metadata)
        self.assertEqual(metadata.name, "test_data")
        self.assertIsInstance(metadata.temporal, TemporalInfo)
        self.assertIsInstance(metadata.hash, HashInfo)

    def test_from_dict_with_all_fields(self):
        """全フィールドを含む辞書からの作成"""
        data = {
            "metadata_version": METADATA_VERSION,
            "name": "test_data",
            "filename": "test.csv",
            "path": "raw/test.csv",
            "profile": PROFILE_RAW,
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
            "bytes": 1024,
            "lines": 10,
            "hash": {"algorithm": "sha256", "value": "abc123"},
            "encoding": "utf-8",
            "created": "2025-01-01T00:00:00Z",
            "modified": "2025-01-01T00:00:00Z",
            "sources": [{"title": "Source", "path": "https://example.com"}],
            "verification": {
                "status": "verified",
                "verified_at": "2025-01-01T00:00:00Z",
                "method": "automated",
                "checks": {},
                "errors": [],
                "warnings": [],
            },
            "_fetch": {
                "source_url": "https://example.com",
                "fetch_time_seconds": 1.0,
                "force_overwrite": False,
                "save_all_zero": False,
            },
        }
        metadata = Metadata.from_dict(data)

        self.assertIsInstance(metadata, Metadata)
        self.assertEqual(metadata.lines, 10)
        self.assertEqual(metadata.encoding, "utf-8")
        self.assertIsInstance(metadata.verification, Verification)
        self.assertIsInstance(metadata._fetch, FetchInfo)


if __name__ == "__main__":
    unittest.main()
