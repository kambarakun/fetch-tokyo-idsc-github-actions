from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "auto_merge_gate.sh"
COMMON_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "_fetch-data-common.yml"
CREATE_PR_SCRIPT = PROJECT_ROOT / "scripts" / "create_pr.sh"


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
  "CONTINUITY_GATE_STATUS=$CONTINUITY_GATE_STATUS"
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
    assert "FETCH_CONTINUED_REASON: ${{ env.FETCH_CONTINUED_REASON }}" in workflow
    assert "PROCESS_CONTINUED_REASON: ${{ env.PROCESS_CONTINUED_REASON }}" in workflow
    assert "VERIFY_CONTINUITY: ${{ inputs.verify_continuity }}" in workflow
    assert "CONTINUITY_VALID: ${{ env.CONTINUITY_VALID }}" in workflow
    assert "VALIDATION_BEFORE_SUCCESS: ${{ env.VALIDATION_BEFORE_SUCCESS }}" in workflow
    assert "VALIDATION_SUCCESS: ${{ env.VALIDATION_SUCCESS }}" in workflow


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


@pytest.mark.parametrize("script", [GATE_SCRIPT, CREATE_PR_SCRIPT])
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
