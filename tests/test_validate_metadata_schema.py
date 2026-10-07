"""scripts/validate_metadata_schema.py のテスト.

実データに依存せず tmp_path 上の最小スキーマ + メタデータでロジックを検証する。
実 schema vs 実データの整合は CI の独立ステップ (uv run python scripts/validate_metadata_schema.py) が担う。
"""

import copy
import json
import re
from pathlib import Path

import pytest

from scripts.validate_metadata_schema import (
    DEFAULT_SCHEMA,
    DEFAULT_VERSION_PROFILES,
    NON_METADATA_FILES,
    ValidationResult,
    iter_metadata_files,
    main,
    validate,
)
from src.models.metadata import METADATA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA = PROJECT_ROOT / DEFAULT_SCHEMA

# 取得経路 (StorageManager.save_with_metadata) の出力と同じキー構成の最小 raw メタデータ
VALID_RAW_METADATA = {
    "metadata_version": METADATA_VERSION,
    "name": "notifiable_weekly_2025_01",
    "filename": "notifiable_weekly_2025_01.csv",
    "path": "notifiable_weekly_2025_01.csv",
    "profile": "tokyo-idsc-raw",
    "data_type": "notifiable_weekly",
    "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
    "bytes": 15,
    "lines": 2,
    "hash": {"algorithm": "sha256", "value": "0" * 64},
    "encoding": "shift_jis",
    "created": "2025-01-01T00:00:00.123456+00:00",
    "modified": "2025-01-01T00:00:00Z",
    "sources": [],
    "_fetch": {"source_url": None, "fetch_time_seconds": 0.0, "force_overwrite": False, "save_all_zero": False},
    "verification": {
        "status": "verified",
        "verified_at": "2025-01-01T00:00:01+09:00",
        "method": "automated",
        "checks": {"file_size": True, "encoding": True, "csv_format": True, "path_safety": True},
        "errors": [],
        "warnings": [],
    },
    "quality": {"validation_timestamp": "2025-01-01T00:00:02+00:00", "validation_status": "completed", "issues": []},
}

# 最小スキーマ: metadata_version (string) を必須とするだけ
SIMPLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["metadata_version"],
    "properties": {"metadata_version": {"type": "string"}},
    "additionalProperties": True,
}


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _make_schema(tmp_path: Path) -> Path:
    schema = tmp_path / "schema.json"
    _write_json(schema, SIMPLE_SCHEMA)
    return schema


def test_iter_metadata_files_excludes_non_metadata(tmp_path):
    """hash_index.json / processing_log.json は列挙対象から除外される."""
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", {"metadata_version": "1.3.0"})
    _write_json(md / "hash_index.json", {})
    _write_json(md / "processing_log.json", {"processing": []})

    names = [f.name for f in iter_metadata_files([md])]

    assert names == ["a.json"]
    assert {"hash_index.json", "processing_log.json"} == NON_METADATA_FILES


def test_iter_metadata_files_skips_missing_dir(tmp_path):
    """存在しないディレクトリは黙ってスキップする."""
    assert list(iter_metadata_files([tmp_path / "does-not-exist"])) == []


def test_validate_all_conforming(tmp_path):
    """全件適合の場合は違反ゼロ."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", {"metadata_version": "1.3.0"})
    _write_json(md / "b.json", {"metadata_version": "1.2.0", "extra": 1})

    result = validate(schema, [md])

    assert result.total == 2
    assert result.violations == []


def test_validate_detects_missing_required_field(tmp_path):
    """必須フィールド欠落を検出する (processing_log で実際に起きた不整合の縮図)."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "good.json", {"metadata_version": "1.3.0"})
    _write_json(md / "bad.json", {"name": "x"})

    result = validate(schema, [md])

    assert result.total == 2
    assert len(result.violations) == 1
    assert result.violations[0][0].name == "bad.json"
    assert "metadata_version" in result.violations[0][1]


def test_validate_handles_invalid_json(tmp_path):
    """壊れた JSON は違反として報告し、処理を継続する."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    md.mkdir()
    (md / "broken.json").write_text("{ invalid json", encoding="utf-8")

    result = validate(schema, [md])

    assert result.total == 1
    assert len(result.violations) == 1
    assert "JSON 読み込み失敗" in result.violations[0][1]


def test_validate_reports_error_location(tmp_path):
    """型エラーはフィールド位置付きで報告される."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "wrong_type.json", {"metadata_version": 130})  # str でなく int

    result = validate(schema, [md])

    assert len(result.violations) == 1
    assert "metadata_version" in result.violations[0][1]


def test_main_success(tmp_path, capsys):
    """全適合時は終了コード0と適合メッセージ."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", {"metadata_version": "1.3.0"})

    rc = main(["--schema", str(schema), str(md)])

    assert rc == 0
    assert "すべて適合" in capsys.readouterr().out


def test_main_violation(tmp_path, capsys):
    """不適合時は終了コード1と不適合メッセージ."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "bad.json", {})

    rc = main(["--schema", str(schema), str(md)])

    assert rc == 1
    assert "不適合" in capsys.readouterr().out


def test_main_schema_not_found(tmp_path, capsys):
    """スキーマファイル不在時は終了コード2."""
    rc = main(["--schema", str(tmp_path / "missing.json"), str(tmp_path)])

    assert rc == 2
    assert "見つかりません" in capsys.readouterr().err


def test_main_truncates_many_violations(tmp_path, capsys):
    """違反が表示上限 (50) を超えると残件数を表示する."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    for i in range(55):
        _write_json(md / f"bad{i:02d}.json", {})

    rc = main(["--schema", str(schema), str(md)])

    assert rc == 1
    assert "他 5件" in capsys.readouterr().out


def test_main_invalid_schema(tmp_path, capsys):
    """スキーマ自体が無効な JSON Schema の場合は終了コード2 (メタスキーマ検証)."""
    bad_schema = tmp_path / "bad_schema.json"
    _write_json(bad_schema, {"type": "not-a-valid-type"})
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", {"metadata_version": "1.3.0"})

    rc = main(["--schema", str(bad_schema), str(md)])

    assert rc == 2
    assert "スキーマ" in capsys.readouterr().err


def _raw(**overrides: object) -> dict:
    metadata = copy.deepcopy(VALID_RAW_METADATA)
    metadata.update(overrides)
    return metadata


def _processed(**overrides: object) -> dict:
    """実データの processed メタデータ (normalized_*) と同じキー構成."""
    metadata = _raw(
        name="normalized_notifiable_weekly_2025_01",
        filename="normalized_notifiable_weekly_2025_01.csv",
        path="processed/normalized_notifiable_weekly_2025_01.csv",
        profile="tokyo-idsc-processed",
        encoding="utf-8",
        sources=[{"title": "notifiable_weekly_2025_01.csv", "path": "raw/notifiable_weekly_2025_01.csv"}],
        _process={"source_name": "notifiable_weekly_2025_01", "source_hash": "0" * 64, "gender": None},
    )
    del metadata["_fetch"], metadata["verification"]
    metadata.update(overrides)
    return metadata


def _validate_one(tmp_path: Path, metadata: dict, *, require_timezone: bool = False) -> ValidationResult:
    md = tmp_path / ".metadata"
    _write_json(md / "x.json", metadata)
    return validate(REAL_SCHEMA, [md], require_timezone=require_timezone)


def test_real_schema_accepts_writer_shaped_metadata(tmp_path):
    """writer と同じ形の raw メタデータは、タイムゾーン必須でも違反も警告も出ない."""
    result = _validate_one(tmp_path, _raw(), require_timezone=True)

    assert result.violations == []
    assert result.version_warnings == {}
    assert result.timezone_warnings == 0


@pytest.mark.parametrize("value", ["not-a-date", "2025-01-01", "2025-01-01 00:00:00", "2025-13-01T00:00:00+00:00"])
def test_invalid_date_time_is_a_violation(tmp_path, value):
    """date-time は RFC 3339 の形で実際に検査される (日付だけ・空白区切り・実在しない日付は不適合)."""
    result = _validate_one(tmp_path, _raw(created=value))

    assert len(result.violations) == 1
    assert result.violations[0][1].startswith("created:")


def test_naive_timestamp_is_a_warning_by_default(tmp_path, capsys):
    """タイムゾーンなしは既定では警告だけで exit 0、--require-timezone で不適合 (exit 1)."""
    md = tmp_path / ".metadata"
    metadata = _raw(created="2025-01-01T00:00:00.5", modified="2025-01-01T00:00:00")
    metadata["verification"]["verified_at"] = "2025-01-01T00:00:01"
    _write_json(md / "naive.json", metadata)
    _write_json(md / "aware.json", _raw())

    result = validate(REAL_SCHEMA, [md])
    assert result.violations == []
    assert result.timezone_warnings == 1

    assert main(["--schema", str(REAL_SCHEMA), str(md)]) == 0
    out = capsys.readouterr().out
    assert "2件すべて適合" in out
    assert re.search(r"^警告: タイムゾーンなし.*1件", out, re.MULTILINE)

    assert main(["--schema", str(REAL_SCHEMA), "--require-timezone", str(md)]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("メタデータ schema 検証: 2件中 1件が不適合")
    assert "created: タイムゾーン" in out
    assert "警告: タイムゾーンなし" not in out


def test_quality_timestamp_is_checked_for_timezone(tmp_path):
    """quality.validation_timestamp もタイムゾーン検査の対象."""
    metadata = _raw()
    metadata["quality"]["validation_timestamp"] = "2025-01-01T00:00:02"

    result = _validate_one(tmp_path, metadata, require_timezone=True)

    assert len(result.violations) == 1
    assert result.violations[0][1].startswith("quality/validation_timestamp:")


def test_uri_format_stays_an_annotation(tmp_path):
    """uri は注釈扱いのまま (processed の相対参照 raw/<file>.csv は適合し続ける)."""
    result = _validate_one(tmp_path, _processed(), require_timezone=True)

    assert result.violations == []


def test_raw_profile_version_mismatch_is_a_violation(tmp_path):
    """既定の version 検査対象 (raw profile) の不一致は不適合."""
    assert frozenset({"tokyo-idsc-raw"}) == DEFAULT_VERSION_PROFILES

    result = _validate_one(tmp_path, _raw(metadata_version="1.2.0"))

    assert len(result.violations) == 1
    assert result.violations[0][1].startswith("metadata_version:")


def test_processed_profile_version_mismatch_is_opt_in(tmp_path, capsys):
    """processed profile の不一致は既定で警告、--version-profiles に含めると不適合."""
    md = tmp_path / ".metadata"
    _write_json(md / "p.json", _processed(metadata_version="1.1.0"))

    assert main(["--schema", str(REAL_SCHEMA), str(md)]) == 0
    out = capsys.readouterr().out
    assert "1件すべて適合" in out
    assert re.search(r"^警告: metadata_version.*1件", out, re.MULTILINE)

    rc = main(["--schema", str(REAL_SCHEMA), "--version-profiles", "tokyo-idsc-raw,tokyo-idsc-processed", str(md)])
    assert rc == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("メタデータ schema 検証: 1件中 1件が不適合")
    assert "警告: metadata_version" not in out


def test_version_is_not_checked_without_profile(tmp_path):
    """profile を持たないファイルは version 検査しない (最小スキーマでの互換)."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", {"metadata_version": "0.0.1"})

    result = validate(schema, [md], version_profiles=frozenset({"tokyo-idsc-raw", "tokyo-idsc-processed"}))

    assert result.violations == []
    assert result.version_warnings == {}


@pytest.mark.parametrize("value", ["tokyo-idsc-unknown", "tokyo-idsc-raw,typo", ","])
def test_unknown_version_profile_exits_2(tmp_path, capsys, value):
    """未知の profile 名 (または空) は exit 2."""
    md = tmp_path / ".metadata"
    _write_json(md / "a.json", _raw())

    assert main(["--schema", str(REAL_SCHEMA), "--version-profiles", value, str(md)]) == 2
    assert "profile" in capsys.readouterr().err


def test_default_schema_matches_metadata_version():
    """DEFAULT_SCHEMA のファイル名と title が METADATA_VERSION と一致する (bump 時の drift 検出)."""
    major, minor, _patch = METADATA_VERSION.split(".")
    assert DEFAULT_SCHEMA.name == f"metadata-v{major}.{minor}.schema.json"

    schema = json.loads(REAL_SCHEMA.read_text(encoding="utf-8"))
    assert f"(v{METADATA_VERSION})" in schema["title"]
