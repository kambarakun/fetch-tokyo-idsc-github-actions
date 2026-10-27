"""Tests for `fetch-data --plan-only`: an offline, read-only listing of the periods a run would fetch."""

import hashlib
import json
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from src.cli import fetch_data as fd
from src.fetchers import periods
from src.fetchers.enhanced_fetcher import EnhancedEpidemicDataFetcher, FetchResult
from src.fetchers.periods import JST

PLAN_ONLY_ARGS = ["fetch-data", "--plan-only", "--config", "config.yml"]


def _write_config(root: Path, *, mode: str | None = "incremental", data_types: list[str] | None = None) -> None:
    lines = ["collection:"]
    if mode is not None:
        lines.append(f"  mode: {mode}")
    lines.append("  start_year: 2025")
    lines.append("  data_types:")
    lines.extend(f"    - {data_type}" for data_type in data_types or ["sentinel_weekly_gender"])
    lines.extend(["storage:", "  base_directory: data/raw", ""])
    (root / "config.yml").write_text("\n".join(lines), encoding="utf-8")


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under `root` mapped to a content hash ("dir" for directories)."""
    return {
        str(path.relative_to(root)): "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
    }


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any HTTP request or socket connection fail the test immediately."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(requests.Session, "request", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch working directory with the clock pinned to Wednesday 2026-10-28 (2026-W44) JST."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(periods, "now_jst", lambda: datetime(2026, 10, 28, 12, 0, tzinfo=JST))
    return tmp_path


def _run_plan(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    monkeypatch.setattr(sys, "argv", [*PLAN_ONLY_ARGS, *extra])
    with pytest.raises(SystemExit) as exc:
        fd.main()
    assert exc.value.code == 0
    return json.loads(capsys.readouterr().out)


def _periods(plan: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(c["data_type"], c["year"], c.get("week", c.get("month"))) for c in plan["candidates"]]


@pytest.mark.usefixtures("offline")
def test_plan_lists_full_mode_candidates_without_network_or_writes(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(scratch)
    before = _snapshot(scratch)

    plan = _run_plan(monkeypatch, capsys, "--mode", "full", "--start-year", "2024", "--end-year", "2024")

    assert plan["kind"] == "fetch-plan"
    assert plan["planned_at_jst"] == "2026-10-28T12:00:00+09:00"
    assert plan["mode"] == "full"
    assert (plan["start_year"], plan["end_year"]) == (2024, 2024)
    assert plan["raw_dir"] == "data/raw"
    assert plan["candidate_count"] == 52
    assert plan["counts"] == {"sentinel_weekly_gender": 52}
    assert plan["candidates"][0] == {
        "data_type": "sentinel_weekly_gender",
        "year": 2024,
        "week": 1,
        "file": "sentinel_weekly_gender_2024_01.csv",
        "exists_locally": False,
    }
    assert plan["candidates"][-1]["file"] == "sentinel_weekly_gender_2024_52.csv"
    assert plan["not_verified"]
    assert not {"successful", "failed", "total_files"} & plan.keys()
    # The missing data directory is planned as empty and not created; no stats or log files appear
    assert _snapshot(scratch) == before


@pytest.mark.usefixtures("offline")
def test_plan_builds_neither_storage_manager_nor_http_session(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(scratch)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("StorageManager or HTTP session built")

    monkeypatch.setattr(fd, "StorageManager", refuse)
    monkeypatch.setattr(requests.Session, "__init__", refuse)

    plan = _run_plan(monkeypatch, capsys, "--start-year", "2026", "--target-weeks", "1")

    assert _periods(plan) == [("sentinel_weekly_gender", 2026, 1)]


@pytest.mark.usefixtures("offline")
def test_incremental_plan_skips_existing_files_and_leaves_them_untouched(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(scratch, mode="incremental")
    raw = scratch / "data" / "raw"
    (raw / ".metadata").mkdir(parents=True)
    for week in (1, 3):
        (raw / f"sentinel_weekly_gender_2026_{week:02d}.csv").write_bytes(b"\x83e\x83X\x83g,1\n")
    before = _snapshot(scratch)

    incremental = _run_plan(monkeypatch, capsys, "--start-year", "2026", "--target-weeks", "1,2,3,4")
    full = _run_plan(monkeypatch, capsys, "--mode", "full", "--start-year", "2026", "--target-weeks", "1,2,3,4")

    assert incremental["mode"] == "incremental"
    assert _periods(incremental) == [("sentinel_weekly_gender", 2026, 2), ("sentinel_weekly_gender", 2026, 4)]
    assert [c["exists_locally"] for c in full["candidates"]] == [True, False, True, False]
    assert _snapshot(scratch) == before


@pytest.mark.usefixtures("offline")
def test_plan_mode_follows_cli_then_config_precedence(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(scratch, mode="force")

    from_config = _run_plan(monkeypatch, capsys, "--target-weeks", "1")
    from_cli = _run_plan(monkeypatch, capsys, "--mode", "incremental", "--target-weeks", "1")
    from_legacy_flag = _run_plan(monkeypatch, capsys, "--skip-existing", "--target-weeks", "1")

    assert (from_config["mode"], from_cli["mode"], from_legacy_flag["mode"]) == ("force", "incremental", "incremental")
    # start_year comes from the config, end_year from the pinned JST clock
    assert (from_config["start_year"], from_config["end_year"]) == (2025, 2026)


@pytest.mark.usefixtures("offline")
def test_plan_suppresses_future_periods_and_respects_iso_and_month_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    # Friday 2027-01-01 JST still belongs to ISO week 2026-W53, but already to calendar month 2027-01
    monkeypatch.setattr(periods, "now_jst", lambda: datetime(2027, 1, 1, 9, 0, tzinfo=JST))
    _write_config(tmp_path, mode="full", data_types=["sentinel_weekly_gender", "sentinel_monthly_age"])

    plan = _run_plan(monkeypatch, capsys, "--start-year", "2026", "--end-year", "2027")

    weekly = [p for p in _periods(plan) if p[0] == "sentinel_weekly_gender"]
    monthly = [p for p in _periods(plan) if p[0] == "sentinel_monthly_age"]
    assert weekly[-1] == ("sentinel_weekly_gender", 2026, 53)
    assert len(weekly) == 53
    assert monthly[-2:] == [("sentinel_monthly_age", 2026, 12), ("sentinel_monthly_age", 2027, 1)]
    assert plan["counts"] == {"sentinel_weekly_gender": 53, "sentinel_monthly_age": 13}
    assert plan["candidates"][-1]["file"] == "sentinel_monthly_age_2027_01.csv"
    assert "week" not in plan["candidates"][-1]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--dry-run"], "--plan-only と --dry-run は同時に指定できません"),
        (["--log-file", "logs/fetch.log"], "--plan-only と --log-file は同時に指定できません"),
    ],
    ids=["dry-run", "log-file"],
)
@pytest.mark.usefixtures("offline")
def test_plan_rejects_conflicting_options_before_creating_files(
    scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    _write_config(scratch)
    before = _snapshot(scratch)
    monkeypatch.setattr(sys, "argv", [*PLAN_ONLY_ARGS, *extra])

    with pytest.raises(SystemExit) as exc:
        fd.main()

    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert _snapshot(scratch) == before


@pytest.mark.parametrize(
    "extra",
    [
        ["--data-types", "sentinel_weekly_gender,unknown_type"],
        ["--mode", "incremental", "--target-weeks", "54"],
    ],
    ids=["unknown-data-type", "week-out-of-range"],
)
@pytest.mark.usefixtures("offline")
def test_plan_fails_on_invalid_targets_without_output(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    _write_config(scratch)
    before = _snapshot(scratch)
    monkeypatch.setattr(sys, "argv", [*PLAN_ONLY_ARGS, *extra])

    with pytest.raises(SystemExit) as exc:
        fd.main()

    assert exc.value.code == 1
    assert capsys.readouterr().out == ""
    assert _snapshot(scratch) == before


@pytest.mark.parametrize(
    ("mode", "extra"),
    [
        ("incremental", ["--start-year", "2026", "--target-weeks", "1,2,3", "--target-months", "8,9"]),
        ("full", ["--start-year", "2025", "--end-year", "2025", "--target-weeks", "52,53", "--target-months", "12"]),
        ("force", ["--start-year", "2026", "--target-weeks", "44,45", "--target-months", "10,11"]),
    ],
)
def test_plan_candidates_match_the_requests_of_a_real_run(
    scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    extra: list[str],
) -> None:
    _write_config(scratch, mode=mode, data_types=["sentinel_weekly_age", "sentinel_monthly_gender"])
    raw = scratch / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "sentinel_weekly_age_2026_02.csv").write_bytes(b"x\n")
    (raw / "sentinel_monthly_gender_2026_09.csv").write_bytes(b"x\n")
    requested: list[tuple[str, int, int]] = []

    def fake_fetch(_self: Any, _method: Any, **params: Any) -> FetchResult:
        requested.append((params["data_type"], int(params["start_year"]), int(params["start_sub_period"])))
        return FetchResult(success=False, error=RuntimeError("offline test"))

    monkeypatch.setattr(fd.EnhancedEpidemicDataFetcher, "fetch_with_retry", fake_fetch)
    monkeypatch.setattr(fd.time, "sleep", lambda _seconds: None)

    plan = _run_plan(monkeypatch, capsys, *extra)
    monkeypatch.setattr(sys, "argv", ["fetch-data", "--config", "config.yml", *extra])
    with pytest.raises(SystemExit):
        fd.main()

    assert requested
    assert _periods(plan) == requested


def test_ci_fetch_smoke_runs_only_the_offline_plan() -> None:
    workflow_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "test.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    runs = [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]

    fetch_runs = [run for run in runs if "fetch-data" in run]

    assert len(fetch_runs) == 1
    assert "--plan-only" in fetch_runs[0]
    assert "--dry-run" not in fetch_runs[0]
    assert "sys.exit(" in fetch_runs[0]  # the JSON content is checked, not only the exit code


def test_plan_known_data_types_are_the_fetchable_ones() -> None:
    fetcher = EnhancedEpidemicDataFetcher()

    assert set(EnhancedEpidemicDataFetcher.REPORT_TYPES) == set(fetcher.fetch_methods)
