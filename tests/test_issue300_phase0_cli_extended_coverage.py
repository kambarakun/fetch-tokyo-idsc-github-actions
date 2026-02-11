"""Extended coverage tests for migrated CLI modules."""

from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.cli import fetch_data as fd
from src.cli import migrate_metadata as mm
from src.cli import validate_data as vd
from src.cli import verify_metadata as vm
from src.fetchers.enhanced_fetcher import FetchParams


def _config(base_dir: Path, *, auto_commit: bool = False, mode: str | None = None, incremental_mode: bool = False):
    collection = SimpleNamespace(
        mode=mode,
        incremental_mode=incremental_mode,
        batch_size=2,
        data_types_to_collect=["sentinel_weekly_gender"],
        start_year=2025,
        max_execution_time_hours=5.5,
    )
    storage = SimpleNamespace(
        auto_commit=auto_commit,
        base_directory=str(base_dir),
        commit_message_template="test {date_range}",
        keep_shift_jis=True,
    )
    return SimpleNamespace(collection=collection, storage=storage)


def _fetch_result(
    *,
    success: bool,
    data: bytes = b"h,v\n1,2\n",
    fetch_time: float = 0.1,
    source_url: str | None = None,
    error: str | None = None,
):
    return SimpleNamespace(
        success=success,
        data=data,
        fetch_time=fetch_time,
        source_url=source_url,
        error=error,
    )


def _save_result(
    *,
    success: bool = True,
    is_duplicate: bool = False,
    is_skipped: bool = False,
    is_new: bool = True,
    error: str | None = None,
):
    return SimpleNamespace(
        success=success,
        is_duplicate=is_duplicate,
        is_skipped=is_skipped,
        is_new=is_new,
        error=error,
    )


def test_fetch_setup_logging_and_save_stats_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = fd.setup_logging(str(tmp_path / "logs" / "app.log"), "DEBUG")
    assert isinstance(logger, logging.Logger)
    assert (tmp_path / "logs").exists()

    monkeypatch.chdir(tmp_path)
    mock_logger = Mock()

    monkeypatch.setenv("FETCH_TIMESTAMP", "invalid")
    fd.save_stats_to_file({"start_time": datetime.now(UTC)}, mock_logger)
    files = sorted((tmp_path / "data" / "logs").glob("stats_*.json"))
    assert len(files) == 1
    payload = json.loads(files[-1].read_text(encoding="utf-8"))
    assert "start_time" in payload

    monkeypatch.setenv("FETCH_TIMESTAMP", "20260211_120000")
    fd.save_stats_to_file({"end_time": datetime.now(UTC)}, mock_logger)
    assert (tmp_path / "data" / "logs" / "stats_20260211_120000.json").exists()

    monkeypatch.delenv("FETCH_TIMESTAMP", raising=False)
    fd.save_stats_to_file({"k": "v"}, mock_logger)
    assert len(list((tmp_path / "data" / "logs").glob("stats_*.json"))) >= 2


def test_fetch_datacollector_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher_mock = Mock()
    storage_mock = Mock()
    storage_mock.get_existing_files.return_value = []
    fetcher_mock.fetch_methods = {"dt": Mock()}
    fetcher_mock.get_missing_data.return_value = []

    monkeypatch.setattr(fd, "EnhancedEpidemicDataFetcher", lambda *_args, **_kwargs: fetcher_mock)
    monkeypatch.setattr(fd, "StorageManager", lambda *_args, **_kwargs: storage_mock)
    monkeypatch.setattr(fd.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fd.random, "uniform", lambda *_args, **_kwargs: 0.0)

    config_with_mode = _config(tmp_path / "raw", mode="force")
    collector = fd.DataCollector(config_with_mode)
    assert str(collector.mode) == "force"
    collector_mode_arg = fd.DataCollector(config_with_mode, mode="full")
    assert str(collector_mode_arg.mode) == "full"

    config_incremental = _config(tmp_path / "raw2", mode=None, incremental_mode=True)
    collector2 = fd.DataCollector(config_incremental)
    assert str(collector2.mode) == "incremental"

    config_default = _config(tmp_path / "raw3", mode=None, incremental_mode=False)
    collector3 = fd.DataCollector(config_default)
    assert str(collector3.mode) == "incremental"
    collector3.mode = "unknown"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown collection mode"):
        collector3._collect_data_type("dt", 2025, 2025)

    config_commit = _config(tmp_path / "raw4", auto_commit=True, mode="full")
    collector4 = fd.DataCollector(config_commit, dry_run=False)
    collector4._collect_data_type = Mock()  # type: ignore[method-assign]
    collector4._commit_changes = Mock()  # type: ignore[method-assign]
    collector4._print_statistics = Mock()  # type: ignore[method-assign]
    stats = collector4.collect_data(data_types=["dt"], start_year=2025, end_year=2025)
    assert stats["start_time"] is not None
    assert stats["end_time"] is not None
    collector4._commit_changes.assert_called_once()

    config_batch = _config(tmp_path / "raw5", mode="full")
    collector5 = fd.DataCollector(config_batch, dry_run=False)
    params = [
        FetchParams(
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
            data_type="dt",
            report_type="1",
        ),
        FetchParams(
            start_year="2025",
            start_sub_period="2",
            end_year="2025",
            end_sub_period="2",
            data_type="dt",
            report_type="1",
        ),
    ]
    collector5._generate_all_params = Mock(return_value=params)  # type: ignore[method-assign]
    collector5._process_batch = Mock()  # type: ignore[method-assign]
    collector5._check_execution_time = Mock(side_effect=[True])  # type: ignore[method-assign]
    collector5._collect_data_type("dt", 2025, 2025)
    collector5._process_batch.assert_called_once()

    config_process = _config(tmp_path / "raw6", mode="full")
    collector6 = fd.DataCollector(config_process, dry_run=False, save_all_zero=True)
    fetcher_mock.fetch_with_retry.return_value = _fetch_result(success=True, source_url="https://example.com")
    storage_mock.save_with_metadata.return_value = _save_result(is_duplicate=True)
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6.stats["duplicates"] == 1

    storage_mock.save_with_metadata.return_value = _save_result(is_skipped=True)
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6.stats["skipped"] == 1

    storage_mock.save_with_metadata.return_value = _save_result(success=True, is_new=False)
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6.stats["updated_files"] == 1

    storage_mock.save_with_metadata.return_value = _save_result(success=True, is_new=True)
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6.stats["new_files"] == 1

    storage_mock.save_with_metadata.return_value = _save_result(success=False, error="save-failed")
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6.stats["failed"] >= 1
    assert "save-failed" in collector6.stats["errors"]

    collector6_dry = fd.DataCollector(config_process, dry_run=True)
    fetcher_mock.fetch_with_retry.return_value = _fetch_result(success=True)
    collector6_dry._process_batch([params[0]], "dt", is_monthly=False)
    assert collector6_dry.stats["successful"] == 1

    fetcher_mock.fetch_with_retry.return_value = _fetch_result(success=False, error="fetch-failed")
    collector6._process_batch([params[0]], "dt", is_monthly=False)
    assert "fetch-failed" in collector6.stats["errors"]

    assert collector6._get_epid_code("sentinel_weekly_health_center") == ""
    assert collector6._get_epid_code("notifiable_weekly") == ""
    assert collector6._get_epid_code("sentinel_weekly_gender") == "00"

    storage_mock.commit_changes.return_value = SimpleNamespace(success=True, message="committed", error=None)
    collector6._commit_changes()
    storage_mock.commit_changes.return_value = SimpleNamespace(success=False, error="commit failed")
    collector6._commit_changes()
    storage_mock.commit_changes.side_effect = RuntimeError("boom")
    collector6._commit_changes()

    collector6.stats["start_time"] = datetime.now(UTC)
    collector6.stats["end_time"] = datetime.now(UTC)
    collector6.stats["errors"] = ["e1", "e2"]
    collector6._print_statistics()


def test_fetch_main_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Mock()
    config = _config(tmp_path / "raw", auto_commit=False, mode=None)
    config_manager = Mock()
    config_manager.load_config.return_value = config

    collector_instance = Mock()
    collector_instance.collect_data.return_value = {"failed": 0}

    monkeypatch.setattr(fd, "setup_logging", lambda *_args, **_kwargs: logger)
    monkeypatch.setattr(fd, "ConfigurationManager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(fd, "DataCollector", lambda **_kwargs: collector_instance)
    save_stats_mock = Mock()
    monkeypatch.setattr(fd, "save_stats_to_file", save_stats_mock)

    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch-data", "--mode", "full", "--skip-existing", "--config", "config/config.yml"],
    )
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch-data",
            "--config",
            "config/config.yml",
            "--target-weeks",
            "1,2",
            "--target-months",
            "3,4",
            "--save-all-zero",
            "--start-year",
            "2024",
            "--end-year",
            "2025",
            "--data-types",
            "sentinel_weekly_gender",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 0
    save_stats_mock.assert_called_once()

    collector_instance.collect_data.return_value = {"failed": 1}
    monkeypatch.setattr(sys, "argv", ["fetch-data", "--config", "config/config.yml"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1

    config_manager.load_config.side_effect = RuntimeError("broken")
    monkeypatch.setattr(sys, "argv", ["fetch-data", "--config", "config/config.yml"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1


def test_validate_data_additional_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    data_root = tmp_path / "data" / "raw"
    data_root.mkdir(parents=True)

    valid_file = data_root / "valid.csv"
    valid_file.write_text("a,b,c\n1,2,3\n4,5,6\n" * 10, encoding="utf-8")

    outside = tmp_path / "data_evil" / "attack.csv"
    outside.parent.mkdir(parents=True)
    outside.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    validator = vd.DataValidator(strict_mode=False, encoding="utf-8")
    ok = validator._check_path_safety(valid_file)
    assert ok["valid"] is True
    ng = validator._check_path_safety(outside)
    assert ng["valid"] is False
    assert any("Path traversal detected" in e for e in ng["errors"])
    dangerous = validator._check_path_safety(Path("../evil.csv"))
    assert dangerous["valid"] is False
    assert any("Dangerous pattern in path" in e for e in dangerous["errors"])

    missing_result = validator.validate_file(tmp_path / "missing.csv")
    assert missing_result["valid"] is False
    assert "File not found" in missing_result["errors"][0]

    monkeypatch.setattr(validator, "_check_file_size", lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))
    failed = validator.validate_file(valid_file)
    assert failed["valid"] is False
    assert any("Validation failed" in e for e in failed["errors"])
    monkeypatch.setattr(
        validator,
        "_check_file_size",
        vd.DataValidator._check_file_size.__get__(validator, vd.DataValidator),
    )

    details_validator = vd.DataValidator(strict_mode=False, encoding="utf-8")
    monkeypatch.setattr(
        details_validator,
        "_check_file_size",
        lambda _p: {"valid": True, "errors": [], "warnings": [], "details": {"size_hint": 1}},
    )
    monkeypatch.setattr(
        details_validator,
        "_check_encoding",
        lambda _p: {"valid": True, "errors": [], "details": {"encoding_hint": "ok"}},
    )
    monkeypatch.setattr(
        details_validator,
        "_check_csv_format",
        lambda _p: {"valid": True, "errors": [], "warnings": [], "details": {"csv_hint": True}},
    )
    monkeypatch.setattr(
        details_validator,
        "_check_path_safety",
        lambda _p: {"valid": True, "errors": [], "details": {"path_hint": "safe"}},
    )
    with_details = details_validator.validate_file(valid_file)
    assert with_details["details"]["size_hint"] == 1
    assert with_details["details"]["encoding_hint"] == "ok"
    assert with_details["details"]["csv_hint"] is True
    assert with_details["details"]["path_hint"] == "safe"

    original_max_file_size = vd.MAX_FILE_SIZE_MB
    original_min_file_size = vd.MIN_FILE_SIZE_BYTES
    monkeypatch.setattr(vd, "MAX_FILE_SIZE_MB", 0.000001)
    monkeypatch.setattr(vd, "MIN_FILE_SIZE_BYTES", 0)
    too_large = validator._check_file_size(valid_file)
    assert too_large["valid"] is False
    assert any("File too large" in e for e in too_large["errors"])
    size_mb = valid_file.stat().st_size / (1024 * 1024)
    monkeypatch.setattr(vd, "MAX_FILE_SIZE_MB", size_mb / 0.9)
    warning_size = validator._check_file_size(valid_file)
    assert any("File size warning" in w for w in warning_size["warnings"])

    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda _self: (_ for _ in ()).throw(OSError("stat-error")))
    stat_error = validator._check_file_size(valid_file)
    assert stat_error["valid"] is False
    monkeypatch.setattr(Path, "stat", original_stat)

    original_open = Path.open
    monkeypatch.setattr(Path, "open", lambda _self, *args, **kwargs: (_ for _ in ()).throw(OSError("open-error")))
    enc_error = validator._check_encoding(valid_file)
    assert enc_error["valid"] is False
    csv_error = validator._check_csv_format(valid_file)
    assert csv_error["valid"] is False
    monkeypatch.setattr(Path, "open", original_open)

    original_max_line_count = vd.MAX_LINE_COUNT
    original_min_line_count = vd.MIN_LINE_COUNT
    original_min_column_count = vd.MIN_COLUMN_COUNT

    monkeypatch.setattr(vd, "MAX_LINE_COUNT", 1)
    many_lines = validator._check_csv_format(valid_file)
    assert many_lines["valid"] is False
    assert any("Too many lines" in e for e in many_lines["errors"])

    one_col = data_root / "one_col.csv"
    one_col.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(vd, "MIN_LINE_COUNT", 1)
    monkeypatch.setattr(vd, "MIN_COLUMN_COUNT", 2)
    few_cols = validator._check_csv_format(one_col)
    assert few_cols["valid"] is False
    assert any("Too few columns" in e for e in few_cols["errors"])

    monkeypatch.setattr(vd, "MAX_COLUMN_COUNT", 1)
    too_many_cols = validator._check_csv_format(valid_file)
    assert too_many_cols["valid"] is False
    assert any("Too many columns" in e for e in too_many_cols["errors"])

    monkeypatch.setattr(vd, "MAX_COLUMN_COUNT", 10)
    monkeypatch.setattr(vd, "MIN_LINE_COUNT", 50)
    too_few_lines = validator._check_csv_format(one_col)
    assert too_few_lines["valid"] is False
    assert any("Too few lines" in e for e in too_few_lines["errors"])

    original_csv_reader = vd.csv.reader

    class BadReader:
        def __iter__(self):
            raise csv.Error("bad-csv")

    monkeypatch.setattr(vd.csv, "reader", lambda _f: BadReader())
    csv_parse_error = validator._check_csv_format(valid_file)
    assert csv_parse_error["valid"] is False
    assert any("CSV format error" in e for e in csv_parse_error["errors"])

    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda _self: (_ for _ in ()).throw(OSError("resolve-error")))
    safety_error = validator._check_path_safety(valid_file)
    assert safety_error["valid"] is False
    monkeypatch.setattr(Path, "resolve", original_resolve)

    no_dir = validator.validate_directory(tmp_path / "not_exists")
    assert no_dir == []
    invalid_results = validator.validate_directory(data_root, pattern="one_col.csv")
    assert len(invalid_results) == 1
    monkeypatch.setattr(
        validator,
        "validate_file",
        lambda _p: {"valid": False, "errors": ["err"], "warnings": ["warn"]},
    )
    warning_results = validator.validate_directory(data_root, pattern="valid.csv")
    assert len(warning_results) == 1

    report_validator = vd.DataValidator(strict_mode=False, encoding="utf-8")
    report_validator.validation_results.append(
        {
            "file": str(valid_file),
            "valid": True,
            "errors": [],
            "warnings": [],
            "checks": {"file_size": {"size_mb": 0.12}, "csv_format": {"line_count": 3}},
        }
    )
    markdown = report_validator.generate_markdown_report()
    assert "| ファイル | サイズ | 行数 | ステータス |" in markdown

    # restore temporary test overrides before executing main()
    monkeypatch.setattr(vd, "MAX_FILE_SIZE_MB", original_max_file_size)
    monkeypatch.setattr(vd, "MIN_FILE_SIZE_BYTES", original_min_file_size)
    monkeypatch.setattr(vd, "MAX_LINE_COUNT", original_max_line_count)
    monkeypatch.setattr(vd, "MIN_LINE_COUNT", original_min_line_count)
    monkeypatch.setattr(vd, "MIN_COLUMN_COUNT", original_min_column_count)
    monkeypatch.setattr(vd.csv, "reader", original_csv_reader)

    # main: JSON output + exit 0
    output_json = tmp_path / "out" / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate-data", str(valid_file), "--encoding", "utf-8", "--output", str(output_json), "--format", "json"],
    )
    with pytest.raises(SystemExit) as exc:
        vd.main()
    assert exc.value.code == 0
    assert output_json.exists()

    # main: markdown output
    output_md = tmp_path / "out" / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate-data", str(valid_file), "--encoding", "utf-8", "--output", str(output_md), "--format", "markdown"],
    )
    with pytest.raises(SystemExit) as exc:
        vd.main()
    assert exc.value.code == 0
    assert output_md.exists()

    monkeypatch.setattr(
        sys,
        "argv",
        ["validate-data", str(data_root), "--pattern", "valid.csv", "--encoding", "utf-8"],
    )
    with pytest.raises(SystemExit) as exc:
        vd.main()
    assert exc.value.code == 0

    # strict mode with warning => exit 1
    warn_csv = data_root / "warn.csv"
    warn_csv.write_text("a,b,c\n1,2\n3,4,5\n" * 10, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate-data", str(warn_csv), "--encoding", "utf-8", "--strict"])
    with pytest.raises(SystemExit) as exc:
        vd.main()
    assert exc.value.code == 1
    assert "検証結果サマリー" in capsys.readouterr().out


def test_verify_metadata_main_and_extra_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    metadata_dir = tmp_path / ".metadata"
    data_dir = tmp_path / "raw"
    metadata_dir.mkdir()
    data_dir.mkdir()

    metadata_path = metadata_dir / "sample.json"
    metadata_path.write_text(json.dumps({"filename": "sample.csv", "verification": None}), encoding="utf-8")
    data_file = data_dir / "sample.csv"
    data_file.write_bytes("a,b,c\n1,2,3\n".encode("shift_jis"))

    storage = Mock()
    storage.validate_file.return_value = {
        "status": "failed",
        "checks": {"encoding": False},
        "errors": ["e1"],
        "warnings": ["w1"],
    }
    result = vm._process_single_file(
        metadata_path, data_dir, storage, dry_run=True, verbose=True, only_unverified=False
    )
    assert result[0] == "failed"

    storage.validate_file.return_value = {
        "status": "verified",
        "checks": {"encoding": True},
        "errors": [],
        "warnings": [],
    }
    result = vm._process_single_file(
        metadata_path, data_dir, storage, dry_run=False, verbose=True, only_unverified=False
    )
    assert result == ("verified", "verified")
    updated = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert updated["verification"]["status"] == "verified"
    skipped = vm._process_single_file(
        metadata_path, data_dir, storage, dry_run=False, verbose=True, only_unverified=True
    )
    assert skipped[0] == "skipped"

    bad_meta = metadata_dir / "bad.json"
    bad_meta.write_text("{ invalid", encoding="utf-8")
    stats = vm.run_verification(metadata_dir=metadata_dir, data_dir=data_dir)
    assert stats["errors"] >= 1

    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(sys, "argv", ["verify-metadata", "--metadata-dir", str(missing_dir)])
    assert vm.main() == 1

    monkeypatch.setattr(
        vm, "run_verification", lambda **_kwargs: {"total": 1, "verified": 1, "failed": 1, "skipped": 0, "errors": 0}
    )
    monkeypatch.setattr(
        sys, "argv", ["verify-metadata", "--metadata-dir", str(metadata_dir), "--dry-run", "--output-json"]
    )
    assert vm.main() == 0
    assert '"verified": 1' in capsys.readouterr().out

    monkeypatch.setattr(
        vm, "run_verification", lambda **_kwargs: {"total": 1, "verified": 0, "failed": 0, "skipped": 0, "errors": 1}
    )
    monkeypatch.setattr(sys, "argv", ["verify-metadata", "--metadata-dir", str(metadata_dir)])
    assert vm.main() == 1


def test_migrate_metadata_additional_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    io_error_file = tmp_path / "io_error.csv"
    io_error_file.write_text("a,b\n1,2\n", encoding="utf-8")
    original_open = Path.open
    monkeypatch.setattr(Path, "open", lambda _self, *_args, **_kwargs: (_ for _ in ()).throw(OSError("open-error")))
    assert mm.count_lines(io_error_file) is None
    monkeypatch.setattr(Path, "open", original_open)

    reg = mm.MigrationRegistry()
    metadata = {"metadata_version": "1.0", "filename": "x.csv"}
    migrated, changes = reg.migrate(metadata, None, "2.0")
    assert migrated == metadata
    assert changes == []

    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
    migrated1, changes1 = mm.migrate_v1_0_to_v1_1_0({"metadata_version": "1.0", "filename": ""}, csv_file)
    assert migrated1["filename"] == "sample.csv"
    assert any("reconstructed from data_file" in c for c in changes1)

    migrated2, changes2 = mm.migrate_v1_0_to_v1_1_0(
        {"metadata_version": "1.0", "filename": "", "data_type": "x", "year": 2025, "period": 2},
        None,
    )
    assert migrated2["filename"] == "x_2025_02.csv"
    assert any("reconstructed from temporal" in c for c in changes2)

    migrated3, changes3 = mm.migrate_v1_0_to_v1_1_0({"metadata_version": "1.0", "filename": ""}, None)
    assert migrated3["filename"] == "unknown.csv"
    assert any("unknown.csv" in c for c in changes3)

    migrated4, changes4 = mm.migrate_v1_0_to_v1_1_0(
        {
            "metadata_version": "1.0",
            "filename": "sentinel_weekly_gender_2025_01.csv",
            "sha256_hash": "",
            "verification": {"status": "verified"},
        },
        csv_file,
    )
    assert migrated4["hash"]["value"] != ""
    assert migrated4["verification"]["status"] == "verified"
    assert any("recalculated from file" in c for c in changes4)

    migrated5, changes5 = mm.migrate_v1_0_to_v1_1_0(
        {
            "metadata_version": "1.0",
            "filename": "x_weekly_2025_01.csv",
            "sha256_hash": "",
            "force_overwrite": None,
            "save_all_zero": None,
        },
        None,
    )
    assert migrated5["_fetch"] == {"source_url": None}
    assert any("WARNING: hash value is empty" in c for c in changes5)

    dt, temporal = mm._extract_data_type_and_temporal("foo_bar_2025_01")
    assert dt == "foo_bar"
    assert temporal["period_type"] == "weekly"
    _, temporal_bad = mm._extract_data_type_and_temporal("foo_weekly_xx_yy")
    assert temporal_bad["year"] == 2000

    v12 = {
        "metadata_version": "1.2.0",
        "verification": {
            "warnings": [123, "[csv_format] Inconsistent column count: {1, x}"],
            "details": {},
        },
    }
    migrated6, _changes6 = mm.migrate_v1_2_0_to_v1_3_0(v12, None)
    assert migrated6["metadata_version"] == "1.3.0"

    class _FakeMatch:
        def group(self, _idx: int) -> None:
            return None

    class _FakePattern:
        def match(self, _warning: str) -> _FakeMatch:
            return _FakeMatch()

    original_compile = mm.re.compile
    monkeypatch.setattr(mm.re, "compile", lambda _pattern: _FakePattern())
    migrated_parse_fail, _ = mm.migrate_v1_2_0_to_v1_3_0(
        {
            "metadata_version": "1.2.0",
            "verification": {"warnings": ["[csv_format] Inconsistent column count: {1, 2}"], "details": {}},
        },
        None,
    )
    assert migrated_parse_fail["metadata_version"] == "1.3.0"
    monkeypatch.setattr(mm.re, "compile", original_compile)

    assert mm.needs_migration({"metadata_version": "1.0"}, target_version="1.0") is True
    assert mm.needs_migration({"metadata_version": "1.1.0"}, target_version="1.1.0") is True
    assert mm.needs_migration({"metadata_version": "1.2.0"}, target_version="1.2.0") is True

    metadata_dir = tmp_path / ".metadata"
    data_dir = tmp_path / "raw"
    metadata_dir.mkdir()
    data_dir.mkdir()

    (metadata_dir / "skip.json").write_text(json.dumps({"metadata_version": mm.METADATA_VERSION}), encoding="utf-8")
    (metadata_dir / "migrate.json").write_text(
        json.dumps({"filename": "legacy.csv", "timestamp": "2025-01-01T00:00:00"}),
        encoding="utf-8",
    )
    (data_dir / "legacy.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    stats = mm.run_migration(
        metadata_dir=metadata_dir,
        data_dir=data_dir,
        target_version=mm.METADATA_VERSION,
        dry_run=False,
        verbose=True,
    )
    assert stats["migrated"] >= 1
    assert stats["skipped"] >= 1

    monkeypatch.setattr(sys, "argv", ["migrate-metadata", "--list-versions"])
    assert mm.main() == 0

    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(sys, "argv", ["migrate-metadata", "--metadata-dir", str(missing_dir)])
    assert mm.main() == 1

    monkeypatch.setattr(
        mm,
        "run_migration",
        lambda **_kwargs: {"target_version": "1.3.0", "total": 1, "migrated": 1, "skipped": 0, "errors": 0},
    )
    monkeypatch.setattr(sys, "argv", ["migrate-metadata", "--metadata-dir", str(metadata_dir), "--dry-run"])
    assert mm.main() == 0

    monkeypatch.setattr(
        mm,
        "run_migration",
        lambda **_kwargs: {"target_version": "1.3.0", "total": 1, "migrated": 0, "skipped": 0, "errors": 1},
    )
    monkeypatch.setattr(sys, "argv", ["migrate-metadata", "--metadata-dir", str(metadata_dir)])
    assert mm.main() == 1


def test_fetch_main_parse_errors_for_target_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Mock()
    config_manager = Mock()
    config_manager.load_config.return_value = _config(tmp_path / "raw")
    monkeypatch.setattr(fd, "setup_logging", lambda *_args, **_kwargs: logger)
    monkeypatch.setattr(fd, "ConfigurationManager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(fd, "DataCollector", lambda **_kwargs: Mock(collect_data=Mock(return_value={"failed": 0})))
    monkeypatch.setattr(fd, "save_stats_to_file", Mock())

    monkeypatch.setattr(sys, "argv", ["fetch-data", "--target-weeks", "x"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1

    monkeypatch.setattr(sys, "argv", ["fetch-data", "--target-months", "y"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1

    # cover force-update + skip-existing conflict branch
    monkeypatch.setattr(sys, "argv", ["fetch-data", "--skip-existing", "--force-update"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 1

    # dry-run path should skip save_stats_to_file
    save_stats_mock = Mock()
    monkeypatch.setattr(fd, "save_stats_to_file", save_stats_mock)
    monkeypatch.setattr(sys, "argv", ["fetch-data", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 0
    save_stats_mock.assert_not_called()
