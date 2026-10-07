"""Integration tests for the manual process-data workflow (issue #774).

The tests execute the real `run:` blocks of `.github/workflows/process-data.yml` in order,
emulating the GitHub Actions step contract that matters here: `if:` conditions, `$GITHUB_ENV`
propagation and the job conclusion. `uv` and `scripts/create_pr.sh` are replaced by fakes in an
isolated temporary repository, so no committed data is touched and no data source is contacted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.cli import process_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "process-data.yml"
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "auto_merge_gate.sh"
EXPRESSION = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
CONDITION_TERM = re.compile(r"^env\.([A-Z_]+)\s*(==|!=)\s*'([^']*)'$")
OLD_STATS = {"total": 987, "succeeded": 987, "failed": 0, "errors": []}
FRESH_STATS = {"total": 2, "succeeded": 2, "failed": 0, "skipped": 0, "errors": []}

FAKE_UV = """\
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_UV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
if args[:1] in (["sync"], ["python"]):
    sys.exit(0)
assert args[:2] == ["run", "--locked"], args
command, rest = args[2], args[3:]

if command == "process-data":
    if "--dry-run" in rest:
        # Delegate to the real CLI so the dry-run contract is exercised end to end.
        env = dict(os.environ, PYTHONPATH=os.environ["FAKE_PROJECT_ROOT"])
        sys.exit(subprocess.run([sys.executable, "-m", "src.cli.process_data", *rest], env=env).returncode)
    mode = os.environ.get("FAKE_PROCESS_MODE", "ok")
    if mode == "fail":
        sys.exit(1)
    Path("data/processed/normalized_fake.csv").write_text("a,b\\n1,2\\n", encoding="utf-8")
    stats = Path("data/processed/stats.json")
    if mode == "ok":
        stats.write_text(os.environ["FAKE_FRESH_STATS"], encoding="utf-8")
    elif mode == "raw_stats":
        stats.write_text(os.environ["FAKE_RAW_STATS"], encoding="utf-8")
    sys.exit(0)

if command == "validate-data":
    output = Path(rest[rest.index("--output") + 1])
    mode = os.environ.get("FAKE_VALIDATE_MODE", "pass")
    reports = {
        "pass": (0, {"total_files": 2, "valid_files": 2, "invalid_files": 0, "has_errors": False}),
        "fail": (1, {"total_files": 2, "valid_files": 1, "invalid_files": 1, "has_errors": True}),
        "empty": (0, {"total_files": 0, "valid_files": 0, "invalid_files": 0, "has_errors": False}),
        "zero_exit_with_errors": (0, {"total_files": 2, "valid_files": 1, "invalid_files": 1, "has_errors": True}),
        "nonzero_exit_without_errors": (1, {"total_files": 2, "valid_files": 2, "invalid_files": 0, "has_errors": False}),
        "invalid_without_has_errors": (0, {"total_files": 2, "valid_files": 1, "invalid_files": 1, "has_errors": False}),
    }
    if mode == "crash":
        sys.exit(2)
    if mode == "missing_report":
        sys.exit(0)
    if mode == "corrupt_report":
        output.write_text("{not json", encoding="utf-8")
        sys.exit(0)
    if mode == "incomplete_report":
        output.write_text(json.dumps({"summary": {"total_files": 2, "has_errors": False}}), encoding="utf-8")
        sys.exit(0)
    if mode in ("malformed_entry", "errors_not_array"):
        summary = {"total_files": 2, "valid_files": 1, "invalid_files": 1, "has_errors": True}
        bad = "garbage" if mode == "malformed_entry" else {"file": "x.csv", "valid": False, "errors": "bad"}
        results = [{"file": "data/processed/normalized_ok.csv", "valid": True, "errors": [], "warnings": []}, bad]
        output.write_text(json.dumps({"summary": summary, "results": results}), encoding="utf-8")
        sys.exit(1)
    if mode == "results_count_mismatch":
        summary = {"total_files": 2, "valid_files": 2, "invalid_files": 0, "has_errors": False}
        results = [{"file": "data/processed/normalized_ok.csv", "valid": True, "errors": [], "warnings": []}]
        output.write_text(json.dumps({"summary": summary, "results": results}), encoding="utf-8")
        sys.exit(0)
    code, summary = reports[mode]
    results = [
        {"file": f"data/processed/normalized_ok_{index}.csv", "valid": True, "errors": [], "warnings": ["w"]}
        for index in range(summary["valid_files"])
    ]
    results += [
        {"file": "data/processed/normalized_fake.csv", "valid": False, "errors": ["bad row"], "warnings": []}
        for _ in range(summary["invalid_files"])
    ]
    output.write_text(json.dumps({"summary": summary, "results": results}), encoding="utf-8")
    sys.exit(code)

sys.exit(f"unexpected uv invocation: {args}")
"""

FAKE_CREATE_PR = """\
#!/usr/bin/env bash
set -eu
WORKFLOW_NAME="$1"
# shellcheck disable=SC1090
source "$FAKE_GATE_SCRIPT"
evaluate_auto_merge_gate
{
  echo "WORKFLOW_NAME=$WORKFLOW_NAME"
  echo "PROCESS_RESULT=${PROCESS_RESULT:-}"
  echo "VERIFY_OUTPUT=${VERIFY_OUTPUT:-}"
  echo "VALIDATION_PASSED=${VALIDATION_PASSED:-}"
  echo "AUTO_MERGE_EFFECTIVE=$AUTO_MERGE_EFFECTIVE"
  echo "AUTO_MERGE_GATE_STATUS=$AUTO_MERGE_GATE_STATUS"
  echo "VALIDATION_GATE_STATUS=$VALIDATION_GATE_STATUS"
} > "$FAKE_PR_RECORD"
echo "PR_URL=https://example.invalid/pull/1" >> "$GITHUB_ENV"
"""


@dataclass
class JobRun:
    """Outcome of one emulated workflow job."""

    root: Path
    env: dict[str, str]
    outcomes: dict[str, str] = field(default_factory=dict)
    logs: dict[str, str] = field(default_factory=dict)
    failed: bool = False

    @property
    def summary(self) -> str:
        return (self.root / "step-summary.md").read_text(encoding="utf-8")

    @property
    def uv_calls(self) -> list[list[str]]:
        log = self.root / "uv-calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @property
    def committed_validation_report(self) -> dict[str, Any]:
        reports = sorted((self.root / "data" / "logs").glob("validation_report_*.json"))
        assert len(reports) == 1, reports
        report: dict[str, Any] = json.loads(reports[0].read_text(encoding="utf-8"))
        return report

    @property
    def pr_record(self) -> dict[str, str] | None:
        record = self.root / "pr-record.env"
        if not record.exists():
            return None
        return dict(line.split("=", 1) for line in record.read_text(encoding="utf-8").splitlines())


def _evaluate_expression(expression: str, env: dict[str, str], inputs: dict[str, str]) -> str:
    if expression.startswith("env."):
        return env.get(expression[4:], "")
    if expression.startswith("github.event.inputs."):
        return inputs.get(expression[len("github.event.inputs.") :], "")
    if expression.startswith("secrets."):
        return "fake-token"
    fixed = {
        "github.run_id": "4242",
        "github.server_url": "https://github.invalid",
        "github.repository": "owner/repo",
    }
    if expression in fixed:
        return fixed[expression]
    raise AssertionError(f"unsupported expression in workflow: {expression}")


def _substitute(text: str, env: dict[str, str], inputs: dict[str, str]) -> str:
    return EXPRESSION.sub(lambda match: _evaluate_expression(match.group(1), env, inputs), text)


def _condition_holds(condition: str | None, env: dict[str, str], job_failed: bool) -> bool:
    if condition is None:
        return not job_failed
    terms = [term.strip() for term in condition.split("&&")]
    if not any(term in {"always()", "failure()"} for term in terms) and job_failed:
        return False
    for term in terms:
        if term == "always()":
            continue
        if term == "failure()":
            if not job_failed:
                return False
            continue
        match = CONDITION_TERM.match(term)
        assert match is not None, f"unsupported condition term: {term}"
        name, operator, value = match.groups()
        if (env.get(name, "") == value) != (operator == "=="):
            return False
    return True


def _read_github_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        assert "<<" not in line, "multi-line GITHUB_ENV values are not emulated"
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _prepare_repository(root: Path, old_stats: dict[str, Any] | None) -> None:
    (root / "data" / "raw").mkdir(parents=True)
    (root / "data" / "processed").mkdir()
    (root / "data" / "logs").mkdir()
    (root / "data" / "raw" / "notifiable_weekly_2025_01.csv").write_bytes("疾病,件数\n".encode("shift_jis"))
    if old_stats is not None:
        (root / "data" / "processed" / "stats.json").write_text(json.dumps(old_stats), encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "create_pr.sh").write_text(FAKE_CREATE_PR, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fixture")


def run_workflow(
    tmp_path: Path,
    inputs: dict[str, str],
    *,
    old_stats: dict[str, Any] | None = None,
    process_mode: str = "ok",
    validate_mode: str = "pass",
    raw_stats: str = "",
) -> JobRun:
    """Run every `run:` step of the process-data job against an isolated repository."""
    root = tmp_path / "repo"
    root.mkdir()
    _prepare_repository(root, old_stats)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    # The failure-notification step calls `gh`; keep it offline.
    fake_gh = bin_dir / "gh"
    fake_gh.write_text('#!/usr/bin/env bash\necho "gh $*" >> "$FAKE_GH_LOG"\n', encoding="utf-8")
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)

    github_env = tmp_path / "github-env"
    github_env.touch()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    base_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
        "GITHUB_ENV": str(github_env),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STEP_SUMMARY": str(root / "step-summary.md"),
        "GITHUB_SHA": "0" * 40,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "tester",
        "GITHUB_REPOSITORY": "owner/repo",
        "FAKE_UV_LOG": str(root / "uv-calls.jsonl"),
        "FAKE_GH_LOG": str(root / "gh-calls.log"),
        "FAKE_PROJECT_ROOT": str(PROJECT_ROOT),
        "FAKE_PROCESS_MODE": process_mode,
        "FAKE_VALIDATE_MODE": validate_mode,
        "FAKE_FRESH_STATS": json.dumps(FRESH_STATS),
        "FAKE_RAW_STATS": raw_stats,
        "FAKE_GATE_SCRIPT": str(GATE_SCRIPT),
        "FAKE_PR_RECORD": str(root / "pr-record.env"),
    }
    (root / "step-summary.md").touch()

    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    run = JobRun(root=root, env={})
    for step in workflow["jobs"]["process-data"]["steps"]:
        name = step["name"]
        if not _condition_holds(step.get("if"), run.env, run.failed):
            run.outcomes[name] = "skipped"
            continue
        if "run" not in step:
            run.outcomes[name] = "would_run"
            continue
        step_env = {key: _substitute(str(value), run.env, inputs) for key, value in step.get("env", {}).items()}
        script = _substitute(step["run"], run.env, inputs)
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-c", script],
            cwd=root,
            env={**base_env, **run.env, **step_env},
            capture_output=True,
            text=True,
            check=False,
        )
        run.logs[name] = result.stdout + result.stderr
        run.env.update(_read_github_env(github_env))
        if result.returncode == 0:
            run.outcomes[name] = "success"
        else:
            run.outcomes[name] = "failure"
            run.failed = True
    return run


def inputs_for(
    *, dry_run: bool, verify_output: bool, target_files: str = "", auto_merge: bool = False
) -> dict[str, str]:
    return {
        "target_files": target_files,
        "dry_run": str(dry_run).lower(),
        "verify_output": str(verify_output).lower(),
        "auto_merge": str(auto_merge).lower(),
    }


def test_dry_run_with_missing_target_does_not_claim_processing_or_reuse_old_stats(tmp_path: Path) -> None:
    run = run_workflow(
        tmp_path,
        inputs_for(dry_run=True, verify_output=True, target_files="data/raw/does_not_exist.csv"),
        old_stats=OLD_STATS,
    )

    assert not run.failed
    assert run.env["PROCESS_RESULT"] == "dry_run"
    assert run.env["VALIDATION_STATUS"] == "not_run"
    assert "STATS_TOTAL" not in run.env
    assert run.outcomes["Verify processed data"] == "skipped"
    assert run.outcomes["Create Pull Request"] == "skipped"
    assert run.outcomes["Enforce validation result"] == "skipped"
    assert not any("validate-data" in call for call in run.uv_calls)
    assert json.loads((run.root / "data" / "processed" / "stats.json").read_text(encoding="utf-8")) == OLD_STATS
    assert "987" not in run.summary
    assert "対象ファイルの存在・変換・品質は未確認" in run.summary
    assert "対象ファイルの存在確認・変換・品質検証は行っていません" in run.logs["Process epidemic data"]


def test_real_run_fails_when_fresh_stats_are_missing_even_if_old_stats_exist(tmp_path: Path) -> None:
    run = run_workflow(
        tmp_path,
        inputs_for(dry_run=False, verify_output=True),
        old_stats=OLD_STATS,
        process_mode="no_stats",
    )

    assert run.failed
    assert run.outcomes["Process epidemic data"] == "failure"
    assert run.env["PROCESS_RESULT"] == "failed"
    assert run.outcomes["Verify processed data"] == "skipped"
    assert run.outcomes["Create Pull Request"] == "skipped"
    assert "987" not in run.summary


@pytest.mark.parametrize(
    "raw_stats",
    [
        pytest.param("{not json", id="corrupt-json"),
        pytest.param(json.dumps({"succeeded": 1, "failed": 0, "errors": []}), id="missing-total"),
        pytest.param(json.dumps({"total": "1", "succeeded": 1, "failed": 0, "errors": []}), id="string-count"),
        pytest.param(json.dumps({"total": -1, "succeeded": -1, "failed": 0, "errors": []}), id="negative-count"),
        pytest.param(json.dumps({"total": 3, "succeeded": 1, "failed": 0, "errors": []}), id="inconsistent-counts"),
        pytest.param(json.dumps({"total": 1, "succeeded": 1, "failed": 0}), id="missing-errors"),
        pytest.param(
            json.dumps({"total": 1, "succeeded": 1, "failed": 0, "skipped": False, "errors": []}), id="bool-skip"
        ),
        pytest.param(
            json.dumps({"total": 1, "succeeded": 1, "failed": 0, "skipped": None, "errors": []}), id="null-skip"
        ),
        pytest.param(json.dumps({"total": True, "succeeded": 1, "failed": 0, "errors": []}), id="bool-total"),
        pytest.param(json.dumps([1, 2]), id="not-an-object"),
    ],
)
def test_real_run_fails_on_invalid_fresh_stats(tmp_path: Path, raw_stats: str) -> None:
    run = run_workflow(
        tmp_path,
        inputs_for(dry_run=False, verify_output=False),
        old_stats=OLD_STATS,
        process_mode="raw_stats",
        raw_stats=raw_stats,
    )

    assert run.failed
    assert run.env["PROCESS_RESULT"] == "failed"
    assert run.outcomes["Create Pull Request"] == "skipped"
    assert "STATS_TOTAL" not in run.env


def test_processing_failure_stops_before_validation_and_pr(tmp_path: Path) -> None:
    run = run_workflow(tmp_path, inputs_for(dry_run=False, verify_output=True), process_mode="fail")

    assert run.failed
    assert run.env["PROCESS_RESULT"] == "failed"
    assert run.outcomes["Verify processed data"] == "skipped"
    assert run.outcomes["Create Pull Request"] == "skipped"
    assert "処理: ❌ 失敗" in run.summary


def test_validation_not_requested_succeeds_but_reports_quality_as_unverified(tmp_path: Path) -> None:
    run = run_workflow(tmp_path, inputs_for(dry_run=False, verify_output=False), old_stats=OLD_STATS)

    assert not run.failed
    assert run.env["PROCESS_RESULT"] == "success"
    assert run.env["VALIDATION_STATUS"] == "not_requested"
    assert run.env["STATS_TOTAL"] == "2"
    assert run.outcomes["Verify processed data"] == "skipped"
    assert run.outcomes["Enforce validation result"] == "skipped"
    assert run.pr_record is not None
    assert run.pr_record["VALIDATION_PASSED"] == ""
    assert run.pr_record["VALIDATION_GATE_STATUS"] == "not_requested"
    assert "品質未検証" in run.summary
    assert "987" not in run.summary
    assert "**処理対象**: 2件" in run.summary


def test_validation_pass_sets_validation_passed_and_allows_requested_auto_merge(tmp_path: Path) -> None:
    run = run_workflow(tmp_path, inputs_for(dry_run=False, verify_output=True, auto_merge=True))

    assert not run.failed
    assert run.env["VALIDATION_STATUS"] == "passed"
    assert run.env["VALIDATION_PASSED"] == "true"
    assert run.outcomes["Enforce validation result"] == "success"
    assert run.pr_record is not None
    assert run.pr_record["VALIDATION_PASSED"] == "true"
    assert run.pr_record["AUTO_MERGE_GATE_STATUS"] == "passed"
    assert "品質検証: ✅ 合格" in run.summary
    # Only the summary and invalid files are committed; the full per-file report stays out of the data PR.
    report = run.committed_validation_report
    assert report["validation_status"] == "passed"
    assert report["summary"]["total_files"] == 2
    assert report["invalid_results"] == []
    assert "results" not in report


@pytest.mark.parametrize(
    ("validate_mode", "expected_status"),
    [
        pytest.param("fail", "failed", id="nonzero-with-errors"),
        pytest.param("crash", "error", id="crash-without-report"),
        pytest.param("missing_report", "error", id="zero-exit-without-report"),
        pytest.param("corrupt_report", "error", id="corrupt-report"),
        pytest.param("empty", "error", id="zero-files-validated"),
        pytest.param("zero_exit_with_errors", "error", id="zero-exit-but-report-has-errors"),
        pytest.param("nonzero_exit_without_errors", "error", id="nonzero-exit-but-report-clean"),
        pytest.param("invalid_without_has_errors", "error", id="invalid-files-without-has-errors"),
        pytest.param("incomplete_report", "error", id="incomplete-report"),
        pytest.param("results_count_mismatch", "error", id="results-count-mismatch"),
        pytest.param("malformed_entry", "error", id="non-object-result-entry"),
        pytest.param("errors_not_array", "error", id="result-errors-not-array"),
    ],
)
def test_unpassed_validation_keeps_investigation_pr_then_fails_job(
    tmp_path: Path, validate_mode: str, expected_status: str
) -> None:
    run = run_workflow(
        tmp_path,
        inputs_for(dry_run=False, verify_output=True, auto_merge=True),
        validate_mode=validate_mode,
    )

    assert run.env["PROCESS_RESULT"] == "success"
    assert run.env["VALIDATION_STATUS"] == expected_status
    assert run.env["VALIDATION_PASSED"] == "false"
    assert run.outcomes["Verify processed data"] == "success"
    assert run.outcomes["Create Pull Request"] == "success"
    assert run.pr_record is not None
    assert run.pr_record["VALIDATION_PASSED"] == "false"
    assert run.pr_record["AUTO_MERGE_EFFECTIVE"] == "false"
    assert run.pr_record["AUTO_MERGE_GATE_STATUS"] == "blocked"
    # The processing-error notification must not fire for a quality verdict.
    assert run.outcomes["Create issue on error"] == "skipped"
    assert run.outcomes["Generate summary"] == "success"
    assert run.outcomes["Enforce validation result"] == "failure"
    assert run.failed
    assert f"品質検証: {'❌ 不合格' if expected_status == 'failed' else '⚠️ 検証不能'}" in run.summary
    report = run.committed_validation_report
    assert report["validation_status"] == expected_status
    if validate_mode == "fail":
        assert [entry["file"] for entry in report["invalid_results"]] == ["data/processed/normalized_fake.csv"]


def test_cli_dry_run_skips_target_checks_and_leaves_stats_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "data"
    stats_file = data_dir / "processed" / "stats.json"
    stats_file.parent.mkdir(parents=True)
    stats_file.write_text(json.dumps(OLD_STATS), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["process-data", "--data-dir", str(data_dir), "--files", str(tmp_path / "missing.csv"), "--dry-run"],
    )

    with caplog.at_level(logging.INFO):
        process_data.main()

    assert json.loads(stats_file.read_text(encoding="utf-8")) == OLD_STATS
    assert "対象ファイルの存在確認・変換・品質検証は行っていません" in caplog.text
    assert "ドライラン完了" not in caplog.text


# --- Runbook (docs/manual-data-processing.md) -----------------------------------------------

RUNBOOK_PATH = PROJECT_ROOT / "docs" / "manual-data-processing.md"
RUNBOOK_SHELLS = [shell for shell in ("bash", "zsh") if shutil.which(shell)]
VALID_TARGETS = "data/raw/notifiable_weekly_2025_01.csv\ndata/raw/sentinel_weekly_age_2025_01.csv\n"

FAKE_RUNBOOK_COMMAND = """\
#!/usr/bin/env bash
# Synthetic git/uv/gh for the runbook tests; behaviour is driven by FAKE_* variables.
name="$(basename "$0")"
echo "$name $*" >> "$FAKE_CALL_LOG"
case "$name $*" in
  "git branch --show-current") echo "${FAKE_BRANCH:-main}" ;;
  "git status --porcelain") printf '%s' "${FAKE_DIRTY:-}"; exit "${FAKE_STATUS_EXIT:-0}" ;;
  "git fetch origin main") exit "${FAKE_FETCH_EXIT:-0}" ;;
  "git merge --ff-only origin/main") exit "${FAKE_MERGE_EXIT:-0}" ;;
  "git rev-parse HEAD") echo "${FAKE_HEAD_SHA:-aaaa}" ;;
  "git rev-parse origin/main") echo "aaaa" ;;
  "uv sync --locked") exit 0 ;;
  "uv run --locked check-data-status --list-needs-processing")
    printf '%s' "${FAKE_TARGETS:-}"
    exit "${FAKE_LIST_EXIT:-0}"
    ;;
  "gh api --paginate repos/{owner}/{repo}/pulls?state=open&per_page=100 "*)
    [ "${FAKE_PR_LIST_EXIT:-0}" -eq 0 ] || exit "$FAKE_PR_LIST_EXIT"
    i=0
    while [ "$i" -lt "${FAKE_OPEN_PRS:-0}" ]; do echo "$((i + 100))"; i=$((i + 1)); done
    ;;
  "gh api --paginate repos/{owner}/{repo}/actions/workflows/process-data.yml/runs?status="*)
    [ "${FAKE_RUN_LIST_EXIT:-0}" -eq 0 ] || exit "$FAKE_RUN_LIST_EXIT"
    case "$*" in
      *"status=${FAKE_RUNNING_STATUS:-in_progress}&"*)
        i=0
        while [ "$i" -lt "${FAKE_RUNNING:-0}" ]; do echo "$((i + 1))"; i=$((i + 1)); done
        ;;
    esac
    ;;
  "git merge-base --is-ancestor cafe HEAD") exit "${FAKE_ANCESTOR_EXIT:-0}" ;;
  "gh api repos/{owner}/{repo}/pulls/789 "*)
    [ "${FAKE_PR_VIEW_EXIT:-0}" -eq 0 ] || exit "$FAKE_PR_VIEW_EXIT"
    echo "${FAKE_PR_INFO-open main data-process-20261008-1 beef}"
    ;;
  "git fetch origin pull/789/head") exit "${FAKE_PR_FETCH_EXIT:-0}" ;;
  "git rev-parse FETCH_HEAD") echo "${FAKE_FETCHED_SHA:-beef}" ;;
  "git diff --no-renames --name-only origin/main...beef")
    printf '%s' "${FAKE_PR_FILES:-}"
    exit "${FAKE_DIFF_EXIT:-0}"
    ;;
  "uv run --locked check-data-status --fail-on-incomplete") echo "complete" > "$FAKE_GATE_RECORD" ;;
  "gh workflow run "*) printf '%s\\n' "$@" > "$FAKE_DISPATCH_RECORD" ;;
  *) echo "unexpected: $name $*" >&2; exit 97 ;;
esac
"""


def runbook_block(marker: str) -> str:
    """Return the fenced shell block that follows `<!-- runbook: <marker> -->` in the runbook."""
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    match = re.search(rf"<!-- runbook: {re.escape(marker)} -->\s*```bash\n(.*?)```", text, re.DOTALL)
    assert match is not None, marker
    return match.group(1)


def run_runbook(
    tmp_path: Path, shell: str, block: str, **fake_env: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "runbook-bin"
    bin_dir.mkdir()
    for name in ("git", "uv", "gh"):
        command = bin_dir / name
        command.write_text(FAKE_RUNBOOK_COMMAND, encoding="utf-8")
        command.chmod(command.stat().st_mode | stat.S_IEXEC)
    dispatch_record = tmp_path / "dispatch.txt"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LANG": "C.UTF-8",
        "FAKE_CALL_LOG": str(tmp_path / "calls.log"),
        "FAKE_DISPATCH_RECORD": str(dispatch_record),
        "FAKE_GATE_RECORD": str(tmp_path / "gate.txt"),
        "FAKE_TARGETS": VALID_TARGETS,
        **fake_env,
    }
    script = block if block.startswith("(") else runbook_block("dispatch-function") + block
    result = subprocess.run([shell, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    return result, dispatch_record


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_apply_dispatches_explicit_inputs_for_a_valid_fresh_list(tmp_path: Path, shell: str) -> None:
    result, dispatch_record = run_runbook(tmp_path, shell, runbook_block("dispatch-apply"))

    assert result.returncode == 0, result.stderr
    assert dispatch_record.read_text(encoding="utf-8").splitlines() == [
        "workflow",
        "run",
        "process-data.yml",
        "--ref",
        "main",
        "-f",
        "target_files=data/raw/notifiable_weekly_2025_01.csv,data/raw/sentinel_weekly_age_2025_01.csv",
        "-f",
        "dry_run=false",
        "-f",
        "verify_output=true",
        "-f",
        "auto_merge=false",
    ]
    assert "STOP" not in result.stderr


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_preview_never_dispatches(tmp_path: Path, shell: str) -> None:
    result, dispatch_record = run_runbook(tmp_path, shell, runbook_block("dispatch-preview"))

    assert result.returncode == 0, result.stderr
    assert "全 2 件" in result.stdout
    assert not dispatch_record.exists()


OVERSIZED_TARGETS = "".join(f"data/raw/{'x' * 130}_{index:03d}.csv\n" for index in range(500))


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
@pytest.mark.parametrize(
    ("block", "fake_env"),
    [
        pytest.param("process_data_dispatch\n", {}, id="mode-omitted"),
        pytest.param("process_data_dispatch dispatch\n", {}, id="unknown-mode"),
        pytest.param(None, {"FAKE_OPEN_PRS": "1"}, id="previous-batch-pr-open"),
        pytest.param(None, {"FAKE_PR_LIST_EXIT": "1"}, id="pr-list-fails"),
        pytest.param(None, {"FAKE_BRANCH": "feature"}, id="not-on-main"),
        pytest.param(None, {"FAKE_DIRTY": " M data/raw/x.csv"}, id="dirty-tree"),
        pytest.param(None, {"FAKE_STATUS_EXIT": "128"}, id="git-status-fails"),
        pytest.param(None, {"FAKE_FETCH_EXIT": "1"}, id="fetch-fails"),
        pytest.param(None, {"FAKE_MERGE_EXIT": "1"}, id="ff-merge-fails"),
        pytest.param(None, {"FAKE_HEAD_SHA": "bbbb"}, id="head-behind-origin-main"),
        pytest.param(None, {"FAKE_LIST_EXIT": "1"}, id="list-generation-fails"),
        pytest.param(None, {"FAKE_TARGETS": ""}, id="empty-list"),
        pytest.param(None, {"FAKE_TARGETS": "README.md\n"}, id="non-raw-path"),
        pytest.param(None, {"FAKE_TARGETS": "data/raw/a.csv,data/raw/b.csv\n"}, id="comma-in-line"),
        pytest.param(None, {"FAKE_TARGETS": "data/raw/a.csv\ndata/raw/a.csv\n"}, id="duplicate-line"),
        pytest.param(None, {"FAKE_TARGETS": OVERSIZED_TARGETS}, id="input-too-large"),
        pytest.param(None, {"FAKE_RUNNING": "1"}, id="previous-run-not-completed"),
        pytest.param(None, {"FAKE_RUNNING": "1", "FAKE_RUNNING_STATUS": "queued"}, id="previous-run-queued"),
        pytest.param(None, {"FAKE_RUNNING": "1", "FAKE_RUNNING_STATUS": "waiting"}, id="previous-run-waiting"),
        pytest.param(None, {"FAKE_RUN_LIST_EXIT": "1"}, id="run-list-fails"),
    ],
)
def test_runbook_apply_stops_without_dispatch_when_a_precondition_fails(
    tmp_path: Path, shell: str, block: str | None, fake_env: dict[str, str]
) -> None:
    result, dispatch_record = run_runbook(tmp_path, shell, block or runbook_block("dispatch-apply"), **fake_env)

    assert result.returncode != 0
    assert "STOP" in result.stderr or "引数" in result.stderr
    assert not dispatch_record.exists()


def test_runbook_shell_blocks_never_put_a_subshell_in_a_condition() -> None:
    # `( set -e; ... ) || x` and `if ! ( ... )` silently disable errexit inside the subshell.
    blocks = re.findall(r"```bash\n(.*?)```", RUNBOOK_PATH.read_text(encoding="utf-8"), re.DOTALL)

    assert blocks
    for block in blocks:
        assert not re.search(r"^\)\s*(\|\||&&)", block, re.MULTILINE), block
        assert not re.search(r"^\s*(if|while|until)\s+!?\s*\(", block, re.MULTILINE), block
        assert not re.search(r"^\s*process_data_dispatch\b.*(\|\||&&)", block, re.MULTILINE), block


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_apply_dispatches_only_the_first_batch_of_a_long_list(tmp_path: Path, shell: str) -> None:
    targets = "".join(f"data/raw/notifiable_weekly_2000_{index:03d}.csv\n" for index in range(501))

    result, dispatch_record = run_runbook(tmp_path, shell, runbook_block("dispatch-apply"), FAKE_TARGETS=targets)

    assert result.returncode == 0, result.stderr
    dispatched = dispatch_record.read_text(encoding="utf-8").splitlines()[6].removeprefix("target_files=").split(",")
    assert dispatched == targets.splitlines()[:500]
    assert "全 501 件" in result.stdout


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_failure_does_not_delete_a_caller_work_directory(tmp_path: Path, shell: str) -> None:
    caller_dir = tmp_path / "caller-work"
    caller_dir.mkdir()
    (caller_dir / "keep.txt").write_text("keep", encoding="utf-8")

    result, dispatch_record = run_runbook(
        tmp_path, shell, runbook_block("dispatch-apply"), FAKE_BRANCH="feature", WORK=str(caller_dir)
    )

    assert result.returncode != 0
    assert (caller_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not dispatch_record.exists()


def post_merge_block() -> str:
    block = runbook_block("post-merge")
    assert block.count('MERGE_COMMIT=""') == 1
    return block.replace('MERGE_COMMIT=""', 'MERGE_COMMIT="cafe"')


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_post_merge_check_runs_the_gate_on_latest_main(tmp_path: Path, shell: str) -> None:
    result, _ = run_runbook(tmp_path, shell, post_merge_block())

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gate.txt").read_text(encoding="utf-8") == "complete\n"


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
@pytest.mark.parametrize(
    ("use_placeholder", "fake_env"),
    [
        pytest.param(True, {}, id="merge-commit-not-set"),
        pytest.param(False, {"FAKE_HEAD_SHA": "bbbb"}, id="local-main-differs-from-origin"),
        pytest.param(False, {"FAKE_ANCESTOR_EXIT": "1"}, id="merge-not-on-main"),
        pytest.param(False, {"FAKE_MERGE_EXIT": "1"}, id="ff-merge-fails"),
    ],
)
def test_runbook_post_merge_check_stops_before_the_gate(
    tmp_path: Path, shell: str, use_placeholder: bool, fake_env: dict[str, str]
) -> None:
    block = runbook_block("post-merge") if use_placeholder else post_merge_block()

    result, _ = run_runbook(tmp_path, shell, block, **fake_env)

    assert result.returncode != 0
    assert not (tmp_path / "gate.txt").exists()


# A normal 500-raw batch touches well over 1,000 files; the diff API rejects anything over 300.
LARGE_PR_FILES = (
    "".join(
        f"data/processed/normalized_notifiable_weekly_2000_{index:03d}.csv\n"
        f"data/processed/.metadata/normalized_notifiable_weekly_2000_{index:03d}.json\n"
        for index in range(600)
    )
    + "data/processed/stats.json\ndata/logs/validation_report_20261008_000000.json\n"
)


def pr_diff_block() -> str:
    block = runbook_block("pr-diff")
    assert block.count('PR_NUMBER=""') == 1
    return block.replace('PR_NUMBER=""', 'PR_NUMBER="789"')


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
def test_runbook_pr_diff_lists_every_file_of_a_large_batch_pr(tmp_path: Path, shell: str) -> None:
    result, _ = run_runbook(tmp_path, shell, pr_diff_block(), FAKE_PR_FILES=LARGE_PR_FILES)

    assert result.returncode == 0, result.stderr
    assert re.search(r"processed CSV: +600$", result.stdout, re.MULTILINE), result.stdout
    assert re.search(r"processed metadata: +600$", result.stdout, re.MULTILINE), result.stdout
    assert re.search(r"stats\.json: +1$", result.stdout, re.MULTILINE), result.stdout
    assert re.search(r"検証レポート: +1$", result.stdout, re.MULTILINE), result.stdout
    assert "想定外のファイルはありません" in result.stdout
    assert "STOP" not in result.stderr
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("shell", RUNBOOK_SHELLS)
@pytest.mark.parametrize(
    ("use_placeholder", "fake_env"),
    [
        pytest.param(True, {}, id="pr-number-not-set"),
        pytest.param(False, {"FAKE_PR_VIEW_EXIT": "1"}, id="pr-lookup-fails"),
        pytest.param(False, {"FAKE_PR_INFO": ""}, id="pr-lookup-empty"),
        pytest.param(False, {"FAKE_PR_INFO": "closed main data-process-20261008-1 beef"}, id="pr-not-open"),
        pytest.param(False, {"FAKE_PR_INFO": "open develop data-process-20261008-1 beef"}, id="base-not-main"),
        pytest.param(False, {"FAKE_PR_INFO": "open main feature-x beef"}, id="not-a-data-process-pr"),
        pytest.param(False, {"FAKE_FETCH_EXIT": "1"}, id="main-fetch-fails"),
        pytest.param(False, {"FAKE_PR_FETCH_EXIT": "1"}, id="pr-fetch-fails"),
        pytest.param(False, {"FAKE_FETCHED_SHA": "dead"}, id="fetched-head-differs"),
        pytest.param(False, {"FAKE_DIFF_EXIT": "128"}, id="diff-fails"),
        pytest.param(False, {}, id="empty-diff"),
        pytest.param(False, {"FAKE_PR_FILES": LARGE_PR_FILES + "data/raw/notifiable_weekly_2000_001.csv\n"}, id="raw"),
        pytest.param(False, {"FAKE_PR_FILES": LARGE_PR_FILES + "src/cli/process_data.py\n"}, id="code"),
    ],
)
def test_runbook_pr_diff_stops_on_a_wrong_target_or_an_incomplete_list(
    tmp_path: Path, shell: str, use_placeholder: bool, fake_env: dict[str, str]
) -> None:
    block = runbook_block("pr-diff") if use_placeholder else pr_diff_block()

    result, _ = run_runbook(tmp_path, shell, block, **fake_env)

    assert result.returncode != 0
    assert "想定外のファイルはありません" not in result.stdout
    assert "unexpected:" not in result.stderr
