"""Workflow inputs reach `run:` only through validated env values (issue #733).

The `Prepare` steps are rendered the way GitHub Actions renders them (`${{ inputs.* }}` is
substituted into the text before bash sees it) and then executed, so an injection that works
against the YAML would show up here as `INJECTED` on stdout.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
COMMON_WORKFLOW = WORKFLOW_DIRECTORY / "_fetch-data-common.yml"
PROCESS_WORKFLOW = WORKFLOW_DIRECTORY / "process-data.yml"
OWNED_WORKFLOWS = ["_fetch-data-common.yml", "fetch-data.yml", "fetch-data-daily.yml", "process-data.yml"]
UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{\s*(inputs|github\.event\.inputs|env|steps|needs)\.")
INPUT_EXPRESSION = re.compile(r"\$\{\{\s*(?:github\.event\.)?inputs\.([a-z_]+)\s*\}\}")
INVALID_INPUT = "::error title=Invalid workflow input::"


def load_workflow(path: Path) -> dict[Any, Any]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def find_step(path: Path, job_name: str, step_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = load_workflow(path)["jobs"][job_name]
    for step in job["steps"]:
        if step.get("name") == step_name:
            return job, step
    raise AssertionError(f"missing step: {step_name}")


def render(text: str, inputs: dict[str, str]) -> str:
    """Substitute `${{ inputs.x }}` like the Actions runner: textually, before the shell runs."""
    return INPUT_EXPRESSION.sub(lambda match: inputs[match.group(1)], text)


def common_inputs(**overrides: str) -> dict[str, str]:
    """Defaults of `_fetch-data-common.yml`, rendered as Actions renders them (booleans as true/false)."""
    declared = load_workflow(COMMON_WORKFLOW)[True]["workflow_call"]["inputs"]
    inputs: dict[str, str] = {}
    for name, spec in declared.items():
        default = str(spec.get("default", ""))
        inputs[name] = default.lower() if spec["type"] == "boolean" else default
    inputs.update(workflow_type="daily", workflow_display_name="毎日簡易チェック")
    inputs.update(overrides)
    return inputs


def run_step(
    tmp_path: Path, path: Path, job_name: str, step_name: str, inputs: dict[str, str], **extra_env: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    job, step = find_step(path, job_name, step_name)
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "GITHUB_ENV": str(github_env),
        **{key: render(str(value), inputs) for key, value in job.get("env", {}).items()},
        **{key: render(str(value), inputs) for key, value in step.get("env", {}).items()},
        **extra_env,
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", render(step["run"], inputs)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, github_env.read_text(encoding="utf-8")


CALENDAR = {"CURRENT_YEAR": "2026", "PREVIOUS_YEAR": "2025", "CURRENT_MONTH": "09", "CURRENT_WEEK": "39"}


def run_prepare(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    result, github_env = run_step(
        tmp_path, COMMON_WORKFLOW, "fetch-data", "Prepare fetch parameters", common_inputs(**overrides), **CALENDAR
    )
    written = dict(line.split("=", 1) for line in github_env.splitlines())
    return result, written


def run_steps(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job.get("steps", []) if "run" in step]


@pytest.mark.parametrize("workflow_name", OWNED_WORKFLOWS)
def test_owned_workflow_run_blocks_do_not_expand_untrusted_expressions(workflow_name: str) -> None:
    workflow = load_workflow(WORKFLOW_DIRECTORY / workflow_name)

    offenders = [
        line.strip()
        for job in workflow["jobs"].values()
        for script in run_steps(job)
        for line in script.splitlines()
        if UNTRUSTED_EXPRESSION.search(line)
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("workflow_type", "field", "payload"),
    [
        pytest.param("manual", "start_year", '2025"; echo "INJECTED"; : "', id="quote-breakout"),
        pytest.param("manual", "end_year", "$(echo INJECTED)x", id="command-substitution"),
        pytest.param("daily", "data_types", "$(echo INJECTED)x", id="data-types-substitution"),
        pytest.param("daily", "target_weeks", "10\nBASH_ENV=/tmp/x", id="newline-env-injection"),
        pytest.param("daily", "target_months", '01"; echo "INJECTED"; : "', id="months-breakout"),
        pytest.param("manual", "mode", "full; id", id="mode-command-list"),
        pytest.param("manual", "dry_run", 'false"; echo "INJECTED"; : "', id="boolean-breakout"),
    ],
)
def test_prepare_step_rejects_injection_payloads(tmp_path: Path, workflow_type: str, field: str, payload: str) -> None:
    result, written = run_prepare(tmp_path, workflow_type=workflow_type, **{field: payload})

    assert result.returncode != 0
    assert "INJECTED" not in result.stdout + result.stderr
    assert f"{INVALID_INPUT}{field}" in result.stdout
    assert written == {}


@pytest.mark.parametrize(
    ("target_weeks", "target_months"),
    [
        pytest.param("52,01,02", "12,01", id="year-boundary"),
        pytest.param("08,09", "09", id="leading-zero-is-not-octal"),
        pytest.param("1,53", "1,12", id="range-limits"),
    ],
)
def test_daily_target_periods_are_accepted_and_exported(tmp_path: Path, target_weeks: str, target_months: str) -> None:
    result, written = run_prepare(
        tmp_path, start_year="2025", mode="incremental", target_weeks=target_weeks, target_months=target_months
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert written == {
        "START_YEAR": "2025",
        "END_YEAR": "2026",
        "MODE": "incremental",
        "DATA_TYPES": "",
        "TARGET_WEEKS": target_weeks,
        "TARGET_MONTHS": target_months,
        "CHECK_PREVIOUS_YEAR": "false",
        "DRY_RUN": "false",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("target_weeks", "0", id="week-0"),
        pytest.param("target_weeks", "54", id="week-54"),
        pytest.param("target_weeks", "60", id="week-60"),
        pytest.param("target_weeks", "10,", id="trailing-comma"),
        pytest.param("target_weeks", "100", id="three-digits"),
        pytest.param("target_months", "13", id="month-13"),
        pytest.param("target_months", "00", id="month-00"),
        pytest.param("start_year", "26", id="short-year"),
        pytest.param("data_types", "Sentinel", id="uppercase-data-type"),
        pytest.param("workflow_type", "hourly", id="unknown-workflow-type"),
    ],
)
def test_prepare_step_rejects_out_of_range_or_malformed_values(tmp_path: Path, field: str, value: str) -> None:
    result, written = run_prepare(tmp_path, **{field: value})

    assert result.returncode != 0
    assert f"{INVALID_INPUT}{field}" in result.stdout
    assert written == {}


@pytest.mark.parametrize(
    ("mode", "data_types"),
    [
        pytest.param("full", "sentinel_weekly_age", id="full-ignores-repair"),
        pytest.param("incremental", "sentinel_weekly_age", id="incremental-ignores-repair"),
        pytest.param("force", "", id="monthly-types-not-narrowed"),
    ],
)
def test_manual_period_repair_requires_force_mode_and_data_types(tmp_path: Path, mode: str, data_types: str) -> None:
    result, written = run_prepare(tmp_path, workflow_type="manual", mode=mode, data_types=data_types, target_weeks="10")

    assert result.returncode != 0
    assert INVALID_INPUT in result.stdout
    assert written == {}


def test_manual_single_week_repair_is_exported(tmp_path: Path) -> None:
    result, written = run_prepare(
        tmp_path,
        workflow_type="manual",
        mode="force",
        data_types="sentinel_weekly_age",
        start_year="2024",
        end_year="2024",
        target_weeks="10",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert written["MODE"] == "force"
    assert written["DATA_TYPES"] == "sentinel_weekly_age"
    assert written["TARGET_WEEKS"] == "10"
    assert written["TARGET_MONTHS"] == ""
    assert (written["START_YEAR"], written["END_YEAR"]) == ("2024", "2024")


def test_manual_defaults_to_the_current_year(tmp_path: Path) -> None:
    result, written = run_prepare(tmp_path, workflow_type="manual")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (written["START_YEAR"], written["END_YEAR"], written["MODE"]) == ("2026", "2026", "full")


@pytest.mark.parametrize(
    ("check_previous_year", "month", "expected_start"),
    [
        pytest.param("false", "09", "2026", id="current-year"),
        pytest.param("true", "09", "2025", id="previous-year-requested"),
        pytest.param("false", "01", "2025", id="january"),
    ],
)
def test_weekly_start_year_follows_previous_year_rule(
    tmp_path: Path, check_previous_year: str, month: str, expected_start: str
) -> None:
    result, github_env = run_step(
        tmp_path,
        COMMON_WORKFLOW,
        "fetch-data",
        "Prepare fetch parameters",
        common_inputs(workflow_type="weekly", mode="force", check_previous_year=check_previous_year),
        **{**CALENDAR, "CURRENT_MONTH": month},
    )
    written = dict(line.split("=", 1) for line in github_env.splitlines())

    assert result.returncode == 0, result.stdout + result.stderr
    assert (written["START_YEAR"], written["END_YEAR"]) == (expected_start, "2026")


FAKE_UV = """\
import json, os, sys
with open(os.environ["FAKE_UV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def test_fetch_step_passes_the_validated_values_to_the_cli(tmp_path: Path) -> None:
    _, prepared = run_prepare(
        tmp_path,
        workflow_type="manual",
        mode="force",
        data_types="sentinel_weekly_age,notifiable_weekly",
        start_year="2024",
        end_year="2024",
        target_weeks="10",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "data" / "logs").mkdir(parents=True)
    uv_log = tmp_path / "uv.jsonl"

    result, github_env = run_step(
        tmp_path,
        COMMON_WORKFLOW,
        "fetch-data",
        "Fetch epidemic data",
        common_inputs(workflow_type="manual"),
        **prepared,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        FAKE_UV_LOG=str(uv_log),
        FETCH_TIMESTAMP="20260920_000000",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    [call] = [json.loads(line) for line in uv_log.read_text(encoding="utf-8").splitlines()]
    assert call == [
        "run", "--locked", "fetch-data",
        "--start-year", "2024", "--end-year", "2024", "--mode", "force",
        "--data-types", "sentinel_weekly_age,notifiable_weekly",
        "--target-weeks", "10",
        "--log-file", "data/logs/fetch_20260920_000000.log",
    ]  # fmt: skip
    assert "FETCH_STATUS=success" in github_env.splitlines()


def test_fetch_data_dispatch_forwards_target_periods() -> None:
    workflow = load_workflow(WORKFLOW_DIRECTORY / "fetch-data.yml")
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    forwarded = workflow["jobs"]["fetch-data"]["with"]

    assert len(inputs) == 10
    for name in ("target_weeks", "target_months"):
        assert inputs[name]["required"] is False
        assert inputs[name]["default"] == ""
        assert "mode=force" in inputs[name]["description"]
        assert forwarded[name] == f"${{{{ inputs.{name} || '' }}}}"


def test_daily_prepare_job_has_no_token_permissions() -> None:
    workflow = load_workflow(WORKFLOW_DIRECTORY / "fetch-data-daily.yml")

    assert workflow["jobs"]["prepare"]["permissions"] == {}


def process_inputs(**overrides: str) -> dict[str, str]:
    inputs = {"target_files": "", "dry_run": "false", "verify_output": "false", "auto_merge": "false"}
    inputs.update(overrides)
    return inputs


@pytest.mark.parametrize(
    ("field", "payload"),
    [
        pytest.param("target_files", 'a.csv"; echo "INJECTED"; : "', id="quote-breakout"),
        pytest.param("target_files", "../x.csv", id="parent-directory"),
        pytest.param("target_files", "data/raw/../x.csv", id="traversal-inside-raw"),
        pytest.param("target_files", "data/raw/a.csv,data/processed/b.csv", id="outside-raw"),
        pytest.param("target_files", "data/raw/a.csv\nBASH_ENV=/tmp/x", id="newline-env-injection"),
        pytest.param("verify_output", 'true"; echo "INJECTED"; : "', id="boolean-breakout"),
    ],
)
def test_process_prepare_step_rejects_malformed_input(tmp_path: Path, field: str, payload: str) -> None:
    result, github_env = run_step(
        tmp_path, PROCESS_WORKFLOW, "process-data", "Prepare processing parameters", process_inputs(**{field: payload})
    )

    assert result.returncode != 0
    assert "INJECTED" not in result.stdout + result.stderr
    assert f"{INVALID_INPUT}{field}" in result.stdout
    assert github_env == ""


def test_process_prepare_step_exports_a_valid_target_list(tmp_path: Path) -> None:
    targets = "data/raw/notifiable_weekly_2025_01.csv,data/raw/sentinel_monthly_age-x_2025_12.csv"

    result, github_env = run_step(
        tmp_path,
        PROCESS_WORKFLOW,
        "process-data",
        "Prepare processing parameters",
        process_inputs(target_files=targets, verify_output="true", dry_run=""),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    written = dict(line.split("=", 1) for line in github_env.splitlines())
    assert written == {
        "TARGET_FILES": targets,
        "DRY_RUN": "false",
        "VERIFY_OUTPUT": "true",
        "AUTO_MERGE": "false",
        "VALIDATION_STATUS": "not_run",
    }
