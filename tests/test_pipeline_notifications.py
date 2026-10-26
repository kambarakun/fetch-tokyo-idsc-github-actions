"""Pipeline anomalies end up in one deduplicated notification issue (issue #733).

`scripts/notify_pipeline_issue.sh` runs against a stub `gh` that applies the real `--jq` program
with `jq`, so the exact-title lookup is exercised, not assumed. The static checks pin the
workflow wiring around it.
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
NOTIFY_SCRIPT = PROJECT_ROOT / "scripts" / "notify_pipeline_issue.sh"
WORKFLOW_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
COMMON_WORKFLOW = WORKFLOW_DIRECTORY / "_fetch-data-common.yml"
PROCESS_WORKFLOW = WORKFLOW_DIRECTORY / "process-data.yml"
OWNED_WORKFLOWS = [COMMON_WORKFLOW, WORKFLOW_DIRECTORY / "fetch-data.yml", PROCESS_WORKFLOW]
# Labels that exist in the repository (2026-10-27); a label outside this set makes `gh issue create` fail.
REPOSITORY_LABELS = {
    "agent-task", "automated", "bug", "codex", "daily", "daily-update", "data-collection", "data-processing",
    "data-update", "dependencies", "documentation", "duplicate", "enhancement", "error", "github-actions",
    "good first issue", "help wanted", "invalid", "metadata-migration", "pre-commit", "python", "question",
    "tracking", "weekly-check", "weekly-update", "wontfix",
}  # fmt: skip
GH_COMMAND = re.compile(r"(?:^|[\s;&|(])gh\s")

FAKE_GH = """\
import json, os, shutil, subprocess, sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "issue_title": os.environ.get("ISSUE_TITLE")}) + "\\n")
if args[:2] == ["issue", "list"]:
    if os.environ.get("FAKE_GH_LIST_EXIT", "0") != "0":
        sys.exit(int(os.environ["FAKE_GH_LIST_EXIT"]))
    program = args[args.index("--jq") + 1]
    issues = os.environ.get("FAKE_GH_ISSUES", "[]")
    result = subprocess.run(["jq", "-r", program], input=issues, capture_output=True, text=True, check=True)
    sys.stdout.write(result.stdout)
    sys.exit(0)
if args[:2] in (["issue", "create"], ["issue", "comment"]):
    shutil.copyfile(args[args.index("--body-file") + 1], os.environ["FAKE_GH_BODY_OUT"])
    sys.exit(int(os.environ.get("FAKE_GH_EXIT", "0")))
sys.exit(f"unexpected gh call: {args}")
"""


class Notification:
    def __init__(self, result: subprocess.CompletedProcess[str], tmp_path: Path) -> None:
        self.result = result
        log = tmp_path / "gh.jsonl"
        self.calls: list[dict[str, Any]] = (
            [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        )
        body = tmp_path / "body.md"
        self.body = body.read_text(encoding="utf-8") if body.exists() else ""

    @property
    def commands(self) -> list[list[str]]:
        return [call["args"][:2] for call in self.calls]

    def option(self, command: list[str], name: str) -> str:
        [call] = [call["args"] for call in self.calls if call["args"][:2] == command]
        return str(call[call.index(name) + 1])


HEALTHY = {
    "PIPELINE_NAME": "daily",
    "JOB_STATUS": "success",
    "FETCH_STATUS": "success",
    "PROCESS_RESULT": "success",
    "AUTO_MERGE_GATE_STATUS": "passed",
    "AUTO_MERGE_BLOCKERS": "none",
    "PR_URL": "https://github.com/o/r/pull/7",
}


def notify(tmp_path: Path, *, token: bool = True, **env: str) -> Notification:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
    run_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "TMPDIR": str(tmp_path),
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "4242",
        "FAKE_GH_LOG": str(tmp_path / "gh.jsonl"),
        "FAKE_GH_BODY_OUT": str(tmp_path / "body.md"),
        **({"GH_TOKEN": "fake-token"} if token else {}),
        **{**HEALTHY, **env},
    }
    result = subprocess.run(
        ["bash", str(NOTIFY_SCRIPT)], cwd=tmp_path, env=run_env, capture_output=True, text=True, check=False
    )
    return Notification(result, tmp_path)


def test_healthy_run_makes_no_gh_call(tmp_path: Path) -> None:
    notification = notify(tmp_path)

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.calls == []
    assert "No pipeline anomaly" in notification.result.stdout


def test_processing_failure_creates_one_issue_with_existing_labels(tmp_path: Path) -> None:
    notification = notify(tmp_path, PROCESS_RESULT="failed", RAW_CHANGED_COUNT="3")

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.commands == [["issue", "list"], ["issue", "create"]]
    title = notification.option(["issue", "create"], "--title")
    assert title == "🚨 [daily] データパイプライン異常: process_failed"
    labels = notification.option(["issue", "create"], "--label").split(",")
    assert labels == ["bug", "automated", "data-collection"]
    assert set(labels) <= REPOSITORY_LABELS
    assert notification.option(["issue", "create"], "--repo") == "o/r"
    assert "--assignee" not in notification.calls[1]["args"]
    assert notification.calls[0]["issue_title"] == title
    assert "https://github.com/o/r/actions/runs/4242" in notification.body
    assert "`process_failed`" in notification.body


def test_existing_open_issue_with_the_same_title_gets_a_comment(tmp_path: Path) -> None:
    issues = [
        {"number": 41, "title": "🚨 [daily] データパイプライン異常: process_failed (old)"},
        {"number": 42, "title": "🚨 [daily] データパイプライン異常: process_failed"},
        {"number": 43, "title": "🚨 [weekly] データパイプライン異常: process_failed"},
    ]

    notification = notify(tmp_path, PROCESS_RESULT="failed", FAKE_GH_ISSUES=json.dumps(issues))

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.commands == [["issue", "list"], ["issue", "comment"]]
    assert notification.calls[1]["args"][2] == "42"
    assert notification.option(["issue", "list"], "--limit") == "200"
    assert notification.option(["issue", "list"], "--label") == "automated"
    assert notification.option(["issue", "list"], "--state") == "open"


def test_blocked_auto_merge_with_a_pr_is_notified(tmp_path: Path) -> None:
    notification = notify(tmp_path, AUTO_MERGE_GATE_STATUS="blocked", AUTO_MERGE_BLOCKERS="coverage,continuity")

    assert notification.commands == [["issue", "list"], ["issue", "create"]]
    assert notification.option(["issue", "create"], "--title").endswith(": auto_merge_blocked")
    assert "coverage,continuity" in notification.body
    assert "https://github.com/o/r/pull/7" in notification.body


def test_blocked_gate_without_a_pr_is_left_to_the_job_failure(tmp_path: Path) -> None:
    # `Fail when gate is blocked without PR` turns this case into job_failed.
    notification = notify(tmp_path, AUTO_MERGE_GATE_STATUS="blocked", PR_URL="")

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.calls == []


def test_auto_merge_setup_failure_is_notified(tmp_path: Path) -> None:
    notification = notify(tmp_path, AUTO_MERGE_SETUP_FAILED="true")

    assert notification.option(["issue", "create"], "--title").endswith(": auto_merge_setup_failed")


def test_cancellation_alone_is_not_notified(tmp_path: Path) -> None:
    notification = notify(tmp_path, JOB_STATUS="cancelled", FETCH_STATUS="", PROCESS_RESULT="")

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.calls == []


def test_highest_priority_kind_is_the_title_and_all_kinds_are_listed(tmp_path: Path) -> None:
    notification = notify(
        tmp_path, JOB_STATUS="failure", FETCH_STATUS="failed", PROCESS_RESULT="failed", AUTO_MERGE_GATE_STATUS="blocked"
    )

    assert notification.option(["issue", "create"], "--title") == "🚨 [daily] データパイプライン異常: fetch_failed"
    for kind in ("fetch_failed", "process_failed", "job_failed", "auto_merge_blocked"):
        assert f"`{kind}`" in notification.body
    assert "auto_merge_setup_failed" not in notification.body


def test_process_pipeline_uses_the_data_processing_label(tmp_path: Path) -> None:
    notification = notify(tmp_path, PIPELINE_NAME="process", FETCH_STATUS="", JOB_STATUS="failure")

    assert notification.option(["issue", "create"], "--title") == "🚨 [process] データパイプライン異常: job_failed"
    assert notification.option(["issue", "create"], "--label") == "bug,automated,data-processing"


def test_detection_failure_is_described_without_a_missing_process_log(tmp_path: Path) -> None:
    notification = notify(tmp_path, PROCESS_RESULT="failed", RAW_CHANGED_COUNT="unknown")

    assert "scripts/list_changed_raw_files.sh" in notification.body
    assert "処理ログは作られない" in notification.body
    assert "process_*.log" not in notification.body


def test_processing_failure_points_to_the_process_log(tmp_path: Path) -> None:
    notification = notify(tmp_path, PROCESS_RESULT="failed", RAW_CHANGED_COUNT="2")

    assert "list_changed_raw_files.sh" not in notification.body
    assert "process_*.log" in notification.body


def test_gh_create_failure_fails_the_step(tmp_path: Path) -> None:
    notification = notify(tmp_path, PROCESS_RESULT="failed", FAKE_GH_EXIT="1")

    assert notification.result.returncode != 0


def test_gh_list_failure_fails_without_creating_a_duplicate(tmp_path: Path) -> None:
    notification = notify(tmp_path, PROCESS_RESULT="failed", FAKE_GH_LIST_EXIT="1")

    assert notification.result.returncode != 0
    assert notification.commands == [["issue", "list"]]


def test_missing_token_with_an_anomaly_fails_loudly(tmp_path: Path) -> None:
    notification = notify(tmp_path, token=False, PROCESS_RESULT="failed")

    assert notification.result.returncode != 0
    assert "::error title=Pipeline notification failed::" in notification.result.stdout
    assert notification.calls == []


def test_github_token_is_accepted_in_place_of_gh_token(tmp_path: Path) -> None:
    notification = notify(tmp_path, token=False, GITHUB_TOKEN="fake-token", PROCESS_RESULT="failed")

    assert notification.result.returncode == 0, notification.result.stderr
    assert notification.commands == [["issue", "list"], ["issue", "create"]]


def test_unknown_pipeline_name_fails_closed(tmp_path: Path) -> None:
    notification = notify(tmp_path, PIPELINE_NAME="hourly", PROCESS_RESULT="failed")

    assert notification.result.returncode != 0
    assert notification.calls == []


def test_notify_script_parses() -> None:
    subprocess.run(["bash", "-n", str(NOTIFY_SCRIPT)], check=True)


# --- Workflow wiring ------------------------------------------------------------------------


def load_workflow(path: Path) -> dict[Any, Any]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def steps_of(path: Path, job_name: str) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in load_workflow(path)["jobs"][job_name]["steps"]}


@pytest.mark.parametrize("workflow_path", OWNED_WORKFLOWS, ids=lambda path: path.name)
def test_steps_that_call_github_have_a_token(workflow_path: Path) -> None:
    for job in load_workflow(workflow_path)["jobs"].values():
        for step in job.get("steps", []):
            script = str(step.get("run", ""))
            if not (GH_COMMAND.search(script) or "create_pr.sh" in script or "notify_pipeline_issue.sh" in script):
                continue
            env = {**job.get("env", {}), **step.get("env", {})}
            assert {"GH_TOKEN", "GITHUB_TOKEN"} & set(env), step["name"]


def test_node20_github_script_is_gone_from_every_workflow() -> None:
    for workflow_path in WORKFLOW_DIRECTORY.glob("*.y*ml"):
        assert "github-script@f28e40c7" not in workflow_path.read_text(encoding="utf-8"), workflow_path.name


@pytest.mark.parametrize(
    ("workflow_path", "job_name", "pipeline_name", "skip_condition"),
    [
        pytest.param(
            COMMON_WORKFLOW, "fetch-data", "${{ inputs.workflow_type }}", "inputs.dry_run != true", id="fetch"
        ),
        pytest.param(PROCESS_WORKFLOW, "process-data", "process", "env.DRY_RUN != 'true'", id="process"),
    ],
)
def test_notification_step_replaces_the_issue_steps(
    workflow_path: Path, job_name: str, pipeline_name: str, skip_condition: str
) -> None:
    steps = steps_of(workflow_path, job_name)
    names = list(steps)
    notify_step = steps["Notify pipeline anomaly"]

    assert "Create issue on processing failure" not in steps
    assert "Create issue on error" not in steps
    assert names.index("Create Pull Request") < names.index("Notify pipeline anomaly") < names.index("Upload logs")
    assert notify_step["if"] == f"always() && {skip_condition}"
    assert notify_step["run"].strip() == "bash scripts/notify_pipeline_issue.sh"
    assert notify_step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "JOB_STATUS": "${{ job.status }}",
        "PIPELINE_NAME": pipeline_name,
    }


def test_common_notification_runs_after_the_blocked_gate_failure() -> None:
    names = list(steps_of(COMMON_WORKFLOW, "fetch-data"))

    assert names.index("Fail when gate is blocked without PR") < names.index("Notify pipeline anomaly")


def test_continued_processing_failure_creates_check_annotation() -> None:
    assert (
        "::error title=Data processing failed::"
        in steps_of(COMMON_WORKFLOW, "fetch-data")["Process epidemic data"]["run"]
    )


@pytest.mark.parametrize(
    ("workflow_path", "job_name", "timestamp"),
    [
        pytest.param(COMMON_WORKFLOW, "fetch-data", "FETCH_TIMESTAMP", id="fetch"),
        pytest.param(PROCESS_WORKFLOW, "process-data", "PROCESS_TIMESTAMP", id="process"),
    ],
)
def test_upload_logs_is_limited_to_this_run(workflow_path: Path, job_name: str, timestamp: str) -> None:
    upload = steps_of(workflow_path, job_name)["Upload logs"]["with"]

    assert upload["path"].startswith("data/logs/*_")
    assert f"${{{{ env.{timestamp} }}}}" in upload["path"]
    assert upload["if-no-files-found"] == "warn"


def test_claude_review_checkout_does_not_persist_credentials() -> None:
    steps = steps_of(WORKFLOW_DIRECTORY / "claude-code-review.yml", "claude-review")

    assert steps["Checkout repository"]["with"]["persist-credentials"] is False


def test_claude_mentions_start_only_for_trusted_associations() -> None:
    workflow = load_workflow(WORKFLOW_DIRECTORY / "claude.yml")
    condition = workflow["jobs"]["claude"]["if"]

    assert workflow[True]["issues"]["types"] == ["opened"]
    branches = [line for line in condition.splitlines() if "github.event_name" in line]
    assert len(branches) == 4
    trusted = """contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), """
    for event, association in (
        ("issue_comment", "github.event.comment.author_association"),
        ("pull_request_review_comment", "github.event.comment.author_association"),
        ("pull_request_review", "github.event.review.author_association"),
        ("issues", "github.event.issue.author_association"),
    ):
        [branch] = [line for line in branches if f"github.event_name == '{event}'" in line]
        assert f"{trusted}{association})" in branch, event


# --- Process step: raw change detection (issue #766 item 7) --------------------------------

FAKE_PROCESS_UV = """\
import json, os, sys
with open(os.environ["FAKE_UV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_PROCESS_EXIT", "0")))
"""


def run_process_step(
    tmp_path: Path, *, detection: str, continue_on_failure: str, force_merge: str = "false", process_exit: str = "0"
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], list[list[str]]]:
    """Run `Process epidemic data` with a stub detection script; `detection` is `fail` or the file list."""
    steps = steps_of(COMMON_WORKFLOW, "fetch-data")
    step = steps["Process epidemic data"]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    listing = "exit 1\n" if detection == "fail" else f"printf '%s' '{detection}'\n"
    (scripts / "list_changed_raw_files.sh").write_text(listing, encoding="utf-8")
    (tmp_path / "data" / "logs").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_PROCESS_UV}", encoding="utf-8")
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    rendered_env = {
        key: {
            "${{ inputs.continue_on_failure }}": continue_on_failure,
            "${{ inputs.force_merge_on_failure }}": force_merge,
        }[value]
        for key, value in step.get("env", {}).items()
    }
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "GITHUB_ENV": str(github_env),
        "RUNNER_TEMP": str(runner_temp),
        "FETCH_TIMESTAMP": "20260920_000000",
        "PRE_FETCH_SHA": "0" * 40,
        "FAKE_UV_LOG": str(tmp_path / "uv.jsonl"),
        "FAKE_PROCESS_EXIT": process_exit,
        **rendered_env,
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    written: dict[str, str] = {}
    for line in github_env.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        written[key] = value
    uv_log = tmp_path / "uv.jsonl"
    calls = [json.loads(line) for line in uv_log.read_text(encoding="utf-8").splitlines()] if uv_log.exists() else []
    return result, written, calls


@pytest.mark.parametrize(
    ("continue_on_failure", "force_merge", "expected_exit", "expected_reason"),
    [
        pytest.param("true", "false", 0, "continue_on_failure=true により継続", id="continue"),
        pytest.param("false", "true", 0, "force_merge_on_failure=true により継続", id="force-merge"),
        pytest.param("false", "false", 1, "none", id="stop"),
    ],
)
def test_detection_failure_is_a_processing_failure(
    tmp_path: Path, continue_on_failure: str, force_merge: str, expected_exit: int, expected_reason: str
) -> None:
    result, written, calls = run_process_step(
        tmp_path, detection="fail", continue_on_failure=continue_on_failure, force_merge=force_merge
    )

    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert written["RAW_CHANGED_COUNT"] == "unknown"
    assert written["PROCESS_RESULT"] == "failed"
    assert written["PROCESS_CONTINUED_REASON"] == expected_reason
    assert "::error title=Raw change detection failed::" in result.stdout
    assert "::error title=Data processing failed::" in result.stdout
    assert calls == []


def test_no_changed_raw_skips_processing(tmp_path: Path) -> None:
    result, written, calls = run_process_step(tmp_path, detection="", continue_on_failure="true")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (written["RAW_CHANGED_COUNT"], written["PROCESS_RESULT"]) == ("0", "skipped")
    assert calls == []


def test_changed_raw_is_processed_and_a_failure_is_annotated(tmp_path: Path) -> None:
    files = "data/raw/a_2026_01.csv\ndata/raw/b 2026_02.csv\n"

    result, written, calls = run_process_step(tmp_path, detection=files, continue_on_failure="true", process_exit="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (written["RAW_CHANGED_COUNT"], written["PROCESS_RESULT"]) == ("2", "failed")
    assert calls == [["run", "--locked", "process-data", "--files", "data/raw/a_2026_01.csv", "data/raw/b 2026_02.csv"]]
    assert "::error title=Data processing failed::" in result.stdout
    assert "::error title=Raw change detection failed::" not in result.stdout


def test_changed_raw_processing_success(tmp_path: Path) -> None:
    result, written, calls = run_process_step(
        tmp_path, detection="data/raw/a_2026_01.csv\n", continue_on_failure="false"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (written["RAW_CHANGED_COUNT"], written["PROCESS_RESULT"]) == ("1", "success")
    assert len(calls) == 1
