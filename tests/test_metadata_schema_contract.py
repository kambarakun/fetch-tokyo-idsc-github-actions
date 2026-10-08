"""メタデータ writer (取得・verify-metadata・migrate-metadata) の出力が実 schema に適合する契約テスト.

どのテストも tmp_path だけを使い、リポジトリの data/ を読まない・書かない。
検査は CI と同じ scripts/validate_metadata_schema.py を、最も厳しい設定
(両 profile の version 検査 + タイムゾーン必須) で使う。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from scripts.validate_metadata_schema import DEFAULT_SCHEMA, validate
from src.cli import migrate_metadata as mm
from src.cli import verify_metadata as vm
from src.managers.storage_manager import StorageManager
from src.models.metadata import METADATA_VERSION

REAL_SCHEMA = Path(__file__).resolve().parent.parent / DEFAULT_SCHEMA
ALL_PROFILES = frozenset({"tokyo-idsc-raw", "tokyo-idsc-processed"})
FORMAT_CHECKS = {"file_size", "encoding", "csv_format", "path_safety"}

GENDER_ISSUE = {
    "check_type": "gender_sum_consistency",
    "validation_status": "completed",
    "message": "Observed mismatch between (male + female) and reported total in 3 record(s)",
    "details": {"source_file": "x.csv", "affected_count": 3, "truncated": False, "affected_locations": []},
}


def _strict_violations(metadata_dir: Path) -> list[tuple[Path, str]]:
    result = validate(REAL_SCHEMA, [metadata_dir], version_profiles=ALL_PROFILES, require_timezone=True)
    assert result.total >= 1
    return result.violations


def _save_raw(tmp_path: Path, data: bytes) -> tuple[Path, Path]:
    raw_dir = tmp_path / "raw"
    result = StorageManager(raw_dir, {"auto_commit": False}).save_with_metadata(
        data=data, data_type="notifiable_weekly", year=2025, period=1
    )
    assert result.success
    assert not result.is_skipped
    return raw_dir, raw_dir / ".metadata" / "notifiable_weekly_2025_01.json"


def _valid_csv() -> bytes:
    # 100 バイト以上・列数一定・0 以外の値を含む (全て0のスキップに掛からない) ので verified になる CSV
    rows = ["col1,col2,col3,col4,col5", *(f"{i}1,{i}2,{i}3,{i}4,{i}5" for i in range(1, 11))]
    return ("\n".join(rows) + "\n").encode("shift_jis")


class _StubQualityValidator:
    """QualityValidator の差し替え. 呼び出しを記録し、指定の結果を返すか例外を投げる."""

    calls: ClassVar[list[tuple[Path, str]]] = []
    result: ClassVar[dict[str, Any] | Exception] = {}

    def __init__(self, raw_data_dir: Path) -> None:
        self.raw_data_dir = Path(raw_data_dir)

    def validate(self, source_filename: str, _data_type: str, _processing_metadata: dict) -> dict[str, Any]:
        type(self).calls.append((self.raw_data_dir, source_filename))
        result = type(self).result
        if isinstance(result, Exception):
            raise result
        return json.loads(json.dumps(result))


@pytest.fixture
def stub_quality(monkeypatch: pytest.MonkeyPatch) -> type[_StubQualityValidator]:
    _StubQualityValidator.calls = []
    _StubQualityValidator.result = {
        "validation_timestamp": "2026-01-01T00:00:00+00:00",
        "validation_status": "completed",
        "issues": [GENDER_ISSUE],
    }
    monkeypatch.setattr(vm, "QualityValidator", _StubQualityValidator)
    monkeypatch.setattr(mm, "QualityValidator", _StubQualityValidator)
    return _StubQualityValidator


def test_fetch_writer_output_conforms(tmp_path: Path) -> None:
    """取得経路 (StorageManager.save_with_metadata) の出力は schema に適合する."""
    raw_dir, _ = _save_raw(tmp_path, b"test,data\n1,2,3")

    assert _strict_violations(raw_dir / ".metadata") == []


def test_verify_metadata_output_conforms_with_gender_issue(
    tmp_path: Path, stub_quality: type[_StubQualityValidator]
) -> None:
    """性別合計の不整合は quality にだけ書かれ、verification はファイル形式の検証結果のまま."""
    raw_dir, metadata_path = _save_raw(tmp_path, _valid_csv())
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["verification"]["status"] == "verified"

    stats = vm.run_verification(metadata_dir=raw_dir / ".metadata", data_dir=raw_dir, dry_run=False)

    assert stats == {"total": 1, "verified": 1, "failed": 0, "skipped": 0, "errors": 0}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert set(metadata["verification"]["checks"]) == FORMAT_CHECKS
    assert metadata["verification"]["status"] == "verified"
    assert metadata["verification"]["errors"] == []
    assert metadata["quality"]["issues"] == [GENDER_ISSUE]
    assert _strict_violations(raw_dir / ".metadata") == []


def test_verify_metadata_output_conforms_when_quality_validation_raises(
    tmp_path: Path, stub_quality: type[_StubQualityValidator]
) -> None:
    """品質検証が例外で終わっても verification は変えず、failed の quality を schema 適合の形で書く."""
    raw_dir, metadata_path = _save_raw(tmp_path, _valid_csv())
    before = json.loads(metadata_path.read_text(encoding="utf-8"))["verification"]
    stub_quality.result = ValueError("broken csv")

    stats = vm.run_verification(metadata_dir=raw_dir / ".metadata", data_dir=raw_dir, dry_run=False)

    assert stats["verified"] == 1
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    after = metadata["verification"]
    assert {k: v for k, v in after.items() if k != "verified_at"} == {
        k: v for k, v in before.items() if k != "verified_at"
    }
    assert metadata["quality"]["validation_status"] == "failed"
    [issue] = metadata["quality"]["issues"]
    assert issue["check_type"] == "gender_sum_consistency"
    assert issue["validation_status"] == "failed"
    assert "broken csv" in issue["message"]
    assert _strict_violations(raw_dir / ".metadata") == []


def _processed_v1_1_0(source_name: str) -> dict[str, Any]:
    """実データ (normalized_*_male_2007_01.json) と同じキー構成の 1.1.0 processed メタデータ."""
    name = f"normalized_sentinel_weekly_medical_district_male_{source_name[-7:]}"
    return {
        "metadata_version": "1.1.0",
        "name": name,
        "filename": f"{name}.csv",
        "path": f"processed/{name}.csv",
        "profile": "tokyo-idsc-processed",
        "data_type": "sentinel_weekly_medical_district",
        "temporal": {"year": 2007, "period": 1, "period_type": "weekly"},
        "bytes": 10,
        "lines": 2,
        "hash": {"algorithm": "sha256", "value": "0" * 64},
        "encoding": "utf-8",
        "created": "2025-12-18T07:33:02.510619+00:00",
        "modified": "2025-12-18T07:33:02.510619+00:00",
        "sources": [{"title": f"{source_name}.csv", "path": f"raw/{source_name}.csv"}],
        "_process": {
            "source_name": source_name,
            "source_hash": "1" * 64,
            "processing_time_seconds": 0.001,
            "gender": "male",
        },
    }


def test_processed_migration_output_conforms(tmp_path: Path, stub_quality: type[_StubQualityValidator]) -> None:
    """processed の移行は raw ソースで品質検証し、processing_log.json を除外し、schema に適合する出力を書く."""
    source_name = "sentinel_weekly_medical_district_2007_01"
    metadata = _processed_v1_1_0(source_name)
    processed_dir = tmp_path / "processed"
    metadata_dir = processed_dir / ".metadata"
    metadata_dir.mkdir(parents=True)
    metadata_path = metadata_dir / f"{metadata['name']}.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    (metadata_dir / "processing_log.json").write_text(json.dumps({"processing": []}), encoding="utf-8")
    (processed_dir / metadata["filename"]).write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / f"{source_name}.csv").write_bytes("性別,男\n".encode("shift_jis"))

    stats = mm.run_migration(metadata_dir=metadata_dir, data_dir=processed_dir, dry_run=False)

    assert stats == {"total": 1, "migrated": 1, "skipped": 0, "errors": 0, "target_version": METADATA_VERSION}
    assert stub_quality.calls == [(tmp_path / "raw", f"{source_name}.csv")]
    migrated = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert migrated["metadata_version"] == METADATA_VERSION
    assert migrated["quality"]["validation_status"] == "completed"
    assert migrated["quality"]["issues"] == [GENDER_ISSUE]
    assert _strict_violations(metadata_dir) == []

    again = mm.run_migration(metadata_dir=metadata_dir, data_dir=processed_dir, dry_run=False)
    assert again["migrated"] == 0
    assert again["errors"] == 0


@pytest.mark.parametrize(
    ("process", "sources"),
    [
        ({"source_name": None}, [{"title": "sentinel_weekly_age_2025_01.csv", "path": "raw/x.csv"}]),
        ({}, []),
    ],
    ids=["falls-back-to-sources-title", "no-source-is-skipped"],
)
def test_processed_quality_source_resolution(
    tmp_path: Path, stub_quality: type[_StubQualityValidator], process: dict, sources: list
) -> None:
    """source_name が無ければ sources[0].title、どちらも無ければ (raw が見つからないので) skipped."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "sentinel_weekly_age_2025_01.csv").write_bytes(b"a,b\n")
    metadata = {
        "metadata_version": "1.1.0",
        "profile": "tokyo-idsc-processed",
        "data_type": "sentinel_weekly_age",
        "_process": process,
        "sources": sources,
    }

    migrated, changes = mm.migrate_v1_1_0_to_v1_2_0(metadata, tmp_path / "processed" / "normalized_x.csv")

    if sources:
        assert stub_quality.calls == [(tmp_path / "raw", "sentinel_weekly_age_2025_01.csv")]
        assert migrated["quality"]["validation_status"] == "completed"
    else:
        assert stub_quality.calls == []
        assert migrated["quality"]["validation_status"] == "skipped"
        assert "quality: added (validation_status=skipped)" in changes
