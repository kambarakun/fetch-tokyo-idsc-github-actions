"""Structure and behavior tests for the gates around automated data PRs (issue #731)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
COMMON_WORKFLOW = WORKFLOW_DIRECTORY / "_fetch-data-common.yml"

# Compare against the commit checked out before fetching: fetch-data may commit raw files locally,
# so a staged-only diff would miss them, while README/data/logs churn must not count as data.
DATA_CHANGE_CHECK = 'git diff --cached --quiet "$PRE_FETCH_SHA" -- data/raw'
BOT_ONLY_PATHS = ["data/**", "docs/images/**", "README.md"]


def load_workflow(name: str) -> dict[Any, Any]:
    workflow = yaml.safe_load((WORKFLOW_DIRECTORY / name).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def common_steps() -> dict[str, dict[str, Any]]:
    workflow = load_workflow(COMMON_WORKFLOW.name)
    return {step["name"]: step for step in workflow["jobs"]["fetch-data"]["steps"]}


def step_index(names: list[str], name: str) -> int:
    assert name in names, f"missing step: {name}"
    return names.index(name)


@pytest.mark.parametrize("workflow_name", ["test.yml", "claude-code-review.yml"])
def test_pull_request_ci_ignores_bot_data_only_paths(workflow_name: str) -> None:
    # PyYAML parses the bare `on:` key as the boolean True.
    triggers = load_workflow(workflow_name)[True]

    assert triggers["pull_request"]["paths-ignore"] == BOT_ONLY_PATHS


def test_push_ci_still_runs_for_every_path() -> None:
    push = load_workflow("test.yml")[True]["push"]

    assert "paths" not in push
    assert "paths-ignore" not in push


@pytest.mark.parametrize("workflow_name", ["fetch-data-daily.yml", "fetch-data-weekly.yml"])
def test_scheduled_fetch_workflows_verify_continuity(workflow_name: str) -> None:
    caller = load_workflow(workflow_name)

    assert caller["jobs"]["fetch-data"]["with"]["verify_continuity"] is True


def test_continuity_check_allows_one_unpublished_period() -> None:
    assert "--grace-periods 1" in common_steps()["Verify data continuity"]["run"]


def test_schema_validation_runs_before_the_gate_with_installed_extras() -> None:
    steps = common_steps()
    names = list(steps)

    assert (
        step_index(names, "Validate metadata against JSON Schema")
        < step_index(names, "Evaluate auto-merge gate")
        < step_index(names, "Create Pull Request")
    )
    schema_step = steps["Validate metadata against JSON Schema"]
    assert schema_step["if"] == "inputs.dry_run != true"
    assert "uv run --locked python scripts/validate_metadata_schema.py" in schema_step["run"]
    assert "SCHEMA_VALID=true" in schema_step["run"]
    assert "SCHEMA_VALID=false" in schema_step["run"]
    assert "::error title=Metadata schema validation failed::" in schema_step["run"]
    # The validator imports jsonschema, which only the dev extra provides.
    assert "--all-extras" in steps["Install dependencies"]["run"]
    for name in ("Evaluate auto-merge gate", "Create Pull Request"):
        assert steps[name]["env"]["SCHEMA_VALID"] == "${{ env.SCHEMA_VALID }}"


def test_presentation_steps_cannot_block_the_data_pr() -> None:
    steps = common_steps()
    names = list(steps)
    expected_ids = {"Generate visualization charts": "charts", "Update README statistics": "readme_stats"}

    for name, step_id in expected_ids.items():
        assert steps[name]["id"] == step_id
        assert steps[name]["continue-on-error"] is True

    warning_steps = [
        name
        for name, step in steps.items()
        if all(f"steps.{step_id}.outcome == 'failure'" in str(step.get("if", "")) for step_id in expected_ids.values())
    ]
    assert len(warning_steps) == 1
    warning_step = warning_steps[0]
    assert "::warning" in steps[warning_step]["run"]
    assert "$GITHUB_STEP_SUMMARY" in steps[warning_step]["run"]
    assert step_index(names, "Update README statistics") < step_index(names, warning_step)
    assert step_index(names, warning_step) < step_index(names, "Create Pull Request")


def test_pull_request_is_created_only_for_raw_data_changes() -> None:
    steps = common_steps()
    names = list(steps)

    assert steps["Create Pull Request"]["if"] == "env.HAS_DATA_CHANGES == 'true' && inputs.dry_run != true"
    check_run = steps["Check for changes"]["run"]
    assert DATA_CHANGE_CHECK in check_run
    assert "HAS_DATA_CHANGES=" in check_run
    assert 'git diff --cached --name-status "$PRE_FETCH_SHA"' in check_run
    assert 'git diff --cached --name-only "$PRE_FETCH_SHA"' in check_run
    assert "PRE_FETCH_SHA=$(git rev-parse HEAD)" in steps["Record pre-fetch commit"]["run"]
    assert step_index(names, "Record pre-fetch commit") < step_index(names, "Fetch epidemic data")
    assert "HAS_DATA_CHANGES" in steps["Generate summary"]["run"]


def test_blocked_gate_without_pr_fails_the_job() -> None:
    steps = common_steps()
    names = list(steps)
    fail_step = steps["Fail when gate is blocked without PR"]

    assert (
        step_index(names, "Create Pull Request")
        < step_index(names, "Fail when gate is blocked without PR")
        < step_index(names, "Upload logs")
    )
    assert "inputs.dry_run != true" in fail_step["if"]
    assert "env.HAS_DATA_CHANGES != 'true'" in fail_step["if"]
    assert "env.AUTO_MERGE_BLOCKERS != 'none'" in fail_step["if"]
    assert "::error title=Auto-merge gate blocked without PR::" in fail_step["run"]
    assert "exit 1" in fail_step["run"]


def isolated_git_env(**extra: str) -> dict[str, str]:
    # Drop GIT_* (e.g. GIT_INDEX_FILE set by hooks) so the temp repository is used.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(extra)
    return env


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        env=isolated_git_env(),
        check=True,
        capture_output=True,
    )


def run_data_change_check(repo: Path, pre_fetch_sha: str) -> int:
    return subprocess.run(
        ["bash", "-c", DATA_CHANGE_CHECK],
        cwd=repo,
        env=isolated_git_env(PRE_FETCH_SHA=pre_fetch_sha),
        check=False,
    ).returncode


@pytest.fixture
def fetched_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "data" / "raw" / ".metadata").mkdir(parents=True)
    (repo / "data" / "logs").mkdir()
    (repo / "data" / "raw" / "a.csv").write_text("old\n", encoding="utf-8")
    (repo / "README.md").write_text("最新データ取得日時: old\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    pre_fetch_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=isolated_git_env(), check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, pre_fetch_sha


def test_data_change_check_ignores_readme_and_log_churn(fetched_repo: tuple[Path, str]) -> None:
    repo, pre_fetch_sha = fetched_repo
    (repo / "README.md").write_text("最新データ取得日時: new\n", encoding="utf-8")
    (repo / "data" / "logs" / "x.json").write_text("{}\n", encoding="utf-8")
    git(repo, "add", "data/", "README.md")

    assert run_data_change_check(repo, pre_fetch_sha) == 0


def test_data_change_check_sees_raw_committed_during_fetch(fetched_repo: tuple[Path, str]) -> None:
    repo, pre_fetch_sha = fetched_repo
    (repo / "data" / "raw" / "a.csv").write_text("new\n", encoding="utf-8")
    git(repo, "add", "data/raw/a.csv")
    git(repo, "commit", "-q", "-m", "fetch auto-commit")
    git(repo, "add", "data/")

    assert run_data_change_check(repo, pre_fetch_sha) == 1


def test_data_change_check_sees_new_untracked_metadata(fetched_repo: tuple[Path, str]) -> None:
    repo, pre_fetch_sha = fetched_repo
    (repo / "data" / "raw" / ".metadata" / "b.json").write_text("{}\n", encoding="utf-8")
    git(repo, "add", "data/")

    assert run_data_change_check(repo, pre_fetch_sha) == 1
