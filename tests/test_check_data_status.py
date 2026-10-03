"""Tests for stale-output detection and the needs-processing CLI modes of src/cli/check_data_status.py."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from src.cli import check_data_status as cds

NOTIFIABLE_ROWS = ["疾病名,報告数", "病気,1"]
SENTINEL_GENDER_ROWS = ["年齢区分,男性,女性", "0歳,1,2"]
SECTIONED_ROWS = [
    row
    for gender in ("男性", "女性", "男女合計")
    for row in (f'性別,"{gender}"', "年齢区分,インフルエンザ,RSウイルス", "0歳,10,5")
]
AGE_OUTPUTS = [
    "normalized_sentinel_weekly_age_female_2025_01.csv",
    "normalized_sentinel_weekly_age_male_2025_01.csv",
    "normalized_sentinel_weekly_age_total_2025_01.csv",
]
STALE_HASH = "0" * 64


def _write_raw(data_dir: Path, raw_name: str, rows: list[str]) -> Path:
    raw_file = data_dir / "raw" / raw_name
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("\n".join(rows) + "\n", encoding="shift_jis")
    return raw_file


def _write_output(data_dir: Path, output_name: str, source_hash: str | None) -> None:
    """Write a processed CSV and, unless source_hash is None, its processor metadata."""
    processed_file = data_dir / "processed" / output_name
    processed_file.parent.mkdir(parents=True, exist_ok=True)
    processed_file.write_text("h1,h2\n1,2\n", encoding="utf-8")
    if source_hash is None:
        return
    metadata_file = data_dir / "processed" / ".metadata" / f"{Path(output_name).stem}.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps({"_process": {"source_hash": source_hash}}), encoding="utf-8")


def _build_source(
    data_dir: Path, raw_name: str, rows: list[str], outputs: list[str], source_hash: str | None = ""
) -> Path:
    """Write a raw source with its outputs; source_hash "" means the raw file's real sha256."""
    raw_file = _write_raw(data_dir, raw_name, rows)
    recorded_hash = hashlib.sha256(raw_file.read_bytes()).hexdigest() if source_hash == "" else source_hash
    for output_name in outputs:
        _write_output(data_dir, output_name, recorded_hash)
    return raw_file


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["check-data-status", *args])
    return cds.main()


def test_source_with_matching_hashes_is_processed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_source(data_dir, "sentinel_weekly_age_2025_01.csv", SECTIONED_ROWS, AGE_OUTPUTS)

    # Act
    coverage = cds.check_status(data_dir)["coverage"]
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--fail-on-incomplete")

    # Assert
    assert coverage["processed_source_count"] == 1
    assert coverage["processed_rate"] == pytest.approx(100.0)
    assert coverage["stale_source_count"] == 0
    assert coverage["stale_sources"] == []
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "改訂後未再処理raw: 0件" in out
    assert "すべての処理が完了しています" in out


def test_source_hash_mismatch_is_reported_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange: outputs were produced from an older revision of the raw file
    data_dir = tmp_path / "data"
    _build_source(
        data_dir,
        "notifiable_weekly_2025_01.csv",
        NOTIFIABLE_ROWS,
        ["normalized_notifiable_weekly_2025_01.csv"],
        STALE_HASH,
    )

    # Act
    status = cds.check_status(data_dir)
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--fail-on-incomplete")
    capsys.readouterr()
    cds.print_status(status, verbose=True)
    out = capsys.readouterr().out

    # Assert
    coverage = status["coverage"]
    assert coverage["stale_source_count"] == 1
    assert coverage["stale_sources"] == [
        {"raw_file": "notifiable_weekly_2025_01.csv", "stale_outputs": ["normalized_notifiable_weekly_2025_01.csv"]}
    ]
    assert coverage["processed_source_count"] == 0
    assert coverage["processed_rate"] == 0.0
    assert coverage["incomplete_source_count"] == 0
    assert exit_code == 1
    assert "改訂後未再処理raw: 1件" in out
    assert "notifiable_weekly_2025_01.csv (再処理が必要: normalized_notifiable_weekly_2025_01.csv)" in out
    assert "改訂後に再処理されていないrawがあります" in out
    assert out.count("--list-needs-processing") == 1
    assert "すべての処理が完了しています" not in out
    assert "データ処理が必要です" not in out


@pytest.mark.parametrize(
    "metadata_content",
    [
        None,
        "{not json",
        json.dumps({"name": "no process key"}),
        json.dumps({"_process": "not a dict"}),
        json.dumps(["not", "a", "dict"]),
    ],
    ids=["missing_metadata", "broken_json", "missing_process_key", "process_not_dict", "non_dict_json"],
)
def test_one_unverifiable_output_makes_multi_output_source_stale(tmp_path: Path, metadata_content: str | None) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_source(data_dir, "sentinel_weekly_age_2025_01.csv", SECTIONED_ROWS, AGE_OUTPUTS)
    male_metadata = data_dir / "processed" / ".metadata" / "normalized_sentinel_weekly_age_male_2025_01.json"
    if metadata_content is None:
        male_metadata.unlink()
    else:
        male_metadata.write_text(metadata_content, encoding="utf-8")

    # Act
    coverage = cds.check_status(data_dir)["coverage"]

    # Assert
    assert coverage["stale_sources"] == [
        {
            "raw_file": "sentinel_weekly_age_2025_01.csv",
            "stale_outputs": ["normalized_sentinel_weekly_age_male_2025_01.csv"],
        }
    ]
    assert coverage["processed_source_count"] == 0
    assert coverage["incomplete_sources"] == []


def test_processing_log_in_metadata_dir_does_not_break_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange: processing_log.json shares .metadata/ but has no _process section
    data_dir = tmp_path / "data"
    _build_source(
        data_dir, "notifiable_weekly_2025_01.csv", NOTIFIABLE_ROWS, ["normalized_notifiable_weekly_2025_01.csv"]
    )
    processing_log = data_dir / "processed" / ".metadata" / "processing_log.json"
    processing_log.write_text(json.dumps({"processed_files": ["notifiable_weekly_2025_01.csv"]}), encoding="utf-8")

    # Act
    coverage = cds.check_status(data_dir)["coverage"]
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--json", "--fail-on-incomplete")

    # Assert
    assert coverage["processed_source_count"] == 1
    assert coverage["stale_source_count"] == 0
    assert coverage["orphaned_processed_files"] == []
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["coverage"]["stale_sources"] == []


def _build_mixed_data_dir(data_dir: Path) -> None:
    """One processed, one missing-output, one stale source, plus sources reprocessing cannot fix."""
    _build_source(
        data_dir, "notifiable_weekly_2025_02.csv", NOTIFIABLE_ROWS, ["normalized_notifiable_weekly_2025_02.csv"]
    )
    _write_raw(data_dir, "notifiable_weekly_2025_01.csv", NOTIFIABLE_ROWS)
    _build_source(
        data_dir,
        "sentinel_weekly_gender_2025_01.csv",
        SENTINEL_GENDER_ROWS,
        ["normalized_sentinel_weekly_gender_2025_01.csv"],
        STALE_HASH,
    )
    _write_raw(data_dir, "invalid.csv", NOTIFIABLE_ROWS)
    _write_raw(data_dir, "sentinel_weekly_gender_2025_02.csv", ["h1,h2", "1,2"])
    _write_raw(data_dir, "a/notifiable_weekly_2025_03.csv", NOTIFIABLE_ROWS)


def test_list_needs_processing_prints_only_missing_and_stale_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_mixed_data_dir(data_dir)
    expected = [
        str(data_dir / "raw" / "notifiable_weekly_2025_01.csv"),
        str(data_dir / "raw" / "sentinel_weekly_gender_2025_01.csv"),
    ]

    # Act
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing")
    out = capsys.readouterr().out
    verbose_exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--verbose")
    verbose_out = capsys.readouterr().out

    # Assert
    assert exit_code == 0
    assert out.splitlines() == expected
    assert verbose_exit_code == 0
    assert verbose_out.splitlines() == expected


def test_list_needs_processing_uses_data_dir_relative_paths_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange: the default --data-dir yields paths process-data --files accepts as-is
    _build_mixed_data_dir(tmp_path / "data")
    monkeypatch.chdir(tmp_path)

    # Act
    exit_code = _run_main(monkeypatch, "--list-needs-processing")

    # Assert
    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "data/raw/notifiable_weekly_2025_01.csv",
        "data/raw/sentinel_weekly_gender_2025_01.csv",
    ]


def test_list_needs_processing_with_fail_on_incomplete_prints_list_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_mixed_data_dir(data_dir)

    # Act
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--fail-on-incomplete")

    # Assert
    assert exit_code == 1
    assert capsys.readouterr().out.splitlines() == [
        str(data_dir / "raw" / "notifiable_weekly_2025_01.csv"),
        str(data_dir / "raw" / "sentinel_weekly_gender_2025_01.csv"),
    ]


def test_list_needs_processing_and_json_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Act
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--json")

    # Assert
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_without_new_flags_exit_code_stays_zero_when_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_mixed_data_dir(data_dir)

    # Act
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir))
    out = capsys.readouterr().out

    # Assert
    assert exit_code == 0
    assert "未完了raw: 4件" in out
    assert "改訂後未再処理raw: 1件" in out
    assert "一部のファイルが処理されていません" in out
    assert "改訂後に再処理されていないrawがあります" in out
    assert out.count("--list-needs-processing") == 1
    assert "処理できないrawファイルが3件あります" in out
    assert "すべての処理が完了しています" not in out


def test_orphaned_outputs_alone_do_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_source(
        data_dir, "notifiable_weekly_2025_01.csv", NOTIFIABLE_ROWS, ["normalized_notifiable_weekly_2025_01.csv"]
    )
    _write_output(data_dir, "normalized_notifiable_weekly_2025_99.csv", None)

    # Act
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--fail-on-incomplete")

    # Assert
    assert exit_code == 0
    assert capsys.readouterr().out == ""
