"""scripts/validate_metadata_schema.py のテスト.

実データに依存せず tmp_path 上の最小スキーマ + メタデータでロジックを検証する。
実 schema vs 実データの整合は CI の独立ステップ (uv run python scripts/validate_metadata_schema.py) が担う。
"""

import json
from pathlib import Path

from scripts.validate_metadata_schema import (
    NON_METADATA_FILES,
    iter_metadata_files,
    main,
    validate,
)

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

    total, violations = validate(schema, [md])

    assert total == 2
    assert violations == []


def test_validate_detects_missing_required_field(tmp_path):
    """必須フィールド欠落を検出する (processing_log で実際に起きた不整合の縮図)."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "good.json", {"metadata_version": "1.3.0"})
    _write_json(md / "bad.json", {"name": "x"})

    total, violations = validate(schema, [md])

    assert total == 2
    assert len(violations) == 1
    assert violations[0][0].name == "bad.json"
    assert "metadata_version" in violations[0][1]


def test_validate_handles_invalid_json(tmp_path):
    """壊れた JSON は違反として報告し、処理を継続する."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    md.mkdir()
    (md / "broken.json").write_text("{ invalid json", encoding="utf-8")

    total, violations = validate(schema, [md])

    assert total == 1
    assert len(violations) == 1
    assert "JSON 読み込み失敗" in violations[0][1]


def test_validate_reports_error_location(tmp_path):
    """型エラーはフィールド位置付きで報告される."""
    schema = _make_schema(tmp_path)
    md = tmp_path / ".metadata"
    _write_json(md / "wrong_type.json", {"metadata_version": 130})  # str でなく int

    _, violations = validate(schema, [md])

    assert len(violations) == 1
    assert "metadata_version" in violations[0][1]


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
