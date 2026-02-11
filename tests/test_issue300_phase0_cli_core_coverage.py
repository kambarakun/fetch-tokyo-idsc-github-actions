"""Coverage tests for newly migrated core CLI entrypoints."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.cli import check_data_status as cds
from src.cli import check_missing as cm
from src.cli import cleanup_all_zero_data as cleanup
from src.cli import process_data as process
from src.processors.data_processor import NormalizationResult


def _write_csv(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_check_data_status_directory_and_status_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    _write_csv(data_dir / "raw" / "a.csv", ["h1,h2", "1,2"])
    _write_csv(data_dir / "processed" / "a.csv", ["h1,h2", "1,2"])
    _write_csv(data_dir / "processed" / "b.csv", ["h1,h2", "3,4"])

    missing = cds.check_directory(data_dir / "missing")
    assert missing["exists"] is False

    verbose_dir = cds.check_directory(data_dir / "processed", verbose=True)
    assert verbose_dir["exists"] is True
    assert verbose_dir["file_count"] == 2
    assert len(verbose_dir["files"]) == 2

    status = cds.check_status(data_dir, verbose=True)
    assert status["coverage"]["processed_rate"] == pytest.approx(200.0)
    empty_status = cds.check_status(tmp_path / "empty-data", verbose=False)
    assert empty_status["coverage"]["processed_rate"] == 0.0

    status_no_raw = {
        "raw": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "processed": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "backups": {"exists": False, "file_count": 0, "total_size_mb": 0, "files": []},
        "logs": {"exists": False, "file_count": 0, "total_size_mb": 0, "files": []},
        "coverage": {"processed_rate": 0.0},
    }
    cds.print_status(status_no_raw, verbose=False)
    out = capsys.readouterr().out
    assert "data/raw/にデータがありません" in out

    status_need_processing = {
        "raw": {"exists": True, "file_count": 2, "total_size_mb": 0},
        "processed": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "backups": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "logs": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "coverage": {"processed_rate": 0.0},
    }
    cds.print_status(status_need_processing, verbose=False)
    out = capsys.readouterr().out
    assert "データ処理が必要です" in out

    status_partial = {
        "raw": {"exists": True, "file_count": 5, "total_size_mb": 0},
        "processed": {"exists": True, "file_count": 3, "total_size_mb": 0},
        "backups": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "logs": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "coverage": {"processed_rate": 60.0},
    }
    cds.print_status(status_partial, verbose=False)
    out = capsys.readouterr().out
    assert "一部のファイルが処理されていません" in out

    status_done = {
        "raw": {"exists": True, "file_count": 2, "total_size_mb": 0},
        "processed": {"exists": True, "file_count": 2, "total_size_mb": 0},
        "backups": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "logs": {"exists": True, "file_count": 0, "total_size_mb": 0},
        "coverage": {"processed_rate": 100.0},
    }
    cds.print_status(status_done, verbose=False)
    out = capsys.readouterr().out
    assert "すべての処理が完了しています" in out


def test_check_data_status_print_dir_and_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = [{"name": f"f{i}.csv", "size_kb": 1.0, "path": f"p/{i}.csv"} for i in range(11)]
    cds.print_dir_status({"exists": False, "file_count": 0, "total_size_mb": 0, "files": []}, verbose=True)
    assert "ディレクトリが存在しません" in capsys.readouterr().out

    cds.print_dir_status({"exists": True, "file_count": 11, "total_size_mb": 1.0, "files": files}, verbose=True)
    out = capsys.readouterr().out
    assert "他 1件" in out

    data_dir = tmp_path / "d"
    _write_csv(data_dir / "raw" / "a.csv", ["h1,h2", "1,2"])
    _write_csv(data_dir / "processed" / "a.csv", ["h1,h2", "1,2"])

    monkeypatch.setattr(sys, "argv", ["check-data-status", "--data-dir", str(data_dir), "--json"])
    cds.main()
    out = capsys.readouterr().out
    assert '"coverage"' in out

    monkeypatch.setattr(sys, "argv", ["check-data-status", "--data-dir", str(data_dir), "--verbose"])
    cds.main()
    assert "東京都感染症データ処理状況" in capsys.readouterr().out

    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(sys, "argv", ["check-data-status", "--data-dir", str(missing_dir)])
    with pytest.raises(SystemExit) as exc:
        cds.main()
    assert exc.value.code == 1
    assert "データディレクトリが見つかりません" in capsys.readouterr().err


def test_check_missing_collect_analyse_report_and_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = tmp_path / "raw"
    _write_csv(raw / "sentinel_weekly_gender_2025_01_a.csv", ["h", "1"])
    _write_csv(raw / "sentinel_weekly_gender_2025_03_b.csv", ["h", "2"])
    _write_csv(raw / "sentinel_monthly_age_2025_02_a.csv", ["h", "1"])
    _write_csv(raw / "unrelated.csv", ["x"])

    weekly, monthly = cm.collect(raw)
    assert ("sentinel_weekly_gender", 2025) in weekly
    assert weekly[("sentinel_weekly_gender", 2025)] == {1, 3}
    assert monthly[("sentinel_monthly_age", 2025)] == {2}

    current_year = datetime.now().year
    found = {("sentinel_weekly_gender", current_year): {1, 3}}
    missing = cm.analyse(found, current_limit=4, max_func=lambda _year: 52)
    assert missing["sentinel_weekly_gender"][current_year] == [2, 4]

    no_missing = cm.analyse({("sentinel_weekly_gender", 2020): {1, 2}}, current_limit=4, max_func=lambda _year: 2)
    assert no_missing == {}

    cm.report("empty", {})
    assert "欠番なし" in capsys.readouterr().out
    cm.report("weekly", {"sentinel_weekly_gender": {2025: [2, 4]}})
    out = capsys.readouterr().out
    assert "合計欠番数: 2" in out

    monkeypatch.setattr(sys, "argv", ["check-missing", str(raw)])
    cm.main()
    out = capsys.readouterr().out
    assert "統計情報" in out

    missing_dir = tmp_path / "not-found"
    monkeypatch.setattr(sys, "argv", ["check-missing", str(missing_dir)])
    with pytest.raises(SystemExit) as exc:
        cm.main()
    assert exc.value.code == 1
    assert "ディレクトリが見つかりません" in capsys.readouterr().out

    # default path branch (len(sys.argv) != 2)
    monkeypatch.setattr(sys, "argv", ["check-missing"])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "raw").mkdir(parents=True)
    cm.main()
    assert "データディレクトリ" in capsys.readouterr().out


def test_cleanup_find_delete_and_main_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    logger = cleanup.setup_logging(verbose=True)
    assert isinstance(logger, logging.Logger)

    data_dir = tmp_path / "raw"
    data_dir.mkdir(parents=True)
    zero_file = data_dir / "zero.csv"
    nonzero_file = data_dir / "nonzero.csv"
    error_file = data_dir / "error.csv"
    zero_file.write_bytes(b"0")
    nonzero_file.write_bytes(b"1")
    error_file.write_bytes(b"2")

    storage = Mock()
    storage._is_all_zero_data.side_effect = lambda data: data == b"0"

    original_read_bytes = Path.read_bytes

    def fake_read_bytes(path: Path) -> bytes:
        if path.name == "error.csv":
            raise OSError("read-failed")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    found = cleanup.find_all_zero_files(storage, data_dir, logger)
    assert found == [zero_file]

    storage.metadata_dir = tmp_path / ".metadata"
    storage.metadata_dir.mkdir()
    meta = storage.metadata_dir / "zero.json"
    meta.write_text("{}", encoding="utf-8")
    deleted_dry = cleanup.delete_files([zero_file], storage, logger, dry_run=True)
    assert deleted_dry == 1
    assert zero_file.exists() is True
    assert meta.exists() is True

    # dry-run branch: metadata file does not exist
    deleted_dry_no_meta = cleanup.delete_files([nonzero_file], storage, logger, dry_run=True)
    assert deleted_dry_no_meta == 1
    assert nonzero_file.exists() is True

    deleted = cleanup.delete_files([zero_file], storage, logger, dry_run=False)
    assert deleted == 1
    assert zero_file.exists() is False

    # delete branch: metadata file does not exist
    deleted_no_meta = cleanup.delete_files([nonzero_file], storage, logger, dry_run=False)
    assert deleted_no_meta == 1
    assert nonzero_file.exists() is False

    bad_file = data_dir / "bad.csv"
    bad_file.write_bytes(b"0")

    def raise_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "bad.csv":
            raise OSError("unlink-failed")
        Path.unlink(path)

    monkeypatch.setattr(Path, "unlink", raise_unlink)
    cleanup.delete_files([bad_file], storage, logger, dry_run=False)
    assert any("削除エラー bad.csv" in r.message for r in caplog.records)

    missing_dir = tmp_path / "missing-dir"
    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(missing_dir)])
    assert cleanup.main() == 1

    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(data_dir)])
    monkeypatch.setattr(cleanup, "StorageManager", lambda *_args, **_kwargs: storage)
    monkeypatch.setattr(cleanup, "find_all_zero_files", lambda *_args, **_kwargs: [])
    assert cleanup.main() == 0

    keep_file = data_dir / "keep.csv"
    keep_file.write_bytes(b"0")
    monkeypatch.setattr(cleanup, "find_all_zero_files", lambda *_args, **_kwargs: [keep_file])
    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(data_dir), "--dry-run"])
    assert cleanup.main() == 0

    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(data_dir)])
    monkeypatch.setattr("builtins.input", lambda *_args: "no")
    assert cleanup.main() == 0

    # confirmation branch: user enters yes/y
    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(data_dir)])
    monkeypatch.setattr("builtins.input", lambda *_args: "yes")
    monkeypatch.setattr(cleanup, "delete_files", lambda *_args, **_kwargs: 1)
    assert cleanup.main() == 0

    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(data_dir), "--yes"])
    monkeypatch.setattr(cleanup, "delete_files", lambda *_args, **_kwargs: 3)
    assert cleanup.main() == 0


def test_process_data_save_stats_print_result_and_main_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "raw").mkdir(parents=True)

    process.save_stats(data_dir, {"total": 1})
    stats_file = data_dir / "processed" / "stats.json"
    assert json.loads(stats_file.read_text(encoding="utf-8"))["total"] == 1

    original_open = Path.open

    def fail_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):
        if path.name == "stats.json":
            raise OSError("open-failed")
        return original_open(
            path,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "open", fail_open)
    process.save_stats(data_dir, {"total": 2})
    assert any("処理統計の保存に失敗しました" in r.message for r in caplog.records)

    process.print_result(
        "処理",
        {
            "total": 6,
            "succeeded": 1,
            "failed": 5,
            "errors": [{"file": f"f{i}", "error": "x"} for i in range(6)],
        },
    )

    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(missing_dir)])
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(data_dir), "--dry-run"])
    process.main()

    class FakeProcessor:
        def __init__(self, _base_dir: Path):
            pass

        def process_all(self) -> dict[str, object]:
            return {"total": 1, "succeeded": 1, "failed": 0, "errors": []}

        def process_file(self, _path: Path) -> NormalizationResult:
            return NormalizationResult(success=True, output_files=[Path("ok.csv")])

    monkeypatch.setattr(process, "DataProcessor", FakeProcessor)
    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(data_dir)])
    process.main()

    class FailProcessor(FakeProcessor):
        def process_all(self) -> dict[str, object]:
            return {"total": 2, "succeeded": 1, "failed": 1, "errors": [{"file": "x", "error": "bad"}]}

    monkeypatch.setattr(process, "DataProcessor", FailProcessor)
    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(data_dir)])
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    missing_file = data_dir / "raw" / "missing.csv"
    monkeypatch.setattr(process, "DataProcessor", FakeProcessor)
    monkeypatch.setattr(
        sys,
        "argv",
        ["process-data", "--data-dir", str(data_dir), "--files", str(missing_file)],
    )
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    inside = data_dir / "raw" / "inside.csv"
    outside = tmp_path / "outside.csv"
    inside.write_text("h,v\n1,2\n", encoding="utf-8")
    outside.write_text("h,v\n1,2\n", encoding="utf-8")

    class FileProcessor(FakeProcessor):
        def process_file(self, path: Path) -> NormalizationResult:
            if path.name == "inside.csv":
                return NormalizationResult(success=False, error="failed")
            return NormalizationResult(success=True, output_files=[Path("ok.csv")])

    monkeypatch.setattr(process, "DataProcessor", FileProcessor)
    monkeypatch.setattr(
        sys,
        "argv",
        ["process-data", "--data-dir", str(data_dir), "--files", str(inside), str(outside)],
    )
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    class FileSuccessProcessor(FakeProcessor):
        def process_file(self, path: Path) -> NormalizationResult:
            assert path.name == "inside.csv"
            return NormalizationResult(success=True, output_files=[Path("ok.csv")])

    monkeypatch.setattr(process, "DataProcessor", FileSuccessProcessor)
    monkeypatch.setattr(
        sys,
        "argv",
        ["process-data", "--data-dir", str(data_dir), "--files", str(inside)],
    )
    process.main()

    class InterruptProcessor(FakeProcessor):
        def process_all(self) -> dict[str, object]:
            raise KeyboardInterrupt

    monkeypatch.setattr(process, "DataProcessor", InterruptProcessor)
    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(data_dir)])
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    class ErrorProcessor(FakeProcessor):
        def process_all(self) -> dict[str, object]:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(process, "DataProcessor", ErrorProcessor)
    monkeypatch.setattr(sys, "argv", ["process-data", "--all", "--data-dir", str(data_dir), "--verbose"])
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 1

    # cover argparse-required branch (no mode args)
    monkeypatch.setattr(sys, "argv", ["process-data", "--data-dir", str(data_dir)])
    with pytest.raises(SystemExit) as exc:
        process.main()
    assert exc.value.code == 2

    # defensive branch: parser returns no mode (all/files both falsy)
    monkeypatch.setattr(process, "DataProcessor", FakeProcessor)
    monkeypatch.setattr(
        process.argparse.ArgumentParser,
        "parse_args",
        lambda _self: SimpleNamespace(
            data_dir=str(data_dir),
            dry_run=False,
            verbose=False,
            all=False,
            files=None,
        ),
    )
    process.main()
