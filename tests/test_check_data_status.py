"""Tests for stale-output detection and the needs-processing CLI modes of src/cli/check_data_status.py."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from src.cli import check_data_status as cds
from src.processors.data_processor import DataProcessor

RAW_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "raw"

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


def _write_output(data_dir: Path, output_name: str) -> None:
    """Write a processed CSV without processor metadata."""
    processed_file = data_dir / "processed" / output_name
    processed_file.parent.mkdir(parents=True, exist_ok=True)
    processed_file.write_text("h1,h2\n1,2\n", encoding="utf-8")


def _write_metadata(data_dir: Path, output_name: str, source_hash: str) -> None:
    """Write the processor metadata that records source_hash for a processed CSV."""
    metadata_file = data_dir / "processed" / ".metadata" / f"{Path(output_name).stem}.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps({"_process": {"source_hash": source_hash}}), encoding="utf-8")


def _build_source(data_dir: Path, raw_name: str, rows: list[str], outputs: list[str], *, stale: bool = False) -> Path:
    """Write a raw source and its outputs with metadata.

    The metadata records the raw file's real sha256, or STALE_HASH when stale is True
    (the outputs were produced from an older revision of the raw file).
    """
    raw_file = _write_raw(data_dir, raw_name, rows)
    recorded_hash = STALE_HASH if stale else hashlib.sha256(raw_file.read_bytes()).hexdigest()
    for output_name in outputs:
        _write_output(data_dir, output_name)
        _write_metadata(data_dir, output_name, recorded_hash)
    return raw_file


def _count_opens_under(monkeypatch: pytest.MonkeyPatch, directory: Path) -> Counter[str]:
    """Count Path.open calls (read_text / read_bytes go through it on Python 3.11) per file name under directory."""
    opens: Counter[str] = Counter()
    original_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if directory in self.parents:
            opens[self.relative_to(directory).as_posix()] += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    return opens


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
        stale=True,
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


def test_normal_coverage_scan_reads_each_raw_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: processed, stale, missing-output and unprocessable sources, plus ones skipped by name or path
    data_dir = tmp_path / "data"
    _build_source(data_dir, "sentinel_weekly_age_2025_01.csv", SECTIONED_ROWS, AGE_OUTPUTS)
    _build_source(
        data_dir,
        "notifiable_weekly_2025_01.csv",
        NOTIFIABLE_ROWS,
        ["normalized_notifiable_weekly_2025_01.csv"],
        stale=True,
    )
    _write_raw(data_dir, "notifiable_weekly_2025_02.csv", NOTIFIABLE_ROWS)
    _write_raw(data_dir, "sentinel_weekly_gender_2025_02.csv", ["h1,h2", "1,2"])
    _write_raw(data_dir, "invalid.csv", NOTIFIABLE_ROWS)
    _write_raw(data_dir, "a/notifiable_weekly_2025_03.csv", NOTIFIABLE_ROWS)
    raw_opens = _count_opens_under(monkeypatch, data_dir / "raw")

    # Act
    coverage = cds.check_processing_coverage(data_dir / "raw", data_dir / "processed")

    # Assert
    assert raw_opens == {
        "sentinel_weekly_age_2025_01.csv": 1,
        "notifiable_weekly_2025_01.csv": 1,
        "notifiable_weekly_2025_02.csv": 1,
        "sentinel_weekly_gender_2025_02.csv": 1,
    }
    assert coverage["processed_source_count"] == 1
    assert coverage["stale_sources"] == [
        {"raw_file": "notifiable_weekly_2025_01.csv", "stale_outputs": ["normalized_notifiable_weekly_2025_01.csv"]}
    ]
    assert coverage["incomplete_source_count"] == 4


def test_processor_source_hash_is_raw_bytes_hash_that_coverage_accepts(tmp_path: Path) -> None:
    # Arrange: a published-shape Shift_JIS raw with CRLF line endings
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "sentinel_weekly_age_2025_10.csv"
    shutil.copyfile(RAW_FIXTURES_DIR / raw_file.name, raw_file)
    raw_bytes = raw_file.read_bytes()
    decoded_text_bytes = raw_bytes.decode("shift_jis").replace("\r\n", "\n").encode("utf-8")
    assert b"\r\n" in raw_bytes
    assert decoded_text_bytes != raw_bytes

    # Act
    result = DataProcessor(data_dir).process_file(raw_file)
    coverage = cds.check_processing_coverage(raw_dir, data_dir / "processed")

    # Assert
    assert result.success
    recorded_hashes = {
        json.loads((data_dir / "processed" / ".metadata" / f"{output.stem}.json").read_text(encoding="utf-8"))[
            "_process"
        ]["source_hash"]
        for output in result.output_files
    }
    assert recorded_hashes == {hashlib.sha256(raw_bytes).hexdigest()}
    assert coverage["processed_source_count"] == 1
    assert coverage["stale_sources"] == []
    assert coverage["incomplete_sources"] == []


def test_raw_read_error_propagates_from_coverage_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    _build_source(
        data_dir, "notifiable_weekly_2025_01.csv", NOTIFIABLE_ROWS, ["normalized_notifiable_weekly_2025_01.csv"]
    )
    original_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "notifiable_weekly_2025_01.csv":
            raise PermissionError("raw read denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    # Act / Assert
    with pytest.raises(PermissionError, match="raw read denied"):
        cds.check_processing_coverage(data_dir / "raw", data_dir / "processed")


def test_standalone_helpers_read_the_raw_file_themselves(tmp_path: Path) -> None:
    # Arrange
    data_dir = tmp_path / "data"
    raw_file = _build_source(data_dir, "sentinel_weekly_age_2025_01.csv", SECTIONED_ROWS, AGE_OUTPUTS)
    metadata_dir = data_dir / "processed" / ".metadata"
    _write_metadata(data_dir, AGE_OUTPUTS[0], STALE_HASH)

    # Act
    expected_outputs = cds.expected_processed_outputs(raw_file)
    stale_outputs = cds.find_stale_outputs(raw_file, AGE_OUTPUTS, metadata_dir)

    # Assert
    assert expected_outputs == AGE_OUTPUTS
    assert stale_outputs == [AGE_OUTPUTS[0]]


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
        stale=True,
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
    _write_output(data_dir, "normalized_notifiable_weekly_2025_99.csv")

    # Act
    exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--fail-on-incomplete")

    # Assert
    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_unfixable_sources_alone_still_fail_but_are_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange: only sources reprocessing cannot fix (unsupported name, nested path, unprocessable content)
    data_dir = tmp_path / "data"
    _write_raw(data_dir, "invalid.csv", NOTIFIABLE_ROWS)
    _write_raw(data_dir, "a/notifiable_weekly_2025_03.csv", NOTIFIABLE_ROWS)
    _write_raw(data_dir, "sentinel_weekly_gender_2025_02.csv", ["h1,h2", "1,2"])

    # Act
    list_exit_code = _run_main(
        monkeypatch, "--data-dir", str(data_dir), "--list-needs-processing", "--fail-on-incomplete"
    )
    list_out = capsys.readouterr().out
    json_exit_code = _run_main(monkeypatch, "--data-dir", str(data_dir), "--json", "--fail-on-incomplete")
    coverage = json.loads(capsys.readouterr().out)["coverage"]

    # Assert
    assert list_exit_code == 1
    assert list_out == ""
    assert json_exit_code == 1
    assert coverage["incomplete_source_count"] == 3
    assert coverage["stale_source_count"] == 0
