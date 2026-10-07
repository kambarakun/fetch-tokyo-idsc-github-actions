from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "auto_merge_gate.sh"
COMMON_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "_fetch-data-common.yml"
CREATE_PR_SCRIPT = PROJECT_ROOT / "scripts" / "create_pr.sh"
LIST_CHANGED_RAW_SCRIPT = PROJECT_ROOT / "scripts" / "list_changed_raw_files.sh"
WORKFLOW_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
UV_PROJECT_COMMAND = re.compile(r"\buv\s+(sync|run)\b")
LOCKED_OPTION = re.compile(r"(?:^|\s)--locked(?=$|[\s;&|])")
NO_PROJECT_OPTION = re.compile(r"(?:^|\s)--no-project(?=$|[\s;&|])")


def test_workflow_project_commands_do_not_mutate_the_lockfile() -> None:
    unguarded_commands: list[str] = []

    for workflow_path in sorted(WORKFLOW_DIRECTORY.glob("*.y*ml")):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict)
        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict)

        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", [])
            if not isinstance(steps, list):
                continue

            for step in steps:
                if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                    continue
                script = re.sub(r"\\\s*\n\s*", " ", step["run"])
                for line in script.splitlines():
                    for command in re.split(r"&&|\|\||;", line):
                        match = UV_PROJECT_COMMAND.search(command)
                        if match is None:
                            continue
                        arguments = command[match.end() :]
                        guarded = LOCKED_OPTION.search(arguments) is not None
                        if match.group(1) == "run":
                            guarded = guarded or NO_PROJECT_OPTION.search(arguments) is not None
                        if guarded:
                            continue
                        step_name = step.get("name", "unnamed step")
                        unguarded_commands.append(f"{workflow_path.name}:{job_name}:{step_name}: {command.strip()}")

    assert unguarded_commands == []


def test_locked_sync_rejects_a_missing_lockfile(tmp_path: Path) -> None:
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        """\
[project]
name = "missing-lockfile"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = []
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        [shutil.which("uv") or "uv", "sync", "--locked"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "uv.lock" in result.stderr


def evaluate_gate(
    *,
    workflow_name: str,
    auto_merge: str,
    force_merge: str,
    fetch_status: str,
    process_result: str,
    validations_passed: str,
    verify_continuity: str = "false",
    continuity_valid: str = "",
    raw_changed_count: str = "0",
    processing_coverage: str = "complete",
    schema_valid: str = "true",
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_NAME": workflow_name,
            "AUTO_MERGE": auto_merge,
            "FORCE_MERGE_ON_FAILURE": force_merge,
            "FETCH_STATUS": fetch_status,
            "PROCESS_RESULT": process_result,
            "VALIDATIONS_PASSED": validations_passed,
            "VALIDATION_BEFORE_SUCCESS": validations_passed,
            "VALIDATION_SUCCESS": validations_passed,
            "VALIDATION_PASSED": validations_passed,
            "VERIFY_OUTPUT": "true",
            "VERIFY_CONTINUITY": verify_continuity,
            "CONTINUITY_VALID": continuity_valid,
            "RAW_CHANGED_COUNT": raw_changed_count,
            "PROCESSING_COVERAGE_STATUS": processing_coverage,
            "SCHEMA_VALID": schema_valid,
        }
    )
    command = """
source "$1"
evaluate_auto_merge_gate
printf '%s\n' \
  "AUTO_MERGE_EFFECTIVE=$AUTO_MERGE_EFFECTIVE" \
  "AUTO_MERGE_GATE_STATUS=$AUTO_MERGE_GATE_STATUS" \
  "AUTO_MERGE_BLOCKERS=$AUTO_MERGE_BLOCKERS" \
  "AUTO_MERGE_OVERRIDE_USED=$AUTO_MERGE_OVERRIDE_USED" \
  "FETCH_GATE_STATUS=$FETCH_GATE_STATUS" \
  "PROCESS_GATE_STATUS=$PROCESS_GATE_STATUS" \
  "VALIDATION_GATE_STATUS=$VALIDATION_GATE_STATUS" \
  "CONTINUITY_GATE_STATUS=$CONTINUITY_GATE_STATUS" \
  "SCHEMA_GATE_STATUS=$SCHEMA_GATE_STATUS"
"""
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(GATE_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


@pytest.mark.parametrize(
    (
        "workflow_name",
        "auto_merge",
        "force_merge",
        "fetch_status",
        "process_result",
        "validations_passed",
        "expected_effective",
        "expected_gate_status",
        "expected_blockers",
    ),
    [
        ("fetch-data-daily", "true", "false", "success", "success", "true", "true", "passed", "none"),
        ("fetch-data-daily", "true", "false", "failed", "success", "true", "false", "blocked", "fetch"),
        ("fetch-data-weekly", "true", "false", "success", "failed", "true", "false", "blocked", "process"),
        ("fetch-data-weekly", "true", "false", "success", "success", "false", "false", "blocked", "validation"),
        ("fetch-data", "false", "false", "success", "skipped", "true", "false", "not_requested", "none"),
        ("fetch-data", "true", "false", "unknown", "success", "true", "false", "blocked", "fetch"),
        ("fetch-data", "true", "true", "failed", "success", "true", "true", "overridden", "fetch"),
    ],
    ids=[
        "daily-success",
        "daily-fetch-failure",
        "weekly-process-failure",
        "weekly-validation-failure",
        "manual-auto-merge-disabled",
        "manual-unknown-fetch-status",
        "manual-explicit-force-override",
    ],
)
def test_fetch_workflow_auto_merge_truth_table(
    workflow_name: str,
    auto_merge: str,
    force_merge: str,
    fetch_status: str,
    process_result: str,
    validations_passed: str,
    expected_effective: str,
    expected_gate_status: str,
    expected_blockers: str,
) -> None:
    result = evaluate_gate(
        workflow_name=workflow_name,
        auto_merge=auto_merge,
        force_merge=force_merge,
        fetch_status=fetch_status,
        process_result=process_result,
        validations_passed=validations_passed,
    )

    assert result["AUTO_MERGE_EFFECTIVE"] == expected_effective
    assert result["AUTO_MERGE_GATE_STATUS"] == expected_gate_status
    assert result["AUTO_MERGE_BLOCKERS"] == expected_blockers
    assert result["AUTO_MERGE_OVERRIDE_USED"] == ("true" if expected_gate_status == "overridden" else "false")


@pytest.mark.parametrize(
    ("continuity_valid", "expected_effective", "expected_status", "expected_blockers"),
    [
        ("true", "true", "passed", "none"),
        ("false", "false", "failed", "continuity"),
        ("", "false", "unknown", "continuity"),
    ],
)
def test_manual_continuity_gate_is_fail_closed(
    continuity_valid: str,
    expected_effective: str,
    expected_status: str,
    expected_blockers: str,
) -> None:
    result = evaluate_gate(
        workflow_name="fetch-data",
        auto_merge="true",
        force_merge="false",
        fetch_status="success",
        process_result="success",
        validations_passed="true",
        verify_continuity="true",
        continuity_valid=continuity_valid,
    )

    assert result["AUTO_MERGE_EFFECTIVE"] == expected_effective
    assert result["CONTINUITY_GATE_STATUS"] == expected_status
    assert result["AUTO_MERGE_BLOCKERS"] == expected_blockers


@pytest.mark.parametrize(
    (
        "workflow_name",
        "force_merge",
        "process_result",
        "raw_changed_count",
        "processing_coverage",
        "expected_gate_status",
        "expected_blockers",
    ),
    [
        ("fetch-data-daily", "false", "skipped", "0", "complete", "passed", "none"),
        ("fetch-data-daily", "false", "skipped", "5", "complete", "blocked", "process"),
        ("fetch-data-weekly", "false", "skipped", "", "complete", "blocked", "process"),
        ("fetch-data", "false", "skipped", "unknown", "complete", "blocked", "process"),
        ("fetch-data-daily", "false", "success", "5", "complete", "passed", "none"),
        ("fetch-data-daily", "false", "success", "5", "incomplete", "blocked", "coverage"),
        ("fetch-data-weekly", "false", "success", "5", "", "blocked", "coverage"),
        ("fetch-data-daily", "true", "skipped", "5", "incomplete", "overridden", "process,coverage"),
    ],
    ids=[
        "skipped-without-raw-changes",
        "skipped-despite-raw-changes",
        "skipped-with-unset-raw-count",
        "skipped-with-unknown-raw-count",
        "processed-raw-changes",
        "coverage-incomplete",
        "coverage-unset",
        "force-overrides-process-and-coverage",
    ],
)
def test_fetch_gate_blocks_unprocessed_raw_changes(
    workflow_name: str,
    force_merge: str,
    process_result: str,
    raw_changed_count: str,
    processing_coverage: str,
    expected_gate_status: str,
    expected_blockers: str,
) -> None:
    result = evaluate_gate(
        workflow_name=workflow_name,
        auto_merge="true",
        force_merge=force_merge,
        fetch_status="success",
        process_result=process_result,
        validations_passed="true",
        raw_changed_count=raw_changed_count,
        processing_coverage=processing_coverage,
    )

    assert result["AUTO_MERGE_GATE_STATUS"] == expected_gate_status
    assert result["AUTO_MERGE_BLOCKERS"] == expected_blockers
    assert result["AUTO_MERGE_EFFECTIVE"] == ("false" if expected_gate_status == "blocked" else "true")


@pytest.mark.parametrize("workflow_name", ["fetch-data-daily", "fetch-data-weekly", "fetch-data"])
@pytest.mark.parametrize(
    ("schema_valid", "expected_effective", "expected_gate_status", "expected_schema_status", "expected_blockers"),
    [
        ("true", "true", "passed", "passed", "none"),
        ("false", "false", "blocked", "failed", "schema"),
        ("", "false", "blocked", "unknown", "schema"),
    ],
    ids=["schema-valid", "schema-invalid", "schema-unset"],
)
def test_schema_gate_blocks_fetch_workflows_fail_closed(
    workflow_name: str,
    schema_valid: str,
    expected_effective: str,
    expected_gate_status: str,
    expected_schema_status: str,
    expected_blockers: str,
) -> None:
    result = evaluate_gate(
        workflow_name=workflow_name,
        auto_merge="true",
        force_merge="false",
        fetch_status="success",
        process_result="success",
        validations_passed="true",
        raw_changed_count="5",
        schema_valid=schema_valid,
    )

    assert result["SCHEMA_GATE_STATUS"] == expected_schema_status
    assert result["AUTO_MERGE_BLOCKERS"] == expected_blockers
    assert result["AUTO_MERGE_GATE_STATUS"] == expected_gate_status
    assert result["AUTO_MERGE_EFFECTIVE"] == expected_effective


@pytest.mark.parametrize("workflow_name", ["process-data", "migrate-metadata"])
def test_schema_gate_is_not_applicable_outside_fetch_workflows(workflow_name: str) -> None:
    result = evaluate_gate(
        workflow_name=workflow_name,
        auto_merge="true",
        force_merge="false",
        fetch_status="success",
        process_result="success",
        validations_passed="true",
        schema_valid="false",
    )

    assert result["SCHEMA_GATE_STATUS"] == "not_applicable"
    assert "schema" not in result["AUTO_MERGE_BLOCKERS"].split(",")
    assert result["AUTO_MERGE_GATE_STATUS"] == "passed"


def test_schema_gate_status_is_exported_for_the_job_summary(tmp_path: Path) -> None:
    github_env = tmp_path / "github-env"
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_NAME": "fetch-data-weekly",
            "AUTO_MERGE": "true",
            "FETCH_STATUS": "success",
            "PROCESS_RESULT": "success",
            "PROCESSING_COVERAGE_STATUS": "complete",
            "VALIDATION_BEFORE_SUCCESS": "true",
            "VALIDATION_SUCCESS": "true",
            "SCHEMA_VALID": "false",
            "GITHUB_ENV": str(github_env),
        }
    )

    subprocess.run(
        ["bash", "-c", 'source "$1"; evaluate_auto_merge_gate; write_auto_merge_gate_env', "bash", str(GATE_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )

    exported = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
    assert exported["SCHEMA_GATE_STATUS"] == "failed"
    assert exported["AUTO_MERGE_BLOCKERS"] == "schema"


def test_process_data_gate_ignores_fetch_only_inputs() -> None:
    result = evaluate_gate(
        workflow_name="process-data",
        auto_merge="true",
        force_merge="false",
        fetch_status="unknown",
        process_result="success",
        validations_passed="true",
        raw_changed_count="",
        processing_coverage="",
    )

    assert result["AUTO_MERGE_GATE_STATUS"] == "passed"
    assert result["AUTO_MERGE_BLOCKERS"] == "none"


def test_manual_validation_gate_is_not_requested() -> None:
    result = evaluate_gate(
        workflow_name="fetch-data",
        auto_merge="true",
        force_merge="false",
        fetch_status="success",
        process_result="success",
        validations_passed="false",
    )

    assert result["VALIDATION_GATE_STATUS"] == "not_requested"
    assert result["AUTO_MERGE_EFFECTIVE"] == "true"
    assert result["AUTO_MERGE_BLOCKERS"] == "none"


def test_common_workflow_forwards_every_gate_input() -> None:
    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")

    assert "FORCE_MERGE_ON_FAILURE: ${{ inputs.force_merge_on_failure }}" in workflow
    assert "FETCH_STATUS: ${{ env.FETCH_STATUS }}" in workflow
    assert "PROCESS_RESULT: ${{ env.PROCESS_RESULT }}" in workflow
    assert "RAW_CHANGED_COUNT: ${{ env.RAW_CHANGED_COUNT }}" in workflow
    assert "PROCESSING_COVERAGE_STATUS: ${{ env.PROCESSING_COVERAGE_STATUS }}" in workflow
    assert "FETCH_CONTINUED_REASON: ${{ env.FETCH_CONTINUED_REASON }}" in workflow
    assert "PROCESS_CONTINUED_REASON: ${{ env.PROCESS_CONTINUED_REASON }}" in workflow
    assert "VERIFY_CONTINUITY: ${{ inputs.verify_continuity }}" in workflow
    assert "CONTINUITY_VALID: ${{ env.CONTINUITY_VALID }}" in workflow
    assert "VALIDATION_BEFORE_SUCCESS: ${{ env.VALIDATION_BEFORE_SUCCESS }}" in workflow
    assert "VALIDATION_SUCCESS: ${{ env.VALIDATION_SUCCESS }}" in workflow
    assert "SCHEMA_VALID: ${{ env.SCHEMA_VALID }}" in workflow


def test_common_workflow_processes_raw_changed_since_pre_fetch_commit() -> None:
    workflow = yaml.safe_load(COMMON_WORKFLOW.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in workflow["jobs"]["fetch-data"]["steps"]}
    names = list(steps)

    ordered = [
        "Record pre-fetch commit",
        "Fetch epidemic data",
        "Process epidemic data",
        "Check processing coverage",
        "Evaluate auto-merge gate",
    ]
    assert [names.index(name) for name in ordered] == sorted(names.index(name) for name in ordered)
    assert "PRE_FETCH_SHA=$(git rev-parse HEAD)" in steps["Record pre-fetch commit"]["run"]
    process_run = steps["Process epidemic data"]["run"]
    assert "scripts/list_changed_raw_files.sh" in process_run
    assert "git status --porcelain" not in process_run
    coverage_run = steps["Check processing coverage"]["run"]
    assert "check-data-status" in coverage_run
    assert "--fail-on-incomplete" in coverage_run
    assert "select(.reason != null)" in coverage_run


def test_common_workflow_captures_canonical_continuity_result() -> None:
    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")
    continuity_step = workflow[workflow.index("- name: Verify data continuity") :]
    continuity_step = continuity_step[: continuity_step.index("- name: Generate visualization charts")]

    assert "uv run --locked check-missing data/raw" in continuity_step
    assert '--start-year "$START_YEAR"' in continuity_step
    assert '--end-year "$END_YEAR"' in continuity_step
    assert '--as-of "$CURRENT_DATE"' in continuity_step
    assert "CURRENT_DATE=$(TZ=Asia/Tokyo date +'%Y-%m-%d')" in workflow
    assert "CURRENT_YEAR=$(TZ=Asia/Tokyo date +'%Y')" in workflow
    assert "CURRENT_MONTH=$(TZ=Asia/Tokyo date +'%m')" in workflow
    assert "CURRENT_WEEK=$(TZ=Asia/Tokyo date +'%V')" in workflow
    assert "--format json" in continuity_step
    assert "CONTINUITY_VALID=true" in continuity_step
    assert "CONTINUITY_VALID=false" in continuity_step
    assert 'echo "CONTINUITY_VALID=$CONTINUITY_VALID" >> "$GITHUB_ENV"' in continuity_step
    assert 'jq .summary "$CONTINUITY_REPORT" || echo "⚠️ レポート解析に失敗しました"' in continuity_step
    assert "|| true" not in continuity_step


def test_common_workflow_evaluates_gate_before_optional_pr_creation() -> None:
    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")

    evaluation_step = workflow.index("- name: Evaluate auto-merge gate")
    create_pr_step = workflow.index("- name: Create Pull Request")

    assert evaluation_step < create_pr_step
    assert "write_auto_merge_gate_env" in workflow[evaluation_step:create_pr_step]
    assert 'echo "AUTO_MERGE_GATE_EVALUATED=$AUTO_MERGE_GATE_EVALUATED"' in GATE_SCRIPT.read_text(encoding="utf-8")
    assert 'if [ "${AUTO_MERGE_GATE_EVALUATED:-false}" != "true" ]; then' in CREATE_PR_SCRIPT.read_text(
        encoding="utf-8"
    )


def test_gate_env_forwards_normalized_request_and_override_to_pr_step(tmp_path: Path) -> None:
    github_env = tmp_path / "github-env"
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_NAME": "fetch-data-daily",
            "AUTO_MERGE": "true",
            "FORCE_MERGE_ON_FAILURE": "true",
            "FETCH_STATUS": "failed",
            "PROCESS_RESULT": "success",
            "VALIDATION_BEFORE_SUCCESS": "true",
            "GITHUB_ENV": str(github_env),
        }
    )
    command = 'source "$1"; evaluate_auto_merge_gate; write_auto_merge_gate_env'

    subprocess.run(
        ["bash", "-c", command, "bash", str(GATE_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )

    exported = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
    assert exported["AUTO_MERGE_REQUESTED"] == "true"
    assert exported["FORCE_MERGE"] == "true"

    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")
    assert "AUTO_MERGE_REQUESTED: ${{ env.AUTO_MERGE_REQUESTED }}" in workflow
    assert "FORCE_MERGE: ${{ env.FORCE_MERGE }}" in workflow


def test_continued_fetch_failure_creates_check_annotation() -> None:
    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")

    assert "::error title=Data fetch failed::" in workflow


@pytest.mark.parametrize("script", [GATE_SCRIPT, CREATE_PR_SCRIPT, LIST_CHANGED_RAW_SCRIPT])
def test_auto_merge_shell_scripts_parse(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], cwd=PROJECT_ROOT, check=True)


def test_pr_body_and_job_summary_report_the_composite_gate() -> None:
    create_pr = CREATE_PR_SCRIPT.read_text(encoding="utf-8")
    workflow = COMMON_WORKFLOW.read_text(encoding="utf-8")

    for field in (
        "FETCH_GATE_STATUS",
        "PROCESS_GATE_STATUS",
        "VALIDATION_GATE_STATUS",
        "CONTINUITY_GATE_STATUS",
        "AUTO_MERGE_GATE_STATUS",
        "AUTO_MERGE_BLOCKERS",
        "AUTO_MERGE_OVERRIDE_USED",
        "FETCH_CONTINUED_REASON",
        "PROCESS_CONTINUED_REASON",
    ):
        assert field in create_pr
        assert field in workflow
